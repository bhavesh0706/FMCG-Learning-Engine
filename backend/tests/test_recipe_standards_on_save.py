"""A recipe is saved only if it meets every recipe standard that applies to the product."""

from conftest import _unique, auth, make_user

from app.database.models import RecipeVersion

def _recipe(*items):
    return {"batch_size": "100 kg", "ingredients": [{"name": n, "quantity": str(q), "unit": "kg"} for n, q in items]}


def _setup(client, org):
    cid = client.post("/categories", headers=auth(org), json={"name": _unique("Snacks ")}).json()["id"]
    pid = client.post("/products", headers=auth(org), json={"category_id": cid, "name": _unique("Masala Peanuts ")}).json()["id"]
    return cid, pid


def _versions(db, pid):
    db.expire_all()
    return db.query(RecipeVersion).filter(RecipeVersion.product_id == pid).count()


def test_a_recipe_missing_a_required_ingredient_is_not_saved(client, db):
    org = make_user(db, role="client")
    _, pid = _setup(client, org)
    rule = client.post("/recipe-standards", headers=auth(org), json={"name": "Cardamom required", "kind": "required_ingredient",
                                                                     "ingredient": "Cardamom", "product_id": pid})
    assert rule.status_code in (200, 201), rule.text
    r = client.put(f"/products/{pid}/recipe", headers=auth(org), json=_recipe(("Peanuts", 98), ("Salt", 2)))
    assert r.status_code == 409, r.text
    d = r.json()["detail"]
    assert d["code"] == "recipe_standards_not_met" and [f["name"] for f in d["failed_standards"]] == ["Cardamom required"]
    assert "Recipe not saved" in d["message"]
    assert _versions(db, pid) == 0, "nothing stored, no version"
    assert client.get(f"/products/{pid}/recipe", headers=auth(org)).json()["standards_count"] >= 1, "known before the first recipe"
    assert client.get(f"/products/{pid}/recipe", headers=auth(org)).json()["recipe"] is None
    ok = client.put(f"/products/{pid}/recipe", headers=auth(org), json=_recipe(("Peanuts", 97), ("Salt", 2), ("Cardamom", 1)))
    assert ok.status_code == 200, ok.text
    assert _versions(db, pid) == 1


def test_an_ingredient_share_outside_the_range_is_not_saved(client, db):
    org = make_user(db, role="client")
    _, pid = _setup(client, org)
    client.post("/recipe-standards", headers=auth(org), json={"name": "Peanuts 60-80%", "kind": "ingredient_share", "ingredient": "Peanuts",
                                                             "min_value": 60, "max_value": 80, "product_id": pid})
    assert client.put(f"/products/{pid}/recipe", headers=auth(org), json=_recipe(("Peanuts", 50), ("Gram flour", 50))).status_code == 409
    assert client.put(f"/products/{pid}/recipe", headers=auth(org), json=_recipe(("Peanuts", 70), ("Gram flour", 30))).status_code == 200


def test_a_category_rule_applies_to_its_products_and_a_recipe_can_still_be_cleared(client, db):
    org = make_user(db, role="client")
    cid, pid = _setup(client, org)
    assert client.put(f"/products/{pid}/recipe", headers=auth(org), json=_recipe(("Peanuts", 100))).status_code == 200
    client.post("/recipe-standards", headers=auth(org), json={"name": "Salt required", "kind": "required_ingredient", "ingredient": "Salt",
                                                             "category_id": cid})
    r = client.put(f"/products/{pid}/recipe", headers=auth(org), json=_recipe(("Peanuts", 99), ("Gram flour", 1)))
    assert r.status_code == 409 and r.json()["detail"]["failed_standards"][0]["name"] == "Salt required"
    cleared = client.put(f"/products/{pid}/recipe", headers=auth(org), json={"batch_size": None, "ingredients": []})
    assert cleared.status_code == 200, "clearing a recipe stays possible"