# Roadmap V1 — W2Health

> Situação após a **Fase 3 — Production Readiness / Hardening**. Itens de DISCOVERY **não**
> são movidos para implementação sem decisão explícita.

## Concluído na Fase 3

| Item (era P0) | Onde |
|---|---|
| Dependências vulneráveis (Next 14 crítico, Starlette, cryptography) | [DEPENDENCY_SECURITY.md](DEPENDENCY_SECURITY.md) — runtime com zero achados |
| Carga fora da requisição HTTP (fila + worker, retry, lease, reaper, dead jobs) | [WORKER_AND_QUEUE.md](WORKER_AND_QUEUE.md) |
| Adapter de object storage (S3-compatível) | [OBJECT_STORAGE.md](OBJECT_STORAGE.md) |
| Imagem de runtime sem dependências de dev; containers não-root | [PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md) §4 |
| Configuração de produção fail-closed; segredos por arquivo | [PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md) §2–3 |
| Arquitetura de TLS/proxy + referência executável validada | [PRODUCTION_DEPLOYMENT.md](PRODUCTION_DEPLOYMENT.md) §1, §5, §8 |
| Restauração por tenant (ferramenta + testes) | [BACKUP_AND_RECOVERY.md](BACKUP_AND_RECOVERY.md) |
| Observabilidade (logs padronizados, métricas, health live/ready) | [OPERATIONS_RUNBOOK.md](OPERATIONS_RUNBOOK.md) |
| CI com testes, lint, migrations, audit, imagens | `.github/workflows/ci.yml` |
| Preparação para pentest, LGPD técnica, resposta a incidentes | [PENTEST_READINESS.md](PENTEST_READINESS.md), [LGPD_TECHNICAL_CONTROLS.md](LGPD_TECHNICAL_CONTROLS.md), [INCIDENT_RESPONSE.md](INCIDENT_RESPONSE.md) |

## Concluído — Evolução V1: investigação de custos (23/10/2026)

Decomposição estrutural que reconcilia (população, perfil etário, frequência, mix de
procedimentos, mix de prestadores, intensidade, preço contratual, custo unitário não
explicado, não comparável, glosa, coparticipação), baseline histórico, rede, coortes
comportamentais, defasagem e conclusões FATO/HIPÓTESE/INVESTIGAR; vínculo da competência e
tabela de preço no modelo canônico. Ver
[EVOLUCAO_V1_INVESTIGACAO_CUSTOS.md](EVOLUCAO_V1_INVESTIGACAO_CUSTOS.md). Pendências de
negócio dessa evolução (empresa/faturamento, condição crônica) estão no §8 daquele documento.

## Concluído — Consolidação do canônico V1 (23/10/2026)

Empresa e associação histórica com contratos; faturamento (documento, itens), recebimento e
receita reconhecida por nível de atribuição — sem rateio; fonte única de receita por
competência com bloqueio em divergência; atribuição estrita do evento ao vínculo vigente;
vidas históricas; sinistralidade contratual e de empresa só com receita identificada;
reconciliação financeira no gate do pipeline. Layout **candidato** (não homologado — ver
divergências em [MODELO_CANONICO_V1_OFICIAL_CANDIDATO.md](MODELO_CANONICO_V1_OFICIAL_CANDIDATO.md)).
Relatório: [CONSOLIDACAO_CANONICO_V1.md](CONSOLIDACAO_CANONICO_V1.md).

## P0 — ainda bloqueia cliente real em PRODUÇÃO

| Item | Por quê | Tipo |
|---|---|---|
| Escolher e provisionar a plataforma (nuvem/região), com TLS real, WAF/LB, rede privada | residência de dados, cifragem em trânsito | infraestrutura / decisão |
| Banco gerenciado com backup automático, PITR, cifragem em repouso e teste de restauração periódico com RTO/RPO medidos | não existe backup de produção | infraestrutura |
| Bucket de produção privado, cifrado (KMS), versionado/replicado | RAW é a única cópia do original | infraestrutura |
| Gerenciador de segredos real montando `SECRETS_DIR`; cofre para `DATA_ENCRYPTION_KEY` | segredos hoje vêm de arquivo local na referência | infraestrutura |
| Coleta de logs/métricas e alertas configurados (runbook) | operação sem visibilidade | infraestrutura |
| Pentest externo + correção dos achados | validação independente | processo |
| DPA, termo de uso, encarregado/DPO, suboperadores, procedimento de notificação validado pelo jurídico | LGPD | jurídico |

## P1 — V1 comercial

| Item |
|---|
| Gold incremental por competência afetada (hoje 114 s para 59 mil eventos — [PERFORMANCE_BASELINE.md](PERFORMANCE_BASELINE.md)) |
| Cache por (tenant, competência) da visão executiva e insights (p95 ~1,3–1,5 s) |
| Política de retenção/eliminação por tenant automatizada (dados + RAW + aviso de expiração em backups) |
| Primeiro conector DATABASE ou API (cadastro e referência de segredo já existem; falta extração) |
| Agendamento de cargas recorrentes; carga incremental (watermark/CDC) |
| `SecretProvider` de nuvem (`vault:`) para credenciais de integração |
| Scan de imagens (Trivy/Grype) e assinatura de imagens no registro |
| Convite de usuário por e-mail com link de uso único; SSO (OIDC/SAML) por tenant |
| Listar/encerrar sessões ativas; alerta de novo dispositivo e de anomalia em conta administrativa |
| FK `tenant_id → tenants.id` no data plane |
| Readiness com frescor e cobertura |
| Tela de Data Quality para o cliente |
| Exportações (CSV/PDF) com auditoria, marca d'água e proteção contra fórmula — **não** criadas |
| Revisão da cor do delta de sinistralidade no card executivo; `?id_contrato=` × `contrato_id` (herdados) |

## P2 — pós-V1

| Item |
|---|
| CSP com nonce no frontend (remover `'unsafe-inline'` de `script-src`) |
| Tailwind 4 (remove a cadeia de advisories de build) e Next 16 |
| Adapters Azure Blob/ADLS ou GCS se a plataforma escolhida exigir |
| Fila em Redis/SQS se o volume superar o padrão PostgreSQL |
| Schema/banco dedicado por cliente (estratégias B/C de MULTI_TENANCY) |
| Histórico de dimensões (SCD2) |
| Billing/medição de uso; preferências por usuário; i18n; tema escuro |

## DISCOVERY — ainda não implementar

Reajuste contratual · alocação gerencial de receita (rateio declarado) · "sinistralidade
individual" · previsão, ML e projeções · LLM/chatbot · conectores MV/Tasy/Benner/Datasul/TISS ·
Data Lake físico, Kafka, Kubernetes, service mesh, microserviços, Airflow, Spark, Databricks,
Data Mesh · funcionalidades clínicas.
Referências: [DISCOVERY_GESTAO_SAUDE.md](DISCOVERY_GESTAO_SAUDE.md),
[EVOLUCAO_FEEDBACK_ESPECIALISTA.md](EVOLUCAO_FEEDBACK_ESPECIALISTA.md).
