"""Recipe standards in any unit of measure (Other Master > Measurement unit): limits are kept in
the rule's UOM and compared in kg, so "500-800 g" and "0.5-0.8 kg" mean the same."""
from types import SimpleNamespace as R

from app.services.recipe_tools import check_rule, costing

RECIPE = {"batch_size": "100 kg", "ingredients": [
    {"name": "Plain Curd", "quantity": "60", "unit": "kg", "cost_per_kg": 80},
    {"name": "Water", "quantity": "39", "unit": "L", "cost_per_kg": 1},
    {"name": "Roasted Cumin Powder", "quantity": "500", "unit": "g", "cost_per_kg": 500},
    {"name": "Black Salt", "quantity": "0.5", "unit": "kg", "cost_per_kg": 80},
]}


def rule(kind, ingredient=None, lo=None, hi=None, unit=None):
    return R(id=1, name="r", kind=kind, ingredient=ingredient, min_value=lo, max_value=hi, unit=unit)


def test_quantity_rules_in_any_unit():
    cost = costing(RECIPE)
    assert check_rule(rule("custom", "Roasted Cumin Powder", 400, 600, "g"), cost)["passed"]
    assert check_rule(rule("custom", "Roasted Cumin Powder", 0.4, 0.6), cost)["passed"], "no unit = kg, as before"
    r = check_rule(rule("custom", "Roasted Cumin Powder", 600, 800, "g"), cost)
    assert not r["passed"] and r["actual"] == "500 g" and "below the minimum 600 g" in r["reason"]
    assert check_rule(rule("custom", "Water", 30, 40, "L"), cost)["passed"]
    assert check_rule(rule("custom", "Water", None, 40000, "mL"), cost)["passed"]
    assert not check_rule(rule("custom", "Plain Curd", None, 0.05, "MT"), cost)["passed"], "60 kg is above 0.05 MT"


def test_cost_rules_per_unit():
    cost = costing(RECIPE)
    r = check_rule(rule("ingredient_cost_per_kg", "Roasted Cumin Powder", hi=0.6, unit="g"), cost)
    assert r["passed"] and r["actual"] == "Roasted Cumin Powder: ₹0.50/g"
    assert not check_rule(rule("ingredient_cost_per_kg", "Roasted Cumin Powder", hi=0.4, unit="g"), cost)["passed"]
    assert check_rule(rule("ingredient_cost_per_kg", "Roasted Cumin Powder", hi=600), cost)["passed"], "₹ per kg as before"
    whole = cost["cost_per_kg"]
    assert check_rule(rule("max_cost_per_kg", hi=whole * 1000 + 1, unit="MT"), cost)["passed"]
    assert not check_rule(rule("max_cost_per_kg", hi=whole * 1000 - 1, unit="MT"), cost)["passed"]


def test_rules_keep_their_unit_through_the_api(client, db):
    from conftest import auth, make_user
    from app.database.models import Category, Product

    org = make_user(db, role="client")
    cat = Category(name="UOM cat", is_active=1)
    db.add(cat)
    db.commit()
    p = Product(category_id=cat.id, name="UOM product", is_active=1)
    db.add(p)
    db.commit()
    body = {"name": "Cumin quantity", "kind": "custom", "ingredient": "cumin", "min_value": 400, "max_value": 600,
            "unit": "g", "product_id": p.id}
    made = client.post("/recipe-standards", headers=auth(org), json=body)
    assert made.status_code in (200, 201), made.text
    assert made.json()["unit"] == "g"
    bad = client.post("/recipe-standards", headers=auth(org), json={**body, "unit": "furlong"})
    assert bad.status_code == 400
