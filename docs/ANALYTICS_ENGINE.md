# Motor analítico — W2Health Intelligence

Implementação: `backend/app/analytics/`. Primitivas puras e testáveis em `formulas.py`
(`tests/test_formulas.py`). **Toda conclusão da plataforma nasce de uma destas funções.**

Notação: `t` = mês selecionado, `0` = mês de comparação (anterior MoM, ou −12 YoY).
`D` = despesa (Σ valor_pago), `R` = receita, `N` = nº de eventos, `P = D/N` = custo médio.

## 1. Sinistralidade e séries

- `sinistralidade(D, R) = D/R × 100` (0 se `R ≤ 0`).
- `variacao_pp(a, b) = a − b` — **pontos percentuais**.
- `variacao_pct(a, b) = (a−b)/b × 100` (`None` se `b ≈ 0`).
- `acumulado_12m` = `Σ D(12) / Σ R(12) × 100`.
- Série mensal com `variacao_pp` MoM e YoY e `acumulado_12m` (`sinistralidade.serie`).

## 2. Decomposição numerador × denominador (`decomposicao_sinistralidade`)

Identidade **exata** (soma = ΔS):

```
efeito_despesa_pp = ΔD / R0 × 100
efeito_receita_pp = (D1/R1 − D1/R0) × 100
ΔS = efeito_despesa_pp + efeito_receita_pp
```

Responde "a sinistralidade subiu por gastar mais ou por arrecadar (relativamente) menos?".

## 3. Contribuição por dimensão (`contribuicoes`)

Para categorias `i` de uma dimensão: `contribuição_i = D_{i,1} − D_{i,0}`.
`participação_i = contribuição_i / ΔD_total × 100`.
**Proteção contra cancelamento**: se `|ΔD_total| < 15% · Σ|Δ|`, o denominador vira `Σ|Δ|`
(evita participações explosivas quando o líquido é pequeno mas os componentes são grandes).
Categorias novas/extintas entram naturalmente (valor 0 no outro mês).

## 4. Bridge frequência × custo médio

### Bennet (padrão — decisão aprovada, sem resíduo)

```
efeito_frequencia = (N1 − N0) · (P0 + P1) / 2
efeito_custo_medio = (P1 − P0) · (N0 + N1) / 2
efeito_frequencia + efeito_custo_medio = N1·P1 − N0·P0     (exato)
```

### Laspeyres (alternativa — `?metodo=laspeyres`)

```
efeito_frequencia = (N1 − N0) · P0
efeito_custo_medio = (P1 − P0) · N0
interacao = (N1 − N0) · (P1 − P0)          (reportada à parte)
```

### Efeito principal (`classificar_efeito`, limiar 0,65)

`|efeito_freq| / (|efeito_freq|+|efeito_custo|) ≥ 0,65` → `"frequencia"`; simétrico para
`"custo_medio"`; senão `"misto"`.

### Bridge de fator coeso (`bridge_composto`)

Para **especialidade / grupo de despesa / procedimento**, o bridge do fator é a **soma dos
bridges por procedimento** que o compõem. Isso separa corretamente *"mais procedimentos"*
(frequência, no grão do procedimento) de *"procedimento mais caro"* (custo médio) e absorve
o efeito de **mix** — sem esse cuidado, ao agregar, uma mudança de composição para
procedimentos caros apareceria como "custo médio". Demais dimensões usam o bridge por
contagem de eventos.

## 5. Concentração (`concentracao`, `gini`)

- `top_k_share[k]` = Σ dos `k` maiores / total.
- Ponto de Pareto = menor `k` com share acumulado ≥ 80%; `pareto_frac = k/n`.
- Gini via curva de Lorenz sobre a lista de valores (despesa por beneficiário ou por
  prestador). 0 = igualdade, →1 = concentração máxima.
- Frase automática: *"5% dos beneficiários concentraram X% da despesa do período."*

## 6. Anomalia de prestador (`providers`)

Grupo de pares = prestadores com a **mesma especialidade principal** no mês.
z-score de: `custo_medio`, `eventos_por_beneficiario`, `concentracao_procedimento`
(`procedimento_top_share`). **Fora do padrão** se `|z| ≥ 3` em 1 métrica **ou** `|z| ≥ 2`
em ≥ 2 métricas. `anomalia_prestadores` varre todos e ordena pelo maior `|z|`.
`ranking_variacao` ordena prestadores por `ΔD = D_prestador(t) − D_prestador(0)`.

## 7. Sazonalidade (`seasonality.classificar`)

Com ≥ 12 meses de histórico: `esperado_t = média_móvel_12m × fator_sazonal(mês)`, onde
`fator_sazonal(m) = média histórica do mês m / média geral`. Classifica pelo resíduo
padronizado `(real − esperado) / σ(resíduos históricos)`:

- `|z| ≤ 2` e `|fator_sazonal − 1| ≥ 0,15` → **`"sazonal"`** (variação esperada do mês);
- `|z| ≤ 2` e fator ~1 → `"normal"`;
- `|z| > 2` → **`"anomalo"`**.

Assim um pico de inverno que **se repete** nos dois anos é classificado como sazonal, não
anômalo.

## 8. Motor de insights (`insights.gerar`)

Percorre os resultados acima e emite insights tipados:

| tipo | dispara quando |
|---|---|
| `variacao_sinistralidade` | sempre (severidade por magnitude da ΔS) |
| `efeito_receita` | `|efeito_receita_pp| ≥ 1` e ≥ 35% da ΔS |
| `fator_dominante_especialidade` | fator de especialidade com maior `|impacto p.p.|` (+ bridge) |
| `fator_dominante_grupo` | idem por grupo de despesa |
| `internacoes` | `|Δfrequência de internações|` ≥ 8% |
| `concentracao_prestadores` | 3 maiores respondem por ≥ 25% do aumento |
| `prestador_anomalo` | há prestador fora do padrão |
| `concentracao_beneficiarios` | share dos 5% maiores (+ Gini + Pareto) |
| `reducao_despesa` | maior fator negativo (severidade "positiva") |
| `sazonalidade` | pneumologia classificada como sazonal/anômala |

Cada insight: `{severidade, emoji, titulo, descricao, metricas, deep_link {rota, params},
score, metodologia}`. **`score`** ≈ `|impacto p.p.| × pesos + |z| + share` — ordena o feed.
Nenhuma frase é fixa: os números vêm dos agregados e o texto é template preenchido; se o
banco muda, o insight muda.

## Rastreabilidade

- Toda resposta de explicação inclui o campo **`metodologia`** (a fórmula usada).
- O drill-down retorna `metodologia` = o próprio objeto `bridge` do fator.
- No frontend, cada `InsightCard` tem **"Como calculamos"** com a metodologia + as métricas.

---

## 9. Análise de coortes — "o porquê do porquê" (v1.1, Etapa A)

Módulo: `app/analytics/cohorts.py`. Um nível abaixo do bridge: para qualquer
`(dimensão, chave)`, classifica os beneficiários da célula em coortes com **identidade
exata** (sem resíduo):

```
Δdespesa = Σ(novos_carteira + novos_categoria)·despesa_atual
         + Σ(recorrentes)·Δdespesa
         − Σ(saída_carteira + permaneceram_sem_evento)·despesa_anterior
```

Cada achado carrega `tipo_evidencia` (`FATO`|`HIPOTESE`|`A_INVESTIGAR`) e
`nivel_confianca` (`ALTA`|`MEDIA`|`BAIXA`). Regra de ouro: **nunca inventar
causalidade**.

| Achado | Classificação | Regra |
|---|---|---|
| Contagens/somas diretas (quem parou de usar, quem saiu da carteira, quem trocou de prestador) | `FATO` / `ALTA` | Sempre — são dados observados |
| "Permaneceram sem evento" **e** ≥60% da despesa anterior é de procedimentos `perfil_utilizacao='pontual'` | `HIPOTESE` / `MEDIA` | Padrão compatível com conclusão de episódio pontual — nunca afirmado como certeza |
| "Permaneceram sem evento" sem esse padrão | `A_INVESTIGAR` / `BAIXA` | Sem evidência suficiente para qualquer hipótese |

`procedimentos.perfil_utilizacao` (pontual|recorrente|variavel) é mapeado por
`grupo_procedimento` no seed — só *apoia* a hipótese, nunca a prova sozinho.

Endpoint: `GET /analytics/sinistralidade/explain/{dimensao}/{chave}/causas`.

## 10. Composição financeira: bruta × líquida (v1.1, Etapa B)

Módulo: `formulas.decomposicao_financeira` + `sinistralidade.composicao`.

```
despesa_liquida = despesa_bruta − glosas − coparticipacao
sinistralidade_x = despesa_x / receita × 100          (x = bruta | liquida)
```

Decomposição de 4 efeitos — **exata**, porque despesa líquida é combinação **linear**
de bruta/glosa/coparticipação (sem termo de interação, ao contrário do bridge
frequência×custo que é multiplicativo):

```
efeito_bruta   =  ΔBruta  / R0 · 100
efeito_glosa   = −ΔGlosa  / R0 · 100
efeito_copart  = −ΔCopart / R0 · 100
efeito_receita = (Dliq1/R1 − Dliq1/R0) · 100
Σ = ΔS_líquida   (sempre exato)
```

**Convenção do MVP**: o KPI "Sinistralidade" (em toda a API/frontend) usa a base
**líquida**; a base bruta é sempre exibida ao lado, nunca omitida. Endpoint:
`GET /analytics/sinistralidade/composicao`.

## 11. Insight × Alerta (v1.1, Etapa C)

Dois módulos irmãos, nunca fundidos:

| | `insights.py` | `alerts.py` |
|---|---|---|
| Natureza | Achado automático do motor | Regra definida pelo gestor (`regras_alerta`) |
| Configurável | Não | Sim — CRUD em `/api/config/regras-alerta` |
| Catálogo | Regras fixas no código | **Catálogo fechado** de indicadores (`indicadores.py`) — `entidade × indicador × operador × limite`, sem linguagem de expressões livre |

`avaliar_regras()` calcula o indicador de verdade e só emite alerta quando o valor
cruza o limite — nunca um alerta fabricado. Indicadores cobrem beneficiário, prestador,
procedimento, plano e financeiro (glosa/coparticipação); `contrato` fica limitado a
despesa/participação/vidas (sinistralidade por contrato exige receita própria, fora do
escopo sem o módulo de reajuste — ver `EVOLUCAO_FEEDBACK_ESPECIALISTA.md`).
**v1.2**: `avaliar_regras()` faz `session.rollback()` ao pular uma regra cujo indicador
falha (uma regra malformada não polui a transação das demais).

---

## 12. v1.2 — concentração no topo, escopo de contrato, composição por dimensão

Ver [V1.2.md](V1.2.md). Resumo do que mudou no motor:

- **C1 — `decomposition.concentracao_variacao_beneficiarios(...)`**: Δdespesa líquida por
  beneficiário (mês − comparação) sobre `agg_beneficiario_competencia.despesa_liquida`;
  top-k share + Gini sobre os aumentos + "nº para crédito de 50%". Entra no retorno de
  `explicar()` (bloco `concentracao_variacao_beneficiarios`), vira insight e endpoint.
- **C2 — composição propagada**: `formulas.decomposicao_financeira` (já existente) agora
  alimenta também `sinistralidade.composicao(dimensao=, chave=)` e `(contrato_id=)` — sem
  receita nesses escopos ⇒ `sinistralidade_*`/`decomposicao` = `None` + `aviso`. Identidade
  `bruta − glosas − copart = líquida` vale em todas as `agg_*`.
- **C3 — `contrato_id` em `explicar` / `drill` / `cohorts.analisar_causas`**: quando
  setado, os agregados por dimensão vêm de `eventos_assistenciais` sob demanda
  (`analytics_repo.dimensao_mes_por_contrato` / `sinistralidade_por_contrato_mes`);
  `impacto_pp`/`sinistralidade`/`efeito_receita` retornam `None`. `dimensao='contrato'` é
  rejeitada nesse escopo.
- **C4 — `app/analytics/contratos.py`**: `listar()` / `detalhe()` sobre
  `agg_contrato_competencia`; concentração via `formulas.concentracao` sobre despesa
  líquida por beneficiário do contrato; drivers = `explicar(..., contrato_id=)`.
- **C5 — `beneficiaries.py`**: `recorrencia`, `novos_casos_alto_custo`,
  `eventos_pontuais_alto_custo`, `participacao_variacao` — descritores do **observado**,
  nunca score clínico nem previsão.
- **C6 — `indicadores.py`**: `contrato/concentracao`, `contrato/n_beneficiarios_alto_custo`,
  `beneficiario/novo_caso_alto_custo`, `beneficiario/recorrencia`. `alerts` deep-link
  `contrato → /contratos/{id}`.

---

## 13. Evolução V1 — investigação de custos: decomposição estrutural

Diagnóstico e decisões: [EVOLUCAO_V1_INVESTIGACAO_CUSTOS.md](EVOLUCAO_V1_INVESTIGACAO_CUSTOS.md).
Módulos: `decomposicao_estrutural.py` (puro), `baseline.py` (puro), `investigacao.py`
(orquestrador + conclusões), `repositories/investigacao_repo.py`. Rota:
`GET /analytics/investigacao`. Nada do que existia mudou de comportamento: Bennet,
`bridge_composto`, `explicar`, `drill`, coortes e composição seguem iguais (`explicar`
ganhou só o bloco aditivo `reconciliacao_base`, ver 13.9).

### 13.1 Pergunta que o método responde

> "A despesa líquida mudou R$ X. Quanto disso veio de mais gente, de gente mais velha, de
> mais uso por pessoa, de procedimentos mais caros, de prestadores mais caros, de mais
> diárias/sessões por atendimento, de reajuste de tabela, de aumento de preço sem tabela,
> de procedimentos que não existiam antes, de glosa e de coparticipação?"

Cada real da variação vai para **um** efeito (sem dupla contagem) e a soma dos efeitos é a
variação observada (reconciliação exata, verificada em toda resposta).

### 13.2 Estrutura

Despesa **bruta** (Σ valor_apresentado) dos procedimentos presentes nos dois períodos:

```
D = E · f · Σ_p s_p · P̄_p
P̄_p = Σ_h w_h · P_h        P_h = k_h · u_h        u_h = T_h · ρ_h (quando há tabela)

E    exposição: beneficiários ativos no 1º dia de cada competência (beneficiário-mês)
f    eventos por beneficiário-mês = Σ_a e_a · r_a   (a = faixa etária)
s_p  participação do procedimento p no nº de eventos
P̄_p  custo médio do procedimento p
w_h  participação do prestador h no volume do procedimento
k_h  quantidade por evento (sessões, diárias, itens)
u_h  valor por unidade
T_h  preço contratado vigente (tabela);  ρ_h = u_h / T_h
```

### 13.3 Atribuição

1. **Estágio 1 — Shapley** entre E, f, s e P̄, termo a termo por procedimento (o valor de
   Shapley de uma soma é a soma dos valores de Shapley). Como cada termo é um produto, o
   efeito de cada fator é `Δx · m_x`, em que `m_x` é a média, sobre todas as ordens de
   substituição, do produto dos demais fatores. **Com dois fatores é exatamente o Bennet**
   (§4): a metodologia já aprovada é um caso particular desta.
2. **Perfil etário × taxa:** `Δf = Σ_a Δe_a · r̄_a` (perfil) `+ resto` (taxa por faixa) — exato.
3. **Estágio 2 — linear**, dentro de cada procedimento (o efeito de P̄ é `Q̄_p · ΔP̄_p`):
   - **mix de prestadores** = `Σ_h Δw_h · (P_h,ref − P̄_ref)` — inclui prestadores que
     entraram (ao seu preço) e que saíram; perder volume de um prestador barato também
     encarece;
   - **custo comparável** (mesmo procedimento e mesmo prestador nos dois períodos) =
     `Σ_h ω_h · ΔP_h`, dividido em **intensidade** (`Δk · ū`) e **custo por unidade**
     (`Δu · k̄`); este, quando há tabela vigente nos dois períodos, em **preço contratual**
     (`ΔT · ρ̄`) e **custo unitário não explicado pela tabela** (`Δρ · T̄`).
4. **Não comparável:** procedimentos presentes em só um período — valor explícito, nunca
   redistribuído entre os demais efeitos.
5. **Camada financeira:** `− Δglosa − Δcoparticipação` (linear) leva da bruta à líquida.

```
Δ líquida = população + perfil etário + frequência + mix de procedimentos
          + mix de prestadores + intensidade + preço contratual
          + custo unitário (sem evidência de tabela) + não comparável
          − Δ glosa − Δ coparticipação                                  (exato)
```

**Unidade de frequência:** evento = 1 linha de `eventos_assistenciais` (item de conta).
Eventos de tipos diferentes só são somados com o mix separado (é o que o efeito "mix de
procedimentos" faz); o volume por tipo de atendimento é exibido à parte, cada tipo na sua
unidade (consultas, internações, sessões...).

**Participação (%):** sobre a variação observada; com muito cancelamento entre efeitos
(|Δ| < 15% de Σ|efeitos|), sobre Σ|efeitos| — a mesma regra de `contribuicoes` (§3).

### 13.4 O que o método NÃO afirma

| Situação | O que aparece |
|---|---|
| Valor por unidade subiu, sem tabela de preço | "Aumento de custo unitário observado" (FATO, quantificado) + HIPÓTESE "reajuste não registrado ou mudança de cobrança" + o que verificar. **Nunca "reajuste".** |
| Tabela com nova vigência | "Reajuste de tabela contratada" (FATO) com prestador, procedimento, valores e data |
| Volume migrou entre prestadores | FATO quantificado + HIPÓTESE de mudança de rede — **o motivo não é presumido** |
| Exposição indisponível no escopo | População e perfil "não calculado" (com motivo); "frequência" passa a medir volume de eventos |
| Procedimentos novos/extintos | "Não comparável" (INVESTIGAR) — não vira preço nem volume |
| < 30 eventos em algum período | Nenhuma HIPÓTESE; INVESTIGAR "dados insuficientes" |

### 13.5 Baseline histórico (`baseline.comparar`)

Valor da janela (média mensal) contra o período anterior de mesmo tamanho, a mesma janela
do ano anterior e a **média das até 12 competências anteriores** (mínimo 6; abaixo disso a
comparação é declarada insuficiente). Desvio padronizado `(atual − média)/(σ/√meses)`;
excepcional se |z| ≥ 2. A sazonalidade (`seasonality.classificar`, §7) só reclassifica um
desvio como "sazonal" com **≥ 24 meses** de histórico — com um único ano o fator do mês é
uma observação, não um padrão. Cada saída traz período de referência, nº de competências,
método e limitações.

### 13.6 Rede, coortes, defasagem

- **Rede:** transferências de participação (≥ 5 p.p.) entre prestadores do mesmo
  procedimento; custo vs **mediana de pares do mesmo procedimento e do mesmo tipo** de
  prestador (≥ 3 pares; razão ≥ 1,25 sinalizada). O detalhe do prestador ganhou o mesmo
  histórico e a mesma comparação por procedimento.
- **Coortes comportamentais** (partição exata da Δ líquida por beneficiário), definidas pelo
  histórico das 6 competências ANTERIORES à base: alto custo histórico (5% maiores, com ≥ 20
  beneficiários), uso recorrente (≥ 4 de 6 meses), esporádico e sem uso prévio. A conclusão
  "recorrentes concentram a variação" exige ≥ 40% da variação, ≥ 1,5× o peso do grupo na
  despesa da base e pouco cancelamento entre coortes. **Não há classificação clínica**:
  condição crônica exige CID + regra validada (pendência de negócio).
- **Defasagem:** partição exata da líquida entre atendimentos do próprio mês da competência
  e atendimentos de meses anteriores (apresentação tardia).

### 13.7 Conclusões (FATO / HIPÓTESE / INVESTIGAR)

Cada conclusão traz indicador, período (base → atual), escopo, valores de referência e de
comparação, contribuição (R$ e %) quando calculável, evidências, limitações, próximo passo
e método. Regras de redação: decomposição = **contribuição** (FATO aritmético), nunca
"causa"; HIPÓTESE sempre com o que verificar; nenhum texto vem de IA generativa.

### 13.8 Testes

- `tests/test_decomposicao_estrutural.py` — cenários matemáticos com resultado conhecido
  (A–D do especialista, preço com/sem tabela, perfil etário, intensidade, prestador novo,
  procedimento novo, camada financeira, 12 bases aleatórias, invariância de ordem).
- `tests/test_cenarios_investigacao.py` — laboratório de 10 cenários (tenant `w2h-cenarios`).
- `tests/test_investigacao.py` — reconciliação com a Gold na massa Vida Plena, cenários
  s1/s7, API, gating, auditoria e isolamento entre tenants com os mesmos códigos.
- `tests/test_pipeline_investigacao.py` — vínculo histórico e tabela de preço pelo
  pipeline real.

### 13.9 `explicar`: base dos fatores declarada

Os fatores de `explicar` somam a variação de **Σ valor_pago** (bruta − glosa) e o topo usa
a despesa **líquida**. A diferença é a Δ coparticipação. O bloco aditivo
`reconciliacao_base` explicita isso (`Σ fatores − Δ coparticipação = Δ líquida`, com `ok`).

---

## 14. Consolidação do canônico V1 — receita, vínculo e escopos

Ver [CONSOLIDACAO_CANONICO_V1.md](CONSOLIDACAO_CANONICO_V1.md) e
[REGRAS_RECEITA_E_FATURAMENTO.md](REGRAS_RECEITA_E_FATURAMENTO.md).

- **Receita da operadora:** uma fonte por competência (`fonte_receita` = reconhecida |
  legada_plano | sem_receita); divergência entre fontes → `receita_status = divergente` e
  `sinistralidade.indicador`/`serie` não publicam a sinistralidade da competência.
- **Vínculo:** contrato/plano do evento = vínculo vigente na data, sem fallback para o
  cadastro atual (chave `nao_atribuido` nas dimensões). Vidas e exposição (inclusive na
  investigação: `investigacao_repo.SQL_EXPOSICAO`) pelo vínculo vigente no 1º dia da
  competência — a limitação "exposição do contrato pelo vínculo atual" da §13 foi resolvida.
- **Sinistralidade contratual / de empresa:** publicada só com receita reconhecida
  identificada (ver REGRAS §6); nunca por rateio.
- **Investigação (`/analytics/investigacao`):** novo escopo `empresa_id` (contratos da
  empresa contratante em cada competência) e bloco `sinistralidade` (contrato, empresa ou
  operadora): `S₀ → S₁`, efeito despesa + efeito receita (identidade exata, §2) e cada efeito
  da decomposição estrutural em p.p. (`efeitos_despesa_pp`, soma = efeito despesa).
  Indisponível → o motivo entra nas limitações; nada é estimado.
