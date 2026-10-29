"""
One role contract for the backend, the web app and the mobile app:

    admin      -> admin dashboard / survey app with full History
    client     -> organization portal (web) / organization pages (mobile)
    executive  -> survey person

These tests cover sign-in for each role, the role always coming from the stored
user record, the "choose your own password" gate (organization accounts created by
app.tools.organization start with must_change_password), role-based access, and
the read-only account check tool.
"""

import pytest
from conftest import _unique, make_user

from app.database.models import User

PASSWORD = "Passw0rd!"


def _login(client, identifier, password=PASSWORD):
    return client.post("/auth/login", json={"identifier": identifier, "password": password})


@pytest.fixture()
def vipin(db):
    """An admin named like the affected account, signing in with an email."""
    user = make_user(db, role="admin", name="Vipin Gupta")
    user.email = f"{_unique('vipin')}@brand.in".lower()
    db.commit()
    return user


@pytest.fixture()
def org(db):
    return make_user(db, role="client", name=_unique("Acme Foods "))


# ---------------------------------------------------------------- sign-in per role


@pytest.mark.parametrize("role", ["admin", "client", "executive"])
def test_each_role_signs_in_and_gets_its_stored_role(client, db, role):
    user = make_user(db, role=role)
    r = _login(client, user.mobile)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["role"] == role
    assert body["token"]
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {body['token']}"})
    assert me.status_code == 200 and me.json()["user"]["role"] == role


def test_vipin_gupta_signs_in_as_admin_with_email_and_mobile(client, db, vipin):
    by_email = _login(client, vipin.email.upper())  # emails are case-insensitive
    by_mobile = _login(client, f"+91 {vipin.mobile[:5]} {vipin.mobile[5:]}")  # +91 and spaces are accepted
    for r in (by_email, by_mobile):
        assert r.status_code == 200, r.text
        assert r.json()["user"]["role"] == "admin"
        assert r.json()["user"]["name"] == "Vipin Gupta"


def test_a_same_named_survey_person_account_does_not_change_the_admins_role(client, db, vipin):
    # A second "Vipin Gupta" (survey person) on another mobile: each sign-in opens its own record.
    other = make_user(db, role="executive", name="Vipin Gupta")
    assert _login(client, vipin.email).json()["user"]["role"] == "admin"
    assert _login(client, other.mobile).json()["user"]["role"] == "executive"


def test_wrong_password_unknown_account_and_bad_identifier_are_rejected(client, db, vipin):
    assert _login(client, vipin.email, "Wrong-pass-1").status_code == 401
    assert _login(client, "nobody@nowhere.in").status_code == 401
    assert _login(client, "12345").status_code == 400
    # Same message whether the account exists or not.
    assert _login(client, vipin.email, "Wrong-pass-1").json()["detail"] == _login(client, "nobody@nowhere.in").json()["detail"]


def test_disabled_account_is_refused_with_403(client, db):
    user = make_user(db, role="admin")
    user.is_active = 0
    db.commit()
    r = _login(client, user.mobile)
    assert r.status_code == 403 and "disabled" in r.json()["detail"]


def test_the_role_comes_from_the_stored_record_during_a_session(client, db):
    user = make_user(db, role="executive")
    token = _login(client, user.mobile).json()["token"]
    db.query(User).filter(User.id == user.id).update({"role": "admin"})
    db.commit()
    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()["user"]
    assert me["role"] == "admin"  # the server re-reads the record; nothing is cached in the token
    assert client.get("/admin/users", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_logout_ends_the_token(client, db, vipin):
    token = _login(client, vipin.email).json()["token"]
    h = {"Authorization": f"Bearer {token}"}
    assert client.post("/auth/logout", headers=h).status_code == 200
    assert client.get("/auth/me", headers=h).status_code == 401


# ---------------------------------------------------------------- choose your own password


def test_organization_must_choose_a_password_before_anything_else(client, db, org):
    org.must_change_password = 1
    db.commit()
    body = _login(client, org.mobile).json()
    assert body["user"]["role"] == "client" and body["user"]["must_change_password"] is True
    h = {"Authorization": f"Bearer {body['token']}"}
    blocked = client.get("/org/overview", headers=h)
    assert blocked.status_code == 403 and "password" in blocked.json()["detail"].lower()
    assert client.get("/auth/me", headers=h).status_code == 200  # allowed while the password is due

    wrong = client.post("/auth/change-password", headers=h, json={"current_password": "nope-123", "new_password": "NewPass2026"})
    assert wrong.status_code == 400
    weak = client.post("/auth/change-password", headers=h, json={"current_password": PASSWORD, "new_password": "short"})
    assert weak.status_code in (400, 422)

    done = client.post("/auth/change-password", headers=h, json={"current_password": PASSWORD, "new_password": "NewPass2026"})
    assert done.status_code == 200, done.text
    assert done.json()["user"]["must_change_password"] is False and done.json()["user"]["role"] == "client"
    new_h = {"Authorization": f"Bearer {done.json()['token']}"}
    assert client.get("/org/overview", headers=new_h).status_code == 200
    assert client.get("/org/overview", headers=h).status_code == 401  # the one-time sign-in ended
    assert _login(client, org.mobile, "NewPass2026").status_code == 200


# ---------------------------------------------------------------- role-based access


def test_role_based_access(client, db, vipin, org):
    exec_user = make_user(db, role="executive")
    tokens = {u.role: _login(client, u.mobile).json()["token"] for u in (vipin, org, exec_user)}
    def status(role, path):
        return client.get(path, headers={"Authorization": f"Bearer {tokens[role]}"}).status_code

    assert status("admin", "/admin/users") == 200
    assert status("client", "/admin/users") == 403
    assert status("executive", "/admin/users") == 403
    assert status("client", "/org/overview") == 200
    assert status("admin", "/org/overview") == 200  # admins may preview the organization portal
    assert status("executive", "/org/overview") == 403  # never survey persons


# ---------------------------------------------------------------- read-only account check


def test_check_accounts_tool_reports_roles_without_secrets(db, vipin, capsys):
    from app.tools import check_accounts

    before = {u.id: (u.role, u.password_hash, u.is_active) for u in db.query(User).all()}
    check_accounts.main(["--name", "Vipin"])
    out = capsys.readouterr().out
    assert "role            : admin" in out and "Vipin Gupta" in out
    assert vipin.password_hash not in out and vipin.email not in out  # no hash, email masked
    db.expire_all()
    assert {u.id: (u.role, u.password_hash, u.is_active) for u in db.query(User).all()} == before  # read-only
