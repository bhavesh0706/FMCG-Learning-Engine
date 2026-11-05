"""
Custom reports from a prompt (services/custom_reports.py), for admins and the Super Admin.

GET  /reports/options          products (with rounds) and filter values this person can use
POST /reports/plan             {prompt, format: pptx, product_ids, round} -> {request_id, plan,
                               summary, warnings, planned_by, surveys}   (logged: every prompt)
GET  /reports/file/{id}        the file of a plan (its creator only; the app opens it as a link)
"""

from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.ownership import account
from app.database.database import get_db
from app.database.models import CustomReportRequest, Product, User
from app.services import audit
from app.services.custom_reports import (PlanError, build_dataset, build_pptx, build_xlsx, content_disposition, describe, filename,
                                         make_plan, options)

router = APIRouter(prefix="/reports", tags=["Custom reports"])

MEDIA_TYPES = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


def _user(request: Request, db: Session) -> User:
    me = account(request)
    if me.get("role") not in ("admin", "client"):
        raise HTTPException(status_code=403, detail="Only admins and the Super Admin can create reports.")
    user = db.get(User, me.get("id"))
    if user is None:
        raise HTTPException(status_code=401, detail="Please sign in again.")
    return user


@router.get("/options")
def report_options(request: Request, db: Session = Depends(get_db)):
    return options(db, _user(request, db))


class PlanIn(BaseModel):
    prompt: Optional[str] = Field(default="", max_length=2000)
    format: Literal["pptx"] = "pptx"  # prompts are for PowerPoint; Excel is the standard download
    product_ids: List[int] = Field(default_factory=list, max_length=200)
    round: Optional[int] = None


@router.post("/plan")
def report_plan(body: PlanIn, request: Request, db: Session = Depends(get_db)):
    user = _user(request, db)
    try:
        plan, warnings, planned_by = make_plan(db, user, body.prompt or "", body.format, body.product_ids, body.round)
    except PlanError as e:
        raise HTTPException(status_code=400, detail=str(e))
    blocks = build_dataset(db, plan)
    surveys = sum((b["stats"] or {}).get("response_count", 0) for b in blocks)
    if not surveys:
        warnings.append("No analyzed surveys match this request: the file would be empty. Try fewer filters or other rounds.")
    row = CustomReportRequest(user_id=user.id, user_name=user.name, user_role=user.role, format=body.format,
                              prompt=(body.prompt or "").strip() or None, plan=plan, planned_by=planned_by)
    db.add(row)
    db.commit()
    names = {p.id: p.name for p in db.query(Product).filter(Product.id.in_(plan["product_ids"])).all()}
    summary = describe(plan, names)
    audit.note(request, summary=f"Planned a custom {body.format.upper()} report: {(body.prompt or 'standard report')[:200]}",
               details={"request_id": row.id, "summary": summary, "planned_by": planned_by})
    return {"request_id": row.id, "plan": plan, "summary": summary, "warnings": warnings, "planned_by": planned_by,
            "surveys": surveys, "blocks": [{"label": b["label"], "surveys": (b["stats"] or {}).get("response_count", 0)} for b in blocks]}


@router.get("/file/{request_id}")
def report_file(request_id: int, request: Request, db: Session = Depends(get_db)):
    user = _user(request, db)
    row = db.get(CustomReportRequest, request_id)
    if row is None or row.user_id != user.id or not row.plan:
        raise HTTPException(status_code=404, detail="Report not found. Prepare it again.")
    plan = row.plan
    blocks = build_dataset(db, plan)
    data = build_pptx(db, plan, blocks) if plan["format"] == "pptx" else build_xlsx(db, plan, blocks)
    name = filename(plan)
    return Response(content=data, media_type=MEDIA_TYPES[plan["format"]],
                    headers={"Content-Disposition": content_disposition(name)})