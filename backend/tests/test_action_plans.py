"""Recipe plans and action plans: planning per product, several plans per round,
team members allocated to cities (enforced), and plans carried to new recipe versions."""

from conftest import start_project, staff_project  # noqa: F401
from conftest import _unique, auth, make_user

from app.database.models import ActionPlan, ProductAssignment, SurveyResponse

RECIPE = {"batch_size": "100 kg", "ingredients": [{"name": "Curd", "quantity": "80", "unit": "kg"}, {"name": "Mango", "quantity": "20", "unit": "kg"}]}
PLAN = {"target": 20, "persons_needed": 2, "start_date": "2026-12-01", "end_date": "2026-12-20", "cities": ["Delhi", "Mumbai"],
        "audience": "external", "notes": "Test in modern trade stores"}


def _project(client, org, **extra):
    return client.post("/categories", headers=auth(org), json={"name": _unique("Dairy "), **extra}).json()["id"]


def _product(client, org, cid, **extra):
    r = client.post("/products", headers=auth(org), json={"category_id": cid, "name": _unique("Lassi "), **extra})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _ready(client, org, pid):
    client.put(f"/products/{pid}/recipe", headers=auth(org), json=RECIPE)
    for text in ("How is the taste?", "How is the sweetness?", "How is the texture?", "How is the aroma?", "Would you buy it again?"):
        client.post(f"/products/{pid}/questions", headers=auth(org), json={"text": text, "question_type": "rating"})  # surveys need 5


def _survey(client, person, pid, city):
    rid = client.post("/surveys/respondents", headers=auth(person),
                      json={"name": "Customer", "consent_given": True, "country": "India", "city": city}).json()["id"]
    return client.post("/surveys/responses", headers=auth(person), json={"product_id": pid, "respondent_id": rid})


def test_a_product_gets_its_recipe_plan_or_its_projects_default(client, db):
    org = make_user(db, role="client")
    pid = _product(client, org, _project(client, org), recipe_plan=PLAN)
    plan = client.get(f"/products/{pid}/recipe-plan", headers=auth(org)).json()["recipe_plan"]
    assert plan["target"] == 20 and plan["persons_needed"] == 2 and plan["cities"] == ["Delhi", "Mumbai"] and plan["end_date"] == "2026-12-20"
    cid = _project(client, org, default_plan={"target": 12, "cities": ["Pune"]})
    assert client.get(f"/products/{_product(client, org, cid)}/recipe-plan", headers=auth(org)).json()["recipe_plan"]["cities"] == ["Pune"]
    bad = client.post("/products", headers=auth(org), json={"category_id": cid, "name": _unique("X "),
                                                           "recipe_plan": {"target": 5, "start_date": "2026-12-10", "end_date": "2026-12-01"}})
    assert bad.status_code == 400 and "before the start date" in bad.json()["detail"]


def test_several_action_plans_with_their_own_teams_and_cities(client, db, admin):
    org, a, b = make_user(db, role="client"), make_user(db), make_user(db)
    pid = _product(client, org, _project(client, org), recipe_plan=PLAN)
    _ready(client, org, pid)
    client.post(f"/org/products/{pid}/rounds", headers=auth(org), json={})
    plans = client.get(f"/products/{pid}/action-plans", headers=auth(org)).json()
    assert [p["name"] for p in plans["plans"]] == ["Main plan"] and plans["plans"][0]["target"] == 20, "the round starts from the recipe plan"
    main = plans["plans"][0]["id"]
    client.put(f"/action-plans/{main}", headers=auth(org), json={"name": "Delhi team", "target": 10, "cities": ["Delhi"],
                                                                "start_date": "2026-12-01", "end_date": "2026-12-10"})
    mumbai = client.post(f"/products/{pid}/action-plans", headers=auth(org),
                         json={"name": "Mumbai team", "target": 8, "persons_needed": 1, "cities": ["Mumbai"], "end_date": "2026-12-31"})
    assert mumbai.status_code == 201
    mumbai = mumbai.json()["plan"]["id"]
    assert client.post(f"/products/{pid}/action-plans", headers=auth(admin), json={"name": "X", "target": 1}).status_code == 403
    assert client.put(f"/admin/action-plans/{main}/members", headers=auth(org), json={"members": []}).status_code == 403
    # Admin allocates: A -> Delhi, B -> Mumbai
    assert client.put(f"/admin/action-plans/{main}/members", headers=auth(admin), json={"members": [{"user_id": a.id, "cities": ["delhi"]}]}).status_code == 200
    assert client.put(f"/admin/action-plans/{mumbai}/members", headers=auth(admin), json={"members": [{"user_id": b.id}]}).status_code == 200
    outside = client.put(f"/admin/action-plans/{main}/members", headers=auth(admin), json={"members": [{"user_id": a.id, "cities": ["Pune"]}]})
    assert outside.status_code == 400 and "not a city of" in outside.json()["detail"]
    view = client.get(f"/products/{pid}/action-plans", headers=auth(admin)).json()
    assert view["round"]["target"] == 18, "the round's target is the sum of its plans"
    assert {p["name"]: [m["cities"] for m in p["members"]] for p in view["plans"]} == {"Delhi team": [["Delhi"]], "Mumbai team": [["Mumbai"]]}
    assert {x.user_id for x in db.query(ProductAssignment).filter(ProductAssignment.product_id == pid)} == {a.id, b.id}, "members can survey it"
    mine = client.get("/my/action-plans", headers=auth(a)).json()["plans"]
    assert mine[0]["plan_name"] == "Delhi team" and mine[0]["cities"] == ["Delhi"]


def test_a_member_surveys_only_their_cities(client, db, admin):
    org, a = make_user(db, role="client"), make_user(db)
    pid = _product(client, org, _project(client, org), recipe_plan={"target": 10, "cities": ["Delhi", "Mumbai"]})
    _ready(client, org, pid)
    client.post(f"/org/products/{pid}/rounds", headers=auth(org), json={})
    plan = client.get(f"/products/{pid}/action-plans", headers=auth(org)).json()["plans"][0]["id"]
    client.put(f"/admin/action-plans/{plan}/members", headers=auth(admin), json={"members": [{"user_id": a.id, "cities": ["Delhi"]}]})
    blocked = _survey(client, a, pid, "Mumbai")
    assert blocked.status_code == 403 and "Delhi only" in blocked.json()["detail"] and "Mumbai" in blocked.json()["detail"]
    assert _survey(client, a, pid, "").status_code == 403, "the city is needed"
    ok = _survey(client, a, pid, "delhi")
    assert ok.status_code == 201
    assert db.get(SurveyResponse, ok.json()["id"]).action_plan_id == plan
    assert _survey(client, admin, pid, "Chennai").status_code == 201, "admins are not limited"


def test_a_new_recipe_version_copies_the_action_plans(client, db, admin):
    org, a = make_user(db, role="client"), make_user(db)
    pid = _product(client, org, _project(client, org), recipe_plan={"target": 5, "cities": ["Delhi"]})
    _ready(client, org, pid)
    first = client.post(f"/org/products/{pid}/rounds", headers=auth(org), json={}).json()["round"]
    plan = client.get(f"/products/{pid}/action-plans", headers=auth(org)).json()["plans"][0]["id"]
    client.put(f"/action-plans/{plan}", headers=auth(org), json={"name": "Delhi team", "target": 5, "cities": ["Delhi"],
                                                                "start_date": "2030-11-01", "end_date": "2030-11-15"})
    client.put(f"/admin/action-plans/{plan}/members", headers=auth(admin), json={"members": [{"user_id": a.id}]})
    started = _survey(client, a, pid, "Delhi")
    assert started.status_code == 201
    db.get(SurveyResponse, started.json()["id"]).status = "submitted"  # a finished survey counts toward the plan
    db.commit()
    assert client.delete(f"/action-plans/{plan}", headers=auth(org)).status_code == 409, "a plan with surveys is kept"
    client.post(f"/org/products/{pid}/rounds/{first['number']}/end", headers=auth(org))
    client.put(f"/products/{pid}/recipe", headers=auth(org), json={**RECIPE, "batch_size": "100 kg batch v2"})
    second = client.post(f"/org/products/{pid}/rounds", headers=auth(org), json={})
    assert second.status_code in (200, 201), second.text
    new = client.get(f"/products/{pid}/action-plans", headers=auth(org)).json()
    copied = new["plans"][0]
    assert new["round"]["number"] == 2 and copied["name"] == "Delhi team" and copied["target"] == 5 and copied["copied_from_id"] == plan
    assert copied["recipe_version"] == new["round"]["recipe_version"] != first["recipe_version"]
    assert [m["user_id"] for m in copied["members"]] == [a.id] and copied["members"][0]["cities"] == ["Delhi"]
    old = client.get(f"/products/{pid}/action-plans?round=1", headers=auth(org)).json()
    assert old["plans"][0]["recipe_version"] == first["recipe_version"] and old["plans"][0]["done"] == 1 and not old["can_edit"]
    assert client.put(f"/action-plans/{plan}", headers=auth(org), json={"name": "X", "target": 1}).status_code == 409, "history stays"
    assert db.query(ActionPlan).filter(ActionPlan.product_id == pid).count() == 2


def test_the_report_lists_each_plans_team_and_their_surveys(client, db, admin):
    org, a, b = make_user(db, role="client"), make_user(db), make_user(db)
    pid = _product(client, org, _project(client, org), recipe_plan={"target": 10, "cities": ["Delhi"]})
    _ready(client, org, pid)
    client.post(f"/org/products/{pid}/rounds", headers=auth(org), json={})
    delhi = client.get(f"/products/{pid}/action-plans", headers=auth(org)).json()["plans"][0]["id"]
    client.put(f"/action-plans/{delhi}", headers=auth(org), json={"name": "Main plan", "target": 6, "cities": ["Delhi"]})
    mumbai = client.post(f"/products/{pid}/action-plans", headers=auth(org), json={"name": "Mumbai team", "target": 4, "cities": ["Mumbai"]}).json()["plan"]["id"]
    client.put(f"/admin/action-plans/{delhi}/members", headers=auth(admin), json={"members": [{"user_id": a.id}]})
    client.put(f"/admin/action-plans/{mumbai}/members", headers=auth(admin), json={"members": [{"user_id": b.id}]})
    assert _survey(client, a, pid, "Delhi").status_code == 201
    assert _survey(client, a, pid, "Delhi").status_code == 201
    assert _survey(client, b, pid, "Mumbai").status_code == 201
    assert _survey(client, admin, pid, "Pune").status_code == 201  # an admin: belongs to no plan
    r = client.get(f"/admin/products/{pid}/plan-surveys", headers=auth(admin))
    assert r.status_code == 200, r.text
    d = r.json()
    plans = {p["name"]: p for p in d["plans"]}
    assert set(plans) == {"Main plan", "Mumbai team"} and d["round"]["number"] == 1
    assert [(m["user_id"], len(m["surveys"])) for m in plans["Main plan"]["members"]] == [(a.id, 2)]
    assert [(m["user_id"], len(m["surveys"])) for m in plans["Mumbai team"]["members"]] == [(b.id, 1)]
    one = plans["Main plan"]["members"][0]["surveys"][0]
    assert one["city"] == "Delhi" and one["status_label"] == "Not finished" and one["feedback_id"] is None
    assert plans["Main plan"]["survey_count"] == 2 and len(d["unplanned"]) == 1 and d["unplanned"][0]["city"] == "Pune"
    assert client.get(f"/admin/products/{pid}/plan-surveys?round=1", headers=auth(admin)).status_code == 200
    for who in (org, a):
        assert client.get(f"/admin/products/{pid}/plan-surveys", headers=auth(who)).status_code == 403



def test_a_survey_person_can_work_in_several_plans_of_a_survey(client, db, admin):
    """E.g. a person in "Main plan" (Greater Noida) is also sent to "Hyd team" (Hyderabad):
    each survey counts in the plan of the customer's city; other cities are refused."""
    org = make_user(db, role="client")
    a = make_user(db, name=_unique("Ishaan "))
    pid = _product(client, org, _project(client, org), recipe_plan=PLAN)
    _ready(client, org, pid)
    client.post(f"/org/products/{pid}/rounds", headers=auth(org), json={})
    main = client.get(f"/products/{pid}/action-plans", headers=auth(org)).json()["plans"][0]["id"]
    client.put(f"/action-plans/{main}", headers=auth(org), json={"name": "Main plan", "target": 10, "cities": ["Greater Noida"]})
    hyd = client.post(f"/products/{pid}/action-plans", headers=auth(org), json={"name": "Hyd team", "target": 5, "cities": ["Hyderabad"]}).json()["plan"]["id"]
    assert client.put(f"/admin/action-plans/{main}/members", headers=auth(admin), json={"members": [{"user_id": a.id}]}).status_code == 200
    both = client.put(f"/admin/action-plans/{hyd}/members", headers=auth(admin), json={"members": [{"user_id": a.id}]})
    assert both.status_code == 200, both.text

    mine = client.get("/my/action-plans", headers=auth(a)).json()["plans"][0]
    assert mine["cities"] == ["Greater Noida", "Hyderabad"] and mine["plan_name"] == "Main plan + Hyd team"

    def survey(city):
        rid = client.post("/surveys/respondents", headers=auth(a), json={"name": "Customer", "consent_given": True,
                                                                        "location": "Sector 1", "city": city}).json()["id"]
        return client.post("/surveys/responses", headers=auth(a), json={"product_id": pid, "respondent_id": rid})

    noida, hyderabad, pune = survey("Greater Noida"), survey("hyderabad"), survey("Pune")
    assert noida.status_code == 201 and hyderabad.status_code == 201
    assert pune.status_code == 403 and "Greater Noida, Hyderabad" in pune.json()["detail"]
    from app.database.models import SurveyResponse

    db.expire_all()
    assert db.get(SurveyResponse, noida.json()["id"]).action_plan_id == main
    assert db.get(SurveyResponse, hyderabad.json()["id"]).action_plan_id == hyd



def test_survey_plans_never_add_up_to_more_than_the_projects_target(client, db):
    """The project's response target (set when it is created) caps its survey plans; raise it to plan more."""
    org = make_user(db, role="client")
    cid = _project(client, org)
    pid = _product(client, org, cid, recipe_plan={"target": 30, "cities": ["Delhi"]})
    _ready(client, org, pid)
    made = client.post("/org/projects", headers=auth(org), json={"project_name": "Lassi study", "category_id": cid, "product_id": pid})
    assert made.json()["total_target"] == 30, "defaults to the product's planned responses"
    proj = made.json()["id"]
    for bad in (5.5, -5, 0, "5.5", "+5", "abc", "1e2", True):
        r = client.put(f"/org/projects/{proj}/target", headers=auth(org), json={"total_target": bad})
        assert r.status_code == 400 and "whole number" in r.json()["detail"], (bad, r.text)
    first = client.post(f"/org/projects/{proj}/plans", headers=auth(org), json={"name": "Delhi team", "target": 20})
    assert first.status_code == 201 and first.json()["total_target"] == 30 and first.json()["planned"] == 20
    for bad in (5.5, -5, 0, "7.25", "x"):
        r = client.post(f"/org/projects/{proj}/plans", headers=auth(org), json={"name": "Bad", "target": bad})
        assert r.status_code == 400 and "whole number" in r.json()["detail"], (bad, r.text)
    over = client.post(f"/org/projects/{proj}/plans", headers=auth(org), json={"name": "Hyd team", "target": 90})
    assert over.status_code == 409 and "at most 10" in over.json()["detail"] and "raise the target first" in over.json()["detail"]
    ok = client.post(f"/org/projects/{proj}/plans", headers=auth(org), json={"name": "Hyd team", "target": 10}).json()
    hyd = next(p["id"] for p in ok["plans"] if p["name"] == "Hyd team")
    assert client.put(f"/action-plans/{hyd}", headers=auth(org), json={"name": "Hyd team", "target": 11}).status_code == 409
    assert client.put(f"/action-plans/{hyd}", headers=auth(org), json={"name": "Hyd team", "target": 8}).status_code == 200

    # Never below what the plans already commit (28); raising it lets a plan grow.
    low = client.put(f"/org/projects/{proj}/target", headers=auth(org), json={"total_target": 27})
    assert low.status_code == 409 and "can't go below 28" in low.json()["detail"]
    assert client.put(f"/org/projects/{proj}/target", headers=auth(org), json={"total_target": 100}).json()["total_target"] == 100
    assert client.put(f"/action-plans/{hyd}", headers=auth(org), json={"name": "Hyd team", "target": 80}).status_code == 200
    started = start_project(client, db, proj, auth(org), {})
    assert started.status_code == 200 and started.json()["round"]["target"] == 100
    # While the survey runs the same cap applies; an Admin can't change the target.
    late = client.post(f"/org/projects/{proj}/plans", headers=auth(org), json={"name": "Extra", "target": 1})
    assert late.status_code == 409 and "at most 0" in late.json()["detail"]
    admin = make_user(db, role="admin")
    assert client.put(f"/org/projects/{proj}/target", headers=auth(admin), json={"total_target": 200}).status_code == 403
