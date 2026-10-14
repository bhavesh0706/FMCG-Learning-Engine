"""
Notification bell API (web dashboard and mobile app). Any signed-in account.

GET  /notifications/unread-count     {unread_count, latest_id}: cheap, poll every ~30 s
GET  /notifications                  ?limit=30&unread_only=false&before_id=
                                     -> {unread_count, latest_id, notifications: [...]}
POST /notifications/{id}/read        mark one as read  -> {notification, unread_count}
POST /notifications/read-all         mark all as read  -> {updated, unread_count}

Each notification: {id, kind, title, message, data, is_read, read_at, resolved,
resolved_at, created_at}. Times are UTC ISO-8601 with "Z".
kind = "survey_missing_video" (admins) | "product_assigned" (survey persons).
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.auth import current_user
from app.database.database import get_db
from app.database.models import User
from app.services import notifications as svc

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.get("/unread-count")
def unread_count(user: User = Depends(current_user), db: Session = Depends(get_db)):
    svc.refresh_for(db, user)
    return {"unread_count": svc.unread_count(db, user), "latest_id": svc.latest_id(db, user)}


@router.get("")
def list_notifications(
    limit: int = Query(default=30, ge=1, le=100),
    unread_only: bool = False,
    before_id: Optional[int] = Query(default=None, ge=1),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    svc.refresh_for(db, user)
    return {
        "unread_count": svc.unread_count(db, user),
        "latest_id": svc.latest_id(db, user),
        "notifications": svc.list_for(db, user, limit=limit, unread_only=unread_only, before_id=before_id),
    }


@router.post("/read-all")
def read_all(user: User = Depends(current_user), db: Session = Depends(get_db)):
    updated = svc.mark_all_read(db, user)
    return {"updated": updated, "unread_count": 0}


@router.post("/{notification_id}/read")
def read_one(notification_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    n = svc.mark_read(db, user, notification_id)
    if n is None:
        raise HTTPException(status_code=404, detail="Notification not found.")
    return {"notification": svc.notification_out(n), "unread_count": svc.unread_count(db, user)}
