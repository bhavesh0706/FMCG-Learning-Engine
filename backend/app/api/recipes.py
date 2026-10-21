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
    steps: List[Step] = Field(default_factory=list, max_length=60)
    notes: Optional[str] = Field(default=None, max_length=3000)


@router.get("/products/{product_id}/recipe")
def get_recipe(product_id: int, request: Request, db: Session = Depends(get_db)):
    check_product_access(db, request, product_id)
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")
    return {"product_id": product.id, "product_name": product.name, "recipe": product.recipe}


@router.put("/products/{product_id}/recipe")
def save_recipe(product_id: int, body: RecipeIn, request: Request, db: Session = Depends(get_db)):
    check_product_access(db, request, product_id)
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")
    recipe = body.model_dump()
    recipe["ingredients"] = [i for i in recipe["ingredients"] if i["name"]]
    recipe["steps"] = [s for s in recipe["steps"] if s["title"]]
    recipe["updated_at"] = datetime.utcnow().isoformat()
    old = product.recipe or None
    product.recipe = recipe if (recipe["ingredients"] or recipe["steps"]) else None
    db.commit()
    who = account(request)
    version = save_version(db, product, old, product.recipe, who)
    if version is not None:
        audit.note(request, details={"version": version.version, "changes": version.changes},
                   summary=f"Changed the recipe of product #{product.id} ({product.name}) to version {version.version}")
        if who.get("role") == "client":
            notify_admins_of_client_change(db, product, version)
    return {"product_id": product.id, "recipe": product.recipe, "version": version.version if version else None}


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
    return {"product_id": product_id,
            "versions": [{**version_out(v, role), "rounds": used.get(v.version, [])} for v in rows]}
