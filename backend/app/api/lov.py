"""
LOV (list of values): m_othermastername + m_othermasterdata (services/lov.py).

GET   /lov                          every active list with its active values (everyone signed in)
                                    ?all=true also returns hidden values (admin screen)
POST  /admin/lov/{master_code}      {code?, label, parent_id?}           add a value (open lists only;
                                    parent_id = the state of a new city, the country of a new state)
PATCH /admin/lov/values/{id}        {label?, sort_order?, is_active?}    change a value (admins)

System lists (is_system = 1): the program uses their codes, so only the label and
the order can change. Values are never deleted: saved data keeps its code.
"""

import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.database.models import OtherMasterData, OtherMasterName
from app.services import audit

router = APIRouter(tags=["LOV"])


def _value(v: OtherMasterData) -> dict:
    return {"id": v.id, "code": v.data_code, "label": v.data_value, "sort_order": v.sort_order,
            "parent_id": v.parent_id, "extra": v.extra or {}, "is_active": bool(v.is_active)}


@router.get("/lov")
def lov(all: bool = False, db: Session = Depends(get_db)):
    masters = db.query(OtherMasterName).filter(OtherMasterName.is_active == 1).all()
    q = db.query(OtherMasterData).filter(OtherMasterData.master_id.in_([m.id for m in masters]))
    if not all:
        q = q.filter(OtherMasterData.is_active == 1)
    values: dict = {}
    for v in q.order_by(OtherMasterData.sort_order, OtherMasterData.data_value, OtherMasterData.id).all():
        values.setdefault(v.master_id, []).append(_value(v))
    codes = {m.id: m.master_code for m in masters}
    return {
        m.master_code: {"id": m.id, "name": m.master_name, "description": m.description,
                        "is_system": bool(m.is_system), "parent": codes.get(m.parent_master_id),
                        "values": values.get(m.id, [])}
        for m in sorted(masters, key=lambda m: m.master_name.lower())
    }


class LovIn(BaseModel):
    label: str = Field(min_length=1, max_length=255)
    code: Optional[str] = Field(default=None, max_length=80)  # default: the label itself
    parent_id: Optional[int] = None  # required for a list inside another list (STATE, CITY)


class LovPatch(BaseModel):
    label: Optional[str] = Field(default=None, min_length=1, max_length=255)
    sort_order: Optional[int] = Field(default=None, ge=0, le=10000)
    is_active: Optional[bool] = None


def _clean(text: str) -> str:
    return " ".join(text.split())


@router.post("/admin/lov/{master_code}", status_code=201)
def add_value(master_code: str, body: LovIn, request: Request, db: Session = Depends(get_db)):
    master = db.query(OtherMasterName).filter(OtherMasterName.master_code == master_code.upper()).first()
    if master is None:
        raise HTTPException(status_code=404, detail="Unknown list.")
    if master.is_system:
        raise HTTPException(status_code=409, detail=f"{master.master_name} is used by the program: values can be renamed, not added.")
    label = _clean(body.label)
    parent = None
    if master.parent_master_id:
        parent = db.get(OtherMasterData, body.parent_id) if body.parent_id else None
        if parent is None or parent.master_id != master.parent_master_id:
            owner = db.get(OtherMasterName, master.parent_master_id)
            raise HTTPException(status_code=422, detail=f"Choose the {owner.master_name.lower()} this {master.master_name.lower()} belongs to.")
    code = _clean(body.code) if body.code and body.code.strip() else (f"{parent.data_code}/{label}" if parent else label)
    if not re.fullmatch(r"[\w .\-/&()]+", code):
        raise HTTPException(status_code=422, detail="The code may contain letters, numbers, spaces and . - / & ( ) only.")
    rows = db.query(OtherMasterData).filter(OtherMasterData.master_id == master.id).all()
    # A label may repeat under different parents (two states can have a city with the same name).
    if any(r.data_code.lower() == code.lower()
           or (r.data_value.lower() == label.lower() and r.parent_id == (parent.id if parent else None)) for r in rows):
        where = f" for {parent.data_value}" if parent else ""
        raise HTTPException(status_code=409, detail=f"“{label}” is already in the list{where}.")
    siblings = [r for r in rows if r.parent_id == (parent.id if parent else None)]
    v = OtherMasterData(master_id=master.id, data_code=code, data_value=label, parent_id=parent.id if parent else None,
                        sort_order=max((r.sort_order for r in siblings), default=-1) + 1)
    db.add(v)
    db.commit()
    audit.note(request, summary=f"Added “{label}” to {master.master_name}" + (f" ({parent.data_value})" if parent else ""))
    return _value(v)


@router.patch("/admin/lov/values/{value_id}")
def change_value(value_id: int, body: LovPatch, request: Request, db: Session = Depends(get_db)):
    v = db.get(OtherMasterData, value_id)
    master = db.get(OtherMasterName, v.master_id) if v else None
    if v is None or master is None:
        raise HTTPException(status_code=404, detail="Value not found.")
    if body.is_active is not None and master.is_system and not body.is_active:
        raise HTTPException(status_code=409, detail=f"{master.master_name} is used by the program: values can't be hidden.")
    if body.label is not None:
        v.data_value = _clean(body.label)
    if body.sort_order is not None:
        v.sort_order = body.sort_order
    if body.is_active is not None:
        v.is_active = 1 if body.is_active else 0
    db.commit()
    audit.note(request, summary=f"Changed “{v.data_value}” in {master.master_name}")
    return _value(v)
