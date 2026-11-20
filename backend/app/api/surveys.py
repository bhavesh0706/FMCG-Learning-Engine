"""
Phase 2 survey workflow API.

Flow:

Category
    -> Product
        -> Respondent
            -> Survey Response
                -> 5-10 Questions / Answers
                    -> Submit
                        -> Audio/Video analysis is linked later

Personal respondent information is stored in the database but is not
sent to Whisper/Qwen and is not used in consolidated product reports.
"""

import re
from datetime import datetime, timedelta, timezone
from typing import List, Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.ownership import account, response_or_404, respondent_or_404, respondent_usable_or_404
from app.database.database import get_db
from app.services.rounds import RoundError, close_if_complete, round_for_new_survey
from app.database.models import (
    Product,
    Respondent,
    SurveyAnswer,
    SurveyQuestion,
    SurveyResponse,
    SurveyRound,
    User,
)


router = APIRouter(
    prefix="/surveys",
    tags=["Surveys"],
)


# ============================================================
# CONSTANTS
# ============================================================

MIN_QUESTIONS = 5
MAX_QUESTIONS = 10


# ============================================================
# HELPERS
# ============================================================

def _clean_text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None

    value = " ".join(str(value).split())

    return value or None


def _get_or_404(
    db: Session,
    model,
    object_id: int,
    label: str,
):
    obj = db.get(model, object_id)

    if not obj:
        raise HTTPException(
            status_code=404,
            detail=f"{label} not found.",
        )

    return obj


def _active_questions(
    db: Session,
    product_id: int,
) -> List[SurveyQuestion]:

    return (
        db.query(SurveyQuestion)
        .filter(
            SurveyQuestion.product_id == product_id,
            SurveyQuestion.is_active == 1,
        )
        .order_by(
            SurveyQuestion.position,
            SurveyQuestion.id,
        )
        .all()
    )


def _validate_product_for_survey(
    db: Session,
    product_id: int,
) -> Product:

    product = _get_or_404(
        db,
        Product,
        product_id,
        "Product",
    )

    if not product.is_active:
        raise HTTPException(
            status_code=400,
            detail="This product is deactivated.",
        )

    questions = _active_questions(
        db,
        product.id,
    )

    if len(questions) < MIN_QUESTIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"This product has only {len(questions)} active "
                f"questions. A survey requires at least "
                f"{MIN_QUESTIONS} questions."
            ),
        )

    if len(questions) > MAX_QUESTIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"This product has {len(questions)} active "
                f"questions. A survey allows at most "
                f"{MAX_QUESTIONS} questions."
            ),
        )

    return product


# ============================================================
# PYDANTIC REQUEST MODELS
# ============================================================

_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _iso_utc(value):
    """A stored (naive UTC) time as ISO 8601 with Z, or None."""
    return value.strftime("%Y-%m-%dT%H:%M:%SZ") if value else None


def _client_uuid(value):
    """A UUID chosen by the phone, lower-case; None when not sent."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    text_value = str(value).strip()
    if not _UUID.match(text_value):
        raise ValueError("client_uuid must be a UUID.")
    return text_value.lower()


def _replayed(db: Session, model, client_uuid: Optional[str], owner_column: str, owner_id):
    """
    The record already created for this client_uuid (a retried offline upload), or None.
    A uuid that belongs to another account is refused rather than revealed.
    """
    if not client_uuid:
        return None
    existing = db.query(model).filter(model.client_uuid == client_uuid).first()
    if existing is None:
        return None
    if getattr(existing, owner_column) != owner_id:
        raise HTTPException(status_code=409, detail="This submission id is already used by another record.")
    return existing


class RespondentCreate(BaseModel):
    name: str = Field(
        min_length=2,
        max_length=150,
    )

    mobile: Optional[str] = Field(
        default=None,
        max_length=20,
    )

    address: Optional[str] = Field(
        default=None,
        max_length=2000,
    )

    gender: Optional[str] = Field(
        default=None,
        max_length=30,
    )

    age: Optional[int] = Field(
        default=None,
        ge=1,
        le=120,
    )

    location: Optional[str] = Field(
        default=None,
        max_length=200,
    )

    # Next update: country, occupation and class (masters), respondent type (master)
    country: Optional[str] = Field(default=None, max_length=80)
    # From the LOV lists STATE and CITY (inside the chosen country / state). Optional,
    # so app versions without these fields keep working.
    state: Optional[str] = Field(default=None, max_length=120)
    city: Optional[str] = Field(default=None, max_length=120)
    occupation: Optional[str] = Field(default=None, max_length=80)
    sec_class: Optional[str] = Field(default=None, max_length=40)
    respondent_type: Optional[str] = Field(default=None, max_length=60)

    consent_given: bool = False

    # Offline mode: the phone's id for this customer. Sending the same id again
    # (a retried upload) returns the record already created instead of a duplicate.
    client_uuid: Optional[str] = None

    @field_validator("client_uuid", mode="before")
    @classmethod
    def check_client_uuid(cls, value):
        return _client_uuid(value)

    @field_validator(
        "name",
        "mobile",
        "address",
        "gender",
        "location",
        "country",
        "state",
        "city",
        "occupation",
        "sec_class",
        "respondent_type",
        mode="before",
    )
    @classmethod
    def clean_text(cls, value):
        return _clean_text(value)

    @field_validator("consent_given")
    @classmethod
    def require_consent(cls, value):
        if not value:
            raise ValueError(
                "Customer consent is required before starting the survey."
            )

        return value


class SurveyResponseCreate(BaseModel):
    product_id: int

    respondent_id: int
    # Internal (staff, family) or External (real customers) survey
    audience: Optional[Literal["internal", "external"]] = None

    # The product version the customer tasted (recipe_versions.version). Optional:
    # without it the survey joins the product's live round, as before.
    recipe_version: Optional[int] = Field(
        default=None,
        ge=1,
    )

    executive_name: Optional[str] = Field(
        default=None,
        max_length=150,
    )

    usage_period: Optional[str] = Field(
        default=None,
        max_length=50,
    )

    location: Optional[str] = Field(
        default=None,
        max_length=200,
    )

    latitude: Optional[float] = Field(
        default=None,
        ge=-90,
        le=90,
    )

    longitude: Optional[float] = Field(
        default=None,
        ge=-180,
        le=180,
    )

    # The GPS fix (same on Android, iOS and the web): accuracy radius in metres,
    # when it was taken (ISO 8601; an offline survey keeps its original time) and
    # where: android | ios | web.
    location_accuracy: Optional[float] = Field(default=None, ge=0, le=100000)
    location_captured_at: Optional[datetime] = None
    location_source: Optional[str] = None

    # Offline mode: the phone's submission id (see RespondentCreate.client_uuid).
    client_uuid: Optional[str] = None

    @field_validator("client_uuid", mode="before")
    @classmethod
    def check_client_uuid(cls, value):
        return _client_uuid(value)

    @field_validator("location_source", mode="before")
    @classmethod
    def clean_source(cls, value):
        value = (str(value).strip().lower() if value is not None else "") or None
        return value if value in (None, "android", "ios", "web") else None  # an unknown platform isn't an error

    @field_validator("location_captured_at")
    @classmethod
    def utc_capture_time(cls, value):
        # Stored as naive UTC like every other time. A time in the future (a phone with a
        # wrong clock) or before 2020 is dropped, never a reason to refuse the survey.
        if value is None:
            return None
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc).replace(tzinfo=None)
        if value > datetime.utcnow() + timedelta(minutes=10) or value.year < 2020:
            return None
        return value

    @model_validator(mode="after")
    def coordinates_together(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Send both latitude and longitude, or neither.")
        if self.latitude is None:
            # No position: accuracy, time and platform mean nothing on their own.
            self.location_accuracy = self.location_captured_at = self.location_source = None
        return self

    @field_validator(
        "executive_name",
        "usage_period",
        "location",
        mode="before",
    )
    @classmethod
    def clean_text(cls, value):
        return _clean_text(value)


class AnswerItem(BaseModel):
    question_id: int

    value: Optional[str] = Field(
        default=None,
        max_length=5000,
    )

    @field_validator("value", mode="before")
    @classmethod
    def clean_value(cls, value):
        if value is None:
            return None

        return str(value).strip()


class AnswersUpdate(BaseModel):
    answers: List[AnswerItem] = Field(
        min_length=1,
        max_length=MAX_QUESTIONS,
    )


# ============================================================
# SERIALIZERS
# ============================================================

def _respondent_out(respondent: Respondent) -> dict:
    return {
        "id": respondent.id,
        "name": respondent.name,
        "mobile": respondent.mobile,
        "address": respondent.address,
        "gender": respondent.gender,
        "age": respondent.age,
        "location": respondent.location,
        "country": respondent.country,
        "state": respondent.state,
        "city": respondent.city,
        "occupation": respondent.occupation,
        "sec_class": respondent.sec_class,
        "respondent_type": respondent.respondent_type,
        "consent_given": bool(respondent.consent_given),
        "consent_at": respondent.consent_at,
        "created_at": respondent.created_at,
    }


def _question_out(question: SurveyQuestion) -> dict:
    return {
        "id": question.id,
        "product_id": question.product_id,
        "position": question.position,
        "text": question.text,
        "question_type": question.question_type,
        "options": question.options,
        "required": bool(question.required),
        "is_active": bool(question.is_active),
    }


def _answer_out(answer: SurveyAnswer) -> dict:
    return {
        "id": answer.id,
        "response_id": answer.response_id,
        "question_id": answer.question_id,
        "question_text": answer.question_text,
        "question_type": answer.question_type,
        "value": answer.value,
        "created_at": answer.created_at,
    }


def _response_out(
    db: Session,
    response: SurveyResponse,
) -> dict:

    product = db.get(
        Product,
        response.product_id,
    )

    respondent = db.get(
        Respondent,
        response.respondent_id,
    )

    answers = (
        db.query(SurveyAnswer)
        .filter(
            SurveyAnswer.response_id == response.id
        )
        .order_by(
            SurveyAnswer.question_id
        )
        .all()
    )

    survey_round = db.get(SurveyRound, response.round_id) if response.round_id else None

    return {
        "id": response.id,
        # The survey round it is filed under ("Round 2"), for the apps to show.
        "round": survey_round.number if survey_round else None,
        "recipe_version": (
            getattr(response, "recipe_version", None)
            or (survey_round.recipe_version if survey_round else None)
        ),

        "product": {
            "id": product.id if product else None,
            "name": product.name if product else None,
            "category_id": product.category_id if product else None,
        },

        "respondent": (
            _respondent_out(respondent)
            if respondent
            else None
        ),

        "executive_name": response.executive_name,
        "usage_period": response.usage_period,
        "location": response.location,
        "latitude": response.latitude,
        "longitude": response.longitude,
        "location_accuracy": getattr(response, "location_accuracy", None),
        "location_captured_at": _iso_utc(getattr(response, "location_captured_at", None)),
        "location_source": getattr(response, "location_source", None),

        "status": response.status,
        "audience": getattr(response, "audience", None),
        # Report review: generated / pending_admin / forwarded / returned / reviewed
        "report_status": getattr(response, "report_status", None),

        "analysis": response.analysis,
        "overall_rating": response.overall_rating,
        "overall_sentiment": response.overall_sentiment,

        "created_at": response.created_at,
        "submitted_at": response.submitted_at,
        "analyzed_at": response.analyzed_at,

        "answers": [
            _answer_out(answer)
            for answer in answers
        ],
    }


# ============================================================
# RESPONDENT
# ============================================================

@router.post(
    "/respondents",
    status_code=201,
)
def create_respondent(
    body: RespondentCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Create the customer/respondent record.

    Consent must be explicitly given.
    """

    now = datetime.utcnow()

    me_id = account(request).get("id")
    existing = _replayed(db, Respondent, body.client_uuid, "created_by", me_id)
    if existing is not None:
        return _respondent_out(existing)

    respondent = Respondent(
        client_uuid=body.client_uuid,
        name=body.name,
        mobile=body.mobile,
        address=body.address,
        gender=body.gender,
        age=body.age,
        location=body.location,
        country=body.country,
        state=body.state,
        city=body.city,
        occupation=body.occupation,
        sec_class=body.sec_class,
        respondent_type=body.respondent_type,
        consent_given=1,
        consent_at=now,
        # The account that recorded this customer (record-level access, ownership.py).
        created_by=me_id,
    )

    db.add(respondent)
    try:
        db.commit()
    except IntegrityError:
        # The same offline submission arrived twice at the same moment: return the first one.
        db.rollback()
        existing = _replayed(db, Respondent, body.client_uuid, "created_by", me_id)
        if existing is None:
            raise
        return _respondent_out(existing)
    db.refresh(respondent)

    return _respondent_out(respondent)


@router.get(
    "/respondents/{respondent_id}",
)
def get_respondent(
    respondent_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    # Admins, the account that recorded the customer, or the owner of a survey with them.
    respondent = respondent_or_404(db, request, respondent_id)

    return _respondent_out(respondent)


# ============================================================
# START SURVEY RESPONSE
# ============================================================

@router.post(
    "/responses",
    status_code=201,
)
def create_survey_response(
    body: SurveyResponseCreate,
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Start one survey session for one respondent and one product.
    """

    # A retried offline upload: return the survey already created for this submission.
    existing = _replayed(db, SurveyResponse, body.client_uuid, "user_id", account(request).get("id"))
    if existing is not None:
        return _response_out(db, existing)

    product = _validate_product_for_survey(
        db,
        body.product_id,
    )

    # Only a customer this account recorded (no one else's customer can be reused).
    respondent = respondent_usable_or_404(db, request, body.respondent_id)

    if not respondent.consent_given:
        raise HTTPException(
            status_code=400,
            detail=(
                "This respondent has not given consent."
            ),
        )

    me = account(request)
    # Only the product's live round assigned to this person (services/product_access.py);
    # rounds are started by the Super Admin, never here.
    me_user = db.get(User, me.get("id")) if me.get("id") else None
    try:
        survey_round = round_for_new_survey(db, product.id, body.recipe_version, user=me_user)
    except RoundError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    # Action plans: a team member surveys only the cities allocated to them
    from app.services.action_plans import PlanError, plan_for_survey

    try:
        action_plan_id = plan_for_survey(db, survey_round.id if survey_round else None, me_user, respondent.city)
    except PlanError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    response = SurveyResponse(
        product_id=product.id,
        round_id=survey_round.id if survey_round else None,
        action_plan_id=action_plan_id,
        recipe_version=survey_round.recipe_version if survey_round else body.recipe_version,
        respondent_id=respondent.id,
        # The survey belongs to the signed-in account from the start
        # (before, the apps had to "claim" it in a second call).
        user_id=me.get("id"),
        executive_name=(body.executive_name or "").strip() or me.get("name"),
        usage_period=body.usage_period,
        location=body.location,
        latitude=body.latitude,
        longitude=body.longitude,
        location_accuracy=round(body.location_accuracy, 1) if body.location_accuracy is not None else None,
        location_captured_at=body.location_captured_at,
        location_source=body.location_source,
        audience=body.audience,
        status="draft",
        client_uuid=body.client_uuid,
    )

    db.add(response)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = _replayed(db, SurveyResponse, body.client_uuid, "user_id", me.get("id"))
        if existing is None:
            raise
        return _response_out(db, existing)
    db.refresh(response)
    # Taken offline: the place fields hold the GPS position ("GPS 28.60, 77.43 ...").
    # Look the address up now that the survey reached the server (after the reply).
    from app.api.geo import is_gps_placeholder

    if response.latitude is not None and (is_gps_placeholder(response.location) or is_gps_placeholder(respondent.location)):
        background.add_task(fill_places_from_gps, response.id)

    return _response_out(
        db,
        response,
    )


def fill_places_from_gps(response_id: int) -> None:
    """Replaces GPS placeholders of an offline survey with the address of its position:
    the survey location, and the customer's area (plus country/state/city when empty).
    Best effort: without an address the position text stays (the coordinates are saved)."""
    from app.api.geo import is_gps_placeholder, reverse_place
    from app.database.database import SessionLocal

    db = SessionLocal()
    try:
        response = db.get(SurveyResponse, response_id)
        if response is None or response.latitude is None:
            return
        respondent = db.get(Respondent, response.respondent_id) if response.respondent_id else None
        try:
            place = reverse_place(response.latitude, response.longitude)
        except HTTPException:
            return
        label = (place.get("label") or "").strip()
        if label and is_gps_placeholder(response.location):
            response.location = label[:200]
        if respondent is not None and is_gps_placeholder(respondent.location):
            respondent.location = ((place.get("locality") or label).strip() or respondent.location)[:200]
            for field, size in (("country", 80), ("state", 120), ("city", 120)):
                if not (getattr(respondent, field, None) or "").strip() and place.get(field):
                    setattr(respondent, field, str(place[field])[:size])
        db.commit()
    finally:
        db.close()


# ============================================================
# GET SURVEY RESPONSE
# ============================================================

@router.get(
    "/responses/{response_id}",
)
def get_survey_response(
    response_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    response = response_or_404(db, request, response_id)

    return _response_out(
        db,
        response,
    )


# ============================================================
# SAVE / UPDATE ANSWERS
# ============================================================

@router.put(
    "/responses/{response_id}/answers",
)
def save_answers(
    response_id: int,
    body: AnswersUpdate,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Save or update all answers for a survey response.

    Each answer stores a snapshot of the question text/type so
    later question edits do not change historical responses.
    """

    response = response_or_404(db, request, response_id)

    if response.status == "analyzed":
        raise HTTPException(
            status_code=400,
            detail=(
                "This survey response has already been analyzed "
                "and cannot be edited."
            ),
        )

    questions = _active_questions(
        db,
        response.product_id,
    )

    questions_by_id = {
        question.id: question
        for question in questions
    }

    submitted_ids = set()

    for item in body.answers:

        if item.question_id in submitted_ids:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Question {item.question_id} "
                    "was submitted more than once."
                ),
            )

        submitted_ids.add(item.question_id)

        question = questions_by_id.get(
            item.question_id
        )

        if not question:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Question {item.question_id} "
                    "does not belong to this product."
                ),
            )

        value = item.value.strip() if item.value else None

        # Validate answer according to question type.
        if question.question_type == "rating":

            if value is not None:

                try:
                    rating = float(value)
                except ValueError:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Question {question.position} "
                            "requires a rating from 1 to 5."
                        ),
                    )

                if rating < 1 or rating > 5:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            f"Question {question.position} "
                            "requires a rating from 1 to 5."
                        ),
                    )

        elif question.question_type == "yes_no":

            if value is not None and (
                value.lower() not in {
                    "yes",
                    "no",
                }
            ):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f'Question {question.position} '
                        'expects "Yes" or "No".'
                    ),
                )

        elif question.question_type == "multiple_choice":

            # Any number of options, stored as "Option A | Option B".
            allowed = {str(option).strip().lower(): str(option).strip() for option in (question.options or [])}
            if value is not None:
                picked = [v.strip() for v in value.split("|") if v.strip()]
                if not picked or any(v.lower() not in allowed for v in picked):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Answers for question {question.position} must be among the available options.",
                    )
                value = " | ".join(dict.fromkeys(allowed[v.lower()] for v in picked))

        elif question.question_type in ("single_choice", "preference"):

            allowed = {
                str(option).strip().lower()
                for option in (question.options or [])
            }

            if (
                value is not None
                and value.lower() not in allowed
            ):
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Answer for question "
                        f"{question.position} "
                        "must be one of the available options."
                    ),
                )

        # Look for existing answer.
        existing = (
            db.query(SurveyAnswer)
            .filter(
                SurveyAnswer.response_id == response.id,
                SurveyAnswer.question_id == question.id,
            )
            .first()
        )

        if existing:
            existing.value = value
            existing.question_text = question.text
            existing.question_type = question.question_type

        else:
            db.add(
                SurveyAnswer(
                    response_id=response.id,
                    question_id=question.id,
                    question_text=question.text,
                    question_type=question.question_type,
                    value=value,
                )
            )

    db.commit()

    return _response_out(
        db,
        response,
    )


# ============================================================
# SUBMIT SURVEY QUESTIONS
# ============================================================

@router.post(
    "/responses/{response_id}/submit",
)
def submit_survey_response(
    response_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Lock the question answers and mark the survey ready
    for audio/video analysis.
    """

    response = response_or_404(db, request, response_id)

    if response.status == "analyzed":
        raise HTTPException(
            status_code=400,
            detail="This survey response is already analyzed.",
        )

    questions = _active_questions(
        db,
        response.product_id,
    )

    answers = (
        db.query(SurveyAnswer)
        .filter(
            SurveyAnswer.response_id == response.id
        )
        .all()
    )

    answers_by_question = {
        answer.question_id: answer
        for answer in answers
    }

    missing = []

    for question in questions:

        answer = answers_by_question.get(
            question.id
        )

        if (
            question.required
            and (
                not answer
                or not answer.value
                or not str(answer.value).strip()
            )
        ):
            missing.append(
                question.position
            )

    if missing:
        raise HTTPException(
            status_code=400,
            detail=(
                "Complete all required questions before "
                f"submitting the survey. Missing question(s): "
                f"{', '.join(map(str, missing))}."
            ),
        )

    response.status = "submitted"
    response.submitted_at = datetime.utcnow()

    db.commit()
    # The round closes by itself once enough surveys are submitted.
    close_if_complete(db, response.round_id)
    db.refresh(response)

    return _response_out(
        db,
        response,
    )