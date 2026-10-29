"""Modelos ORM do W2Health Intelligence.

Camada fonte (OLTP-like): catálogos, carteira, eventos assistenciais, receitas.
Camada analítica: agregações mensais, gabarito de cenários e manifesto do seed.
Data Platform (v1.2): controle de ingestão (estrutura, não uso).
Control plane (Fundação SaaS V1, `ControlBase`): tenants, usuários, sessões, planos,
features, branding, configurações, segredos e auditoria — `app/models/saas.py`.
"""

from app.models.analytics import (
    AggBeneficiarioCompetencia,
    AggCompetenciaDimensao,
    AggContratoCompetencia,
    AggPrestadorCompetencia,
    AggSinistralidadeCompetencia,
    CenarioGabarito,
    SeedManifest,
)
from app.models.benchmark import BenchmarkMercado
from app.models.catalog import (
    Contrato,
    Diagnostico,
    Especialidade,
    Plano,
    PlanoContrato,
    PrecoContratado,
    Prestador,
    Procedimento,
    Regiao,
)
from app.models.config import RegraAlerta
from app.models.events import EventoAssistencial
from app.models.financeiro import (
    Empresa,
    EmpresaContrato,
    Fatura,
    FaturaItem,
    Recebimento,
    ReceitaReconhecida,
)
from app.models.gestao import AssinaturaAlerta, EnvioNotificacao, PlanoAcao
from app.models.jobs import PipelineJob, WorkerHeartbeat
from app.models.membership import Beneficiario, BeneficiarioVinculo, Competencia, Receita
from app.models.platform import (
    CapabilityReadiness,
    DataQualityResult,
    IngestionRun,
    PipelineRun,
    RawObject,
    ReconciliationResult,
    SourceConnection,
    SourceEntity,
    Tenant,
)
from app.models.saas import (
    AuditLog,
    AuthRateLimit,
    AuthSession,
    Feature,
    Plan,
    PlanFeature,
    RefreshToken,
    TenantBranding,
    TenantBrandingAsset,
    TenantFeature,
    TenantOnboarding,
    TenantSecret,
    TenantSetting,
    User,
    UserRecoveryCode,
    UserTenant,
)

__all__ = [
    "PipelineJob",
    "WorkerHeartbeat",
    "RegraAlerta",
    "BenchmarkMercado",
    "PlanoAcao",
    "AssinaturaAlerta",
    "EnvioNotificacao",
    "Regiao",
    "Plano",
    "Contrato",
    "PlanoContrato",
    "PrecoContratado",
    "Especialidade",
    "Procedimento",
    "Prestador",
    "Diagnostico",
    "Competencia",
    "Beneficiario",
    "BeneficiarioVinculo",
    "Receita",
    "EventoAssistencial",
    "Empresa",
    "EmpresaContrato",
    "Fatura",
    "FaturaItem",
    "Recebimento",
    "ReceitaReconhecida",
    "AggSinistralidadeCompetencia",
    "AggCompetenciaDimensao",
    "AggPrestadorCompetencia",
    "AggBeneficiarioCompetencia",
    "AggContratoCompetencia",
    "CenarioGabarito",
    "SeedManifest",
    # Data Platform (v1.2)
    "Tenant",
    "SourceConnection",
    "SourceEntity",
    "IngestionRun",
    "PipelineRun",
    "DataQualityResult",
    # Control plane — Fundação SaaS V1
    "User",
    "UserTenant",
    "AuthSession",
    "RefreshToken",
    "Plan",
    "Feature",
    "PlanFeature",
    "TenantFeature",
    "TenantBranding",
    "TenantBrandingAsset",
    "TenantSetting",
    "TenantSecret",
    "AuditLog",
    # Fase 2
    "RawObject",
    "ReconciliationResult",
    "CapabilityReadiness",
    "TenantOnboarding",
    "UserRecoveryCode",
    "AuthRateLimit",
]
