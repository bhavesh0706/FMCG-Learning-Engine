"""
Starting a survey round requires every applicable active Recipe Standard to be met.
The server re-checks immediately before creating the round; there is no override.
Recipe: Mango Lassi (Cardamom powder 2,600/kg, recipe 84.14/kg, sugar 8.5%).
"""

import pytest
from conftest import _unique, auth, make_user

from app.database.models import AuditLog, Category, Notification, Product, SurveyRound

LASSI = [("Set curd / dahi", 55, 68), ("Mango pulp, Alphonso", 20, 125), ("Toned milk", 14.5, 56), ("Sugar (refined)", 8.5, 46),
         ("Potable water", 1.62, 0.5), ("Pectin, high-methoxyl", 0.3, 1850), ("Cardamom powder", 0.05, 2600), ("Saffron extract", 0.03, 9500)]


def _recipe(rows):
    return {"batch_size": "100 kg", "ingredients": [{"name": n, "quantity": str(q), "unit": "kg", "cost_per_kg": c} for n, q, c in rows]}


@pytest.fixture()
def org(db):
    return make_user(db, role="client")


@pytest.fixture()
def lassi(client, db, org):
    dairy = Category(name=_unique("Dairy "), is_active=1)
    db.add(dairy)
    db.commit()
    p = Product(category_id=dairy.id, name=_unique("Mango Lassi "), is_active=1)
    db.add(p)
    db.commit()
    assert client.put(f"/products/{p.id}/recipe", headers=auth(org), json=_recipe(LASSI)).status_code == 200
    return p


def _rule(client, org, **body):
    r = client.post("/recipe-standards", headers=auth(org), json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _three_failing_rules(client, org, product):
    _rule(client, org, name="Cardamom at most 2500/kg", kind="ingredient_cost_per_kg", ingredient="cardamom powder", max_value=2500, product_id=product.id)
    _rule(client, org, name="Recipe at most 80/kg", kind="max_cost_per_kg", max_value=80, product_id=product.id)
    _rule(client, org, name="Sugar at most 5%", kind="ingredient_share", ingredient="sugar", max_value=5, product_id=product.id)
    _rule(client, org, name="Contains mango pulp", kind="required_ingredient", ingredient="mango pulp", product_id=product.id)  # met


def _start(client, org, product, **extra):
    return client.post(f"/org/products/{product.id}/rounds", headers=auth(org), json={"target": 10, **extra})


def _counts(db, product):
    db.expire_all()
    return (db.query(SurveyRound).filter(SurveyRound.product_id == product.id).count(),
            db.query(Notification).count(), db.get(Product, product.id).needs_round)


def test_three_failed_standards_block_the_round_and_nothing_is_created(client, db, org, lassi):
    _three_failing_rules(client, org, lassi)
    before = _counts(db, lassi)
    r = _start(client, org, lassi)
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "recipe_standards_not_met" and detail["recipe_version"] == 1
    failed = {f["name"]: f for f in detail["failed_standards"]}
    assert set(failed) == {"Cardamom at most 2500/kg", "Recipe at most 80/kg", "Sugar at most 5%"}, "the met rule isn't listed"
    c = failed["Cardamom at most 2500/kg"]
    assert (c["ingredient"], c["expected"], c["actual"]) == ("cardamom powder", "cardamom powder at most ₹2,500.00/kg", "Cardamom powder: ₹2,600.00/kg")
    assert failed["Recipe at most 80/kg"]["actual"] == "₹84.14/kg" and failed["Recipe at most 80/kg"]["ingredient"] is None
    assert (failed["Sugar at most 5%"]["ingredient"], failed["Sugar at most 5%"]["expected"], failed["Sugar at most 5%"]["actual"]) == ("sugar", "at most 5%", "8.5%")
    assert _counts(db, lassi) == before, "no round, no notification, product unchanged"
    assert db.query(AuditLog).filter(AuditLog.summary.like(f"Round not started for product #{lassi.id}%")).count() == 1
    shown = client.get(f"/org/products/{lassi.id}", headers=auth(org)).json()
    assert shown["can_start_round"] is False and len(shown["standards_failed"]) == 3


def test_the_round_starts_once_every_standard_is_met(client, db, org, lassi):
    _three_failing_rules(client, org, lassi)
    assert _start(client, org, lassi).status_code == 409
    fixed = [(n, 3 if n == "Sugar (refined)" else q, 2400 if n == "Cardamom powder" else 40 if n == "Mango pulp, Alphonso" else c) for n, q, c in LASSI]
    assert client.put(f"/products/{lassi.id}/recipe", headers=auth(org), json=_recipe(fixed)).status_code == 200
    assert client.get(f"/org/products/{lassi.id}", headers=auth(org)).json()["can_start_round"] is True
    r = _start(client, org, lassi)
    assert r.status_code == 201, r.text
    assert r.json()["round"]["number"] == 1 and r.json()["round"]["recipe_version"] == 2


def test_the_server_rechecks_at_creation_time_no_cached_result(client, db, org, lassi):
    assert client.get(f"/org/products/{lassi.id}", headers=auth(org)).json()["can_start_round"] is True  # page loaded: OK
    _rule(client, org, name="Cardamom at most 2500/kg", kind="ingredient_cost_per_kg", ingredient="cardamom", max_value=2500, product_id=lassi.id)
    r = _start(client, org, lassi)  # the button was enabled when the page loaded
    assert r.status_code == 409 and [f["name"] for f in r.json()["detail"]["failed_standards"]] == ["Cardamom at most 2500/kg"]


def test_the_recipe_version_the_round_tests_is_validated(client, db, org, lassi):
    fixed = [(n, q, 2400 if n == "Cardamom powder" else c) for n, q, c in LASSI]
    client.put(f"/products/{lassi.id}/recipe", headers=auth(org), json=_recipe(fixed))  # v2: cardamom 2,400
    _rule(client, org, name="Cardamom at most 2500/kg", kind="ingredient_cost_per_kg", ingredient="cardamom", max_value=2500, product_id=lassi.id)
    old = _start(client, org, lassi, recipe_version=1)  # v1 still has cardamom at 2,600
    assert old.status_code == 409 and old.json()["detail"]["recipe_version"] == 1
    assert _start(client, org, lassi, recipe_version=2).status_code == 201


def test_rules_for_other_products_categories_and_inactive_rules_do_not_block(client, db, org, lassi):
    other = Category(name=_unique("Cereal "), is_active=1)
    db.add(other)
    db.commit()
    flakes = Product(category_id=other.id, name=_unique("Flakes "), is_active=1)
    db.add(flakes)
    db.commit()
    _rule(client, org, name="Flakes only", kind="required_ingredient", ingredient="maize", product_id=flakes.id)
    _rule(client, org, name="Cereal only", kind="required_ingredient", ingredient="maize", category_id=other.id)
    off = _rule(client, org, name="Inactive", kind="max_cost_per_kg", max_value=1, product_id=lassi.id)
    client.put(f"/recipe-standards/{off['id']}", headers=auth(org), json={"name": "Inactive", "kind": "max_cost_per_kg", "max_value": 1,
                                                                         "product_id": lassi.id, "is_active": False})
    assert _start(client, org, lassi).status_code == 201


def test_there_is_no_override(client, db, org, admin, lassi):
    _three_failing_rules(client, org, lassi)
    for extra in ({"force": True}, {"override": True}, {"skip_standards": True}):
        assert _start(client, org, lassi, **extra).status_code == 409
    # the admin's path to the same endpoint is checked the same way
    assert client.post(f"/org/products/{lassi.id}/rounds", headers=auth(admin), json={"target": 10}).status_code == 409
    assert _counts(db, lassi)[0] == 0
