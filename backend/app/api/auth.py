"""
Accounts for survey executives (mobile app).

POST /auth/signup   {name, identifier, password}   -> {token, user}
POST /auth/login    {identifier, password}         -> {token, user}
GET  /auth/me       Authorization: Bearer <token>  -> {user}

identifier = a 10-digit mobile number or an email address.

Only the Python standard library is used:
  - passwords: PBKDF2-HMAC-SHA256 with a random salt (never stored in plain text)
  - tokens: a signed, expiring token (HMAC-SHA256), valid for 30 days

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

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.database.models import SurveyResponse, User

router = APIRouter(prefix="/auth", tags=["Accounts"])

TOKEN_DAYS = 30

# Accounts are created by admins (Team page). Self sign-up only if enabled:
#   $env:FMCG_ALLOW_SIGNUP = "1"
ALLOW_SIGNUP = os.environ.get("FMCG_ALLOW_SIGNUP", "").strip().lower() in ("1", "true", "yes")
PBKDF2_ROUNDS = 200_000

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


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_token(user: User) -> str:
    payload = _b64(json.dumps({"uid": user.id, "exp": int(time.time()) + TOKEN_DAYS * 86400}).encode())
    signature = _b64(hmac.new(_secret(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{signature}"


def read_token(token: str) -> Optional[int]:
    """User id from a valid, unexpired token; otherwise None."""
    try:
        payload, signature = token.split(".")
        expected = _b64(hmac.new(_secret(), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return None
        data = json.loads(_unb64(payload))
        if int(data.get("exp", 0)) < time.time():
            return None
        return int(data["uid"])
    except Exception:
        return None


def optional_user(authorization: Optional[str] = Header(default=None), db: Session = Depends(get_db)) -> Optional[User]:
    """The signed-in user if a valid token was sent, otherwise None (e.g. the web app)."""
    token = (authorization or "").removeprefix("Bearer ").strip()
    uid = read_token(token) if token else None
    user = db.get(User, uid) if uid else None
    return user if user and user.is_active else None


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
    token = (authorization or "").removeprefix("Bearer ").strip()
    uid = read_token(token) if token else None
    user = db.get(User, uid) if uid else None
    if not user or not user.is_active:
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
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None,
    }


def surveys_of(user: User):
    """SQL condition for this user's surveys: linked to the account, or (older surveys) done under their name."""
    return or_(
        SurveyResponse.user_id == user.id,
        and_(
            SurveyResponse.user_id.is_(None),
            func.lower(func.trim(SurveyResponse.executive_name)) == " ".join(user.name.split()).lower(),
        ),
    )


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
    """Surveys done under this name before the account existed become this account's."""
    linked = (
        db.query(SurveyResponse)
        .filter(
            SurveyResponse.user_id.is_(None),
            func.lower(func.trim(SurveyResponse.executive_name)) == " ".join(user.name.split()).lower(),
        )
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
def first_admin(body: SignupIn, db: Session = Depends(get_db)):
    """Create the first admin account. Refused once any admin exists."""
    if db.query(User).filter(User.role == "admin").first():
        raise HTTPException(status_code=403, detail="An admin account already exists. Sign in instead.")
    kind, value = _parse_identifier(body.identifier)
    check_password(body.password)
    existing = db.query(User).filter((User.mobile if kind == "mobile" else User.email) == value).first()
    if existing:
        # An existing executive becomes the admin (they already know their password).
        if not verify_password(body.password, existing.password_hash):
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


@router.post("/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    kind, value = _parse_identifier(body.identifier)
    column = User.mobile if kind == "mobile" else User.email
    user = db.query(User).filter(column == value).first()
    # Same message whether the account exists or not.
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="The mobile/email or password is incorrect.")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="This account has been disabled. Contact your admin.")
    user.last_login_at = datetime.utcnow()
    db.commit()
    return {"token": make_token(user), "user": _user_out(user)}


@router.get("/me")
def me(user: User = Depends(current_user)):
    return {"user": _user_out(user)}


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

    return {
        "response_id": survey.id,
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