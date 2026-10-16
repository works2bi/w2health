"""Contrato × plano passa a ser N:N (`planos_contratos`).

Antes: `contratos.id_plano` — um contrato tinha exatamente um plano. Na operadora um
contrato oferece vários planos, e é o BENEFICIÁRIO que diz em qual deles está.

* `planos_contratos` (data plane, RLS): PK `(tenant_id, id_contrato, id_plano)`, com
  linhagem; backfill com os pares de `contratos.id_plano` e com os pares já usados pelos
  beneficiários (nenhum beneficiário fica órfão);
* `beneficiarios` ganha FK composta `(tenant_id, id_contrato, id_plano)` → `planos_contratos`
  — o plano do beneficiário tem de ser um plano do contrato dele;
* `contratos.id_plano` é removida (contrato não guarda dado de plano).

Preço por par contrato/plano (tabela de preço × grupo/faixa etária) é outra entidade, fora
da v1.

Revision ID: c9d0e1f2a3b4
Revises: b8e9f0a1c2d3
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c9d0e1f2a3b4"
down_revision: str | None = "b8e9f0a1c2d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLE_RE = re.compile(r"^[a-z_][a-z0-9_]{2,62}$")
_EXPR = "tenant_id = current_setting('app.tenant_id', true)"
NEW_DATA_PLANE_TABLES = ("planos_contratos",)
_T = NEW_DATA_PLANE_TABLES[0]


def _role(env: str, default: str) -> str:
    r = os.environ.get(env, default)
    if not _ROLE_RE.match(r):
        raise RuntimeError(f"{env} inválido")
    return r


def _existe(role: str) -> bool:
    sql = f"SELECT 1 FROM pg_roles WHERE rolname = '{role}'"
    return bool(op.get_bind().exec_driver_sql(sql).first())


def upgrade() -> None:
    op.create_table(
        _T,
        sa.Column("tenant_id", sa.String(40), nullable=False),
        sa.Column("id_contrato", sa.Integer(), nullable=False),
        sa.Column("id_plano", sa.Integer(), nullable=False),
        sa.Column("source_system", sa.String(60), nullable=True),
        sa.Column("source_connection_id", sa.Integer(), nullable=True),
        sa.Column("ingestion_run_id", sa.BigInteger(), nullable=True),
        sa.Column("source_record_id", sa.String(120), nullable=True),
        sa.ForeignKeyConstraint(["id_contrato"], ["contratos.id"],
                                name="fk_planos_contratos_id_contrato_contratos"),
        sa.ForeignKeyConstraint(["id_plano"], ["planos.id"],
                                name="fk_planos_contratos_id_plano_planos"),
        sa.PrimaryKeyConstraint("tenant_id", "id_contrato", "id_plano", name="pk_planos_contratos"),
    )
    op.create_index("ix_planos_contratos_tenant_id", _T, ["tenant_id"])
    op.create_index("ix_planos_contratos_id_plano", _T, ["id_plano"])

    # backfill: o plano atual de cada contrato + qualquer par já usado por beneficiário
    op.execute(f"""
        INSERT INTO {_T} (tenant_id, id_contrato, id_plano, source_system, source_connection_id,
                          ingestion_run_id, source_record_id)
        SELECT ct.tenant_id, ct.id, ct.id_plano, ct.source_system, ct.source_connection_id,
               ct.ingestion_run_id, ct.codigo || '/' || pl.codigo
        FROM contratos ct JOIN planos pl ON pl.id = ct.id_plano
    """)
    op.execute(f"""
        INSERT INTO {_T} (tenant_id, id_contrato, id_plano, source_system, source_record_id)
        SELECT DISTINCT b.tenant_id, b.id_contrato, b.id_plano, ct.source_system,
               ct.codigo || '/' || pl.codigo
        FROM beneficiarios b
        JOIN contratos ct ON ct.id = b.id_contrato
        JOIN planos pl ON pl.id = b.id_plano
        ON CONFLICT DO NOTHING
    """)

    op.create_foreign_key(
        "fk_beneficiarios_plano_contrato", "beneficiarios", _T,
        ["tenant_id", "id_contrato", "id_plano"], ["tenant_id", "id_contrato", "id_plano"])

    op.drop_index("ix_contratos_id_plano", table_name="contratos")
    op.drop_constraint("fk_contratos_id_plano_planos", "contratos", type_="foreignkey")
    op.drop_column("contratos", "id_plano")

    op.execute(f"ALTER TABLE {_T} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {_T} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {_T}")
    op.execute(f"CREATE POLICY tenant_isolation ON {_T} USING ({_EXPR}) WITH CHECK ({_EXPR})")

    app = _role("APP_DB_ROLE", "w2health_app")
    if _existe(app):
        op.execute(f"GRANT SELECT ON {_T} TO {app}")
    pipe = _role("PIPELINE_DB_ROLE", "w2health_pipeline")
    if _existe(pipe):
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_T} TO {pipe}")


def downgrade() -> None:
    # volta a 1 plano por contrato: fica o de menor id entre os planos do contrato
    op.add_column("contratos", sa.Column("id_plano", sa.Integer(), nullable=True))
    op.execute(f"""
        UPDATE contratos ct SET id_plano = pc.id_plano
        FROM (SELECT id_contrato, MIN(id_plano) AS id_plano FROM {_T} GROUP BY id_contrato) pc
        WHERE pc.id_contrato = ct.id
    """)
    op.alter_column("contratos", "id_plano", nullable=False)
    op.create_foreign_key("fk_contratos_id_plano_planos", "contratos", "planos",
                          ["id_plano"], ["id"])
    op.create_index("ix_contratos_id_plano", "contratos", ["id_plano"])
    op.drop_constraint("fk_beneficiarios_plano_contrato", "beneficiarios", type_="foreignkey")
    op.drop_index("ix_planos_contratos_id_plano", table_name=_T)
    op.drop_index("ix_planos_contratos_tenant_id", table_name=_T)
    op.drop_table(_T)
