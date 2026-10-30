"""
Product versions (= recipe versions) and who manages recipes and survey questions.

  - The catalog lists every saved version and the versions in fieldwork, with
    analyzed responses and the average rating of each.
  - A survey person can choose the version when taking a survey; the survey joins
    that version's round and keeps the version. Without a choice: the live round.
  - Surveys made before survey_responses.recipe_version existed get it from their round.
  - Recipes and survey questions are changed only by the organization; admins and
    survey persons can read them.
"""

from datetime import datetime

import pytest
from conftest import _unique, answers_payload, assign, auth, make_user, open_round

from app.database.models import Category, FeedbackRecord, Product, SurveyQuestion, SurveyResponse

RECIPE = {
    "batch_size": "10 kg",
    "ingredients": [{"name": "Oats", "quantity": "6", "unit": "kg"}, {"name": "Sugar", "quantity": "0.8", "unit": "kg"}],
    "steps": [{"title": "Mix"}, {"title": "Bake"}],
}
LESS_SUGAR = {**RECIPE, "ingredients": [{"name": "Oats", "quantity": "6", "unit": "kg"},
                                        {"name": "Sugar", "quantity": "0.6", "unit": "kg"}]}


@pytest.fixture()
def org(db):
    return make_user(db, role="client", name=_unique("Acme "))


def _survey(client, db, person, product, version=None, rating=None):
    """A submitted survey (optionally for a chosen version), analyzed when a rating is given.
    The survey person is assigned to the live round first, as an admin would."""
    assign(db, product, person)
    r = client.post("/surveys/respondents", headers=auth(person),
                    json={"name": "Customer Test", "mobile": "9876543210", "location": "Pune", "consent_given": True})
    assert r.status_code == 201, r.text
    body = {"product_id": product.id, "respondent_id": r.json()["id"]}
    if version is not None:
        body["recipe_version"] = version
    created = client.post("/surveys/responses", headers=auth(person), json=body)
    assert created.status_code == 201, created.text
    rid = created.json()["id"]
    assert client.put(f"/surveys/responses/{rid}/answers", headers=auth(person),
                      json=answers_payload(db, product)).status_code == 200
    assert client.post(f"/surveys/responses/{rid}/submit", headers=auth(person)).status_code == 200
    if rating is not None:
        survey = db.get(SurveyResponse, rid)
        survey.status = "analyzed"
        survey.overall_rating = rating
        db.add(FeedbackRecord(original_filename="r.m4a", video_path="x", duration_seconds=60,
                              uploaded_at=datetime.utcnow(), overall_summary="ok", overall_sentiment="positive",
                              overall_rating=rating, transcript="good", chapters=[], response_id=rid))
        db.commit()
    return created.json()


def _two_versions(client, org, product):
    """Recipe v1 in Round 1 (ended), recipe v2 in Round 2 (live)."""
    assert client.put(f"/products/{product.id}/recipe", headers=auth(org), json=RECIPE).status_code == 200
    assert client.post(f"/org/products/{product.id}/rounds", headers=auth(org), json={"target": 50}).status_code == 201
    assert client.post(f"/org/products/{product.id}/rounds/1/end", headers=auth(org)).status_code == 200
    assert client.put(f"/products/{product.id}/recipe", headers=auth(org), json=LESS_SUGAR).status_code == 200
    r = client.post(f"/org/products/{product.id}/rounds", headers=auth(org), json={"target": 50})
    assert r.status_code == 201 and r.json()["round"]["recipe_version"] == 2


def _listed(client, user, product):
    return {p["id"]: p for p in client.get("/products", headers=auth(user)).json()["products"]}[product.id]


# ---------------------------------------------------------------- versions


def test_catalog_lists_saved_and_fieldwork_versions_with_their_numbers(client, db, admin, org, alice, product):
    # Round 1 (recipe v1) collects a survey while live, then ends; Round 2 tests recipe v2.
    assert client.put(f"/products/{product.id}/recipe", headers=auth(org), json=RECIPE).status_code == 200
    assert client.post(f"/org/products/{product.id}/rounds", headers=auth(org), json={"target": 50}).status_code == 201
    _survey(client, db, alice, product, version=1, rating=3.0)
    assert client.post(f"/org/products/{product.id}/rounds/1/end", headers=auth(org)).status_code == 200
    assert client.put(f"/products/{product.id}/recipe", headers=auth(org), json=LESS_SUGAR).status_code == 200
    assert client.post(f"/org/products/{product.id}/rounds", headers=auth(org), json={"target": 50}).status_code == 201
    _survey(client, db, alice, product, version=2, rating=4.5)
    _survey(client, db, alice, product, version=2, rating=4.0)
    item = _listed(client, admin, product)
    assert item["recipe_versions"] == [1, 2]
    by_version = {v["version"]: v for v in item["survey_versions"]}
    assert by_version[1]["rounds"] == [1] and by_version[1]["live"] is False
    assert by_version[2]["rounds"] == [2] and by_version[2]["live"] is True
    assert by_version[1]["responses"] == 1 and by_version[1]["rating"] == 3.0
    assert by_version[2]["responses"] == 2 and by_version[2]["rating"] == 4.25
    # Survey persons (web and app) are offered only the live round's version.
    assert [v["version"] for v in _listed(client, alice, product)["survey_versions"]] == [2]


def test_a_product_with_one_version_has_one_choice(client, db, org, alice, product):
    client.put(f"/products/{product.id}/recipe", headers=auth(org), json=RECIPE)
    client.post(f"/org/products/{product.id}/rounds", headers=auth(org), json={"target": 50})
    assign(db, product, alice)
    item = _listed(client, alice, product)
    assert [v["version"] for v in item["survey_versions"]] == [1]


def test_survey_joins_the_live_round_and_an_ended_version_is_refused(client, db, org, alice, product):
    _two_versions(client, org, product)
    newer = _survey(client, db, alice, product, version=2)
    db.expire_all()
    assert newer["recipe_version"] == 2 and newer["round"] == 2
    assert db.get(SurveyResponse, newer["id"]).recipe_version == 2
    # Round 1 (version 1) has ended: only the live round's version can be surveyed now.
    rid = client.post("/surveys/respondents", headers=auth(alice),
                      json={"name": "Customer Test", "location": "Pune", "consent_given": True}).json()["id"]
    old = client.post("/surveys/responses", headers=auth(alice), json={"product_id": product.id, "respondent_id": rid, "recipe_version": 1})
    assert old.status_code == 400 and "Use version 2" in old.json()["detail"]


def test_without_a_choice_the_survey_joins_the_live_round_as_before(client, db, org, alice, product):
    _two_versions(client, org, product)
    survey = _survey(client, db, alice, product)  # older app builds send no version
    assert survey["round"] == 2 and survey["recipe_version"] == 2


def test_a_version_that_was_never_surveyed_is_refused(client, db, org, alice, product):
    _two_versions(client, org, product)
    assign(db, product, alice)
    r = client.post("/surveys/respondents", headers=auth(alice),
                    json={"name": "Customer Test", "mobile": "9876543210", "location": "Pune", "consent_given": True})
    assert r.status_code == 201, r.text
    bad = client.post("/surveys/responses", headers=auth(alice),
                      json={"product_id": product.id, "respondent_id": r.json()["id"], "recipe_version": 7})
    assert bad.status_code == 400 and "Version 7" in bad.json()["detail"]


def test_existing_surveys_get_their_round_version(client, db, org, alice, product):
    from app.database.migrations import backfill_survey_versions

    assert client.put(f"/products/{product.id}/recipe", headers=auth(org), json=RECIPE).status_code == 200
    assert client.post(f"/org/products/{product.id}/rounds", headers=auth(org), json={"target": 50}).status_code == 201
    survey = _survey(client, db, alice, product, version=1)
    row = db.get(SurveyResponse, survey["id"])
    row.recipe_version = None  # as before the column existed
    db.commit()
    assert backfill_survey_versions() >= 1
    db.expire_all()
    assert db.get(SurveyResponse, survey["id"]).recipe_version == 1
    assert backfill_survey_versions() == 0  # nothing left to fill: running again changes nothing


# ---------------------------------------------------------------- recipes and questions: organization only


QUESTION = {"text": "How is the crunch?", "question_type": "rating"}


def test_admins_and_survey_persons_cannot_change_questions(client, db, admin, alice, product):
    open_round(db, product)  # admins see the product once a round has started
    assign(db, product, alice)
    q = db.query(SurveyQuestion).filter_by(product_id=product.id).first()
    for person in (admin, alice):
        attempts = [
            client.post(f"/products/{product.id}/questions", headers=auth(person), json=QUESTION),
            client.put(f"/questions/{q.id}", headers=auth(person), json={"text": "Changed"}),
            client.delete(f"/questions/{q.id}", headers=auth(person)),
            client.put(f"/products/{product.id}/questions/order", headers=auth(person), json={"question_ids": [q.id]}),
        ]
        assert all(r.status_code == 403 for r in attempts), [r.text for r in attempts]
        assert attempts[0].json()["code"] == "organization_only"
    db.expire_all()
    assert db.get(SurveyQuestion, q.id).text != "Changed" and db.get(SurveyQuestion, q.id).is_active
    # Reading stays possible (catalog, survey workflow).
    assert client.get(f"/products/{product.id}/questions", headers=auth(admin)).status_code == 200


def test_the_organization_manages_questions(client, db, org, alice, product):
    before = client.get(f"/products/{product.id}/questions", headers=auth(org)).json()["questions"]
    added = client.post(f"/products/{product.id}/questions", headers=auth(org), json=QUESTION)
    assert added.status_code == 201, added.text
    qid = added.json()["id"]
    changed = client.put(f"/questions/{qid}", headers=auth(org),
                         json={"text": "How crunchy is it?", "question_type": "single_choice",
                               "options": ["Too soft", "Just right", "Too hard"]})
    assert changed.status_code == 200 and changed.json()["options"] == ["Too soft", "Just right", "Too hard"]
    order = [qid] + [q["id"] for q in before]
    assert client.put(f"/products/{product.id}/questions/order", headers=auth(org),
                      json={"question_ids": order}).status_code == 200
    # Survey persons assigned to the live round get the new question first in their survey.
    open_round(db, product)
    assign(db, product, alice)
    seen = client.get(f"/products/{product.id}/questions", headers=auth(alice)).json()["questions"]
    assert seen[0]["text"] == "How crunchy is it?"
    assert client.delete(f"/questions/{qid}", headers=auth(org)).status_code == 200
    assert [q["id"] for q in client.get(f"/products/{product.id}/questions", headers=auth(org)).json()["questions"]] \
        == [q["id"] for q in before]


def test_the_organization_cannot_change_questions_of_inactive_products(client, db, org):
    cat = Category(name=_unique("C "), is_active=1)
    db.add(cat)
    db.commit()
    old = Product(category_id=cat.id, name=_unique("Old "), is_active=0)
    db.add(old)
    db.commit()
    assert client.post(f"/products/{old.id}/questions", headers=auth(org), json=QUESTION).status_code == 404
    assert client.get(f"/products/{old.id}/questions", headers=auth(org)).status_code == 404


def test_who_can_survey_never_hides_products_from_the_organization(client, db, org, admin, alice, product):
    # A round is live and assigned to one survey person (Catalog -> Who can survey).
    open_round(db, product)
    bob = make_user(db, role="executive")
    assert client.put(f"/admin/products/{product.id}/executives", headers=auth(admin),
                      json={"user_ids": [bob.id]}).status_code == 200
    # The organization still reads and changes its questions and recipe.
    assert client.get(f"/products/{product.id}/questions", headers=auth(org)).status_code == 200
    assert client.post(f"/products/{product.id}/questions", headers=auth(org), json=QUESTION).status_code == 201
    assert client.put(f"/products/{product.id}/recipe", headers=auth(org), json=RECIPE).status_code == 200
    # Survey persons who aren't assigned are still kept out.
    assert client.get(f"/products/{product.id}/questions", headers=auth(alice)).status_code == 403


def test_question_changes_are_audited(client, db, org, product):
    from app.database.models import AuditLog

    qid = client.post(f"/products/{product.id}/questions", headers=auth(org), json=QUESTION).json()["id"]
    db.expire_all()
    entry = db.query(AuditLog).filter(AuditLog.target_id == product.id, AuditLog.user_id == org.id).order_by(
        AuditLog.id.desc()).first()
    assert entry is not None and f"#{qid}" in entry.summary


def test_only_the_organization_creates_products_without_starting_questions(client, db, admin, org, product):
    cat_id = product.category_id
    by_admin = client.post("/products", headers=auth(admin), json={"category_id": cat_id, "name": _unique("New ")})
    assert by_admin.status_code == 403 and by_admin.json()["code"] == "organization_only"
    blocked = client.post("/products", headers=auth(org),
                          json={"category_id": cat_id, "name": _unique("New "), "copy_questions_from_product_id": product.id})
    assert blocked.status_code == 403 and "Super Admin" in blocked.json()["detail"]  # the role is shown as "Super Admin"
    created = client.post("/products", headers=auth(org), json={"category_id": cat_id, "name": _unique("New ")})
    assert created.status_code == 201 and created.json()["question_count"] == 0