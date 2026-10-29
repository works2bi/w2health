"""Alertas por e-mail: assinatura pessoal, prévia, envio e histórico (Evolução, item 4).

* feature `alerts` obrigatória;
* cada usuário (VIEWER+) gerencia a PRÓPRIA assinatura — e-mail vem da sessão, nunca do payload;
* "enviar agora" e o histórico de envios: `alert_rules:write` / `alert_rules:read`.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics import notificacoes as conteudo
from app.api.v1.routes._common import analytics_guard, competencia_dep
from app.core.config import get_settings
from app.models.gestao import AssinaturaAlerta, EnvioNotificacao
from app.saas import audit
from app.saas import notificacoes as envio
from app.schemas.gestao import AssinaturaIn, AssinaturaOut
from app.security.deps import TenantContext, get_tenant_context, get_tenant_db, require_permission
from app.security.rbac import Perm

router = APIRouter(prefix="/notificacoes", tags=["Alertas por E-mail"], dependencies=analytics_guard("alerts"))


def _minha(db: Session, ctx: TenantContext) -> AssinaturaAlerta | None:
    return db.execute(select(AssinaturaAlerta).where(
        AssinaturaAlerta.tenant_id == ctx.tenant_id,
        AssinaturaAlerta.user_id == ctx.principal.user_id)).scalar_one_or_none()


@router.get("/minha-assinatura", summary="Assinatura de alertas do usuário logado (ou null)")
def obter_assinatura(ctx: TenantContext = Depends(get_tenant_context),
                     db: Session = Depends(get_tenant_db)) -> dict:
    a = _minha(db, ctx)
    return {"assinatura": AssinaturaOut.model_validate(a).model_dump() if a else None,
            "email": ctx.principal.email,
            "transporte": "smtp" if get_settings().smtp_host else "simulado"}


@router.put("/minha-assinatura", summary="Cria ou altera a assinatura do usuário logado")
def salvar_assinatura(payload: AssinaturaIn, ctx: TenantContext = Depends(get_tenant_context),
                      db: Session = Depends(get_tenant_db)) -> dict:
    a = _minha(db, ctx)
    if a is None:
        a = AssinaturaAlerta(tenant_id=ctx.tenant_id, user_id=ctx.principal.user_id,
                             email=ctx.principal.email)
        db.add(a)
    for k, v in payload.model_dump().items():
        setattr(a, k, v)
    a.email = ctx.principal.email
    db.flush()
    audit.add(db, "alert_subscription.saved", actor=ctx.actor, tenant_id=ctx.tenant_id,
              entity_type="assinatura_alerta", entity_id=a.id, details=payload.model_dump())
    db.commit()
    db.refresh(a)
    return {"assinatura": AssinaturaOut.model_validate(a).model_dump(), "email": a.email}


@router.get("/previa", summary="Prévia do resumo que o usuário receberia nesta competência")
def previa(
    competencia: date = Depends(competencia_dep),
    severidade_minima: Literal["critica", "atencao", "informativo"] = Query("atencao"),
    incluir_insights: bool = Query(True),
    frequencia: Literal["semanal", "mensal"] = Query("semanal"),
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_tenant_db),
) -> dict:
    resumo = conteudo.montar_resumo(db, competencia, ctx.features, severidade_minima=severidade_minima,
                                    incluir_insights=incluir_insights,
                                    base_url=get_settings().app_public_url)
    assunto, texto, html = conteudo.renderizar(resumo, ctx.tenant_name, frequencia)
    return {**resumo, "assunto": assunto, "texto": texto, "html": html}


@router.post("/enviar", summary="Envia agora os resumos das assinaturas ativas de uma frequência")
def enviar_agora(
    frequencia: Literal["semanal", "mensal"] = Query("semanal"),
    forcar: bool = Query(False, description="Reenvia mesmo se já enviado nesta janela"),
    competencia: date = Depends(competencia_dep),
    ctx: TenantContext = Depends(require_permission(Perm.ALERT_RULES_WRITE)),
    db: Session = Depends(get_tenant_db),
) -> dict:
    res = envio.executar(db, tenant_id=ctx.tenant_id, tenant_nome=ctx.tenant_name, features=ctx.features,
                         frequencia=frequencia, competencia=competencia, forcar=forcar)
    audit.add(db, "alert_digest.sent", actor=ctx.actor, tenant_id=ctx.tenant_id,
              entity_type="envio_notificacao", details=res)
    db.commit()
    return res


@router.get("/envios", summary="Histórico dos envios (sem o corpo das mensagens)")
def historico(
    limite: int = Query(50, ge=1, le=500),
    _ctx: TenantContext = Depends(require_permission(Perm.ALERT_RULES_READ)),
    db: Session = Depends(get_tenant_db),
) -> dict:
    from app.db.tenant_scope import tenant_of

    rows = db.execute(select(EnvioNotificacao).where(EnvioNotificacao.tenant_id == tenant_of(db))
                      .order_by(EnvioNotificacao.enviado_em.desc(), EnvioNotificacao.id.desc())
                      .limit(limite)).scalars().all()
    return {"itens": [{
        "id": r.id, "competencia": r.competencia.isoformat(), "destinatario": r.destinatario,
        "assunto": r.assunto, "status": r.status, "itens": r.itens, "erro": r.erro,
        "enviado_em": r.enviado_em.isoformat() if r.enviado_em else None,
    } for r in rows]}
