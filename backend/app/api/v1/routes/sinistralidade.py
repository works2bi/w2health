"""Sinistralidade: indicador, evolução, explicação da variação e drill-down.

v1.2: `explain`/`explain/{dim}/{chave}`/`.../causas` e `composicao` aceitam
`?contrato_id=` (escopo de contrato, sem receita própria); novo `concentracao-variacao`
traz a concentração do aumento de despesa líquida em poucos beneficiários (C1).

Fundação SaaS V1: feature `loss_ratio_intelligence` no router; `financial_composition`,
`advanced_explanations` e `beneficiary_intelligence` nas rotas que as expõem; respostas
compostas saneadas por `feature_filters.sanitize` (os cálculos não mudam).
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.analytics import benchmark, cohorts, decomposition
from app.analytics import sinistralidade as sin
from app.api.v1.routes._common import (
    analytics_guard,
    comparacao_dep,
    competencia_dep,
    contrato_id_dep,
    exigir_feature_da_dimensao,
)
from app.saas import audit
from app.saas.feature_filters import BENEFICIARY, sanitize
from app.security.deps import TenantContext, get_tenant_context, get_tenant_db, require_feature

router = APIRouter(
    prefix="/analytics/sinistralidade", tags=["Sinistralidade"],
    dependencies=analytics_guard("loss_ratio_intelligence"),
)

_METODOS = ("bennet", "laspeyres")


def _metodo(metodo: str = Query("bennet", description="Método do bridge freq × custo.")) -> str:
    if metodo not in _METODOS:
        raise HTTPException(422, f"metodo inválido. Use: {', '.join(_METODOS)}")
    return metodo


@router.get("", summary="Indicador de sinistralidade do mês + comparação + decomposição num/den")
def indicador(
    competencia: date = Depends(competencia_dep),
    comparacao: str = Depends(comparacao_dep),
    db: Session = Depends(get_tenant_db),
) -> dict:
    return sin.indicador(db, competencia, comparacao)


@router.get("/benchmark", summary="Operadora × mercado (dados abertos ANS): sinistralidade e custo por vida")
def benchmark_mercado(
    segmento: str = Query("medicina_de_grupo", description="Modalidade de comparação"),
    competencia: date = Depends(competencia_dep),
    db: Session = Depends(get_tenant_db),
) -> dict:
    try:
        return benchmark.comparar(db, competencia, segmento)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@router.get("/evolucao", summary="Série mensal com sinistralidade, acumulado 12m e variações")
def evolucao(db: Session = Depends(get_tenant_db)) -> dict:
    return {"serie": sin.serie(db)}


@router.get(
    "/composicao",
    summary="Composição financeira: bruta, glosas, coparticipação, líquida + decomposição",
    dependencies=[Depends(require_feature("financial_composition"))],
)
def composicao(
    competencia: date = Depends(competencia_dep),
    comparacao: str = Depends(comparacao_dep),
    contrato_id: int | None = Depends(contrato_id_dep),
    dimensao: str | None = Query(None, description="v1.2 — escopo de dimensão (com `chave`)."),
    chave: str | None = Query(None),
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_tenant_db),
) -> dict:
    if dimensao is not None and dimensao not in decomposition.DIMENSOES_VALIDAS:
        raise HTTPException(422, f"dimensão inválida: {dimensao}")
    exigir_feature_da_dimensao(ctx, dimensao)
    try:
        return sin.composicao(db, competencia, comparacao, dimensao, chave, contrato_id)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@router.get(
    "/concentracao-variacao",
    summary="C1 — concentração do aumento de despesa líquida em poucos beneficiários",
    dependencies=[Depends(require_feature("beneficiary_intelligence"))],
)
def concentracao_variacao(
    competencia: date = Depends(competencia_dep),
    comparacao: str = Depends(comparacao_dep),
    contrato_id: int | None = Depends(contrato_id_dep),
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_tenant_db),
) -> dict:
    res = decomposition.concentracao_variacao_beneficiarios(db, competencia, comparacao, contrato_id)
    if res is None:
        raise HTTPException(404, "sem dados de beneficiário para o período")
    _auditar_drill(db, ctx, "data.beneficiary.concentration", {"competencia": competencia.isoformat()})
    return res


@router.get("/explain", summary="Explicação automática: fatores da variação por dimensão + bridge")
def explain(
    competencia: date = Depends(competencia_dep),
    comparacao: str = Depends(comparacao_dep),
    contrato_id: int | None = Depends(contrato_id_dep),
    dimensao: str = Query("especialidade", description="Dimensão da decomposição."),
    metodo: str = Depends(_metodo),
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_tenant_db),
) -> dict:
    exigir_feature_da_dimensao(ctx, dimensao)
    try:
        res = decomposition.explicar(db, competencia, comparacao, dimensao, metodo,
                                     contrato_id=contrato_id)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return sanitize(res, ctx.features)


@router.get(
    "/explain/{dimensao}/{chave}",
    summary="Drill-down de um fator: bridge + onde investigar (prestadores/beneficiários)",
)
def explain_drill(
    dimensao: str,
    chave: str,
    competencia: date = Depends(competencia_dep),
    comparacao: str = Depends(comparacao_dep),
    contrato_id: int | None = Depends(contrato_id_dep),
    metodo: str = Depends(_metodo),
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_tenant_db),
) -> dict:
    exigir_feature_da_dimensao(ctx, dimensao)
    try:
        res = decomposition.drill(db, competencia, dimensao, chave, comparacao, metodo,
                                  contrato_id=contrato_id)
    except KeyError as e:
        raise HTTPException(404, f"fator sem dados para {dimensao}={chave}") from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    _auditar_drill(db, ctx, "data.beneficiary.drilldown",
                   {"dimensao": dimensao, "chave": chave, "competencia": competencia.isoformat()})
    return sanitize(res, ctx.features)


@router.get(
    "/explain/{dimensao}/{chave}/causas",
    summary="Por que o fator mudou? Coortes de beneficiários com FATO/HIPÓTESE/A_INVESTIGAR",
    dependencies=[Depends(require_feature("advanced_explanations"))],
)
def explain_causas(
    dimensao: str,
    chave: str,
    competencia: date = Depends(competencia_dep),
    comparacao: str = Depends(comparacao_dep),
    contrato_id: int | None = Depends(contrato_id_dep),
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_tenant_db),
) -> dict:
    if dimensao not in decomposition.DIMENSOES_VALIDAS:
        raise HTTPException(422, f"dimensão inválida: {dimensao}")
    exigir_feature_da_dimensao(ctx, dimensao)
    res = sanitize(
        cohorts.analisar_causas(db, dimensao, chave, competencia, comparacao, contrato_id),
        ctx.features,
    )
    _auditar_drill(db, ctx, "data.beneficiary.cohorts",
                   {"dimensao": dimensao, "chave": chave, "competencia": competencia.isoformat()})
    return res


def _auditar_drill(db: Session, ctx: TenantContext, action: str, details: dict) -> None:
    """Só audita quando a resposta inclui amostras individuais (feature de beneficiário ativa)."""
    if ctx.has_feature(BENEFICIARY):
        audit.data_access(db.get_bind(), actor=ctx.actor, tenant_id=ctx.tenant_id, action=action,
                          entity_type="beneficiario", details=details)
