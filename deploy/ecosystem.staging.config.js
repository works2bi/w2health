// PM2 — W2Health na VPS sem Docker (staging/demonstração). Ver docs/DEPLOY_VPS.md.
//
// Segredos e URLs de banco ficam SOMENTE em backend/.env no servidor (lido pelo
// pydantic-settings a partir do cwd). A URL do papel dono do banco fica em
// backend/.env.migrate, lida só pelo passo de migration — API e worker não a recebem.
const BASE = "/srv/apps/w2data_w2health/staging";

const env = {
  ENVIRONMENT: "staging",
  TMPDIR: `${BASE}/tmp`,
  WORKER_HEARTBEAT_FILE: `${BASE}/tmp/worker.alive`,
  DATA_PLATFORM_DIR: `${BASE}/backend/data_platform`,
  PYTHONDONTWRITEBYTECODE: "1",
  PYTHONUNBUFFERED: "1",
};

module.exports = {
  apps: [
    {
      name: "w2data-stg-w2health-worker",
      cwd: `${BASE}/backend`,
      script: ".venv/bin/python",
      args: "-m app.worker",
      interpreter: "none",
      exec_mode: "fork",
      autorestart: true,
      watch: false,
      // o worker trata SIGTERM: termina o passo atual e devolve o job à fila
      kill_timeout: 30000,
      max_memory_restart: "768M",
      env,
    },
    {
      name: "w2data-stg-w2health-backend",
      cwd: `${BASE}/backend`,
      script: ".venv/bin/uvicorn",
      // bind só em loopback (nginx na frente); IP real do cliente só via proxy local
      args: [
        "app.main:app",
        "--host", "127.0.0.1",
        "--port", "8002",
        "--workers", "1",
        "--proxy-headers",
        "--forwarded-allow-ips", "127.0.0.1",
        "--no-server-header",
        "--no-access-log",
        "--limit-concurrency", "50",
        "--timeout-keep-alive", "5",
        "--timeout-graceful-shutdown", "25",
      ].join(" "),
      interpreter: "none",
      exec_mode: "fork",
      autorestart: true,
      watch: false,
      kill_timeout: 30000,
      max_memory_restart: "768M",
      env,
    },
  ],
};
