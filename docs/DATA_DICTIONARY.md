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
| `tipo_prestador` | string | não | hospital / clinica / laboratorio / pronto_atendimento / consultorio / profissional (pessoa física). Base da comparação com pares do mesmo tipo (Evolução V1). | rede credenciada | operacional | — |
| `id_regiao` | bigint | não | FK região do prestador. | rede credenciada | interno | — |
| `id_especialidade_principal` | bigint | provider | Especialidade principal — define o grupo de PARES para detecção de anomalia (z-score). | rede credenciada | interno | — |

## vinculo_beneficiario

*Grão:* 1 linha por período de vínculo de um beneficiário a um contrato/plano  
*Chave primária:* `tenant_id, id_vinculo`  
*Chaves de negócio:* (tenant_id, beneficiario_codigo, vigencia_inicio)  
*Carga:* UPSERT

| Campo | Tipo | Obrig. | Descrição | Origem típica | Sensibilidade | Regras DQ |
|---|---|---|---|---|---|---|
| `tenant_id` | string | sim | Cliente. | constante | interno | tenant_id_ausente |
| `id_vinculo` | bigint | contract | Surrogate key. | gerado | interno | — |
| `id_beneficiario` | bigint | contract | FK beneficiário. | cadastro de beneficiários (histórico de movimentação) | interno | fk_inexistente_beneficiario_codigo |
| `id_contrato` | bigint | contract | FK contrato no período. | histórico de movimentação cadastral | interno | fk_inexistente_contrato_codigo |
| `id_plano` | bigint | contract | FK plano no período — tem de ser um plano do contrato (plano_contrato). | histórico de movimentação cadastral | interno | fk_inexistente_plano_codigo, plano_fora_do_contrato |
| `vigencia_inicio` | date | contract | Início do vínculo (inclusive). | movimentação cadastral | operacional | vigencia_sobreposta |
| `vigencia_fim` | date | não | Fim do vínculo (EXCLUSIVO). Vazio = vigente. | movimentação cadastral | operacional | vigencia_invertida, vigencia_sobreposta |
| `situacao` | string | não | ativo / suspenso / cancelado. Vazio = ativo. Exposição (vidas) conta só ativo — 'suspenso' como exposição é pendência de negócio (Consolidação V1). | movimentação cadastral | operacional | — |

> OPCIONAL (Evolução V1). Quando informado, o Silver grava em CADA evento o contrato/plano vigente na data do atendimento (evento_assistencial.id_contrato/id_plano). Beneficiário COM histórico e sem vigência na data → evento NÃO ATRIBUÍDO. Sem histórico, o cadastro vale como vínculo único de data_adesao a data_saida (exclusiva). Vidas por contrato também pelo vínculo vigente no 1º dia da competência (Consolidação V1). Sobreposição verificada no lote e contra o já carregado.

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

## preco_contratado

*Grão:* 1 linha por vigência de preço unitário contratado de um procedimento num prestador  
*Chave primária:* `tenant_id, id_preco`  
*Chaves de negócio:* (tenant_id, prestador_codigo, procedimento_codigo, vigencia_inicio)  
*Carga:* UPSERT

| Campo | Tipo | Obrig. | Descrição | Origem típica | Sensibilidade | Regras DQ |
|---|---|---|---|---|---|---|
| `tenant_id` | string | sim | Cliente. | constante | interno | tenant_id_ausente |
| `id_preco` | bigint | explicacao_preco | Surrogate key. | gerado | interno | — |
| `id_prestador` | bigint | explicacao_preco | FK prestador (executante) a quem o preço se aplica. | contrato de credenciamento / tabela de remuneração | interno | fk_inexistente_prestador_codigo |
| `id_procedimento` | bigint | explicacao_preco | FK procedimento. | tabela de remuneração (TUSS/CBHPM/própria) | interno | fk_inexistente_procedimento_codigo |
| `vigencia_inicio` | date | explicacao_preco | Início da vigência (inclusive). Uma nova vigência = um reajuste/renegociação. | aditivo contratual / tabela vigente | operacional | vigencia_sobreposta |
| `vigencia_fim` | date | não | Fim da vigência (EXCLUSIVO). Vazio = vigente. | aditivo contratual | operacional | vigencia_invertida, vigencia_sobreposta |
| `valor_unitario` | numeric | explicacao_preco | Valor contratado por UNIDADE do item (mesma unidade de evento.quantidade). | tabela de remuneração | operacional | preco_nao_positivo |
| `tipo_remuneracao` | string | não | tabela / pacote / diaria / honorario / outro — informativo, não entra no cálculo. | contrato de credenciamento | operacional | — |

> OPCIONAL (Evolução V1 — investigação de custos). É a ÚNICA evidência aceita pelo motor para afirmar "reajuste de preço": sem esta entidade, ou sem preço vigente nos DOIS períodos comparados para um par prestador × procedimento, a variação de valor por unidade aparece como "custo unitário observado (sem evidência de tabela)". Não é um módulo de negociação: guarda só o valor vigente e o histórico de vigências. Vigências da mesma chave não podem se sobrepor no arquivo (vigencia_sobreposta); a sobreposição com cargas anteriores não é verificada nesta versão.

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
| `id_contrato` | bigint | contract | FK contrato VIGENTE NA DATA DO EVENTO — congelado na carga (Evolução V1). Uma troca posterior de contrato não reclassifica a despesa. | derivado de beneficiário × vigência | interno | contrato_inexistente |
| `id_plano` | bigint | contract | FK plano vigente na data do evento — mesma regra de id_contrato (Evolução V1). | derivado de beneficiário × vigência | interno | — |
| `id_prestador` | bigint | provider | FK prestador executante. | conta | interno | prestador_inexistente |
| `id_estabelecimento` | bigint | não | FK prestador onde o atendimento ocorreu (hospital, clínica...). Opcional — id_prestador segue sendo o EXECUTANTE (Evolução V1). | conta / guia (local de atendimento, CNES) | interno | fk_inexistente_estabelecimento_codigo |
| `id_prestador_solicitante` | bigint | não | FK prestador/profissional SOLICITANTE. Opcional (Evolução V1). | guia (solicitante) | interno | fk_inexistente_prestador_solicitante_codigo |
| `id_procedimento` | bigint | loss_ratio | FK procedimento. | conta (código TUSS/CBHPM) | interno | — |
| `id_especialidade` | bigint | loss_ratio | FK especialidade do atendimento. | conta / derivado do procedimento | interno | — |
| `id_diagnostico` | bigint | não | FK diagnóstico (CID). Dado de saúde sensível. | conta / autorização / laudo | **dado de saúde sensível** | — |
| `data_evento` | date | sim | Data do atendimento. | conta | operacional | evento_sem_data |
| `competencia` | date | sim | Mês de referência (1º dia). Pode ser competência de pagamento OU de atendimento — DECISÃO por cliente, documentar. | derivado ou campo da conta | interno | competencia_invalida |
| `competencia_apresentacao` | date | não | Competência em que a conta foi APRESENTADA pelo prestador (Consolidação V1). | processamento de contas | interno | — |
| `competencia_pagamento` | date | não | Competência de PAGAMENTO/contabilização (Consolidação V1). A competência analítica é `competencia` (decisão de onboarding no mapping). | contas a pagar / contabilidade | interno | — |
| `numero_guia` | string | não | Guia de origem. Guia autorizada NÃO é conta apresentada nem paga. | autorização / TISS | operacional | — |
| `numero_conta` | string | não | Conta médica (cabeçalho) à qual o item pertence. O grão do evento é o ITEM. | contas médicas | operacional | — |
| `unidade_medida` | string | não | Unidade de `quantidade` (sessão, diária, unidade, ml...). | conta | operacional | — |
| `natureza_lancamento` | string | não | normal / estorno / ajuste / cancelamento. Vazio = normal. Estorno/ajuste/cancelamento admitem valor negativo. | processamento de contas | operacional | valor_negativo |
| `valor_pago_origem` | string | não | DERIVADO: informado (valor_pago veio da origem) / derivado (apresentado − glosado). valor_pago = valor APROVADO após glosa, não pago em caixa. | derivado | interno | — |
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

## empresa

*Grão:* 1 linha por empresa (pessoa jurídica que agrupa contratos empresariais/coletivos)  
*Chave primária:* `tenant_id, id_empresa`  
*Chaves de negócio:* (tenant_id, codigo)  
*Carga:* UPSERT

| Campo | Tipo | Obrig. | Descrição | Origem típica | Sensibilidade | Regras DQ |
|---|---|---|---|---|---|---|
| `tenant_id` | string | sim | Cliente. | constante | interno | tenant_id_ausente |
| `id_empresa` | bigint | contract | Surrogate key. | gerado | interno | — |
| `codigo` | string | contract | Código da empresa na operadora. Único por tenant. NÃO é CNPJ. | cadastro comercial | operacional | — |
| `nome` | string | não | Nome/razão social de exibição (pode ser mascarado). | cadastro comercial | operacional | — |
| `tipo` | string | não | empresarial / adesao / associacao / entidade_classe / outro. | cadastro comercial | operacional | — |
| `status` | string | não | ativa / inativa. | cadastro comercial | operacional | — |

> OPCIONAL (Consolidação do canônico V1). Empresa NÃO é um nível fixo entre plano e contrato: agrupa contratos por `empresa_contrato` (histórico). Identificador fiscal não é exigido para o funcionamento analítico; se uma integração futura precisar, segue a política de proteção de dados (docs/LGPD_TECHNICAL_CONTROLS.md).

## empresa_contrato

*Grão:* 1 linha por período de associação de uma empresa a um contrato num papel  
*Chave primária:* `tenant_id, id_empresa_contrato`  
*Chaves de negócio:* (tenant_id, contrato_codigo, papel, vigencia_inicio)  
*Carga:* UPSERT

| Campo | Tipo | Obrig. | Descrição | Origem típica | Sensibilidade | Regras DQ |
|---|---|---|---|---|---|---|
| `tenant_id` | string | sim | Cliente. | constante | interno | tenant_id_ausente |
| `id_empresa_contrato` | bigint | contract | Surrogate key. | gerado | interno | — |
| `id_empresa` | bigint | contract | FK empresa. | cadastro comercial | interno | fk_inexistente_empresa_codigo |
| `id_contrato` | bigint | contract | FK contrato. | cadastro comercial | interno | fk_inexistente_contrato_codigo |
| `papel` | string | contract | contratante / estipulante / administradora / subestipulante. Vazio = contratante. Só 'contratante' agrupa receita/despesa. | cadastro comercial | operacional | — |
| `vigencia_inicio` | date | contract | Início (inclusive). | cadastro comercial | operacional | vigencia_sobreposta |
| `vigencia_fim` | date | não | Fim (EXCLUSIVO). Vazio = vigente. | cadastro comercial | operacional | vigencia_invertida, vigencia_sobreposta |

> OPCIONAL. Regra: no máximo UMA empresa por contrato e papel em cada data — vigências da mesma (contrato, papel) não se sobrepõem, verificado no lote E contra o já carregado. Mais de uma contratante simultânea é pendência de negócio (rejeitada até haver regra), o que garante que nenhum contrato conte em duas empresas.

## fatura

*Grão:* 1 linha por documento de cobrança (fatura/boleto/nota) emitido pela operadora  
*Chave primária:* `tenant_id, id_fatura`  
*Chaves de negócio:* (tenant_id, numero_documento)  
*Carga:* UPSERT

| Campo | Tipo | Obrig. | Descrição | Origem típica | Sensibilidade | Regras DQ |
|---|---|---|---|---|---|---|
| `tenant_id` | string | sim | Cliente. | constante | interno | tenant_id_ausente |
| `id_fatura` | bigint | financial | Surrogate key. | gerado | interno | — |
| `numero_documento` | string | financial | Número do documento na origem. Único por tenant. | faturamento | operacional | — |
| `unidade_cobranca` | string | financial | contrato / empresa — quem é cobrado. Exige o código correspondente (e só ele). | faturamento | operacional | unidade_cobranca_inconsistente |
| `id_contrato` | bigint | não | FK contrato (unidade = contrato). | faturamento | interno | fk_inexistente_contrato_codigo |
| `id_empresa` | bigint | não | FK empresa (unidade = empresa). | faturamento | interno | fk_inexistente_empresa_codigo |
| `competencia_faturamento` | date | financial | Competência de FATURAMENTO (1º dia do mês). Não é a de reconhecimento nem a de recebimento. | faturamento | interno | — |
| `data_emissao` | date | não | Data de emissão. | faturamento | operacional | — |
| `data_vencimento` | date | não | Vencimento. | faturamento | operacional | — |
| `valor_bruto` | numeric | financial | Valor antes de descontos e acréscimos. | faturamento | operacional | valor_negativo |
| `valor_desconto` | numeric | não | Descontos (≥ 0). | faturamento | operacional | valor_negativo |
| `valor_acrescimo` | numeric | não | Juros, multas e acréscimos (≥ 0). | faturamento | operacional | valor_negativo |
| `valor_total` | numeric | financial | = bruto − desconto + acréscimo (derivado se ausente; se informado, tem de bater). | faturamento | operacional | fatura_total_inconsistente |
| `status` | string | não | emitida / cancelada / substituida. Só 'emitida' entra no faturado. | faturamento | operacional | — |

> OPCIONAL. Documento de cobrança — FATURADO ≠ RECONHECIDO ≠ RECEBIDO. Nunca usado como receita da sinistralidade (essa é `receita_reconhecida`). Itens em `fatura_item`; recebimentos em `recebimento`.

## fatura_item

*Grão:* 1 linha por item de um documento de cobrança  
*Chave primária:* `tenant_id, id_fatura_item`  
*Chaves de negócio:* (tenant_id, numero_documento, numero_item)  
*Carga:* SNAPSHOT_DOCUMENTO

| Campo | Tipo | Obrig. | Descrição | Origem típica | Sensibilidade | Regras DQ |
|---|---|---|---|---|---|---|
| `tenant_id` | string | sim | Cliente. | constante | interno | tenant_id_ausente |
| `id_fatura_item` | bigint | financial | Surrogate key. | gerado | interno | — |
| `id_fatura` | bigint | financial | FK do documento. | faturamento | interno | fk_inexistente_numero_documento |
| `numero_item` | string | financial | Número do item no documento. | faturamento | operacional | — |
| `natureza` | string | não | mensalidade / coparticipacao / taxa / ajuste / retroativo / desconto / acrescimo / outro. Vazio = mensalidade. | faturamento | operacional | — |
| `id_contrato` | bigint | não | Contrato do item, quando a origem detalha. | faturamento | interno | fk_inexistente_contrato_codigo |
| `id_plano` | bigint | não | Plano do item, quando detalhado (par do contrato). | faturamento | interno | plano_fora_do_contrato |
| `competencia_referencia` | date | não | Competência de cobertura do item. | faturamento | interno | — |
| `quantidade_beneficiarios` | int | não | Vidas cobradas no item. | faturamento | operacional | — |
| `valor` | numeric | financial | Valor do item. Negativo só em desconto/ajuste/retroativo. | faturamento | operacional | valor_negativo |

> OPCIONAL. Itens de um documento são substituídos a cada carga do documento (snapshot por documento). Σ itens = valor_total do documento (reconciliação `fatura_itens_vs_total`). Em documento de EMPRESA, item com contrato precisa ser de contrato associado à empresa na competência (`empresa_vs_contratos_relacionados`).

## recebimento

*Grão:* 1 linha por baixa/pagamento recebido de um documento  
*Chave primária:* `tenant_id, id_recebimento`  
*Chaves de negócio:* (tenant_id, numero_recebimento)  
*Carga:* UPSERT

| Campo | Tipo | Obrig. | Descrição | Origem típica | Sensibilidade | Regras DQ |
|---|---|---|---|---|---|---|
| `tenant_id` | string | sim | Cliente. | constante | interno | tenant_id_ausente |
| `id_recebimento` | bigint | financial | Surrogate key. | gerado | interno | — |
| `numero_recebimento` | string | financial | Identificador da baixa na origem. | contas a receber | operacional | — |
| `id_fatura` | bigint | não | Documento quitado (quando identificado). | contas a receber | interno | fk_inexistente_numero_documento |
| `data_recebimento` | date | financial | Data do crédito. | contas a receber | operacional | — |
| `competencia_recebimento` | date | financial | Competência de RECEBIMENTO (mês de data_recebimento se ausente). | contas a receber | interno | — |
| `valor_recebido` | numeric | financial | Valor recebido (≥ 0). | contas a receber | operacional | valor_negativo |

> OPCIONAL. Caixa — informativo frente ao faturado e ao reconhecido; não entra na sinistralidade.

## receita_reconhecida

*Grão:* 1 linha por lançamento de contraprestação reconhecida, no nível de atribuição que a origem permite  
*Chave primária:* `tenant_id, id_receita_reconhecida`  
*Chaves de negócio:* (tenant_id, competencia, source_record_id)  
*Carga:* COMPETENCIA_SNAPSHOT

| Campo | Tipo | Obrig. | Descrição | Origem típica | Sensibilidade | Regras DQ |
|---|---|---|---|---|---|---|
| `tenant_id` | string | sim | Cliente. | constante | interno | tenant_id_ausente |
| `id_receita_reconhecida` | bigint | contract_loss_ratio | Surrogate key. | gerado | interno | — |
| `source_record_id` | string | contract_loss_ratio | Identificador do lançamento na origem (chave com a competência). | contabilidade / faturamento | operacional | — |
| `competencia` | date | loss_ratio, contract_loss_ratio | Competência de RECONHECIMENTO (cobertura) — a usada na sinistralidade. | contabilidade | interno | competencia_invalida |
| `nivel_atribuicao` | string | contract_loss_ratio | DERIVADO no Silver: contrato_plano / contrato / empresa / nao_atribuida. Nunca informado pela origem. | derivado | interno | — |
| `id_contrato` | bigint | contract_loss_ratio | Contrato (detalhado). Código sem cadastro → não atribuída (WARNING), nunca rejeitada. | faturamento por contrato | interno | receita_sem_cadastro |
| `id_plano` | bigint | não | Plano (par do contrato). | faturamento | interno | plano_fora_do_contrato, receita_sem_cadastro |
| `id_empresa` | bigint | não | Empresa, SÓ para cobrança consolidada SEM detalhamento por contrato. | faturamento por empresa | interno | receita_nivel_ambiguo, receita_sem_cadastro |
| `natureza` | string | não | contraprestacao / ajuste / outro. Negativo só em ajuste. | contabilidade | operacional | valor_negativo |
| `numero_documento` | string | não | Documento de cobrança de origem. O mesmo documento não pode ter receita consolidada E detalhada. | faturamento | operacional | receita_documento_duplicado |
| `quantidade_beneficiarios` | int | não | Vidas da contraprestação. | faturamento | operacional | — |
| `receita_contraprestacao` | numeric | loss_ratio | Valor reconhecido. | contabilidade | operacional | valor_negativo |
| `estimada` | bool | não | Reservado para alocação gerencial FUTURA (metodologia, versão, valor original). Sempre false nas cargas; o motor ignora linhas estimadas. | - | interno | — |

> Consolidação do canônico V1 — substitui o layout `receita_contrato` (v1.2, nunca populado, que admitia rateio como "metodologia"). RATEIO É PROIBIDO: receita de empresa sem detalhamento fica no nível `empresa`; código sem cadastro, em `nao_atribuida` — ambas contam na receita da operadora e NUNCA na sinistralidade de contrato. Fonte da receita da operadora por competência: reconhecida, se existir; senão a legada (`receita`, competência × plano) — nunca as duas somadas; divergência entre elas reprova a carga e bloqueia a sinistralidade da competência. Ver docs/REGRAS_RECEITA_E_FATURAMENTO.md.

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
