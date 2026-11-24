"""
Product recipes: how each product is made, from the first step to packing.

GET /products/{product_id}/recipe   -> {recipe}   (null if none yet)
PUT /products/{product_id}/recipe   {batch_size, ingredients, steps, notes} -> {recipe, version}
GET /products/{product_id}/recipe/versions  -> {versions: [newest first]}  every saved version, who and what changed

Admins and the organization's login: every product. When the organization
changes a recipe, every admin gets a bell notification.

The consolidated report reads the recipe and links customer feedback to
specific ingredients and steps ("7 of 10 find it too sweet: reduce sugar in step 3").

Register in app/main.py:
    from app.api.recipes import router as recipes_router
    app.include_router(recipes_router)
"""

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.api.ownership import account, check_product_access
from app.database.database import get_db
from app.database.models import Product, RecipeVersion, SurveyRound
from app.services import audit
from app.services.recipe_versions import notify_admins_of_client_change, save_version, version_out

router = APIRouter(tags=["Recipes"])


def _clean(v):
    return " ".join(str(v).split()) if isinstance(v, str) else v


class Ingredient(BaseModel):
    name: str = Field(default="", max_length=120)  # blank rows are ignored
    quantity: Optional[str] = Field(default=None, max_length=40)  # "450", "2.5", "a pinch"
    unit: Optional[str] = Field(default=None, max_length=30)  # g, kg, ml, %, ...
    notes: Optional[str] = Field(default=None, max_length=300)
    cost_per_kg: Optional[float] = Field(default=None, ge=0, le=10_000_000)  # Cost in Use = Cost per KG x Usage %

    _c = field_validator("name", "quantity", "unit", "notes", mode="before")(_clean)


class Step(BaseModel):
    title: str = Field(default="", max_length=160)  # blank rows are ignored
    details: Optional[str] = Field(default=None, max_length=1500)
    duration: Optional[str] = Field(default=None, max_length=40)  # "12 min"
    temperature: Optional[str] = Field(default=None, max_length=40)  # "85 °C"

    _c = field_validator("title", "duration", "temperature", mode="before")(_clean)


class RecipeIn(BaseModel):
    batch_size: Optional[str] = Field(default=None, max_length=120)  # "10 kg batch = 100 cups of 100 g"
    ingredients: List[Ingredient] = Field(default_factory=list, max_length=60)
    # Steps are no longer part of a recipe (the process is described with notes and
    # attachments). Sent steps are ignored; earlier versions keep theirs, read-only.
    steps: List[Step] = Field(default_factory=list, max_length=60)
    notes: Optional[str] = Field(default=None, max_length=3000)
    # The production process typed in (alongside, or instead of, the process attachments)
    process: Optional[str] = Field(default=None, max_length=10000)


@router.get("/products/{product_id}/recipe")
def get_recipe(product_id: int, request: Request, db: Session = Depends(get_db)):
    check_product_access(db, request, product_id)
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")
    from app.api.recipe_master import recipe_extras

    return {"product_id": product.id, "product_name": product.name, "recipe": product.recipe, **recipe_extras(db, product)}


@router.put("/products/{product_id}/recipe")
def save_recipe(product_id: int, body: RecipeIn, request: Request, db: Session = Depends(get_db)):
    check_product_access(db, request, product_id)
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")
    from app.services.rounds import live_survey_lock

    locked = live_survey_lock(db, [product.id])
    if locked:  # no recipe change (and no new version) while consumers test the current one
        raise HTTPException(status_code=409, detail={"code": "survey_ongoing", "message": locked})
    recipe = body.model_dump()
    from app.services.recipe_tools import normalize_ingredients

    # "65 kg" typed in Quantity -> 65 + kg; a number without a unit is kg
    recipe["ingredients"] = normalize_ingredients([i for i in recipe["ingredients"] if i["name"]])
    # Batch weight: the ingredients must add up to exactly the batch size
    from app.services.recipe_tools import batch_check

    check = batch_check(recipe)
    if not check["ok"]:
        raise HTTPException(status_code=400, detail=check["message"])
    recipe["steps"] = []  # no Steps in new versions; earlier versions keep theirs (read-only)
    # Recipe standards: a recipe is saved only if it meets every standard that applies to the
    # product (this product's, its category's, all products'). Clearing a recipe stays possible.
    if recipe["ingredients"]:
        from app.services.rounds import recipe_failures

        failures = recipe_failures(db, product, recipe)
        if failures:
            n = len(failures)
            raise HTTPException(status_code=409, detail={
                "code": "recipe_standards_not_met",
                "message": f"Recipe not saved: it doesn't meet {n} recipe standard{'s' if n != 1 else ''}. "
                           "Change the recipe so every standard is met (or change the standard), then save.",
                "failed_standards": failures})
    recipe["updated_at"] = datetime.utcnow().isoformat()
    old = product.recipe or None
    recipe["process"] = (recipe.get("process") or "").strip() or None
    product.recipe = recipe if (recipe["ingredients"] or recipe.get("notes") or recipe.get("process") or recipe.get("batch_size")) else None
    db.commit()
    who = account(request)
    version = save_version(db, product, old, product.recipe, who)
    in_place = bool(getattr(version, "edited_in_place", False))
    if version is not None:
        audit.note(request, details={"version": version.version, "changes": version.changes, "updated_in_place": in_place},
                   summary=(f"Updated version {version.version} of the recipe of product #{product.id} ({product.name}); "
                            "no consumer had tasted it yet") if in_place else
                           f"Changed the recipe of product #{product.id} ({product.name}) to version {version.version}")
        if who.get("role") == "client":
            notify_admins_of_client_change(db, product, version)
    from app.api.recipe_master import recipe_extras

    return {"product_id": product.id, "recipe": product.recipe, "version": version.version if version else None,
            "version_updated_in_place": in_place, **recipe_extras(db, product)}


@router.delete("/products/{product_id}/recipe/versions/{number}")
def delete_recipe_version(product_id: int, number: int, request: Request, db: Session = Depends(get_db)):
    """A recipe version saved by mistake: the latest one, while no survey uses it."""
    from app.services.recipe_versions import VersionError, delete_latest_version

    check_product_access(db, request, product_id)
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")
    from app.services.rounds import live_survey_lock

    locked = live_survey_lock(db, [product.id])
    if locked:
        raise HTTPException(status_code=409, detail={"code": "survey_ongoing", "message": locked})
    try:
        current = delete_latest_version(db, product, number)
    except VersionError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    audit.note(request, summary=f"Deleted recipe version {number} of product #{product.id} ({product.name})")
    return {"deleted": number, "current_version": current.version if current else None, "recipe": product.recipe}


@router.get("/products/{product_id}/recipe/versions")
def recipe_versions(product_id: int, request: Request, db: Session = Depends(get_db)):
    """Every saved version of the recipe, newest first, with who saved it and what changed."""
    check_product_access(db, request, product_id)
    if not db.get(Product, product_id):
        raise HTTPException(status_code=404, detail="Product not found.")
    rows = (
        db.query(RecipeVersion)
        .filter(RecipeVersion.product_id == product_id)
        .order_by(RecipeVersion.version.desc())
        .limit(200)
        .all()
    )
    role = account(request).get("role")
    # Which survey rounds used each version ("Round 2" chips in the history).
    used = {}
    for number, version, status in (
        db.query(SurveyRound.number, SurveyRound.recipe_version, SurveyRound.status)
        .filter(SurveyRound.product_id == product_id)
        .order_by(SurveyRound.number)
        .all()
    ):
        used.setdefault(version, []).append({"number": number, "status": status})
    # The project of each version (one project per product version, api/projects.py).
    from app.database.models import Project

    projects = {p.product_version: {"id": p.id, "project_code": p.project_code, "project_name": p.project_name}
                for p in db.query(Project).filter(Project.product_id == product_id)}
    return {"product_id": product_id,
            "versions": [{**version_out(v, role), "rounds": used.get(v.version, []), "project": projects.get(v.version)}
                         for v in rows]}