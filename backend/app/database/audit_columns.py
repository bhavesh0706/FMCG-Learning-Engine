"""
Fills created_by / mod_by / mod_at (models.AuditMixin) on every ORM insert and update.

The access middleware (api/access.py) stores the signed-in account's id in
`current_user_id` for the whole request, so every session opened while handling
it (get_db, or a service's own SessionLocal()) knows who is making the change.
Outside a request (background workers, start-up, seed scripts) it is None.

A session can name the user itself: session.info["user_id"] = 12.

mod_at is also set by the column's onupdate, which covers bulk
query(...).update(...) statements; mod_by can only be set for objects the session
saves, so bulk updates leave mod_by as it was.
"""

from contextvars import ContextVar
from datetime import datetime
from typing import Optional

from sqlalchemy import event
from sqlalchemy.orm import Session

current_user_id: ContextVar[Optional[int]] = ContextVar("current_user_id", default=None)


def _user_id(session: Session) -> Optional[int]:
    if "user_id" in session.info:
        return session.info["user_id"]
    return current_user_id.get()


@event.listens_for(Session, "before_flush")
def _stamp_audit_columns(session, flush_context, instances):  # noqa: ARG001 (SQLAlchemy event signature)
    from app.database.models import AuditMixin

    uid = _user_id(session)
    now = datetime.utcnow()
    for obj in session.new:
        if isinstance(obj, AuditMixin):
            if obj.created_by is None:
                obj.created_by = uid
            if obj.mod_by is None:
                obj.mod_by = uid
            if obj.mod_at is None:
                obj.mod_at = now
    for obj in session.dirty:
        if isinstance(obj, AuditMixin) and session.is_modified(obj, include_collections=False):
            obj.mod_by = uid
            obj.mod_at = now
