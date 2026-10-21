"""
Survey rounds in the admin dashboard (the organization starts them in its portal).

GET  /products/{product_id}/rounds                every round: recipe, progress, who started it, rating
POST /products/{product_id}/rounds/{number}/end   end the live round early (e.g. the field work stopped)

Admins only (access.py). Surveys from the field app are filed under the live
round automatically (services/rounds.py), so there is nothing else to manage.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.org import end_round_for
from app.api.product_feedback import rounds_summary
from app.database.database import get_db
from app.database.models import Product

router = APIRouter(tags=["Survey rounds"])


@router.get("/products/{product_id}/rounds")
def product_rounds(product_id: int, db: Session = Depends(get_db)):
    if not db.get(Product, product_id):
        raise HTTPException(status_code=404, detail="Product not found.")
    return {"product_id": product_id, "rounds": rounds_summary(db, product_id, viewer_role="admin")}


@router.post("/products/{product_id}/rounds/{number}/end")
def end_product_round(product_id: int, number: int, request: Request, db: Session = Depends(get_db)):
    return end_round_for(db, request, product_id, number)
