# Deploy na VPS (staging / demonstração), sem Docker

Mesmo modelo dos outros apps da VPS: build no GitHub Actions → rsync via SSH → PM2 do
usuário de deploy. Diferenças: o backend é **Python** (API + worker) e o frontend é o
**export estático** do Next servido pelo nginx (sem Node do app em runtime).

| | |
|---|---|
| URL | `https://stg-w2health.works2data.com.br` (HTTPS no Apache → nginx :80 da VPS) |
| Diretório | `/srv/apps/w2data_w2health/staging/{frontend,backend,data/raw,tmp}` |
| API | `127.0.0.1:8002` (só loopback), prefixo `/api` |
| PM2 | `w2data-stg-w2health-backend` (API), `w2data-stg-w2health-worker` (fila/uploads) |
| Banco | `w2health_stg`; papéis `w2health_stg_owner` / `w2health_stg_app` / `w2health_stg_pipeline` |
| Pipeline | `.github/workflows/deploy-staging.yml`, disparado por push no `main` ou manualmente |
| GitHub | iguais aos outros repos: secret `SSH_PRIVATE_KEY`; vars `SSH_HOST`, `SSH_USER` |

Arquivos de apoio em `deploy/`:
- `ecosystem.staging.config.js`;
- `remote_deploy_staging.sh`;
- `backend.staging.env.example` e `backend.staging.env.migrate.example`;
- `nginx/w2data_stg_w2health.conf`.

## Checklist (nesta ordem)

| # | Onde | O quê | Seção |
|---|---|---|---|
| 1 | VPS | criar as pastas | 1 |
| 2 | PostgreSQL (pgAdmin) | papéis + banco | 2 |
| 3 | VPS | copiar os 2 arquivos de configuração, com as senhas preenchidas | 3 |
| 4 | Apache + nginx | proxy | 4 |
| 5 | GitHub | secret + 2 variables no repo `works2bi/w2health` | 5 |
| 6 | GitHub | push no `main` (ou *Run workflow*): build, envio, migrations e PM2 automáticos | 6 |
| 7 | VPS | carga inicial da demo (uma vez) | 7 |
| 8 | navegador/VPS | verificação | 8 |

Faça os passos 1 a 5 **antes** do primeiro push. O deploy aborta se não encontrar o `.env` ou o `.env.migrate` no servidor. A partir daí, cada push no `main` faz o deploy sozinho.

## 1. Pré-requisitos na VPS (uma vez)

O Python da VPS já atende: `python3 --version` → 3.12.3, e o backend exige 3.12 ou mais. O Ubuntu, porém, **não traz o módulo de venv/pip** por padrão, e o deploy precisa dele para criar a `.venv`:

```bash
sudo apt install -y python3.12-venv
```

```bash
mkdir -p /srv/apps/w2data_w2health/staging/{frontend,backend,data/raw,tmp}
```

## 2. PostgreSQL (uma vez, como superusuário do Postgres)

O app usa **três papéis**. A configuração de staging recusa subir com um papel só:
- **owner:** dono das tabelas, usado só nas migrations;
- **app:** usado pela API, sujeito a Row-Level Security;
- **pipeline:** usado pelo worker, também sob RLS.

Nenhum deles precisa de privilégio de cluster.

Execute no **pgAdmin**, conectado ao banco `postgres` como superusuário, em **3 etapas separadas**. Em cada etapa, selecione o bloco e execute. O `CREATE DATABASE` não pode rodar junto com outros comandos.

**Etapa 1: papéis.** Troque as 3 senhas. São as mesmas que vão nos marcadores `__PREENCHER_SENHA_*__` do `.env` e do `.env.migrate`.

```sql
CREATE ROLE w2health_stg_owner    LOGIN PASSWORD '<SENHA_OWNER>'    NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS CONNECTION LIMIT 5;
CREATE ROLE w2health_stg_app      LOGIN PASSWORD '<SENHA_APP>'      NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS CONNECTION LIMIT 20;
CREATE ROLE w2health_stg_pipeline LOGIN PASSWORD '<SENHA_PIPELINE>' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS CONNECTION LIMIT 10;
ALTER ROLE w2health_stg_app      SET statement_timeout = '30s';
ALTER ROLE w2health_stg_pipeline SET statement_timeout = '15min';
SHOW server_version;   -- anote: se for < 15, faça a etapa 4
```

**Etapa 2: banco.** Execute sozinho.

```sql
CREATE DATABASE w2health_stg OWNER w2health_stg_owner ENCODING 'UTF8' TEMPLATE template0;
```

**Etapa 3: acesso.**

```sql
REVOKE ALL ON DATABASE w2health_stg FROM PUBLIC;
GRANT CONNECT ON DATABASE w2health_stg TO w2health_stg_app, w2health_stg_pipeline;
```

**Etapa 4: só se o PostgreSQL for < 15.** Abra um Query Tool **no banco `w2health_stg`** e execute:

```sql
ALTER SCHEMA public OWNER TO w2health_stg_owner;
```

No `psql`, a alternativa a `PASSWORD '...'` é criar o papel sem senha e usar `\password <papel>`. Assim o hash é gerado no cliente e a senha não passa em texto pelo servidor.

**Senhas:** prefira senhas sem `@ : / ? # %`, porque vão dentro de URL.

**Migrations sem superusuário:** como os papéis são criados aqui, o deploy roda as migrations com `DB_ROLES_PROVISIONED=true`.
- Elas só **verificam** os papéis e falham se algum tiver SUPERUSER, BYPASSRLS, CREATEROLE ou CREATEDB.
- Nunca executam `CREATE/ALTER ROLE`, então o owner não precisa ser superusuário.

## 3. Arquivos de configuração no servidor (uma vez, manual)

Os dois arquivos já foram gerados, com os segredos do app preenchidos, em `deploy/secrets/staging/`. Essa pasta é ignorada pelo git e **não vai para o repositório**. Antes de copiar:
1. Abra os dois arquivos e troque os 3 marcadores `__PREENCHER_SENHA_*__` pelas senhas definidas na seção 2:
   - `backend.env`: `__PREENCHER_SENHA_APP__` e `__PREENCHER_SENHA_PIPELINE__`;
   - `backend.env.migrate`: `__PREENCHER_SENHA_OWNER__`.
2. Copie para a VPS **renomeando** (WinSCP, ou `scp` como abaixo):

| Arquivo local | Destino na VPS |
|---|---|
| `deploy/secrets/staging/backend.env` | `/srv/apps/w2data_w2health/staging/backend/.env` |
| `deploy/secrets/staging/backend.env.migrate` | `/srv/apps/w2data_w2health/staging/backend/.env.migrate` |

```bash
scp deploy/secrets/staging/backend.env         ubuntu@<vps>:/srv/apps/w2data_w2health/staging/backend/.env
scp deploy/secrets/staging/backend.env.migrate ubuntu@<vps>:/srv/apps/w2data_w2health/staging/backend/.env.migrate
ssh ubuntu@<vps> "chmod 600 /srv/apps/w2data_w2health/staging/backend/.env*"
```

- **`.env`:** configuração de runtime da API e do worker.
- **`.env.migrate`:** só a URL do owner. O deploy carrega esse arquivo apenas no passo de migration, então a API e o worker não recebem essa credencial.
- **Guarde uma cópia dos dois arquivos.** Trocar a `DATA_ENCRYPTION_KEY` depois invalida os MFA e os segredos já gravados.
- **Modelos sem valores**, para referência: `deploy/backend.staging.env.example` e `deploy/backend.staging.env.migrate.example`.

As URLs usam `@/w2health_stg?host=127.0.0.1`, com o host na query. A configuração de staging recusa `@localhost` e `@127.0.0.1` dentro da URL. Este formato conecta por TCP local, igual aos outros apps, sem mexer no `pg_hba`.

O deploy não sobrescreve `.env`, `.env.migrate`, `.venv`, `data/` nem `tmp/`.

## 4. Apache e nginx (uma vez)

**Cadeia:** navegador → **Apache** (HTTPS, outro servidor) → **nginx :80 da VPS** → uvicorn `127.0.0.1:8002`. O certificado fica no Apache, então o nginx não precisa de certbot.

**Apache (vhost 443 de `stg-w2health.works2data.com.br`):** igual aos outros sites. A regra é encaminhar tudo para o nginx da VPS **sem mexer no caminho**: o `/api` precisa chegar ao nginx.

```apache
ProxyPreserveHost On
ProxyTimeout 300
ProxyPass        / http://10.0.0.133/
ProxyPassReverse / http://10.0.0.133/
```

- **`ProxyPreserveHost On` é obrigatório.** A API recusa hosts fora de `TRUSTED_HOSTS` com 400.
- **Upload:** não deixe `LimitRequestBody` abaixo de 110 MB nesse vhost. O padrão do Apache 2.4 já é maior.

**nginx:** crie o arquivo e ative o site.

```bash
sudo nano /etc/nginx/sites-available/w2data_stg_w2health.conf     # colar o conteúdo abaixo
sudo ln -sf /etc/nginx/sites-available/w2data_stg_w2health.conf /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

Conteúdo completo (o mesmo de `deploy/nginx/w2data_stg_w2health.conf`):

```nginx
# W2Health — staging/demonstração. docs/DEPLOY_VPS.md
# Cadeia: navegador → Apache (HTTPS, outro servidor) → este nginx :80 → uvicorn 127.0.0.1:8002
# Frontend = export estático do Next (sem Node em runtime). Arquivo autocontido.

limit_req_zone $binary_remote_addr zone=w2data_stg_w2health_api:10m    rate=10r/s;
limit_req_zone $binary_remote_addr zone=w2data_stg_w2health_login:10m  rate=10r/m;
limit_req_zone $binary_remote_addr zone=w2data_stg_w2health_upload:10m rate=6r/m;

upstream w2data_stg_w2health_api {
  server 127.0.0.1:8002;
  keepalive 32;
}

server {
  listen 80;
  server_name stg-w2health.works2data.com.br;

  root /srv/apps/w2data_w2health/staging/frontend;
  index index.html;

  access_log /var/log/nginx/w2data_stg_w2health.access.log;
  error_log  /var/log/nginx/w2data_stg_w2health.error.log warn;

  include /etc/nginx/snippets/gzip.conf;

  # IP real do cliente: o Apache envia X-Forwarded-For. Só confia nele vindo da rede interna
  # (ajuste para o IP/rede do servidor Apache). Sem isso o rate limit veria só o Apache.
  set_real_ip_from 10.0.0.0/24;
  real_ip_header X-Forwarded-For;
  real_ip_recursive on;

  server_tokens off;
  # corpo pequeno por padrão; só a rota de upload aceita mais (abaixo)
  client_max_body_size 1m;
  client_body_timeout 30s;
  limit_req_status 429;

  # Headers de segurança do frontend (equivalem ao headers() do next.config.mjs, que não vale
  # no export). Ficam no server e as locations NÃO usam add_header (usam `expires`), para não
  # perder a herança. A API envia os próprios headers além destes.
  add_header Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'" always;
  add_header X-Content-Type-Options "nosniff" always;
  add_header X-Frame-Options "DENY" always;
  add_header Referrer-Policy "strict-origin-when-cross-origin" always;
  add_header Permissions-Policy "camera=(), microphone=(), geolocation=(), payment=(), usb=()" always;
  add_header Cross-Origin-Opener-Policy "same-origin" always;
  add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;

  # ------------------------------------------------------------------------------- API
  # proxy_pass SEM barra no final: o prefixo /api é repassado (o backend monta as rotas em
  # /api e o cookie de sessão tem Path=/api/auth — remover o prefixo quebra o login).
  location /api/ {
    limit_req zone=w2data_stg_w2health_api burst=20 nodelay;
    proxy_pass http://w2data_stg_w2health_api;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-Proto https;
    proxy_connect_timeout 5s;
    proxy_send_timeout 60s;
    proxy_read_timeout 60s;
  }

  # força bruta: login, MFA e troca de senha
  location ~ ^/api/auth/(login|mfa/verify|mfa/confirm|mfa/setup|password/) {
    limit_req zone=w2data_stg_w2health_login burst=10 nodelay;
    proxy_pass http://w2data_stg_w2health_api;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-Proto https;
    proxy_connect_timeout 5s;
    proxy_send_timeout 60s;
    proxy_read_timeout 60s;
  }

  # upload de pacotes (Admin → Integrações). Sem buffer: a API recusa token inválido ou
  # tamanho acima do limite antes de ler o corpo.
  location ~ ^/api/admin/tenants/[^/]+/sources/[^/]+/uploads/?$ {
    limit_req zone=w2data_stg_w2health_upload burst=3 nodelay;
    client_max_body_size 110m;
    proxy_request_buffering off;
    proxy_pass http://w2data_stg_w2health_api;
    proxy_http_version 1.1;
    proxy_set_header Connection "";
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-Proto https;
    proxy_connect_timeout 5s;
    proxy_send_timeout 300s;
    proxy_read_timeout 300s;
  }

  # -------------------------------------------------------------------------- frontend
  # assets com hash no nome: cache longo
  location /_next/static/ {
    expires max;
    try_files $uri =404;
  }

  # Rotas de detalhe [id]: o export gera UMA página (placeholder "_") e o id real é lido da
  # URL no navegador. O payload RSC de id não pré-gerado responde 404 → o Next recarrega a
  # página na mesma URL, que cai na regra seguinte.
  location ~ ^/(beneficiarios|contratos|prestadores|admin/tenants)/[^/]+/index\.txt$ {
    return 404;
  }
  location ~ ^/(beneficiarios|contratos|prestadores|admin/tenants)/[^/]+/?$ {
    expires -1;
    try_files /$1/_/index.html =404;
  }

  # demais páginas (cada rota é um index.html gerado no build)
  location / {
    expires -1;
    try_files $uri $uri/index.html $uri.html =404;
  }

  error_page 404 /404.html;

  # arquivos ocultos
  location ~ /\. {
    deny all;
  }
}
```

**O que mudou em relação ao modelo dos outros sites, e por quê:**

| Item | Motivo |
|---|---|
| `proxy_pass http://w2data_stg_w2health_api;` **sem barra no final** | o backend monta as rotas em `/api` e o cookie de sessão tem `Path=/api/auth`. Com a barra, o nginx remove o `/api` e o login quebra |
| `set_real_ip_from 10.0.0.0/24` + `real_ip_header X-Forwarded-For` | o IP do cliente vem do Apache. Sem isso, o rate limit trataria todos como o IP do Apache. Ajuste a rede se o Apache estiver fora de `10.0.0.0/24` |
| `X-Forwarded-Proto https` fixo | o TLS termina no Apache. A API precisa saber que a origem é HTTPS |
| `client_max_body_size 1m` global, 110 MB só no upload, sem buffer | a API recusa upload sem token ou acima do limite antes de ler o corpo |
| `limit_req` (API geral, login/MFA, upload) | defesa contra força bruta e abuso; a API também tem rate limit e bloqueio de conta |
| Headers de segurança no `server`, `expires` nas locations | o `headers()` do Next não vale no export. Usar `add_header` dentro das locations cancelaria os headers herdados; por isso não se usam `security_headers.conf`, `proxy_api.conf` nem `spa_static.conf` |
| Rotas `/beneficiarios/<id>`, `/contratos/<id>`, `/prestadores/<id>`, `/admin/tenants/<id>` | o export gera uma página única (placeholder `_`) e o id é lido da URL. Abrir um detalhe recarrega a página |
| Sem as regras do Angular (`ngsw`, `manifest`) | o frontend é Next, não Angular |

## 5. GitHub (uma vez)

No repositório **`works2bi/w2health`**: *Settings → Secrets and variables → Actions*. São os mesmos nomes dos outros repos (`demo_portal`, `gestao-prime`). Se lá estiverem no nível do repositório, é preciso criá-los também aqui; se estiverem na organização, basta liberar o acesso a este repo.

| Tipo | Aba | Nome | Valor |
|---|---|---|---|
| Secret | *Secrets* | `SSH_PRIVATE_KEY` | a mesma chave privada usada nos outros repos (conteúdo completo, com `-----BEGIN/END ...-----`) |
| Variable | *Variables* | `SSH_HOST` | o mesmo host/IP da VPS dos outros repos |
| Variable | *Variables* | `SSH_USER` | `ubuntu` |

Nenhuma senha de banco vai para o GitHub: elas ficam só nos arquivos da seção 3.

## 6. Deploy

1. Push no `main` (automático) ou Actions → **Deploy Staging** → *Run workflow* (manual).
2. O build e o envio ficam iguais aos dos outros apps.
3. Na VPS, o `deploy/remote_deploy_staging.sh`:
   1. cria/atualiza a `.venv`, instalando o lock com hashes e só wheels;
   2. roda `alembic upgrade head` e `sync-catalog` com o `.env.migrate`;
   3. faz `pm2 delete`/`start` do ecosystem e `pm2 save`;
   4. espera `/health/ready` responder.

## 7. Carga inicial da demonstração (uma vez, após o 1º deploy)

O seed sintético grava em massa com `COPY`, que o PostgreSQL não aceita em tabela com Row-Level Security forçada. Por isso, **só durante o seed**, o owner recebe `BYPASSRLS`. Isso não é necessário nos deploys, nas migrations nem no uso normal.

**a) pgAdmin (superusuário):**

```sql
ALTER ROLE w2health_stg_owner BYPASSRLS;
```

**b) VPS:** substitua os valores entre `<>`, **sem** os sinais `<` e `>`, que no bash viram redirecionamento.

```bash
cd /srv/apps/w2data_w2health/staging/backend
set -a; . ./.env.migrate; set +a
.venv/bin/python -m app.saas.cli bootstrap-demo          # planos + usuários demo (senhas exibidas UMA vez)
.venv/bin/python -m app.saas.cli create-superadmin --email "seu.email@works2data.com.br" --name "Seu Nome"   # opcional; MFA no 1º login
.venv/bin/python -m app.seed.run --beneficiarios 20000   # dados sintéticos do tenant demo
.venv/bin/python -m app.seed.run --tenant w2h-demo-b --tenant-name "Operadora Horizonte" --seed 7   # opcional
.venv/bin/python -m app.saas.cli bootstrap-demo          # de novo, só se semeou o w2h-demo-b: cria admin@horizonte e o plano dele
exit   # ou feche o shell: não deixe DATABASE_ADMIN_URL exportada
```

**c) pgAdmin: devolva a restrição.**

```sql
ALTER ROLE w2health_stg_owner NOBYPASSRLS;
```

**Observações:**
- **Rodar de novo:** o seed apaga e regera os dados daquele tenant, então pode ser repetido.
- **Superadmin:** o `bootstrap-demo` já cria o `superadmin@works2data.example`. O `create-superadmin` só serve para um superadmin com e-mail real.
- **Execução repetida do `bootstrap-demo`:** é idempotente. Não recria contas existentes e só imprime as senhas das contas criadas naquela execução.

## 8. Verificação

```bash
pm2 ls                                                   # backend e worker online
ss -ltnp | grep 8002                                     # só 127.0.0.1:8002
curl -fsS http://127.0.0.1:8002/health/ready
curl -s -o /dev/null -w '%{http_code}\n' https://stg-w2health.works2data.com.br/api/health   # 200
curl -s -o /dev/null -w '%{http_code}\n' https://stg-w2health.works2data.com.br/docs         # 404
```

**No navegador:**
1. O login funciona (com MFA para o superadmin).
2. Deep links como `/contratos/<id>` abrem direto.
3. Um upload pelo Admin → Integrações é processado pelo worker.

## Cuidados

- **Não use `docker compose` nesta VPS.** O `docker-compose.yml` é de desenvolvimento e publica portas.
- **Não rode `pytest` contra o Postgres da VPS.** Os testes fazem `DROP DATABASE`.
- **Não importe backups de tenant de origem desconhecida** (`app.ops.tenant_backup import`).
- **Uso esporádico:** entre apresentações é possível dar `pm2 stop` nos dois processos. A limpeza de uploads de teste é `rm -rf /srv/apps/w2data_w2health/staging/data/raw/*`.
- **Encerramento:**
  1. `pm2 delete` dos dois processos;
  2. `DROP DATABASE w2health_stg` e `DROP ROLE` dos três papéis;
  3. remover o site do nginx e `/srv/apps/w2data_w2health`.
