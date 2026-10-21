"""
Notifications for the bell in the web dashboard and the mobile app.

The kinds:

  survey_missing_video   to every active admin
      A survey person submitted a survey (answers saved, status "submitted") but no
      recording reached the server: no analysis job and no feedback record for it.
      Created only after a grace period (FMCG_MISSING_VIDEO_MINUTES, default 10), so
      a video that is still uploading doesn't cause a false alarm. When the video
      arrives later, the notification is marked resolved ("Video received") and
      stops counting as unread.

  product_assigned       to the survey person
      An admin gave them a product to survey (Team page or Catalog page).

  client_recipe_updated  to every active admin
      The organization changed the recipe of a product in its portal
      (created by services/recipe_versions.py, with the version and what changed).

  client_round_started   to every active admin
  client_round_ended     to every active admin
      The organization started a survey round (recipe version, target, wanted-by
      date, note) or ended one early (services/rounds.py).

  round_started          to the survey persons who may survey the product
      "New round: <product>": shown in the mobile app's bell like any notification.

Clients poll GET /notifications/unread-count (every ~30 s) and open
GET /notifications when the bell is clicked. Missing-video checks run during those
requests (at most every CHECK_EVERY_SECONDS), so no extra background job is needed.
"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta
from typing import Iterable, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database.models import (
    AnalysisJob,
    Category,
    FeedbackRecord,
    Notification,
    Product,
    ProductAssignment,
    Respondent,
    SurveyResponse,
    User,
)

MISSING_VIDEO = "survey_missing_video"
PRODUCT_ASSIGNED = "product_assigned"

# Minutes to wait after a survey is submitted before reporting a missing video.
GRACE_MINUTES = max(0, int(os.environ.get("FMCG_MISSING_VIDEO_MINUTES", "10") or 10))
# Only surveys submitted in this window are checked (no flood of very old surveys
# the first time this runs).
LOOKBACK_HOURS = max(1, int(os.environ.get("FMCG_MISSING_VIDEO_LOOKBACK_HOURS", "48") or 48))
CHECK_EVERY_SECONDS = 20

_last_check = 0.0
_check_lock = threading.Lock()


def _iso(dt: Optional[datetime]) -> Optional[str]:
    """Stored times are UTC: send them with 'Z' so browsers and phones show local time."""
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


# ============================================================
# Creating
# ============================================================

def notify(
    db: Session,
    user_ids: Iterable[int],
    kind: str,
    title: str,
    message: str,
    data: Optional[dict] = None,
    dedupe_key: Optional[str] = None,
) -> int:
    """One notification per recipient. With a dedupe_key, nobody gets the same one twice."""
    recipients = {int(u) for u in user_ids if u is not None}
    if not recipients:
        return 0
    if dedupe_key:
        already = {
            uid
            for (uid,) in db.query(Notification.user_id)
            .filter(Notification.dedupe_key == dedupe_key, Notification.user_id.in_(recipients))
            .all()
        }
        recipients -= already
    now = datetime.utcnow()
    for uid in recipients:
        db.add(
            Notification(
                user_id=uid,
                kind=kind,
                title=title[:200],
                message=message,
                data=data or {},
                dedupe_key=dedupe_key,
                is_read=0,
                created_at=now,
            )
        )
    try:
        db.commit()
    except IntegrityError:
        # Another request created the same notification at the same moment.
        db.rollback()
        return 0
    return len(recipients)


def _product_info(db: Session, product_id: int) -> dict:
    product = db.get(Product, product_id)
    category = db.get(Category, product.category_id) if product else None
    return {
        "product_id": product_id,
        "product_name": product.name if product else f"Product {product_id}",
        "category_name": category.name if category else None,
    }


def notify_product_assigned(db: Session, new_assignments: dict, assigned_by: Optional[str]) -> int:
    """
    new_assignments: {user_id: {product_id, ...}} with only the products that were
    just added. Admins are skipped (they can survey every product anyway).
    """
    created = 0
    admin_name = (assigned_by or "").strip() or "Your admin"
    for user_id, product_ids in new_assignments.items():
        user = db.get(User, user_id)
        if not user or user.role == "admin" or not user.is_active:
            continue
        for product_id in sorted(product_ids):
            info = _product_info(db, product_id)
            row = (
                db.query(ProductAssignment)
                .filter_by(user_id=user_id, product_id=product_id)
                .first()
            )
            category = f" ({info['category_name']})" if info["category_name"] else ""
            created += notify(
                db,
                [user_id],
                PRODUCT_ASSIGNED,
                title=f"New product: {info['product_name']}",
                message=f"{admin_name} has authorised you to survey {info['product_name']}{category}.",
                data={
                    **info,
                    "assigned_by": admin_name,
                    "assigned_at": _iso(row.created_at if row and row.created_at else datetime.utcnow()),
                },
                # The assignment row's id: removing and re-adding the product notifies again.
                dedupe_key=f"assigned:{row.id}" if row else None,
            )
    return created


# ============================================================
# Missing videos (checked when someone looks at notifications)
# ============================================================

def _has_media(db: Session, response_ids: list) -> set:
    if not response_ids:
        return set()
    jobs = {r for (r,) in db.query(AnalysisJob.response_id).filter(AnalysisJob.response_id.in_(response_ids)).all()}
    records = {r for (r,) in db.query(FeedbackRecord.response_id).filter(FeedbackRecord.response_id.in_(response_ids)).all()}
    return jobs | records


def sync_missing_video(db: Session, force: bool = False) -> None:
    """Create missing-video notifications for admins, and resolve those whose video arrived."""
    global _last_check
    with _check_lock:
        if not force and time.time() - _last_check < CHECK_EVERY_SECONDS:
            return
        _last_check = time.time()

    now = datetime.utcnow()
    admins = [uid for (uid,) in db.query(User.id).filter(User.role == "admin", User.is_active == 1).all()]

    # 1. New: submitted (not analyzed) surveys with no recording, after the grace period.
    if admins:
        candidates = (
            db.query(SurveyResponse)
            .filter(
                SurveyResponse.status == "submitted",
                SurveyResponse.submitted_at.isnot(None),
                SurveyResponse.submitted_at <= now - timedelta(minutes=GRACE_MINUTES),
                SurveyResponse.submitted_at >= now - timedelta(hours=LOOKBACK_HOURS),
            )
            .order_by(SurveyResponse.submitted_at.asc())
            .limit(200)
            .all()
        )
        with_media = _has_media(db, [c.id for c in candidates])
        for survey in candidates:
            if survey.id in with_media:
                continue
            person = db.get(User, survey.user_id) if survey.user_id else None
            if person is not None and person.role == "admin":
                continue  # only survey persons' surveys
            name = (person.name if person else None) or survey.executive_name or "A survey person"
            info = _product_info(db, survey.product_id)
            respondent = db.get(Respondent, survey.respondent_id)
            notify(
                db,
                admins,
                MISSING_VIDEO,
                title="Survey submitted without a video",
                message=(
                    f"{name} submitted a survey for {info['product_name']} "
                    "but hasn't uploaded the customer's video yet."
                ),
                data={
                    **info,
                    "survey_id": survey.id,
                    "survey_person": {
                        "id": person.id if person else None,
                        "name": name,
                        "mobile": person.mobile if person else None,
                        "email": person.email if person else None,
                    },
                    # The customer's city only: no name, mobile or address in notifications.
                    "respondent_location": respondent.location if respondent else None,
                    "survey_location": survey.location,
                    "submitted_at": _iso(survey.submitted_at),
                    "survey_started_at": _iso(survey.created_at),
                },
                dedupe_key=f"missing_video:{survey.id}",
            )

    # 2. Resolved: the video arrived, the survey was analyzed, or it was deleted.
    open_items = (
        db.query(Notification)
        .filter(Notification.kind == MISSING_VIDEO, Notification.resolved_at.is_(None))
        .limit(1000)
        .all()
    )
    if not open_items:
        return
    survey_ids = {int((n.data or {}).get("survey_id") or 0) for n in open_items} - {0}
    surveys = {s.id: s for s in db.query(SurveyResponse).filter(SurveyResponse.id.in_(survey_ids)).all()} if survey_ids else {}
    with_media = _has_media(db, list(survey_ids))
    changed = False
    for n in open_items:
        sid = int((n.data or {}).get("survey_id") or 0)
        survey = surveys.get(sid)
        if survey is None:
            reason = "survey_deleted"
        elif sid in with_media or survey.status == "analyzed":
            reason = "video_received"
        else:
            continue
        n.resolved_at = now
        n.data = {**(n.data or {}), "resolution": reason, "resolved_at": _iso(now)}
        if not n.is_read:
            n.is_read = 1  # nothing left to act on: don't keep it in the unread count
            n.read_at = now
        changed = True
    if changed:
        db.commit()


# ============================================================
# Reading
# ============================================================

def notification_out(n: Notification) -> dict:
    return {
        "id": n.id,
        "kind": n.kind,
        "title": n.title,
        "message": n.message,
        "data": n.data or {},
        "is_read": bool(n.is_read),
        "read_at": _iso(n.read_at),
        "resolved": n.resolved_at is not None,
        "resolved_at": _iso(n.resolved_at),
        "created_at": _iso(n.created_at),
    }


def unread_count(db: Session, user: User) -> int:
    return db.query(Notification).filter(Notification.user_id == user.id, Notification.is_read == 0).count()


def latest_id(db: Session, user: User) -> int:
    row = db.query(Notification.id).filter(Notification.user_id == user.id).order_by(Notification.id.desc()).first()
    return row[0] if row else 0


def refresh_for(db: Session, user: User) -> None:
    """Admins: bring missing-video notifications up to date before answering."""
    if user.role == "admin":
        sync_missing_video(db)


def list_for(db: Session, user: User, limit: int = 30, unread_only: bool = False, before_id: Optional[int] = None) -> list:
    query = db.query(Notification).filter(Notification.user_id == user.id)
    if unread_only:
        query = query.filter(Notification.is_read == 0)
    if before_id:
        query = query.filter(Notification.id < before_id)
    return [notification_out(n) for n in query.order_by(Notification.id.desc()).limit(limit).all()]


def mark_read(db: Session, user: User, notification_id: int) -> Optional[Notification]:
    n = db.query(Notification).filter(Notification.id == notification_id, Notification.user_id == user.id).first()
    if n and not n.is_read:
        n.is_read = 1
        n.read_at = datetime.utcnow()
        db.commit()
    return n


def mark_all_read(db: Session, user: User) -> int:
    count = (
        db.query(Notification)
        .filter(Notification.user_id == user.id, Notification.is_read == 0)
        .update({Notification.is_read: 1, Notification.read_at: datetime.utcnow()}, synchronize_session=False)
    )
    db.commit()
    return count
