"""Receita: composição por fonte/nível e reconciliação financeira (Consolidação V1).

Feature `financial_composition` (mesma da composição da despesa). Somente leitura; nenhum
valor individual de beneficiário é exposto.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.analytics import receitas
from app.api.v1.routes._common import analytics_guard, competencia_dep
from app.security.deps import get_tenant_db

router = APIRouter(
    prefix="/analytics/receitas", tags=["Receitas"],
    dependencies=analytics_guard("financial_composition"),
)


@router.get("/composicao", summary="Receita da competência por fonte e nível de atribuição; "
                                   "faturado e recebido à parte (não equivalentes)")
def composicao(competencia: date = Depends(competencia_dep),
               db: Session = Depends(get_tenant_db)) -> dict:
    try:
        return receitas.composicao(db, competencia)
    except ValueError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/reconciliacao", summary="Reconciliação financeira: receita, despesa e vidas")
def reconciliacao(competencia: date = Depends(competencia_dep),
                  db: Session = Depends(get_tenant_db)) -> dict:
    return receitas.reconciliacao(db, competencia)
