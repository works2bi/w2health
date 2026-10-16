# Dicionário de Dados — W2Health (camada canônica / Silver)

> **Gerado** de `data_platform/contracts/*.yaml` por `data_platform/generate_dictionary.py`. Não editar à mão.
> Documento para enviar ao cliente / consultor / engenheiro de dados: *"este é o layout que o W2Health precisa receber"*.

Sensibilidade: `interno` · `operacional` · **dado pessoal** (LGPD) · **dado de saúde sensível** (LGPD art. 11 — pseudonimizar, nunca em log).

## tenant

*Grão:* 1 linha por cliente (operadora) da plataforma W2Health  
*Chave primária:* `id`  
*Chaves de negócio:* (id)  
*Carga:* FULL

| Campo | Tipo | Obrig. | Descrição | Origem típica | Sensibilidade | Regras DQ |
|---|---|---|---|---|---|---|
| `id` | string | sim | Slug do cliente. Vai em `tenant_id` de TODAS as demais entidades. | definido no onboarding, não vem de sistema de origem | interno | — |
| `nome` | string | sim | Razão social / nome de exibição da operadora. | onboarding | interno | — |
| `status` | string | sim | onboarding / ativo / suspenso | gestão da plataforma | interno | — |

> `tenant` é o único cadastro global da plataforma (não tem `tenant_id`). Toda linha de qualquer outra entidade DEVE referenciar um `tenant.id` existente.

## competencia

*Grão:* 1 linha por mês de referência (1º dia do mês)  
*Chave primária:* `competencia`  
*Chaves de negócio:* (competencia)  
*Carga:* FULL

| Campo | Tipo | Obrig. | Descrição | Origem típica | Sensibilidade | Regras DQ |
|---|---|---|---|---|---|---|
| `competencia` | date | sim | Primeiro dia do mês (AAAA-MM-01). Grão temporal de toda a análise. | gerado (calendário), não vem do cliente | interno | competencia_invalida |
| `ano` | int | sim | Ano. | derivado | interno | — |
| `mes` | int | sim | Mês 1-12. | derivado | interno | — |
| `trimestre` | int | não | Trimestre 1-4. | derivado | interno | — |

> Calendário global. Não recebe `tenant_id` — é o único caso além de `tenant`.

## beneficiario

*Grão:* 1 linha por beneficiário (vida) da operadora — estado atual  
*Chave primária:* `tenant_id, id_beneficiario`  
*Chaves de negócio:* (tenant_id, codigo); (tenant_id, source_system, source_record_id)  
*Carga:* UPSERT

| Campo | Tipo | Obrig. | Descrição | Origem típica | Sensibilidade | Regras DQ |
|---|---|---|---|---|---|---|
| `tenant_id` | string | sim | Cliente. | constante do conector | interno | tenant_id_ausente |
| `id_beneficiario` | bigint | sim | Chave técnica do W2Health (surrogate). NUNCA usar CPF/CNS como chave. | gerado no W2Health a partir da business key | interno | — |
| `codigo` | string | sim | Identificador do beneficiário no sistema de origem (matrícula/carteirinha), pseudonimizado. Único POR TENANT. | cadastro / carteira | operacional | — |
| `data_nascimento` | date | beneficiary, contract | Usada para faixa etária e (futuro) rateio de receita por faixa. | cadastro | **dado pessoal** | — |
| `sexo` | string | não | M / F (como registrado na operadora). | cadastro | **dado pessoal** | — |
| `id_plano` | bigint | loss_ratio, contract | Plano em que a vida está. Tem de ser um dos planos do contrato do beneficiário (FK composta (tenant_id, id_contrato, id_plano) -> plano_contrato). | cadastro | interno | plano_fora_do_contrato |
| `id_contrato` | bigint | contract, beneficiary | FK para contrato. Se ausente, Contract Intelligence fica indisponível. | cadastro comercial | interno | contrato_inexistente, plano_fora_do_contrato |
| `data_adesao` | date | cohorts, contract | Início de vínculo. Base da coorte 'novos na carteira' e da contagem de vidas. | movimentação cadastral | operacional | data_saida_antes_adesao |
| `data_saida` | date | cohorts | Fim de vínculo (null = ativo). Base da coorte 'saíram da carteira'. | movimentação cadastral | operacional | data_saida_antes_adesao |
| `motivo_saida` | string | não | cancelamento / óbito / demissão / portabilidade / inadimplência. RECOMENDADO — melhora a leitura das coortes. Não modelado no W2Health v1.2. | movimentação cadastral | operacional | — |
| `status` | string | sim | ativo / inativo (derivável de data_saida). | cadastro | operacional | — |

> `codigo` chega pseudonimizado. CPF/CNS, se vierem, ficam apenas em RAW cifrado e NUNCA são promovidos a Silver como chave. Faixa etária é derivada por competência na Gold (o W2Health v1.2 ainda fixa a faixa no seed — dívida documentada).

## plano

*Grão:* 1 linha por produto/plano de saúde da operadora  
*Chave primária:* `tenant_id, id_plano`  
*Chaves de negócio:* (tenant_id, codigo)  
*Carga:* UPSERT

| Campo | Tipo | Obrig. | Descrição | Origem típica | Sensibilidade | Regras DQ |
|---|---|---|---|---|---|---|
| `tenant_id` | string | sim | Cliente. | constante | interno | tenant_id_ausente |
| `id_plano` | bigint | sim | Surrogate key. | gerado | interno | — |
| `codigo` | string | sim | Código do produto na operadora / registro ANS. Único por tenant. | cadastro de produtos | operacional | — |
| `nome` | string | sim | Nome comercial do plano. | cadastro de produtos | operacional | — |
| `segmentacao` | string | não | ambulatorial / hospitalar / completo (segmentação assistencial ANS). | cadastro / registro ANS | operacional | — |
| `tem_coparticipacao` | bool | explicacao_coparticipacao | Se o plano cobra coparticipação. | cadastro de produtos / nota técnica | operacional | — |
| `percentual_coparticipacao` | numeric | explicacao_coparticipacao | Fração (0-1) cobrada do beneficiário sobre eventos elegíveis, quando não vier por evento. | cadastro de produtos | operacional | — |

## contrato

*Grão:* 1 linha por contrato (apólice coletiva, adesão ou vínculo individual) da operadora  
*Chave primária:* `tenant_id, id_contrato`  
*Chaves de negócio:* (tenant_id, codigo)  
*Carga:* UPSERT

| Campo | Tipo | Obrig. | Descrição | Origem típica | Sensibilidade | Regras DQ |
|---|---|---|---|---|---|---|
| `tenant_id` | string | sim | Cliente. | constante | interno | tenant_id_ausente |
| `id_contrato` | bigint | contract | Surrogate key. | gerado | interno | — |
| `codigo` | string | contract | Número/identificador do contrato na operadora. Único por tenant. | cadastro comercial / contratos | operacional | — |
| `nome` | string | não | Nome/razão social do contratante (mascarado se PF). | cadastro comercial | operacional | — |
| `tipo` | string | não | PF / PME / Empresarial (afeta regra de reajuste — fora do escopo v1.2). | cadastro comercial | operacional | — |
| `data_base` | date | reajuste | Mês de aniversário do contrato (data-base do reajuste). RECOMENDADO. Não usado na v1.2. | contratos | operacional | — |
| `meta_sinistralidade` | numeric | reajuste | Meta de sinistralidade do contrato (%). OPCIONAL — pré-requisito de reajuste, não usado na v1.2. | atuária / comercial | operacional | — |

> Contrato NÃO guarda plano: contrato × plano é N:N e os planos que o contrato oferece vêm em `plano_contrato`. O plano de cada vida fica no beneficiário. No modelo sintético do W2Health, contrato tem `vidas_alvo` (só orienta a geração). Num cliente real esse campo não existe. `data_base` / `meta_sinistralidade` são o começo do layout de reajuste — mantidos aqui como contrato, sem lógica associada na v1.2.

## plano_contrato

*Grão:* 1 linha por plano oferecido por um contrato (contrato × plano é N:N)  
*Chave primária:* `tenant_id, id_contrato, id_plano`  
*Chaves de negócio:* (tenant_id, contrato_codigo, plano_codigo)  
*Carga:* UPSERT

| Campo | Tipo | Obrig. | Descrição | Origem típica | Sensibilidade | Regras DQ |
|---|---|---|---|---|---|---|
| `tenant_id` | string | sim | Cliente. | constante | interno | tenant_id_ausente |
| `id_contrato` | bigint | contract | FK para contrato. Parte da chave. | cadastro comercial / contratos | interno | — |
| `id_plano` | bigint | contract | FK para plano. Parte da chave. | cadastro comercial / contratos | interno | — |

> Um contrato oferece N planos e um plano é vendido em N contratos. O beneficiário aponta para o par (contrato, plano) em que está — só pode estar num plano do próprio contrato. Par ausente numa carga não é removido (beneficiários podem apontar para ele). Preço por par (tabela de preço × grupo/faixa etária) é outra entidade, fora da v1.

## prestador

*Grão:* 1 linha por prestador (hospital, clínica, laboratório, PA, consultório)  
*Chave primária:* `tenant_id, id_prestador`  
*Chaves de negócio:* (tenant_id, codigo)  
*Carga:* UPSERT

| Campo | Tipo | Obrig. | Descrição | Origem típica | Sensibilidade | Regras DQ |
|---|---|---|---|---|---|---|
| `tenant_id` | string | sim | Cliente. | constante | interno | tenant_id_ausente |
| `id_prestador` | bigint | provider | Surrogate key. | gerado | interno | — |
| `codigo` | string | provider | Código do prestador na operadora (ou CNES mascarado). Único por tenant. | rede credenciada | operacional | — |
| `nome_ficticio` | string | não | Nome de exibição (pode ser mascarado). | rede credenciada | operacional | — |
| `tipo_prestador` | string | não | hospital / clinica / laboratorio / pronto_atendimento / consultorio. | rede credenciada | operacional | — |
| `id_regiao` | bigint | não | FK região do prestador. | rede credenciada | interno | — |
| `id_especialidade_principal` | bigint | provider | Especialidade principal — define o grupo de PARES para detecção de anomalia (z-score). | rede credenciada | interno | — |

## especialidade

*Grão:* 1 linha por especialidade médica  
*Chave primária:* `tenant_id, id_especialidade`  
*Chaves de negócio:* (tenant_id, codigo)  
*Carga:* FULL

| Campo | Tipo | Obrig. | Descrição | Origem típica | Sensibilidade | Regras DQ |
|---|---|---|---|---|---|---|
| `tenant_id` | string | sim | Cliente. | constante | interno | tenant_id_ausente |
| `id_especialidade` | bigint | loss_ratio | Surrogate key. | gerado | interno | — |
| `codigo` | string | loss_ratio | Código da especialidade (CBO ou tabela própria). Único por tenant. | cadastro / tabela CBHPM | operacional | — |
| `nome` | string | loss_ratio | Nome da especialidade. | cadastro | operacional | — |
| `grupo` | string | não | clinica / cirurgica / diagnostico / terapia (agrupador de análise). | classificação própria | operacional | — |

## procedimento

*Grão:* 1 linha por procedimento / item assistencial (código de tabela)  
*Chave primária:* `tenant_id, id_procedimento`  
*Chaves de negócio:* (tenant_id, codigo)  
*Carga:* FULL

| Campo | Tipo | Obrig. | Descrição | Origem típica | Sensibilidade | Regras DQ |
|---|---|---|---|---|---|---|
| `tenant_id` | string | sim | Cliente. | constante | interno | tenant_id_ausente |
| `id_procedimento` | bigint | loss_ratio | Surrogate key. | gerado | interno | — |
| `codigo` | string | loss_ratio | Código do procedimento (TUSS/CBHPM ou tabela própria). Único por tenant. | tabela de procedimentos | operacional | — |
| `descricao` | string | loss_ratio | Descrição do procedimento. | tabela de procedimentos | operacional | — |
| `id_especialidade` | bigint | loss_ratio | Especialidade típica do procedimento. | tabela / classificação | interno | — |
| `grupo_procedimento` | string | não | Agrupador de análise (ex.: 'Cirurgias oftalmológicas', 'Consultas', 'Internações clínicas'). | classificação própria | operacional | — |
| `perfil_utilizacao` | string | cohorts | pontual / recorrente / variavel. APOIA (nunca prova) a hipótese de 'episódio concluído' nas coortes. | derivado de grupo_procedimento | operacional | — |

## evento_assistencial

*Grão:* 1 linha por atendimento/procedimento/ocorrência de um beneficiário (item de conta)  
*Chave primária:* `tenant_id, id_evento`  
*Chaves de negócio:* (tenant_id, source_system, source_record_id)  
*Carga:* INCREMENTAL_ID

| Campo | Tipo | Obrig. | Descrição | Origem típica | Sensibilidade | Regras DQ |
|---|---|---|---|---|---|---|
| `tenant_id` | string | sim | Cliente. | constante do conector | interno | tenant_id_ausente |
| `id_evento` | bigint | sim | Surrogate key do W2Health. | gerado a partir da business key | interno | — |
| `source_record_id` | string | sim | Identificador do item na origem (nº da conta + item, nº da autorização, etc.). | contas médicas / TISS | operacional | evento_duplicado |
| `id_beneficiario` | bigint | sim | FK beneficiário. | conta / autorização | interno | benef_inexistente_em_evento |
| `id_contrato` | bigint | contract | FK contrato (normalmente derivada do beneficiário na data do evento). | derivado de beneficiário × vigência | interno | contrato_inexistente |
| `id_prestador` | bigint | provider | FK prestador executante. | conta | interno | prestador_inexistente |
| `id_procedimento` | bigint | loss_ratio | FK procedimento. | conta (código TUSS/CBHPM) | interno | — |
| `id_especialidade` | bigint | loss_ratio | FK especialidade do atendimento. | conta / derivado do procedimento | interno | — |
| `id_diagnostico` | bigint | não | FK diagnóstico (CID). Dado de saúde sensível. | conta / autorização / laudo | **dado de saúde sensível** | — |
| `data_evento` | date | sim | Data do atendimento. | conta | operacional | evento_sem_data |
| `competencia` | date | sim | Mês de referência (1º dia). Pode ser competência de pagamento OU de atendimento — DECISÃO por cliente, documentar. | derivado ou campo da conta | interno | competencia_invalida |
| `tipo_atendimento` | string | loss_ratio | consulta / exame / terapia / pronto_socorro / internacao / cirurgia / opme. | conta / regra de negócio | operacional | — |
| `quantidade` | int | não | Quantidade do item (sessões, diárias...). | conta | operacional | — |
| `valor_apresentado` | numeric | sim | Despesa BRUTA apresentada pelo prestador. | conta médica | operacional | valor_negativo |
| `valor_glosado` | numeric | explicacao_glosa | Parcela glosada (não paga ao prestador). | processamento de contas / auditoria | operacional | valor_negativo, glosa_maior_que_apresentado |
| `valor_coparticipacao` | numeric | explicacao_coparticipacao | Parcela de valor_pago cobrada do beneficiário. 0 quando não há coparticipação. | faturamento / regra do plano | operacional | coparticipacao_acima_permitido |
| `valor_pago` | numeric | sim | valor_apresentado - valor_glosado (pago ao prestador). | conta | operacional | valor_negativo |
| `despesa_liquida` | numeric | não | DERIVADA: valor_apresentado - valor_glosado - valor_coparticipacao. Calculada na Gold; não precisa vir da origem. | derivado | operacional | despesa_liquida_inconsistente |

> Entidade mais volumosa. Glosas que chegam DEPOIS (reprocessamento) e reversões de glosa são tratadas por UPSERT na business key + reprocesso do lote — ver docs/INTEGRATION_GUIDE.md § Histórico. `competencia` de atendimento vs. de pagamento é uma decisão de onboarding que muda o resultado e precisa ficar registrada por cliente.

## receita

*Grão:* 1 linha por competência × plano — contraprestação (receita) reconhecida  
*Chave primária:* `tenant_id, competencia, id_plano`  
*Chaves de negócio:* (tenant_id, competencia, id_plano)  
*Carga:* UPSERT

| Campo | Tipo | Obrig. | Descrição | Origem típica | Sensibilidade | Regras DQ |
|---|---|---|---|---|---|---|
| `tenant_id` | string | sim | Cliente. | constante | interno | tenant_id_ausente |
| `competencia` | date | sinistralidade, loss_ratio | Mês de competência da receita (1º dia). | faturamento / contábil | interno | competencia_invalida |
| `id_plano` | bigint | loss_ratio | FK plano. | faturamento | interno | — |
| `quantidade_beneficiarios` | int | sinistralidade | Vidas expostas no mês (base do PMPM / exposição). | faturamento / cadastro | operacional | valor_negativo |
| `receita_contraprestacao` | numeric | sinistralidade, loss_ratio | Contraprestação (prêmio) reconhecida no mês para o plano. | faturamento / contábil | operacional | valor_negativo, receita_duplicada |

> É o grão MÍNIMO para calcular sinistralidade. Vem do sistema de faturamento/contábil — quase sempre uma fonte distinta do assistencial/TISS.

## receita_contrato

*Grão:* 1 linha por competência × contrato — contraprestação no grão do contrato  
*Chave primária:* `tenant_id, competencia, id_contrato`  
*Chaves de negócio:* (tenant_id, competencia, id_contrato)  
*Carga:* UPSERT
  
> ⚠️ **Layout preparado — não populado nem lido na v1.2.**

| Campo | Tipo | Obrig. | Descrição | Origem típica | Sensibilidade | Regras DQ |
|---|---|---|---|---|---|---|
| `tenant_id` | string | contract_sinistralidade, reajuste | Cliente. | constante | interno | tenant_id_ausente |
| `competencia` | date | contract_sinistralidade | Mês (1º dia). | faturamento | interno | competencia_invalida |
| `id_contrato` | bigint | contract_sinistralidade | FK contrato. | faturamento por contrato | interno | — |
| `quantidade_beneficiarios` | int | contract_sinistralidade | Vidas do contrato no mês. | faturamento / cadastro | operacional | valor_negativo |
| `receita_contraprestacao` | numeric | contract_sinistralidade | Contraprestação do contrato no mês. Fonte: contraprestação faturada do contrato OU soma das mensalidades individuais OU rateio (marcar `metodologia`). | faturamento por contrato | operacional | valor_negativo |
| `reajuste_aplicado_no_periodo` | numeric | reajuste | Fração de reajuste aplicada na data-base que cai neste período (histórico). | área de contratos | operacional | — |
| `metodologia` | string | não | faturado_contrato / soma_mensalidades / rateio_vidas / rateio_faixa. OBRIGATÓRIO quando não for receita faturada real. | onboarding | interno | — |

> **v1.2 não popula nem lê esta entidade.** Existe para que os conectores futuros já tenham um alvo canônico e para o discovery de receita granular / reajuste. A tabela `receitas_contrato` no Postgres é o espelho ORM (vazia).

## glosa

*Grão:* atributo do evento_assistencial (valor_glosado) + eventual detalhe de motivo  
*Chave primária:* `tenant_id, id_evento`  
*Chaves de negócio:* (tenant_id, source_system, source_record_id)  
*Carga:* acompanha evento_assistencial (UPSERT na mesma business key)

| Campo | Tipo | Obrig. | Descrição | Origem típica | Sensibilidade | Regras DQ |
|---|---|---|---|---|---|---|
| `valor_glosado` | numeric | explicacao_glosa | Parcela do valor_apresentado glosada pela operadora. 0 se não houver glosa. | processamento de contas / auditoria médica | operacional | valor_negativo, glosa_maior_que_apresentado |
| `motivo_glosa` | string | não | Código/descrição do motivo (RECOMENDADO — habilita 'explicação por motivo de glosa', fora do escopo v1.2). | auditoria | operacional | — |

> A glosa entra no W2Health como `eventos_assistenciais.valor_glosado`. A decomposição bruta → glosas → líquida acontece na Gold (agg_*). Reversão de glosa: reprocessar o evento com o novo `valor_glosado` (a business key não muda).

## coparticipacao

*Grão:* atributo do evento_assistencial (valor_coparticipacao)  
*Chave primária:* `tenant_id, id_evento`  
*Chaves de negócio:* (tenant_id, source_system, source_record_id)  
*Carga:* acompanha evento_assistencial

| Campo | Tipo | Obrig. | Descrição | Origem típica | Sensibilidade | Regras DQ |
|---|---|---|---|---|---|---|
| `valor_coparticipacao` | numeric | explicacao_coparticipacao | Parcela de valor_pago cobrada do beneficiário. Reduz a despesa LÍQUIDA da operadora. Se a origem não traz por evento, derivar: valor_pago * plano.percentual_coparticipacao para tipos elegíveis (consulta/exame/terapia/pronto_socorro), 0 caso contrário. | faturamento; ou derivada da regra do plano | operacional | coparticipacao_acima_permitido |
| `origem_dado` | string | não | faturada / derivada — RECOMENDADO para não confundir número real com estimado. | conector | interno | — |

> Regra de elegibilidade por tipo de atendimento e percentual são do cadastro de produtos (`plano.tem_coparticipacao` / `percentual_coparticipacao`). Coparticipação NUNCA incide sobre internação/cirurgia/OPME no modelo sintético — confirmar a regra real por cliente.
