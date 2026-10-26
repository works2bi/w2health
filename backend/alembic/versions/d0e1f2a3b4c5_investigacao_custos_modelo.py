"""Evolução V1 — investigação de custos: vínculo histórico, rede e preço contratado.

Tudo ADITIVO (nenhuma coluna removida/renomeada; nenhum valor existente alterado):

* `eventos_assistenciais`: `id_contrato`, `id_plano` (vínculo NA DATA DO EVENTO — congela
  a competência), `id_estabelecimento`, `id_prestador_solicitante` (rede, opcionais).
  Colunas nullable → sem reescrita da tabela. Backfill de contrato/plano a partir do
  vínculo atual do beneficiário (o mesmo valor que a Gold já usava pelo JOIN);
* `beneficiario_vinculos` (data plane, RLS): histórico opcional de vínculo contratual;
* `precos_contratados` (data plane, RLS): preço unitário contratado prestador ×
  procedimento com vigência — única evidência aceita para afirmar reajuste.

Ver docs/EVOLUCAO_V1_INVESTIGACAO_CUSTOS.md.

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d0e1f2a3b4c5"
down_revision: str | None = "c9d0e1f2a3b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLE_RE = re.compile(r"^[a-z_][a-z0-9_]{2,62}$")
_EXPR = "tenant_id = current_setting('app.tenant_id', true)"
NEW_DATA_PLANE_TABLES = ("beneficiario_vinculos", "precos_contratados")
_EV = "eventos_assistenciais"
_EV_FKS = (
    ("id_contrato", "contratos"),
    ("id_plano", "planos"),
    ("id_estabelecimento", "prestadores"),
    ("id_prestador_solicitante", "prestadores"),
)


def _role(env: str, default: str) -> str:
    r = os.environ.get(env, default)
    if not _ROLE_RE.match(r):
        raise RuntimeError(f"{env} inválido")
    return r


def _existe(role: str) -> bool:
    sql = f"SELECT 1 FROM pg_roles WHERE rolname = '{role}'"
    return bool(op.get_bind().exec_driver_sql(sql).first())


def _linhagem() -> list[sa.Column]:
    return [
        sa.Column("source_system", sa.String(60), nullable=True),
        sa.Column("source_connection_id", sa.Integer(), nullable=True),
        sa.Column("ingestion_run_id", sa.BigInteger(), nullable=True),
        sa.Column("source_record_id", sa.String(120), nullable=True),
    ]


def upgrade() -> None:
    # ---- evento: vínculo da competência + rede
    for col, alvo in _EV_FKS:
        op.add_column(_EV, sa.Column(col, sa.Integer(), nullable=True))
        op.create_foreign_key(f"fk_{_EV}_{col}_{alvo}", _EV, alvo, [col], ["id"])
    op.execute(f"""
        UPDATE {_EV} e SET id_contrato = b.id_contrato, id_plano = b.id_plano
        FROM beneficiarios b
        WHERE b.id = e.id_beneficiario AND e.id_contrato IS NULL
    """)

    # ---- histórico de vínculo
    op.create_table(
        "beneficiario_vinculos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.String(40), nullable=False),
        sa.Column("id_beneficiario", sa.Integer(), nullable=False),
        sa.Column("id_contrato", sa.Integer(), nullable=False),
        sa.Column("id_plano", sa.Integer(), nullable=False),
        sa.Column("vigencia_inicio", sa.Date(), nullable=False),
        sa.Column("vigencia_fim", sa.Date(), nullable=True),
        *_linhagem(),
        sa.ForeignKeyConstraint(["id_beneficiario"], ["beneficiarios.id"],
                                name="fk_beneficiario_vinculos_id_beneficiario_beneficiarios"),
        sa.ForeignKeyConstraint(["id_contrato"], ["contratos.id"],
                                name="fk_beneficiario_vinculos_id_contrato_contratos"),
        sa.ForeignKeyConstraint(["id_plano"], ["planos.id"],
                                name="fk_beneficiario_vinculos_id_plano_planos"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "id_contrato", "id_plano"],
            ["planos_contratos.tenant_id", "planos_contratos.id_contrato",
             "planos_contratos.id_plano"],
            name="fk_vinculos_plano_contrato"),
        sa.UniqueConstraint("tenant_id", "id_beneficiario", "vigencia_inicio",
                            name="uq_vinculos_tenant_benef_inicio"),
        sa.PrimaryKeyConstraint("id", name="pk_beneficiario_vinculos"),
    )
    op.create_index("ix_beneficiario_vinculos_tenant_id", "beneficiario_vinculos", ["tenant_id"])
    op.create_index("ix_beneficiario_vinculos_id_beneficiario", "beneficiario_vinculos",
                    ["id_beneficiario"])

    # ---- preço contratado
    op.create_table(
        "precos_contratados",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.String(40), nullable=False),
        sa.Column("id_prestador", sa.Integer(), nullable=False),
        sa.Column("id_procedimento", sa.Integer(), nullable=False),
        sa.Column("vigencia_inicio", sa.Date(), nullable=False),
        sa.Column("vigencia_fim", sa.Date(), nullable=True),
        sa.Column("valor_unitario", sa.Numeric(14, 2), nullable=False),
        sa.Column("tipo_remuneracao", sa.String(20), nullable=True),
        *_linhagem(),
        sa.ForeignKeyConstraint(["id_prestador"], ["prestadores.id"],
                                name="fk_precos_contratados_id_prestador_prestadores"),
        sa.ForeignKeyConstraint(["id_procedimento"], ["procedimentos.id"],
                                name="fk_precos_contratados_id_procedimento_procedimentos"),
        sa.UniqueConstraint("tenant_id", "id_prestador", "id_procedimento", "vigencia_inicio",
                            name="uq_precos_tenant_prest_proc_inicio"),
        sa.PrimaryKeyConstraint("id", name="pk_precos_contratados"),
    )
    op.create_index("ix_precos_contratados_tenant_id", "precos_contratados", ["tenant_id"])
    op.create_index("ix_precos_tenant_proc_prest", "precos_contratados",
                    ["tenant_id", "id_procedimento", "id_prestador"])

    app = _role("APP_DB_ROLE", "w2health_app")
    pipe = _role("PIPELINE_DB_ROLE", "w2health_pipeline")
    for t in NEW_DATA_PLANE_TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
        op.execute(f"CREATE POLICY tenant_isolation ON {t} USING ({_EXPR}) WITH CHECK ({_EXPR})")
        if _existe(app):
            op.execute(f"GRANT SELECT ON {t} TO {app}")
        if _existe(pipe):
            op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {t} TO {pipe}")
            op.execute(f"GRANT USAGE, SELECT ON SEQUENCE {t}_id_seq TO {pipe}")


def downgrade() -> None:
    op.drop_index("ix_precos_tenant_proc_prest", table_name="precos_contratados")
    op.drop_index("ix_precos_contratados_tenant_id", table_name="precos_contratados")
    op.drop_table("precos_contratados")
    op.drop_index("ix_beneficiario_vinculos_id_beneficiario", table_name="beneficiario_vinculos")
    op.drop_index("ix_beneficiario_vinculos_tenant_id", table_name="beneficiario_vinculos")
    op.drop_table("beneficiario_vinculos")
    for col, alvo in reversed(_EV_FKS):
        op.drop_constraint(f"fk_{_EV}_{col}_{alvo}", _EV, type_="foreignkey")
        op.drop_column(_EV, col)
