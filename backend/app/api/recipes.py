"""
Product recipes: how each product is made, from the first step to packing.

GET /products/{product_id}/recipe   -> {recipe}   (null if none yet)
PUT /products/{product_id}/recipe   {batch_size, ingredients, steps, notes} -> {recipe}

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

from app.database.database import get_db
from app.database.models import Product
from app.services import audit

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
def get_recipe(product_id: int, db: Session = Depends(get_db)):
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")
    return {"product_id": product.id, "product_name": product.name, "recipe": product.recipe}


@router.put("/products/{product_id}/recipe")
def save_recipe(product_id: int, body: RecipeIn, request: Request, db: Session = Depends(get_db)):
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")
    recipe = body.model_dump()
    recipe["ingredients"] = [i for i in recipe["ingredients"] if i["name"]]
    recipe["steps"] = [s for s in recipe["steps"] if s["title"]]
    recipe["updated_at"] = datetime.utcnow().isoformat()
    old = product.recipe or {}
    product.recipe = recipe if (recipe["ingredients"] or recipe["steps"]) else None
    db.commit()
    audit.note(request, details={
        "ingredients": [len(old.get("ingredients") or []), len(recipe["ingredients"])],
        "steps": [len(old.get("steps") or []), len(recipe["steps"])],
        "batch_size": [old.get("batch_size"), recipe.get("batch_size")],
    })
    return {"product_id": product.id, "recipe": product.recipe}
