"""
Analyzed recordings (feedback records).

GET    /feedback              Every record, newest first (admins only: access.py)
GET    /feedback/{id}         One record: admins, or the owner of its survey
GET    /feedback/{id}/video   Original recording: admins, or the owner of its survey
DELETE /feedback/{id}         Admins only (access.py)

Record-level checks live in ownership.py; other people get 404.
"""

import mimetypes
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.ownership import feedback_or_404
from app.services.recordings import resolve_recording
from app.services.storage import get_storage
from app.database.database import get_db
from app.database.models import FeedbackRecord


# ============================================================
# ROUTER
# ============================================================

router = APIRouter(
    prefix="/feedback",
    tags=["Feedback"],
)


# ============================================================
# FORMAT DATABASE RECORD
# ============================================================

def format_feedback_record(record):
    """
    Convert a database record into the structure required
    by the frontend/admin dashboard.
    """

    return {
        "id": record.id,
        "filename": record.original_filename,
        # A storage key ("videos/123_x.mp4"); the file plays through /feedback/{id}/video.
        "video_path": get_storage().public_key(record.video_path),
        "video_duration_seconds": record.duration_seconds,
        "processing_time_seconds": record.processing_time_seconds,
        "uploaded_at": (
            record.uploaded_at.isoformat()
            if record.uploaded_at
            else None
        ),
        "summary": record.overall_summary,
        "sentiment": record.overall_sentiment,
        "overall_rating": record.overall_rating,
        "transcript": record.transcript,
        "chapters": record.chapters or [],
    }


# ============================================================
# GET ALL FEEDBACK RECORDS (admins only, see access.py)
# ============================================================

@router.get("")
def get_all_feedback(db: Session = Depends(get_db)):
    records = (
        db.query(FeedbackRecord)
        .order_by(FeedbackRecord.uploaded_at.desc())
        .all()
    )

    return {
        "total": len(records),
        "feedback": [
            format_feedback_record(record)
            for record in records
        ],
    }


# ============================================================
# GET SINGLE FEEDBACK RECORD
# ============================================================

@router.get("/{feedback_id}")
def get_feedback(
    feedback_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    record = feedback_or_404(db, request, feedback_id)
    return format_feedback_record(record)


# ============================================================
# GET ORIGINAL RECORDING
# ============================================================

@router.get("/{feedback_id}/video")
def get_feedback_video(feedback_id: int, request: Request, db: Session = Depends(get_db)):
    """Recordings are confidential: they're no longer served here (to any role). An Admin given
    access to the product (or the Super Admin) plays one with the product passkey:
    POST /recordings/{id}/authorise, then GET /recordings/{id}/stream (api/recordings.py)."""
    feedback_or_404(db, request, feedback_id)
    raise HTTPException(status_code=403, detail={
        "code": "recording_protected",
        "message": "Recordings are encrypted. An Admin with access to this product plays them with the product's passkey.",
        "authorise_url": f"/recordings/{feedback_id}/authorise",
    })


# ============================================================
# DELETE FEEDBACK RECORD (admins only, see access.py)
# ============================================================

@router.delete("/{feedback_id}")
def delete_feedback(
    feedback_id: int,
    db: Session = Depends(get_db),
):
    record = db.get(FeedbackRecord, feedback_id)

    if not record:
        raise HTTPException(
            status_code=404,
            detail=f"Feedback record {feedback_id} not found.",
        )

    video_path = resolve_recording(record.video_path)

    if video_path is not None:
        try:
            video_path.unlink()
        except OSError as error:
            print(f"Warning: Could not delete recording file: {error}")

    try:
        db.delete(record)
        db.commit()
    except Exception as error:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=str(error),
        ) from error

    return {
        "message": "Feedback record deleted successfully.",
        "feedback_id": feedback_id,
    }