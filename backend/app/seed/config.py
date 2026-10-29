"""Parâmetros do gerador sintético."""

from dataclasses import dataclass, field
from datetime import date

from app.core.tenant import DEFAULT_TENANT


@dataclass(frozen=True)
class SeedConfig:
    """Configuração reprodutível da geração.

    Defaults do MVP: 20.000 beneficiários, jan/2025 a dez/2026, seed 42.
    Para gerar 100.000, basta `SeedConfig(n_beneficiarios=100_000)`.
    """

    seed: int = 42
    n_beneficiarios: int = 20_000
    # Tenant a que a massa sintética pertence (v1.2). Toda linha gerada é carimbada com
    # este valor; as `agg_*` também. Um segundo tenant sintético usaria outro valor aqui.
    tenant_id: str = DEFAULT_TENANT
    inicio: date = date(2025, 1, 1)
    fim: date = date(2026, 12, 1)
    escala_eventos: float = 1.0
    # Sinistralidade média-alvo do baseline (primeiros meses), usada para calibrar a receita.
    sinistralidade_alvo: float = 0.75
    aplicar_cenarios: bool = True
    # Evolução, item 1 — simula a carga real: as contas da ÚLTIMA competência apresentadas
    # depois do corte (fim) ficam fora. Muda os números de dez/2026 (e o cenário S13); por
    # isso é opção da demo (`make seed`), não da suíte de testes.
    carga_parcial: bool = False

    # Lote para inserts em massa.
    chunk: int = field(default=10_000, compare=False)

    def competencias(self) -> list[date]:
        meses: list[date] = []
        y, m = self.inicio.year, self.inicio.month
        while (y, m) <= (self.fim.year, self.fim.month):
            meses.append(date(y, m, 1))
            m += 1
            if m == 13:
                m = 1
                y += 1
        return meses
