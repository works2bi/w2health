# W2Health Intelligence

**Decision Intelligence Platform for Healthcare**

Plataforma que **identifica e explica automaticamente** as principais causas das variações
da sinistralidade de uma operadora de saúde, correlacionando dados financeiros e
assistenciais **até o nível de prestadores, procedimentos e beneficiários**.

> 🔐 **Fundação SaaS V1:** multi-tenant real (aplicação + Row-Level Security), login com MFA,
> RBAC, planos/features, branding por tenant, auditoria e áreas administrativas
> (`/admin` Works2Data, `/gestao` do tenant). Ver [docs/V1_SAAS_FOUNDATION.md](docs/V1_SAAS_FOUNDATION.md)
> e [docs/SECURITY_AND_TENANT_ISOLATION.md](docs/SECURITY_AND_TENANT_ISOLATION.md).

> ⚠️ **Ambiente demonstrativo — todos os dados são sintéticos.** Nenhuma informação de
> pessoa real é utilizada. Operadora fictícia: **Vida Plena**.

O diferencial não é calcular a sinistralidade (qualquer BI faz). É **explicar a variação**:
separar efeito frequência de efeito custo médio, achar os prestadores/procedimentos/
beneficiários responsáveis e gerar insights derivados matematicamente dos dados.

> **v1.1** aprofunda a árvore de explicação: coortes de beneficiários com evidências
> rotuladas Fato/Hipótese/A investigar, composição financeira (bruta/glosa/
> coparticipação/líquida) e configuração de alertas. Ver [docs/V1.1.md](docs/V1.1.md).

---

## Stack

| Camada   | Tecnologia |
|----------|------------|
| Frontend | Next.js 14 (App Router) · TypeScript · Tailwind CSS · Recharts · SWR |
| Backend  | Python 3.12 · FastAPI · SQLAlchemy 2.0 · Alembic · Pydantic v2 · NumPy · `uv` |
| Banco    | PostgreSQL 16 (camada fonte + camada analítica dimensional/agregada) |
| Infra    | Docker + Docker Compose |

Documentos V1 SaaS: [Fundação](docs/V1_SAAS_FOUNDATION.md) · [Baseline](docs/V1_SAAS_BASELINE.md) ·
[Segurança e isolamento](docs/SECURITY_AND_TENANT_ISOLATION.md) · [Threat model](docs/THREAT_MODEL_V1.md) ·
[RBAC](docs/RBAC_MATRIX.md) · [Features](docs/FEATURE_CATALOG.md) ·
[Evolução do banco](docs/DATABASE_EVOLUTION_V1.md) · [Roadmap V1](docs/V1_ROADMAP.md)

Documentos anteriores: [v1.2](docs/V1.2.md) · [v1.1](docs/V1.1.md) · [MVP](docs/MVP.md) ·
[Arquitetura](docs/ARCHITECTURE.md) ·
[Modelo de dados](docs/DATA_MODEL.md) · [Motor analítico](docs/ANALYTICS_ENGINE.md) ·
[Dados sintéticos](docs/SYNTHETIC_DATA.md) · [Backlog](docs/BACKLOG.md) ·
[Roteiro de demonstração](docs/DEMO.md) · [Decisões do MVP](docs/DECISOES-MVP.md) ·
[Planejamento](docs/00-PLANEJAMENTO-MVP.md)

---

## Pré-requisitos

- **Docker Desktop** com o daemon em execução (inclui Docker Compose v2).
- Portas livres no host: **3000** (frontend), **8010** (backend), **15432** (Postgres).
  Ajustáveis em `.env` (`FRONTEND_PORT`, `BACKEND_PORT`, `POSTGRES_PORT`).
- Para desenvolvimento fora de container (opcional): `uv` e Node 20+.

---

## Como iniciar do zero

```bash
git clone <repo> && cd w2health
./scripts/gen-secrets.ps1            # cria .env com segredos ALEATÓRIOS (JWT, criptografia,
                                     # senha do papel de runtime). Nunca versione o .env.

docker compose up -d --build         # sobe postgres + backend + frontend
                                     # o backend aplica as migrations no start (inclui RLS
                                     # e o papel de banco w2health_app)

docker compose exec backend python -m app.seed.run --beneficiarios 20000
                                     # massa sintética "Operadora Vida Plena" (tenant w2h-demo)
docker compose exec backend python -m app.saas.cli bootstrap-demo
                                     # planos/features + usuários demo; senhas exibidas UMA vez
```

Abra **http://localhost:3000/login**. Usuários demo: `admin@vidaplena.example`
(administrador do tenant), `gestor@vidaplena.example`, `leitor@vidaplena.example`,
`superadmin@works2data.example` (Works2Data — MFA obrigatório no primeiro login).
Opcional: `make seed-tenant-b` cria um 2º tenant sintético (plano BASIC) com os mesmos
códigos de negócio, para demonstrar isolamento e gating por plano.

| Serviço  | URL |
|----------|-----|
| Frontend | http://localhost:3000 |
| API      | http://localhost:8010/api/health |
| Swagger  | http://localhost:8010/docs (desligado em produção/staging) |
| Postgres | `127.0.0.1:15432` — dono `w2health`; a API usa o papel `w2health_app` (RLS) |

### Atalhos

```bash
make up            # docker compose up -d --build
make migrate       # alembic upgrade head (dentro do container)
make seed          # 20.000 beneficiários
make seed-full     # 100.000 beneficiários
make rebuild-agg   # recontrói só as tabelas agg_* a partir da fato bruta
make test          # suíte de testes do backend (em container)
make down          # para (mantém o banco) · make clean = down -v (apaga o banco)
```

No Windows sem `make`: `./scripts/up.ps1`, `./scripts/down.ps1`, `./scripts/seed.ps1`,
`./scripts/test-backend.ps1`.

---

## Como executar migrations

```bash
docker compose exec backend alembic upgrade head      # aplicar
docker compose exec backend alembic revision --autogenerate -m "mensagem"   # criar nova
```
Fora do container: `cd backend && uv run alembic upgrade head` (usa `backend/.env`).

---

## Como gerar dados

```bash
# dentro do container (recomendado)
docker compose exec backend python -m app.seed.run \
    --beneficiarios 20000 --seed 42 --inicio 2025-01 --fim 2026-12

# 100 mil beneficiários
docker compose exec backend python -m app.seed.run --beneficiarios 100000

# sem os cenários intencionais (base "limpa")
docker compose exec backend python -m app.seed.run --no-cenarios
```
Reprodutível por `--seed`. O seed é **por tenant** (`--tenant`, padrão `w2h-demo`): limpa e
recria os dados só daquele tenant e, ao final, reconstrói a
camada analítica (`agg_*`) e grava o `seed_manifest` e o `cenarios_gabarito`. O gerador é
registrado como fonte `SYNTHETIC` com ingestão e linhagem (Caminho A).

### Carga de uma fonte externa (Fase 2 — arquivo CSV)

Pela interface: Admin → **Integrações** (SUPER_ADMIN) → fonte FILE → Importação controlada.
Pela CLI (tenant sempre explícito; usa o papel `w2health_pipeline`):

```bash
docker compose exec backend python -m app.data_platform.cli register-source     --tenant <tenant> --name "Pacote CSV" --source-system generic_csv --mapping generic_operator --version 1
docker compose exec backend python -m app.data_platform.cli ingest     --tenant <tenant> --source <id> --dir /data_platform/examples/generic_operator/pacote --by cli:operador
```
Arquitetura e operação: [docs/PHASE2_DATA_PLATFORM.md](docs/PHASE2_DATA_PLATFORM.md).
Verificações operacionais: `scripts/security-check.ps1` (dependências) e
`scripts/backup-restore-check.ps1` (backup/restore local — não é backup de produção).

---

## Como rodar os testes

```bash
docker compose exec backend pytest                     # tudo
docker compose exec backend pytest tests/test_formulas.py     # fórmulas (sem banco)
docker compose exec backend pytest -m scenarios              # detecção de cenários + API
```
Os testes de cenário/API criam um banco separado `w2health_test`, aplicam o schema e
geram uma massa pequena (6.000 beneficiários, seed 42). Se o PostgreSQL não estiver
acessível, esses testes são **pulados** (os de fórmula continuam rodando).

Fora do container: `cd backend && uv sync && uv run pytest`.

---

## Produção (Fase 3)

* O compose de desenvolvimento sobe também o **worker** (`docker compose up -d`): cargas de
  arquivo são enfileiradas pela API (resposta `202` + `job_id`) e processadas por ele.
  Operação da fila: Admin → Integrações → Fila; `docker compose exec worker python -m app.worker drain`.
* Referência de deployment "como produção" (TLS no proxy, migrations em job separado, papéis
  de banco distintos, object storage S3, segredos por arquivo, contêineres não-root):
  `python deploy/gen_prod_secrets.py` e
  `docker compose -f docker-compose.production.example.yml up -d --build` —
  ver [docs/PRODUCTION_DEPLOYMENT.md](docs/PRODUCTION_DEPLOYMENT.md).
* Staging/demonstração em VPS **sem Docker** (frontend estático no nginx, API + worker no
  PM2, papéis de banco provisionados pelo DBA):
  `.github/workflows/deploy-staging.yml` — ver [docs/DEPLOY_VPS.md](docs/DEPLOY_VPS.md).
* Testes em contêiner: `docker compose --profile test run --rm --build backend-test`.
* CI: `.github/workflows/ci.yml`. Gate de release: [docs/PRODUCTION_RELEASE_CHECKLIST.md](docs/PRODUCTION_RELEASE_CHECKLIST.md).
* Restauração de um tenant: `python -m app.ops.tenant_backup --help`.
* Estado e classificação: [docs/PHASE3_PRODUCTION_READINESS.md](docs/PHASE3_PRODUCTION_READINESS.md).

---

## Estrutura de diretórios

```
w2health/
├─ docker-compose.yml · .env.example · Makefile · scripts/*.ps1
├─ docs/                     # V1.1, MVP, ARCHITECTURE, DATA_MODEL, ANALYTICS_ENGINE,
│                            # SYNTHETIC_DATA, BACKLOG, DEMO, EVOLUCAO_FEEDBACK_ESPECIALISTA
├─ backend/
│  ├─ Dockerfile · pyproject.toml · uv.lock · requirements.txt · alembic/
│  └─ app/
│     ├─ main.py                    # create_app()
│     ├─ core/                      # config, logging, faixas etárias
│     ├─ db/                        # engine, sessão, Base
│     ├─ models/                    # ORM: catálogos, carteira, eventos, camada analítica, config
│     ├─ schemas/                   # Pydantic (endpoints de escrita — regras de alerta)
│     ├─ repositories/              # consultas SQL sobre agg_* e fato bruta (+ config_repo)
│     ├─ analytics/                 # MOTOR: formulas, sinistralidade, decomposition,
│     │                             #        procedimentos, providers, seasonality, insights,
│     │                             #        cohorts (v1.1), indicadores + alerts (v1.1)
│     ├─ api/v1/routes/             # endpoints (executive, sinistralidade, prestadores,
│     │                             #            ..., config [v1.1])
│     └─ seed/                      # gerador: config, catalogs, affinities, generator,
│                                   #          scenarios, aggregate, run
│  └─ tests/                        # test_formulas, test_scenarios, test_api, test_health,
│                                   # test_cohorts (v1.1), test_alerts (v1.1)
└─ frontend/
   ├─ Dockerfile · package.json · tailwind.config.ts
   └─ src/
      ├─ app/                       # / , /sinistralidade , /prestadores[/id] , /beneficiarios[/id] ,
      │                             # /insights , /configuracao/insights (v1.1)
      ├─ components/                # shell/, ui, charts, InsightCard, CausasPanel (v1.1),
      │                             # CompositionCard (v1.1)
      └─ lib/                       # api, useApi (SWR), filters (contexto global), format
```

---

## Fluxo de investigação (a tese do produto)

```
Visão Executiva → "a sinistralidade aumentou" → Por quê? (explain por dimensão)
   → grupo / especialidade / procedimento → bridge frequência × custo médio
   → prestadores responsáveis → beneficiários / eventos → Insight rastreável
```

Cada número exibido tem uma fórmula no backend (`app/analytics/formulas.py`) e cada
insight traz a seção **"Como calculamos"**.
