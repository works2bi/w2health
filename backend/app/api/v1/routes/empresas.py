"""Empresas (Consolidação V1) — agrupamento de contratos sem dupla contagem.

Feature `contract_intelligence`. Empresa de outro tenant é indistinguível de inexistente.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.analytics import empresas
from app.api.v1.routes._common import analytics_guard, competencia_dep
from app.security.deps import get_tenant_db

router = APIRouter(
    prefix="/analytics/empresas", tags=["Empresas"],
    dependencies=analytics_guard("contract_intelligence"),
)


@router.get("", summary="Empresas: contratos, vidas, despesa, receita e sinistralidade (se completa)")
def listar(competencia: date = Depends(competencia_dep), db: Session = Depends(get_tenant_db)) -> dict:
    return empresas.listar(db, competencia)


@router.get("/{id_empresa}", summary="Detalhe da empresa: comparação dos contratos e histórico de associação")
def detalhe(id_empresa: int, competencia: date = Depends(competencia_dep),
            db: Session = Depends(get_tenant_db)) -> dict:
    try:
        return empresas.detalhe(db, id_empresa, competencia)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e
