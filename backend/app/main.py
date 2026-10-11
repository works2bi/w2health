"""Ponto de entrada da aplicação FastAPI do W2Health Intelligence.

Fundação SaaS V1: autenticação, contexto de tenant, RBAC, features, auditoria e
administração. Camadas transversais registradas aqui:

* middleware de requisição — `X-Request-ID`, IP/User-Agent para auditoria, headers de
  segurança e `Cache-Control: no-store` (respostas com dado de tenant nunca vão para cache
  compartilhado);
* tratadores de erro — corpo padronizado `{"detail", "code"}`; exceção não tratada vira
  500 genérico com `request_id` (sem stack trace, sem estrutura interna);
* validação de configuração — em produção/staging a API não sobe sem segredos.

Fase 3: Trusted Hosts, HSTS/CSP na API, IP real só via proxy confiável (uvicorn
`--proxy-headers --forwarded-allow-ips`), log de acesso estruturado com `duration_ms`,
métricas (`/metrics`) e health de liveness/readiness (`/health/live`, `/health/ready`).
Limite de corpo e token no upload checados antes de ler o corpo (`app/security/body_limit.py`).
"""

import hmac
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1.router import api_v1_router
from app.core import metrics
from app.core.config import get_settings
from app.core.logging import clear_log_context, configure_logging, set_log_context
from app.core.tenant import TenantContextMissing
from app.saas.audit import RequestMeta, set_request_meta
from app.security.body_limit import BodyLimitMiddleware
from app.security.errors import ApiError

settings = get_settings()
log = logging.getLogger("app")

_REQ_ID = re.compile(r"^[A-Za-z0-9._-]{8,40}$")
_CODE_BY_STATUS = {400: "invalid_request", 401: "not_authenticated", 403: "forbidden",
                   404: "not_found", 405: "invalid_request", 409: "conflict",
                   422: "invalid_request", 429: "rate_limited", 503: "service_unavailable"}

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-site",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
    # a API só devolve JSON/arquivos: nada pode ser executado nem emoldurado a partir dela
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'; base-uri 'none'",
}
_HEALTH = ("/health/live", "/health/ready")
log_http = logging.getLogger("app.http")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> Any:
    """Startup: logging + validação de configuração de segurança."""
    configure_logging()
    problemas = settings.validate_for_runtime()
    if problemas and settings.is_production_like:
        raise RuntimeError("Configuração de segurança inválida: " + "; ".join(problemas))
    for p in problemas:
        log.warning("configuração (desenvolvimento): %s", p)
    yield


def _client_ip(request: Request) -> str | None:
    """IP do cliente. Atrás de proxy, o uvicorn já substituiu `client.host` pelo IP de
    X-Forwarded-For — e SÓ quando a conexão veio de um IP em FORWARDED_ALLOW_IPS. A
    aplicação nunca lê o cabeçalho diretamente (evita spoofing de IP no rate limit/auditoria)."""
    return request.client.host[:64] if request.client else None


_TEMPLATES: list[tuple[Any, str]] | None = None


def _route_template(request: Request) -> str:
    """Template da rota (`/api/contratos/{contrato_id}`) — nunca o caminho com ids. Usa o
    esquema OpenAPI (API pública e estável do FastAPI), compilado uma vez por processo."""
    global _TEMPLATES
    if _TEMPLATES is None:
        from starlette.routing import compile_path

        paths = list(request.app.openapi().get("paths", {})) + list(_HEALTH) + ["/metrics", "/"]
        # caminhos estáticos antes dos parametrizados (ex.: /x/novo antes de /x/{id})
        paths.sort(key=lambda p: (p.count("{"), -len(p)))
        _TEMPLATES = [(compile_path(p)[0], p) for p in paths]
    for regex, tpl in _TEMPLATES:
        if regex.match(request.url.path):
            return tpl
    return "(sem rota)"


def _user_id(request: Request) -> str | None:
    principal = getattr(request.state, "principal", None)
    return str(principal.user_id) if principal is not None else None


def _host_permitido(request: Request) -> bool:
    hosts = settings.trusted_hosts
    if not hosts or "*" in hosts or request.url.path in _HEALTH:
        return True
    host = (request.headers.get("host") or "").split(":")[0].lower()
    return any(host == h.lower() or (h.startswith("*.") and host.endswith(h[1:].lower()))
               for h in hosts)


def _body(detail: Any, code: str, request: Request, **extra: Any) -> dict:
    out = {"detail": detail, "code": code, **extra}
    rid = getattr(request.state, "request_id", None)
    if rid:
        out["request_id"] = rid
    return out


def create_app() -> FastAPI:
    """Factory da aplicação — facilita instâncias isoladas nos testes."""
    app = FastAPI(
        title=settings.project_name,
        description=(
            "Decision Intelligence Platform for Healthcare — API REST. "
            "Toda rota de dados exige autenticação e contexto de tenant."
        ),
        version=settings.version,
        lifespan=lifespan,
        # documentação interativa só fora de produção (reduz superfície exposta)
        docs_url=None if settings.is_production_like else "/docs",
        redoc_url=None if settings.is_production_like else "/redoc",
        openapi_url=None if settings.is_production_like else "/openapi.json",
    )

    # mais interno: recusa corpo grande / upload sem token antes de ler o corpo; a resposta
    # ainda passa pelo request_context (request id, headers de segurança, métricas)
    app.add_middleware(BodyLimitMiddleware)

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        t0 = time.perf_counter()
        rid = request.headers.get("x-request-id") or ""
        rid = rid if _REQ_ID.match(rid) else uuid.uuid4().hex
        request.state.request_id = rid
        clear_log_context()  # nada de uma requisição anterior no mesmo contexto
        set_log_context(request_id=rid, correlation_id=rid)
        set_request_meta(RequestMeta(ip=_client_ip(request),
                                     user_agent=request.headers.get("user-agent"),
                                     request_id=rid))
        if not _host_permitido(request):
            response = JSONResponse({"detail": "Host não permitido.", "code": "invalid_request",
                                     "request_id": rid}, status_code=400)
        else:
            response = await call_next(request)
        response.headers["X-Request-ID"] = rid
        for k, v in _SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        if settings.hsts:
            response.headers.setdefault("Strict-Transport-Security",
                                        "max-age=31536000; includeSubDomains")
        if request.url.path.startswith(settings.api_v1_prefix):
            # assets públicos de branding definem o próprio cache
            response.headers.setdefault("Cache-Control", "no-store")
        dur = time.perf_counter() - t0
        rota = _route_template(request)
        if request.url.path not in _HEALTH and request.url.path != "/metrics":
            metrics.observe_http(request.method, rota, response.status_code, dur)
            log_http.info("http.request", extra={
                "event": "http.request", "method": request.method, "route": rota,
                "status": response.status_code, "duration_ms": int(dur * 1000),
                "user_id": _user_id(request)})
        return response

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )

    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(_body(exc.detail, exc.code, request, **exc.extra),
                            status_code=exc.status_code, headers=exc.headers)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _CODE_BY_STATUS.get(exc.status_code, "error")
        return JSONResponse(_body(exc.detail, code, request), status_code=exc.status_code,
                            headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        erros = [{"loc": list(e.get("loc", [])), "msg": e.get("msg")} for e in exc.errors()]
        return JSONResponse(_body(erros, "invalid_request", request), status_code=422)

    @app.exception_handler(TenantContextMissing)
    async def _sem_tenant(request: Request, exc: TenantContextMissing) -> JSONResponse:
        log.error("acesso a dado sem tenant no contexto: %s %s", request.method, request.url.path)
        return JSONResponse(_body("Erro interno.", "internal_error", request), status_code=500)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("erro não tratado em %s %s", request.method, request.url.path)
        return JSONResponse(_body("Erro interno.", "internal_error", request), status_code=500)

    app.include_router(api_v1_router, prefix=settings.api_v1_prefix)

    # ------------------------------------------------------------- health (sem /api)
    @app.get("/health/live", tags=["Infraestrutura"], summary="Liveness: o processo responde")
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", tags=["Infraestrutura"],
             summary="Readiness: banco e armazenamento acessíveis (sem detalhes internos)")
    def ready() -> JSONResponse:
        from app.ops.health import readiness_checks

        checks = readiness_checks()
        ok = all(v != "fail" for v in checks.values())
        return JSONResponse({"status": "ready" if ok else "not_ready", "checks": checks},
                            status_code=200 if ok else 503)

    @app.get("/metrics", tags=["Infraestrutura"], include_in_schema=False)
    def metrics_endpoint(request: Request) -> PlainTextResponse:
        token = settings.metrics_token.get_secret_value() if settings.metrics_token else ""
        if not token:
            if settings.is_production_like:
                return PlainTextResponse("not found", status_code=404)
        else:
            enviado = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
            if not hmac.compare_digest(enviado, token):
                return PlainTextResponse("unauthorized", status_code=401)
        linhas = metrics.render_http()
        try:
            from app.db.session import SessionLocal

            with SessionLocal() as s:
                linhas += metrics.render_pipeline(s)
        except Exception:  # noqa: BLE001 — métricas de fila indisponíveis não derrubam a coleta
            log.warning("metrics: fila indisponível")
            linhas.append("w2h_metrics_pipeline_up 0")
        return PlainTextResponse("\n".join(linhas) + "\n", media_type="text/plain; version=0.0.4")

    @app.get("/", tags=["Infraestrutura"], summary="Metadados da API")
    def root() -> dict[str, str]:
        return {
            "service": settings.project_name,
            "version": settings.version,
            "docs": "/docs",
            "health": f"{settings.api_v1_prefix}/health",
        }

    return app


app = create_app()
