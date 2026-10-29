"""Agregador de rotas da API v1."""

from fastapi import APIRouter

from app.api.v1.routes import (
    admin,
    admin_integrations,
    auth,
    beneficiarios,
    config,
    contratos,
    empresas,
    executive,
    health,
    insights,
    investigacao,
    notificacoes,
    planos_acao,
    prestadores,
    procedimentos,
    public,
    receitas,
    sinistralidade,
    tenant_admin,
)

api_v1_router = APIRouter()
api_v1_router.include_router(health.router)
api_v1_router.include_router(public.router)
api_v1_router.include_router(auth.router)
api_v1_router.include_router(admin.router)
api_v1_router.include_router(admin_integrations.router)
api_v1_router.include_router(tenant_admin.router)
api_v1_router.include_router(executive.router)
api_v1_router.include_router(sinistralidade.router)
api_v1_router.include_router(procedimentos.router)
api_v1_router.include_router(prestadores.router)
api_v1_router.include_router(beneficiarios.router)
api_v1_router.include_router(contratos.router)
api_v1_router.include_router(empresas.router)
api_v1_router.include_router(receitas.router)
api_v1_router.include_router(insights.router)
api_v1_router.include_router(investigacao.router)
api_v1_router.include_router(planos_acao.router)
api_v1_router.include_router(notificacoes.router)
api_v1_router.include_router(config.router)
