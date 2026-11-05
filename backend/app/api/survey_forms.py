"""
Survey form approval (admins).
GET  /admin/survey-forms?status=pending|returned|approved|all   forms and their questions
POST /admin/products/{product_id}/survey-form/approve   {note}
POST /admin/products/{product_id}/survey-form/return    {note}  (a note is required)
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.ownership import account
from app.database.database import get_db
from app.database.models import Category, Product, SurveyQuestion
from app.services import audit
from app.services.survey_forms import review

router = APIRouter(tags=["Survey forms"])


def _form(db: Session, p: Product, cats: dict) -> dict:
    qs = (db.query(SurveyQuestion).filter(SurveyQuestion.product_id == p.id, SurveyQuestion.is_active == 1)
          .order_by(SurveyQuestion.position).all())
    return {
        "product_id": p.id, "product_name": p.name, "category_name": cats.get(p.category_id),
        "status": p.questions_status or "approved", "note": p.questions_note,
        "reviewed_by": p.questions_reviewed_by,
        "reviewed_at": p.questions_reviewed_at.isoformat() + "Z" if p.questions_reviewed_at else None,
        "questions": [{"id": q.id, "position": q.position, "text": q.text, "question_type": q.question_type,
                       "options": q.options, "required": bool(q.required)} for q in qs],
    }


@router.get("/admin/survey-forms")
def list_forms(status: str = Query("pending"), db: Session = Depends(get_db)):
    q = db.query(Product).filter(Product.is_active == 1)
    if status != "all":
        if status not in ("pending", "returned", "approved"):
            raise HTTPException(status_code=400, detail="status is pending, returned, approved or all.")
        q = q.filter(Product.questions_status == status)
    cats = {c.id: c.name for c in db.query(Category).all()}
    pending = db.query(Product).filter(Product.is_active == 1, Product.questions_status == "pending").count()
    return {"counts": {"pending": pending}, "forms": [_form(db, p, cats) for p in q.order_by(Product.name).all()]}


class FormReviewIn(BaseModel):
    note: Optional[str] = Field(default=None, max_length=2000)


def _review(product_id: int, approve: bool, body: FormReviewIn, request: Request, db: Session):
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found.")
    if not approve and not (body.note or "").strip():
        raise HTTPException(status_code=400, detail="Add a note so the Super Admin knows what to change.")
    if not db.query(SurveyQuestion).filter(SurveyQuestion.product_id == product_id, SurveyQuestion.is_active == 1).count():
        raise HTTPException(status_code=400, detail="This product has no survey questions yet.")
    me = account(request)
    review(db, product, approve, me, body.note or "")
    db.commit()
    audit.note(request, summary=f"{'Approved' if approve else 'Returned'} the survey form of {product.name}"
               + (f": {product.questions_note}" if product.questions_note else ""))
    cats = {c.id: c.name for c in db.query(Category).all()}
    return _form(db, product, cats)


@router.post("/admin/products/{product_id}/survey-form/approve")
def approve_form(product_id: int, request: Request, body: FormReviewIn = FormReviewIn(), db: Session = Depends(get_db)):
    return _review(product_id, True, body, request, db)


@router.post("/admin/products/{product_id}/survey-form/return")
def return_form(product_id: int, body: FormReviewIn, request: Request, db: Session = Depends(get_db)):
    return _review(product_id, False, body, request, db)
