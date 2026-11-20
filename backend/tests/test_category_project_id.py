"""categories.project_id: unique for every category; generated (CAT-####) when left empty; PRJ-#### is for projects."""

import re

from conftest import _unique, auth, make_user
from sqlalchemy import create_engine, inspect, text

from app.database.models import Category


def _org(db):
    return make_user(db, role="client", name=_unique("Acme "))


def test_a_new_category_gets_the_next_project_id(client, db):
    org = _org(db)
    first = client.post("/categories", headers=auth(org), json={"name": _unique("Cat ")}).json()
    second = client.post("/categories", headers=auth(org), json={"name": _unique("Cat ")}).json()
    assert re.fullmatch(r"CAT-\d{4}", first["project_id"])
    assert int(second["project_id"][4:]) == int(first["project_id"][4:]) + 1


def test_categories_made_in_code_get_one_too(db):
    cat = Category(name=_unique("Code cat "))
    db.add(cat)
    db.commit()
    assert cat.project_id.startswith("CAT-")


def test_project_id_can_be_given_and_must_be_unique(client, db):
    org = _org(db)
    pid = _unique("CLIENT-")
    r = client.post("/categories", headers=auth(org), json={"name": _unique("Cat "), "project_id": pid})
    assert r.status_code == 201 and r.json()["project_id"] == pid
    again = client.post("/categories", headers=auth(org), json={"name": _unique("Cat "), "project_id": pid.lower()})
    assert again.status_code == 409 and "already used" in again.json()["detail"]
    bad = client.post("/categories", headers=auth(org), json={"name": _unique("Cat "), "project_id": "has space"})
    assert bad.status_code == 422


def test_project_id_can_be_changed_but_not_to_one_in_use(client, db):
    org = _org(db)
    a = client.post("/categories", headers=auth(org), json={"name": _unique("Cat ")}).json()
    b = client.post("/categories", headers=auth(org), json={"name": _unique("Cat ")}).json()
    assert client.put(f"/categories/{b['id']}", headers=auth(org), json={"project_id": a["project_id"]}).status_code == 409
    new = _unique("NEW-")
    r = client.put(f"/categories/{b['id']}", headers=auth(org), json={"project_id": new})
    assert r.status_code == 200 and r.json()["project_id"] == new
    # Saving the same category with its own project ID is fine; other edits keep it.
    assert client.put(f"/categories/{b['id']}", headers=auth(org), json={"project_id": new, "name": _unique("Renamed ")}).status_code == 200
    assert client.put(f"/categories/{b['id']}", headers=auth(org), json={"description": "x"}).json()["project_id"] == new
    listed = {c["id"]: c["project_id"] for c in client.get("/categories", headers=auth(org)).json()["categories"]}
    assert listed[b["id"]] == new


def test_migration_fills_existing_categories(tmp_path, monkeypatch):
    from app.database import migrations

    eng = create_engine(f"sqlite:///{tmp_path / 'cats.db'}")
    with eng.begin() as c:
        c.execute(text("CREATE TABLE categories (record_no INTEGER PRIMARY KEY, name VARCHAR(150))"))
        c.execute(text("INSERT INTO categories VALUES (4, 'Food'), (2, 'Fragrance'), (9, 'Home')"))
    monkeypatch.setattr(migrations, "engine", eng)
    migrations.category_project_id()
    migrations.category_project_id()  # runs again without harm
    with eng.connect() as c:
        rows = c.execute(text("SELECT record_no, project_id FROM categories ORDER BY record_no")).all()
    assert rows == [(2, "CAT-0001"), (4, "CAT-0002"), (9, "CAT-0003")]
    assert any(ix["name"] == "ix_categories_project_id" and ix["unique"] for ix in inspect(eng).get_indexes("categories"))


def test_categories_saved_together_get_different_project_ids(db):
    cats = [Category(name=_unique("Together ")) for _ in range(3)]
    db.add_all(cats)
    db.commit()
    assert len({c.project_id for c in cats}) == 3