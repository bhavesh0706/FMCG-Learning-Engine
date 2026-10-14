"""
Team management for admins (web "Team" page).

GET  /admin/users                     every account with its survey numbers
POST /admin/users                     add a team member (admin sets the password)
PUT  /admin/users/{user_id}           change can_view_history, is_active, role, name, age, gender
PUT  /admin/users/{user_id}/password  set a new password (e.g. forgotten)
GET  /admin/users/{user_id}/products  products assigned to this executive
PUT  /admin/users/{user_id}/products  {product_ids: [...]} replace them
GET  /admin/products/{product_id}/executives  executives assigned to this product
PUT  /admin/products/{product_id}/executives  {user_ids: [...]} replace them ([] = every executive)

Note: the web app has no login yet, so these are open to anyone who can reach
the backend, like the rest of the web API. Admin login for the web is the next step.

Register in app/main.py:
    from app.api.admin import router as admin_router
    app.include_router(admin_router)
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.auth import (
    _parse_identifier,
    _user_out,
    check_password,
    hash_password,
    link_earlier_surveys,
    survey_counts,
    surveys_of,
)
from app.database.database import get_db
from app.database.models import Product, ProductAssignment, User
from app.services.notifications import notify_product_assigned
from app.services.product_access import (
    assignees_for_product,
    assignments_for_user,
    set_product_users,
    set_user_products,
)

router = APIRouter(prefix="/admin", tags=["Admin"])


def _admin_name(request: Request) -> str:
    """Name of the signed-in admin (set by the access-control middleware)."""
    account = getattr(request.state, "account", None) or {}
    return account.get("name") or "Your admin"


@router.get("/users")
def list_users(
    q: Optional[str] = Query(default=None, max_length=150),
    tz_offset: int = Query(default=330, ge=-720, le=840),
    db: Session = Depends(get_db),
):
    query = db.query(User)
    if q and q.strip():
        like = f"%{q.strip().lower()}%"
        query = query.filter((User.name.ilike(like)) | (User.mobile.ilike(like)) | (User.email.ilike(like)))
    users = query.order_by(User.created_at.desc()).all()
    assigned = {}
    for uid, pid in db.query(ProductAssignment.user_id, ProductAssignment.product_id).all():
        assigned.setdefault(uid, []).append(pid)
    return {
        "users": [
            {**_user_out(u), "stats": survey_counts(db, surveys_of(u), tz_offset), "assigned_products": sorted(assigned.get(u.id, []))}
            for u in users
        ]
    }


class ProductIds(BaseModel):
    product_ids: list[int] = Field(default_factory=list, max_length=5000)


class UserIds(BaseModel):
    user_ids: list[int] = Field(default_factory=list, max_length=5000)


@router.get("/users/{user_id}/products")
def user_products(user_id: int, db: Session = Depends(get_db)):
    if not db.get(User, user_id):
        raise HTTPException(status_code=404, detail="User not found.")
    return {"user_id": user_id, "product_ids": assignments_for_user(db, user_id)}


@router.put("/users/{user_id}/products")
def save_user_products(user_id: int, body: ProductIds, request: Request, db: Session = Depends(get_db)):
    if not db.get(User, user_id):
        raise HTTPException(status_code=404, detail="User not found.")
    known = {pid for (pid,) in db.query(Product.id).filter(Product.id.in_(body.product_ids)).all()} if body.product_ids else set()
    unknown = set(body.product_ids) - known
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown product id(s): {sorted(unknown)}")
    before = set(assignments_for_user(db, user_id))
    saved = set_user_products(db, user_id, body.product_ids)
    # Tell the survey person about products they didn't have before.
    added = set(saved) - before
    if added:
        notify_product_assigned(db, {user_id: added}, _admin_name(request))
    return {"user_id": user_id, "product_ids": saved}


@router.get("/products/{product_id}/executives")
def product_executives(product_id: int, db: Session = Depends(get_db)):
    if not db.get(Product, product_id):
        raise HTTPException(status_code=404, detail="Product not found.")
    return {"product_id": product_id, "user_ids": assignees_for_product(db, product_id)}


@router.put("/products/{product_id}/executives")
def save_product_executives(product_id: int, body: UserIds, request: Request, db: Session = Depends(get_db)):
    if not db.get(Product, product_id):
        raise HTTPException(status_code=404, detail="Product not found.")
    known = {uid for (uid,) in db.query(User.id).filter(User.id.in_(body.user_ids)).all()} if body.user_ids else set()
    unknown = set(body.user_ids) - known
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown user id(s): {sorted(unknown)}")
    before = set(assignees_for_product(db, product_id))
    saved = set_product_users(db, product_id, body.user_ids)
    # Tell each newly added survey person about this product.
    added = set(saved) - before
    if added:
        notify_product_assigned(db, {uid: {product_id} for uid in added}, _admin_name(request))
    return {"product_id": product_id, "user_ids": saved}


GENDERS = {"Male", "Female", "Other", "Prefer not to say"}


class NewMember(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    mobile: str = Field(min_length=10, max_length=20)
    email: Optional[str] = Field(default=None, max_length=190)
    password: str = Field(min_length=8, max_length=128)
    age: Optional[int] = Field(default=None, ge=16, le=100)
    gender: Optional[str] = None
    can_view_history: bool = False
    role: str = "executive"


@router.post("/users", status_code=201)
def add_member(body: NewMember, db: Session = Depends(get_db)):
    """The admin creates the account and gives the executive their login details."""
    from app.database.models import User as U

    kind, mobile = _parse_identifier(body.mobile)
    if kind != "mobile":
        raise HTTPException(status_code=400, detail="Enter a 10-digit mobile number.")
    email = None
    if body.email and body.email.strip():
        k, email = _parse_identifier(body.email)
        if k != "email":
            raise HTTPException(status_code=400, detail="Enter a valid email address, or leave it empty.")
    check_password(body.password)
    if body.gender and body.gender not in GENDERS:
        raise HTTPException(status_code=400, detail="Choose a gender from the list.")
    if body.role not in ("executive", "admin"):
        raise HTTPException(status_code=400, detail="Role must be 'executive' or 'admin'.")
    if db.query(U).filter(U.mobile == mobile).first():
        raise HTTPException(status_code=409, detail="Someone in the team already uses this mobile number.")
    if email and db.query(U).filter(U.email == email).first():
        raise HTTPException(status_code=409, detail="Someone in the team already uses this email.")

    user = U(
        name=" ".join(body.name.split()),
        mobile=mobile,
        email=email,
        password_hash=hash_password(body.password),
        role=body.role,
        age=body.age,
        gender=body.gender,
        can_view_history=1 if body.can_view_history else 0,
        is_active=1,
        created_at=datetime.utcnow(),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    linked = link_earlier_surveys(db, user)
    return {"user": _user_out(user), "linked_surveys": linked}


class NewPassword(BaseModel):
    password: str = Field(min_length=8, max_length=128)


@router.put("/users/{user_id}/password")
def reset_password(user_id: int, body: NewPassword, db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    check_password(body.password)
    user.password_hash = hash_password(body.password)
    db.commit()
    return {"ok": True}


class UserUpdate(BaseModel):
    can_view_history: Optional[bool] = None
    is_active: Optional[bool] = None
    role: Optional[str] = None
    name: Optional[str] = Field(default=None, max_length=150)
    age: Optional[int] = Field(default=None, ge=16, le=100)
    gender: Optional[str] = None


@router.put("/users/{user_id}")
def update_user(user_id: int, body: UserUpdate, db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")
    if body.role is not None:
        if body.role not in ("executive", "admin"):
            raise HTTPException(status_code=400, detail="Role must be 'executive' or 'admin'.")
        user.role = body.role
    if body.can_view_history is not None:
        user.can_view_history = 1 if body.can_view_history else 0
    if body.is_active is not None:
        user.is_active = 1 if body.is_active else 0
    if body.name is not None and len(body.name.strip()) >= 2:
        user.name = " ".join(body.name.split())
    if body.age is not None:
        user.age = body.age
    if body.gender is not None:
        if body.gender not in GENDERS:
            raise HTTPException(status_code=400, detail="Choose a gender from the list.")
        user.gender = body.gender
    db.commit()
    db.refresh(user)
    return {"user": _user_out(user)}