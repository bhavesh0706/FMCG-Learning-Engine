# from pathlib import Path

# from fastapi import APIRouter, HTTPException
# from fastapi.responses import FileResponse
# from sqlalchemy.orm import Session

# from app.database.database import SessionLocal
# from app.database.models import FeedbackRecord


# # ============================================================
# # ROUTER
# # ============================================================

# router = APIRouter(
#     prefix="/feedback",
#     tags=["Feedback"]
# )


# # ============================================================
# # DATABASE HELPER
# # ============================================================

# def get_db():
#     db: Session = SessionLocal()

#     try:
#         return db

#     except Exception:
#         db.close()
#         raise


# # ============================================================
# # FORMAT DATABASE RECORD
# # ============================================================

# def format_feedback_record(record):
#     """
#     Convert a database record into the structure
#     required by the frontend/admin dashboard.
#     """

#     return {
#         "id": record.id,

#         "filename": record.original_filename,

#         "video_path": record.video_path,

#         "video_duration_seconds": (
#             record.duration_seconds
#         ),

#         "processing_time_seconds": (
#             record.processing_time_seconds
#         ),

#         "uploaded_at": (
#             record.uploaded_at.isoformat()
#             if record.uploaded_at
#             else None
#         ),

#         "summary": (
#             record.overall_summary
#         ),

#         "sentiment": (
#             record.overall_sentiment
#         ),

#         "transcript": (
#             record.transcript
#         ),

#         "chapters": (
#             record.chapters or []
#         ),
#     }


# # ============================================================
# # GET ALL FEEDBACK RECORDS
# # ============================================================

# @router.get("")
# def get_all_feedback():

#     db = get_db()

#     try:

#         records = (
#             db.query(FeedbackRecord)
#             .order_by(
#                 FeedbackRecord.uploaded_at.desc()
#             )
#             .all()
#         )

#         return {
#             "total": len(records),

#             "feedback": [
#                 format_feedback_record(record)
#                 for record in records
#             ]
#         }

#     finally:

#         db.close()


# # ============================================================
# # GET SINGLE FEEDBACK RECORD
# # ============================================================

# @router.get("/{feedback_id}")
# def get_feedback(
#     feedback_id: int
# ):

#     db = get_db()

#     try:

#         record = (
#             db.query(FeedbackRecord)
#             .filter(
#                 FeedbackRecord.id == feedback_id
#             )
#             .first()
#         )

#         if not record:

#             raise HTTPException(
#                 status_code=404,
#                 detail=(
#                     f"Feedback record "
#                     f"{feedback_id} not found."
#                 )
#             )

#         return format_feedback_record(
#             record
#         )

#     finally:

#         db.close()


# # ============================================================
# # GET ORIGINAL VIDEO
# # ============================================================

# @router.get("/{feedback_id}/video")
# def get_feedback_video(
#     feedback_id: int
# ):

#     db = get_db()

#     try:

#         record = (
#             db.query(FeedbackRecord)
#             .filter(
#                 FeedbackRecord.id == feedback_id
#             )
#             .first()
#         )

#         if not record:

#             raise HTTPException(
#                 status_code=404,
#                 detail=(
#                     f"Feedback record "
#                     f"{feedback_id} not found."
#                 )
#             )

#         video_path = Path(
#             record.video_path
#         )

#         if not video_path.exists():

#             raise HTTPException(
#                 status_code=404,
#                 detail=(
#                     "Original video file "
#                     "could not be found."
#                 )
#             )

#         return FileResponse(
#             path=str(video_path),
#             filename=record.original_filename,
#             media_type="video/mp4"
#         )

#     finally:

#         db.close()


# # ============================================================
# # DELETE FEEDBACK RECORD
# # ============================================================

# @router.delete("/{feedback_id}")
# def delete_feedback(
#     feedback_id: int
# ):

#     db = get_db()

#     try:

#         record = (
#             db.query(FeedbackRecord)
#             .filter(
#                 FeedbackRecord.id == feedback_id
#             )
#             .first()
#         )

#         if not record:

#             raise HTTPException(
#                 status_code=404,
#                 detail=(
#                     f"Feedback record "
#                     f"{feedback_id} not found."
#                 )
#             )

#         # --------------------------------------------
#         # Delete original video
#         # --------------------------------------------

#         video_path = Path(
#             record.video_path
#         )

#         if video_path.exists():

#             try:
#                 video_path.unlink()

#             except Exception as error:

#                 print(
#                     "Warning: Could not delete "
#                     f"video file: {error}"
#                 )

#         # --------------------------------------------
#         # Delete database record
#         # --------------------------------------------

#         db.delete(record)

#         db.commit()

#         return {
#             "message": (
#                 "Feedback record deleted successfully."
#             ),

#             "feedback_id": feedback_id
#         }

#     except HTTPException:
#         raise

#     except Exception as error:

#         db.rollback()

#         raise HTTPException(
#             status_code=500,
#             detail=str(error)
#         )

#     finally:

#         db.close()




from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database.database import SessionLocal
from app.database.models import FeedbackRecord


# ============================================================
# ROUTER
# ============================================================

router = APIRouter(
    prefix="/feedback",
    tags=["Feedback"]
)


# ============================================================
# DATABASE HELPER
# ============================================================

def get_db():
    db: Session = SessionLocal()

    try:
        return db

    except Exception:
        db.close()
        raise


# ============================================================
# FORMAT DATABASE RECORD
# ============================================================

def format_feedback_record(record):
    """
    Convert a database record into the structure
    required by the frontend/admin dashboard.
    """

    return {
        "id": record.id,

        "filename": record.original_filename,

        "video_path": record.video_path,

        "video_duration_seconds": (
            record.duration_seconds
        ),

        "processing_time_seconds": (
            record.processing_time_seconds
        ),

        "uploaded_at": (
            record.uploaded_at.isoformat()
            if record.uploaded_at
            else None
        ),

        "summary": (
            record.overall_summary
        ),

        "sentiment": (
            record.overall_sentiment
        ),

        "overall_rating": (
            record.overall_rating
        ),

        "transcript": (
            record.transcript
        ),

        "chapters": (
            record.chapters or []
        ),
    }


# ============================================================
# GET ALL FEEDBACK RECORDS
# ============================================================

@router.get("")
def get_all_feedback():

    db = get_db()

    try:

        records = (
            db.query(FeedbackRecord)
            .order_by(
                FeedbackRecord.uploaded_at.desc()
            )
            .all()
        )

        return {
            "total": len(records),

            "feedback": [
                format_feedback_record(record)
                for record in records
            ]
        }

    finally:

        db.close()


# ============================================================
# GET SINGLE FEEDBACK RECORD
# ============================================================

@router.get("/{feedback_id}")
def get_feedback(
    feedback_id: int
):

    db = get_db()

    try:

        record = (
            db.query(FeedbackRecord)
            .filter(
                FeedbackRecord.id == feedback_id
            )
            .first()
        )

        if not record:

            raise HTTPException(
                status_code=404,
                detail=(
                    f"Feedback record "
                    f"{feedback_id} not found."
                )
            )

        return format_feedback_record(
            record
        )

    finally:

        db.close()


# ============================================================
# GET ORIGINAL VIDEO
# ============================================================

@router.get("/{feedback_id}/video")
def get_feedback_video(
    feedback_id: int
):

    db = get_db()

    try:

        record = (
            db.query(FeedbackRecord)
            .filter(
                FeedbackRecord.id == feedback_id
            )
            .first()
        )

        if not record:

            raise HTTPException(
                status_code=404,
                detail=(
                    f"Feedback record "
                    f"{feedback_id} not found."
                )
            )

        video_path = Path(
            record.video_path
        )

        if not video_path.exists():

            raise HTTPException(
                status_code=404,
                detail=(
                    "Original video file "
                    "could not be found."
                )
            )

        return FileResponse(
            path=str(video_path),
            filename=record.original_filename,
            media_type="video/mp4"
        )

    finally:

        db.close()


# ============================================================
# DELETE FEEDBACK RECORD
# ============================================================

@router.delete("/{feedback_id}")
def delete_feedback(
    feedback_id: int
):

    db = get_db()

    try:

        record = (
            db.query(FeedbackRecord)
            .filter(
                FeedbackRecord.id == feedback_id
            )
            .first()
        )

        if not record:

            raise HTTPException(
                status_code=404,
                detail=(
                    f"Feedback record "
                    f"{feedback_id} not found."
                )
            )

        # --------------------------------------------
        # Delete original video
        # --------------------------------------------

        video_path = Path(
            record.video_path
        )

        if video_path.exists():

            try:
                video_path.unlink()

            except Exception as error:

                print(
                    "Warning: Could not delete "
                    f"video file: {error}"
                )

        # --------------------------------------------
        # Delete database record
        # --------------------------------------------

        db.delete(record)

        db.commit()

        return {
            "message": (
                "Feedback record deleted successfully."
            ),

            "feedback_id": feedback_id
        }

    except HTTPException:
        raise

    except Exception as error:

        db.rollback()

        raise HTTPException(
            status_code=500,
            detail=str(error)
        )

    finally:

        db.close()