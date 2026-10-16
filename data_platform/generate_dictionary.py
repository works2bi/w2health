"""Gera docs/DATA_DICTIONARY.md a partir de data_platform/contracts/*.yaml.

Uso:  python data_platform/generate_dictionary.py   (rodar da raiz do repo)
Mantém o dicionário sempre em sincronia com os contratos (fonte de verdade).
"""

from __future__ import annotations

import glob
import os

import yaml

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACTS = os.path.join(RAIZ, "data_platform", "contracts")
SAIDA = os.path.join(RAIZ, "docs", "DATA_DICTIONARY.md")

SENSIBILIDADE = {
    "internal": "interno",
    "operational": "operacional",
    "pii": "**dado pessoal**",
    "phi": "**dado de saúde sensível**",
}

ORDEM = [
    "tenant", "competencia", "beneficiario", "plano", "contrato", "plano_contrato", "prestador",
    "especialidade", "procedimento", "evento_assistencial", "receita",
    "receita_contrato", "glosa", "coparticipacao",
]


def main() -> None:
    contratos = {}
    for f in glob.glob(os.path.join(CONTRACTS, "*.yaml")):
        with open(f, encoding="utf-8") as fh:
            d = yaml.safe_load(fh)
        contratos[d["entity"]] = d

    linhas = [
        "# Dicionário de Dados — W2Health (camada canônica / Silver)",
        "",
        "> **Gerado** de `data_platform/contracts/*.yaml` por "
        "`data_platform/generate_dictionary.py`. Não editar à mão.",
        "> Documento para enviar ao cliente / consultor / engenheiro de dados: "
        "*\"este é o layout que o W2Health precisa receber\"*.",
        "",
        "Sensibilidade: `interno` · `operacional` · **dado pessoal** (LGPD) · "
        "**dado de saúde sensível** (LGPD art. 11 — pseudonimizar, nunca em log).",
        "",
    ]

    for nome in ORDEM:
        d = contratos.get(nome)
        if not d:
            continue
        linhas.append(f"## {nome}")
        linhas.append("")
        linhas.append(f"*Grão:* {d.get('grain', '—')}  ")
        linhas.append(f"*Chave primária:* `{', '.join(d.get('primary_key', []))}`  ")
        bks = "; ".join("(" + ", ".join(bk) + ")" for bk in d.get("business_keys", []))
        linhas.append(f"*Chaves de negócio:* {bks or '—'}  ")
        linhas.append(f"*Carga:* {d.get('load_strategy', '—')}")
        if d.get("status") == "draft" or d.get("version") == 0:
            linhas.append("  ")
            linhas.append("> ⚠️ **Layout preparado — não populado nem lido na v1.2.**")
        linhas.append("")
        if d.get("fields"):
            linhas.append(
                "| Campo | Tipo | Obrig. | Descrição | Origem típica | Sensibilidade | Regras DQ |"
            )
            linhas.append("|---|---|---|---|---|---|---|")
            for c in d["fields"]:
                rf = c.get("required_for", [])
                obr = "sim" if rf in (["ALL"], "ALL") else (", ".join(rf) if rf else "não")
                sens = SENSIBILIDADE.get(c.get("classification", "internal"), "interno")
                dq = ", ".join(c.get("quality_rules", []) or []) or "—"
                desc = (c.get("description", "") or "").replace("\n", " ").replace("|", "/").strip()
                linhas.append(
                    f"| `{c['name']}` | {c['type']} | {obr} | {desc} | "
                    f"{c.get('typical_source', '—')} | {sens} | {dq} |"
                )
            linhas.append("")
        if d.get("notes"):
            linhas.append("> " + " ".join(str(d["notes"]).split()))
            linhas.append("")

    with open(SAIDA, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(linhas))
    print(f"gerado: {SAIDA} ({len(linhas)} linhas)")


if __name__ == "__main__":
    main()
