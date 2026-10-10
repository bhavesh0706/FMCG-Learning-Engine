"""
Access control for the whole API, in one place.

Every request passes through this middleware, including routers that don't
check permissions themselves (feedback.py, surveys.py, video.py, catalog.py).

  public  anyone: sign-in, first-time setup, health check, API docs
  user    any signed-in, active account (admin or survey person)
  admin   admins only: team, survey history, reports, catalog changes, deleting

Anything not listed is "user", so a new endpoint is never accidentally public.

Recordings are played by the browser's <video> / the phone's player, which can't
send a header, so for media only the token may also come as ?token=...

Register in app/main.py BEFORE the CORS middleware (so error responses still
carry CORS headers):
    from app.api.access import install_access_control
    install_access_control(app)
"""

import json
import re
from typing import Optional

from fastapi.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.api.auth import read_token
from app.database.database import SessionLocal
from app.database.models import User

WRITE = {"POST", "PUT", "PATCH", "DELETE"}

PUBLIC = [
    ({"GET"}, r"^/$"),
    ({"GET"}, r"^/health$"),
    (None, r"^/(docs|redoc|openapi\.json)"),
    ({"POST"}, r"^/auth/login$"),
    ({"POST"}, r"^/auth/signup$"),  # the endpoint itself refuses unless sign-up is enabled
    ({"GET"}, r"^/auth/setup-status$"),
    ({"POST"}, r"^/auth/setup$"),  # only works while no admin exists
]

ADMIN = [
    (None, r"^/admin(/|$)"),
    # Survey history and reports: everything except an executive's own survey list
    (None, r"^/product-feedback(?!/surveys/?$)(/|$)"),
    # Catalog changes (reading the catalog is needed to take surveys)
    (WRITE, r"^/(categories|products|questions)(/|$)"),
    ({"GET"}, r"^/products/\d+/recipe$"),
    # The full list of recordings, and changing or deleting them
    ({"GET"}, r"^/feedback/?$"),
    ({"PUT", "PATCH", "DELETE"}, r"^/feedback(/|$)"),
    # The whole analysis queue (one job's own status is "user")
    ({"GET"}, r"^/analysis/jobs/?$"),
    # Re-running an analysis (and replacing its results) is for admins
    ({"POST"}, r"^/feedback/\d+/reanalyze$"),
    ({"POST"}, r"^/analysis/reanalyze/\d+$"),
]

MEDIA = re.compile(r"^/feedback/\d+/(video|audio|media)")

_PUBLIC = [(m, re.compile(p)) for m, p in PUBLIC]
_ADMIN = [(m, re.compile(p)) for m, p in ADMIN]


def required_role(method: str, path: str) -> str:
    """'public', 'admin' or 'user' for one request."""
    method = method.upper()
    path = path.rstrip("/") or "/"
    for methods, rx in _PUBLIC:
        if (methods is None or method in methods) and rx.search(path):
            return "public"
    for methods, rx in _ADMIN:
        if (methods is None or method in methods) and rx.search(path):
            return "admin"
    return "user"


def _account(token: Optional[str]) -> Optional[dict]:
    uid = read_token(token) if token else None
    if not uid:
        return None
    db = SessionLocal()
    try:
        user = db.get(User, uid)
        if not user or not user.is_active:
            return None
        return {"id": user.id, "role": user.role, "name": user.name}
    finally:
        db.close()


SURVEY_CREATE = re.compile(r"^/surveys/responses$")


def _may_survey(token: str, product_id: int) -> bool:
    from app.services.product_access import can_survey

    uid = read_token(token) if token else None
    db = SessionLocal()
    try:
        user = db.get(User, uid) if uid else None
        return user is None or can_survey(db, user, product_id)  # no user: the sign-in check answers
    finally:
        db.close()


class ProductAssignmentGuard:
    """
    Survey creation (POST /surveys/responses, in surveys.py) for a product that an
    admin assigned to other executives is refused. The request body is read here
    and handed on unchanged, so surveys.py needs no changes.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or not SURVEY_CREATE.search(scope["path"].rstrip("/")):
            return await self.app(scope, receive, send)

        chunks, more = [], True
        while more:
            message = await receive()
            if message["type"] != "http.request":
                break
            chunks.append(message.get("body", b""))
            more = message.get("more_body", False)
        body = b"".join(chunks)

        try:
            product_id = int(json.loads(body or b"{}").get("product_id"))
        except (ValueError, TypeError, AttributeError):
            product_id = None
        if product_id is not None:
            headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
            token = headers.get("authorization", "").removeprefix("Bearer ").strip()
            if not await run_in_threadpool(_may_survey, token, product_id):
                response = JSONResponse(status_code=403, content={"detail": "This product isn't assigned to you. Ask your admin."})
                return await response(scope, receive, send)

        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def install_access_control(app):
    app.add_middleware(ProductAssignmentGuard)

    @app.middleware("http")
    async def access_control(request: Request, call_next):
        if request.method == "OPTIONS":  # CORS preflight
            return await call_next(request)
        need = required_role(request.method, request.url.path)
        if need == "public":
            return await call_next(request)

        token = (request.headers.get("authorization") or "").removeprefix("Bearer ").strip()
        if not token and MEDIA.search(request.url.path):
            token = request.query_params.get("token", "")
        account = await run_in_threadpool(_account, token)
        if account is None:
            return JSONResponse(status_code=401, content={"detail": "Please sign in again."})
        if need == "admin" and account["role"] != "admin":
            return JSONResponse(status_code=403, content={"detail": "This is only available to admins."})
        request.state.account = account
        return await call_next(request)