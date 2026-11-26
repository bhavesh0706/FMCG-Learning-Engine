"""Other Master Data: master tables with Active / Can Edit / Add Value enforced by the API,
and the measurement-unit master (Weight/Mass, Volume) for recipe ingredients."""
from conftest import _unique, auth, make_user

from app.services.lov import seed_lov

URL = "/admin/other-masters"


def _create(client, admin, **flags):
    body = {"name": _unique("Packaging type "), "description": "How the product is packed", **flags}
    r = client.post(URL, headers=auth(admin), json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_create_a_table_then_add_and_edit_values(client, admin):
    t = _create(client, admin)
    assert t["id"] and t["code"].startswith("PACKAGING_TYPE") and t["is_active"] and t["can_edit"] and t["allow_add"]
    assert any(x["id"] == t["id"] for x in client.get(URL, headers=auth(admin)).json()["tables"])
    v = client.post(f"{URL}/{t['id']}/values", headers=auth(admin), json={"code": "POUCH", "description": "Pouch"})
    assert v.status_code == 201 and v.json()["code"] == "POUCH"
    assert client.post(f"{URL}/{t['id']}/values", headers=auth(admin), json={"code": "pouch"}).status_code == 409, "no duplicates"
    ch = client.patch(f"{URL}/values/{v.json()['id']}", headers=auth(admin), json={"description": "Stand-up pouch"})
    assert ch.status_code == 200 and ch.json()["description"] == "Stand-up pouch"
    listed = client.get(f"{URL}/{t['id']}/values", headers=auth(admin)).json()
    assert listed["table"]["values"] == 1 and listed["values"][0]["code"] == "POUCH"
    # The app's lists (GET /lov) show an active table and its values.
    assert client.get("/lov", headers=auth(admin)).json()[t["code"]]["values"][0]["label"] == "Stand-up pouch"


def test_permissions_are_enforced_by_the_server(client, admin):
    t = _create(client, admin, allow_add=False)
    assert client.post(f"{URL}/{t['id']}/values", headers=auth(admin), json={"code": "X"}).status_code == 409, "Add Value off"
    client.patch(f"{URL}/{t['id']}", headers=auth(admin), json={"allow_add": True})
    vid = client.post(f"{URL}/{t['id']}/values", headers=auth(admin), json={"code": "BOX"}).json()["id"]
    client.patch(f"{URL}/{t['id']}", headers=auth(admin), json={"can_edit": False})
    assert client.patch(f"{URL}/values/{vid}", headers=auth(admin), json={"description": "Carton"}).status_code == 409, "Can Edit off"
    client.patch(f"{URL}/{t['id']}", headers=auth(admin), json={"can_edit": True, "is_active": False})
    assert client.post(f"{URL}/{t['id']}/values", headers=auth(admin), json={"code": "TIN"}).status_code == 409, "inactive"
    assert client.patch(f"{URL}/values/{vid}", headers=auth(admin), json={"description": "Carton"}).status_code == 409
    assert t["code"] not in client.get("/lov", headers=auth(admin)).json(), "an inactive table isn't offered"


def test_only_admins_manage_master_data(client, db, admin):
    t = _create(client, admin)
    for role in ("executive", "client"):
        someone = make_user(db, role=role)
        assert client.post(URL, headers=auth(someone), json={"name": "Nope", "description": "x"}).status_code == 403
        assert client.post(f"{URL}/{t['id']}/values", headers=auth(someone), json={"code": "X"}).status_code == 403


def test_system_tables_and_values_are_protected(client, admin):
    seed_lov()
    tables = {x["code"]: x for x in client.get(URL, headers=auth(admin)).json()["tables"]}
    sentiment = tables["SENTIMENT"]
    assert sentiment["is_system"] and not sentiment["allow_add"]
    assert client.patch(f"{URL}/{sentiment['id']}", headers=auth(admin), json={"is_active": False}).status_code == 409
    assert client.patch(f"{URL}/{sentiment['id']}", headers=auth(admin), json={"allow_add": True}).status_code == 409
    assert client.post(f"{URL}/{sentiment['id']}/values", headers=auth(admin), json={"code": "x"}).status_code == 409
    assert client.delete(f"{URL}/{sentiment['id']}", headers=auth(admin)).status_code == 409
    # Built-in tables are kept; a table created here is deleted with its values.
    assert client.delete(f"{URL}/{tables['GENDER']['id']}", headers=auth(admin)).status_code == 409
    t = _create(client, admin)
    client.post(f"{URL}/{t['id']}/values", headers=auth(admin), json={"code": "A"})
    gone = client.delete(f"{URL}/{t['id']}", headers=auth(admin))
    assert gone.status_code == 200 and gone.json()["values_deleted"] == 1
    assert client.get(f"{URL}/{t['id']}/values", headers=auth(admin)).status_code == 404


def test_measurement_units_weight_and_volume(client, admin):
    seed_lov()
    lov = client.get("/lov", headers=auth(admin)).json()
    cats = {v["code"]: v for v in lov["UNIT_CATEGORY"]["values"]}
    assert set(cats) == {"weight", "volume"}
    units = lov["MEASUREMENT_UNIT"]
    assert units["parent"] == "UNIT_CATEGORY"
    by_cat = {}
    for u in units["values"]:
        by_cat.setdefault(u["parent_id"], []).append(u["code"])
    assert by_cat[cats["weight"]["id"]] == ["µg", "mg", "g", "kg", "MT"]
    assert by_cat[cats["volume"]["id"]] == ["µL", "mL", "L", "kL"]
    tables = {x["code"]: x for x in client.get(URL, headers=auth(admin)).json()["tables"]}
    # Only the two categories: no third one can be added.
    assert client.post(f"{URL}/{tables['UNIT_CATEGORY']['id']}/values", headers=auth(admin), json={"code": "length"}).status_code == 409
    # A unit must belong to a category.
    assert client.post(f"{URL}/{tables['MEASUREMENT_UNIT']['id']}/values", headers=auth(admin), json={"code": "cg"}).status_code == 422



def test_the_super_admin_reads_the_units_for_recipes(client, db):
    seed_lov()
    org = make_user(db, role="client")
    lov = client.get("/lov", headers=auth(org))
    assert lov.status_code == 200 and len(lov.json()["MEASUREMENT_UNIT"]["values"]) == 9


def test_new_units_count_in_the_batch_weight():
    from app.services.recipe_tools import batch_check

    ok = batch_check({"batch_size": "1 MT", "ingredients": [
        {"name": "Peanuts", "quantity": "999", "unit": "kg"}, {"name": "Salt", "quantity": "1000", "unit": "g"}]})
    assert ok["ok"], ok
    vol = batch_check({"batch_size": "100 kg", "ingredients": [
        {"name": "Oil", "quantity": "0.1", "unit": "kL"}]})
    assert vol["ok"], vol


def test_a_unit_added_in_other_master_works_everywhere_without_code(client, admin):
    """No unit or factor is written in code: a new unit (centigram) is used by the batch weight,
    the costing and the recipe standards as soon as it is added, and stops when made inactive."""
    from types import SimpleNamespace

    from app.services.recipe_tools import TO_KG, batch_check, check_rule, costing

    seed_lov()
    tables = {x["code"]: x for x in client.get(URL, headers=auth(admin)).json()["tables"]}
    units = client.get(f"{URL}/{tables['MEASUREMENT_UNIT']['id']}/values", headers=auth(admin)).json()
    weight = next(p for p in units["parent_options"] if p["code"] == "weight")
    assert "cg" not in TO_KG
    url = f"{URL}/{tables['MEASUREMENT_UNIT']['id']}/values"
    assert client.post(url, headers=auth(admin), json={"code": "cg", "parent_id": weight["id"]}).status_code == 422, "factor required"
    made = client.post(url, headers=auth(admin), json={"code": "cg", "description": "Centigram", "parent_id": weight["id"], "factor": 0.00001})
    assert made.status_code == 201 and made.json()["extra"] == {"to_base": 0.00001, "base": "kg"}
    assert TO_KG["cg"] == 0.00001
    recipe = {"batch_size": "1 kg", "ingredients": [{"name": "Base", "quantity": "999", "unit": "g"},
                                                    {"name": "Spice", "quantity": "100", "unit": "cg"}]}
    assert batch_check(recipe)["ok"] and batch_check(recipe)["total_kg"] == 1.0
    rule = SimpleNamespace(id=1, name="r", kind="custom", ingredient="Spice", min_value=90, max_value=110, unit="cg")
    assert check_rule(rule, costing(recipe))["passed"]
    # Changing the factor changes the calculations at once.
    client.patch(f"{URL}/values/{made.json()['id']}", headers=auth(admin), json={"factor": 0.001})
    assert TO_KG["cg"] == 0.001
    # Inactive: no longer a unit anywhere.
    client.patch(f"{URL}/values/{made.json()['id']}", headers=auth(admin), json={"is_active": False})
    assert "cg" not in TO_KG
