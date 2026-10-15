"""
Request IDs, access logs and security headers for every API response (OPS-03, SEC-11).

  - X-Request-ID: taken from the caller (proxy, app) when present, otherwise
    generated; returned on the response and written in the access log, so one
    request can be followed from the phone to the server logs.
  - One JSON line per request on the "fmcg.access" logger:
        {"ts": ..., "request_id": ..., "method": "GET", "path": "/health",
         "status": 200, "ms": 4.2, "user_id": 3}
    No request bodies, query strings or tokens are logged (customer data and
    sign-in tokens never reach the logs).
  - Security headers suitable for a JSON API (the web app's headers are set by
    nginx / Caddy): nosniff, no framing, no referrer.

Log level: LOG_LEVEL (default INFO). Logs go to stdout, where Docker collects them.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
import uuid

from starlette.requests import Request

_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,128}$")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-site",
}


def _logger() -> logging.Logger:
    log = logging.getLogger("fmcg.access")
    if not log.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        log.addHandler(handler)
        log.propagate = False
    log.setLevel(os.environ.get("LOG_LEVEL", "INFO").upper())
    return log


def install_observability(app):
    log = _logger()

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if _REQUEST_ID.match(incoming) else uuid.uuid4().hex
        request.state.request_id = request_id
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
        finally:
            account = getattr(request.state, "account", None) or {}
            log.info(json.dumps({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()),
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status": status,
                "ms": round((time.perf_counter() - started) * 1000, 1),
                "user_id": account.get("id"),
            }))
        response.headers["X-Request-ID"] = request_id
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response
