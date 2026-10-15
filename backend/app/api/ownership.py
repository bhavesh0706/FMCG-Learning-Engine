"""
Record-level access: who may see or change which survey, customer, recording
and analysis job.

The access middleware (access.py) decides WHICH KIND of account may call a
route (public / signed-in / admin). This module decides WHOSE RECORD a
signed-in survey person may reach:

    admin            every record
    survey person    only surveys whose user_id is their own account, and the
                     customers, recordings and jobs that belong to those surveys

Anything else answers 404 "not found", exactly like a record that doesn't
exist, so record numbers can't be probed one by one.
"""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException, Request
from sqlalchemy.orm import Session

from app.database.models import AnalysisJob, FeedbackRecord, Respondent, SurveyResponse


def account(request: Request) -> dict:
    """The signed-in account set by the access middleware: {id, role, name}."""
    return getattr(request.state, "account", None) or {}


def account_id(request: Request) -> Optional[int]:
    return account(request).get("id")


def is_admin(request: Request) -> bool:
    return account(request).get("role") == "admin"


def _not_found(label: str):
    raise HTTPException(status_code=404, detail=f"{label} not found.")


def owns_response(request: Request, response: Optional[SurveyResponse]) -> bool:
    if response is None:
        return False
    if is_admin(request):
        return True
    uid = account_id(request)
    return uid is not None and response.user_id == uid


def response_or_404(db: Session, request: Request, response_id: int, label: str = "Survey response") -> SurveyResponse:
    response = db.get(SurveyResponse, response_id)
    if not owns_response(request, response):
        _not_found(label)
    return response


def respondent_or_404(db: Session, request: Request, respondent_id: int) -> Respondent:
    """A customer: admins, the account that recorded them, or the owner of a survey with them."""
    respondent = db.get(Respondent, respondent_id)
    if respondent is None:
        _not_found("Respondent")
    if is_admin(request):
        return respondent
    uid = account_id(request)
    if uid is not None and respondent.created_by == uid:
        return respondent
    owned = (
        db.query(SurveyResponse.id)
        .filter(SurveyResponse.respondent_id == respondent.id, SurveyResponse.user_id == uid)
        .first()
    )
    if uid is None or owned is None:
        _not_found("Respondent")
    return respondent


def respondent_usable_or_404(db: Session, request: Request, respondent_id: int) -> Respondent:
    """
    A customer a new survey may be started with: one this account recorded.
    Older customers recorded before ownership was stored (created_by empty) are
    usable only while no survey uses them yet.
    """
    respondent = db.get(Respondent, respondent_id)
    if respondent is None:
        _not_found("Respondent")
    if is_admin(request):
        return respondent
    uid = account_id(request)
    if respondent.created_by is not None:
        if respondent.created_by != uid:
            _not_found("Respondent")
        return respondent
    in_use = db.query(SurveyResponse.id).filter(SurveyResponse.respondent_id == respondent.id).first()
    if in_use is not None:
        _not_found("Respondent")
    return respondent


def feedback_or_404(db: Session, request: Request, feedback_id: int) -> FeedbackRecord:
    """A recording's result: admins, or the owner of the survey it belongs to."""
    record = db.get(FeedbackRecord, feedback_id)
    if record is None:
        _not_found(f"Feedback record {feedback_id}")
    if is_admin(request):
        return record
    if record.response_id is None:  # older uploads without a survey: admins only
        _not_found(f"Feedback record {feedback_id}")
    if not owns_response(request, db.get(SurveyResponse, record.response_id)):
        _not_found(f"Feedback record {feedback_id}")
    return record


def job_or_404(db: Session, request: Request, job_id: int) -> AnalysisJob:
    """An analysis job: admins, the account that queued it, or the owner of its survey."""
    job = db.get(AnalysisJob, job_id)
    if job is None:
        _not_found("Analysis job")
    if is_admin(request):
        return job
    uid = account_id(request)
    if uid is not None and job.user_id == uid:
        return job
    if job.response_id is not None and owns_response(request, db.get(SurveyResponse, job.response_id)):
        return job
    _not_found("Analysis job")
