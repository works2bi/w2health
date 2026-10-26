# Capability Readiness — W2Health (v1.2 · implementado na Fase 2)

> Nem todo cliente tem todos os dados. Este documento define, para cada capacidade, o que
> é **obrigatório**, o que é **desejável**, e **o que acontece se faltar** — para decidir
> quais módulos habilitar no onboarding de cada operadora.

## Modelo conceitual

Para um tenant, cada capacidade tem um estado:

- ✅ **Pronta** — todos os dados obrigatórios presentes e com qualidade (sem `ERROR` de DQ).
- 🟡 **Parcial** — obrigatórios presentes, faltam desejáveis → habilita com aviso/limitação.
- ❌ **Indisponível** — falta dado obrigatório → módulo escondido.

Avaliação (conceitual): sobre a Silver carregada do tenant, checar presença/preenchimento
das entidades/campos abaixo + resultado da Data Quality.

## Matriz

| Capacidade | Dados OBRIGATÓRIOS | Dados DESEJÁVEIS | Resultado se faltar obrigatório | Se faltar só desejável |
|---|---|---|---|---|
| **Executive Intelligence** (visão da carteira) | receita (competência, valor, vidas) + evento (valor_pago, competência) | — | ❌ sem sinistralidade | — |
| **Loss Ratio Intelligence** (explicar a variação) | + evento com especialidade, procedimento, tipo_atendimento | glosa/coparticipação por evento | ❌ sem decomposição por driver | 🟡 sem separar glosa/copart nos drivers |
| **Provider Intelligence** | + prestador; evento com id_prestador | prestador.id_especialidade_principal | ❌ | 🟡 sem detecção de anomalia vs pares (z-score) |
| **Beneficiary Intelligence** | + evento por beneficiário; beneficiário | data_adesao/data_saida; perfil_utilizacao | ❌ | 🟡 sem coortes "novos/saíram"; hipótese de episódio vira "a investigar" |
| **Contract Intelligence** (`/contratos`) | + contrato; beneficiário.id_contrato | — | ❌ módulo escondido | — |
| **Contrato — sinistralidade própria** | receita_contrato (competência × contrato) | — | ❌ (v1.2: sempre indisponível) | — |
| **Explicação por glosa** | valor_glosado por evento | motivo_glosa | 🟡 assume glosa = 0 | 🟡 sem "explicação por motivo" |
| **Explicação por coparticipação** | valor_coparticipacao por evento **ou** plano.percentual_coparticipacao | origem_dado (faturada/derivada) | 🟡 assume copart = 0 | 🟡 número marcado "estimado" |
| **Coortes (o porquê do porquê)** | beneficiário + evento + data_adesao/data_saida | perfil_utilizacao do procedimento | 🟡 só "novos/recorrentes/deixaram de usar" | 🟡 hipótese de episódio pontual não é levantada |
| **Concentração de beneficiários (C1)** | evento por beneficiário; despesa_liquida | — | ❌ | — |
| **Descritores de comportamento (C5)** | série mensal do beneficiário (agg_beneficiario) | — | ❌ | — |
| **Alertas configuráveis** | qualquer capacidade acima habilitada | — | herda das capacidades | — |
| **Reajuste contratual** | receita_contrato + contrato.data_base/meta + histórico | VCMH, RN aplicável | ❌ (fora do escopo v1.2 — discovery pendente) | — |
| **Previsibilidade / projeção** | ≥ 24 meses de série contínua e consistente | — | ❌ (fora do escopo v1.2) | — |

## Exemplo de leitura para um tenant

```
Cliente possui:
  eventos ✅   beneficiários ✅   prestadores ✅   receita por plano ✅
  valor_glosado por evento ✅     valor_coparticipacao por evento ❌ (só % do plano)
  beneficiário.id_contrato ✅     receita por contrato ❌

Resultado:
  Executive Intelligence          ✅
  Loss Ratio Intelligence         ✅
  Provider Intelligence           ✅
  Beneficiary Intelligence        ✅
  Contract Intelligence           🟡  (sem sinistralidade contratual — só vidas/despesa/concentração)
  Explicação por glosa            ✅
  Explicação por coparticipação   🟡  (derivada da regra do plano — marcada "estimada")
  Reajuste                        ❌
```

## Implementação (Fase 2)

**Regra:** `disponível = contratada (plano/override/global) E dados prontos`. Mora só no
backend (`backend/app/data_platform/readiness.py`); o frontend recebe o resultado pronto.

| Peça | Onde |
|---|---|
| Sinais de dados por tenant (existência na Silver/Gold) | `readiness.signals` — eventos, receita, beneficiários, contratos com vínculo, prestadores, Gold construída, glosa, coparticipação, saída de carteira, perfil de utilização |
| Requisitos por capability (obrigatórios × desejáveis) | `readiness.REQUIREMENTS` (tabela abaixo) |
| Estado persistido | `capability_readiness` (tenant × feature × `READY`/`PARTIAL`/`NOT_READY` × motivo × checks), com RLS; recalculado ao publicar uma carga, no seed sintético e sob demanda (Admin → Recalcular) |
| API | `/auth/me` → `capabilities[]` (`entitled`, `data_status`, `available`, `reason`); `features` = só as disponíveis |
| Bloqueio | rotas analíticas de uma capability contratada mas sem dados respondem `403 capability_not_ready` (diferente de `feature_not_enabled`) |
| Interface | menu esconde o que não está disponível; rota direta mostra "Recurso contratado — dados ainda não disponíveis" com o motivo |
| Admin | Integrações → Capabilities (contratada × dados × disponível × motivo) |

| Capability | Obrigatórios (bloqueiam) | Desejáveis (geram `PARTIAL`) |
|---|---|---|
| `executive_overview`, `loss_ratio_intelligence`, `insights` | eventos, receita, Gold | — |
| `financial_composition` | eventos, receita (legada **ou** reconhecida — Consolidação V1), Gold | glosa, coparticipação |
| `advanced_explanations` | eventos, beneficiários, Gold | saída de carteira, perfil de utilização |
| `contract_intelligence` | eventos, beneficiários, contratos com vínculo, Gold | receita reconhecida atribuída a contrato (Consolidação V1 — sem ela: PARTIAL, sem sinistralidade de contrato/empresa) |
| `provider_intelligence` | eventos, prestadores, Gold | — |
| `beneficiary_intelligence` | eventos, beneficiários, Gold | — |
| `alerts` | eventos, Gold | — |
| `custom_branding` | — (configuração) | — |

`PARTIAL` continua **disponível** e expõe o motivo. Testes:
`test_readiness_contratada_mas_sem_receita_nao_fica_pronta`,
`test_feature_contratada_mas_sem_dados_fica_indisponivel` (tenant carregado **sem**
`receitas.csv`: sinistralidade contratada, mas indisponível).

Limite: os sinais verificam **existência** de dado, não cobertura percentual nem frescor
(ex.: "última competência há mais de 2 meses"). A matriz acima, mais granular, continua
sendo a referência para a conversa de onboarding.
