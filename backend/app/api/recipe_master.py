"""
Recipe Master (requirements points 1-3).

GET    /recipes                                every product's current recipe: version, cost per kg,
                                               standards result, attachments (admins and the Super Admin)
GET    /recipe-standards                       the standard rules
POST   /recipe-standards                       add a rule            (Super Admin)
PUT    /recipe-standards/{id}                  change a rule         (Super Admin)
DELETE /recipe-standards/{id}                  remove a rule         (Super Admin)
GET    /products/{id}/recipe/attachments       supporting files of the recipe process
POST   /products/{id}/recipe/attachments       upload one file       (Super Admin)
GET    /recipe-attachments/{id}/file           download
DELETE /recipe-attachments/{id}                remove                (Super Admin)
"""

import re
import uuid
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.ownership import account, check_product_access
from app.database.database import get_db
from app.database.models import Category, Product, RecipeAttachment, RecipeStandard, RecipeVersion
from app.services import audit
from app.services.recipe_tools import costing, validate

router = APIRouter(tags=["Recipe Master"])

from app.services.storage import get_storage  # noqa: E402

ATTACH_DIR = get_storage().area_dir("recipe_files")  # FMCG_STORAGE_ROOT / FMCG_ATTACHMENTS_DIR
ALLOWED = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv", ".txt"}
MAX_BYTES = 20 * 1024 * 1024


def _role(request: Request) -> str:
    return account(request).get("role") or ""


def _admin_or_super(request: Request) -> dict:
    me = account(request)
    if me.get("role") not in ("admin", "client"):
        raise HTTPException(status_code=403, detail="Only admins and the Super Admin can open recipes.")
    return me


def _attachment_out(a: RecipeAttachment) -> dict:
    return {"id": a.id, "filename": a.filename, "content_type": a.content_type, "size_bytes": a.size_bytes,
            "recipe_version": a.recipe_version, "uploaded_by": a.uploaded_by,
            "created_at": a.created_at.isoformat() + "Z" if a.created_at else None}


def _latest_version(db: Session, product_id: int) -> Optional[int]:
    v = db.query(RecipeVersion.version).filter(RecipeVersion.product_id == product_id).order_by(RecipeVersion.version.desc()).first()
    return v[0] if v else None


def recipe_extras(db: Session, product: Product) -> dict:
    """Costing, standards, attachments and earlier steps (read-only) for one product."""
    recipe = product.recipe or None
    rules = db.query(RecipeStandard).filter(RecipeStandard.is_active == 1).all()
    legacy = None
    if recipe and recipe.get("steps"):
        legacy = {"version": _latest_version(db, product.id), "steps": recipe["steps"]}
    else:
        for v in db.query(RecipeVersion).filter(RecipeVersion.product_id == product.id).order_by(RecipeVersion.version.desc()).limit(50):
            if v.recipe and v.recipe.get("steps"):
                legacy = {"version": v.version, "steps": v.recipe["steps"]}
                break
    files = db.query(RecipeAttachment).filter(RecipeAttachment.product_id == product.id).order_by(RecipeAttachment.created_at.desc()).all()
    return {
        "costing": costing(recipe),
        "standards": validate(recipe, rules, product.category_id, product.id) if recipe else {"passed": None, "failed_count": 0, "checked": 0, "results": []},
        "attachments": [_attachment_out(a) for a in files],
        "legacy_steps": legacy,  # steps of an earlier version, shown read-only
        "latest_version": _latest_version(db, product.id),  # (not "version": a save returns its own)
    }


@router.get("/recipes")
def recipe_master(request: Request, db: Session = Depends(get_db)):
    me = _admin_or_super(request)
    q = db.query(Product).filter(Product.is_active == 1)
    if me.get("role") == "admin":
        from app.database.models import User
        from app.services.product_access import visible_product_ids

        visible = visible_product_ids(db, db.get(User, me.get("id")))
        if visible is not None:
            q = q.filter(Product.id.in_(visible or {-1}))
    cats = {c.id: c.name for c in db.query(Category).all()}
    rules = db.query(RecipeStandard).filter(RecipeStandard.is_active == 1).all()
    counts = {}
    for (pid,) in db.query(RecipeAttachment.product_id).all():
        counts[pid] = counts.get(pid, 0) + 1
    out = []
    for p in q.order_by(Product.name).all():
        recipe = p.recipe or None
        c = costing(recipe)
        v = validate(recipe, rules, p.category_id, p.id) if recipe else None
        out.append({
            "product_id": p.id, "product_name": p.name, "sku": p.sku, "category_name": cats.get(p.category_id),
            "has_recipe": bool(recipe), "version": _latest_version(db, p.id),
            "ingredients": len((recipe or {}).get("ingredients") or []),
            "cost_per_kg": c["cost_per_kg"], "costing_complete": c["complete"], "missing_costs": len(c["missing_costs"]),
            "standards": {"passed": v["passed"], "failed_count": v["failed_count"], "checked": v["checked"]} if v else None,
            "attachments": counts.get(p.id, 0),
            "updated_at": (recipe or {}).get("updated_at"),
        })
    return {"recipes": out, "standards": len(rules)}


# ---------- standards ----------

class StandardIn(BaseModel):
    name: str = Field(min_length=3, max_length=160)
    kind: Literal["required_ingredient", "ingredient_share", "max_cost_per_kg", "costs_complete", "ingredient_cost_per_kg"]
    ingredient: Optional[str] = Field(default=None, max_length=120)
    min_value: Optional[float] = Field(default=None, ge=0)
    max_value: Optional[float] = Field(default=None, ge=0)
    # Scope, exactly one: a product, a category, or every product (explicit)
    product_id: Optional[int] = None
    category_id: Optional[int] = None
    applies_to_all: bool = False
    is_active: bool = True


def _names(db: Session) -> tuple:
    return ({c.id: c.name for c in db.query(Category).all()}, {p.id: p.name for p in db.query(Product).all()})


def _standard_out(r: RecipeStandard, cats: dict, products: Optional[dict] = None) -> dict:
    scope = "product" if r.product_id else "category" if r.category_id else "all" if r.applies_to_all else "none"
    label = {"product": f"Only {(products or {}).get(r.product_id, 'this product')}",
             "category": f"Only {cats.get(r.category_id, 'this category')}", "all": "All products", "none": "Applies to nothing"}[scope]
    return {"id": r.id, "name": r.name, "kind": r.kind, "ingredient": r.ingredient, "min_value": r.min_value,
            "max_value": r.max_value, "product_id": r.product_id, "product_name": (products or {}).get(r.product_id),
            "category_id": r.category_id, "category_name": cats.get(r.category_id), "applies_to_all": bool(r.applies_to_all),
            "scope": scope, "scope_label": label, "is_active": bool(r.is_active), "created_by": r.created_by}


def _check_standard(body: StandardIn, db: Session) -> None:
    scopes = sum([bool(body.product_id), bool(body.category_id), bool(body.applies_to_all)])
    if scopes != 1:
        raise HTTPException(status_code=400, detail="Choose what the rule applies to: one product, one category, or all products.")
    if body.product_id and db.get(Product, body.product_id) is None:
        raise HTTPException(status_code=400, detail="That product doesn't exist.")
    if body.category_id and db.get(Category, body.category_id) is None:
        raise HTTPException(status_code=400, detail="That category doesn't exist.")
    if body.kind in ("required_ingredient", "ingredient_share", "ingredient_cost_per_kg") and not (body.ingredient or "").strip():
        raise HTTPException(status_code=400, detail="Name the ingredient this rule checks.")
    if body.kind == "ingredient_cost_per_kg" and body.max_value is None:
        raise HTTPException(status_code=400, detail="Give the maximum Cost per KG for this ingredient.")
    if body.kind == "ingredient_share" and body.min_value is None and body.max_value is None:
        raise HTTPException(status_code=400, detail="Give a minimum and/or maximum Usage %.")
    if body.kind == "ingredient_share" and body.min_value is not None and body.max_value is not None and body.min_value > body.max_value:
        raise HTTPException(status_code=400, detail="The minimum is above the maximum.")
    if body.kind == "max_cost_per_kg" and body.max_value is None:
        raise HTTPException(status_code=400, detail="Give the maximum cost per kg.")


@router.get("/recipe-standards")
def list_standards(request: Request, product_id: Optional[int] = None, db: Session = Depends(get_db)):
    """Every rule, or with ?product_id= the rules made for that one product."""
    _admin_or_super(request)
    cats, products = _names(db)
    q = db.query(RecipeStandard)
    if product_id:
        q = q.filter(RecipeStandard.product_id == product_id)
    return {"standards": [_standard_out(r, cats, products) for r in q.order_by(RecipeStandard.id).all()]}


def _fields(body: StandardIn) -> dict:
    d = body.model_dump(exclude={"is_active", "applies_to_all"})
    d["applies_to_all"] = 1 if body.applies_to_all else 0
    d["ingredient"] = (body.ingredient or "").strip() or None
    return d


@router.post("/recipe-standards", status_code=201)
def add_standard(body: StandardIn, request: Request, db: Session = Depends(get_db)):
    _check_standard(body, db)
    r = RecipeStandard(**_fields(body), is_active=1 if body.is_active else 0, created_by=account(request).get("name"))
    db.add(r)
    db.commit()
    audit.note(request, summary=f"Added recipe standard “{r.name}”")
    return _standard_out(r, *_names(db))


@router.put("/recipe-standards/{standard_id}")
def change_standard(standard_id: int, body: StandardIn, request: Request, db: Session = Depends(get_db)):
    r = db.get(RecipeStandard, standard_id)
    if r is None:
        raise HTTPException(status_code=404, detail="Rule not found.")
    _check_standard(body, db)
    for k, v in _fields(body).items():
        setattr(r, k, v)
    r.is_active = 1 if body.is_active else 0
    db.commit()
    audit.note(request, summary=f"Changed recipe standard “{r.name}”")
    return _standard_out(r, *_names(db))


@router.delete("/recipe-standards/{standard_id}")
def remove_standard(standard_id: int, request: Request, db: Session = Depends(get_db)):
    r = db.get(RecipeStandard, standard_id)
    if r is None:
        raise HTTPException(status_code=404, detail="Rule not found.")
    db.delete(r)
    db.commit()
    audit.note(request, summary=f"Removed recipe standard “{r.name}”")
    return {"id": standard_id, "deleted": True}


# ---------- attachments ----------

@router.get("/products/{product_id}/recipe/attachments")
def list_attachments(product_id: int, request: Request, db: Session = Depends(get_db)):
    _admin_or_super(request)
    check_product_access(db, request, product_id)
    rows = db.query(RecipeAttachment).filter(RecipeAttachment.product_id == product_id).order_by(RecipeAttachment.created_at.desc()).all()
    return {"attachments": [_attachment_out(a) for a in rows]}


@router.post("/products/{product_id}/recipe/attachments", status_code=201)
async def upload_attachment(product_id: int, request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
    check_product_access(db, request, product_id)
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found.")
    name = Path(file.filename or "file").name[:200]
    ext = Path(name).suffix.lower()
    if ext not in ALLOWED:
        raise HTTPException(status_code=400, detail="Attach a PDF, image, Word, Excel, PowerPoint, CSV or text file.")
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=400, detail="The file is larger than 20 MB.")
    if not data:
        raise HTTPException(status_code=400, detail="The file is empty.")
    folder = get_storage().area_dir("recipe_files")
    stored = folder / f"p{product_id}_{uuid.uuid4().hex}{ext}"
    stored.write_bytes(data)
    a = RecipeAttachment(product_id=product_id, recipe_version=_latest_version(db, product_id), filename=re.sub(r"[\r\n]", " ", name),
                         stored_path=get_storage().to_stored(stored), content_type=file.content_type, size_bytes=len(data),
                         uploaded_by=account(request).get("name"))
    db.add(a)
    db.commit()
    audit.note(request, summary=f"Attached {a.filename} to the recipe of {product.name}")
    return _attachment_out(a)


def _attachment_or_404(db: Session, request: Request, attachment_id: int) -> RecipeAttachment:
    a = db.get(RecipeAttachment, attachment_id)
    if a is None:
        raise HTTPException(status_code=404, detail="File not found.")
    check_product_access(db, request, a.product_id)
    return a


@router.get("/recipe-attachments/{attachment_id}/file")
def download_attachment(attachment_id: int, request: Request, db: Session = Depends(get_db)):
    _admin_or_super(request)
    a = _attachment_or_404(db, request, attachment_id)
    found = get_storage().resolve(a.stored_path)
    if found is None:
        raise HTTPException(status_code=404, detail="The file is missing on the server.")
    return FileResponse(found, media_type=a.content_type or "application/octet-stream", filename=a.filename)


@router.delete("/recipe-attachments/{attachment_id}")
def delete_attachment(attachment_id: int, request: Request, db: Session = Depends(get_db)):
    a = _attachment_or_404(db, request, attachment_id)
    path = get_storage().resolve(a.stored_path)  # only ever inside the storage folders
    db.delete(a)
    db.commit()
    try:
        if path is not None:
            path.unlink(missing_ok=True)
    except OSError:
        pass
    audit.note(request, summary=f"Removed attachment {a.filename}")
    return {"id": attachment_id, "deleted": True}
