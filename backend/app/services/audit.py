"""
Audit log: who changed what, and when (proposal section 3, "audit logging").

Every successful change request (POST / PUT / PATCH / DELETE) by a signed-in
account is written to the audit_log table automatically by the middleware
below, so a new endpoint is covered without extra code. Each entry has:

    when, who (id, name, role), action ("product.delete"), target ("product", 12),
    a one-line summary, the request path and status, address and request id

Endpoints can add detail for the same entry with note(request, ...), e.g. which
fields of a team member changed ({"is_active": [true, false]}). Sign-in,
failed sign-in and sign-out are recorded explicitly by auth.py.

Never stored: passwords, tokens, request bodies, customer names, phone numbers
or addresses. Entries are never edited; entries older than FMCG_AUDIT_KEEP_DAYS
(default 1095 = 3 years, longer than the 2-year support period) are removed.

Admins read the log on the web "Activity log" page (GET /admin/audit).
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta
from typing import Any, Optional

from fastapi.concurrency import run_in_threadpool
from starlette.requests import Request

from app.database.database import SessionLocal
from app.database.models import AuditLog, RevokedToken

log = logging.getLogger("fmcg.audit")

KEEP_DAYS = int(os.environ.get("FMCG_AUDIT_KEEP_DAYS", "") or 1095)
WRITE = {"POST", "PUT", "PATCH", "DELETE"}

# (methods, path pattern, action, target type, summary). "{id}" is the first
# number in the path. Checked in order; the first match wins.
RULES: list[tuple[set, str, str, Optional[str], str]] = [
    # Team (admin)
    ({"POST"}, r"^/admin/users$", "user.create", "user", "Added a team member"),
    ({"PUT"}, r"^/admin/users/(\d+)/password$", "user.password_reset", "user", "Reset the password of team member #{id}"),
    ({"POST"}, r"^/admin/users/(\d+)/sign-out$", "user.sign_out_all", "user", "Signed team member #{id} out of every device"),
    ({"PUT"}, r"^/admin/users/(\d+)/products$", "user.products", "user", "Changed the products assigned to team member #{id}"),
    ({"PUT"}, r"^/admin/users/(\d+)$", "user.update", "user", "Changed team member #{id}"),
    ({"PUT"}, r"^/admin/products/(\d+)/executives$", "product.assignees", "product", "Changed who may survey product #{id}"),
    # Catalog
    ({"POST"}, r"^/categories$", "category.create", "category", "Added a category"),
    ({"PUT"}, r"^/categories/(\d+)$", "category.update", "category", "Changed category #{id}"),
    ({"DELETE"}, r"^/categories/(\d+)/permanent$", "category.delete", "category", "Deleted category #{id} permanently"),
    ({"DELETE"}, r"^/categories/(\d+)$", "category.deactivate", "category", "Deactivated category #{id}"),
    ({"POST"}, r"^/products$", "product.create", "product", "Added a product"),
    ({"PUT"}, r"^/products/(\d+)/recipe$", "recipe.update", "product", "Changed the recipe of product #{id}"),
    ({"POST"}, r"^/(?:org/)?products/(\d+)/rounds$", "round.start", "product", "Started a survey round of product #{id}"),
    ({"POST"}, r"^/(?:org/)?products/(\d+)/rounds/\d+/end$", "round.end", "product", "Ended a survey round of product #{id}"),
    ({"PUT"}, r"^/products/(\d+)/questions/order$", "question.reorder", "product", "Reordered the questions of product #{id}"),
    ({"POST"}, r"^/products/(\d+)/questions$", "question.create", "product", "Added a question to product #{id}"),
    ({"PUT"}, r"^/products/(\d+)$", "product.update", "product", "Changed product #{id}"),
    ({"DELETE"}, r"^/products/(\d+)/permanent$", "product.delete", "product", "Deleted product #{id} permanently"),
    ({"DELETE"}, r"^/products/(\d+)$", "product.deactivate", "product", "Deactivated product #{id}"),
    ({"PUT"}, r"^/questions/(\d+)$", "question.update", "question", "Changed question #{id}"),
    ({"DELETE"}, r"^/questions/(\d+)$", "question.delete", "question", "Removed question #{id}"),
    ({"POST"}, r"^/question-templates$", "template.create", "template", "Added a question template"),
    ({"PUT"}, r"^/question-templates/(\d+)$", "template.update", "template", "Changed question template #{id}"),
    ({"DELETE"}, r"^/question-templates/(\d+)$", "template.delete", "template", "Deleted question template #{id}"),
    # Surveys and recordings
    ({"POST"}, r"^/surveys/respondents$", "customer.create", "customer", "Recorded a new customer"),
    ({"POST"}, r"^/surveys/responses$", "survey.create", "survey", "Started a survey"),
    ({"PUT"}, r"^/surveys/responses/(\d+)/answers$", "survey.answers", "survey", "Saved the answers of survey #{id}"),
    ({"POST"}, r"^/surveys/responses/(\d+)/submit$", "survey.submit", "survey", "Submitted survey #{id}"),
    ({"POST"}, r"^/auth/me/claim/(\d+)$", "survey.claim", "survey", "Linked survey #{id} to own account"),
    ({"DELETE"}, r"^/product-feedback/surveys/(\d+)$", "survey.delete", "survey", "Deleted survey #{id}"),
    ({"POST"}, r"^/product-feedback/(\d+)/consolidate$", "report.generate", "product", "Regenerated the report of product #{id}"),
    ({"POST"}, r"^/analysis/jobs$", "recording.upload", "survey", "Uploaded a recording"),
    ({"POST"}, r"^/video/upload$", "recording.upload", "survey", "Uploaded a recording"),
    ({"POST"}, r"^/feedback/(\d+)/reanalyze$", "recording.reanalyze", "recording", "Re-ran the analysis of recording #{id}"),
    ({"POST"}, r"^/analysis/reanalyze/(\d+)$", "recording.reanalyze", "recording", "Re-ran the analysis of recording #{id}"),
    ({"DELETE"}, r"^/feedback/(\d+)$", "recording.delete", "recording", "Deleted recording #{id}"),
    ({"PUT", "PATCH"}, r"^/feedback/(\d+)", "recording.update", "recording", "Changed the result of recording #{id}"),
]
_RULES = [(m, re.compile(p), a, t, s) for m, p, a, t, s in RULES]

# Writes that change nothing anyone needs to audit.
SKIP = [
    re.compile(r"^/notifications(/|$)"),       # marking the bell read
    re.compile(r"^/auth/me/surveys/existing$"),  # a lookup sent as POST
    re.compile(r"^/auth/(login|logout|logout-all|setup|signup|change-password)$"),  # recorded by auth.py itself
    re.compile(r"^/org/updates/seen$"),  # opening the Updates page
]

# Fields that must never reach the log, even if an endpoint passes them by mistake.
_SECRET_KEYS = re.compile(r"pass|token|secret|mobile|phone|address|email", re.I)


def describe(method: str, path: str) -> tuple[str, Optional[str], Optional[int], str]:
    """(action, target type, target id, summary) for one change request."""
    method = method.upper()
    for methods, rx, action, target, summary in _RULES:
        m = rx.search(path)
        if method in methods and m:
            target_id = int(m.group(1)) if m.groups() and m.group(1) else None
            return action, target, target_id, summary.replace("{id}", str(target_id or ""))
    return "other", None, None, f"{method} {path}"


# Names added to the summary ("Deleted product #12 (Aam Jelly)"), read BEFORE the
# change so they survive a deletion. Customers are never named.
def _name_of(target_type: Optional[str], target_id: Optional[int]) -> Optional[str]:
    from app.database.models import Category, Product, QuestionTemplate, SurveyQuestion, User

    models = {"user": (User, "name"), "product": (Product, "name"), "category": (Category, "name"),
              "template": (QuestionTemplate, "name"), "question": (SurveyQuestion, "text")}
    if target_type not in models or not target_id:
        return None
    model, attr = models[target_type]
    db = SessionLocal()
    try:
        row = db.get(model, target_id)
        value = getattr(row, attr, None) if row is not None else None
        return " ".join(str(value).split())[:80] if value else None
    except Exception:
        return None
    finally:
        db.close()


def _clean(details: Any) -> Any:
    """Drop secret or personal fields from details (recursively)."""
    if isinstance(details, dict):
        return {k: _clean(v) for k, v in details.items() if not _SECRET_KEYS.search(str(k))}
    if isinstance(details, (list, tuple)):
        return [_clean(v) for v in details]
    return details


def note(request: Request, *, summary: Optional[str] = None, details: Optional[dict] = None,
         target_type: Optional[str] = None, target_id: Optional[int] = None, action: Optional[str] = None) -> None:
    """Add detail to the audit entry written for this request (call from an endpoint)."""
    extra = getattr(request.state, "audit", None) or {}
    for key, value in (("summary", summary), ("details", details), ("target_type", target_type),
                       ("target_id", target_id), ("action", action)):
        if value is not None:
            extra[key] = value
    request.state.audit = extra


def changes(before: dict, after: dict) -> dict:
    """{"field": [old, new]} for the fields whose value changed."""
    return {k: [before.get(k), after.get(k)] for k in after if before.get(k) != after.get(k)}


def record(*, action: str, account: Optional[dict] = None, target_type: Optional[str] = None,
           target_id: Optional[int] = None, summary: Optional[str] = None, details: Optional[dict] = None,
           method: Optional[str] = None, path: Optional[str] = None, status: Optional[int] = None,
           ip: Optional[str] = None, request_id: Optional[str] = None) -> None:
    """Write one entry in its own database session. Never raises: the log must not break the app."""
    account = account or {}
    db = SessionLocal()
    try:
        db.add(AuditLog(
            user_id=account.get("id"),
            user_name=(account.get("name") or None),
            user_role=account.get("role"),
            action=action[:60],
            target_type=target_type,
            target_id=target_id,
            summary=(summary or "")[:300] or None,
            details=_clean(details) if details else None,
            method=method,
            path=(path or "")[:300] or None,
            status=status,
            ip=(ip or "")[:64] or None,
            request_id=request_id,
        ))
        db.commit()
    except Exception as error:  # pragma: no cover - logged, never fatal
        db.rollback()
        log.warning("audit entry not written (%s): %s", action, error)
    finally:
        db.close()
    _maybe_prune()


def record_from_request(request: Request, *, action: str, account: Optional[dict] = None, status: int = 200,
                        target_type: Optional[str] = None, target_id: Optional[int] = None,
                        summary: Optional[str] = None, details: Optional[dict] = None) -> None:
    """record() with the request's address, path and request id filled in."""
    from app.api.auth import client_address

    record(
        action=action,
        account=account if account is not None else getattr(request.state, "account", None),
        target_type=target_type, target_id=target_id, summary=summary, details=details,
        method=request.method, path=request.url.path, status=status,
        ip=client_address(request), request_id=getattr(request.state, "request_id", None),
    )


# ------------------------------------------------------------
# Removing old entries (and expired sign-out records), at most once an hour
# ------------------------------------------------------------

_prune_lock = threading.Lock()
_last_prune = 0.0


def _maybe_prune() -> None:
    global _last_prune
    if time.time() - _last_prune < 3600 or not _prune_lock.acquire(blocking=False):
        return
    try:
        _last_prune = time.time()
        prune()
    finally:
        _prune_lock.release()


def prune() -> int:
    """Delete audit entries older than KEEP_DAYS and sign-out records past their expiry."""
    db = SessionLocal()
    try:
        removed = db.query(AuditLog).filter(AuditLog.created_at < datetime.utcnow() - timedelta(days=KEEP_DAYS)).delete(
            synchronize_session=False
        )
        db.query(RevokedToken).filter(RevokedToken.expires_at < datetime.utcnow()).delete(synchronize_session=False)
        db.commit()
        return removed
    except Exception as error:  # pragma: no cover
        db.rollback()
        log.warning("audit prune failed: %s", error)
        return 0
    finally:
        db.close()


# ------------------------------------------------------------
# Middleware: one entry per successful change request
# ------------------------------------------------------------

def install_audit(app):
    """Register AFTER install_access_control (so it runs outside it and sees the signed-in account)."""

    @app.middleware("http")
    async def audit_changes(request: Request, call_next):
        path = request.url.path.rstrip("/") or "/"
        if request.method not in WRITE or any(rx.search(path) for rx in SKIP):
            return await call_next(request)
        action, target_type, target_id, summary = describe(request.method, path)
        name = await run_in_threadpool(_name_of, target_type, target_id) if target_id else None
        response = await call_next(request)
        account = getattr(request.state, "account", None)
        if response.status_code >= 400 or not account:
            return response
        if name:
            summary = f"{summary} ({name})"
        extra = getattr(request.state, "audit", None) or {}
        await run_in_threadpool(
            record_from_request, request,
            action=extra.get("action", action),
            account=account,
            status=response.status_code,
            target_type=extra.get("target_type", target_type),
            target_id=extra.get("target_id", target_id),
            summary=extra.get("summary", summary),
            details=extra.get("details"),
        )
        return response
