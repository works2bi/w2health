"""Consultas da investigação de custos (Evolução V1).

Mesmo padrão de `analytics_repo`: **fail-closed por tenant** (`_t(session)` levanta se não
houver tenant amarrado à sessão) + RLS do PostgreSQL como 2ª camada. As consultas leem a
fato `eventos_assistenciais` sob demanda, restritas às competências da janela analisada.

Escopo (`Escopo`): operadora inteira, um contrato (`contrato_id`), um fator
(`dimensao`/`chave`, mesmas dimensões de `explicar`) ou a combinação contrato + fator.
Contrato/plano do EVENTO = vínculo congelado na competência (`COALESCE` com o beneficiário
para registros legados).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.tenant_scope import tenant_of as _t
from app.repositories.analytics_repo import NAO_ATRIBUIDO, _filtro_dimensao

_FROM = """
    FROM eventos_assistenciais e
    JOIN beneficiarios b ON b.id = e.id_beneficiario
    JOIN procedimentos proc ON proc.id = e.id_procedimento
"""

#: dimensões do escopo que também restringem a POPULAÇÃO exposta (denominador). `x` é a
#: exposição pelo VÍNCULO VIGENTE (Consolidação V1) — contrato/plano do vínculo, não do cadastro
_FILTRO_POPULACAO: dict[str, str] = {
    "faixa_etaria": "x.faixa_etaria = :kt",
    "sexo": "x.sexo = :kt",
    "plano": "x.id_plano = :kv",
    "contrato": "x.id_contrato = :kv",
    "regiao": "x.id_regiao = :kv",
}

#: contrato é da empresa (papel contratante) na data `{d}` — vigência [início, fim)
_CONTRATOS_DA_EMPRESA = (
    "SELECT ec.id_contrato FROM empresas_contratos ec WHERE ec.tenant_id = :t "
    "AND ec.id_empresa = :emp AND ec.papel = 'contratante' AND ec.vigencia_inicio <= {d} "
    "AND (ec.vigencia_fim IS NULL OR ec.vigencia_fim > {d})"
)

NIVEIS_REDE = ("executante", "estabelecimento")

#: beneficiário-mês exposto, com o contrato/plano do VÍNCULO VIGENTE no 1º dia de cada
#: competência de `:meses` (mesma regra de `seed/aggregate._SQL_VIDAS_CTE`)
SQL_EXPOSICAO = """
hist AS (SELECT DISTINCT id_beneficiario FROM beneficiario_vinculos WHERE tenant_id = :t),
exposicao AS (
    SELECT m.c, b.id AS id_beneficiario, b.faixa_etaria, b.sexo, b.id_regiao,
           v.id_contrato, v.id_plano
    FROM unnest(CAST(:meses AS date[])) AS m(c)
    JOIN beneficiario_vinculos v
      ON v.tenant_id = :t AND v.vigencia_inicio <= m.c
     AND (v.vigencia_fim IS NULL OR v.vigencia_fim > m.c)
     AND COALESCE(v.situacao, 'ativo') = 'ativo'
    JOIN beneficiarios b ON b.id = v.id_beneficiario
    UNION ALL
    SELECT m.c, b.id, b.faixa_etaria, b.sexo, b.id_regiao, b.id_contrato, b.id_plano
    FROM unnest(CAST(:meses AS date[])) AS m(c)
    JOIN beneficiarios b
      ON b.tenant_id = :t AND b.data_adesao <= m.c
     AND (b.data_saida IS NULL OR b.data_saida > m.c)
    WHERE NOT EXISTS (SELECT 1 FROM hist h WHERE h.id_beneficiario = b.id)
)"""


@dataclass(frozen=True)
class Escopo:
    dimensao: str | None = None
    chave: str | None = None
    contrato_id: int | None = None
    nivel_rede: str = "executante"
    empresa_id: int | None = None  # Consolidação V1 — contratos da empresa na competência

    def filtro_eventos(self) -> tuple[str, dict]:
        where, params = [], {}
        if self.dimensao is not None:
            clausula, p = _filtro_dimensao(self.dimensao, self.chave)
            where.append(clausula)
            params.update(p)
        if self.contrato_id is not None:
            where.append("e.id_contrato = :ctr")
            params["ctr"] = self.contrato_id
        if self.empresa_id is not None:
            where.append(f"e.id_contrato IN ({_CONTRATOS_DA_EMPRESA.format(d='e.competencia')})")
            params["emp"] = self.empresa_id
        return ("AND " + " AND ".join(where)) if where else "", params

    def filtro_populacao(self) -> tuple[str, dict] | None:
        """Filtro da exposição. None = a dimensão não define população (ex.: procedimento):
        o denominador é a carteira inteira (ou a do contrato)."""
        where, params = [], {}
        if self.dimensao in _FILTRO_POPULACAO:
            if self.dimensao in ("contrato", "plano") and self.chave == NAO_ATRIBUIDO:
                where.append(f"x.id_{self.dimensao} IS NULL")
            else:
                clausula = _FILTRO_POPULACAO[self.dimensao]
                where.append(clausula)
                params.update({"kv": int(self.chave)} if ":kv" in clausula else {"kt": self.chave})
        if self.contrato_id is not None:
            where.append("x.id_contrato = :ctr")
            params["ctr"] = self.contrato_id
        if self.empresa_id is not None:
            where.append(f"x.id_contrato IN ({_CONTRATOS_DA_EMPRESA.format(d='x.c')})")
            params["emp"] = self.empresa_id
        return ("AND " + " AND ".join(where)) if where else "", params

    @property
    def expr_prestador(self) -> str:
        if self.nivel_rede == "estabelecimento":
            return "COALESCE(e.id_estabelecimento, e.id_prestador)"
        return "e.id_prestador"


def celulas(session: Session, meses: list[date], esc: Escopo) -> list[dict]:
    """procedimento × prestador × faixa etária: eventos, quantidade e despesa BRUTA."""
    filtro, params = esc.filtro_eventos()
    rows = session.execute(
        text(
            f"""
            SELECT e.id_procedimento AS procedimento, {esc.expr_prestador} AS prestador,
                   b.faixa_etaria AS faixa, COUNT(*) AS eventos,
                   SUM(e.quantidade) AS quantidade, SUM(e.valor_apresentado) AS valor
            {_FROM}
            WHERE e.tenant_id = :t AND e.competencia = ANY(:meses) {filtro}
            GROUP BY 1, 2, 3
            """
        ),
        {"t": _t(session), "meses": meses, **params},
    ).mappings().all()
    return [dict(r) for r in rows]


def financeiro(session: Session, meses: list[date], esc: Escopo) -> dict:
    """Totais do escopo na janela + partição por defasagem (atendimento de mês anterior à
    competência = apresentação tardia)."""
    filtro, params = esc.filtro_eventos()
    r = session.execute(
        text(
            f"""
            SELECT COUNT(*) AS eventos,
                   COALESCE(SUM(e.quantidade), 0) AS quantidade,
                   COUNT(DISTINCT e.id_beneficiario) AS beneficiarios,
                   COALESCE(SUM(e.valor_apresentado), 0) AS bruta,
                   COALESCE(SUM(e.valor_glosado), 0) AS glosa,
                   COALESCE(SUM(e.valor_coparticipacao), 0) AS coparticipacao,
                   COALESCE(SUM(e.valor_apresentado - e.valor_glosado - e.valor_coparticipacao), 0)
                       AS liquida,
                   COALESCE(SUM(e.valor_apresentado - e.valor_glosado - e.valor_coparticipacao)
                            FILTER (WHERE date_trunc('month', e.data_evento) < e.competencia), 0)
                       AS liquida_defasada,
                   COUNT(*) FILTER (WHERE date_trunc('month', e.data_evento) < e.competencia)
                       AS eventos_defasados,
                   COUNT(*) FILTER (WHERE e.id_estabelecimento IS NOT NULL) AS com_estabelecimento,
                   COUNT(*) FILTER (WHERE e.id_prestador_solicitante IS NOT NULL) AS com_solicitante,
                   COUNT(*) FILTER (WHERE e.id_contrato IS NOT NULL) AS com_vinculo
            {_FROM}
            WHERE e.tenant_id = :t AND e.competencia = ANY(:meses) {filtro}
            """
        ),
        {"t": _t(session), "meses": meses, **params},
    ).mappings().one()
    return {k: float(v) if v is not None else 0.0 for k, v in dict(r).items()}


def exposicao_por_faixa(session: Session, meses: list[date], esc: Escopo) -> dict[str, float]:
    """Beneficiários-mês expostos por faixa etária na janela (ativos no 1º dia de cada
    competência — mesma regra de `agg_contrato_competencia.vidas`)."""
    filtro, params = esc.filtro_populacao()
    rows = session.execute(
        text(
            f"""
            WITH {SQL_EXPOSICAO}
            SELECT x.faixa_etaria AS faixa, COUNT(*) AS expo
            FROM exposicao x
            WHERE TRUE {filtro}
            GROUP BY x.faixa_etaria
            """
        ),
        {"t": _t(session), "meses": meses, **params},
    ).mappings().all()
    return {r["faixa"]: float(r["expo"]) for r in rows}


def precos_vigentes(session: Session, meses: list[date], esc: Escopo) -> dict[tuple[int, int], float]:
    """(procedimento, prestador) -> preço contratado médio da janela, SÓ para pares com
    preço vigente em TODAS as competências da janela (sem cobertura parcial disfarçada).
    Nível `estabelecimento` não usa tabela (o preço é do executante)."""
    if esc.nivel_rede != "executante":
        return {}
    rows = session.execute(
        text(
            """
            SELECT pc.id_procedimento AS p, pc.id_prestador AS h,
                   COUNT(DISTINCT m.c) AS meses_cobertos, AVG(pc.valor_unitario) AS valor
            FROM unnest(CAST(:meses AS date[])) AS m(c)
            JOIN precos_contratados pc
              ON pc.tenant_id = :t AND pc.vigencia_inicio <= m.c
             AND (pc.vigencia_fim IS NULL OR pc.vigencia_fim > m.c)
            GROUP BY pc.id_procedimento, pc.id_prestador
            """
        ),
        {"t": _t(session), "meses": meses},
    ).mappings().all()
    n = len(set(meses))
    return {(int(r["p"]), int(r["h"])): float(r["valor"]) for r in rows if r["meses_cobertos"] == n}


def mudancas_de_preco(
    session: Session, pares: list[tuple[int, int]], inicio: date, fim: date
) -> list[dict]:
    """Vigências que COMEÇARAM entre `inicio` (exclusivo) e `fim` (inclusivo) para os pares
    informados, com o valor anterior — a evidência documental de um reajuste."""
    if not pares:
        return []
    rows = session.execute(
        text(
            """
            SELECT pc.id_procedimento AS p, pc.id_prestador AS h, pc.vigencia_inicio,
                   pc.valor_unitario AS valor_novo, pc.tipo_remuneracao,
                   (SELECT ant.valor_unitario FROM precos_contratados ant
                     WHERE ant.tenant_id = pc.tenant_id AND ant.id_prestador = pc.id_prestador
                       AND ant.id_procedimento = pc.id_procedimento
                       AND ant.vigencia_inicio < pc.vigencia_inicio
                     ORDER BY ant.vigencia_inicio DESC LIMIT 1) AS valor_anterior
            FROM precos_contratados pc
            WHERE pc.tenant_id = :t AND pc.vigencia_inicio > :ini AND pc.vigencia_inicio <= :fim
              AND (pc.id_procedimento, pc.id_prestador) IN (
                    SELECT x.p, x.h FROM unnest(CAST(:ps AS int[]), CAST(:hs AS int[])) AS x(p, h))
            ORDER BY pc.vigencia_inicio
            """
        ),
        {"t": _t(session), "ini": inicio, "fim": fim,
         "ps": [p for p, _ in pares], "hs": [h for _, h in pares]},
    ).mappings().all()
    return [dict(r) for r in rows]


def serie_mensal(session: Session, esc: Escopo) -> list[dict]:
    """Série mensal do escopo (todas as competências com dado): líquida, bruta, eventos."""
    filtro, params = esc.filtro_eventos()
    rows = session.execute(
        text(
            f"""
            SELECT e.competencia,
                   SUM(e.valor_apresentado - e.valor_glosado - e.valor_coparticipacao) AS liquida,
                   SUM(e.valor_apresentado) AS bruta, COUNT(*) AS eventos
            {_FROM}
            WHERE e.tenant_id = :t {filtro}
            GROUP BY e.competencia ORDER BY e.competencia
            """
        ),
        {"t": _t(session), **params},
    ).mappings().all()
    return [dict(r) for r in rows]


def beneficiarios_periodos(
    session: Session, meses0: list[date], meses1: list[date], meses_hist: list[date], esc: Escopo,
) -> list[dict]:
    """Por beneficiário do escopo: despesa líquida na base, no atual e no histórico anterior
    à base (`meses_hist`), e nº de competências com uso no histórico. Base das coortes
    COMPORTAMENTAIS — nenhuma informação clínica é lida."""
    filtro, params = esc.filtro_eventos()
    todos = sorted(set(meses0) | set(meses1) | set(meses_hist))
    rows = session.execute(
        text(
            f"""
            SELECT e.id_beneficiario AS id, b.codigo,
                   COALESCE(SUM(e.valor_apresentado - e.valor_glosado - e.valor_coparticipacao)
                            FILTER (WHERE e.competencia = ANY(:m0)), 0) AS liquida_base,
                   COALESCE(SUM(e.valor_apresentado - e.valor_glosado - e.valor_coparticipacao)
                            FILTER (WHERE e.competencia = ANY(:m1)), 0) AS liquida_atual,
                   COUNT(*) FILTER (WHERE e.competencia = ANY(:m0)) AS eventos_base,
                   COUNT(*) FILTER (WHERE e.competencia = ANY(:m1)) AS eventos_atual,
                   COALESCE(SUM(e.valor_apresentado - e.valor_glosado - e.valor_coparticipacao)
                            FILTER (WHERE e.competencia = ANY(:mh)), 0) AS liquida_hist,
                   COUNT(DISTINCT e.competencia) FILTER (WHERE e.competencia = ANY(:mh))
                       AS meses_uso_hist
            {_FROM}
            WHERE e.tenant_id = :t AND e.competencia = ANY(:todos) {filtro}
            GROUP BY e.id_beneficiario, b.codigo
            """
        ),
        {"t": _t(session), "m0": meses0, "m1": meses1, "mh": meses_hist, "todos": todos,
         **params},
    ).mappings().all()
    return [dict(r) for r in rows]


def unidades_por_tipo(session: Session, meses: list[date], esc: Escopo) -> dict[str, dict]:
    """Volume por tipo de atendimento — cada tipo com a SUA unidade (não se somam)."""
    filtro, params = esc.filtro_eventos()
    rows = session.execute(
        text(
            f"""
            SELECT e.tipo_atendimento AS tipo, COUNT(*) AS eventos,
                   SUM(e.quantidade) AS quantidade, SUM(e.valor_apresentado) AS bruta,
                   COUNT(DISTINCT e.id_beneficiario) AS beneficiarios
            {_FROM}
            WHERE e.tenant_id = :t AND e.competencia = ANY(:meses) {filtro}
            GROUP BY e.tipo_atendimento
            """
        ),
        {"t": _t(session), "meses": meses, **params},
    ).mappings().all()
    return {r["tipo"]: {k: float(v) for k, v in dict(r).items() if k != "tipo"} for r in rows}


def rotulos_procedimentos(session: Session, ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    rows = session.execute(
        text("SELECT id, codigo, descricao, grupo_procedimento, tipo_atendimento_tipico "
             "FROM procedimentos WHERE tenant_id = :t AND id = ANY(:ids)"),
        {"t": _t(session), "ids": ids},
    ).mappings().all()
    return {int(r["id"]): dict(r) for r in rows}


def rotulos_prestadores(session: Session, ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    rows = session.execute(
        text("SELECT id, codigo, nome_ficticio AS nome, tipo_prestador AS tipo "
             "FROM prestadores WHERE tenant_id = :t AND id = ANY(:ids)"),
        {"t": _t(session), "ids": ids},
    ).mappings().all()
    return {int(r["id"]): dict(r) for r in rows}


def rotulo_contrato(session: Session, id_contrato: int) -> str | None:
    return session.execute(
        text("SELECT nome FROM contratos WHERE tenant_id = :t AND id = :c"),
        {"t": _t(session), "c": id_contrato},
    ).scalar_one_or_none()


def rotulo_empresa(session: Session, id_empresa: int) -> str | None:
    return session.execute(
        text("SELECT nome FROM empresas WHERE tenant_id = :t AND id = :e"),
        {"t": _t(session), "e": id_empresa},
    ).scalar_one_or_none()


# ---- receita por escopo (Consolidação V1) — (valor, motivo de indisponibilidade)
def receita_operadora_janela(session: Session, meses: list[date]) -> tuple[float | None, str | None]:
    rows = session.execute(text(
        "SELECT competencia, receita, receita_status FROM agg_sinistralidade_competencia "
        "WHERE tenant_id = :t AND competencia = ANY(:m)"), {"t": _t(session), "m": meses}).all()
    if len(rows) < len(meses) or any(float(r[1] or 0) <= 0 for r in rows):
        return None, "competência sem receita"
    if any(r[2] == "divergente" for r in rows):
        return None, "receita divergente entre fontes"
    return sum(float(r[1]) for r in rows), None


def receita_contrato_janela(session: Session, meses: list[date], id_contrato: int
                            ) -> tuple[float | None, str | None]:
    rows = session.execute(text(
        "SELECT competencia, receita_reconhecida, sinistralidade_liquida, motivo_sem_sinistralidade "
        "FROM agg_contrato_competencia WHERE tenant_id = :t AND id_contrato = :c "
        "AND competencia = ANY(:m)"), {"t": _t(session), "c": id_contrato, "m": meses}).all()
    if len(rows) < len(meses):
        return None, "contrato sem dados em todas as competências"
    sem = [r for r in rows if r[2] is None]
    if sem:
        return None, (sem[0][3] or "sem_receita_atribuida").replace("_", " ")
    return sum(float(r[1]) for r in rows), None


def receita_empresa_janela(session: Session, meses: list[date], id_empresa: int
                           ) -> tuple[float | None, str | None]:
    """Receita dos contratos da empresa (atribuída) + consolidada da empresa. Indisponível se
    algum contrato da empresa ficou sem receita e não há receita consolidada que o cubra."""
    t = _t(session)
    status = session.execute(text(
        "SELECT bool_or(receita_status = 'divergente') FROM agg_sinistralidade_competencia "
        "WHERE tenant_id = :t AND competencia = ANY(:m)"), {"t": t, "m": meses}).scalar()
    if status:
        return None, "receita divergente entre fontes"
    ctr = session.execute(text(
        "SELECT ac.competencia, ac.receita_reconhecida FROM agg_contrato_competencia ac "
        "JOIN empresas_contratos ec ON ec.tenant_id = ac.tenant_id AND ec.id_contrato = ac.id_contrato "
        " AND ec.id_empresa = :e AND ec.papel = 'contratante' AND ec.vigencia_inicio <= ac.competencia "
        " AND (ec.vigencia_fim IS NULL OR ec.vigencia_fim > ac.competencia) "
        "WHERE ac.tenant_id = :t AND ac.competencia = ANY(:m)"), {"t": t, "e": id_empresa, "m": meses}).all()
    emp = {r[0]: float(r[1]) for r in session.execute(text(
        "SELECT competencia, SUM(receita_contraprestacao) FROM receitas_reconhecidas "
        "WHERE tenant_id = :t AND id_empresa = :e AND nivel_atribuicao = 'empresa' AND NOT estimada "
        "AND competencia = ANY(:m) GROUP BY competencia"), {"t": t, "e": id_empresa, "m": meses}).all()}
    if any(r[1] is None and emp.get(r[0], 0) <= 0 for r in ctr):
        return None, "há contratos da empresa sem receita identificada"
    total = sum(float(r[1] or 0) for r in ctr) + sum(emp.values())
    return (total, None) if total > 0 else (None, "sem receita da empresa no período")

