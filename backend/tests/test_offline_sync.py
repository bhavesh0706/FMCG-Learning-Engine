"""
Mobile offline mode: a survey collected without internet is uploaded later, possibly
several times (dropped connections, the app killed mid-way). These tests replay
those retries against the real API and check that nothing is ever duplicated.
"""

import hashlib
import uuid

from app.database.models import AnalysisJob, Respondent, SurveyResponse, UploadSession
from tests.conftest import answers_payload, assign, auth, open_round

CUSTOMER = {"name": "Offline Customer", "mobile": "9876500000", "location": "Pune", "consent_given": True}


def _ready(db, product, user):
    open_round(db, product)
    assign(db, product, user)


def _respondent(client, user, client_uuid):
    return client.post("/surveys/respondents", headers=auth(user), json={**CUSTOMER, "client_uuid": client_uuid})


def _response(client, user, product, respondent_id, client_uuid):
    return client.post(
        "/surveys/responses",
        headers=auth(user),
        json={"product_id": product.id, "respondent_id": respondent_id, "location": "Market", "client_uuid": client_uuid},
    )


# ---------------------------------------------------------------- idempotent records


def test_retried_respondent_and_survey_return_the_same_records(client, db, product, alice):
    _ready(db, product, alice)
    submission = str(uuid.uuid4())

    first = _respondent(client, alice, submission)
    again = _respondent(client, alice, submission)  # the reply was lost: the phone sends it again
    assert first.status_code == 201 and again.status_code == 201
    assert first.json()["id"] == again.json()["id"]
    assert db.query(Respondent).filter(Respondent.client_uuid == submission).count() == 1

    r1 = _response(client, alice, product, first.json()["id"], submission)
    r2 = _response(client, alice, product, first.json()["id"], submission)
    assert r1.status_code == 201 and r2.status_code == 201
    assert r1.json()["id"] == r2.json()["id"]
    assert db.query(SurveyResponse).filter(SurveyResponse.client_uuid == submission).count() == 1


def test_uppercase_uuid_is_the_same_submission(client, db, product, alice):
    _ready(db, product, alice)
    submission = str(uuid.uuid4())
    a = _respondent(client, alice, submission)
    b = _respondent(client, alice, submission.upper())
    assert a.json()["id"] == b.json()["id"]


def test_without_client_uuid_the_old_behaviour_is_unchanged(client, db, product, alice):
    _ready(db, product, alice)
    a = client.post("/surveys/respondents", headers=auth(alice), json=CUSTOMER)
    b = client.post("/surveys/respondents", headers=auth(alice), json=CUSTOMER)
    assert a.status_code == 201 and b.status_code == 201
    assert a.json()["id"] != b.json()["id"]


def test_another_account_cannot_reuse_or_see_a_submission(client, db, product, alice, bob):
    _ready(db, product, alice)
    submission = str(uuid.uuid4())
    assert _respondent(client, alice, submission).status_code == 201
    r = _respondent(client, bob, submission)
    assert r.status_code == 409
    assert "Offline Customer" not in r.text


def test_invalid_client_uuid_is_refused(client, db, product, alice):
    r = client.post("/surveys/respondents", headers=auth(alice), json={**CUSTOMER, "client_uuid": "not-a-uuid"})
    assert r.status_code == 422


# ---------------------------------------------------------------- resumable uploads


def _survey(client, db, product, user):
    _ready(db, product, user)
    submission = str(uuid.uuid4())
    respondent_id = _respondent(client, user, submission).json()["id"]
    response_id = _response(client, user, product, respondent_id, submission).json()["id"]
    assert client.put(f"/surveys/responses/{response_id}/answers", headers=auth(user),
                      json=answers_payload(db, product)).status_code == 200
    assert client.post(f"/surveys/responses/{response_id}/submit", headers=auth(user)).status_code == 200
    return response_id


def _start(client, user, upload_id, data, response_id=None, name="feedback.mp4", checksum=True):
    body = {"upload_id": upload_id, "filename": name, "total_bytes": len(data), "response_id": response_id}
    if checksum:
        body["sha256"] = hashlib.sha256(data).hexdigest()
    return client.post("/uploads/resumable", headers=auth(user), json=body)


def _put(client, user, upload_id, offset, piece):
    return client.put(f"/uploads/resumable/{upload_id}", params={"offset": offset}, content=piece,
                      headers={**auth(user), "Content-Type": "application/octet-stream"})


def test_interrupted_upload_resumes_and_queues_one_analysis(client, db, product, alice):
    response_id = _survey(client, db, product, alice)
    data = bytes(range(256)) * 4000  # ~1 MB "recording"
    upload_id = str(uuid.uuid4())

    r = _start(client, alice, upload_id, data, response_id)
    assert r.status_code == 200, r.text
    assert r.json()["received_bytes"] == 0

    # First piece arrives, then the connection drops.
    assert _put(client, alice, upload_id, 0, data[:300_000]).json()["received_bytes"] == 300_000

    # The app restarts: starting the same upload again reports what arrived.
    state = _start(client, alice, upload_id, data, response_id).json()
    assert state["received_bytes"] == 300_000 and state["status"] == "receiving"

    # A piece from too far ahead is refused with the position to continue from.
    gap = _put(client, alice, upload_id, 500_000, data[500_000:600_000])
    assert gap.status_code == 409
    assert gap.json()["detail"]["received_bytes"] == 300_000

    # The lost reply case: the previous piece is sent again, overlapping. Nothing is written twice.
    assert _put(client, alice, upload_id, 200_000, data[200_000:700_000]).json()["received_bytes"] == 700_000
    assert _put(client, alice, upload_id, 700_000, data[700_000:]).json()["received_bytes"] == len(data)

    done = client.post(f"/uploads/resumable/{upload_id}/complete", headers=auth(alice))
    assert done.status_code == 200, done.text
    job = done.json()["job"]
    assert job["status"] == "queued" and job["response_id"] == response_id

    # The server's file is byte-for-byte the phone's file.
    stored = db.query(AnalysisJob).get(job["job_id"]).stored_path
    from app.services.storage import get_storage

    assert get_storage().resolve(stored).read_bytes() == data

    # "complete" retried after a lost reply: the same job, never a second analysis.
    again = client.post(f"/uploads/resumable/{upload_id}/complete", headers=auth(alice)).json()
    assert again["job"]["job_id"] == job["job_id"]
    assert db.query(AnalysisJob).filter(AnalysisJob.response_id == response_id).count() == 1


def test_complete_before_all_bytes_arrive_is_refused(client, db, product, alice):
    data = b"x" * 5000
    upload_id = str(uuid.uuid4())
    _start(client, alice, upload_id, data)
    _put(client, alice, upload_id, 0, data[:1000])
    r = client.post(f"/uploads/resumable/{upload_id}/complete", headers=auth(alice))
    assert r.status_code == 409
    assert r.json()["detail"]["received_bytes"] == 1000


def test_damaged_file_is_detected_and_restarted(client, db, product, alice):
    data = b"real recording bytes" * 100
    upload_id = str(uuid.uuid4())
    _start(client, alice, upload_id, data)
    damaged = bytearray(data)
    damaged[10] ^= 0xFF
    _put(client, alice, upload_id, 0, bytes(damaged))
    r = client.post(f"/uploads/resumable/{upload_id}/complete", headers=auth(alice))
    assert r.status_code == 422
    assert client.get(f"/uploads/resumable/{upload_id}", headers=auth(alice)).json()["received_bytes"] == 0
    # Sent again correctly, it completes.
    _put(client, alice, upload_id, 0, data)
    assert client.post(f"/uploads/resumable/{upload_id}/complete", headers=auth(alice)).status_code == 200


def test_uploads_are_private_to_their_account(client, db, product, alice, bob):
    data = b"y" * 100
    upload_id = str(uuid.uuid4())
    _start(client, alice, upload_id, data)
    assert client.get(f"/uploads/resumable/{upload_id}", headers=auth(bob)).status_code == 404
    assert _put(client, bob, upload_id, 0, data).status_code == 404
    assert _start(client, bob, upload_id, data).status_code == 409
    # Someone else's survey cannot be attached to an upload.
    response_id = _survey(client, db, product, alice)
    assert _start(client, bob, str(uuid.uuid4()), data, response_id).status_code == 404


def test_upload_needs_a_signed_in_account(client):
    r = client.post("/uploads/resumable", json={"upload_id": str(uuid.uuid4()), "filename": "a.mp4", "total_bytes": 1})
    assert r.status_code == 401


def test_unsupported_format_and_oversize_are_refused(client, alice):
    r = client.post("/uploads/resumable", headers=auth(alice),
                    json={"upload_id": str(uuid.uuid4()), "filename": "virus.exe", "total_bytes": 10})
    assert r.status_code == 400
    r = client.post("/uploads/resumable", headers=auth(alice),
                    json={"upload_id": str(uuid.uuid4()), "filename": "big.mp4", "total_bytes": 10 ** 12})
    assert r.status_code == 413


def test_same_upload_id_for_a_different_file_is_refused(client, alice):
    upload_id = str(uuid.uuid4())
    _start(client, alice, upload_id, b"a" * 10)
    assert _start(client, alice, upload_id, b"a" * 11).status_code == 409


def test_a_survey_already_being_analyzed_is_not_queued_twice(client, db, product, alice):
    response_id = _survey(client, db, product, alice)
    jobs = []
    for _ in range(2):  # two different uploads of the same survey (e.g. a reinstalled app)
        data = b"z" * 2048
        upload_id = str(uuid.uuid4())
        _start(client, alice, upload_id, data, response_id)
        _put(client, alice, upload_id, 0, data)
        jobs.append(client.post(f"/uploads/resumable/{upload_id}/complete", headers=auth(alice)).json()["job"]["job_id"])
    assert jobs[0] == jobs[1]
    assert db.query(AnalysisJob).filter(AnalysisJob.response_id == response_id).count() == 1
    assert db.query(UploadSession).filter(UploadSession.response_id == response_id).count() == 2


def test_abandoned_partial_uploads_are_cleaned_up(client, db, alice):
    from datetime import datetime, timedelta

    from app.services.storage import get_storage

    data = b"p" * 1000
    old_id, new_id = str(uuid.uuid4()), str(uuid.uuid4())
    _start(client, alice, old_id, data)
    _put(client, alice, old_id, 0, data[:500])
    stale = db.query(UploadSession).filter(UploadSession.upload_id == old_id).one()
    part = get_storage().resolve(stale.part_path)
    assert part.exists()
    stale.updated_at = datetime.utcnow() - timedelta(days=30)
    db.commit()

    _start(client, alice, new_id, data)  # any new upload cleans up
    db.expire_all()
    assert db.query(UploadSession).filter(UploadSession.upload_id == old_id).count() == 0
    assert not part.exists()
    assert db.query(UploadSession).filter(UploadSession.upload_id == new_id).count() == 1
