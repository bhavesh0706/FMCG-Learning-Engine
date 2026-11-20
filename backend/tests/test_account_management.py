"""
Adding accounts from the dashboards:
  Super Admin portal -> Super Admins (/org/super-admins), stored as users.role = "client"
  Admin Team page    -> Admins (/admin/users with role "admin")
"""

from conftest import _unique, auth, make_user

from app.database.models import User


def _login(client, identifier, password):
    return client.post("/auth/login", json={"identifier": identifier, "password": password})


def _new_super_admin(client, org, name="Priya Shah", email=None, password="Passw0rd!"):
    email = email or f"{_unique('sa')}@acmefoods.com"
    return client.post("/org/super-admins", headers=auth(org), json={"name": name, "email": email, "current_password": password})


def test_a_super_admin_adds_a_super_admin_who_can_sign_in(client, db):
    org = make_user(db, role="client")
    r = _new_super_admin(client, org, name="Priya  Shah")
    assert r.status_code == 201, r.text
    body = r.json()
    row = db.query(User).filter(User.email == body["user"]["email"]).first()
    assert row is not None and row.role == "client" and row.is_active == 1 and row.must_change_password == 1, "saved in the database"
    assert row.name == "Priya Shah" and row.mobile is None and len(body["password"]) >= 12
    signed_in = _login(client, row.email, body["password"])
    assert signed_in.status_code == 200, signed_in.text
    assert signed_in.json()["user"]["role"] == "client" and signed_in.json()["user"]["must_change_password"] is True
    listed = client.get("/org/super-admins", headers=auth(org)).json()["super_admins"]
    assert {u["email"] for u in listed} >= {row.email} and any(u["is_me"] for u in listed)


def test_adding_needs_the_right_password_a_new_email_and_a_super_admin(client, db, admin):
    org = make_user(db, role="client")
    assert _new_super_admin(client, org, password="wrong").status_code == 403
    taken = _new_super_admin(client, org).json()["user"]["email"]
    assert _new_super_admin(client, org, email=taken.upper()).status_code == 409
    assert _new_super_admin(client, org, email="not-an-email").status_code == 400
    person = make_user(db)
    for user in (admin, person):  # admins can reach some /org routes: refused in the code
        assert client.get("/org/super-admins", headers=auth(user)).status_code == 403
        assert client.post("/org/super-admins", headers=auth(user), json={"name": "X Y", "email": "x@y.com", "current_password": "Passw0rd!"}).status_code == 403
    assert db.query(User).filter(User.email == "x@y.com").count() == 0


def test_reset_password_and_disable_another_super_admin(client, db):
    org = make_user(db, role="client")
    new = _new_super_admin(client, org).json()
    uid, email, first = new["user"]["id"], new["user"]["email"], new["password"]
    old_token = _login(client, email, first).json()["token"]
    reset = client.post(f"/org/super-admins/{uid}/reset-password", headers=auth(org), json={"current_password": "Passw0rd!"})
    assert reset.status_code == 200
    assert _login(client, email, first).status_code == 401 and _login(client, email, reset.json()["password"]).status_code == 200
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {old_token}"}).status_code == 401, "signed out everywhere"
    off = client.put(f"/org/super-admins/{uid}", headers=auth(org), json={"is_active": False, "current_password": "Passw0rd!"})
    assert off.status_code == 200 and off.json()["user"]["is_active"] is False
    assert _login(client, email, reset.json()["password"]).status_code in (401, 403)
    assert client.put(f"/org/super-admins/{org.id}", headers=auth(org), json={"is_active": False, "current_password": "Passw0rd!"}).status_code == 400
    on = client.put(f"/org/super-admins/{uid}", headers=auth(org), json={"is_active": True, "current_password": "Passw0rd!"})
    assert on.json()["user"]["is_active"] is True and _login(client, email, reset.json()["password"]).status_code == 200


def test_an_admin_adds_an_admin_who_can_sign_in(client, db, admin):
    mobile = f"97{int(_unique('')):08d}"  # a valid, unique 10-digit mobile
    r = client.post("/admin/users", headers=auth(admin), json={"name": "Neha Verma", "mobile": mobile, "email": f"{_unique('neha')}@ione.in",
                                                                 "password": "Start#2026x", "role": "admin"})
    assert r.status_code == 201, r.text
    row = db.get(User, r.json()["user"]["id"])
    assert row.role == "admin" and row.is_active == 1, "saved in the database as an admin"
    assert _login(client, mobile, "Start#2026x").json()["user"]["role"] == "admin"
    listed = client.get("/admin/users", headers=auth(admin)).json()["users"]
    assert any(u["id"] == row.id and u["role"] == "admin" for u in listed)


def test_an_admin_cannot_lock_themselves_out(client, db, admin):
    assert client.put(f"/admin/users/{admin.id}", headers=auth(admin), json={"is_active": False}).status_code == 400
    assert client.put(f"/admin/users/{admin.id}", headers=auth(admin), json={"role": "executive"}).status_code == 400
    other = make_user(db, role="admin")
    assert client.put(f"/admin/users/{other.id}", headers=auth(admin), json={"is_active": False}).status_code == 200
    db.refresh(other)
    assert other.is_active == 0
