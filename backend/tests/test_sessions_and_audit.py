"""
Phase 2: shorter sign-ins with real sign-out (SEC-06) and the audit log.
"""

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timedelta

from conftest import auth, make_user, start_survey

from app.api import auth as auth_module
from app.database.models import AuditLog, Product, User
from app.services import audit as audit_module


def _claims(token: str) -> dict:
    payload = token.split(".")[0]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))


def _login(client, user, password="Passw0rd!"):
    r = client.post("/auth/login", json={"identifier": user.mobile, "password": password},
                    headers={"X-Forwarded-For": f"10.9.{user.id % 250}.1"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _entries(db, **filters):
    db.expire_all()
    return db.query(AuditLog).filter_by(**filters).order_by(AuditLog.id).all()


# ---------------- sign-in length ----------------

def test_survey_person_token_lasts_7_days_and_admin_token_24_hours(client, db):
    person = make_user(db)
    admin = make_user(db, role="admin")
    c = _claims(_login(client, person))
    assert 7 * 86400 - 60 <= c["exp"] - c["iat"] <= 7 * 86400
    c = _claims(_login(client, admin))
    assert 24 * 3600 - 60 <= c["exp"] - c["iat"] <= 24 * 3600


# ---------------- sign-out ----------------

def test_sign_out_ends_only_this_device(client, db):
    person = make_user(db)
    phone, laptop = _login(client, person), _login(client, person)
    assert client.post("/auth/logout", headers=_bearer(phone)).status_code == 200
    assert client.get("/auth/me", headers=_bearer(phone)).status_code == 401
    assert client.get("/surveys/responses/1", headers=_bearer(phone)).status_code == 401  # middleware refuses it too
    assert client.get("/auth/me", headers=_bearer(laptop)).status_code == 200
    # Signing out twice, or with an old token, is harmless.
    assert client.post("/auth/logout", headers=_bearer(phone)).status_code == 200


def test_sign_out_everywhere(client, db):
    person = make_user(db)
    phone, laptop = _login(client, person), _login(client, person)
    assert client.post("/auth/logout-all", headers=_bearer(phone)).status_code == 200
    assert client.get("/auth/me", headers=_bearer(phone)).status_code == 401
    assert client.get("/auth/me", headers=_bearer(laptop)).status_code == 401
    assert client.get("/auth/me", headers=_bearer(_login(client, person))).status_code == 200  # a new sign-in works


def test_signed_out_token_cannot_play_recordings(client, db, alice, product, tmp_path):
    from test_access_control import _feedback_for

    token = _login(client, alice)
    ids = start_survey(client, alice, product)
    record = _feedback_for(db, ids["response_id"], tmp_path)
    assert client.get(f"/feedback/{record.id}/video?token={token}").status_code == 200
    client.post("/auth/logout", headers=_bearer(token))
    assert client.get(f"/feedback/{record.id}/video?token={token}").status_code == 401


def test_admin_actions_sign_a_person_out_everywhere(client, db, admin):
    person = make_user(db)
    token = _login(client, person)

    # Changing what they may see does NOT sign them out.
    r = client.put(f"/admin/users/{person.id}", json={"can_view_history": False}, headers=auth(admin))
    assert r.status_code == 200
    assert client.get("/auth/me", headers=_bearer(token)).status_code == 200

    # Lost phone: "sign out of every device".
    assert client.post(f"/admin/users/{person.id}/sign-out", headers=auth(admin)).status_code == 200
    assert client.get("/auth/me", headers=_bearer(token)).status_code == 401

    # Password reset.
    token = _login(client, person)
    client.put(f"/admin/users/{person.id}/password", json={"password": "N3wPassw0rd!"}, headers=auth(admin))
    assert client.get("/auth/me", headers=_bearer(token)).status_code == 401
    token = _login(client, person, "N3wPassw0rd!")

    # Disabled, then enabled again: the old sign-in stays ended.
    client.put(f"/admin/users/{person.id}", json={"is_active": False}, headers=auth(admin))
    client.put(f"/admin/users/{person.id}", json={"is_active": True}, headers=auth(admin))
    assert client.get("/auth/me", headers=_bearer(token)).status_code == 401

    # Role change.
    token = _login(client, person, "N3wPassw0rd!")
    client.put(f"/admin/users/{person.id}", json={"role": "admin"}, headers=auth(admin))
    assert client.get("/auth/me", headers=_bearer(token)).status_code == 401


def test_survey_persons_cannot_sign_others_out(client, db, alice, bob):
    assert client.post(f"/admin/users/{bob.id}/sign-out", headers=auth(alice)).status_code == 403


# ---------------- renewal and older tokens ----------------

def test_token_is_renewed_once_a_day_old(client, db, monkeypatch):
    person = make_user(db)
    token = _login(client, person)
    body = client.get("/auth/me", headers=_bearer(token)).json()
    assert "token" not in body  # brand new: nothing to renew
    monkeypatch.setattr(auth_module, "RENEW_AFTER_SECONDS", 0)
    renewed = client.get("/auth/me", headers=_bearer(token)).json()["token"]
    assert renewed != token and client.get("/auth/me", headers=_bearer(renewed)).status_code == 200


def _old_style_token(user: User) -> str:
    """A token from the previous version: no issue date, no version number, 30 days."""
    payload = auth_module._b64(json.dumps({"uid": user.id, "exp": int(time.time()) + 30 * 86400}).encode())
    signature = auth_module._b64(hmac.new(auth_module._secret(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{signature}"


def test_tokens_from_the_previous_version_keep_working_and_are_replaced(client, db):
    person = make_user(db)
    old = _old_style_token(person)
    body = client.get("/auth/me", headers=_bearer(old)).json()
    assert body["user"]["id"] == person.id
    assert _claims(body["token"])["exp"] - time.time() <= 7 * 86400  # swapped for a 7-day token
    # ...and an old token can be signed out like any other.
    client.post("/auth/logout", headers=_bearer(old))
    assert client.get("/auth/me", headers=_bearer(old)).status_code == 401


# ---------------- audit log ----------------

def test_sign_in_and_failed_sign_in_are_logged_without_the_full_number(client, db):
    person = make_user(db)
    _login(client, person)
    client.post("/auth/login", json={"identifier": person.mobile, "password": "wrong-password"},
                headers={"X-Forwarded-For": "10.99.0.1"})
    ok = _entries(db, action="auth.login", user_id=person.id)
    assert ok and ok[-1].summary == "Signed in"
    failed = _entries(db, action="auth.login_failed", target_id=person.id)
    assert failed and person.mobile not in failed[-1].summary and person.mobile[-2:] in failed[-1].summary
    assert failed[-1].ip == "testclient" or failed[-1].ip  # an address is stored


def test_team_changes_are_logged_with_what_changed(client, db, admin):
    person = make_user(db, can_view_history=1)
    client.put(f"/admin/users/{person.id}", json={"can_view_history": False, "is_active": False}, headers=auth(admin))
    entry = _entries(db, action="user.update", target_id=person.id)[-1]
    assert entry.user_id == admin.id and entry.user_name == admin.name and entry.user_role == "admin"
    assert entry.details == {"can_view_history": [1, 0], "is_active": [1, 0]}
    assert "history access" in entry.summary and entry.status == 200 and entry.request_id


def test_password_reset_is_logged_without_the_password(client, db, admin):
    person = make_user(db)
    client.put(f"/admin/users/{person.id}/password", json={"password": "S3cretPassw0rd!"}, headers=auth(admin))
    entry = _entries(db, action="user.password_reset", target_id=person.id)[-1]
    assert "S3cretPassw0rd!" not in json.dumps({"s": entry.summary, "d": entry.details, "p": entry.path})


def test_catalog_changes_keep_the_name_even_after_deletion(client, db, admin, product):
    # Products are managed by the organization (its portal's Catalog).
    org = make_user(db, role="client")
    name, pid = product.name, product.id
    assert client.delete(f"/products/{pid}", headers=auth(org)).status_code == 200
    assert client.delete(f"/products/{pid}/permanent", headers=auth(org)).status_code == 200
    db.expire_all()
    assert db.get(Product, pid) is None
    deactivated = _entries(db, action="product.deactivate", target_id=pid)[-1]
    deleted = _entries(db, action="product.delete", target_id=pid)[-1]
    assert name in deactivated.summary and name in deleted.summary


def test_recipe_change_is_logged(client, db, admin, product):
    # Recipes are changed by the organization (its portal); the change is logged with its version.
    org = make_user(db, role="client")
    recipe = {"batch_size": "10 kg", "ingredients": [{"name": "Sugar", "quantity": "2", "unit": "kg"}],
              "steps": [{"title": "Mix"}, {"title": "Bake"}]}
    assert client.put(f"/products/{product.id}/recipe", json=recipe, headers=auth(org)).status_code == 200
    entry = _entries(db, action="recipe.update", target_id=product.id)[-1]
    assert entry.details["version"] == 1
    assert entry.details["changes"] == ["Recipe added: 1 ingredients, 2 steps"]


def test_survey_steps_are_logged_without_customer_details(client, db, alice, product):
    ids = start_survey(client, alice, product)
    customer = _entries(db, action="customer.create", user_id=alice.id)[-1]
    survey = _entries(db, action="survey.create", user_id=alice.id)[-1]
    assert survey.user_role == "executive"
    text = json.dumps([customer.summary, customer.details, survey.summary, survey.details])
    assert "Customer Test" not in text and "9876543210" not in text and "Andheri" not in text
    assert ids  # survey created


def test_reads_failures_and_bell_clicks_are_not_logged(client, db, alice):
    before = db.query(AuditLog).count()
    client.get("/auth/me", headers=auth(alice))
    client.post("/notifications/read-all", headers=auth(alice))
    client.put("/surveys/responses/999999/answers", json={"answers": []}, headers=auth(alice))  # 404
    db.expire_all()
    assert db.query(AuditLog).count() == before


def test_activity_log_page_api(client, db, admin, alice, product):
    start_survey(client, alice, product)
    r = client.get(f"/admin/audit?user_id={alice.id}&action=survey.", headers=auth(admin))
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1 and all(e["user_id"] == alice.id and e["action"].startswith("survey.") for e in body["entries"])
    assert body["entries"] == sorted(body["entries"], key=lambda e: -e["id"])  # newest first
    r = client.get("/admin/audit?limit=1&offset=0", headers=auth(admin))
    assert len(r.json()["entries"]) == 1
    assert client.get("/admin/audit", headers=auth(alice)).status_code == 403


def test_old_entries_are_removed_after_the_keep_period(db, monkeypatch):
    old = AuditLog(action="test.old", created_at=datetime.utcnow() - timedelta(days=40))
    new = AuditLog(action="test.new")
    db.add_all([old, new])
    db.commit()
    monkeypatch.setattr(audit_module, "KEEP_DAYS", 30)
    audit_module.prune()
    db.expire_all()
    assert not db.query(AuditLog).filter_by(action="test.old").count()
    assert db.query(AuditLog).filter_by(action="test.new").count() == 1