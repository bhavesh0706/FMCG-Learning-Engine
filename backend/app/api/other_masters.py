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
DELETE /admin/other-masters/values/{value_id}    only a value nothing uses (needs Active + Can Edit)

Active    the table can be used (an inactive table disappears from GET /lov and takes no new values).
Can Edit  its existing values may be changed.
Add Value new values may be added.
System tables (is_system) hold codes the program relies on: they stay active, take no new
values and their codes never change. A value may be deleted when nothing uses it: not a code
the program itself relies on (built-in question / rule types, the unit categories, kg and L),
no value belongs to it, and no question, rule or recipe uses it. Otherwise it is deactivated.
"""

import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.database.models import OtherMasterData, OtherMasterName, Product, RecipeStandard, RecipeVersion, SurveyQuestion, User
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
        "closed_why": None,
        # The server's own verdicts (None = allowed): the page shows these, never its own rules.
        "add_problem": may_add(m), "edit_problem": may_edit(m),
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

def _calculation_codes(m: OtherMasterName) -> set:
    """The only values that are never deleted: every quantity and cost is converted through them
    (the Weight/Mass and Volume categories and their base units kg and L)."""
    from app.services.lov import UNIT_CATEGORIES

    return {
        "UNIT_CATEGORY": {c for c, *_ in UNIT_CATEGORIES},
        "MEASUREMENT_UNIT": {base for *_, base in UNIT_CATEGORIES},
    }.get(m.master_code, set())


def _built_in_codes(m: OtherMasterName) -> set:
    """Starting codes the app knows (built-in question / rule types, sentiments, roles ...). They
    may be deleted: saved questions, rules and results keep their own code and keep working; the
    value only stops being offered. The delete dialog says so (delete_warning)."""
    from app.services.lov import BUILT_IN_QUESTION_TYPES, BUILT_IN_STANDARD_TYPES, DEFAULTS

    if m.is_system:
        # Lists the program reads by code (sentiments, ratings, roles ...): their starting codes stay.
        for code, _name, _sys, values in DEFAULTS:
            if code == m.master_code:
                return {c for c, *_ in values}
    return {
        "QUESTION_TYPE": set(BUILT_IN_QUESTION_TYPES),
        "RECIPE_STANDARD_TYPE": set(BUILT_IN_STANDARD_TYPES),
    }.get(m.master_code, set())


def value_delete_warning(m: OtherMasterName, v: OtherMasterData) -> Optional[str]:
    if v.data_code not in _built_in_codes(m):
        return None
    if m.master_code == "RECIPE_STANDARD_TYPE":
        return "It's one of the app's starting rule types: it won't be offered for new rules. Rules already made with it keep being checked."
    if m.master_code == "QUESTION_TYPE":
        return "It's one of the app's starting question types: it won't be offered for new questions. Questions already made with it keep working."
    return "It's one of the app's starting values: the app may still produce this code (e.g. from the AI analysis), and it is then shown by its code."


def _recipe_units(db: Session) -> set:
    """Lower-case units used by any recipe (current or saved version)."""
    used = set()
    for (recipe,) in list(db.query(Product.recipe)) + list(db.query(RecipeVersion.recipe)):
        for ing in ((recipe or {}).get("ingredients") or []) if isinstance(recipe, dict) else []:
            if isinstance(ing, dict) and ing.get("unit"):
                used.add(str(ing["unit"]).strip().lower())
    return used


def _usage(db: Session, m: OtherMasterName) -> dict:
    """What uses this table's values: {"children": {value_id: n}, "questions": {code: n},
    "rules": {code: n}, "units": set of lower-case codes}."""
    child_tables = [c.id for c in db.query(OtherMasterName).filter(OtherMasterName.parent_master_id == m.id)]
    children = dict(db.query(OtherMasterData.parent_id, func.count()).filter(OtherMasterData.master_id.in_(child_tables))
                    .group_by(OtherMasterData.parent_id)) if child_tables else {}
    out = {"children": children, "questions": {}, "rules": {}, "units": set()}
    if m.master_code == "QUESTION_TYPE":
        out["questions"] = dict(db.query(SurveyQuestion.type_code, func.count()).filter(SurveyQuestion.type_code.isnot(None))
                                .group_by(SurveyQuestion.type_code))
    elif m.master_code == "RECIPE_STANDARD_TYPE":
        for code, name in db.query(RecipeStandard.type_code, RecipeStandard.name).filter(RecipeStandard.type_code.isnot(None)):
            out["rules"].setdefault(code, []).append(name)
    elif m.master_code == "MEASUREMENT_UNIT":
        out["units"] = _recipe_units(db) | {u.lower() for (u,) in db.query(RecipeStandard.unit).filter(RecipeStandard.unit.isnot(None))}
    return out


def value_delete_problem(m: OtherMasterName, v: OtherMasterData, usage: dict) -> Optional[str]:
    """Why this value can't be deleted (None = it can). Deactivating is always the alternative."""
    if v.data_code in _calculation_codes(m):
        return f"Every quantity and cost is converted through “{v.data_code}”, so it must stay. Deactivate it to hide it."
    n = usage["children"].get(v.id)
    if n:
        return f"{n} value{'s' if n != 1 else ''} belong{'' if n != 1 else 's'} to “{v.data_value or v.data_code}”: delete or move them first, or deactivate it."
    n = usage["questions"].get(v.data_code)
    if n:
        return f"{n} survey question{'s use' if n != 1 else ' uses'} “{v.data_code}”. Deactivate it instead: saved surveys keep it."
    names = usage["rules"].get(v.data_code)
    if names:
        shown = ", ".join(f"“{n}”" for n in names[:3]) + (f" and {len(names) - 3} more" if len(names) > 3 else "")
        return f"Recipe rule{'s' if len(names) != 1 else ''} {shown} use{'' if len(names) != 1 else 's'} it: remove {'them' if len(names) != 1 else 'it'} first, then delete. Or deactivate it."
    if v.data_code.lower() in usage["units"]:
        return f"Recipes or recipe rules use the unit “{v.data_code}”. Deactivate it instead: saved recipes keep it."
    return None


def _value_out(v: OtherMasterData, parents: dict, names: dict, delete_problem_text: Optional[str] = None,
               delete_warning_text: Optional[str] = None) -> dict:
    parent = parents.get(v.parent_id)
    return {
        "delete_problem": delete_problem_text, "delete_warning": delete_warning_text,
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
    usage = _usage(db, m)
    return {"table": _one(db, m), "values": [_value_out(r, parents, names, value_delete_problem(m, r, usage), value_delete_warning(m, r)) for r in rows],
            "parent_options": options}


class ValueIn(BaseModel):
    code: str = Field(min_length=1, max_length=80)
    description: Optional[str] = Field(default=None, max_length=255)
    is_active: bool = True
    parent_id: Optional[int] = None
    # Units of measure: how many of the category's base unit one of this unit is (1 g = 0.001 kg).
    factor: Optional[float] = Field(default=None, gt=0)
    # Question type: the built-in type a new type works like (its answers, options and report).
    behaves_like: Optional[str] = Field(default=None, max_length=30)


class ValuePatch(BaseModel):
    description: Optional[str] = Field(default=None, max_length=255)
    factor: Optional[float] = Field(default=None, gt=0)
    is_active: Optional[bool] = None
    sort_order: Optional[int] = Field(default=None, ge=0, le=10000)


# Why each system list takes no new values (shown on the Master page).
CLOSED_WHY = {
    "RATING_SCALE": "Ratings are always 1 to 5: averages and reports are built on that scale. Labels can be renamed.",
    "SENTIMENT": "Sentiments are produced by the AI analysis, so a new one would never be used. Labels can be renamed.",
    "SEVERITY": "Severities are produced by the AI analysis, so a new one would never be used. Labels can be renamed.",
    "SURVEY_STATUS": "Survey statuses are set by the app as a survey moves along. Labels can be renamed.",
    "PRODUCT_STATE": "Product states are worked out by the app from its surveys. Labels can be renamed.",
    "USER_ROLE": "Each role carries its own permissions in the app. Labels can be renamed.",
    "REPORT_BREAKDOWN": "Each breakdown reads one field of the respondent. Labels can be renamed.",
    "REPORT_ROLE": "Each role fills one section of the report. Labels can be renamed.",
    "UNIT_CATEGORY": "Units are grouped in exactly two categories: Weight/Mass and Volume. Add units in Measurement unit.",
}


def may_add(m: OtherMasterName) -> Optional[str]:
    """Why no value may be added to this table now (None = it may)."""
    if not m.is_active:
        return f"“{m.master_name}” is inactive: make it active to add values."
    # Every table, built-in ones too, follows its own Add Value switch: the admin decides.
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
    from app.services.lov import BEHAVES_LIKE

    if m.master_code in BEHAVES_LIKE:
        from app.services.lov import ADDED_BEHAVES_LIKE

        # Nothing to choose: a new question type is answered as text, a new rule type is checked by people.
        base = body.behaves_like if body.behaves_like in BEHAVES_LIKE[m.master_code] else ADDED_BEHAVES_LIKE[m.master_code]
        extra = {"behaves_like": base}
    rows = db.query(OtherMasterData).filter(OtherMasterData.master_id == m.id).all()
    if any(r.data_code.lower() == code.lower() for r in rows):
        raise HTTPException(status_code=409, detail=f"The value code “{code}” is already in “{m.master_name}”.")
    siblings = [r for r in rows if r.parent_id == (parent.id if parent else None)]
    v = OtherMasterData(master_id=m.id, data_code=code, data_value=description, parent_id=parent.id if parent else None,
                        is_active=int(body.is_active), sort_order=max((r.sort_order for r in siblings), default=-1) + 1,
                        extra=extra)
    if code in (m.removed_codes or []):
        m.removed_codes = [c for c in m.removed_codes if c != code]  # added back on purpose
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
    # Any value may be made active or inactive by the admin (inactive = no longer offered in lists;
    # data saved with it keeps it).
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


@router.delete("/values/{value_id}")
def delete_value(value_id: int, request: Request, db: Session = Depends(get_db)):
    """Deletes a value nothing uses. Saved data that only copied its label is unaffected."""
    v = db.get(OtherMasterData, value_id)
    m = db.get(OtherMasterName, v.master_id) if v else None
    if v is None or m is None:
        raise HTTPException(status_code=404, detail="Value not found.")
    problem = may_edit(m) or value_delete_problem(m, v, _usage(db, m))
    if problem:
        raise HTTPException(status_code=409, detail=problem)
    code = v.data_code
    # Remember it, so start-up seeding doesn't add a deleted starting value back.
    m.removed_codes = sorted(set(m.removed_codes or []) | {code})
    db.delete(v)
    db.commit()
    unit_table.invalidate()
    audit.note(request, target_id=m.id, summary=f"Deleted “{code}” from the master table “{m.master_name}”")
    return {"deleted": value_id}
