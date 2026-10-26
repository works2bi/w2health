# Reconciliação Financeira V1 — receita, despesa e vidas

> Consolidação do canônico V1. Implementação: `app/analytics/reconciliacao_financeira.py`
> (verificações), `app/data_platform/reconciliation.py::financeiro` (gate do pipeline),
> API `GET /analytics/receitas/reconciliacao`. Complementa a reconciliação de carga
> (origem × Silver × Gold) de [RECONCILIATION.md](RECONCILIATION.md).

## Status

| Status | Significado | Efeito |
|---|---|---|
| `PASS` | diferença ≤ R$ 0,01 (ou 0 registros/vidas) | — |
| `WARNING` | diferença **explicada** (ex.: receita não detalhada até contrato × plano) | registrada; a carga publica |
| `FAIL` | divergência que impede o indicador afetado | **a carga não é publicada**; na API, o indicador afetado não é publicado |
| `INFO` | comparação informativa entre fatos não equivalentes | fora do gate |

## Verificações

| `check_id` | Esperado × obtido | Garante |
|---|---|---|
| `receita_sem_duplicidade` | receita da fonte escolhida × receita da Gold | uma fonte por competência, sem soma de fontes nem duplicação em join |
| `receita_original_vs_niveis` | Σ reconhecida × (atribuída + empresa sem detalhamento + não atribuída) | nenhum valor perdido na atribuição |
| `receita_reconhecida_vs_legada` | legada × reconhecida (competências com as duas) | divergência bloqueia a sinistralidade da competência |
| `receita_atribuida_vs_plano` | legada do plano × detalhada contrato × plano | consolidado por plano; diferença ≤ receita não detalhada = WARNING explicado |
| `receita_atribuida_vs_contratos` | Σ atribuída (Silver) × Σ receita por contrato (Gold) | consolidado por contrato |
| `empresa_vs_contratos_relacionados` | itens detalhados de documento de empresa fora dos contratos da empresa | receita por empresa × contratos relacionados |
| `fatura_itens_vs_total` | Σ itens × valor total do documento | documento consistente |
| `faturado_vs_recebido` | faturado × recebido (INFO) | não equivalência declarada |
| `despesa_eventos_vs_gold` | Σ líquida dos eventos × Gold | despesa por evento × agregados |
| `despesa_contratos_mais_nao_atribuida` | total × (Σ contratos + sem vínculo vigente) | despesa por contrato histórico + não atribuída explicada |
| `despesa_beneficiarios_vs_total` | total × Σ por beneficiário | despesa por beneficiário |
| `vidas_historicas` | exposição pelo vínculo vigente × Σ vidas por contrato (Gold) | contagem histórica de vidas |
| `receita_documento_em_dois_niveis` | documentos consolidados E detalhados | ausência de duplicidade financeira |

## Demonstração (laboratório `w2h-financeiro`, verificada em `tests/test_cenarios_financeiro.py`)

- jan/2026: reconhecida = R$ 14.850 = 10.500 atribuída (ALFA 6.200 + BETA 4.300) + 4.100
  empresa GAMA sem detalhamento + 250 sem cadastro; legada = 14.850 → `ok`.
- Por plano: legada − detalhada = R$ 2.350 (PL-A) e R$ 2.000 (PL-B) ≤ R$ 4.350 não detalhada →
  `WARNING` explicado (nunca `FAIL`, nunca rateio).
- Despesa de mai/2026: R$ 4.350 = Σ contratos + R$ 100 de B-Z sem vínculo vigente.
- Vidas: CT-A1 10 → 9, CT-A2 8 → 9 (troca de B-X em 01/04), CT-B1 6 → 5 (saída de B-Z).
- Divergência induzida (+R$ 100 na legada de mar): `receita_reconhecida_vs_legada = FAIL`,
  sinistralidade de mar = nula com aviso, contratos `receita_divergente`.
- Pipeline: carga com receita legada divergente da reconhecida já carregada é reprovada
  (`FAILED`/`PROCESSED`) e nada é publicado (`tests/test_pipeline_financeiro.py`).
