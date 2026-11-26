"""
Fixing mistakes with surveys (rounds) and recipe versions:
  - a recipe completed after a survey was started, before anyone answered, keeps
    "Survey 1 · version 1" instead of becoming version 2 / Survey 2
  - the latest survey can be deleted while it has no responses
  - the latest recipe version can be deleted while no survey uses it
"""

import pytest
from conftest import _unique, assign, auth, make_user

from app.database.models import ActionPlan, ProductAssignment, RecipeVersion, SurveyRound

# Written for "every recipe change is a new version" (see conftest.legacy_recipe_versions).
pytestmark = pytest.mark.usefixtures("legacy_recipe_versions")

DRAFT = {"batch_size": "10 kg", "ingredients": [{"name": "Peanuts", "quantity": "10", "unit": "kg"}]}
FULL = {"batch_size": "10 kg", "ingredients": [{"name": "Peanuts", "quantity": "9", "unit": "kg"},
                                               {"name": "Masala", "quantity": "1", "unit": "kg"}]}
SALTIER = {"batch_size": "10 kg", "ingredients": [{"name": "Peanuts", "quantity": "8.8", "unit": "kg"},
                                                  {"name": "Masala", "quantity": "1.2", "unit": "kg"}]}


@pytest.fixture()
def org(db):
    return make_user(db, role="client", name=_unique("Acme "))


def _versions(db, product):
    return [v.version for v in db.query(RecipeVersion).filter_by(product_id=product.id).order_by(RecipeVersion.version)]


def _start(client, org, product, **body):
    r = client.post(f"/org/products/{product.id}/rounds", headers=auth(org), json={"target": 5, **body})
    assert r.status_code == 201, r.text
    return r.json()["round"]


def _answer(client, db, person, product):
    assign(db, product, person)
    rid = client.post("/surveys/respondents", headers=auth(person),
                      json={"name": "Customer", "consent_given": True, "location": "Pune"}).json()["id"]
    created = client.post("/surveys/responses", headers=auth(person), json={"product_id": product.id, "respondent_id": rid})
    assert created.status_code == 201, created.text
    return created.json()["id"]


# ---------------------------------------------------------------- recipe completed after the survey started


def test_the_recipe_is_locked_while_its_survey_is_collecting(client, db, org, product):
    """No recipe change (and so no new version) while consumers test the current one, even before
    the first answer; it unlocks once the survey is ended."""
    assert client.put(f"/products/{product.id}/recipe", headers=auth(org), json=DRAFT).status_code == 200
    survey = _start(client, org, product)
    assert survey["number"] == 1 and survey["recipe_version"] == 1

    r = client.put(f"/products/{product.id}/recipe", headers=auth(org), json=FULL)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "survey_ongoing"
    assert "is collecting consumer feedback" in r.json()["detail"]["message"]
    assert client.delete(f"/products/{product.id}/recipe/versions/1", headers=auth(org)).status_code == 409
    assert _versions(db, product) == [1], "no version 2"
    v1 = db.query(RecipeVersion).filter_by(product_id=product.id, version=1).one()
    assert len(v1.recipe["ingredients"]) == 1, "the tested recipe is unchanged"
    live = db.query(SurveyRound).filter_by(product_id=product.id).all()
    assert [(x.number, x.recipe_version, x.status) for x in live] == [(1, 1, "live")], "still Survey 1, still live"
    client.post(f"/org/products/{product.id}/rounds/1/end", headers=auth(org))
    assert client.put(f"/products/{product.id}/recipe", headers=auth(org), json=FULL).json()["version"] == 2


def test_once_a_consumer_answered_a_change_is_a_new_version(client, db, org, alice, product):
    client.put(f"/products/{product.id}/recipe", headers=auth(org), json=FULL)
    _start(client, org, product)
    _answer(client, db, alice, product)
    client.post(f"/org/products/{product.id}/rounds/1/end", headers=auth(org))
    r = client.put(f"/products/{product.id}/recipe", headers=auth(org), json=SALTIER)
    assert r.json()["version"] == 2 and r.json()["version_updated_in_place"] is False
    assert _versions(db, product) == [1, 2]
    assert db.query(RecipeVersion).filter_by(product_id=product.id, version=1).one().recipe["ingredients"][1]["quantity"] == "1"


def test_changes_before_any_survey_still_make_versions(client, db, org, product):
    client.put(f"/products/{product.id}/recipe", headers=auth(org), json=DRAFT)
    client.put(f"/products/{product.id}/recipe", headers=auth(org), json=FULL)
    assert _versions(db, product) == [1, 2]


# ---------------------------------------------------------------- deleting a survey (round)


def test_the_latest_survey_without_responses_can_be_deleted(client, db, org, admin, alice, product):
    client.put(f"/products/{product.id}/recipe", headers=auth(org), json=FULL)
    _start(client, org, product)
    plan = db.query(ActionPlan).filter_by(product_id=product.id).count()
    assign(db, product, alice)  # an admin assigned the survey to a team member
    assert db.query(ProductAssignment).filter_by(product_id=product.id).count() == 1

    r = client.delete(f"/org/products/{product.id}/rounds/1", headers=auth(org))
    assert r.status_code == 200, r.text
    assert db.query(SurveyRound).filter_by(product_id=product.id).count() == 0
    assert db.query(ProductAssignment).filter_by(product_id=product.id).count() == 0
    assert db.query(ActionPlan).filter_by(product_id=product.id).count() == 0 <= plan
    assert _versions(db, product) == [1], "the recipe version stays"
    # The next survey starts again as Survey 1.
    assert _start(client, org, product)["number"] == 1


def test_a_survey_with_responses_is_never_deleted(client, db, org, alice, product):
    client.put(f"/products/{product.id}/recipe", headers=auth(org), json=FULL)
    _start(client, org, product)
    _answer(client, db, alice, product)
    r = client.delete(f"/org/products/{product.id}/rounds/1", headers=auth(org))
    assert r.status_code == 409 and "already has 1 response" in r.json()["detail"]
    assert db.query(SurveyRound).filter_by(product_id=product.id).count() == 1


def test_only_the_latest_survey_can_be_deleted(client, db, org, product):
    client.put(f"/products/{product.id}/recipe", headers=auth(org), json=FULL)
    _start(client, org, product)
    client.post(f"/org/products/{product.id}/rounds/1/end", headers=auth(org))
    _start(client, org, product)
    r = client.delete(f"/org/products/{product.id}/rounds/1", headers=auth(org))
    assert r.status_code == 409 and "Survey 2" in r.json()["detail"]
    assert client.delete(f"/org/products/{product.id}/rounds/2", headers=auth(org)).status_code == 200
    assert client.delete(f"/org/products/{product.id}/rounds/1", headers=auth(org)).status_code == 200


def test_admins_can_delete_from_the_dashboard_but_survey_persons_cannot(client, db, org, admin, alice, product):
    client.put(f"/products/{product.id}/recipe", headers=auth(org), json=FULL)
    _start(client, org, product)
    assert client.delete(f"/org/products/{product.id}/rounds/1", headers=auth(alice)).status_code == 403
    assert client.delete(f"/products/{product.id}/rounds/1", headers=auth(alice)).status_code == 403
    assert client.delete(f"/products/{product.id}/rounds/1", headers=auth(admin)).status_code == 200
    assert client.delete(f"/products/{product.id}/rounds/9", headers=auth(admin)).status_code == 404


# ---------------------------------------------------------------- deleting a recipe version


def test_an_unused_latest_version_can_be_deleted_and_the_recipe_goes_back(client, db, org, product):
    client.put(f"/products/{product.id}/recipe", headers=auth(org), json=FULL)
    client.put(f"/products/{product.id}/recipe", headers=auth(org), json=SALTIER)
    assert _versions(db, product) == [1, 2]
    r = client.delete(f"/products/{product.id}/recipe/versions/2", headers=auth(org))
    assert r.status_code == 200, r.text
    assert r.json()["current_version"] == 1 and r.json()["recipe"]["ingredients"][1]["quantity"] == "1"
    assert _versions(db, product) == [1]
    db.refresh(product)
    assert product.recipe["ingredients"][1]["quantity"] == "1"
    # The next change is version 2 again.
    client.put(f"/products/{product.id}/recipe", headers=auth(org), json=SALTIER)
    assert _versions(db, product) == [1, 2]


def test_a_version_used_by_a_survey_or_not_the_latest_is_kept(client, db, org, admin, product):
    client.put(f"/products/{product.id}/recipe", headers=auth(org), json=FULL)
    _start(client, org, product)
    r = client.delete(f"/products/{product.id}/recipe/versions/1", headers=auth(org))
    assert r.status_code == 409 and "Survey 1" in str(r.json()["detail"])
    client.post(f"/org/products/{product.id}/rounds/1/end", headers=auth(org))
    client.put(f"/products/{product.id}/recipe", headers=auth(org), json=SALTIER)
    assert client.delete(f"/products/{product.id}/recipe/versions/1", headers=auth(org)).status_code == 409
    assert client.delete(f"/products/{product.id}/recipe/versions/7", headers=auth(org)).status_code == 404
    # Recipes belong to the organization: admins can't delete versions.
    assert client.delete(f"/products/{product.id}/recipe/versions/2", headers=auth(admin)).status_code == 403
    assert _versions(db, product) == [1, 2]


def test_mistake_flow_survey_then_version(client, db, org, product):
    """The whole fix-up: delete the survey started by mistake, then its unused version."""
    client.put(f"/products/{product.id}/recipe", headers=auth(org), json=FULL)
    client.put(f"/products/{product.id}/recipe", headers=auth(org), json=SALTIER)
    _start(client, org, product)  # Survey 1 on version 2, by mistake
    assert client.delete(f"/org/products/{product.id}/rounds/1", headers=auth(org)).status_code == 200
    assert client.delete(f"/products/{product.id}/recipe/versions/2", headers=auth(org)).status_code == 200
    assert _start(client, org, product)["recipe_version"] == 1
