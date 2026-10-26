# Exemplos do layout V1 — arquivos opcionais (fictícios)

Arquivos **fictícios** no layout do mapping `data_platform/mappings/generic_operator_v1.yaml`
(separador `;`, decimal com vírgula, datas `dd/mm/aaaa`, competências `aaaa-mm`). Servem de
referência para o desenvolvimento de extratores — complementam o pacote base
(`../generic_operator/`), que traz especialidades, planos, contratos, plano × contrato,
prestadores, procedimentos, beneficiários, receitas por plano e eventos.

Nenhum nome de tabela/coluna de ERP aparece aqui: o extrator de cada origem entrega estes
layouts (ou um mapping próprio para o mesmo contrato canônico).

| Arquivo | Entidade canônica | O que mostra |
|---|---|---|
| `empresas.csv` | `empresa` | duas empresas; sem CNPJ |
| `empresa_contratos.csv` | `empresa_contrato` | uma empresa com 2 contratos; papel `contratante` |
| `faturas.csv` | `fatura` | cobrança direta (contrato, com acréscimo) e consolidada (empresa) |
| `fatura_itens.csv` | `fatura_item` | itens por contrato × plano + item de acréscimo; Σ itens = total |
| `recebimentos.csv` | `recebimento` | baixa do documento (caixa) |
| `receitas_reconhecidas.csv` | `receita_reconhecida` | detalhada por contrato × plano e consolidada da empresa SEM detalhamento |
| `vinculos.csv` | `vinculo_beneficiario` | troca de contrato em 01/04/2026, sem sobreposição |
| `precos.csv` | `preco_contratado` | reajuste de tabela de +10% com nova vigência |

Regras que estes arquivos respeitam (e que a carga verifica): ver
[docs/MODELO_CANONICO_V1_OFICIAL_CANDIDATO.md](../../../docs/MODELO_CANONICO_V1_OFICIAL_CANDIDATO.md)
e [docs/REGRAS_RECEITA_E_FATURAMENTO.md](../../../docs/REGRAS_RECEITA_E_FATURAMENTO.md).
