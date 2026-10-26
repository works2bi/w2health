# Dados sintéticos — W2Health Intelligence

Gerador: `backend/app/seed/`. CLI: `python -m app.seed.run`.
**100% fictício.** Operadora **Vida Plena**. Sem nomes, CPF, CNS ou endereços reais.

## Parâmetros (`SeedConfig`)

| Flag | Default | Descrição |
|---|---|---|
| `--seed` | 42 | semente — reprodutibilidade total |
| `--beneficiarios` | 20000 | tamanho da carteira (testado até 100000) |
| `--inicio` / `--fim` | 2025-01 / 2026-12 | janela (24 competências) |
| `--escala` | 1.0 | multiplicador da taxa de eventos |
| `--no-cenarios` | — | gera a base sem os cenários intencionais |

Volume default: ~20 mil beneficiários, ~120 prestadores, 85 procedimentos, 15
especialidades, 12 planos, **48 contratos** (v1.2 — vários por plano, ~60 a ~4.900
vidas-alvo), ~40 diagnósticos, **~330 mil eventos**, 24 meses.
Tempo: ~60–90 s. Grava `seed_manifest` e `cenarios_gabarito`. **v1.2**: toda linha é
carimbada com `SeedConfig.tenant_id` (default `w2h-demo`); `run.py` faz upsert em `tenants`.

`--tenant`? não existe flag — passar `SeedConfig(tenant_id=...)` para gerar um 2º tenant
sintético. A escolha de **plano** do beneficiário NÃO mudou na v1.2 (RNG dedicado
`seed+909` para a escolha de contrato) ⇒ os cenários S1–S13 permanecem byte-idênticos.

## Pipeline (`run.py`)

1. `wipe_dados` — limpa tudo (respeita FKs), preserva `alembic_version`.
2. `load_catalogos` — insere competências, regiões, planos, contratos, especialidades,
   procedimentos, diagnósticos e prestadores (`catalogs.py`, determinístico).
3. `generate_beneficiarios` — pirâmide etária realista, sexo, plano/região por peso,
   `data_adesao` (82% já ativos antes do início), rotatividade ~1%/mês.
4. `build_scenarios` — monta os hooks e o gabarito (se `--cenarios`).
5. `generate_eventos` — mês a mês, **vetorizado com NumPy**:
   - taxa mensal por idade (curva em "U": alta em bebês e idosos) × ruído leve;
   - `Poisson(λ)` por beneficiário ativo;
   - especialidade via **Gumbel-max** com pesos de afinidade idade×sexo (`affinities.py`);
   - procedimento dentro da especialidade (peso de frequência típico + filtro de faixa
     etária — catarata só 45+, pediatria só <13, obstetrícia só mulheres 15–45…);
   - prestador (mesma região pesa 3×);
   - `valor_apresentado = custo_base × nivel_preco_prestador × lognormal(0, 0.16) × qtd`;
     glosa ~ `clip(N(0.03, 0.03), 0, 0.15)`; `valor_pago = apresentado − glosado`;
   - diagnóstico coerente com a especialidade (~62% dos eventos).
6. `generate_receitas` — por competência × plano: `ativos × PMPM_alvo × ticket_relativo ×
   reajuste_acumulado(maio) × ruído`. O `PMPM_alvo` é **calibrado** para a sinistralidade
   média-alvo do baseline (`sinistralidade_alvo = 0.75`).
7. `rebuild_aggregations` — reconstrói as tabelas `agg_*` (SQL no Postgres).

## Coerência garantida (verificável)

Pediatria → idade < 13 · Catarata → 45+ (predominante 55–79) · Obstetrícia → mulheres
15–45 · Internação `custo_medio` ≫ consulta · procedimentos complexos custam mais ·
procedimento sempre ligado à especialidade correspondente.

**v1.1**: `procedimentos.perfil_utilizacao` (pontual|recorrente|variavel) mapeado por
`grupo_procedimento` (`GRUPO_PERFIL_UTILIZACAO` em `catalogs.py`) — cirurgias/
internações/OPME/obstetrícia = pontual; consultas/exames laboratoriais/terapias/
quimioterapia = recorrente; o restante = variavel. **Coparticipação**: cada plano tem
`tem_coparticipacao`/`percentual_coparticipacao` (planos individuais/PME de entrada
cobram 10–30%; empresariais e de apartamento, em geral, não cobram); aplicada sobre
`valor_pago` só em consulta/exame/terapia/pronto_socorro (não em internação/cirurgia/OPME).

## Cenários intencionais (13) e gabarito

`k = beneficiarios / 20000` escala todos os volumes injetados. Cada cenário grava uma
linha em `cenarios_gabarito`. Os 3 procedimentos "controlados" (catarata, ressonância,
fisioterapia) têm a geração orgânica **suprimida** — só o hook os gera, para o efeito
plantado dominar o agregado.

| # | Código | O que é plantado | `efeito_esperado` | Competência-alvo |
|---|---|---|---|---|
| 1 | `s1_catarata_freq` | Jul–Ago/2026: facectomia +~100% de **frequência**, custo médio ~+3%, excedente concentrado em 3 prestadores de oftalmologia | `frequencia` (procedimento) | 2026-07 |
| 2 | `s2_internacoes` | Set–Out/2026: internações +~65%, excedente em beneficiários **60+** | `frequencia` (tipo_atendimento) | 2026-09 |
| 3 | `s3_prestador_anomalo` | Um prestador de ortopedia: custo médio ~45% acima dos pares (recebe só eventos do hook), frequência crescente em 2026, 2 procedimentos concentrados | `misto` (prestador) | 2026-06 |
| 4 | `s4_alto_custo` | ~30 beneficiários (×k) com terapias de altíssimo custo (quimio alvo, imunobiológico, hemodiálise) a partir de mar/2026 | `concentracao` (beneficiário) | 2026-06 |
| 5 | `s5_sazonalidade_resp` | Jun–Ago de **2025 e 2026**: alta de atendimentos respiratórios (repete → sazonal, não anômalo) | `sazonal` (especialidade pneumologia) | 2026-07 |
| 6 | `s6_ps_recorrente` | ~480 beneficiários (×k) com 3–5 idas ao pronto-socorro **todo mês** | `frequencia` (tipo_atendimento) | 2026-06 |
| 7 | `s7_custo_medio` | Ressonância magnética: **frequência estável** todo mês, custo médio com **degrau de +30%** a partir de jun/2026 | `custo_medio` (procedimento) | 2026-06 |
| 8 | `s8_melhora_fisioterapia` | Sessão de fisioterapia: a partir de out/2026 um programa de gestão reduz ~50% a **frequência** → despesa cai (insight positivo) | `frequencia` / redução (procedimento) | 2026-10 |
| 9 | `s9_receita_estagnada` | O reajuste anual de maio/2026 é **suprimido**: receita per capita estagna, sinistralidade sobe pelo **denominador** | `receita` | 2026-05 |
| 10 | `s10_glosa_aumenta` *(v1.1)* | Fev/2026: taxa de glosa sobe ~3,4× (despesa bruta ~estável) | `glosa` | 2026-02 |
| 11 | `s11_coparticipacao_aumenta` *(v1.1)* | Mai/2026: percentual de coparticipação sobe ~3,6× | `coparticipacao` | 2026-05 |
| 12 | `s12_glosa_copart_combinado` *(v1.1)* | Out/2026: glosa e coparticipação sobem juntas (~2,2× cada) | `misto_financeiro` | 2026-10 |
| 13 | `s13_receita_cai_mais` *(v1.1)* | Dez/2026: despesa líquida cai, mas um ajuste pontual de receita (−15%) faz a sinistralidade **piorar** | `receita` | 2026-12 |

**v1.2 — cenários S14–S19 (Contract Intelligence).** 6 hooks que injetam eventos em
beneficiários de UM contrato específico (`cart.contrato_id`), com RNG dedicado
(`seed+1414`) — não perturbam o stream principal nem os cenários S1–S13. Não dependem de
receita por contrato.

| # | Código | Alvo | `efeito_esperado` | Competência |
|---|---|---|---|---|
| 14 | `s14_contrato_concentracao` | poucos benef. elevam a despesa do contrato | `concentracao_contrato` | 2026-08 |
| 15 | `s15_contrato_homogeneo` | aumento distribuído (exames de imagem em ~muitos) | `distribuicao_homogenea` | 2026-08 |
| 16 | `s16_contrato_novo_alto_custo` | 1 novo caso de alto custo em contrato estável | `novo_caso_alto_custo` | 2026-09 |
| 17 | `s17_contrato_recorrente` | grupo com terapias recorrentes, freq. crescente | `recorrencia` | 2026-07→12 |
| 18 | `s18_contrato_evento_pontual` | colecistectomia (perfil pontual) que não se repete | `evento_pontual` | 2026-11 |
| 19 | `s19_contrato_concentracao_extrema` | 3 benef. ≈ toda a despesa do contrato (Gini→1) | `concentracao_extrema` | 2026-08 |

Os 4 cenários financeiros (10–13) atuam via multiplicadores de mês (`glosa_mult_por_mes`,
`copart_mult_por_mes`, `receita_ajuste_pontual` em `generator.py`/`scenarios.py`), não
via hooks de eventos — calibrados empiricamente para que o efeito plantado supere o
ruído orgânico de despesa bruta entre meses adjacentes (que já é ±1–11% na base, mesmo
sem nenhum cenário financeiro atuando).

## Detecção (testes)

`tests/test_scenarios.py` compara a saída do motor com o gabarito — 14 asserções
cobrindo os 13 cenários (S1 tem 2: frequência e concentração em 3 prestadores).
`tests/test_cohorts.py` valida a análise de coortes (v1.1) contra os cenários S1/S8.
Ver [ANALYTICS_ENGINE.md](ANALYTICS_ENGINE.md), [V1.1.md](V1.1.md) e [DEMO.md](DEMO.md).

## Evolução V1 — investigação de custos

**Massa Vida Plena:** nenhum evento muda. O seed passa a (1) gravar o vínculo da competência
em cada evento (`id_contrato`/`id_plano` = vínculo do beneficiário — idêntico ao que a Gold
já usava) e (2) criar **tabelas de preço** vigentes desde o início da janela para os pares
prestador × procedimento de hospitais e laboratórios, no mesmo valor que o gerador usa
(`custo_base × nivel_preco` por unidade). Como a tabela não muda, o degrau de +30% do `s7`
aparece corretamente como "custo unitário observado, **sem** evidência de reajuste".

**Laboratório de cenários** (`app/seed/cenarios_investigacao.py`, tenant `w2h-cenarios`,
`python -m app.seed.cenarios_investigacao`): tenant próprio para não alterar os totais dos
quais os 19 cenários acima dependem. Um contrato por cenário, preços fixos (sem ruído),
contagens controladas na comparação (mai/2026) e no alvo (jun/2026); histórico jan/2025–
abr/2026 com variação Poisson. O motor só recebe `contrato_id`.

| Código | Contrato | O que é plantado | Resultado esperado (teste) |
|---|---|---|---|
| `inv01_frequencia` | CEN-01 | todas as células +40% | frequência = 40% da bruta-base; mix e custo ≈ 0 |
| `inv02_custo_medio` | CEN-02 | valores +15%, sem tabela | custo unitário = 15% da bruta-base; preço contratual **não calculável**; hipótese + investigar |
| `inv03_migracao_hospital` | CEN-03 | internação/cirurgia migram para hospital 1,6× mais caro | mix de prestadores = R$ 44.100; transferência para Hospital Beta |
| `inv04_mix_procedimentos` | CEN-04 | −30 consultas, +30 cirurgias (total igual) | mix de procedimentos = R$ 212.256; frequência ≈ 0 |
| `inv05_recorrentes` | CEN-05 | 30 beneficiários recorrentes triplicam fisioterapia | ≥ 90% da variação na coorte recorrente; hipótese sem linguagem clínica |
| `inv06_perfil` | CEN-06 | entram 125 beneficiários de 60–69 anos (taxa 2,5×) | população + perfil etário = Δ bruta; frequência ≈ 0 |
| `inv07_reajuste_tabela` | CEN-07 | tabela +10% (4 pares) em jun/2026 | preço contratual = R$ 18.350; 4 mudanças de tabela de +10% |
| `inv08_defasagem` | CEN-08 | 10 contas de abr/2026 apresentadas em jun/2026 | Δ defasada = Δ líquida; hipótese de apresentação tardia |
| `inv09_reducao` | CEN-09 | todas as células −40% | frequência = −40% da bruta-base |
| `inv10_insuficiente` | CEN-10 | 6 vidas, 2 competências, 1 cirurgia | sem hipóteses; "dados insuficientes" e "histórico insuficiente" |

Testes: `tests/test_cenarios_investigacao.py`.

## Consolidação do canônico V1 — laboratório cadastral e financeiro

`app/seed/cenarios_financeiro.py` (tenant `w2h-financeiro`, `python -m app.seed.cenarios_financeiro`):
43 beneficiários, jan–jun/2026, valores redondos (PL-A R$ 300, PL-B R$ 500 por vida-mês;
consulta R$ 100; exame R$ 50). A massa **Vida Plena** permanece só com receita legada por plano
(prova de compatibilidade).

| # | Cenário | Onde | Verificado |
|---|---|---|---|
| 1 | empresa com múltiplos contratos | EMP-ALFA (CT-A1, CT-A2) | sinistralidade da empresa 1.800/6.400 em abr |
| 2 | contrato com múltiplos planos | CT-A1 (PL-A, PL-B) | receita jan 1.800 + 2.000 |
| 3 | faturamento direto por contrato | FAT-AAAAMM-CT-A* | faturado em M−1, reconhecido em M |
| 4 | faturamento consolidado por empresa | FAT-AAAAMM-EMP-BETA | unidade empresa, 2 contratos |
| 5 | receita de empresa com detalhamento | EMP-BETA | sinistralidade de CT-B1 publicada |
| 6 | receita de empresa sem detalhamento | EMP-GAMA | nível empresa, sem rateio; contratos sem sinistralidade; empresa 1.100/4.100 |
| 7 | beneficiário mudando de contrato | B-X (CT-A1 → CT-A2 em 01/04) | eventos antes/depois em contratos diferentes |
| 8 | beneficiário mudando de plano | B-Y (PL-A → PL-B em 01/04) | plano do evento pela data |
| 9 | despesa antes e depois da mudança | exames de B-X em fev e mai | CT-A1 fev 1.050; CT-A2 mai 950; reconstruir a Gold não reclassifica |
| 10 | vidas históricas | CT-A1 10→9, CT-A2 8→9, CT-B1 6→5 | vínculo vigente no dia 1 |
| 11 | receita sem correspondência cadastral | `CT-INEXISTENTE` (R$ 250/mês) | não atribuída; conta na operadora, não em contrato |
| 12 | contrato sem receita atribuível | CT-SEM | "Receita por contrato indisponível" + motivo |

Também: B-Z com vínculo encerrado e atendimento posterior (despesa não atribuída, explicada);
acréscimo de juros (faturado ≠ reconhecido); recebimento parcial e fatura não recebida
(recebido ≠ faturado). Testes: `tests/test_cenarios_financeiro.py`.

