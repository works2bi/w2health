"""Consultas da camada analítica. Retornam estruturas Python simples (dicts/listas).

Fundação SaaS V1 — **isolamento por tenant na aplicação (fail-closed)**:
toda função lê o tenant amarrado à sessão com `_t(session)` (levanta
`TenantContextMissing` se ausente) e filtra a tabela raiz por `tenant_id = :t`. Buscas por
id (prestador, beneficiário, contrato, código) também filtram tenant: um id de outro tenant
é indistinguível de inexistente (sem IDOR). Esta é a 1ª camada; a 2ª é o RLS do
PostgreSQL (`app.tenant_id`), que também cobre as tabelas de JOIN. Nenhuma fórmula muda.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.tenant_scope import tenant_of as _t


def competencias(session: Session) -> list[date]:
    rows = session.execute(
        text("SELECT competencia FROM agg_sinistralidade_competencia WHERE tenant_id = :t "
             "ORDER BY competencia"),
        {"t": _t(session)},
    ).scalars().all()
    return list(rows)


# Colunas comuns às consultas de sinistralidade mensal. `despesa`/`sinistralidade` são
# ALIASES de despesa_liquida/sinistralidade_liquida — a convenção oficial do MVP para o
# KPI principal (ver docs/DATA_MODEL.md). Nenhuma coluna redundante é criada: o alias
# só existe na consulta, não no banco.
_COLUNAS_SINISTRALIDADE = """
    competencia, receita,
    despesa_liquida AS despesa, sinistralidade_liquida AS sinistralidade,
    despesa_bruta, glosas, coparticipacao, despesa_liquida,
    sinistralidade_bruta, sinistralidade_liquida,
    beneficiarios_ativos, exposicao_beneficiario_mes, eventos, custo_pmpm,
    receita_media_beneficiario,
    fonte_receita, receita_status, receita_legada, receita_atribuida_contrato,
    receita_empresa_nao_detalhada, receita_nao_atribuida, cobertura_receita_contratual
"""


def serie_sinistralidade(session: Session) -> list[dict]:
    rows = session.execute(
        text(
            f"SELECT {_COLUNAS_SINISTRALIDADE} FROM agg_sinistralidade_competencia "
            "WHERE tenant_id = :t ORDER BY competencia"
        ),
        {"t": _t(session)},
    ).mappings().all()
    return [dict(r) for r in rows]


def sinistralidade_mes(session: Session, competencia: date) -> dict | None:
    r = session.execute(
        text(
            f"SELECT {_COLUNAS_SINISTRALIDADE} FROM agg_sinistralidade_competencia "
            "WHERE tenant_id = :t AND competencia = :c"
        ),
        {"t": _t(session), "c": competencia},
    ).mappings().first()
    return dict(r) if r else None


_COLS_DIMENSAO = (
    "chave, rotulo, despesa, despesa_bruta, glosas, coparticipacao, despesa_liquida, "
    "eventos, quantidade, beneficiarios, custo_medio, freq_por_mil"
)


def dimensao_mes(session: Session, competencia: date, dimensao: str) -> dict[str, dict]:
    """chave -> métricas do agregado (competência x dimensão). Inclui a composição
    financeira (bruta/glosas/coparticipação/líquida) desde a v1.2."""
    rows = session.execute(
        text(
            f"""
            SELECT {_COLS_DIMENSAO}
            FROM agg_competencia_dimensao
            WHERE tenant_id = :t AND competencia = :c AND dimensao = :d
            """
        ),
        {"t": _t(session), "c": competencia, "d": dimensao},
    ).mappings().all()
    return {r["chave"]: dict(r) for r in rows}


def dimensao_serie(session: Session, dimensao: str, chave: str) -> list[dict]:
    rows = session.execute(
        text(
            f"""
            SELECT competencia, {_COLS_DIMENSAO}
            FROM agg_competencia_dimensao
            WHERE tenant_id = :t AND dimensao = :d AND chave = :k ORDER BY competencia
            """
        ),
        {"t": _t(session), "d": dimensao, "k": chave},
    ).mappings().all()
    return [dict(r) for r in rows]


def prestadores_mes(session: Session, competencia: date) -> list[dict]:
    rows = session.execute(
        text(
            """
            SELECT a.id_prestador, p.nome_ficticio AS nome, p.tipo_prestador AS tipo,
                   r.cidade || '/' || r.uf AS regiao, e.nome AS especialidade_principal,
                   a.despesa, a.eventos, a.beneficiarios, a.custo_medio, a.participacao,
                   a.procedimento_top_id, a.procedimento_top_share
            FROM agg_prestador_competencia a
            JOIN prestadores p ON p.id = a.id_prestador
            JOIN regioes r ON r.id = p.id_regiao
            JOIN especialidades e ON e.id = p.id_especialidade_principal
            WHERE a.tenant_id = :t AND a.competencia = :c
            """
        ),
        {"t": _t(session), "c": competencia},
    ).mappings().all()
    return [dict(r) for r in rows]


def prestador_serie(session: Session, id_prestador: int) -> list[dict]:
    rows = session.execute(
        text(
            """
            SELECT competencia, despesa, eventos, beneficiarios, custo_medio, participacao,
                   procedimento_top_id, procedimento_top_share
            FROM agg_prestador_competencia
            WHERE tenant_id = :t AND id_prestador = :p ORDER BY competencia
            """
        ),
        {"t": _t(session), "p": id_prestador},
    ).mappings().all()
    return [dict(r) for r in rows]


def prestador_info(session: Session, id_prestador: int) -> dict | None:
    r = session.execute(
        text(
            """
            SELECT p.id, p.nome_ficticio AS nome, p.tipo_prestador AS tipo,
                   p.nivel_preco, r.cidade || '/' || r.uf AS regiao,
                   e.id AS id_especialidade_principal, e.nome AS especialidade_principal
            FROM prestadores p
            JOIN regioes r ON r.id = p.id_regiao
            JOIN especialidades e ON e.id = p.id_especialidade_principal
            WHERE p.tenant_id = :t AND p.id = :p
            """
        ),
        {"t": _t(session), "p": id_prestador},
    ).mappings().first()
    return dict(r) if r else None


def prestador_top_procedimentos(
    session: Session, id_prestador: int, competencia: date, limit: int = 8
) -> list[dict]:
    rows = session.execute(
        text(
            """
            SELECT pr.id, pr.descricao, pr.grupo_procedimento,
                   COUNT(*) AS eventos, SUM(e.valor_pago) AS despesa,
                   AVG(e.valor_pago) AS custo_medio
            FROM eventos_assistenciais e
            JOIN procedimentos pr ON pr.id = e.id_procedimento
            WHERE e.tenant_id = :t AND e.id_prestador = :p AND e.competencia = :c
            GROUP BY pr.id, pr.descricao, pr.grupo_procedimento
            ORDER BY despesa DESC LIMIT :lim
            """
        ),
        {"t": _t(session), "p": id_prestador, "c": competencia, "lim": limit},
    ).mappings().all()
    return [dict(r) for r in rows]


def prestador_peers_mes(
    session: Session, competencia: date, id_especialidade_principal: int
) -> list[dict]:
    """Métricas do mês para todos os prestadores com a mesma especialidade principal."""
    rows = session.execute(
        text(
            """
            SELECT a.id_prestador, p.tipo_prestador AS tipo,
                   a.despesa, a.eventos, a.beneficiarios, a.custo_medio,
                   a.procedimento_top_share
            FROM agg_prestador_competencia a
            JOIN prestadores p ON p.id = a.id_prestador
            WHERE a.tenant_id = :t AND a.competencia = :c AND p.id_especialidade_principal = :e
            """
        ),
        {"t": _t(session), "c": competencia, "e": id_especialidade_principal},
    ).mappings().all()
    return [dict(r) for r in rows]


def custo_procedimentos_por_prestador(
    session: Session, competencia: date, procedimentos: list[int]
) -> list[dict]:
    """Custo médio (Σ valor_pago / eventos) de CADA prestador nos procedimentos informados,
    no mês — base da comparação com pares do MESMO procedimento e MESMO tipo (Evolução V1)."""
    if not procedimentos:
        return []
    rows = session.execute(
        text(
            """
            SELECT e.id_procedimento, e.id_prestador, p.tipo_prestador AS tipo,
                   COUNT(*) AS eventos, SUM(e.valor_pago) AS despesa
            FROM eventos_assistenciais e
            JOIN prestadores p ON p.id = e.id_prestador
            WHERE e.tenant_id = :t AND e.competencia = :c AND e.id_procedimento = ANY(:procs)
            GROUP BY e.id_procedimento, e.id_prestador, p.tipo_prestador
            """
        ),
        {"t": _t(session), "c": competencia, "procs": procedimentos},
    ).mappings().all()
    return [dict(r) for r in rows]


def beneficiario_serie(session: Session, id_beneficiario: int) -> list[dict]:
    rows = session.execute(
        text(
            """
            SELECT competencia, despesa, despesa_liquida, eventos
            FROM agg_beneficiario_competencia
            WHERE tenant_id = :t AND id_beneficiario = :b ORDER BY competencia
            """
        ),
        {"t": _t(session), "b": id_beneficiario},
    ).mappings().all()
    return [dict(r) for r in rows]


def beneficiarios_top(
    session: Session, competencia: date, limit: int, offset: int,
    faixa_etaria: str | None = None, sexo: str | None = None, id_plano: int | None = None,
    id_contrato: int | None = None,
) -> tuple[list[dict], int]:
    where = ["a.tenant_id = :t", "a.competencia = :c"]
    params: dict = {"t": _t(session), "c": competencia, "lim": limit, "off": offset}
    if faixa_etaria:
        where.append("b.faixa_etaria = :fe")
        params["fe"] = faixa_etaria
    if sexo:
        where.append("b.sexo = :sx")
        params["sx"] = sexo
    if id_plano:
        where.append("b.id_plano = :pl")
        params["pl"] = id_plano
    if id_contrato:
        where.append("b.id_contrato = :ct")
        params["ct"] = id_contrato
    w = " AND ".join(where)
    total = session.execute(
        text(
            f"SELECT COUNT(*) FROM agg_beneficiario_competencia a "
            f"JOIN beneficiarios b ON b.id = a.id_beneficiario WHERE {w}"
        ),
        params,
    ).scalar_one()
    rows = session.execute(
        text(
            f"""
            SELECT b.id, b.codigo, b.sexo, b.faixa_etaria,
                   r.cidade || '/' || r.uf AS regiao, pl.nome AS plano,
                   a.despesa, a.eventos
            FROM agg_beneficiario_competencia a
            JOIN beneficiarios b ON b.id = a.id_beneficiario
            JOIN regioes r ON r.id = b.id_regiao
            JOIN planos pl ON pl.id = b.id_plano
            WHERE {w}
            ORDER BY a.despesa DESC
            LIMIT :lim OFFSET :off
            """
        ),
        params,
    ).mappings().all()
    return [dict(r) for r in rows], int(total)


def beneficiario_info(session: Session, id_beneficiario: int) -> dict | None:
    r = session.execute(
        text(
            """
            SELECT b.id, b.codigo, b.sexo, b.faixa_etaria, b.data_nascimento,
                   b.data_adesao, b.status,
                   r.cidade || '/' || r.uf AS regiao, r.macrorregiao,
                   pl.nome AS plano, ct.nome AS contrato
            FROM beneficiarios b
            JOIN regioes r ON r.id = b.id_regiao
            JOIN planos pl ON pl.id = b.id_plano
            JOIN contratos ct ON ct.id = b.id_contrato
            WHERE b.tenant_id = :t AND b.id = :b
            """
        ),
        {"t": _t(session), "b": id_beneficiario},
    ).mappings().first()
    return dict(r) if r else None


def beneficiario_por_codigo(session: Session, codigo: str) -> int | None:
    return session.execute(
        text("SELECT id FROM beneficiarios WHERE tenant_id = :t AND codigo = :c"),
        {"t": _t(session), "c": codigo},
    ).scalar_one_or_none()


def beneficiario_eventos(session: Session, id_beneficiario: int) -> list[dict]:
    rows = session.execute(
        text(
            """
            SELECT e.id, e.data_evento, e.competencia, e.tipo_atendimento, e.quantidade,
                   e.valor_apresentado, e.valor_glosado, e.valor_pago,
                   p.descricao AS procedimento, p.grupo_procedimento,
                   es.nome AS especialidade, pr.nome_ficticio AS prestador,
                   d.descricao AS diagnostico
            FROM eventos_assistenciais e
            JOIN procedimentos p ON p.id = e.id_procedimento
            JOIN especialidades es ON es.id = e.id_especialidade
            JOIN prestadores pr ON pr.id = e.id_prestador
            LEFT JOIN diagnosticos d ON d.id = e.id_diagnostico
            WHERE e.tenant_id = :t AND e.id_beneficiario = :b
            ORDER BY e.data_evento
            """
        ),
        {"t": _t(session), "b": id_beneficiario},
    ).mappings().all()
    return [dict(r) for r in rows]


def despesa_por_beneficiario(session: Session, competencia: date) -> list[float]:
    rows = session.execute(
        text(
            "SELECT despesa FROM agg_beneficiario_competencia "
            "WHERE tenant_id = :t AND competencia = :c"
        ),
        {"t": _t(session), "c": competencia},
    ).scalars().all()
    return [float(x) for x in rows]


# Cláusula WHERE (parametrizada) para filtrar eventos por dimensão/chave. Compartilhada
# por `eventos_da_categoria` e pelas consultas de coortes (Etapa A da v1.1).
_FILTRO_DIMENSAO: dict[str, str] = {
    "especialidade": "e.id_especialidade = :kv",
    "procedimento": "e.id_procedimento = :kv",
    "prestador": "e.id_prestador = :kv",
    "regiao": "e.id_regiao = :kv",
    "tipo_atendimento": "e.tipo_atendimento = :kt",
    "grupo_despesa": "proc.grupo_procedimento = :kt",
    "faixa_etaria": "b.faixa_etaria = :kt",
    "sexo": "b.sexo = :kt",
    # vínculo da competência gravado no EVENTO (Consolidação V1) — nunca o cadastro atual
    "plano": "e.id_plano = :kv",
    "contrato": "e.id_contrato = :kv",
}

#: chave da dimensão para eventos sem vínculo vigente (contrato/plano)
NAO_ATRIBUIDO = "nao_atribuido"


def _filtro_dimensao(dimensao: str, chave: str) -> tuple[str, dict]:
    """Retorna (cláusula SQL, params) para filtrar eventos por (dimensao, chave)."""
    if dimensao in ("contrato", "plano") and chave == NAO_ATRIBUIDO:
        return f"e.id_{dimensao} IS NULL", {}
    clausula = _FILTRO_DIMENSAO[dimensao]
    params = {"kv": int(chave)} if ":kv" in clausula else {"kt": chave}
    return clausula, params


def eventos_da_categoria(
    session: Session, competencia: date, dimensao: str, chave: str,
    agrupar_por: str = "prestador", limit: int = 10,
) -> list[dict]:
    """Top `agrupar_por` (prestador|beneficiario) dentro de uma célula dimensão/chave/mês.

    Usado no drill-down "onde investigar primeiro".
    """
    filtro_dim, fparams = _filtro_dimensao(dimensao, chave)
    params: dict = {"t": _t(session), "c": competencia, "lim": limit, **fparams}

    if agrupar_por == "beneficiario":
        sel = "b.id AS id, b.codigo AS rotulo"
        grp = "b.id, b.codigo"
    else:
        sel = "prov.id AS id, prov.nome_ficticio AS rotulo"
        grp = "prov.id, prov.nome_ficticio"

    rows = session.execute(
        text(
            f"""
            SELECT {sel}, COUNT(*) AS eventos, SUM(e.valor_pago) AS despesa,
                   AVG(e.valor_pago) AS custo_medio
            FROM eventos_assistenciais e
            JOIN procedimentos proc ON proc.id = e.id_procedimento
            JOIN prestadores prov ON prov.id = e.id_prestador
            JOIN beneficiarios b ON b.id = e.id_beneficiario
            WHERE e.tenant_id = :t AND e.competencia = :c AND {filtro_dim}
            GROUP BY {grp}
            ORDER BY despesa DESC
            LIMIT :lim
            """
        ),
        params,
    ).mappings().all()
    return [dict(r) for r in rows]


def beneficiarios_da_categoria(
    session: Session, competencia: date, dimensao: str, chave: str,
    contrato_id: int | None = None,
) -> dict[int, dict]:
    """id_beneficiario -> {despesa, eventos} para TODOS os beneficiários de uma célula
    dimensão/chave/mês (sem limite — base da análise de coortes, Etapa A).
    `contrato_id` (v1.2) restringe ao contrato."""
    filtro_dim, fparams = _filtro_dimensao(dimensao, chave)
    filtro_ctr = "AND e.id_contrato = :ctr" if contrato_id is not None else ""
    if contrato_id is not None:
        fparams["ctr"] = contrato_id
    rows = session.execute(
        text(
            f"""
            SELECT e.id_beneficiario AS id, SUM(e.valor_pago) AS despesa, COUNT(*) AS eventos
            FROM eventos_assistenciais e
            JOIN procedimentos proc ON proc.id = e.id_procedimento
            JOIN beneficiarios b ON b.id = e.id_beneficiario
            WHERE e.tenant_id = :t AND e.competencia = :c AND {filtro_dim} {filtro_ctr}
            GROUP BY e.id_beneficiario
            """
        ),
        {"t": _t(session), "c": competencia, **fparams},
    ).mappings().all()
    return {
        int(r["id"]): {"despesa": float(r["despesa"]), "eventos": int(r["eventos"])} for r in rows
    }


def beneficiarios_status_bulk(session: Session, ids: list[int]) -> dict[int, dict]:
    """Metadados de carteira em lote — status, saída, adesão, perfil demográfico."""
    if not ids:
        return {}
    rows = session.execute(
        text(
            """
            SELECT id, codigo, status, data_saida, data_adesao, faixa_etaria, sexo, id_contrato
            FROM beneficiarios WHERE tenant_id = :t AND id = ANY(:ids)
            """
        ),
        {"t": _t(session), "ids": ids},
    ).mappings().all()
    return {int(r["id"]): dict(r) for r in rows}


def prestadores_por_beneficiario_na_categoria(
    session: Session, competencia: date, dimensao: str, chave: str, ids: list[int],
    contrato_id: int | None = None,
) -> dict[int, set[int]]:
    """id_beneficiario -> conjunto de prestadores usados, dentro da célula/mês (para
    detectar troca de prestador entre dois meses)."""
    if not ids:
        return {}
    filtro_dim, fparams = _filtro_dimensao(dimensao, chave)
    filtro_ctr = "AND e.id_contrato = :ctr" if contrato_id is not None else ""
    if contrato_id is not None:
        fparams["ctr"] = contrato_id
    rows = session.execute(
        text(
            f"""
            SELECT DISTINCT e.id_beneficiario AS id, e.id_prestador AS id_prestador
            FROM eventos_assistenciais e
            JOIN procedimentos proc ON proc.id = e.id_procedimento
            JOIN beneficiarios b ON b.id = e.id_beneficiario
            WHERE e.tenant_id = :t AND e.competencia = :c AND {filtro_dim} {filtro_ctr}
                  AND e.id_beneficiario = ANY(:ids)
            """
        ),
        {"t": _t(session), "c": competencia, "ids": ids, **fparams},
    ).mappings().all()
    out: dict[int, set[int]] = {}
    for r in rows:
        out.setdefault(int(r["id"]), set()).add(int(r["id_prestador"]))
    return out


def perfil_utilizacao_despesa(
    session: Session, competencia: date, dimensao: str, chave: str, ids: list[int],
    contrato_id: int | None = None,
) -> dict[str, float]:
    """Despesa (na célula/mês, restrita a `ids`) somada por perfil_utilizacao do
    procedimento — 'pontual' | 'recorrente' | 'variavel'. Apoia a hipótese de conclusão
    de episódio pontual (nunca usada sozinha para afirmar causalidade)."""
    if not ids:
        return {}
    filtro_dim, fparams = _filtro_dimensao(dimensao, chave)
    filtro_ctr = "AND e.id_contrato = :ctr" if contrato_id is not None else ""
    if contrato_id is not None:
        fparams["ctr"] = contrato_id
    rows = session.execute(
        text(
            f"""
            SELECT proc.perfil_utilizacao AS perfil, SUM(e.valor_pago) AS despesa
            FROM eventos_assistenciais e
            JOIN procedimentos proc ON proc.id = e.id_procedimento
            JOIN beneficiarios b ON b.id = e.id_beneficiario
            WHERE e.tenant_id = :t AND e.competencia = :c AND {filtro_dim} {filtro_ctr}
                  AND e.id_beneficiario = ANY(:ids)
            GROUP BY proc.perfil_utilizacao
            """
        ),
        {"t": _t(session), "c": competencia, "ids": ids, **fparams},
    ).mappings().all()
    return {r["perfil"]: float(r["despesa"]) for r in rows}


def procedimentos_mes_detalhe(session: Session, competencia: date) -> list[dict]:
    """Por procedimento no mês: eventos, despesa, custo médio + chaves de agrupamento
    (especialidade, grupo). Base da decomposição correta de fatores coesos."""
    rows = session.execute(
        text(
            """
            SELECT e.id_procedimento AS id,
                   pr.id_especialidade::text AS especialidade,
                   pr.grupo_procedimento AS grupo_despesa,
                   pr.descricao AS rotulo,
                   COUNT(*) AS eventos,
                   SUM(e.valor_pago) AS despesa
            FROM eventos_assistenciais e
            JOIN procedimentos pr ON pr.id = e.id_procedimento
            WHERE e.tenant_id = :t AND e.competencia = :c
            GROUP BY e.id_procedimento, pr.id_especialidade, pr.grupo_procedimento, pr.descricao
            """
        ),
        {"t": _t(session), "c": competencia},
    ).mappings().all()
    return [dict(r) for r in rows]


def beneficiarios_despesa_mes(session: Session, competencia: date) -> dict[int, dict]:
    """id_beneficiario -> {despesa, eventos, codigo} para TODOS os beneficiários no mês.

    Base para os indicadores de alerta de beneficiário (Etapa C da v1.1).
    """
    rows = session.execute(
        text(
            """
            SELECT a.id_beneficiario AS id, a.despesa, a.eventos, b.codigo
            FROM agg_beneficiario_competencia a
            JOIN beneficiarios b ON b.id = a.id_beneficiario
            WHERE a.tenant_id = :t AND a.competencia = :c
            """
        ),
        {"t": _t(session), "c": competencia},
    ).mappings().all()
    return {
        int(r["id"]): {
            "despesa": float(r["despesa"]), "eventos": r["eventos"], "codigo": r["codigo"],
        }
        for r in rows
    }


def planos_sinistralidade_mes(session: Session, competencia: date) -> list[dict]:
    """Sinistralidade por plano (receita já é nativamente por competência × plano)."""
    rows = session.execute(
        text(
            """
            SELECT pl.id, pl.nome AS rotulo,
                   COALESCE(r.receita, 0) AS receita,
                   COALESCE(d.despesa, 0) AS despesa,
                   COALESCE(v.vidas, 0) AS vidas
            FROM planos pl
            LEFT JOIN receitas r
                   ON r.id_plano = pl.id AND r.competencia = :c AND r.tenant_id = :t
            LEFT JOIN (
                SELECT e.id_plano AS id_plano, SUM(e.valor_pago) AS despesa
                FROM eventos_assistenciais e JOIN beneficiarios b ON b.id = e.id_beneficiario
                WHERE e.tenant_id = :t AND e.competencia = :c
                GROUP BY e.id_plano
            ) d ON d.id_plano = pl.id
            LEFT JOIN (
                SELECT id_plano, COUNT(*) AS vidas FROM beneficiarios
                WHERE tenant_id = :t AND status = 'ativo' GROUP BY id_plano
            ) v ON v.id_plano = pl.id
            WHERE pl.tenant_id = :t
            """
        ),
        {"t": _t(session), "c": competencia},
    ).mappings().all()
    out = []
    for r in rows:
        receita = float(r["receita"])
        despesa = float(r["despesa"])
        sin = despesa / receita * 100 if receita > 0 else 0.0
        out.append({"id": r["id"], "rotulo": r["rotulo"], "receita": receita,
                     "despesa": despesa, "sinistralidade": sin, "vidas": r["vidas"]})
    return out


def contratos_vidas_mes(session: Session) -> dict[int, dict]:
    """id_contrato -> {rotulo, vidas ativas}. Sem receita própria (ver docs/V1.1.md)."""
    rows = session.execute(
        text(
            """
            SELECT ct.id, ct.nome AS rotulo, COUNT(b.id) AS vidas
            FROM contratos ct
            LEFT JOIN beneficiarios b
                   ON b.id_contrato = ct.id AND b.status = 'ativo' AND b.tenant_id = :t
            WHERE ct.tenant_id = :t
            GROUP BY ct.id, ct.nome
            """
        ),
        {"t": _t(session)},
    ).mappings().all()
    return {int(r["id"]): {"rotulo": r["rotulo"], "vidas": r["vidas"]} for r in rows}


def gabarito(session: Session) -> list[dict]:
    rows = session.execute(
        text(
            """
            SELECT codigo, nome, competencia_alvo, dimensao, chave_alvo, rotulo_alvo,
                   efeito_esperado, descricao, params
            FROM cenarios_gabarito WHERE tenant_id = :t ORDER BY codigo
            """
        ),
        {"t": _t(session)},
    ).mappings().all()
    return [dict(r) for r in rows]


# =====================================================================================
# v1.2 — escopo de contrato, composição por dimensão, concentração da variação
# =====================================================================================

# dimensão -> (join extra sobre "eventos_assistenciais e" + "beneficiarios b", expr da chave,
# expr do rótulo). `b` já está no FROM (necessário para o filtro de contrato).
_DIM_ESCOPO_CONTRATO: dict[str, tuple[str, str, str]] = {
    "grupo_despesa": ("JOIN procedimentos pr ON pr.id = e.id_procedimento",
                      "pr.grupo_procedimento", "pr.grupo_procedimento"),
    "tipo_atendimento": ("", "e.tipo_atendimento", "e.tipo_atendimento"),
    "especialidade": ("JOIN especialidades es ON es.id = e.id_especialidade",
                      "es.id::text", "es.nome"),
    "procedimento": ("JOIN procedimentos pr ON pr.id = e.id_procedimento",
                     "pr.id::text", "pr.descricao"),
    "prestador": ("JOIN prestadores p ON p.id = e.id_prestador",
                  "p.id::text", "p.nome_ficticio"),
    "regiao": ("JOIN regioes r ON r.id = e.id_regiao",
               "r.id::text", "concat(r.cidade, '/', r.uf)"),
    "faixa_etaria": ("", "b.faixa_etaria", "b.faixa_etaria"),
    "sexo": ("", "b.sexo", "b.sexo"),
    "plano": ("LEFT JOIN planos pl ON pl.id = e.id_plano",
              "COALESCE(pl.id::text, 'nao_atribuido')", "COALESCE(pl.nome, 'Não atribuído')"),
}


#: contrato × plano é N:N — o rótulo "plano" do contrato é a lista dos planos que ele oferece
_PLANOS_DO_CONTRATO = """
    SELECT pc.id_contrato, string_agg(pl.nome, ', ' ORDER BY pl.nome) AS plano
    FROM planos_contratos pc JOIN planos pl ON pl.id = pc.id_plano
    WHERE pc.tenant_id = :t
    GROUP BY pc.id_contrato
"""


def contrato_info(session: Session, id_contrato: int) -> dict | None:
    r = session.execute(
        text(
            """
            SELECT ct.id, ct.nome, ct.tipo, ct.vidas_alvo, pc.plano
            FROM contratos ct
            LEFT JOIN (""" + _PLANOS_DO_CONTRATO + """) pc ON pc.id_contrato = ct.id
            WHERE ct.tenant_id = :t AND ct.id = :c
            """
        ),
        {"t": _t(session), "c": id_contrato},
    ).mappings().first()
    return dict(r) if r else None


def contrato_competencia(session: Session, id_contrato: int, competencia: date) -> dict | None:
    r = session.execute(
        text(
            """
            SELECT competencia, vidas, despesa, despesa_bruta, glosas, coparticipacao,
                   despesa_liquida, eventos, beneficiarios_com_evento, custo_pmpm,
                   gini, top5_share, n_beneficiarios_alto_custo,
                   receita_reconhecida, sinistralidade_liquida, motivo_sem_sinistralidade
            FROM agg_contrato_competencia
            WHERE tenant_id = :t AND id_contrato = :c AND competencia = :m
            """
        ),
        {"t": _t(session), "c": id_contrato, "m": competencia},
    ).mappings().first()
    return dict(r) if r else None


def contrato_serie(session: Session, id_contrato: int) -> list[dict]:
    rows = session.execute(
        text(
            """
            SELECT competencia, vidas, despesa, despesa_bruta, glosas, coparticipacao,
                   despesa_liquida, eventos, custo_pmpm, gini, top5_share,
                   n_beneficiarios_alto_custo,
                   receita_reconhecida, sinistralidade_liquida, motivo_sem_sinistralidade
            FROM agg_contrato_competencia
            WHERE tenant_id = :t AND id_contrato = :c ORDER BY competencia
            """
        ),
        {"t": _t(session), "c": id_contrato},
    ).mappings().all()
    return [dict(r) for r in rows]


def contratos_resumo_mes(session: Session, competencia: date) -> list[dict]:
    rows = session.execute(
        text(
            """
            SELECT ac.id_contrato, ct.nome, ct.tipo, pc.plano,
                   ac.vidas, ac.despesa, ac.despesa_bruta, ac.glosas, ac.coparticipacao,
                   ac.despesa_liquida, ac.eventos, ac.custo_pmpm, ac.gini, ac.top5_share,
                   ac.n_beneficiarios_alto_custo,
                   ac.receita_reconhecida, ac.sinistralidade_liquida, ac.motivo_sem_sinistralidade,
                   em.id AS id_empresa, em.nome AS empresa
            FROM agg_contrato_competencia ac
            JOIN contratos ct ON ct.id = ac.id_contrato
            LEFT JOIN (""" + _PLANOS_DO_CONTRATO + """) pc ON pc.id_contrato = ct.id
            LEFT JOIN empresas_contratos ec
                   ON ec.tenant_id = :t AND ec.id_contrato = ct.id AND ec.papel = 'contratante'
                  AND ec.vigencia_inicio <= :m AND (ec.vigencia_fim IS NULL OR ec.vigencia_fim > :m)
            LEFT JOIN empresas em ON em.id = ec.id_empresa
            WHERE ac.tenant_id = :t AND ac.competencia = :m
            ORDER BY ac.despesa_liquida DESC
            """
        ),
        {"t": _t(session), "m": competencia},
    ).mappings().all()
    return [dict(r) for r in rows]


def sinistralidade_por_contrato_mes(
    session: Session, competencia: date, id_contrato: int
) -> dict | None:
    """Shape compatível com `sinistralidade_mes`, porém SEM receita/sinistralidade
    (contrato não tem receita própria na v1.2). `despesa` = Σ valor_pago do contrato."""
    r = contrato_competencia(session, id_contrato, competencia)
    if r is None:
        return None
    return {
        "competencia": competencia,
        "receita": None,
        "despesa": r["despesa"],
        "sinistralidade": None,
        "despesa_bruta": r["despesa_bruta"],
        "glosas": r["glosas"],
        "coparticipacao": r["coparticipacao"],
        "despesa_liquida": r["despesa_liquida"],
        "sinistralidade_bruta": None,
        "sinistralidade_liquida": None,
        "beneficiarios_ativos": r["vidas"],
        "exposicao_beneficiario_mes": r["vidas"],
        "eventos": r["eventos"],
        "custo_pmpm": r["custo_pmpm"],
        "receita_media_beneficiario": None,
    }


def dimensao_mes_por_contrato(
    session: Session, competencia: date, dimensao: str, id_contrato: int
) -> dict[str, dict]:
    """Igual a `dimensao_mes`, calculado sob demanda a partir de `eventos_assistenciais`
    filtrado por `beneficiarios.id_contrato` (v1.2). `despesa` = Σ valor_pago."""
    if dimensao == "contrato":
        return {}
    joins, chave_expr, rotulo_expr = _DIM_ESCOPO_CONTRATO[dimensao]
    rows = session.execute(
        text(
            f"""
            SELECT {chave_expr} AS chave, MIN({rotulo_expr}) AS rotulo,
                   SUM(e.valor_pago) AS despesa,
                   SUM(e.valor_apresentado) AS despesa_bruta,
                   SUM(e.valor_glosado) AS glosas,
                   SUM(e.valor_coparticipacao) AS coparticipacao,
                   SUM(e.valor_apresentado - e.valor_glosado - e.valor_coparticipacao) AS despesa_liquida,
                   COUNT(*) AS eventos, SUM(e.quantidade) AS quantidade,
                   COUNT(DISTINCT e.id_beneficiario) AS beneficiarios,
                   CASE WHEN COUNT(*) > 0 THEN SUM(e.valor_pago) / COUNT(*) ELSE 0 END AS custo_medio,
                   0.0 AS freq_por_mil
            FROM eventos_assistenciais e
            JOIN beneficiarios b ON b.id = e.id_beneficiario
            {joins}
            WHERE e.tenant_id = :t AND e.competencia = :m
                  AND e.id_contrato = :c
            GROUP BY {chave_expr}
            """
        ),
        {"t": _t(session), "m": competencia, "c": id_contrato},
    ).mappings().all()
    return {r["chave"]: dict(r) for r in rows}


def procedimentos_mes_detalhe_por_contrato(
    session: Session, competencia: date, id_contrato: int
) -> list[dict]:
    rows = session.execute(
        text(
            """
            SELECT e.id_procedimento AS id,
                   pr.id_especialidade::text AS especialidade,
                   pr.grupo_procedimento AS grupo_despesa,
                   pr.descricao AS rotulo,
                   COUNT(*) AS eventos,
                   SUM(e.valor_pago) AS despesa
            FROM eventos_assistenciais e
            JOIN beneficiarios b ON b.id = e.id_beneficiario
            JOIN procedimentos pr ON pr.id = e.id_procedimento
            WHERE e.tenant_id = :t AND e.competencia = :m
                  AND e.id_contrato = :c
            GROUP BY e.id_procedimento, pr.id_especialidade, pr.grupo_procedimento, pr.descricao
            """
        ),
        {"t": _t(session), "m": competencia, "c": id_contrato},
    ).mappings().all()
    return [dict(r) for r in rows]


def beneficiarios_delta_mes(
    session: Session, competencia: date, competencia_ant: date, id_contrato: int | None = None
) -> dict[int, dict]:
    """id_beneficiario -> {codigo, id_contrato, atual, anterior, delta} sobre despesa
    LÍQUIDA (agg_beneficiario_competencia). Base da concentração da VARIAÇÃO (C1)."""
    filtro = "AND ac.id_contrato = :ctr" if id_contrato is not None else ""
    params: dict = {"t": _t(session), "m": competencia, "a": competencia_ant}
    if id_contrato is not None:
        params["ctr"] = id_contrato
    rows = session.execute(
        text(
            f"""
            SELECT b.id AS id, b.codigo AS codigo, ac.id_contrato AS id_contrato,
                   COALESCE(SUM(ac.despesa_liquida) FILTER (WHERE ac.competencia = :m), 0) AS atual,
                   COALESCE(SUM(ac.despesa_liquida) FILTER (WHERE ac.competencia = :a), 0) AS anterior
            FROM agg_beneficiario_competencia ac
            JOIN beneficiarios b ON b.id = ac.id_beneficiario
            WHERE ac.tenant_id = :t AND ac.competencia IN (:m, :a) {filtro}
            GROUP BY b.id, b.codigo, ac.id_contrato
            """
        ),
        params,
    ).mappings().all()
    out: dict[int, dict] = {}
    for r in rows:
        atual, anterior = float(r["atual"]), float(r["anterior"])
        out[int(r["id"])] = {
            "codigo": r["codigo"], "id_contrato": r["id_contrato"],
            "atual": atual, "anterior": anterior, "delta": atual - anterior,
        }
    return out


def beneficiarios_liquida_contrato_mes(
    session: Session, competencia: date, id_contrato: int
) -> list[dict]:
    """[{id, codigo, despesa_liquida, eventos}] dos beneficiários do contrato com evento no mês."""
    rows = session.execute(
        text(
            """
            SELECT b.id, b.codigo, ac.despesa_liquida, ac.eventos
            FROM agg_beneficiario_competencia ac
            JOIN beneficiarios b ON b.id = ac.id_beneficiario
            WHERE ac.tenant_id = :t AND ac.competencia = :m AND ac.id_contrato = :c
                  AND ac.despesa_liquida > 0
            ORDER BY ac.despesa_liquida DESC
            """
        ),
        {"t": _t(session), "m": competencia, "c": id_contrato},
    ).mappings().all()
    return [dict(r) for r in rows]


def beneficiario_serie_contrato(session: Session, id_beneficiario: int) -> int | None:
    return session.execute(
        text("SELECT id_contrato FROM beneficiarios WHERE tenant_id = :t AND id = :b"),
        {"t": _t(session), "b": id_beneficiario},
    ).scalar_one_or_none()


def beneficiarios_novo_caso_alto_custo(
    session: Session, competencia: date, meses_baseline: int, limiar: float,
    baseline_frac: float = 0.25, id_contrato: int | None = None,
) -> list[dict]:
    """Beneficiários com despesa líquida >= `limiar` no mês, cuja MAIOR despesa líquida
    mensal nos `meses_baseline` meses anteriores ficou abaixo de `limiar * baseline_frac`.
    Descritivo — nunca um score clínico."""
    filtro = "AND b.id_contrato = :ctr" if id_contrato is not None else ""
    params: dict = {
        "t": _t(session), "m": competencia, "lim": limiar, "base": limiar * baseline_frac,
        "ini": _menos_meses(competencia, meses_baseline),
    }
    if id_contrato is not None:
        params["ctr"] = id_contrato
    rows = session.execute(
        text(
            f"""
            WITH atual AS (
                SELECT ac.id_beneficiario, ac.despesa_liquida
                FROM agg_beneficiario_competencia ac
                WHERE ac.tenant_id = :t AND ac.competencia = :m AND ac.despesa_liquida >= :lim
            ),
            base AS (
                SELECT ac.id_beneficiario, MAX(ac.despesa_liquida) AS max_ant
                FROM agg_beneficiario_competencia ac
                WHERE ac.tenant_id = :t AND ac.competencia < :m AND ac.competencia >= :ini
                GROUP BY ac.id_beneficiario
            )
            SELECT b.id, b.codigo, b.id_contrato, a.despesa_liquida,
                   COALESCE(base.max_ant, 0) AS max_ant
            FROM atual a
            JOIN beneficiarios b ON b.id = a.id_beneficiario
            LEFT JOIN base ON base.id_beneficiario = a.id_beneficiario
            WHERE COALESCE(base.max_ant, 0) < :base {filtro}
            ORDER BY a.despesa_liquida DESC
            """
        ),
        params,
    ).mappings().all()
    return [dict(r) for r in rows]


def _menos_meses(d: date, n: int) -> date:
    """Primeiro dia do mês `n` meses antes de `d` (competências são sempre dia 1)."""
    total = d.year * 12 + (d.month - 1) - n
    return date(total // 12, total % 12 + 1, 1)


def recorrencia_beneficiarios_mes(
    session: Session, competencia: date, janela: int
) -> list[dict]:
    """[{id, codigo, meses_com_evento}] — nº de meses com evento na janela terminando em
    `competencia` (inclusive), por beneficiário. Base do indicador de alerta 'recorrencia'."""
    inicio = _menos_meses(competencia, janela - 1)
    rows = session.execute(
        text(
            """
            SELECT b.id, b.codigo, COUNT(*) AS meses_com_evento
            FROM agg_beneficiario_competencia ac
            JOIN beneficiarios b ON b.id = ac.id_beneficiario
            WHERE ac.tenant_id = :t AND ac.eventos > 0 AND ac.competencia BETWEEN :ini AND :m
            GROUP BY b.id, b.codigo
            """
        ),
        {"t": _t(session), "m": competencia, "ini": inicio},
    ).mappings().all()
    return [dict(r) for r in rows]


def eventos_pontuais_do_beneficiario(session: Session, id_beneficiario: int) -> list[dict]:
    """Eventos de procedimentos de perfil 'pontual' do beneficiário (para C5)."""
    rows = session.execute(
        text(
            """
            SELECT e.competencia, e.data_evento, pr.descricao AS procedimento,
                   pr.grupo_procedimento, pr.perfil_utilizacao,
                   e.valor_apresentado, e.valor_glosado, e.valor_coparticipacao,
                   (e.valor_apresentado - e.valor_glosado - e.valor_coparticipacao) AS despesa_liquida
            FROM eventos_assistenciais e
            JOIN procedimentos pr ON pr.id = e.id_procedimento
            WHERE e.tenant_id = :t AND e.id_beneficiario = :b AND pr.perfil_utilizacao = 'pontual'
            ORDER BY e.data_evento
            """
        ),
        {"t": _t(session), "b": id_beneficiario},
    ).mappings().all()
    return [dict(r) for r in rows]
