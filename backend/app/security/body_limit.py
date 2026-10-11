"""Limite de corpo das requisições, aplicado ANTES de qualquer leitura do corpo.

O FastAPI resolve o formulário multipart (e espalha arquivos > 1 MB em disco temporário)
antes de executar as dependências de autenticação da rota. Sem este middleware, um POST
não autenticado de centenas de MB na rota de upload consumiria memória/disco do host.

Regras (só para métodos com corpo):

* sem `Content-Length` e com `Transfer-Encoding` (chunked) → 411 — o tamanho precisa ser
  declarado para ser checado sem ler o corpo (o servidor ASGI garante que o corpo não
  excede o `Content-Length` declarado);
* `Content-Length` acima do limite → 413, sem ler o corpo;
* limite padrão: `REQUEST_MAX_BODY_KB`; rota de upload de pacotes:
  `UPLOAD_MAX_FILE_MB × UPLOAD_MAX_FILES` + 1 MB de margem para o envelope multipart;
* rota de upload exige access token válido (assinatura/expiração) já no cabeçalho, senão
  401 sem ler o corpo. Sessão, papel e permissão continuam sendo checados pela rota.
"""

from __future__ import annotations

import json
import re

from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import get_settings
from app.security.tokens import TokenError, decode_access_token

_COM_CORPO = frozenset({"POST", "PUT", "PATCH"})
_MARGEM_MULTIPART = 1024 * 1024


class BodyLimitMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        s = get_settings()
        self.limite_padrao = s.request_max_body_kb * 1024
        self.limite_upload = s.upload_max_file_mb * s.upload_max_files * 1024 * 1024 + _MARGEM_MULTIPART
        prefixo = re.escape(s.api_v1_prefix.rstrip("/"))
        self.rota_upload = re.compile(rf"^{prefixo}/admin/tenants/[^/]+/sources/[^/]+/uploads/?$")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in _COM_CORPO:
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        bruto = headers.get("content-length")
        if bruto is None:
            if "transfer-encoding" in headers:
                await _responder(send, 411, "invalid_request", "Content-Length obrigatório.")
                return
            await self.app(scope, receive, send)
            return
        try:
            tamanho = int(bruto)
        except ValueError:
            await _responder(send, 400, "invalid_request", "Content-Length inválido.")
            return

        upload = bool(self.rota_upload.match(scope["path"]))
        if upload and not _token_valido(headers.get("authorization", "")):
            await _responder(send, 401, "not_authenticated", "Autenticação necessária.",
                             extra_headers=[(b"www-authenticate", b"Bearer")])
            return
        if tamanho > (self.limite_upload if upload else self.limite_padrao):
            await _responder(send, 413, "invalid_request", "Requisição maior que o permitido.")
            return
        await self.app(scope, receive, send)


def _token_valido(authorization: str) -> bool:
    esquema, _, token = authorization.partition(" ")
    if esquema.lower() != "bearer" or not token.strip():
        return False
    try:
        decode_access_token(token.strip())
    except TokenError:
        return False
    return True


async def _responder(send: Send, status: int, code: str, detail: str,
                     extra_headers: list[tuple[bytes, bytes]] | None = None) -> None:
    corpo = json.dumps({"detail": detail, "code": code}).encode()
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(corpo)).encode()),
            (b"connection", b"close"),
            *(extra_headers or []),
        ],
    })
    await send({"type": "http.response.body", "body": corpo})
