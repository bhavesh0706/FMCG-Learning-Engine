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

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.auth import optional_user
from app.database.database import get_db
from app.database.models import AnalysisJob, FeedbackRecord, User
from app.services import analysis_worker

router = APIRouter(prefix="/analysis", tags=["Analysis queue"])
# Re-analysis also lives under /feedback/{id}/ (no prefix on this router).
reanalyze_router = APIRouter(tags=["Analysis queue"])

QUEUE_DIR = Path(__file__).resolve().parents[2] / "uploads" / "queue"
QUEUE_DIR.mkdir(parents=True, exist_ok=True)

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
    file: UploadFile = File(...),
    response_id: Optional[int] = Form(default=None),
    user: Optional[User] = Depends(optional_user),
    db: Session = Depends(get_db),
):
    name = Path(file.filename or "recording").name
    if Path(name).suffix.lower() not in ALLOWED:
        raise HTTPException(
            status_code=400,
            detail="Unsupported media format. Use MP4, MOV, AVI, MKV, WEBM, MP3, WAV, M4A, OGG, AAC or WEBA.",
        )

    # The same survey shouldn't be analyzed twice at once (e.g. a retry after a dropped connection).
    if response_id is not None:
        running = (
            db.query(AnalysisJob)
            .filter(AnalysisJob.response_id == response_id, AnalysisJob.status.in_(("queued", "processing")))
            .first()
        )
        if running:
            return JSONResponse(status_code=202, content=_job_out(db, running, include_result=False))

    stored = QUEUE_DIR / f"{int(time.time() * 1000)}_{name}"
    with open(stored, "wb") as out:
        shutil.copyfileobj(file.file, out, length=1024 * 1024)
    if stored.stat().st_size == 0:
        stored.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="The file is empty. Record or choose the file again.")

    job = AnalysisJob(
        status="queued",
        filename=name,
        stored_path=str(stored),
        response_id=response_id,
        user_id=user.id if user else None,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    analysis_worker.notify()
    return _job_out(db, job, include_result=False)


@router.get("/jobs/{job_id}")
def job_status(job_id: int, include_result: bool = True, db: Session = Depends(get_db)):
    job = db.get(AnalysisJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Analysis job not found.")
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

    source = Path(record.video_path or "")
    if not record.video_path or not source.is_file():
        raise HTTPException(
            status_code=409,
            detail="The original recording is no longer stored on the server, so it can't be re-analyzed.",
        )

    # Work on a copy, so the stored recording is never touched.
    name = Path(record.original_filename or source.name).name
    stored = QUEUE_DIR / f"{int(time.time() * 1000)}_re_{feedback_id}_{name}"
    shutil.copy2(source, stored)

    job = AnalysisJob(
        status="queued",
        kind="reanalyze",
        target_feedback_id=feedback_id,
        filename=name,
        stored_path=str(stored),
        response_id=record.response_id,  # shows "Analyzing…" for that survey meanwhile
        user_id=user.id if user else None,
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