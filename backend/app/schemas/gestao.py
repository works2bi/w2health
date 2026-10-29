"""Schemas da gestão da ação: planos de ação e assinaturas de alertas (Evolução 3 e 4)."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.analytics import DIMENSOES
from app.models.gestao import FREQUENCIAS, ORIGENS_PLANO, SEVERIDADES, STATUS_PLANO

StatusPlano = Literal["aberto", "em_andamento", "concluido", "cancelado"]
OrigemPlano = Literal["insight", "alerta", "manual"]
Dimensao = Literal["grupo_despesa", "tipo_atendimento", "especialidade", "procedimento",
                   "prestador", "regiao", "faixa_etaria", "sexo", "plano", "contrato"]
Frequencia = Literal["semanal", "mensal"]
Severidade = Literal["critica", "atencao", "informativo"]

assert set(StatusPlano.__args__) == set(STATUS_PLANO)  # trava as duas fontes de verdade
assert set(OrigemPlano.__args__) == set(ORIGENS_PLANO)
assert set(Dimensao.__args__) == set(DIMENSOES)
assert set(Frequencia.__args__) == set(FREQUENCIAS)
assert set(Severidade.__args__) == set(SEVERIDADES)


class PlanoAcaoCreate(BaseModel):
    titulo: str = Field(min_length=3, max_length=160)
    descricao: str = Field(default="", max_length=4000)
    origem_tipo: OrigemPlano = "manual"
    origem_ref: str | None = Field(default=None, max_length=160)
    dimensao: Dimensao | None = None
    chave: str | None = Field(default=None, max_length=120)
    alvo_rotulo: str | None = Field(default=None, max_length=200)
    responsavel: str = Field(min_length=2, max_length=120)
    prazo: date | None = None
    status: StatusPlano = "aberto"
    competencia_inicio: date
    meta_reducao_pct: float | None = Field(default=None, ge=0, le=100)


class PlanoAcaoUpdate(BaseModel):
    titulo: str | None = Field(default=None, min_length=3, max_length=160)
    descricao: str | None = Field(default=None, max_length=4000)
    responsavel: str | None = Field(default=None, min_length=2, max_length=120)
    prazo: date | None = None
    status: StatusPlano | None = None
    competencia_inicio: date | None = None
    meta_reducao_pct: float | None = Field(default=None, ge=0, le=100)


class PlanoAcaoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    titulo: str
    descricao: str
    origem_tipo: str
    origem_ref: str | None
    dimensao: str | None
    chave: str | None
    alvo_rotulo: str
    responsavel: str
    prazo: date | None
    status: str
    competencia_inicio: date
    meta_reducao_pct: float | None
    criado_por: str
    criado_em: datetime
    atualizado_em: datetime


class AssinaturaIn(BaseModel):
    frequencia: Frequencia = "semanal"
    severidade_minima: Severidade = "atencao"
    incluir_insights: bool = True
    ativo: bool = True


class AssinaturaOut(AssinaturaIn):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
