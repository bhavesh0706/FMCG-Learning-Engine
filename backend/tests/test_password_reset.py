"""
Forgot password for Admin and Super Admin: a 6-digit code is emailed, then the code,
the new password and the repeat set it (services/password_reset.py). The same code
replaces the current password on the Account page.
"""

import re
from datetime import datetime, timedelta

import pytest
from conftest import _unique, auth, make_user

from app.database.models import PasswordResetCode


@pytest.fixture(autouse=True)
def email_mode(monkeypatch):
    """These tests are about the emailed code (production); simple mode is tested below."""
    monkeypatch.setenv("FMCG_PASSWORD_RESET", "email")


@pytest.fixture()
def outbox(monkeypatch):
    sent = []
    monkeypatch.setattr("app.services.mailer.send_mail", lambda to, subject, text: sent.append((to, text)) or True)
    return sent


def _with_email(db, role):
    user = make_user(db, role=role)
    user.email = f"{_unique('boss')}@acme.in".lower()
    db.commit()
    return user


def _code(outbox):
    return re.search(r"\b(\d{6})\b", outbox[-1][1]).group(1)


def _request(client, email):
    r = client.post("/auth/password-reset/request", json={"email": email})
    assert r.status_code == 200
    return r.json()["detail"]


@pytest.mark.parametrize("role", ["admin", "client"])
def test_admins_and_super_admins_reset_with_an_emailed_code(client, db, outbox, role):
    user = _with_email(db, role)
    old = auth(user)
    _request(client, user.email.upper())  # emails are case-insensitive
    assert len(outbox) == 1 and outbox[0][0] == user.email
    bad = client.post("/auth/password-reset/confirm", json={"email": user.email, "code": "000000", "new_password": "NewPass2026"})
    assert bad.status_code == 400
    ok = client.post("/auth/password-reset/confirm", json={"email": user.email, "code": _code(outbox), "new_password": "NewPass2026"})
    assert ok.status_code == 200, ok.text
    assert client.get("/auth/me", headers=old).status_code == 401, "every old sign-in ended"
    assert client.post("/auth/login", json={"identifier": user.email, "password": "NewPass2026"}).status_code == 200
    again = client.post("/auth/password-reset/confirm", json={"email": user.email, "code": _code(outbox), "new_password": "Other2026x"})
    assert again.status_code == 400, "a code works once"


def test_unknown_emails_get_the_same_answer_and_no_email(client, db, outbox):
    user = _with_email(db, "admin")
    assert _request(client, "nobody@nowhere.in") == _request(client, user.email)
    assert [to for to, _ in outbox] == [user.email]


def test_survey_persons_and_disabled_accounts_get_no_code(client, db, outbox):
    person = _with_email(db, "executive")
    off = _with_email(db, "admin")
    off.is_active = 0
    db.commit()
    _request(client, person.email)
    _request(client, off.email)
    assert outbox == []


def test_codes_expire_lock_after_five_tries_and_are_not_resent_at_once(client, db, outbox):
    user = _with_email(db, "admin")
    _request(client, user.email)
    _request(client, user.email)
    assert len(outbox) == 1, "a second request within a minute isn't sent"
    code = _code(outbox)
    for _ in range(5):
        client.post("/auth/password-reset/confirm", json={"email": user.email, "code": "111111", "new_password": "NewPass2026"})
    locked = client.post("/auth/password-reset/confirm", json={"email": user.email, "code": code, "new_password": "NewPass2026"})
    assert locked.status_code == 400 and "Too many" in locked.json()["detail"]
    row = db.query(PasswordResetCode).filter_by(user_id=user.id).first()
    row.attempts, row.expires_at = 0, datetime.utcnow() - timedelta(minutes=1)
    db.commit()
    expired = client.post("/auth/password-reset/confirm", json={"email": user.email, "code": code, "new_password": "NewPass2026"})
    assert expired.status_code == 400 and "expired" in expired.json()["detail"]
    assert row.code_hash != code, "stored hashed"


def test_weak_new_passwords_are_refused(client, db, outbox):
    user = _with_email(db, "client")
    _request(client, user.email)
    r = client.post("/auth/password-reset/confirm", json={"email": user.email, "code": _code(outbox), "new_password": "onlyletters"})
    assert r.status_code in (400, 422)


def test_the_account_page_changes_the_password_with_a_code_instead_of_the_current_one(client, db, outbox):
    user = _with_email(db, "client")
    h = auth(user)
    sent = client.post("/auth/me/password-code", headers=h)
    assert sent.status_code == 200 and sent.json()["sent"] is True and "…@" in sent.json()["email"]
    no_proof = client.post("/auth/change-password", headers=h, json={"new_password": "NewPass2026"})
    assert no_proof.status_code == 400
    done = client.post("/auth/change-password", headers=h, json={"code": _code(outbox), "new_password": "NewPass2026"})
    assert done.status_code == 200 and done.json()["token"]
    assert client.post("/auth/login", json={"identifier": user.email, "password": "NewPass2026"}).status_code == 200
    # survey persons can't ask for codes (their admin resets their password)
    person = make_user(db, role="executive")
    assert client.post("/auth/me/password-code", headers=auth(person)).status_code == 400


# ---------------------------------------------------------------- simple mode (no real emails yet)


@pytest.fixture()
def simple(monkeypatch):
    monkeypatch.setenv("FMCG_PASSWORD_RESET", "simple")


def test_simple_mode_resets_with_the_mobile_number_and_a_new_password(client, db, simple):
    admin = make_user(db, role="admin")
    old = auth(admin)
    r = client.post("/auth/password-reset/simple", json={"identifier": f"+91 {admin.mobile}", "new_password": "NewPass2026"})
    assert r.status_code == 200, r.text
    assert client.get("/auth/me", headers=old).status_code == 401, "every old sign-in ended"
    assert client.post("/auth/login", json={"identifier": admin.mobile, "password": "NewPass2026"}).status_code == 200
    boss = _with_email(db, "client")
    assert client.post("/auth/password-reset/simple", json={"identifier": boss.email, "new_password": "NewPass2026"}).status_code == 200


def test_simple_mode_refuses_survey_persons_unknown_numbers_and_weak_passwords(client, db, simple):
    person = make_user(db, role="executive")
    assert client.post("/auth/password-reset/simple", json={"identifier": person.mobile, "new_password": "NewPass2026"}).status_code == 400
    assert client.post("/auth/password-reset/simple", json={"identifier": "9000099999", "new_password": "NewPass2026"}).status_code == 400
    admin = make_user(db, role="admin")
    assert client.post("/auth/password-reset/simple", json={"identifier": admin.mobile, "new_password": "onlyletters"}).status_code in (400, 422)


def test_simple_mode_account_page_needs_only_the_new_password(client, db, simple):
    boss = _with_email(db, "client")
    r = client.post("/auth/change-password", headers=auth(boss), json={"new_password": "NewPass2026"})
    assert r.status_code == 200 and r.json()["token"]
    assert client.post("/auth/login", json={"identifier": boss.email, "password": "NewPass2026"}).status_code == 200


def test_production_uses_the_emailed_code_only(client, db, monkeypatch):
    monkeypatch.delenv("FMCG_PASSWORD_RESET", raising=False)
    monkeypatch.setenv("APP_ENV", "production")
    admin = make_user(db, role="admin")
    assert client.get("/auth/setup-status").json()["password_reset"] == "email"
    assert client.post("/auth/password-reset/simple", json={"identifier": admin.mobile, "new_password": "NewPass2026"}).status_code == 403
    assert client.post("/auth/change-password", headers=auth(admin), json={"new_password": "NewPass2026"}).status_code == 400
    monkeypatch.setenv("APP_ENV", "development")
    assert client.get("/auth/setup-status").json()["password_reset"] == "simple"
