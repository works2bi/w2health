"""Fase 3 — testes de segurança de produção (hardening).

Mapa dos 15 cenários críticos pedidos para a fase → onde cada um é verificado:

 1. Tenant A não acessa B pela API ............ test_a_nao_acessa_dado_nem_ingestao_de_b (+ test_tenant_isolation)
 2. Tenant A não acessa RAW de B ............... test_a_nao_acessa_dado_nem_ingestao_de_b, test_object_storage::prefixo
 3. Worker A não herda contexto de B ........... test_worker::test_worker_nao_vaza_contexto_entre_tenants_a_b_a
 4. Job adulterado com outro tenant falha ...... test_worker::test_job_adulterado_com_tenant_diferente_falha_sem_tocar_dados
 5. source_connection de outro tenant falha .... test_pipeline_e2e::test_fonte_de_outro_tenant_nao_e_utilizavel
 6. Object key traversal falha ................. test_object_storage::test_chave_maliciosa_recusada_em_todas_as_operacoes
 7. Secret nunca aparece na API ................ test_segredo_nunca_aparece_na_api_nem_nos_logs
 8. Secret nunca aparece em logs ............... test_segredo_nunca_aparece_na_api_nem_nos_logs
 9. Viewer não dispara carga ................... test_worker::test_operacao_de_jobs_exige_super_admin
10. Tenant admin não altera plano .............. test_tenant_admin_nao_altera_plano (+ test_features)
11. Feature desabilitada bloqueia backend ...... test_features::test_feature_bloqueada_no_backend_mesmo_sem_menu
12. Capability não pronta bloqueia analytics ... test_pipeline_e2e::test_feature_contratada_mas_sem_dados_fica_indisponivel
13. Upload inválido falha ...................... test_upload_* (abaixo)
14. Refresh token inválido falha ............... test_refresh_token_invalido_falha
15. Recovery code reutilizado falha ............ test_phase2_security::test_recovery_codes_gerados_uma_vez_hash_e_uso_unico
"""

from __future__ import annotations

import importlib.util
import json
import logging
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import text

import app.main as main_mod
from app.api.v1.routes import admin_integrations
from app.core.config import Settings
from app.core.logging import JsonFormatter
from app.data_platform.paths import data_platform_dir
from app.security.rate_limit import InMemoryRateLimiter
from tests.conftest import (
    ISO_APP_ROLE,
    ISO_PIPELINE_ROLE,
    SUPERADMIN_EMAIL,
    create_tenant,
    create_user,
    drain_queue,
    iso_client,
    make_client,
    upload_and_wait,
)

pytestmark = pytest.mark.scenarios
SA_, SB_ = "sec-a", "sec-b"


def _pacote(tmp: Path, seed: int) -> dict[str, bytes]:
    p = Path(data_platform_dir()) / "examples" / "generic_operator" / "generate_package.py"
    spec = importlib.util.spec_from_file_location("w2h_pkg_sec", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.gerar(tmp, 40, seed, date(2026, 1, 1), 2, True)
    return {f.name: f.read_bytes() for f in sorted(tmp.glob("*.csv"))}


@pytest.fixture(scope="module")
def sec(iso_env, tmp_path_factory):
    for t in (SA_, SB_):
        create_tenant(iso_env, t)
    create_user(iso_env, "sec.a@iso.example", tenant=SA_, role="MANAGER")
    sa = iso_client(iso_env, SUPERADMIN_EMAIL, None)
    fontes, runs = {}, {}
    for t in (SA_, SB_):
        fontes[t] = sa.post(
            f"/api/admin/tenants/{t}/sources",
            json={
                "name": "Pacote CSV",
                "source_type": "FILE",
                "source_system": "generic_csv",
                "configuration": {"mapping_id": "generic_operator", "mapping_version": 1},
            },
        ).json()["id"]
        runs[t] = upload_and_wait(sa, t, fontes[t], _pacote(tmp_path_factory.mktemp(t), seed=77))
        assert runs[t]["status"] == "SUCCESS", runs[t]
    return {"sa": sa, "fontes": fontes, "runs": runs, "iso": iso_env, "tmp": tmp_path_factory}


# ===================================================================== 1, 2 — cross-tenant
def test_a_nao_acessa_dado_nem_ingestao_de_b(sec):
    iso = sec["iso"]
    with iso.owner() as s:
        id_b = s.execute(
            text("SELECT id FROM beneficiarios WHERE tenant_id = :t LIMIT 1"), {"t": SB_}
        ).scalar_one()
    a = iso_client(iso, "sec.a@iso.example", SA_)
    assert a.get(f"/api/beneficiarios/{id_b}").status_code == 404
    # ingestão/RAW de B pelo caminho de A: invisível (RLS) — mesmo para o SUPER_ADMIN
    run_b = sec["runs"][SB_]["ingestion_run_id"]
    assert sec["sa"].get(f"/api/admin/tenants/{SA_}/ingestion-runs/{run_b}").status_code == 404
    det = sec["sa"].get(f"/api/admin/tenants/{SB_}/ingestion-runs/{run_b}").json()
    assert all(o["storage_key"].startswith(f"tenant/{SB_}/") for o in det["lineage"]["raw_objects"])


# ===================================================================== 7, 8 — segredos
def test_segredo_nunca_aparece_na_api_nem_nos_logs(sec):
    segredo = "S3gr3d0-Que-Nao-Pode-Vazar-4821"
    registros: list[str] = []

    class Captura(logging.Handler):
        def emit(self, record):
            registros.append(JsonFormatter().format(record))

    raiz = logging.getLogger()
    h, nivel = Captura(level=logging.DEBUG), raiz.level
    raiz.addHandler(h)
    raiz.setLevel(logging.DEBUG)
    try:
        sa = sec["sa"]
        r = sa.put(f"/api/admin/tenants/{SA_}/secrets/conector-db", json={"value": segredo})
        assert r.status_code == 200 and segredo not in r.text
        r = sa.post(
            f"/api/admin/tenants/{SA_}/sources",
            json={
                "name": "Banco do cliente",
                "source_type": "DATABASE",
                "source_system": "erp_cliente",
                "configuration": {"host": "db.cliente.example", "port": 5432},
                "secret_reference": "tenant:conector-db",
            },
        )
        assert r.status_code == 201 and segredo not in r.text and "conector-db" not in r.text
        assert r.json()["has_secret"] is True
        # credencial em configuration é recusada
        r = sa.post(
            f"/api/admin/tenants/{SA_}/sources",
            json={
                "name": "Banco errado",
                "source_type": "DATABASE",
                "source_system": "erp_cliente",
                "configuration": {"host": "x", "password": segredo},
            },
        )
        assert r.status_code == 422 and segredo not in r.text
        for path in (
            f"/api/admin/tenants/{SA_}/secrets",
            f"/api/admin/tenants/{SA_}/sources",
            "/api/admin/integrations",
            f"/api/admin/tenants/{SA_}/ingestion-runs",
        ):
            assert segredo not in sa.get(path).text, path
        upload_and_wait(sa, SA_, sec["fontes"][SA_], _pacote(sec["tmp"].mktemp("s"), seed=78))
    finally:
        raiz.removeHandler(h)
        raiz.setLevel(nivel)
    assert registros, "nenhum log capturado"
    assert not [r for r in registros if segredo in r]
    with sec["iso"].owner() as s:
        assert segredo not in " ".join(
            str(d) for d in s.execute(text("SELECT details FROM audit_logs")).scalars()
        )
        cfg = s.execute(
            text(
                "SELECT configuration::text, secret_reference FROM source_connections "
                "WHERE name = 'Banco do cliente'"
            )
        ).one()
    assert (
        segredo not in cfg[0] and cfg[1] == "tenant:conector-db"
    )  # só a referência, nunca o valor


# ===================================================================== 10, 14
def test_tenant_admin_nao_altera_plano(sec, iso_env):
    create_user(iso_env, "sec.admin@iso.example", tenant=SA_, role="TENANT_ADMIN")
    c = iso_client(iso_env, "sec.admin@iso.example", SA_)
    assert c.put(f"/api/admin/tenants/{SA_}/plan", json={"plan_code": "BASIC"}).status_code == 403


def test_refresh_token_invalido_falha(iso_env):
    c = make_client(iso_env.app)
    c.cookies.set("w2h_refresh", "token-forjado-" + "x" * 40, path="/api/auth")
    r = c.post("/api/auth/refresh")
    assert r.status_code == 401 and "access_token" not in r.text


# ===================================================================== 13 — upload endurecido
def _upload(sec, files, mime="text/csv"):
    return sec["sa"].post(
        f"/api/admin/tenants/{SA_}/sources/{sec['fontes'][SA_]}/uploads",
        files=[("files", (n, d, mime)) for n, d in files.items()],
    )


@pytest.mark.parametrize(
    "nome,conteudo,mime,trecho",
    [
        ("eventos.csv", b"", "text/csv", "vazio"),
        ("eventos.csv", b"\x89PNG\r\n\x1a\n" + b"x" * 50, "text/csv", "binário"),
        ("eventos.csv", b"PK\x03\x04" + b"x" * 50, "text/csv", "binário"),
        ("eventos.csv", "id;nome\n1;Jos\xe9\n".encode("latin-1"), "text/csv", "encoding"),
        ("eventos.csv", b'id;b\n"aberto;2\n', "text/csv", "malformado"),
        (
            "eventos.csv",
            (";".join(f"c{i}" for i in range(400)) + "\n").encode(),
            "text/csv",
            "colunas",
        ),
        ("eventos.csv", b"id;obs\n1;" + b"a" * 70000 + b"\n", "text/csv", "linha maior"),
        ("planilha.xlsx", b"x;y\n1;2\n", "text/csv", "nome de arquivo"),
        ("eventos.csv.exe", b"x;y\n1;2\n", "text/csv", "nome de arquivo"),
        ("desconhecido.csv", b"x;y\n1;2\n", "text/csv", "não pertence"),
    ],
    ids=[
        "vazio",
        "png",
        "zip",
        "latin1",
        "malformado",
        "colunas",
        "linha-longa",
        "xlsx",
        "exe",
        "entidade",
    ],
)
def test_upload_invalido_falha_sem_enfileirar(sec, nome, conteudo, mime, trecho):
    r = _upload(sec, {nome: conteudo}, mime)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "FAILED" and out["job_id"] is None and trecho in out["message"], out


def test_upload_com_mime_incoerente_e_recusado(sec):
    r = _upload(sec, {"eventos.csv": b"x;y\n1;2\n"}, mime="image/png")
    assert r.status_code == 422 and "tipo de arquivo" in r.json()["detail"]


def test_upload_nome_com_caminho_nao_vira_chave_do_storage(sec):
    files = _pacote(sec["tmp"].mktemp("trav"), seed=79)
    files = {f"../../../tenant/{SB_}/{n}": d for n, d in files.items()}  # tentativa de traversal
    out = upload_and_wait(sec["sa"], SA_, sec["fontes"][SA_], files)
    det = sec["sa"].get(f"/api/admin/tenants/{SA_}/ingestion-runs/{out['ingestion_run_id']}").json()
    chaves = [o["storage_key"] for o in det["lineage"]["raw_objects"]]
    assert chaves and all(k.startswith(f"tenant/{SA_}/raw/") and ".." not in k for k in chaves)


def test_upload_limitado_por_usuario(sec, monkeypatch):
    monkeypatch.setattr(admin_integrations, "_upload_limiter", InMemoryRateLimiter(1, 3600))
    files = {"eventos.csv": b"x;y\n1;2\n"}
    assert _upload(sec, files).status_code in (200, 202)
    r = _upload(sec, files)
    assert r.status_code == 429 and r.json()["code"] == "rate_limited"
    drain_queue()


def test_formula_no_csv_e_tratada_como_texto(sec):
    """Conteúdo tipo fórmula (=, +, -, @) nunca é avaliado: fica como texto/rejeição de tipo."""
    files = _pacote(sec["tmp"].mktemp("formula"), seed=80)
    linhas = files["especialidades.csv"].decode().splitlines()
    campos = linhas[1].split(";")
    campos[1] = '=HYPERLINK("http://evil.example";"x")'.replace(";", ",")
    linhas[1] = ";".join(campos)
    files["especialidades.csv"] = ("\n".join(linhas) + "\n").encode()
    out = upload_and_wait(sec["sa"], SA_, sec["fontes"][SA_], files)
    assert out["status"] in ("SUCCESS", "PARTIAL", "FAILED")  # nunca executa; só dado
    with sec["iso"].owner() as s:
        nomes = (
            s.execute(
                text("SELECT nome FROM especialidades WHERE tenant_id = :t AND nome LIKE '=%'"),
                {"t": SA_},
            )
            .scalars()
            .all()
        )
    assert all(n.startswith("=HYPERLINK") for n in nomes)


# ===================================================================== configuração fail-closed
def _prod(**kw) -> Settings:
    base = dict(
        environment="production",
        jwt_secret_key="j" * 48,
        data_encryption_key="0" * 43 + "=",
        cookie_secure=True,
        cors_origins=["https://app.cliente.example"],
        trusted_hosts=["api.cliente.example"],
        database_url="postgresql+psycopg://w2health_app:Sx9@db.interno:5432/w2health",
        database_admin_url="postgresql+psycopg://w2health_owner:Kq2@db.interno:5432/w2health",
        database_pipeline_url="postgresql+psycopg://w2health_pipeline:Pz7@db.interno:5432/w2health",
        raw_storage_backend="s3",
        s3_bucket="w2h-raw",
        log_format="json",
        rate_limit_backend="database",
        super_admin_require_mfa=True,
    )
    from cryptography.fernet import Fernet

    base["data_encryption_key"] = Fernet.generate_key().decode()
    base.update(kw)
    return Settings(_env_file=None, **base)


def test_producao_bem_configurada_sobe():
    assert _prod().validate_for_runtime() == []


@pytest.mark.parametrize(
    "campo,valor,trecho",
    [
        ("jwt_secret_key", None, "JWT_SECRET_KEY"),
        ("data_encryption_key", "nao-e-fernet", "Fernet"),
        ("cookie_secure", False, "COOKIE_SECURE"),
        ("cors_origins", ["*"], "CORS_ORIGINS"),
        ("cors_origins", ["http://app.cliente.example"], "CORS_ORIGINS"),
        ("trusted_hosts", ["*"], "TRUSTED_HOSTS"),
        ("forwarded_allow_ips", "*", "FORWARDED_ALLOW_IPS"),
        ("super_admin_require_mfa", False, "SUPER_ADMIN_REQUIRE_MFA"),
        ("rate_limit_backend", "memory", "RATE_LIMIT_BACKEND"),
        ("log_format", "text", "LOG_FORMAT"),
        ("database_pipeline_url", None, "DATABASE_PIPELINE_URL"),
        (
            "database_admin_url",
            "postgresql+psycopg://w2health_app:Sx9@db.interno:5432/w2health",
            "mesmo papel",
        ),
        (
            "database_url",
            "postgresql+psycopg://w2health:w2health@db:5432/w2health",
            "credencial de exemplo",
        ),
        ("raw_storage_backend", "local", "RAW_STORAGE_ALLOW_LOCAL"),
        ("s3_bucket", None, "S3_BUCKET"),
    ],
)
def test_producao_fail_closed_sem_fallback_inseguro(campo, valor, trecho):
    problemas = _prod(**{campo: valor}).validate_for_runtime()
    assert any(trecho in p for p in problemas), problemas


def test_runtime_sobe_sem_credencial_do_papel_dono():
    """API/worker não usam o papel dono: a credencial dele fica fora do ambiente deles."""
    s = _prod(database_admin_url=None)
    assert s.validate_for_runtime() == []
    # quem precisa do papel dono (migrations/CLIs) falha explicitamente — sem fallback
    with pytest.raises(RuntimeError, match="DATABASE_ADMIN_URL"):
        _ = s.admin_database_url


def test_dev_sem_url_do_dono_usa_a_de_runtime():
    s = Settings(_env_file=None, environment="development", database_admin_url=None,
                 database_url="postgresql+psycopg://a:b@db:5432/x")
    assert s.admin_database_url == "postgresql+psycopg://a:b@db:5432/x"


def test_ambiente_desconhecido_nao_sobe():
    with pytest.raises(ValueError):
        Settings(_env_file=None, environment="prod")


def test_worker_nao_sobe_em_producao_com_configuracao_insegura(monkeypatch):
    from app.worker import __main__ as wmain

    monkeypatch.setattr(wmain, "get_settings", lambda: _prod(cookie_secure=False))
    monkeypatch.setattr(wmain, "configure_logging", lambda: None)
    assert wmain.main(["worker", "run"]) == 2


# ===================================================================== headers / hosts / CORS
def test_headers_de_seguranca_e_hsts(client, monkeypatch):
    r = client.get("/health/live")
    for h in (
        "X-Content-Type-Options",
        "X-Frame-Options",
        "Referrer-Policy",
        "Permissions-Policy",
        "Content-Security-Policy",
    ):
        assert h in r.headers, h
    assert "frame-ancestors 'none'" in r.headers["Content-Security-Policy"]
    assert "Strict-Transport-Security" not in r.headers  # dev: sem TLS
    monkeypatch.setattr(main_mod.settings, "hsts_enabled", True)
    assert "max-age=" in client.get("/health/live").headers["Strict-Transport-Security"]


def test_trusted_hosts_recusa_host_desconhecido_mas_libera_health(client, monkeypatch):
    monkeypatch.setattr(main_mod.settings, "trusted_hosts", ["api.cliente.example"])
    assert client.get("/api/health", headers={"Host": "evil.example"}).status_code == 400
    assert client.get("/api/health", headers={"Host": "api.cliente.example"}).status_code == 200
    assert client.get("/health/live", headers={"Host": "10.0.0.7"}).status_code == 200


def test_cors_nao_reflete_origem_desconhecida(client):
    r = client.options(
        "/api/auth/login",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert r.headers.get("access-control-allow-origin") != "https://evil.example"


def test_ip_do_cliente_nao_vem_de_x_forwarded_for_na_aplicacao(client):
    """X-Forwarded-For só é aceito pelo uvicorn vindo de FORWARDED_ALLOW_IPS — a aplicação
    não lê o cabeçalho (spoofing de IP no rate limit/auditoria)."""
    from starlette.requests import Request

    req = Request(
        {
            "type": "http",
            "client": ("10.1.2.3", 1),
            "headers": [(b"x-forwarded-for", b"6.6.6.6")],
            "path": "/",
            "method": "GET",
            "query_string": b"",
        }
    )
    assert main_mod._client_ip(req) == "10.1.2.3"


# ===================================================================== health / métricas / logs
def test_health_live_e_ready_sem_detalhes_internos(client):
    assert client.get("/health/live").json() == {"status": "ok"}
    r = client.get("/health/ready")
    corpo = r.json()
    assert r.status_code in (200, 503)
    assert set(corpo["checks"]) == {"database", "pipeline_database", "object_storage"}
    assert set(corpo["checks"].values()) <= {"ok", "fail", "disabled"}
    assert "postgresql" not in r.text and "@" not in r.text and "password" not in r.text.lower()


def test_metrics_exige_token_e_expoe_fila(client, monkeypatch):
    from pydantic import SecretStr

    monkeypatch.setattr(main_mod.settings, "metrics_token", SecretStr("tok-metrics-123"))
    assert client.get("/metrics").status_code == 401
    client.get("/api/health")
    r = client.get("/metrics", headers={"Authorization": "Bearer tok-metrics-123"})
    assert r.status_code == 200 and "w2h_http_requests_total" in r.text
    assert 'route="/api/health"' in r.text
    monkeypatch.setattr(main_mod.settings, "metrics_token", None)
    monkeypatch.setattr(main_mod.settings, "environment", "production")
    assert client.get("/metrics").status_code == 404


def test_metrics_de_fila_e_pipeline(sec, iso_env, monkeypatch):
    from pydantic import SecretStr

    monkeypatch.setattr(main_mod.settings, "metrics_token", SecretStr("tok-metrics-123"))
    c = make_client(iso_env.app)
    from app.core import metrics
    from app.db import session as session_mod

    monkeypatch.setattr(session_mod, "SessionLocal", iso_env.app)
    txt = c.get("/metrics", headers={"Authorization": "Bearer tok-metrics-123"}).text
    for m in (
        "w2h_queue_depth",
        "w2h_jobs{",
        "w2h_job_retries_total",
        "w2h_pipeline_records_total",
        "w2h_workers_alive",
        "w2h_jobs_stuck",
    ):
        assert m in txt, m
    assert SA_ not in txt and SB_ not in txt  # sem rótulo de tenant
    assert metrics  # módulo carregado


def test_log_json_tem_campos_padronizados_e_redige_sensiveis():
    rec = logging.LogRecord("app.worker", logging.INFO, "", 0, "job.finished", (), None)
    rec.event, rec.duration_ms, rec.status = "job.finished", 120, "SUCCESS"
    rec.authorization, rec.secret_key = "Bearer abc", "xyz"
    out = json.loads(JsonFormatter().format(rec))
    for campo in ("timestamp", "level", "service", "environment", "event", "duration_ms", "status"):
        assert campo in out, campo
    assert out["authorization"] == "[REDACTED]" and out["secret_key"] == "[REDACTED]"


# ===================================================================== papéis de banco
def test_papeis_de_runtime_e_pipeline_com_menor_privilegio(iso_env):
    with iso_env.owner() as s:
        for role in (ISO_APP_ROLE, ISO_PIPELINE_ROLE):
            r = s.execute(
                text(
                    "SELECT rolsuper, rolbypassrls, rolcreatedb, rolcreaterole, rolinherit "
                    "FROM pg_roles WHERE rolname = :r"
                ),
                {"r": role},
            ).one()
            assert not any(r), (role, r)
            donos = s.execute(
                text("SELECT count(*) FROM pg_tables WHERE tableowner = :r"), {"r": role}
            ).scalar_one()
            assert donos == 0, role
            assert not s.execute(
                text("SELECT has_schema_privilege(:r, 'public', 'CREATE')"), {"r": role}
            ).scalar()

        def pode(role, tabela, priv):
            return s.execute(
                text("SELECT has_table_privilege(:r, :t, :p)"), {"r": role, "t": tabela, "p": priv}
            ).scalar()

        # runtime: lê a fila, não enfileira nem altera; não escreve fatos/ingestões
        assert pode(ISO_APP_ROLE, "pipeline_jobs", "SELECT") and not pode(
            ISO_APP_ROLE, "pipeline_jobs", "INSERT"
        )
        assert not pode(ISO_APP_ROLE, "pipeline_jobs", "UPDATE")
        assert not pode(ISO_APP_ROLE, "ingestion_runs", "UPDATE")
        assert not pode(ISO_APP_ROLE, "eventos_assistenciais", "INSERT")
        assert not pode(ISO_APP_ROLE, "audit_logs", "UPDATE") and not pode(
            ISO_APP_ROLE, "audit_logs", "DELETE"
        )
        # pipeline: nada de identidade, sessões, segredos ou auditoria editável
        for t in (
            "users",
            "auth_sessions",
            "refresh_tokens",
            "tenant_secrets",
            "user_recovery_codes",
        ):
            assert not pode(ISO_PIPELINE_ROLE, t, "SELECT"), t
        assert not pode(ISO_PIPELINE_ROLE, "audit_logs", "UPDATE")
        assert not pode(ISO_PIPELINE_ROLE, "pipeline_jobs", "DELETE")
