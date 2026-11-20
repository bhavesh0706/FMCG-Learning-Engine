"""
Encrypted consumer recordings: the key devices encrypt with, passkey-authorised playback,
the access log, and the Super Admin's recording passkey: ONE for the organization, the same
for every product. Per product the Super Admin chooses the Admins who may play its recordings.
(services/recording_crypto.py: encryption; services/recording_access.py: who may play.)

Devices (survey persons, every role signed in):
GET  /recordings/public-key                 the server's PUBLIC key (devices encrypt offline with it)

Playing a recording (Admin given access to the product, or the Super Admin):
POST /recordings/{feedback_id}/authorise    {passkey} -> a 10-minute playback token (logged)
GET  /recordings/{feedback_id}/stream?pt=   the decrypted recording, streamed (Range supported,
                                            no-store, inline only); authenticated by the token
GET  /recordings/{feedback_id}/history      who opened this recording, when, and the outcome

Super Admin (their portal):
GET  /org/recording-passkey                         the organization's passkey status (never the passkey)
PUT  /org/recording-passkey                         {passkey}: set or rotate (all products)
POST /org/recording-passkey/share                   {passkey, user_ids}: send it inside the app (one reveal)
GET  /org/products/{id}/recording-access            passkey status and the product's Admins
PUT  /org/products/{id}/recording-access/passkey    {passkey}: the same as PUT /org/recording-passkey
PUT  /org/products/{id}/recording-access/admins     {user_ids}: Admins allowed to play this product's recordings
POST /org/products/{id}/recording-access/share      {passkey, user_ids}: the same as /org/recording-passkey/share
GET  /org/products/{id}/recording-access/history    the product's access log
GET  /org/recording-access/admins                   the Admins that can be chosen

Admin:
GET  /admin/recording-access                        products I may play, and whether a passkey waits for me
POST /admin/recording-access/reveal                 the passkey sent to me, shown once
POST /admin/recording-access/{product_id}/reveal    the same (older app versions)
"""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.ownership import account
from app.database.database import get_db
from app.database.models import FeedbackRecord, Product, RecordingAccessGrant, User
from app.services import audit
from app.services import recording_access as A
from app.services import recording_crypto as C
from app.services.recordings import resolve_recording

router = APIRouter(tags=["Encrypted recordings"])

MIME = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".m4a": "audio/mp4", ".aac": "audio/aac", ".ogg": "audio/ogg",
        ".oga": "audio/ogg", ".opus": "audio/ogg", ".flac": "audio/flac", ".mp4": "video/mp4", ".mov": "video/quicktime",
        ".webm": "video/webm", ".weba": "audio/webm", ".mkv": "video/x-matroska", ".avi": "video/x-msvideo"}

# Never cached, never offered as a download, never embedded by other sites.
SAFE_HEADERS = {
    "Cache-Control": "no-store, private, max-age=0",
    "Pragma": "no-cache",
    "Content-Disposition": "inline",
    "X-Content-Type-Options": "nosniff",
    "Cross-Origin-Resource-Policy": "cross-origin",
    "Referrer-Policy": "no-referrer",
}


def _fail(e: A.AccessError):
    raise HTTPException(status_code=e.status, detail=str(e))


def _ip(request: Request) -> Optional[str]:
    return request.client.host if request.client else None


# ---------------------------------------------------------------- devices
@router.get("/recordings/public-key")
def public_key(db: Session = Depends(get_db)):
    """Not secret: it can only encrypt. The private key never leaves the server."""
    return C.public_key_info(db)


# ---------------------------------------------------------------- playback
class PasskeyIn(BaseModel):
    passkey: str = Field(min_length=1, max_length=128)


@router.post("/recordings/{feedback_id}/authorise")
def authorise(feedback_id: int, body: PasskeyIn, request: Request, db: Session = Depends(get_db)):
    me = account(request)
    try:
        out = A.authorise_playback(db, me, feedback_id, body.passkey, _ip(request))
    except A.AccessError as e:
        _fail(e)
    return {**out, "stream_url": f"/recordings/{feedback_id}/stream?pt={out['token']}"}


_RANGE = re.compile(r"bytes=(\d*)-(\d*)$")


@router.get("/recordings/{feedback_id}/stream")
def stream(feedback_id: int, request: Request, pt: str = "", db: Session = Depends(get_db)):
    """Decrypts on the server and streams. Authenticated by the playback token (players can't send
    headers); the first request of a token is logged as "played"."""
    try:
        user_id = A.token_user(db, pt, feedback_id)
    except A.AccessError as e:
        _fail(e)
    rec = db.get(FeedbackRecord, feedback_id)
    path = resolve_recording(rec.video_path) if rec and rec.video_path else None
    if path is None:
        raise HTTPException(status_code=404, detail="The recording file isn't on the server.")
    name = Path(rec.original_filename or path.name).name
    media_type = MIME.get(Path(name).suffix.lower()) or mimetypes.guess_type(name)[0] or "application/octet-stream"
    user = db.get(User, user_id)
    encrypted = bool(rec.media_encrypted and rec.media_wrapped_key)
    if encrypted:
        data_key = C.unwrap_key(db, rec.media_key_id, rec.media_wrapped_key)
        size = C.layout(path).size
    else:
        data_key, size = None, path.stat().st_size  # an older recording stored before encryption

    start, end, status = 0, size - 1, 200
    match = _RANGE.match(request.headers.get("range", "").strip())
    if match and size:
        a, b = match.groups()
        if a:
            start, end = int(a), min(int(b) if b else size - 1, size - 1)
        elif b:
            start, end = max(0, size - int(b)), size - 1
        if start > end or start >= size:
            return JSONResponse(status_code=416, content={"detail": "Range not satisfiable."},
                                headers={"Content-Range": f"bytes */{size}"})
        status = 206
    if start == 0 and user is not None:
        A.log(db, {"id": user.id, "name": user.name, "role": user.role}, "played", None, feedback_id, rec.response_id, _ip(request))

    def body():
        if encrypted:
            yield from C.decrypt_range(path, data_key, start, end)
        else:
            with open(path, "rb") as f:
                f.seek(start)
                left = end - start + 1
                while left > 0:
                    part = f.read(min(1024 * 1024, left))
                    if not part:
                        break
                    left -= len(part)
                    yield part

    headers = {**SAFE_HEADERS, "Accept-Ranges": "bytes", "Content-Length": str(end - start + 1 if size else 0)}
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(body(), status_code=status, media_type=media_type, headers=headers)


@router.get("/recordings/{feedback_id}/history")
def recording_history(feedback_id: int, request: Request, db: Session = Depends(get_db)):
    me = account(request)
    try:
        _, product_id, _ = A.recording_of(db, feedback_id)
        A.may_play(db, me, product_id)
    except A.AccessError as e:
        _fail(e)
    return {"feedback_id": feedback_id, "history": A.access_history(db, feedback_id=feedback_id)}


# ---------------------------------------------------------------- Super Admin
def _client(request: Request) -> dict:
    me = account(request)
    if me.get("role") != "client":
        raise HTTPException(status_code=403, detail="Recording passkeys are managed by the Super Admin.")
    return me


def _product(db: Session, product_id: int) -> Product:
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found.")
    return product


@router.get("/org/recording-access/admins")
def choosable_admins(request: Request, db: Session = Depends(get_db)):
    _client(request)
    admins = db.query(User).filter(User.role == "admin", User.is_active == 1).order_by(User.name).all()
    return {"admins": [{"user_id": u.id, "name": u.name, "email": u.email} for u in admins]}


@router.get("/org/recording-passkey")
def org_passkey_status(request: Request, db: Session = Depends(get_db)):
    _client(request)
    return A.passkey_status(db)


class NewPasskey(BaseModel):
    passkey: str = Field(min_length=1, max_length=64)


def _set_org_passkey(db: Session, request: Request, passkey: str) -> None:
    me = _client(request)
    try:
        row = A.set_passkey(db, passkey, me)
    except A.AccessError as e:
        _fail(e)
    db.commit()
    action = "Set" if row.version == 1 else f"Changed (version {row.version})"
    audit.note(request, summary=f"{action} the recording passkey (all products)")


@router.put("/org/recording-passkey")
def set_org_passkey(body: NewPasskey, request: Request, db: Session = Depends(get_db)):
    _set_org_passkey(db, request, body.passkey)
    return A.passkey_status(db)


@router.get("/org/products/{product_id}/recording-access")
def access_status(product_id: int, request: Request, db: Session = Depends(get_db)):
    _client(request)
    _product(db, product_id)
    return A.passkey_status(db, product_id)


@router.put("/org/products/{product_id}/recording-access/passkey")
def set_passkey(product_id: int, body: NewPasskey, request: Request, db: Session = Depends(get_db)):
    """The organization's passkey (the same for every product), from a product's page."""
    _product(db, product_id)
    _set_org_passkey(db, request, body.passkey)
    return A.passkey_status(db, product_id)


class AdminsIn(BaseModel):
    user_ids: list[int] = Field(default_factory=list)


@router.put("/org/products/{product_id}/recording-access/admins")
def set_admins(product_id: int, body: AdminsIn, request: Request, db: Session = Depends(get_db)):
    me = _client(request)
    product = _product(db, product_id)
    try:
        added = A.set_admins(db, product, body.user_ids, me)
    except A.AccessError as e:
        _fail(e)
    db.commit()
    audit.note(request, target_id=product.id, summary=f"Recording access of {product.name}: {len(body.user_ids)} Admin(s)",
               details={"user_ids": body.user_ids})
    if added:
        from app.services.notifications import notify

        notify(db, added, "recording_access", title=f"Recording access: {product.name}",
               message=f"You can now play {product.name}'s consumer recordings with its passkey. "
                       "The Super Admin sends you the passkey.", data={"product_id": product.id})
    return A.passkey_status(db, product_id)


class ShareIn(BaseModel):
    passkey: str = Field(min_length=1, max_length=64)
    user_ids: list[int] = Field(min_length=1)


def _share(db: Session, request: Request, body) -> int:
    _client(request)
    try:
        made = A.share_passkey(db, body.passkey, body.user_ids)
    except A.AccessError as e:
        _fail(e)
    db.commit()
    from app.services.notifications import notify

    notify(db, body.user_ids, "recording_passkey", title="Recording passkey",
           message="The Super Admin sent you the recording passkey (the same for every product). Open a recording to see it "
                   "(shown once, within 7 days).", data={})
    audit.note(request, summary=f"Sent the recording passkey to {made} Admin(s)", details={"user_ids": body.user_ids})
    return made


@router.post("/org/recording-passkey/share")
def share_org(body: ShareIn, request: Request, db: Session = Depends(get_db)):
    """The passkey travels encrypted and is shown to each Admin once (then deleted)."""
    _share(db, request, body)
    return A.passkey_status(db)


@router.post("/org/products/{product_id}/recording-access/share")
def share(product_id: int, body: ShareIn, request: Request, db: Session = Depends(get_db)):
    _product(db, product_id)
    _share(db, request, body)
    return A.passkey_status(db, product_id)


@router.get("/org/products/{product_id}/recording-access/history")
def product_history(product_id: int, request: Request, db: Session = Depends(get_db)):
    _client(request)
    return {"product_id": product_id, "history": A.access_history(db, product_id=product_id, limit=200)}


# ---------------------------------------------------------------- Admin
@router.get("/admin/recording-access")
def my_access(request: Request, db: Session = Depends(get_db)):
    me = account(request)
    grants = (db.query(RecordingAccessGrant, Product).join(Product, Product.id == RecordingAccessGrant.product_id)
              .filter(RecordingAccessGrant.user_id == me.get("id")).order_by(Product.name).all())
    waiting = bool(grants) and A.passkey_waiting(db, me.get("id"))
    return {"passkey_waiting": waiting,
            # passkey_waiting per product too, for older app versions (it's the same passkey)
            "products": [{"product_id": p.id, "name": p.name, "passkey_waiting": waiting} for _, p in grants]}


def _reveal(db: Session, request: Request, product_id: Optional[int]) -> dict:
    me = account(request)
    try:
        if me.get("role") != "admin":
            raise A.AccessError("Only an Admin can be sent the recording passkey.", 403, "not_authorised")
        if product_id is not None:
            A.may_play(db, me, product_id)
        elif not db.query(RecordingAccessGrant.id).filter(RecordingAccessGrant.user_id == me.get("id")).first():
            raise A.AccessError("You haven't been given access to any product's recordings. Ask the Super Admin.", 403,
                                "not_authorised")
        passkey = A.reveal_share(db, me)
    except A.AccessError as e:
        db.commit()
        _fail(e)
    db.commit()
    audit.note(request, summary=f"Revealed the recording passkey sent to {me.get('name')}")
    return {"passkey": passkey, "scope": "organization",
            "note": "Shown once: it has been deleted from the server. It opens the recordings of every product you're allowed "
                    "for. Keep it safe; never share it with survey persons."}


@router.post("/admin/recording-access/reveal")
def reveal_mine(request: Request, db: Session = Depends(get_db)):
    return _reveal(db, request, None)


@router.post("/admin/recording-access/{product_id}/reveal")
def reveal(product_id: int, request: Request, db: Session = Depends(get_db)):
    return {**_reveal(db, request, product_id), "product_id": product_id}
