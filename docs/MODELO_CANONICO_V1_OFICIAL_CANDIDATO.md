# Modelo Canônico V1 — Layout Oficial **Candidato**

> **Status: CANDIDATO — NÃO HOMOLOGADO.** Ainda há divergências entre YAML, banco, pipeline e
> cálculos (seção 5). O layout só passa a "oficial" quando a seção 5 estiver vazia.
> Gerado em 23/10/2026 (Consolidação do canônico V1). As tabelas de campos da seção 4 são
> geradas do **contrato operacional** (`backend/app/data_platform/canonical.py`, o que a carga
> aceita hoje) e dos contratos YAML (`data_platform/contracts/`, sensibilidade e
> capacidades). Dicionário completo: [DATA_DICTIONARY.md](DATA_DICTIONARY.md).
> Regras de negócio: [REGRAS_RECEITA_E_FATURAMENTO.md](REGRAS_RECEITA_E_FATURAMENTO.md).
> Exemplos fictícios de arquivos: `data_platform/examples/generic_operator/` (base) e
> `data_platform/examples/layout_v1/` (entidades opcionais).

O modelo é **independente de ERP**: nenhum nome de tabela/coluna de MV, Tasy, Benner ou
outro sistema aparece aqui; cada origem tem um mapping (`data_platform/mappings/`) para este
contrato. O extrator entrega os dados de origem; o mapping os traduz.

## 1. Entidades e relacionamentos

```
especialidade ─┬─ procedimento ─┐
               └─ prestador ────┼─ preco_contratado (vigência)
plano ──┬─ plano_contrato ─ contrato ─┬─ empresa_contrato (papel, vigência) ─ empresa
        │                             ├─ beneficiario ─ vinculo_beneficiario (vigência, situação)
        │                             ├─ fatura (unidade = contrato | empresa) ─ fatura_item ─ recebimento
        │                             └─ receita_reconhecida (nível de atribuição)
        └─ receita (legada: competência × plano)
evento_assistencial (item) → beneficiário, prestador (executante/estabelecimento/solicitante),
                             procedimento; contrato/plano = vínculo vigente na data (congelado)
```

| Entidade | Obrigatória na carga? | Capacidade principal |
|---|---|---|
| especialidade, plano, contrato, plano_contrato, prestador, procedimento, beneficiario, evento_assistencial | **sim** | todas |
| receita (legada por plano) **ou** receita_reconhecida | **uma das duas** para sinistralidade | loss_ratio |
| vinculo_beneficiario | não | histórico de contrato/plano; vidas históricas |
| preco_contratado | não | reajuste com evidência |
| empresa, empresa_contrato | não | agrupamento; sinistralidade de empresa |
| fatura, fatura_item, recebimento | não | faturado × recebido (transparência) |
| receita_reconhecida | não | sinistralidade de contrato/empresa |

## 2. Regras de competência

| Fato | Competência usada nos indicadores | Preservadas |
|---|---|---|
| evento_assistencial | `competencia` — base definida no mapping (padrão: atendimento) | `data_evento`, `competencia_apresentacao`, `competencia_pagamento` |
| receita_reconhecida / receita | reconhecimento | — |
| fatura | faturamento | `data_emissao`, `data_vencimento` |
| recebimento | recebimento | `data_recebimento` |

## 3. Regras de vínculo e de carga

- Vigências `[início, fim)` sem sobreposição por chave (vínculo: beneficiário; preço:
  prestador × procedimento; empresa × contrato: contrato × papel) — no lote **e** contra o já
  carregado (ERROR `vigencia_sobreposta`).
- Evento: contrato/plano pela vigência na data do atendimento; sem vigência → não atribuído.
- Receita reconhecida: nível derivado (seção 3 de REGRAS); sem cadastro → preservada;
  documento em dois níveis → ERROR.
- Gate de DQ: linha com ERROR é rejeitada; acima de `max_rejected_ratio` (padrão 0) a carga
  inteira é bloqueada. Reconciliação (carga + financeira) em FAIL → nada é publicado.

## 4. Entidades — campos

### `especialidade`

- **Grão:** 1 linha por especialidade médica
- **Chave de negócio (carga):** `(codigo)` por tenant
- **Política de carga:** FULL

| Campo | Tipo | Obrig. | Vocabulário / regra | Sensibilidade | Capacidades |
|---|---|---|---|---|---|
| `codigo` | string | sim |  | operacional | loss_ratio |
| `nome` | string | sim |  | operacional | loss_ratio |
| `grupo` | string | não |  | operacional |  |

### `plano`

- **Grão:** 1 linha por produto/plano de saúde da operadora
- **Chave de negócio (carga):** `(codigo)` por tenant
- **Política de carga:** UPSERT

| Campo | Tipo | Obrig. | Vocabulário / regra | Sensibilidade | Capacidades |
|---|---|---|---|---|---|
| `codigo` | string | sim |  | operacional | ALL |
| `nome` | string | sim |  | operacional | ALL |
| `segmentacao` | string | não |  | operacional |  |
| `tem_coparticipacao` | bool | não |  | operacional | explicacao_coparticipacao |
| `percentual_coparticipacao` | decimal | não | fração 0–1 aplicada sobre valor_pago (informativo nesta fase) | operacional | explicacao_coparticipacao |

### `contrato`

- **Grão:** 1 linha por contrato (apólice coletiva, adesão ou vínculo individual) da operadora
- **Chave de negócio (carga):** `(codigo)` por tenant
- **Política de carga:** UPSERT

| Campo | Tipo | Obrig. | Vocabulário / regra | Sensibilidade | Capacidades |
|---|---|---|---|---|---|
| `codigo` | string | sim |  | operacional | contract |
| `nome` | string | não |  | operacional |  |
| `tipo` | string | não |  | operacional |  |

### `plano_contrato`

- **Grão:** 1 linha por plano oferecido por um contrato (contrato × plano é N:N)
- **Chave de negócio (carga):** `(contrato_codigo, plano_codigo)` por tenant
- **Política de carga:** UPSERT

| Campo | Tipo | Obrig. | Vocabulário / regra | Sensibilidade | Capacidades |
|---|---|---|---|---|---|
| `contrato_codigo` | string | sim |  | interno | contract |
| `plano_codigo` | string | sim |  | interno | contract |

### `empresa`

- **Grão:** 1 linha por empresa (pessoa jurídica que agrupa contratos empresariais/coletivos)
- **Chave de negócio (carga):** `(codigo)` por tenant
- **Política de carga:** UPSERT

| Campo | Tipo | Obrig. | Vocabulário / regra | Sensibilidade | Capacidades |
|---|---|---|---|---|---|
| `codigo` | string | sim |  | operacional | contract |
| `nome` | string | sim |  | operacional |  |
| `tipo` | string | não | empresarial · adesao · associacao · entidade_classe · outro | operacional |  |
| `status` | string | não | ativa · inativa | operacional |  |

### `empresa_contrato`

- **Grão:** 1 linha por período de associação de uma empresa a um contrato num papel
- **Chave de negócio (carga):** `(contrato_codigo, papel, vigencia_inicio)` por tenant
- **Política de carga:** UPSERT

| Campo | Tipo | Obrig. | Vocabulário / regra | Sensibilidade | Capacidades |
|---|---|---|---|---|---|
| `empresa_codigo` | string | sim |  | interno | contract |
| `contrato_codigo` | string | sim |  | interno | contract |
| `papel` | string | não | contratante · estipulante · administradora · subestipulante | operacional | contract |
| `vigencia_inicio` | date | sim |  | operacional | contract |
| `vigencia_fim` | date | não | exclusiva; vazio = vigente | operacional |  |

### `prestador`

- **Grão:** 1 linha por prestador (hospital, clínica, laboratório, PA, consultório)
- **Chave de negócio (carga):** `(codigo)` por tenant
- **Política de carga:** UPSERT

| Campo | Tipo | Obrig. | Vocabulário / regra | Sensibilidade | Capacidades |
|---|---|---|---|---|---|
| `codigo` | string | sim |  | operacional | provider |
| `nome` | string | sim |  | operacional |  |
| `tipo_prestador` | string | não |  | operacional |  |
| `cidade` | string | sim |  | operacional |  |
| `uf` | string | sim |  | operacional |  |
| `especialidade_codigo` | string | sim | especialidade principal — grupo de pares da detecção de anomalia | operacional |  |

### `procedimento`

- **Grão:** 1 linha por procedimento / item assistencial (código de tabela)
- **Chave de negócio (carga):** `(codigo)` por tenant
- **Política de carga:** FULL

| Campo | Tipo | Obrig. | Vocabulário / regra | Sensibilidade | Capacidades |
|---|---|---|---|---|---|
| `codigo` | string | sim |  | operacional | loss_ratio |
| `descricao` | string | sim |  | operacional | loss_ratio |
| `especialidade_codigo` | string | sim |  | interno | loss_ratio |
| `grupo_procedimento` | string | não |  | operacional |  |
| `perfil_utilizacao` | string | não | pontual · recorrente · variavel | operacional | cohorts |

### `beneficiario`

- **Grão:** 1 linha por beneficiário (vida) da operadora — estado atual
- **Chave de negócio (carga):** `(codigo)` por tenant
- **Política de carga:** UPSERT

| Campo | Tipo | Obrig. | Vocabulário / regra | Sensibilidade | Capacidades |
|---|---|---|---|---|---|
| `codigo` | string | sim | identificador pseudonimizado | operacional | ALL |
| `data_nascimento` | date | sim |  | pessoal | beneficiary, contract |
| `sexo` | string | não | M · F | pessoal |  |
| `plano_codigo` | string | sim | plano do beneficiário — tem de ser um dos planos do contrato | interno | loss_ratio, contract |
| `contrato_codigo` | string | sim |  | interno | contract, beneficiary |
| `cidade` | string | sim |  | operacional |  |
| `uf` | string | sim |  | operacional |  |
| `data_adesao` | date | sim |  | operacional | cohorts, contract |
| `data_saida` | date | não |  | operacional | cohorts |

### `vinculo_beneficiario`

- **Grão:** 1 linha por período de vínculo de um beneficiário a um contrato/plano
- **Chave de negócio (carga):** `(beneficiario_codigo, vigencia_inicio)` por tenant
- **Política de carga:** UPSERT

| Campo | Tipo | Obrig. | Vocabulário / regra | Sensibilidade | Capacidades |
|---|---|---|---|---|---|
| `beneficiario_codigo` | string | sim |  | interno | contract |
| `contrato_codigo` | string | sim |  | interno | contract |
| `plano_codigo` | string | sim | tem de ser um dos planos do contrato (plano_contrato) | interno | contract |
| `vigencia_inicio` | date | sim |  | operacional | contract |
| `vigencia_fim` | date | não | exclusiva; vazio = vigente | operacional |  |
| `situacao` | string | não | ativo · suspenso · cancelado | operacional |  |

### `preco_contratado`

- **Grão:** 1 linha por vigência de preço unitário contratado de um procedimento num prestador
- **Chave de negócio (carga):** `(prestador_codigo, procedimento_codigo, vigencia_inicio)` por tenant
- **Política de carga:** UPSERT

| Campo | Tipo | Obrig. | Vocabulário / regra | Sensibilidade | Capacidades |
|---|---|---|---|---|---|
| `prestador_codigo` | string | sim |  | interno | explicacao_preco |
| `procedimento_codigo` | string | sim |  | interno | explicacao_preco |
| `vigencia_inicio` | date | sim |  | operacional | explicacao_preco |
| `vigencia_fim` | date | não | exclusiva; vazio = vigente | operacional |  |
| `valor_unitario` | decimal | sim | valor por unidade (quantidade) do item | operacional | explicacao_preco |
| `tipo_remuneracao` | string | não | tabela · pacote · diaria · honorario · outro | operacional |  |

### `receita`

- **Grão:** 1 linha por competência × plano — contraprestação (receita) reconhecida
- **Chave de negócio (carga):** `(competencia, plano_codigo)` por tenant
- **Política de carga:** UPSERT

| Campo | Tipo | Obrig. | Vocabulário / regra | Sensibilidade | Capacidades |
|---|---|---|---|---|---|
| `competencia` | month | sim |  | interno | sinistralidade, loss_ratio |
| `plano_codigo` | string | sim |  | interno | loss_ratio |
| `quantidade_beneficiarios` | int | sim |  | operacional | sinistralidade |
| `receita_contraprestacao` | decimal | sim |  | operacional | sinistralidade, loss_ratio |

### `fatura`

- **Grão:** 1 linha por documento de cobrança (fatura/boleto/nota) emitido pela operadora
- **Chave de negócio (carga):** `(numero_documento)` por tenant
- **Política de carga:** UPSERT

| Campo | Tipo | Obrig. | Vocabulário / regra | Sensibilidade | Capacidades |
|---|---|---|---|---|---|
| `numero_documento` | string | sim |  | operacional | financial |
| `unidade_cobranca` | string | sim | contrato · empresa | operacional | financial |
| `contrato_codigo` | string | não | obrigatório se unidade = contrato | interno |  |
| `empresa_codigo` | string | não | obrigatório se unidade = empresa | interno |  |
| `competencia_faturamento` | month | sim |  | interno | financial |
| `data_emissao` | date | não |  | operacional |  |
| `data_vencimento` | date | não |  | operacional |  |
| `valor_bruto` | decimal | sim |  | operacional | financial |
| `valor_desconto` | decimal | não |  | operacional |  |
| `valor_acrescimo` | decimal | não |  | operacional |  |
| `valor_total` | decimal | não | se ausente: bruto − desconto + acréscimo; se informado, tem de bater | operacional | financial |
| `status` | string | não | emitida · cancelada · substituida | operacional |  |

### `fatura_item`

- **Grão:** 1 linha por item de um documento de cobrança
- **Chave de negócio (carga):** `(numero_documento, numero_item)` por tenant
- **Política de carga:** SNAPSHOT_DOCUMENTO

| Campo | Tipo | Obrig. | Vocabulário / regra | Sensibilidade | Capacidades |
|---|---|---|---|---|---|
| `numero_documento` | string | sim |  | operacional |  |
| `numero_item` | string | sim |  | operacional | financial |
| `natureza` | string | não | mensalidade · coparticipacao · taxa · ajuste · retroativo · desconto · acrescimo · outro | operacional |  |
| `contrato_codigo` | string | não |  | interno |  |
| `plano_codigo` | string | não |  | interno |  |
| `competencia_referencia` | month | não |  | interno |  |
| `quantidade_beneficiarios` | int | não |  | operacional |  |
| `valor` | decimal | sim |  | operacional | financial |

### `recebimento`

- **Grão:** 1 linha por baixa/pagamento recebido de um documento
- **Chave de negócio (carga):** `(numero_recebimento)` por tenant
- **Política de carga:** UPSERT

| Campo | Tipo | Obrig. | Vocabulário / regra | Sensibilidade | Capacidades |
|---|---|---|---|---|---|
| `numero_recebimento` | string | sim |  | operacional | financial |
| `numero_documento` | string | não |  | operacional |  |
| `data_recebimento` | date | sim |  | operacional | financial |
| `competencia_recebimento` | month | não | se ausente: mês de data_recebimento | interno | financial |
| `valor_recebido` | decimal | sim |  | operacional | financial |

### `receita_reconhecida`

- **Grão:** 1 linha por lançamento de contraprestação reconhecida, no nível de atribuição que a origem permite
- **Chave de negócio (carga):** `(competencia, source_record_id)` por tenant
- **Política de carga:** COMPETENCIA_SNAPSHOT

| Campo | Tipo | Obrig. | Vocabulário / regra | Sensibilidade | Capacidades |
|---|---|---|---|---|---|
| `source_record_id` | string | sim |  | operacional | contract_loss_ratio |
| `competencia` | month | sim | competência de RECONHECIMENTO | interno | loss_ratio, contract_loss_ratio |
| `contrato_codigo` | string | não |  | interno | contract_loss_ratio |
| `plano_codigo` | string | não |  | interno |  |
| `empresa_codigo` | string | não | cobrança consolidada da empresa SEM detalhamento por contrato | interno |  |
| `natureza` | string | não | contraprestacao · ajuste · outro | operacional |  |
| `numero_documento` | string | não |  | operacional |  |
| `quantidade_beneficiarios` | int | não |  | operacional |  |
| `receita_contraprestacao` | decimal | sim |  | operacional | loss_ratio |

### `evento_assistencial`

- **Grão:** 1 linha por atendimento/procedimento/ocorrência de um beneficiário (item de conta)
- **Chave de negócio (carga):** `(source_record_id)` por tenant
- **Política de carga:** INCREMENTAL_ID

| Campo | Tipo | Obrig. | Vocabulário / regra | Sensibilidade | Capacidades |
|---|---|---|---|---|---|
| `source_record_id` | string | sim |  | operacional | ALL |
| `beneficiario_codigo` | string | sim |  | interno | ALL |
| `prestador_codigo` | string | sim |  | interno | provider |
| `procedimento_codigo` | string | sim |  | interno | loss_ratio |
| `especialidade_codigo` | string | não |  | interno | loss_ratio |
| `estabelecimento_codigo` | string | não | local do atendimento (prestador) — opcional; executante = prestador_codigo | interno |  |
| `prestador_solicitante_codigo` | string | não | quem solicitou (prestador) — opcional | interno |  |
| `data_evento` | date | sim |  | operacional | ALL |
| `competencia` | month | não | se ausente: mês de data_evento (DECISÃO de onboarding — documentar) | interno | ALL |
| `competencia_apresentacao` | month | não |  | interno |  |
| `competencia_pagamento` | month | não |  | interno |  |
| `numero_guia` | string | não |  | operacional |  |
| `numero_conta` | string | não |  | operacional |  |
| `unidade_medida` | string | não |  | operacional |  |
| `natureza_lancamento` | string | não | normal · estorno · ajuste · cancelamento | operacional |  |
| `tipo_atendimento` | string | sim | consulta · exame · terapia · pronto_socorro · internacao · cirurgia · opme | operacional | loss_ratio |
| `quantidade` | int | não |  | operacional |  |
| `valor_apresentado` | decimal | sim |  | operacional | ALL |
| `valor_glosado` | decimal | não |  | operacional | explicacao_glosa |
| `valor_pago` | decimal | não | se ausente: valor_apresentado − valor_glosado (contrato) | operacional | ALL |
| `valor_coparticipacao` | decimal | não | se ausente: 0 — registrado em Data Quality (INFO) | operacional | explicacao_coparticipacao |


## 5. Divergências pendentes (impedem a homologação)

| # | Divergência | Situação |
|---|---|---|
| D1 | `beneficiario.sexo` opcional no contrato; NOT NULL no banco | aberta — arquivo sem sexo passa no DQ e falha no Silver |
| D2 | `beneficiario.codigo` VARCHAR(16) no banco | aberta — códigos/hashes longos quebram a carga |
| D3 | pseudonimização é responsabilidade da origem (mapping não oferece hash) | aberta |
| D4 | `procedimento.id_especialidade` nullable no YAML, obrigatório na carga | aberta |
| D7 | chave do evento: YAML usa `source_system`, banco usa `source_connection_id` | aberta |
| D8 | "Política de carga" dos YAMLs (FULL/INCREMENTAL/UPSERT) ≠ o que o Silver faz (UPSERT de dimensões; SNAPSHOT por competência de fatos) — a coluna acima mostra o YAML | aberta |
| D9 | contrato YAML de `tenant` desatualizado | aberta |
| D10 | `regiao` e `diagnostico` sem contrato YAML | aberta |
| D11 | metadados RAW por linha no `_schema.md` × hash por arquivo implementado | aberta |
| D12 | `faixa_etaria` fixada na 1ª competência carregada | aberta |
| D6 | contrato do evento pelo cadastro atual | ✅ resolvida (Evolução V1 + Consolidação V1) |
| — | receita por contrato só como layout com rateio | ✅ resolvida (`receita_reconhecida`, sem rateio) |
| — | vidas por contrato pelo vínculo atual | ✅ resolvida (vínculo vigente) |
| — | sobreposição de vigência só no lote | ✅ resolvida (também contra o persistido) |

## 6. Pendências de negócio

Ver [CONSOLIDACAO_CANONICO_V1.md §7](CONSOLIDACAO_CANONICO_V1.md): alocação gerencial de
receita (P1), múltiplas contratantes simultâneas (P2), vínculo suspenso como exposição (P3),
contagem de estornos (P4), valor pago em caixa (P5), regra contábil de reconhecimento (P6).
