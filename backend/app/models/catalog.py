"""Catálogos: regiões, planos, contratos, especialidades, procedimentos, prestadores.

Todos são **tenant-aware** (v1.2): uma operadora real traz o próprio catálogo de planos,
contratos, prestadores etc. As chaves de negócio (`codigo`, `cid`) são únicas
**por tenant** — `(tenant_id, codigo)` —, nunca globalmente.
"""

from decimal import Decimal

from sqlalchemy import Boolean, ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models._mixins import LineageMixin, TenantMixin


class Regiao(TenantMixin, LineageMixin, Base):
    __tablename__ = "regioes"

    id: Mapped[int] = mapped_column(primary_key=True)
    cidade: Mapped[str] = mapped_column(String(80))
    uf: Mapped[str] = mapped_column(String(2))
    macrorregiao: Mapped[str] = mapped_column(String(20))


class Plano(TenantMixin, LineageMixin, Base):
    __tablename__ = "planos"
    __table_args__ = (UniqueConstraint("tenant_id", "codigo", name="uq_planos_tenant_codigo"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(20))
    nome: Mapped[str] = mapped_column(String(80))
    segmentacao: Mapped[str | None] = mapped_column(String(30), nullable=True)  # ambulatorial | hospitalar | completo
    # só o gerador sintético usa (calibração de receita) — fonte externa não fornece
    ticket_medio_base: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    # Coparticipação (v1.1, Etapa B): se o plano cobra, e o percentual sobre valor_pago
    # aplicado a atendimentos tipicamente sujeitos a copay (consulta/exame/terapia/PS).
    tem_coparticipacao: Mapped[bool] = mapped_column(Boolean, default=False)
    percentual_coparticipacao: Mapped[Decimal] = mapped_column(Numeric(5, 4), default=0)


class Contrato(TenantMixin, LineageMixin, Base):
    __tablename__ = "contratos"
    __table_args__ = (UniqueConstraint("tenant_id", "codigo", name="uq_contratos_tenant_codigo"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # chave de negócio (contrato canônico) — Fase 2
    codigo: Mapped[str] = mapped_column(String(40))
    # sem plano aqui: um contrato oferece N planos (`PlanoContrato`)
    nome: Mapped[str] = mapped_column(String(80))
    tipo: Mapped[str | None] = mapped_column(String(20), nullable=True)  # PF | PME | Empresarial
    # Bucket de tamanho-alvo da massa sintética (~vidas). Só orienta a geração; um
    # cliente real não precisa fornecer isto (nulo para fontes externas). v1.2.
    vidas_alvo: Mapped[int | None] = mapped_column(Integer, nullable=True, default=0)


class PlanoContrato(TenantMixin, LineageMixin, Base):
    """Planos oferecidos por um contrato (N:N). Chave: `(tenant_id, id_contrato, id_plano)`.

    O beneficiário aponta para o par exato (FK composta em `beneficiarios`), então ele só
    pode estar num plano que o contrato dele oferece. Preço (tabela/faixa etária por par)
    é outra entidade — fora da v1."""

    __tablename__ = "planos_contratos"

    tenant_id: Mapped[str] = mapped_column(String(40), primary_key=True, index=True)
    id_contrato: Mapped[int] = mapped_column(ForeignKey("contratos.id"), primary_key=True)
    id_plano: Mapped[int] = mapped_column(ForeignKey("planos.id"), primary_key=True, index=True)

    contrato: Mapped[Contrato] = relationship()
    plano: Mapped[Plano] = relationship()


class Especialidade(TenantMixin, LineageMixin, Base):
    __tablename__ = "especialidades"
    __table_args__ = (
        UniqueConstraint("tenant_id", "codigo", name="uq_especialidades_tenant_codigo"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(20))
    nome: Mapped[str] = mapped_column(String(80))
    grupo: Mapped[str | None] = mapped_column(String(30), nullable=True)  # clinica | cirurgica | diagnostico | terapia


class Procedimento(TenantMixin, LineageMixin, Base):
    __tablename__ = "procedimentos"
    __table_args__ = (
        UniqueConstraint("tenant_id", "codigo", name="uq_procedimentos_tenant_codigo"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(20))
    descricao: Mapped[str] = mapped_column(String(120))
    id_especialidade: Mapped[int] = mapped_column(ForeignKey("especialidades.id"), index=True)
    grupo_procedimento: Mapped[str] = mapped_column(String(60))
    # complexidade/custo_base/tipo típico/idades: parâmetros do GERADOR sintético —
    # nulos quando o procedimento vem de fonte externa.
    complexidade: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1..5
    custo_base: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    tipo_atendimento_tipico: Mapped[str | None] = mapped_column(String(20), nullable=True)
    idade_min: Mapped[int] = mapped_column(Integer, default=0)
    idade_max: Mapped[int] = mapped_column(Integer, default=120)
    # pontual | recorrente | variavel — apoio à classificação de hipóteses na análise de
    # coortes (Etapa A da v1.1). Nunca usado sozinho para afirmar causalidade.
    # Nulo = origem não informou (a análise de coortes então não levanta hipótese).
    perfil_utilizacao: Mapped[str | None] = mapped_column(String(12), nullable=True, default="variavel")

    especialidade: Mapped[Especialidade] = relationship()


class Prestador(TenantMixin, LineageMixin, Base):
    __tablename__ = "prestadores"
    __table_args__ = (UniqueConstraint("tenant_id", "codigo", name="uq_prestadores_tenant_codigo"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # chave de negócio (contrato canônico) — Fase 2
    codigo: Mapped[str] = mapped_column(String(40))
    nome_ficticio: Mapped[str] = mapped_column(String(100))
    # hospital | clinica | laboratorio | pronto_atendimento | consultorio
    tipo_prestador: Mapped[str | None] = mapped_column(String(30), nullable=True)
    id_regiao: Mapped[int] = mapped_column(ForeignKey("regioes.id"), index=True)
    id_especialidade_principal: Mapped[int] = mapped_column(
        ForeignKey("especialidades.id"), index=True
    )
    # multiplicador interno do GERADOR sintético (nulo para fontes externas)
    nivel_preco: Mapped[float | None] = mapped_column(Numeric(5, 3), nullable=True, default=1.0)

    regiao: Mapped[Regiao] = relationship()
    especialidade_principal: Mapped[Especialidade] = relationship()


class Diagnostico(TenantMixin, LineageMixin, Base):
    __tablename__ = "diagnosticos"
    __table_args__ = (UniqueConstraint("tenant_id", "cid", name="uq_diagnosticos_tenant_cid"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    cid: Mapped[str] = mapped_column(String(10))
    descricao: Mapped[str] = mapped_column(String(120))
    id_especialidade: Mapped[int | None] = mapped_column(
        ForeignKey("especialidades.id"), nullable=True, index=True
    )
