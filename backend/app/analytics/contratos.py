"""Contract Intelligence — visão consolidada por contrato.

v1.2: vidas, despesa (bruta/glosa/coparticipação/líquida), evolução, concentração,
beneficiários de maior impacto, drivers e alertas do contrato.

**Consolidação do canônico V1:** sinistralidade contratual SÓ quando a Gold a publicou —
receita reconhecida ATRIBUÍDA ao contrato, competência sem divergência de receita e
cobertura contratual suficiente (`agg_contrato_competencia.sinistralidade_liquida`). Sem
isso: `receita_disponivel: false`, o motivo e o mesmo aviso de antes — nunca rateio.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from app.analytics import alerts, decomposition
from app.analytics import formulas as f
from app.analytics.periodo import competencia_comparacao
from app.repositories import analytics_repo as repo

_AVISO = (
    "Receita por contrato ainda não disponível — esta visão cobre vidas, despesa e "
    "concentração, mas não sinistralidade contratual."
)


def _f(x) -> float:
    return float(x) if x is not None else 0.0


def _pct(a: float, b: float) -> float | None:
    return round((a - b) / b * 100.0, 2) if abs(b) > 1e-9 else None


MOTIVO_SEM_SINISTRALIDADE = {
    "sem_receita_atribuida": "sem receita reconhecida atribuída ao contrato no período",
    "receita_divergente": "receita da competência divergente entre fontes",
    "receita_empresa_sem_detalhamento": "a empresa contratante tem receita consolidada sem "
                                        "detalhamento na competência (pode conter parte deste "
                                        "contrato) — não é rateada",
}


def sinistralidade_contratual(r: dict | None) -> dict:
    """Bloco da sinistralidade de contrato a partir da linha da Gold (nada recalculado aqui)."""
    if r is None:
        return {"disponivel": False, "valor": None, "receita_reconhecida": None,
                "motivo": "contrato sem dados na competência"}
    s = r.get("sinistralidade_liquida")
    motivo = r.get("motivo_sem_sinistralidade")
    return {
        "disponivel": s is not None,
        "valor": round(float(s), 2) if s is not None else None,
        "receita_reconhecida": round(float(r["receita_reconhecida"]), 2)
        if r.get("receita_reconhecida") is not None else None,
        "despesa_liquida": round(_f(r["despesa_liquida"]), 2),
        "motivo": None if s is not None else MOTIVO_SEM_SINISTRALIDADE.get(motivo, motivo),
        "formula": "despesa líquida atribuída ÷ receita reconhecida atribuída × 100",
    }


def listar(session: Session, competencia: date, comparacao: str = "mes_anterior") -> dict:
    comp_ant = competencia_comparacao(competencia, comparacao)
    atu = repo.contratos_resumo_mes(session, competencia)
    ant = {r["id_contrato"]: r for r in repo.contratos_resumo_mes(session, comp_ant)}

    itens = []
    for r in atu:
        a = ant.get(r["id_contrato"])
        dliq = _f(r["despesa_liquida"])
        dliq_ant = _f(a["despesa_liquida"]) if a else 0.0
        pmpm = _f(r["custo_pmpm"])
        pmpm_ant = _f(a["custo_pmpm"]) if a else 0.0
        itens.append({
            "id_contrato": r["id_contrato"],
            "nome": r["nome"],
            "tipo": r["tipo"],
            "plano": r["plano"],
            "vidas": r["vidas"],
            "despesa_bruta": round(_f(r["despesa_bruta"]), 2),
            "glosas": round(_f(r["glosas"]), 2),
            "coparticipacao": round(_f(r["coparticipacao"]), 2),
            "despesa_liquida": round(dliq, 2),
            "custo_pmpm": round(pmpm, 2),
            "eventos": r["eventos"],
            "gini": round(_f(r["gini"]), 4),
            "top5_share": round(_f(r["top5_share"]), 4),
            "n_beneficiarios_alto_custo": r["n_beneficiarios_alto_custo"],
            "variacao_despesa_liquida_pct": _pct(dliq, dliq_ant),
            "variacao_custo_pmpm_pct": _pct(pmpm, pmpm_ant),
            "empresa": {"id": r["id_empresa"], "nome": r["empresa"]} if r.get("id_empresa") else None,
            "sinistralidade_contratual": sinistralidade_contratual(r),
        })
    com_receita = any(i["sinistralidade_contratual"]["receita_reconhecida"] for i in itens)
    return {
        "competencia": competencia.isoformat(),
        "comparacao": comparacao,
        "receita_disponivel": com_receita,
        "aviso": None if com_receita else _AVISO,
        "total": len(itens),
        "itens": itens,
    }


def _composicao_bloco(r: dict | None) -> dict | None:
    if r is None:
        return None
    return {
        "despesa_bruta": round(_f(r["despesa_bruta"]), 2),
        "glosas": round(_f(r["glosas"]), 2),
        "coparticipacao": round(_f(r["coparticipacao"]), 2),
        "despesa_liquida": round(_f(r["despesa_liquida"]), 2),
    }


def detalhe(
    session: Session, id_contrato: int, competencia: date, comparacao: str = "mes_anterior"
) -> dict:
    info = repo.contrato_info(session, id_contrato)
    if info is None:
        raise ValueError("contrato não encontrado")
    comp_ant = competencia_comparacao(competencia, comparacao)
    atual = repo.contrato_competencia(session, id_contrato, competencia)
    if atual is None:
        raise ValueError("contrato sem dados na competência")
    ant = repo.contrato_competencia(session, id_contrato, comp_ant)

    serie = [
        {
            "competencia": r["competencia"].isoformat(),
            "vidas": r["vidas"],
            "despesa_liquida": round(_f(r["despesa_liquida"]), 2),
            "despesa_bruta": round(_f(r["despesa_bruta"]), 2),
            "glosas": round(_f(r["glosas"]), 2),
            "coparticipacao": round(_f(r["coparticipacao"]), 2),
            "custo_pmpm": round(_f(r["custo_pmpm"]), 2),
            "eventos": r["eventos"],
            "gini": round(_f(r["gini"]), 4),
            "receita_reconhecida": round(float(r["receita_reconhecida"]), 2)
            if r.get("receita_reconhecida") is not None else None,
            "sinistralidade_liquida": round(float(r["sinistralidade_liquida"]), 2)
            if r.get("sinistralidade_liquida") is not None else None,
        }
        for r in repo.contrato_serie(session, id_contrato)
    ]

    # concentração (Gini / Pareto) sobre despesa líquida por beneficiário do contrato/mês
    benef = repo.beneficiarios_liquida_contrato_mes(session, competencia, id_contrato)
    valores = [_f(b["despesa_liquida"]) for b in benef]
    conc = f.concentracao(valores, ks=(1, 3, 5, 10, 20))
    total_liq = conc.total or 1.0
    top_beneficiarios = [
        {
            "id": b["id"], "codigo": b["codigo"],
            "despesa_liquida": round(_f(b["despesa_liquida"]), 2),
            "eventos": b["eventos"],
            "participacao_pct": round(_f(b["despesa_liquida"]) / total_liq * 100.0, 2),
        }
        for b in benef[:10]
    ]

    # drivers dentro do contrato (decomposição com escopo)
    try:
        drv = decomposition.explicar(
            session, competencia, comparacao, "especialidade", contrato_id=id_contrato
        )
        drivers = {
            "principais_fatores": drv["principais_fatores"][:5],
            "fatores_reducao": drv["fatores_reducao"][:3],
            "concentracao_variacao_beneficiarios": drv["concentracao_variacao_beneficiarios"],
        }
    except ValueError:
        drivers = None

    # alertas do contrato: regras de entidade 'contrato' para este id + regras de
    # 'beneficiario' cujo alvo pertence ao contrato.
    ids_contrato = {b["id"] for b in benef}
    todos = alerts.avaliar_regras(session, competencia, comparacao)
    alertas = [
        a.as_dict() for a in todos
        if (a.entidade == "contrato" and a.entidade_id == str(id_contrato))
        or (a.entidade == "beneficiario" and a.entidade_id.isdigit()
            and int(a.entidade_id) in ids_contrato)
    ]

    return {
        "contrato": {
            "id": info["id"], "nome": info["nome"], "tipo": info["tipo"],
            "plano": info["plano"], "vidas_alvo": info["vidas_alvo"],
        },
        "competencia": competencia.isoformat(),
        "comparacao": comparacao,
        "receita_disponivel": bool(atual.get("receita_reconhecida")),
        "aviso": None if atual.get("receita_reconhecida") else _AVISO,
        "sinistralidade_contratual": sinistralidade_contratual(atual),
        "kpis": {
            "vidas": atual["vidas"],
            "despesa_bruta": round(_f(atual["despesa_bruta"]), 2),
            "glosas": round(_f(atual["glosas"]), 2),
            "coparticipacao": round(_f(atual["coparticipacao"]), 2),
            "despesa_liquida": round(_f(atual["despesa_liquida"]), 2),
            "custo_pmpm": round(_f(atual["custo_pmpm"]), 2),
            "eventos": atual["eventos"],
            "gini": round(_f(atual["gini"]), 4),
            "top5_share": round(_f(atual["top5_share"]), 4),
            "n_beneficiarios_alto_custo": atual["n_beneficiarios_alto_custo"],
            "variacao_despesa_liquida_pct": _pct(
                _f(atual["despesa_liquida"]), _f(ant["despesa_liquida"]) if ant else 0.0
            ),
        },
        "composicao": {
            "atual": _composicao_bloco(atual),
            "comparacao_valores": _composicao_bloco(ant),
        },
        "evolucao": serie,
        "concentracao": {
            **conc.as_dict(),
            "top_beneficiarios": top_beneficiarios,
        },
        "drivers": drivers,
        "alertas": alertas,
        "metodologia": (
            "Agregado materializado `agg_contrato_competencia`; concentração = top-k share "
            "+ Gini + Pareto sobre despesa líquida por beneficiário do contrato no mês; "
            "drivers = decomposição da despesa com escopo de contrato (sem receita)."
        ),
    }
