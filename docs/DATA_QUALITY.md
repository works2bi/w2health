# Data Quality — W2Health (v1.2)

Regras mínimas de qualidade aplicadas às **linhas canônicas** (Silver), antes de promover
para Gold/Serving. Implementação executável e testada:
[`data_platform/quality/rules.py`](../data_platform/quality/rules.py)
(testes: `backend/tests/test_data_quality.py`). Catálogo em tabela:
[`data_platform/quality/rules.md`](../data_platform/quality/rules.md).

## Severidades

| Severidade | Significado | Efeito no pipeline |
|---|---|---|
| **ERROR** | dado inconsistente que invalida a análise | **bloqueia** a entidade — a carga não avança para Silver/Gold; corrigir origem/mapping e reprocessar |
| **WARNING** | anomalia que não invalida, mas precisa de atenção | **continua**; grava em `data_quality_results`; pode exigir revisão manual |
| **INFO** | observação | apenas anotação |

Decisão programática: `rules.bloqueia_processamento(violacoes)` → `True` se houver
qualquer `ERROR`.

## Regras

| id | Entidade | Severidade | Condição |
|---|---|---|---|
| `tenant_id_ausente` | todas | ERROR | linha sem `tenant_id` |
| `benef_inexistente_em_evento` | evento | ERROR | `id_beneficiario` não existe na Silver |
| `contrato_inexistente` | evento, beneficiario | ERROR | `id_contrato` referenciado não existe |
| `prestador_inexistente` | evento | ERROR | `id_prestador` não existe |
| `valor_negativo` | evento, receita | ERROR | `valor_apresentado`/`valor_pago`/`receita_contraprestacao` < 0 |
| `competencia_invalida` | evento, receita | ERROR | competência não é o 1º dia de um mês válido |
| `evento_sem_data` | evento | ERROR | `data_evento` ausente |
| `glosa_maior_que_apresentado` | evento | ERROR | `valor_glosado` > `valor_apresentado` |
| `data_saida_antes_adesao` | beneficiario | ERROR | `data_saida` < `data_adesao` |
| `evento_duplicado` | evento | WARNING | `(source_system, source_record_id)` repetido no lote |
| `receita_duplicada` | receita | WARNING | `(competencia, id_plano)` repetido |
| `despesa_liquida_inconsistente` | evento | WARNING | `\|despesa_liquida − (bruta − glosa − copart)\| > 0,01` (será recalculada na Gold) |
| `coparticipacao_acima_permitido` | evento | WARNING | `valor_coparticipacao` > `valor_pago` |
| `vigencia_invertida` | preco_contratado, vinculo_beneficiario | ERROR | `vigencia_fim` ≤ `vigencia_inicio` (Evolução V1) |
| `vigencia_sobreposta` | preco_contratado, vinculo_beneficiario | ERROR | duas vigências da mesma chave se sobrepõem no lote (Evolução V1) — a sobreposição com cargas anteriores não é verificada nesta versão |
| `preco_nao_positivo` | preco_contratado | ERROR | `valor_unitario` ≤ 0 (Evolução V1) |
| `plano_fora_do_contrato` | beneficiario, vinculo_beneficiario, fatura_item, receita_reconhecida | ERROR | o plano não é oferecido pelo contrato (`plano_contrato`) |
| `vigencia_sobreposta` (persistido) | vinculo, preço, empresa_contrato | ERROR | Consolidação V1: sobreposição também contra o JÁ carregado (mesma `vigencia_inicio` = atualização) |
| `unidade_cobranca_inconsistente` | fatura | ERROR | unidade `contrato`/`empresa` sem (ou com os dois) códigos |
| `fatura_total_inconsistente` | fatura | ERROR | `valor_total ≠ bruto − desconto + acréscimo` |
| `receita_nivel_ambiguo` | receita_reconhecida | ERROR | contrato e empresa ao mesmo tempo |
| `receita_documento_duplicado` | receita_reconhecida | ERROR | mesmo documento consolidado (empresa) E detalhado (contrato) — no lote ou com o persistido |
| `receita_sem_cadastro` | receita_reconhecida | WARNING | código sem cadastro → preservada como **não atribuída** (não rejeitada) |
| `valor_negativo` (exceções) | evento, fatura_item, receita_reconhecida | ERROR | Consolidação V1: negativo admitido só em estorno/ajuste/cancelamento (evento), desconto/ajuste/retroativo (item), ajuste (receita) |

## Onde os resultados ficam

`data_quality_results` (tenant-aware, RLS): `ingestion_run_id`, `pipeline_run_id`,
`rule_id`, `rule_description`, `entity`, `severity`, `blocking`, `records_checked`,
`records_failed`, `sample`. Desde a Fase 2 é **preenchida a cada ingestão** — inclusive
quando a carga falha. A amostra guarda só **referências** (linha do arquivo,
`source_record_id`, motivo), nunca o conteúdo da linha.

## Bloqueio vs. continuação — resumo

- **Bloqueia** (`ERROR`): referências quebradas (beneficiário/contrato/prestador
  inexistentes), valores impossíveis (negativos, glosa > apresentado), datas inválidas
  (competência, evento sem data, saída antes da adesão), `tenant_id` ausente.
- **Continua com alerta** (`WARNING`): duplicidades (dedup resolve), inconsistência de
  despesa líquida (Gold recalcula), coparticipação acima do pago (registra e segue).

## Extensões previstas (ainda não implementadas)

Volumetria por competência (queda/pico anômalo de linhas), completude por campo (%
preenchido), consistência temporal (competência de pagamento << competência de
atendimento), reconciliação com o fechamento contábil do cliente.

## Fase 2 — DQ no pipeline de ingestão

Implementação: `backend/app/data_platform/quality.py`, que **reusa** as regras acima
(`data_platform/quality/rules.py`, carregado sem duplicar lógica) e acrescenta as regras
que só existem na carga de arquivo, avaliadas **antes** de qualquer promoção para Silver:

| id | Severidade | Condição |
|---|---|---|
| `arquivo_obrigatorio_ausente` | ERROR (bloqueia) | arquivo de entidade `required: true` no mapping não veio no pacote |
| `arquivo_opcional_ausente` | INFO | arquivo opcional ausente — capacidades dependentes ficam indisponíveis (readiness) |
| `colunas_ausentes` | ERROR (bloqueia) | coluna de origem do mapping ausente no arquivo |
| `mapping_invalido` | ERROR | linha rejeitada pelo mapping (tipo, obrigatório, vocabulário) — com motivos agregados |
| `chave_duplicada` / `evento_duplicado` / `receita_duplicada` | WARNING | chave de negócio repetida no arquivo — mantém a última |
| `fk_inexistente_<campo>` | ERROR | código referenciado não existe no pacote válido nem na Silver do tenant |
| `uf_invalida` | ERROR | UF fora da lista oficial (a região é derivada da UF) |
| `cobertura_<campo>` | INFO | % de preenchimento de campos opcionais (glosa, coparticipação, saída…) |
| `volume` | INFO | linhas recebidas por entidade |
| `gate_rejeicao` | ERROR (bloqueia) | linhas rejeitadas / recebidas > `quality.max_rejected_ratio` do mapping |

**Gate:** com o limite padrão (`max_rejected_ratio: 0`), qualquer linha com ERROR bloqueia a
carga inteira: ingestão `FAILED`, estágio `RECEIVED`, nada publicado, motivo em
`error_summary` e resultados em `data_quality_results`. Com limite > 0 (decisão de
onboarding), as linhas com ERROR são descartadas e a carga é `PARTIAL`. Referências por
código são resolvidas em ordem de dependência: um código só serve de referência se a
linha dele for válida.

Testes: `test_data_quality_bloqueia_promocao`, `test_arquivo_obrigatorio_ausente_falha`,
`test_linha_invalida_e_rejeitada_com_motivo`, `test_coluna_ausente_no_arquivo_rejeita_a_entidade`.
