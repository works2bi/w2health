# Catálogo de Features (Capabilities) — W2Health V1

> Fonte de verdade: `backend/app/saas/catalog.py` (chaves) e tabelas `features`,
> `plan_features`, `tenant_features` (estado configurável). Resolução:
> `backend/app/saas/features.py`. Saneamento de respostas: `app/saas/feature_filters.py`.

## Regra de resolução

```
1. Feature global ativa (features.is_active)?   não → DESABILITADA (kill switch, vale p/ todos)
2. Override do tenant (tenant_features)?        sim → valor do override (sempre com motivo)
3. Plano do tenant inclui (plan_features)?      sim → HABILITADA
4. caso contrário (ou tenant sem plano)              → DESABILITADA
```

Nenhuma regra de negócio lê o nome do plano. Backend bloqueia; frontend só adapta.

## Features implementadas

| Chave | Módulo | O que libera | Onde é exigida |
|---|---|---|---|
| `executive_overview` | Executive Overview | `/executive/overview` | router `executive` |
| `loss_ratio_intelligence` | Loss Ratio Intelligence | indicador, evolução, explicação, drill, procedimentos | routers `sinistralidade`, `procedimentos` |
| `financial_composition` | Loss Ratio Intelligence | `/sinistralidade/composicao`; `/analytics/receitas/composicao` e `/analytics/receitas/reconciliacao` (Consolidação V1) | rota |
| `advanced_explanations` | Loss Ratio Intelligence | coortes FATO / HIPÓTESE / A INVESTIGAR (`/explain/.../causas`); **investigação de custos** (`/analytics/investigacao`, Evolução V1 — sem `provider_intelligence` o detalhe por prestador é omitido; sem `beneficiary_intelligence`, as amostras de beneficiário) | rota |
| `contract_intelligence` | Contract Intelligence | `/analytics/contratos*` (sinistralidade contratual quando há receita atribuída), `/analytics/empresas*`, `?contrato_id=` / `?empresa_id=`, dimensão `contrato`, catálogo de contratos | router + `contrato_id_dep` + dimensão |
| `provider_intelligence` | Provider Intelligence | `/analytics/prestadores*`, dimensão `prestador`, concentração por prestador | router + dimensão + saneamento |
| `beneficiary_intelligence` | Beneficiary Intelligence | `/analytics/beneficiarios*`, concentração da variação, amostras de beneficiários | router + rota + saneamento |
| `insights` | Insights & Alerts | `/analytics/insights`, fatores de atenção da visão executiva | rota + executiva |
| `alerts` | Insights & Alerts | regras de alerta e `/analytics/alertas`; alertas no detalhe do contrato | router `config` + contrato |
| `custom_branding` | Plataforma | identidade visual editável pelo TENANT_ADMIN (a plataforma sempre pode editar) | `/api/tenant/branding*` |

### Saneamento de respostas compostas

Quando a feature falta, o backend **remove** (lista vazia / `null`) e declara em
`restricoes_plano`:

| Bloco | Feature |
|---|---|
| `concentracao_variacao_beneficiarios`, `beneficiarios_maior_despesa`, `beneficiarios_amostra`, `top_beneficiarios` | `beneficiary_intelligence` |
| `prestadores_maior_despesa`, `prestadores_maior_contribuicao_variacao` | `provider_intelligence` |
| insights com deep-link para módulo não contratado | módulo do link |
| alertas de entidade `beneficiario`/`prestador`/`contrato` | módulo da entidade |

Os cálculos nunca mudam por plano — só o que é devolvido.

## Planos padrão (matriz DEMONSTRATIVA, configurável em `/admin/planos`)

| Feature | BASIC | BUSINESS | ENTERPRISE |
|---|:-:|:-:|:-:|
| executive_overview | ✓ | ✓ | ✓ |
| loss_ratio_intelligence | ✓ | ✓ | ✓ |
| financial_composition | ✓ | ✓ | ✓ |
| contract_intelligence | ✓ | ✓ | ✓ |
| insights | ✓ | ✓ | ✓ |
| provider_intelligence | | ✓ | ✓ |
| beneficiary_intelligence | | ✓ | ✓ |
| alerts | | ✓ | ✓ |
| advanced_explanations | | ✓ | ✓ |
| custom_branding | | | ✓ |

Não é tabela comercial. O tenant demonstrativo `w2h-demo` recebeu ENTERPRISE no backfill
(mantém tudo o que já tinha); `w2h-demo-b` usa BASIC para demonstrar gating.

## Candidatas (NÃO implementadas — não entram no catálogo até existirem)

| Chave sugerida | Motivo de não existir agora |
|---|---|
| `data_quality` | há regras em `data_platform/quality`, mas nenhuma rota/tela de qualidade por tenant |
| `integrations` | não há conector real nesta fase |
| `contract_loss_ratio` | depende de receita por contrato (discovery) |
| `readjustment_simulation` | discovery de reajuste não concluído |
| `forecasting` | fora do escopo (previsão/ML) |

## Como adicionar uma feature

1. Implementar o recurso e protegê-lo com `require_feature("<chave>")` (e saneamento se
   aparecer em resposta composta).
2. Adicionar a `FEATURES` em `catalog.py` **e** inserir via migration de dados.
3. Mapear a rota em `frontend/src/lib/nav.ts` (`feature`).
4. Teste de bloqueio em `test_features.py`.
