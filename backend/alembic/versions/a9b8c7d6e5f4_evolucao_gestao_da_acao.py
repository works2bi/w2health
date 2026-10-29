"""Evolução do produto — planos de ação, alertas por e-mail e referência de mercado.

Aditiva. Três tabelas do data plane, RLS `tenant_isolation`, ESCRITAS pelo runtime
(como `regras_alerta`): `planos_acao`, `assinaturas_alerta`, `envios_notificacao`.
Uma do control plane, global e SOMENTE LEITURA para o runtime: `benchmarks_mercado`.

Revision ID: a9b8c7d6e5f4
Revises: e1f2a3b4c5d6
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a9b8c7d6e5f4"
down_revision: str | None = "e1f2a3b4c5d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLE_RE = re.compile(r"^[a-z_][a-z0-9_]{2,62}$")
_EXPR = "tenant_id = current_setting('app.tenant_id', true)"
NEW_DATA_PLANE_TABLES = ("planos_acao", "assinaturas_alerta", "envios_notificacao")


def _role(env: str, default: str) -> str:
    r = os.environ.get(env, default)
    if not _ROLE_RE.match(r):
        raise RuntimeError(f"{env} inválido")
    return r


def _existe(role: str) -> bool:
    return bool(op.get_bind().exec_driver_sql(f"SELECT 1 FROM pg_roles WHERE rolname = '{role}'").first())


def _agora(nome: str) -> sa.Column:
    return sa.Column(nome, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    op.create_table(
        "planos_acao",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(40), nullable=False, index=True),
        sa.Column("titulo", sa.String(160), nullable=False),
        sa.Column("descricao", sa.Text(), nullable=False),
        sa.Column("origem_tipo", sa.String(10), nullable=False),
        sa.Column("origem_ref", sa.String(160)),
        sa.Column("dimensao", sa.String(30)),
        sa.Column("chave", sa.String(120)),
        sa.Column("alvo_rotulo", sa.String(200), nullable=False),
        sa.Column("responsavel", sa.String(120), nullable=False),
        sa.Column("prazo", sa.Date()),
        sa.Column("status", sa.String(14), nullable=False),
        sa.Column("competencia_inicio", sa.Date(), nullable=False),
        sa.Column("meta_reducao_pct", sa.Float()),
        sa.Column("criado_por", sa.String(254), nullable=False),
        _agora("criado_em"), _agora("atualizado_em"),
    )
    op.create_table(
        "assinaturas_alerta",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(40), nullable=False, index=True),
        sa.Column("user_id", sa.Uuid(), nullable=False, index=True),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("frequencia", sa.String(10), nullable=False),
        sa.Column("severidade_minima", sa.String(12), nullable=False),
        sa.Column("incluir_insights", sa.Boolean(), nullable=False),
        sa.Column("ativo", sa.Boolean(), nullable=False),
        _agora("criado_em"), _agora("atualizado_em"),
    )
    op.create_table(
        "envios_notificacao",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", sa.String(40), nullable=False, index=True),
        sa.Column("assinatura_id", sa.Integer(),
                  sa.ForeignKey("assinaturas_alerta.id", ondelete="SET NULL")),
        sa.Column("competencia", sa.Date(), nullable=False),
        sa.Column("canal", sa.String(10), nullable=False),
        sa.Column("destinatario", sa.String(254), nullable=False),
        sa.Column("assunto", sa.String(200), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("itens", sa.JSON(), nullable=False),
        sa.Column("erro", sa.String(300)),
        _agora("enviado_em"),
    )
    op.create_table(
        "benchmarks_mercado",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("fonte", sa.String(30), nullable=False),
        sa.Column("periodo", sa.String(10), nullable=False),
        sa.Column("segmento", sa.String(40), nullable=False),
        sa.Column("indicador", sa.String(40), nullable=False),
        sa.Column("p25", sa.Float(), nullable=False),
        sa.Column("mediana", sa.Float(), nullable=False),
        sa.Column("p75", sa.Float(), nullable=False),
        sa.Column("media_ponderada", sa.Float()),
        sa.Column("n_operadoras", sa.Integer(), nullable=False),
        _agora("atualizado_em"),
        sa.PrimaryKeyConstraint("id", name="pk_benchmarks_mercado"),
        sa.UniqueConstraint("fonte", "periodo", "segmento", "indicador",
                            name="uq_benchmarks_mercado_fonte"),
    )
    app = _role("APP_DB_ROLE", "w2health_app")
    if _existe(app):
        op.execute(f"GRANT SELECT ON benchmarks_mercado TO {app}")
    pipe = _role("PIPELINE_DB_ROLE", "w2health_pipeline")
    for t in NEW_DATA_PLANE_TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
        op.execute(f"CREATE POLICY tenant_isolation ON {t} USING ({_EXPR}) WITH CHECK ({_EXPR})")
        if _existe(app):
            op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {t} TO {app}")
            op.execute(f"GRANT USAGE, SELECT ON SEQUENCE {t}_id_seq TO {app}")
        if _existe(pipe):  # o envio agendado roda pelo papel de pipeline (worker)
            op.execute(f"GRANT SELECT, INSERT, UPDATE ON {t} TO {pipe}")
            op.execute(f"GRANT USAGE, SELECT ON SEQUENCE {t}_id_seq TO {pipe}")


def downgrade() -> None:
    op.drop_table("benchmarks_mercado")
    for t in reversed(NEW_DATA_PLANE_TABLES):
        op.drop_table(t)
