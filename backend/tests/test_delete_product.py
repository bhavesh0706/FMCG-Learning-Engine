"""Permanently deleting a product created by mistake removes everything that belongs only to it."""

from conftest import _unique, auth, make_user

from app.database.models import (ActionPlan, ActionPlanMember, Product, ProductAssignment, RecipeAttachment, RecipeStandard,
                                  RecipeVersion, SurveyQuestion, SurveyRound)

RECIPE = {"batch_size": "100 kg", "ingredients": [{"name": "Curd", "quantity": "80", "unit": "kg"}, {"name": "Mango", "quantity": "20", "unit": "kg"}]}


def _full_product(client, org, admin, person, cid):
    pid = client.post("/products", headers=auth(org), json={"category_id": cid, "name": _unique("Mango Lassi "),
                                                           "recipe_plan": {"target": 5, "cities": ["Delhi"]}}).json()["id"]
    for batch in ("100 kg", "100 kg v2", "100 kg v3"):
        client.put(f"/products/{pid}/recipe", headers=auth(org), json={**RECIPE, "batch_size": batch})
    for t in ("Taste?", "Sweetness?", "Thickness?", "Aroma?", "Buy again?"):
        client.post(f"/products/{pid}/questions", headers=auth(org), json={"text": t, "question_type": "rating"})
    client.post("/recipe-standards", headers=auth(org), json={"name": "Curd must be present", "kind": "required_ingredient",
                                                             "ingredient": "Curd", "product_id": pid})
    att = client.post(f"/products/{pid}/recipe/attachments", headers=auth(org), files={"file": ("process.pdf", b"%PDF-1.4 test", "application/pdf")})
    assert att.status_code in (200, 201), att.text
    started = client.post(f"/org/products/{pid}/rounds", headers=auth(org), json={})
    assert started.status_code == 201, started.text
    plan = client.get(f"/products/{pid}/action-plans", headers=auth(org)).json()["plans"][0]["id"]
    client.put(f"/admin/action-plans/{plan}/members", headers=auth(admin), json={"members": [{"user_id": person.id}]})
    return pid


def test_a_product_with_rounds_versions_standards_files_and_plans_is_deleted_completely(client, db, admin):
    from app.services.storage import get_storage

    org, person = make_user(db, role="client"), make_user(db)
    cid = client.post("/categories", headers=auth(org), json={"name": _unique("Dairy ")}).json()["id"]
    # A category-wide standard (added before the surveys start: it can't change while they collect)
    project_rule = client.post("/recipe-standards", headers=auth(org), json={"name": "Curd in the category", "kind": "required_ingredient", "ingredient": "Curd", "category_id": cid})
    pid = _full_product(client, org, admin, person, cid)
    keep = _full_product(client, org, admin, make_user(db), cid)  # another product: must stay as it is
    files = [get_storage().resolve(a.stored_path) for a in db.query(RecipeAttachment).filter(RecipeAttachment.product_id == pid)]
    assert files and all(f.exists() for f in files)
    counts = lambda p: {  # noqa: E731
        "rounds": db.query(SurveyRound).filter(SurveyRound.product_id == p).count(),
        "versions": db.query(RecipeVersion).filter(RecipeVersion.product_id == p).count(),
        "standards": db.query(RecipeStandard).filter(RecipeStandard.product_id == p).count(),
        "attachments": db.query(RecipeAttachment).filter(RecipeAttachment.product_id == p).count(),
        "plans": db.query(ActionPlan).filter(ActionPlan.product_id == p).count(),
        "questions": db.query(SurveyQuestion).filter(SurveyQuestion.product_id == p).count(),
        "assignments": db.query(ProductAssignment).filter(ProductAssignment.product_id == p).count(),
    }
    before = counts(pid)
    assert before["rounds"] == 1 and before["versions"] >= 3 and before["standards"] == 1 and before["plans"] == 1
    assert client.delete(f"/products/{pid}/permanent", headers=auth(org)).status_code == 400, "deactivate first"
    client.delete(f"/products/{pid}", headers=auth(org))
    r = client.delete(f"/products/{pid}/permanent", headers=auth(org))
    assert r.status_code == 200, r.text
    assert r.json()["removed"]["rounds"] == 1 and r.json()["removed"]["recipe_versions"] >= 3 and r.json()["removed"]["action_plan_members"] == 1
    db.expire_all()
    assert db.get(Product, pid) is None and set(counts(pid).values()) == {0}, "nothing left pointing to it"
    kept_plans = [p.id for p in db.query(ActionPlan).filter(ActionPlan.product_id == keep)]
    assert db.query(ActionPlanMember).filter(ActionPlanMember.action_plan_id.in_(kept_plans)).count() == 1, "the other product's team stays"
    assert not any(f.exists() for f in files), "the attachment file is removed too"
    assert counts(keep) == {**before, "versions": counts(keep)["versions"]}, "the other product is untouched"
    assert db.get(RecipeStandard, project_rule.json()["id"]) is not None, "a project-wide standard is kept"


def test_a_product_with_surveys_is_still_never_deleted(client, db, admin):
    org, person = make_user(db, role="client"), make_user(db)
    cid = client.post("/categories", headers=auth(org), json={"name": _unique("Dairy ")}).json()["id"]
    pid = _full_product(client, org, admin, person, cid)
    rid = client.post("/surveys/respondents", headers=auth(person), json={"name": "Customer", "consent_given": True, "city": "Delhi"}).json()["id"]
    assert client.post("/surveys/responses", headers=auth(person), json={"product_id": pid, "respondent_id": rid}).status_code == 201
    client.delete(f"/products/{pid}", headers=auth(org))
    r = client.delete(f"/products/{pid}/permanent", headers=auth(org))
    assert r.status_code == 409 and "can't be deleted" in r.json()["detail"]
    db.expire_all()
    assert db.get(Product, pid) is not None and db.query(SurveyRound).filter(SurveyRound.product_id == pid).count() == 1