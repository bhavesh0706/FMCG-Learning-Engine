"""Every saved recipe version gets a recipe code RCP-0001, RCP-0002 ... (shown in the portal)."""

import pytest

import re

from conftest import _unique, auth, make_user

# Written for "every recipe change is a new version" (see conftest.legacy_recipe_versions).
pytestmark = pytest.mark.usefixtures("legacy_recipe_versions")

CODE = re.compile(r"^RCP-\d{4}$")


def _product(client, org):
    cid = client.post("/categories", headers=auth(org), json={"name": _unique("Snacks ")}).json()["id"]
    return client.post("/products", headers=auth(org), json={"category_id": cid, "name": _unique("Masala Peanuts ")}).json()["id"]


def _recipe(qty):
    return {"batch_size": "100 kg", "ingredients": [{"name": "Peanuts", "quantity": str(qty), "unit": "kg"},
                                                    {"name": "Salt", "quantity": str(100 - qty), "unit": "kg"}]}


def test_each_saved_recipe_version_gets_its_own_code(client, db):
    org = make_user(db, role="client")
    a, b = _product(client, org), _product(client, org)
    assert client.put(f"/products/{a}/recipe", headers=auth(org), json=_recipe(98)).status_code == 200   # a v1
    assert client.put(f"/products/{b}/recipe", headers=auth(org), json=_recipe(97)).status_code == 200   # b v1
    assert client.put(f"/products/{a}/recipe", headers=auth(org), json=_recipe(99)).status_code == 200   # a v2
    va = client.get(f"/products/{a}/recipe/versions", headers=auth(org)).json()["versions"]
    vb = client.get(f"/products/{b}/recipe/versions", headers=auth(org)).json()["versions"]
    codes = [v["recipe_code"] for v in va + vb]
    assert all(CODE.match(c) for c in codes), codes
    assert len(set(codes)) == 3, "one code per version, unique across products"
    newest_a = va[0]  # newest first
    assert int(newest_a["recipe_code"][4:]) > int(vb[0]["recipe_code"][4:]), "codes follow the order of saving"
    # the latest code everywhere the portal shows it
    assert client.get(f"/products/{a}/recipe", headers=auth(org)).json()["recipe_code"] == newest_a["recipe_code"]
    rows = {p["id"]: p for p in client.get("/org/overview", headers=auth(org)).json()["products"]}
    assert rows[a]["recipe"]["code"] == newest_a["recipe_code"] and rows[a]["recipe"]["version"] == 2


def test_a_product_without_a_recipe_has_no_code(client, db):
    org = make_user(db, role="client")
    pid = _product(client, org)
    assert client.get(f"/products/{pid}/recipe", headers=auth(org)).json()["recipe_code"] is None