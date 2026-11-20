"""LOV lists: m_othermastername + m_othermasterdata, GET /lov and the admin changes."""

from conftest import _unique, auth, make_user

from app.database.models import OtherMasterData, OtherMasterName
from app.services.lov import DEFAULTS, seed_lov


def _values(client, user, code, all=False):
    r = client.get("/lov", headers=auth(user), params={"all": "true"} if all else {})
    assert r.status_code == 200, r.text
    return r.json()[code]["values"]


def test_every_default_list_is_seeded(client, admin):
    data = client.get("/lov", headers=auth(admin)).json()
    for code, name, is_system, values in DEFAULTS:
        assert data[code]["name"] == name and data[code]["is_system"] == bool(is_system)
        assert [v["code"] for v in data[code]["values"]] == [c for c, _, _ in values]
    assert data["SENTIMENT"]["values"][0]["extra"] == {"tone": "positive"}


def test_every_signed_in_role_can_read_but_only_admins_change(client, db, admin, alice):
    org = make_user(db, role="client", name=_unique("Acme "))
    for person in (admin, alice, org):
        assert client.get("/lov", headers=auth(person)).status_code == 200
    assert client.get("/lov").status_code == 401
    for person in (alice, org):
        assert client.post("/admin/lov/GENDER", headers=auth(person), json={"label": "X"}).status_code == 403


def test_admin_adds_hides_and_renames_open_list_values(client, admin):
    label = _unique("Non-binary ")
    r = client.post("/admin/lov/gender", headers=auth(admin), json={"label": label})
    assert r.status_code == 201, r.text
    new = r.json()
    assert new["code"] == label and new["is_active"]
    assert client.post("/admin/lov/GENDER", headers=auth(admin), json={"label": label}).status_code == 409

    assert client.patch(f"/admin/lov/values/{new['id']}", headers=auth(admin), json={"is_active": False}).status_code == 200
    assert label not in [v["label"] for v in _values(client, admin, "GENDER")]
    assert label in [v["label"] for v in _values(client, admin, "GENDER", all=True)]

    renamed = client.patch(f"/admin/lov/values/{new['id']}", headers=auth(admin), json={"label": "  Renamed   value ", "is_active": True}).json()
    assert renamed["label"] == "Renamed value" and renamed["code"] == label  # saved data keeps its code


def test_system_lists_only_allow_label_and_order_changes(client, admin):
    assert client.post("/admin/lov/SENTIMENT", headers=auth(admin), json={"label": "Angry"}).status_code == 409
    positive = next(v for v in _values(client, admin, "SENTIMENT") if v["code"] == "positive")
    assert client.patch(f"/admin/lov/values/{positive['id']}", headers=auth(admin), json={"is_active": False}).status_code == 409
    r = client.patch(f"/admin/lov/values/{positive['id']}", headers=auth(admin), json={"label": "Happy", "sort_order": 9})
    assert r.status_code == 200 and r.json()["label"] == "Happy"
    client.patch(f"/admin/lov/values/{positive['id']}", headers=auth(admin), json={"label": "Positive", "sort_order": 0})


def test_unknown_list_and_value(client, admin):
    assert client.post("/admin/lov/NOPE", headers=auth(admin), json={"label": "x"}).status_code == 404
    assert client.patch("/admin/lov/values/999999", headers=auth(admin), json={"label": "x"}).status_code == 404


def test_seed_never_overwrites_admin_changes(db):
    master = db.query(OtherMasterName).filter(OtherMasterName.master_code == "USAGE_PERIOD").one()
    row = db.query(OtherMasterData).filter(OtherMasterData.master_id == master.id, OtherMasterData.data_code == "last_week").one()
    row.data_value = "Pichle hafte"
    db.commit()
    assert seed_lov() == 0
    db.expire_all()
    assert db.get(OtherMasterData, row.id).data_value == "Pichle hafte"
    row.data_value = "Less than 7 days"  # back to the default
    db.commit()


def test_a_renamed_default_label_is_updated_unless_an_admin_changed_it(db):
    """SURVEY_STATUS / needs_review: old default "Product check" becomes "Product to verify"."""
    master = db.query(OtherMasterName).filter(OtherMasterName.master_code == "SURVEY_STATUS").one()
    row = db.query(OtherMasterData).filter(OtherMasterData.master_id == master.id, OtherMasterData.data_code == "needs_review").one()
    row.data_value = "Product check"  # a database seeded before the rename
    db.commit()
    seed_lov()
    db.expire_all()
    assert db.get(OtherMasterData, row.id).data_value == "Product to verify"
    row = db.get(OtherMasterData, row.id)
    row.data_value = "Wrong product?"  # an admin's own label is kept
    db.commit()
    seed_lov()
    db.expire_all()
    assert db.get(OtherMasterData, row.id).data_value == "Wrong product?"
    row = db.get(OtherMasterData, row.id)
    row.data_value = "Product to verify"
    db.commit()


def test_the_usage_period_labels_are_renamed_unless_an_admin_changed_them(db):
    """USAGE_PERIOD: "In the last week" ... become "Less than 7 days" ... (codes unchanged)."""
    master = db.query(OtherMasterName).filter(OtherMasterName.master_code == "USAGE_PERIOD").one()
    rows = {r.data_code: r for r in db.query(OtherMasterData).filter(OtherMasterData.master_id == master.id)}
    rows["last_week"].data_value, rows["last_month"].data_value = "In the last week", "Pichla mahina"
    db.commit()
    seed_lov()
    db.expire_all()
    assert db.get(OtherMasterData, rows["last_week"].id).data_value == "Less than 7 days"
    assert db.get(OtherMasterData, rows["last_month"].id).data_value == "Pichla mahina", "an admin's label is kept"
    db.get(OtherMasterData, rows["last_month"].id).data_value = "For the past 1 month"
    db.commit()