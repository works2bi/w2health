"""Contract Intelligence (v1.2). Sem receita por contrato — ver docstring de
`app.analytics.contratos`."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.analytics import contratos as ctr
from app.analytics import reajuste
from app.api.v1.routes._common import analytics_guard, comparacao_dep, competencia_dep
from app.saas import audit
from app.saas.feature_filters import BENEFICIARY, filter_alertas, sanitize
from app.security.deps import TenantContext, get_tenant_context, get_tenant_db

router = APIRouter(
    prefix="/analytics/contratos", tags=["Contratos"],
    dependencies=analytics_guard("contract_intelligence"),
)


@router.get("", summary="Lista de contratos: vidas, despesa (bruta/glosa/copart/líquida), concentração")
def listar(
    competencia: date = Depends(competencia_dep),
    comparacao: str = Depends(comparacao_dep),
    db: Session = Depends(get_tenant_db),
) -> dict:
    return ctr.listar(db, competencia, comparacao)


@router.get("/{id_contrato}", summary="Detalhe do contrato: KPIs, evolução, concentração, drivers, alertas")
def detalhe(
    id_contrato: int,
    competencia: date = Depends(competencia_dep),
    comparacao: str = Depends(comparacao_dep),
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_tenant_db),
) -> dict:
    try:
        res = ctr.detalhe(db, id_contrato, competencia, comparacao)
    except ValueError as e:
        # contrato de outro tenant é indistinguível de inexistente
        raise HTTPException(404, str(e)) from e
    res = sanitize(res, ctx.features)
    if ctx.has_feature(BENEFICIARY):  # o detalhe traz os maiores beneficiários do contrato
        audit.data_access(db.get_bind(), actor=ctx.actor, tenant_id=ctx.tenant_id,
                          action="data.beneficiary.contract_top", entity_type="contrato",
                          entity_id=id_contrato, details={"competencia": competencia.isoformat()})
    if ctx.has_feature("alerts"):
        res["alertas"] = filter_alertas(res.get("alertas") or [], ctx.features)
    else:
        res["alertas"] = []
        res["restricoes_plano"] = sorted({*res.get("restricoes_plano", []), "alerts"})
    return res


@router.get("/{id_contrato}/simulacao-reajuste",
            summary="Simulador de reajuste: meta de sinistralidade × VCMH → reajuste técnico")
def simulacao_reajuste(
    id_contrato: int,
    meta: float = Query(75.0, description="Meta de sinistralidade (%)"),
    vcmh: float = Query(10.0, description="Variação do custo médico-hospitalar projetada (%)"),
    meses: int = Query(12, ge=3, le=24, description="Meses de histórico na base"),
    ajustar_completude: bool = Query(True, description="Completa meses preliminares com o IBNR"),
    receita_mensal: float | None = Query(None, gt=0, description="Receita mensal informada "
                                         "(só quando o contrato não tem receita atribuída)"),
    competencia: date = Depends(competencia_dep),
    db: Session = Depends(get_tenant_db),
) -> dict:
    try:
        return reajuste.simular(db, id_contrato, competencia, meta=meta, vcmh=vcmh, meses=meses,
                                ajustar_completude=ajustar_completude,
                                receita_informada=receita_mensal)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
