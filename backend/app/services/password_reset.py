"""
Password reset by emailed code (Admin and Super Admin accounts).

  1. request_code(email): a 6-digit code is emailed to the account's address.
     The answer never says whether the email has an account (no probing).
  2. confirm(email, code, new password): the code must be the latest one, unused,
     at most CODE_TTL old and tried fewer than MAX_TRIES times. The password is
     changed and every sign-in of the account ends.

Signed-in accounts use the same code (sent to their own email) instead of their
current password on the Account page. Codes are stored hashed, never in clear.
Survey persons don't sign in with email: their admin resets their password.

Simple mode (reset_mode() == "simple"): while accounts have no real email (development),
"Forgot password" asks only for the mobile number or email and the new password, and the
Account page only for the new password. It is on outside production by default and must
stay off in production (anyone knowing an admin's number could take the account):
  FMCG_PASSWORD_RESET=simple | email   (default: email when APP_ENV=production, else simple)
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.database.models import PasswordResetCode, User
from app.services import mailer

CODE_TTL = timedelta(minutes=15)
MAX_TRIES = 5
RESEND_AFTER = timedelta(seconds=60)
MAX_PER_HOUR = 5
RESET_ROLES = ("admin", "client")


class ResetError(ValueError):
    pass


def reset_mode() -> str:
    """"simple" (new password only) or "email" (emailed 6-digit code)."""
    chosen = os.environ.get("FMCG_PASSWORD_RESET", "").strip().lower()
    if chosen in ("simple", "email"):
        return chosen
    return "email" if os.environ.get("APP_ENV", "development").strip().lower() == "production" else "simple"


def account_for_identifier(db: Session, identifier: str) -> Optional[User]:
    """Active Admin / Super Admin account by mobile number or email (simple mode)."""
    from fastapi import HTTPException

    from app.api.auth import _parse_identifier

    try:
        kind, value = _parse_identifier(identifier or "")
    except HTTPException:
        return None
    column = User.mobile if kind == "mobile" else User.email
    return db.query(User).filter(column == value, User.role.in_(RESET_ROLES), User.is_active == 1).first()


def account_for(db: Session, email: str) -> Optional[User]:
    email = (email or "").strip().lower()
    if not email:
        return None
    return db.query(User).filter(User.email == email, User.role.in_(RESET_ROLES), User.is_active == 1).first()


def request_code(db: Session, user: User) -> bool:
    """Emails a new code (unless one was sent moments ago / too many this hour). True when sent."""
    from app.api.auth import hash_password

    now = datetime.utcnow()
    recent = (db.query(PasswordResetCode).filter(PasswordResetCode.user_id == user.id,
                                                 PasswordResetCode.created_at >= now - timedelta(hours=1))
              .order_by(PasswordResetCode.created_at.desc()).all())
    if recent and (now - recent[0].created_at < RESEND_AFTER or len(recent) >= MAX_PER_HOUR):
        return False
    code = f"{secrets.randbelow(1_000_000):06d}"
    db.add(PasswordResetCode(user_id=user.id, code_hash=hash_password(code), created_at=now, expires_at=now + CODE_TTL))
    db.commit()
    mailer.send_mail(
        user.email,
        "Your FMCG Insights password code",
        f"Hello {user.name},\n\nYour code to set a new password is: {code}\n\n"
        f"It works for 15 minutes. If you didn't ask for it, ignore this email: your password stays the same.\n",
    )
    return True


def check_code(db: Session, user: User, code: str) -> PasswordResetCode:
    """The matching, still valid code (raises ResetError otherwise). Wrong tries are counted."""
    from app.api.auth import verify_password

    latest = (db.query(PasswordResetCode).filter(PasswordResetCode.user_id == user.id, PasswordResetCode.used_at.is_(None))
              .order_by(PasswordResetCode.created_at.desc()).first())
    if latest is None or latest.expires_at < datetime.utcnow():
        raise ResetError("The code is wrong or has expired. Ask for a new code.")
    if latest.attempts >= MAX_TRIES:
        raise ResetError("Too many wrong tries. Ask for a new code.")
    if not verify_password((code or "").strip(), latest.code_hash):
        latest.attempts += 1
        db.commit()
        raise ResetError("The code is wrong or has expired. Ask for a new code.")
    return latest


def use_code(db: Session, row: PasswordResetCode) -> None:
    row.used_at = datetime.utcnow()
