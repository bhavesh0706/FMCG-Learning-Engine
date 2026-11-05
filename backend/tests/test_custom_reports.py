"""
Custom reports from a prompt: plan (AI, or keywords when the AI is unavailable, as in
these tests), validation, real-data files, cross-product, access.
"""

import io

import pytest
from conftest import _unique, auth, make_user, open_round, start_survey

from app.api.video import save_feedback_record
from app.database.models import Category, Product, Respondent, SurveyQuestion, SurveyResponse, SurveyRound


def _product(db, name, category):
    p = Product(category_id=category.id, name=name, is_active=1)
    db.add(p)
    db.commit()
    for pos in range(1, 6):
        db.add(SurveyQuestion(product_id=p.id, position=pos, text=f"Question {pos}?", question_type="text", required=1, is_active=1))
    db.commit()
    return p


def _survey(client, db, person, product, city, rtype, rating, text):
    rid = start_survey(client, person, product)["response_id"]
    resp = db.get(SurveyResponse, rid)
    who = db.get(Respondent, resp.respondent_id)
    who.location, who.respondent_type, who.country = city, rtype, "India"
    db.commit()
    save_feedback_record("a.m4a", __file__, 40, 1, rating, text, text, [], response_id=rid)
    db.expire_all()
    return rid


@pytest.fixture()
def data(client, db):
    cat = Category(name=_unique("Cereals "), is_active=1)
    db.add(cat)
    db.commit()
    flakes = _product(db, _unique("Crunchy Flakes "), cat)
    oats = _product(db, _unique("Morning Oats "), cat)
    person = make_user(db)
    city = _unique("Pune")
    r1 = [_survey(client, db, person, flakes, city, "Retailer", 4.0, f"{flakes.name} is crunchy"),
          _survey(client, db, person, flakes, "Delhi", "End consumer", 3.0, f"{flakes.name} is too sweet")]
    db.query(SurveyRound).filter(SurveyRound.product_id == flakes.id).update({"status": "closed"})
    db.commit()
    open_round(db, flakes, number=2)
    r2 = [_survey(client, db, person, flakes, city, "Retailer", 5.0, f"{flakes.name} round two"),
          _survey(client, db, person, flakes, city, "End consumer", 2.0, f"{flakes.name} round two too sweet")]
    _survey(client, db, person, oats, city, "Retailer", 4.5, f"{oats.name} is filling")
    return {"flakes": flakes, "oats": oats, "city": city, "r1": r1, "r2": r2}


def test_a_prompt_for_one_product_filters_compares_rounds_and_limits_slides(client, db, admin, data):
    flakes, city = data["flakes"], data["city"]
    prompt = f"5 slides about concerns, only retailers in {city}, compare round 1 and round 2"
    r = client.post("/reports/plan", headers=auth(admin), json={"prompt": prompt, "format": "pptx", "product_ids": [flakes.id]})
    assert r.status_code == 200, r.text
    body = r.json()
    plan = body["plan"]
    assert body["planned_by"] == "keywords"  # no AI service in the tests
    assert plan["product_ids"] == [flakes.id] and plan["rounds"] == [1, 2] and plan["compare_rounds"]
    assert plan["filters"]["respondent_types"] == ["Retailer"] and plan["filters"]["cities"] == [city]
    assert plan["max_slides"] == 5 and plan["focus"] == "negative"
    # real numbers: one retailer from that city in each round
    assert [b["surveys"] for b in body["blocks"]] == [1, 1] and body["surveys"] == 2
    assert "rounds compared" in body["summary"] and "Retailer" in body["summary"]
    f = client.get(f"/reports/file/{body['request_id']}", headers=auth(admin))
    assert f.status_code == 200
    from pptx import Presentation

    deck = Presentation(io.BytesIO(f.content))
    assert 2 <= len(deck.slides) <= 5
    titles = [s.shapes[0].text_frame.text for s in deck.slides]
    assert any(t.startswith("Concerns") or t == "Comparison" for t in titles)


def test_cross_product_prompts_use_the_real_numbers(client, db, admin, data):
    org = make_user(db, role="client")
    r = client.post("/reports/plan", headers=auth(org), json={"prompt": "compare all products in the latest round", "format": "pptx"})
    assert r.status_code == 200, r.text
    plan = r.json()["plan"]
    assert {data["flakes"].id, data["oats"].id} <= set(plan["product_ids"]) and "comparison" in plan["sections"]
    blocks = {b["label"]: b["surveys"] for b in r.json()["blocks"]}
    assert blocks[f"{data['flakes'].name} · Round 2"] == 2 and blocks[f"{data['oats'].name} · Round 1"] == 1
    f = client.get(f"/reports/file/{r.json()['request_id']}", headers=auth(org))
    from pptx import Presentation

    deck = Presentation(io.BytesIO(f.content))
    comparison = next(s for s in deck.slides if s.shapes[0].text_frame.text == "Comparison")
    table = next(sh.table for sh in comparison.shapes if sh.has_table)
    cells = {table.cell(r, 0).text: table.cell(r, 1).text for r in range(1, len(table.rows))}
    assert cells[f"{data['flakes'].name} · Round 2"] == "2" and cells[f"{data['oats'].name} · Round 1"] == "1"


def test_prompts_are_for_powerpoint_only(client, db, admin, data):
    r = client.post("/reports/plan", headers=auth(admin), json={"prompt": "everything", "format": "xlsx", "product_ids": [data["flakes"].id]})
    assert r.status_code == 422


def test_an_empty_prompt_is_the_standard_report(client, db, admin, data):
    r = client.post("/reports/plan", headers=auth(admin), json={"prompt": "", "format": "pptx", "product_ids": [data["flakes"].id], "round": 1})
    body = r.json()
    assert body["planned_by"] == "standard" and body["plan"]["rounds"] == [1] and body["surveys"] == 2
    assert len(body["plan"]["sections"]) >= 10


def test_an_ai_plan_is_checked_against_what_exists(client, db, admin, data, monkeypatch):
    monkeypatch.setattr("app.services.custom_reports._llm_plan", lambda *a, **k: {
        "product_ids": [data["flakes"].id, 999999], "rounds": "latest",
        "filters": {"cities": ["Atlantis"], "respondent_types": ["retailer"]}, "sections": ["kpis", "made_up"], "max_slides": 500})
    r = client.post("/reports/plan", headers=auth(admin), json={"prompt": "anything", "format": "pptx", "product_ids": [data["flakes"].id]})
    body = r.json()
    assert body["planned_by"] == "ai" and body["plan"]["product_ids"] == [data["flakes"].id]
    assert body["plan"]["filters"]["cities"] == [] and body["plan"]["filters"]["respondent_types"] == ["Retailer"]
    assert body["plan"]["sections"] == ["kpis"] and body["plan"]["max_slides"] == 40
    assert any("Atlantis" in w for w in body["warnings"])


def test_access(client, db, admin, data):
    person = make_user(db)
    assert client.post("/reports/plan", headers=auth(person), json={"prompt": "", "format": "pptx", "product_ids": [data["flakes"].id]}).status_code == 403
    r = client.post("/reports/plan", headers=auth(admin), json={"prompt": "", "format": "pptx", "product_ids": [data["flakes"].id]})
    other = make_user(db, role="admin")
    assert client.get(f"/reports/file/{r.json()['request_id']}", headers=auth(other)).status_code == 404
    assert client.get("/reports/options", headers=auth(admin)).json()["products"]