"""
Product checks review (admins): surveys whose feedback seems to be about another
product (services/product_check.py).

GET  /admin/product-checks?status=pending|unverified|reviewed|all
GET  /admin/product-checks/{response_id}          transcript, summary, evidence, history
POST /admin/product-checks/{response_id}/review   {action: confirm|correct|invalid,
                                                   product_id (correct), remarks}
Mismatched surveys stay out of every report until reviewed; a correction moves the
survey to the right product (its current round). Every decision is audited.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.ownership import account
from app.database.database import get_db
from app.database.models import AuditLog, FeedbackRecord, Product, ProductCheck, Respondent, SurveyResponse, User
from app.services import audit
from app.services.product_check import review

router = APIRouter(prefix="/admin/product-checks", tags=["Product checks"])

LABELS = {"match": "Match", "mismatch": "Mismatch detected", "uncertain": "Unverified"}


def _names(db: Session, ids) -> dict:
    ids = {i for i in ids if i}
    return {p.id: p.name for p in db.query(Product).filter(Product.id.in_(ids)).all()} if ids else {}


def _out(c: ProductCheck, r: Optional[SurveyResponse], names: dict, customer: str = "") -> dict:
    return {
        "response_id": c.response_id,
        "feedback_id": c.feedback_id,
        "status": c.status,
        "label": LABELS.get(c.status, c.status),
        "confidence": c.confidence,
        "evidence": c.evidence or {},
        "checked_at": c.checked_at.isoformat() + "Z" if c.checked_at else None,
        "review_status": c.review_status,
        "reviewed_by": c.reviewed_by_name,
        "reviewed_at": c.reviewed_at.isoformat() + "Z" if c.reviewed_at else None,
        "remarks": c.remarks,
        "selected_product": {"id": c.selected_product_id, "name": names.get(c.selected_product_id)},
        "detected_product": {"id": c.detected_product_id, "name": names.get(c.detected_product_id)} if c.detected_product_id else None,
        "corrected_product": {"id": c.corrected_product_id, "name": names.get(c.corrected_product_id)} if c.corrected_product_id else None,
        "survey": {
            "status": r.status if r else None,
            "executive_name": r.executive_name if r else None,
            "customer": customer,
            "created_at": r.created_at.isoformat() + "Z" if r and r.created_at else None,
        },
    }


@router.get("")
def list_checks(status: str = Query("pending"), db: Session = Depends(get_db)):
    q = db.query(ProductCheck)
    if status == "pending":
        q = q.filter(ProductCheck.review_status == "pending")
    elif status == "unverified":
        q = q.filter(ProductCheck.review_status == "optional")
    elif status == "reviewed":
        q = q.filter(ProductCheck.review_status.in_(("confirmed", "corrected", "invalid")))
    elif status != "all":
        raise HTTPException(status_code=400, detail="status is pending, unverified, reviewed or all.")
    checks = q.order_by(ProductCheck.checked_at.desc()).limit(500).all()
    responses = {r.id: r for r in db.query(SurveyResponse).filter(SurveyResponse.id.in_([c.response_id for c in checks])).all()} if checks else {}
    people = {p.id: p.name for p in db.query(Respondent).filter(Respondent.id.in_({r.respondent_id for r in responses.values()})).all()} if responses else {}
    names = _names(db, [i for c in checks for i in (c.selected_product_id, c.detected_product_id, c.corrected_product_id)])
    counts = {
        "pending": db.query(ProductCheck).filter(ProductCheck.review_status == "pending").count(),
        "unverified": db.query(ProductCheck).filter(ProductCheck.review_status == "optional").count(),
    }
    return {"counts": counts, "checks": [_out(c, responses.get(c.response_id), names,
                                               people.get(getattr(responses.get(c.response_id), "respondent_id", None), "")) for c in checks]}


def _check_or_404(db: Session, response_id: int):
    c = db.query(ProductCheck).filter(ProductCheck.response_id == response_id).first()
    r = db.get(SurveyResponse, response_id)
    if c is None or r is None:
        raise HTTPException(status_code=404, detail="No product check for this survey.")
    return c, r


@router.get("/{response_id}")
def check_detail(response_id: int, db: Session = Depends(get_db)):
    c, r = _check_or_404(db, response_id)
    record = db.get(FeedbackRecord, c.feedback_id) if c.feedback_id else None
    person = db.get(Respondent, r.respondent_id)
    out = _out(c, r, _names(db, [c.selected_product_id, c.detected_product_id, c.corrected_product_id]), person.name if person else "")
    out["transcript"] = record.transcript if record else None
    out["summary"] = record.overall_summary if record else None
    out["history"] = [
        {"at": a.created_at.isoformat() + "Z", "by": a.user_name, "action": a.action, "summary": a.summary}
        for a in db.query(AuditLog).filter(AuditLog.target_type == "survey", AuditLog.target_id == response_id,
                                           AuditLog.action.like("product_check.%")).order_by(AuditLog.created_at).all()
    ]
    return out


class ReviewIn(BaseModel):
    action: str = Field(pattern="^(confirm|correct|invalid)$")
    product_id: Optional[int] = None
    remarks: Optional[str] = Field(default=None, max_length=2000)


@router.post("/{response_id}/review")
def review_check(response_id: int, body: ReviewIn, request: Request, db: Session = Depends(get_db)):
    c, r = _check_or_404(db, response_id)
    me = account(request)
    reviewer = db.get(User, me.get("id")) if me.get("id") else None
    before = c.selected_product_id if c.review_status != "corrected" else c.corrected_product_id
    try:
        touched = review(db, r, c, body.action, reviewer, body.remarks or "", body.product_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    db.commit()
    names = _names(db, [before, c.corrected_product_id])
    summary = {
        "confirm": f"Confirmed survey #{response_id} belongs to {names.get(before, 'the selected product')}",
        "correct": f"Moved survey #{response_id} from {names.get(before)} to {names.get(c.corrected_product_id)}",
        "invalid": f"Marked survey #{response_id} invalid (kept out of reports; recording kept)",
    }[body.action]
    audit.record_from_request(request, action=f"product_check.{body.action}", account=me, target_type="survey", target_id=response_id,
                              summary=summary + (f". Note: {c.remarks}" if c.remarks else ""),
                              details={"selected_product_id": c.selected_product_id, "detected_product_id": c.detected_product_id,
                                       "corrected_product_id": c.corrected_product_id})
    for pid in touched:  # reports follow the decision
        try:
            from app.api.product_feedback import build_product_report

            build_product_report(db, pid)
        except Exception as e:
            print(f"Report rebuild after product check failed for product {pid}: {e}", flush=True)
    return check_detail(response_id, db)