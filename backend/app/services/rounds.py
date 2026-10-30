"""
Survey rounds: consumers taste a product made with one recipe version until the
target number of surveys is reached. Every round has its own report, so the
organization can change the recipe, start a new round and compare the two.

Who does what
  Super Admin   starts every round (portal: Start new round), can end it early
  admin         sees the product once a round has started, and assigns each live
                round to survey persons (services/product_access.py)
  survey person sees and surveys only the live rounds assigned to them; a new
                survey is filed under that round (round_for_new_survey)

Rules
  - one live round per product; the next round starts only when the Super Admin
    starts it (never automatically)
  - a round closes by itself when its submitted surveys reach the target
  - a survey can only go into the live round assigned to its survey person
    (surveys recorded offline may upload up to 7 days after the round closed)
  - products that already had surveys get Round 1 holding them (create_first_rounds)
"""

from __future__ import annotations

import os
from datetime import date, datetime
from typing import Iterable, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database.models import Product, RecipeVersion, SurveyResponse, SurveyRound, User

LIVE = "live"
CLOSED = "closed"
DONE_STATUSES = ("submitted", "analyzed")

DEFAULT_TARGET = max(1, int(os.environ.get("FMCG_ROUND_TARGET", "30") or 30))
MIN_TARGET, MAX_TARGET = 1, 5000

# Notification kinds (services/notifications.py lists every kind)
ROUND_STARTED_BY_ORG = "client_round_started"  # to admins
ROUND_ENDED_BY_ORG = "client_round_ended"  # to admins
ROUND_STARTED = "round_started"  # to survey persons (shown in the mobile app's bell)

RESEARCH_TEAM = "Research team"


class RoundError(Exception):
    """A request that can't be done; the message is shown to the person."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _iso(dt) -> Optional[str]:
    if isinstance(dt, datetime):
        return dt.isoformat(timespec="seconds") + "Z"
    if isinstance(dt, date):
        return dt.isoformat()
    return None


# ============================================================
# Reading
# ============================================================

def rounds_of(db: Session, product_id: int) -> list[SurveyRound]:
    return db.query(SurveyRound).filter(SurveyRound.product_id == product_id).order_by(SurveyRound.number).all()


def round_by_number(db: Session, product_id: int, number: int) -> Optional[SurveyRound]:
    return db.query(SurveyRound).filter(SurveyRound.product_id == product_id, SurveyRound.number == number).first()


def live_round(db: Session, product_id: int) -> Optional[SurveyRound]:
    return (
        db.query(SurveyRound)
        .filter(SurveyRound.product_id == product_id, SurveyRound.status == LIVE)
        .order_by(SurveyRound.number.desc())
        .first()
    )


def latest_round(db: Session, product_id: int) -> Optional[SurveyRound]:
    return db.query(SurveyRound).filter(SurveyRound.product_id == product_id).order_by(SurveyRound.number.desc()).first()


def rounds_by_product(db: Session, product_ids: Optional[Iterable[int]] = None) -> dict[int, list[SurveyRound]]:
    """{product_id: [rounds, oldest first]} in one query."""
    q = db.query(SurveyRound)
    if product_ids is not None:
        ids = list(product_ids)
        if not ids:
            return {}
        q = q.filter(SurveyRound.product_id.in_(ids))
    out: dict[int, list[SurveyRound]] = {}
    for r in q.order_by(SurveyRound.product_id, SurveyRound.number).all():
        out.setdefault(r.product_id, []).append(r)
    return out


def done_counts(db: Session, round_ids: Iterable[int]) -> dict[int, int]:
    """{round_id: surveys submitted or analyzed} (drafts don't count)."""
    ids = [i for i in round_ids if i is not None]
    if not ids:
        return {}
    rows = (
        db.query(SurveyResponse.round_id, func.count(SurveyResponse.id))
        .filter(SurveyResponse.round_id.in_(ids), SurveyResponse.status.in_(DONE_STATUSES))
        .group_by(SurveyResponse.round_id)
        .all()
    )
    return {rid: n for rid, n in rows}


def starter_name(r: SurveyRound, viewer_role: Optional[str]) -> str:
    """Organizations never see the names of the research team's admins."""
    if r.started_by_role == "client":
        return r.started_by_name or "Super Admin"
    if r.started_by_role == "admin" and viewer_role != "client":
        return r.started_by_name or RESEARCH_TEAM
    return RESEARCH_TEAM


def round_out(r: SurveyRound, done: int = 0, viewer_role: Optional[str] = None, report=None) -> dict:
    return {
        "id": r.id,
        "number": r.number,
        "status": r.status,
        "recipe_version": r.recipe_version,
        "target": r.target,
        "done": done,
        "due_date": _iso(r.due_date),
        "note": r.note or "",
        "started_by_name": starter_name(r, viewer_role),
        "started_by_role": r.started_by_role,
        "started_at": _iso(r.started_at),
        "closed_at": _iso(r.closed_at),
        "closed_reason": r.closed_reason,
        "rating": report.overall_rating if report is not None else None,
        "report_count": report.response_count if report is not None else 0,
    }


# ============================================================
# Recipe versions used by rounds
# ============================================================

def latest_version_number(db: Session, product_id: int) -> Optional[int]:
    v = db.query(func.max(RecipeVersion.version)).filter(RecipeVersion.product_id == product_id).scalar()
    return int(v) if v else None


def ensure_first_version(db: Session, product: Product) -> Optional[int]:
    """
    A recipe saved before version history existed becomes version 1, so a round
    can say which recipe it used. Returns the latest version number (None: no recipe).
    """
    latest = latest_version_number(db, product.id)
    if latest is None and product.recipe:
        db.add(RecipeVersion(product_id=product.id, version=1, recipe=product.recipe, changed_by_name=None,
                             changed_by_role=None, changes=["Recipe as it was before version history"],
                             created_at=product.updated_at or datetime.utcnow()))
        db.commit()
        latest = 1
    return latest


# ============================================================
# Filing surveys
# ============================================================

def survey_versions_from(rounds: list[SurveyRound]) -> list[dict]:
    """
    The product versions (recipe versions) that are or were in fieldwork, from a
    product's rounds, oldest first: [{version, rounds: [1, 3], live: bool}].
    These are the versions a survey person can choose when taking a survey.
    """
    found: dict[int, dict] = {}
    for r in sorted(rounds, key=lambda x: x.number):
        if r.recipe_version is None:
            continue
        v = found.setdefault(r.recipe_version, {"version": r.recipe_version, "rounds": [], "live": False})
        v["rounds"].append(r.number)
        v["live"] = v["live"] or r.status == LIVE
    return [found[k] for k in sorted(found)]


def survey_versions(db: Session, product_id: int) -> list[dict]:
    return survey_versions_from(rounds_of(db, product_id))


def versions_by_product(db: Session, product_ids: Iterable[int]) -> dict[int, dict]:
    """
    {product_id: {"recipe_versions": [1, 2, 3], "survey_versions": [{version, rounds, live}]}}
    in two queries. recipe_versions: every saved version; survey_versions: the ones
    in fieldwork (they have a survey round), which survey persons can choose.
    """
    ids = [i for i in product_ids if i is not None]
    if not ids:
        return {}
    saved: dict[int, list[int]] = {}
    for pid, v in (
        db.query(RecipeVersion.product_id, RecipeVersion.version)
        .filter(RecipeVersion.product_id.in_(ids))
        .order_by(RecipeVersion.product_id, RecipeVersion.version)
        .all()
    ):
        saved.setdefault(pid, []).append(v)
    by_rounds = rounds_by_product(db, ids)
    # Analyzed surveys and their average rating per version (Home dashboard, version picker).
    # Ratings come from the analyses (feedback_records), like every other average in the app.
    from app.database.models import FeedbackRecord

    counts = {
        (pid, v): n
        for pid, v, n in (
            db.query(SurveyResponse.product_id, SurveyResponse.recipe_version, func.count(SurveyResponse.id))
            .filter(SurveyResponse.product_id.in_(ids), SurveyResponse.status == "analyzed",
                    SurveyResponse.recipe_version.isnot(None))
            .group_by(SurveyResponse.product_id, SurveyResponse.recipe_version)
            .all()
        )
    }
    averages = {
        (pid, v): avg
        for pid, v, avg in (
            db.query(SurveyResponse.product_id, SurveyResponse.recipe_version, func.avg(FeedbackRecord.overall_rating))
            .join(FeedbackRecord, FeedbackRecord.response_id == SurveyResponse.id)
            .filter(SurveyResponse.product_id.in_(ids), SurveyResponse.status == "analyzed",
                    SurveyResponse.recipe_version.isnot(None), FeedbackRecord.overall_rating > 0)
            .group_by(SurveyResponse.product_id, SurveyResponse.recipe_version)
            .all()
        )
    }
    stats = {key: (counts.get(key, 0), averages.get(key)) for key in set(counts) | set(averages)}
    out = {}
    for pid in ids:
        versions = survey_versions_from(by_rounds.get(pid, []))
        for v in versions:
            n, avg = stats.get((pid, v["version"]), (0, None))
            v["responses"] = int(n or 0)
            v["rating"] = round(float(avg), 2) if avg is not None else None
        out[pid] = {"recipe_versions": saved.get(pid, []), "survey_versions": versions}
    return out


def round_for_new_survey(db: Session, product_id: int, recipe_version: Optional[int] = None, user=None) -> SurveyRound:
    """
    The round a new survey by this person goes into: the product's live round, if
    an Admin assigned it to them. Rounds are never created here: only the Super
    Admin starts rounds. A version that isn't the live round's version is refused.
    """
    from app.services.product_access import round_for_survey

    r = round_for_survey(db, user, product_id)
    if r is None:
        raise RoundError(
            "This product has no survey round assigned to you. The Super Admin starts rounds and your admin assigns them.",
            status=409,
        )
    if recipe_version is not None and r.recipe_version is not None and int(recipe_version) != int(r.recipe_version):
        raise RoundError(
            f"Version {recipe_version} of this product isn't being surveyed now. Use version {r.recipe_version}.",
            status=400,
        )
    return r


def close_if_complete(db: Session, round_id: Optional[int]) -> bool:
    """Called after a survey is submitted: the round closes when the target is reached."""
    if not round_id:
        return False
    r = db.get(SurveyRound, round_id)
    if r is None or r.status != LIVE:
        return False
    if done_counts(db, [r.id]).get(r.id, 0) < (r.target or 0):
        return False
    r.status, r.closed_at, r.closed_reason = CLOSED, datetime.utcnow(), "target"
    db.commit()
    return True


def create_first_rounds(db: Session) -> int:
    """
    Products with surveys that belong to no round: Round 1 holds them (closed,
    target = the number of surveys), or, if the product already has rounds, they
    join its latest round. Does nothing once done.
    """
    loose = (
        db.query(SurveyResponse.product_id, func.count(SurveyResponse.id), func.min(SurveyResponse.created_at),
                 func.max(SurveyResponse.created_at))
        .filter(SurveyResponse.round_id.is_(None))
        .group_by(SurveyResponse.product_id)
        .all()
    )
    created = 0
    for product_id, count, first, last in loose:
        product = db.get(Product, product_id)
        if product is None:
            continue
        r = latest_round(db, product_id)
        if r is None:
            done = (
                db.query(func.count(SurveyResponse.id))
                .filter(SurveyResponse.product_id == product_id, SurveyResponse.round_id.is_(None),
                        SurveyResponse.status.in_(DONE_STATUSES))
                .scalar()
                or 0
            )
            r = SurveyRound(
                product_id=product_id, number=1, recipe_version=ensure_first_version(db, product),
                target=max(done, 1), status=CLOSED, started_by=None, started_by_name=RESEARCH_TEAM,
                started_by_role="system", started_at=first or datetime.utcnow(), closed_at=last or datetime.utcnow(),
                closed_reason="target",
            )
            db.add(r)
            db.flush()
            created += 1
        db.query(SurveyResponse).filter(SurveyResponse.product_id == product_id, SurveyResponse.round_id.is_(None)).update(
            {SurveyResponse.round_id: r.id}, synchronize_session=False
        )
        db.commit()
    if created:
        print(f"Database updated: created Round 1 for {created} products that already had surveys")
    return created


# ============================================================
# Starting and ending rounds
# ============================================================

def _parse_due(value) -> Optional[date]:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        raise RoundError("Enter the 'wanted by' date as a date, or leave it empty.")


def start_round(db: Session, product: Product, account: dict, recipe_version: Optional[int] = None,
                target: Optional[int] = None, due_date=None, note: Optional[str] = None) -> SurveyRound:
    live = live_round(db, product.id)
    if live is not None:
        raise RoundError(f"Round {live.number} is still collecting surveys. The next round can start when it is complete.", 409)
    latest_v = ensure_first_version(db, product)
    if latest_v is None:
        raise RoundError("Add a recipe first, so the round knows which recipe consumers taste.")
    version = int(recipe_version or latest_v)
    if not db.query(RecipeVersion.id).filter(RecipeVersion.product_id == product.id, RecipeVersion.version == version).first():
        raise RoundError(f"Recipe version {version} doesn't exist for this product.")
    target = int(target or DEFAULT_TARGET)
    if not MIN_TARGET <= target <= MAX_TARGET:
        raise RoundError(f"Consumers to survey must be between {MIN_TARGET} and {MAX_TARGET}.")
    due = _parse_due(due_date)
    if due is not None and due < date.today():
        raise RoundError("The 'wanted by' date is in the past.")
    last = latest_round(db, product.id)
    r = SurveyRound(
        product_id=product.id, number=(last.number if last else 0) + 1, recipe_version=version, target=target,
        due_date=due, note=(" ".join((note or "").split())[:1000] or None), status=LIVE,
        started_by=account.get("id"), started_by_name=account.get("name"), started_by_role=account.get("role"),
        started_at=datetime.utcnow(),
    )
    db.add(r)
    product.needs_round = 1  # from now on every round is started by the Super Admin
    db.commit()
    db.refresh(r)
    _notify_round_started(db, product, r)
    return r


def end_round(db: Session, r: SurveyRound) -> SurveyRound:
    if r.status != LIVE:
        raise RoundError(f"Round {r.number} has already ended.")
    r.status, r.closed_at, r.closed_reason = CLOSED, datetime.utcnow(), "ended"
    db.commit()
    db.refresh(r)
    return r


# ============================================================
# Notifications
# ============================================================

def _admins(db: Session) -> list[int]:
    return [uid for (uid,) in db.query(User.id).filter(User.role == "admin", User.is_active == 1).all()]


def _survey_people(db: Session, product_id: int) -> list[int]:
    """Active survey persons assigned to the product's live round (none right after the
    Super Admin starts it: they're notified when an Admin assigns them)."""
    from app.services.product_access import assignees_for_product

    assigned = assignees_for_product(db, product_id)
    if not assigned:
        return []
    q = db.query(User.id).filter(User.role == "executive", User.is_active == 1, User.id.in_(assigned))
    return [uid for (uid,) in q.all()]


def _notify_round_started(db: Session, product: Product, r: SurveyRound) -> None:
    from app.services.notifications import _product_info, notify

    info = _product_info(db, product.id)
    data = {**info, "round": r.number, "round_id": r.id, "recipe_version": r.recipe_version, "target": r.target,
            "due_date": _iso(r.due_date), "note": r.note or "", "started_by": r.started_by_name,
            "started_at": _iso(r.started_at)}
    due = f" by {r.due_date.strftime('%d %b %Y')}" if r.due_date else ""
    if r.started_by_role == "client":
        who = r.started_by_name or "The Super Admin"
        notify(
            db, _admins(db), ROUND_STARTED_BY_ORG,
            title=f"{who} started Round {r.number} of {info['product_name']}",
            message=f"Recipe v{r.recipe_version}, {r.target} consumers{due}." + (f' Note: "{r.note}"' if r.note else ""),
            data=data, dedupe_key=f"round:{r.id}:started",
        )
    notify(
        db, _survey_people(db, product.id), ROUND_STARTED,
        title=f"New round: {info['product_name']} (recipe v{r.recipe_version})",
        message=f"Round {r.number} tests recipe v{r.recipe_version}. Please survey {r.target} consumers{due}.",
        data=data, dedupe_key=f"round:{r.id}:field",
    )


def notify_round_ended(db: Session, product: Product, r: SurveyRound, account: dict) -> None:
    if account.get("role") != "client":
        return
    from app.services.notifications import _product_info, notify

    info = _product_info(db, product.id)
    who = account.get("name") or "The Super Admin"
    done = done_counts(db, [r.id]).get(r.id, 0)
    notify(
        db, _admins(db), ROUND_ENDED_BY_ORG,
        title=f"{who} ended Round {r.number} of {info['product_name']}",
        message=f"Ended early with {done} of {r.target} consumers surveyed.",
        data={**info, "round": r.number, "round_id": r.id, "done": done, "target": r.target},
        dedupe_key=f"round:{r.id}:ended",
    )