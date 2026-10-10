"""
Product-wise feedback history and consolidated reports.

GET  /product-feedback                       every product with its feedback totals
GET  /product-feedback/unlinked              recordings not linked to any survey
GET  /product-feedback/{product_id}          one product: its responses + latest report
POST /product-feedback/{product_id}/consolidate   generate a new consolidated report
GET  /product-feedback/{product_id}/reports  every report version (newest first)

Register in app/main.py:
    from app.api.product_feedback import router as product_feedback_router
    app.include_router(product_feedback_router)
"""

import time
from collections import Counter, defaultdict
from datetime import datetime

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database.database import get_db
from app.database.models import (
    Category,
    ConsolidatedReport,
    FeedbackRecord,
    Product,
    Respondent,
    SurveyAnswer,
    SurveyQuestion,
    SurveyResponse,
)
from app.api.auth import admin_guard, history_guard, optional_user, surveys_of
from app.database.models import User
from app.services.consolidation import (
    build_stats,
    ensure_translations,
    feedback_items,
    response_tone,
    write_narrative,
)

# Access (see auth.py):
#   /surveys          executives with the history permission see ONLY their own surveys; admins see all
#   everything else   product reports combine everyone's feedback: admins only
router = APIRouter(prefix="/product-feedback", tags=["Product feedback"])


# ============================================================
# Helpers
# ============================================================

def _iso(dt):
    return dt.isoformat() if isinstance(dt, datetime) else dt


def _summary_text(record: FeedbackRecord) -> str:
    text = (record.overall_summary or "").strip()
    if text.startswith("{"):
        try:
            import json

            text = str(json.loads(text).get("summary", text))
        except Exception:
            pass
    return text


def _analyzed_pairs(db: Session, product_ids=None):
    """(SurveyResponse, FeedbackRecord) for analyzed surveys that have a linked recording."""
    q = (
        db.query(SurveyResponse, FeedbackRecord)
        .join(FeedbackRecord, FeedbackRecord.response_id == SurveyResponse.id)
        .filter(SurveyResponse.status == "analyzed")
    )
    if product_ids is not None:
        q = q.filter(SurveyResponse.product_id.in_(product_ids))
    # One recording per survey: keep the newest if a survey was re-analyzed.
    latest = {}
    for resp, rec in q.all():
        if resp.id not in latest or (rec.uploaded_at or datetime.min) > (latest[resp.id][1].uploaded_at or datetime.min):
            latest[resp.id] = (resp, rec)
    return list(latest.values())


def _report_out(r: ConsolidatedReport) -> dict:
    return {
        "id": r.id,
        "product_id": r.product_id,
        "version": r.version,
        "response_count": r.response_count,
        "response_ids": r.response_ids,
        "overall_rating": r.overall_rating,
        "stats": r.stats,
        "narrative": r.narrative,
        "filters": r.filters,
        "generated_at": _iso(r.generated_at),
    }


def _latest_report(db: Session, product_id: int):
    return (
        db.query(ConsolidatedReport)
        .filter(ConsolidatedReport.product_id == product_id)
        .order_by(ConsolidatedReport.version.desc())
        .first()
    )


# ============================================================
# Product list with feedback totals
# ============================================================

@router.get("", dependencies=[Depends(admin_guard)])
def products_with_feedback(db: Session = Depends(get_db)):
    rows = (
        db.query(Product, Category)
        .join(Category, Category.id == Product.category_id)
        .filter(Product.is_active == 1, Category.is_active == 1)
        .all()
    )
    pairs = _analyzed_pairs(db)
    by_product = defaultdict(list)
    for resp, rec in pairs:
        by_product[resp.product_id].append((resp, rec))

    pending = dict(
        db.query(SurveyResponse.product_id, func.count(SurveyResponse.id))
        .filter(SurveyResponse.status != "analyzed")
        .group_by(SurveyResponse.product_id)
        .all()
    )
    from app.database.models import AnalysisJob

    analyzing = dict(
        db.query(SurveyResponse.product_id, func.count(func.distinct(AnalysisJob.response_id)))
        .join(AnalysisJob, AnalysisJob.response_id == SurveyResponse.id)
        .filter(AnalysisJob.status.in_(("queued", "processing")))
        .group_by(SurveyResponse.product_id)
        .all()
    )

    # Latest report per product (one query).
    reports = {}
    for r in db.query(ConsolidatedReport).order_by(ConsolidatedReport.product_id, ConsolidatedReport.version.desc()).all():
        reports.setdefault(r.product_id, r)

    products = []
    for p, c in rows:
        items = by_product.get(p.id, [])
        ratings = [rec.overall_rating for _, rec in items if rec.overall_rating]
        tones = Counter(response_tone(rec.overall_sentiment, rec.chapters, rec.overall_rating) for _, rec in items)
        last = max((rec.uploaded_at for _, rec in items if rec.uploaded_at), default=None)
        report = reports.get(p.id)
        products.append({
            "id": p.id,
            "name": p.name,
            "sku": p.sku,
            "category_id": c.id,
            "category_name": c.name,
            "min_responses": p.min_responses,
            "responses": len(items),
            "pending": pending.get(p.id, 0),
            "analyzing": analyzing.get(p.id, 0),
            "avg_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
            "sentiment": {k: tones.get(k, 0) for k in ("positive", "mixed", "negative", "neutral")},
            "last_feedback_at": _iso(last),
            "can_consolidate": len(items) >= 1,
            "report": None if not report else {
                "version": report.version,
                "generated_at": _iso(report.generated_at),
                "response_count": report.response_count,
                "overall_rating": report.overall_rating,
                # New feedback arrived after the report was made.
                # New or deleted surveys since: the worker is updating it.
                "outdated": sorted(report.response_ids or []) != sorted(resp.id for resp, _ in items),
            },
        })

    unlinked = db.query(func.count(FeedbackRecord.id)).filter(FeedbackRecord.response_id.is_(None)).scalar() or 0
    return {"products": products, "unlinked_count": unlinked}


@router.get("/unlinked", dependencies=[Depends(admin_guard)])
def unlinked_feedback(db: Session = Depends(get_db)):
    """Recordings analyzed outside a survey (for example before surveys existed)."""
    records = (
        db.query(FeedbackRecord)
        .filter(FeedbackRecord.response_id.is_(None))
        .order_by(FeedbackRecord.uploaded_at.desc())
        .all()
    )
    return {
        "feedback": [
            {
                "id": r.id,
                "filename": r.original_filename,
                "uploaded_at": _iso(r.uploaded_at),
                "video_duration_seconds": r.duration_seconds,
                "overall_rating": r.overall_rating,
                "sentiment": r.overall_sentiment,
                "summary": _summary_text(r),
            }
            for r in records
        ]
    }


# ============================================================
# Recent surveys across products (mobile History)
# ============================================================

def _analysis_status(db: Session, response_ids) -> dict:
    """Latest analysis job per survey: {response_id: {status, position, job_id}} for queued/processing/failed."""
    from app.database.models import AnalysisJob

    if not response_ids:
        return {}
    jobs = (
        db.query(AnalysisJob.id, AnalysisJob.response_id, AnalysisJob.status, AnalysisJob.error)
        .filter(AnalysisJob.response_id.in_(list(response_ids)))
        .order_by(AnalysisJob.id.asc())
        .all()
    )
    latest = {}
    for j in jobs:
        latest[j.response_id] = j  # ascending id: the last one wins
    # Queue positions for all queued jobs in one pass (cheap even with thousands queued).
    queue = [
        r.id
        for r in db.query(AnalysisJob.id)
        .filter(AnalysisJob.status.in_(("queued", "processing")))
        .order_by(AnalysisJob.status.desc(), AnalysisJob.id.asc())  # processing first
        .all()
    ]
    position = {jid: i + 1 for i, jid in enumerate(queue)}
    out = {}
    for rid, j in latest.items():
        if j.status in ("queued", "processing", "failed"):
            out[rid] = {"job_id": j.id, "status": j.status, "position": position.get(j.id), "error": j.error}
    return out


def _short_name(full: Optional[str]) -> str:
    """'Priya Sharma' -> 'Priya S.'  Phones get only this, never the full name or mobile."""
    parts = [p for p in (full or "").split() if p]
    if not parts:
        return "Respondent"
    return parts[0] if len(parts) == 1 else f"{parts[0]} {parts[-1][0].upper()}."


@router.get("/surveys", dependencies=[Depends(history_guard)])
def recent_surveys(
    executive: Optional[str] = Query(default=None, max_length=150),
    product_id: Optional[int] = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: Optional[User] = Depends(optional_user),
    db: Session = Depends(get_db),
):
    q = db.query(SurveyResponse)
    # An executive only ever sees their own surveys, whatever they ask for.
    if user is not None and user.role != "admin":
        q = q.filter(surveys_of(user))
    if executive and executive.strip():
        q = q.filter(func.lower(SurveyResponse.executive_name) == executive.strip().lower())
    if product_id:
        q = q.filter(SurveyResponse.product_id == product_id)
    total = q.count()
    analyzed_total = q.filter(SurveyResponse.status == "analyzed").count()
    # Average rating over every analyzed survey in this list (orphaned analyses never count).
    ids = q.with_entities(SurveyResponse.id).subquery()
    avg_rating, rated = (
        db.query(func.avg(FeedbackRecord.overall_rating), func.count(FeedbackRecord.id))
        .filter(FeedbackRecord.response_id.in_(db.query(ids.c.id)), FeedbackRecord.overall_rating > 0)
        .one()
    )
    rows = q.order_by(SurveyResponse.created_at.desc(), SurveyResponse.id.desc()).offset(offset).limit(limit).all()

    ids = [r.id for r in rows]
    products = {p.id: p for p in db.query(Product).filter(Product.id.in_({r.product_id for r in rows})).all()} if rows else {}
    categories = {c.id: c for c in db.query(Category).filter(Category.id.in_({p.category_id for p in products.values()})).all()} if products else {}
    people = {p.id: p for p in db.query(Respondent).filter(Respondent.id.in_({r.respondent_id for r in rows})).all()} if rows else {}
    records = {}
    if ids:
        for rec in db.query(FeedbackRecord).filter(FeedbackRecord.response_id.in_(ids)).all():
            if rec.response_id not in records or (rec.uploaded_at or datetime.min) > (records[rec.response_id].uploaded_at or datetime.min):
                records[rec.response_id] = rec

    analysis = _analysis_status(db, [r.id for r in rows if r.id not in records])
    surveys = []
    for r in rows:
        product = products.get(r.product_id)
        category = categories.get(product.category_id) if product else None
        person = people.get(r.respondent_id)
        rec = records.get(r.id)
        surveys.append({
            "response_id": r.id,
            "created_at": _iso(r.created_at),
            "status": r.status,
            "executive_name": r.executive_name,
            "product_id": r.product_id,
            "product_name": product.name if product else "",
            "category_name": category.name if category else "",
            "respondent_label": _short_name(person.name if person else None),
            "area": person.location if person else None,
            # Running or failed analysis, for surveys without a result yet.
            "analysis": analysis.get(r.id),
            "feedback": None if not rec else {
                "id": rec.id,
                "overall_rating": rec.overall_rating,
                "sentiment": rec.overall_sentiment,
                "tone": response_tone(rec.overall_sentiment, rec.chapters, rec.overall_rating),
                "summary": _summary_text(rec),
                "uploaded_at": _iso(rec.uploaded_at),
            },
        })
    return {
        "surveys": surveys,
        "total": total,
        "analyzed_total": analyzed_total,
        "avg_rating": round(float(avg_rating), 2) if avg_rating is not None else None,
        "rated": rated or 0,
    }


@router.delete("/surveys/{response_id}", dependencies=[Depends(admin_guard)])
def delete_survey(response_id: int, db: Session = Depends(get_db)):
    """
    Deletes a survey completely: its answers, its analysis (and the stored recording),
    queued jobs, and the respondent if no other survey uses them. Counts update everywhere.
    """
    from pathlib import Path

    from app.database.models import AnalysisJob, Respondent, SurveyAnswer

    survey = db.get(SurveyResponse, response_id)
    if not survey:
        raise HTTPException(status_code=404, detail="Survey not found.")
    files = []
    for rec in db.query(FeedbackRecord).filter(FeedbackRecord.response_id == response_id).all():
        if rec.video_path:
            files.append(rec.video_path)
        db.delete(rec)
    db.query(SurveyAnswer).filter(SurveyAnswer.response_id == response_id).delete(synchronize_session=False)
    db.query(AnalysisJob).filter(AnalysisJob.response_id == response_id).delete(synchronize_session=False)
    respondent_id = survey.respondent_id
    db.delete(survey)
    db.flush()
    if respondent_id and not db.query(SurveyResponse).filter(SurveyResponse.respondent_id == respondent_id).first():
        person = db.get(Respondent, respondent_id)
        if person:
            db.delete(person)
    db.commit()
    for f in files:
        try:
            Path(f).unlink(missing_ok=True)
        except Exception:
            pass
    return {"deleted": response_id}


# ============================================================
# One product
# ============================================================

@router.get("/{product_id}", dependencies=[Depends(admin_guard)])
def product_feedback(product_id: int, db: Session = Depends(get_db)):
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")
    category = db.get(Category, product.category_id)

    responses = (
        db.query(SurveyResponse)
        .filter(SurveyResponse.product_id == product_id)
        .order_by(SurveyResponse.created_at.desc())
        .all()
    )
    ids = [r.id for r in responses]
    respondents = {
        r.id: r for r in db.query(Respondent).filter(Respondent.id.in_([x.respondent_id for x in responses])).all()
    } if responses else {}
    records = {}
    if ids:
        for rec in db.query(FeedbackRecord).filter(FeedbackRecord.response_id.in_(ids)).all():
            if rec.response_id not in records or (rec.uploaded_at or datetime.min) > (records[rec.response_id].uploaded_at or datetime.min):
                records[rec.response_id] = rec
    answer_counts = dict(
        db.query(SurveyAnswer.response_id, func.count(SurveyAnswer.id))
        .filter(SurveyAnswer.response_id.in_(ids))
        .group_by(SurveyAnswer.response_id)
        .all()
    ) if ids else {}

    out = []
    for r in responses:
        person = respondents.get(r.respondent_id)
        rec = records.get(r.id)
        out.append({
            "response_id": r.id,
            "status": r.status,
            "created_at": _iso(r.created_at),
            "usage_period": r.usage_period,
            "survey_location": r.location,
            "executive_name": r.executive_name,
            "answers": answer_counts.get(r.id, 0),
            # Admin view: name, age, gender and city. Mobile and address are not sent.
            "respondent": None if not person else {
                "name": person.name,
                "age": person.age,
                "gender": person.gender,
                "location": person.location,
            },
            "feedback": None if not rec else {
                "id": rec.id,
                "filename": rec.original_filename,
                "uploaded_at": _iso(rec.uploaded_at),
                "duration_seconds": rec.duration_seconds,
                "overall_rating": rec.overall_rating,
                "sentiment": rec.overall_sentiment,
                "tone": response_tone(rec.overall_sentiment, rec.chapters, rec.overall_rating),
                "summary": _summary_text(rec),
            },
        })

    analyzed = [x for x in out if x["status"] == "analyzed" and x["feedback"]]
    ratings = [x["feedback"]["overall_rating"] for x in analyzed if x["feedback"]["overall_rating"]]
    tones = Counter(x["feedback"]["tone"] for x in analyzed)
    report = _latest_report(db, product_id)

    return {
        "product": {
            "id": product.id,
            "name": product.name,
            "sku": product.sku,
            "description": product.description,
            "category_id": product.category_id,
            "category_name": category.name if category else None,
            "min_responses": product.min_responses,
        },
        "summary": {
            "responses": len(analyzed),
            "pending": len(out) - len(analyzed),
            "avg_rating": round(sum(ratings) / len(ratings), 2) if ratings else None,
            "sentiment": {k: tones.get(k, 0) for k in ("positive", "mixed", "negative", "neutral")},
            "can_consolidate": len(analyzed) >= 1,
            # True while the worker refreshes the report after new or deleted surveys.
            "report_updating": report_is_stale(db, product_id, report),
        },
        "responses": out,
        "report": _report_out(report) if report else None,
    }


@router.get("/{product_id}/reports", dependencies=[Depends(admin_guard)])
def report_versions(product_id: int, db: Session = Depends(get_db)):
    reports = (
        db.query(ConsolidatedReport)
        .filter(ConsolidatedReport.product_id == product_id)
        .order_by(ConsolidatedReport.version.desc())
        .all()
    )
    return {"reports": [_report_out(r) for r in reports]}


# ============================================================
# Generate a consolidated report
# ============================================================

@router.post("/{product_id}/consolidate", dependencies=[Depends(admin_guard)])
def consolidate(product_id: int, db: Session = Depends(get_db)):
    """Refresh the product's report now (it also refreshes by itself after each new analysis)."""
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")
    report = build_product_report(db, product_id)
    if report is None:
        raise HTTPException(status_code=400, detail=f"{product.name} has no analyzed surveys yet.")
    return _report_out(report)


def stale_report_products(db: Session) -> list:
    """Products whose report doesn't match their analyzed surveys (or that have none yet)."""
    ids_by_product = defaultdict(list)
    for resp, _ in _analyzed_pairs(db):
        ids_by_product[resp.product_id].append(resp.id)
    reports = {}
    for r in db.query(ConsolidatedReport).order_by(ConsolidatedReport.product_id, ConsolidatedReport.version.desc()).all():
        reports.setdefault(r.product_id, r)
    stale = []
    for pid in set(ids_by_product) | set(reports):
        current = sorted(ids_by_product.get(pid, []))
        report = reports.get(pid)
        if (report is None and current) or (report is not None and sorted(report.response_ids or []) != current):
            stale.append(pid)
    return sorted(stale)


def report_is_stale(db: Session, product_id: int, report=None) -> bool:
    """True when the report doesn't match the product's analyzed surveys (new, or deleted)."""
    ids = sorted(resp.id for resp, _ in _analyzed_pairs(db, [product_id]))
    report = report if report is not None else _latest_report(db, product_id)
    if not ids:
        return report is not None  # every survey was deleted: the report must go
    if report is None:
        return True
    return sorted(report.response_ids or []) != ids


def build_product_report(db: Session, product_id: int):
    """
    Build (or rebuild) THE consolidated report of one product from all of its analyzed
    surveys. Exactly one report per product: an existing one is replaced, never copied.
    Returns the report, or None when the product has no analyzed surveys (any old report is removed).
    """
    product = db.get(Product, product_id)
    if not product:
        return None
    pairs = _analyzed_pairs(db, [product_id])
    if not pairs:
        db.query(ConsolidatedReport).filter(ConsolidatedReport.product_id == product_id).delete(synchronize_session=False)
        db.commit()
        return None

    started = time.time()
    response_ids = [resp.id for resp, _ in pairs]

    # Usually already done in the background; otherwise one request for the rest.
    try:
        translated = ensure_translations(db, response_ids=response_ids)
        if translated:
            print(f"Consolidated report: translated {translated} remaining answers in {time.time() - started:.0f}s.")
    except Exception as e:
        print(f"Consolidated report: translation skipped ({e}).")

    respondents = {
        r.id: r for r in db.query(Respondent).filter(Respondent.id.in_([resp.respondent_id for resp, _ in pairs])).all()
    }
    product_questions = db.query(SurveyQuestion).filter(SurveyQuestion.product_id == product_id).all()
    positions = {q.id: q.position for q in product_questions}
    # For the client summary: what each question means, and its options in catalog order.
    roles = {q.id: (getattr(q, "report_role", None), q.options or []) for q in product_questions}
    answers = defaultdict(list)
    translations = {}
    for a in db.query(SurveyAnswer).filter(SurveyAnswer.response_id.in_(response_ids)).all():
        if a.value and a.value_en:
            translations[a.value.strip()] = a.value_en
        answers[a.response_id].append({
            "question_id": a.question_id,
            "position": positions.get(a.question_id, 0),
            "question_text": a.question_text,
            "question_type": a.question_type,
            "value": a.value,
            "report_role": roles.get(a.question_id, (None, []))[0],
            "options": roles.get(a.question_id, (None, []))[1],
        })

    items, summaries = [], []
    for resp, rec in pairs:
        person = respondents.get(resp.respondent_id)
        aspects = []
        for f in feedback_items(rec.chapters):
            name = f.get("aspect_name") or f.get("aspect") or f.get("aspect_key")
            key = (f.get("aspect_key") or str(name)).strip().lower().replace(" ", "_")
            aspects.append({
                "key": key,
                "name": str(name).strip().capitalize(),
                "sentiment": f.get("sentiment"),
                "rating": f.get("rating"),
                "evidence": f.get("evidence"),
            })
        items.append({
            "rating": rec.overall_rating,
            "tone": response_tone(rec.overall_sentiment, rec.chapters, rec.overall_rating),
            "aspects": aspects,
            "answers": answers.get(resp.id, []),
            # Demographics only: no name, mobile or address.
            "gender": person.gender if person else None,
            "age": person.age if person else None,
            "location": person.location if person else None,
            "survey_location": resp.location,  # where the survey/placement happened
            "usage_period": resp.usage_period,
        })
        summary = _summary_text(rec)
        if summary:
            summaries.append(summary[:400])

    stats = build_stats(items)
    narrative = write_narrative(product.name, stats, summaries, translations, recipe=product.recipe)

    # One report per product: update it in place, remove any older copies.
    report = _latest_report(db, product_id)
    if report is None:
        report = ConsolidatedReport(product_id=product_id, version=0)
        db.add(report)
    else:
        db.query(ConsolidatedReport).filter(
            ConsolidatedReport.product_id == product_id, ConsolidatedReport.id != report.id
        ).delete(synchronize_session=False)
    report.version = (report.version or 0) + 1  # how many times it has been updated
    report.filters = {}
    report.response_ids = response_ids
    report.response_count = len(response_ids)
    report.stats = stats
    report.narrative = narrative
    report.overall_rating = stats["overall_rating"]
    report.generated_at = datetime.utcnow()
    db.commit()
    db.refresh(report)
    print(f"Consolidated report for {product.name} updated ({len(response_ids)} surveys, {time.time() - started:.0f}s).")
    return report