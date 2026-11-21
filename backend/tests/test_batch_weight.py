"""Recipe batch weight: the weighed ingredients must add up to exactly the batch size, to the
gram (services/recipe_tools.batch_check)."""

import pytest
from conftest import _unique, auth, make_user

from app.services.recipe_tools import TOLERANCE_BANDS, batch_check, batch_weight_kg, tolerance_pct


def _recipe(batch, *kgs, unit="kg"):
    return {"batch_size": batch, "ingredients": [{"name": f"Ingredient {i}", "quantity": str(q), "unit": unit} for i, q in enumerate(kgs)]}


@pytest.mark.parametrize("text, kg", [("100 kg batch = 500 bottles of 200 ml", 100), ("1.5 tonnes", 1500), ("2 quintal", 200),
                                      ("250kg", 250), ("500 bottles", None), ("", None)])
def test_the_batch_weight_is_read_from_the_text(text, kg):
    assert batch_weight_kg(text) == kg


def test_every_100_kg_band_has_its_own_percentage():
    pcts = [pct for _, pct in TOLERANCE_BANDS]
    assert len(set(pcts)) == len(pcts) == 10 and pcts == sorted(pcts, reverse=True)
    assert [tolerance_pct(b) for b in (50, 100, 101, 200, 1000, 1001)] == [2.0, 2.0, 1.8, 1.8, 0.9, 0.8]


def test_the_ingredients_may_weigh_up_to_the_tolerance_over_the_batch_never_less():
    assert batch_check(_recipe("100 kg batch", 80, 20))["ok"]
    assert batch_check(_recipe("100 kg batch", 82, 20))["ok"], "102 kg: at the limit"
    over = batch_check(_recipe("100 kg batch", 84.5, 20))  # 104.5 kg
    assert not over["ok"] and over["max_kg"] == 102 and over["over_kg"] == 2.5
    assert "up to 102 kg" in over["message"] and "+2% tolerance for batches up to 100 kg" in over["message"]
    short = batch_check(_recipe("100 kg batch", 80, 19.5))
    assert not short["ok"] and short["short_kg"] == 0.5 and "0.5 kg less than the 100 kg batch" in short["message"]
    assert batch_check(_recipe("300 kg", 300, 4.8))["ok"] and not batch_check(_recipe("300 kg", 300, 5))["ok"]
    assert batch_check(_recipe("1000 kg", 1009))["ok"] and not batch_check(_recipe("1000 kg", 1009.5))["ok"]
    assert batch_check(_recipe("1500 kg", 1512))["ok"] and not batch_check(_recipe("1500 kg", 1512.1))["ok"]


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
    short = client.put(f"/products/{pid}/recipe", headers=auth(org), json=_recipe("100 kg batch = 500 bottles", 80, 19.5))
    assert short.status_code == 400 and "less than the 100 kg batch" in short.json()["detail"]
    over = client.put(f"/products/{pid}/recipe", headers=auth(org), json=_recipe("100 kg batch = 500 bottles", 84.5, 20))
    assert over.status_code == 400 and "more than the 100 kg batch allows" in over.json()["detail"]
    assert client.put(f"/products/{pid}/recipe", headers=auth(org), json=_recipe("100 kg batch = 500 bottles", 82, 20)).status_code == 200
    ok = client.put(f"/products/{pid}/recipe", headers=auth(org), json=_recipe("100 kg batch = 500 bottles", 80, 20))
    assert ok.status_code == 200
    check = ok.json()["costing"]["batch_check"]
    assert check["ok"] and check["total_kg"] == 100
