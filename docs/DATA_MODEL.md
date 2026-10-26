# Modelo de dados — W2Health Intelligence

Fonte de verdade: `backend/app/models/`. Migrations: `backend/alembic/versions/`.

> **Fundação SaaS V1**: control plane (`ControlBase`: tenants, users, user_tenants, auth_sessions, refresh_tokens, plans, features, plan_features, tenant_features, tenant_branding(_assets), tenant_settings, tenant_secrets, audit_logs), `tenant_id` sem default e RLS no data plane — ver [DATABASE_EVOLUTION_V1.md](DATABASE_EVOLUTION_V1.md). `tenants` passou a `id (código), uuid, name, legal_name, status ACTIVE|SUSPENDED|INACTIVE, plan_id, is_synthetic, created_at, updated_at`.

> **v1.2** ([V1.2.md](V1.2.md)):
> - **`tenant_id`** (`String(40)`, NOT NULL, indexado — mixin `app/models/_mixins.py`) em
>   **todas** as tabelas persistidas, exceto `competencias` (calendário global) e `tenants`.
>   Chaves de negócio → `(tenant_id, natural)`: `beneficiarios (tenant_id, codigo)`,
>   `planos/especialidades/procedimentos (tenant_id, codigo)`, `diagnosticos (tenant_id, cid)`,
>   `receitas (tenant_id, competencia, id_plano)`, `cenarios_gabarito (tenant_id, codigo)`,
>   `uq_agg*`. `agg_sinistralidade_competencia` tem PK `(tenant_id, competencia)`.
> - `contratos.vidas_alvo` (int) — só orienta a massa sintética (48 contratos).
> - `agg_competencia_dimensao` / `agg_prestador_competencia` / `agg_beneficiario_competencia`:
>   `+ despesa_bruta, glosas, coparticipacao, despesa_liquida` (aditivo à `despesa`).
>   `agg_beneficiario_competencia + id_contrato`.
> - **`agg_contrato_competencia`** (nova) — ver §Camada analítica.
> - `app/models/platform.py`: `tenants`, `source_connections`, `source_entities`,
>   `ingestion_runs`, `pipeline_runs`, `data_quality_results`, `receitas_contrato`
>   (**layout preparado — não populado nem lido**). Todas tenant-aware. Ver
>   [CANONICAL_DATA_MODEL.md](CANONICAL_DATA_MODEL.md) e `data_platform/contracts/`.

## Camada fonte (OLTP-like)

### `regioes`
`id`, `cidade`, `uf`, `macrorregiao`

### `planos`
`id`, `codigo` (único), `nome`, `segmentacao` (ambulatorial|hospitalar|completo),
`ticket_medio_base`, `tem_coparticipacao` (bool, v1.1), `percentual_coparticipacao`
(v1.1 — aplicado sobre `valor_pago` em consulta/exame/terapia/pronto_socorro; não incide
em internação/cirurgia/OPME)

### `contratos`
`id`, `codigo`, `nome`, `tipo` (PF|PME|Empresarial). Sem plano — ver `planos_contratos`.

### `planos_contratos`
PK (`tenant_id`, `id_contrato→contratos`, `id_plano→planos`). Contrato × plano é N:N: 1 linha
por plano oferecido pelo contrato.

### `especialidades`
`id`, `codigo` (único), `nome`, `grupo` (clinica|cirurgica|diagnostico|terapia)

### `procedimentos`
`id`, `codigo` (único), `descricao`, `id_especialidade→especialidades`,
`grupo_procedimento`, `complexidade` (1–5), `custo_base`, `tipo_atendimento_tipico`,
`idade_min`, `idade_max`, `perfil_utilizacao` (v1.1 — pontual|recorrente|variavel,
mapeado por `grupo_procedimento`; apoia a classificação de hipóteses na análise de
coortes, nunca usado sozinho para afirmar causalidade)

### `prestadores`
`id`, `nome_ficticio`, `tipo_prestador` (hospital|clinica|laboratorio|pronto_atendimento|
consultorio), `id_regiao→regioes`, `id_especialidade_principal→especialidades`,
`nivel_preco` (multiplicador interno de preço, não exposto)

### `diagnosticos`
`id`, `cid` (fictício, único), `descricao`, `id_especialidade→especialidades` (nullable)

### `competencias`
`competencia` (PK, 1º dia do mês), `ano`, `mes`, `mes_nome`, `trimestre`, `is_inverno`

### `beneficiarios`
`id`, `codigo` (`BEN-000001`, único), `sexo` (M|F), `data_nascimento`, `faixa_etaria`,
`id_regiao`, `id_plano`, `id_contrato`, `data_adesao`, `data_saida` (nullable), `status`
(ativo|inativo). FK composta (`tenant_id`, `id_contrato`, `id_plano`) → `planos_contratos`:
o plano da vida tem de ser um plano do contrato dela.

### `beneficiario_vinculos`  (Evolução V1) — histórico de vínculo, opcional
`id`, `id_beneficiario`, `id_contrato`, `id_plano`, `vigencia_inicio`, `vigencia_fim`
(exclusiva; nula = vigente) + linhagem. Único: (`tenant_id`, `id_beneficiario`,
`vigencia_inicio`). FK composta → `planos_contratos`. Só é usado pelo Silver para resolver
o contrato/plano de cada evento.

### `precos_contratados`  (Evolução V1) — preço unitário com vigência, opcional
`id`, `id_prestador`, `id_procedimento`, `vigencia_inicio`, `vigencia_fim` (exclusiva),
`valor_unitario` (por unidade de `quantidade`), `tipo_remuneracao` + linhagem. Único:
(`tenant_id`, `id_prestador`, `id_procedimento`, `vigencia_inicio`). Única evidência aceita
para afirmar reajuste ([ANALYTICS_ENGINE.md](ANALYTICS_ENGINE.md) §13).

### `receitas`  — grão: competência × plano
`id`, `competencia`, `id_plano→planos`, `quantidade_beneficiarios`,
`receita_contraprestacao`. Único: (`competencia`, `id_plano`).

### `eventos_assistenciais`  — grão: 1 linha por atendimento/procedimento
`id` (bigint), `id_beneficiario`, `id_prestador`, `id_procedimento`, `id_especialidade`,
`id_diagnostico` (nullable), `id_regiao`, `data_evento`, `competencia`, `tipo_atendimento`
(consulta|exame|terapia|pronto_socorro|internacao|cirurgia|opme), `quantidade`,
`valor_apresentado`, `valor_glosado`, `valor_pago`, `valor_coparticipacao` (v1.1),
`cenario_tag` (nullable — rótulo do cenário sintético que injetou/alterou a linha; só
QA/testes).
**Evolução V1 (nullable):** `id_contrato`, `id_plano` — vínculo vigente NA DATA DO EVENTO,
congelado na carga (a Gold usa `COALESCE(evento, beneficiário)`); `id_estabelecimento`
(local do atendimento) e `id_prestador_solicitante` — FKs → `prestadores`; `id_prestador`
segue sendo o executante.

Semântica financeira (v1.1 — ver [V1.1.md](V1.1.md) § Etapa B):
```
valor_apresentado    = despesa BRUTA apresentada pelo prestador
valor_glosado        = parcela glosada (não paga ao prestador)
valor_pago           = valor_apresentado − valor_glosado          (existente, inalterado)
valor_coparticipacao = parcela de valor_pago cobrada do beneficiário
despesa líquida (calculada, não persistida por evento)
    = valor_apresentado − valor_glosado − valor_coparticipacao
```
**Despesa assistencial bruta = Σ valor_apresentado. Despesa líquida (KPI oficial do
MVP) = Σ (valor_apresentado − valor_glosado − valor_coparticipacao).**

Índices: `competencia`; compostos `(competencia, id_especialidade|id_prestador|
id_procedimento|tipo_atendimento)`; FKs individuais; `cenario_tag`.

## Camada analítica (materializada pós-seed)

### `agg_sinistralidade_competencia`  — PK `competencia`
`receita`, `beneficiarios_ativos`,
`exposicao_beneficiario_mes` (= Σ `quantidade_beneficiarios` das receitas do mês),
`eventos`, `custo_pmpm`, `receita_media_beneficiario`.
**v1.1**: `despesa_bruta` (Σ apresentado), `glosas` (Σ glosado), `coparticipacao`
(Σ coparticipação), `despesa_liquida` (= bruta − glosas − coparticipacao),
`sinistralidade_bruta`, `sinistralidade_liquida` (%). Não há mais colunas soltas
`despesa`/`sinistralidade` no banco — as consultas em `analytics_repo.py` expõem
`despesa_liquida`/`sinistralidade_liquida` também sob os aliases `despesa`/
`sinistralidade` (convenção oficial do MVP), sem duplicar dado.

### `agg_competencia_dimensao`  — único (`competencia`, `dimensao`, `chave`)
`dimensao` ∈ {grupo_despesa, tipo_atendimento, especialidade, procedimento, prestador,
regiao, faixa_etaria, sexo, plano, **contrato** (v1.1)}. `chave` (id ou valor categórico), `rotulo`,
`despesa`, `eventos`, `quantidade`, `beneficiarios`, `custo_medio` (= despesa/eventos),
`freq_por_mil` (= eventos / exposição × 1000). Índices em `competencia` e `dimensao`.
`despesa` aqui é sempre **Σ valor_pago** (bruta − glosa, sem coparticipação) — o efeito
glosa/coparticipação só é decomposto no nível executivo (`agg_sinistralidade_competencia`
+ `/analytics/sinistralidade/composicao`), não propagado ao bridge por dimensão (ver
[V1.1.md](V1.1.md) § Limitações — "Fase B" fica para uma versão futura).

### `agg_prestador_competencia`  — único (`competencia`, `id_prestador`)
`despesa`, `eventos`, `beneficiarios`, `custo_medio`, `participacao` (fração da despesa do
mês), `procedimento_top_id`, `procedimento_top_share`

### `agg_beneficiario_competencia`  — único (`tenant_id`, `competencia`, `id_beneficiario`)
`despesa` (Σ valor_pago), `despesa_bruta`, `glosas`, `coparticipacao`, `despesa_liquida`
(v1.2), `id_contrato` (v1.2), `eventos`

### `agg_contrato_competencia`  (v1.2) — único (`tenant_id`, `competencia`, `id_contrato`)
`vidas` (beneficiários ativos no mês), `despesa` (Σ valor_pago), `despesa_bruta`, `glosas`,
`coparticipacao`, `despesa_liquida`, `eventos`, `beneficiarios_com_evento`, `custo_pmpm`
(= despesa_liquida/vidas), `gini` e `top5_share` (sobre despesa líquida por beneficiário
do contrato), `n_beneficiarios_alto_custo` (despesa líquida no mês ≥ `ALTO_CUSTO_MES`,
`app/core/thresholds.py`). **Sem receita/sinistralidade próprias.**

### `cenarios_gabarito`
`id`, `codigo` (único, ex. `s1_catarata_freq`), `nome`, `competencia_alvo`, `dimensao`,
`chave_alvo`, `rotulo_alvo`, `efeito_esperado` (frequencia|custo_medio|misto|sazonal|
concentracao|receita), `descricao`, `params` (JSON). Consumido pelos testes de integração.

### `seed_manifest`
`id`, `seed`, `beneficiarios`, `inicio`, `fim`, `escala_eventos`, `criado_em`,
`contagens` (JSON: eventos, competências, cenários, contagens das `agg_*`).

## Configuração (v1.1)

### `regras_alerta`
`id`, `nome`, `entidade` (beneficiario|prestador|procedimento|plano|contrato|financeiro),
`indicador` (chave do catálogo fechado — `app/analytics/indicadores.py`), `operador`
(>=|>|<=|<|==), `limite`, `severidade` (critica|atencao|informativo), `escopo` (JSON,
opcional), `ativo`, `criado_em`, `atualizado_em`. Único dado **configurável pelo
usuário** no produto — preservado entre reseeds da massa sintética (o seed só insere 3
regras de exemplo se a tabela estiver vazia). ⚠️ Endpoints de escrita sem autenticação —
ver [V1.1.md](V1.1.md) § Dívida Técnica.

## Faixas etárias (bandas de saúde)

`0-1, 2-4, 5-9, 10-14, 15-19, 20-29, 30-39, 40-49, 50-59, 60-69, 70-79, 80+`
(`app/core/faixas.py`). Gravada em `beneficiarios.faixa_etaria` no momento do seed
(não recalculada por competência no MVP — aproximação documentada).

## Notas de precisão

Valores monetários: `Numeric(14,2)` no banco; o motor converte para `float` na leitura
(aceitável para a demonstração). A soma dos efeitos do bridge de Bennet é exata em
aritmética real; arredondamentos de exibição usam 2 casas.
