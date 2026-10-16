"""Insights automáticos + concentração + catálogos + metadados + transparência.

Fundação SaaS V1: todas as rotas exigem `analytics:read` no tenant do contexto; insights
exigem a feature `insights`; concentração exige o módulo da base pedida; catálogos são
lidos SEMPRE filtrados pelo tenant; o gabarito de cenários (QA) só existe para tenants
marcados como sintéticos.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.analytics import beneficiaries, transparency
from app.analytics import insights as insights_mod
from app.api.v1.routes._common import analytics_guard, comparacao_dep, competencia_dep
from app.db.tenant_scope import tenant_of
from app.repositories import analytics_repo as repo
from app.saas.feature_filters import BENEFICIARY, CONTRACT, PROVIDER, filter_insights
from app.security.deps import (
    TenantContext,
    feature_error,
    get_tenant_context,
    get_tenant_db,
    require_feature,
)
from app.security.errors import ApiError

router = APIRouter(tags=["Insights & Metadados"], dependencies=analytics_guard())


@router.get("/analytics/insights", summary="Insights automáticos derivados dos dados",
            dependencies=[Depends(require_feature("insights"))])
def insights(
    competencia: date = Depends(competencia_dep),
    comparacao: str = Depends(comparacao_dep),
    severidade: str | None = Query(None, description="alta|media|baixa|positiva|info"),
    tipo: str | None = Query(None),
    limit: int = Query(20, ge=1, le=100),
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_tenant_db),
) -> dict:
    itens = filter_insights(insights_mod.gerar(db, competencia, comparacao), ctx.features)
    if severidade:
        itens = [i for i in itens if i["severidade"] == severidade]
    if tipo:
        itens = [i for i in itens if i["tipo"] == tipo]
    return {
        "competencia": competencia.isoformat(),
        "comparacao": comparacao,
        "total": len(itens),
        "itens": itens[:limit],
    }


@router.get("/analytics/concentracao", summary="Concentração de despesa (beneficiários ou prestadores)")
def concentracao(
    competencia: date = Depends(competencia_dep),
    base: str = Query("beneficiario", description="beneficiario|prestador"),
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_tenant_db),
) -> dict:
    exigida = PROVIDER if base == "prestador" else BENEFICIARY
    if not ctx.has_feature(exigida):
        raise feature_error(ctx, exigida)
    return beneficiaries.concentracao(db, competencia, base)


@router.get("/analytics/gabarito", summary="Gabarito dos cenários sintéticos plantados (QA/demo)")
def gabarito(ctx: TenantContext = Depends(get_tenant_context),
             db: Session = Depends(get_tenant_db)) -> dict:
    if not ctx.is_synthetic:
        raise ApiError("not_found")
    return {"itens": repo.gabarito(db)}


@router.get("/meta/competencias", summary="Competências disponíveis na base analítica")
def competencias(db: Session = Depends(get_tenant_db)) -> dict:
    cs = [c.isoformat() for c in repo.competencias(db)]
    return {"itens": cs, "primeira": cs[0] if cs else None, "ultima": cs[-1] if cs else None}


@router.get("/meta/transparencia", summary="Procedência dos dados e definições dos indicadores")
def meta_transparencia(ctx: TenantContext = Depends(get_tenant_context),
                       db: Session = Depends(get_tenant_db)) -> dict:
    return transparency.procedencia(db, is_synthetic=ctx.is_synthetic)


# Catálogos: SQL fixo por nome (sem interpolação de entrada) e SEMPRE com tenant.
_CATALOGOS: dict[str, tuple[str, str | None]] = {
    "planos": ("SELECT id, nome FROM planos WHERE tenant_id = :t ORDER BY nome", None),
    "contratos": (
        "SELECT ct.id, ct.nome || ' (' || string_agg(pl.nome, ', ' ORDER BY pl.nome) || ')' AS nome "
        "FROM contratos ct "
        "JOIN planos_contratos pc ON pc.id_contrato = ct.id AND pc.tenant_id = ct.tenant_id "
        "JOIN planos pl ON pl.id = pc.id_plano AND pl.tenant_id = pc.tenant_id "
        "WHERE ct.tenant_id = :t GROUP BY ct.id, ct.nome ORDER BY ct.nome", CONTRACT),
    "regioes": ("SELECT id, cidade || '/' || uf AS nome FROM regioes WHERE tenant_id = :t ORDER BY nome", None),
    "especialidades": ("SELECT id, nome FROM especialidades WHERE tenant_id = :t ORDER BY nome", None),
    "grupos-despesa": (
        "SELECT DISTINCT grupo_procedimento AS id, grupo_procedimento AS nome FROM procedimentos "
        "WHERE tenant_id = :t ORDER BY 1", None),
}


@router.get("/catalogos/{nome}", summary="Catálogos para filtros (planos, regioes, especialidades, ...)")
def catalogos(nome: str, ctx: TenantContext = Depends(get_tenant_context),
              db: Session = Depends(get_tenant_db)) -> dict:
    if nome == "faixas-etarias":
        from app.core.faixas import FAIXA_LABELS

        return {"itens": [{"id": f, "nome": f} for f in FAIXA_LABELS]}
    if nome == "dimensoes":
        from app.analytics.decomposition import DIMENSOES_VALIDAS

        return {"itens": [{"id": d, "nome": d} for d in DIMENSOES_VALIDAS]}
    entrada = _CATALOGOS.get(nome)
    if entrada is None:
        return {"itens": []}
    sql, feature = entrada
    if feature is not None and not ctx.has_feature(feature):
        raise feature_error(ctx, feature)
    rows = db.execute(text(sql), {"t": tenant_of(db)}).mappings().all()
    return {"itens": [dict(r) for r in rows]}
