"""Row-Level Security — 2ª camada de isolamento (defesa em profundidade).

Política única aplicada a TODA tabela do data plane (as que têm `tenant_id`):

    ALTER TABLE t ENABLE ROW LEVEL SECURITY;
    ALTER TABLE t FORCE  ROW LEVEL SECURITY;        -- vale também para o dono não-superuser
    CREATE POLICY tenant_isolation ON t
        USING      (tenant_id = current_setting('app.tenant_id', true))
        WITH CHECK (tenant_id = current_setting('app.tenant_id', true));

* `current_setting(..., true)` devolve NULL/'' quando nada foi definido → a igualdade nunca
  é verdadeira → **zero linhas** (fail-closed). O valor é definido por transação em
  `app.db.tenant_scope` (`set_config(..., true)`), nunca por sessão de conexão.
* A API conecta com o papel `w2health_app`: NOSUPERUSER, NOBYPASSRLS, sem DDL, só os grants
  mínimos (`GRANTS`). `audit_logs` é append-only para ele (sem UPDATE/DELETE).
* Superusuários ignoram RLS por definição do PostgreSQL — por isso migrations/seed (papel
  dono) são processos de linha de comando, nunca expostos por HTTP.

A migration `e8b9c0d1f2a3` aplica exatamente estas instruções (cópia estática); os testes
de RLS usam este módulo sobre um banco criado por `create_all`.
"""

from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.engine import Connection

from app.db.base import Base, ControlBase

POLICY = "tenant_isolation"
_EXPR = "tenant_id = current_setting('app.tenant_id', true)"
_ROLE = re.compile(r"^[a-z_][a-z0-9_]{2,62}$")

#: tabelas do data plane em que o papel de runtime também ESCREVE
APP_WRITABLE_DATA_TABLES = frozenset({"regras_alerta", "capability_readiness"})
#: escrita do runtime sem DELETE (cadastro de fontes pela administração)
APP_INSERT_UPDATE_DATA_TABLES = frozenset({"source_connections"})

#: data plane que o PIPELINE escreve (canônico/Silver + Gold + metadados operacionais)
PIPELINE_WRITABLE = frozenset({
    "regioes", "planos", "contratos", "planos_contratos", "especialidades", "procedimentos",
    "prestadores", "diagnosticos", "beneficiarios", "receitas", "eventos_assistenciais",
    "precos_contratados", "beneficiario_vinculos",
    # Consolidação do canônico V1 — cadastro de empresa e fatos financeiros da receita
    "empresas", "empresas_contratos", "faturas", "fatura_itens", "recebimentos",
    "receitas_reconhecidas",
    "agg_sinistralidade_competencia", "agg_competencia_dimensao", "agg_prestador_competencia",
    "agg_beneficiario_competencia", "agg_contrato_competencia",
    "ingestion_runs", "raw_objects", "pipeline_runs", "data_quality_results",
    "reconciliation_results", "capability_readiness",
})
#: control plane somente-inserção para o runtime (trilha imutável)
APPEND_ONLY = frozenset({"audit_logs"})
#: control plane com privilégio reduzido para o runtime (Fase 3): a API só LÊ a fila
#: (retry/cancel administrativos também passam pelo papel de pipeline, com transição condicional).
APP_CONTROL_PRIVS = {"pipeline_jobs": "SELECT", "worker_heartbeats": "SELECT"}


def data_plane_tables() -> list[str]:
    return sorted(t.name for t in Base.metadata.sorted_tables if "tenant_id" in t.c)


def control_plane_tables() -> list[str]:
    return sorted(t.name for t in ControlBase.metadata.sorted_tables)


def rls_statements(table: str) -> list[str]:
    return [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"DROP POLICY IF EXISTS {POLICY} ON {table}",
        f"CREATE POLICY {POLICY} ON {table} USING ({_EXPR}) WITH CHECK ({_EXPR})",
    ]


def grant_statements(role: str, *, data_tables: list[str], control_tables: list[str]) -> list[str]:
    if not _ROLE.match(role):
        raise ValueError("nome de papel inválido")
    out = [f"GRANT USAGE ON SCHEMA public TO {role}", f"GRANT SELECT ON competencias TO {role}"]
    for t in data_tables:
        if t in APP_WRITABLE_DATA_TABLES:
            privs = "SELECT, INSERT, UPDATE, DELETE"
        elif t in APP_INSERT_UPDATE_DATA_TABLES:
            privs = "SELECT, INSERT, UPDATE"
        else:
            privs = "SELECT"
        out.append(f"GRANT {privs} ON {t} TO {role}")
    for t in control_tables:
        privs = ("SELECT, INSERT" if t in APPEND_ONLY else
                 APP_CONTROL_PRIVS.get(t, "SELECT, INSERT, UPDATE, DELETE"))
        out.append(f"GRANT {privs} ON {t} TO {role}")
    out.append(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}")
    return out


def pipeline_grant_statements(role: str) -> list[str]:
    """Privilégios mínimos do papel de pipeline (mesmos da migration f4c5d6e7a8b9)."""
    if not _ROLE.match(role):
        raise ValueError("nome de papel inválido")
    out = [f"GRANT USAGE ON SCHEMA public TO {role}", f"GRANT SELECT, INSERT ON competencias TO {role}"]
    out += [f"GRANT SELECT, INSERT, UPDATE, DELETE ON {t} TO {role}" for t in sorted(PIPELINE_WRITABLE)]
    out += [f"GRANT SELECT, UPDATE ON source_connections TO {role}",
            f"GRANT SELECT ON source_entities, tenants TO {role}",
            f"GRANT SELECT, INSERT, UPDATE ON tenant_onboarding TO {role}",
            f"GRANT INSERT ON audit_logs TO {role}",
            f"GRANT SELECT (id, occurred_at) ON audit_logs TO {role}",
            # Fase 3 — fila e presença do worker (control plane, só ids técnicos)
            f"GRANT SELECT, INSERT, UPDATE ON pipeline_jobs TO {role}",
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON worker_heartbeats TO {role}",
            f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {role}"]
    return out


def ensure_role(conn: Connection, role: str, password: str | None) -> None:
    """Cria o papel de runtime se não existir (NOSUPERUSER NOBYPASSRLS). Sem senha → NOLOGIN."""
    if not _ROLE.match(role):
        raise ValueError("nome de papel inválido")
    existe = conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}).first()
    login = "LOGIN PASSWORD " + _literal(password) if password else "NOLOGIN"
    attrs = "NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT"
    if existe:
        conn.execute(text(f"ALTER ROLE {role} {attrs}"))
        if password:
            conn.execute(text(f"ALTER ROLE {role} {login}"))
    else:
        conn.execute(text(f"CREATE ROLE {role} {login} {attrs}"))


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def apply_pipeline_role(conn: Connection, role: str, password: str | None) -> None:
    ensure_role(conn, role, password)
    for stmt in pipeline_grant_statements(role):
        conn.execute(text(stmt))


def apply_all(conn: Connection, role: str, password: str | None) -> None:
    """Papel + grants + RLS em todas as tabelas do data plane (idempotente)."""
    ensure_role(conn, role, password)
    dados = data_plane_tables()
    for stmt in grant_statements(role, data_tables=dados, control_tables=control_plane_tables()):
        conn.execute(text(stmt))
    for t in dados:
        for stmt in rls_statements(t):
            conn.execute(text(stmt))
