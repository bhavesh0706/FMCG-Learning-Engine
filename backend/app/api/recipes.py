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
    # "Add new recipe version": only once the current version has consumer responses (it is then
    # locked); otherwise the current version is corrected in place.
    new_version: bool = False
    # A new version that exceeds the cost limit or the margin minimum needs this explicit approval
    approve_financials: bool = False
    # What the Super Admin decided about each consumer recommendation: [{change, ingredient, decision}]
    recommendation_decisions: list = Field(default_factory=list)


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
    from app.services.recipe_versions import latest_version_row, version_locked
    from app.services.rounds import live_survey_lock

    latest = latest_version_row(db, product.id)
    is_locked = latest is not None and version_locked(db, product.id, latest.version)
    if body.new_version:
        if latest is None or not is_locked:
            raise HTTPException(status_code=409, detail={
                "code": "version_not_locked",
                "message": (f"Recipe v{latest.version} has no consumer responses yet: edit it directly. "
                            "A new version is added only after consumers have tested the current one.")
                if latest is not None else "Save the first recipe first."})
    elif is_locked:
        raise HTTPException(status_code=409, detail={
            "code": "recipe_version_locked", "version": latest.version,
            "message": f"Recipe v{latest.version} has consumer responses, so it is locked. "
                       "Use Add new recipe version to change the recipe."})
    # While a survey is collecting, the recipe it tests can't change at all (in place or as a new
    # version): consumers must all taste the same recipe. The lock ends when that survey ends.
    live = live_survey_lock(db, [product.id])
    if live:
        raise HTTPException(status_code=409, detail={"code": "survey_ongoing", "message": live})
    recipe = body.model_dump()
    for extra in ("new_version", "approve_financials", "recommendation_decisions"):
        recipe.pop(extra, None)
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
    comparison = None
    if body.new_version:  # is the new version still commercially viable? (cost limit, margin)
        from app.services.recipe_economics import compare, targets_of
        comparison = compare(product.recipe, recipe, targets_of(product))
        if comparison["needs_approval"] and not body.approve_financials:
            raise HTTPException(status_code=409, detail={
                "code": "financial_review_needed",
                "message": "The new version: " + " ".join(w["message"] for w in comparison["warnings"]
                                                         if w["code"] != "costs_incomplete") + " Review it and approve to release it anyway.",
                "comparison": comparison})
    recipe["updated_at"] = datetime.utcnow().isoformat()
    old = product.recipe or None
    recipe["process"] = (recipe.get("process") or "").strip() or None
    product.recipe = recipe if (recipe["ingredients"] or recipe.get("notes") or recipe.get("process") or recipe.get("batch_size")) else None
    db.commit()
    who = account(request)
    version = save_version(db, product, old, product.recipe, who, new_version=body.new_version)
    if body.new_version and version is not None:  # history: the comparison and the decisions, with the version
        version.cost_comparison = comparison
        version.decisions = list(body.recommendation_decisions or [])
        db.commit()
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


class TargetsIn(BaseModel):
    selling_price_per_kg: Optional[float] = Field(default=None, ge=0)
    target_cost_per_kg: Optional[float] = Field(default=None, ge=0)
    min_margin_pct: Optional[float] = Field(default=None, ge=-100, le=100)


class CompareIn(BaseModel):
    recipe: dict


def _product(db, request, product_id):
    check_product_access(db, request, product_id)
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")
    return product


@router.put("/products/{product_id}/commercials")
def save_targets(product_id: int, body: TargetsIn, request: Request, db: Session = Depends(get_db)):
    """The product's selling price per kg, cost limit per kg and minimum margin (all optional)."""
    from app.services.recipe_economics import targets_of
    product = _product(db, request, product_id)
    product.selling_price_per_kg, product.target_cost_per_kg, product.min_margin_pct = (
        body.selling_price_per_kg, body.target_cost_per_kg, body.min_margin_pct)
    db.commit()
    return targets_of(product)


@router.post("/products/{product_id}/recipe/compare")
def compare_recipe(product_id: int, body: CompareIn, request: Request, db: Session = Depends(get_db)):
    """The proposed recipe against the current version: cost by ingredient, totals, profit, warnings."""
    from app.services.recipe_economics import compare, targets_of
    from app.services.recipe_tools import normalize_ingredients
    product = _product(db, request, product_id)
    proposed = dict(body.recipe or {})
    proposed["ingredients"] = normalize_ingredients([i for i in proposed.get("ingredients") or [] if (i or {}).get("name")])
    return compare(product.recipe, proposed, targets_of(product))


@router.get("/products/{product_id}/recipe/recommendations")
def recipe_recommendations(product_id: int, request: Request, db: Session = Depends(get_db)):
    """The latest consumer report's recipe recommendations, with the quantity each proposes."""
    from app.services.recipe_economics import recommendations
    return recommendations(db, _product(db, request, product_id))