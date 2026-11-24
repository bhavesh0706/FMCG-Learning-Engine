"""Quantities with their unit in one field, kg by default, and standard names with optional brackets."""

from conftest import _unique, auth, make_user

from app.services.recipe_tools import batch_check, normalize_ingredients

NOODLES = [("Wheat Flour", 65, 40), ("Palm Oil", 12, 120), ("Salt", 3, 18), ("Tapioca Starch", 5, 55), ("Red Chilli Powder", 0.8, 220),
           ("Onion Powder", 2, 180), ("Garlic Powder", 1, 250), ("Coriander Powder", 1.5, 180), ("Cumin Powder", 0.5, 300),
           ("Turmeric Powder", 0.3, 180), ("Sugar", 2, 45), ("Maltodextrin", 3, 60), ("Tomato Powder", 1.5, 200),
           ("Yeast Extract", 0.5, 400), ("Citric Acid", 0.2, 150), ("Amchur Powder", 0.5, 220), ("Black Pepper Powder", 0.2, 600),
           ("Anti-Caking Agent", 0.1, 300), ("Permitted Flavouring", 0.9, 500)]


def test_quantity_and_unit_in_one_field_and_kg_by_default():
    got = normalize_ingredients([{"name": "A", "quantity": "65 kg", "unit": ""}, {"name": "B", "quantity": "500g", "unit": ""},
                                 {"name": "C", "quantity": "12", "unit": ""}, {"name": "D", "quantity": "2", "unit": "l"},
                                 {"name": "E", "quantity": "a pinch", "unit": ""}])
    assert [(i["quantity"], i["unit"]) for i in got] == [("65", "kg"), ("500", "g"), ("12", "kg"), ("2", "l"), ("a pinch", "")]


def test_removing_the_units_no_longer_skips_the_batch_check():
    short = batch_check({"batch_size": "100kg = 200 packs of 500g", "ingredients": [{"name": "Wheat Flour", "quantity": "65", "unit": ""}]})
    assert short["checked"] and not short["ok"], "65 (kg) is less than the 100 kg batch"


def test_the_noodles_recipe_without_units_meets_its_standards(client, db):
    org = make_user(db, role="client")
    cid = client.post("/categories", headers=auth(org), json={"name": _unique("Instant Noodles ")}).json()["id"]
    pid = client.post("/products", headers=auth(org), json={"category_id": cid, "name": _unique("Masala Instant Noodles ")}).json()["id"]
    rules = [
        {"name": "Mandatory Wheat Flour Base", "kind": "required_ingredient", "ingredient": "Wheat Flour (Maida)"},
        {"name": "Mandatory Masala Seasoning", "kind": "required_ingredient", "ingredient": "Red Chilli Powder"},
        {"name": "Wheat Flour Composition Control", "kind": "ingredient_share", "ingredient": "Wheat Flour (Maida)", "min_value": 63, "max_value": 68},
        {"name": "Palm Oil Usage Control", "kind": "ingredient_share", "ingredient": "Palm Oil", "min_value": 10, "max_value": 14},
        {"name": "Salt Content Control", "kind": "ingredient_share", "ingredient": "Salt", "min_value": 2.5, "max_value": 4},
        {"name": "Finished Product Cost Control", "kind": "max_cost_per_kg", "max_value": 100},
        {"name": "Complete Ingredient Costing", "kind": "costs_complete"},
    ]
    for r in rules:
        assert client.post("/recipe-standards", headers=auth(org), json={**r, "product_id": pid}).status_code in (200, 201)
    body = {"batch_size": "100kg = 200 packs of 500g",
            "ingredients": [{"name": n, "quantity": str(q), "unit": "", "cost_per_kg": str(c)} for n, q, c in NOODLES]}
    r = client.put(f"/products/{pid}/recipe", headers=auth(org), json=body)
    assert r.status_code == 200, r.json()
    saved = client.get(f"/products/{pid}/recipe", headers=auth(org)).json()
    assert {i["unit"] for i in saved["recipe"]["ingredients"]} == {"kg"}, "plain numbers are saved as kg"
    assert saved["costing"]["cost_per_kg"] is not None and saved["costing"]["cost_per_kg"] <= 100
    assert saved["standards"]["failed_count"] == 0, saved["standards"]