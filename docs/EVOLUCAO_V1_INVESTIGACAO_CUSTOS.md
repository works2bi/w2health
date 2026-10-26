# W2Health — Evolução V1: Investigação Inteligente de Custos e Explicabilidade Avançada

> **Status:** Etapas A–E concluídas em 23/10/2026 — relatório final na seção 9. Nada foi
> enviado ao repositório remoto.
> Data do diagnóstico: 2026-10-23 · Base auditada: `b98b05c` (Contrato × plano N:N).
> Origem: feedback de um diretor financeiro de operadora (ver seção 1).
> Documento anterior sobre o mesmo tema (preservado): [EVOLUCAO_FEEDBACK_ESPECIALISTA.md](EVOLUCAO_FEEDBACK_ESPECIALISTA.md).

---

## 1. O pedido, em uma frase

Ferramentas de mercado já mostram **desvio** de custo por prestador, percentual e
comparação histórica. O diferencial pedido é responder **por que** o custo mudou —
demonstrando matematicamente quanto veio de **utilização**, **custo médio**, **mix de
procedimentos**, **mudança de rede/hospital**, **perfil da população** e **preço
contratual**, e separando o que está **comprovado** do que ainda **precisa ser
investigado**.

---

## 2. Inventário — o que JÁ EXISTE (e será reutilizado, não reescrito)

A auditoria leu documentação, modelos, migrations, contratos de dados, mappings, Silver,
Gold, Serving, motor analítico, rotas e frontend. Várias capacidades pedidas já existem
com outro nome:

| Capacidade pedida | Já existe como | Onde | Avaliação |
|---|---|---|---|
| Quanto a despesa variou (R$ e %) | `explicar()` → `variacao_despesa`; `sinistralidade.indicador` | `analytics/decomposition.py`, `sinistralidade.py` | ✅ reutilizar |
| Efeito despesa × efeito receita (p.p.) | `decomposicao_sinistralidade` (identidade exata) | `formulas.py` | ✅ reutilizar |
| Bruta → glosa → coparticipação → líquida | `decomposicao_financeira` (4 efeitos, exata) | `formulas.py`, `sinistralidade.composicao` | ✅ reutilizar como camada financeira |
| Frequência × custo médio | Bridge de **Bennet** (padrão) e Laspeyres | `formulas.bennet_bridge` | ✅ é o caso de 2 fatores da nova decomposição (ver §5.2) |
| Separar "mais procedimentos" de "procedimento mais caro" | `bridge_composto` (soma dos bridges por procedimento) | `formulas.py` | ⚠️ **absorve o mix dentro de "frequência"** — não o reporta separado (lacuna L1) |
| Contribuição por dimensão (10 dimensões) | `contribuicoes` + proteção contra cancelamento | `formulas.py`, `agg_competencia_dimensao` | ✅ reutilizar |
| Quais prestadores contribuíram | `drill()` → `prestadores_maior_contribuicao_variacao`; `providers.ranking_variacao` | `decomposition.py`, `providers.py` | ✅ reutilizar |
| Prestador fora do padrão vs pares | z-score vs pares da mesma especialidade principal | `providers.py` | ✅ (compara por especialidade, não por procedimento — lacuna L6) |
| Sazonalidade | `seasonality.classificar` (média móvel 12m × fator sazonal, 2σ) | `seasonality.py` | ✅ reutilizar no baseline |
| Coortes de beneficiários (novos / recorrentes / deixaram de usar) | `cohorts.analisar_causas` com identidade exata e FATO/HIPÓTESE/A_INVESTIGAR | `cohorts.py` | ✅ reutilizar |
| Rótulos FATO / HIPÓTESE / A INVESTIGAR | `Evidencia(tipo_evidencia, nivel_confianca)` | `cohorts.py`, `CausasPanel.tsx` | ✅ padronizar no novo motor |
| Concentração (top-k, Gini, Pareto) | `concentracao`, `concentracao_variacao_beneficiarios` | `formulas.py`, `decomposition.py` | ✅ reutilizar |
| Recorrência / novo caso de alto custo / evento pontual | descritores comportamentais (sem score clínico) | `beneficiaries.py` | ✅ reutilizar |
| Escopo de contrato | `contrato_id` em `explicar`/`drill`/`causas` | `decomposition.py`, `analytics_repo.py` | ✅ reutilizar |
| Tipo de evento assistencial | `eventos_assistenciais.tipo_atendimento` (7 valores) | `models/events.py`, contrato canônico | ✅ **é** o tipo de evento — não criar entidade nova |
| Tipo de prestador | `prestadores.tipo_prestador` (hospital, clínica, laboratório, PA, consultório) | `models/catalog.py` | ✅ existe; opcional na origem |
| Transparência de KPI / procedência | `transparency.KPIS` + `procedencia()` | `transparency.py`, `KpiInfo.tsx` | ✅ reutilizar |
| Feature gating / readiness / auditoria / sanitização | `analytics_guard`, `require_feature`, `readiness`, `audit.data_access`, `feature_filters.sanitize` | `api/`, `saas/`, `data_platform/readiness.py` | ✅ toda rota nova passa pelos mesmos guardas |

**Conclusão do inventário:** o produto já explica *o quê* mudou (dimensão, prestador,
beneficiário, glosa/coparticipação) e já separa frequência de custo médio. O que falta é
**um nível estrutural a mais**: isolar mix, rede, intensidade, preço contratual e perfil
populacional numa única identidade que reconcilie, comparar com o histórico e gerar
conclusões rastreáveis.

---

## 3. Lacunas encontradas

| # | Lacuna | Evidência no código | Impacto |
|---|---|---|---|
| **L1** | Mix de procedimentos não é isolado | `bridge_composto` soma Bennet por procedimento: migrar volume para procedimento caro aparece como **frequência** | Cenário C do especialista (mais complexidade com frequência estável) fica indistinguível |
| **L2** | Mix de prestadores / rede não é isolado | Nenhum bridge separa "mesmo procedimento em prestador mais caro" de "aumento de preço no mesmo prestador" | Cenário D (migração para hospital caro) aparece como "custo médio" |
| **L3** | Sem histórico de preço contratado | Não há tabela de preço, vigência ou data de reajuste. `prestadores.nivel_preco` e `procedimentos.custo_base` são **parâmetros do gerador**, nulos para fontes externas | Impossível afirmar reajuste; hoje nada afirma — correto, mas não há como comprovar quando existir |
| **L4** | Vínculo contratual não é histórico | `eventos_assistenciais` não guarda contrato/plano; Gold e repositórios fazem `JOIN beneficiarios b … b.id_contrato`. O contrato YAML de evento já **prevê** `id_contrato` "derivado do beneficiário × vigência" | Se um beneficiário mudar de contrato, despesas antigas são **reclassificadas** para o contrato atual |
| **L5** | Estabelecimento / solicitante não modelados | Evento tem só `id_prestador` (executante) | Não dá para separar "onde foi atendido" de "quem executou/solicitou" |
| **L6** | Comparação de prestadores por especialidade, não por procedimento | `providers._zscores` usa pares da mesma `especialidade_principal` | Compara hospital geral com clínica da mesma especialidade sem contextualizar o mix |
| **L7** | Baseline histórico limitado | Só MoM/YoY e sazonalidade da série total; `comparacao=acumulado_12m` cai em `mes_anterior` (`periodo.competencia_comparacao`) | Sem média histórica com nº de competências, desvio e limitação declarados |
| **L8** | Perfil populacional não entra na explicação | Exposição só no total (receitas); faixa etária é dimensão, não fator | Envelhecimento da carteira aparece como "frequência" |
| **L9** | Defasagem de competência não é visível | `data_evento` × `competencia` existem, mas nada mede apresentação tardia | Aumento "aparente" por faturamento atrasado é lido como aumento real |
| **L10** | Base financeira mista em `explicar()` | Topo usa **líquida** (`despesa` é alias de `despesa_liquida`); fatores por dimensão usam **Σ valor_pago** (antes da coparticipação). A soma dos fatores difere de `variacao_despesa` pela Δcoparticipação | Risco de leitura incorreta da reconciliação na tela de Sinistralidade (documentado em DATA_MODEL.md, mas não exibido) |
| **L11** | Condição crônica | Há `id_diagnostico` (CID) nos eventos sintéticos, mas **não há regra clínica validada** | Não classificar crônico (requisito). Só classificação comportamental |
| **L12** | Intensidade por evento | `quantidade` (sessões, diárias) existe e é ignorada na explicação | Mais diárias por internação aparece como "custo médio" |

## 4. Dados disponíveis para separar preço, frequência, intensidade e mix

| Fator | Dado necessário | Disponível? | Observação |
|---|---|---|---|
| Frequência | nº de eventos, exposição (beneficiário-mês) | ✅ eventos; exposição derivável de `beneficiarios` (adesão/saída) | Unidade: **evento** = 1 linha de `eventos_assistenciais` (item de conta) |
| Perfil populacional | faixa etária do beneficiário + exposição por faixa | ✅ `beneficiarios.faixa_etaria` | Faixa fixada na 1ª competência carregada (limitação já existente no Silver) |
| Mix de procedimentos | procedimento por evento | ✅ | Obrigatório no contrato |
| Mix de prestadores / rede | prestador executante por evento | ✅ | Estabelecimento: **novo campo opcional** (L5) |
| Intensidade | `quantidade` por evento | ✅ opcional (default 1) | Sem dado ⇒ efeito = 0, declarado |
| Custo unitário comparável | valor ÷ quantidade no mesmo par procedimento × prestador | ✅ | Só para pares presentes nos dois períodos |
| Preço contratual | tabela de preço com vigência | ❌ → **nova entidade opcional** `preco_contratado` | Sem ela: "aumento de custo médio observado", nunca "reajuste" |
| Vínculo histórico | contrato/plano na data do evento | ⚠️ → colunas novas no evento + entidade opcional `vinculo_beneficiario` | Sem histórico na origem: vínculo do beneficiário **no momento da carga** é congelado no evento |
| Defasagem | `data_evento` vs `competencia` | ✅ | |

## 5. Decisões

### 5.1 Princípios (inegociáveis)

1. **Nada é reescrito.** `explicar`, `drill`, `causas`, `composicao`, Bennet, insights e
   alertas continuam com o mesmo contrato de saída. Tudo novo é aditivo.
2. **Toda decomposição reconcilia** com a variação observada — testado.
3. **Nenhum efeito é inventado:** o que não é identificável vai para "não comparável /
   não isolado", com valor explícito.
4. **Contribuição contábil ≠ causa.** A decomposição é um FATO aritmético; a causa é
   HIPÓTESE até haver evidência (ex.: tabela de preço).
5. **"Reajuste" só com tabela de preço.** Sem ela: "aumento de custo unitário observado".
6. **Sem inferência clínica.** Coortes são comportamentais (recorrência, alto custo
   histórico) — nunca "crônico", "deficitário" ou "sinistralidade individual".
7. **Sem IA generativa** nas explicações — texto é template preenchido por números.
8. **Canônico independente de ERP** — entidades novas são opcionais e genéricas.

### 5.2 Método de decomposição (resumo — detalhe em ANALYTICS_ENGINE.md §13)

Decomposição estrutural em **dois estágios**, ambos com identidade exata:

**Estágio 1 — procedimento** (procedimentos presentes nos dois períodos):

```
D = E · f · Σ_p s_p · P̄_p
    E   = exposição (beneficiários-mês)          → efeito POPULAÇÃO
    f   = eventos por beneficiário-mês           → efeito FREQUÊNCIA (subdividido em PERFIL ETÁRIO × TAXA)
    s_p = participação do procedimento p nos eventos → efeito MIX DE PROCEDIMENTOS
    P̄_p = custo médio do procedimento p          → (vai para o estágio 2)
```

A atribuição entre fatores usa **valores de Shapley** (média sobre todas as ordens de
substituição) — simétrica, sem resíduo e **igual ao Bennet quando há 2 fatores**, o que
mantém a coerência com a metodologia já aprovada.

**Estágio 2 — dentro de cada procedimento** (linear, exato): a variação do custo médio
P̄_p se divide em **mix de prestadores** (rede) e **custo comparável no mesmo
prestador**; este, em **intensidade** (quantidade/evento) e **custo por unidade**; este,
quando há tabela de preço, em **preço contratual** e **custo unitário não explicado por
tabela**.

**Não comparável:** procedimentos que só existem em um dos períodos entram em
"não comparável" (valor explícito). **Camada financeira:** a decomposição assistencial é
feita sobre a despesa **bruta (apresentada)**; glosa e coparticipação são somadas como
efeitos lineares até a **líquida** — mantendo a separação exigida.

```
Δ despesa líquida = população + perfil etário + frequência + mix procedimentos
                  + mix prestadores + intensidade + preço contratual
                  + custo unitário (sem evidência de tabela) + não comparável
                  − Δ glosa − Δ coparticipação                      (exato)
```

### 5.3 Modelo canônico — extensões (todas opcionais, aditivas)

| Extensão | Decisão | Motivo |
|---|---|---|
| `eventos_assistenciais.id_contrato`, `id_plano` | **Implementar** (nullable + backfill) | L4 — congela o vínculo da competência; Gold usa `COALESCE(evento, beneficiário)` |
| `beneficiario_vinculos` (entidade `vinculo_beneficiario`) | **Implementar** | Histórico de vínculo quando a origem tiver; resolve o contrato do evento pela data |
| `precos_contratados` (entidade `preco_contratado`) | **Implementar** | L3 — única forma segura de afirmar reajuste |
| `eventos_assistenciais.id_estabelecimento`, `id_prestador_solicitante` | **Implementar** (nullable) | L5 — análise por estabelecimento quando houver dado |
| Tipo de evento assistencial | **Não criar** | Já é `tipo_atendimento` |
| Tipo de prestador | **Não criar** | Já é `prestadores.tipo_prestador`; vocabulário mantido |
| Coortes analíticas | **Não persistir** | Calculadas sob demanda a partir do histórico (como as coortes da v1.1) |
| Empresa (agrupador de contratos / nível de faturamento) | **Pendência de negócio** — não implementado | O nível em que a receita é faturada (empresa × contrato) muda a reconciliação da receita; sem regra validada, implementar só o agrupamento criaria uma entidade sem uso analítico. Proposta registrada em §8 |
| Condição crônica | **Não implementar** | L11 — exige CID + regra clínica validada e versionada (§8) |

### 5.4 Produto

- Uma jornada só, **dentro das telas existentes**: seção **"Investigação da variação"**
  em `/sinistralidade` (escopo operadora, contrato ou fator selecionado) e em
  `/contratos/[id]`; histórico comparável no detalhe do prestador. Nenhum dashboard novo.
- Ordem: **Indicador → Variação → Decomposição → Contribuições → Evidências → Investigação**.
- Gating: rota sob `loss_ratio_intelligence` + `advanced_explanations`; dimensões
  `prestador`/`contrato` exigem as features próprias; amostras de beneficiário saem pelo
  `sanitize` e o acesso é auditado — exatamente o padrão das coortes.

### 5.5 Dados sintéticos

Os 19 cenários atuais da massa "Vida Plena" têm testes que dependem dos totais mensais;
injetar 10 cenários novos na mesma carteira mudaria esses totais. Decisão: um
**laboratório de cenários** determinístico em um tenant próprio (`w2h-cenarios`), um
**contrato por cenário**, com preços fixos e contagens controladas para que o resultado
esperado seja conhecido. Na massa "Vida Plena" entram apenas **tabelas de preço** (sem
alterar nenhum evento) — o cenário `s7` (ressonância +30% sem mudança de tabela) passa a
demonstrar "custo subiu **sem** evidência de reajuste".

## 6. Riscos de regressão e mitigação

| Risco | Mitigação |
|---|---|
| Trocar o JOIN de contrato/plano para o evento mudar números existentes | Backfill a partir do beneficiário + `COALESCE`; na massa sintética os valores são idênticos; suíte completa antes/depois |
| Migration em tabela grande (`eventos_assistenciais`) | Colunas **nullable** (sem rewrite), backfill em `UPDATE … FROM`; downgrade remove só o que criou |
| Novas tabelas sem RLS | Mesma política `tenant_isolation`, grants app=SELECT / pipeline=CRUD; `test_rls` cobre todo o data plane automaticamente |
| Vazamento de beneficiário pela nova rota | Amostras sob chaves já restritas no `sanitize`; auditoria de acesso; teste com tenant sem a feature |
| Performance (decomposição sob demanda) | Consultas agregadas por (procedimento × prestador) no período — mesmas ordens de grandeza do `drill`; janela limitada a 12 meses |
| Leitura causal indevida | Texto padronizado: "contribuição", nunca "causa"; HIPÓTESE sempre acompanhada de "Investigar" |

## 7. Plano de implementação

| Etapa | Entregas |
|---|---|
| **B — Modelo** | Migration aditiva; modelos `PrecoContratado`, `BeneficiarioVinculo`; colunas no evento; contratos YAML, canônico, Silver, DQ, mapping template, dicionário; RLS/grants |
| **C — Analytics** | `decomposicao_estrutural.py` (puro), `baseline.py`, `investigacao.py` (orquestrador + motor de conclusões), `investigacao_repo.py`; reconciliação de base em `explicar` (aditivo); histórico no detalhe do prestador |
| **D — Produto** | Rota `GET /analytics/investigacao`; `InvestigacaoPanel` em Sinistralidade e Contrato; histórico no prestador |
| **E — Validação** | Testes matemáticos (cenários com resultado conhecido), laboratório de 10 cenários, regressão completa, isolamento entre tenants, documentação |

## 8. Pendências de negócio (não implementadas por segurança)

1. **Empresa / nível de faturamento.** Proposta: entidade opcional `empresa` (código,
   nome) e `contrato.empresa_codigo`. Decidir: a receita é faturada por empresa, por
   contrato ou por ambos? Até lá, a receita segue no grão competência × plano.
2. **Condição crônica.** Exige lista de CIDs validada por equipe médica, versionada, com
   regra de persistência (ex.: ≥ 2 eventos com CID da lista em 12 meses) e revisão de
   LGPD (dado de saúde sensível). Até lá: apenas "utilização recorrente".
3. **Competência de atendimento × de pagamento.** Já é decisão de onboarding por cliente;
   a lente de defasagem (§5.2) mostra o efeito, mas não muda a regra.
4. **Receita por contrato.** `receitas_contrato` segue só como layout preparado.

## 9. Relatório final de implementação

### 9.1 Reutilizado (sem reescrita)
Bennet (é o caso de 2 fatores do novo método), `decomposicao_financeira` (camada glosa/
coparticipação), `contribuicoes` (regra de cancelamento), `seasonality.classificar`,
`formulas.concentracao`, coortes da v1.1 (`CausasPanel` continua no drill), `transparency`,
guardas de feature/RBAC/auditoria/`sanitize`, escopo de contrato, Gold e repositórios.

### 9.2 Novo
| Camada | Entrega |
|---|---|
| Motor | `analytics/decomposicao_estrutural.py` (Shapley + divisão linear exata, 11 efeitos), `analytics/baseline.py`, `analytics/investigacao.py` (rede, coortes comportamentais, defasagem, preços, conclusões FATO/HIPÓTESE/INVESTIGAR), `repositories/investigacao_repo.py` |
| Aditivo em existentes | `explicar` → `reconciliacao_base` (L10); detalhe do prestador → `historico` e `custo_vs_pares_por_procedimento` (L6) |
| API | `GET /api/analytics/investigacao` (`competencia`, `comparacao` mes_anterior/periodo_anterior/ano_anterior, `meses` 1/3/6/12, `dimensao`+`chave`, `contrato_id`, `nivel_rede` executante/estabelecimento) |
| Frontend | `components/InvestigacaoPanel.tsx` em `/sinistralidade` (escopo operadora/contrato e fator do drill) e `/contratos/[id]`; cards de histórico e pares em `/prestadores/[id]`. Nenhuma rota nova |
| Dados sintéticos | tabelas de preço e vínculo nos eventos da massa Vida Plena (nenhum evento alterado); laboratório `w2h-cenarios` com 10 cenários (`app/seed/cenarios_investigacao.py`) |

### 9.3 Modelo canônico e migration
Migration aditiva **`d0e1f2a3b4c5`** (testada: upgrade do zero, downgrade −1, upgrade,
`alembic check` sem divergência):
- `eventos_assistenciais`: `id_contrato`, `id_plano` (vínculo da competência, backfill pelo
  beneficiário), `id_estabelecimento`, `id_prestador_solicitante` — nullable;
- `beneficiario_vinculos` e `precos_contratados` — RLS `tenant_isolation`, app = SELECT,
  pipeline = CRUD.

Contratos YAML (`vinculo_beneficiario`, `preco_contratado`, campos novos do evento), contrato
operacional, Silver (resolução do contrato pela data do evento), DQ (`vigencia_invertida`,
`vigencia_sobreposta`, `preco_nao_positivo`), mapping genérico (arquivos opcionais
`vinculos.csv`, `precos.csv`), dicionário regenerado. Gold e repositórios usam
`COALESCE(evento, beneficiário)` para contrato/plano. Divergência D6 do modelo canônico resolvida.

### 9.4 Cenários validados (laboratório, resultado exato)
Frequência (+40%), custo médio sem tabela (+15%, sem afirmar reajuste), migração para
hospital caro (R$ 44.100 em mix de rede), mix de procedimentos (R$ 212.256), concentração
em recorrentes (≥ 90%), perfil etário (população + perfil = Δ, frequência 0), reajuste com
tabela (R$ 18.350, 4 vigências +10%), defasagem (100% da Δ), redução (−40%), dados
insuficientes (sem hipóteses). Na massa Vida Plena: s1 → frequência; s7 → custo unitário
**sem** reajuste (tabela constante).

### 9.5 Testes
| | Antes | Depois |
|---|---|---|
| Coletados | 431 | 517 (+86: 34 matemáticos, 22 laboratório, 22 investigação/API/isolamento, 4 pipeline, 4 DQ) |
| Resultado | 1 falha (`test_worker::test_cargas_concorrentes…`, timeout) | 501 ok · 15 skipped · 1 falha — a mesma, pré-existente; **passa isolada** (`test_worker.py` + `test_object_storage.py`: 49 ok). Os 15 skipped são o adapter S3 sem `S3_TEST_ENDPOINT` |

Frontend: `tsc`, ESLint e `next build` sem erros. Testes existentes ajustados apenas na
infraestrutura: `test_rls` (inclui a nova migration na lista de tabelas com RLS) e
`test_data_platform_unit` (entidades opcionais no mapping genérico).

### 9.6 Evidências de reconciliação
Toda resposta traz `decomposicao.reconciliacao` (Σ efeitos = Δ líquida). Testado: 12 bases
aleatórias; operadora contra `agg_sinistralidade_competencia` em 5 combinações de
competência/janela/comparação (incl. 12 meses vs ano anterior); contrato contra
`agg_contrato_competencia`; fator contra `agg_competencia_dimensao`; coortes somam a Δ.

### 9.7 Limitações conhecidas
- ~~Exposição por contrato usa o vínculo **atual** do beneficiário~~ — resolvido na
  Consolidação do canônico V1 (vínculo vigente; ver CONSOLIDACAO_CANONICO_V1.md).
- Faixa etária fixada na 1ª competência carregada (D12).
- ~~Sobreposição de vigências só dentro do lote~~ — resolvido na Consolidação V1 (também
  contra o já carregado).
- Pares procedimento × prestador esparsos (dados reais) aumentam "não comparável" e reduzem
  "custo comparável" — o valor fica explícito, nunca redistribuído.
- Coortes exigem ≥ 3 competências de histórico anteriores à base; nível de rede
  "solicitante" não é decomposto (só cobertura).
- Investigação é calculada sob demanda (sem cache); janelas de 12 meses na operadora inteira
  são as consultas mais pesadas.

### 9.8 Pendências para dados reais
Tabela de preço com vigência por prestador × procedimento (sem ela não há "reajuste");
histórico de movimentação cadastral (vínculos); estabelecimento/solicitante na conta; decisão
de competência (atendimento × pagamento); e as pendências de negócio do §8 (empresa,
condição crônica, receita por contrato).

### 9.9 Riscos de implantação
Backfill de `id_contrato`/`id_plano` é um `UPDATE` na tabela de eventos (rodar em janela de
manutenção em bases grandes); o seed fica mais lento pelo mesmo `UPDATE`; leitores precisam
entender "contribuição" ≠ "causa" (texto da UI e do §13 de ANALYTICS_ENGINE.md).

### 9.10 Estado da V1
Investigação de custos implementada de ponta a ponta (modelo → pipeline → motor → API → UI)
com reconciliação exata, sem regressão de segurança/isolamento detectada e com as
pendências de negócio registradas. Pronta para revisão; não houve commit nem push.
