# Modelo Canônico W2Health (camada Silver)

> **Estado:** revisado contra o código da `main` após a Fase 3 (out/2026). Substitui a
> versão "v1.2" deste documento. **Evolução V1 — investigação de custos (23/10/2026):**
> entidades opcionais `vinculo_beneficiario` e `preco_contratado`, vínculo da competência e
> rede (estabelecimento/solicitante) no evento — ver
> [EVOLUCAO_V1_INVESTIGACAO_CUSTOS.md](EVOLUCAO_V1_INVESTIGACAO_CUSTOS.md).
> **Consolidação do canônico V1 (23/10/2026):** empresa, empresa × contrato, fatura, item,
> recebimento e receita reconhecida (substitui `receita_contrato`); atribuição estrita do
> evento ao vínculo vigente; vidas históricas. Layout candidato e divergências atualizadas:
> [MODELO_CANONICO_V1_OFICIAL_CANDIDATO.md](MODELO_CANONICO_V1_OFICIAL_CANDIDATO.md); regras:
> [REGRAS_RECEITA_E_FATURAMENTO.md](REGRAS_RECEITA_E_FATURAMENTO.md).
> **Para quem:** quem vai produzir a documentação formal de dados (engenharia de dados).
> As seções 7 e 8 listam o que está divergente ou em aberto e precisa de decisão antes de
> virar documentação definitiva.

## 1. Onde está a verdade (ordem de precedência)

| # | Artefato | Papel |
|---|---|---|
| 1 | `data_platform/contracts/*.yaml` | **Contrato canônico** — fonte de verdade do modelo (15 entidades). Formato em `data_platform/contracts/_schema.md`. |
| 2 | `backend/app/data_platform/canonical.py` | **Contrato operacional v1** — o subconjunto que uma carga de arquivo pode entregar hoje (11 entidades; 2 opcionais da Evolução V1), com FKs por código de negócio. |
| 3 | `backend/app/models/` + `backend/alembic/versions/` | **Implementação física** — as tabelas Silver no PostgreSQL (nomes no plural). |
| 4 | `docs/DATA_DICTIONARY.md` | Dicionário campo a campo, **gerado** de (1) por `python data_platform/generate_dictionary.py`. Nunca editar à mão. Em sincronia com os YAMLs na data desta revisão. |

Documentos irmãos: obrigatoriedade por capacidade em [DATA_CONTRACT.md](DATA_CONTRACT.md) e
[CAPABILITY_READINESS.md](CAPABILITY_READINESS.md); mapping de origem em
[MAPPING_FRAMEWORK.md](MAPPING_FRAMEWORK.md); linhagem em [DATA_LINEAGE.md](DATA_LINEAGE.md);
regras de qualidade em [DATA_QUALITY.md](DATA_QUALITY.md); tabelas físicas e agregados em
[DATA_MODEL.md](DATA_MODEL.md).

## 2. Princípio e fluxo

O motor analítico **não conhece nenhum nome de coluna de origem**. Toda integração traduz a
origem para o modelo canônico; Gold e Serving leem apenas o canônico.

```
ARQUIVO/FONTE ─▶ RAW (object storage, imutável) ─▶ MAPPING (data_platform/mappings/*.yaml)
   ─▶ GATE DE DATA QUALITY ─▶ SILVER = modelo canônico (PostgreSQL, RLS por tenant)
   ─▶ GOLD (agg_*) ─▶ RECONCILIAÇÃO ─▶ publicação ─▶ API
```

- Desde a **Fase 3** a ingestão é assíncrona: o upload vira um `pipeline_jobs` (fila no
  PostgreSQL) processado por um worker; o RAW vai para object storage (S3 ou disco local).
  Isso **não mudou o modelo canônico** — mudou só o transporte até ele
  ([PIPELINE_ARCHITECTURE.md](PIPELINE_ARCHITECTURE.md), [OBJECT_STORAGE.md](OBJECT_STORAGE.md)).
- Silver → Gold → reconciliação rodam numa única transação: se qualquer etapa falhar, nada
  é publicado.
- Teste que garante o desacoplamento:
  `backend/tests/test_multitenancy.py::test_source_agnostic_mapping_para_canonico_e_agregacao`.

## 3. Mapa de entidades

Legenda da coluna **Carga**: ✅ carregável pelo contrato operacional v1 · 🔹 derivada na
Silver · ⏸ layout preparado, sem carga · — cadastro da plataforma.

| Entidade canônica | Tabela física | Grão | Chave de negócio | Carga |
|---|---|---|---|---|
| `tenant` | `tenants` | 1 por cliente | `id` (slug) | — |
| `competencia` | `competencias` | 1 por mês (dia 1) | `competencia` | 🔹 criada a partir das datas carregadas |
| `especialidade` | `especialidades` | 1 por especialidade | `(tenant_id, codigo)` | ✅ |
| `plano` | `planos` | 1 por produto | `(tenant_id, codigo)` | ✅ |
| `contrato` | `contratos` | 1 por contrato/apólice | `(tenant_id, codigo)` | ✅ |
| `plano_contrato` | `planos_contratos` | 1 por plano oferecido pelo contrato (N:N) | `(tenant_id, contrato, plano)` | ✅ |
| `prestador` | `prestadores` | 1 por prestador | `(tenant_id, codigo)` | ✅ |
| `procedimento` | `procedimentos` | 1 por código de procedimento | `(tenant_id, codigo)` | ✅ |
| `beneficiario` | `beneficiarios` | 1 por vida (estado atual) | `(tenant_id, codigo)` | ✅ |
| `vinculo_beneficiario` | `beneficiario_vinculos` | 1 por período de vínculo (contrato/plano) | `(tenant_id, beneficiário, vigencia_inicio)` | ✅ opcional (Evolução V1) |
| `preco_contratado` | `precos_contratados` | 1 por vigência de preço prestador × procedimento | `(tenant_id, prestador, procedimento, vigencia_inicio)` | ✅ opcional (Evolução V1) |
| `receita` | `receitas` | competência × plano | `(tenant_id, competencia, plano)` | ✅ |
| `evento_assistencial` | `eventos_assistenciais` | 1 por item de conta/atendimento | `(tenant_id, fonte, source_record_id)` | ✅ |
| `glosa` | coluna `eventos_assistenciais.valor_glosado` | atributo do evento | — | ✅ (no evento) |
| `coparticipacao` | coluna `eventos_assistenciais.valor_coparticipacao` | atributo do evento | — | ✅ (no evento) |
| `empresa` | `empresas` | 1 por empresa | `(tenant_id, codigo)` | ✅ opcional (Consolidação V1) |
| `empresa_contrato` | `empresas_contratos` | período de associação empresa × contrato × papel | `(tenant_id, contrato, papel, vigencia_inicio)` | ✅ opcional |
| `fatura` / `fatura_item` | `faturas` / `fatura_itens` | documento / item de cobrança | `(tenant_id, numero_documento[, numero_item])` | ✅ opcional |
| `recebimento` | `recebimentos` | baixa | `(tenant_id, numero_recebimento)` | ✅ opcional |
| `receita_reconhecida` | `receitas_reconhecidas` (era `receitas_contrato`) | lançamento × nível de atribuição | `(tenant_id, competencia, source_record_id)` | ✅ opcional — sem rateio |
| *(sem contrato YAML)* | `regioes` | cidade × UF por tenant | `(tenant_id, cidade, uf)` — lógica, sem constraint no banco | 🔹 derivada de `cidade`/`uf` |
| *(sem contrato YAML)* | `diagnosticos` | 1 por CID | `(tenant_id, cid)` | ⏸ só o gerador sintético popula |

`tenant` e `competencia` são os únicos sem `tenant_id`. Toda outra tabela tem `tenant_id`
NOT NULL, sem default, e é protegida por RLS ([MULTI_TENANCY.md](MULTI_TENANCY.md)).

## 4. Entidades — contrato operacional v1 (o que a carga entrega)

FKs chegam por **código de negócio** (`plano_codigo`, `beneficiario_codigo`…) e são
convertidas em ids técnicos (`id_plano`…) na Silver. "Obrig." = obrigatório no contrato
operacional (`canonical.py`). Ordem de carga: especialidade → plano → contrato → plano_contrato →
prestador → procedimento → beneficiário → vínculo (opcional) → preço contratado (opcional) →
receita → evento.

### 4.1 especialidade — carga: UPSERT por código
| Campo | Tipo | Obrig. | Observação |
|---|---|---|---|
| `codigo` | string | sim | CBO ou tabela própria |
| `nome` | string | sim | |
| `grupo` | string | não | `clinica` · `cirurgica` · `diagnostico` · `terapia`; ausente fica nulo |

### 4.2 plano — carga: UPSERT por código
| Campo | Tipo | Obrig. | Observação |
|---|---|---|---|
| `codigo` | string | sim | código do produto / registro ANS |
| `nome` | string | sim | |
| `segmentacao` | string | não | `ambulatorial` · `hospitalar` · `completo` |
| `tem_coparticipacao` | bool | não | ausente → `false` |
| `percentual_coparticipacao` | decimal | não | fração 0–1 sobre `valor_pago`; ausente → 0; **informativo** nesta fase |

Só no banco: `ticket_medio_base` (parâmetro do gerador sintético; nulo para fonte real).

### 4.3 contrato — carga: UPSERT por código
| Campo | Tipo | Obrig. | Observação |
|---|---|---|---|
| `codigo` | string | sim | |
| `nome` | string | não | ausente → recebe o próprio `codigo` (exibição) |
| `tipo` | string | não | `PF` · `PME` · `Empresarial` |

Contrato **não** guarda plano: um contrato oferece N planos (ver `plano_contrato`).

No YAML, mas **fora da carga e do banco**: `data_base`, `meta_sinistralidade` (layout de
reajuste). Só no banco: `vidas_alvo` (gerador sintético; nulo para fonte real).

### 4.3.1 plano_contrato — carga: UPSERT pelo par (contrato, plano)
| Campo | Tipo | Obrig. | Observação |
|---|---|---|---|
| `contrato_codigo` | string | sim | FK → contrato |
| `plano_codigo` | string | sim | FK → plano |

1 linha por plano que o contrato oferece. PK no banco: `(tenant_id, id_contrato, id_plano)`.
Par ausente numa carga não é removido (beneficiários podem apontar para ele). Preço por par
(tabela de preço × grupo/faixa etária) é outra entidade — fora da v1.

### 4.4 prestador — carga: UPSERT por código
| Campo | Tipo | Obrig. | Observação |
|---|---|---|---|
| `codigo` | string | sim | código interno ou CNES mascarado |
| `nome` | string | sim | gravado em `prestadores.nome_ficticio` |
| `tipo_prestador` | string | não | `hospital` · `clinica` · `laboratorio` · `pronto_atendimento` · `consultorio` |
| `cidade`, `uf` | string | sim | viram `id_regiao`; macrorregião pela divisão do IBGE |
| `especialidade_codigo` | string | sim | especialidade principal = grupo de pares da detecção de anomalia |

Só no banco: `nivel_preco` (gerador sintético).

### 4.5 procedimento — carga: UPSERT por código
| Campo | Tipo | Obrig. | Observação |
|---|---|---|---|
| `codigo` | string | sim | TUSS/CBHPM ou tabela própria |
| `descricao` | string | sim | |
| `especialidade_codigo` | string | sim | especialidade típica |
| `grupo_procedimento` | string | não | ausente → `"nao_informado"` (a dimensão de análise exige valor) |
| `perfil_utilizacao` | string | não | `pontual` · `recorrente` · `variavel`; apoia, nunca prova, hipótese de coorte |

Só no banco: `complexidade`, `custo_base`, `tipo_atendimento_tipico`, `idade_min`,
`idade_max` (parâmetros do gerador sintético).

### 4.6 beneficiario — carga: UPSERT por código
| Campo | Tipo | Obrig. | Observação |
|---|---|---|---|
| `codigo` | string | sim | identificador **pseudonimizado**; nunca CPF/CNS |
| `data_nascimento` | date | sim | |
| `sexo` | string | não | `M` · `F` — ver divergência D1 |
| `plano_codigo` | string | sim | FK → plano; tem de ser um plano do contrato (`plano_fora_do_contrato` = ERROR) |
| `contrato_codigo` | string | sim | FK → contrato |
| `cidade`, `uf` | string | sim | viram `id_regiao` |
| `data_adesao` | date | sim | |
| `data_saida` | date | não | nulo = ativo |

Derivados na Silver: `faixa_etaria` (calculada na **1ª competência carregada** — mesma
aproximação do gerador; o contrato pede faixa por competência) e `status`
(`inativo` se `data_saida` ≤ fim do período carregado, senão `ativo`).
No YAML, fora da carga e do banco: `motivo_saida` (recomendado).

### 4.7 receita — carga: SNAPSHOT por competência
| Campo | Tipo | Obrig. | Observação |
|---|---|---|---|
| `competencia` | month | sim | 1º dia do mês |
| `plano_codigo` | string | sim | FK → plano |
| `quantidade_beneficiarios` | int | sim | vidas expostas (base do PMPM) |
| `receita_contraprestacao` | decimal | sim | contraprestação reconhecida no mês |

Grão **mínimo** para sinistralidade. Normalmente vem do faturamento/contábil, não do
assistencial. As competências presentes no arquivo substituem integralmente as existentes
do tenant.

### 4.8 evento_assistencial — carga: SNAPSHOT por competência e fonte
| Campo | Tipo | Obrig. | Observação |
|---|---|---|---|
| `source_record_id` | string | sim | id do item na origem (conta + item, autorização…) |
| `beneficiario_codigo` | string | sim | FK → beneficiário |
| `prestador_codigo` | string | sim | FK → prestador executante |
| `procedimento_codigo` | string | sim | FK → procedimento |
| `especialidade_codigo` | string | não | ausente → especialidade do procedimento |
| `estabelecimento_codigo` | string | não | FK → prestador: LOCAL do atendimento (Evolução V1) |
| `prestador_solicitante_codigo` | string | não | FK → prestador solicitante (Evolução V1) |
| `data_evento` | date | sim | |
| `competencia` | month | não | ausente → mês de `data_evento` (**decisão de onboarding a registrar por cliente**) |
| `tipo_atendimento` | string | sim | `consulta` · `exame` · `terapia` · `pronto_socorro` · `internacao` · `cirurgia` · `opme` |
| `quantidade` | int | não | ausente → 1 |
| `valor_apresentado` | decimal | sim | despesa bruta |
| `valor_glosado` | decimal | não | ausente → 0 (cobertura registrada como INFO no DQ) |
| `valor_pago` | decimal | não | ausente → `valor_apresentado − valor_glosado` |
| `valor_coparticipacao` | decimal | não | ausente → 0 (cobertura registrada como INFO no DQ) |

Derivados na Silver: `id_regiao` (região do **beneficiário**), `id_diagnostico` = nulo,
**`id_contrato` / `id_plano` = vínculo vigente na `data_evento`** (histórico de
`vinculo_beneficiario` se enviado; senão o vínculo atual do beneficiário no momento da carga)
— **congelados no evento**: uma troca posterior de contrato não reclassifica despesa já
carregada (Evolução V1; a Gold usa `COALESCE(evento, beneficiário)` para registros legados).
Os eventos das competências do arquivo **da mesma fonte** (`source_connection_id`) são
substituídos; eventos de outras fontes do mesmo tenant ficam intactos. Idempotência:
índice único `(tenant_id, source_connection_id, source_record_id)`.
Só no banco: `cenario_tag` (QA do gerador sintético).

### 4.9 vinculo_beneficiario — carga: UPSERT por (beneficiário, início) · OPCIONAL
| Campo | Tipo | Obrig. | Observação |
|---|---|---|---|
| `beneficiario_codigo` | string | sim | FK → beneficiário |
| `contrato_codigo`, `plano_codigo` | string | sim | o par tem de existir em `plano_contrato` (`plano_fora_do_contrato` = ERROR) |
| `vigencia_inicio` | date | sim | inclusive |
| `vigencia_fim` | date | não | **exclusiva**; vazio = vigente. Sobreposição na mesma chave = ERROR |

Usado só para resolver o contrato/plano de cada evento. A exposição (vidas) por contrato
ainda usa o vínculo atual do beneficiário — limitação registrada.

### 4.10 preco_contratado — carga: UPSERT por (prestador, procedimento, início) · OPCIONAL
| Campo | Tipo | Obrig. | Observação |
|---|---|---|---|
| `prestador_codigo`, `procedimento_codigo` | string | sim | FKs |
| `vigencia_inicio` | date | sim | uma nova vigência = reajuste/renegociação |
| `vigencia_fim` | date | não | exclusiva; vazio = vigente |
| `valor_unitario` | decimal | sim | por UNIDADE (mesma unidade de `evento.quantidade`); ≤ 0 = ERROR |
| `tipo_remuneracao` | string | não | `tabela` · `pacote` · `diaria` · `honorario` · `outro` — informativo |

**Única evidência aceita para afirmar "reajuste".** Sem preço vigente nos dois períodos
comparados, a variação de valor por unidade é "custo unitário observado". Não é módulo de
negociação.

## 5. Semântica financeira

```
valor_apresentado     = despesa BRUTA apresentada pelo prestador
valor_glosado         = parcela glosada (não paga ao prestador)
valor_pago            = valor_apresentado − valor_glosado
valor_coparticipacao  = parcela de valor_pago cobrada do beneficiário
despesa_liquida       = valor_apresentado − valor_glosado − valor_coparticipacao   (DERIVADA, só na Gold)
```

- `sinistralidade = despesa_liquida / receita × 100` (convenção do MVP); a bruta é sempre
  exibida ao lado.
- No **escopo de contrato** não há receita (`receitas_contrato` vazia) → só composição da
  despesa, PMPM e concentração.
- Coparticipação derivada pelo plano (`valor_pago × percentual` para consulta/exame/
  terapia/PS) está **descrita no YAML mas não implementada**: hoje, ausente = 0.

## 6. Linhagem, carga e qualidade

**Linhagem por linha** (todas as tabelas Silver de tenant, `LineageMixin`):
`source_system`, `source_connection_id`, `ingestion_run_id`, `source_record_id`. Nas
dimensões, `source_record_id` = código de negócio; em receita = `AAAA-MM/plano`; em região
= `cidade/UF`. O RAW guarda o arquivo original byte a byte com hash
(`raw_objects`); ingestões ficam em `ingestion_runs`
(status `QUEUED` → `RUNNING` → `SUCCESS`/`PARTIAL`/`FAILED`/`CANCELLED`).

**Regras de carga reais** (`backend/app/data_platform/silver.py`):
- dimensões: UPSERT pela chave de negócio; registro ausente no arquivo **não** é apagado;
- fatos (receita, evento): SNAPSHOT das competências presentes no arquivo;
- reenviar o mesmo arquivo não duplica.

**Gate de Data Quality** (`backend/app/data_platform/quality.py` +
`data_platform/quality/rules.py`): linha com ERROR é rejeitada (nunca "corrigida");
acima de `quality.max_rejected_ratio` do mapping (padrão 0) a carga é bloqueada.

| Regra | Severidade | Entidade |
|---|---|---|
| `tenant_id_ausente` | ERROR | todas |
| `benef_inexistente_em_evento`, `prestador_inexistente`, `contrato_inexistente`, `fk_inexistente_<campo>` | ERROR | evento, beneficiário, dimensões |
| `evento_sem_data`, `competencia_invalida`, `valor_negativo`, `glosa_maior_que_apresentado` | ERROR | evento, receita |
| `data_saida_antes_adesao` | ERROR | beneficiário |
| `vigencia_invertida`, `vigencia_sobreposta` | ERROR | vínculo, preço contratado (Evolução V1) |
| `preco_nao_positivo` | ERROR | preço contratado (Evolução V1) |
| `uf_invalida`, `colunas_ausentes`, `mapping_invalido`, `arquivo_obrigatorio_ausente` | ERROR | carga |
| `coparticipacao_acima_permitido`, `despesa_liquida_inconsistente` | WARNING | evento |
| `evento_duplicado`, `receita_duplicada`, duplicidade de chave no lote | WARNING (mantém a última) | evento, receita, dimensões |
| `cobertura_<campo>`, `volume`, `arquivo_opcional_ausente` | INFO | todas |

## 7. Divergências conhecidas (contrato × carga × banco)

Itens verificados no código. Os marcados **⚠ quebra carga** fazem uma carga válida pelo
contrato operacional falhar no banco — precisam de correção no código, não só de texto.

| # | Onde | Divergência | Impacto |
|---|---|---|---|
| D1 | `beneficiario.sexo` | Opcional no YAML e no `canonical.py`; **NOT NULL** em `beneficiarios`. | ⚠ quebra carga: arquivo sem sexo passa no DQ e falha na Silver (transação inteira volta). |
| D2 | `beneficiario.codigo` | Banco `VARCHAR(16)`; contrato/prestador usam 40. O YAML pede hash/máscara do identificador — um hash comum passa de 16. | ⚠ quebra carga com códigos > 16 caracteres. Plano, especialidade e procedimento têm `VARCHAR(20)`. |
| D3 | Pseudonimização | O YAML diz "hash ou máscara, nunca CPF em claro", mas o mapping só oferece `strip/upper/lower/collapse_spaces/digits_only`. | Hoje a pseudonimização é responsabilidade do cliente antes do envio. Documentar ou implementar. |
| D4 | `procedimento.id_especialidade` | Nullable no YAML; obrigatório na carga e no banco. | Contrato canônico e físico discordam. |
| D5 | `prestador.cidade/uf/especialidade`, `beneficiario.contrato/cidade/uf` | Nullable no YAML; obrigatórios na carga. | Diferença **deliberada**, já documentada em [MAPPING_FRAMEWORK.md](MAPPING_FRAMEWORK.md) §6. |
| D6 | `evento_assistencial.id_contrato` | ✅ **Resolvida na Evolução V1** (migration `d0e1f2a3b4c5`): `id_contrato`/`id_plano` gravados no evento pela data do atendimento. Resta: vidas por contrato ainda pelo vínculo atual. | — |
| D7 | Chave do evento | YAML: `(tenant_id, source_system, source_record_id)`; banco: `(tenant_id, source_connection_id, source_record_id)`. | Duas conexões do mesmo sistema podem gerar o "mesmo" evento duas vezes. |
| D8 | Estratégia de carga | YAML declara `INCREMENTAL_ID` (evento) e `UPSERT` (receita); o código faz SNAPSHOT por competência. | Atualizar os YAMLs ou o código; uma carga parcial de um mês apaga o resto daquele mês. |
| D9 | `tenant` | YAML: `nome`, `status` = `onboarding/ativo/suspenso`; banco: `name`, `legal_name`, `uuid`, `plan_id`, `is_synthetic`, `status` = `ACTIVE…`. | YAML desatualizado desde a Fundação SaaS V1. |
| D10 | `regiao` e `diagnostico` | Tabelas Silver sem contrato YAML. `diagnostico` (CID, dado sensível `phi`) não tem carga. | Faltam contratos; CID fica fora de qualquer fonte real. |
| D11 | Metadados RAW | `_schema.md` lista `ingestion_id`, `ingestion_timestamp`, `raw_payload_hash`, `reference_date` por linha; a Silver guarda só os 4 campos de linhagem, e o hash fica por **arquivo** (`raw_objects`). | Atualizar `_schema.md` para o que foi implementado. |
| D12 | `faixa_etaria` | Contrato: por competência na Gold. Implementação: fixa na 1ª competência carregada. | Dívida conhecida desde a v1.2. |
| D13 | Doc vs código | [MAPPING_FRAMEWORK.md](MAPPING_FRAMEWORK.md) §6 diz que `grupo` de especialidade ausente vira "não informado"; no código isso vale para `grupo_procedimento`, e `especialidade.grupo` fica nulo. | Corrigir o texto. |

## 8. Pendências para a documentação formal

1. **Decidir D1, D2 e D3** (correções de código) antes de publicar o contrato para clientes.
2. **Alinhar os YAMLs ao implementado** — D4, D6–D11 — e regenerar
   `docs/DATA_DICTIONARY.md` (`python data_platform/generate_dictionary.py`).
3. Criar contratos YAML para `regiao` e `diagnostico` (ou declarar `diagnostico` fora do
   escopo até haver fonte real com CID e base legal LGPD).
4. Registrar, por cliente, as **decisões de onboarding** que mudam números: competência de
   atendimento vs. pagamento, origem da coparticipação (`faturada` vs. `derivada`), fonte da
   receita.
5. Documentar a política de **exclusão**: hoje nenhuma dimensão é apagada por ausência na
   origem, e fatos são substituídos por competência.
6. Diagrama ER da Silver (o físico está em [DATA_MODEL.md](DATA_MODEL.md); não há diagrama).
7. Atualizar o cabeçalho de `data_platform/README.md`, que ainda diz que "nenhum pipeline
   roda aqui" — o pipeline roda desde a Fase 2.
