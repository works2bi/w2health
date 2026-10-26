"""Cadastro de empresas e fatos financeiros da receita (Consolidação do canônico V1).

Ver docs/REGRAS_RECEITA_E_FATURAMENTO.md. Cada fato tem o SEU grão e a SUA competência —
nenhum é derivado do outro:

* `Fatura`       — documento de cobrança (unidade de cobrança = contrato OU empresa);
                   competência de FATURAMENTO;
* `FaturaItem`   — item do documento; contrato × plano quando a origem detalha;
* `Recebimento`  — baixa financeira de um documento; competência de RECEBIMENTO;
* `ReceitaReconhecida` — contraprestação reconhecida, competência de RECONHECIMENTO, no
                   nível de atribuição que a origem permite (contrato × plano · contrato ·
                   empresa · não atribuída). Evolui o layout `receitas_contrato` (nunca
                   populado). **Rateio é proibido**: `estimada` só existe para marcar
                   alocação gerencial futura, e o motor não lê linhas estimadas.

A receita LEGADA (`receitas`, competência × plano) continua existindo e é a fonte da
receita da operadora quando não há receita reconhecida na competência — nunca somada a ela.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models._mixins import LineageMixin, TenantMixin

#: papel da empresa no contrato — só `contratante` agrupa receita/despesa (sem dupla contagem)
PAPEIS_EMPRESA = ("contratante", "estipulante", "administradora", "subestipulante")
UNIDADES_COBRANCA = ("contrato", "empresa")
NIVEIS_ATRIBUICAO = ("contrato_plano", "contrato", "empresa", "nao_atribuida")
NATUREZAS_ITEM = (
    "mensalidade",
    "coparticipacao",
    "taxa",
    "ajuste",
    "retroativo",
    "desconto",
    "acrescimo",
    "outro",
)


class Empresa(TenantMixin, LineageMixin, Base):
    """Entidade cadastral que agrupa contratos empresariais/coletivos. NÃO é um nível fixo
    entre plano e contrato. Identificador fiscal não é exigido."""

    __tablename__ = "empresas"
    __table_args__ = (UniqueConstraint("tenant_id", "codigo", name="uq_empresas_tenant_codigo"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(40))
    nome: Mapped[str] = mapped_column(String(120))
    # empresarial | adesao | associacao | entidade_classe | outro (informativo)
    tipo: Mapped[str | None] = mapped_column(String(30), nullable=True)
    status: Mapped[str | None] = mapped_column(String(12), nullable=True)  # ativa | inativa


class EmpresaContrato(TenantMixin, LineageMixin, Base):
    """Associação HISTÓRICA empresa × contrato. Regra: no máximo uma empresa por contrato e
    papel em cada data (vigências sem sobreposição — verificado no lote e contra o já
    persistido). Vigência [início, fim)."""

    __tablename__ = "empresas_contratos"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "id_contrato",
            "papel",
            "vigencia_inicio",
            name="uq_empctr_tenant_ctr_papel_inicio",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    id_empresa: Mapped[int] = mapped_column(ForeignKey("empresas.id"), index=True)
    id_contrato: Mapped[int] = mapped_column(ForeignKey("contratos.id"), index=True)
    papel: Mapped[str] = mapped_column(String(20), default="contratante")
    vigencia_inicio: Mapped[date] = mapped_column(Date)
    vigencia_fim: Mapped[date | None] = mapped_column(Date, nullable=True)


class Fatura(TenantMixin, LineageMixin, Base):
    """Documento de cobrança. `valor_total = valor_bruto − valor_desconto + valor_acrescimo`
    (verificado no DQ). Faturado ≠ reconhecido ≠ recebido."""

    __tablename__ = "faturas"
    __table_args__ = (
        UniqueConstraint("tenant_id", "numero_documento", name="uq_faturas_tenant_numero"),
        CheckConstraint(
            "(unidade_cobranca = 'contrato' AND id_contrato IS NOT NULL AND id_empresa IS NULL) OR "
            "(unidade_cobranca = 'empresa' AND id_empresa IS NOT NULL AND id_contrato IS NULL)",
            name="unidade_cobranca",
        ),
        Index("ix_faturas_tenant_competencia", "tenant_id", "competencia_faturamento"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    numero_documento: Mapped[str] = mapped_column(String(60))
    unidade_cobranca: Mapped[str] = mapped_column(String(12))  # contrato | empresa
    id_contrato: Mapped[int | None] = mapped_column(ForeignKey("contratos.id"), nullable=True)
    id_empresa: Mapped[int | None] = mapped_column(ForeignKey("empresas.id"), nullable=True)
    competencia_faturamento: Mapped[date] = mapped_column(Date)
    data_emissao: Mapped[date | None] = mapped_column(Date, nullable=True)
    data_vencimento: Mapped[date | None] = mapped_column(Date, nullable=True)
    valor_bruto: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    valor_desconto: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    valor_acrescimo: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    valor_total: Mapped[Decimal] = mapped_column(Numeric(16, 2))
    status: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )  # emitida | cancelada | substituida


class FaturaItem(TenantMixin, LineageMixin, Base):
    __tablename__ = "fatura_itens"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "id_fatura", "numero_item", name="uq_fatitens_tenant_fatura_item"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    id_fatura: Mapped[int] = mapped_column(ForeignKey("faturas.id"), index=True)
    numero_item: Mapped[str] = mapped_column(String(40))
    natureza: Mapped[str] = mapped_column(String(20), default="mensalidade")
    id_contrato: Mapped[int | None] = mapped_column(ForeignKey("contratos.id"), nullable=True)
    id_plano: Mapped[int | None] = mapped_column(ForeignKey("planos.id"), nullable=True)
    competencia_referencia: Mapped[date | None] = mapped_column(Date, nullable=True)
    quantidade_beneficiarios: Mapped[int | None] = mapped_column(Integer, nullable=True)
    valor: Mapped[Decimal] = mapped_column(Numeric(16, 2))


class Recebimento(TenantMixin, LineageMixin, Base):
    __tablename__ = "recebimentos"
    __table_args__ = (
        UniqueConstraint("tenant_id", "numero_recebimento", name="uq_receb_tenant_numero"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    numero_recebimento: Mapped[str] = mapped_column(String(60))
    id_fatura: Mapped[int | None] = mapped_column(
        ForeignKey("faturas.id"), nullable=True, index=True
    )
    data_recebimento: Mapped[date] = mapped_column(Date)
    competencia_recebimento: Mapped[date] = mapped_column(Date)
    valor_recebido: Mapped[Decimal] = mapped_column(Numeric(16, 2))


class ReceitaReconhecida(TenantMixin, LineageMixin, Base):
    """Contraprestação RECONHECIDA no nível de atribuição que a origem permite.

    * `contrato_plano` / `contrato`: atribuída ao contrato — base da sinistralidade contratual;
    * `empresa`: cobrança consolidada SEM detalhamento — conta na operadora, nunca no contrato;
    * `nao_atribuida`: sem correspondência cadastral (`referencia_origem` guarda o código).
    Chave: (tenant, competência, `source_record_id`) — o identificador do registro na origem.
    """

    __tablename__ = "receitas_reconhecidas"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "competencia", "source_record_id", name="uq_recrec_tenant_comp_registro"
        ),
        Index("ix_recrec_tenant_comp_ctr", "tenant_id", "competencia", "id_contrato"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    competencia: Mapped[date] = mapped_column(Date, index=True)  # de RECONHECIMENTO
    nivel_atribuicao: Mapped[str] = mapped_column(String(16), default="contrato")
    id_contrato: Mapped[int | None] = mapped_column(
        ForeignKey("contratos.id"), nullable=True, index=True
    )
    id_plano: Mapped[int | None] = mapped_column(ForeignKey("planos.id"), nullable=True)
    id_empresa: Mapped[int | None] = mapped_column(ForeignKey("empresas.id"), nullable=True)
    natureza: Mapped[str] = mapped_column(String(20), default="contraprestacao")
    numero_documento: Mapped[str | None] = mapped_column(String(60), nullable=True)
    referencia_origem: Mapped[str | None] = mapped_column(String(120), nullable=True)
    quantidade_beneficiarios: Mapped[int] = mapped_column(Integer, default=0)
    receita_contraprestacao: Mapped[Decimal] = mapped_column(Numeric(16, 2), default=0)
    estimada: Mapped[bool] = mapped_column(Boolean, default=False)
    metodologia: Mapped[str | None] = mapped_column(String(40), nullable=True)
    reajuste_aplicado_no_periodo: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 4), nullable=True
    )
