"""Planos de ação sobre insights/alertas + medição de efeito (Evolução, item 3).

* leitura: `analytics:read` (VIEWER+) · escrita: `action_plans:write` (MANAGER+);
* feature `insights` obrigatória (o plano nasce de um achado);
* toda criação/alteração/exclusão é auditada na mesma transação.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics import efeito_acao
from app.api.v1.routes._common import analytics_guard
from app.db.tenant_scope import tenant_of
from app.models.gestao import PlanoAcao
from app.saas import audit
from app.schemas.gestao import PlanoAcaoCreate, PlanoAcaoOut, PlanoAcaoUpdate
from app.security.deps import TenantContext, get_tenant_db, require_permission
from app.security.rbac import Perm

router = APIRouter(prefix="/planos-acao", tags=["Planos de Ação"], dependencies=analytics_guard("insights"))

_ESCREVER = require_permission(Perm.ACTION_PLANS_WRITE)


def _obter(db: Session, id_plano: int) -> PlanoAcao:
    p = db.execute(select(PlanoAcao).where(PlanoAcao.id == id_plano,
                                           PlanoAcao.tenant_id == tenant_of(db))).scalar_one_or_none()
    if p is None:
        raise HTTPException(404, "plano de ação não encontrado")
    return p


def _out(db: Session, p: PlanoAcao) -> dict:
    d = PlanoAcaoOut.model_validate(p).model_dump(mode="json")
    d["efeito"] = efeito_acao.medir(db, p)
    return d


@router.get("", summary="Lista os planos de ação do tenant (com a medição de efeito)")
def listar(db: Session = Depends(get_tenant_db)) -> dict:
    planos = db.execute(select(PlanoAcao).where(PlanoAcao.tenant_id == tenant_of(db))
                        .order_by(PlanoAcao.criado_em.desc(), PlanoAcao.id.desc())).scalars().all()
    itens = [_out(db, p) for p in planos]
    return {"total": len(itens), "itens": itens}


@router.get("/{id_plano}", summary="Detalhe do plano de ação com a medição de efeito")
def obter(id_plano: int, db: Session = Depends(get_tenant_db)) -> dict:
    return _out(db, _obter(db, id_plano))


@router.post("", status_code=201, summary="Cria um plano de ação")
def criar(payload: PlanoAcaoCreate, ctx: TenantContext = Depends(_ESCREVER),
          db: Session = Depends(get_tenant_db)) -> dict:
    dados = payload.model_dump()
    if bool(dados["dimensao"]) != bool(dados["chave"]):
        raise HTTPException(422, "informe dimensão e chave juntas (ou nenhuma, para a carteira)")
    dados["competencia_inicio"] = dados["competencia_inicio"].replace(day=1)
    dados["alvo_rotulo"] = dados["alvo_rotulo"] or (
        f"{dados['dimensao']}: {dados['chave']}" if dados["dimensao"] else "Carteira")
    p = PlanoAcao(**dados, tenant_id=ctx.tenant_id, criado_por=ctx.principal.email)
    db.add(p)
    db.flush()
    audit.add(db, "action_plan.created", actor=ctx.actor, tenant_id=ctx.tenant_id,
              entity_type="plano_acao", entity_id=p.id,
              details={"origem": p.origem_tipo, "alvo": p.alvo_rotulo})
    db.commit()
    db.refresh(p)
    return _out(db, p)


@router.patch("/{id_plano}", summary="Atualiza status, responsável, prazo ou meta")
def atualizar(id_plano: int, payload: PlanoAcaoUpdate, ctx: TenantContext = Depends(_ESCREVER),
              db: Session = Depends(get_tenant_db)) -> dict:
    p = _obter(db, id_plano)
    dados = payload.model_dump(exclude_unset=True)
    if dados.get("competencia_inicio"):
        dados["competencia_inicio"] = dados["competencia_inicio"].replace(day=1)
    for k, v in dados.items():
        setattr(p, k, v)
    audit.add(db, "action_plan.updated", actor=ctx.actor, tenant_id=ctx.tenant_id,
              entity_type="plano_acao", entity_id=p.id, details={"campos": sorted(dados)})
    db.commit()
    db.refresh(p)
    return _out(db, p)


@router.delete("/{id_plano}", status_code=204, response_model=None, summary="Exclui um plano de ação")
def excluir(id_plano: int, ctx: TenantContext = Depends(_ESCREVER),
            db: Session = Depends(get_tenant_db)) -> None:
    p = _obter(db, id_plano)
    audit.add(db, "action_plan.deleted", actor=ctx.actor, tenant_id=ctx.tenant_id,
              entity_type="plano_acao", entity_id=p.id, details={"titulo": p.titulo})
    db.delete(p)
    db.commit()
