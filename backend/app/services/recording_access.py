"""
Who may play a consumer recording, and the recording passkey (services/recording_crypto.py
explains the encryption).

  Super Admin  sets / generates / rotates ONE recording passkey for the organization: the same
               passkey opens the recordings of every product. Per product, the Super Admin
               chooses the Admins allowed to play its recordings. The passkey can be sent to an
               Admin inside the app (encrypted, revealed to that Admin once, then deleted).
  Admin        plays a recording of a product they're allowed for, after entering the passkey.
               5 wrong passkeys lock playback for that Admin for 15 minutes.
  Survey person never: no passkey, no key, no playback.

Only the passkey's scrypt hash is stored. A correct passkey gives a playback token valid for
10 minutes, for one recording and one user; the stream decrypts on the server. Every attempt
(granted, wrong passkey, not allowed, locked, played) is written to recording_access_log.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.database.models import (
    FeedbackRecord,
    PasskeyShare,
    PlaybackToken,
    Product,
    ProductPasskey,
    RecordingAccessGrant,
    RecordingAccessLog,
    SurveyResponse,
    User,
)

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1
MIN_PASSKEY = 8
MAX_FAILURES = 5
LOCK_MINUTES = 15
TOKEN_MINUTES = 10
SHARE_DAYS = 7
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I: easy to read out
ORG = 0  # product_passkeys / passkey_shares rows with product_id 0: the organization's one passkey


class AccessError(Exception):
    def __init__(self, message: str, status: int = 403, outcome: str = "not_authorised"):
        super().__init__(message)
        self.status = status
        self.outcome = outcome


# ---------------------------------------------------------------- passkeys
def generate_passkey() -> str:
    """e.g. K7QM-9XPD-2RTF-H4WB (16 random characters, ~80 bits)."""
    raw = "".join(secrets.choice(_ALPHABET) for _ in range(16))
    return "-".join(raw[i:i + 4] for i in range(0, 16, 4))


def _normal(passkey: str) -> str:
    return "".join(str(passkey or "").split()).upper()


def hash_passkey(passkey: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(_normal(passkey).encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def passkey_matches(stored: str, passkey: str) -> bool:
    try:
        _, n, r, p, salt, digest = stored.split("$")
        mine = hashlib.scrypt(_normal(passkey).encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p),
                              dklen=len(base64.b64decode(digest)))
        return hmac.compare_digest(mine, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


def check_new_passkey(passkey: str) -> str:
    value = _normal(passkey)
    if len(value.replace("-", "")) < MIN_PASSKEY:
        raise AccessError(f"The passkey needs at least {MIN_PASSKEY} characters.", 400, "invalid")
    if len(value) > 64:
        raise AccessError("The passkey can have at most 64 characters.", 400, "invalid")
    return value


def org_passkey(db: Session) -> Optional[ProductPasskey]:
    """The organization's recording passkey (one for all products), or None."""
    return db.query(ProductPasskey).filter(ProductPasskey.product_id == ORG).first()


def set_passkey(db: Session, passkey: str, who: dict) -> ProductPasskey:
    """Sets (or rotates) the organization's passkey. Pending in-app shares of the old one are withdrawn."""
    value = check_new_passkey(passkey)
    row = org_passkey(db)
    if row is None:
        row = ProductPasskey(product_id=ORG, version=1)
        db.add(row)
    else:
        row.version = (row.version or 1) + 1
        db.query(PasskeyShare).filter(PasskeyShare.revealed_at.is_(None)).delete(synchronize_session=False)
    row.passkey_hash = hash_passkey(value)
    row.hint = value[-2:]
    row.set_by, row.set_by_name, row.set_at = who.get("id"), who.get("name"), datetime.utcnow()
    db.flush()
    return row


def _pending(db: Session) -> set:
    return {s.user_id for s in db.query(PasskeyShare).filter(PasskeyShare.revealed_at.is_(None),
                                                            PasskeyShare.expires_at > datetime.utcnow())}


def passkey_status(db: Session, product_id: Optional[int] = None) -> dict:
    """The organization's passkey (never the passkey itself); with a product, also its Admins."""
    row = org_passkey(db)
    out = {
        "scope": "organization",
        "has_passkey": row is not None,
        "hint": row.hint if row else None,
        "version": row.version if row else None,
        "set_at": row.set_at.isoformat(timespec="seconds") + "Z" if row else None,
        "set_by": row.set_by_name if row else None,
    }
    if product_id is None:
        # Every active Admin can be sent the passkey (they pass it on to survey persons).
        pending = _pending(db)
        users = db.query(User).filter(User.role == "admin", User.is_active == 1).order_by(User.name).all()
        out["admins"] = [{"user_id": u.id, "name": u.name, "share_pending": u.id in pending} for u in users]
    if product_id is not None:
        pending = _pending(db)
        grants = (db.query(RecordingAccessGrant, User).join(User, User.id == RecordingAccessGrant.user_id)
                  .filter(RecordingAccessGrant.product_id == product_id).order_by(User.name).all())
        out["admins"] = [{"user_id": u.id, "name": u.name, "email": u.email, "share_pending": u.id in pending,
                          "granted_at": g.granted_at.isoformat(timespec="seconds") + "Z"} for g, u in grants]
    return out


def set_admins(db: Session, product: Product, user_ids: list, who: dict) -> list:
    """The Admins allowed to play this product's recordings (replaces the list)."""
    wanted = set()
    for uid in user_ids or []:
        user = db.get(User, int(uid))
        if user is None or user.role != "admin" or not user.is_active:
            raise AccessError(f"#{uid} is not an active Admin.", 400, "invalid")
        wanted.add(user.id)
    current = {g.user_id: g for g in db.query(RecordingAccessGrant).filter(RecordingAccessGrant.product_id == product.id)}
    for uid, g in current.items():
        if uid not in wanted:
            db.delete(g)
    db.flush()
    for uid in wanted - set(current):
        db.add(RecordingAccessGrant(product_id=product.id, user_id=uid, granted_by=who.get("id"), granted_by_name=who.get("name")))
    # An Admin with no product left loses a passkey still waiting for them.
    left = {uid for (uid,) in db.query(RecordingAccessGrant.user_id)} | wanted
    for uid in set(current) - left:
        db.query(PasskeyShare).filter(PasskeyShare.user_id == uid).delete(synchronize_session=False)
    db.flush()
    return sorted(wanted - set(current))


def share_passkey(db: Session, passkey: str, user_ids: list, to_role: str = "admin") -> int:
    """Sends the organization's passkey inside the app: encrypted with the master key, revealed once
    by the recipient, gone after 7 days. The Super Admin sends it to Admins (to_role "admin"); an
    Admin passes it on to survey persons (to_role "executive"). It must be the current passkey."""
    from app.services.recording_crypto import seal_secret

    row = org_passkey(db)
    if row is None:
        raise AccessError("No recording passkey has been set yet. The Super Admin sets it on the Products page.", 409, "no_passkey")
    if not passkey_matches(row.passkey_hash, passkey):
        raise AccessError("That isn't the current recording passkey.", 400, "wrong_passkey")
    allowed = {uid for (uid,) in db.query(User.id).filter(User.role == to_role, User.is_active == 1)}
    made = 0
    for uid in {int(u) for u in user_ids or []}:
        if uid not in allowed:
            who = "an active Admin" if to_role == "admin" else "an active survey person"
            raise AccessError(f"#{uid} is not {who}.", 400, "invalid")
        db.query(PasskeyShare).filter(PasskeyShare.user_id == uid).delete(synchronize_session=False)
        db.add(PasskeyShare(product_id=ORG, user_id=uid, sealed=seal_secret(_normal(passkey)), passkey_version=row.version,
                            expires_at=datetime.utcnow() + timedelta(days=SHARE_DAYS)))
        made += 1
    db.flush()
    return made


def passkey_waiting(db: Session, user_id: int) -> bool:
    return user_id in _pending(db)


def reveal_share(db: Session, user: dict) -> str:
    """The passkey sent to this Admin, once: the sealed copy is deleted as it's shown."""
    from app.services.recording_crypto import open_secret

    share = (db.query(PasskeyShare).filter(PasskeyShare.user_id == user.get("id"), PasskeyShare.revealed_at.is_(None))
             .order_by(PasskeyShare.id.desc()).first())
    if share is None or share.sealed is None:
        raise AccessError("No passkey is waiting for you. Ask the Super Admin.", 404, "invalid")
    if share.expires_at < datetime.utcnow():
        db.delete(share)
        db.flush()
        raise AccessError("This passkey message has expired (7 days). Ask the Super Admin to send it again.", 410, "invalid")
    passkey = open_secret(share.sealed)
    share.sealed, share.revealed_at = None, datetime.utcnow()
    db.flush()
    return passkey


# ---------------------------------------------------------------- who may play what
def recording_of(db: Session, feedback_id: int):
    """(FeedbackRecord, product_id, response_id) of a recording."""
    rec = db.get(FeedbackRecord, feedback_id)
    if rec is None:
        raise AccessError("Recording not found.", 404, "not_found")
    product_id = None
    if rec.response_id:
        response = db.get(SurveyResponse, rec.response_id)
        product_id = response.product_id if response else None
    product_id = product_id or getattr(rec, "product_id", None)
    return rec, product_id, rec.response_id


def may_play(db: Session, user: dict, product_id: Optional[int], response_id: Optional[int] = None) -> None:
    """Role rule: the Super Admin and Admins play any recording (no passkey); a survey person plays
    only the recordings of their own surveys, and only with the passkey (checked in authorise_playback)."""
    role = user.get("role")
    if role in ("client", "admin"):
        return
    if role == "executive":
        response = db.get(SurveyResponse, response_id) if response_id else None
        if response is not None and response.user_id == user.get("id"):
            return
        raise AccessError("You can play only the recordings of your own surveys.", 403, "not_authorised")
    raise AccessError("You can't play consumer recordings.", 403, "not_authorised")


def _failures(db: Session, user_id: int) -> int:
    """Wrong passkeys by this user in the last 15 minutes (one passkey, so across all products)."""
    since = datetime.utcnow() - timedelta(minutes=LOCK_MINUTES)
    return (db.query(RecordingAccessLog).filter(RecordingAccessLog.user_id == user_id,
                                                RecordingAccessLog.outcome == "wrong_passkey", RecordingAccessLog.at >= since).count())


def log(db: Session, user: dict, outcome: str, product_id=None, feedback_id=None, response_id=None, ip=None) -> None:
    db.add(RecordingAccessLog(user_id=user.get("id"), user_name=user.get("name"), role=user.get("role"), product_id=product_id,
                              feedback_id=feedback_id, response_id=response_id, outcome=outcome, ip=(ip or "")[:64] or None))
    db.commit()


def authorise_playback(db: Session, user: dict, feedback_id: int, passkey: Optional[str] = None, ip: Optional[str] = None) -> dict:
    """Super Admin and Admins get a playback token straight away (no passkey); survey persons never.
    Every attempt is logged."""
    rec, product_id, response_id = recording_of(db, feedback_id)
    try:
        may_play(db, user, product_id, response_id)
        if user.get("role") == "executive":  # survey persons: only with the passkey from their Admin
            if _failures(db, user.get("id")) >= MAX_FAILURES:
                raise AccessError(f"Too many wrong passkeys. Try again in {LOCK_MINUTES} minutes.", 429, "locked")
            row = org_passkey(db)
            if row is None:
                raise AccessError("No recording passkey has been set yet. Ask your Admin.", 409, "no_passkey")
            if not passkey or not passkey_matches(row.passkey_hash, passkey):
                left = MAX_FAILURES - _failures(db, user.get("id")) - 1
                raise AccessError("Wrong passkey." + (f" {left} tries left before a {LOCK_MINUTES}-minute lock." if left > 0 else
                                                      f" Locked for {LOCK_MINUTES} minutes."), 403, "wrong_passkey")
    except AccessError as e:
        log(db, user, e.outcome, product_id, feedback_id, response_id, ip)
        raise
    token = secrets.token_urlsafe(32)
    db.add(PlaybackToken(token_hash=hashlib.sha256(token.encode()).hexdigest(), user_id=user.get("id"), feedback_id=feedback_id,
                         expires_at=datetime.utcnow() + timedelta(minutes=TOKEN_MINUTES)))
    db.query(PlaybackToken).filter(PlaybackToken.expires_at < datetime.utcnow()).delete(synchronize_session=False)
    log(db, user, "granted", product_id, feedback_id, response_id, ip)
    return {"token": token, "expires_in": TOKEN_MINUTES * 60, "kind": "video" if _is_video(rec) else "audio"}


def _is_video(rec: FeedbackRecord) -> bool:
    name = (getattr(rec, "original_filename", "") or "").lower()
    return not name.endswith((".m4a", ".mp3", ".wav", ".aac", ".ogg", ".opus", ".webm.audio"))


def token_user(db: Session, token: str, feedback_id: int) -> int:
    """The user a valid playback token belongs to (for this recording only)."""
    row = db.query(PlaybackToken).filter(PlaybackToken.token_hash == hashlib.sha256((token or "").encode()).hexdigest()).first()
    if row is None or row.feedback_id != feedback_id or row.expires_at < datetime.utcnow():
        raise AccessError("This playback link has expired. Enter the passkey again.", 403, "expired")
    return row.user_id


def access_history(db: Session, product_id: Optional[int] = None, feedback_id: Optional[int] = None, limit: int = 100) -> list:
    q = db.query(RecordingAccessLog)
    if product_id is not None:
        q = q.filter(RecordingAccessLog.product_id == product_id)
    if feedback_id is not None:
        q = q.filter(RecordingAccessLog.feedback_id == feedback_id)
    return [{"at": r.at.isoformat(timespec="seconds") + "Z", "user": r.user_name, "role": r.role, "outcome": r.outcome,
             "feedback_id": r.feedback_id, "response_id": r.response_id}
            for r in q.order_by(RecordingAccessLog.at.desc(), RecordingAccessLog.id.desc()).limit(limit)]
