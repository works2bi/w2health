"""Camada analítica: agregações mensais materializadas + gabarito de cenários.

Estas tabelas são (re)construídas pelo job de agregação (`app/seed/aggregate.py`) após o
seed. O motor analítico lê predominantemente daqui — a fato bruta só é varrida em telas
de 1 beneficiário e no escopo de contrato (v1.2, sob demanda).

Todas são **tenant-aware** (v1.2). As chaves únicas passam a incluir `tenant_id`.

**v1.2 — composição financeira propagada** (`docs/V1.2.md`): `agg_competencia_dimensao`,
`agg_prestador_competencia` e `agg_beneficiario_competencia` ganham
`despesa_bruta / glosas / coparticipacao / despesa_liquida`. A coluna `despesa` mantém a
semântica de sempre (`Σ valor_pago` = bruta − glosa, sem coparticipação) — nenhum cálculo
ou teste existente muda; as quatro colunas novas são aditivas.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models._mixins import TenantMixin

# Dimensões suportadas pela decomposição (valor da coluna `dimensao`).
DIMENSOES = (
    "grupo_despesa",
    "tipo_atendimento",
    "especialidade",
    "procedimento",
    "prestador",
    "regiao",
    "faixa_etaria",
    "sexo",
    "plano",
    "contrato",
)


class AggSinistralidadeCompetencia(TenantMixin, Base):
    __tablename__ = "agg_sinistralidade_competencia"

    # Composição financeira (v1.1, Etapa B — ver docs/DATA_MODEL.md):
    #   despesa_bruta    = Σ valor_apresentado
    #   glosas           = Σ valor_glosado
    #   coparticipacao   = Σ valor_coparticipacao
    #   despesa_liquida  = despesa_bruta - glosas - coparticipacao  (base oficial do KPI p/ MVP)
    #   sinistralidade_bruta / _liquida = despesa_{bruta,liquida} / receita * 100
    # PK composta (tenant_id, competencia) — a única agg_* com PK natural (v1.2).
    tenant_id: Mapped[str] = mapped_column(String(40), primary_key=True, index=True)
    competencia: Mapped[date] = mapped_column(Date, primary_key=True)
    receita: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    despesa_bruta: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    glosas: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    coparticipacao: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    despesa_liquida: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    sinistralidade_bruta: Mapped[float] = mapped_column(Float, default=0.0)  # %
    sinistralidade_liquida: Mapped[float] = mapped_column(Float, default=0.0)  # %
    beneficiarios_ativos: Mapped[int] = mapped_column(Integer, default=0)
    exposicao_beneficiario_mes: Mapped[int] = mapped_column(Integer, default=0)
    eventos: Mapped[int] = mapped_column(Integer, default=0)
    custo_pmpm: Mapped[float] = mapped_column(Float, default=0.0)
    receita_media_beneficiario: Mapped[float] = mapped_column(Float, default=0.0)
    # Consolidação V1 — de onde veio a receita da competência (NUNCA a soma das fontes):
    # reconhecida | legada_plano | sem_receita; `receita_status` = ok | divergente
    fonte_receita: Mapped[str | None] = mapped_column(String(14), nullable=True)
    receita_status: Mapped[str | None] = mapped_column(String(12), nullable=True)
    receita_legada: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    receita_atribuida_contrato: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    receita_empresa_nao_detalhada: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    receita_nao_atribuida: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=0)
    cobertura_receita_contratual: Mapped[float | None] = mapped_column(Float, nullable=True)


class AggCompetenciaDimensao(TenantMixin, Base):
    __tablename__ = "agg_competencia_dimensao"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "competencia", "dimensao", "chave", name="uq_aggdim_tenant_comp_dim_chave"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    competencia: Mapped[date] = mapped_column(Date, index=True)
    dimensao: Mapped[str] = mapped_column(String(20), index=True)
    chave: Mapped[str] = mapped_column(String(60))
    rotulo: Mapped[str] = mapped_column(String(160))

    despesa: Mapped[float] = mapped_column(Float, default=0.0)  # Σ valor_pago (bruta − glosa)
    # v1.2 — composição financeira propagada (aditivo)
    despesa_bruta: Mapped[float] = mapped_column(Float, default=0.0)
    glosas: Mapped[float] = mapped_column(Float, default=0.0)
    coparticipacao: Mapped[float] = mapped_column(Float, default=0.0)
    despesa_liquida: Mapped[float] = mapped_column(Float, default=0.0)
    eventos: Mapped[int] = mapped_column(Integer, default=0)
    quantidade: Mapped[int] = mapped_column(Integer, default=0)
    beneficiarios: Mapped[int] = mapped_column(Integer, default=0)
    custo_medio: Mapped[float] = mapped_column(Float, default=0.0)
    freq_por_mil: Mapped[float] = mapped_column(Float, default=0.0)


class AggPrestadorCompetencia(TenantMixin, Base):
    __tablename__ = "agg_prestador_competencia"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "competencia", "id_prestador", name="uq_aggprest_tenant_comp_prest"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    competencia: Mapped[date] = mapped_column(Date, index=True)
    id_prestador: Mapped[int] = mapped_column(ForeignKey("prestadores.id"), index=True)

    despesa: Mapped[float] = mapped_column(Float, default=0.0)  # Σ valor_pago (bruta − glosa)
    # v1.2 — composição financeira propagada (aditivo)
    despesa_bruta: Mapped[float] = mapped_column(Float, default=0.0)
    glosas: Mapped[float] = mapped_column(Float, default=0.0)
    coparticipacao: Mapped[float] = mapped_column(Float, default=0.0)
    despesa_liquida: Mapped[float] = mapped_column(Float, default=0.0)
    eventos: Mapped[int] = mapped_column(Integer, default=0)
    beneficiarios: Mapped[int] = mapped_column(Integer, default=0)
    custo_medio: Mapped[float] = mapped_column(Float, default=0.0)
    participacao: Mapped[float] = mapped_column(Float, default=0.0)  # fração da despesa do mês
    procedimento_top_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    procedimento_top_share: Mapped[float] = mapped_column(Float, default=0.0)


class AggBeneficiarioCompetencia(TenantMixin, Base):
    __tablename__ = "agg_beneficiario_competencia"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "competencia", "id_beneficiario", name="uq_aggben_tenant_comp_ben"
        ),
        Index("ix_aggben_tenant_beneficiario", "tenant_id", "id_beneficiario"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    competencia: Mapped[date] = mapped_column(Date, index=True)
    id_beneficiario: Mapped[int] = mapped_column(ForeignKey("beneficiarios.id"), index=True)
    id_contrato: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)  # v1.2
    despesa: Mapped[float] = mapped_column(Float, default=0.0)  # Σ valor_pago (bruta − glosa)
    # v1.2 — composição financeira propagada (aditivo)
    despesa_bruta: Mapped[float] = mapped_column(Float, default=0.0)
    glosas: Mapped[float] = mapped_column(Float, default=0.0)
    coparticipacao: Mapped[float] = mapped_column(Float, default=0.0)
    despesa_liquida: Mapped[float] = mapped_column(Float, default=0.0)
    eventos: Mapped[int] = mapped_column(Integer, default=0)


class AggContratoCompetencia(TenantMixin, Base):
    """Agregado competência × contrato (v1.2). Base das telas de Contract Intelligence.

    **Sem receita/sinistralidade próprias** — `receitas_contrato` existe só como layout
    preparado, não é populada nem lida na v1.2. As telas de contrato mostram
    explicitamente "Receita por contrato ainda não disponível".
    """

    __tablename__ = "agg_contrato_competencia"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "competencia", "id_contrato", name="uq_aggctr_tenant_comp_ctr"
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    competencia: Mapped[date] = mapped_column(Date, index=True)
    id_contrato: Mapped[int] = mapped_column(ForeignKey("contratos.id"), index=True)

    vidas: Mapped[int] = mapped_column(Integer, default=0)  # beneficiários ativos no mês
    despesa: Mapped[float] = mapped_column(Float, default=0.0)  # Σ valor_pago (bruta − glosa)
    despesa_bruta: Mapped[float] = mapped_column(Float, default=0.0)
    glosas: Mapped[float] = mapped_column(Float, default=0.0)
    coparticipacao: Mapped[float] = mapped_column(Float, default=0.0)
    despesa_liquida: Mapped[float] = mapped_column(Float, default=0.0)
    eventos: Mapped[int] = mapped_column(Integer, default=0)
    beneficiarios_com_evento: Mapped[int] = mapped_column(Integer, default=0)
    custo_pmpm: Mapped[float] = mapped_column(Float, default=0.0)  # despesa_liquida / vidas
    gini: Mapped[float] = mapped_column(Float, default=0.0)  # sobre despesa líquida por beneficiário
    top5_share: Mapped[float] = mapped_column(Float, default=0.0)  # fração nos 5 maiores
    n_beneficiarios_alto_custo: Mapped[int] = mapped_column(Integer, default=0)
    # Consolidação V1 — receita RECONHECIDA atribuída ao contrato e sinistralidade contratual
    # (nula + motivo quando a cobertura não permite; nunca por rateio)
    receita_reconhecida: Mapped[float | None] = mapped_column(Float, nullable=True)
    sinistralidade_liquida: Mapped[float | None] = mapped_column(Float, nullable=True)
    motivo_sem_sinistralidade: Mapped[str | None] = mapped_column(String(40), nullable=True)


class CenarioGabarito(TenantMixin, Base):
    """Verdade-fundamental dos cenários plantados pelo gerador (consumido pelos testes)."""

    __tablename__ = "cenarios_gabarito"
    __table_args__ = (
        UniqueConstraint("tenant_id", "codigo", name="uq_gabarito_tenant_codigo"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(40))
    nome: Mapped[str] = mapped_column(String(120))
    competencia_alvo: Mapped[date | None] = mapped_column(Date, nullable=True)
    dimensao: Mapped[str | None] = mapped_column(String(20), nullable=True)
    chave_alvo: Mapped[str | None] = mapped_column(String(60), nullable=True)
    rotulo_alvo: Mapped[str | None] = mapped_column(String(160), nullable=True)
    efeito_esperado: Mapped[str | None] = mapped_column(String(30), nullable=True)
    descricao: Mapped[str] = mapped_column(String(600), default="")
    params: Mapped[dict] = mapped_column(JSON, default=dict)


class SeedManifest(TenantMixin, Base):
    __tablename__ = "seed_manifest"

    id: Mapped[int] = mapped_column(primary_key=True)
    seed: Mapped[int] = mapped_column(Integer)
    beneficiarios: Mapped[int] = mapped_column(Integer)
    inicio: Mapped[date] = mapped_column(Date)
    fim: Mapped[date] = mapped_column(Date)
    escala_eventos: Mapped[float] = mapped_column(Float, default=1.0)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    contagens: Mapped[dict] = mapped_column(JSON, default=dict)
