"""Reconciliação financeira (Consolidação do canônico V1) — receita, despesa e vidas.

Verificações SOB DEMANDA sobre a Silver e a Gold de um tenant (qualquer origem: pipeline,
gerador sintético). Usadas pela API (`/analytics/receitas/reconciliacao`), pelo pipeline
(reprova a carga em FAIL) e pelos testes. Ver docs/RECONCILIACAO_FINANCEIRA_V1.md.

Status: PASS (diferença ≤ R$ 0,01 / 0 vidas) · WARNING (diferença explicada — ex.: receita
não atribuída) · FAIL (divergência que impede publicar o indicador afetado) · INFO.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.tenant_scope import tenant_of as _t
from app.repositories.investigacao_repo import SQL_EXPOSICAO

PASS, WARNING, FAIL, INFO = "PASS", "WARNING", "FAIL", "INFO"
TOL = 0.01


@dataclass
class Verificacao:
    check_id: str
    escopo: str
    esperado: float
    obtido: float
    status: str
    explicacao: str

    @property
    def diferenca(self) -> float:
        return round(self.obtido - self.esperado, 2)

    def as_dict(self) -> dict:
        return {
            "check_id": self.check_id,
            "escopo": self.escopo,
            "esperado": round(self.esperado, 2),
            "obtido": round(self.obtido, 2),
            "diferenca": self.diferenca,
            "status": self.status,
            "explicacao": self.explicacao,
        }


def _brl(v: float) -> str:
    return "R$ " + f"{v:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _bate(a: float, b: float, tol: float = TOL) -> bool:
    return abs(a - b) <= tol


def _q(session: Session, sql: str, **params) -> list[dict]:
    return [
        dict(r) for r in session.execute(text(sql), {"t": _t(session), **params}).mappings().all()
    ]


def verificar(session: Session, competencias: list[date] | None = None) -> list[Verificacao]:
    """Todas as verificações do tenant da sessão (opcionalmente só nas competências dadas)."""
    filtro = "AND competencia = ANY(:meses)" if competencias else ""
    mp = {"meses": competencias} if competencias else {}
    out: list[Verificacao] = []

    # ---- receita: fonte única por competência, sem duplicidade
    gold = {
        r["competencia"]: r
        for r in _q(
            session,
            f"""
        SELECT competencia, receita, fonte_receita, receita_status, receita_legada,
               receita_atribuida_contrato, receita_empresa_nao_detalhada, receita_nao_atribuida
        FROM agg_sinistralidade_competencia WHERE tenant_id = :t {filtro}""",
            **mp,
        )
    }
    rec = {
        r["competencia"]: r
        for r in _q(
            session,
            f"""
        SELECT competencia, SUM(receita_contraprestacao) AS total,
               SUM(receita_contraprestacao) FILTER (WHERE nivel_atribuicao IN ('contrato_plano','contrato')) AS atrib,
               SUM(receita_contraprestacao) FILTER (WHERE nivel_atribuicao = 'empresa') AS emp,
               SUM(receita_contraprestacao) FILTER (WHERE nivel_atribuicao = 'nao_atribuida') AS na
        FROM receitas_reconhecidas WHERE tenant_id = :t AND NOT estimada {filtro}
        GROUP BY competencia""",
            **mp,
        )
    }
    leg = {
        r["competencia"]: float(r["total"])
        for r in _q(
            session,
            f"""
        SELECT competencia, SUM(receita_contraprestacao) AS total FROM receitas
        WHERE tenant_id = :t {filtro} GROUP BY competencia""",
            **mp,
        )
    }

    for comp, g in sorted(gold.items()):
        esc = f"{comp:%Y-%m}"
        r = rec.get(comp)
        fonte = float(r["total"]) if r else leg.get(comp, 0.0)
        out.append(
            Verificacao(
                "receita_sem_duplicidade",
                esc,
                fonte,
                float(g["receita"] or 0),
                PASS if _bate(fonte, float(g["receita"] or 0)) else FAIL,
                f"receita da Gold = fonte '{g['fonte_receita']}' (reconhecida OU legada, nunca a soma)",
            )
        )
        if r:
            soma_niveis = sum(float(r[k] or 0) for k in ("atrib", "emp", "na"))
            out.append(
                Verificacao(
                    "receita_original_vs_niveis",
                    esc,
                    float(r["total"]),
                    soma_niveis,
                    PASS if _bate(float(r["total"]), soma_niveis) else FAIL,
                    "receita reconhecida = atribuída a contrato + empresa sem detalhamento + não atribuída",
                )
            )
        if r and comp in leg:
            div = not _bate(float(r["total"]), leg[comp])
            out.append(
                Verificacao(
                    "receita_reconhecida_vs_legada",
                    esc,
                    leg[comp],
                    float(r["total"]),
                    FAIL if div else PASS,
                    "as duas fontes existem na competência e precisam bater; divergência bloqueia a "
                    "sinistralidade da competência"
                    if div
                    else "fontes reconhecida e legada iguais",
                )
            )

    # ---- atribuída × consolidado por plano (só onde há as duas fontes)
    for row in _q(
        session,
        f"""
        WITH rp AS (
            SELECT competencia, id_plano, SUM(receita_contraprestacao) AS atrib
            FROM receitas_reconhecidas
            WHERE tenant_id = :t AND NOT estimada AND nivel_atribuicao = 'contrato_plano' {filtro}
            GROUP BY competencia, id_plano),
        lp AS (SELECT competencia, id_plano, SUM(receita_contraprestacao) AS legada FROM receitas
               WHERE tenant_id = :t {filtro} GROUP BY competencia, id_plano)
        SELECT lp.competencia, lp.id_plano, lp.legada, COALESCE(rp.atrib, 0) AS atrib
        FROM lp JOIN (SELECT DISTINCT competencia FROM rp) c ON c.competencia = lp.competencia
        LEFT JOIN rp ON rp.competencia = lp.competencia AND rp.id_plano = lp.id_plano""",
        **mp,
    ):
        r = rec.get(row["competencia"]) or {}
        # receita da competência que NÃO chega a contrato × plano (empresa sem detalhamento,
        # não atribuída) — é o que pode explicar a diferença por plano
        nao_plano = float(r.get("emp") or 0) + float(r.get("na") or 0)
        dif = abs(float(row["legada"]) - float(row["atrib"]))
        ok = dif <= TOL
        out.append(
            Verificacao(
                "receita_atribuida_vs_plano",
                f"{row['competencia']:%Y-%m} · plano {row['id_plano']}",
                float(row["legada"]),
                float(row["atrib"]),
                PASS if ok else (WARNING if dif <= nao_plano + TOL else FAIL),
                "receita detalhada (contrato × plano) do plano = receita legada do plano"
                if ok
                else "diferença = parte da receita do plano não detalhada até contrato × plano "
                f"(empresa sem detalhamento / não atribuída na competência: {_brl(nao_plano)})",
            )
        )

    # ---- atribuída × Gold por contrato
    for row in _q(
        session,
        f"""
        WITH s AS (SELECT competencia, SUM(receita_contraprestacao) AS v FROM receitas_reconhecidas
                   WHERE tenant_id = :t AND NOT estimada
                     AND nivel_atribuicao IN ('contrato_plano','contrato') {filtro} GROUP BY competencia),
             g AS (SELECT competencia, SUM(receita_reconhecida) AS v FROM agg_contrato_competencia
                   WHERE tenant_id = :t {filtro} GROUP BY competencia)
        SELECT s.competencia, s.v AS silver, COALESCE(g.v, 0) AS gold FROM s
        LEFT JOIN g ON g.competencia = s.competencia""",
        **mp,
    ):
        out.append(
            Verificacao(
                "receita_atribuida_vs_contratos",
                f"{row['competencia']:%Y-%m}",
                float(row["silver"]),
                float(row["gold"]),
                PASS if _bate(float(row["silver"]), float(row["gold"])) else FAIL,
                "Σ receita atribuída a contratos (Silver) = Σ receita por contrato (Gold)",
            )
        )

    # ---- empresa × contratos relacionados: item detalhado de documento da empresa precisa
    #      ser de contrato associado (contratante) à empresa na competência
    fora = _q(
        session,
        f"""
        SELECT f.numero_documento, it.numero_item, f.competencia_faturamento AS competencia
        FROM fatura_itens it JOIN faturas f ON f.id = it.id_fatura
        WHERE it.tenant_id = :t AND f.unidade_cobranca = 'empresa' AND it.id_contrato IS NOT NULL
          AND NOT EXISTS (
              SELECT 1 FROM empresas_contratos ec
              WHERE ec.tenant_id = :t AND ec.id_empresa = f.id_empresa AND ec.id_contrato = it.id_contrato
                AND ec.papel = 'contratante' AND ec.vigencia_inicio <= f.competencia_faturamento
                AND (ec.vigencia_fim IS NULL OR ec.vigencia_fim > f.competencia_faturamento))
          {filtro.replace("competencia", "f.competencia_faturamento")}""",
        **mp,
    )
    out.append(
        Verificacao(
            "empresa_vs_contratos_relacionados",
            "documentos de empresa",
            0,
            len(fora),
            PASS if not fora else FAIL,
            "itens detalhados de faturas de empresa pertencem a contratos da empresa na competência"
            + (
                f" — fora: {', '.join(f['numero_documento'] + '/' + f['numero_item'] for f in fora[:5])}"
                if fora
                else ""
            ),
        )
    )

    # ---- documento: Σ itens = total; faturado × recebido (informativo, não equivalentes)
    for row in _q(
        session,
        f"""
        SELECT f.numero_documento, f.valor_total, SUM(it.valor) AS itens
        FROM faturas f JOIN fatura_itens it ON it.id_fatura = f.id
        WHERE f.tenant_id = :t {filtro.replace("competencia", "f.competencia_faturamento")}
        GROUP BY f.numero_documento, f.valor_total""",
        **mp,
    ):
        ok = _bate(float(row["valor_total"]), float(row["itens"]))
        if not ok:
            out.append(
                Verificacao(
                    "fatura_itens_vs_total",
                    row["numero_documento"],
                    float(row["valor_total"]),
                    float(row["itens"]),
                    FAIL,
                    "Σ itens do documento ≠ valor total",
                )
            )
    n_docs = _q(
        session,
        f"""SELECT count(DISTINCT f.id) AS n FROM faturas f JOIN fatura_itens it
                             ON it.id_fatura = f.id WHERE f.tenant_id = :t
                             {filtro.replace("competencia", "f.competencia_faturamento")}""",
        **mp,
    )[0]["n"]
    if n_docs and not any(v.check_id == "fatura_itens_vs_total" for v in out):
        out.append(
            Verificacao(
                "fatura_itens_vs_total",
                f"{n_docs} documento(s)",
                0,
                0,
                PASS,
                "Σ itens = valor total em todos os documentos detalhados",
            )
        )
    for row in _q(
        session,
        f"""
        WITH fat AS (SELECT competencia_faturamento AS competencia, SUM(valor_total) AS v FROM faturas
                     WHERE tenant_id = :t AND COALESCE(status, 'emitida') = 'emitida'
                     {filtro.replace("competencia", "competencia_faturamento")}
                     GROUP BY competencia_faturamento),
             rcb AS (SELECT competencia_recebimento AS competencia, SUM(valor_recebido) AS v FROM recebimentos
                     WHERE tenant_id = :t {filtro.replace("competencia", "competencia_recebimento")}
                     GROUP BY competencia_recebimento)
        SELECT COALESCE(fat.competencia, rcb.competencia) AS competencia,
               COALESCE(fat.v, 0) AS faturado, COALESCE(rcb.v, 0) AS recebido
        FROM fat FULL JOIN rcb ON rcb.competencia = fat.competencia""",
        **mp,
    ):
        out.append(
            Verificacao(
                "faturado_vs_recebido",
                f"{row['competencia']:%Y-%m}",
                float(row["faturado"]),
                float(row["recebido"]),
                INFO,
                "informativo — faturamento, recebimento e receita reconhecida NÃO são equivalentes "
                "(competências e regras diferentes)",
            )
        )

    # ---- despesa: evento × Gold; contratos + não atribuída = total; beneficiário = total
    for row in _q(
        session,
        f"""
        WITH ev AS (SELECT competencia,
                           SUM(valor_apresentado - valor_glosado - valor_coparticipacao) AS total,
                           SUM(valor_apresentado - valor_glosado - valor_coparticipacao)
                               FILTER (WHERE id_contrato IS NULL) AS nao_atrib
                    FROM eventos_assistenciais WHERE tenant_id = :t {filtro} GROUP BY competencia),
             g AS (SELECT competencia, despesa_liquida FROM agg_sinistralidade_competencia
                   WHERE tenant_id = :t {filtro}),
             c AS (SELECT competencia, SUM(despesa_liquida) AS v FROM agg_contrato_competencia
                   WHERE tenant_id = :t {filtro} GROUP BY competencia),
             b AS (SELECT competencia, SUM(despesa_liquida) AS v FROM agg_beneficiario_competencia
                   WHERE tenant_id = :t {filtro} GROUP BY competencia)
        SELECT ev.competencia, ev.total, COALESCE(ev.nao_atrib, 0) AS nao_atrib,
               COALESCE(g.despesa_liquida, 0) AS gold, COALESCE(c.v, 0) AS contratos,
               COALESCE(b.v, 0) AS benef
        FROM ev LEFT JOIN g USING (competencia) LEFT JOIN c USING (competencia)
        LEFT JOIN b USING (competencia)""",
        **mp,
    ):
        esc = f"{row['competencia']:%Y-%m}"
        tot = float(row["total"])
        out.append(
            Verificacao(
                "despesa_eventos_vs_gold",
                esc,
                tot,
                float(row["gold"]),
                PASS if _bate(tot, float(row["gold"])) else FAIL,
                "Σ despesa líquida dos eventos = Gold da competência",
            )
        )
        soma = float(row["contratos"]) + float(row["nao_atrib"])
        out.append(
            Verificacao(
                "despesa_contratos_mais_nao_atribuida",
                esc,
                tot,
                soma,
                PASS if _bate(tot, soma) else FAIL,
                "Σ despesa por contrato (vínculo da competência) + despesa sem vínculo vigente = total"
                + (
                    f" (não atribuída: {_brl(float(row['nao_atrib']))})"
                    if float(row["nao_atrib"])
                    else ""
                ),
            )
        )
        out.append(
            Verificacao(
                "despesa_beneficiarios_vs_total",
                esc,
                tot,
                float(row["benef"]),
                PASS if _bate(tot, float(row["benef"])) else FAIL,
                "Σ despesa por beneficiário = total",
            )
        )

    # ---- vidas históricas: Σ vidas por contrato (Gold) = exposição pelo vínculo vigente
    meses_v = competencias if competencias else sorted(gold)
    if meses_v:
        expo = {
            r["c"]: int(r["n"])
            for r in _q(
                session,
                f"""
            WITH {SQL_EXPOSICAO}
            SELECT x.c, count(*) AS n FROM exposicao x WHERE x.id_contrato IS NOT NULL GROUP BY x.c""",
                meses=meses_v,
            )
        }
        vid = {
            r["competencia"]: int(r["v"])
            for r in _q(
                session,
                f"""
            SELECT competencia, SUM(vidas) AS v FROM agg_contrato_competencia
            WHERE tenant_id = :t {filtro} GROUP BY competencia""",
                **mp,
            )
        }
        for m in meses_v:
            out.append(
                Verificacao(
                    "vidas_historicas",
                    f"{m:%Y-%m}",
                    expo.get(m, 0),
                    vid.get(m, 0),
                    PASS if expo.get(m, 0) == vid.get(m, 0) else FAIL,
                    "Σ vidas por contrato = beneficiários expostos pelo vínculo vigente "
                    "no 1º dia da competência",
                )
            )

    # ---- receita: mesmo documento em dois níveis (consolidado E detalhado) = dupla contagem
    dup = _q(
        session,
        f"""
        SELECT numero_documento FROM receitas_reconhecidas
        WHERE tenant_id = :t AND numero_documento IS NOT NULL AND NOT estimada {filtro}
        GROUP BY numero_documento
        HAVING bool_or(nivel_atribuicao = 'empresa') AND bool_or(nivel_atribuicao <> 'empresa')""",
        **mp,
    )
    out.append(
        Verificacao(
            "receita_documento_em_dois_niveis",
            "documentos",
            0,
            len(dup),
            PASS if not dup else FAIL,
            "nenhum documento com receita consolidada E detalhada (seria contada 2×)",
        )
    )
    return out


def resumo(verificacoes: list[Verificacao]) -> dict:
    st = {v.status for v in verificacoes}
    geral = FAIL if FAIL in st else (WARNING if WARNING in st else PASS)
    return {
        "status": geral,
        "contagem": {
            s: sum(1 for v in verificacoes if v.status == s) for s in (PASS, WARNING, FAIL, INFO)
        },
        "verificacoes": [v.as_dict() for v in verificacoes],
    }
