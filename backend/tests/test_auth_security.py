"""
Sign-in hardening: login throttling (SEC-04), password hashing cost and rehash
(SEC-09), removed legacy endpoints (SEC-02), upload limits (SEC-08), and the
end of name-based survey matching (DATA-01).
"""

import os
from pathlib import Path

from conftest import auth, make_user, start_survey

from app.api import auth as auth_module
from app.database.models import LoginFailure, Respondent, SurveyResponse, User


def _login(client, identifier, password, ip="203.0.113.7"):
    return client.post("/auth/login", json={"identifier": identifier, "password": password},
                       headers={"X-Forwarded-For": ip})


# ---------------- login throttling ----------------

def test_login_works_with_the_right_password(client, db):
    user = make_user(db, password="Right0ne!")
    r = _login(client, user.mobile, "Right0ne!")
    assert r.status_code == 200
    assert r.json()["user"]["id"] == user.id


def test_sixth_wrong_password_is_throttled(client, db):
    user = make_user(db, password="Right0ne!")
    for _ in range(auth_module.MAX_FAILURES_PER_ACCOUNT):
        assert _login(client, user.mobile, "wrong-pass1").status_code == 401
    r = _login(client, user.mobile, "Right0ne!")  # even the right password waits
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) > 0


def test_success_clears_the_failure_count(client, db):
    user = make_user(db, password="Right0ne!")
    for _ in range(auth_module.MAX_FAILURES_PER_ACCOUNT - 1):
        _login(client, user.mobile, "wrong-pass1")
    assert _login(client, user.mobile, "Right0ne!").status_code == 200
    assert db.query(LoginFailure).filter(LoginFailure.key == f"id:{user.mobile}").count() == 0


def test_unknown_accounts_get_the_same_answer(client):
    r = _login(client, "9999988888", "whatever1")
    assert r.status_code == 401
    assert r.json()["detail"] == "The mobile/email or password is incorrect."


# ---------------- password hashing ----------------

def test_new_hashes_use_the_configured_cost():
    stored = auth_module.hash_password("Secret123")
    assert stored.split("$")[1] == str(auth_module.PBKDF2_ROUNDS)
    assert auth_module.verify_password("Secret123", stored)
    assert not auth_module.verify_password("Secret124", stored)


def test_old_hashes_are_upgraded_on_login(client, db):
    user = make_user(db, password="Upgrade1!")
    salt = "00" * 16
    import hashlib
    old_rounds = auth_module.PBKDF2_ROUNDS // 2
    digest = hashlib.pbkdf2_hmac("sha256", b"Upgrade1!", salt.encode(), old_rounds).hex()
    user.password_hash = f"pbkdf2_sha256${old_rounds}${salt}${digest}"
    db.commit()
    assert _login(client, user.mobile, "Upgrade1!", ip="198.51.100.20").status_code == 200
    db.expire_all()
    assert db.get(User, user.id).password_hash.split("$")[1] == str(auth_module.PBKDF2_ROUNDS)


def test_production_default_cost_follows_owasp():
    assert auth_module.DEFAULT_PBKDF2_ROUNDS >= 600_000


# ---------------- legacy endpoint removed ----------------

def test_csv_upload_endpoint_is_gone_and_cannot_write_files(client, db, tmp_path):
    user = make_user(db)
    target = Path(os.getcwd()).parent / "PT_should_not_exist.csv"
    r = client.post(
        "/upload/",
        headers=auth(user),
        files={"file": ("../../PT_should_not_exist.csv", b"feedback\nnice\n", "text/csv")},
    )
    assert r.status_code in (404, 405)
    assert not target.exists()


# ---------------- upload size limit ----------------

def test_oversized_upload_is_refused(client, db, product, monkeypatch):
    from app.api import uploads_limit
    user = make_user(db)
    ids = start_survey(client, user, product)
    monkeypatch.setattr(uploads_limit, "MAX_UPLOAD_BYTES", 1024)
    big = ("big.m4a", b"\x00" * 4096, "audio/mp4")
    r = client.post("/analysis/jobs", headers=auth(user), files={"file": big}, data={"response_id": str(ids["response_id"])})
    assert r.status_code == 413
    queue = Path(os.environ["FMCG_QUEUE_DIR"])
    assert not any(p.name.endswith("big.m4a") for p in queue.glob("*"))  # partial file removed


# ---------------- no more name-based matching ----------------

def _legacy_survey(db, product, executive_name):
    person = Respondent(name="Legacy Customer", consent_given=1)
    db.add(person)
    db.commit()
    survey = SurveyResponse(product_id=product.id, respondent_id=person.id, executive_name=executive_name,
                            status="submitted", user_id=None)
    db.add(survey)
    db.commit()
    return survey


def test_same_name_people_do_not_see_each_others_old_surveys(client, db, product):
    first = make_user(db, name="Rahul Sharma")
    second = make_user(db, name="Rahul  Sharma")  # same name, different spacing
    old = _legacy_survey(db, product, "rahul sharma")
    from app.database.migrations import link_unclaimed_surveys
    link_unclaimed_surveys()
    db.expire_all()
    assert db.get(SurveyResponse, old.id).user_id is None  # ambiguous: left for an admin
    for person in (first, second):
        listed = client.get("/auth/me/surveys", headers=auth(person)).json()
        ids = {s.get("response_id") or s.get("id") for s in listed.get("surveys", listed if isinstance(listed, list) else [])}
        assert old.id not in ids


def test_unique_name_old_surveys_are_linked_once(db, product):
    owner = make_user(db, name="Meera Unique Name")
    old = _legacy_survey(db, product, "Meera  unique name")
    from app.database.migrations import link_unclaimed_surveys
    link_unclaimed_surveys()
    db.expire_all()
    assert db.get(SurveyResponse, old.id).user_id == owner.id
