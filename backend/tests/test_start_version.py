"""A product's first recipe version number, chosen when the product is added."""

from conftest import _unique, auth, make_user

RECIPE = {"batch_size": "10 kg", "ingredients": [{"name": "Milk", "quantity": "8", "unit": "kg"}, {"name": "Sugar", "quantity": "2", "unit": "kg"}]}


def _product(client, org, **extra):
    cid = client.post("/categories", headers=auth(org), json={"name": _unique("Dairy ")}).json()["id"]
    r = client.post("/products", headers=auth(org), json={"category_id": cid, "name": _unique("Lassi "), **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _versions(client, org, pid):
    return sorted(v["version"] for v in client.get(f"/org/products/{pid}", headers=auth(org)).json()["versions"])


def test_a_product_can_start_at_a_later_version(client, db):
    org = make_user(db, role="client")
    pid = _product(client, org, start_version=3)
    client.put(f"/products/{pid}/recipe", headers=auth(org), json=RECIPE)
    assert _versions(client, org, pid) == [3]
    changed = {**RECIPE, "batch_size": "10 kg batch = 100 cups"}
    client.put(f"/products/{pid}/recipe", headers=auth(org), json=changed)
    assert _versions(client, org, pid) == [3, 4], "later saves continue from the starting version"
    assert client.post(f"/org/products/{pid}/rounds", headers=auth(org), json={"target": 5}).json()["round"]["recipe_version"] == 4


def test_the_default_is_version_1_and_bad_numbers_are_refused(client, db):
    org = make_user(db, role="client")
    pid = _product(client, org)
    client.put(f"/products/{pid}/recipe", headers=auth(org), json=RECIPE)
    assert _versions(client, org, pid) == [1]
    cid = client.post("/categories", headers=auth(org), json={"name": _unique("Snacks ")}).json()["id"]
    for bad in (0, -2, 1000):
        assert client.post("/products", headers=auth(org), json={"category_id": cid, "name": _unique("X "), "start_version": bad}).status_code == 422
