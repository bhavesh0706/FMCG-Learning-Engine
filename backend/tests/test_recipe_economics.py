"""Recipe version cost comparison and profitability: computed on the server, checked before release."""

from datetime import datetime

from conftest import _unique, auth, make_user

from app.database.models import ConsolidatedReport, RecipeVersion, Respondent, SurveyResponse


def _recipe(base_kg, sugar_kg, base_cost=50, oil_kg=0):
    ings = [{"name": "Base mix", "quantity": str(base_kg), "unit": "kg", "cost_per_kg": str(base_cost)},
            {"name": "Sugar", "quantity": str(sugar_kg), "unit": "kg", "cost_per_kg": "20"}]
    if oil_kg:
        ings.append({"name": "Edible oil", "quantity": str(oil_kg), "unit": "kg", "cost_per_kg": "150"})
    return {"batch_size": "100 kg", "ingredients": ings}


def _tested_product(client, db, org):
    cid = client.post("/categories", headers=auth(org), json={"name": _unique("Snacks ")}).json()["id"]
    pid = client.post("/products", headers=auth(org), json={"category_id": cid, "name": _unique("Masala Peanuts ")}).json()["id"]
    assert client.put(f"/products/{pid}/recipe", headers=auth(org), json=_recipe(95, 1, oil_kg=4)).status_code == 200
    who = Respondent(name="Priya", consent_given=1, created_at=datetime.utcnow())
    db.add(who)
    db.flush()
    db.add(SurveyResponse(product_id=pid, respondent_id=who.id, recipe_version=1, status="submitted", created_at=datetime.utcnow()))
    db.commit()  # v1 has a consumer response: locked, changes go to a new version
    return pid


def test_the_comparison_recalculates_costs_and_profit(client, db):
    org = make_user(db, role="client")
    pid = _tested_product(client, db, org)
    t = client.put(f"/products/{pid}/commercials", headers=auth(org),
                   json={"selling_price_per_kg": 80, "target_cost_per_kg": 60, "min_margin_pct": 20})
    assert t.status_code == 200
    proposed = {**_recipe(93, 3, oil_kg=4)}  # sugar 1 kg -> 3 kg (+2 kg x Rs 20 = +Rs 40)
    c = client.post(f"/products/{pid}/recipe/compare", headers=auth(org), json={"recipe": proposed}).json()
    sugar = next(r for r in c["rows"] if r["ingredient"] == "Sugar")
    assert (sugar["old_kg"], sugar["new_kg"], sugar["batch_cost_change"]) == (1, 3, 40), sugar
    assert c["old"]["batch_cost"] == 95 * 50 + 20 + 4 * 150 and c["new"]["batch_cost"] == 93 * 50 + 60 + 4 * 150
    assert c["change"]["batch_cost"] == -60 and c["new"]["cost_per_kg"] == round(c["new"]["batch_cost"] / 100, 2)
    assert c["new"]["gross_profit_per_kg"] == round(80 - c["new"]["cost_per_kg"], 2)
    assert c["new"]["margin_pct"] == round(100 * (80 - c["new"]["cost_per_kg"]) / 80, 1)
    assert not c["needs_approval"]


def test_a_new_version_over_the_cost_limit_needs_approval_and_keeps_its_comparison(client, db):
    org = make_user(db, role="client")
    pid = _tested_product(client, db, org)
    client.put(f"/products/{pid}/commercials", headers=auth(org), json={"selling_price_per_kg": 80, "target_cost_per_kg": 60, "min_margin_pct": 20})
    pricey = _recipe(95, 1, base_cost=75, oil_kg=4)  # cost per kg above the Rs 60 limit, margin below 20%
    c = client.post(f"/products/{pid}/recipe/compare", headers=auth(org), json={"recipe": pricey}).json()
    assert c["needs_approval"] and {w["code"] for w in c["warnings"]} >= {"over_cost_limit", "margin_below_minimum"}
    r = client.put(f"/products/{pid}/recipe", headers=auth(org), json={**pricey, "new_version": True})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "financial_review_needed", "not released without approval"
    assert [v.version for v in db.query(RecipeVersion).filter(RecipeVersion.product_id == pid)] == [1]
    decisions = [{"change": "Increase sugar from 1 kg to 3 kg", "ingredient": "Sugar", "decision": "rejected"}]
    r = client.put(f"/products/{pid}/recipe", headers=auth(org),
                   json={**pricey, "new_version": True, "approve_financials": True, "recommendation_decisions": decisions})
    assert r.status_code == 200 and r.json()["version"] == 2
    db.expire_all()
    v2 = db.query(RecipeVersion).filter(RecipeVersion.product_id == pid, RecipeVersion.version == 2).one()
    assert v2.cost_comparison["needs_approval"] and v2.decisions == decisions, "the comparison and decisions are history"
    v1 = db.query(RecipeVersion).filter(RecipeVersion.product_id == pid, RecipeVersion.version == 1).one()
    assert float(v1.recipe["ingredients"][0]["cost_per_kg"]) == 50, "v1 keeps its own costs"
    hist = client.get(f"/products/{pid}/recipe/versions", headers=auth(org)).json()
    rows = hist["versions"] if isinstance(hist, dict) else hist
    assert any(v.get("cost_comparison") for v in rows)


def test_recommendations_propose_quantities(client, db):
    org = make_user(db, role="client")
    pid = _tested_product(client, db, org)
    db.add(ConsolidatedReport(product_id=pid, response_ids=[], response_count=5, narrative={"recipe_recommendations": [
        {"change": "Increase sugar from 1 kg to 3 kg", "ingredient": "Sugar", "reason": "Too salty", "customers": 2},
        {"change": "Reduce edible oil by 1 kg", "ingredient": "Edible oil", "reason": "Too oily", "customers": 1},
        {"change": "Make the coating crunchier", "ingredient": "", "reason": "Soft", "customers": 1}]}))
    db.commit()
    recs = client.get(f"/products/{pid}/recipe/recommendations", headers=auth(org)).json()["recommendations"]
    by = {r["change"]: r for r in recs}
    assert by["Increase sugar from 1 kg to 3 kg"]["proposed_kg"] == 3 and by["Increase sugar from 1 kg to 3 kg"]["current_kg"] == 1
    assert by["Reduce edible oil by 1 kg"]["proposed_kg"] == 3
    assert by["Make the coating crunchier"]["applicable"] is False


def test_the_report_shows_what_its_recommendations_would_cost(client, db):
    from app.services.recipe_economics import report_cost_comparison, with_cost_comparison
    org = make_user(db, role="client")
    pid = _tested_product(client, db, org)
    rep = ConsolidatedReport(product_id=pid, response_ids=[], response_count=5, narrative={"recipe_recommendations": [
        {"change": "Increase sugar from 1 kg to 3 kg", "ingredient": "Sugar", "reason": "Too salty", "customers": 2},
        {"change": "Make the coating crunchier", "ingredient": "", "reason": "Soft", "customers": 1}]})
    db.add(rep)
    db.commit()
    cc = report_cost_comparison(db, rep)
    sugar = next(r for r in cc["rows"] if r["ingredient"] == "Sugar")
    assert sugar["batch_cost_change"] == 40 and cc["change"]["batch_cost"] == 40
    assert [a["ingredient"] for a in cc["applied"]] == ["Sugar"], "only recommendations with a quantity are applied"
    assert with_cost_comparison(rep, rep.narrative)["cost_comparison"]["change"]["batch_cost"] == 40
    plain = ConsolidatedReport(product_id=pid, response_ids=[], response_count=5, narrative={"recipe_recommendations": [
        {"change": "Make it crunchier", "ingredient": "", "customers": 1}]})
    db.add(plain)
    db.commit()
    assert report_cost_comparison(db, plain) is None and "cost_comparison" not in with_cost_comparison(plain, plain.narrative)


def test_the_report_cost_box_from_stored_recommendations(client, db):
    from app.services.recipe_economics import with_cost_comparison
    org = make_user(db, role="client")
    pid = _tested_product(client, db, org)
    client.put(f"/products/{pid}/commercials", headers=auth(org), json={"selling_price_per_kg": 80, "target_cost_per_kg": 60, "min_margin_pct": 20})
    rep = ConsolidatedReport(product_id=pid, response_ids=[], response_count=5, narrative={"recipe_recommendations": [
        {"change": "Increase sugar from 1 kg to 3 kg", "ingredient": "Sugar", "reason": "Too salty", "customers": 2}]})
    db.add(rep)
    db.commit()
    cc = with_cost_comparison(rep, rep.narrative)["cost_comparison"]
    sugar = next(r for r in cc["rows"] if r["ingredient"] == "Sugar")
    assert (sugar["old_kg"], sugar["new_kg"], sugar["batch_cost_change"]) == (1, 3, 40)
    assert cc["change"]["batch_cost"] == 40 and cc["new"]["margin_pct"] is not None
    empty = ConsolidatedReport(product_id=pid, response_ids=[], response_count=5, narrative={"recipe_recommendations": []})
    db.add(empty)
    db.commit()
    assert "cost_comparison" not in with_cost_comparison(empty, empty.narrative), "no recommendations: no box"