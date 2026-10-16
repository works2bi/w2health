# Guia de Integração — W2Health (v1.2 → Fase 2)

Como escrever um conector. Desde a Fase 2 existe **um** caminho executável — fonte `FILE`
(pacote CSV) — que é a implementação de referência deste contrato (§Fase 2, no fim).
Nenhum conector de sistema específico (MV, Tasy, Benner, Datasul, TISS) existe; este é o
contrato que qualquer integração (arquivos, API, SQL) deverá cumprir.

## 1. O contrato

```
SOURCE MODEL  ──▶  MAPPING (data_platform/mappings/*.yaml)  ──▶  W2HEALTH CANONICAL MODEL
```

O conector é responsável por: extrair da origem, aplicar o mapping, escrever RAW com
metadados, e disparar as transformações Silver→Gold→Serving. O **motor analítico nunca
vê o nome da coluna de origem**.

## 2. Metadados obrigatórios em toda linha RAW

`tenant_id`, `source_system`, `source_entity`, `source_record_id`, `ingestion_id`,
`ingestion_timestamp`, `source_updated_at` (nullable), `reference_date`,
`raw_payload_hash`.

## 3. Estratégias de carga

Cada `data_platform/contracts/*.yaml` declara `load_strategy`, `business_key`,
`watermark_column`, `updated_at_column`, `deduplication_strategy`.

| Estratégia | Quando usar | Como |
|---|---|---|
| **FULL** | catálogos pequenos e estáveis (especialidade, plano) | trunca e recarrega o tenant |
| **INCREMENTAL_DATA** | fatos com data de referência confiável e sem correção retroativa | `WHERE data_ref > último_watermark` |
| **INCREMENTAL_ID / WATERMARK** | fatos com id/sequência monotônica (evento_assistencial) | `WHERE id > último_id` ou `updated_at > watermark` |
| **CDC** | origem expõe log de mudanças | aplicar inserts/updates/deletes por `updated_at_column` |
| **UPSERT** | dimensões que mudam (beneficiário, contrato, prestador) e fatos com correção (glosa tardia) | `ON CONFLICT (business_key) DO UPDATE` com desempate por `updated_at` |

**Idempotência:** `raw_payload_hash` — payload idêntico não reprocessa. `batch_id`
(→ `ingestion_runs`) permite reprocessar um lote inteiro.

## 4. Tratamento de histórico

Regra: o W2Health guarda **estado atual** nas dimensões e **histórico por competência**
nos fatos/agregados. Sem SCD complexo na v1.2 — mas documentar onde vai doer:

| Mudança na origem | Tratamento v1.2 | Onde precisará versionar (futuro) |
|---|---|---|
| Beneficiário **muda de plano** | UPSERT sobrescreve `beneficiario.id_plano` (estado atual) | histórico de vínculo `beneficiario_plano_periodo` p/ atribuir eventos ao plano vigente na data |
| Beneficiário **muda de contrato** | idem `id_contrato` | idem — hoje o evento pega o contrato atual do beneficiário |
| Prestador **muda de classificação/especialidade** | UPSERT sobrescreve | agregados passados já materializados não mudam (aceitável) |
| Contrato **muda de condição** (data-base, meta) | UPSERT | histórico de parâmetros — pré-req de reajuste |
| **Receita retificada** | UPSERT na `(competencia, id_plano)`; RAW mantém a versão anterior | — |
| **Evento glosado depois** | UPSERT na business key com novo `valor_glosado`; reprocessar Gold da competência | — |
| **Glosa revertida** | idem (novo `valor_glosado` menor) | — |
| **Coparticipação ajustada** | idem | marcar `origem_dado` |

## 5. Ordem de carga (dependências)

```
tenant → competencia
      → regiao, especialidade, plano
      → contrato
      → plano_contrato (depende de contrato, plano — N:N)
      → procedimento (depende de especialidade), diagnostico
      → prestador (depende de regiao, especialidade)
      → beneficiario (depende de plano_contrato, regiao)
      → receita (depende de plano)
      → evento_assistencial (depende de tudo acima)
      → [Gold] agg_sinistralidade → agg_competencia_dimensao / agg_prestador / agg_beneficiario → agg_contrato
```

## 6. Validação antes de promover para Serving

1. Data Quality sem `ERROR` (`data_platform/quality/rules.py`).
2. Reconciliação: `Σ despesa_liquida` por contrato = despesa líquida da carteira, para
   cada competência (todo evento pertence a exatamente um contrato).
3. Sanidade: sinistralidade e PMPM na ordem de grandeza esperada pela operadora.
4. Capacidades: rodar a matriz de `docs/CAPABILITY_READINESS.md`.

## 7. O que NÃO fazer

Não referenciar nomes de tabela/coluna de fornecedor fora do mapping. Não usar CPF/CNS
como chave. Não promover PII/PHI a Silver além do necessário. Não assumir que "receita por
beneficiário" existe — ela **não existe** como dado direto (ver
`docs/DISCOVERY_GESTAO_SAUDE.md`).

## Fase 2 — implementação de referência (fonte FILE)

| Passo do contrato | Onde está implementado |
|---|---|
| Cadastro da fonte (sem credencial em claro) | `app/data_platform/connections.py` + Admin → Integrações |
| Tenant explícito, papel próprio | `PipelineContext` + `PipelineSession` (`w2health_pipeline`) |
| RAW com metadados e hash | `storage.py` + `raw_objects` (sha256, linhas) |
| Mapping SOURCE → canônico | `mapping.py` + `data_platform/mappings/*.yaml` |
| Data Quality antes de promover | `quality.py` (gate) |
| Silver / Gold | `silver.py` (UPSERT dimensões, snapshot por competência dos fatos) + `rebuild_aggregations` |
| Reconciliação | `reconciliation.py` |
| Idempotência | checksum do pacote (`ingestion_runs.checksum`, `duplicate_of`) — substitui o `raw_payload_hash` por linha descrito acima, que fica para conectores incrementais |
| Linhagem | colunas de linhagem em toda tabela canônica |

Um conector novo (ex.: DATABASE) precisa só **produzir os mesmos arquivos lógicos por
entidade** (ou linhas equivalentes) e chamar o mesmo fluxo: tudo a partir do mapping é
reaproveitado. Diferenças em relação ao texto acima, nesta fase: as estratégias
`INCREMENTAL_*`/`CDC` e `FULL` **não** estão implementadas — a carga de arquivo usa
`UPSERT` (dimensões) e `COMPETENCIA_SNAPSHOT` (fatos). Detalhes:
[INGESTION_FRAMEWORK.md](INGESTION_FRAMEWORK.md), [PIPELINE_ARCHITECTURE.md](PIPELINE_ARCHITECTURE.md).
