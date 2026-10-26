# Matriz de Capacidades × Dados — V1

> Consolidação do canônico V1. Fonte executável: `app/data_platform/readiness.py`
> (`REQUIREMENTS`, `signals`). Status por tenant em `/auth/me` (`capabilities`):
> **READY** (tudo), **PARTIAL** (obrigatórios ok, falta desejável — funciona com limitação),
> **NOT_READY** (falta obrigatório — indisponível). Feature comercial + dado pronto =
> disponível ([CAPABILITY_READINESS.md](CAPABILITY_READINESS.md)).

## Capacidades comerciais

| Capability | Obrigatório | Desejável (sem → PARTIAL) | O que fica limitado sem o desejável |
|---|---|---|---|
| `executive_overview` | eventos, receita¹, Gold | — | — |
| `loss_ratio_intelligence` | eventos, receita¹, Gold | — | — |
| `financial_composition` | eventos, receita¹, Gold | glosa, coparticipação | efeitos de glosa/coparticipação = 0 |
| `advanced_explanations` | eventos, beneficiários, Gold | saída da carteira, perfil de utilização | hipóteses de coorte menos específicas |
| `contract_intelligence` | eventos, beneficiários, contratos, Gold | **receita contratual**² | **sem sinistralidade de contrato/empresa** (vidas, despesa, concentração seguem) |
| `provider_intelligence` | eventos, prestadores, Gold | — | — |
| `beneficiary_intelligence` | eventos, beneficiários, Gold | — | — |
| `insights` | eventos, receita¹, Gold | — | — |
| `alerts` | eventos, Gold | — | — |

¹ receita = legada por plano **ou** reconhecida (não estimada).
² receita reconhecida atribuída a contrato (`contrato_plano`/`contrato`).

## Indicador × dado mínimo

| Indicador / análise | Entidades | Sem o dado |
|---|---|---|
| Sinistralidade da operadora | `evento_assistencial` + (`receita` **ou** `receita_reconhecida`) | indisponível |
| Composição bruta → líquida | evento (glosa, coparticipação) | glosa/copart = 0 |
| Sinistralidade de contrato | `receita_reconhecida` atribuída + vínculo no evento | "indisponível: motivo" |
| Sinistralidade de empresa | `empresa`, `empresa_contrato`, receita dos contratos e/ou consolidada | "indisponível: motivo" |
| Vidas históricas por contrato | `beneficiario` (adesão/saída) e/ou `vinculo_beneficiario` | vínculo do cadastro como único período |
| Atribuição de despesa a contrato | vínculo vigente na data do evento | evento não atribuído (explicado) |
| Reajuste de preço | `preco_contratado` | "custo unitário observado", nunca reajuste |
| Faturado × recebido | `fatura`, `recebimento` | não exibido |
| Rede por estabelecimento/solicitante | campos opcionais do evento | só executante |
| Condição crônica | — (pendência) | não classificada |

## Sinais de dados (`readiness.signals`)

`eventos`, `receita`, `receita_contratual`, `empresas`, `beneficiarios`, `contratos`,
`prestadores`, `gold`, `glosa`, `coparticipacao`, `saida_carteira`, `perfil_utilizacao`.
