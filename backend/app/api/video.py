from pathlib import Path
from datetime import datetime
import json
import os
import time
import re

from fastapi import (
    APIRouter,
    File,
    UploadFile,
    HTTPException,
    Form,
    Request,
)

from app.api.ownership import owns_response
from app.api.uploads_limit import safe_name, save_upload
from sqlalchemy.orm import Session

from app.database.database import SessionLocal, init_db
from app.database.models import (
    FeedbackRecord,
    FeedbackAspect,
    FeedbackRatingRule,
    SurveyResponse,
)

# Whisper, FFmpeg and Qwen now run in the AI Model Service (its own port).
# The backend sends the recording there over HTTP and gets the raw results back.
from app.services import ai_client


# ============================================================
# DATABASE INITIALIZATION
# ============================================================

init_db()


# ============================================================
# ROUTER
# ============================================================

router = APIRouter(
    prefix="/video",
    tags=["Video"]
)


# ============================================================
# DIRECTORIES
# ============================================================

PROJECT_ROOT = (
    Path(__file__).resolve().parents[3]
)

# FMCG_RECORDINGS_DIR moves the stored recordings (e.g. to a mounted disk).
VIDEO_DIR = Path(
    os.environ.get("FMCG_RECORDINGS_DIR")
    or PROJECT_ROOT / "uploads" / "videos"
)

VIDEO_DIR.mkdir(
    parents=True,
    exist_ok=True
)

# Temporary audio is handled by the AI service (ai_service/work).


# The no-speech results (NO_SPEECH_PART_SUMMARY / NO_SPEECH_SUMMARY) moved to
# ai_service/pipeline.py together with the rest of the AI steps.


# ============================================================
# TIME FORMATTER
# ============================================================

def format_seconds(seconds: int):

    minutes = seconds // 60

    remaining_seconds = seconds % 60

    return (
        f"{minutes:02d}:"
        f"{remaining_seconds:02d}"
    )


# ============================================================
# EXTRACT OVERALL SUMMARY TEXT
# ============================================================

def get_summary_text(summary):
    """
    Safely extract the main summary text from the
    response returned by Ollama.
    """

    if isinstance(summary, dict):

        value = summary.get(
            "summary",
            ""
        )

        if value:
            return str(value)

        return json.dumps(
            summary,
            ensure_ascii=False
        )

    if summary is None:
        return ""

    return str(summary)


# ============================================================
# EXTRACT OVERALL SENTIMENT
# ============================================================

def get_summary_sentiment(summary):
    """
    Safely extract sentiment from the overall
    Ollama analysis.
    """

    if isinstance(summary, dict):

        sentiment = summary.get(
            "sentiment",
            "Neutral"
        )

        if sentiment:
            return str(sentiment)

    return "Neutral"


# ============================================================
# ASPECT MATCHING & RATING LOOKUP HELPERS
# ============================================================

def normalize_aspect_text(value: str) -> str:
    if not value:
        return ""

    value = value.lower().strip()

    value = re.sub(
        r"[^a-z0-9\s]",
        " ",
        value
    )

    value = re.sub(
        r"\s+",
        " ",
        value
    )

    return value.strip()


def match_aspect(db: Session, raw_aspect: str):
    """
    Match raw aspect string to feedback_aspects table using:
    1. aspect_key exact match (normalized)
    2. aspect_name exact match (normalized)
    3. keyword match (normalized)
    4. None if no match found.
    """
    if not raw_aspect:
        return None

    cleaned_aspect = normalize_aspect_text(raw_aspect)

    # 1. aspect_key exact match
    aspect_rec = db.query(FeedbackAspect).filter(FeedbackAspect.aspect_key == cleaned_aspect).first()
    if aspect_rec:
        return aspect_rec

    # 2. aspect_name exact match using normalized form or ilike
    all_aspects = db.query(FeedbackAspect).all()
    for asp in all_aspects:
        if asp.aspect_name and normalize_aspect_text(asp.aspect_name) == cleaned_aspect:
            return asp

    # 3. keyword match using normalized text comparison
    for asp in all_aspects:
        if asp.keywords:
            keywords_list = []
            if isinstance(asp.keywords, list):
                keywords_list = [normalize_aspect_text(k) for k in asp.keywords]
            elif isinstance(asp.keywords, str):
                try:
                    parsed = json.loads(asp.keywords)
                    if isinstance(parsed, list):
                        keywords_list = [normalize_aspect_text(k) for k in parsed]
                    else:
                        keywords_list = [normalize_aspect_text(k) for k in asp.keywords.split(",")]
                except Exception:
                    keywords_list = [normalize_aspect_text(k) for k in asp.keywords.split(",")]
            
            if cleaned_aspect in keywords_list or any(kw in cleaned_aspect for kw in keywords_list if kw):
                return asp

    return None


def lookup_rating(db: Session, sentiment: str, severity: str):
    """
    Lookup rating from feedback_rating_rules where is_active = 1
    """
    if not sentiment or not severity:
        return None

    rule = db.query(FeedbackRatingRule).filter(
        FeedbackRatingRule.is_active == 1,
        FeedbackRatingRule.sentiment.ilike(sentiment.strip()),
        FeedbackRatingRule.severity.ilike(severity.strip())
    ).first()

    if rule and rule.rating is not None:
        return float(rule.rating)

    return None


def process_part_feedback(db: Session, raw_feedback_list):
    """
    Process each raw feedback item from Qwen chunk summary,
    match aspect, lookup rating rule, and build enriched feedback objects.
    """
    processed_feedback = []
    if not isinstance(raw_feedback_list, list):
        return processed_feedback

    for item in raw_feedback_list:
        if not isinstance(item, dict):
            continue

        raw_aspect = item.get("aspect", "")
        sentiment = item.get("sentiment", "Neutral")
        severity = item.get("severity", "Moderate")
        confidence = item.get("confidence", 0.8)
        evidence = item.get("evidence", item.get("text", ""))
        product = item.get("product", "")

        matched_aspect = match_aspect(db, raw_aspect)
        
        aspect_key = matched_aspect.aspect_key if matched_aspect else normalize_aspect_text(raw_aspect).replace(" ", "_")
        aspect_name = matched_aspect.aspect_name if matched_aspect else raw_aspect
        category = matched_aspect.category if matched_aspect else "General"

        rating = lookup_rating(db, sentiment, severity)

        processed_feedback.append({
            "product": product,
            "aspect": raw_aspect,
            "aspect_key": aspect_key,
            "aspect_name": aspect_name,
            "category": category,
            "sentiment": sentiment,
            "severity": severity,
            "confidence": float(confidence),
            "rating": rating,
            "evidence": evidence
        })

    return processed_feedback


def aggregate_feedback_by_aspect(chapters):
    """
    Combine repeated mentions of the same canonical aspect.

    Example:
        Part 1 -> price -> 2.0
        Part 2 -> price -> 2.0

    becomes:

        price -> one aggregated rating

    This prevents the same aspect from being counted multiple
    times just because it appears in different video parts.
    """

    grouped = {}

    for chapter in chapters:

        feedback_items = chapter.get(
            "feedback",
            []
        )

        for item in feedback_items:

            aspect_key = item.get(
                "aspect_key"
            )

            if not aspect_key:
                continue

            rating = item.get(
                "rating"
            )

            confidence = item.get(
                "confidence",
                0.0
            )

            try:
                rating = float(rating)
                confidence = float(confidence)
            except (TypeError, ValueError):
                continue

            if rating < 1 or rating > 5:
                continue

            if confidence <= 0:
                continue

            if aspect_key not in grouped:
                grouped[aspect_key] = []

            grouped[aspect_key].append(
                item
            )

    aggregated = []

    for aspect_key, items in grouped.items():

        weighted_total = 0.0
        confidence_total = 0.0

        for item in items:

            rating = float(
                item["rating"]
            )

            confidence = float(
                item["confidence"]
            )

            weighted_total += (
                rating * confidence
            )

            confidence_total += confidence

        if confidence_total <= 0:
            continue

        aggregated_rating = round(
            weighted_total / confidence_total,
            2
        )

        # Use the strongest-confidence mention as the
        # representative metadata for this aspect.
        representative = max(
            items,
            key=lambda item: float(
                item.get(
                    "confidence",
                    0.0
                )
            )
        )

        # Average confidence instead of summing it.
        # This prevents repeated mentions from automatically
        # giving the aspect disproportionate weight.
        average_confidence = round(
            sum(
                float(
                    item.get(
                        "confidence",
                        0.0
                    )
                )
                for item in items
            )
            / len(items),
            3
        )

        aggregated.append(
            {
                "aspect_key": aspect_key,

                "aspect_name": representative.get(
                    "aspect_name",
                    aspect_key
                ),

                "category": representative.get(
                    "category",
                    "General"
                ),

                "rating": aggregated_rating,

                "confidence": average_confidence,

                "mentions": len(items),

                "sentiment": representative.get(
                    "sentiment",
                    "Neutral"
                ),

                "severity": representative.get(
                    "severity",
                    "None"
                )
            }
        )

    return aggregated


def calculate_overall_rating(aspect_ratings):
    """
    Calculate the final product rating using one score
    per unique feedback aspect.

    Each unique aspect contributes once to the final rating.
    """

    weighted_total = 0.0
    total_confidence = 0.0

    for aspect in aspect_ratings:

        rating = aspect.get(
            "rating"
        )

        confidence = aspect.get(
            "confidence",
            0.0
        )

        try:
            rating = float(rating)
            confidence = float(confidence)
        except (TypeError, ValueError):
            continue

        if rating < 1 or rating > 5:
            continue

        if confidence <= 0:
            continue

        weighted_total += (
            rating * confidence
        )

        total_confidence += confidence

    if total_confidence == 0:
        return None

    return round(
        weighted_total / total_confidence,
        2
    )


# ============================================================
# SAVE FEEDBACK RECORD
# ============================================================

def get_next_feedback_id(db: Session) -> int:
    """
    Return the smallest available positive feedback ID.
    Example:
        Existing IDs: 1,2,3,5,6
        Next ID:      4
        Existing IDs: 1..20,22..60
        Next ID:      21
        Existing IDs: 1..60
        Next ID:      61
    """
    existing_ids = [
        row[0]
        for row in (
            db.query(FeedbackRecord.id)
            .order_by(FeedbackRecord.id.asc())
            .all()
        )
        if row[0] is not None
    ]
    next_id = 1
    for existing_id in existing_ids:
        existing_id = int(existing_id)
        if existing_id == next_id:
            next_id += 1
        elif existing_id > next_id:
            break
    return next_id


def save_feedback_record(
    original_filename,
    video_path,
    duration_seconds,
    processing_time_seconds,
    overall_rating,
    summary,
    transcript,
    chapters,
    response_id=None,
    aspect_ratings=None,
):
    """
    Save the completed feedback analysis
    into SQLite.
    """

    db: Session = SessionLocal()

    try:

        response = None

        if response_id is not None:
            response = (
                db.query(SurveyResponse)
                .filter(
                    SurveyResponse.id == response_id
                )
                .first()
            )

            if response is None:
                raise ValueError(
                    f"Survey response {response_id} was not found."
                )

        analysis_payload = (
            dict(summary)
            if isinstance(summary, dict)
            else {
                "summary": str(summary)
            }
        )

        analysis_payload["aspect_ratings"] = (
            aspect_ratings or []
        )

        analysis_payload["chapters"] = chapters

        next_feedback_id = get_next_feedback_id(db)

        record = FeedbackRecord(

            id=next_feedback_id,

            original_filename=(
                original_filename
            ),

            video_path=str(
                video_path
            ),

            duration_seconds=(
                float(duration_seconds)
            ),

            processing_time_seconds=(
                float(processing_time_seconds)
            ),

            overall_summary=(
                get_summary_text(summary)
            ),

            overall_sentiment=(
                get_summary_sentiment(summary)
            ),

            overall_rating=(
                float(overall_rating)
                if overall_rating is not None
                else None
            ),

            transcript=(
                transcript
            ),

            chapters=(
                chapters
            ),

            response_id=(
                response_id
            ),
        )

        db.add(record)

        if response is not None:
            response.analysis = analysis_payload

            response.overall_rating = (
                float(overall_rating)
                if overall_rating is not None
                else None
            )

            response.overall_sentiment = (
                get_summary_sentiment(summary)
            )

            response.status = "analyzed"
            response.analyzed_at = datetime.utcnow()

        db.commit()

        db.refresh(record)

        print(
            f"Feedback record saved. "
            f"Database ID: {record.id}"
        )

        return record.id

    except Exception:

        db.rollback()

        raise

    finally:

        db.close()


# ============================================================
# UPLOAD VIDEO
# ============================================================

@router.post("/upload")
async def upload_video(
    request: Request,
    file: UploadFile = File(...),
    response_id: int | None = Form(None),
):

    total_start = time.time()

    if response_id is not None:

        validation_db: Session = SessionLocal()

        try:

            survey_response = (
                validation_db.query(SurveyResponse)
                .filter(
                    SurveyResponse.id == response_id
                )
                .first()
            )

            # Only the survey's owner (or an admin); others get the same 404.
            # request is None only when the analysis worker calls this function
            # directly for a job that was already checked when it was queued.
            if survey_response is None or (request is not None and not owns_response(request, survey_response)):
                raise HTTPException(
                    status_code=404,
                    detail=(
                        f"Survey response {response_id} "
                        "was not found."
                    )
                )

            if survey_response.status != "submitted":
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "Survey response must be submitted "
                        "before uploading feedback media."
                    )
                )

        finally:

            validation_db.close()

    print("\n")
    print("=" * 70)
    print("NEW VIDEO UPLOAD")
    print("=" * 70)

    print(
        f"Filename: {file.filename}"
    )

    # --------------------------------------------------------
    # Validate extension
    # --------------------------------------------------------

    allowed_extensions = {
        # Video
        ".mp4",
        ".mov",
        ".avi",
        ".mkv",
        ".webm",

        # Audio
        ".mp3",
        ".wav",
        ".m4a",
        ".ogg",
        ".weba",
    }

    extension = (
        Path(file.filename)
        .suffix
        .lower()
    )

    if extension not in allowed_extensions:

        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported media format. "
                "Use MP4, MOV, AVI, MKV, WEBM, MP3, WAV, M4A, OGG or WEBA."
            )
        )

    # --------------------------------------------------------
    # Save uploaded video
    # --------------------------------------------------------

    # Size-capped copy (FMCG_MAX_UPLOAD_MB); 413 above the cap, 400 when empty.
    video_path = save_upload(file, VIDEO_DIR, safe_name(file.filename))

    try:

        print(
            "Video saved successfully."
        )

        # ----------------------------------------------------
        # AI analysis in the AI Model Service
        # (duration check, audio extraction, 3-minute parts,
        #  Whisper + Qwen per part, overall summary)
        # ----------------------------------------------------

        print("\n")
        print(
            "Sending recording to the AI service..."
        )

        try:
            ai = await ai_client.analyze_media(
                video_path,
                file.filename,
            )
        except ai_client.AIServiceError as ai_error:
            # The file can't be opened: remove it, as before. (Too short /
            # too long recordings were kept before, so they still are.)
            if ai_error.code == "unreadable":
                video_path.unlink(missing_ok=True)
            print(f"AI service error ({ai_error.status_code}): {ai_error.detail}"[:500])
            raise HTTPException(status_code=ai_error.status_code, detail=ai_error.detail)

        duration_seconds = ai["duration_seconds"]
        duration_formatted = ai["duration_formatted"]
        part_seconds = ai["part_duration_seconds"]
        total_parts = ai["total_parts"]
        transcript = ai["transcript"]
        complete_transcript = ai["complete_transcript"]
        speech_detected = ai["speech_detected"]
        summary = ai["summary"]

        print(
            f"Video Length: "
            f"{duration_formatted}"
        )

        print(
            f"AI analysis completed in "
            f"{ai.get('ai_processing_seconds')}s "
            f"({total_parts} parts)."
        )

        # ====================================================
        # MATCH ASPECTS & RATINGS FOR EACH PART (MySQL)
        # ====================================================

        chapters = []

        db_session: Session = SessionLocal()

        try:
            for part in ai["parts"]:

                part_summary = part.get("part_summary") or {}

                # ------------------------------------------------
                # Process feedback & ratings for this part
                # ------------------------------------------------
                raw_feedback = part_summary.get("feedback", [])
                processed_feedback = process_part_feedback(db_session, raw_feedback)

                # ------------------------------------------------
                # Store chapter
                # ------------------------------------------------

                chapters.append(
                    {
                        "part": part["part"],

                        "start": part["start"],

                        "end": part["end"],

                        "summary": part_summary.get(
                            "summary",
                            "No summary generated."
                        ),

                        "sentiment": part_summary.get(
                            "sentiment",
                            "Neutral"
                        ),

                        "key_point": part_summary.get(
                            "key_point",
                            "No key takeaway generated."
                        ),

                        "transcript": part["transcript"],

                        "feedback": processed_feedback,
                    }
                )
        finally:
            db_session.close()

        # --------------------------------------------------------
        # Aggregate repeated feedback aspects
        # --------------------------------------------------------

        aspect_ratings = (
            aggregate_feedback_by_aspect(
                chapters
            )
        )

        # --------------------------------------------------------
        # Calculate overall product rating
        # --------------------------------------------------------

        overall_rating = (
            calculate_overall_rating(
                aspect_ratings
            )
        )

        print(
            f"Unique feedback aspects: "
            f"{len(aspect_ratings)}"
        )

        for aspect in aspect_ratings:

            print(
                f"  {aspect['aspect_name']} "
                f"-> {aspect['rating']} "
                f"({aspect['mentions']} mention(s))"
            )

        # ====================================================
        # TOTAL PROCESSING TIME
        # ====================================================

        total_time = round(
            time.time()
            - total_start,
            2
        )

        # ====================================================
        # SAVE TO DATABASE
        # ====================================================

        print("\n")
        print(
            "=" * 70
        )

        print(
            "SAVING FEEDBACK RECORD..."
        )

        print(
            "=" * 70
        )

        feedback_id = save_feedback_record(
            original_filename=file.filename,
            video_path=video_path,
            duration_seconds=duration_seconds,
            processing_time_seconds=total_time,
            overall_rating=overall_rating,
            summary=summary,
            transcript=complete_transcript,
            chapters=chapters,
            response_id=response_id,
            aspect_ratings=aspect_ratings,
        )

        # ====================================================
        # FINAL LOG
        # ====================================================

        print("\n")
        print("=" * 70)
        print("PROCESS COMPLETED")
        print("=" * 70)

        print(
            f"Feedback ID    : "
            f"{feedback_id}"
        )

        print(
            f"Video Length    : "
            f"{duration_formatted}"
        )

        print(
            f"Part Size       : "
            f"{part_seconds // 60} minutes"
        )

        print(
            f"Parts Analyzed  : "
            f"{total_parts}"
        )

        print(
            f"Speech detected : "
            f"{'yes' if speech_detected else 'no'}"
        )

        print(
            f"Overall Rating  : "
            f"{overall_rating}"
        )

        print(
            f"Processing Time : "
            f"{total_time}s"
        )

        print("=" * 70)

        # ====================================================
        # API RESPONSE
        # ====================================================

        return {

            # Database identifier
            "feedback_id":
                feedback_id,

            # Original filename shown to user
            "filename":
                file.filename,

            "video_duration":
                duration_formatted,

            "video_duration_seconds":
                duration_seconds,

            "part_duration_seconds":
                part_seconds,

            "part_duration":
                f"{part_seconds // 60} minutes",

            "parts_analyzed":
                total_parts,

            # Kept so your current frontend
            # continues working.
            "chunks_processed":
                total_parts,

            # False when Whisper found no recognisable speech.
            "speech_detected":
                speech_detected,

            "overall_rating":
                overall_rating,

            "aspect_ratings":
                aspect_ratings,

            "processing_time_seconds":
                total_time,

            "transcript":
                transcript,

            "summary":
                summary,

            "chapters":
                chapters,
        }

    except HTTPException:
        raise

    except Exception as error:

        print("\n")
        print("=" * 70)
        print("VIDEO PROCESSING ERROR")
        print("=" * 70)

        print(
            repr(error)
        )

        print("=" * 70)

        raise HTTPException(
            status_code=500,
            detail=str(error)
        )

    finally:
        await file.close()