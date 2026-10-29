# Evolução do Produto — 7 Itens

Sete melhorias que levam o W2Health do "explicar o número" ao "agir e acompanhar". Todas
seguem as regras do produto: FATO / ESTIMATIVA / HIPÓTESE rotulados, nada de rateio de
receita, nenhum beneficiário identificado fora das telas autorizadas, feature e permissão
revalidadas no backend.

| # | Item | Backend | Tela | Feature / permissão |
|---|------|---------|------|---------------------|
| 1 | Completude da competência e IBNR | `analytics/completude.py` · `GET /executive/completude` | Banner em Visão Executiva e Sinistralidade | `executive_overview` |
| 2 | Simulador de reajuste do contrato | `analytics/reajuste.py` · `GET /analytics/contratos/{id}/simulacao-reajuste` | Detalhe do contrato | `contract_intelligence` |
| 3 | Planos de ação + medição de efeito | `analytics/efeito_acao.py` · `/planos-acao` (CRUD) | `/planos-acao` e "Criar plano de ação" no insight | `insights` · escrita `action_plans:write` (MANAGER+) |
| 4 | Alertas por e-mail | `analytics/notificacoes.py`, `saas/notificacoes.py` · `/notificacoes/*` · `python -m app.ops.enviar_alertas` | `/configuracao/alertas-email` | `alerts` · envio `alert_rules:write` |
| 5 | Relatório mensal da diretoria | `analytics/relatorio_mensal.py` · `GET /executive/relatorio-mensal` | `/relatorio` (Gerar PDF) | `executive_overview` |
| 6 | Padrões ANS (TISS, SIB, DIOPS) | `data_platform/padroes_ans.py` · mapping `ans_padroes_v1` · `/admin/tenants/{t}/sources/{s}/padroes-ans`, `/admin/tenants/{t}/diops-conferencia` | Admin › Integrações | SUPER_ADMIN |
| 7 | Benchmark de mercado (dados abertos ANS) | `analytics/benchmark.py` · `GET /analytics/sinistralidade/benchmark` · `python -m app.ops.benchmark_ans` | Card em Sinistralidade e no relatório | `loss_ratio_intelligence` |

Migration: `a9b8c7d6e5f4` — `planos_acao`, `assinaturas_alerta`, `envios_notificacao` (data
plane, RLS, escrita do runtime) e `benchmarks_mercado` (control plane, somente leitura do runtime).

## 1. Completude da Competência

Chain-ladder sobre o triângulo **competência de atendimento × defasagem até a competência de
apresentação** (`eventos_assistenciais.competencia_apresentacao`). Corte = maior competência de
apresentação carregada. Fator f_k = Σ C(k+1) ÷ Σ C(k) nas 12 competências mais recentes com
k+1 meses de desenvolvimento; defasagem máxima 6 meses. Estimativa final = observado × Π f_k.

* Competência com completude < 99,5% é **preliminar**; o banner mostra % recebido, estimativa
  final, valor a chegar e sinistralidade projetada.
* Sem data de apresentação em ≥ 90% dos eventos: `disponivel: false` com o motivo — nada é estimado.
* O observado reconcilia com `agg_sinistralidade_competencia.despesa_liquida` (teste).
* Seed: `seed/apresentacao.py` carimba a apresentação com defasagem por tipo de atendimento
  (hash do id, sem consumir o RNG). `make seed` usa `--carga-parcial`: as contas de dez/2026
  apresentadas depois do corte ficam fora da carga, como numa base real — dezembro aparece
  "bom demais" e o banner mostra a projeção. Isso muda dez/2026 (o cenário S13 vale só na
  suíte de testes, que semeia sem a opção).

## 2. Simulador de Reajuste

`reajuste técnico = (despesa líquida N meses × (1 + VCMH) ÷ meta) ÷ receita N meses − 1`.
Receita = reconhecida **atribuída** ao contrato; sem ela, só com a receita mensal **informada**
por quem simula (rotulada HIPÓTESE). Meses preliminares podem ser completados com a estimativa
do item 1 (proporcional, ESTIMATIVA). Devolve tabela de cenários 0–30% com "atinge a meta".

## 3. Planos de Ação

Plano = título, responsável, prazo, status, competência de início, meta de redução e o **alvo**
(dimensão × chave da camada analítica, ou a carteira). Efeito = média 3 meses antes × até 3
meses fechados depois; comparado ao restante da carteira (diferença-em-diferenças simples).
Variações = FATO; efeito relativo = HIPÓTESE. Meses preliminares ficam fora do "depois".

## 4. Alertas por E-mail

Cada usuário assina para si (e-mail da sessão): semanal/mensal, severidade mínima, com ou sem
insights. A mensagem nunca identifica beneficiário (alertas de beneficiário viram contagem;
`BEN-…` é mascarado). Sem `SMTP_HOST`, o envio é **simulado**: `.eml` em
`NOTIFICACOES_OUTBOX_DIR`. Idempotente por assinatura × competência na janela (7/28 dias).
Agendamento: cron com `python -m app.ops.enviar_alertas --frequencia semanal`.

Variáveis: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_STARTTLS`, `SMTP_FROM`,
`NOTIFICACOES_OUTBOX_DIR`, `APP_PUBLIC_URL`.

## 5. Relatório Mensal

Consolida o que já existe (KPIs, completude, fatores, alertas, planos, mercado) respeitando o
plano do tenant (`restricoes_plano`). PDF pela impressão do navegador, sem o shell
(`@media print`, classe `no-print`) — sem dependência nova no backend.

## 6. Padrões ANS

* **TISS** (`mensagemTISS`/`loteGuias`): consulta, SP/SADT e resumo de internação → eventos
  (um por procedimento executado), prestador, procedimento (TUSS) e especialidade (CBO).
  `dataRegistroTransacao` = data de apresentação (alimenta o item 1).
* **SIB**: beneficiários, planos (registro ANS), contratos (CNPJ da contratante; sem CNPJ =
  individual). Nome, CPF e CNS **não** são lidos.
* O pacote gerado segue o pipeline normal (mapping → DQ → Silver → Gold → reconciliação) pela
  fonte FILE com `{"mapping_id": "ans_padroes", "mapping_version": 1}`.
* UF do prestador (não vem na guia) = UF predominante dos beneficiários atendidos; senão a UF
  padrão informada; sem UF a carga é recusada antes de enfileirar.
* XML com DOCTYPE/ENTITY é recusado.
* **DIOPS**: conferência (não carga) das contas 311 (contraprestações) e 411 (eventos
  indenizáveis) do trimestre contra a Gold, com tolerância. Diferença de calendário contábil é
  esperada — a conferência aponta onde olhar.
* Limitação: glosa e valor pago não vêm na guia (valor pago = apresentado até integrar o
  demonstrativo de análise de conta).

## 7. Benchmark de Mercado

Distribuição (p25 / mediana / p75 / média ponderada) por modalidade calculada dos dados abertos
da ANS: demonstrações contábeis (411 ÷ 311), CADOP (modalidade) e, opcionalmente, beneficiários
por operadora (despesa por vida/mês). Operadoras com < 5 no segmento não geram referência;
sinistralidade fora de 10–300% é descartada. Só a distribuição é gravada.

Sem carga real, o seed grava uma referência **ilustrativa** — exibida sempre com o aviso
"Ilustrativo". Carga real:

    python -m app.ops.benchmark_ans --periodo 2025 --demonstracoes 4T2025.csv \
        --cadop Relatorio_cadop.csv --beneficiarios beneficiarios_2025.csv
