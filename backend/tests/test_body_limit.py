"""Limite de corpo antes da leitura (app/security/body_limit.py) e rótulo de método nas métricas.

Não exigem PostgreSQL: as recusas acontecem antes de qualquer rota/dependência.
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.core import metrics
from app.security.body_limit import BodyLimitMiddleware
from app.security.tokens import create_access_token, create_challenge_token

UPLOAD = "/api/admin/tenants/t1/sources/7/uploads"


def _app() -> tuple[TestClient, BodyLimitMiddleware]:
    inner = FastAPI()

    @inner.post("/{path:path}")
    async def eco(request: Request) -> dict:
        return {"bytes": len(await request.body())}

    mw = BodyLimitMiddleware(inner)
    mw.limite_padrao = 1024
    mw.limite_upload = 4096
    return TestClient(mw), mw


def _bearer() -> dict[str, str]:
    token, _ = create_access_token(user_id=uuid.uuid4(), session_id=uuid.uuid4(),
                                   tenant_id=None, token_version=1)
    return {"Authorization": f"Bearer {token}"}


def test_corpo_acima_do_limite_padrao_recusado_sem_leitura():
    c, _ = _app()
    assert c.post("/api/auth/login", content=b"x" * 1024).status_code == 200
    r = c.post("/api/auth/login", content=b"x" * 1025)
    assert r.status_code == 413 and r.json()["code"] == "invalid_request"


def test_corpo_sem_tamanho_declarado_recusado():
    c, _ = _app()

    def pedacos():
        yield b"a" * 10

    assert c.post("/api/auth/login", content=pedacos()).status_code == 411


def test_upload_sem_token_valido_recusado_antes_do_corpo():
    c, _ = _app()
    assert c.post(UPLOAD, content=b"x" * 100).status_code == 401
    assert c.post(UPLOAD, content=b"x", headers={"Authorization": "Bearer lixo"}).status_code == 401
    # token de desafio (MFA) não é access token
    desafio = create_challenge_token(user_id=uuid.uuid4(), typ="mfa", token_version=1)
    r = c.post(UPLOAD, content=b"x", headers={"Authorization": f"Bearer {desafio}"})
    assert r.status_code == 401 and r.headers["www-authenticate"] == "Bearer"


def test_upload_com_token_usa_limite_proprio():
    c, _ = _app()
    r = c.post(UPLOAD, content=b"x" * 4096, headers=_bearer())
    assert r.status_code == 200 and r.json() == {"bytes": 4096}
    assert c.post(UPLOAD, content=b"x" * 4097, headers=_bearer()).status_code == 413
    # o limite maior vale só para a rota de upload
    assert c.post("/api/admin/tenants/t1/branding/logo", content=b"x" * 2048,
                  headers=_bearer()).status_code == 413


def test_get_nao_e_afetado():
    c, _ = _app()
    assert c.get("/qualquer").status_code == 405  # chegou à app interna


def test_limites_vem_da_configuracao():
    from app.core.config import get_settings

    s = get_settings()
    mw = BodyLimitMiddleware(FastAPI())
    assert mw.limite_padrao == s.request_max_body_kb * 1024
    assert mw.limite_upload > s.upload_max_file_mb * s.upload_max_files * 1024 * 1024
    assert mw.rota_upload.match(UPLOAD) and not mw.rota_upload.match(UPLOAD + "/x")


def test_metodo_arbitrario_nao_cria_serie_nova():
    metrics.observe_http("XPTO123", "(sem rota)", 405, 0.001)
    metrics.observe_http("GET", "(sem rota)", 404, 0.001)
    chaves = {m for (m, _, _) in metrics._req_total}
    assert "XPTO123" not in chaves and "OTHER" in chaves and "GET" in chaves
