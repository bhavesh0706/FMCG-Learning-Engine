"""
Next update requirements (PDF): recipe costing, standards, attachments, Recipe Master,
new question types, respondent details and masters, survey form approval, report
breakdowns, Excel / PowerPoint downloads, and the report review chain.
Everything goes through the real API.
"""

import io

from conftest import _unique, auth, make_user, open_round, start_survey

from app.api.product_feedback import build_round_report
from app.api.video import save_feedback_record
from app.database.models import Product, SurveyQuestion, SurveyResponse

RECIPE = {"batch_size": "10 kg", "ingredients": [
    {"name": "Cocoa mass", "quantity": "6", "unit": "kg", "cost_per_kg": 500},
    {"name": "Sugar", "quantity": "3", "unit": "kg", "cost_per_kg": 45},
    {"name": "Cocoa butter", "quantity": "1000", "unit": "g", "cost_per_kg": 800},
]}


# ---------- 1. Recipe costing ----------

def test_cost_in_use_is_cost_per_kg_times_usage(client, db, admin, product):
    org = make_user(db, role="client")
    assert client.put(f"/products/{product.id}/recipe", headers=auth(org), json=RECIPE).status_code == 200
    c = client.get(f"/products/{product.id}/recipe", headers=auth(admin)).json()["costing"]
    rows = {r["name"]: r for r in c["ingredients"]}
    assert rows["Cocoa mass"]["usage_pct"] == 60.0 and rows["Cocoa mass"]["cost_in_use"] == 300.0  # 500 x 60%
    assert rows["Sugar"]["cost_in_use"] == 13.5 and rows["Cocoa butter"]["cost_in_use"] == 80.0
    assert c["cost_per_kg"] == 393.5 and c["batch_kg"] == 10.0 and c["batch_cost"] == 3935.0 and c["complete"] is True


# ---------- 2. Attachments and Steps ----------

def test_recipe_attachments(client, db, admin, product):
    org = make_user(db, role="client")
    files = {"file": ("process.pdf", b"%PDF-1.4 test", "application/pdf")}
    up = client.post(f"/products/{product.id}/recipe/attachments", headers=auth(org), files=files)
    assert up.status_code == 201, up.text
    aid = up.json()["id"]
    assert client.post(f"/products/{product.id}/recipe/attachments", headers=auth(admin), files=files).status_code == 403
    bad = client.post(f"/products/{product.id}/recipe/attachments", headers=auth(org), files={"file": ("x.exe", b"MZ", "application/octet-stream")})
    assert bad.status_code == 400
    listed = client.get(f"/products/{product.id}/recipe", headers=auth(admin)).json()["attachments"]
    assert [a["filename"] for a in listed] == ["process.pdf"]
    got = client.get(f"/recipe-attachments/{aid}/file", headers=auth(admin))
    assert got.status_code == 200 and got.content == b"%PDF-1.4 test"
    assert client.delete(f"/recipe-attachments/{aid}", headers=auth(org)).status_code == 200


# ---------- 3. Standards and the Recipe Master ----------

def test_standards_validate_recipes_and_show_in_the_recipe_master(client, db, admin, product):
    org = make_user(db, role="client")
    client.put(f"/products/{product.id}/recipe", headers=auth(org), json=RECIPE)
    rules = [
        {"name": "Contains cocoa", "kind": "required_ingredient", "ingredient": "cocoa"},
        {"name": "Sugar at most 25%", "kind": "ingredient_share", "ingredient": "sugar", "max_value": 25},
        {"name": "Cost per kg at most 500", "kind": "max_cost_per_kg", "max_value": 500},
        {"name": "Every ingredient costed", "kind": "costs_complete"},
    ]
    ids = [client.post("/recipe-standards", headers=auth(org), json=r).json()["id"] for r in rules]
    assert client.post("/recipe-standards", headers=auth(admin), json=rules[0]).status_code == 403  # Super Admin only
    assert client.post("/recipe-standards", headers=auth(org), json={"name": "Bad rule", "kind": "ingredient_share", "ingredient": "x"}).status_code == 400
    v = client.get(f"/products/{product.id}/recipe", headers=auth(admin)).json()["standards"]
    by_name = {r["name"]: r for r in v["results"]}
    assert by_name["Contains cocoa"]["passed"] and by_name["Every ingredient costed"]["passed"]
    assert not by_name["Sugar at most 25%"]["passed"] and "30.0%" in by_name["Sugar at most 25%"]["detail"]
    assert by_name["Cost per kg at most 500"]["passed"] and v["passed"] is False and v["failed_count"] == 1
    master = client.get("/recipes", headers=auth(org)).json()["recipes"]
    row = next(r for r in master if r["product_id"] == product.id)
    assert row["cost_per_kg"] == 393.5 and row["standards"]["failed_count"] == 1
    person = make_user(db)
    assert client.get("/recipes", headers=auth(person)).status_code == 403
    for i in ids:
        client.delete(f"/recipe-standards/{i}", headers=auth(org))


# ---------- 4 and 5. Survey types, respondent details, masters ----------

def test_masters_have_defaults_and_admins_manage_them(client, db, admin):
    person = make_user(db)
    m = client.get("/masters", headers=auth(person)).json()
    assert "SEC A" in [v["name"] for v in m["sec_class"]] and "Retailer" in [v["name"] for v in m["respondent_type"]]
    assert "Homemaker" in [v["name"] for v in m["occupation"]]
    name = _unique("Chef ")
    assert client.post("/admin/masters/occupation", headers=auth(admin), json={"name": name}).status_code == 201
    assert client.post("/admin/masters/occupation", headers=auth(person), json={"name": "Nope"}).status_code == 403
    assert name in [v["name"] for v in client.get("/masters", headers=auth(person)).json()["occupation"]]


def _with_new_questions(db, product):
    multi = SurveyQuestion(product_id=product.id, position=6, text="What do you like?", question_type="multiple_choice",
                           options=["Taste", "Price", "Pack"], required=0, is_active=1)
    pref = SurveyQuestion(product_id=product.id, position=7, text="Which sample do you prefer?", question_type="preference",
                          options=["Sample A", "Sample B"], required=0, is_active=1)
    db.add_all([multi, pref])
    db.commit()
    return multi, pref


def test_new_question_types_respondent_details_and_breakdowns(client, db, admin, product):
    org = make_user(db, role="client")
    made = client.post(f"/products/{product.id}/questions", headers=auth(org),
                       json={"text": "Pick everything you liked", "question_type": "multiple_choice", "options": ["A", "B", "C"]})
    assert made.status_code == 201
    pref = client.post(f"/products/{product.id}/questions", headers=auth(org), json={"text": "Which sample is better?", "question_type": "preference"})
    assert pref.status_code == 201 and pref.json()["options"] == ["Sample A", "Sample B"]
    for q in (made.json()["id"], pref.json()["id"]):
        client.delete(f"/questions/{q}", headers=auth(org))
    db.expire_all()  # the API changed the product in another session
    db.get(Product, product.id).questions_status = "approved"
    db.commit()
    multi, pref_q = _with_new_questions(db, product)
    person = make_user(db)
    rid = client.post("/surveys/respondents", headers=auth(person), json={
        "name": "Riya", "location": "Pune", "country": "India", "occupation": "Homemaker", "sec_class": "SEC B",
        "respondent_type": "Retailer", "consent_given": True}).json()
    assert rid["country"] == "India" and rid["sec_class"] == "SEC B" and rid["respondent_type"] == "Retailer"
    rnd = open_round(db, product)
    from conftest import assign

    assign(db, product, person)
    r = client.post("/surveys/responses", headers=auth(person), json={"product_id": product.id, "respondent_id": rid["id"], "audience": "internal"})
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    bad = client.put(f"/surveys/responses/{sid}/answers", headers=auth(person), json={"answers": [{"question_id": multi.id, "value": "Taste | Colour"}]})
    assert bad.status_code == 400
    ok = client.put(f"/surveys/responses/{sid}/answers", headers=auth(person), json={"answers": [
        {"question_id": multi.id, "value": "price|Taste"}, {"question_id": pref_q.id, "value": "Sample B"}]})
    assert ok.status_code == 200, ok.text
    stored = {a["question_id"]: a["value"] for a in ok.json()["answers"]}
    assert stored[multi.id] == "Price | Taste" and stored[pref_q.id] == "Sample B"
    save_feedback_record("a.m4a", __file__, 30, 1, 4.0, f"The customer liked the {product.name}.", f"{product.name} is nice", [], response_id=sid)
    db.expire_all()
    build_round_report(db, rnd.id)  # the analysis worker does this after every analysis
    from app.database.models import ConsolidatedReport

    rep = db.query(ConsolidatedReport).filter(ConsolidatedReport.round_id == rnd.id).order_by(ConsolidatedReport.id.desc()).first()
    b = rep.stats["breakdowns"]
    assert {"group": "Retailer"}.items() <= b["respondent_type"][0].items() and b["audience"][0]["group"] == "Internal"
    assert b["country"][0]["group"] == "India" and b["sec_class"][0]["group"] == "SEC B"
    qs = {q["question"]: q for q in rep.stats["questions"]}
    assert qs["What do you like?"]["distribution"] == {"Price": 1, "Taste": 1}
    assert qs["Which sample do you prefer?"]["distribution"] == {"Sample B": 1}
    # 6 and 7. Excel and PowerPoint downloads of the same report
    x = client.get(f"/product-feedback/{product.id}/export.xlsx", headers=auth(admin))
    assert x.status_code == 200, x.text
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(x.content))
    assert wb.sheetnames == ["Summary", "Surveys", "Questions", "Breakdowns", "Findings"]
    survey_row = [c.value for c in wb["Surveys"][2]]
    assert survey_row[0] == sid and "Retailer" in survey_row and "Internal" in survey_row and "Price | Taste" in survey_row
    assert any(row[0].value == "Respondent type" and row[1].value == "Retailer" for row in wb["Breakdowns"].iter_rows(min_row=2))
    p = client.get(f"/product-feedback/{product.id}/export.pptx", headers=auth(org))
    assert p.status_code == 200
    from pptx import Presentation

    deck = Presentation(io.BytesIO(p.content))
    titles = [s.shapes[0].text_frame.text for s in deck.slides]
    assert product.name in titles[0] and "Key results" in titles and "By respondent type" in titles
    assert client.get(f"/product-feedback/{product.id}/export.xlsx", headers=auth(person)).status_code == 403


# ---------- 4. Survey form approval ----------

def test_survey_forms_need_admin_approval(client, db, admin):
    org = make_user(db, role="client")
    cid = client.post("/categories", headers=auth(org), json={"name": _unique("Snacks ")}).json()["id"]
    pid = client.post("/products", headers=auth(org), json={"category_id": cid, "name": _unique("Chips "), "min_responses": 1}).json()["id"]
    for i in range(5):
        client.post(f"/products/{pid}/questions", headers=auth(org), json={"text": f"Question {i}?", "question_type": "text"})
    assert client.put(f"/products/{pid}/recipe", headers=auth(org), json=RECIPE).status_code == 200
    assert client.get(f"/products/{pid}", headers=auth(org)).json()["questions_status"] == "pending"
    pending = client.get("/admin/survey-forms", headers=auth(admin)).json()
    assert any(f["product_id"] == pid for f in pending["forms"]) and pending["counts"]["pending"] >= 1
    started = client.post(f"/org/products/{pid}/rounds", headers=auth(org), json={"target": 5})
    assert started.status_code == 201, started.text
    person = make_user(db)
    assigned = client.put(f"/admin/products/{pid}/executives", headers=auth(admin), json={"user_ids": [person.id]})
    assert assigned.status_code == 200, assigned.text
    ids = lambda: {p["id"] for p in client.get("/products", headers=auth(person)).json()["products"]}  # noqa: E731
    assert pid not in ids(), "not usable before approval"
    assert client.post(f"/admin/products/{pid}/survey-form/return", headers=auth(admin), json={}).status_code == 400  # note required
    assert client.post(f"/admin/products/{pid}/survey-form/return", headers=auth(admin), json={"note": "Q3 unclear"}).json()["status"] == "returned"
    assert pid not in ids()
    ok = client.post(f"/admin/products/{pid}/survey-form/approve", headers=auth(admin), json={})
    assert ok.status_code == 200 and ok.json()["status"] == "approved" and ok.json()["reviewed_by"] == admin.name
    assert pid in ids()
    client.post(f"/products/{pid}/questions", headers=auth(org), json={"text": "One more question?", "question_type": "text"})
    assert pid not in ids(), "a change needs approval again"


# ---------- 9. Report review chain ----------

def test_report_review_chain_and_super_admin_visibility(client, db, admin, product):
    person, other = make_user(db), make_user(db)
    sid = start_survey(client, person, product)["response_id"]
    assert client.post(f"/surveys/responses/{sid}/report/submit", headers=auth(person)).status_code == 400  # not analyzed yet
    save_feedback_record("r.m4a", __file__, 30, 1, 3.5, "Fine.", "It was fine.", [], response_id=sid)
    db.expire_all()
    assert db.get(SurveyResponse, sid).report_status == "generated"
    assert client.post(f"/surveys/responses/{sid}/report/submit", headers=auth(other)).status_code == 403
    assert client.post(f"/surveys/responses/{sid}/report/submit", headers=auth(person)).json()["report_status"] == "pending_admin"
    org = make_user(db, role="client")
    assert client.post(f"/org/surveys/{sid}/review", headers=auth(org)).status_code == 409  # not forwarded yet
    queue = client.get("/admin/report-reviews", headers=auth(admin)).json()
    assert any(r["response_id"] == sid for r in queue["reports"])
    assert client.post(f"/admin/report-reviews/{sid}/return", headers=auth(admin), json={"note": "Add the city"}).json()["report_status"] == "returned"
    assert client.post(f"/surveys/responses/{sid}/report/submit", headers=auth(person)).json()["report_status"] == "pending_admin"
    fwd = client.post(f"/admin/report-reviews/{sid}/approve", headers=auth(admin), json={})
    assert fwd.json()["report_status"] == "forwarded"
    listed = client.get("/org/surveys", headers=auth(org)).json()
    row = next(r for r in listed["surveys"] if r["response_id"] == sid)
    assert row["report_status"] == "forwarded" and row["product_name"] == product.name
    detail = client.get(f"/org/surveys/{sid}", headers=auth(org)).json()
    assert detail["analysis"]["summary"] == "Fine." and "mobile" not in detail
    done = client.post(f"/org/surveys/{sid}/review", headers=auth(org), json={"note": "Seen"})
    assert done.json()["report_status"] == "reviewed"
    steps = [h["to"] for h in done.json()["history"]]
    assert steps == ["pending_admin", "returned", "pending_admin", "forwarded", "reviewed"]
    assert client.get("/org/surveys", headers=auth(person)).status_code == 403
    assert client.get(f"/surveys/responses/{sid}/report/history", headers=auth(person)).json()["report_status"] == "reviewed"


def test_downloads_open_with_a_token_link_for_the_mobile_app(client, db, admin, product):
    """The app opens downloads in the phone's browser: export and attachment links accept ?token=."""
    org = make_user(db, role="client")
    up = client.post(f"/products/{product.id}/recipe/attachments", headers=auth(org), files={"file": ("a.pdf", b"%PDF", "application/pdf")})
    token = auth(admin)["Authorization"].removeprefix("Bearer ")
    assert client.get(f"/recipe-attachments/{up.json()['id']}/file?token={token}").status_code == 200
    assert client.get(f"/recipes?token={token}").status_code == 401, "other routes still need the header"
