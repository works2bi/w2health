"""Investigação inteligente de custos (Evolução V1) — POR QUE a despesa mudou.

Mesmos guardas das explicações avançadas: `analytics:read` + `loss_ratio_intelligence`
(router) + `advanced_explanations` (rota); escopo de contrato exige `contract_intelligence`
e as dimensões `prestador`/`contrato` exigem as features próprias. Sem
`provider_intelligence` o bloco de rede e os nomes de prestador nas conclusões não são
gerados; sem `beneficiary_intelligence` as amostras de beneficiário saem pelo `sanitize`.
Acesso com amostra individual é auditado.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.analytics import decomposition, investigacao
from app.api.v1.routes._common import (
    analytics_guard,
    competencia_dep,
    contrato_id_dep,
    exigir_feature_da_dimensao,
)
from app.repositories.investigacao_repo import NIVEIS_REDE
from app.saas import audit
from app.saas.feature_filters import BENEFICIARY, CONTRACT, PROVIDER, sanitize
from app.security.deps import (
    TenantContext,
    feature_error,
    get_tenant_context,
    get_tenant_db,
    require_feature,
)

router = APIRouter(
    prefix="/analytics/investigacao", tags=["Investigação de custos"],
    dependencies=analytics_guard("loss_ratio_intelligence"),
)


@router.get(
    "",
    summary="Por que a despesa mudou? Decomposição que reconcilia + histórico + rede + "
            "coortes + conclusões FATO/HIPÓTESE/INVESTIGAR",
    dependencies=[Depends(require_feature("advanced_explanations"))],
)
def investigar(
    competencia: date = Depends(competencia_dep),
    comparacao: str = Query("mes_anterior", description="mes_anterior (= período anterior) | "
                                                        "periodo_anterior | ano_anterior"),
    meses: int = Query(1, description="Tamanho da janela em competências: 1, 3, 6 ou 12."),
    dimensao: str | None = Query(None, description="Fator do escopo (com `chave`)."),
    chave: str | None = Query(None),
    contrato_id: int | None = Depends(contrato_id_dep),
    nivel_rede: str = Query("executante", description="executante | estabelecimento"),
    empresa_id: int | None = Query(None, description="contratos da empresa (contratante) em cada competência"),
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_tenant_db),
) -> dict:
    if comparacao not in investigacao.COMPARACOES:
        raise HTTPException(422, f"comparacao inválida. Use: {', '.join(investigacao.COMPARACOES)}")
    if meses not in investigacao.JANELAS:
        raise HTTPException(422, f"meses inválido. Use: {', '.join(map(str, investigacao.JANELAS))}")
    if nivel_rede not in NIVEIS_REDE:
        raise HTTPException(422, f"nivel_rede inválido. Use: {', '.join(NIVEIS_REDE)}")
    if (dimensao is None) != (chave is None):
        raise HTTPException(422, "informe dimensao e chave juntas")
    if dimensao is not None and dimensao not in decomposition.DIMENSOES_VALIDAS:
        raise HTTPException(422, f"dimensão inválida: {dimensao}")
    exigir_feature_da_dimensao(ctx, dimensao)
    if empresa_id is not None:
        if not ctx.has_feature(CONTRACT):
            raise feature_error(ctx, CONTRACT)
        if contrato_id is not None:
            raise HTTPException(422, "use contrato_id OU empresa_id")
        if db.execute(text("SELECT 1 FROM empresas WHERE tenant_id = :t AND id = :e"),
                      {"t": ctx.tenant_id, "e": empresa_id}).first() is None:
            raise HTTPException(404, f"Empresa inexistente: {empresa_id}")
    try:
        res = investigacao.investigar(
            db, competencia, comparacao, meses, dimensao, chave, contrato_id, nivel_rede,
            empresa_id=empresa_id,
            incluir_prestadores=ctx.has_feature(PROVIDER),
            incluir_beneficiarios=ctx.has_feature(BENEFICIARY),
        )
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    res = sanitize(res, ctx.features)
    if ctx.has_feature(BENEFICIARY):
        audit.data_access(db.get_bind(), actor=ctx.actor, tenant_id=ctx.tenant_id,
                          action="data.beneficiary.investigation", entity_type="beneficiario",
                          details={"competencia": competencia.isoformat(), "meses": meses,
                                   "dimensao": dimensao, "chave": chave,
                                   "contrato_id": contrato_id})
    return res
