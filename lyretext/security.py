"""Local-API protection and key hygiene for the desktop app (#67, decision #84).

The desktop app runs this backend on 127.0.0.1 with the user's own API keys in
its environment. Binding to loopback alone would still let any local program,
or a web page in the user's browser (CSRF, DNS rebinding), drive the API and
spend those keys. So, when the app sets LYRETEXT_AUTH_TOKEN:

* every request except ``GET /api/health`` must carry that per-launch token,
  either in the ``lyretext_token`` cookie (set HttpOnly + SameSite=Strict on
  the app window's session, so the page never sees it) or in the
  ``X-LyreTeXt-Token`` header (used by the Electron main process);
* the ``Host`` header must be ``127.0.0.1:<port>`` or ``localhost:<port>``,
  which defeats DNS rebinding;
* the dev-only permissive CORS policy is switched off.

With no token set (the dev workflow) every request passes through untouched.

It also holds the log-redaction filter that keeps key values (and the token)
out of backend.log, and ``redact()`` for error text that is shown to the user.
"""
from __future__ import annotations

import hmac
import logging
import os
from typing import Iterable

from starlette.datastructures import Headers
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import cookie_parser
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import AUTH_TOKEN_ENV

AUTH_COOKIE = "lyretext_token"
AUTH_HEADER = "X-LyreTeXt-Token"

# Requests answered without a token: the shell's liveness poll, which runs
# before the window (and so the cookie) exists.
_EXEMPT = {("GET", "/api/health")}

_LOOPBACK_HOSTS = ("127.0.0.1", "localhost")


def auth_token() -> str:
    """The per-launch token, or "" in dev mode. Read per request (cheap), so
    tests and the app agree on it without import-time state."""
    return os.environ.get(AUTH_TOKEN_ENV, "")


def token_matches(candidate: str | None, token: str) -> bool:
    """Constant-time comparison; a missing candidate never matches."""
    if not candidate or not token:
        return False
    return hmac.compare_digest(candidate.encode("utf-8"), token.encode("utf-8"))


def _host_allowed(host: str | None, scope: Scope) -> bool:
    server = scope.get("server")
    if not host or not server or server[1] is None:
        return False
    port = server[1]
    return host.strip().lower() in {f"{h}:{port}" for h in _LOOPBACK_HOSTS}


class LocalAuthMiddleware:
    """Require the per-launch token and a loopback Host (desktop mode only)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        token = auth_token()
        if not token:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        if not _host_allowed(headers.get("host"), scope):
            await self._reject(scope, receive, send, 400, "Invalid host header")
            return
        if (scope.get("method"), scope.get("path")) in _EXEMPT:
            await self.app(scope, receive, send)
            return
        candidate = headers.get(AUTH_HEADER)
        if candidate is None:
            candidate = cookie_parser(headers.get("cookie", "")).get(AUTH_COOKIE)
        if not token_matches(candidate, token):
            await self._reject(scope, receive, send, 401, "Unauthorized")
            return
        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(scope: Scope, receive: Receive, send: Send, status: int, detail: str) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await JSONResponse({"detail": detail}, status_code=status)(scope, receive, send)


class DevOnlyCORS:
    """The dev workflow's permissive CORS policy, off in desktop mode.

    ``allow_origins=["*"]`` lets any web page read this API's responses. That
    is harmless for a developer's local server with .env keys, but in desktop
    mode the only legitimate caller is the app's own same-origin window, so no
    CORS headers are sent at all.
    """

    def __init__(self, app: ASGIApp, **cors_options) -> None:
        self.app = app
        self.cors = CORSMiddleware(app, **cors_options)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if auth_token():
            await self.app(scope, receive, send)
        else:
            await self.cors(scope, receive, send)


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

REDACTED = "[REDACTED]"

# Shorter values are not plausible keys, and redacting them would mangle
# ordinary text (a test env with KEY=x must not turn every "x" into a mask).
_MIN_SECRET_LEN = 8


def current_secrets() -> list[str]:
    """Every value that must never be logged: all *_API_KEY variables and the
    auth token. Longest first, so a key containing another is masked whole."""
    values = {
        v for k, v in os.environ.items()
        if (k.upper().endswith("_API_KEY") or k == AUTH_TOKEN_ENV) and len(v) >= _MIN_SECRET_LEN
    }
    return sorted(values, key=len, reverse=True)


def redact(text: str, secrets: Iterable[str] | None = None) -> str:
    """Replace each secret value in *text* with ``[REDACTED]``."""
    if not text:
        return text
    for secret in (current_secrets() if secrets is None else secrets):
        if secret in text:
            text = text.replace(secret, REDACTED)
    return text


class KeyRedactionFilter(logging.Filter):
    """Mask key values and the token in a log record, in place.

    Redacts the message template, each argument (keeping the args' shape, which
    uvicorn's access formatter unpacks), and the formatted traceback. Always
    returns True: it cleans records, it never drops them.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        secrets = current_secrets()
        if not secrets:
            return True
        if isinstance(record.msg, str):
            record.msg = redact(record.msg, secrets)
        if isinstance(record.args, tuple):
            record.args = tuple(self._redact_arg(a, secrets) for a in record.args)
        elif isinstance(record.args, dict):
            record.args = {k: self._redact_arg(v, secrets) for k, v in record.args.items()}
        if record.exc_info and not record.exc_text:
            # Format now so the text can be cleaned; Formatter.format reuses
            # exc_text rather than formatting the exception again.
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact(record.exc_text, secrets)
        if record.stack_info:
            record.stack_info = redact(record.stack_info, secrets)
        return True

    @staticmethod
    def _redact_arg(arg, secrets):
        if isinstance(arg, str):
            return redact(arg, secrets)
        if isinstance(arg, (int, float, bool)) or arg is None:
            return arg
        # e.g. an exception passed as %s: only replace it if its text leaks.
        try:
            text = str(arg)
        except Exception:  # noqa: BLE001 - never let logging fail here
            return arg
        cleaned = redact(text, secrets)
        return cleaned if cleaned != text else arg


_redaction_installed = False


def install_log_redaction() -> None:
    """Run KeyRedactionFilter on every log record the process creates.

    Installed as a record factory rather than on handlers, so it covers every
    logger — ours, uvicorn's, the provider SDKs' — whatever handlers uvicorn's
    logging config sets up later. Idempotent.
    """
    global _redaction_installed
    if _redaction_installed:
        return
    previous = logging.getLogRecordFactory()
    redaction = KeyRedactionFilter()

    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        redaction.filter(record)
        return record

    logging.setLogRecordFactory(factory)
    _redaction_installed = True
