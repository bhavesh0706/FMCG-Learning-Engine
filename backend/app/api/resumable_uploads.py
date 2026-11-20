"""
Resumable recording uploads (mobile offline mode).

A survey recorded offline can be a large video uploaded over a weak connection.
Instead of one long request that restarts from zero when the connection drops,
the phone sends the file in pieces and, after any interruption, asks the server
how much arrived and continues from there.

POST /uploads/resumable
     {upload_id (UUID chosen by the phone), filename, total_bytes, response_id?, sha256?}
     -> the upload's state. Sending the same upload_id again returns its state
        (it never starts a second upload).
GET  /uploads/resumable/{upload_id}
     -> {upload_id, status, received_bytes, total_bytes, job}
PUT  /uploads/resumable/{upload_id}?offset=N      body: the raw bytes of one piece
     -> the state after appending. A piece that already arrived (a retry after a lost
        reply) is accepted without writing it twice. offset beyond what arrived -> 409
        with received_bytes, so the phone continues from there.
POST /uploads/resumable/{upload_id}/complete
     -> checks the size (and the SHA-256 when one was given), then queues the
        analysis exactly once and returns the state with "job" (as POST /analysis/jobs).
        Calling it again returns the same job.

Only the account that started an upload can see or continue it.
"""

from __future__ import annotations

import hashlib
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.analysis import ALLOWED, _job_out, running_job_for
from app.api.auth import current_user
from app.api.ownership import response_or_404
from app.api.surveys import _client_uuid
from app.api.uploads_limit import MAX_UPLOAD_BYTES, safe_name
from app.database.database import get_db
from app.database.models import AnalysisJob, UploadSession, User
from app.services import analysis_worker
from app.services.storage import get_storage

router = APIRouter(prefix="/uploads/resumable", tags=["Resumable uploads"])

# Largest piece accepted in one PUT (the phone sends 2 MB pieces).
MAX_PIECE_BYTES = int(float(os.environ.get("FMCG_UPLOAD_PIECE_MB", "") or 8) * 1024 * 1024)

# Unfinished uploads untouched for this long are deleted (the phone starts a new one).
STALE_AFTER = timedelta(days=float(os.environ.get("FMCG_UPLOAD_STALE_DAYS", "") or 14))


def purge_stale_uploads(db: Session, limit: int = 50) -> int:
    """Deletes unfinished uploads (and their partial files) not continued for STALE_AFTER."""
    cutoff = datetime.utcnow() - STALE_AFTER
    stale = (db.query(UploadSession)
             .filter(UploadSession.status == "receiving", UploadSession.updated_at < cutoff)
             .limit(limit).all())
    for upload in stale:
        path = get_storage().resolve(upload.part_path)
        if path is not None:
            path.unlink(missing_ok=True)
        db.delete(upload)
    if stale:
        db.commit()
    return len(stale)


class UploadStart(BaseModel):
    upload_id: str
    filename: str = Field(min_length=1, max_length=255)
    total_bytes: int = Field(gt=0)
    response_id: Optional[int] = None
    sha256: Optional[str] = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    # An encrypted recording (FMCGREC1): the data key wrapped with the server's public key
    key_id: Optional[int] = None
    wrapped_key: Optional[str] = Field(default=None, max_length=4096)

    @field_validator("upload_id", mode="before")
    @classmethod
    def check_upload_id(cls, value):
        checked = _client_uuid(value)
        if checked is None:
            raise ValueError("upload_id is required.")
        return checked


def _state(db: Session, upload: UploadSession) -> dict:
    job = db.get(AnalysisJob, upload.job_id) if upload.job_id else None
    return {
        "upload_id": upload.upload_id,
        "status": upload.status,
        "filename": upload.filename,
        "response_id": upload.response_id,
        "received_bytes": int(upload.received_bytes or 0),
        "total_bytes": int(upload.total_bytes),
        "job": _job_out(db, job, include_result=False) if job else None,
    }


def _upload_or_404(db: Session, upload_id: str, user: User, lock: bool = False) -> UploadSession:
    query = db.query(UploadSession).filter(UploadSession.upload_id == (upload_id or "").lower())
    if lock:
        query = query.with_for_update()  # one piece at a time per upload (MySQL row lock)
    upload = query.first()
    if upload is None or upload.user_id != user.id:
        raise HTTPException(status_code=404, detail="Upload not found.")
    return upload


def _part_file(upload: UploadSession) -> Path:
    path = get_storage().resolve(upload.part_path)
    if path is None:
        raise HTTPException(status_code=410, detail="The partial upload is no longer on the server. Start it again.")
    return path


@router.post("")
def start_upload(body: UploadStart, request: Request, user: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    name = safe_name(body.filename)
    if Path(name).suffix.lower() not in ALLOWED:
        raise HTTPException(status_code=400, detail="Unsupported media format.")
    if body.total_bytes > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"The file is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB. Record a shorter clip.",
        )

    existing = db.query(UploadSession).filter(UploadSession.upload_id == body.upload_id).first()
    if existing is not None:
        if existing.user_id != user.id:
            raise HTTPException(status_code=409, detail="This upload id is already used.")
        if existing.total_bytes != body.total_bytes:
            raise HTTPException(status_code=409, detail="This upload id was started for a different file.")
        return _state(db, existing)

    if body.response_id is not None:
        response_or_404(db, request, body.response_id)  # only the survey's owner (or an admin)
    if body.wrapped_key or body.key_id is not None:
        from app.services.recording_crypto import RecordingCryptoError, unwrap_key

        if not (body.wrapped_key and body.key_id is not None):
            raise HTTPException(status_code=400, detail="An encrypted recording needs key_id and wrapped_key.")
        try:
            unwrap_key(db, body.key_id, body.wrapped_key)  # refused now rather than after the whole upload
        except RecordingCryptoError:
            raise HTTPException(status_code=400, detail={"code": "recording_key_unknown",
                                                         "message": "This recording was sealed for another server key. "
                                                                    "Sign in online once so the app gets the current key."})

    purge_stale_uploads(db)
    folder = get_storage().area_dir("queue")
    part = folder / f"{int(time.time() * 1000)}_{body.upload_id}.part"
    part.touch()
    upload = UploadSession(
        upload_id=body.upload_id,
        user_id=user.id,
        response_id=body.response_id,
        filename=name,
        total_bytes=body.total_bytes,
        received_bytes=0,
        sha256=body.sha256.lower() if body.sha256 else None,
        part_path=get_storage().to_stored(part),
        status="receiving",
        enc_key_id=body.key_id if body.wrapped_key else None,
        enc_wrapped_key=body.wrapped_key or None,
    )
    db.add(upload)
    try:
        db.commit()
    except IntegrityError:  # the same upload started twice at the same moment
        db.rollback()
        part.unlink(missing_ok=True)
        return _state(db, _upload_or_404(db, body.upload_id, user))
    db.refresh(upload)
    return _state(db, upload)


@router.get("/{upload_id}")
def upload_state(upload_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _state(db, _upload_or_404(db, upload_id, user))


async def _read_piece(request: Request) -> bytes:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_PIECE_BYTES:
        raise HTTPException(status_code=413, detail=f"Send at most {MAX_PIECE_BYTES} bytes per piece.")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_PIECE_BYTES:
            raise HTTPException(status_code=413, detail=f"Send at most {MAX_PIECE_BYTES} bytes per piece.")
    return bytes(data)


def _append(db: Session, upload_id: str, user: User, offset: int, data: bytes) -> dict:
    upload = _upload_or_404(db, upload_id, user, lock=True)
    if upload.status != "receiving":
        return _state(db, upload)  # already complete: nothing more to write
    received = int(upload.received_bytes or 0)
    if offset > received:
        raise HTTPException(
            status_code=409,
            detail={"message": "Continue from the bytes already received.", "received_bytes": received},
        )
    # Bytes before `received` already arrived (a retried piece): keep only the new tail.
    new = data[received - offset:] if offset < received else data
    if received + len(new) > upload.total_bytes:
        raise HTTPException(status_code=400, detail="More bytes than the file size given when the upload started.")
    if new:
        path = _part_file(upload)
        with open(path, "r+b") as out:
            out.truncate(received)  # drop anything half-written by an interrupted request
            out.seek(received)
            out.write(new)
            out.flush()
            os.fsync(out.fileno())
        upload.received_bytes = received + len(new)
        db.commit()
    return _state(db, upload)


@router.put("/{upload_id}")
async def upload_piece(upload_id: str, request: Request, offset: int = Query(..., ge=0),
                       user: User = Depends(current_user), db: Session = Depends(get_db)):
    data = await _read_piece(request)
    return await run_in_threadpool(_append, db, upload_id, user, offset, data)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


@router.post("/{upload_id}/complete")
def complete_upload(upload_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    upload = _upload_or_404(db, upload_id, user, lock=True)
    if upload.status == "complete":
        return _state(db, upload)  # a retried "complete": the same job
    if int(upload.received_bytes or 0) != upload.total_bytes:
        raise HTTPException(
            status_code=409,
            detail={"message": "The upload is not finished yet.", "received_bytes": int(upload.received_bytes or 0)},
        )
    part = _part_file(upload)
    if part.stat().st_size != upload.total_bytes:
        # The partial file and the record disagree (e.g. the disk was restored): start over.
        upload.received_bytes = 0
        part.write_bytes(b"")
        db.commit()
        raise HTTPException(status_code=409, detail={"message": "Upload the file again.", "received_bytes": 0})
    if upload.sha256 and _sha256(part) != upload.sha256:
        upload.received_bytes = 0
        part.write_bytes(b"")
        db.commit()
        raise HTTPException(
            status_code=422,
            detail={"message": "The file arrived damaged (checksum mismatch). Upload it again.", "received_bytes": 0},
        )

    # The job and "complete" are saved in ONE commit, so a crash in between can never
    # leave a queued job whose file a retry would then delete.
    job = running_job_for(db, upload.response_id) if upload.response_id is not None else None
    if job is not None:
        upload.status, upload.job_id = "complete", job.id  # this survey is already being analyzed
        db.commit()
        part.unlink(missing_ok=True)
        return _state(db, upload)

    if upload.enc_wrapped_key:
        # Every chunk must decrypt with its key (nothing is kept): a damaged or foreign file is refused here.
        from app.services.recording_crypto import RecordingCryptoError, unwrap_key, verify_file

        try:
            verify_file(part, unwrap_key(db, upload.enc_key_id, upload.enc_wrapped_key))
        except RecordingCryptoError:
            upload.received_bytes = 0
            part.write_bytes(b"")
            db.commit()
            raise HTTPException(status_code=422, detail={"message": "The encrypted recording arrived damaged. Upload it again.",
                                                         "received_bytes": 0})

    final = part.with_name(f"{int(time.time() * 1000)}_{upload.filename}" + (".enc" if upload.enc_wrapped_key else ""))
    part.rename(final)
    job = AnalysisJob(
        status="queued",
        filename=upload.filename,
        stored_path=get_storage().to_stored(final),
        response_id=upload.response_id,
        user_id=user.id,
        enc_key_id=upload.enc_key_id,
        enc_wrapped_key=upload.enc_wrapped_key,
    )
    db.add(job)
    db.flush()
    upload.part_path = job.stored_path
    upload.status, upload.job_id = "complete", job.id
    try:
        db.commit()
    except Exception:
        db.rollback()
        final.rename(part)  # back to "receiving" exactly as before
        raise
    analysis_worker.notify()
    return _state(db, upload)
