"""
Product check: feedback about another product than the one selected
(services/product_check.py, api/product_checks.py).

Analyses are saved through the real pipeline function (api.video.save_feedback_record),
then reports are read through the real report query (_analyzed_pairs) and API.
"""

import pytest
from conftest import _unique, auth, make_user, open_round, start_survey

from app.api.product_feedback import _analyzed_pairs
from app.api.video import save_feedback_record
from app.database.models import AuditLog, Category, Notification, Product, ProductCheck, SurveyQuestion, SurveyResponse


@pytest.fixture()
def catalog(db):
    """Three products in two categories (names are data, nothing is hardcoded in the check)."""
    food = Category(name=_unique("Food & Confectionery "), is_active=1)
    cereal = Category(name=_unique("Breakfast Cereals "), is_active=1)
    db.add_all([food, cereal])
    db.commit()
    choc = Product(category_id=food.id, name="Dark Chocolate", is_active=1)
    flakes = Product(category_id=cereal.id, name="Kellogg's Corn Flakes", is_active=1)
    jelly = Product(category_id=food.id, name="Aam (Mango) Jelly", is_active=1)
    db.add_all([choc, flakes, jelly])
    db.commit()
    for item in (choc, flakes, jelly):  # surveys need at least 5 questions
        for position in range(1, 6):
            db.add(SurveyQuestion(product_id=item.id, position=position, text=f"Question {position}?",
                                  question_type="text", required=1, is_active=1))
    db.commit()
    return {"choc": choc, "flakes": flakes, "jelly": jelly}


def _analyze(client, db, person, product, transcript, summary="The customer shared feedback."):
    rid = start_survey(client, person, product)["response_id"]
    save_feedback_record("interview.m4a", __file__, 60, 1, 4.0, summary, transcript, [], response_id=rid)  # a file that exists
    db.expire_all()
    return rid


def _check(db, rid):
    db.expire_all()
    return db.query(ProductCheck).filter_by(response_id=rid).first()


def _in_report(db, product, rid):
    return rid in {r.id for r, _ in _analyzed_pairs(db, [product.id])}


def test_1_matching_feedback_is_accepted(client, db, catalog):
    alice = make_user(db)
    rid = _analyze(client, db, alice, catalog["choc"], "I really liked this dark chocolate, rich and smooth.")
    c = _check(db, rid)
    assert c.status == "match" and c.review_status == "none" and c.detected_product_id == catalog["choc"].id
    assert db.get(SurveyResponse, rid).status == "analyzed" and _in_report(db, catalog["choc"], rid)


def test_2_and_7_mismatch_is_flagged_and_kept_out_of_the_report(client, db, catalog, admin):
    alice = make_user(db)
    rid = _analyze(client, db, alice, catalog["choc"], "Honestly the corn flakes were crunchy, I eat Kellogg's corn flakes every morning.")
    c = _check(db, rid)
    assert c.status == "mismatch" and c.review_status == "pending" and c.detected_product_id == catalog["flakes"].id
    assert c.selected_product_id == catalog["choc"].id, "the original selection is preserved"
    assert db.get(SurveyResponse, rid).status == "needs_review"
    assert not _in_report(db, catalog["choc"], rid) and not _in_report(db, catalog["flakes"], rid)
    # admins and the survey person are told
    kinds = {(n.user_id, n.kind) for n in db.query(Notification).filter(Notification.kind == "product_mismatch")}
    assert (alice.id, "product_mismatch") in kinds and (admin.id, "product_mismatch") in kinds
    listed = client.get("/admin/product-checks", headers=auth(admin)).json()
    row = next(x for x in listed["checks"] if x["response_id"] == rid)
    assert row["selected_product"]["name"] == "Dark Chocolate" and row["detected_product"]["name"] == "Kellogg's Corn Flakes"
    assert row["label"] == "Mismatch detected" and listed["counts"]["pending"] >= 1


def test_3_aliases_find_products_named_differently(client, db, catalog):
    alice = make_user(db)
    rid = _analyze(client, db, alice, catalog["flakes"], "The mango jelly was too sweet but my kids loved the mango jelly.")
    c = _check(db, rid)
    assert c.status == "mismatch" and c.detected_product_id == catalog["jelly"].id


def test_4_generic_feedback_is_unverified_not_blocked(client, db, catalog, admin):
    alice = make_user(db)
    rid = _analyze(client, db, alice, catalog["choc"], "It was nice, good taste, the price is a little high.")
    c = _check(db, rid)
    assert c.status == "uncertain" and c.review_status == "optional" and c.detected_product_id is None
    assert _in_report(db, catalog["choc"], rid), "no product named: stays under the selected product"
    assert any(x["response_id"] == rid for x in client.get("/admin/product-checks?status=unverified", headers=auth(admin)).json()["checks"])


def test_5_multiple_products_in_one_recording(client, db, catalog):
    alice = make_user(db)
    both = _analyze(client, db, alice, catalog["choc"], "This dark chocolate is better than corn flakes. Dark chocolate wins.")
    assert _check(db, both).status == "match"
    mostly_other = _analyze(client, db, alice, catalog["choc"],
                            "Dark chocolate is fine, but corn flakes, corn flakes and more corn flakes is what I buy.")
    c = _check(db, mostly_other)
    assert c.status == "uncertain" and c.detected_product_id == catalog["flakes"].id


def test_6_and_8_an_admin_corrects_the_product_and_the_report_follows(client, db, catalog, admin):
    alice = make_user(db)
    open_round(db, catalog["flakes"])  # the corn flakes have a live round
    rid = _analyze(client, db, alice, catalog["choc"], "Kellogg's corn flakes with cold milk, very crunchy.")
    original_round = db.get(SurveyResponse, rid).round_id
    r = client.post(f"/admin/product-checks/{rid}/review", headers=auth(admin),
                    json={"action": "correct", "product_id": catalog["flakes"].id, "remarks": "Customer tasted the cereal"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["review_status"] == "corrected" and body["corrected_product"]["name"] == "Kellogg's Corn Flakes"
    assert body["selected_product"]["name"] == "Dark Chocolate", "original selection kept on the check"
    assert body["history"] and "Moved survey" in body["history"][-1]["summary"] and body["history"][-1]["by"] == admin.name
    db.expire_all()
    s = db.get(SurveyResponse, rid)
    flakes_round = db.query(ProductCheck).filter_by(response_id=rid).first()
    assert s.product_id == catalog["flakes"].id and s.status == "analyzed" and s.round_id != original_round
    assert flakes_round.selected_round_id == original_round
    assert _in_report(db, catalog["flakes"], rid) and not _in_report(db, catalog["choc"], rid)
    assert db.query(AuditLog).filter(AuditLog.action == "product_check.correct", AuditLog.target_id == rid).count() == 1


def test_confirm_and_invalid_reviews(client, db, catalog, admin):
    alice = make_user(db)
    kept = _analyze(client, db, alice, catalog["choc"], "corn flakes corn flakes")
    gone = _analyze(client, db, alice, catalog["choc"], "aam jelly is good")
    assert client.post(f"/admin/product-checks/{kept}/review", headers=auth(admin), json={"action": "confirm"}).status_code == 200
    assert client.post(f"/admin/product-checks/{gone}/review", headers=auth(admin), json={"action": "invalid", "remarks": "Wrong sample"}).status_code == 200
    db.expire_all()
    assert _in_report(db, catalog["choc"], kept)
    assert db.get(SurveyResponse, gone).status == "invalid" and not _in_report(db, catalog["choc"], gone)
    bad = client.post(f"/admin/product-checks/{kept}/review", headers=auth(admin), json={"action": "correct"})
    assert bad.status_code == 400


def test_9_rounds_keep_their_own_surveys(client, db, catalog):
    alice = make_user(db)
    r1 = _analyze(client, db, alice, catalog["choc"], "dark chocolate, round one")
    first_round = db.get(SurveyResponse, r1).round_id
    from app.database.models import SurveyRound

    rnd = db.get(SurveyRound, first_round)
    rnd.status = "closed"
    db.commit()
    open_round(db, catalog["choc"], number=2)
    r2 = _analyze(client, db, alice, catalog["choc"], "the dark chocolate in round two")
    assert db.get(SurveyResponse, r2).round_id != first_round
    assert _check(db, r1).selected_round_id == first_round and _check(db, r2).selected_round_id == db.get(SurveyResponse, r2).round_id


def test_10_existing_feedback_without_checks_keeps_working(client, db, catalog):
    alice = make_user(db)
    rid = _analyze(client, db, alice, catalog["choc"], "dark chocolate")
    db.query(ProductCheck).filter_by(response_id=rid).delete()  # analyzed before checks existed
    db.commit()
    assert _in_report(db, catalog["choc"], rid)


def test_review_needs_an_admin(client, db, catalog):
    alice = make_user(db)
    rid = _analyze(client, db, alice, catalog["choc"], "corn flakes")
    assert client.get("/admin/product-checks", headers=auth(alice)).status_code == 403
    assert client.post(f"/admin/product-checks/{rid}/review", headers=auth(alice), json={"action": "confirm"}).status_code == 403


def test_aliases_are_built_from_the_catalogue_name():
    from app.services.product_check import aliases

    assert {"aam (mango) jelly", "aam jelly", "mango jelly"} <= aliases("Aam (Mango) Jelly")
    assert {"kellogg's corn flakes", "kelloggs corn flakes", "corn flakes"} <= aliases("Kellogg's Corn Flakes")
    assert aliases("Dark Chocolate") == {"dark chocolate"}