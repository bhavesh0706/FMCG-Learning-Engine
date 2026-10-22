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
        "video_path": record.video_path,
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
def get_feedback_video(
    feedback_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """Return the original audio or video recording."""

    record = feedback_or_404(db, request, feedback_id)

    if not record.video_path:
        raise HTTPException(
            status_code=404,
            detail="Recording path is not available.",
        )

    video_path = Path(record.video_path)

    if not video_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="Original recording file could not be found.",
        )

    # Explicit MIME types for common audio/video formats.
    extension = video_path.suffix.lower()
    mime_types = {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
        ".aac": "audio/aac",
        ".ogg": "audio/ogg",
        ".oga": "audio/ogg",
        ".opus": "audio/ogg",
        ".flac": "audio/flac",
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".webm": "audio/webm",
    }

    media_type = mime_types.get(extension)
    if not media_type:
        media_type = (
            mimetypes.guess_type(video_path.name)[0]
            or "application/octet-stream"
        )

    download_filename = (
        Path(record.original_filename).name
        if record.original_filename
        else video_path.name
    )

    return FileResponse(
        path=str(video_path),
        media_type=media_type,
        filename=download_filename,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cross-Origin-Resource-Policy": "cross-origin",
        },
    )


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

    video_path = Path(record.video_path or "")

    if record.video_path and video_path.is_file():
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