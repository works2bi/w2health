"""Eventos assistenciais — grão de 1 linha por atendimento/procedimento."""

from datetime import date
from decimal import Decimal

from sqlalchemy import BigInteger, Date, ForeignKey, Index, Integer, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models._mixins import LineageMixin, TenantMixin

# Tipos de atendimento admitidos (validação em código; armazenado como texto).
TIPOS_ATENDIMENTO = (
    "consulta",
    "exame",
    "terapia",
    "pronto_socorro",
    "internacao",
    "cirurgia",
    "opme",
)


class EventoAssistencial(TenantMixin, LineageMixin, Base):
    __tablename__ = "eventos_assistenciais"
    __table_args__ = (
        Index("ix_evento_comp_espec", "competencia", "id_especialidade"),
        Index("ix_evento_comp_prest", "competencia", "id_prestador"),
        Index("ix_evento_comp_proc", "competencia", "id_procedimento"),
        Index("ix_evento_comp_tipo", "competencia", "tipo_atendimento"),
        # Fundação SaaS V1 — consultas sempre filtram tenant primeiro
        Index("ix_eventos_tenant_competencia", "tenant_id", "competencia"),
        Index("ix_eventos_tenant_beneficiario", "tenant_id", "id_beneficiario"),
        # Fase 2 — idempotência por registro de origem (fontes externas)
        Index("uq_eventos_tenant_fonte_registro", "tenant_id", "source_connection_id",
              "source_record_id", unique=True,
              postgresql_where=text("source_record_id IS NOT NULL")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    id_beneficiario: Mapped[int] = mapped_column(ForeignKey("beneficiarios.id"), index=True)
    id_prestador: Mapped[int] = mapped_column(ForeignKey("prestadores.id"), index=True)
    id_procedimento: Mapped[int] = mapped_column(ForeignKey("procedimentos.id"), index=True)
    id_especialidade: Mapped[int] = mapped_column(ForeignKey("especialidades.id"), index=True)
    id_diagnostico: Mapped[int | None] = mapped_column(
        ForeignKey("diagnosticos.id"), nullable=True
    )
    id_regiao: Mapped[int] = mapped_column(ForeignKey("regioes.id"), index=True)

    # Vínculo NA DATA DO EVENTO (Evolução V1 — investigação de custos). Congela contrato/
    # plano da competência: se o beneficiário mudar de contrato depois, a despesa antiga
    # não é reclassificada. Nulo = registro anterior à evolução; a Gold usa
    # COALESCE(evento, beneficiário).
    id_contrato: Mapped[int | None] = mapped_column(ForeignKey("contratos.id"), nullable=True)
    id_plano: Mapped[int | None] = mapped_column(ForeignKey("planos.id"), nullable=True)
    # Rede (opcionais — só quando a origem informa). `id_prestador` segue sendo o EXECUTANTE.
    id_estabelecimento: Mapped[int | None] = mapped_column(ForeignKey("prestadores.id"), nullable=True)
    id_prestador_solicitante: Mapped[int | None] = mapped_column(
        ForeignKey("prestadores.id"), nullable=True
    )

    data_evento: Mapped[date] = mapped_column(Date)
    # competência ANALÍTICA — a base (atendimento | apresentação | pagamento) é decisão de
    # onboarding registrada no mapping; as outras competências ficam preservadas abaixo.
    competencia: Mapped[date] = mapped_column(Date, index=True)
    # Consolidação do canônico V1 — todos opcionais (a origem pode não ter)
    competencia_apresentacao: Mapped[date | None] = mapped_column(Date, nullable=True)
    competencia_pagamento: Mapped[date | None] = mapped_column(Date, nullable=True)
    numero_guia: Mapped[str | None] = mapped_column(String(60), nullable=True)
    numero_conta: Mapped[str | None] = mapped_column(String(60), nullable=True)
    unidade_medida: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # normal | estorno | ajuste | cancelamento — estorno/ajuste podem ter valor negativo
    natureza_lancamento: Mapped[str | None] = mapped_column(String(14), nullable=True)
    # informado (veio da origem) | derivado (apresentado − glosado, regra do contrato)
    valor_pago_origem: Mapped[str | None] = mapped_column(String(10), nullable=True)
    tipo_atendimento: Mapped[str] = mapped_column(String(20))
    quantidade: Mapped[int] = mapped_column(Integer, default=1)

    # Semântica financeira (v1.1, Etapa B — ver docs/DATA_MODEL.md):
    #   valor_apresentado    = despesa BRUTA apresentada pelo prestador
    #   valor_glosado        = parcela glosada pela operadora (não paga ao prestador)
    #   valor_pago           = valor_apresentado - valor_glosado (pago ao prestador)
    #   valor_coparticipacao = parcela de valor_pago cobrada do beneficiário
    #   despesa líquida da operadora (calculada, não persistida por evento) =
    #       valor_pago - valor_coparticipacao
    #       = valor_apresentado - valor_glosado - valor_coparticipacao
    valor_apresentado: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    valor_glosado: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    valor_pago: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    valor_coparticipacao: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)

    # Rótulo do cenário sintético que injetou/alterou a linha (apenas QA/testes).
    cenario_tag: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
