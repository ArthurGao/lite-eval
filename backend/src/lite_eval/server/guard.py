"""Refuse requests a browser page could make (design §18.10).

Binding to loopback stops other machines, not the user's own browser: a page can POST to
127.0.0.1 (CSRF) or rebind its own domain to 127.0.0.1 and read the replies (DNS rebinding).
So every request needs a loopback Host and, when it has one, a loopback Origin; a write also
needs the Viewer's own Origin, this process's token and a body of at most 16 KB. No CORS
headers are ever sent, so the browser's same-origin policy keeps other sites from reading
responses. Adapted from llm-gateway's `server/guard.py`.
"""

import ipaddress
import json
import secrets
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_BODY = 16 * 1024
TOKEN_HEADER = "x-lite-eval-token"
READ_METHODS = {"GET", "HEAD", "OPTIONS"}


def is_loopback(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _host(netloc_or_url: str) -> str | None:
    # urlsplit strips the port and IPv6 brackets ("[::1]:8720") and lowercases the name.
    url = netloc_or_url if "//" in netloc_or_url else f"//{netloc_or_url}"
    try:
        return urlsplit(url).hostname
    except ValueError:
        return None


def _loopback(netloc_or_url: str) -> bool:
    host = _host(netloc_or_url)
    return host is not None and is_loopback(host)


def error_body(message: str, code: str) -> bytes:
    return json.dumps({"error": {"message": message, "code": code}}).encode()


async def _reply(send: Send, status: int, message: str, code: str) -> None:
    body = error_body(message, code)
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class Guard:
    """Pure ASGI middleware enforcing §18.10. `origins`: the Viewer's own origins, e.g.
    {"http://127.0.0.1:8720"}; `token`: this process's write token."""

    def __init__(self, app: ASGIApp, *, origins: set[str], token: str) -> None:
        self.app = app
        self.origins = {o.rstrip("/").lower() for o in origins}
        self.token = token.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        if not _loopback(headers.get("host", "")):
            await _reply(send, 403, "Host header is not a loopback address", "forbidden_host")
            return
        origin = headers.get("origin")
        # Opaque origins ("null", file://, extensions) have no loopback host, so they fail too.
        if origin is not None and not _loopback(origin):
            await _reply(send, 403, "cross-origin requests are not allowed", "forbidden_origin")
            return
        if scope["method"] in READ_METHODS:
            await self.app(scope, receive, send)
            return
        if origin is None or origin.rstrip("/").lower() not in self.origins:
            await _reply(send, 403, "a write must come from the Viewer's page", "forbidden_origin")
            return
        token = headers.get(TOKEN_HEADER, "").encode()
        if not secrets.compare_digest(token, self.token):
            await _reply(send, 403, "a write needs this Viewer's token", "missing_token")
            return
        declared = headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > MAX_BODY:
            await _reply(send, 413, f"a request body is at most {MAX_BODY} bytes", "too_large")
            return
        body = await _read_body(receive)
        if body is None:
            await _reply(send, 413, f"a request body is at most {MAX_BODY} bytes", "too_large")
            return
        await self.app(scope, _replay(body), send)


async def _read_body(receive: Receive) -> bytes | None:
    """The whole body, or None once it passes MAX_BODY (counted, whatever Content-Length says)."""
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message["type"] == "http.disconnect":
            return b"".join(chunks)
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > MAX_BODY:
            return None
        chunks.append(chunk)
        if not message.get("more_body", False):
            return b"".join(chunks)


def _replay(body: bytes) -> Receive:
    sent = False

    async def receive() -> Message:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return receive
