"""Question templates reused at Add project: independent copies, product name filled in, originals kept."""

from conftest import _unique, auth, make_user

from app.database.models import QuestionTemplate, SurveyQuestion

QS = [("How would you rate the taste of {name}?", "rating", None), ("How spicy is {name} for you?", "single_choice", ["Too mild", "Just right", "Too spicy"]),
      ("What would you pay for a pack?", "single_choice", ["Rs 10", "Rs 15", "Rs 20"]), ("Would you buy it again?", "yes_no", None),
      ("What should we improve?", "text", None)]


def _product(client, org, name):
    cid = client.post("/categories", headers=auth(org), json={"name": _unique("Cat ")}).json()["id"]
    return client.post("/products", headers=auth(org), json={"category_id": cid, "name": name}).json()["id"]


def _active(db, pid):
    db.expire_all()
    return db.query(SurveyQuestion).filter(SurveyQuestion.product_id == pid, SurveyQuestion.is_active == 1).order_by(SurveyQuestion.position).all()


def test_save_a_set_as_template_and_reuse_it_for_another_product(client, db):
    org = make_user(db, role="client")
    peanuts = _product(client, org, _unique("Masala Peanuts "))
    pname = client.get(f"/products/{peanuts}", headers=auth(org)).json()["name"]
    for text, kind, opts in QS:
        r = client.post(f"/products/{peanuts}/questions", headers=auth(org),
                        json={"text": text.format(name=pname), "question_type": kind, **({"options": opts} if opts else {})})
        assert r.status_code == 201, r.text
    t = client.post("/question-templates", headers=auth(org), json={"name": _unique("Snack taste test "), "from_product_id": peanuts})
    assert t.status_code == 201, t.text
    tpl = t.json()
    assert tpl["question_count"] == 5 and "{product}" in tpl["questions"][0]["text"], "the product name becomes {product}"
    # another product, another category: an independent copy with its own name
    noodles = _product(client, org, _unique("Masala Noodles "))
    nname = client.get(f"/products/{noodles}", headers=auth(org)).json()["name"]
    r = client.post("/question-templates/apply", headers=auth(org), json={"product_id": noodles, "template_id": tpl["id"]})
    assert r.status_code == 200 and r.json()["question_count"] == 5
    copied = _active(db, noodles)
    assert copied[0].text == f"How would you rate the taste of {nname}?" and copied[1].options == ["Too mild", "Just right", "Too spicy"]
    assert copied[1].question_type == "single_choice"
    # editing and removing the copies changes neither the template nor the original product
    client.put(f"/questions/{copied[0].id}", headers=auth(org), json={"text": "Changed for noodles?"})
    client.delete(f"/questions/{copied[4].id}", headers=auth(org))
    db.expire_all()
    assert db.get(QuestionTemplate, tpl["id"]).questions[0]["text"] == tpl["questions"][0]["text"]
    assert [q.text for q in _active(db, peanuts)][0] == f"How would you rate the taste of {pname}?"
    assert len(_active(db, noodles)) == 4


def test_replace_keeps_old_answers_add_respects_the_limit_and_products_are_sources(client, db, alice):
    org = make_user(db, role="client")
    src = _product(client, org, _unique("Dark Chocolate "))
    sname = client.get(f"/products/{src}", headers=auth(org)).json()["name"]
    for text, kind, opts in QS:
        client.post(f"/products/{src}/questions", headers=auth(org), json={"text": text.format(name=sname), "question_type": kind, **({"options": opts} if opts else {})})
    dst = _product(client, org, _unique("Milk Chocolate "))
    dname = client.get(f"/products/{dst}", headers=auth(org)).json()["name"]
    old = client.post(f"/products/{dst}/questions", headers=auth(org), json={"text": "Old question?", "question_type": "rating"}).json()
    # earlier products are offered as starting sets
    srcs = client.get("/question-templates/products", headers=auth(org), params={"exclude_product_id": dst}).json()["products"]
    assert any(p["id"] == src and p["question_count"] == 5 for p in srcs) and all(p["id"] != dst for p in srcs)
    # replace: the old question is deactivated (kept with its answers), the new ones read this product's name
    r = client.post("/question-templates/apply", headers=auth(org), json={"product_id": dst, "from_product_id": src, "replace": True})
    assert r.status_code == 200 and r.json()["question_count"] == 5
    db.expire_all()
    assert db.get(SurveyQuestion, old["id"]).is_active == 0, "replaced, not deleted"
    assert _active(db, dst)[0].text == f"How would you rate the taste of {dname}?"
    # add: 5 + 5 = 10 fits; 10 + 5 would not
    assert client.post("/question-templates/apply", headers=auth(org), json={"product_id": dst, "from_product_id": src, "replace": False}).json()["question_count"] == 10
    over = client.post("/question-templates/apply", headers=auth(org), json={"product_id": dst, "from_product_id": src, "replace": False})
    assert over.status_code == 400 and "at most 10" in over.json()["detail"]
    # survey persons can't use templates
    assert client.get("/question-templates", headers=auth(alice)).status_code == 403