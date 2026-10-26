"""Consolidação do canônico V1 — empresa, faturamento, recebimento e receita reconhecida.

Aditiva (nenhuma linha apagada):

* `empresas`, `empresas_contratos` (associação histórica com papel), `faturas`,
  `fatura_itens`, `recebimentos` — data plane, RLS `tenant_isolation`;
* `receitas_contrato` (layout v1.2, nunca populado) → **`receitas_reconhecidas`**, com nível
  de atribuição; linhas antigas, se houver, ficam `nivel_atribuicao = 'contrato'` e
  `estimada = true` quando a metodologia era rateio (nunca promovidas a receita real);
* `beneficiario_vinculos.situacao`;
* `eventos_assistenciais`: competências de apresentação/pagamento, guia, conta, unidade,
  natureza do lançamento e origem do `valor_pago` (todas nullable);
* `agg_sinistralidade_competencia` / `agg_contrato_competencia`: fonte e cobertura da receita,
  receita reconhecida e sinistralidade contratual.

Ver docs/CONSOLIDACAO_CANONICO_V1.md.

Revision ID: e1f2a3b4c5d6
Revises: d0e1f2a3b4c5
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: str | None = "d0e1f2a3b4c5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLE_RE = re.compile(r"^[a-z_][a-z0-9_]{2,62}$")
_EXPR = "tenant_id = current_setting('app.tenant_id', true)"
NEW_DATA_PLANE_TABLES = ("empresas", "empresas_contratos", "faturas", "fatura_itens",
                         "recebimentos", "receitas_reconhecidas")
_CRIADAS = NEW_DATA_PLANE_TABLES[:-1]  # receitas_reconhecidas vem do rename (RLS já existe)
_OLD, _NEW = "receitas_contrato", "receitas_reconhecidas"
_EV = "eventos_assistenciais"
_EV_COLS = (
    ("competencia_apresentacao", sa.Date()), ("competencia_pagamento", sa.Date()),
    ("numero_guia", sa.String(60)), ("numero_conta", sa.String(60)),
    ("unidade_medida", sa.String(20)), ("natureza_lancamento", sa.String(14)),
    ("valor_pago_origem", sa.String(10)),
)
_AGG_SIN = (
    ("fonte_receita", sa.String(14)), ("receita_status", sa.String(12)),
    ("receita_legada", sa.Numeric(18, 2)), ("cobertura_receita_contratual", sa.Float()),
)
_AGG_SIN_NN = ("receita_atribuida_contrato", "receita_empresa_nao_detalhada", "receita_nao_atribuida")
_AGG_CTR = (
    ("receita_reconhecida", sa.Float()), ("sinistralidade_liquida", sa.Float()),
    ("motivo_sem_sinistralidade", sa.String(40)),
)


def _role(env: str, default: str) -> str:
    r = os.environ.get(env, default)
    if not _ROLE_RE.match(r):
        raise RuntimeError(f"{env} inválido")
    return r


def _existe(role: str) -> bool:
    return bool(op.get_bind().exec_driver_sql(f"SELECT 1 FROM pg_roles WHERE rolname = '{role}'").first())


def _base(nome: str) -> list:
    return [
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tenant_id", sa.String(40), nullable=False),
    ]


def _linhagem() -> list:
    return [
        sa.Column("source_system", sa.String(60), nullable=True),
        sa.Column("source_connection_id", sa.Integer(), nullable=True),
        sa.Column("ingestion_run_id", sa.BigInteger(), nullable=True),
        sa.Column("source_record_id", sa.String(120), nullable=True),
    ]


def _fk(tabela: str, col: str, alvo: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint([col], [f"{alvo}.id"], name=f"fk_{tabela}_{col}_{alvo}")


def upgrade() -> None:
    op.create_table(
        "empresas", *_base("empresas"),
        sa.Column("codigo", sa.String(40), nullable=False),
        sa.Column("nome", sa.String(120), nullable=False),
        sa.Column("tipo", sa.String(30), nullable=True),
        sa.Column("status", sa.String(12), nullable=True),
        *_linhagem(),
        sa.PrimaryKeyConstraint("id", name="pk_empresas"),
        sa.UniqueConstraint("tenant_id", "codigo", name="uq_empresas_tenant_codigo"),
    )
    op.create_table(
        "empresas_contratos", *_base("empresas_contratos"),
        sa.Column("id_empresa", sa.Integer(), nullable=False),
        sa.Column("id_contrato", sa.Integer(), nullable=False),
        sa.Column("papel", sa.String(20), nullable=False),
        sa.Column("vigencia_inicio", sa.Date(), nullable=False),
        sa.Column("vigencia_fim", sa.Date(), nullable=True),
        *_linhagem(),
        sa.PrimaryKeyConstraint("id", name="pk_empresas_contratos"),
        _fk("empresas_contratos", "id_empresa", "empresas"),
        _fk("empresas_contratos", "id_contrato", "contratos"),
        sa.UniqueConstraint("tenant_id", "id_contrato", "papel", "vigencia_inicio",
                            name="uq_empctr_tenant_ctr_papel_inicio"),
    )
    op.create_table(
        "faturas", *_base("faturas"),
        sa.Column("numero_documento", sa.String(60), nullable=False),
        sa.Column("unidade_cobranca", sa.String(12), nullable=False),
        sa.Column("id_contrato", sa.Integer(), nullable=True),
        sa.Column("id_empresa", sa.Integer(), nullable=True),
        sa.Column("competencia_faturamento", sa.Date(), nullable=False),
        sa.Column("data_emissao", sa.Date(), nullable=True),
        sa.Column("data_vencimento", sa.Date(), nullable=True),
        sa.Column("valor_bruto", sa.Numeric(16, 2), nullable=False),
        sa.Column("valor_desconto", sa.Numeric(16, 2), nullable=False),
        sa.Column("valor_acrescimo", sa.Numeric(16, 2), nullable=False),
        sa.Column("valor_total", sa.Numeric(16, 2), nullable=False),
        sa.Column("status", sa.String(20), nullable=True),
        *_linhagem(),
        sa.PrimaryKeyConstraint("id", name="pk_faturas"),
        _fk("faturas", "id_contrato", "contratos"),
        _fk("faturas", "id_empresa", "empresas"),
        sa.UniqueConstraint("tenant_id", "numero_documento", name="uq_faturas_tenant_numero"),
        sa.CheckConstraint(
            "(unidade_cobranca = 'contrato' AND id_contrato IS NOT NULL AND id_empresa IS NULL) OR "
            "(unidade_cobranca = 'empresa' AND id_empresa IS NOT NULL AND id_contrato IS NULL)",
            name="ck_faturas_unidade_cobranca"),
    )
    op.create_index("ix_faturas_tenant_competencia", "faturas", ["tenant_id", "competencia_faturamento"])
    op.create_table(
        "fatura_itens", *_base("fatura_itens"),
        sa.Column("id_fatura", sa.Integer(), nullable=False),
        sa.Column("numero_item", sa.String(40), nullable=False),
        sa.Column("natureza", sa.String(20), nullable=False),
        sa.Column("id_contrato", sa.Integer(), nullable=True),
        sa.Column("id_plano", sa.Integer(), nullable=True),
        sa.Column("competencia_referencia", sa.Date(), nullable=True),
        sa.Column("quantidade_beneficiarios", sa.Integer(), nullable=True),
        sa.Column("valor", sa.Numeric(16, 2), nullable=False),
        *_linhagem(),
        sa.PrimaryKeyConstraint("id", name="pk_fatura_itens"),
        _fk("fatura_itens", "id_fatura", "faturas"),
        _fk("fatura_itens", "id_contrato", "contratos"),
        _fk("fatura_itens", "id_plano", "planos"),
        sa.UniqueConstraint("tenant_id", "id_fatura", "numero_item", name="uq_fatitens_tenant_fatura_item"),
    )
    op.create_table(
        "recebimentos", *_base("recebimentos"),
        sa.Column("numero_recebimento", sa.String(60), nullable=False),
        sa.Column("id_fatura", sa.Integer(), nullable=True),
        sa.Column("data_recebimento", sa.Date(), nullable=False),
        sa.Column("competencia_recebimento", sa.Date(), nullable=False),
        sa.Column("valor_recebido", sa.Numeric(16, 2), nullable=False),
        *_linhagem(),
        sa.PrimaryKeyConstraint("id", name="pk_recebimentos"),
        _fk("recebimentos", "id_fatura", "faturas"),
        sa.UniqueConstraint("tenant_id", "numero_recebimento", name="uq_receb_tenant_numero"),
    )
    for t in _CRIADAS:
        op.create_index(f"ix_{t}_tenant_id", t, ["tenant_id"])
    op.create_index("ix_empresas_contratos_id_empresa", "empresas_contratos", ["id_empresa"])
    op.create_index("ix_empresas_contratos_id_contrato", "empresas_contratos", ["id_contrato"])
    op.create_index("ix_fatura_itens_id_fatura", "fatura_itens", ["id_fatura"])
    op.create_index("ix_recebimentos_id_fatura", "recebimentos", ["id_fatura"])

    # ---- receitas_contrato → receitas_reconhecidas (RLS e grants acompanham a tabela)
    op.rename_table(_OLD, _NEW)
    op.execute(f"ALTER TABLE {_NEW} RENAME CONSTRAINT pk_{_OLD} TO pk_{_NEW}")
    op.execute(f"ALTER TABLE {_NEW} RENAME CONSTRAINT fk_{_OLD}_id_contrato_contratos "
               f"TO fk_{_NEW}_id_contrato_contratos")
    op.execute(f"ALTER SEQUENCE {_OLD}_id_seq RENAME TO {_NEW}_id_seq")
    for col in ("tenant_id", "competencia", "id_contrato"):
        op.execute(f"ALTER INDEX ix_{_OLD}_{col} RENAME TO ix_{_NEW}_{col}")
    op.drop_constraint("uq_recctr_tenant_comp_ctr", _NEW, type_="unique")
    op.alter_column(_NEW, "id_contrato", existing_type=sa.Integer(), nullable=True)
    op.add_column(_NEW, sa.Column("nivel_atribuicao", sa.String(16), nullable=False,
                                  server_default="contrato"))
    op.alter_column(_NEW, "nivel_atribuicao", server_default=None)
    op.add_column(_NEW, sa.Column("id_plano", sa.Integer(), nullable=True))
    op.add_column(_NEW, sa.Column("id_empresa", sa.Integer(), nullable=True))
    op.add_column(_NEW, sa.Column("natureza", sa.String(20), nullable=False,
                                  server_default="contraprestacao"))
    op.alter_column(_NEW, "natureza", server_default=None)
    op.add_column(_NEW, sa.Column("numero_documento", sa.String(60), nullable=True))
    op.add_column(_NEW, sa.Column("referencia_origem", sa.String(120), nullable=True))
    op.add_column(_NEW, sa.Column("estimada", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.alter_column(_NEW, "estimada", server_default=None)
    for c in _linhagem():
        op.add_column(_NEW, c)
    op.execute(f"UPDATE {_NEW} SET source_record_id = 'legado-' || id WHERE source_record_id IS NULL")
    op.execute(f"UPDATE {_NEW} SET estimada = true WHERE metodologia LIKE 'rateio%'")
    op.create_foreign_key(f"fk_{_NEW}_id_plano_planos", _NEW, "planos", ["id_plano"], ["id"])
    op.create_foreign_key(f"fk_{_NEW}_id_empresa_empresas", _NEW, "empresas", ["id_empresa"], ["id"])
    op.create_unique_constraint("uq_recrec_tenant_comp_registro", _NEW,
                                ["tenant_id", "competencia", "source_record_id"])
    op.create_index("ix_recrec_tenant_comp_ctr", _NEW, ["tenant_id", "competencia", "id_contrato"])

    # ---- vínculo, evento, Gold
    op.add_column("beneficiario_vinculos", sa.Column("situacao", sa.String(12), nullable=True))
    for col, tipo in _EV_COLS:
        op.add_column(_EV, sa.Column(col, tipo, nullable=True))
    for col, tipo in _AGG_SIN:
        op.add_column("agg_sinistralidade_competencia", sa.Column(col, tipo, nullable=True))
    for col in _AGG_SIN_NN:
        op.add_column("agg_sinistralidade_competencia",
                      sa.Column(col, sa.Numeric(18, 2), nullable=False, server_default="0"))
        op.alter_column("agg_sinistralidade_competencia", col, server_default=None)
    for col, tipo in _AGG_CTR:
        op.add_column("agg_contrato_competencia", sa.Column(col, tipo, nullable=True))

    app = _role("APP_DB_ROLE", "w2health_app")
    pipe = _role("PIPELINE_DB_ROLE", "w2health_pipeline")
    for t in _CRIADAS:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {t}")
        op.execute(f"CREATE POLICY tenant_isolation ON {t} USING ({_EXPR}) WITH CHECK ({_EXPR})")
        if _existe(app):
            op.execute(f"GRANT SELECT ON {t} TO {app}")
        if _existe(pipe):
            op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {t} TO {pipe}")
            op.execute(f"GRANT USAGE, SELECT ON SEQUENCE {t}_id_seq TO {pipe}")
    if _existe(pipe):  # receitas_reconhecidas agora é escrita pelo pipeline
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {_NEW} TO {pipe}")
        op.execute(f"GRANT USAGE, SELECT ON SEQUENCE {_NEW}_id_seq TO {pipe}")


def downgrade() -> None:
    for col, _t in reversed(_AGG_CTR):
        op.drop_column("agg_contrato_competencia", col)
    for col in reversed(_AGG_SIN_NN):
        op.drop_column("agg_sinistralidade_competencia", col)
    for col, _t in reversed(_AGG_SIN):
        op.drop_column("agg_sinistralidade_competencia", col)
    for col, _t in reversed(_EV_COLS):
        op.drop_column(_EV, col)
    op.drop_column("beneficiario_vinculos", "situacao")

    # receitas_reconhecidas → receitas_contrato: só o que cabia no layout antigo volta
    op.execute(f"DELETE FROM {_NEW} WHERE id_contrato IS NULL OR id_plano IS NOT NULL "
               f"OR nivel_atribuicao <> 'contrato'")
    op.drop_index("ix_recrec_tenant_comp_ctr", table_name=_NEW)
    op.drop_constraint("uq_recrec_tenant_comp_registro", _NEW, type_="unique")
    op.drop_constraint(f"fk_{_NEW}_id_empresa_empresas", _NEW, type_="foreignkey")
    op.drop_constraint(f"fk_{_NEW}_id_plano_planos", _NEW, type_="foreignkey")
    for c in ("source_record_id", "ingestion_run_id", "source_connection_id", "source_system",
              "estimada", "referencia_origem", "numero_documento", "natureza", "id_empresa",
              "id_plano", "nivel_atribuicao"):
        op.drop_column(_NEW, c)
    op.alter_column(_NEW, "id_contrato", existing_type=sa.Integer(), nullable=False)
    op.create_unique_constraint("uq_recctr_tenant_comp_ctr", _NEW, ["tenant_id", "competencia", "id_contrato"])
    for col in ("tenant_id", "competencia", "id_contrato"):
        op.execute(f"ALTER INDEX ix_{_NEW}_{col} RENAME TO ix_{_OLD}_{col}")
    op.execute(f"ALTER SEQUENCE {_NEW}_id_seq RENAME TO {_OLD}_id_seq")
    op.execute(f"ALTER TABLE {_NEW} RENAME CONSTRAINT fk_{_NEW}_id_contrato_contratos "
               f"TO fk_{_OLD}_id_contrato_contratos")
    op.execute(f"ALTER TABLE {_NEW} RENAME CONSTRAINT pk_{_NEW} TO pk_{_OLD}")
    op.rename_table(_NEW, _OLD)

    for t in reversed(_CRIADAS):
        op.drop_table(t)
