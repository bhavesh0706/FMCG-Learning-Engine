"""
Notification bell (regression suite for the feature added on 14 Oct 2026):
product-assigned notices for survey persons, missing-video notices for admins.
"""

from datetime import datetime, timedelta

from conftest import auth, make_user, open_round

import app.services.notifications as svc
from app.database.models import AnalysisJob, Category, Notification, Product, Respondent, SurveyResponse


def _unread(client, user):
    return client.get("/notifications/unread-count", headers=auth(user)).json()["unread_count"]


def _catalog(db):
    category = Category(name=f"Food {datetime.utcnow().timestamp()}", is_active=1)
    db.add(category)
    db.commit()
    first = Product(category_id=category.id, name="Honey Nut Granola", is_active=1)
    second = Product(category_id=category.id, name="Aam Jelly", is_active=1)
    db.add_all([first, second])
    db.commit()
    open_round(db, first)  # the Super Admin has started a round of each (admins assign live rounds)
    open_round(db, second)
    return category, first, second


def test_product_assignment_notifications(client, db):
    admin = make_user(db, role="admin", name="Anuj Admin")
    other_admin = make_user(db, role="admin", name="Second Admin")
    riya = make_user(db, name="Riya Kapoor")
    amit = make_user(db, name="Amit Shah")
    category, p1, p2 = _catalog(db)

    r = client.put(f"/admin/users/{riya.id}/products", json={"product_ids": [p1.id]}, headers=auth(admin))
    assert r.status_code == 200
    assert _unread(client, riya) == 1
    note = client.get("/notifications", headers=auth(riya)).json()["notifications"][0]
    assert note["kind"] == "product_assigned"
    assert note["message"] == f"Anuj Admin has authorised you to survey Honey Nut Granola ({category.name})."

    client.put(f"/admin/users/{riya.id}/products", json={"product_ids": [p1.id]}, headers=auth(admin))
    assert _unread(client, riya) == 1, "saving the same products again: no duplicate"
    client.put(f"/admin/users/{riya.id}/products", json={"product_ids": [p1.id, p2.id]}, headers=auth(admin))
    assert _unread(client, riya) == 2, "a second product: one more"

    # Catalog page: assign survey persons to a product's live round (admins can't be assigned)
    bad = client.put(f"/admin/products/{p1.id}/executives", json={"user_ids": [riya.id, amit.id, other_admin.id]}, headers=auth(admin))
    assert bad.status_code == 400 and "survey persons" in bad.json()["detail"]
    client.put(f"/admin/products/{p1.id}/executives", json={"user_ids": [riya.id, amit.id]}, headers=auth(admin))
    assert _unread(client, amit) == 1
    assert _unread(client, riya) == 2, "already had it: not notified again"
    assert _unread(client, other_admin) == 0, "admins are not notified about assignments"

    client.put(f"/admin/users/{riya.id}/products", json={"product_ids": [p2.id]}, headers=auth(admin))
    client.put(f"/admin/users/{riya.id}/products", json={"product_ids": [p1.id, p2.id]}, headers=auth(admin))
    assert _unread(client, riya) == 3, "removed then re-assigned: notified again"
    assert client.put(f"/admin/users/{riya.id}/products", json={"product_ids": [p1.id]}, headers=auth(riya)).status_code == 403


def _survey(db, product, by, minutes_ago):
    person = Respondent(name="Customer X", mobile="9999999999", location="Andheri, Mumbai", consent_given=1)
    db.add(person)
    db.commit()
    survey = SurveyResponse(
        product_id=product.id, respondent_id=person.id, executive_name=by.name, user_id=by.id,
        location="D-Mart Andheri", status="submitted",
        created_at=datetime.utcnow() - timedelta(minutes=minutes_ago + 5),
        submitted_at=datetime.utcnow() - timedelta(minutes=minutes_ago),
    )
    db.add(survey)
    db.commit()
    return survey


def _missing_for(client, admin, survey_ids):
    items = client.get("/notifications?limit=100", headers=auth(admin)).json()["notifications"]
    return [n for n in items if n["kind"] == "survey_missing_video" and n["data"].get("survey_id") in survey_ids]


def test_missing_video_notifications(client, db):
    admin = make_user(db, role="admin", name="Video Admin")
    riya = make_user(db, name="Riya Missing")
    amit = make_user(db, name="Amit Missing")
    _, product, _ = _catalog(db)

    old = _survey(db, product, riya, 15)          # past the 10-minute grace, no video -> notify
    fresh = _survey(db, product, riya, 3)         # still in grace -> not yet
    by_admin = _survey(db, product, admin, 30)    # an admin's own survey -> skip
    with_video = _survey(db, product, amit, 20)   # has a job -> skip
    db.add(AnalysisJob(status="queued", filename="a.mp4", stored_path="x", response_id=with_video.id))
    db.commit()
    ancient = _survey(db, product, amit, 60 * 24 * 5)  # outside the 48 h lookback
    mine = {old.id, fresh.id, by_admin.id, with_video.id, ancient.id}

    svc._last_check = 0
    notes = _missing_for(client, admin, mine)
    assert [n["data"]["survey_id"] for n in notes] == [old.id]
    note = notes[0]
    assert note["data"]["survey_person"]["mobile"] == riya.mobile
    assert "Customer X" not in str(note) and "9999999999" not in str(note), "customer details never in notifications"
    assert not any(n["kind"] == "survey_missing_video" for n in client.get("/notifications", headers=auth(riya)).json()["notifications"])

    fresh.submitted_at = datetime.utcnow() - timedelta(minutes=11)
    db.commit()
    svc._last_check = 0
    assert {n["data"]["survey_id"] for n in _missing_for(client, admin, mine)} == {old.id, fresh.id}
    svc._last_check = 0
    client.get("/notifications/unread-count", headers=auth(admin))
    assert db.query(Notification).filter_by(kind="survey_missing_video", user_id=admin.id).filter(
        Notification.dedupe_key.in_([f"missing_video:{old.id}", f"missing_video:{fresh.id}"])).count() == 2, "no duplicates"

    # the video arrives later -> resolved and no longer unread
    db.add(AnalysisJob(status="queued", filename="late.mp4", stored_path="y", response_id=old.id))
    db.commit()
    svc._last_check = 0
    resolved = [n for n in _missing_for(client, admin, mine) if n["data"]["survey_id"] == old.id][0]
    assert resolved["resolved"] and resolved["data"]["resolution"] == "video_received" and resolved["is_read"]


def test_notification_read_state_is_private(client, db):
    admin = make_user(db, role="admin")
    riya = make_user(db)
    _, product, _ = _catalog(db)
    client.put(f"/admin/users/{riya.id}/products", json={"product_ids": [product.id]}, headers=auth(admin))
    nid = client.get("/notifications", headers=auth(riya)).json()["notifications"][0]["id"]
    assert client.post(f"/notifications/{nid}/read", headers=auth(admin)).status_code == 404, "not someone else's"
    assert client.post(f"/notifications/{nid}/read", headers=auth(riya)).json()["unread_count"] == 0
    assert client.post("/notifications/read-all", headers=auth(riya)).json()["unread_count"] == 0
    assert client.get("/notifications/unread-count").status_code == 401
    assert client.get("/notifications?unread_only=true", headers=auth(riya)).json()["notifications"] == []