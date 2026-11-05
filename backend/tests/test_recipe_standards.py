"""
Recipe standards: scope (one product, one category, or all products explicitly),
the five rule types, MET / NOT MET with expected, actual and reason.
Recipe: Mango Lassi (Cardamom powder at 2,600 per kg).
"""

import pytest
from conftest import _unique, auth, make_user

from app.database.models import Category, Product, RecipeStandard

LASSI = [("Set curd / dahi", 55, 68), ("Mango pulp, Alphonso", 20, 125), ("Toned milk", 14.5, 56), ("Sugar (refined)", 8.5, 46),
         ("Potable water", 1.62, 0.5), ("Pectin, high-methoxyl", 0.3, 1850), ("Cardamom powder", 0.05, 2600), ("Saffron extract", 0.03, 9500)]


def _recipe(rows):
    return {"batch_size": "100 kg", "ingredients": [{"name": n, "quantity": str(q), "unit": "kg", "cost_per_kg": c} for n, q, c in rows]}


@pytest.fixture()
def org(db):
    return make_user(db, role="client")


@pytest.fixture()
def shop(client, db, org):
    dairy = Category(name=_unique("Dairy "), is_active=1)
    cereal = Category(name=_unique("Cereal "), is_active=1)
    db.add_all([dairy, cereal])
    db.commit()
    lassi = Product(category_id=dairy.id, name=_unique("Mango Lassi "), is_active=1)
    curd = Product(category_id=dairy.id, name=_unique("Sweet Curd "), is_active=1)
    flakes = Product(category_id=cereal.id, name=_unique("Corn Flakes "), is_active=1)
    db.add_all([lassi, curd, flakes])
    db.commit()
    for p, rows in ((lassi, LASSI), (curd, [("Toned milk", 90, 56), ("Sugar", 10, 46)]), (flakes, [("Maize grits", 90, 40), ("Sugar", 10, None)])):
        assert client.put(f"/products/{p.id}/recipe", headers=auth(org), json=_recipe(rows)).status_code == 200
    return {"dairy": dairy, "cereal": cereal, "lassi": lassi, "curd": curd, "flakes": flakes}


def _rule(client, org, **body):
    r = client.post("/recipe-standards", headers=auth(org), json={"name": body.pop("name", "A rule"), **body})
    assert r.status_code == 201, r.text
    return r.json()


def _results(client, org, product):
    return {x["name"]: x for x in client.get(f"/products/{product.id}/recipe", headers=auth(org)).json()["standards"]["results"]}


def test_a_product_rule_applies_to_that_product_only(client, org, shop):
    _rule(client, org, name="Lassi has mango", kind="required_ingredient", ingredient="mango pulp", product_id=shop["lassi"].id)
    assert _results(client, org, shop["lassi"])["Lassi has mango"]["status"] == "MET"
    assert "Lassi has mango" not in _results(client, org, shop["curd"]), "same category, other product: not affected"
    assert "Lassi has mango" not in _results(client, org, shop["flakes"])


def test_a_category_rule_applies_to_that_category_only(client, org, shop):
    _rule(client, org, name="Dairy sugar max 9%", kind="ingredient_share", ingredient="sugar", max_value=9, category_id=shop["dairy"].id)
    lassi, curd = _results(client, org, shop["lassi"]), _results(client, org, shop["curd"])
    assert lassi["Dairy sugar max 9%"]["status"] == "MET" and lassi["Dairy sugar max 9%"]["actual"] == "8.5%"
    assert curd["Dairy sugar max 9%"]["status"] == "NOT MET" and "above the maximum 9%" in curd["Dairy sugar max 9%"]["reason"]
    assert "Dairy sugar max 9%" not in _results(client, org, shop["flakes"])


def test_a_rule_needs_a_scope_and_all_products_must_be_explicit(client, org, shop):
    r = client.post("/recipe-standards", headers=auth(org), json={"name": "No scope", "kind": "costs_complete"})
    assert r.status_code == 400 and "applies to" in r.text
    both = client.post("/recipe-standards", headers=auth(org), json={"name": "Two scopes", "kind": "costs_complete",
                                                                       "product_id": shop["lassi"].id, "category_id": shop["dairy"].id})
    assert both.status_code == 400
    _rule(client, org, name="Everything costed", kind="costs_complete", applies_to_all=True)
    assert _results(client, org, shop["lassi"])["Everything costed"]["status"] == "MET"
    flakes = _results(client, org, shop["flakes"])["Everything costed"]
    assert flakes["status"] == "NOT MET" and flakes["reason"] == "Missing Cost per KG: Sugar", "missing costs are reported"


def test_ingredient_cost_per_kg_met_and_not_met(client, org, shop):
    lassi = shop["lassi"]
    rule = _rule(client, org, name="Cardamom at most 2500/kg", kind="ingredient_cost_per_kg", ingredient="cardamom powder",
                 max_value=2500, product_id=lassi.id)
    res = _results(client, org, lassi)["Cardamom at most 2500/kg"]
    assert res["status"] == "NOT MET" and res["passed"] is False
    assert res["expected"] == "cardamom powder at most ₹2,500.00/kg"
    assert res["actual"] == "Cardamom powder: ₹2,600.00/kg"
    assert res["reason"] == "Cardamom powder costs ₹2,600.00/kg, ₹100.00 above the maximum ₹2,500.00/kg"
    cheaper = [(n, q, 2400 if n == "Cardamom powder" else c) for n, q, c in LASSI]
    assert client.put(f"/products/{lassi.id}/recipe", headers=auth(org), json=_recipe(cheaper)).status_code == 200
    res = _results(client, org, lassi)["Cardamom at most 2500/kg"]
    assert res["status"] == "MET" and res["actual"] == "Cardamom powder: ₹2,400.00/kg"
    assert rule["kind"] == "ingredient_cost_per_kg" and rule["scope"] == "product"


def test_the_recipe_cost_rule_is_independent_of_ingredient_costs(client, org, shop):
    lassi = shop["lassi"]
    _rule(client, org, name="Cardamom at most 2500/kg", kind="ingredient_cost_per_kg", ingredient="cardamom", max_value=2500, product_id=lassi.id)
    _rule(client, org, name="Recipe at most 90/kg", kind="max_cost_per_kg", max_value=90, product_id=lassi.id)
    res = _results(client, org, lassi)
    assert res["Cardamom at most 2500/kg"]["status"] == "NOT MET", "one ingredient too expensive"
    assert res["Recipe at most 90/kg"]["status"] == "MET" and res["Recipe at most 90/kg"]["actual"] == "₹84.14/kg", "the whole recipe is fine"
    _rule(client, org, name="Recipe at most 80/kg", kind="max_cost_per_kg", max_value=80, product_id=lassi.id)
    over = _results(client, org, lassi)["Recipe at most 80/kg"]
    assert over["status"] == "NOT MET" and "₹4.14 above the maximum ₹80.00/kg" in over["reason"]


def test_missing_costs_for_ingredient_and_recipe_rules(client, org, shop):
    flakes = shop["flakes"]
    _rule(client, org, name="Sugar at most 50/kg", kind="ingredient_cost_per_kg", ingredient="sugar", max_value=50, product_id=flakes.id)
    _rule(client, org, name="Missing ingredient", kind="ingredient_cost_per_kg", ingredient="cocoa", max_value=50, product_id=flakes.id)
    res = _results(client, org, flakes)
    assert res["Sugar at most 50/kg"]["status"] == "NOT MET" and res["Sugar at most 50/kg"]["reason"] == "No Cost per KG for Sugar"
    assert res["Missing ingredient"]["reason"] == "No ingredient named like “cocoa”"


def test_edit_and_remove_a_product_rule(client, org, shop):
    lassi = shop["lassi"]
    rule = _rule(client, org, name="Cardamom cap", kind="ingredient_cost_per_kg", ingredient="cardamom", max_value=2500, product_id=lassi.id)
    assert [r["id"] for r in client.get(f"/recipe-standards?product_id={lassi.id}", headers=auth(org)).json()["standards"]] == [rule["id"]]
    changed = client.put(f"/recipe-standards/{rule['id']}", headers=auth(org), json={"name": "Cardamom cap", "kind": "ingredient_cost_per_kg",
                                                                                    "ingredient": "cardamom", "max_value": 3000, "product_id": lassi.id})
    assert changed.status_code == 200 and changed.json()["max_value"] == 3000
    assert _results(client, org, lassi)["Cardamom cap"]["status"] == "MET"
    assert client.delete(f"/recipe-standards/{rule['id']}", headers=auth(org)).status_code == 200
    assert "Cardamom cap" not in _results(client, org, lassi)


def test_only_the_super_admin_creates_rules(client, db, admin, shop):
    body = {"name": "Admin rule", "kind": "costs_complete", "applies_to_all": True}
    assert client.post("/recipe-standards", headers=auth(admin), json=body).status_code == 403
    assert client.get("/recipe-standards", headers=auth(admin)).status_code == 200, "admins can read them"


def test_older_unscoped_rules_keep_applying_to_all_products(client, db, org, shop):
    """Rows saved before scopes existed (no product, no category) are marked all-products by the migration."""
    from sqlalchemy import text

    from app.database.database import engine

    db.add(RecipeStandard(name="Legacy rule", kind="costs_complete", is_active=1, applies_to_all=0))
    db.commit()
    with engine.begin() as conn:  # what run_migrations does once, when the scope columns are new
        conn.execute(text("UPDATE recipe_standards SET applies_to_all = 1 WHERE product_id IS NULL AND category_id IS NULL"))
    db.expire_all()
    assert "Legacy rule" in _results(client, org, shop["lassi"]) and "Legacy rule" in _results(client, org, shop["flakes"])
    db.query(RecipeStandard).filter(RecipeStandard.name == "Legacy rule").delete()
    db.commit()
