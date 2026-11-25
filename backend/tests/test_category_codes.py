"""Category codes are automatic: the lowest free CAT-#### number, so a deleted category's number is given again."""

from conftest import _unique, auth, make_user


def _remove(client, org, cid):
    assert client.delete(f"/categories/{cid}", headers=auth(org)).status_code == 200           # deactivate
    assert client.delete(f"/categories/{cid}/permanent", headers=auth(org)).status_code == 200  # then delete


def _new(client, org):
    r = client.post("/categories", headers=auth(org), json={"name": _unique("Codes test ")})
    assert r.status_code == 201, r.text
    return r.json()["id"], r.json()["project_id"]


def _next(client, org):
    r = client.get("/categories/next-code", headers=auth(org))
    assert r.status_code == 200, r.text
    return r.json()["code"]


def test_the_lowest_free_code_is_given_again_after_a_delete(client, db):
    org = make_user(db, role="client")
    shown = _next(client, org)
    a, code_a = _new(client, org)
    assert code_a == shown, "the dialog shows the code the category gets"
    b, code_b = _new(client, org)
    c, code_c = _new(client, org)
    num = lambda code: int(code[4:])  # noqa: E731
    assert code_a.startswith("CAT-") and num(code_a) < num(code_b) < num(code_c)
    # the newest deleted: its number is given again (CAT-0006 deleted with 5 left -> CAT-0006)
    _remove(client, org, c)
    assert _next(client, org) == code_c
    assert _new(client, org)[1] == code_c
    # a gap is filled first: the oldest of the three deleted -> its number comes back next
    _remove(client, org, a)
    assert _next(client, org) == code_a
    assert _new(client, org)[1] == code_a


def test_a_category_keeps_its_code_when_edited(client, db):
    org = make_user(db, role="client")
    cid, code = _new(client, org)
    r = client.put(f"/categories/{cid}", headers=auth(org), json={"name": _unique("Renamed ")})
    assert r.status_code == 200 and r.json()["project_id"] == code


def test_the_same_number_in_another_form_is_refused_and_codes_are_stored_standard(client, db):
    org = make_user(db, role="client")
    cid, code = _new(client, org)
    short = "CAT-" + str(int(code[4:])).zfill(3)  # e.g. CAT-005 for CAT-0005
    r = client.post("/categories", headers=auth(org), json={"name": _unique("Same number "), "project_id": short})
    assert r.status_code == 409 and "already used" in r.json()["detail"]
    free = int(client.get("/categories/next-code", headers=auth(org)).json()["code"][4:])
    r = client.post("/categories", headers=auth(org), json={"name": _unique("Short form "), "project_id": f"cat-{free}"})
    assert r.status_code == 201 and r.json()["project_id"] == f"CAT-{free:04d}"


def test_duplicate_numbers_already_saved_are_made_unique_once(client, db):
    """Snacks CAT-0005 (older) and Dairy Products CAT-005 (newer, typed by hand): Snacks keeps it."""
    from app.database.migrations import category_codes_unique
    from app.database.models import Category

    org = make_user(db, role="client")
    a_id, code = _new(client, org)          # the older category
    b_id, _ = _new(client, org)             # the newer one, then given the same number in another form
    same = "CAT-" + str(int(code[4:])).zfill(3)
    db.query(Category).filter(Category.id == b_id).update({"project_id": same})
    db.commit()
    category_codes_unique()
    db.expire_all()
    a, b = db.get(Category, a_id), db.get(Category, b_id)
    assert a.project_id == code, "the older category keeps the number"
    assert b.project_id != code and b.project_id.startswith("CAT-") and len(b.project_id) == 8
    codes = [c.project_id for c in db.query(Category) if c.project_id]
    assert len(codes) == len(set(codes)), "every category has its own code"
    before = {c.id: c.project_id for c in db.query(Category)}
    category_codes_unique()  # repeating it changes nothing
    db.expire_all()
    assert {c.id: c.project_id for c in db.query(Category)} == before