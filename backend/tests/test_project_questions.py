"""A project is created only once its product has its survey questions (5-10), set on the Add project page."""

from conftest import _unique, auth, make_user


def _product(client, org):
    cid = client.post("/categories", headers=auth(org), json={"name": _unique("Snacks ")}).json()["id"]
    pid = client.post("/products", headers=auth(org), json={"category_id": cid, "name": _unique("Masala Peanuts ")}).json()["id"]
    client.put(f"/products/{pid}/recipe", headers=auth(org), json={"batch_size": "100 kg",
               "ingredients": [{"name": "Peanuts", "quantity": "100", "unit": "kg"}]})
    return cid, pid


def _add_questions(client, org, pid, n):
    for i in range(n):
        assert client.post(f"/products/{pid}/questions", headers=auth(org),
                           json={"text": f"Question {i + 1}?", "question_type": "rating"}).status_code in (200, 201)


def _create(client, org, cid, pid):
    return client.post("/org/projects", headers=auth(org), json={"project_name": "Launch study", "category_id": cid,
                                                                   "product_id": pid, "total_target": 10})


def test_no_project_without_its_survey_questions(client, db):
    org = make_user(db, role="client")
    cid, pid = _product(client, org)
    r = _create(client, org, cid, pid)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "questions_not_ready" and r.json()["detail"]["questions"] == 0
    _add_questions(client, org, pid, 4)
    r = _create(client, org, cid, pid)
    assert r.status_code == 400 and "add 5-10 survey questions" in r.json()["detail"]["message"]
    _add_questions(client, org, pid, 1)
    assert _create(client, org, cid, pid).status_code == 201, "5 questions: the project is created"


def test_more_than_ten_questions_are_refused(client, db):
    org = make_user(db, role="client")
    cid, pid = _product(client, org)
    _add_questions(client, org, pid, 10)
    from app.database.models import SurveyQuestion
    db.add(SurveyQuestion(product_id=pid, position=11, text="An eleventh question?", question_type="rating", is_active=1))
    db.commit()
    r = _create(client, org, cid, pid)
    assert r.status_code == 400 and "at most 10" in r.json()["detail"]["message"]