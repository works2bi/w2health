"""Fase 2: Data Platform operacional (fontes, ingestão, RAW, linhagem, DQ, reconciliação,
readiness, onboarding), papel de pipeline e itens de segurança (recovery codes, rate limit)

Aditiva e não destrutiva a partir de `e8b9c0d1f2a3`:

1. `source_connections`, `ingestion_runs`, `pipeline_runs`, `data_quality_results` evoluem
   (estavam vazias — renomes por ALTER, sem perda); novas `raw_objects`,
   `reconciliation_results`, `capability_readiness` (data plane, RLS).
2. Silver = tabelas canônicas existentes: + linhagem (`source_system`,
   `source_connection_id`, `ingestion_run_id`, `source_record_id`), backfill
   `synthetic_generator` nos dados existentes (todos vieram do gerador).
3. `contratos.codigo` e `prestadores.codigo` (chave de negócio do contrato canônico):
   coluna nula → backfill determinístico por tenant → NOT NULL → UNIQUE(tenant, codigo).
4. Colunas que só o gerador sabe preencher passam a aceitar nulo.
5. Índice único parcial `(tenant_id, source_connection_id, source_record_id)` em eventos.
6. Control plane: `tenant_onboarding` (backfill), `user_recovery_codes`, `auth_rate_limits`.
7. Papel `w2health_pipeline` (NOSUPERUSER, NOBYPASSRLS, sem DDL; senha em
   `PIPELINE_DB_PASSWORD`, sem ela NOLOGIN) + grants mínimos; grants novos do runtime.
   Com `DB_ROLES_PROVISIONED=true` o papel é criado pelo DBA e aqui só é verificado.

Revision ID: f4c5d6e7a8b9
Revises: e8b9c0d1f2a3
Create Date: 2026-10-05
"""
import os
import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f4c5d6e7a8b9"
down_revision: str | None = "e8b9c0d1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: novas tabelas do data plane (recebem a mesma política de RLS da e8b9c0d1f2a3)
NEW_DATA_PLANE_TABLES = ("capability_readiness", "raw_objects", "reconciliation_results")

CANONICAL_TABLES = ("regioes", "planos", "contratos", "especialidades", "procedimentos",
                    "prestadores", "diagnosticos", "beneficiarios", "receitas",
                    "eventos_assistenciais")
AGG_TABLES = ("agg_sinistralidade_competencia", "agg_competencia_dimensao",
              "agg_prestador_competencia", "agg_beneficiario_competencia",
              "agg_contrato_competencia")
PIPELINE_METADATA_RW = ("ingestion_runs", "raw_objects", "pipeline_runs",
                        "data_quality_results", "reconciliation_results",
                        "capability_readiness")

_EXPR = "tenant_id = current_setting('app.tenant_id', true)"
_ROLE_RE = re.compile(r"^[a-z_][a-z0-9_]{2,62}$")


def _role(env: str, default: str) -> str:
    r = os.environ.get(env, default)
    if not _ROLE_RE.match(r):
        raise RuntimeError(f"{env} inválido")
    return r


def _papeis_provisionados() -> bool:
    """`DB_ROLES_PROVISIONED=true`: o DBA cria os papéis; a migration só os verifica.

    Assim o dono do schema não precisa de SUPERUSER/CREATEROLE no cluster e nenhuma senha
    aparece em DDL (que pode ir para o log do servidor).
    """
    return os.environ.get("DB_ROLES_PROVISIONED", "").strip().lower() in ("1", "true", "yes")


def _verificar_papel(role: str) -> None:
    linha = op.get_bind().exec_driver_sql(
        "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb FROM pg_roles "
        f"WHERE rolname = '{role}'"
    ).first()
    if linha is None:
        raise RuntimeError(f"papel {role} não existe (DB_ROLES_PROVISIONED=true: crie-o antes da migration)")
    if any(linha):
        raise RuntimeError(f"papel {role} não pode ter SUPERUSER, BYPASSRLS, CREATEROLE nem CREATEDB")


def _ensure_role(role: str, password: str) -> None:
    if _papeis_provisionados():
        _verificar_papel(role)
        return
    attrs = "NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT"
    login = "LOGIN PASSWORD '" + password.replace("'", "''") + "'" if password else "NOLOGIN"
    existe = op.get_bind().exec_driver_sql(f"SELECT 1 FROM pg_roles WHERE rolname = '{role}'").first()
    if existe:
        op.execute(f"ALTER ROLE {role} {attrs}")
        if password:
            op.execute(f"ALTER ROLE {role} {login}")
    else:
        op.execute(f"CREATE ROLE {role} {login} {attrs}")


def upgrade() -> None:
    # ------------------------------------------------------------ 1. source_connections
    op.alter_column("source_connections", "tipo", new_column_name="source_type")
    op.alter_column("source_connections", "config", new_column_name="configuration")
    op.alter_column("source_connections", "criado_em", new_column_name="created_at")
    op.add_column("source_connections", sa.Column("name", sa.String(120), nullable=True))
    op.add_column("source_connections", sa.Column("status", sa.String(20), server_default="ACTIVE", nullable=False))
    op.add_column("source_connections", sa.Column("secret_reference", sa.String(120), nullable=True))
    op.add_column("source_connections", sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False))
    for col in ("last_run_at", "last_success_at", "last_error_at"):
        op.add_column("source_connections", sa.Column(col, sa.DateTime(timezone=True), nullable=True))
    op.add_column("source_connections", sa.Column("last_error_summary", sa.String(300), nullable=True))
    op.execute("""
        UPDATE source_connections SET
          source_type = CASE lower(source_type) WHEN 'db_sql' THEN 'DATABASE' WHEN 'api' THEN 'API'
                                                ELSE 'FILE' END,
          status = CASE WHEN ativo THEN 'ACTIVE' ELSE 'DISABLED' END,
          name = coalesce(name, source_system || '-' || id)
    """)
    op.drop_column("source_connections", "ativo")
    op.alter_column("source_connections", "name", nullable=False)
    op.create_unique_constraint("uq_srcconn_tenant_name", "source_connections", ["tenant_id", "name"])
    op.create_check_constraint("ck_source_connections_source_type_valido", "source_connections",
                               "source_type IN ('FILE', 'DATABASE', 'API', 'SYNTHETIC')")
    op.create_check_constraint("ck_source_connections_status_valido", "source_connections",
                               "status IN ('ACTIVE', 'DISABLED')")

    # ------------------------------------------------------------ ingestion_runs
    op.add_column("ingestion_runs", sa.Column("source_connection_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_ingestion_runs_source_connection_id_source_connections",
                          "ingestion_runs", "source_connections", ["source_connection_id"], ["id"])
    op.create_index("ix_ingestion_runs_source_connection_id", "ingestion_runs", ["source_connection_id"])
    op.alter_column("ingestion_runs", "entity", existing_type=sa.String(80), nullable=True)
    for name, type_, default in (
        ("stage", sa.String(20), None), ("records_valid", sa.Integer(), "0"),
        ("warnings_count", sa.Integer(), "0"), ("errors_count", sa.Integer(), "0"),
        ("checksum", sa.String(64), None), ("triggered_by", sa.String(254), None),
        ("mapping_ref", sa.String(80), None), ("competencia_inicio", sa.Date(), None),
        ("competencia_fim", sa.Date(), None), ("duplicate_of", sa.BigInteger(), None),
        ("correlation_id", sa.String(40), None),
    ):
        op.add_column("ingestion_runs", sa.Column(name, type_, nullable=default is None,
                                                  server_default=default))
    op.add_column("ingestion_runs", sa.Column("error_summary", sa.JSON(), nullable=False,
                                              server_default=sa.text("'{}'::json")))
    op.create_index("ix_ingestion_runs_checksum", "ingestion_runs", ["checksum"])
    op.execute("UPDATE ingestion_runs SET status = CASE lower(status) WHEN 'success' THEN 'SUCCESS' "
               "WHEN 'failed' THEN 'FAILED' WHEN 'running' THEN 'RUNNING' ELSE 'PENDING' END")
    op.alter_column("ingestion_runs", "status", server_default="PENDING")
    op.create_check_constraint("ck_ingestion_runs_status_valido", "ingestion_runs",
                               "status IN ('PENDING', 'RUNNING', 'SUCCESS', 'PARTIAL', 'FAILED')")

    # ------------------------------------------------------------ raw_objects
    op.create_table(
        "raw_objects",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(40), nullable=False),
        sa.Column("source_connection_id", sa.Integer(), nullable=False),
        sa.Column("ingestion_run_id", sa.BigInteger(), nullable=False),
        sa.Column("source_entity", sa.String(80), nullable=False),
        sa.Column("storage_key", sa.String(400), nullable=False),
        sa.Column("file_name", sa.String(200), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("records", sa.Integer(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["source_connection_id"], ["source_connections.id"],
                                name="fk_raw_objects_source_connection_id_source_connections"),
        sa.ForeignKeyConstraint(["ingestion_run_id"], ["ingestion_runs.id"],
                                name="fk_raw_objects_ingestion_run_id_ingestion_runs"),
        sa.PrimaryKeyConstraint("id", name="pk_raw_objects"),
        sa.UniqueConstraint("storage_key", name="uq_raw_objects_storage_key"),
    )
    for col in ("tenant_id", "source_connection_id", "ingestion_run_id", "sha256"):
        op.create_index(f"ix_raw_objects_{col}", "raw_objects", [col])

    # ------------------------------------------------------------ pipeline_runs
    op.add_column("pipeline_runs", sa.Column("ingestion_run_id", sa.BigInteger(), nullable=True))
    op.add_column("pipeline_runs", sa.Column("source_connection_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_pipeline_runs_ingestion_run_id_ingestion_runs", "pipeline_runs",
                          "ingestion_runs", ["ingestion_run_id"], ["id"])
    op.create_foreign_key("fk_pipeline_runs_source_connection_id_source_connections", "pipeline_runs",
                          "source_connections", ["source_connection_id"], ["id"])
    op.create_index("ix_pipeline_runs_ingestion_run_id", "pipeline_runs", ["ingestion_run_id"])
    op.create_index("ix_pipeline_runs_source_connection_id", "pipeline_runs", ["source_connection_id"])
    op.add_column("pipeline_runs", sa.Column("triggered_by", sa.String(254), nullable=True))
    op.add_column("pipeline_runs", sa.Column("correlation_id", sa.String(40), nullable=True))
    op.add_column("pipeline_runs", sa.Column("steps", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")))
    op.alter_column("pipeline_runs", "entity", existing_type=sa.String(80), nullable=True)
    op.execute("UPDATE pipeline_runs SET status = upper(status)")

    # ------------------------------------------------------------ data_quality_results
    op.alter_column("data_quality_results", "run_id", new_column_name="pipeline_run_id")
    op.add_column("data_quality_results", sa.Column("ingestion_run_id", sa.BigInteger(), nullable=True))
    op.add_column("data_quality_results", sa.Column("rule_description", sa.String(300), nullable=True))
    op.add_column("data_quality_results", sa.Column("blocking", sa.Boolean(), nullable=False, server_default="false"))
    op.create_index("ix_data_quality_results_pipeline_run_id", "data_quality_results", ["pipeline_run_id"])
    op.create_index("ix_data_quality_results_ingestion_run_id", "data_quality_results", ["ingestion_run_id"])

    # ------------------------------------------------------------ reconciliation / readiness
    op.create_table(
        "reconciliation_results",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(40), nullable=False),
        sa.Column("pipeline_run_id", sa.BigInteger(), nullable=True),
        sa.Column("ingestion_run_id", sa.BigInteger(), nullable=True),
        sa.Column("check_id", sa.String(60), nullable=False),
        sa.Column("entity", sa.String(60), nullable=False),
        sa.Column("scope", sa.String(40), nullable=False),
        sa.Column("expected", sa.Numeric(18, 2), nullable=False),
        sa.Column("actual", sa.Numeric(18, 2), nullable=False),
        sa.Column("difference", sa.Numeric(18, 2), nullable=False),
        sa.Column("tolerance", sa.Numeric(18, 2), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_reconciliation_results"),
    )
    for col in ("tenant_id", "pipeline_run_id", "ingestion_run_id"):
        op.create_index(f"ix_reconciliation_results_{col}", "reconciliation_results", [col])
    op.create_table(
        "capability_readiness",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.String(40), nullable=False),
        sa.Column("feature_key", sa.String(60), nullable=False),
        sa.Column("status", sa.String(12), nullable=False),
        sa.Column("reason", sa.String(300), nullable=False),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_capability_readiness"),
        sa.UniqueConstraint("tenant_id", "feature_key", name="uq_capready_tenant_feature"),
    )
    op.create_index("ix_capability_readiness_tenant_id", "capability_readiness", ["tenant_id"])

    # ------------------------------------------------------------ 2. linhagem na Silver
    for t in CANONICAL_TABLES:
        op.add_column(t, sa.Column("source_system", sa.String(60), nullable=True))
        op.add_column(t, sa.Column("source_connection_id", sa.Integer(), nullable=True))
        op.add_column(t, sa.Column("ingestion_run_id", sa.BigInteger(), nullable=True))
        op.add_column(t, sa.Column("source_record_id", sa.String(120), nullable=True))
        op.execute(f"UPDATE {t} SET source_system = 'synthetic_generator' WHERE source_system IS NULL")

    # ------------------------------------------------------------ 3. chaves de negócio
    op.add_column("contratos", sa.Column("codigo", sa.String(40), nullable=True))
    op.add_column("prestadores", sa.Column("codigo", sa.String(40), nullable=True))
    op.execute("""
        UPDATE contratos c SET codigo = x.cod FROM (
            SELECT id, 'CTR-' || lpad(row_number() OVER (PARTITION BY tenant_id ORDER BY id)::text, 4, '0') AS cod
            FROM contratos) x WHERE x.id = c.id AND c.codigo IS NULL
    """)
    op.execute("""
        UPDATE prestadores p SET codigo = x.cod FROM (
            SELECT id, 'PRE-' || lpad(row_number() OVER (PARTITION BY tenant_id ORDER BY id)::text, 4, '0') AS cod
            FROM prestadores) x WHERE x.id = p.id AND p.codigo IS NULL
    """)
    op.alter_column("contratos", "codigo", nullable=False)
    op.alter_column("prestadores", "codigo", nullable=False)
    op.create_unique_constraint("uq_contratos_tenant_codigo", "contratos", ["tenant_id", "codigo"])
    op.create_unique_constraint("uq_prestadores_tenant_codigo", "prestadores", ["tenant_id", "codigo"])

    # ------------------------------------------------------------ 4. colunas só do gerador
    for tbl, col, type_ in (
        ("planos", "segmentacao", sa.String(30)), ("planos", "ticket_medio_base", sa.Numeric(12, 2)),
        ("contratos", "tipo", sa.String(20)), ("contratos", "vidas_alvo", sa.Integer()),
        ("especialidades", "grupo", sa.String(30)),
        ("procedimentos", "complexidade", sa.Integer()), ("procedimentos", "custo_base", sa.Numeric(12, 2)),
        ("procedimentos", "tipo_atendimento_tipico", sa.String(20)),
        ("procedimentos", "perfil_utilizacao", sa.String(12)),
        ("prestadores", "tipo_prestador", sa.String(30)), ("prestadores", "nivel_preco", sa.Numeric(5, 3)),
    ):
        op.alter_column(tbl, col, existing_type=type_, nullable=True)

    # ------------------------------------------------------------ 5. idempotência de eventos
    op.create_index("uq_eventos_tenant_fonte_registro", "eventos_assistenciais",
                    ["tenant_id", "source_connection_id", "source_record_id"], unique=True,
                    postgresql_where=sa.text("source_record_id IS NOT NULL"))

    # ------------------------------------------------------------ 6. control plane
    op.create_table(
        "tenant_onboarding",
        sa.Column("tenant_id", sa.String(40), nullable=False),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("history", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "state IN ('TENANT_CREATED', 'SOURCE_REGISTERED', 'CONNECTION_VALIDATED', 'RAW_LOADED', "
            "'MAPPING_VALIDATED', 'DATA_QUALITY_VALIDATED', 'SILVER_READY', 'GOLD_READY', 'RECONCILED', "
            "'CAPABILITIES_READY', 'HOMOLOGATED', 'ACTIVE')", name="ck_tenant_onboarding_state_valido"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], name="fk_tenant_onboarding_tenant_id_tenants",
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id", name="pk_tenant_onboarding"),
    )
    # tenants existentes: os que já têm dados carregados (todos sintéticos) ficam ACTIVE
    op.execute("""
        INSERT INTO tenant_onboarding (tenant_id, state, history)
        SELECT t.id,
               CASE WHEN t.status = 'ACTIVE' AND EXISTS (
                         SELECT 1 FROM agg_sinistralidade_competencia a WHERE a.tenant_id = t.id)
                    THEN 'ACTIVE' ELSE 'TENANT_CREATED' END,
               '[{"state": "backfill", "note": "migração f4c5d6e7a8b9"}]'::json
        FROM tenants t
    """)
    op.create_table(
        "user_recovery_codes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("code_hash", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user_recovery_codes_user_id_users",
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_user_recovery_codes"),
    )
    op.create_index("ix_user_recovery_codes_user_id", "user_recovery_codes", ["user_id"])
    op.create_table(
        "auth_rate_limits",
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("hits", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("key", "window_start", name="pk_auth_rate_limits"),
    )

    # ------------------------------------------------------------ 7. RLS nas novas tabelas
    for t in NEW_DATA_PLANE_TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
        op.execute(f"CREATE POLICY tenant_isolation ON {t} USING ({_EXPR}) WITH CHECK ({_EXPR})")

    # ------------------------------------------------------------ grants
    app = _role("APP_DB_ROLE", "w2health_app")
    if op.get_bind().exec_driver_sql(f"SELECT 1 FROM pg_roles WHERE rolname = '{app}'").first():
        op.execute(f"GRANT SELECT ON raw_objects, reconciliation_results TO {app}")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON capability_readiness TO {app}")
        op.execute(f"GRANT SELECT, INSERT, UPDATE ON source_connections TO {app}")
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON tenant_onboarding, user_recovery_codes, "
                   f"auth_rate_limits TO {app}")
        op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {app}")

    pipe = _role("PIPELINE_DB_ROLE", "w2health_pipeline")
    _ensure_role(pipe, os.environ.get("PIPELINE_DB_PASSWORD") or "")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {pipe}")
    op.execute(f"GRANT SELECT, INSERT ON competencias TO {pipe}")
    for t in CANONICAL_TABLES + AGG_TABLES + PIPELINE_METADATA_RW:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {t} TO {pipe}")
    op.execute(f"GRANT SELECT, UPDATE ON source_connections TO {pipe}")
    op.execute(f"GRANT SELECT ON source_entities, tenants TO {pipe}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE ON tenant_onboarding TO {pipe}")
    op.execute(f"GRANT INSERT ON audit_logs TO {pipe}")
    op.execute(f"GRANT SELECT (id, occurred_at) ON audit_logs TO {pipe}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {pipe}")


def downgrade() -> None:
    pipe = _role("PIPELINE_DB_ROLE", "w2health_pipeline")
    if op.get_bind().exec_driver_sql(f"SELECT 1 FROM pg_roles WHERE rolname = '{pipe}'").first():
        op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {pipe}")
        op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {pipe}")
        op.execute(f"REVOKE USAGE ON SCHEMA public FROM {pipe}")

    op.drop_table("auth_rate_limits")
    op.drop_index("ix_user_recovery_codes_user_id", table_name="user_recovery_codes")
    op.drop_table("user_recovery_codes")
    op.drop_table("tenant_onboarding")
    op.drop_index("uq_eventos_tenant_fonte_registro", table_name="eventos_assistenciais")

    for tbl, col, type_, default in (
        ("planos", "segmentacao", sa.String(30), "'nao_informado'"),
        ("planos", "ticket_medio_base", sa.Numeric(12, 2), "0"),
        ("contratos", "tipo", sa.String(20), "'nao_informado'"),
        ("contratos", "vidas_alvo", sa.Integer(), "0"),
        ("especialidades", "grupo", sa.String(30), "'nao_informado'"),
        ("procedimentos", "complexidade", sa.Integer(), "1"),
        ("procedimentos", "custo_base", sa.Numeric(12, 2), "0"),
        ("procedimentos", "tipo_atendimento_tipico", sa.String(20), "'consulta'"),
        ("procedimentos", "perfil_utilizacao", sa.String(12), "'variavel'"),
        ("prestadores", "tipo_prestador", sa.String(30), "'nao_informado'"),
        ("prestadores", "nivel_preco", sa.Numeric(5, 3), "1"),
    ):
        op.execute(f"UPDATE {tbl} SET {col} = {default} WHERE {col} IS NULL")
        op.alter_column(tbl, col, existing_type=type_, nullable=False)

    op.drop_constraint("uq_prestadores_tenant_codigo", "prestadores", type_="unique")
    op.drop_constraint("uq_contratos_tenant_codigo", "contratos", type_="unique")
    op.drop_column("prestadores", "codigo")
    op.drop_column("contratos", "codigo")
    for t in CANONICAL_TABLES:
        for col in ("source_record_id", "ingestion_run_id", "source_connection_id", "source_system"):
            op.drop_column(t, col)

    op.drop_index("ix_capability_readiness_tenant_id", table_name="capability_readiness")
    op.drop_table("capability_readiness")
    for col in ("tenant_id", "pipeline_run_id", "ingestion_run_id"):
        op.drop_index(f"ix_reconciliation_results_{col}", table_name="reconciliation_results")
    op.drop_table("reconciliation_results")

    op.drop_index("ix_data_quality_results_ingestion_run_id", table_name="data_quality_results")
    op.drop_index("ix_data_quality_results_pipeline_run_id", table_name="data_quality_results")
    for col in ("blocking", "rule_description", "ingestion_run_id"):
        op.drop_column("data_quality_results", col)
    op.alter_column("data_quality_results", "pipeline_run_id", new_column_name="run_id")

    op.execute("UPDATE pipeline_runs SET status = lower(status)")
    op.drop_index("ix_pipeline_runs_source_connection_id", table_name="pipeline_runs")
    op.drop_index("ix_pipeline_runs_ingestion_run_id", table_name="pipeline_runs")
    op.drop_constraint("fk_pipeline_runs_source_connection_id_source_connections", "pipeline_runs", type_="foreignkey")
    op.drop_constraint("fk_pipeline_runs_ingestion_run_id_ingestion_runs", "pipeline_runs", type_="foreignkey")
    for col in ("steps", "correlation_id", "triggered_by", "source_connection_id", "ingestion_run_id"):
        op.drop_column("pipeline_runs", col)
    op.execute("UPDATE pipeline_runs SET entity = '' WHERE entity IS NULL")
    op.alter_column("pipeline_runs", "entity", existing_type=sa.String(80), nullable=False)

    for col in ("tenant_id", "source_connection_id", "ingestion_run_id", "sha256"):
        op.drop_index(f"ix_raw_objects_{col}", table_name="raw_objects")
    op.drop_table("raw_objects")

    op.drop_constraint("ck_ingestion_runs_status_valido", "ingestion_runs", type_="check")
    op.execute("UPDATE ingestion_runs SET status = CASE status WHEN 'SUCCESS' THEN 'success' "
               "WHEN 'FAILED' THEN 'failed' ELSE 'running' END")
    op.alter_column("ingestion_runs", "status", server_default=None)
    op.drop_index("ix_ingestion_runs_checksum", table_name="ingestion_runs")
    for col in ("error_summary", "correlation_id", "duplicate_of", "competencia_fim",
                "competencia_inicio", "mapping_ref", "triggered_by", "checksum", "errors_count",
                "warnings_count", "records_valid", "stage"):
        op.drop_column("ingestion_runs", col)
    op.execute("UPDATE ingestion_runs SET entity = '' WHERE entity IS NULL")
    op.alter_column("ingestion_runs", "entity", existing_type=sa.String(80), nullable=False)
    op.drop_index("ix_ingestion_runs_source_connection_id", table_name="ingestion_runs")
    op.drop_constraint("fk_ingestion_runs_source_connection_id_source_connections", "ingestion_runs", type_="foreignkey")
    op.drop_column("ingestion_runs", "source_connection_id")

    op.drop_constraint("ck_source_connections_status_valido", "source_connections", type_="check")
    op.drop_constraint("ck_source_connections_source_type_valido", "source_connections", type_="check")
    op.drop_constraint("uq_srcconn_tenant_name", "source_connections", type_="unique")
    op.add_column("source_connections", sa.Column("ativo", sa.Boolean(), nullable=False, server_default="true"))
    op.execute("UPDATE source_connections SET ativo = (status = 'ACTIVE')")
    for col in ("last_error_summary", "last_error_at", "last_success_at", "last_run_at",
                "updated_at", "secret_reference", "status", "name"):
        op.drop_column("source_connections", col)
    op.alter_column("source_connections", "created_at", new_column_name="criado_em")
    op.alter_column("source_connections", "configuration", new_column_name="config")
    op.alter_column("source_connections", "source_type", new_column_name="tipo")
