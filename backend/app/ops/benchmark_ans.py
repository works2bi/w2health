"""Carga da referência de mercado a partir dos dados abertos da ANS (Evolução, item 7).

    python -m app.ops.benchmark_ans --periodo 2025 \\
        --demonstracoes 4T2025.csv --cadop Relatorio_cadop.csv [--beneficiarios benef_2025.csv]
    python -m app.ops.benchmark_ans --ilustrativo        # só a referência de demonstração

Arquivos em https://dadosabertos.ans.gov.br (demonstrações contábeis, operadoras ativas e
beneficiários por operadora). Grava somente a DISTRIBUIÇÃO por modalidade (nenhum dado de
operadora identificada). Roda com o papel dono (`DATABASE_ADMIN_URL`).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.analytics import benchmark
from app.db.session import AdminSessionLocal


def main() -> None:
    ap = argparse.ArgumentParser(prog="benchmark_ans")
    ap.add_argument("--periodo", help="AAAA (ano fechado, arquivo do 4º trimestre) ou AAAA-Tn")
    ap.add_argument("--demonstracoes", type=Path)
    ap.add_argument("--cadop", type=Path)
    ap.add_argument("--beneficiarios", type=Path)
    ap.add_argument("--meses", type=int, default=12, help="meses cobertos pelo saldo acumulado")
    ap.add_argument("--ilustrativo", action="store_true")
    a = ap.parse_args()

    with AdminSessionLocal() as s:
        if a.ilustrativo:
            n = benchmark.garantir_ilustrativo(s)
        else:
            if not (a.periodo and a.demonstracoes and a.cadop):
                ap.error("informe --periodo, --demonstracoes e --cadop (ou --ilustrativo)")
            linhas = benchmark.calcular_de_dados_abertos(
                a.demonstracoes.read_bytes(), a.cadop.read_bytes(), a.periodo,
                a.beneficiarios.read_bytes() if a.beneficiarios else None, a.meses)
            n = benchmark.gravar(s, linhas)
        s.commit()
    print(json.dumps({"linhas_gravadas": n}, ensure_ascii=False))


if __name__ == "__main__":
    main()
