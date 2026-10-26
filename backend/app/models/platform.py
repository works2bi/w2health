"""Data Platform — modelo de controle de ingestão.

**Fase 2: em uso.** `source_connections`, `ingestion_runs`, `raw_objects`, `pipeline_runs`,
`data_quality_results`, `reconciliation_results` e `capability_readiness` são os metadados
operacionais da Data Platform (`app/data_platform/`). Todas tenant-scoped + RLS.

`Tenant` é o cadastro de clientes — pertence ao **control plane** (`ControlBase`) desde a
Fundação SaaS V1. As demais tabelas (data plane) rastreiam cargas por tenant.
O antigo layout `receitas_contrato` virou `receitas_reconhecidas` em `app/models/financeiro.py`
(Consolidação do canônico V1).
"""

from __future__ import annotations

import uuid as uuid_mod
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, ControlBase
from app.models._mixins import TenantMixin


class Tenant(ControlBase):
    """Cadastro de clientes (operadoras) — control plane.

    `id` é o **código** (slug) imutável do tenant e o valor gravado em `tenant_id` em todas
    as tabelas do data plane (ex.: `w2h-demo`). `uuid` é o identificador público estável.
    Decisão conservadora da Fundação SaaS V1: manter o slug como chave técnica evita
    reescrever 24 colunas/~330 mil linhas — ver `docs/DATABASE_EVOLUTION_V1.md`.
    """

    __tablename__ = "tenants"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE', 'SUSPENDED', 'INACTIVE')", name="status_valido"
        ),
        CheckConstraint("id ~ '^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$'", name="codigo_valido"),
    )

    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # código/slug imutável
    uuid: Mapped[uuid_mod.UUID] = mapped_column(
        Uuid, unique=True, default=uuid_mod.uuid4, server_default=text("gen_random_uuid()")
    )
    name: Mapped[str] = mapped_column(String(120))
    legal_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", server_default="ACTIVE")
    plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("plans.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    # Massa sintética (demonstração/testes) — exibido em toda a interface (transparência).
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    @property
    def code(self) -> str:
        return self.id


class SourceConnection(TenantMixin, Base):
    """Uma fonte de dados de um tenant (FILE, DATABASE, API ou SYNTHETIC).

    `configuration` guarda SÓ parâmetros não sensíveis (mapping usado, formato...).
    Credenciais ficam no cofre (`tenant_secrets`, cifradas) e aqui entram apenas como
    `secret_reference` (ex.: `tenant:erp.api_token`) — ver app/core/secrets.py.
    """

    __tablename__ = "source_connections"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_srcconn_tenant_name"),
        CheckConstraint("source_type IN ('FILE', 'DATABASE', 'API', 'SYNTHETIC')",
                        name="source_type_valido"),
        CheckConstraint("status IN ('ACTIVE', 'DISABLED')", name="status_valido"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    source_type: Mapped[str] = mapped_column(String(30))
    source_system: Mapped[str] = mapped_column(String(60))  # rótulo livre da origem
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE", server_default="ACTIVE")
    configuration: Mapped[dict] = mapped_column(JSON, default=dict)
    secret_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_summary: Mapped[str | None] = mapped_column(String(300), nullable=True)


class SourceEntity(TenantMixin, Base):
    """Mapeamento de uma entidade de origem para uma entidade canônica + estratégia de carga."""

    __tablename__ = "source_entities"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "source_system", "source_entity", name="uq_srcent_tenant_sys_ent"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_system: Mapped[str] = mapped_column(String(60))
    source_entity: Mapped[str] = mapped_column(String(80))
    target_entity: Mapped[str] = mapped_column(String(60))  # entidade canônica W2Health
    load_strategy: Mapped[str] = mapped_column(String(20), default="FULL")
    business_key: Mapped[str] = mapped_column(String(200), default="")
    watermark_column: Mapped[str | None] = mapped_column(String(80), nullable=True)
    updated_at_column: Mapped[str | None] = mapped_column(String(80), nullable=True)
    deduplication_strategy: Mapped[str] = mapped_column(String(120), default="business_key")
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)


INGESTION_STATUS = ("PENDING", "RUNNING", "SUCCESS", "PARTIAL", "FAILED")
#: estágio de disponibilidade da carga — nada é "sucesso" só porque o arquivo chegou
INGESTION_STAGES = ("RECEIVED", "VALIDATED", "PROCESSED", "RECONCILED", "AVAILABLE")


class IngestionRun(TenantMixin, Base):
    """Uma ingestão (pacote de arquivos ou execução do gerador) de uma fonte."""

    __tablename__ = "ingestion_runs"
    __table_args__ = (
        # Fase 3: QUEUED (aguardando worker) e CANCELLED (cancelamento administrativo).
        # PENDING permanece só por compatibilidade com linhas antigas.
        CheckConstraint("status IN ('PENDING', 'QUEUED', 'RUNNING', 'SUCCESS', 'PARTIAL', 'FAILED', "
                        "'CANCELLED')", name="status_valido"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_connection_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_connections.id"), nullable=True, index=True
    )
    source_system: Mapped[str] = mapped_column(String(60))
    entity: Mapped[str | None] = mapped_column(String(80), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", server_default="PENDING")
    stage: Mapped[str | None] = mapped_column(String(20), nullable=True)
    records_received: Mapped[int] = mapped_column(Integer, default=0)
    records_valid: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    records_inserted: Mapped[int] = mapped_column(Integer, default=0)
    records_updated: Mapped[int] = mapped_column(Integer, default=0)
    records_rejected: Mapped[int] = mapped_column(Integer, default=0)
    warnings_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    errors_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    checksum: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    triggered_by: Mapped[str | None] = mapped_column(String(254), nullable=True)
    mapping_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    competencia_inicio: Mapped[date | None] = mapped_column(Date, nullable=True)
    competencia_fim: Mapped[date | None] = mapped_column(Date, nullable=True)
    duplicate_of: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    watermark: Mapped[str | None] = mapped_column(String(80), nullable=True)
    batch_id: Mapped[str | None] = mapped_column(String(80), nullable=True)


class RawObject(TenantMixin, Base):
    """Metadados de um objeto RAW (o conteúdo fica no RawStorage, nunca no banco)."""

    __tablename__ = "raw_objects"
    __table_args__ = (UniqueConstraint("storage_key", name="uq_raw_objects_storage_key"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_connection_id: Mapped[int] = mapped_column(ForeignKey("source_connections.id"), index=True)
    ingestion_run_id: Mapped[int] = mapped_column(ForeignKey("ingestion_runs.id"), index=True)
    source_entity: Mapped[str] = mapped_column(String(80))
    storage_key: Mapped[str] = mapped_column(String(400))
    file_name: Mapped[str] = mapped_column(String(200))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    records: Mapped[int] = mapped_column(Integer, default=0)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PipelineRun(TenantMixin, Base):
    """Uma execução de pipeline (raw→mapping→DQ→silver→gold→serving→reconciliação).
    `steps` registra cada estágio com status, contagens e duração."""

    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ingestion_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("ingestion_runs.id"), nullable=True, index=True
    )
    source_connection_id: Mapped[int | None] = mapped_column(
        ForeignKey("source_connections.id"), nullable=True, index=True
    )
    layer: Mapped[str] = mapped_column(String(20))  # último estágio alcançado
    entity: Mapped[str | None] = mapped_column(String(80), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="RUNNING")
    rows_in: Mapped[int] = mapped_column(Integer, default=0)
    rows_out: Mapped[int] = mapped_column(Integer, default=0)
    triggered_by: Mapped[str | None] = mapped_column(String(254), nullable=True)
    correlation_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    steps: Mapped[list] = mapped_column(JSON, default=list)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)


class DataQualityResult(TenantMixin, Base):
    """Resultado de uma regra de qualidade numa execução de pipeline. `sample` guarda só
    referências (linha, id de origem, motivo) — nunca o payload completo."""

    __tablename__ = "data_quality_results"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    pipeline_run_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    ingestion_run_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    rule_id: Mapped[str] = mapped_column(String(60))
    rule_description: Mapped[str | None] = mapped_column(String(300), nullable=True)
    entity: Mapped[str] = mapped_column(String(80))
    severity: Mapped[str] = mapped_column(String(10))  # ERROR | WARNING | INFO
    blocking: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    records_checked: Mapped[int] = mapped_column(Integer, default=0)
    records_failed: Mapped[int] = mapped_column(Integer, default=0)
    sample: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReconciliationResult(TenantMixin, Base):
    """Uma verificação de reconciliação (origem × Silver × Gold) de uma carga."""

    __tablename__ = "reconciliation_results"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    pipeline_run_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    ingestion_run_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True, index=True)
    check_id: Mapped[str] = mapped_column(String(60))
    entity: Mapped[str] = mapped_column(String(60))
    scope: Mapped[str] = mapped_column(String(40))  # "total" ou competência AAAA-MM
    expected: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    actual: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    difference: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    tolerance: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    status: Mapped[str] = mapped_column(String(10))  # PASS | WARNING | FAIL
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CapabilityReadiness(TenantMixin, Base):
    """Prontidão de DADOS por capability (independente do plano comercial)."""

    __tablename__ = "capability_readiness"
    __table_args__ = (UniqueConstraint("tenant_id", "feature_key", name="uq_capready_tenant_feature"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    feature_key: Mapped[str] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(12))  # READY | PARTIAL | NOT_READY
    reason: Mapped[str] = mapped_column(String(300), default="")
    checks: Mapped[dict] = mapped_column(JSON, default=dict)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

