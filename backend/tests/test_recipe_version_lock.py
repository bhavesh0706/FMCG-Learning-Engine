"""A recipe version is corrected in place until its first consumer response; then it is locked and
only "Add new recipe version" changes the recipe (a new, separate version)."""

from datetime import datetime

from conftest import _unique, auth, make_user

from app.database.models import RecipeVersion, Respondent, SurveyResponse

RECIPE = {"batch_size": "100 kg", "ingredients": [{"name": "Peanuts", "quantity": "90", "unit": "kg", "cost_per_kg": "120"},
                                                  {"name": "Salt", "quantity": "10", "unit": "kg", "cost_per_kg": "20"}]}


def _product(client, org):
    cid = client.post("/categories", headers=auth(org), json={"name": _unique("Snacks ")}).json()["id"]
    return client.post("/products", headers=auth(org), json={"category_id": cid, "name": _unique("Masala Peanuts ")}).json()["id"]


def _versions(db, pid):
    db.expire_all()
    return [(v.version, v.recipe_code) for v in db.query(RecipeVersion).filter(RecipeVersion.product_id == pid).order_by(RecipeVersion.version)]


def _respond(db, pid, version, status="submitted"):
    who = Respondent(name="Priya", consent_given=1, created_at=datetime.utcnow())
    db.add(who)
    db.flush()
    db.add(SurveyResponse(product_id=pid, respondent_id=who.id, recipe_version=version, status=status, created_at=datetime.utcnow()))
    db.commit()


def test_corrections_before_the_first_response_update_the_same_version(client, db):
    org = make_user(db, role="client")
    pid = _product(client, org)
    assert client.put(f"/products/{pid}/recipe", headers=auth(org), json=RECIPE).status_code == 200
    first = _versions(db, pid)
    assert len(first) == 1
    # quantity, batch size, process: all corrections of v1 (same number, same recipe code)
    fixed = {**RECIPE, "batch_size": "100 kg = 200 packs", "process": "Roast, coat, pack",
             "ingredients": [{"name": "Peanuts", "quantity": "92", "unit": "kg", "cost_per_kg": "120"}, {"name": "Salt", "quantity": "8", "unit": "kg", "cost_per_kg": "20"}]}
    r = client.put(f"/products/{pid}/recipe", headers=auth(org), json=fixed)
    assert r.status_code == 200 and r.json()["version_updated_in_place"] is True
    assert _versions(db, pid) == first, "no new version before the first consumer response"
    # an unfinished (draft) survey doesn't lock it either
    _respond(db, pid, 1, status="draft")
    assert client.put(f"/products/{pid}/recipe", headers=auth(org), json=RECIPE).status_code == 200
    assert _versions(db, pid) == first
    assert client.get(f"/products/{pid}/recipe", headers=auth(org)).json()["version_locked"] is False
    # Add new recipe version is refused while it isn't locked
    r = client.put(f"/products/{pid}/recipe", headers=auth(org), json={**RECIPE, "new_version": True})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "version_not_locked"


def test_after_a_response_the_version_is_locked_and_a_new_version_keeps_the_old_one(client, db):
    org = make_user(db, role="client")
    pid = _product(client, org)
    client.put(f"/products/{pid}/recipe", headers=auth(org), json=RECIPE)
    _respond(db, pid, 1)  # one submitted consumer response
    assert client.get(f"/products/{pid}/recipe", headers=auth(org)).json()["version_locked"] is True
    r = client.put(f"/products/{pid}/recipe", headers=auth(org), json={**RECIPE, "notes": "less salt"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "recipe_version_locked", "v1 can't be edited any more"
    v2 = {**RECIPE, "ingredients": [{"name": "Peanuts", "quantity": "93", "unit": "kg", "cost_per_kg": "120"},
                                    {"name": "Salt", "quantity": "7", "unit": "kg", "cost_per_kg": "20"}], "new_version": True}
    r = client.put(f"/products/{pid}/recipe", headers=auth(org), json=v2)
    assert r.status_code == 200 and r.json()["version"] == 2 and r.json()["version_updated_in_place"] is False
    vs = _versions(db, pid)
    assert [v for v, _ in vs] == [1, 2] and vs[0][1] != vs[1][1], "each version keeps its own number and code"
    db.expire_all()
    old = db.query(RecipeVersion).filter(RecipeVersion.product_id == pid, RecipeVersion.version == 1).one()
    assert old.recipe["ingredients"][1]["quantity"] == "10", "the tested version keeps its own data"
    # v2 has no responses yet: corrected in place again
    assert client.put(f"/products/{pid}/recipe", headers=auth(org), json={**RECIPE, "notes": "v2 fix"}).status_code == 200
    assert [v for v, _ in _versions(db, pid)] == [1, 2]