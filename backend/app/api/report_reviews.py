"""
Report review chain for each survey's analysis (requirements point 9) and the
Super Admin's view of every survey.

Statuses (survey_responses.report_status), every change kept in report_review_events:
  generated      the analysis is ready (set when the analysis finishes)
  pending_admin  the survey person submitted it for review
  returned       the admin sent it back with a note (the survey person can submit again)
  forwarded      approved by the admin and forwarded to the Super Admin
  reviewed       the Super Admin reviewed it

POST /surveys/responses/{id}/report/submit        survey person (their own survey)
GET  /surveys/responses/{id}/report/history       its owner, admins, the Super Admin
GET  /admin/report-reviews?status=...             admins
POST /admin/report-reviews/{id}/approve  {note}   admins: forward to the Super Admin
POST /admin/report-reviews/{id}/return   {note}   admins: back to the survey person
GET  /org/surveys?status=&report_status=&product_id=&q=   Super Admin: every survey
GET  /org/surveys/{id}                            Super Admin: one survey, its report and history
POST /org/surveys/{id}/review            {note}   Super Admin: mark a forwarded report reviewed
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.ownership import account
from app.database.database import get_db
from app.database.models import (Category, FeedbackRecord, Product, ReportReviewEvent, Respondent, SurveyAnswer,
                                 SurveyResponse, User)
from app.services import audit

router = APIRouter(tags=["Report reviews"])

LABELS = {
    "generated": "Generated",
    "pending_admin": "Pending Admin review",
    "returned": "Returned to survey person",
    "forwarded": "Approved by Admin · forwarded to Super Admin",
    "reviewed": "Reviewed by Super Admin",
}


class NoteIn(BaseModel):
    note: Optional[str] = Field(default=None, max_length=2000)


def set_status(db: Session, response: SurveyResponse, to: str, me: dict, note: Optional[str] = None) -> None:
    db.add(ReportReviewEvent(response_id=response.id, from_status=response.report_status, to_status=to,
                             by_user_id=me.get("id"), by_name=me.get("name"), by_role=me.get("role"),
                             note=(note or "").strip()[:2000] or None))
    response.report_status = to
    response.report_status_at = datetime.utcnow()


def _notify(db: Session, user_ids, title: str, message: str, response: SurveyResponse) -> None:
    try:
        from app.services.notifications import notify

        notify(db, user_ids, "report_review", title, message,
               data={"response_id": response.id, "report_status": response.report_status, "label": LABELS.get(response.report_status)})
    except Exception as e:
        print(f"Report review notification failed: {e}", flush=True)


def _role_ids(db: Session, role: str) -> list:
    return [uid for (uid,) in db.query(User.id).filter(User.role == role, User.is_active == 1).all()]


def _history(db: Session, response_id: int) -> list:
    return [{"at": e.created_at.isoformat() + "Z", "from": e.from_status, "to": e.to_status, "label": LABELS.get(e.to_status, e.to_status),
             "by": e.by_name, "role": e.by_role, "note": e.note}
            for e in db.query(ReportReviewEvent).filter(ReportReviewEvent.response_id == response_id).order_by(ReportReviewEvent.id)]


def _row(db: Session, r: SurveyResponse, products: dict, cats: dict, people: dict, records: dict) -> dict:
    p = products.get(r.product_id)
    person = people.get(r.respondent_id)
    rec = records.get(r.id)
    return {
        "response_id": r.id, "status": r.status, "report_status": r.report_status,
        "report_label": LABELS.get(r.report_status or "", "Not generated yet"),
        "report_status_at": r.report_status_at.isoformat() + "Z" if r.report_status_at else None,
        "product_id": r.product_id, "product_name": p.name if p else None, "category_name": cats.get(p.category_id) if p else None,
        "recipe_version": r.recipe_version, "executive_name": r.executive_name,
        "created_at": r.created_at.isoformat() + "Z" if r.created_at else None,
        "customer": person.name if person else None,
        "city": person.location if person else None, "country": person.country if person else None,
        "respondent_type": person.respondent_type if person else None, "occupation": person.occupation if person else None,
        "sec_class": person.sec_class if person else None, "audience": r.audience,
        "feedback_id": rec.id if rec else None,
        "rating": rec.overall_rating if rec else None, "sentiment": rec.overall_sentiment if rec else None,
        "summary": (rec.overall_summary or "")[:400] if rec else None,
    }


def _rows(db: Session, responses: list) -> list:
    pids = {r.product_id for r in responses}
    products = {p.id: p for p in db.query(Product).filter(Product.id.in_(pids)).all()} if pids else {}
    cats = {c.id: c.name for c in db.query(Category).all()}
    people = {p.id: p for p in db.query(Respondent).filter(Respondent.id.in_({r.respondent_id for r in responses})).all()} if responses else {}
    records = {}
    if responses:
        for rec in db.query(FeedbackRecord).filter(FeedbackRecord.response_id.in_([r.id for r in responses])).order_by(FeedbackRecord.id):
            records[rec.response_id] = rec  # latest wins
    return [_row(db, r, products, cats, people, records) for r in responses]


def _response_or_404(db: Session, response_id: int) -> SurveyResponse:
    r = db.get(SurveyResponse, response_id)
    if r is None:
        raise HTTPException(status_code=404, detail="Survey not found.")
    return r


# ---------- survey person ----------

@router.post("/surveys/responses/{response_id}/report/submit")
def submit_report(response_id: int, request: Request, body: NoteIn = NoteIn(), db: Session = Depends(get_db)):
    me = account(request)
    r = _response_or_404(db, response_id)
    if me.get("role") != "admin" and r.user_id != me.get("id"):
        raise HTTPException(status_code=403, detail="You can submit only your own surveys.")
    if r.status != "analyzed":
        raise HTTPException(status_code=400, detail="The report isn't ready: the survey must be analyzed first.")
    if (r.report_status or "generated") not in ("generated", "returned"):
        raise HTTPException(status_code=409, detail=f"Already {LABELS.get(r.report_status, r.report_status).lower()}.")
    set_status(db, r, "pending_admin", me, body.note)
    db.commit()
    _notify(db, _role_ids(db, "admin"), f"Report to review: survey #{r.id}",
            f"{me.get('name') or 'A survey person'} submitted the report of survey #{r.id} for your review.", r)
    return {"response_id": r.id, "report_status": r.report_status, "label": LABELS[r.report_status], "history": _history(db, r.id)}


@router.get("/surveys/responses/{response_id}/report/history")
def report_history(response_id: int, request: Request, db: Session = Depends(get_db)):
    me = account(request)
    r = _response_or_404(db, response_id)
    if me.get("role") not in ("admin", "client") and r.user_id != me.get("id"):
        raise HTTPException(status_code=403, detail="Not your survey.")
    return {"response_id": r.id, "report_status": r.report_status, "label": LABELS.get(r.report_status or "", "Not generated yet"),
            "history": _history(db, r.id)}


# ---------- admins ----------

@router.get("/admin/report-reviews")
def admin_reviews(status: str = Query("pending_admin"), db: Session = Depends(get_db)):
    q = db.query(SurveyResponse).filter(SurveyResponse.report_status.isnot(None))
    if status != "all":
        if status not in LABELS:
            raise HTTPException(status_code=400, detail="Unknown status.")
        q = q.filter(SurveyResponse.report_status == status)
    rows = q.order_by(SurveyResponse.report_status_at.desc()).limit(500).all()
    pending = db.query(SurveyResponse).filter(SurveyResponse.report_status == "pending_admin").count()
    return {"counts": {"pending_admin": pending}, "labels": LABELS, "reports": _rows(db, rows)}


def _admin_decide(response_id: int, approve: bool, body: NoteIn, request: Request, db: Session):
    me = account(request)
    r = _response_or_404(db, response_id)
    if r.report_status != "pending_admin":
        raise HTTPException(status_code=409, detail="Only reports pending admin review can be approved or returned.")
    if not approve and not (body.note or "").strip():
        raise HTTPException(status_code=400, detail="Add a note so the survey person knows what to fix.")
    set_status(db, r, "forwarded" if approve else "returned", me, body.note)
    db.commit()
    audit.note(request, summary=f"{'Approved and forwarded' if approve else 'Returned'} the report of survey #{r.id}")
    if approve:
        _notify(db, _role_ids(db, "client"), f"Report to review: survey #{r.id}",
                f"{me.get('name') or 'An admin'} approved the report of survey #{r.id} and forwarded it to you.", r)
        if r.user_id:
            _notify(db, [r.user_id], f"Report approved: survey #{r.id}", f"{me.get('name') or 'Your admin'} approved your report.", r)
    elif r.user_id:
        _notify(db, [r.user_id], f"Report returned: survey #{r.id}", f"{me.get('name') or 'Your admin'} returned your report: {body.note}", r)
    return {"response_id": r.id, "report_status": r.report_status, "label": LABELS[r.report_status], "history": _history(db, r.id)}


@router.post("/admin/report-reviews/{response_id}/approve")
def admin_approve(response_id: int, request: Request, body: NoteIn = NoteIn(), db: Session = Depends(get_db)):
    return _admin_decide(response_id, True, body, request, db)


@router.post("/admin/report-reviews/{response_id}/return")
def admin_return(response_id: int, body: NoteIn, request: Request, db: Session = Depends(get_db)):
    return _admin_decide(response_id, False, body, request, db)


# ---------- Super Admin: every survey ----------

def _client(request: Request) -> dict:
    me = account(request)
    if me.get("role") != "client":
        raise HTTPException(status_code=403, detail="Only the Super Admin can open this.")
    return me


@router.get("/org/surveys")
def org_surveys(request: Request, status: Optional[str] = None, report_status: Optional[str] = None,
                product_id: Optional[int] = None, q: Optional[str] = None, limit: int = Query(300, ge=1, le=1000),
                db: Session = Depends(get_db)):
    _client(request)
    query = db.query(SurveyResponse).filter(SurveyResponse.status != "draft")
    if status:
        query = query.filter(SurveyResponse.status == status)
    if report_status:
        query = query.filter(SurveyResponse.report_status == report_status)
    if product_id:
        query = query.filter(SurveyResponse.product_id == product_id)
    rows = _rows(db, query.order_by(SurveyResponse.created_at.desc()).limit(limit).all())
    if q:
        needle = q.strip().lower()
        rows = [r for r in rows if needle in " ".join(str(r.get(k) or "") for k in ("product_name", "executive_name", "customer", "city", "country")).lower()]
    counts = {"forwarded": db.query(SurveyResponse).filter(SurveyResponse.report_status == "forwarded").count(),
              "total": db.query(SurveyResponse).filter(SurveyResponse.status != "draft").count()}
    return {"counts": counts, "labels": LABELS, "surveys": rows}


@router.get("/org/surveys/{response_id}")
def org_survey(response_id: int, request: Request, db: Session = Depends(get_db)):
    _client(request)
    r = _response_or_404(db, response_id)
    out = _rows(db, [r])[0]
    rec = db.query(FeedbackRecord).filter(FeedbackRecord.response_id == r.id).order_by(FeedbackRecord.id.desc()).first()
    out["answers"] = [{"question": a.question_text, "type": a.question_type, "value": a.value}
                      for a in db.query(SurveyAnswer).filter(SurveyAnswer.response_id == r.id).order_by(SurveyAnswer.id)]
    out["analysis"] = {
        "summary": rec.overall_summary, "rating": rec.overall_rating, "sentiment": rec.overall_sentiment,
        "transcript": rec.transcript, "chapters": rec.chapters, "duration_seconds": rec.duration_seconds,
    } if rec else None
    out["history"] = _history(db, r.id)
    return out


@router.post("/org/surveys/{response_id}/review")
def org_review(response_id: int, request: Request, body: NoteIn = NoteIn(), db: Session = Depends(get_db)):
    me = _client(request)
    r = _response_or_404(db, response_id)
    if r.report_status != "forwarded":
        raise HTTPException(status_code=409, detail="Only reports forwarded by an admin can be marked reviewed.")
    set_status(db, r, "reviewed", me, body.note)
    db.commit()
    audit.note(request, summary=f"Reviewed the report of survey #{r.id}")
    _notify(db, _role_ids(db, "admin"), f"Report reviewed: survey #{r.id}", f"{me.get('name') or 'The Super Admin'} reviewed the report.", r)
    return {"response_id": r.id, "report_status": r.report_status, "label": LABELS[r.report_status], "history": _history(db, r.id)}
