"""Referência de mercado (Evolução, item 7) — control plane, global, somente leitura p/ o runtime.

Uma linha por (fonte, período, segmento, indicador): distribuição do indicador entre as
operadoras do segmento (p25 / mediana / p75 / média ponderada). Não há dado de nenhuma
operadora identificada aqui — só a distribuição agregada.

* `fonte = 'ans_dados_abertos'`: calculado de arquivos públicos da ANS
  (`python -m app.ops.benchmark_ans`);
* `fonte = 'ilustrativo'`: valores de DEMONSTRAÇÃO, sempre exibidos como tal.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Float, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import ControlBase


class BenchmarkMercado(ControlBase):
    __tablename__ = "benchmarks_mercado"
    __table_args__ = (UniqueConstraint("fonte", "periodo", "segmento", "indicador"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fonte: Mapped[str] = mapped_column(String(30))
    periodo: Mapped[str] = mapped_column(String(10))       # AAAA (ano) ou AAAA-Tn
    segmento: Mapped[str] = mapped_column(String(40))      # modalidade (ou 'mercado')
    indicador: Mapped[str] = mapped_column(String(40))     # sinistralidade_pct | despesa_pmpm
    p25: Mapped[float] = mapped_column(Float)
    mediana: Mapped[float] = mapped_column(Float)
    p75: Mapped[float] = mapped_column(Float)
    media_ponderada: Mapped[float | None] = mapped_column(Float, nullable=True)
    n_operadoras: Mapped[int] = mapped_column(Integer)
    atualizado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
