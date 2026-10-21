"""
Accounts for survey executives (mobile app).

POST /auth/signup   {name, identifier, password}   -> {token, user}
POST /auth/login    {identifier, password}         -> {token, user}
GET  /auth/me       Authorization: Bearer <token>  -> {user, token?}  (token: renewed one, when due)
POST /auth/logout   ends this token
POST /auth/logout-all  ends every token of this account (lost phone)

identifier = a 10-digit mobile number or an email address.

Only the Python standard library is used:
  - passwords: PBKDF2-HMAC-SHA256 with a random salt (never stored in plain text),
    600,000 rounds as OWASP recommends; older, cheaper hashes are upgraded at the
    next successful login
  - tokens: a signed, expiring token (HMAC-SHA256). Survey persons stay signed in
    for FMCG_TOKEN_DAYS (default 7), admins for FMCG_ADMIN_TOKEN_HOURS (default 24).
    GET /auth/me hands out a fresh token once the current one is a day old, so
    people who use the app regularly are not asked to sign in again.
  - sign-out ends the token on the server (POST /auth/logout: this device;
    POST /auth/logout-all: every device). A password reset, a disabled account
    or a role change signs the account out everywhere (users.token_version).
  - repeated wrong passwords are slowed down: after 5 failures for one account
    (or 30 from one address) in 15 minutes, login waits (HTTP 429)

The signing key comes from $env:FMCG_SECRET_KEY, or is generated once and kept
in backend/.secret_key (keep that file private; deleting it signs everyone out).

Register in app/main.py:
    from app.api.auth import router as auth_router
    app.include_router(auth_router)
"""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.database.models import LoginFailure, RevokedToken, SurveyResponse, User
from app.services import audit

router = APIRouter(prefix="/auth", tags=["Accounts"])

# How long a sign-in lasts. Shorter for admins, whose accounts can change everything.
TOKEN_DAYS = float(os.environ.get("FMCG_TOKEN_DAYS", "") or 7)
ADMIN_TOKEN_HOURS = float(os.environ.get("FMCG_ADMIN_TOKEN_HOURS", "") or 24)
# /auth/me renews a token that is older than this (seconds).
RENEW_AFTER_SECONDS = int(float(os.environ.get("FMCG_TOKEN_RENEW_HOURS", "") or 24) * 3600)

# Accounts are created by admins (Team page). Self sign-up only if enabled:
#   $env:FMCG_ALLOW_SIGNUP = "1"
ALLOW_SIGNUP = os.environ.get("FMCG_ALLOW_SIGNUP", "").strip().lower() in ("1", "true", "yes")
# OWASP Password Storage Cheat Sheet: PBKDF2-HMAC-SHA256 with 600,000 iterations.
DEFAULT_PBKDF2_ROUNDS = 600_000
# Lower only for automated tests (FMCG_PBKDF2_ROUNDS); never lower in production.
PBKDF2_ROUNDS = int(os.environ.get("FMCG_PBKDF2_ROUNDS", "") or DEFAULT_PBKDF2_ROUNDS)

# Login throttling (counted in the login_failures table, so it holds across processes).
FAILURE_WINDOW = timedelta(minutes=15)
MAX_FAILURES_PER_ACCOUNT = 5
MAX_FAILURES_PER_ADDRESS = 30
# Behind the HTTPS proxy (Docker Compose) the client address arrives in X-Forwarded-For.
TRUST_PROXY = os.environ.get("FMCG_TRUST_PROXY", "").strip().lower() in ("1", "true", "yes")

# ============================================================
# Secret key, passwords, tokens
# ============================================================

_KEY_FILE = Path(__file__).resolve().parents[2] / ".secret_key"


def _secret() -> bytes:
    env = os.environ.get("FMCG_SECRET_KEY", "").strip()
    if env:
        return env.encode()
    if not _KEY_FILE.exists():
        _KEY_FILE.write_text(secrets.token_hex(32))
    return _KEY_FILE.read_text().strip().encode()


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF2_ROUNDS).hex()
    return f"pbkdf2_sha256${PBKDF2_ROUNDS}${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, rounds, salt, digest = stored.split("$")
        check = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(rounds)).hex()
        return hmac.compare_digest(check, digest)
    except Exception:
        return False


def needs_rehash(stored: str) -> bool:
    """True for hashes made with fewer rounds than today's setting."""
    try:
        return int(stored.split("$")[1]) < PBKDF2_ROUNDS
    except (IndexError, ValueError):
        return True


# Checked when the account doesn't exist, so a wrong mobile number takes as long
# as a wrong password and response times don't reveal which accounts exist.
_DUMMY_HASH = hash_password(secrets.token_hex(16))


# ------------------------------------------------------------
# Login throttling
# ------------------------------------------------------------

def client_address(request: Request) -> str:
    if TRUST_PROXY:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()[:100]
    return (request.client.host if request.client else "unknown")[:100]


def _recent_failures(db: Session, key: str) -> tuple[int, Optional[datetime]]:
    since = datetime.utcnow() - FAILURE_WINDOW
    q = db.query(LoginFailure).filter(LoginFailure.key == key, LoginFailure.created_at >= since)
    oldest = q.order_by(LoginFailure.created_at.asc()).with_entities(LoginFailure.created_at).first()
    return q.count(), (oldest[0] if oldest else None)


def check_login_allowed(db: Session, identifier_key: str, address: str):
    """Refuse (429) while too many recent failures exist for this account or address."""
    for key, limit in ((identifier_key, MAX_FAILURES_PER_ACCOUNT), (f"ip:{address}", MAX_FAILURES_PER_ADDRESS)):
        count, oldest = _recent_failures(db, key)
        if count >= limit:
            wait = int(((oldest + FAILURE_WINDOW) - datetime.utcnow()).total_seconds()) + 1 if oldest else 60
            raise HTTPException(
                status_code=429,
                detail=f"Too many wrong attempts. Try again in {max(1, round(wait / 60))} minute(s).",
                headers={"Retry-After": str(max(1, wait))},
            )


def record_login_failure(db: Session, identifier_key: str, address: str):
    now = datetime.utcnow()
    db.add_all([LoginFailure(key=identifier_key, created_at=now), LoginFailure(key=f"ip:{address}", created_at=now)])
    # Housekeeping: keep the table small.
    db.query(LoginFailure).filter(LoginFailure.created_at < now - timedelta(days=1)).delete(synchronize_session=False)
    db.commit()


def clear_login_failures(db: Session, identifier_key: str):
    db.query(LoginFailure).filter(LoginFailure.key == identifier_key).delete(synchronize_session=False)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def token_lifetime_seconds(user: User) -> int:
    if user.role == "admin":
        return int(ADMIN_TOKEN_HOURS * 3600)
    return int(TOKEN_DAYS * 86400)


def make_token(user: User) -> str:
    now = int(time.time())
    claims = {
        "uid": user.id,
        "iat": now,
        "exp": now + token_lifetime_seconds(user),
        "ver": int(user.token_version or 0),
        # Random part: two sign-ins in the same second still get different tokens,
        # so signing out on one device never ends the other.
        "jti": secrets.token_urlsafe(9),
    }
    payload = _b64(json.dumps(claims, separators=(",", ":")).encode())
    signature = _b64(hmac.new(_secret(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{signature}"


def read_claims(token: str) -> Optional[dict]:
    """The token's contents if its signature is valid and it hasn't expired; otherwise None."""
    try:
        payload, signature = token.split(".")
        expected = _b64(hmac.new(_secret(), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return None
        data = json.loads(_unb64(payload))
        if int(data.get("exp", 0)) < time.time():
            return None
        data["uid"] = int(data["uid"])
        return data
    except Exception:
        return None


def read_token(token: str) -> Optional[int]:
    """User id from a valid, unexpired token; otherwise None (signature and expiry only)."""
    claims = read_claims(token)
    return claims["uid"] if claims else None


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def user_for_token(db: Session, token: Optional[str]) -> Optional[User]:
    """
    The account a token belongs to, or None when the token is invalid, expired,
    signed out (POST /auth/logout), issued before the account was signed out
    everywhere, or the account is disabled. Every sign-in check goes through here.
    """
    claims = read_claims(token) if token else None
    if not claims:
        return None
    user = db.get(User, claims["uid"])
    if not user or not user.is_active:
        return None
    if int(claims.get("ver", 0)) != int(user.token_version or 0):
        return None
    if db.get(RevokedToken, token_hash(token)) is not None:
        return None
    return user


def revoke_token(db: Session, token: str) -> None:
    """End one token (this device) until it would have expired anyway."""
    claims = read_claims(token)
    if not claims:
        return
    key = token_hash(token)
    if db.get(RevokedToken, key) is None:
        db.add(RevokedToken(token_hash=key, user_id=claims["uid"], expires_at=datetime.utcfromtimestamp(int(claims["exp"]))))
        db.commit()


def sign_out_everywhere(user: User) -> None:
    """Every token issued so far for this account stops working (caller commits)."""
    user.token_version = int(user.token_version or 0) + 1


def _bearer(authorization: Optional[str]) -> str:
    return (authorization or "").removeprefix("Bearer ").strip()


def optional_user(authorization: Optional[str] = Header(default=None), db: Session = Depends(get_db)) -> Optional[User]:
    """The signed-in user if a valid token was sent, otherwise None (e.g. the web app)."""
    return user_for_token(db, _bearer(authorization))


def can_view_history(user: User) -> bool:
    return user.role == "admin" or bool(user.can_view_history)


def history_guard(user: Optional[User] = Depends(optional_user)):
    """
    Protects feedback content (history, reports). An executive's app is refused
    unless an admin enabled history for them. Requests without a token (the web
    app, until it gets its own admin login) are allowed.
    """
    if user is not None and not can_view_history(user):
        raise HTTPException(status_code=403, detail="Survey history isn't enabled for your account. Ask your admin.")


def admin_guard(user: Optional[User] = Depends(optional_user)):
    """
    Company-wide data (product reports, other people's surveys): admins only.
    Requests without a token (the web app, until it gets its own login) are allowed.
    """
    if user is not None and user.role != "admin":
        raise HTTPException(status_code=403, detail="Only admins can see company-wide feedback.")


def current_user(authorization: Optional[str] = Header(default=None), db: Session = Depends(get_db)) -> User:
    """Use as a dependency on endpoints that need a signed-in user."""
    user = user_for_token(db, _bearer(authorization))
    if not user:
        raise HTTPException(status_code=401, detail="Please sign in again.")
    return user


# ============================================================
# Validation
# ============================================================

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _parse_identifier(identifier: str):
    """('mobile', '9876543210') or ('email', 'a@b.com'), or raise a clear 400."""
    text = (identifier or "").strip()
    if "@" in text:
        if not EMAIL_RE.match(text):
            raise HTTPException(status_code=400, detail="Enter a valid email address.")
        return "email", text.lower()
    digits = re.sub(r"\D", "", text)
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]  # +91 98765 43210
    if len(digits) != 10:
        raise HTTPException(status_code=400, detail="Enter a 10-digit mobile number or an email address.")
    return "mobile", digits


def _user_out(user: User) -> dict:
    return {
        "id": user.id,
        "name": user.name,
        "mobile": user.mobile,
        "email": user.email,
        "role": user.role,
        "is_active": bool(user.is_active),
        "age": user.age,
        "gender": user.gender,
        "can_view_history": can_view_history(user),
        # True until the person chooses their own password (organization's first sign-in).
        "must_change_password": bool(getattr(user, "must_change_password", 0)),
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }


def surveys_of(user: User):
    """
    SQL condition for this user's surveys: the surveys linked to the account.

    Surveys are no longer matched by executive name (two people with the same
    name would see each other's surveys). Older unlinked surveys are linked once,
    only when the name is unique (database/migrations.py, link_earlier_surveys).
    """
    return SurveyResponse.user_id == user.id


def survey_counts(db: Session, condition, tz_offset_minutes: int = 330) -> dict:
    """Total, today (in the phone's time zone), last 7 days and analyzed, for one condition."""
    now_local = datetime.utcnow() + timedelta(minutes=tz_offset_minutes)
    today_start_utc = datetime(now_local.year, now_local.month, now_local.day) - timedelta(minutes=tz_offset_minutes)
    week_start_utc = today_start_utc - timedelta(days=6)
    q = db.query(SurveyResponse).filter(condition)
    last = q.order_by(SurveyResponse.created_at.desc()).with_entities(SurveyResponse.created_at).first()
    return {
        "total": q.count(),
        "today": q.filter(SurveyResponse.created_at >= today_start_utc).count(),
        "this_week": q.filter(SurveyResponse.created_at >= week_start_utc).count(),
        "analyzed": q.filter(SurveyResponse.status == "analyzed").count(),
        "last_survey_at": last[0].isoformat() if last and last[0] else None,
    }


class SignupIn(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    identifier: str = Field(min_length=3, max_length=190)
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    identifier: str = Field(min_length=3, max_length=190)
    password: str = Field(min_length=1, max_length=128)


# ============================================================
# Endpoints
# ============================================================

def check_password(password: str):
    if len(password) < 8 or not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise HTTPException(status_code=400, detail="Use at least 8 characters with letters and numbers.")


def link_earlier_surveys(db: Session, user: User) -> int:
    """
    Surveys done under this name before the account existed become this account's,
    but only when no other account has the same name (otherwise an admin decides).
    """
    if user.role == "client":
        return 0  # client logins (brands) never take surveys
    wanted = " ".join(user.name.split()).lower()
    same_name = [
        uid for uid, name in db.query(User.id, User.name).filter(User.role != "client").all()
        if " ".join((name or "").split()).lower() == wanted
    ]
    if same_name != [user.id]:
        return 0
    candidates = [
        sid
        for sid, name in db.query(SurveyResponse.id, SurveyResponse.executive_name)
        .filter(SurveyResponse.user_id.is_(None), SurveyResponse.executive_name.isnot(None))
        .all()
        if " ".join(name.split()).lower() == wanted
    ]
    if not candidates:
        return 0
    linked = (
        db.query(SurveyResponse)
        .filter(SurveyResponse.id.in_(candidates), SurveyResponse.user_id.is_(None))
        .update({SurveyResponse.user_id: user.id}, synchronize_session=False)
    )
    db.commit()
    return linked


@router.get("/setup-status")
def setup_status(db: Session = Depends(get_db)):
    """The web shows 'Create the admin account' until the first admin exists."""
    has_admin = db.query(User).filter(User.role == "admin").first() is not None
    return {"needs_setup": not has_admin, "signup_open": ALLOW_SIGNUP}


@router.post("/setup")
def first_admin(body: SignupIn, request: Request, db: Session = Depends(get_db)):
    """Create the first admin account. Refused once any admin exists."""
    if db.query(User).filter(User.role == "admin").first():
        raise HTTPException(status_code=403, detail="An admin account already exists. Sign in instead.")
    kind, value = _parse_identifier(body.identifier)
    check_password(body.password)
    existing = db.query(User).filter((User.mobile if kind == "mobile" else User.email) == value).first()
    if existing:
        # An existing executive becomes the admin (they already know their password).
        key, address = f"id:{value}", client_address(request)
        check_login_allowed(db, key, address)
        if not verify_password(body.password, existing.password_hash):
            record_login_failure(db, key, address)
            raise HTTPException(status_code=409, detail="This mobile/email already has an account with a different password.")
        user = existing
        user.role = "admin"
    else:
        user = User(name=" ".join(body.name.split()), password_hash=hash_password(body.password), role="admin")
        setattr(user, kind, value)
        db.add(user)
    user.can_view_history = 1
    user.is_active = 1
    user.last_login_at = datetime.utcnow()
    db.commit()
    db.refresh(user)
    link_earlier_surveys(db, user)
    audit.record_from_request(request, action="auth.setup", account=_account_of(user), target_type="user",
                              target_id=user.id, summary="Created the first admin account")
    return {"token": make_token(user), "user": _user_out(user)}


@router.post("/signup")
def signup(body: SignupIn, db: Session = Depends(get_db)):
    if not ALLOW_SIGNUP:
        raise HTTPException(status_code=403, detail="Accounts are created by your admin. Ask them for your login details.")
    kind, value = _parse_identifier(body.identifier)
    name = " ".join(body.name.split())
    if len(name) < 2:
        raise HTTPException(status_code=400, detail="Enter your full name.")
    check_password(body.password)

    column = User.mobile if kind == "mobile" else User.email
    if db.query(User).filter(column == value).first():
        raise HTTPException(
            status_code=409,
            detail=f"An account with this {'mobile number' if kind == 'mobile' else 'email'} already exists. Log in instead.",
        )

    user = User(name=name, password_hash=hash_password(body.password), role="executive", last_login_at=datetime.utcnow())
    setattr(user, kind, value)
    db.add(user)
    db.commit()
    db.refresh(user)

    # Surveys done under this name before the account existed become this account's,
    # permanently (so a later account with the same name can't see them).
    linked = link_earlier_surveys(db, user)
    return {"token": make_token(user), "user": _user_out(user), "linked_surveys": linked}


def _account_of(user: User) -> dict:
    return {"id": user.id, "name": user.name, "role": user.role}


def _masked(value: str) -> str:
    """98******10 / an***@example.com: enough to recognise, not to reuse."""
    if "@" in value:
        name, _, domain = value.partition("@")
        return f"{name[:2]}***@{domain}"
    return f"{value[:2]}{'*' * max(len(value) - 4, 0)}{value[-2:]}"


@router.post("/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    kind, value = _parse_identifier(body.identifier)
    key, address = f"id:{value}", client_address(request)
    check_login_allowed(db, key, address)
    column = User.mobile if kind == "mobile" else User.email
    user = db.query(User).filter(column == value).first()
    # Same message (and about the same time) whether the account exists or not.
    valid = verify_password(body.password, user.password_hash if user else _DUMMY_HASH)
    if not user or not valid:
        record_login_failure(db, key, address)
        audit.record_from_request(
            request, action="auth.login_failed", account={}, status=401,
            target_type="user" if user else None, target_id=user.id if user else None,
            summary=f"Wrong password or unknown account ({_masked(value)})",
        )
        raise HTTPException(status_code=401, detail="The mobile/email or password is incorrect.")
    if not user.is_active:
        audit.record_from_request(request, action="auth.login_refused", account=_account_of(user), status=403,
                                  target_type="user", target_id=user.id, summary="Sign-in refused: account disabled")
        raise HTTPException(status_code=403, detail="This account has been disabled. Contact your admin.")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)  # upgrade to today's hashing cost
    clear_login_failures(db, key)
    user.last_login_at = datetime.utcnow()
    db.commit()
    audit.record_from_request(request, action="auth.login", account=_account_of(user), target_type="user",
                              target_id=user.id, summary="Signed in")
    return {"token": make_token(user), "user": _user_out(user)}


@router.post("/logout")
def logout(request: Request, authorization: Optional[str] = Header(default=None), db: Session = Depends(get_db)):
    """Sign out on this device: the token stops working at once, not when it expires."""
    token = _bearer(authorization)
    user = user_for_token(db, token)
    if user is not None:
        revoke_token(db, token)
        audit.record_from_request(request, action="auth.logout", account=_account_of(user), target_type="user",
                                  target_id=user.id, summary="Signed out")
    return {"ok": True}  # also fine for a token that had already ended


@router.post("/logout-all")
def logout_all(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Sign out on every device (e.g. a lost phone). This device must sign in again too."""
    sign_out_everywhere(user)
    db.commit()
    audit.record_from_request(request, action="auth.logout_all", account=_account_of(user), target_type="user",
                              target_id=user.id, summary="Signed out of every device")
    return {"ok": True}


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


@router.post("/change-password")
def change_password(body: ChangePasswordIn, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """
    Choose a new password (8+ characters with letters and numbers). Every other
    sign-in of this account ends; this device gets a new token and stays signed in.
    Used by the organization at its first sign-in and on its Account page.
    """
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="The current password is incorrect.")
    check_password(body.new_password)
    if verify_password(body.new_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Choose a password different from the current one.")
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = 0
    sign_out_everywhere(user)  # other devices (and anyone with the old password) are signed out
    db.commit()
    db.refresh(user)
    audit.record_from_request(request, action="auth.password_changed", account=_account_of(user), target_type="user",
                              target_id=user.id, summary="Changed own password (signed out of other devices)")
    return {"token": make_token(user), "user": _user_out(user)}


@router.get("/me")
def me(authorization: Optional[str] = Header(default=None), user: User = Depends(current_user)):
    """
    The account's latest details. When the token is more than a day old (or was
    issued by an older version without a date), a fresh token is included as
    "token": apps that store it stay signed in while they are being used.
    """
    out = {"user": _user_out(user)}
    claims = read_claims(_bearer(authorization)) or {}
    if time.time() - int(claims.get("iat", 0)) >= RENEW_AFTER_SECONDS:
        out["token"] = make_token(user)
    return out


@router.get("/me/stats")
def my_stats(
    tz_offset: int = Query(default=330, ge=-720, le=840, description="Minutes ahead of UTC (India = 330)"),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
):
    """Only this executive's own surveys: a new executive sees 0."""
    return survey_counts(db, surveys_of(user), tz_offset)


class SurveyIdsIn(BaseModel):
    ids: list[int] = Field(default_factory=list, max_length=500)


@router.post("/me/surveys/existing")
def existing_surveys(body: SurveyIdsIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Which of these surveys (done on this phone) still exist? Deleted ones disappear from the app."""
    if not body.ids:
        return {"existing": []}
    rows = db.query(SurveyResponse.id).filter(SurveyResponse.id.in_(body.ids)).all()
    return {"existing": [r[0] for r in rows]}


@router.get("/me/surveys")
def my_surveys(limit: int = Query(default=50, ge=1, le=200), user: User = Depends(current_user), db: Session = Depends(get_db)):
    """
    The signed-in person's own surveys (web Analyze page, mobile History).
    Includes ratings and summaries: it's their own work.
    """
    from app.api.product_feedback import recent_surveys  # avoids a circular import

    data = recent_surveys(executive=None, product_id=None, limit=limit, offset=0, user=user, db=db)
    # Already limited to this person's own surveys (admins: all). Everyone sees the
    # analysis of the surveys THEY took; "Survey history" only controls the mobile History tab.
    data["can_view_history"] = True
    return data


@router.get("/me/surveys/{response_id}")
def my_survey(response_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """
    One of the signed-in person's own surveys: details, the customer's answers,
    the recording, and its full analysis.
    """
    from app.api.product_feedback import _short_name
    from app.database.models import Category, FeedbackRecord, Product, Respondent, SurveyAnswer, SurveyQuestion
    from app.services.consolidation import response_tone

    survey = db.get(SurveyResponse, response_id)
    mine = survey is not None and (
        user.role == "admin" or db.query(SurveyResponse.id).filter(SurveyResponse.id == response_id, surveys_of(user)).first()
    )
    if not mine:
        raise HTTPException(status_code=404, detail="Survey not found.")

    product = db.get(Product, survey.product_id)
    category = db.get(Category, product.category_id) if product else None
    person = db.get(Respondent, survey.respondent_id) if survey.respondent_id else None
    positions = {q.id: q.position for q in db.query(SurveyQuestion).filter(SurveyQuestion.product_id == survey.product_id).all()}
    def answers_of(rid):
        return sorted(
            db.query(SurveyAnswer).filter(SurveyAnswer.response_id == rid).all(),
            key=lambda a: (positions.get(a.question_id, 999), a.id),
        )

    answers = answers_of(response_id)
    answers_from = response_id
    if not answers and survey.respondent_id:
        # A retried web survey can leave the answers on a sibling record (same customer
        # and product) while the recording's analysis is linked to this one.
        sibling = (
            db.query(SurveyResponse)
            .filter(
                SurveyResponse.respondent_id == survey.respondent_id,
                SurveyResponse.product_id == survey.product_id,
                SurveyResponse.id != response_id,
            )
            .order_by(SurveyResponse.id.desc())
            .all()
        )
        for sib in sibling:
            found = answers_of(sib.id)
            if found:
                answers, answers_from = found, sib.id
                print(f"My survey {response_id}: answers found on sibling survey {sib.id} (retried survey).")
                break
    # The person who took the survey always sees its analysis.
    allowed = True
    record = (
        db.query(FeedbackRecord).filter(FeedbackRecord.response_id == response_id).order_by(FeedbackRecord.id.desc()).first()
    )

    feedback = None
    if record:
        feedback = {
            "id": record.id,
            "duration_seconds": record.duration_seconds,
            "uploaded_at": record.uploaded_at.isoformat() if record.uploaded_at else None,
            "filename": record.original_filename,
            "is_video": str(record.original_filename or "").lower().endswith((".mp4", ".mov", ".avi", ".mkv", ".webm")),
        }
        if allowed:
            feedback.update(_analysis_payload(record, response_tone))

    from app.database.models import SurveyRound

    survey_round = db.get(SurveyRound, survey.round_id) if getattr(survey, "round_id", None) else None
    return {
        "response_id": survey.id,
        # Which round and recipe version this survey tested ("Round 2 · recipe v2").
        "round": survey_round.number if survey_round else None,
        "recipe_version": survey_round.recipe_version if survey_round else None,
        "status": survey.status,
        "created_at": survey.created_at.isoformat() if survey.created_at else None,
        "product_name": product.name if product else None,
        "category_name": category.name if category else None,
        "respondent_label": _short_name(person.name if person else None),
        "area": person.location if person else None,
        "survey_location": survey.location,
        "usage_period": survey.usage_period,
        "can_view_history": allowed,
        "answers": [
            {
                "question": a.question_text,
                "type": a.question_type,
                "value": a.value,
                "value_en": a.value_en if a.value_en and a.value_en.strip() != (a.value or "").strip() else None,
            }
            for a in answers
        ],
        "answers_from_response": answers_from if answers_from != response_id else None,
        "feedback": feedback,
    }


def _as_json(value):
    """Columns may hold JSON as text depending on the database driver."""
    if isinstance(value, str):
        t = value.strip()
        if t[:1] in "[{":
            try:
                import json

                return json.loads(t)
            except ValueError:
                return value
    return value


def _analysis_payload(record, response_tone) -> dict:
    """
    Everything the analysis produced, whatever format it was stored in
    (older and newer analyses store transcripts and details differently).
    """
    chapters = _as_json(record.chapters) or []
    if not isinstance(chapters, list):
        chapters = []
    parts = []
    for c in chapters:
        if not isinstance(c, dict):
            continue
        items = _as_json(c.get("feedback")) or []
        parts.append({
            "part": c.get("part"),
            "start": c.get("start"),
            "end": c.get("end"),
            "sentiment": c.get("sentiment"),
            "summary": c.get("summary"),
            "key_point": c.get("key_point") or c.get("keyPoint"),
            "transcript": c.get("transcript"),
            "original_transcript": c.get("original_transcript") or c.get("original_text"),
            "feedback": [
                {
                    "aspect": i.get("aspect_name") or i.get("aspect") or i.get("aspect_key") or "General",
                    "category": i.get("category"),
                    "rating": i.get("rating"),
                    "sentiment": i.get("sentiment"),
                    "severity": i.get("severity"),
                    "evidence": i.get("evidence"),
                    "mentions": i.get("mentions") or 1,
                }
                for i in items
                if isinstance(i, dict)
            ],
        })

    # Transcript: the record's own, else joined from the parts.
    transcript = _as_json(record.transcript)
    if not transcript:
        joined = "\n".join(p["transcript"] for p in parts if p.get("transcript"))
        transcript = joined or None
    original = getattr(record, "original_transcript", None) or "\n".join(
        p["original_transcript"] for p in parts if p.get("original_transcript")
    ) or None

    # Rating: the overall one, else the average of the aspect ratings.
    rating = record.overall_rating
    if not rating:
        rs = [float(f["rating"]) for p in parts for f in p["feedback"] if isinstance(f.get("rating"), (int, float)) and f["rating"] > 0]
        rating = round(sum(rs) / len(rs), 2) if rs else None
    summary = record.overall_summary or " ".join(p["summary"] for p in parts if p.get("summary")) or None

    return {
        "overall_rating": rating,
        "overall_sentiment": record.overall_sentiment,
        "tone": response_tone(record.overall_sentiment, chapters, rating),
        "summary": summary,
        "transcript": transcript,
        "original_transcript": original,
        "language": getattr(record, "language_name", None) or getattr(record, "language", None),
        "processing_time_seconds": getattr(record, "processing_time_seconds", None),
        "parts": parts,
    }


@router.post("/me/claim/{response_id}")
def claim_survey(response_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Links a survey the app just created to this account (called once per survey)."""
    survey = db.get(SurveyResponse, response_id)
    if not survey:
        raise HTTPException(status_code=404, detail="Survey not found.")
    if survey.user_id not in (None, user.id):
        raise HTTPException(status_code=403, detail="This survey belongs to another account.")
    if survey.executive_name and survey.executive_name.strip().lower() != user.name.lower():
        raise HTTPException(status_code=403, detail="This survey was done by another executive.")
    survey.user_id = user.id
    survey.executive_name = survey.executive_name or user.name
    db.commit()
    return {"ok": True, "response_id": survey.id}
