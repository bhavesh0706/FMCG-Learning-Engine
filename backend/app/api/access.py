"""
Access control for the whole API, in one place.

Every request passes through this middleware, including routers that don't
check permissions themselves (feedback.py, surveys.py, video.py, catalog.py).

  public  anyone: sign-in, first-time setup, health check, API docs
  user    any signed-in, active account (admin or survey person)
  admin   admins only: team, survey history, reports, catalog changes, deleting
  client  the organization's login: ONLY the routes in CLIENT_ALLOWED (its portal)
  org     recipes and survey questions are written ONLY by the organization
          (ORG_CONTENT); admins and survey persons may read them, not change them

An account that must choose a new password (the organization's first sign-in,
or after its password was reset on the server) may only read /auth/me, change
its password and sign out, until it has done so.

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

from app.api.auth import user_for_token
from app.database.database import SessionLocal

WRITE = {"POST", "PUT", "PATCH", "DELETE"}

PUBLIC = [
    ({"GET"}, r"^/$"),
    ({"GET"}, r"^/health$"),
    ({"GET"}, r"^/health/ready$"),
    (None, r"^/(docs|redoc|openapi\.json)"),
    ({"POST"}, r"^/auth/login$"),
    ({"POST"}, r"^/auth/signup$"),  # the endpoint itself refuses unless sign-up is enabled
    ({"GET"}, r"^/auth/setup-status$"),
    ({"POST"}, r"^/auth/setup$"),  # only works while no admin exists
    ({"POST"}, r"^/auth/logout$"),  # ends the sent token if it is still valid; harmless otherwise
]

ADMIN = [
    (None, r"^/admin(/|$)"),
    # Organization portal: admins (preview) and the organization (CLIENT_ALLOWED), never survey persons
    (None, r"^/org(/|$)"),
    # Survey rounds of a product (admin view; the organization uses /org/...)
    ({"GET"}, r"^/products/\d+/rounds$"),
    # Survey history and reports: everything except an executive's own survey list
    (None, r"^/product-feedback(?!/surveys/?$)(/|$)"),
    # Catalog changes (reading the catalog is needed to take surveys)
    (WRITE, r"^/(categories|products|questions)(/|$)"),
    ({"GET"}, r"^/products/\d+/recipe(/versions)?$"),
    # Question templates (starting questions for new products): Catalog admins only
    (None, r"^/question-templates(/|$)"),
    # The full list of recordings, and changing or deleting them
    ({"GET"}, r"^/feedback/?$"),
    ({"PUT", "PATCH", "DELETE"}, r"^/feedback(/|$)"),
    # The whole analysis queue (one job's own status is "user")
    ({"GET"}, r"^/analysis/jobs/?$"),
    # Re-running an analysis (and replacing its results) is for admins
    ({"POST"}, r"^/feedback/\d+/reanalyze$"),
    ({"POST"}, r"^/analysis/reanalyze/\d+$"),
]

# Product content the ORGANIZATION manages (its portal: Recipe and Survey questions
# tabs). Admins and survey persons can read recipes and questions but not change
# them: these answer 403 for every role except "client".
ORG_CONTENT = [
    ({"PUT"}, r"^/products/\d+/recipe$"),
    ({"POST"}, r"^/products/\d+/questions$"),
    ({"PUT"}, r"^/products/\d+/questions/order$"),
    ({"PUT", "PATCH", "DELETE"}, r"^/questions/\d+$"),
]

# The organization's login may use ONLY these routes; every other route answers
# 403 for it. It sees every active product (ownership.check_product_access), but
# only aggregated results: no customer details, survey persons or recordings.
CLIENT_ALLOWED = [
    ({"GET"}, r"^/auth/me$"),
    ({"POST"}, r"^/auth/(logout-all|change-password)$"),
    (None, r"^/org(/|$)"),
    ({"GET", "PUT"}, r"^/products/\d+/recipe$"),
    ({"GET"}, r"^/products/\d+/recipe/versions$"),
    # Survey questions of a product: read, add, edit, reorder, remove
    ({"GET", "POST"}, r"^/products/\d+/questions$"),
    ({"PUT"}, r"^/products/\d+/questions/order$"),
    ({"PUT", "PATCH", "DELETE"}, r"^/questions/\d+$"),
]

# While an account must choose a new password, only these work.
PASSWORD_CHANGE_ALLOWED = [
    ({"GET"}, r"^/auth/me$"),
    ({"POST"}, r"^/auth/change-password$"),
]

MEDIA = re.compile(r"^/feedback/\d+/(video|audio|media)")

_PUBLIC = [(m, re.compile(p)) for m, p in PUBLIC]
_ADMIN = [(m, re.compile(p)) for m, p in ADMIN]
_CLIENT = [(m, re.compile(p)) for m, p in CLIENT_ALLOWED]
_ORG_CONTENT = [(m, re.compile(p)) for m, p in ORG_CONTENT]
_PASSWORD_CHANGE = [(m, re.compile(p)) for m, p in PASSWORD_CHANGE_ALLOWED]


def _matches(rules, method: str, path: str) -> bool:
    method = method.upper()
    path = path.rstrip("/") or "/"
    return any((methods is None or method in methods) and rx.search(path) for methods, rx in rules)


def client_may(method: str, path: str) -> bool:
    return _matches(_CLIENT, method, path)


def organization_only(method: str, path: str) -> bool:
    """Recipe and survey-question changes: only the organization may make them."""
    return _matches(_ORG_CONTENT, method, path)


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
    """The signed-in account, or None (invalid, expired or signed-out token, disabled account)."""
    if not token:
        return None
    db = SessionLocal()
    try:
        user = user_for_token(db, token)
        if user is None:
            return None
        return {"id": user.id, "role": user.role, "name": user.name,
                "must_change_password": bool(getattr(user, "must_change_password", 0))}
    finally:
        db.close()


SURVEY_CREATE = re.compile(r"^/surveys/responses$")


def _may_survey(token: str, product_id: int) -> bool:
    from app.services.product_access import can_survey

    db = SessionLocal()
    try:
        user = user_for_token(db, token) if token else None
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
        if account.get("must_change_password") and not _matches(_PASSWORD_CHANGE, request.method, request.url.path):
            return JSONResponse(status_code=403, content={"detail": "Please choose your own password first.",
                                                          "code": "password_change_required"})
        if account["role"] == "client":
            if not client_may(request.method, request.url.path):
                return JSONResponse(status_code=403, content={"detail": "The organization portal can't open this page."})
        elif organization_only(request.method, request.url.path):
            return JSONResponse(status_code=403, content={
                "detail": "Recipes and survey questions are managed by the organization in its portal.",
                "code": "organization_only"})
        elif need == "admin" and account["role"] != "admin":
            return JSONResponse(status_code=403, content={"detail": "This is only available to admins."})
        request.state.account = account
        return await call_next(request)
