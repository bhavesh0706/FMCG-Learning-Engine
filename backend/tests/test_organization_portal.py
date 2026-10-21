"""
Organization portal: the organization's login (made on the server, never on the
admin dashboard), its forced first password change, survey rounds, round
reports, comparing rounds, and the admin dashboard updating by itself.
"""

import re
from datetime import datetime

import pytest
from conftest import _unique, answers_payload, auth, make_user, start_survey

from app.database.models import (
    Category,
    ConsolidatedReport,
    FeedbackRecord,
    Product,
    SurveyResponse,
    SurveyRound,
    User,
)

RECIPE = {
    "batch_size": "10 kg",
    "ingredients": [{"name": "Oats", "quantity": "6", "unit": "kg"}, {"name": "Sugar", "quantity": "0.8", "unit": "kg"}],
    "steps": [{"title": "Mix"}, {"title": "Bake", "duration": "25 min"}],
}
LESS_SUGAR = {**RECIPE, "ingredients": [{"name": "Oats", "quantity": "6", "unit": "kg"}, {"name": "Sugar", "quantity": "0.65", "unit": "kg"}]}


@pytest.fixture()
def org(db):
    return make_user(db, role="client", name=_unique("Acme Foods "))


def _org_via_command(capsys, db):
    from app.tools.organization import main

    email = f"{_unique('insights')}@acmefoods.com"
    main(["create", "--name", "Acme Foods Pvt Ltd", "--email", email])
    out = capsys.readouterr().out
    password = re.search(r"Password\s*:\s*(\S+)", out).group(1)
    db.expire_all()
    return db.query(User).filter_by(email=email).one(), email, password


def _submit(client, db, person, product):
    ids = start_survey(client, person, product)
    rid = ids["response_id"]
    assert client.put(f"/surveys/responses/{rid}/answers", headers=auth(person), json=answers_payload(db, product)).status_code == 200
    r = client.post(f"/surveys/responses/{rid}/submit", headers=auth(person))
    assert r.status_code == 200, r.text
    return rid


def _analyze(db, rid, rating=4.0, sentiment="positive"):
    survey = db.get(SurveyResponse, rid)
    survey.status = "analyzed"
    db.add(FeedbackRecord(original_filename="r.m4a", video_path="x", duration_seconds=60, uploaded_at=datetime.utcnow(),
                          overall_summary="Liked it", overall_sentiment=sentiment, overall_rating=rating,
                          transcript="good taste", chapters=[], response_id=rid))
    db.commit()


def _rounds(db, product):
    db.expire_all()
    return db.query(SurveyRound).filter_by(product_id=product.id).order_by(SurveyRound.number).all()


# ------------------------------------------------------------
# The organization's login
# ------------------------------------------------------------

def test_server_command_creates_the_login_and_first_sign_in_forces_a_new_password(client, db, capsys):
    user, email, password = _org_via_command(capsys, db)
    assert user.role == "client" and user.must_change_password == 1 and user.mobile is None

    r = client.post("/auth/login", json={"identifier": email, "password": password})
    assert r.status_code == 200, r.text
    token, body = r.json()["token"], r.json()["user"]
    assert body["must_change_password"] is True
    h = {"Authorization": f"Bearer {token}"}
    blocked = client.get("/org/overview", headers=h)
    assert blocked.status_code == 403 and blocked.json()["code"] == "password_change_required"
    assert client.get("/auth/me", headers=h).status_code == 200

    weak = client.post("/auth/change-password", headers=h, json={"current_password": password, "new_password": "short"})
    assert weak.status_code == 422
    same = client.post("/auth/change-password", headers=h, json={"current_password": password, "new_password": password})
    assert same.status_code == 400
    r = client.post("/auth/change-password", headers=h, json={"current_password": password, "new_password": "AcmeInsights2026"})
    assert r.status_code == 200, r.text
    new_token = r.json()["token"]
    assert r.json()["user"]["must_change_password"] is False
    assert client.get("/auth/me", headers=h).status_code == 401  # the starting password's sign-in ended
    assert client.get("/org/overview", headers={"Authorization": f"Bearer {new_token}"}).status_code == 200
    assert client.post("/auth/login", json={"identifier": email, "password": password}).status_code == 401


def test_reset_password_command_signs_out_and_forces_a_change_again(client, db, capsys):
    from app.tools.organization import main

    user, email, password = _org_via_command(capsys, db)
    old = auth(user)
    main(["reset-password", "--email", email])
    new_password = re.search(r"Password\s*:\s*(\S+)", capsys.readouterr().out).group(1)
    assert new_password != password
    assert client.get("/auth/me", headers=old).status_code == 401
    r = client.post("/auth/login", json={"identifier": email, "password": new_password})
    assert r.json()["user"]["must_change_password"] is True


def test_admin_dashboard_has_no_client_options(client, db, admin, org):
    listed = [u["id"] for u in client.get("/admin/users", headers=auth(admin)).json()["users"]]
    assert org.id not in listed
    r = client.post("/admin/users", headers=auth(admin), json={"name": "Brand", "mobile": "8123456780", "password": "Passw0rd!", "role": "client"})
    assert r.status_code == 400
    for method, path in (("put", f"/admin/users/{org.id}/password"), ("post", f"/admin/users/{org.id}/sign-out"),
                         ("put", f"/admin/users/{org.id}")):
        kwargs = {"json": {"password": "Passw0rd!9"}} if path.endswith("password") else ({"json": {"name": "X Y"}} if method == "put" else {})
        assert getattr(client, method)(path, headers=auth(admin), **kwargs).status_code == 404
    for gone in ("/admin/users/1/client-products", "/admin/products/1/clients"):
        assert client.get(gone, headers=auth(admin)).status_code in (404, 405)


def test_organization_sees_every_active_product_and_nothing_else(client, db, org, alice, product):
    cat = Category(name=_unique("Cat "), is_active=1)
    db.add(cat)
    db.commit()
    hidden = Product(category_id=cat.id, name=_unique("Old "), is_active=0)
    db.add(hidden)
    db.commit()
    ids = [p["id"] for p in client.get("/org/products", headers=auth(org)).json()["products"]]
    assert product.id in ids and hidden.id not in ids
    assert client.get(f"/org/products/{hidden.id}", headers=auth(org)).status_code == 404
    for path in ("/admin/users", f"/product-feedback/{product.id}", "/product-feedback/surveys", "/feedback", "/notifications"):
        assert client.get(path, headers=auth(org)).status_code == 403, path
    assert client.get("/org/overview", headers=auth(alice)).status_code == 403  # survey persons never


# ------------------------------------------------------------
# Rounds
# ------------------------------------------------------------

def test_first_survey_opens_round_1_and_the_target_closes_it(client, db, alice, product):
    rid = _submit(client, db, alice, product)
    rounds = _rounds(db, product)
    assert len(rounds) == 1 and rounds[0].number == 1 and rounds[0].status == "live" and rounds[0].started_by_role == "system"
    assert db.get(SurveyResponse, rid).round_id == rounds[0].id
    rounds[0].target = 2
    db.commit()
    _submit(client, db, alice, product)
    r1 = _rounds(db, product)[0]
    assert r1.status == "closed" and r1.closed_reason == "target"
    # No live round: later surveys still belong to the latest round.
    rid3 = _submit(client, db, alice, product)
    assert db.get(SurveyResponse, rid3).round_id == r1.id


def test_organization_starts_a_round_and_everyone_is_told(client, db, admin, org, alice, product):
    first = client.post(f"/org/products/{product.id}/rounds", headers=auth(org), json={"target": 10})
    assert first.status_code == 400 and "recipe" in first.json()["detail"].lower()

    assert client.put(f"/products/{product.id}/recipe", headers=auth(org), json=RECIPE).status_code == 200
    r = client.post(f"/org/products/{product.id}/rounds", headers=auth(org),
                    json={"target": 12, "due_date": "2099-01-31", "note": "Less sugar"})
    assert r.status_code == 201, r.text
    rnd = r.json()["round"]
    assert rnd["number"] == 1 and rnd["status"] == "live" and rnd["recipe_version"] == 1 and rnd["target"] == 12
    assert client.post(f"/org/products/{product.id}/rounds", headers=auth(org), json={}).status_code == 409

    bell = client.get("/notifications", headers=auth(admin)).json()["notifications"]
    note = next(n for n in bell if n["kind"] == "client_round_started" and n["data"]["product_id"] == product.id)
    assert note["data"]["round"] == 1 and note["data"]["note"] == "Less sugar" and org.name in note["title"]
    field = client.get("/notifications", headers=auth(alice)).json()["notifications"]
    assert any(n["kind"] == "round_started" and n["data"]["product_id"] == product.id for n in field)

    rid = _submit(client, db, alice, product)
    assert db.get(SurveyResponse, rid).round_id == rnd["id"]
    # The admin catalog and history show the live round.
    listed = {p["id"]: p for p in client.get("/products", headers=auth(admin)).json()["products"]}
    assert listed[product.id]["live_round"]["number"] == 1 and listed[product.id]["live_round"]["done"] == 1
    admin_rounds = client.get(f"/products/{product.id}/rounds", headers=auth(admin)).json()["rounds"]
    assert admin_rounds[0]["started_by_name"] == org.name

    ended = client.post(f"/org/products/{product.id}/rounds/1/end", headers=auth(org))
    assert ended.status_code == 200 and ended.json()["round"]["status"] == "closed"
    assert any(n["kind"] == "client_round_ended" for n in client.get("/notifications", headers=auth(admin)).json()["notifications"])
    assert client.post(f"/org/products/{product.id}/rounds/1/end", headers=auth(org)).status_code == 400


def test_round_reports_compare_and_admin_round_view(client, db, admin, org, alice, product):
    from app.api.product_feedback import build_round_report, stale_round_ids

    client.put(f"/products/{product.id}/recipe", headers=auth(admin), json=RECIPE)
    client.post(f"/org/products/{product.id}/rounds", headers=auth(org), json={"target": 1})
    a = _submit(client, db, alice, product)
    _analyze(db, a, rating=3.5, sentiment="mixed")
    client.put(f"/products/{product.id}/recipe", headers=auth(org), json=LESS_SUGAR)
    r = client.post(f"/org/products/{product.id}/rounds", headers=auth(org), json={"target": 1, "note": "Less sugar"})
    assert r.status_code == 201, r.text
    assert r.json()["round"]["recipe_version"] == 2
    b = _submit(client, db, alice, product)
    _analyze(db, b, rating=4.5)

    rounds = _rounds(db, product)
    assert set(stale_round_ids(db, [product.id])) == {x.id for x in rounds}
    for x in rounds:
        assert build_round_report(db, x.id) is not None
    assert stale_round_ids(db, [product.id]) == []

    rep = client.get(f"/org/products/{product.id}/report?round=1", headers=auth(org)).json()
    assert rep["round"]["number"] == 1 and rep["report"]["overall_rating"] == 3.5 and "response_ids" not in rep["report"]
    assert "Customer Test" not in str(rep) and "9876543210" not in str(rep)  # no customer details
    latest = client.get(f"/org/products/{product.id}/report", headers=auth(org)).json()
    assert latest["round"]["number"] == 2 and latest["report"]["overall_rating"] == 4.5

    cmp = client.get(f"/org/products/{product.id}/compare", headers=auth(org)).json()
    assert cmp["enough_rounds"] and cmp["a"]["number"] == 1 and cmp["b"]["number"] == 2
    assert cmp["a"]["numbers"]["rating"] == 3.5 and cmp["b"]["numbers"]["rating"] == 4.5
    assert cmp["recipe_changes"] == ["Sugar: 0.8 kg → 0.65 kg"] and cmp["headline"].startswith("Rating up from 3.5 to 4.5")

    detail = client.get(f"/org/products/{product.id}", headers=auth(org)).json()
    assert [v["version"] for v in detail["versions"]] == [2, 1]
    assert detail["versions"][1]["changed_by_name"] == "Research team"  # admins' names are hidden
    assert detail["versions"][0]["rounds"] == [{"number": 2, "status": "closed"}]

    view = client.get(f"/product-feedback/{product.id}?round=2", headers=auth(admin)).json()
    assert [x["response_id"] for x in view["responses"]] == [b] and view["round"] == 2
    assert view["report"]["overall_rating"] == 4.5 and len(view["rounds"]) == 2
    everything = client.get(f"/product-feedback/{product.id}", headers=auth(admin)).json()
    assert {x["response_id"] for x in everything["responses"]} == {a, b} and everything["round"] is None


def test_updates_feed_and_unread_count(client, db, admin, org, product):
    client.put(f"/products/{product.id}/recipe", headers=auth(admin), json=RECIPE)
    feed = client.get("/org/updates", headers=auth(org)).json()
    mine = [u for u in feed["updates"] if u["product_id"] == product.id]
    assert mine[0]["title"].startswith("Research team updated the recipe") and mine[0]["unread"]
    assert client.get("/org/updates/unread", headers=auth(org)).json()["unread"] >= 1
    client.post("/org/updates/seen", headers=auth(org))
    assert client.get("/org/updates/unread", headers=auth(org)).json()["unread"] == 0
    client.put(f"/products/{product.id}/recipe", headers=auth(org), json=LESS_SUGAR)
    feed = client.get("/org/updates", headers=auth(org)).json()
    own = next(u for u in feed["updates"] if u["product_id"] == product.id)
    assert own["title"].startswith("You saved recipe v2") and not own["unread"]
    overview = client.get("/org/overview", headers=auth(org)).json()
    assert overview["numbers"]["products"] >= 1 and len(overview["updates"]) <= 4


def test_older_surveys_get_round_1(client, db, alice, product):
    from app.services.rounds import create_first_rounds

    rid = _submit(client, db, alice, product)
    db.query(SurveyRound).filter_by(product_id=product.id).delete()
    survey = db.get(SurveyResponse, rid)
    survey.round_id = None
    db.commit()
    assert create_first_rounds(db) >= 1
    rounds = _rounds(db, product)
    assert len(rounds) == 1 and rounds[0].status == "closed" and rounds[0].target == 1
    db.expire_all()
    assert db.get(SurveyResponse, rid).round_id == rounds[0].id
    assert create_first_rounds(db) == 0  # once only


def test_round_report_reuses_the_product_report_when_surveys_match(client, db, alice, product, monkeypatch):
    from app.api import product_feedback as pf

    rid = _submit(client, db, alice, product)
    _analyze(db, rid)
    db.add(ConsolidatedReport(product_id=product.id, version=1, response_count=1, response_ids=[rid], overall_rating=4.0,
                              stats={"response_count": 1, "overall_rating": 4.0}, narrative={"conclusion": "Copied"}, filters={}))
    db.commit()
    monkeypatch.setattr(pf, "_compose", lambda *a, **k: pytest.fail("the AI must not run again"))
    report = pf.build_round_report(db, _rounds(db, product)[0].id)
    assert report.narrative == {"conclusion": "Copied"} and report.round_id is not None
    # The product's own report is still the one the admin dashboard shows.
    assert pf._latest_report(db, product.id).round_id is None


def test_survey_persons_see_the_recipe_version_being_tested(client, db, admin, org, alice, product):
    client.put(f"/products/{product.id}/recipe", headers=auth(admin), json=RECIPE)
    client.post(f"/org/products/{product.id}/rounds", headers=auth(org), json={"target": 5})
    client.post(f"/org/products/{product.id}/rounds/1/end", headers=auth(org))
    client.put(f"/products/{product.id}/recipe", headers=auth(org), json=LESS_SUGAR)
    client.post(f"/org/products/{product.id}/rounds", headers=auth(org), json={"target": 5, "note": "Less sweet version"})

    listed = {p["id"]: p for p in client.get("/products", headers=auth(alice)).json()["products"]}
    live = listed[product.id]["live_round"]
    assert live["number"] == 2 and live["recipe_version"] == 2 and live["new_version"] is True
    assert live["note"] == "Less sweet version"
    note = next(n for n in client.get("/notifications", headers=auth(alice)).json()["notifications"]
                if n["kind"] == "round_started" and n["data"]["round"] == 2)
    assert "recipe v2" in note["title"] and "recipe v2" in note["message"]

    ids = start_survey(client, alice, product)
    one = client.get(f"/surveys/responses/{ids['response_id']}", headers=auth(alice)).json()
    assert one["round"] == 2 and one["recipe_version"] == 2
    mine = client.get("/auth/me/surveys", headers=auth(alice)).json()["surveys"]
    row = next(s for s in mine if s["response_id"] == ids["response_id"])
    assert row["round"] == 2 and row["recipe_version"] == 2
    detail = client.get(f"/auth/me/surveys/{ids['response_id']}", headers=auth(alice)).json()
    assert detail["round"] == 2 and detail["recipe_version"] == 2
