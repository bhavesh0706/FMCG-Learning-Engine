"""
Super Admin accounts, managed from the Super Admin's own portal (Account page).
Same rules as the command-line tool (app.tools.super_admin): a one-time starting password,
changed at the first sign-in; resetting it signs that person out everywhere.

GET  /org/super-admins                       every Super Admin
POST /org/super-admins                       {name, email, current_password} -> {user, password}
POST /org/super-admins/{id}/reset-password   {current_password}              -> {user, password}
PUT  /org/super-admins/{id}                  {is_active, current_password}   -> {user}

Only a signed-in Super Admin may use these (checked here, not only by the address), and
every change needs that Super Admin's own current password. In the database a Super
Admin is a row of `users` with role "client".
"""

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.auth import EMAIL_RE, hash_password, sign_out_everywhere, verify_password
from app.api.ownership import account
from app.database.database import get_db
from app.database.models import User
from app.services import audit
from app.tools.organization import starting_password

router = APIRouter(prefix="/org/super-admins", tags=["Super Admin accounts"])
ROLE = "client"  # the Super Admin's role in the database and the API


def _me(request: Request, db: Session) -> User:
    me = account(request)
    if me.get("role") != ROLE:
        raise HTTPException(status_code=403, detail="Only a Super Admin can manage Super Admin accounts.")
    user = db.get(User, me.get("id"))
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Please sign in again.")
    return user


def _confirm(me: User, password: str) -> None:
    if not password or not verify_password(password, me.password_hash):
        raise HTTPException(status_code=403, detail="Your current password is not correct.")


def _other_super_admin(db: Session, me: User, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None or user.role != ROLE:
        raise HTTPException(status_code=404, detail="Super Admin not found.")
    if user.id == me.id:
        raise HTTPException(status_code=400, detail="Use your own Password and Sign-ins settings for your account.")
    return user


def _out(u: User, me_id: int) -> dict:
    return {
        "id": u.id, "name": u.name, "email": u.email, "is_active": bool(u.is_active), "is_me": u.id == me_id,
        "must_change_password": bool(u.must_change_password),
        "created_at": u.created_at.isoformat() if u.created_at else None,
        "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
    }


@router.get("")
def list_super_admins(request: Request, db: Session = Depends(get_db)):
    me = _me(request, db)
    rows = db.query(User).filter(User.role == ROLE).order_by(User.name).all()
    return {"super_admins": [_out(u, me.id) for u in rows]}


class NewSuperAdmin(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    email: str = Field(min_length=5, max_length=190)
    current_password: str = Field(min_length=1, max_length=200)


@router.post("", status_code=201)
def add_super_admin(body: NewSuperAdmin, request: Request, db: Session = Depends(get_db)):
    me = _me(request, db)
    _confirm(me, body.current_password)
    name = " ".join(body.name.split())
    email = body.email.strip().lower()
    if not EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Enter a valid email address.")
    if db.query(User).filter(User.email == email).first():
        raise HTTPException(status_code=409, detail=f"{email} already has an account. Use another email.")
    password = starting_password()
    user = User(name=name, email=email, mobile=None, password_hash=hash_password(password), role=ROLE, is_active=1,
                can_view_history=0, must_change_password=1, created_at=datetime.utcnow())
    db.add(user)
    db.commit()
    db.refresh(user)
    audit.note(request, summary=f"Added Super Admin {user.name} ({user.email})", details={"user_id": user.id})
    return {"user": _out(user, me.id), "password": password}


class Confirm(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)


@router.post("/{user_id}/reset-password")
def reset_super_admin_password(user_id: int, body: Confirm, request: Request, db: Session = Depends(get_db)):
    me = _me(request, db)
    _confirm(me, body.current_password)
    user = _other_super_admin(db, me, user_id)
    password = starting_password()
    user.password_hash = hash_password(password)
    user.must_change_password = 1
    sign_out_everywhere(user)
    db.commit()
    audit.note(request, summary=f"Reset the password of Super Admin {user.name} ({user.email})", details={"user_id": user.id})
    return {"user": _out(user, me.id), "password": password}


class Change(BaseModel):
    is_active: Optional[bool] = None
    current_password: str = Field(min_length=1, max_length=200)


@router.put("/{user_id}")
def change_super_admin(user_id: int, body: Change, request: Request, db: Session = Depends(get_db)):
    me = _me(request, db)
    _confirm(me, body.current_password)
    user = _other_super_admin(db, me, user_id)
    if body.is_active is not None and bool(user.is_active) != body.is_active:
        user.is_active = 1 if body.is_active else 0
        sign_out_everywhere(user)  # disabled (or re-enabled): every old sign-in ends
        db.commit()
        audit.note(request, summary=f"{'Reactivated' if body.is_active else 'Disabled'} Super Admin {user.name} ({user.email})",
                   details={"user_id": user.id})
    return {"user": _out(user, me.id)}
