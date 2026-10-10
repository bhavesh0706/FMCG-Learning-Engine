"""
Which products each person may survey.

Rule:
  - Admins see every product.
  - A product with no assignments is open to every executive.
  - A product assigned to specific executives is visible only to them.

Used by the catalog (product lists, questions), survey creation, and Team/Catalog admin.
"""

from typing import Optional, Set

from sqlalchemy.orm import Session

from app.database.models import ProductAssignment


def hidden_product_ids(db: Session, user) -> Set[int]:
    """Products this person may NOT survey (empty for admins and when nothing is assigned)."""
    if user is None or getattr(user, "role", None) == "admin":
        return set()
    rows = db.query(ProductAssignment.product_id, ProductAssignment.user_id).all()
    assigned_to_someone = {pid for pid, _ in rows}
    mine = {pid for pid, uid in rows if uid == user.id}
    return assigned_to_someone - mine


def can_survey(db: Session, user, product_id: int) -> bool:
    return int(product_id) not in hidden_product_ids(db, user)


def assignments_for_user(db: Session, user_id: int) -> list:
    return sorted(pid for (pid,) in db.query(ProductAssignment.product_id).filter(ProductAssignment.user_id == user_id).all())


def assignees_for_product(db: Session, product_id: int) -> list:
    return sorted(uid for (uid,) in db.query(ProductAssignment.user_id).filter(ProductAssignment.product_id == product_id).all())


def set_user_products(db: Session, user_id: int, product_ids) -> list:
    wanted = {int(p) for p in product_ids}
    current = set(assignments_for_user(db, user_id))
    for pid in current - wanted:
        db.query(ProductAssignment).filter_by(user_id=user_id, product_id=pid).delete(synchronize_session=False)
    for pid in wanted - current:
        db.add(ProductAssignment(user_id=user_id, product_id=pid))
    db.commit()
    return sorted(wanted)


def set_product_users(db: Session, product_id: int, user_ids) -> list:
    """Empty list = open to every executive again."""
    wanted = {int(u) for u in user_ids}
    current = set(assignees_for_product(db, product_id))
    for uid in current - wanted:
        db.query(ProductAssignment).filter_by(user_id=uid, product_id=product_id).delete(synchronize_session=False)
    for uid in wanted - current:
        db.add(ProductAssignment(user_id=uid, product_id=product_id))
    db.commit()
    return sorted(wanted)


def visible_count(db: Session, user, product_ids) -> Optional[int]:
    hidden = hidden_product_ids(db, user)
    return sum(1 for p in product_ids if p not in hidden)