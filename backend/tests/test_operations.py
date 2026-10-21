"""
Operations and data: readiness check, request IDs, the stored tone column used
by the report pages (PERF-01), and lightweight report staleness (PERF-02).
"""

from datetime import datetime

from conftest import auth, start_survey

from app.database.models import FeedbackRecord, SurveyResponse


def test_health_reports_database_status(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["database"] == "ok"
    assert body["status"] in ("healthy", "degraded")  # degraded: AI service not running in tests


def test_ready_endpoint_checks_the_database(client):
    r = client.get("/health/ready")
    assert r.status_code == 200
    assert r.json() == {"status": "ready", "database": "ok"}


def test_every_response_carries_a_request_id(client):
    r = client.get("/health", headers={"X-Request-ID": "phone-req-0001"})
    assert r.headers["X-Request-ID"] == "phone-req-0001"
    generated = client.get("/health").headers["X-Request-ID"]
    assert len(generated) >= 16


def test_security_headers_on_api_responses(client):
    r = client.get("/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert r.headers["Referrer-Policy"] == "no-referrer"


def _analyzed(db, client, user, product, chapters, rating, sentiment="positive"):
    ids = start_survey(client, user, product)
    survey = db.get(SurveyResponse, ids["response_id"])
    survey.status = "analyzed"
    record = FeedbackRecord(original_filename="r.m4a", video_path="/nonexistent/r.m4a", duration_seconds=60.0,
                            uploaded_at=datetime.utcnow(), overall_summary="s", overall_sentiment=sentiment,
                            overall_rating=rating, transcript="t" * 5000, chapters=chapters, response_id=survey.id)
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def test_tone_is_stored_when_a_result_is_saved(client, db, alice, product):
    happy = _analyzed(db, client, alice, product, [{"feedback": [{"aspect": "taste", "sentiment": "positive"}]}], 4.6)
    unhappy = _analyzed(db, client, alice, product, [{"feedback": [{"aspect": "price", "sentiment": "negative"}]}], 1.5,
                        sentiment="negative")
    assert happy.tone == "positive"
    assert unhappy.tone == "negative"
    happy.overall_rating = 1.0  # a re-analysis changes the result: the tone follows
    happy.chapters = [{"feedback": [{"aspect": "taste", "sentiment": "negative"}]}]
    db.commit()
    db.refresh(happy)
    assert happy.tone == "negative"


def test_admin_product_list_uses_stored_tones(client, db, alice, admin, product):
    _analyzed(db, client, alice, product, [{"feedback": [{"aspect": "taste", "sentiment": "positive"}]}], 4.8)
    body = client.get("/product-feedback", headers=auth(admin)).json()
    row = next(p for p in body["products"] if p["id"] == product.id)
    assert row["responses"] >= 1
    assert row["sentiment"]["positive"] >= 1
    assert row["report"] is None


def test_stale_report_detection_without_loading_results(client, db, alice, product):
    from app.api.product_feedback import stale_report_products, report_is_stale
    _analyzed(db, client, alice, product, [], 4.0)
    assert product.id in stale_report_products(db)  # analyzed survey, no report yet
    assert report_is_stale(db, product.id)
