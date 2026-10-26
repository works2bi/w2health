"""Sinistralidade: indicador, evolução mensal, comparações (MoM/YoY) e acumulado 12m."""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.analytics import formulas as f
from app.analytics.periodo import competencia_comparacao
from app.repositories import analytics_repo as repo


def _f(x) -> float:
    return float(x) if x is not None else 0.0


AVISO_RECEITA_DIVERGENTE = (
    "Receita da competência divergente entre a fonte reconhecida e a legada por plano — "
    "sinistralidade NÃO publicada até a divergência ser resolvida (ver "
    "/analytics/receitas/reconciliacao)."
)


def _divergente(r: dict | None) -> bool:
    return bool(r) and r.get("receita_status") == "divergente"


def _fonte(r: dict) -> dict:
    """Origem da receita da competência — nunca a soma das fontes (Consolidação V1)."""
    return {"fonte_receita": r.get("fonte_receita"), "receita_status": r.get("receita_status")}


def serie(session: Session) -> list[dict]:
    """Série mensal completa com sinistralidade, acumulado 12m e variações."""
    linhas = repo.serie_sinistralidade(session)
    despesas: list[float] = []
    receitas: list[float] = []
    out: list[dict] = []
    for i, r in enumerate(linhas):
        despesas.append(_f(r["despesa"]))
        receitas.append(_f(r["receita"]))
        s = None if _divergente(r) else _f(r["sinistralidade"])
        s_ant = None if i == 0 or _divergente(linhas[i - 1]) else _f(linhas[i - 1]["sinistralidade"])
        s_yoy = None if i < 12 or _divergente(linhas[i - 12]) else _f(linhas[i - 12]["sinistralidade"])
        div12 = any(_divergente(x) for x in linhas[max(0, i - 11): i + 1])
        out.append(
            {
                "competencia": r["competencia"].isoformat(),
                "receita": round(_f(r["receita"]), 2),
                "despesa": round(_f(r["despesa"]), 2),
                "sinistralidade": round(s, 2) if s is not None else None,
                **_fonte(r),
                "despesa_bruta": round(_f(r["despesa_bruta"]), 2),
                "sinistralidade_bruta": round(_f(r["sinistralidade_bruta"]), 2),
                "beneficiarios_ativos": r["beneficiarios_ativos"],
                "eventos": r["eventos"],
                "custo_pmpm": round(_f(r["custo_pmpm"]), 2),
                "receita_media_beneficiario": round(_f(r["receita_media_beneficiario"]), 2),
                "variacao_pp_mes_anterior": round(f.variacao_pp(s, s_ant), 2)
                if s is not None and s_ant is not None else None,
                "variacao_pp_ano_anterior": round(f.variacao_pp(s, s_yoy), 2)
                if s is not None and s_yoy is not None else None,
                "acumulado_12m": round(f.acumulado_12m(despesas, receitas), 2)
                if i >= 11 and not div12 else None,
            }
        )
    return out


def indicador(session: Session, competencia: date, comparacao: str = "mes_anterior") -> dict:
    """Indicador de sinistralidade do mês + comparação escolhida + decomposição num/den."""
    atu = repo.sinistralidade_mes(session, competencia)
    if atu is None:
        raise ValueError(f"Competência sem dados: {competencia}")

    comp_ant = competencia_comparacao(competencia, comparacao)
    if _divergente(atu) or (comparacao != "acumulado_12m"
                            and _divergente(repo.sinistralidade_mes(session, comp_ant))):
        return {
            "competencia": competencia.isoformat(), "comparacao": comparacao,
            "competencia_comparacao": comp_ant.isoformat() if comparacao != "acumulado_12m" else None,
            "sinistralidade_atual": None, "sinistralidade_comparacao": None, "variacao_pp": None,
            "receita": None, "despesa": round(_f(atu["despesa"]), 2),
            "despesa_bruta": round(_f(atu["despesa_bruta"]), 2), "sinistralidade_bruta": None,
            "beneficiarios_ativos": atu["beneficiarios_ativos"],
            "custo_pmpm": round(_f(atu["custo_pmpm"]), 2), "receita_media_beneficiario": None,
            "eventos": atu["eventos"], "decomposicao_receita_despesa": None, **_fonte(atu),
            "aviso": AVISO_RECEITA_DIVERGENTE, "metodologia": "",
        }
    linhas = repo.serie_sinistralidade(session)
    idx = {r["competencia"]: i for i, r in enumerate(linhas)}

    s_atu = _f(atu["sinistralidade"])
    if comparacao == "acumulado_12m":
        i = idx.get(competencia, len(linhas) - 1)
        d12 = [_f(r["despesa"]) for r in linhas[max(0, i - 11): i + 1]]
        r12 = [_f(r["receita"]) for r in linhas[max(0, i - 11): i + 1]]
        s_ref = f.acumulado_12m(d12 + [0] * 12, r12 + [0] * 12) if len(d12) >= 12 else s_atu
        dec = None
    else:
        ant = repo.sinistralidade_mes(session, comp_ant)
        s_ref = _f(ant["sinistralidade"]) if ant else s_atu
        dec = (
            f.decomposicao_sinistralidade(
                _f(ant["despesa"]), _f(ant["receita"]),
                _f(atu["despesa"]), _f(atu["receita"]),
            ).as_dict()
            if ant
            else None
        )

    return {
        "competencia": competencia.isoformat(),
        "comparacao": comparacao,
        "competencia_comparacao": comp_ant.isoformat() if comparacao != "acumulado_12m" else None,
        "sinistralidade_atual": round(s_atu, 2),
        "sinistralidade_comparacao": round(s_ref, 2),
        "variacao_pp": round(f.variacao_pp(s_atu, s_ref), 2),
        "receita": round(_f(atu["receita"]), 2),
        "despesa": round(_f(atu["despesa"]), 2),
        "despesa_bruta": round(_f(atu["despesa_bruta"]), 2),
        "sinistralidade_bruta": round(_f(atu["sinistralidade_bruta"]), 2),
        "beneficiarios_ativos": atu["beneficiarios_ativos"],
        "custo_pmpm": round(_f(atu["custo_pmpm"]), 2),
        "receita_media_beneficiario": round(_f(atu["receita_media_beneficiario"]), 2),
        "eventos": atu["eventos"],
        "decomposicao_receita_despesa": dec,
        **_fonte(atu),
        "metodologia": (
            "S = despesa/receita*100. Variação em pontos percentuais (p.p.). "
            "Decomposição num/den: efeito_despesa = ΔD/R0; efeito_receita = D1/R1 − D1/R0. "
            "Convenção do MVP: 'despesa'/'sinistralidade' referem-se à base LÍQUIDA "
            "(bruta − glosas − coparticipação). Ver /analytics/sinistralidade/composicao "
            "para bruta, glosas, coparticipação e a decomposição financeira completa."
        ),
    }


def composicao(
    session: Session, competencia: date, comparacao: str = "mes_anterior",
    dimensao: str | None = None, chave: str | None = None, contrato_id: int | None = None,
) -> dict:
    """Composição financeira da despesa: bruta → glosas → coparticipação → líquida, e a
    decomposição da variação da sinistralidade LÍQUIDA em 4 efeitos (Etapa B da v1.1).

    v1.2: aceita escopo de **dimensão/chave** ou de **contrato** — nesses casos não há
    receita, então `sinistralidade_*` e `decomposicao` vêm `None` (só a composição da
    despesa). `despesa` no escopo de dimensão = Σ valor_pago; a decomposição de 4 efeitos
    da carteira permanece inalterada."""
    comp_ant = competencia_comparacao(competencia, comparacao)
    escopo = "carteira"

    if contrato_id is not None:
        escopo = "contrato"
        atu = repo.contrato_competencia(session, contrato_id, competencia)
        ant = repo.contrato_competencia(session, contrato_id, comp_ant)
        com_receita = False
    elif dimensao is not None and chave is not None:
        escopo = f"{dimensao}={chave}"
        atu = repo.dimensao_mes(session, competencia, dimensao).get(chave)
        ant = repo.dimensao_mes(session, comp_ant, dimensao).get(chave)
        com_receita = False
    else:
        atu = repo.sinistralidade_mes(session, competencia)
        ant = repo.sinistralidade_mes(session, comp_ant)
        com_receita = True

    if atu is None:
        raise ValueError("Sem dados para o escopo/competência informados")

    def _bloco(r: dict | None) -> dict | None:
        if r is None:
            return None
        rec = _f(r["receita"]) if com_receita else None
        dl = _f(r["despesa_liquida"])
        db = _f(r["despesa_bruta"])
        return {
            "despesa_bruta": round(db, 2),
            "glosas": round(_f(r["glosas"]), 2),
            "coparticipacao": round(_f(r["coparticipacao"]), 2),
            "despesa_liquida": round(dl, 2),
            "receita": round(rec, 2) if rec is not None else None,
            "sinistralidade_bruta": round(db / rec * 100, 2) if rec else None,
            "sinistralidade_liquida": round(dl / rec * 100, 2) if rec else None,
        }

    dec = None
    if com_receita and ant:
        dec = f.decomposicao_financeira(
            _f(ant["despesa_bruta"]), _f(ant["glosas"]), _f(ant["coparticipacao"]), _f(ant["receita"]),
            _f(atu["despesa_bruta"]), _f(atu["glosas"]), _f(atu["coparticipacao"]), _f(atu["receita"]),
        ).as_dict()

    out = {
        "competencia": competencia.isoformat(),
        "comparacao": comparacao,
        "competencia_comparacao": comp_ant.isoformat(),
        "escopo": escopo,
        "atual": _bloco(atu),
        "comparacao_valores": _bloco(ant),
        "decomposicao": dec,
        "metodologia": (
            "despesa_liquida = despesa_bruta - glosas - coparticipacao; sinistralidade_x = "
            "despesa_x / receita * 100. Decomposição (identidade exata, soma = variação da "
            "sinistralidade líquida): efeito_bruta = ΔBruta/R0; efeito_glosa = -ΔGlosa/R0; "
            "efeito_coparticipacao = -ΔCopart/R0; efeito_receita = Dliq1/R1 - Dliq1/R0."
        ),
    }
    if not com_receita:
        out["aviso"] = (
            "Escopo sem receita própria — apenas a composição da despesa "
            "(bruta/glosas/coparticipação/líquida); sinistralidade e decomposição não se aplicam."
        )
    return out


def executivo(session: Session, competencia: date, comparacao: str = "mes_anterior") -> dict:
    """Payload da Visão Executiva: KPIs + série + comparação com mês anterior e YoY."""
    ind = indicador(session, competencia, comparacao)
    s = serie(session)
    linha = next((x for x in s if x["competencia"] == competencia.isoformat()), s[-1])
    return {
        "competencia": competencia.isoformat(),
        "kpis": {
            "sinistralidade": ind["sinistralidade_atual"],
            "variacao_pp": ind["variacao_pp"],
            "receita": ind["receita"],
            "despesa": ind["despesa"],
            "beneficiarios": ind["beneficiarios_ativos"],
            "custo_assistencial_por_beneficiario": ind["custo_pmpm"],
            "receita_media_por_beneficiario": ind["receita_media_beneficiario"],
            "variacao_pp_ano_anterior": linha["variacao_pp_ano_anterior"],
            "acumulado_12m": linha["acumulado_12m"],
        },
        "comparacao": comparacao,
        "decomposicao_receita_despesa": ind["decomposicao_receita_despesa"],
        "serie": s,
    }
