#!/usr/bin/env bash
# Executado NA VPS pelo workflow "Deploy Staging" (via stdin do ssh: `bash -s`).
# Recebe APP_BASE_DIR exportada. Arquivos esperados no servidor (docs/DEPLOY_VPS.md):
#   backend/.env          — configuração de runtime (API e worker)
#   backend/.env.migrate  — só para migrations: DATABASE_ADMIN_URL, APP_DB_ROLE,
#                           PIPELINE_DB_ROLE, DB_ROLES_PROVISIONED=true

# ambiente de login (pm2 via nvm/npm global), como nos outros apps — antes do `set -u`
for f in /etc/profile "$HOME/.bash_profile" "$HOME/.profile" "$HOME/.bashrc"; do
  [ -f "$f" ] && . "$f" >/dev/null 2>&1 || true
done
[ -s "$HOME/.nvm/nvm.sh" ] && . "$HOME/.nvm/nvm.sh" >/dev/null 2>&1 || true

set -euo pipefail

: "${APP_BASE_DIR:?}"
API="w2data-stg-w2health-backend"
WORKER="w2data-stg-w2health-worker"

command -v pm2 >/dev/null 2>&1 || { echo "pm2 nao encontrado no servidor para o usuario $USER" >&2; exit 127; }
PY="$(command -v python3.12 || command -v python3 || true)"
[ -n "$PY" ] || { echo "python3 nao encontrado no servidor" >&2; exit 127; }
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' \
  || { echo "o backend exige Python >= 3.12 ($("$PY" --version))" >&2; exit 1; }

cd "$APP_BASE_DIR/backend"
[ -f .env ] || { echo "backend/.env ausente (ver docs/DEPLOY_VPS.md)" >&2; exit 1; }
[ -f .env.migrate ] || { echo "backend/.env.migrate ausente (ver docs/DEPLOY_VPS.md)" >&2; exit 1; }
for f in .env .env.migrate; do
  if [ ! -O "$f" ] || [ ! -r "$f" ]; then
    echo "backend/$f precisa pertencer ao usuario $USER e ser legivel por ele (hoje: $(stat -c '%U:%G %a' "$f"))." >&2
    echo "Corrija na VPS: sudo chown $USER:$USER $APP_BASE_DIR/backend/$f && chmod 600 $APP_BASE_DIR/backend/$f" >&2
    exit 1
  fi
  chmod 600 "$f"
done
mkdir -p "$APP_BASE_DIR/tmp" "$APP_BASE_DIR/data/raw"

echo "==> dependencias (lock com hashes)"
# venv incompleta (ex.: criada sem o pacote python3-venv, sem pip) é recriada
if ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
  rm -rf .venv
  "$PY" -m venv .venv >/dev/null 2>&1 || true
  if ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
    rm -rf .venv
    echo "nao foi possivel criar a venv com pip: falta o modulo venv/ensurepip do Ubuntu." >&2
    echo "Corrija na VPS: sudo apt install -y python3.12-venv   (e rode o deploy de novo)" >&2
    exit 1
  fi
fi
.venv/bin/python -m pip install --quiet --disable-pip-version-check --no-input \
  --require-hashes --only-binary=:all: -r requirements.lock.txt

echo "==> migrations"
# subshell: a credencial do dono do banco nao vaza para o ambiente do PM2
(
  set -a
  . ./.env.migrate
  set +a
  export ENVIRONMENT=staging DB_ROLES_PROVISIONED="${DB_ROLES_PROVISIONED:-true}"
  .venv/bin/alembic upgrade head
  .venv/bin/python -m app.saas.cli sync-catalog
)

echo "==> pm2"
pm2 delete "$WORKER" >/dev/null 2>&1 || true
pm2 delete "$API" >/dev/null 2>&1 || true
pm2 start ecosystem.staging.config.js
pm2 save

echo "==> health"
for _ in $(seq 1 20); do
  if curl -fsS --max-time 5 http://127.0.0.1:8002/health/ready >/dev/null 2>&1; then
    echo "deploy ok"
    exit 0
  fi
  sleep 3
done
curl -sS --max-time 5 http://127.0.0.1:8002/health/ready || true
pm2 logs "$API" --lines 60 --nostream || true
exit 1
