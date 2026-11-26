"""Projects (Super Admin): one project per product VERSION, made by hand on the Add project page
(never automatically), status from the survey rounds of that version, linked to the catalog."""

import pytest

from conftest import open_round, start_project, staff_project  # noqa: F401
from conftest import _unique, auth, make_user

from app.database.models import Product, Project, RecipeVersion

# Written for "every recipe change is a new version" (see conftest.legacy_recipe_versions).
pytestmark = pytest.mark.usefixtures("legacy_recipe_versions")

RECIPE = {"batch_size": "100 kg", "ingredients": [{"name": "Water", "quantity": "100", "unit": "kg"}]}
SWEETER = {"batch_size": "100 kg", "ingredients": [{"name": "Water", "quantity": "98", "unit": "kg"},
                                                   {"name": "Sugar", "quantity": "2", "unit": "kg"}]}


def _setup(client, org, recipe=True, project=True):
    cid = client.post("/categories", headers=auth(org), json={"name": _unique("Beverages ")}).json()["id"]
    other = client.post("/categories", headers=auth(org), json={"name": _unique("Snacks ")}).json()["id"]
    pid = client.post("/products", headers=auth(org), json={"category_id": cid, "name": _unique("VitaBoost ")}).json()["id"]
    if recipe:
        client.put(f"/products/{pid}/recipe", headers=auth(org), json=RECIPE)
    for t in ("Taste?", "Sweetness?", "Mint?", "Pack?", "Buy again?"):
        client.post(f"/products/{pid}/questions", headers=auth(org), json={"text": t, "question_type": "rating"})
    if project:
        _add(client, org, cid, pid, 1)
    return cid, other, pid


def _add(client, org, cid, pid, version):
    """Projects > Add project for this product version."""
    r = client.post("/org/projects", headers=auth(org), json={"project_name": f"Study v{version}", "category_id": cid,
                                                              "product_id": pid, "product_version": version})
    assert r.status_code == 201, r.text
    return r.json()


def _projects(db, pid):
    db.expire_all()
    return db.query(Project).filter(Project.product_id == pid).order_by(Project.product_version).all()


def test_no_project_is_made_automatically_it_is_added_by_hand(client, db):
    org = make_user(db, role="client")
    cid, _, pid = _setup(client, org, recipe=False, project=False)
    assert _projects(db, pid) == [], "a new product has no project until one is added"
    client.put(f"/products/{pid}/recipe", headers=auth(org), json=RECIPE)
    assert client.put(f"/products/{pid}/recipe", headers=auth(org), json=SWEETER).json()["version"] == 2
    assert _projects(db, pid) == [], "saving recipes (new versions) makes no project either"
    options = client.get("/org/projects/options", headers=auth(org)).json()["categories"]
    assert [p["versions"] for c in options for p in c["products"] if p["id"] == pid] == [[1, 2]]

    made = _add(client, org, cid, pid, 1)
    [p] = _projects(db, pid)
    assert p.id == made["id"] and p.product_version == 1 and p.category_id == cid and p.project_code.startswith("PRJ-")
    assert p.created_by == org.id
    body = client.get(f"/org/projects/{p.id}", headers=auth(org)).json()
    assert body["product_version"] == 1 and body["version_label"] == "v1" and body["is_latest_version"] is False
    assert body["status"] == "draft" and body["category"]["id"] == cid and body["product"]["id"] == pid
    options = client.get("/org/projects/options", headers=auth(org)).json()["categories"]
    assert [p["versions"] for c in options for p in c["products"] if p["id"] == pid] == [[2]]


def test_each_version_can_have_its_own_project(client, db):
    org = make_user(db, role="client")
    cid, _, pid = _setup(client, org)
    assert client.put(f"/products/{pid}/recipe", headers=auth(org), json=SWEETER).json()["version"] == 2
    assert [x.product_version for x in _projects(db, pid)] == [1]
    _add(client, org, cid, pid, 2)
    v1, v2 = _projects(db, pid)
    assert (v1.product_version, v2.product_version) == (1, 2) and v1.project_code != v2.project_code
    one, two = (client.get(f"/org/projects/{x.id}", headers=auth(org)).json() for x in (v1, v2))
    assert one["is_latest_version"] is False and two["is_latest_version"] is True and two["latest_version"] == 2
    assert one["linked"]["recipe_ingredients"] == 1 and two["linked"]["recipe_ingredients"] == 2, "each shows its own recipe"
    assert [v["product_version"] for v in two["versions"]] == [1, 2]
    # The version history shows each version's project.
    hist = client.get(f"/products/{pid}/recipe/versions", headers=auth(org)).json()["versions"]
    assert {h["version"]: h["project"]["project_code"] for h in hist} == {1: v1.project_code, 2: v2.project_code}


def test_status_follows_the_rounds_of_the_projects_own_version(client, db):
    org = make_user(db, role="client")
    cid, _, pid = _setup(client, org)
    [v1] = _projects(db, pid)
    open_round(db, db.get(Product, pid), number=1, recipe_version=1, target=3)
    now = client.get(f"/org/projects/{v1.id}", headers=auth(org)).json()
    assert now["status"] == "ongoing" and now["live_round"]["number"] == 1 and now["live_round"]["target"] == 3
    assert now["linked"]["questions"] == 5
    assert client.post(f"/org/products/{pid}/rounds/1/end", headers=auth(org)).status_code == 200
    client.put(f"/products/{pid}/recipe", headers=auth(org), json=SWEETER)  # version 2 after the survey
    _add(client, org, cid, pid, 2)
    _, v2 = _projects(db, pid)
    assert client.get(f"/org/projects/{v1.id}", headers=auth(org)).json()["status"] == "completed"
    assert client.get(f"/org/projects/{v2.id}", headers=auth(org)).json()["status"] == "draft"
    open_round(db, db.get(Product, pid), number=2, recipe_version=2, target=4)
    two = client.get(f"/org/projects/{v2.id}", headers=auth(org)).json()
    one = client.get(f"/org/projects/{v1.id}", headers=auth(org)).json()
    assert two["status"] == "ongoing" and two["live_round"]["number"] == 2 and two["rounds"] == 1
    assert one["status"] == "completed" and one["rounds"] == 1, "version 1 keeps only its own survey"
    k = client.get("/org/projects", headers=auth(org)).json()["kpis"]
    assert k["total"] == k["draft"] + k["ongoing"] + k["completed"]


def test_a_version_deleted_by_mistake_takes_its_draft_project_and_editing_in_place_adds_none(client, db):
    org = make_user(db, role="client")
    cid, _, pid = _setup(client, org)
    client.put(f"/products/{pid}/recipe", headers=auth(org), json=SWEETER)
    _add(client, org, cid, pid, 2)
    assert [p.product_version for p in _projects(db, pid)] == [1, 2]
    assert client.delete(f"/products/{pid}/recipe/versions/2", headers=auth(org)).status_code == 200
    assert [p.product_version for p in _projects(db, pid)] == [1]
    # While v1's survey collects, its recipe can't change: no new version, no new project.
    open_round(db, db.get(Product, pid), number=1, recipe_version=1, target=3)
    r = client.put(f"/products/{pid}/recipe", headers=auth(org), json=SWEETER)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "survey_ongoing"
    assert [p.product_version for p in _projects(db, pid)] == [1]


def test_add_project_checks_the_category_version_and_duplicates(client, db):
    org = make_user(db, role="client")
    cid, other, pid = _setup(client, org)
    [v1] = _projects(db, pid)
    wrong = client.post("/org/projects", headers=auth(org), json={"project_name": "Wrong", "category_id": other, "product_id": pid})
    assert wrong.status_code == 400 and "is not in the category" in wrong.json()["detail"]
    again = client.post("/org/projects", headers=auth(org), json={"project_name": "Second", "category_id": cid, "product_id": pid})
    assert again.status_code == 409 and "v1 already has a project" in again.json()["detail"]
    options = client.get("/org/projects/options", headers=auth(org)).json()["categories"]
    assert pid not in [p["id"] for c in options for p in c["products"]], "every version has a project"

    assert client.delete(f"/org/projects/{v1.id}", headers=auth(org)).status_code == 200
    options = client.get("/org/projects/options", headers=auth(org)).json()["categories"]
    assert [p["versions"] for c in options for p in c["products"] if p["id"] == pid] == [[1]]
    bad = client.post("/org/projects", headers=auth(org), json={"project_name": "Version seven", "category_id": cid, "product_id": pid,
                                                                "product_version": 7})
    assert bad.status_code == 400 and "no version 7" in bad.json()["detail"]
    made = client.post("/org/projects", headers=auth(org), json={"project_name": "Lemon mint launch", "description": "Delhi",
                                                                 "category_id": cid, "product_id": pid})
    assert made.status_code == 201, made.text
    assert made.json()["product_version"] == 1 and made.json()["created_by"] == org.name


def test_edit_and_only_the_super_admin(client, db, admin):
    org = make_user(db, role="client")
    _, _, pid = _setup(client, org)
    [v1] = _projects(db, pid)
    r = client.put(f"/org/projects/{v1.id}", headers=auth(org), json={"project_name": "Study 2", "description": "New text"})
    assert r.json()["project_name"] == "Study 2" and r.json()["mod_by"] == org.name and r.json()["product_version"] == 1
    for who in (admin, make_user(db)):
        assert client.get("/org/projects", headers=auth(who)).status_code == 403
    assert client.delete(f"/org/projects/{v1.id}", headers=auth(org)).status_code == 200
    assert client.get(f"/products/{pid}", headers=auth(org)).status_code == 200, "the product is unchanged"


def test_projects_follow_their_product_and_go_with_a_deleted_product(client, db):
    org = make_user(db, role="client")
    cid, other, pid = _setup(client, org)
    client.put(f"/products/{pid}/recipe", headers=auth(org), json=SWEETER)
    _add(client, org, cid, pid, 2)
    ids = [p.id for p in _projects(db, pid)]
    assert client.put(f"/products/{pid}", headers=auth(org), json={"category_id": other}).status_code == 200
    assert {client.get(f"/org/projects/{i}", headers=auth(org)).json()["category"]["id"] for i in ids} == {other}
    client.delete(f"/products/{pid}", headers=auth(org))
    r = client.delete(f"/products/{pid}/permanent", headers=auth(org))
    assert r.status_code == 200 and r.json()["removed"]["projects"] == 2
    assert _projects(db, pid) == [] and db.query(RecipeVersion).filter_by(product_id=pid).count() == 0


PLAN = {"name": "Delhi team", "target": 12, "persons_needed": 2, "cities": ["Delhi"], "audience": "external"}


def test_recipe_plan_then_start_the_survey_from_the_project(client, db):
    """Plans are made in the project before the survey; starting it attaches them."""
    from app.database.models import ActionPlan, SurveyRound

    org = make_user(db, role="client")
    _, _, pid = _setup(client, org)
    [v1] = _projects(db, pid)
    state = client.get(f"/org/projects/{v1.id}/plans", headers=auth(org)).json()
    assert state["stage"] == "draft" and state["plans"] == [] and state["can_start"] is False
    assert "at least one survey plan" in state["start_problem"]
    assert start_project(client, db, v1.id, auth(org), {}).status_code == 409

    a = client.post(f"/org/projects/{v1.id}/plans", headers=auth(org), json=PLAN)
    assert a.status_code == 201, a.text
    b = client.post(f"/org/projects/{v1.id}/plans", headers=auth(org), json={**PLAN, "name": "Pune team", "target": 8, "cities": ["Pune"]}).json()
    assert [p["name"] for p in b["plans"]] == ["Delhi team", "Pune team"] and b["target"] == 20
    assert b["can_start"] is False and "Response target" in b["start_problem"], "a response target is required"
    client.put(f"/org/projects/{v1.id}/target", headers=auth(org), json={"total_target": 20})
    c = client.get(f"/org/projects/{v1.id}/plans", headers=auth(org)).json()
    assert c["can_start"] is True, "the Super Admin doesn't wait for the Admin's team allocation"
    assert {s["key"]: s["optional"] for s in c["checklist"]}["allocation"] is True, "allocation is shown, not required"
    plan_id = b["plans"][0]["id"]
    # Planned plans can be edited and deleted before the survey starts (within the response target).
    client.put(f"/org/projects/{v1.id}/target", headers=auth(org), json={"total_target": 23})
    r = client.put(f"/action-plans/{plan_id}", headers=auth(org), json={**PLAN, "target": 15})
    assert r.status_code == 200 and r.json()["plan"]["target"] == 15 and r.json()["plan"]["round_id"] is None
    assert db.query(SurveyRound).filter_by(product_id=pid).count() == 0, "nothing starts until Start survey"

    started = start_project(client, db, v1.id, auth(org), {"note": "Launch"})
    assert started.status_code == 200, started.text
    body = started.json()
    assert body["stage"] == "live" and body["started"] == {"number": 1, "target": 23} and body["round"]["target"] == 23
    assert {p["name"] for p in body["plans"]} == {"Delhi team", "Pune team"}
    db.expire_all()
    rnd = db.query(SurveyRound).filter_by(product_id=pid).one()
    assert rnd.recipe_version == 1 and rnd.status == "live"
    assert {p.round_id for p in db.query(ActionPlan).filter_by(product_id=pid)} == {rnd.id}
    # While it runs, a new plan joins the live survey and raises its target (after raising the project's).
    client.put(f"/org/projects/{v1.id}/target", headers=auth(org), json={"total_target": 28})
    more = client.post(f"/org/projects/{v1.id}/plans", headers=auth(org), json={**PLAN, "name": "Extra", "target": 5}).json()
    assert more["round"]["target"] == 28 and len(more["plans"]) == 3
    assert client.get(f"/org/projects/{v1.id}", headers=auth(org)).json()["status"] == "ongoing"


def test_an_older_recipes_project_is_managed_on_its_own_and_admins_cannot_plan(client, db, admin):
    """Projects are independent: v1's project keeps its own plans and survey even after v2 exists."""
    org = make_user(db, role="client")
    cid, _, pid = _setup(client, org)
    client.put(f"/products/{pid}/recipe", headers=auth(org), json=SWEETER)
    _add(client, org, cid, pid, 2)
    v1, v2 = _projects(db, pid)
    old = client.get(f"/org/projects/{v1.id}/plans", headers=auth(org)).json()
    assert old["can_edit"] is True and "newer" not in (old["start_problem"] or "")
    assert client.post(f"/org/projects/{v1.id}/plans", headers=auth(org), json=PLAN).status_code == 201
    assert client.post(f"/org/projects/{v2.id}/plans", headers=auth(admin), json=PLAN).status_code == 403
    assert client.post(f"/org/projects/{v2.id}/plans", headers=auth(org), json=PLAN).status_code == 201
    started = start_project(client, db, v1.id, auth(org), {}).json()
    assert started["round"]["recipe_version"] == 1, "v1's survey tests v1's recipe"
    blocked = client.get(f"/org/projects/{v2.id}/plans", headers=auth(org)).json()
    assert blocked["can_start"] is False and "still collecting" in blocked["start_problem"], "one collecting survey per product"


def test_deleting_the_whole_project(client, db, alice):
    """A project can be deleted until consumer feedback comes in, even after its survey started;
    its survey plans have to be deleted first, then its empty survey goes with it."""
    from conftest import assign

    from app.database.models import ActionPlan, Product, ProductAssignment, SurveyRound

    org = make_user(db, role="client")
    cid, _, pid = _setup(client, org)
    [v1] = _projects(db, pid)
    plan_id = client.post(f"/org/projects/{v1.id}/plans", headers=auth(org), json=PLAN).json()["plans"][0]["id"]
    assert start_project(client, db, v1.id, auth(org), {}).status_code == 200
    assert client.get(f"/org/projects/{v1.id}", headers=auth(org)).json()["can_delete"] is True, "started, but no feedback"

    blocked = client.delete(f"/org/projects/{v1.id}", headers=auth(org))
    assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "plans_exist"
    assert "first delete its active survey plan: Delhi team. Then delete the project." in blocked.json()["detail"]["message"]
    assert client.delete(f"/action-plans/{plan_id}", headers=auth(org)).status_code == 200
    gone = client.delete(f"/org/projects/{v1.id}", headers=auth(org))
    assert gone.status_code == 200 and gone.json()["removed_surveys"] == [1], gone.text
    db.expire_all()
    assert _projects(db, pid) == [] and db.query(SurveyRound).filter_by(product_id=pid).count() == 0
    assert db.query(ActionPlan).filter_by(product_id=pid).count() == 0
    assert db.query(ProductAssignment).filter_by(product_id=pid).count() == 0, "gone from the Admin dashboard too"

    # Once a consumer response has come in, the project is kept.
    made = _add(client, org, cid, pid, 1)
    client.post(f"/org/projects/{made['id']}/plans", headers=auth(org), json={**PLAN, "cities": []})
    start_project(client, db, made['id'], auth(org), {})
    assign(db, db.get(Product, pid), alice)
    rid = client.post("/surveys/respondents", headers=auth(alice), json={"name": "Customer", "consent_given": True, "location": "Sector 18"}).json()["id"]
    assert client.post("/surveys/responses", headers=auth(alice), json={"product_id": pid, "respondent_id": rid}).status_code == 201
    assert client.get(f"/org/projects/{made['id']}", headers=auth(org)).json()["can_delete"] is False
    kept = client.delete(f"/org/projects/{made['id']}", headers=auth(org))
    assert kept.status_code == 409 and kept.json()["detail"]["code"] == "has_feedback"
    assert "1 consumer response has already come in" in kept.json()["detail"]["message"]
    db.expire_all()
    assert len(_projects(db, pid)) == 1 and db.query(SurveyRound).filter_by(product_id=pid).count() == 1


def test_product_summary_for_the_add_project_page(client, db):
    org = make_user(db, role="client")
    _, _, pid = _setup(client, org)
    client.put(f"/products/{pid}/recipe", headers=auth(org), json=SWEETER)
    s = client.get(f"/org/projects/product-summary?product_id={pid}", headers=auth(org))
    assert s.status_code == 200, s.text
    body = s.json()
    assert body["recipe"]["version"] == 2 and body["recipe"]["versions"] == [1, 2]
    assert [i["name"] for i in body["recipe"]["ingredients"]] == ["Water", "Sugar"] and body["recipe"]["ingredients"][1]["amount"] == "2 kg"
    assert body["questions"]["count"] == 5 and len(body["questions"]["sample"]) == 5, "every question (the page folds them behind +N more)"
    one = client.get(f"/org/projects/product-summary?product_id={pid}&version=1", headers=auth(org)).json()
    assert one["recipe"]["ingredient_count"] == 1, "the chosen recipe version"
    assert client.get("/org/projects/product-summary?product_id=987654", headers=auth(org)).status_code == 404
    assert client.get(f"/org/projects/product-summary?product_id={pid}", headers=auth(make_user(db))).status_code == 403


def test_a_completed_survey_reopens_for_any_target_above_what_it_collected(client, db, alice):
    """Survey 1 ended (early) with what it collected; nothing can be planned or started. Saving any
    response target above what it collected reopens Survey 1 itself; its plan matches the new target."""
    from conftest import answers_payload

    from app.database.models import ActionPlan, Product, SurveyRound

    org = make_user(db, role="client")
    _, _, pid = _setup(client, org)
    [v1] = _projects(db, pid)
    client.put(f"/org/projects/{v1.id}/target", headers=auth(org), json={"total_target": 5})
    plan = client.post(f"/org/projects/{v1.id}/plans", headers=auth(org), json={**PLAN, "target": 5, "cities": []}).json()["plans"][0]
    client.put(f"/admin/action-plans/{plan['id']}/members", headers=auth(make_user(db, role="admin")),
               json={"members": [{"user_id": alice.id, "target": 5}]})
    assert client.post(f"/org/projects/{v1.id}/start", headers=auth(org), json={}).status_code == 200
    product = db.get(Product, pid)
    for _ in range(3):  # 3 consumers answer, then the survey is ended early
        rid = client.post("/surveys/respondents", headers=auth(alice), json={"name": "Customer", "consent_given": True, "location": "Sector 18"}).json()["id"]
        resp = client.post("/surveys/responses", headers=auth(alice), json={"product_id": pid, "respondent_id": rid}).json()["id"]
        answers = answers_payload(db, product)
        for a in answers["answers"]:
            a["value"] = "4"
        client.put(f"/surveys/responses/{resp}/answers", headers=auth(alice), json=answers)
        assert client.post(f"/surveys/responses/{resp}/submit", headers=auth(alice)).status_code == 200
    assert client.post(f"/org/products/{pid}/rounds/1/end", headers=auth(org)).status_code == 200

    state = client.get(f"/org/projects/{v1.id}/plans", headers=auth(org)).json()
    assert state["stage"] == "ended" and state["can_add_plans"] is False and state["can_start"] is False
    assert state["used"] == 3 and "set the response target above 3" in state["start_problem"]
    assert client.post(f"/org/projects/{v1.id}/plans", headers=auth(org), json={**PLAN, "target": 1}).status_code == 409
    low = client.put(f"/org/projects/{v1.id}/target", headers=auth(org), json={"total_target": 2})
    assert low.status_code == 409 and "can't go below 3" in low.json()["detail"]

    reopened = client.put(f"/org/projects/{v1.id}/target", headers=auth(org), json={"total_target": 4}).json()
    assert reopened["reopened"] == {"number": 1, "target": 4}, "4 > 3 collected: no minimum of 5"
    db.expire_all()
    rnd = db.query(SurveyRound).filter_by(product_id=pid).one()
    assert rnd.status == "live" and rnd.target == 4 and [p.target for p in db.query(ActionPlan).filter_by(round_id=rnd.id)] == [4]
    state = client.get(f"/org/projects/{v1.id}/plans", headers=auth(org)).json()
    assert state["stage"] == "live" and [p["name"] for p in state["plans"]] == ["Delhi team"], "its plans show again"

    # Ended again at 3: saving 6 reopens it once more, for 6.
    client.post(f"/org/products/{pid}/rounds/1/end", headers=auth(org))
    again = client.put(f"/org/projects/{v1.id}/target", headers=auth(org), json={"total_target": 6}).json()
    assert again["reopened"] == {"number": 1, "target": 6}


def test_an_older_recipes_survey_reopens_from_its_own_project(client, db):
    org = make_user(db, role="client")
    _, _, pid = _setup(client, org)
    [v1] = _projects(db, pid)
    client.put(f"/org/projects/{v1.id}/target", headers=auth(org), json={"total_target": 5})
    open_round(db, db.get(Product, pid), number=1, recipe_version=1, target=5)
    assert client.post(f"/org/products/{pid}/rounds/1/end", headers=auth(org)).status_code == 200
    client.put(f"/products/{pid}/recipe", headers=auth(org), json=SWEETER)  # version 2
    state = client.get(f"/org/projects/{v1.id}/plans", headers=auth(org)).json()
    assert "set the response target above 0" in state["start_problem"]
    again = client.put(f"/org/projects/{v1.id}/target", headers=auth(org), json={"total_target": 9}).json()
    assert again["reopened"] == {"number": 1, "target": 9} and again["round"]["recipe_version"] == 1


def test_survey_numbers_run_across_the_products_recipes(client, db):
    org = make_user(db, role="client")
    cid, _, pid = _setup(client, org)
    [v1] = _projects(db, pid)
    assert client.get(f"/org/projects/{v1.id}/plans", headers=auth(org)).json()["next_number"] == 1
    client.post(f"/org/projects/{v1.id}/plans", headers=auth(org), json=PLAN)
    assert start_project(client, db, v1.id, auth(org), {}).json()["started"]["number"] == 1
    client.post(f"/org/products/{pid}/rounds/1/end", headers=auth(org))
    client.put(f"/products/{pid}/recipe", headers=auth(org), json=SWEETER)
    v2 = _add(client, org, cid, pid, 2)
    assert client.get(f"/org/projects/{v2['id']}/plans", headers=auth(org)).json()["next_number"] == 2, "recipe v2's first survey is Survey 2"
    client.post(f"/org/projects/{v2['id']}/plans", headers=auth(org), json=PLAN)
    assert start_project(client, db, v2['id'], auth(org), {}).json()["started"]["number"] == 2


def test_admins_see_projects_read_only_and_allocate_the_team_there(client, db, admin, alice):
    """A new project tells every Admin; they open it read-only and staff its survey plans (the single
    place for team allocation), but can't change, start or delete the project."""
    from app.database.models import Notification, ProductAssignment

    org = make_user(db, role="client")
    cid, _, pid = _setup(client, org, project=False)
    made = client.post("/org/projects", headers=auth(org), json={"project_name": "Mint launch", "category_id": cid,
                                                                 "product_id": pid, "total_target": 12})
    assert made.status_code == 201 and made.json()["total_target"] == 12
    proj = made.json()["id"]
    note = db.query(Notification).filter_by(user_id=admin.id, kind="project_created").order_by(Notification.id.desc()).first()
    assert note is not None and note.data["project_id"] == proj

    listed = client.get("/admin/projects", headers=auth(admin)).json()["projects"]
    assert proj in [p["id"] for p in listed] and all("can_delete" not in p for p in listed)
    detail = client.get(f"/admin/projects/{proj}", headers=auth(admin)).json()
    assert detail["total_target"] == 12 and detail["product"]["id"] == pid and "can_delete" not in detail
    assert client.get("/admin/projects", headers=auth(alice)).status_code == 403
    for method, path, body in (("put", f"/org/projects/{proj}", {"project_name": "Renamed"}), ("delete", f"/org/projects/{proj}", None),
                               ("put", f"/org/projects/{proj}/target", {"total_target": 50}),
                               ("post", f"/org/projects/{proj}/plans", PLAN), ("post", f"/org/projects/{proj}/start", {})):
        r = getattr(client, method)(path, headers=auth(admin), **({"json": body} if body is not None else {}))
        assert r.status_code == 403, (method, path)

    plan_id = client.post(f"/org/projects/{proj}/plans", headers=auth(org), json={**PLAN, "cities": []}).json()["plans"][0]["id"]
    plans = client.get(f"/admin/projects/{proj}/plans", headers=auth(admin)).json()
    assert plans["can_allocate"] is True and plans["can_edit"] is False and plans["can_start"] is False
    r = client.put(f"/admin/action-plans/{plan_id}/members", headers=auth(admin), json={"members": [{"user_id": alice.id}]})
    assert r.status_code == 200
    assert start_project(client, db, proj, auth(org), {}).status_code == 200
    db.expire_all()
    assert db.query(ProductAssignment).filter_by(product_id=pid, user_id=alice.id).count() == 1, "the plan's team surveys it"
    # The old per-product assignment can't change a survey that has plans.
    blocked = client.put(f"/admin/products/{pid}/executives", headers=auth(admin), json={"user_ids": []})
    assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "allocate_in_project"
    assert client.put(f"/admin/users/{alice.id}/products", headers=auth(admin), json={"product_ids": []}).status_code == 409


def test_plan_dates_cant_be_in_the_past_and_standards_lock_while_collecting(client, db):
    from datetime import date, timedelta

    org = make_user(db, role="client")
    _, _, pid = _setup(client, org)
    [v1] = _projects(db, pid)
    past = (date.today() - timedelta(days=1)).isoformat()
    r = client.post(f"/org/projects/{v1.id}/plans", headers=auth(org), json={**PLAN, "start_date": past, "end_date": None})
    assert r.status_code == 400 and "can't be in the past" in r.json()["detail"]
    today = date.today().isoformat()
    assert client.post(f"/org/projects/{v1.id}/plans", headers=auth(org),
                       json={**PLAN, "start_date": today, "end_date": today}).status_code == 201
    rule = client.post("/recipe-standards", headers=auth(org), json={"name": "Has water", "kind": "required_ingredient",
                                                                   "ingredient": "Water", "product_id": pid})
    assert rule.status_code == 201
    assert start_project(client, db, v1.id, auth(org), {}).status_code == 200
    for r in (client.put(f"/recipe-standards/{rule.json()['id']}", headers=auth(org),
                         json={"name": "Has water", "kind": "required_ingredient", "ingredient": "Water", "product_id": pid}),
              client.delete(f"/recipe-standards/{rule.json()['id']}", headers=auth(org)),
              client.post("/recipe-standards", headers=auth(org), json={"name": "New", "kind": "costs_complete", "product_id": pid}),
              client.put(f"/products/{pid}/recipe", headers=auth(org), json=SWEETER)):
        assert r.status_code == 409 and r.json()["detail"]["code"] == "survey_ongoing", r.text
    client.post(f"/org/products/{pid}/rounds/1/end", headers=auth(org))
    assert client.delete(f"/recipe-standards/{rule.json()['id']}", headers=auth(org)).status_code == 200


def test_the_admin_shares_the_target_among_the_team_and_nobody_exceeds_their_share(client, db, admin):
    """Super Admin: target 4 in one plan. Admin: Ishaan 3 + Suresh 1. Each submits only their share;
    the allocation must match before the start, is logged, and a target change asks for reallocation."""
    from conftest import answers_payload

    from app.database.models import Notification, Product

    org = make_user(db, role="client")
    cid, _, pid = _setup(client, org, project=False)
    proj = client.post("/org/projects", headers=auth(org), json={"project_name": "Mint", "category_id": cid, "product_id": pid,
                                                                 "total_target": 4}).json()["id"]
    plan = client.post(f"/org/projects/{proj}/plans", headers=auth(org), json={**PLAN, "target": 3, "cities": []}).json()["plans"][0]
    ishaan, suresh = make_user(db, name=_unique("Ishaan ")), make_user(db, name=_unique("Suresh "))
    url = f"/admin/action-plans/{plan['id']}/members"

    for bad in ([{"user_id": ishaan.id, "target": 3}, {"user_id": suresh.id, "target": 1}],   # 4 > 3
                [{"user_id": ishaan.id, "target": 2.5}], [{"user_id": ishaan.id, "target": 0}], [{"user_id": ishaan.id, "target": -1}]):
        assert client.put(url, headers=auth(admin), json={"members": bad}).status_code in (400, 409), bad
    assert client.put(url, headers=auth(admin), json={"members": [{"user_id": ishaan.id, "target": 2}]}).status_code == 200
    state = client.get(f"/org/projects/{proj}/plans", headers=auth(org)).json()
    assert state["plans"][0]["unallocated"] == 1 and state["plans"][0]["needs_allocation"] is True
    client.put(url, headers=auth(admin), json={"members": [{"user_id": ishaan.id, "target": 2}, {"user_id": suresh.id, "target": 1}]})
    state = client.get(f"/org/projects/{proj}/plans", headers=auth(org)).json()
    assert state["can_start"] is True and state["planned"] == 3, "fewer planned than the total (4) may start"
    client.put(f"/action-plans/{plan['id']}", headers=auth(org), json={**PLAN, "target": 4, "cities": []})
    assert db.query(Notification).filter_by(user_id=admin.id, kind="allocation_needed").count() >= 1, "Admin told to reallocate"
    client.put(url, headers=auth(admin), json={"members": [{"user_id": ishaan.id, "target": 3}, {"user_id": suresh.id, "target": 1}]})
    assert client.post(f"/org/projects/{proj}/start", headers=auth(org), json={}).status_code == 200

    hist = client.get(f"/admin/action-plans/{plan['id']}/allocation-history", headers=auth(admin)).json()["history"]
    assert {(h["old"], h["new"]) for h in hist} >= {(None, 2), (2, 3), (None, 1)} and all(h["by"] for h in hist)

    product = db.get(Product, pid)
    def survey(person):
        rid = client.post("/surveys/respondents", headers=auth(person), json={"name": "Customer", "consent_given": True,
                                                                             "location": "Sector 18"}).json()["id"]
        r = client.post("/surveys/responses", headers=auth(person), json={"product_id": pid, "respondent_id": rid})
        if r.status_code != 201:
            return r
        resp_id = r.json()["id"]
        answers = answers_payload(db, product)
        for a in answers["answers"]:
            a["value"] = "4"  # rating questions
        saved = client.put(f"/surveys/responses/{resp_id}/answers", headers=auth(person), json=answers)
        assert saved.status_code == 200, saved.text
        return client.post(f"/surveys/responses/{resp_id}/submit", headers=auth(person))

    assert survey(suresh).status_code == 200
    blocked = survey(suresh)
    assert blocked.status_code == 409 and "all 1 responses allocated to you" in blocked.json()["detail"]
    mine = client.get("/my/action-plans", headers=auth(suresh)).json()["plans"][0]
    assert mine["my_target"] == 1 and mine["my_done"] == 1
    # Suresh's submitted response can't be taken away: never below what he collected.
    low = client.put(url, headers=auth(admin), json={"members": [{"user_id": ishaan.id, "target": 4}]})
    assert low.status_code == 409 and "already submitted 1" in low.json()["detail"]


def test_ending_a_project_ends_its_survey_and_survey_persons_are_told_their_share(client, db, admin, alice):
    """Admin allocation notifies the survey person (once per assignment). The project's counts use
    one set of words (target, submitted, remaining). Ending the project ends its collecting survey;
    nothing more can be planned, started or reopened, and its responses are kept."""
    from app.database.models import Notification, SurveyRound

    org = make_user(db, role="client")
    _, _, pid = _setup(client, org)
    [v1] = _projects(db, pid)
    client.put(f"/org/projects/{v1.id}/target", headers=auth(org), json={"total_target": 4})
    plan = client.post(f"/org/projects/{v1.id}/plans", headers=auth(org), json={**PLAN, "target": 4, "cities": []}).json()["plans"][0]
    for _ in range(2):  # saving the same share twice notifies once
        client.put(f"/admin/action-plans/{plan['id']}/members", headers=auth(admin), json={"members": [{"user_id": alice.id, "target": 4}]})
    notes = db.query(Notification).filter_by(user_id=alice.id, kind="plan_assigned").all()
    assert len(notes) == 1 and notes[0].data["target"] == 4 and notes[0].data["project_id"] == v1.id
    assert client.post(f"/org/projects/{v1.id}/start", headers=auth(org), json={}).status_code == 200
    state = client.get(f"/org/projects/{v1.id}/plans", headers=auth(org)).json()
    assert state["submitted"] == 0 and state["remaining"] == 4 and state["can_end"] is True

    ended = client.post(f"/org/projects/{v1.id}/end", headers=auth(org))
    assert ended.status_code == 200 and ended.json()["ended_surveys"] == [1] and ended.json()["stage"] == "closed"
    db.expire_all()
    assert db.query(SurveyRound).filter_by(product_id=pid).one().status == "closed"
    detail = client.get(f"/org/projects/{v1.id}", headers=auth(org)).json()
    assert detail["closed"] is True and detail["status_label"] == "Ended"
    for r in (client.post(f"/org/projects/{v1.id}/plans", headers=auth(org), json=PLAN),
              client.put(f"/org/projects/{v1.id}/target", headers=auth(org), json={"total_target": 9}),
              client.post(f"/org/products/{pid}/rounds", headers=auth(org), json={"target": 3}),
              client.post(f"/org/projects/{v1.id}/end", headers=auth(org))):
        assert r.status_code == 409, r.text
    assert client.post(f"/org/projects/{v1.id}/end", headers=auth(admin)).status_code == 403


def test_surveys_are_compared_as_separate_surveys_never_with_themselves(client, db):
    """Survey 1 and Survey 2 of the same recipe version are two surveys: they can be compared, a survey
    can't be compared with itself, and several can be compared side by side, each with its own results."""
    org = make_user(db, role="client")
    _, _, pid = _setup(client, org)
    for n in (1, 2, 3):
        open_round(db, db.get(Product, pid), number=n, recipe_version=1, target=3)
        client.post(f"/org/products/{pid}/rounds/{n}/end", headers=auth(org))
    two = client.get(f"/org/products/{pid}/compare?a=1&b=2", headers=auth(org)).json()
    assert two["same_recipe"] is True and two["a"]["number"] == 1 and two["b"]["number"] == 2
    assert all(r["project"] and r["project"]["code"].startswith("PRJ-") for r in two["rounds"]), "each survey's project"
    assert client.get(f"/org/products/{pid}/compare?a=2&b=2", headers=auth(org)).status_code == 400
    assert client.get(f"/org/products/{pid}/compare?surveys=1,1", headers=auth(org)).status_code == 400
    many = client.get(f"/org/products/{pid}/compare?surveys=1,2,3", headers=auth(org)).json()
    assert [s["number"] for s in many["surveys"]] == [1, 2, 3]