"""v1 SaaS: Row-Level Security no data plane + papel de runtime sem privilégio

Defesa em profundidade (ver app/db/rls.py e docs/SECURITY_AND_TENANT_ISOLATION.md):

1. papel `w2health_app` (ou APP_DB_ROLE): NOSUPERUSER, NOBYPASSRLS, sem DDL. Senha vem de
   `APP_DB_PASSWORD`; sem ela o papel é criado NOLOGIN (operador define a senha depois).
   Papéis são do CLUSTER — o downgrade revoga grants mas NÃO remove o papel.
   Com `DB_ROLES_PROVISIONED=true` o papel é criado pelo DBA e aqui só é verificado;
2. grants mínimos: SELECT no data plane (escrita só em `regras_alerta`); CRUD no control
   plane; `audit_logs` somente SELECT/INSERT (trilha imutável para a aplicação);
3. RLS ENABLE + FORCE em todas as 24 tabelas com `tenant_id`, política `tenant_isolation`
   comparando com `current_setting('app.tenant_id', true)` — sem tenant definido, zero
   linhas.

Revision ID: e8b9c0d1f2a3
Revises: c7a1e2b3d4f5
Create Date: 2026-10-05
"""
import os
import re
from collections.abc import Sequence

from alembic import op

revision: str = "e8b9c0d1f2a3"
down_revision: str | None = "c7a1e2b3d4f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DATA_PLANE_TABLES = (
    "agg_beneficiario_competencia", "agg_competencia_dimensao", "agg_contrato_competencia",
    "agg_prestador_competencia", "agg_sinistralidade_competencia", "beneficiarios",
    "cenarios_gabarito", "contratos", "data_quality_results", "diagnosticos",
    "especialidades", "eventos_assistenciais", "ingestion_runs", "pipeline_runs", "planos",
    "prestadores", "procedimentos", "receitas", "receitas_contrato", "regioes",
    "regras_alerta", "seed_manifest", "source_connections", "source_entities",
)
CONTROL_PLANE_TABLES = (
    "audit_logs", "auth_sessions", "features", "plan_features", "plans", "refresh_tokens",
    "tenant_branding", "tenant_branding_assets", "tenant_features", "tenant_secrets",
    "tenant_settings", "tenants", "user_tenants", "users",
)
APP_WRITABLE_DATA = {"regras_alerta"}
APPEND_ONLY = {"audit_logs"}
_EXPR = "tenant_id = current_setting('app.tenant_id', true)"


def _role() -> str:
    role = os.environ.get("APP_DB_ROLE", "w2health_app")
    if not re.match(r"^[a-z_][a-z0-9_]{2,62}$", role):
        raise RuntimeError("APP_DB_ROLE inválido")
    return role


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


def upgrade() -> None:
    role = _role()
    senha = os.environ.get("APP_DB_PASSWORD") or ""
    attrs = "NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT"
    login = "LOGIN PASSWORD '" + senha.replace("'", "''") + "'" if senha else "NOLOGIN"
    conn = op.get_bind()
    existe = conn.exec_driver_sql(f"SELECT 1 FROM pg_roles WHERE rolname = '{role}'").first()
    if _papeis_provisionados():
        _verificar_papel(role)
    elif existe:
        op.execute(f"ALTER ROLE {role} {attrs}")
        if senha:
            op.execute(f"ALTER ROLE {role} {login}")
    else:
        op.execute(f"CREATE ROLE {role} {login} {attrs}")

    op.execute(f"GRANT USAGE ON SCHEMA public TO {role}")
    op.execute(f"GRANT SELECT ON competencias TO {role}")
    for t in DATA_PLANE_TABLES:
        privs = "SELECT, INSERT, UPDATE, DELETE" if t in APP_WRITABLE_DATA else "SELECT"
        op.execute(f"GRANT {privs} ON {t} TO {role}")
    for t in CONTROL_PLANE_TABLES:
        privs = "SELECT, INSERT" if t in APPEND_ONLY else "SELECT, INSERT, UPDATE, DELETE"
        op.execute(f"GRANT {privs} ON {t} TO {role}")
    op.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}")

    for t in DATA_PLANE_TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
        op.execute(f"CREATE POLICY tenant_isolation ON {t} USING ({_EXPR}) WITH CHECK ({_EXPR})")


def downgrade() -> None:
    role = _role()
    for t in DATA_PLANE_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
        op.execute(f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
    existe = op.get_bind().exec_driver_sql(f"SELECT 1 FROM pg_roles WHERE rolname = '{role}'").first()
    if existe:
        op.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role}")
        op.execute(f"REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM {role}")
        op.execute(f"REVOKE USAGE ON SCHEMA public FROM {role}")
