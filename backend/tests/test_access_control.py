"""
Record-level access (SEC-01): a survey person reaches only their own surveys,
customers, recordings and analysis jobs; admins reach everything; other people
get 404 (not 403), so record IDs can't be probed.
"""

from datetime import datetime

from conftest import answers_payload, auth, start_survey

from app.database.models import AnalysisJob, FeedbackRecord, SurveyResponse

AUDIO = ("clip.m4a", b"\x00\x00\x00\x18ftypM4A " + b"\x00" * 2048, "audio/mp4")


# ---------------- surveys ----------------

def test_new_survey_belongs_to_its_creator(client, db, alice, product):
    ids = start_survey(client, alice, product)
    survey = db.get(SurveyResponse, ids["response_id"])
    assert survey.user_id == alice.id
    assert survey.executive_name == alice.name  # filled from the account when the app sends none


def test_owner_and_admin_can_read_a_survey(client, alice, admin, product):
    ids = start_survey(client, alice, product)
    assert client.get(f"/surveys/responses/{ids['response_id']}", headers=auth(alice)).status_code == 200
    assert client.get(f"/surveys/responses/{ids['response_id']}", headers=auth(admin)).status_code == 200


def test_other_survey_person_cannot_read_a_survey(client, alice, bob, product):
    ids = start_survey(client, alice, product)
    r = client.get(f"/surveys/responses/{ids['response_id']}", headers=auth(bob))
    assert r.status_code == 404
    assert "9876543210" not in r.text  # the customer's mobile never leaks


def test_other_survey_person_cannot_read_the_customer(client, alice, bob, admin, product):
    ids = start_survey(client, alice, product)
    assert client.get(f"/surveys/respondents/{ids['respondent_id']}", headers=auth(bob)).status_code == 404
    assert client.get(f"/surveys/respondents/{ids['respondent_id']}", headers=auth(alice)).status_code == 200
    assert client.get(f"/surveys/respondents/{ids['respondent_id']}", headers=auth(admin)).status_code == 200


def test_other_survey_person_cannot_write_answers_or_submit(client, db, alice, bob, product):
    ids = start_survey(client, alice, product)
    payload = answers_payload(db, product)
    assert client.put(f"/surveys/responses/{ids['response_id']}/answers", headers=auth(bob), json=payload).status_code == 404
    assert client.post(f"/surveys/responses/{ids['response_id']}/submit", headers=auth(bob)).status_code == 404
    # The owner still can.
    assert client.put(f"/surveys/responses/{ids['response_id']}/answers", headers=auth(alice), json=payload).status_code == 200
    assert client.post(f"/surveys/responses/{ids['response_id']}/submit", headers=auth(alice)).status_code == 200


def test_cannot_start_a_survey_with_someone_elses_customer(client, alice, bob, product):
    ids = start_survey(client, alice, product)
    r = client.post(
        "/surveys/responses",
        headers=auth(bob),
        json={"product_id": product.id, "respondent_id": ids["respondent_id"]},
    )
    assert r.status_code == 404


def test_claim_still_works_for_the_owner_and_not_for_others(client, alice, bob, product):
    ids = start_survey(client, alice, product)
    assert client.post(f"/auth/me/claim/{ids['response_id']}", headers=auth(alice)).status_code == 200
    assert client.post(f"/auth/me/claim/{ids['response_id']}", headers=auth(bob)).status_code in (403, 404)


def test_requests_without_a_token_are_refused(client, alice, product):
    ids = start_survey(client, alice, product)
    assert client.get(f"/surveys/responses/{ids['response_id']}").status_code == 401


# ---------------- recordings and results ----------------

def _feedback_for(db, response_id, tmp_path) -> FeedbackRecord:
    media = tmp_path / "rec.m4a"
    media.write_bytes(b"fake media")
    record = FeedbackRecord(
        original_filename="rec.m4a",
        video_path=str(media),
        duration_seconds=60.0,
        uploaded_at=datetime.utcnow(),
        overall_summary="Liked the taste.",
        overall_sentiment="positive",
        overall_rating=4.5,
        transcript="the taste was good",
        chapters=[],
        response_id=response_id,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def test_feedback_and_recording_only_for_owner_and_admin(client, db, alice, bob, admin, product, tmp_path):
    ids = start_survey(client, alice, product)
    record = _feedback_for(db, ids["response_id"], tmp_path)
    assert client.get(f"/feedback/{record.id}", headers=auth(bob)).status_code == 404
    assert client.get(f"/feedback/{record.id}/video", headers=auth(bob)).status_code == 404
    assert client.get(f"/feedback/{record.id}", headers=auth(alice)).status_code == 200
    assert client.get(f"/feedback/{record.id}/video", headers=auth(alice)).status_code == 200
    assert client.get(f"/feedback/{record.id}", headers=auth(admin)).status_code == 200


def test_recordings_without_a_survey_are_admin_only(client, db, alice, admin, tmp_path):
    record = _feedback_for(db, None, tmp_path)
    assert client.get(f"/feedback/{record.id}", headers=auth(alice)).status_code == 404
    assert client.get(f"/feedback/{record.id}", headers=auth(admin)).status_code == 200


# ---------------- analysis jobs ----------------

def test_analysis_job_is_private_to_its_owner(client, db, alice, bob, admin, product):
    ids = start_survey(client, alice, product)
    r = client.post("/analysis/jobs", headers=auth(alice), files={"file": AUDIO}, data={"response_id": str(ids["response_id"])})
    assert r.status_code == 202, r.text
    job_id = r.json()["job_id"]
    assert client.get(f"/analysis/jobs/{job_id}", headers=auth(bob)).status_code == 404
    assert client.get(f"/analysis/jobs/{job_id}", headers=auth(alice)).status_code == 200
    assert client.get(f"/analysis/jobs/{job_id}", headers=auth(admin)).status_code == 200
    job = db.get(AnalysisJob, job_id)
    assert job.user_id == alice.id


def test_cannot_upload_a_recording_for_someone_elses_survey(client, alice, bob, product):
    ids = start_survey(client, alice, product)
    r = client.post("/analysis/jobs", headers=auth(bob), files={"file": AUDIO}, data={"response_id": str(ids["response_id"])})
    assert r.status_code == 404
    r = client.post("/video/upload", headers=auth(bob), files={"file": AUDIO}, data={"response_id": str(ids["response_id"])})
    assert r.status_code == 404


# ---------------- history list ----------------

def test_survey_list_shows_only_own_surveys_to_survey_persons(client, alice, bob, admin, product):
    mine = start_survey(client, alice, product)
    theirs = start_survey(client, bob, product)
    listed = {s["response_id"] for s in client.get("/product-feedback/surveys?limit=500", headers=auth(alice)).json()["surveys"]}
    assert mine["response_id"] in listed and theirs["response_id"] not in listed
    everyone = {s["response_id"] for s in client.get("/product-feedback/surveys?limit=500", headers=auth(admin)).json()["surveys"]}
    assert {mine["response_id"], theirs["response_id"]} <= everyone


def test_admin_only_routes_refuse_survey_persons(client, alice):
    for path in ("/admin/users", "/product-feedback", "/feedback", "/analysis/jobs", "/question-templates"):
        assert client.get(path, headers=auth(alice)).status_code == 403, path


def test_worker_internal_upload_call_still_works(client, db, alice, product):
    """The analysis worker calls upload_video directly (no HTTP request); the ownership
    check must not break that path. With the AI service down it fails at the AI step."""
    import asyncio
    import inspect
    import io

    from fastapi import HTTPException, UploadFile

    from app.api.video import upload_video
    from app.services import analysis_worker

    ids = start_survey(client, alice, product)
    survey = db.get(SurveyResponse, ids["response_id"])
    survey.status = "submitted"
    db.commit()
    assert "request" in inspect.signature(upload_video).parameters
    upload = UploadFile(file=io.BytesIO(b"\x00" * 4096), filename="clip.m4a")
    try:
        asyncio.run(upload_video(request=None, file=upload, response_id=survey.id))
    except HTTPException as error:  # AI service unreachable in tests: expected
        assert error.status_code != 404
    # And the worker's own helper fills request=None for parameters without defaults.
    source = inspect.getsource(analysis_worker._call_upload)
    assert "inspect.Parameter.empty else None" in source
