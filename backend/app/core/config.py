"""Configuração da aplicação, carregada de variáveis de ambiente / arquivo .env.

Fundação SaaS V1: além dos metadados/CORS, concentra os parâmetros de segurança
(JWT, criptografia de segredos, cookies, bloqueio de login) e as duas URLs de banco:

* `DATABASE_URL`        — papel de RUNTIME da API (`w2health_app`, sem superusuário, sujeito
                          a Row-Level Security);
* `DATABASE_ADMIN_URL`  — papel DONO do schema (migrations, seed, CLIs). Os processos de
                          runtime (API e worker) NÃO usam esta URL: em produção/staging ela
                          fica fora do ambiente deles e é exigida só por quem a usa.

Segredos nunca têm default no código. Em ambientes de desenvolvimento, a ausência de
`JWT_SECRET_KEY` gera uma chave efêmera por processo (sessões caem a cada restart) e a
ausência de `DATA_ENCRYPTION_KEY` desabilita o cadastro de MFA/segredos com erro claro.
Em produção/staging, a aplicação **não sobe** sem eles (`validate_for_runtime`).

Fase 3 — ambientes formais: `development`, `test`, `staging`, `production`. Staging e
production são **fail-closed**: qualquer item de `validate_for_runtime` impede a subida
(API e worker). Segredos podem vir de variável de ambiente OU de arquivo montado
(`SECRETS_DIR`, um arquivo por campo — padrão Docker/Kubernetes secrets): o código não
conhece o gerenciador de segredos usado pela infraestrutura. Ver
docs/PRODUCTION_DEPLOYMENT.md §Configuração.
"""

import os
from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ENVIRONMENTS = frozenset({"development", "test", "staging", "production"})
#: Ambientes em que a configuração de segurança é obrigatória (falha na subida).
PRODUCTION_LIKE = frozenset({"production", "staging"})
#: credenciais de exemplo/dev que nunca podem chegar a produção
_CREDENCIAIS_DE_EXEMPLO = ("w2health:w2health@", "w2health_app_dev", "w2health_pipeline_dev", "minioadmin")


class Settings(BaseSettings):
    """Configurações da aplicação.

    Observação sobre `cors_origins`: por ser uma coleção, o pydantic-settings tenta
    decodificar a variável de ambiente como JSON. Portanto `CORS_ORIGINS` deve ser um
    array JSON válido, ex.: `CORS_ORIGINS=["http://localhost:3000"]`.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    project_name: str = Field(default="W2Health Intelligence")
    environment: str = Field(default="development")
    # nome lógico do processo nos logs/métricas ("api", "worker", "cli")
    service_name: str = Field(default="api")
    api_v1_prefix: str = Field(default="/api")
    version: str = Field(default="0.2.0")

    # Origens permitidas para CORS. Na variável de ambiente, informar como array JSON.
    cors_origins: list[str] = Field(default=["http://localhost:3000"])

    # ---------------------------------------------------------------- banco de dados
    # Papel de runtime da API. Em produção NÃO pode ser superusuário (RLS seria ignorado).
    database_url: str = Field(
        default="postgresql+psycopg://w2health:w2health@localhost:5432/w2health"
    )
    # Papel dono do schema — migrations, seed e CLIs. Se ausente: em dev usa `database_url`;
    # em produção/staging, quem precisar dela falha com erro claro (nunca há fallback).
    database_admin_url: str | None = Field(default=None)
    # Papel de runtime criado/garantido pela migration de RLS.
    app_db_role: str = Field(default="w2health_app")
    app_db_password: SecretStr | None = Field(default=None)

    # ------------------------------------------------------------------ autenticação
    jwt_secret_key: SecretStr | None = Field(default=None)
    jwt_issuer: str = Field(default="w2health-api")
    jwt_audience: str = Field(default="w2health-app")
    access_token_minutes: int = Field(default=15, ge=1, le=60)
    refresh_token_hours: int = Field(default=12, ge=1, le=24 * 7)
    # Validade do desafio intermediário (MFA / troca obrigatória de senha).
    challenge_token_minutes: int = Field(default=5, ge=1, le=15)

    # Equipe de plataforma (SUPER_ADMIN) obrigada a usar MFA — recomendado sempre ligado.
    super_admin_require_mfa: bool = Field(default=True)

    login_max_failures: int = Field(default=5, ge=3, le=20)
    login_lockout_minutes: int = Field(default=15, ge=1, le=24 * 60)
    # Limite por IP (janela de 1 min) nas rotas de autenticação — proteção de força bruta.
    auth_rate_limit_per_minute: int = Field(default=20, ge=1, le=1000)

    # Cookie do refresh token (httpOnly). `secure` obrigatório fora de dev.
    refresh_cookie_name: str = Field(default="w2h_refresh")
    cookie_secure: bool = Field(default=False)
    cookie_samesite: str = Field(default="strict")

    # Proxy reverso: o IP real do cliente é resolvido pelo servidor ASGI (uvicorn
    # `--proxy-headers --forwarded-allow-ips`) SOMENTE para conexões vindas dos IPs listados
    # em FORWARDED_ALLOW_IPS. A aplicação nunca lê X-Forwarded-* por conta própria.
    forwarded_allow_ips: str = Field(default="127.0.0.1")
    # Hosts aceitos (TrustedHostMiddleware). Obrigatório e sem curinga em produção.
    trusted_hosts: list[str] = Field(default=["*"])
    # HSTS (só faz sentido atrás de TLS). Ligado automaticamente em produção/staging.
    hsts_enabled: bool | None = Field(default=None)

    # Backend do limitador de tentativas: "database" (compartilhado entre processos/réplicas,
    # tabela auth_rate_limits) ou "memory" (só por processo — testes/dev isolado).
    rate_limit_backend: str = Field(default="database")

    # ------------------------------------------------------------- data platform (Fase 2)
    # Papel de PIPELINE (w2health_pipeline): escreve no data plane do tenant amarrado ao
    # PipelineContext, sujeito a RLS. Sem esta URL, pipelines não executam (nunca há
    # fallback para o papel dono/superusuário).
    database_pipeline_url: str | None = Field(default=None)
    pipeline_db_role: str = Field(default="w2health_pipeline")
    pipeline_db_password: SecretStr | None = Field(default=None)
    # Armazenamento RAW (abstração RawStorage). "local" = filesystem (DEV); adapters de
    # object storage (S3/ADLS/MinIO) entram sem mudar o pipeline.
    raw_storage_backend: str = Field(default="local")
    raw_storage_root: str = Field(default="./var/raw")
    # Diretório da Data Platform (contratos, mappings, regras de qualidade, exemplos).
    data_platform_dir: str | None = Field(default=None)
    # Limite de corpo das requisições (KB), checado pelo Content-Length ANTES de ler o corpo.
    # A rota de upload tem limite próprio (upload_max_file_mb × upload_max_files).
    request_max_body_kb: int = Field(default=1024, ge=16, le=1024 * 1024)
    # Limites do upload controlado (fonte FILE).
    upload_max_file_mb: int = Field(default=50, ge=1, le=1024)
    upload_max_files: int = Field(default=20, ge=1, le=100)
    upload_max_columns: int = Field(default=200, ge=5, le=2000)
    upload_max_line_bytes: int = Field(default=65536, ge=1024, le=10 * 1024 * 1024)
    upload_max_rows: int = Field(default=5_000_000, ge=1)
    # uploads por usuário por hora (proteção contra abuso/DoS acidental do pipeline)
    upload_rate_limit_per_hour: int = Field(default=30, ge=1, le=10_000)

    # Object storage S3-compatível (RAW_STORAGE_BACKEND=s3). Credenciais podem ficar vazias
    # para usar a cadeia padrão do SDK (perfil de instância / workload identity).
    s3_endpoint_url: str | None = Field(default=None)
    s3_region: str | None = Field(default=None)
    s3_bucket: str | None = Field(default=None)
    s3_prefix: str = Field(default="")
    s3_force_path_style: bool = Field(default=False)
    s3_access_key_id: SecretStr | None = Field(default=None)
    s3_secret_access_key: SecretStr | None = Field(default=None)
    # Filesystem local em produção só com decisão explícita (volume persistente e backup).
    raw_storage_allow_local_in_production: bool = Field(default=False)

    # ------------------------------------------------------------ worker / fila (Fase 3)
    worker_poll_seconds: float = Field(default=2.0, ge=0.05, le=60)
    job_max_attempts: int = Field(default=3, ge=1, le=10)
    job_backoff_base_seconds: int = Field(default=30, ge=0)
    job_backoff_max_seconds: int = Field(default=900, ge=0)
    # lease renovado a cada passo do pipeline; expirado = worker considerado morto
    job_lease_seconds: int = Field(default=600, ge=1)
    # tempo máximo de um job (mesmo com heartbeat) antes de ser tratado como travado
    job_max_runtime_seconds: int = Field(default=3600, ge=1)
    worker_heartbeat_file: str = Field(default="/tmp/w2h-worker.alive")

    # ------------------------------------------------------------ observabilidade
    # Token exigido em GET /metrics. Em produção sem token, /metrics fica desligado.
    metrics_token: SecretStr | None = Field(default=None)
    # Formato de log: "json" (estruturado, padrão) ou "text".
    log_format: str = Field(default="json")

    # ------------------------------------------------------------------ criptografia
    # Chave(s) Fernet para segredos em repouso (MFA, credenciais de integração).
    # Várias chaves separadas por vírgula = rotação (a 1ª cifra, todas decifram).
    data_encryption_key: SecretStr | None = Field(default=None)

    @field_validator("environment")
    @classmethod
    def _ambiente_conhecido(cls, v: str) -> str:
        v = (v or "").strip().lower()
        if v in ("docker", "local"):  # nomes históricos dos .env de desenvolvimento
            v = "development"
        if v not in ENVIRONMENTS:
            raise ValueError(f"ENVIRONMENT inválido: use um de {sorted(ENVIRONMENTS)}")
        return v

    @property
    def hsts(self) -> bool:
        return self.is_production_like if self.hsts_enabled is None else self.hsts_enabled

    @property
    def admin_database_url(self) -> str:
        if self.database_admin_url:
            return self.database_admin_url
        if self.is_production_like:
            raise RuntimeError("DATABASE_ADMIN_URL necessário para esta operação (papel dono do schema)")
        return self.database_url

    @property
    def is_production_like(self) -> bool:
        return self.environment.lower() in PRODUCTION_LIKE

    def validate_for_runtime(self) -> list[str]:
        """Problemas de configuração. Em produção/staging, qualquer item impede a subida."""
        problems: list[str] = []
        key = self.jwt_secret_key.get_secret_value() if self.jwt_secret_key else ""
        if len(key) < 32:
            problems.append("JWT_SECRET_KEY ausente ou curta (mínimo 32 caracteres)")
        if not self.data_encryption_key:
            problems.append("DATA_ENCRYPTION_KEY ausente (MFA e segredos indisponíveis)")
        if self.is_production_like and not self.cookie_secure:
            problems.append("COOKIE_SECURE deve ser true fora de desenvolvimento")
        if self.is_production_like:
            problems += self._production_problems()
        return problems

    def _production_problems(self) -> list[str]:
        """Regras fail-closed de produção/staging (sem fallback inseguro)."""
        p: list[str] = []
        if self.data_encryption_key:
            try:
                from cryptography.fernet import Fernet

                for k in self.data_encryption_key.get_secret_value().split(","):
                    Fernet(k.strip().encode())
            except Exception:  # noqa: BLE001
                p.append("DATA_ENCRYPTION_KEY não é uma chave Fernet válida")
        if not self.cors_origins:
            p.append("CORS_ORIGINS vazio")
        for o in self.cors_origins:
            if o.strip() == "*" or not o.startswith("https://"):
                p.append(f"CORS_ORIGINS deve listar origens https explícitas (inválida: {o!r})")
        if not self.trusted_hosts or any(h.strip() in ("*", "") for h in self.trusted_hosts):
            p.append("TRUSTED_HOSTS deve listar os hosts públicos explicitamente (sem '*')")
        if self.forwarded_allow_ips.strip() == "*":
            p.append("FORWARDED_ALLOW_IPS='*' confia em X-Forwarded-* de qualquer origem")
        if self.cookie_samesite.lower() not in ("strict", "lax"):
            p.append("COOKIE_SAMESITE deve ser strict ou lax")
        if not self.super_admin_require_mfa:
            p.append("SUPER_ADMIN_REQUIRE_MFA deve ser true")
        if self.rate_limit_backend != "database":
            p.append("RATE_LIMIT_BACKEND deve ser compartilhado (database)")
        if self.log_format != "json":
            p.append("LOG_FORMAT deve ser json")
        # DATABASE_ADMIN_URL não é exigida aqui: API e worker não usam o papel dono, e a
        # credencial dele não deve estar no ambiente desses processos. Se estiver definida,
        # precisa ser um papel distinto do runtime.
        if self.database_admin_url and self.database_url == self.database_admin_url:
            p.append("DATABASE_URL não pode usar o mesmo papel das migrations")
        if not self.database_pipeline_url:
            p.append("DATABASE_PIPELINE_URL ausente (pipeline exige papel próprio)")
        for u in (self.database_url, self.database_admin_url or "", self.database_pipeline_url or ""):
            if any(x in u for x in _CREDENCIAIS_DE_EXEMPLO) or "@localhost" in u or "@127.0.0.1" in u:
                p.append("URL de banco com credencial de exemplo ou host local")
                break
        if self.raw_storage_backend == "local" and not self.raw_storage_allow_local_in_production:
            p.append("RAW_STORAGE_BACKEND=local exige RAW_STORAGE_ALLOW_LOCAL_IN_PRODUCTION=true")
        if self.raw_storage_backend == "s3" and not self.s3_bucket:
            p.append("S3_BUCKET ausente")
        if self.s3_secret_access_key and any(
                x in self.s3_secret_access_key.get_secret_value() for x in _CREDENCIAIS_DE_EXEMPLO):
            p.append("credencial de object storage de exemplo")
        return p


@lru_cache
def get_settings() -> Settings:
    """Retorna uma instância única (cacheada) das configurações.

    `SECRETS_DIR` (opcional): diretório com um arquivo por campo secreto, nome = campo
    (ex.: /run/secrets/jwt_secret_key). Variável de ambiente explícita tem precedência.
    """
    secrets_dir = os.environ.get("SECRETS_DIR")
    if secrets_dir and os.path.isdir(secrets_dir):
        return Settings(_secrets_dir=secrets_dir)
    return Settings()
