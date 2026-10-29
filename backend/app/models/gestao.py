"""Gestão da ação (Evolução do produto, itens 3 e 4) — data plane, escrita pelo runtime.

* `PlanoAcao` — ação decidida a partir de um insight/alerta (responsável, prazo, status) e
  o ALVO cujo efeito será medido (dimensão × chave da camada analítica, ou a carteira).
* `AssinaturaAlerta` — quem recebe o resumo de alertas/insights, com que frequência.
* `EnvioNotificacao` — trilha de cada envio (o quê, para quem, quando, resultado).

Todas tenant-aware (RLS `tenant_isolation`, migration a9b8c7d6e5f4).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models._mixins import TenantMixin

STATUS_PLANO = ("aberto", "em_andamento", "concluido", "cancelado")
ORIGENS_PLANO = ("insight", "alerta", "manual")
FREQUENCIAS = ("semanal", "mensal")
SEVERIDADES = ("critica", "atencao", "informativo")


class PlanoAcao(TenantMixin, Base):
    __tablename__ = "planos_acao"

    id: Mapped[int] = mapped_column(primary_key=True)
    titulo: Mapped[str] = mapped_column(String(160))
    descricao: Mapped[str] = mapped_column(Text, default="")
    origem_tipo: Mapped[str] = mapped_column(String(10), default="manual")
    origem_ref: Mapped[str | None] = mapped_column(String(160), nullable=True)
    # alvo da medição: dimensão da camada analítica (+ chave) ou None = carteira inteira
    dimensao: Mapped[str | None] = mapped_column(String(30), nullable=True)
    chave: Mapped[str | None] = mapped_column(String(120), nullable=True)
    alvo_rotulo: Mapped[str] = mapped_column(String(200), default="Carteira")
    responsavel: Mapped[str] = mapped_column(String(120))
    prazo: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(14), default="aberto")
    # a partir de qual competência a ação vale (antes = linha de base; depois = efeito)
    competencia_inicio: Mapped[date] = mapped_column(Date)
    meta_reducao_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    criado_por: Mapped[str] = mapped_column(String(254))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AssinaturaAlerta(TenantMixin, Base):
    __tablename__ = "assinaturas_alerta"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    email: Mapped[str] = mapped_column(String(254))
    frequencia: Mapped[str] = mapped_column(String(10), default="semanal")
    severidade_minima: Mapped[str] = mapped_column(String(12), default="atencao")
    incluir_insights: Mapped[bool] = mapped_column(Boolean, default=True)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EnvioNotificacao(TenantMixin, Base):
    __tablename__ = "envios_notificacao"

    id: Mapped[int] = mapped_column(primary_key=True)
    assinatura_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("assinaturas_alerta.id", ondelete="SET NULL"), nullable=True)
    competencia: Mapped[date] = mapped_column(Date)
    canal: Mapped[str] = mapped_column(String(10), default="email")
    destinatario: Mapped[str] = mapped_column(String(254))
    assunto: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(10))  # enviado | simulado | falhou | vazio
    itens: Mapped[dict] = mapped_column(JSON, default=dict)
    erro: Mapped[str | None] = mapped_column(String(300), nullable=True)
    enviado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
