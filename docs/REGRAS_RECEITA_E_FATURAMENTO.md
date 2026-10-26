# Regras de Receita e Faturamento — W2Health V1

> Consolidação do canônico V1 (23/10/2026). Fonte funcional: regras de um profissional de
> sistemas de gestão de operadoras. Implementação: `app/models/financeiro.py`,
> `app/data_platform/silver.py` (`atribuicao_receita`), `app/seed/aggregate.py`,
> `app/analytics/{receitas,empresas,contratos,reconciliacao_financeira}.py`.
> Contexto: [CONSOLIDACAO_CANONICO_V1.md](CONSOLIDACAO_CANONICO_V1.md).

## 1. Cadastro

```
Plano ──┐
        ├── planos_contratos (N:N) ── Contrato ── Beneficiário (vínculo com vigência)
        │                                │
        │                     empresas_contratos (papel, vigência)
        │                                │
        └────────────────────────────  Empresa (agrupador — NÃO é nível fixo)
```

- **Plano** = produto; **contrato** = vínculo contratual; **beneficiário** = pessoa coberta.
- Um contrato oferece 1+ planos (`planos_contratos`); o beneficiário está num par
  (contrato, plano) válido — FK composta, também no histórico de vínculos.
- **Empresa** agrupa contratos empresariais/coletivos por `empresas_contratos` (histórico,
  com papel). **No máximo uma empresa contratante por contrato em cada data** — a carga
  rejeita sobreposição (no lote e contra o já carregado). É isso que garante que nenhum
  contrato conte em duas empresas.

## 2. Os fatos financeiros da receita — cada um no seu grão

| Fato | Entidade | Grão | Competência | Entra na sinistralidade? |
|---|---|---|---|---|
| Unidade de cobrança | `fatura.unidade_cobranca` | contrato **ou** empresa | — | — |
| Documento de faturamento | `fatura` | documento | **faturamento** | não |
| Item do documento | `fatura_item` | item (natureza; contrato × plano se detalhado) | referência do item | não |
| Recebimento | `recebimento` | baixa | **recebimento** | não |
| **Receita reconhecida** | `receita_reconhecida` | lançamento, com **nível de atribuição** | **reconhecimento** | **sim** |
| Receita legada | `receita` | competência × plano | reconhecimento | sim (se não houver reconhecida) |

**Faturado ≠ recebido ≠ reconhecido.** São fatos diferentes, com competências diferentes;
a plataforma não converte um no outro nem os soma. Descontos e acréscimos ficam no
documento (`valor_desconto`, `valor_acrescimo`) e nos itens de natureza `desconto`/
`acrescimo` — não são receita reconhecida.

## 3. Nível de atribuição da receita reconhecida

Resolvido no Silver (`atribuicao_receita`) a partir do que a origem informou **e** do que
existe no cadastro. Nunca é informado pela origem, nunca é inferido:

| A origem informou | Existe no cadastro | Nível |
|---|---|---|
| contrato + plano | ambos | `contrato_plano` |
| contrato | sim | `contrato` |
| só empresa (cobrança consolidada **sem** detalhamento) | sim | `empresa` |
| qualquer código sem cadastro (ou plano desconhecido) | não | `nao_atribuida` (+ `referencia_origem`) |

- Cobrança da empresa **com** detalhamento por contrato × plano → carregar o detalhamento
  (linhas `contrato_plano`, com `numero_documento` da fatura da empresa).
- Cobrança da empresa **sem** detalhamento → uma linha `empresa`. **Não é distribuída.**
- O mesmo documento **não pode** ter linhas consolidadas E detalhadas (seria contado duas
  vezes): ERROR `receita_documento_duplicado` na carga; FAIL na reconciliação.
- Receita sem correspondência cadastral é **preservada** (WARNING `receita_sem_cadastro`),
  não rejeitada: é dinheiro real da operadora.

## 4. Rateio é proibido

Nenhuma receita é distribuída por vidas, despesa ou outra regra e apresentada como real.
`receita_reconhecida.estimada` existe só para uma futura alocação gerencial — que precisará
de metodologia, versão, valor original, valor alocado e sinalização de estimativa. Nesta
versão **nenhuma carga grava `estimada = true` e o motor ignora linhas estimadas**. O antigo
layout `receita_contrato` (que admitia `rateio_vidas`/`rateio_faixa`) foi substituído; linhas
antigas de rateio, se existissem, foram migradas como `estimada = true`.

## 5. Receita da operadora (sinistralidade geral)

Por competência, **uma** fonte:

1. receita reconhecida (soma de todos os níveis, não estimada), se houver linha;
2. senão, a receita legada por plano.

**Nunca as duas somadas.** Se ambas existem e diferem mais de R$ 0,01: `receita_status =
divergente` → a API não publica a sinistralidade da competência (nem acumulado 12 meses que
a inclua) e a carga que gerou a divergência é **reprovada** na reconciliação. Tenants só com
receita legada funcionam exatamente como antes (`fonte_receita = legada_plano`).

## 6. Sinistralidade contratual

```
sinistralidade_contrato = despesa assistencial líquida atribuída ao contrato
                        ÷ receita reconhecida ATRIBUÍDA ao contrato × 100
```

Publicada (`agg_contrato_competencia.sinistralidade_liquida`) **somente** quando:

| Condição | Se falhar, `motivo_sem_sinistralidade` |
|---|---|
| receita reconhecida atribuída ao contrato > 0 (níveis `contrato_plano`/`contrato`) | `sem_receita_atribuida` |
| receita da competência sem divergência entre fontes | `receita_divergente` |
| a empresa contratante do contrato NÃO tem receita consolidada sem detalhamento na competência (ela pode conter parte deste contrato) | `receita_empresa_sem_detalhamento` |

Faturado e recebido nunca entram. Despesa = eventos com `id_contrato` = o contrato (vínculo
da competência). Sem receita, a visão de contrato segue com vidas, despesa, PMPM,
concentração e utilização — e informa por que não há sinistralidade.

**Sinistralidade da empresa:** despesa dos contratos associados (contratante, na
competência) ÷ (receita atribuída a esses contratos + receita consolidada da empresa).
Indisponível se houver contrato da empresa sem receita identificada e nenhuma receita
consolidada, ou divergência.

## 7. Competências

| Fato | Competência | Campo |
|---|---|---|
| Receita reconhecida (indicadores) | reconhecimento | `receita_reconhecida.competencia` |
| Faturamento | faturamento | `fatura.competencia_faturamento` |
| Recebimento | recebimento | `recebimento.competencia_recebimento` |
| Item de fatura | referência (cobertura) | `fatura_item.competencia_referencia` |
| Despesa (indicadores) | **analítica** = decisão de onboarding registrada no mapping (atendimento, apresentação ou pagamento) | `evento.competencia` |
| Despesa — preservadas | atendimento / apresentação / pagamento | `data_evento`, `competencia_apresentacao`, `competencia_pagamento` |

Padrão do layout genérico: competência analítica da despesa = **mês de atendimento**;
da receita = **reconhecimento**. A lente de defasagem da investigação mostra o efeito de
atendimentos de meses anteriores lançados na competência.

## 8. Despesa e vínculo

- Grão do evento = **item** de conta (`source_record_id`); `numero_conta`/`numero_guia`
  preservam cabeçalho e guia. Guia autorizada ≠ conta apresentada ≠ conta paga.
- Contrato/plano do evento = **vínculo vigente na data do atendimento**, congelado na carga:
  histórico (se o beneficiário tem); senão o cadastro, só se `adesão ≤ data < saída`. Fora
  disso: **não atribuído** (`id_contrato` nulo, chave `nao_atribuido` nas dimensões) — nunca
  o contrato atual.
- `valor_pago` = valor **aprovado** após glosa (informado ou derivado — `valor_pago_origem`),
  não o pago em caixa ao prestador (pendência P5).
- Estorno/ajuste/cancelamento (`natureza_lancamento`) admitem valor negativo; a contagem de
  eventos ainda não desconta estornos (pendência P4).
