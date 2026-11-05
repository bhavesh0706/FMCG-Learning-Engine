"""
Master lists for the respondent form (requirements points 4 and 5).
GET    /masters                       {occupation: [...], sec_class: [...], respondent_type: [...]} (everyone signed in)
POST   /admin/masters/{kind}          {name}                     add a value (admins)
PATCH  /admin/masters/{kind}/{id}     {name?, is_active?}        rename / switch off (admins)
Values are never deleted: surveys that used them keep their text.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.database.models import MasterValue
from app.services import audit
from app.services.masters import KINDS

router = APIRouter(tags=["Masters"])


def _out(v: MasterValue) -> dict:
    return {"id": v.id, "name": v.name, "is_active": bool(v.is_active), "position": v.position}


@router.get("/masters")
def masters(all: bool = False, db: Session = Depends(get_db)):
    q = db.query(MasterValue)
    if not all:
        q = q.filter(MasterValue.is_active == 1)
    rows = q.order_by(MasterValue.kind, MasterValue.position, MasterValue.name).all()
    out = {k: [] for k in KINDS}
    for v in rows:
        out.setdefault(v.kind, []).append(_out(v))
    return {**out, "labels": KINDS}


class MasterIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class MasterPatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=80)
    is_active: Optional[bool] = None


def _kind(kind: str) -> str:
    if kind not in KINDS:
        raise HTTPException(status_code=404, detail="Unknown list.")
    return kind


@router.post("/admin/masters/{kind}", status_code=201)
def add_value(kind: str, body: MasterIn, request: Request, db: Session = Depends(get_db)):
    kind = _kind(kind)
    name = " ".join(body.name.split())
    if db.query(MasterValue).filter(MasterValue.kind == kind, MasterValue.name.ilike(name)).first():
        raise HTTPException(status_code=409, detail=f"“{name}” is already in the list.")
    last = db.query(MasterValue).filter(MasterValue.kind == kind).order_by(MasterValue.position.desc()).first()
    v = MasterValue(kind=kind, name=name, position=(last.position + 1) if last else 0)
    db.add(v)
    db.commit()
    audit.note(request, summary=f"Added “{name}” to {KINDS[kind]}")
    return _out(v)


@router.patch("/admin/masters/{kind}/{value_id}")
def change_value(kind: str, value_id: int, body: MasterPatch, request: Request, db: Session = Depends(get_db)):
    kind = _kind(kind)
    v = db.get(MasterValue, value_id)
    if v is None or v.kind != kind:
        raise HTTPException(status_code=404, detail="Value not found.")
    if body.name is not None:
        v.name = " ".join(body.name.split())
    if body.is_active is not None:
        v.is_active = 1 if body.is_active else 0
    db.commit()
    audit.note(request, summary=f"Changed “{v.name}” in {KINDS[kind]}")
    return _out(v)
