"""
The round workflow (services/product_access.py), end to end through the API:

  Super Admin creates a product  -> invisible to admins and survey persons
  Super Admin starts Round 1     -> admins see it; survey persons still don't
  Admin assigns Round 1          -> only the assigned survey persons see and survey it
  Round 1 ends                   -> nobody can start new surveys; history stays
  Super Admin starts Round 2     -> assignments don't carry over: the admin assigns again
  Reports keep each round's feedback separate.

Also: disabled team members, the one-time assignment migration, and GPS -> address.
"""

import pytest
from conftest import _unique, answers_payload, auth, make_user

from app.database.models import ProductAssignment, SurveyRound, SurveyResponse

RECIPE = {"batch_size": "10 L", "ingredients": [{"name": "Lemon juice", "quantity": "1", "unit": "L"}],
          "steps": [{"title": "Mix"}, {"title": "Chill", "duration": "2 h"}]}
RECIPE_V2 = {**RECIPE, "ingredients": [{"name": "Lemon juice", "quantity": "0.8", "unit": "L"}]}


@pytest.fixture()
def org(db):
    return make_user(db, role="client", name=_unique("Acme "))


def _ids(client, user, path="/products"):
    r = client.get(path, headers=auth(user))
    assert r.status_code == 200, r.text
    return {p["id"] for p in r.json()["products"]}


def _new_product(client, org):
    cid = client.post("/categories", headers=auth(org), json={"name": _unique("Drinks ")}).json()["id"]
    pid = client.post("/products", headers=auth(org), json={"category_id": cid, "name": _unique("VitaBoost "), "min_responses": 1}).json()["id"]
    for i in range(5):
        assert client.post(f"/products/{pid}/questions", headers=auth(org),
                           json={"text": f"Question {i}?", "question_type": "text", "required": True}).status_code == 201
    assert client.put(f"/products/{pid}/recipe", headers=auth(org), json=RECIPE).status_code == 200
    return pid, cid


def _survey(client, db, person, pid):
    rid = client.post("/surveys/respondents", headers=auth(person),
                      json={"name": "Customer", "location": "Noida", "consent_given": True}).json()["id"]
    r = client.post("/surveys/responses", headers=auth(person), json={"product_id": pid, "respondent_id": rid})
    if r.status_code != 201:
        return r
    sid = r.json()["id"]

    class _P:
        id = pid
    assert client.put(f"/surveys/responses/{sid}/answers", headers=auth(person), json=answers_payload(db, _P)).status_code == 200
    assert client.post(f"/surveys/responses/{sid}/submit", headers=auth(person)).status_code == 200
    return r


def test_the_super_admin_admin_survey_person_workflow_round_by_round(client, db, org, admin):
    alice, bob = make_user(db, name="Alice Field"), make_user(db, name="Bob Field")
    pid, cid = _new_product(client, org)

    # 1. Created, no round: only the Super Admin sees it.
    assert pid in _ids(client, org)
    assert pid not in _ids(client, admin) and pid not in _ids(client, alice)
    assert pid not in {p["id"] for p in client.get("/product-feedback", headers=auth(admin)).json()["products"]}
    assert client.get(f"/product-feedback/{pid}", headers=auth(admin)).status_code == 404
    assert cid not in {c["id"] for c in client.get("/categories", headers=auth(alice)).json()["categories"]}
    assert _survey(client, db, alice, pid).status_code == 403
    assert db.query(SurveyRound).filter_by(product_id=pid).count() == 0, "no round is ever created by a survey"

    # 2. The Super Admin starts Round 1: admins see it, survey persons still don't.
    assert client.post(f"/org/products/{pid}/rounds", headers=auth(org), json={"target": 20}).status_code == 201
    assert pid in _ids(client, admin)
    assert pid in {p["id"] for p in client.get("/product-feedback", headers=auth(admin)).json()["products"]}
    who = client.get(f"/admin/products/{pid}/executives", headers=auth(admin)).json()
    assert who["round"]["number"] == 1 and who["user_ids"] == []
    assert pid not in _ids(client, alice) and _survey(client, db, alice, pid).status_code == 403

    # 3. The admin assigns Round 1 to alice: only alice sees and surveys it.
    assert client.put(f"/admin/products/{pid}/executives", headers=auth(admin), json={"user_ids": [alice.id]}).status_code == 200
    assert pid in _ids(client, alice) and pid not in _ids(client, bob)
    assert cid in {c["id"] for c in client.get("/categories", headers=auth(alice)).json()["categories"]}
    assert _survey(client, db, bob, pid).status_code == 403
    r1_survey = _survey(client, db, alice, pid)
    assert r1_survey.status_code == 201 and r1_survey.json()["round"] == 1

    # 4. Round 1 ends: nothing new starts by itself, and the product leaves alice's list.
    assert client.post(f"/org/products/{pid}/rounds/1/end", headers=auth(org)).status_code == 200
    assert db.query(SurveyRound).filter_by(product_id=pid).count() == 1
    assert pid not in _ids(client, alice) and pid in _ids(client, admin)
    assert client.get(f"/admin/products/{pid}/executives", headers=auth(admin)).json()["round"] is None
    assert client.put(f"/admin/products/{pid}/executives", headers=auth(admin), json={"user_ids": [bob.id]}).status_code == 409

    # 5. Round 2: started by the Super Admin, assigned again by the admin (to bob this time).
    assert client.put(f"/products/{pid}/recipe", headers=auth(org), json=RECIPE_V2).status_code == 200
    assert client.post(f"/org/products/{pid}/rounds", headers=auth(org), json={"target": 20}).status_code == 201
    who = client.get(f"/admin/products/{pid}/executives", headers=auth(admin)).json()
    assert who["round"]["number"] == 2 and who["user_ids"] == [], "Round 1's people don't carry over"
    assert pid not in _ids(client, alice) and pid not in _ids(client, bob)
    assert client.put(f"/admin/products/{pid}/executives", headers=auth(admin), json={"user_ids": [bob.id]}).status_code == 200
    assert pid in _ids(client, bob) and pid not in _ids(client, alice)
    r2_survey = _survey(client, db, bob, pid)
    assert r2_survey.status_code == 201 and r2_survey.json()["round"] == 2

    # 6. Each round keeps its own feedback; Round 1's assignment stays as history.
    r1 = client.get(f"/product-feedback/{pid}", headers=auth(admin), params={"round": 1}).json()
    r2 = client.get(f"/product-feedback/{pid}", headers=auth(admin), params={"round": 2}).json()
    assert [x["response_id"] for x in r1["responses"]] == [r1_survey.json()["id"]]
    assert [x["response_id"] for x in r2["responses"]] == [r2_survey.json()["id"]]
    rounds = {r.number: r.id for r in db.query(SurveyRound).filter_by(product_id=pid)}
    assert {(a.round_id, a.user_id) for a in db.query(ProductAssignment).filter_by(product_id=pid)} == {(rounds[1], alice.id), (rounds[2], bob.id)}


def test_the_team_page_assigns_live_rounds_by_person(client, db, org, admin):
    alice = make_user(db)
    pid, _ = _new_product(client, org)
    assert client.put(f"/admin/users/{alice.id}/products", headers=auth(admin), json={"product_ids": [pid]}).status_code == 409
    client.post(f"/org/products/{pid}/rounds", headers=auth(org), json={"target": 5})
    assert client.put(f"/admin/users/{alice.id}/products", headers=auth(admin), json={"product_ids": [pid]}).status_code == 200
    assert client.get(f"/admin/users/{alice.id}/products", headers=auth(admin)).json()["product_ids"] == [pid]
    assert pid in _ids(client, alice)


def test_disabled_members_cant_sign_in_or_be_assigned_and_keep_their_history(client, db, org, admin):
    alice = make_user(db, name="Alice Field")
    pid, _ = _new_product(client, org)
    client.post(f"/org/products/{pid}/rounds", headers=auth(org), json={"target": 5})
    client.put(f"/admin/products/{pid}/executives", headers=auth(admin), json={"user_ids": [alice.id]})
    survey_id = _survey(client, db, alice, pid).json()["id"]
    token = auth(alice)

    off = client.put(f"/admin/users/{alice.id}", headers=auth(admin), json={"is_active": False})
    assert off.status_code == 200 and off.json()["user"]["is_active"] is False
    assert client.post("/auth/login", json={"identifier": alice.mobile, "password": "Passw0rd!"}).status_code == 403
    assert client.get("/products", headers=token).status_code == 401, "signed out everywhere"
    # Not assignable while disabled; history kept.
    bad = client.put(f"/admin/products/{pid}/executives", headers=auth(admin), json={"user_ids": [alice.id]})
    assert bad.status_code == 400 and "Reactivate" in bad.json()["detail"]
    assert db.get(SurveyResponse, survey_id) is not None
    member = next(u for u in client.get("/admin/users", headers=auth(admin)).json()["users"] if u["id"] == alice.id)
    assert member["is_active"] is False and member["stats"]["total"] == 1

    on = client.put(f"/admin/users/{alice.id}", headers=auth(admin), json={"is_active": True})
    assert on.status_code == 200 and on.json()["user"]["is_active"] is True
    assert client.post("/auth/login", json={"identifier": alice.mobile, "password": "Passw0rd!"}).status_code == 200


def test_the_one_time_migration_keeps_fieldwork_in_progress(db):
    from app.database.migrations import scope_assignments_to_rounds
    from app.database.models import Category, Product

    cat = Category(name=_unique("Old "), is_active=1)
    db.add(cat)
    db.commit()
    open_p = Product(category_id=cat.id, name="Open product", is_active=1)
    kept_p = Product(category_id=cat.id, name="Assigned product", is_active=1)
    db.add_all([open_p, kept_p])
    db.commit()
    r_open = SurveyRound(product_id=open_p.id, number=1, target=10, status="live")
    r_kept = SurveyRound(product_id=kept_p.id, number=1, target=10, status="live")
    db.add_all([r_open, r_kept])
    alice, bob = make_user(db), make_user(db)
    gone = make_user(db)
    gone.is_active = 0
    db.add(ProductAssignment(user_id=bob.id, product_id=kept_p.id, round_id=None))  # old per-product row
    db.commit()

    scope_assignments_to_rounds()
    db.expire_all()
    open_people = {a.user_id for a in db.query(ProductAssignment).filter_by(round_id=r_open.id)}
    assert {alice.id, bob.id} <= open_people and gone.id not in open_people, "open to everyone -> every active survey person"
    assert {a.user_id for a in db.query(ProductAssignment).filter_by(round_id=r_kept.id)} == {bob.id}
    assert db.query(ProductAssignment).filter(ProductAssignment.round_id.is_(None)).count() == 0


SAMPLE = {"address": {"suburb": "Sector 62", "city": "Noida", "state": "Uttar Pradesh", "country": "India"},
          "display_name": "Sector 62, Noida, Gautam Buddha Nagar, Uttar Pradesh, India"}


def test_gps_position_becomes_a_readable_survey_location(client, db, admin, monkeypatch):
    from app.api import geo

    geo._CACHE.clear()
    alice = make_user(db)
    calls = []
    monkeypatch.setattr(geo, "_lookup", lambda lat, lng: calls.append((lat, lng)) or SAMPLE)
    r = client.get("/geo/reverse", headers=auth(alice), params={"lat": 28.601075, "lng": 77.432698})
    assert r.status_code == 200, r.text
    assert r.json()["label"] == "Sector 62, Noida, Uttar Pradesh" and r.json()["city"] == "Noida"
    client.get("/geo/reverse", headers=auth(admin), params={"lat": 28.6011, "lng": 77.4327})
    assert len(calls) == 1, "a nearby position is answered from the cache"

    def broken(lat, lng):
        raise OSError("network down")
    monkeypatch.setattr(geo, "_lookup", broken)
    down = client.get("/geo/reverse", headers=auth(alice), params={"lat": 19.07, "lng": 72.87})
    assert down.status_code == 503 and "Type the location" in down.json()["detail"]
    monkeypatch.setenv("FMCG_GEOCODER", "off")
    assert client.get("/geo/reverse", headers=auth(alice), params={"lat": 12.97, "lng": 77.59}).status_code == 503
    assert client.get("/geo/reverse", headers=auth(alice), params={"lat": 123, "lng": 77}).status_code == 422
    assert client.get("/geo/reverse", params={"lat": 28.6, "lng": 77.4}).status_code == 401


def test_the_super_admin_portal_does_not_use_gps_lookup(client, db, org):
    assert client.get("/geo/reverse", headers=auth(org), params={"lat": 28.6, "lng": 77.4}).status_code == 403


def test_a_survey_saves_location_text_and_coordinates(client, db, org, admin):
    alice = make_user(db)
    pid, _ = _new_product(client, org)
    client.post(f"/org/products/{pid}/rounds", headers=auth(org), json={"target": 5})
    client.put(f"/admin/products/{pid}/executives", headers=auth(admin), json={"user_ids": [alice.id]})
    rid = client.post("/surveys/respondents", headers=auth(alice), json={"name": "Customer Test", "location": "Noida", "consent_given": True}).json()["id"]
    r = client.post("/surveys/responses", headers=auth(alice), json={
        "product_id": pid, "respondent_id": rid, "location": "Sector 62, Noida, Uttar Pradesh",
        "latitude": 28.601075, "longitude": 77.432698})
    assert r.status_code == 201, r.text
    row = db.get(SurveyResponse, r.json()["id"])
    assert row.location == "Sector 62, Noida, Uttar Pradesh" and abs(row.latitude - 28.601075) < 1e-6 and abs(row.longitude - 77.432698) < 1e-6


def _existing_product(client, db, org, admin):
    """A product surveyed before rounds were required: all its rounds are complete,
    and the migration marked it open (needs_round = 0)."""
    from app.database.models import Product

    pid, _ = _new_product(client, org)
    alice = make_user(db, name="Earlier Field")
    client.post(f"/org/products/{pid}/rounds", headers=auth(org), json={"target": 1})
    client.put(f"/admin/products/{pid}/executives", headers=auth(admin), json={"user_ids": [alice.id]})
    _survey(client, db, alice, pid)  # target reached: Round 1 closes by itself
    db.query(ProductAssignment).filter_by(product_id=pid).delete()  # as before: open to everyone
    product = db.get(Product, pid)
    product.needs_round = 0
    db.commit()
    assert db.query(SurveyRound).filter_by(product_id=pid, status="live").count() == 0
    return pid


def test_existing_products_stay_open_to_every_survey_person_without_a_round(client, db, org, admin):
    pid = _existing_product(client, db, org, admin)
    riya, amit = make_user(db, name="Riya Field"), make_user(db, name="Amit Field")
    assert pid in _ids(client, riya) and pid in _ids(client, amit) and pid in _ids(client, admin)
    made = _survey(client, db, riya, pid)
    assert made.status_code == 201 and made.json()["round"] == 1, "goes into its latest round, no new round needed"
    who = client.get(f"/admin/products/{pid}/executives", headers=auth(admin)).json()
    assert who["open_to_all"] is True and who["needs_round"] is False and who["round"]["number"] == 1
    member = next(u for u in client.get("/admin/users", headers=auth(admin)).json()["users"] if u["id"] == amit.id)
    assert pid in member["assigned_products"], "the Team page shows it as surveyable"


def test_an_existing_product_can_be_restricted_and_opened_again(client, db, org, admin):
    pid = _existing_product(client, db, org, admin)
    riya, amit = make_user(db), make_user(db)
    assert client.put(f"/admin/products/{pid}/executives", headers=auth(admin), json={"user_ids": [riya.id]}).status_code == 200
    assert pid in _ids(client, riya) and pid not in _ids(client, amit)
    assert _survey(client, db, amit, pid).status_code == 403
    assert client.put(f"/admin/products/{pid}/executives", headers=auth(admin), json={"user_ids": []}).status_code == 200
    assert pid in _ids(client, amit), "nobody assigned: open to everyone again"


def test_a_new_recipe_version_needs_the_super_admin_to_start_its_round(client, db, org, admin):
    pid = _existing_product(client, db, org, admin)
    riya = make_user(db)
    assert pid in _ids(client, riya)
    assert client.put(f"/products/{pid}/recipe", headers=auth(org), json=RECIPE_V2).status_code == 200
    assert pid not in _ids(client, riya) and _survey(client, db, riya, pid).status_code == 403
    assert client.post(f"/org/products/{pid}/rounds", headers=auth(org), json={"target": 5}).status_code == 201
    assert pid not in _ids(client, riya), "the new version's round is assigned by an admin"
    client.put(f"/admin/products/{pid}/executives", headers=auth(admin), json={"user_ids": [riya.id]})
    made = _survey(client, db, riya, pid)
    assert made.status_code == 201 and made.json()["round"] == 2


def test_the_migration_keeps_already_surveyed_products_open(client, db, org, admin):
    from sqlalchemy import text

    from app.database.database import engine
    from app.database.models import Product

    surveyed = _existing_product(client, db, org, admin)
    fresh, _ = _new_product(client, org)
    for pid in (surveyed, fresh):
        db.get(Product, pid).needs_round = 1
    db.commit()
    with engine.begin() as conn:  # the step run_migrations does once, when the column is added
        conn.execute(text("UPDATE products SET needs_round = 0 WHERE id IN (SELECT DISTINCT product_id FROM survey_responses)"))
    db.expire_all()
    assert db.get(Product, surveyed).needs_round == 0 and db.get(Product, fresh).needs_round == 1