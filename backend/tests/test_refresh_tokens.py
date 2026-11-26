"""The phone stays signed in with a rotating refresh token: renewed without the password, revoked by
logout, password change, sign-out everywhere, disabling the account, reuse of an old token, expiry."""

from datetime import datetime, timedelta

from conftest import make_user

from app.database.models import RefreshToken, User


def _login(client, user, remember=True):
    r = client.post("/auth/login", json={"identifier": user.mobile, "password": "Passw0rd!", "remember_device": remember, "device": "Pixel 7"})
    assert r.status_code == 200, r.text
    return r.json()


def _refresh(client, token):
    return client.post("/auth/refresh", json={"refresh_token": token})


def test_the_web_gets_no_refresh_token_and_the_phone_does(client, db):
    u = make_user(db)
    assert "refresh_token" not in _login(client, u, remember=False)
    data = _login(client, u)
    assert len(data["refresh_token"]) > 40
    row = db.query(RefreshToken).filter(RefreshToken.user_id == u.id).one()
    assert row.token_hash != data["refresh_token"], "only a hash is stored"


def test_refresh_renews_and_rotates_and_a_reused_token_ends_the_device_sign_in(client, db):
    u = make_user(db)
    first = _login(client, u)["refresh_token"]
    r = _refresh(client, first)
    assert r.status_code == 200 and r.json()["user"]["id"] == u.id
    second, access = r.json()["refresh_token"], r.json()["token"]
    assert second != first and client.get("/auth/me", headers={"Authorization": f"Bearer {access}"}).status_code == 200
    assert _refresh(client, first).status_code == 401, "a used-up token can't be used again"
    assert _refresh(client, second).status_code == 401, "...and its reuse ended the whole device sign-in"


def test_logout_password_change_sign_out_everywhere_disable_and_expiry_end_it(client, db, admin):
    u = make_user(db)
    t = _login(client, u)
    assert client.post("/auth/logout", json={"refresh_token": t["refresh_token"]}, headers={"Authorization": f"Bearer {t['token']}"}).status_code == 200
    assert _refresh(client, t["refresh_token"]).status_code == 401, "logout revokes it"
    t = _login(client, u)
    assert client.post("/auth/logout-all", headers={"Authorization": f"Bearer {t['token']}"}).status_code == 200
    assert _refresh(client, t["refresh_token"]).status_code == 401, "sign-out everywhere revokes it"
    t = _login(client, u)
    db.expire_all()
    db.get(User, u.id).token_version = int(db.get(User, u.id).token_version or 0) + 1  # e.g. password reset by the admin
    db.commit()
    assert _refresh(client, t["refresh_token"]).status_code == 401, "a password change revokes it"
    t = _login(client, u)
    db.expire_all()
    db.get(User, u.id).is_active = 0
    db.commit()
    assert _refresh(client, t["refresh_token"]).status_code == 403, "a disabled account can't renew"
    db.get(User, u.id).is_active = 1
    db.commit()
    t = _login(client, u)
    db.expire_all()
    row = db.query(RefreshToken).filter(RefreshToken.user_id == u.id, RefreshToken.revoked_at.is_(None)).one()
    row.expires_at = datetime.utcnow() - timedelta(minutes=1)
    db.commit()
    assert _refresh(client, t["refresh_token"]).status_code == 401, "an expired one ends"


def test_a_survey_person_reconnects_without_the_password_until_revoked(client, db, admin):
    """Signed in online once; offline for a while; back online the phone renews with the refresh
    token only (no password), and uploads work; once the admin disables the account it ends."""
    from conftest import auth
    person = make_user(db, role="executive")
    first = _login(client, person)                     # 1. online sign-in (the phone keeps the refresh token)
    assert first["refresh_token"] and "Passw0rd!" not in str(first), "the password is never sent back"
    stored = db.query(RefreshToken).filter(RefreshToken.user_id == person.id).one()
    assert stored.token_hash != first["refresh_token"], "only a hash is stored on the server"
    # 2. offline: nothing reaches the server. 3. back online: renewal without the password
    back = _refresh(client, first["refresh_token"])
    assert back.status_code == 200 and back.json()["refresh_token"] != first["refresh_token"]
    h = {"Authorization": f"Bearer {back.json()['token']}"}
    assert client.get("/auth/me", headers=h).status_code == 200
    assert client.get("/auth/me/surveys", headers=h).status_code == 200, "their own data with the renewed session"
    up = client.post("/surveys/respondents", headers=h, json={"name": "Offline customer", "consent_given": True, "city": "Noida"})
    assert up.status_code in (200, 201), "uploads work with the renewed session"
    # the next renewal (e.g. a later reconnection) also works without the password
    again = _refresh(client, back.json()["refresh_token"])
    assert again.status_code == 200
    # 4. the admin disables the account: the phone's next renewal ends the sign-in
    r = client.post(f"/admin/users/{person.id}/disable", headers=auth(admin))
    if r.status_code == 404:  # older route name
        r = client.put(f"/admin/users/{person.id}", headers=auth(admin), json={"is_active": False})
    assert r.status_code in (200, 204), r.text
    ended = _refresh(client, again.json()["refresh_token"])
    assert ended.status_code in (401, 403)
    db.expire_all()
    assert all(t.revoked_at is not None for t in db.query(RefreshToken).filter(RefreshToken.user_id == person.id))