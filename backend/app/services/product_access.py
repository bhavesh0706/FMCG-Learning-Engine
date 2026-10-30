"""
Which products each person may see and survey: the round workflow.

  New products, and any product after a new recipe version (products.needs_round = 1):
    1. The Super Admin creates it (recipe, questions). Nobody else sees it yet.
    2. The Super Admin starts a round. Admins see the product and the round.
    3. An admin assigns the live round to survey persons; only they see it.
    4. Every next round is started by the Super Admin and assigned again.
  Existing products, surveyed before rounds were required (needs_round = 0):
    they stay open as before, without starting a round: surveys go into their
    latest round, every active survey person sees them, and once an admin assigns
    specific people only those people do. A new recipe version (or a round started
    by the Super Admin) switches them to the workflow above.

Rules (enforced by the API, not only hidden in the apps):
  - Super Admin: every product (they own the catalog).
  - Admin: products with a started round, and existing products.
  - Survey person: products whose CURRENT round (the live round, or an existing
    product's latest round) is assigned to them, or that is an existing product's
    round with nobody assigned. Disabled accounts see nothing and can't be assigned.

product_assignments rows carry round_id: one row = this survey person may survey
this round. Rows of earlier rounds stay as history.
"""

from datetime import datetime, timedelta
from typing import Optional, Set

from sqlalchemy.orm import Session

from app.database.models import Product, ProductAssignment, SurveyRound, User

LIVE = "live"
# Surveys recorded offline while a round was live may upload a little later.
LATE_UPLOAD_GRACE = timedelta(days=7)


class AssignmentError(ValueError):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def live_rounds(db: Session) -> dict:
    """{product_id: live SurveyRound}"""
    return {r.product_id: r for r in db.query(SurveyRound).filter(SurveyRound.status == LIVE).all()}


def live_round_of(db: Session, product_id: int) -> Optional[SurveyRound]:
    return (
        db.query(SurveyRound)
        .filter(SurveyRound.product_id == product_id, SurveyRound.status == LIVE)
        .order_by(SurveyRound.number.desc())
        .first()
    )


def open_product_ids(db: Session) -> Set[int]:
    """Existing products that stay open without a Super Admin round (needs_round = 0)."""
    return {pid for (pid,) in db.query(Product.id).filter(Product.needs_round == 0).all()}


def current_rounds(db: Session) -> dict:
    """{product_id: SurveyRound new surveys go into}: the live round, or an existing
    (open) product's latest round."""
    rounds = live_rounds(db)
    open_ids = open_product_ids(db) - set(rounds)
    if open_ids:
        for r in (db.query(SurveyRound).filter(SurveyRound.product_id.in_(list(open_ids)))
                  .order_by(SurveyRound.product_id, SurveyRound.number).all()):
            rounds[r.product_id] = r  # ordered by number: the latest wins
    return rounds


def current_round_of(db: Session, product_id: int) -> Optional[SurveyRound]:
    return current_rounds(db).get(product_id)


def started_product_ids(db: Session) -> Set[int]:
    """Products admins see: a round was started, or an existing open product."""
    return {pid for (pid,) in db.query(SurveyRound.product_id).distinct().all()} | open_product_ids(db)


def _people_by_round(db: Session, round_ids) -> dict:
    out: dict = {}
    if round_ids:
        for rid, uid in db.query(ProductAssignment.round_id, ProductAssignment.user_id).filter(
                ProductAssignment.round_id.in_(list(round_ids))).all():
            out.setdefault(rid, set()).add(uid)
    return out


def visible_product_ids(db: Session, user) -> Optional[Set[int]]:
    """Products this person may see. None = no restriction (Super Admin)."""
    role = getattr(user, "role", None)
    if user is None or role == "client":
        return None
    if role == "admin":
        return started_product_ids(db)
    if not getattr(user, "is_active", 1):
        return set()
    rounds = current_rounds(db)
    open_ids = open_product_ids(db)
    people = _people_by_round(db, [r.id for r in rounds.values()])
    visible = set()
    for pid, r in rounds.items():
        assigned = people.get(r.id, set())
        if user.id in assigned or (pid in open_ids and not assigned):
            visible.add(pid)
    return visible


def hidden_product_ids(db: Session, user) -> Set[int]:
    """Products this person may NOT see (complement of visible_product_ids)."""
    visible = visible_product_ids(db, user)
    if visible is None:
        return set()
    return {pid for (pid,) in db.query(Product.id).all()} - visible


def round_for_survey(db: Session, user, product_id: int) -> Optional[SurveyRound]:
    """
    The round a new survey by this person goes into, or None when they may not
    survey the product: its current round (see current_rounds), if they may survey
    it. A survey recorded offline during a round may still upload within
    LATE_UPLOAD_GRACE after the round closed (if that round was theirs).
    """
    role = getattr(user, "role", None)
    if user is None or role == "client" or not getattr(user, "is_active", 1):
        return None
    current = current_round_of(db, product_id)
    if current is None:
        latest = (db.query(SurveyRound).filter(SurveyRound.product_id == product_id)
                  .order_by(SurveyRound.number.desc()).first())
        if latest is None or latest.closed_at is None or datetime.utcnow() - latest.closed_at > LATE_UPLOAD_GRACE:
            return None
        current = latest
    if role == "admin":
        return current
    assigned = _people_by_round(db, [current.id]).get(current.id, set())
    if user.id in assigned:
        return current
    product = db.get(Product, product_id)
    if product is not None and not product.needs_round and not assigned:
        return current  # existing product, open to every survey person
    return None


def can_survey(db: Session, user, product_id: int) -> bool:
    return round_for_survey(db, user, int(product_id)) is not None


def assignments_for_user(db: Session, user_id: int) -> list:
    """Products whose current round is assigned to this person by name."""
    rounds = {r.id: r.product_id for r in current_rounds(db).values()}
    if not rounds:
        return []
    rows = (
        db.query(ProductAssignment.round_id)
        .filter(ProductAssignment.user_id == user_id, ProductAssignment.round_id.in_(list(rounds)))
        .all()
    )
    return sorted({rounds[rid] for (rid,) in rows})


def assignees_for_product(db: Session, product_id: int) -> list:
    """Survey persons assigned by name to the product's current round."""
    r = current_round_of(db, product_id)
    if r is None:
        return []
    return sorted(uid for (uid,) in db.query(ProductAssignment.user_id).filter(ProductAssignment.round_id == r.id).all())


def _assignable_people(db: Session, user_ids) -> Set[int]:
    wanted = {int(u) for u in user_ids}
    if not wanted:
        return set()
    found = db.query(User.id, User.role, User.is_active).filter(User.id.in_(wanted)).all()
    ok = {uid for uid, role, active in found if role == "executive" and active}
    if wanted - ok:
        raise AssignmentError("Only active survey persons can be assigned. Reactivate a disabled member first.", status=400)
    return ok


def set_product_users(db: Session, product_id: int, user_ids) -> list:
    """Who surveys the product's current round (earlier rounds are untouched).
    An existing open product with nobody assigned is open to everyone again."""
    r = current_round_of(db, product_id)
    if r is None:
        raise AssignmentError("No round of this product is live. The Super Admin starts rounds; assign people once it has started.")
    wanted = _assignable_people(db, user_ids)
    current = set(assignees_for_product(db, product_id))
    for uid in current - wanted:
        db.query(ProductAssignment).filter_by(round_id=r.id, user_id=uid).delete(synchronize_session=False)
    for uid in wanted - current:
        db.add(ProductAssignment(user_id=uid, product_id=product_id, round_id=r.id))
    db.commit()
    return sorted(wanted)


def set_user_products(db: Session, user_id: int, product_ids) -> list:
    """Which current rounds this person surveys (by product)."""
    _assignable_people(db, [user_id])
    rounds = current_rounds(db)
    wanted = {int(p) for p in product_ids}
    if wanted - set(rounds):
        raise AssignmentError("Some products have no live round. The Super Admin starts rounds; assign them once started.")
    current = set(assignments_for_user(db, user_id))
    for pid in current - wanted:
        db.query(ProductAssignment).filter_by(round_id=rounds[pid].id, user_id=user_id).delete(synchronize_session=False)
    for pid in wanted - current:
        db.add(ProductAssignment(user_id=user_id, product_id=pid, round_id=rounds[pid].id))
    db.commit()
    return sorted(wanted)


def visible_count(db: Session, user, product_ids) -> Optional[int]:
    hidden = hidden_product_ids(db, user)
    return sum(1 for p in product_ids if p not in hidden)