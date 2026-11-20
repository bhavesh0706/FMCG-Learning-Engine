"""Recipe batch weight: the weighed ingredients must add up to exactly the batch size, to the
gram (services/recipe_tools.batch_check)."""

import pytest
from conftest import _unique, auth, make_user

from app.services.recipe_tools import batch_check, batch_weight_kg


def _recipe(batch, *kgs, unit="kg"):
    return {"batch_size": batch, "ingredients": [{"name": f"Ingredient {i}", "quantity": str(q), "unit": unit} for i, q in enumerate(kgs)]}


@pytest.mark.parametrize("text, kg", [("100 kg batch = 500 bottles of 200 ml", 100), ("1.5 tonnes", 1500), ("2 quintal", 200),
                                      ("250kg", 250), ("500 bottles", None), ("", None)])
def test_the_batch_weight_is_read_from_the_text(text, kg):
    assert batch_weight_kg(text) == kg


def test_the_ingredients_must_weigh_exactly_the_batch():
    assert batch_check(_recipe("100 kg batch", 80, 20))["ok"]
    short = batch_check(_recipe("100 kg batch", 80, 19.5))
    assert not short["ok"] and short["short_kg"] == 0.5 and "0.5 kg less than the 100 kg batch" in short["message"]
    over = batch_check(_recipe("100 kg batch", 80, 20.1))
    assert not over["ok"] and over["over_kg"] == 0.1 and "0.1 kg more than the 100 kg batch" in over["message"]
    assert not batch_check(_recipe("100 kg", 99.999))["ok"], "one gram short is refused"


def test_many_small_quantities_add_up_without_rounding_errors():
    # The Masala Peanuts recipe: 15 ingredients, many below 1 kg.
    kgs = [80, 8, 3, 4, 1.5, 1.1, 0.35, 0.5, 0.3, 0.3, 0.3, 0.1, 0.02, 0.08, 0.45]
    assert batch_check(_recipe("100 kg batch = approximately 666 servings of 150 g", *kgs))["ok"]
    assert batch_check(_recipe("100 kg", 100000, unit="g"))["ok"]                # grams are converted
    assert batch_check(_recipe("500 bottles", 999))["checked"] is False         # no weight in the batch text


def test_saving_a_recipe_that_doesnt_match_the_batch_is_refused(client, db):
    org = make_user(db, role="client")
    cid = client.post("/categories", headers=auth(org), json={"name": _unique("Dairy ")}).json()["id"]
    pid = client.post("/products", headers=auth(org), json={"category_id": cid, "name": _unique("Lassi ")}).json()["id"]
    for kgs in ((80, 19.5), (80, 22)):
        r = client.put(f"/products/{pid}/recipe", headers=auth(org), json=_recipe("100 kg batch = 500 bottles", *kgs))
        assert r.status_code == 400 and "must add up to exactly 100 kg" in r.json()["detail"]
    ok = client.put(f"/products/{pid}/recipe", headers=auth(org), json=_recipe("100 kg batch = 500 bottles", 80, 20))
    assert ok.status_code == 200
    check = ok.json()["costing"]["batch_check"]
    assert check["ok"] and check["total_kg"] == 100
