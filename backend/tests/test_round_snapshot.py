"""An earlier survey keeps showing what it used: its recipe version and the questions it asked,
after the recipe and the questions changed (GET /org/products/{id}/rounds/{n}/snapshot)."""
from app.database.models import RecipeVersion, SurveyQuestion, SurveyRound

from conftest import answers_payload, auth, make_user, start_survey


def test_an_earlier_survey_shows_its_own_recipe_and_questions(client, db, product):
    org = make_user(db, role="client", name="Acme Snapshot")
    field = make_user(db)
    db.add(RecipeVersion(product_id=product.id, version=1, recipe={"batch_size": "100 kg", "ingredients": [{"name": "Corn", "quantity": "100", "unit": "kg", "cost_per_kg": 40}]},
                         changed_by_name="Acme"))
    db.add(RecipeVersion(product_id=product.id, version=2, recipe={"batch_size": "100 kg", "ingredients": [{"name": "Wheat", "quantity": "100", "unit": "kg"}]},
                         changed_by_name="Acme"))
    db.commit()
    made = start_survey(client, field, product)
    rnd = db.query(SurveyRound).filter_by(product_id=product.id).one()
    rnd.recipe_version = 1
    db.commit()
    assert client.put(f"/surveys/responses/{made['response_id']}/answers", headers=auth(field),
                      json=answers_payload(db, product)).status_code == 200
    # Afterwards a question is retired and a new one added.
    old = db.query(SurveyQuestion).filter_by(product_id=product.id, position=1).one()
    old.is_active = 0
    db.add(SurveyQuestion(product_id=product.id, position=6, text="Brand new question?", question_type="text", required=1, is_active=1))
    db.commit()

    snap = client.get(f"/org/products/{product.id}/rounds/{rnd.number}/snapshot", headers=auth(org))
    assert snap.status_code == 200, snap.text
    body = snap.json()
    assert body["recipe"]["version"] == 1 and body["recipe"]["ingredients"][0]["name"] == "Corn"
    assert body["attachments"] == [], "process files of the survey's recipe version (none here)"
    assert body["costing"] is not None, "old versions keep Cost per KG and Cost in Use"
    texts = [q["text"] for q in body["questions"]]
    assert "Question 1?" in texts and "Brand new question?" not in texts
    assert next(q for q in body["questions"] if q["text"] == "Question 1?")["still_asked"] is False
    assert client.get(f"/org/products/{product.id}/rounds/99/snapshot", headers=auth(org)).status_code == 404
    assert client.get(f"/org/products/{product.id}/rounds/{rnd.number}/snapshot", headers=auth(field)).status_code in (401, 403)
