"""
Analysis queue: uploads return immediately and are analyzed in the background.

POST /analysis/jobs        multipart: file, optional response_id  -> 202 {job_id, status, position}
GET  /analysis/jobs/{id}   -> {status, position, elapsed_seconds, feedback_id, error, result (when done)}
GET  /analysis/jobs        -> jobs still queued or processing (for "analyzing…" banners)

POST /feedback/{feedback_id}/reanalyze      run an analyzed recording again (admins)
POST /analysis/reanalyze/{feedback_id}      same, alternative address
     -> 202 {job_id, status, position, ...}; follow it with GET /analysis/jobs/{job_id}.
        When done, the SAME feedback record holds the new results, and the
        product's consolidated report is rebuilt.
     ?wait=true -> waits and returns the finished job (with "result") instead

status: queued -> processing -> done | failed

Register in app/main.py:
    from app.api.analysis import router as analysis_router
    from app.services import analysis_worker
    app.include_router(analysis_router)
    analysis_worker.start()
"""

import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.auth import optional_user
from app.api.ownership import job_or_404, response_or_404
from app.api.uploads_limit import safe_name, save_upload
from app.services.recordings import resolve_recording
from app.database.database import get_db
from app.database.models import AnalysisJob, FeedbackRecord, User
from app.services import analysis_worker

router = APIRouter(prefix="/analysis", tags=["Analysis queue"])
# Re-analysis also lives under /feedback/{id}/ (no prefix on this router).
reanalyze_router = APIRouter(tags=["Analysis queue"])

# Uploads waiting for analysis (services/storage.py: FMCG_STORAGE_ROOT, FMCG_QUEUE_DIR);
# jobs store a key ("queue/<file>"), resolved again by the worker.
from app.services.storage import get_storage  # noqa: E402

QUEUE_DIR = get_storage().area_dir("queue")

ALLOWED = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".mp3", ".wav", ".m4a", ".ogg", ".weba", ".aac"}


def _position(db: Session, job: AnalysisJob) -> int:
    """1 = next in line (or running). 0 when finished."""
    if job.status not in ("queued", "processing"):
        return 0
    if job.status == "processing":
        return 1
    ahead = db.query(AnalysisJob).filter(AnalysisJob.status.in_(("queued", "processing")), AnalysisJob.id < job.id).count()
    return ahead + 1


def _job_out(db: Session, job: AnalysisJob, include_result: bool = True, position: Optional[int] = None) -> dict:
    end = job.finished_at or datetime.utcnow()
    since = job.started_at or job.created_at
    return {
        "job_id": job.id,
        "status": job.status,
        "position": position if position is not None else _position(db, job),
        "filename": job.filename,
        "response_id": job.response_id,
        "feedback_id": job.feedback_id,
        "error": job.error,
        "elapsed_seconds": int((end - since).total_seconds()) if since else 0,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "result": job.result if include_result and job.status == "done" else None,
        "kind": getattr(job, "kind", None) or "analyze",
        "target_feedback_id": getattr(job, "target_feedback_id", None),
    }


# A plain "def": the file is saved in a worker thread, never blocking the server.
@router.post("/jobs", status_code=202)
def queue_analysis(
    request: Request,
    file: UploadFile = File(...),
    response_id: Optional[int] = Form(default=None),
    key_id: Optional[int] = Form(default=None),
    wrapped_key: Optional[str] = Form(default=None),
    user: Optional[User] = Depends(optional_user),
    db: Session = Depends(get_db),
):
    name = safe_name(file.filename)
    if Path(name).suffix.lower() not in ALLOWED:
        raise HTTPException(
            status_code=400,
            detail="Unsupported media format. Use MP4, MOV, AVI, MKV, WEBM, MP3, WAV, M4A, OGG, AAC or WEBA.",
        )

    # The same survey shouldn't be analyzed twice at once (e.g. a retry after a dropped connection).
    if response_id is not None:
        response_or_404(db, request, response_id)  # only the survey's owner (or an admin)
        running = running_job_for(db, response_id)
        if running:
            return JSONResponse(status_code=202, content=_job_out(db, running, include_result=False))

    # Size-capped copy (FMCG_MAX_UPLOAD_MB); 413 above the cap, 400 when empty.
    stored = save_upload(file, get_storage().area_dir("queue"), name + (".enc" if wrapped_key else ""))
    if wrapped_key:
        # Encrypted in the browser / on the phone: it must decrypt with its key (nothing is kept).
        from app.services.recording_crypto import RecordingCryptoError, unwrap_key, verify_file

        try:
            verify_file(stored, unwrap_key(db, key_id, wrapped_key))
        except (RecordingCryptoError, TypeError):
            stored.unlink(missing_ok=True)
            raise HTTPException(status_code=422, detail="The encrypted recording can't be opened with this server's key. Try again.")

    job = queue_stored_file(db, stored, name, response_id, user.id if user else None,
                            key_id=key_id if wrapped_key else None, wrapped_key=wrapped_key or None)
    return _job_out(db, job, include_result=False)


def running_job_for(db: Session, response_id: int) -> Optional[AnalysisJob]:
    """A queued or running analysis of this survey, if any."""
    return (
        db.query(AnalysisJob)
        .filter(AnalysisJob.response_id == response_id, AnalysisJob.status.in_(("queued", "processing")))
        .first()
    )


def queue_stored_file(db: Session, stored: Path, name: str, response_id: Optional[int],
                      user_id: Optional[int], key_id: Optional[int] = None, wrapped_key: Optional[str] = None) -> AnalysisJob:
    """Queue a recording already saved in the queue folder for analysis (commits)."""
    job = AnalysisJob(
        status="queued",
        filename=name,
        stored_path=get_storage().to_stored(stored),
        response_id=response_id,
        user_id=user_id,
        enc_key_id=key_id,
        enc_wrapped_key=wrapped_key,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    analysis_worker.notify()
    return job


@router.get("/jobs/{job_id}")
def job_status(job_id: int, request: Request, include_result: bool = True, db: Session = Depends(get_db)):
    # Admins, the account that queued it, or the owner of its survey; others get 404.
    job = job_or_404(db, request, job_id)
    return _job_out(db, job, include_result=include_result)


@router.get("/jobs")
def active_jobs(limit: int = 200, db: Session = Depends(get_db)):
    """Jobs still queued or processing, processing first. Positions computed in one pass."""
    jobs = (
        db.query(AnalysisJob)
        .filter(AnalysisJob.status.in_(("queued", "processing")))
        .order_by(AnalysisJob.status.desc(), AnalysisJob.id.asc())
        .all()
    )
    out = []
    for i, j in enumerate(jobs[: max(1, min(limit, 1000))]):
        item = _job_out(db, j, include_result=False, position=i + 1)
        out.append(item)
    return {"jobs": out, "total": len(jobs)}


# ---------------------------------------------------------------------------
# Re-analysis of an existing recording
# ---------------------------------------------------------------------------

def _queue_reanalysis(feedback_id: int, user: Optional[User], db: Session) -> AnalysisJob:
    record = db.get(FeedbackRecord, feedback_id)
    if not record:
        raise HTTPException(status_code=404, detail="Feedback not found.")

    running = (
        db.query(AnalysisJob)
        .filter(
            AnalysisJob.target_feedback_id == feedback_id,
            AnalysisJob.status.in_(("queued", "processing")),
        )
        .first()
    )
    if running:
        return running  # already being re-analyzed: don't start a second one

    source = resolve_recording(record.video_path)
    if source is None:
        raise HTTPException(
            status_code=409,
            detail="The original recording is no longer stored on the server, so it can't be re-analyzed.",
        )

    # Work on a copy, so the stored recording is never touched (an encrypted one stays encrypted).
    name = Path(record.original_filename or source.name).name
    encrypted = bool(record.media_encrypted and record.media_wrapped_key)
    stored = get_storage().area_dir("queue") / (f"{int(time.time() * 1000)}_re_{feedback_id}_{name}" + (".enc" if encrypted else ""))
    shutil.copy2(source, stored)

    job = AnalysisJob(
        status="queued",
        kind="reanalyze",
        target_feedback_id=feedback_id,
        filename=name,
        stored_path=get_storage().to_stored(stored),
        response_id=record.response_id,  # shows "Analyzing…" for that survey meanwhile
        user_id=user.id if user else None,
        enc_key_id=record.media_key_id if encrypted else None,
        enc_wrapped_key=record.media_wrapped_key if encrypted else None,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    analysis_worker.notify()
    return job


def _reanalyze(feedback_id: int, wait: bool, user, db: Session):
    job = _queue_reanalysis(feedback_id, user, db)
    if not wait:
        return JSONResponse(status_code=202, content=_job_out(db, job, include_result=False))
    # Wait for the worker (this request only; the rest of the API keeps answering).
    deadline = time.time() + 60 * 60
    while time.time() < deadline:
        db.expire_all()
        job = db.get(AnalysisJob, job.id)
        if job.status == "done":
            return _job_out(db, job)
        if job.status == "failed":
            raise HTTPException(status_code=500, detail=job.error or "The re-analysis failed.")
        time.sleep(2)
    raise HTTPException(status_code=504, detail="The re-analysis is taking long; it continues in the background.")


@reanalyze_router.post("/feedback/{feedback_id}/reanalyze", status_code=202)
def reanalyze_feedback(
    feedback_id: int,
    wait: bool = False,
    user: Optional[User] = Depends(optional_user),
    db: Session = Depends(get_db),
):
    return _reanalyze(feedback_id, wait, user, db)


@router.post("/reanalyze/{feedback_id}", status_code=202)
def reanalyze_feedback_alias(
    feedback_id: int,
    wait: bool = False,
    user: Optional[User] = Depends(optional_user),
    db: Session = Depends(get_db),
):
    return _reanalyze(feedback_id, wait, user, db)