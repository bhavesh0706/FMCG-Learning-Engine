"""
Other Master Data (admins): the master tables (m_othermastername) and their values
(m_othermasterdata), the same tables the app's lists come from (GET /lov).

GET    /admin/other-masters                      every master table with its counts and permissions
POST   /admin/other-masters                      {name, description, is_active, can_edit, allow_add}
PATCH  /admin/other-masters/{id}                 change name, description or a permission
DELETE /admin/other-masters/{id}                 only an empty table that nothing depends on
GET    /admin/other-masters/{id}/values          its values (also inactive ones)
POST   /admin/other-masters/{id}/values          {code, description?, is_active?, parent_id?}   needs Active + Add Value
PATCH  /admin/other-masters/values/{value_id}    {description?, is_active?, sort_order?}        needs Active + Can Edit

Active    the table can be used (an inactive table disappears from GET /lov and takes no new values).
Can Edit  its existing values may be changed.
Add Value new values may be added.
System tables (is_system) hold codes the program relies on: they stay active, take no new
values and their codes never change. Values are never deleted (saved data keeps the code):
they are deactivated instead.
"""

import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.database.models import OtherMasterData, OtherMasterName, User
from app.services import audit
from app.services import units as unit_table

router = APIRouter(prefix="/admin/other-masters", tags=["Other Master Data"])

_CODE_OK = re.compile(r"[\w .\-/&()µ]+")


def _clean(text: Optional[str]) -> str:
    return " ".join((text or "").split())


def _names(db: Session, ids) -> dict:
    ids = {i for i in ids if i}
    return {u.id: u.name for u in db.query(User).filter(User.id.in_(ids))} if ids else {}


def _iso(dt) -> Optional[str]:
    return dt.isoformat(timespec="seconds") + "Z" if dt else None


def _table_out(m: OtherMasterName, counts: dict, parents: dict, names: dict) -> dict:
    total, active = counts.get(m.id, (0, 0))
    parent = parents.get(m.parent_master_id)
    return {
        "id": m.id, "code": m.master_code, "name": m.master_name, "description": m.description,
        "is_active": bool(m.is_active), "can_edit": bool(m.can_edit), "allow_add": bool(m.allow_add),
        "is_system": bool(m.is_system),
        "built_in": bool(m.is_system) or m.master_code in built_in_codes(),
        "parent": {"id": parent.id, "name": parent.master_name} if parent else None,
        "values": total, "active_values": active,
        "created_at": _iso(m.created_at), "created_by": names.get(m.created_by),
        "mod_at": _iso(m.mod_at), "mod_by": names.get(m.mod_by),
    }


def _counts(db: Session) -> dict:
    rows = (db.query(OtherMasterData.master_id, func.count(OtherMasterData.id),
                     func.sum(OtherMasterData.is_active)).group_by(OtherMasterData.master_id).all())
    return {mid: (int(total), int(active or 0)) for mid, total, active in rows}


def _table_or_404(db: Session, table_id: int) -> OtherMasterName:
    m = db.get(OtherMasterName, table_id)
    if m is None:
        raise HTTPException(status_code=404, detail="Master table not found.")
    return m


def _one(db: Session, m: OtherMasterName) -> dict:
    parents = {m.parent_master_id: db.get(OtherMasterName, m.parent_master_id)} if m.parent_master_id else {}
    return _table_out(m, _counts(db), parents, _names(db, [m.created_by, m.mod_by]))


@router.get("")
def list_tables(db: Session = Depends(get_db)):
    tables = db.query(OtherMasterName).order_by(func.lower(OtherMasterName.master_name)).all()
    by_id = {m.id: m for m in tables}
    counts = _counts(db)
    names = _names(db, [x for m in tables for x in (m.created_by, m.mod_by)])
    return {"tables": [_table_out(m, counts, by_id, names) for m in tables]}


class TableIn(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    description: str = Field(min_length=1, max_length=2000)
    is_active: bool = True
    can_edit: bool = True
    allow_add: bool = True


class TablePatch(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=150)
    description: Optional[str] = Field(default=None, min_length=1, max_length=2000)
    is_active: Optional[bool] = None
    can_edit: Optional[bool] = None
    allow_add: Optional[bool] = None


def _new_code(db: Session, name: str) -> str:
    """The table's code from its name ("Packaging type" -> PACKAGING_TYPE), unique."""
    base = re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")[:44] or "MASTER"
    taken = {c for (c,) in db.query(OtherMasterName.master_code).filter(OtherMasterName.master_code.like(f"{base}%"))}
    code, n = base, 2
    while code in taken:
        code, n = f"{base}_{n}", n + 1
    return code


@router.post("", status_code=201)
def create_table(body: TableIn, request: Request, db: Session = Depends(get_db)):
    name = _clean(body.name)
    if db.query(OtherMasterName).filter(func.lower(OtherMasterName.master_name) == name.lower()).first():
        raise HTTPException(status_code=409, detail=f"A master table named “{name}” already exists.")
    m = OtherMasterName(master_code=_new_code(db, name), master_name=name, description=_clean(body.description),
                        is_system=0, is_active=int(body.is_active), can_edit=int(body.can_edit),
                        allow_add=int(body.allow_add))
    db.add(m)
    db.commit()
    audit.note(request, target_id=m.id, summary=f"Created the master table “{name}” ({m.master_code})")
    return _one(db, m)


@router.patch("/{table_id}")
def change_table(table_id: int, body: TablePatch, request: Request, db: Session = Depends(get_db)):
    m = _table_or_404(db, table_id)
    if m.is_system and body.is_active is False:
        raise HTTPException(status_code=409, detail=f"{m.master_name} is used by the program: it stays active.")
    if m.is_system and body.allow_add:
        raise HTTPException(status_code=409, detail=f"{m.master_name} is used by the program: its values are fixed, new ones can't be added.")
    if body.name is not None:
        name = _clean(body.name)
        clash = (db.query(OtherMasterName).filter(func.lower(OtherMasterName.master_name) == name.lower(),
                                                   OtherMasterName.id != m.id).first())
        if clash:
            raise HTTPException(status_code=409, detail=f"A master table named “{name}” already exists.")
        m.master_name = name
    if body.description is not None:
        m.description = _clean(body.description)
    for field in ("is_active", "can_edit", "allow_add"):
        value = getattr(body, field)
        if value is not None:
            setattr(m, field, int(value))
    db.commit()
    unit_table.invalidate()  # e.g. the Measurement unit table made inactive
    audit.note(request, target_id=m.id, summary=f"Changed the master table “{m.master_name}”")
    return _one(db, m)


def built_in_codes() -> set:
    """Tables the app itself creates and reads (services/lov.py): never deleted."""
    from app.services.lov import DEFAULTS, GEO_LISTS

    return {c for c, *_ in DEFAULTS} | {c for c, *_ in GEO_LISTS} | {"UNIT_CATEGORY", "MEASUREMENT_UNIT"}


def delete_problem(db: Session, m: OtherMasterName) -> Optional[str]:
    if m.is_system or m.master_code in built_in_codes():
        return f"{m.master_name} is built into the app and can't be deleted. Make it inactive instead."
    child = db.query(OtherMasterName).filter(OtherMasterName.parent_master_id == m.id).first()
    if child:
        return f"“{child.master_name}” belongs to this table: delete it first."
    return None


@router.delete("/{table_id}")
def delete_table(table_id: int, request: Request, db: Session = Depends(get_db)):
    """Deletes a table created in Other Master together with its values."""
    m = _table_or_404(db, table_id)
    problem = delete_problem(db, m)
    if problem:
        raise HTTPException(status_code=409, detail=problem)
    name = m.master_name
    n = db.query(OtherMasterData).filter(OtherMasterData.master_id == m.id).delete(synchronize_session=False)
    db.delete(m)
    db.commit()
    unit_table.invalidate()
    audit.note(request, target_id=table_id, summary=f"Deleted the master table “{name}” and its {n} value(s)")
    return {"deleted": table_id, "values_deleted": n}


# ---------------------------------------------------------------- values

def _value_out(v: OtherMasterData, parents: dict, names: dict) -> dict:
    parent = parents.get(v.parent_id)
    return {
        "id": v.id, "code": v.data_code, "description": v.data_value, "is_active": bool(v.is_active),
        "sort_order": v.sort_order, "extra": v.extra or {},
        "parent": {"id": parent.id, "code": parent.data_code, "description": parent.data_value} if parent else None,
        "created_at": _iso(v.created_at), "created_by": names.get(v.created_by),
        "mod_at": _iso(v.mod_at), "mod_by": names.get(v.mod_by),
    }


@router.get("/{table_id}/values")
def list_values(table_id: int, db: Session = Depends(get_db)):
    m = _table_or_404(db, table_id)
    rows = (db.query(OtherMasterData).filter(OtherMasterData.master_id == m.id)
            .order_by(OtherMasterData.parent_id, OtherMasterData.sort_order, OtherMasterData.id).all())
    parent_ids = {r.parent_id for r in rows if r.parent_id}
    parents = {p.id: p for p in db.query(OtherMasterData).filter(OtherMasterData.id.in_(parent_ids))} if parent_ids else {}
    names = _names(db, [x for r in rows for x in (r.created_by, r.mod_by)])
    options = []
    if m.parent_master_id:  # the values a new value may belong to (e.g. the unit's category)
        options = [{"id": p.id, "code": p.data_code, "description": p.data_value, "extra": p.extra or {}}
                   for p in db.query(OtherMasterData).filter(OtherMasterData.master_id == m.parent_master_id,
                                                             OtherMasterData.is_active == 1)
                   .order_by(OtherMasterData.sort_order, OtherMasterData.data_value)]
    return {"table": _one(db, m), "values": [_value_out(r, parents, names) for r in rows], "parent_options": options}


class ValueIn(BaseModel):
    code: str = Field(min_length=1, max_length=80)
    description: Optional[str] = Field(default=None, max_length=255)
    is_active: bool = True
    parent_id: Optional[int] = None
    # Units of measure: how many of the category's base unit one of this unit is (1 g = 0.001 kg).
    factor: Optional[float] = Field(default=None, gt=0)


class ValuePatch(BaseModel):
    description: Optional[str] = Field(default=None, max_length=255)
    factor: Optional[float] = Field(default=None, gt=0)
    is_active: Optional[bool] = None
    sort_order: Optional[int] = Field(default=None, ge=0, le=10000)


def may_add(m: OtherMasterName) -> Optional[str]:
    """Why no value may be added to this table now (None = it may)."""
    if not m.is_active:
        return f"“{m.master_name}” is inactive: make it active to add values."
    if m.is_system:
        return f"{m.master_name} is used by the program: values can be renamed, not added."
    if not m.allow_add:
        return f"Adding values to “{m.master_name}” is closed (Add Value is off)."
    return None


def may_edit(m: OtherMasterName) -> Optional[str]:
    """Why this table's values may not be changed now (None = they may)."""
    if not m.is_active:
        return f"“{m.master_name}” is inactive: make it active to change its values."
    if not m.can_edit:
        return f"Editing “{m.master_name}” is closed (Can Edit is off)."
    return None


@router.post("/{table_id}/values", status_code=201)
def add_value(table_id: int, body: ValueIn, request: Request, db: Session = Depends(get_db)):
    m = _table_or_404(db, table_id)
    problem = may_add(m)
    if problem:
        raise HTTPException(status_code=409, detail=problem)
    code = _clean(body.code)
    if not _CODE_OK.fullmatch(code):
        raise HTTPException(status_code=422, detail="The value code may contain letters, numbers, spaces and . - / & ( ) only.")
    description = _clean(body.description) or code
    parent = None
    if m.parent_master_id:
        parent = db.get(OtherMasterData, body.parent_id) if body.parent_id else None
        if parent is None or parent.master_id != m.parent_master_id:
            owner = db.get(OtherMasterName, m.parent_master_id)
            raise HTTPException(status_code=422, detail=f"Choose the {owner.master_name.lower()} this value belongs to.")
    # A value inside a category that has a base unit (Measurement unit: kg / L) is a unit of
    # measure: it needs its conversion factor, which every calculation then uses.
    extra = None
    base = (parent.extra or {}).get("base") if parent else None
    if base:
        if body.factor is None:
            raise HTTPException(status_code=422, detail=f"Give the conversion factor: 1 {code} = how many {base}?")
        extra = {"to_base": body.factor, "base": base}
    rows = db.query(OtherMasterData).filter(OtherMasterData.master_id == m.id).all()
    if any(r.data_code.lower() == code.lower() for r in rows):
        raise HTTPException(status_code=409, detail=f"The value code “{code}” is already in “{m.master_name}”.")
    siblings = [r for r in rows if r.parent_id == (parent.id if parent else None)]
    v = OtherMasterData(master_id=m.id, data_code=code, data_value=description, parent_id=parent.id if parent else None,
                        is_active=int(body.is_active), sort_order=max((r.sort_order for r in siblings), default=-1) + 1,
                        extra=extra)
    db.add(v)
    db.commit()
    unit_table.invalidate()
    audit.note(request, target_id=m.id, summary=f"Added “{code}” to the master table “{m.master_name}”")
    return _value_out(v, {parent.id: parent} if parent else {}, _names(db, [v.created_by]))


@router.patch("/values/{value_id}")
def change_value(value_id: int, body: ValuePatch, request: Request, db: Session = Depends(get_db)):
    v = db.get(OtherMasterData, value_id)
    m = db.get(OtherMasterName, v.master_id) if v else None
    if v is None or m is None:
        raise HTTPException(status_code=404, detail="Value not found.")
    problem = may_edit(m)
    if problem:
        raise HTTPException(status_code=409, detail=problem)
    if body.is_active is False and m.is_system:
        raise HTTPException(status_code=409, detail=f"{m.master_name} is used by the program: its values can't be made inactive.")
    if body.description is not None:
        v.data_value = _clean(body.description) or v.data_code
    if body.is_active is not None:
        v.is_active = int(body.is_active)
    if body.sort_order is not None:
        v.sort_order = body.sort_order
    if body.factor is not None:
        if "to_base" not in (v.extra or {}):
            raise HTTPException(status_code=422, detail=f"“{v.data_code}” isn't a unit of measure: it has no conversion factor.")
        v.extra = {**(v.extra or {}), "to_base": body.factor}  # a new dict, so the JSON column is saved
    db.commit()
    unit_table.invalidate()
    audit.note(request, target_id=m.id, summary=f"Changed “{v.data_code}” in the master table “{m.master_name}”")
    parent = db.get(OtherMasterData, v.parent_id) if v.parent_id else None
    return _value_out(v, {parent.id: parent} if parent else {}, _names(db, [v.created_by, v.mod_by]))
