"""Fixtures compartilhadas dos testes.

Fundação SaaS V1 — adaptações de INFRAESTRUTURA de teste (nenhuma asserção antiga mudou):
* o banco de testes é criado com o papel DONO (`DATABASE_ADMIN_URL`), com as duas metadatas
  (data plane + control plane);
* após o seed, o catálogo de features/planos é sincronizado, o tenant demo recebe o plano
  ENTERPRISE (o mesmo backfill da migration) e são criados usuários de teste por papel;
* `db` devolve uma sessão já amarrada ao tenant demo (os repositórios são fail-closed);
* `auth_headers` fornece um access token real de um MANAGER do tenant demo — usado pelas
  fixtures `api` dos testes de endpoint, que agora exigem autenticação.
"""

from __future__ import annotations

import os
import secrets

# Chaves EFÊMERAS para a sessão de testes quando o ambiente não define (CI/container).
# Precisam existir antes de `app.*` ser importado (configuração é cacheada).
os.environ.setdefault("JWT_SECRET_KEY", secrets.token_urlsafe(48))
# Os testes verificam os PADRÕES de segurança — não dependem de overrides do .env local.
os.environ["SUPER_ADMIN_REQUIRE_MFA"] = "true"
os.environ["RATE_LIMIT_BACKEND"] = "memory"
os.environ["LOG_FORMAT"] = "text"
os.environ["RAW_STORAGE_ROOT"] = os.path.join(
    os.environ.get("TMPDIR") or os.environ.get("TEMP") or "/tmp", f"w2h-raw-test-{secrets.token_hex(4)}")
if not os.environ.get("DATA_ENCRYPTION_KEY"):
    from cryptography.fernet import Fernet

    os.environ["DATA_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

import pytest  # noqa: E402
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.core.tenant import DEFAULT_TENANT
from app.main import create_app


@pytest.fixture
def client() -> TestClient:
    """Cliente de teste sobre uma instância isolada da aplicação."""
    return TestClient(create_app())


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    """O limitador por IP é global ao processo; cada teste começa zerado."""
    from app.api.v1.routes import admin_integrations
    from app.api.v1.routes.auth import auth_limiter

    auth_limiter.reset()
    admin_integrations._upload_limiter = None  # Fase 3: limite de uploads por usuário/hora
    yield


# --------------------------------------------------------------------------------------
# Banco de testes com massa sintética pequena e determinística (para os testes de
# cenário e de endpoints). Criado uma vez por sessão de testes em um banco separado.
# --------------------------------------------------------------------------------------
def _base_url() -> str:
    return get_settings().admin_database_url


def _test_url() -> str:
    base = _base_url()
    return os.environ.get("TEST_DATABASE_URL") or base.rsplit("/", 1)[0] + "/w2health_test"


def _db_disponivel(url: str) -> bool:
    try:
        create_engine(url).connect().close()
        return True
    except Exception:
        return False


def recreate_database(dbname: str):
    """(Re)cria um banco de testes vazio e devolve o engine (papel dono)."""
    base_url = _base_url()
    if not _db_disponivel(base_url):
        pytest.skip("PostgreSQL indisponível — testes de banco pulados")
    admin = create_engine(base_url, isolation_level="AUTOCOMMIT", connect_args=_CONNECT_ARGS)
    with _conectar(admin) as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)'))
        c.execute(text(f'CREATE DATABASE "{dbname}"'))
        # Banco de teste é EFÊMERO: sem espera de fsync do WAL a cada commit. Sob a carga da
        # suíte completa (massas sintéticas grandes), o fsync do disco virtual do Docker
        # Desktop chegou a segurar commits por >100 s — causa medida (wait_event = WALSync)
        # da intermitência de test_cargas_concorrentes_a_b_isoladas_e_processadas_uma_vez.
        # Só afeta os bancos criados aqui; produção mantém synchronous_commit padrão.
        c.execute(text(f'ALTER DATABASE "{dbname}" SET synchronous_commit = off'))
    admin.dispose()
    url = base_url.rsplit("/", 1)[0] + "/" + dbname
    engine = create_engine(url, future=True)
    aquece = create_engine(url, connect_args=_CONNECT_ARGS)  # caminho até o banco novo
    _conectar(aquece).close()
    aquece.dispose()
    return engine, url


#: Conexão nova que trava no handshake (o proxy de porta do Docker Desktop aceita o TCP e
#: não repassa ao PostgreSQL — medido com py-spy: `psycopg.wait_conn` parado em
#: `recreate_database`, sem sessão no servidor, por ~30 min) falha rápido e é refeita.
_CONNECT_ARGS = {"connect_timeout": 15}


def _conectar(engine, tentativas: int = 5):
    import time

    from sqlalchemy.exc import OperationalError

    for i in range(tentativas):
        try:
            return engine.connect()
        except OperationalError:
            if i == tentativas - 1:
                raise
            engine.dispose()
            time.sleep(2)


TEST_PASSWORD = "Teste-" + secrets.token_urlsafe(12)

#: e-mail → (papel de tenant, papel de plataforma) dos usuários do tenant demo nos testes
TEST_USERS = {
    "tenant_admin@test.example": ("TENANT_ADMIN", None),
    "manager@test.example": ("MANAGER", None),
    "viewer@test.example": ("VIEWER", None),
}


def bootstrap_saas(session: Session, tenant_id: str = DEFAULT_TENANT, plan_code: str = "ENTERPRISE",
                   users: dict | None = None) -> None:
    """Catálogo + plano do tenant + usuários de teste (idempotente)."""
    from app.models import Plan, Tenant, User, UserTenant
    from app.saas.catalog import sync_catalog
    from app.security.passwords import hash_password

    sync_catalog(session)
    t = session.get(Tenant, tenant_id)
    t.plan_id = session.execute(select(Plan.id).where(Plan.code == plan_code)).scalar_one()
    senha_hash = hash_password(TEST_PASSWORD)
    for email, (papel, plataforma) in (users or TEST_USERS).items():
        u = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if u is None:
            u = User(email=email, name=email.split("@")[0], password_hash=senha_hash,
                     platform_role=plataforma)
            session.add(u)
            session.flush()
        if papel and session.get(UserTenant, (u.id, tenant_id)) is None:
            session.add(UserTenant(user_id=u.id, tenant_id=tenant_id, role=papel))
    session.commit()


def token_for(session: Session, email: str, tenant_id: str | None) -> str:
    """Access token real (sessão persistida) — sem passar pelo fluxo de login/MFA."""
    from app.models import User
    from app.security.sessions import create_session

    u = session.execute(select(User).where(User.email == email)).scalar_one()
    emitido = create_session(session, u, tenant_id, ip="testclient", user_agent="pytest")
    session.commit()
    return emitido.access_token


@pytest.fixture(scope="session")
def seeded_sessionmaker():
    """Cria/recria `w2health_test`, aplica o schema, gera 6.000 beneficiários com cenários.

    Pula todos os testes que dependem de banco se o PostgreSQL não estiver acessível.
    """
    import app.models  # noqa: F401  (registra tabelas nas metadatas)
    from app.db.base import create_all

    dbname = _test_url().rsplit("/", 1)[1]
    engine, _ = recreate_database(dbname)
    create_all(engine)
    Maker = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    from app.seed.config import SeedConfig
    from app.seed.run import run_seed

    with Maker() as s:
        run_seed(SeedConfig(n_beneficiarios=6000, seed=42), s, verbose=False)
    with Maker() as s:
        bootstrap_saas(s)

    yield Maker
    engine.dispose()


@pytest.fixture
def db(seeded_sessionmaker) -> Session:
    from app.db.tenant_scope import bind_tenant

    s = seeded_sessionmaker()
    bind_tenant(s, DEFAULT_TENANT)
    try:
        yield s
    finally:
        s.close()


@pytest.fixture(scope="session")
def auth_headers(seeded_sessionmaker) -> dict[str, str]:
    """Cabeçalho Authorization de um MANAGER do tenant demo (todas as features: ENTERPRISE)."""
    with seeded_sessionmaker() as s:
        return {"Authorization": f"Bearer {token_for(s, 'manager@test.example', DEFAULT_TENANT)}"}


# --------------------------------------------------------------------------------------
# Banco de ISOLAMENTO (Fundação SaaS V1): dois tenants sintéticos com os MESMOS códigos de
# negócio (BEN-000001, mesmos códigos de contrato/prestador/procedimento), RLS aplicado e
# um papel de runtime de teste (NOSUPERUSER, NOBYPASSRLS). Separado do banco principal para
# não alterar o invariante "um único tenant" verificado por test_multitenancy.py.
# --------------------------------------------------------------------------------------
TENANT_A, TENANT_B = "tenant-a", "tenant-b"
ISO_USERS_A = {
    "a.admin@iso.example": ("TENANT_ADMIN", None),
    "a.manager@iso.example": ("MANAGER", None),
    "a.viewer@iso.example": ("VIEWER", None),
}
ISO_USERS_B = {
    "b.admin@iso.example": ("TENANT_ADMIN", None),
    "b.manager@iso.example": ("MANAGER", None),
}
SUPERADMIN_EMAIL = "superadmin@iso.example"
ISO_APP_ROLE = "w2health_app_test"
ISO_PIPELINE_ROLE = "w2health_pipeline_test"


@pytest.fixture(scope="session")
def iso_env():
    """SimpleNamespace(owner=sessionmaker dono, app=sessionmaker papel de runtime c/ RLS)."""
    from types import SimpleNamespace

    from sqlalchemy.engine import make_url

    import app.models  # noqa: F401
    from app.db import rls
    from app.db.base import create_all
    from app.seed.config import SeedConfig
    from app.seed.run import run_seed

    engine, url = recreate_database("w2health_test_iso")
    create_all(engine)
    senha_role = secrets.token_urlsafe(16)
    senha_pipe = secrets.token_urlsafe(16)
    with engine.begin() as c:
        rls.apply_all(c, ISO_APP_ROLE, senha_role)
        rls.apply_pipeline_role(c, ISO_PIPELINE_ROLE, senha_pipe)
    Owner = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    for code, nome, seed in ((TENANT_A, "Operadora A (sintética)", 42),
                             (TENANT_B, "Operadora B (sintética)", 7)):
        with Owner() as s:
            run_seed(SeedConfig(n_beneficiarios=1500, seed=seed, tenant_id=code), s,
                     verbose=False, tenant_name=nome)
    with Owner() as s:
        bootstrap_saas(s, TENANT_A, "ENTERPRISE", users=ISO_USERS_A)
        bootstrap_saas(s, TENANT_B, "ENTERPRISE", users=ISO_USERS_B)
        bootstrap_saas(s, TENANT_A, "ENTERPRISE", users={SUPERADMIN_EMAIL: (None, "SUPER_ADMIN")})

    app_url = make_url(url).set(username=ISO_APP_ROLE, password=senha_role)
    app_engine = create_engine(app_url, future=True)
    AppMaker = sessionmaker(bind=app_engine, autoflush=False, expire_on_commit=False)
    # Fase 2: pipelines executam com o papel de PIPELINE (sem superusuário, sob RLS)
    from app.db.pipeline import set_pipeline_engine

    pipe_engine = create_engine(make_url(url).set(username=ISO_PIPELINE_ROLE, password=senha_pipe),
                                future=True)
    set_pipeline_engine(pipe_engine)
    yield SimpleNamespace(owner=Owner, app=AppMaker, app_engine=app_engine, pipeline_engine=pipe_engine)
    set_pipeline_engine(None)
    pipe_engine.dispose()
    app_engine.dispose()
    engine.dispose()


def make_client(maker, headers: dict | None = None) -> TestClient:
    from app.db.session import get_db

    application = create_app()

    def _get_db():
        s = maker()
        try:
            yield s
        finally:
            s.close()

    application.dependency_overrides[get_db] = _get_db
    return TestClient(application, headers=headers or {})


@pytest.fixture(params=["runtime_rls", "somente_aplicacao"])
def iso_mode(request, iso_env):
    """Roda o teste duas vezes: com o papel de runtime (aplicação + RLS) e com o papel
    dono, que IGNORA RLS — provando que a camada de aplicação isola sozinha."""
    return iso_env.app if request.param == "runtime_rls" else iso_env.owner


@pytest.fixture
def client_a(iso_env, iso_mode) -> TestClient:
    with iso_env.owner() as s:
        tok = token_for(s, "a.manager@iso.example", TENANT_A)
    return make_client(iso_mode, {"Authorization": f"Bearer {tok}"})


def create_user(iso_env, email: str, *, tenant: str | None = None, role: str | None = None,
                platform_role: str | None = None, **attrs):
    """Cria (ou recria limpo) um usuário de teste no banco de isolamento."""
    from app.models import User, UserTenant
    from app.security.passwords import hash_password

    with iso_env.owner() as s:
        antigo = s.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if antigo is not None:
            s.delete(antigo)
            s.flush()
        u = User(email=email, name=email.split("@")[0], password_hash=hash_password(TEST_PASSWORD),
                 platform_role=platform_role, **attrs)
        s.add(u)
        s.flush()
        if tenant:
            s.add(UserTenant(user_id=u.id, tenant_id=tenant, role=role))
        s.commit()
        return u.id


def create_tenant(iso_env, code: str, *, status: str = "ACTIVE", plan: str = "ENTERPRISE") -> None:
    from app.models import Plan, Tenant

    with iso_env.owner() as s:
        t = s.get(Tenant, code)
        if t is None:
            t = Tenant(id=code, name=f"Tenant {code}", is_synthetic=True)
            s.add(t)
        t.status = status
        t.plan_id = s.execute(select(Plan.id).where(Plan.code == plan)).scalar_one()
        s.commit()


def login(client: TestClient, email: str, password: str | None = None, **extra):
    return client.post("/api/auth/login", json={"email": email, "password": password or TEST_PASSWORD, **extra})


def iso_client(iso_env, email: str, tenant: str | None, maker=None) -> TestClient:
    with iso_env.owner() as s:
        tok = token_for(s, email, tenant)
    return make_client(maker or iso_env.app, {"Authorization": f"Bearer {tok}"})


@pytest.fixture
def gabarito(db) -> dict:
    from app.repositories import analytics_repo as repo

    return {g["codigo"]: g for g in repo.gabarito(db)}


# --------------------------------------------------------------------------------------
# Fase 3 — fila/worker nos testes: o MESMO worker de produção, drenado no processo de teste.
# --------------------------------------------------------------------------------------
def drain_queue(max_jobs: int = 100) -> int:
    from app.worker.service import Worker

    return Worker(worker_id="pytest-worker").drain(max_jobs)


def upload_and_wait(sa, tenant: str, source_id: int, files: dict[str, bytes]) -> dict:
    """POST do upload (202 = enfileirado) → worker processa → resumo no formato da Fase 2."""
    r = sa.post(f"/api/admin/tenants/{tenant}/sources/{source_id}/uploads",
                files=[("files", (nome, data, "text/csv")) for nome, data in files.items()])
    assert r.status_code in (200, 202), r.text
    out = r.json()
    if r.status_code == 200:
        return out
    assert out["status"] == "QUEUED" and out["job_id"], out
    drain_queue()
    d = sa.get(f"/api/admin/tenants/{tenant}/ingestion-runs/{out['ingestion_run_id']}").json()
    run = d["run"]
    erro = (run.get("error_summary") or {}).get("erro")
    return {**out, "status": run["status"], "stage": run["stage"], "received": run["records_received"],
            "valid": run["records_valid"], "rejected": run["records_rejected"], "errors": run["errors"],
            "warnings": run["warnings"], "duplicate_of": run["duplicate_of"],
            "reconciliation": d["reconciliation_status"], "message": erro or "carga publicada",
            "job": d.get("job")}



# --------------------------------------------------------------------------------------
# Evolução do produto — clientes por papel no tenant demo (planos de ação, alertas, benchmark)
# --------------------------------------------------------------------------------------
def _cliente_papel(maker, email: str) -> TestClient:
    from app.db.session import get_db

    app = create_app()

    def _get_db():
        s: Session = maker()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _get_db
    with maker() as s:
        tok = token_for(s, email, DEFAULT_TENANT)
    return TestClient(app, headers={"Authorization": f"Bearer {tok}"})


@pytest.fixture
def gestor(seeded_sessionmaker) -> TestClient:
    return _cliente_papel(seeded_sessionmaker, "manager@test.example")


@pytest.fixture
def leitor(seeded_sessionmaker) -> TestClient:
    return _cliente_papel(seeded_sessionmaker, "viewer@test.example")
