"""
Survey form approval (requirements point 4): the Super Admin creates and edits the
questions; an Admin reviews and approves the form before survey persons can use it.
  approved  survey persons can survey the product (when its round is assigned to them)
  pending   changed by the Super Admin, waiting for an Admin
  returned  an Admin sent it back with a note; still not usable
Enforced in services/product_access.py (survey persons see approved forms only).
"""

from datetime import datetime

from sqlalchemy.orm import Session

from app.database.models import Product, User


def mark_pending(db: Session, product: Product, who: dict) -> None:
    was = getattr(product, "questions_status", None)
    product.questions_status = "pending"
    product.questions_note = None
    if was != "pending":
        _tell(db, "admin", "survey_form_pending", f"Survey form to approve: {product.name}",
              f"{who.get('name') or 'The Super Admin'} changed the survey questions of {product.name}. "
              "Approve the form so survey persons can use it.", product)


def review(db: Session, product: Product, approve: bool, reviewer: dict, note: str = "") -> None:
    product.questions_status = "approved" if approve else "returned"
    product.questions_note = (note or "").strip()[:2000] or None
    product.questions_reviewed_by = reviewer.get("name")
    product.questions_reviewed_at = datetime.utcnow()
    _tell(db, "client", "survey_form_reviewed",
          f"Survey form {'approved' if approve else 'returned'}: {product.name}",
          (f"{reviewer.get('name') or 'An admin'} approved the survey questions of {product.name}; survey persons can use them."
           if approve else f"{reviewer.get('name') or 'An admin'} returned the survey questions of {product.name}"
           + (f": {product.questions_note}" if product.questions_note else ".")), product)


def _tell(db: Session, role: str, kind: str, title: str, message: str, product: Product) -> None:
    try:
        from app.services.notifications import notify

        ids = [uid for (uid,) in db.query(User.id).filter(User.role == role, User.is_active == 1).all()]
        notify(db, ids, kind, title, message, data={"product_id": product.id, "product_name": product.name,
                                                    "status": product.questions_status, "note": product.questions_note})
    except Exception as e:  # a notification never blocks the change
        print(f"Survey form notification failed: {e}", flush=True)
