"""Carteira e receita: competências, beneficiários, receitas de contraprestação.

`Competencia` é um **calendário global** (não pertence a um cliente) — único modelo
persistido sem `tenant_id`. `Beneficiario` e `Receita` são tenant-aware; as chaves de
negócio passam a ser `(tenant_id, codigo)` e `(tenant_id, competencia, id_plano)`.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models._mixins import LineageMixin, TenantMixin
from app.models.catalog import Contrato, Plano, Regiao


class Competencia(Base):
    __tablename__ = "competencias"

    competencia: Mapped[date] = mapped_column(Date, primary_key=True)  # 1º dia do mês
    ano: Mapped[int] = mapped_column(Integer)
    mes: Mapped[int] = mapped_column(Integer)
    mes_nome: Mapped[str] = mapped_column(String(20))
    trimestre: Mapped[int] = mapped_column(Integer)
    is_inverno: Mapped[bool] = mapped_column(Boolean, default=False)


class Beneficiario(TenantMixin, LineageMixin, Base):
    __tablename__ = "beneficiarios"
    __table_args__ = (
        UniqueConstraint("tenant_id", "codigo", name="uq_beneficiarios_tenant_codigo"),
        # o plano do beneficiário tem de ser um dos planos do contrato dele
        ForeignKeyConstraint(
            ["tenant_id", "id_contrato", "id_plano"],
            ["planos_contratos.tenant_id", "planos_contratos.id_contrato",
             "planos_contratos.id_plano"],
            name="fk_beneficiarios_plano_contrato",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(16), index=True)  # BEN-000001 (único por tenant)
    sexo: Mapped[str] = mapped_column(String(1))  # M | F
    data_nascimento: Mapped[date] = mapped_column(Date)
    faixa_etaria: Mapped[str] = mapped_column(String(10), index=True)
    id_regiao: Mapped[int] = mapped_column(ForeignKey("regioes.id"), index=True)
    id_plano: Mapped[int] = mapped_column(ForeignKey("planos.id"), index=True)
    id_contrato: Mapped[int] = mapped_column(ForeignKey("contratos.id"), index=True)
    data_adesao: Mapped[date] = mapped_column(Date)
    data_saida: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(12), default="ativo")  # ativo | inativo

    regiao: Mapped[Regiao] = relationship()
    plano: Mapped[Plano] = relationship()
    contrato: Mapped[Contrato] = relationship()


class Receita(TenantMixin, LineageMixin, Base):
    __tablename__ = "receitas"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "competencia", "id_plano", name="uq_receitas_tenant_comp_plano"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    competencia: Mapped[date] = mapped_column(Date, index=True)
    id_plano: Mapped[int] = mapped_column(ForeignKey("planos.id"), index=True)
    quantidade_beneficiarios: Mapped[int] = mapped_column(Integer)
    receita_contraprestacao: Mapped[Decimal] = mapped_column(Numeric(16, 2))

    plano: Mapped[Plano] = relationship()
