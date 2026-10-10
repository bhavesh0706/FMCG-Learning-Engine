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

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.database.models import (
    Product,
    Respondent,
    SurveyAnswer,
    SurveyQuestion,
    SurveyResponse,
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

    consent_given: bool = False

    @field_validator(
        "name",
        "mobile",
        "address",
        "gender",
        "location",
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

    return {
        "id": response.id,

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

        "status": response.status,

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
    db: Session = Depends(get_db),
):
    """
    Create the customer/respondent record.

    Consent must be explicitly given.
    """

    now = datetime.utcnow()

    respondent = Respondent(
        name=body.name,
        mobile=body.mobile,
        address=body.address,
        gender=body.gender,
        age=body.age,
        location=body.location,
        consent_given=1,
        consent_at=now,
    )

    db.add(respondent)
    db.commit()
    db.refresh(respondent)

    return _respondent_out(respondent)


@router.get(
    "/respondents/{respondent_id}",
)
def get_respondent(
    respondent_id: int,
    db: Session = Depends(get_db),
):
    respondent = _get_or_404(
        db,
        Respondent,
        respondent_id,
        "Respondent",
    )

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
    db: Session = Depends(get_db),
):
    """
    Start one survey session for one respondent and one product.
    """

    product = _validate_product_for_survey(
        db,
        body.product_id,
    )

    respondent = _get_or_404(
        db,
        Respondent,
        body.respondent_id,
        "Respondent",
    )

    if not respondent.consent_given:
        raise HTTPException(
            status_code=400,
            detail=(
                "This respondent has not given consent."
            ),
        )

    response = SurveyResponse(
        product_id=product.id,
        respondent_id=respondent.id,
        executive_name=body.executive_name,
        usage_period=body.usage_period,
        location=body.location,
        latitude=body.latitude,
        longitude=body.longitude,
        status="draft",
    )

    db.add(response)
    db.commit()
    db.refresh(response)

    return _response_out(
        db,
        response,
    )


# ============================================================
# GET SURVEY RESPONSE
# ============================================================

@router.get(
    "/responses/{response_id}",
)
def get_survey_response(
    response_id: int,
    db: Session = Depends(get_db),
):
    response = _get_or_404(
        db,
        SurveyResponse,
        response_id,
        "Survey response",
    )

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
    db: Session = Depends(get_db),
):
    """
    Save or update all answers for a survey response.

    Each answer stores a snapshot of the question text/type so
    later question edits do not change historical responses.
    """

    response = _get_or_404(
        db,
        SurveyResponse,
        response_id,
        "Survey response",
    )

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

        elif question.question_type == "single_choice":

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
    db: Session = Depends(get_db),
):
    """
    Lock the question answers and mark the survey ready
    for audio/video analysis.
    """

    response = _get_or_404(
        db,
        SurveyResponse,
        response_id,
        "Survey response",
    )

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
    db.refresh(response)

    return _response_out(
        db,
        response,
    )