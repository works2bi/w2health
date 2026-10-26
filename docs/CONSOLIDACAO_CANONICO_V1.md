# W2Health V1 — Consolidação do Modelo Canônico Cadastral, Contratual e Financeiro

> **Status:** Etapas A–F concluídas (23–24/10/2026) — relatório final na seção 8. Layout
> **candidato, não homologado** (divergências abertas em MODELO_CANONICO_V1_OFICIAL_CANDIDATO.md).
> Nada foi enviado ao repositório remoto. Base auditada: árvore de trabalho após a Evolução V1 de investigação de
> custos ([EVOLUCAO_V1_INVESTIGACAO_CUSTOS.md](EVOLUCAO_V1_INVESTIGACAO_CUSTOS.md)).
> Documentos derivados: [MODELO_CANONICO_V1_OFICIAL_CANDIDATO.md](MODELO_CANONICO_V1_OFICIAL_CANDIDATO.md) ·
> [REGRAS_RECEITA_E_FATURAMENTO.md](REGRAS_RECEITA_E_FATURAMENTO.md) ·
> [RECONCILIACAO_FINANCEIRA_V1.md](RECONCILIACAO_FINANCEIRA_V1.md) ·
> [MATRIZ_CAPACIDADES_DADOS_V1.md](MATRIZ_CAPACIDADES_DADOS_V1.md).

Esta é uma **correção estrutural** do modelo, não uma nova plataforma. A investigação de
custos (decomposição estrutural, vínculo histórico, preço contratado, FATO/HIPÓTESE/
INVESTIGAR) é preservada integralmente.

---

## 1. Estado atual (auditado no código)

| Tema | Como está | Onde |
|---|---|---|
| Plano × contrato | N:N em `planos_contratos`; beneficiário com FK composta para o par | `models/catalog.py`, migration `c9d0e1f2a3b4` |
| Beneficiário | cadastro atual (`beneficiarios`) + histórico opcional (`beneficiario_vinculos`) | `models/membership.py` |
| Situação do vínculo | **não existe** no histórico | — |
| Empresa | **não existe** (só `contratos.tipo` = PF/PME/Empresarial) | — |
| Receita | **só** `receitas` (competência × plano, contraprestação + vidas) | `models/membership.py`, Gold |
| Receita por contrato | `receitas_contrato`: layout `version: 0`, **nunca populado nem lido**; o YAML ainda admite `rateio_vidas`/`rateio_faixa` como "metodologia" | `models/platform.py`, `contracts/receita_contrato.yaml` |
| Faturamento / documento / recebimento | **não modelados** | — |
| Sinistralidade por contrato | deliberadamente ausente (`receita_disponivel: false` + aviso) | `analytics/contratos.py`, `test_contratos.py` |
| Atribuição evento → contrato | Silver grava `id_contrato/id_plano` pela vigência do histórico; **sem vigência, usa o cadastro ATUAL do beneficiário** (sem checar adesão/saída); Gold e repositórios usam `COALESCE(evento, beneficiário)` | `data_platform/silver.py`, `seed/aggregate.py`, `analytics_repo.py` |
| Vidas por contrato | `agg_contrato_competencia.vidas` = beneficiários ativos pelo contrato **atual** | `seed/aggregate.py` |
| Despesa por contrato | via `agg_beneficiario_competencia.id_contrato = MIN(contrato do mês)` — um beneficiário que muda de contrato no mês tem a despesa inteira num só contrato | `seed/aggregate.py` |
| Sobreposição de vigências | verificada **só dentro do lote** | `quality/rules.py` |
| Competências | evento: `competencia` (decisão de onboarding no mapping) + `data_evento`; receita: `competencia` única | contratos YAML, `canonical.py` |
| `valor_pago` | semântica = `apresentado − glosado` (derivado quando ausente); não é "pago em caixa" | `CANONICAL_DATA_MODEL.md §5` |
| Reconciliação | origem × Silver × Gold de eventos e receita por competência | `data_platform/reconciliation.py` |
| Teste intermitente | `test_worker::test_cargas_concorrentes…` falha sob carga | ver §5 |

## 2. Lacunas

| # | Lacuna | Risco |
|---|---|---|
| G1 | Sem empresa nem associação empresa × contrato | não há agrupamento nem faturamento consolidado |
| G2 | Receita num grão único (competência × plano) | sinistralidade contratual impossível; faturado = reconhecido = recebido implicitamente |
| G3 | Layout de receita por contrato admite rateio como "metodologia" | receita estimada apresentada como real |
| G4 | Evento sem vigência é atribuído ao contrato atual | **viola a regra**: atribuição sem vigência válida |
| G5 | Vidas por contrato pelo vínculo atual | exposição histórica errada após troca de contrato |
| G6 | Despesa por contrato via `MIN(contrato)` do beneficiário no mês | troca no meio do mês joga a despesa inteira num contrato |
| G7 | Vigências só checadas no lote | duas cargas podem criar vínculos sobrepostos (ambiguidade) |
| G8 | Situação do vínculo inexistente | não dá para saber se o vínculo estava ativo |
| G9 | Competências de apresentação/pagamento e identificação de conta/guia/estorno inexistentes no evento | conciliação com o financeiro impossível |
| G10 | Calendário global gravado dentro da transação de publicação | cargas de tenants diferentes se serializam (causa do teste intermitente) |

## 3. Decisões

### 3.1 Cadastro
- **Preservado:** `planos_contratos` (N:N) e a FK composta do beneficiário.
- **Novo:** `empresas` (código, nome, tipo, status) e `empresas_contratos` (empresa,
  contrato, **papel**, vigência). Regra: **no máximo uma empresa por contrato e papel em cada
  data** (sem sobreposição, inclusive contra cargas anteriores). Agrupamentos e receitas por
  empresa usam somente o papel `contratante` — um contrato nunca conta duas vezes.
- **`beneficiario_vinculos.situacao`** (opcional). Exposição conta vínculos com situação
  vazia ou `ativo`; `suspenso` **não** é exposição até decisão de negócio (pendência P3).

### 3.2 Receita (detalhe em [REGRAS_RECEITA_E_FATURAMENTO.md](REGRAS_RECEITA_E_FATURAMENTO.md))
Quatro fatos, cada um no seu grão — nenhum é derivado do outro:

| Fato | Tabela | Grão | Competência |
|---|---|---|---|
| Documento de cobrança | `faturas` | 1 por documento; unidade de cobrança = contrato **ou** empresa | faturamento |
| Item do documento | `fatura_itens` | 1 por item; natureza; contrato × plano quando a origem detalha | referência do item |
| Recebimento | `recebimentos` | 1 por baixa/pagamento | recebimento |
| Receita reconhecida | `receitas_reconhecidas` (evolui o layout `receitas_contrato`) | competência × **nível de atribuição** (contrato × plano · contrato · empresa · não atribuída) | reconhecimento |
| Receita legada | `receitas` (inalterada) | competência × plano | reconhecimento |

- **Proibido rateio.** `estimada = true` existe só como marcação; o motor **não** lê receita
  estimada em indicador algum nesta versão.
- **Não atribuída preservada:** receita de empresa sem detalhamento, ou com código sem
  cadastro, fica no grão original (`nivel_atribuicao` = `empresa` / `nao_atribuida`) — conta
  no total da operadora, **nunca** na sinistralidade de contrato.
- **Fonte da receita da operadora por competência:** reconhecida, se existir; senão legada.
  **Nunca as duas somadas.** Se as duas existem e divergem acima da tolerância, a receita da
  competência é marcada `divergente` e a sinistralidade daquela competência deixa de ser
  publicada (a carga que causar a divergência é reprovada na reconciliação).

### 3.3 Despesa e vínculo
- Contrato/plano do evento = **vínculo vigente na data do atendimento**: histórico, se o
  beneficiário tem histórico; senão o cadastro, **somente se** `data_adesao ≤ data_evento <
  data_saida`. Fora disso: **não atribuído** (`id_contrato` nulo) — nunca o contrato atual.
- Gold/repositórios passam a usar **apenas o vínculo do evento** (sem `COALESCE` com o
  cadastro). Os registros antigos já foram preenchidos pela migration `d0e1f2a3b4c5`, então
  nenhum valor existente muda. Dimensões contrato/plano ganham a chave `nao_atribuido`.
- Despesa por contrato direto do evento (G6). Vidas por contrato pelo vínculo vigente (G5).
- Evento ganha campos opcionais: `numero_guia`, `numero_conta`, `unidade_medida`,
  `natureza_lancamento` (normal · estorno · ajuste · cancelamento), `competencia_apresentacao`,
  `competencia_pagamento`, `valor_pago_origem` (`informado` | `derivado`).
- `valor_pago` **mantém** a semântica `apresentado − glosado` (valor aprovado para pagamento);
  não é tratado como pago em caixa (pendência P5).

### 3.4 Sinistralidade contratual
Publicada para um contrato × competência **somente** quando: receita reconhecida atribuída
ao contrato > 0; receita da competência não divergente; e a empresa contratante do contrato
**não** tem receita consolidada sem detalhamento na competência (ela pode conter parte da
receita deste contrato). Caso contrário: `null` + motivo. Fórmula: `despesa líquida
atribuída ÷ receita reconhecida atribuída × 100`.

> Revisão durante a implementação: o diagnóstico propunha um limiar GLOBAL de cobertura
> (≥ 90% da receita da competência atribuída a contratos). O laboratório mostrou que isso
> suprimiria a sinistralidade de TODOS os contratos sempre que UMA empresa fosse faturada de
> forma consolidada — o risco é local (a empresa do contrato), não global. A cobertura global
> continua calculada e exibida (`cobertura_receita_contratual`), só como informação.

### 3.5 Massa sintética
- **Vida Plena** permanece um tenant **só com receita legada por plano** — prova a
  compatibilidade (e mantém válidos os testes que exigem "sem receita por contrato").
- **Novo laboratório `w2h-financeiro`** com os 12 cenários cadastrais/financeiros e gabarito.

## 4. Impacto técnico

| Camada | Mudança |
|---|---|
| Migration | `e1f2a3b4c5d6`: `empresas`, `empresas_contratos`, `faturas`, `fatura_itens`, `recebimentos`; `receitas_contrato` → `receitas_reconhecidas` (renomeada + colunas); `beneficiario_vinculos.situacao`; 7 colunas opcionais no evento; colunas de receita/sinistralidade em `agg_sinistralidade_competencia` e `agg_contrato_competencia`. Aditiva; nenhuma linha apagada |
| Canônico / YAML | 6 entidades novas; `receita_contrato.yaml` substituído por `receita_reconhecida.yaml` (sem rateio) |
| Pipeline | loaders novos; DQ: sobreposição contra o persistido, unidade de cobrança, total do documento, receita sem cadastro (WARNING, preservada); reconciliação financeira |
| Gold | fonte da receita por competência; vidas e despesa por contrato pelo vínculo; receita/sinistralidade contratual com cobertura |
| API | `/analytics/receitas` (composição e reconciliação), `/analytics/empresas`, sinistralidade contratual em `/analytics/contratos*`, escopo de empresa e sinistralidade na investigação |
| UI | contrato (sinistralidade ou motivo), lista de contratos agrupável por empresa, composição da receita em Sinistralidade |

## 5. Teste intermitente de workers — causa raiz

`SilverLoader.ensure_competencias` grava o calendário GLOBAL `competencias`
(`INSERT … ON CONFLICT DO NOTHING`) **dentro** da transação de publicação do tenant. Duas
cargas com os mesmos meses: a segunda espera a primeira **commitar** (conflito de chave em
linha ainda não confirmada) — as cargas de tenants diferentes se serializam e, sob carga, o
par passa do `join(120)` do teste. Correção no produto (não no timeout): o calendário é
gravado numa transação curta e própria, antes da publicação (idempotente). Teste novo prova
que uma publicação aberta não bloqueia outra no calendário.

## 6. Estratégia de migração e compatibilidade

1. Migration aditiva e reversível; `receitas_contrato` é renomeada (linhas existentes, se
   houver, ficam com `nivel_atribuicao = contrato` e `estimada = true` quando a metodologia
   antiga era rateio — nunca promovidas a receita real).
2. Tenants só com `receitas` continuam idênticos (fonte legada).
3. Nenhum contrato ou empresa é inventado para receita antiga.
4. O backend expõe a fonte (`fonte_receita`) e o nível de cada valor; somas entre níveis
   incompatíveis não são oferecidas pela API.

## 7. Pendências de negócio (não implementadas — o cálculo afetado fica bloqueado)

| # | Pendência | Bloqueia |
|---|---|---|
| P1 | Alocação gerencial de receita de empresa sem detalhamento | sinistralidade contratual desses contratos (fica "não disponível") |
| P2 | Mais de uma empresa contratante por contrato na mesma data | rejeitado na carga até haver regra |
| P3 | Vínculo `suspenso` conta como exposição? | contado como **não** exposto |
| P4 | Contagem de eventos com estorno/cancelamento (hoje o valor é líquido de estornos; a contagem não) | frequência em bases com estornos — limitação declarada |
| P5 | Valor efetivamente pago em caixa ao prestador | não modelado; `valor_pago` = aprovado após glosa |
| P6 | Receita reconhecida × faturada × recebida: regra contábil de reconhecimento (pró-rata, competência de cobertura) | a plataforma só carrega o reconhecido informado pela origem |

## 8. Relatório final

### 8.1 Estado inicial → final

| Tema | Antes | Depois |
|---|---|---|
| Empresa | inexistente | `empresas` + `empresas_contratos` (papel, vigência; 1 contratante por contrato/data) |
| Receita | só competência × plano | 4 fatos separados (fatura, item, recebimento, **receita reconhecida** por nível) + legada preservada |
| Rateio | admitido no layout `receita_contrato` | proibido; `estimada` só marcação futura, ignorada pelo motor |
| Fonte da receita da operadora | legada | uma por competência (reconhecida OU legada); divergência bloqueia a sinistralidade |
| Evento → contrato | fallback para o cadastro atual | vínculo vigente na data; senão **não atribuído** |
| Vidas por contrato | vínculo atual | vínculo vigente no 1º dia da competência |
| Despesa por contrato | `MIN(contrato)` do beneficiário no mês | direto do evento |
| Sobreposição de vigência | só no lote | lote + já carregado |
| Sinistralidade de contrato | ausente por decisão | publicada só com receita atribuída, sem divergência e sem receita consolidada não detalhada da empresa |
| Teste de workers concorrentes | intermitente | 2 causas medidas e corrigidas (calendário global; fsync em banco de teste) |

### 8.2 Entidades criadas ou alteradas

Criadas: `empresa`, `empresa_contrato`, `fatura`, `fatura_item`, `recebimento`,
`receita_reconhecida` (evolução de `receita_contrato`). Alteradas: `vinculo_beneficiario`
(+`situacao`), `evento_assistencial` (+competências de apresentação/pagamento, guia, conta,
unidade, natureza do lançamento, origem do `valor_pago`). Preservadas: `plano_contrato`
(N:N) e a FK composta do beneficiário.

### 8.3 Grãos financeiros

Documento (`fatura`), item (`fatura_item`), baixa (`recebimento`), lançamento reconhecido ×
nível de atribuição (`receita_reconhecida`), competência × plano (`receita`, legada), item de
conta (`evento_assistencial`). Unicidade: número do documento; documento + item; número da
baixa; competência + identificador de origem; competência + plano. Ver
[REGRAS_RECEITA_E_FATURAMENTO.md](REGRAS_RECEITA_E_FATURAMENTO.md).

### 8.4 Migration

`e1f2a3b4c5d6_consolidacao_canonico_financeiro` (revisa `d0e1f2a3b4c5`): aditiva; renomeia
`receitas_contrato` → `receitas_reconhecidas` preservando linhas; testada com upgrade,
`alembic check` (sem divergência), downgrade −1 e upgrade.

### 8.5 Silver / Gold / Serving

- Silver: loaders das 6 entidades; nível de atribuição (`atribuicao_receita`); vínculo
  estrito do evento; calendário global em transação própria; DQ com sobreposição persistida,
  unidade de cobrança, total do documento, documento em dois níveis, receita sem cadastro
  preservada, negativos só em estorno/ajuste.
- Gold: fonte única da receita e status; receita por nível e cobertura; vidas e despesa por
  contrato pelo vínculo; receita reconhecida e sinistralidade de contrato com motivo.
- Pipeline: reconciliação financeira no gate (FAIL não publica).

### 8.6 APIs e páginas

Novas rotas: `GET /analytics/receitas/composicao`, `GET /analytics/receitas/reconciliacao`
(`financial_composition`), `GET /analytics/empresas`, `GET /analytics/empresas/{id}`
(`contract_intelligence`); `/analytics/contratos*` com `sinistralidade_contratual` e empresa;
`/analytics/sinistralidade*` com `fonte_receita`/`receita_status` (e bloqueio em divergência);
`/analytics/investigacao?empresa_id=` e bloco `sinistralidade`. Páginas: Sinistralidade
(cartão "De onde vem a receita"), Contratos (empresas agrupando contratos, receita e
sinistralidade por contrato), detalhe do contrato (sinistralidade contratual ou motivo),
painel de investigação (sinistralidade do escopo).

### 8.7 Cenários e reconciliações demonstradas

12 cenários no laboratório `w2h-financeiro` com valores esperados calculados à mão
([SYNTHETIC_DATA.md](SYNTHETIC_DATA.md)); reconciliações em
[RECONCILIACAO_FINANCEIRA_V1.md](RECONCILIACAO_FINANCEIRA_V1.md) — 0 FAIL no laboratório,
WARNINGs por plano explicados pela receita não detalhada, divergência induzida bloqueando a
competência, carga divergente reprovada no pipeline.

### 8.8 Testes

| | Antes desta evolução | Depois |
|---|---|---|
| Coletados | 517 | **550** (+20 laboratório financeiro, +7 pipeline financeiro, +5 DQ, +1 calendário) |
| Suíte no caminho oficial (`docker compose --profile test run backend-test`, rede interna do compose) | — | **535 aprovados · 0 falhas · 15 ignorados** |
| Ignorados | 15 | 15 — adapter S3 sem `S3_TEST_ENDPOINT` (inalterado) |

Frontend: `tsc`, ESLint e `next build` sem erros. Migration: upgrade, `alembic check` sem
divergência, downgrade e upgrade.

Testes existentes alterados (só infraestrutura/regra nova, com justificativa):
`test_rls` e `test_multitenancy` (`receitas_contrato` → `receitas_reconhecidas`),
`test_data_platform_unit` (entidades opcionais no mapping genérico),
`test_pipeline_investigacao` (eventos fora do vínculo agora ficam NÃO atribuídos — o teste
compara com a contagem exata desses eventos pela mesma regra), `test_worker` (diagnóstico).

**Teste intermitente de workers concorrentes — investigação e correções:**
1. **Causa no produto (corrigida):** o calendário global `competencias` era gravado dentro da
   transação de publicação → cargas de tenants diferentes se serializavam. Agora em transação
   própria; teste novo `test_calendario_global_nao_serializa_publicacoes` prova a ausência de
   bloqueio (lock_timeout de 2 s).
2. **Causas de ambiente (medidas):** rodando a suíte a partir do Windows via `localhost:15432`,
   (a) commits chegaram a esperar o fsync do disco virtual (`wait_event = WALSync`) — bancos de
   teste efêmeros agora com `synchronous_commit = off`; (b) o proxy de porta do Docker Desktop
   travou uma conexão nova por ~30 min (py-spy: `psycopg.wait_conn` em `recreate_database`,
   sem sessão no servidor) e, depois, acrescentou latência a cada ida e volta (servidor em
   `ClientRead` enquanto o cliente espera resposta) — `recreate_database` agora usa
   `connect_timeout` + novas tentativas. Pelo caminho oficial (dentro da rede do compose, como
   a CI), o teste passa; o timeout NÃO foi aumentado e o teste NÃO foi desabilitado. Se voltar
   a falhar, a mensagem traz as pilhas das threads, o estado do pool e as sessões do banco.
3. Pelo host Windows, a suíte completa ainda pode falhar nesse teste quando o proxy do Docker
   Desktop degrada — limitação do ambiente local, registrada.

### 8.9 Compatibilidade com receita legada

Tenants só com `receita` (Vida Plena, pacotes CSV antigos) seguem idênticos: `fonte_receita
= legada_plano`, sinistralidade inalterada, contratos com "Receita por contrato ainda não
disponível" — os testes v1.2 de contrato passam sem alteração. Nenhum contrato ou empresa foi
inventado para receita antiga; a migration não altera linhas de receita.

### 8.10 Prontidão para o extrator

O contrato de integração está definido (layout candidato com grão, chave, campos, tipos,
obrigatoriedade, relacionamentos, competência, carga, qualidade, sensibilidade e
capacidades), com exemplos fictícios e mapping genérico executável. **Pode-se iniciar o
extrator** contra o layout candidato, ciente de que ele **não está homologado** enquanto as
divergências D1–D4 e D7–D12 de
[MODELO_CANONICO_V1_OFICIAL_CANDIDATO.md](MODELO_CANONICO_V1_OFICIAL_CANDIDATO.md) estiverem
abertas — D1 e D2 (sexo obrigatório no banco; código de beneficiário de 16 caracteres)
quebram cargas reais e devem ser corrigidas antes do primeiro cliente.

### 8.11 Riscos residuais

- Divergências D1/D2 podem quebrar a primeira carga real (ver acima).
- Atribuição estrita: origens com datas de adesão/saída imprecisas deixarão eventos "não
  atribuídos" (correto, mas visível) — validar no onboarding.
- Regra de vínculo `suspenso` (P3) e contagem de estornos (P4) podem mudar vidas e frequência.
- Bases grandes: a Gold agora calcula vidas por vínculo (CTE por competência) — medir no
  primeiro volume real (PERFORMANCE_BASELINE.md).
- Sinistralidade de contrato depende de a origem detalhar receita por contrato; sem isso o
  produto mostra o motivo, não um número.
