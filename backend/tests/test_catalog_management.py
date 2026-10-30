"""
Catalog management belongs to the organization (its portal's Catalog page).

  - Only the organization adds, changes, deactivates, reactivates and deletes products
    and categories; admins and survey persons get 403 (code "organization_only").
  - Admins still read the catalog and manage who can survey each product.
  - Every organization change notifies every active admin (bell, kind
    "client_catalog_changed") with what changed; saves without changes and refused
    changes notify nobody.
"""

import pytest
from conftest import _unique, auth, make_user, open_round

from app.database.models import Category, Notification, Product


@pytest.fixture()
def org(db):
    return make_user(db, role="client", name=_unique("Acme "))


def _bell(db, user, kind="client_catalog_changed"):
    db.expire_all()
    return db.query(Notification).filter(Notification.user_id == user.id, Notification.kind == kind).order_by(Notification.id).all()


# ---------------------------------------------------------------- who may change the catalog


def test_admins_and_survey_persons_cannot_change_products_or_categories(client, db, admin, alice, product):
    cat_id = product.category_id
    for person in (admin, alice):
        attempts = [
            client.post("/categories", headers=auth(person), json={"name": _unique("Cat ")}),
            client.put(f"/categories/{cat_id}", headers=auth(person), json={"name": "Renamed"}),
            client.delete(f"/categories/{cat_id}", headers=auth(person)),
            client.delete(f"/categories/{cat_id}/permanent", headers=auth(person)),
            client.post("/products", headers=auth(person), json={"category_id": cat_id, "name": _unique("Prod ")}),
            client.put(f"/products/{product.id}", headers=auth(person), json={"name": "Renamed"}),
            client.delete(f"/products/{product.id}", headers=auth(person)),
            client.delete(f"/products/{product.id}/permanent", headers=auth(person)),
        ]
        assert [r.status_code for r in attempts] == [403] * len(attempts), [r.text for r in attempts]
        assert all(r.json().get("code") == "organization_only" for r in attempts)
    db.expire_all()
    assert db.get(Product, product.id).name == product.name and db.get(Product, product.id).is_active
    assert db.get(Category, cat_id).is_active


def test_admins_see_a_product_once_its_round_starts_and_assign_it(client, db, admin, alice, product):
    assert client.get("/categories", headers=auth(admin)).status_code == 200
    assert client.get("/products", headers=auth(admin)).status_code == 200
    # Before the Super Admin starts a round, admins don't see the product.
    assert client.get(f"/products/{product.id}", headers=auth(admin)).status_code == 403
    assert client.put(f"/admin/products/{product.id}/executives", headers=auth(admin), json={"user_ids": [alice.id]}).status_code == 404
    open_round(db, product)
    assert client.get(f"/products/{product.id}", headers=auth(admin)).status_code == 200
    r = client.put(f"/admin/products/{product.id}/executives", headers=auth(admin), json={"user_ids": [alice.id]})
    assert r.status_code == 200


def test_the_organization_manages_the_whole_catalog(client, db, org, product):
    assert client.get("/categories", headers=auth(org)).status_code == 200
    assert client.get("/products", headers=auth(org)).status_code == 200
    cat = client.post("/categories", headers=auth(org), json={"name": _unique("Snacks "), "description": "Crisps"})
    assert cat.status_code == 201
    cid = cat.json()["id"]
    assert client.put(f"/categories/{cid}", headers=auth(org), json={"description": "Crisps and namkeen"}).status_code == 200
    p = client.post("/products", headers=auth(org), json={"category_id": cid, "name": _unique("Masala Chips "), "sku": _unique("SKU-")})
    assert p.status_code == 201
    pid = p.json()["id"]
    assert client.put(f"/products/{pid}", headers=auth(org), json={"description": "Spicy", "min_responses": 20}).status_code == 200
    assert client.get(f"/products/{pid}", headers=auth(org)).json()["min_responses"] == 20
    assert client.delete(f"/products/{pid}", headers=auth(org)).status_code == 200
    assert client.put(f"/products/{pid}", headers=auth(org), json={"is_active": True}).status_code == 200
    assert client.delete(f"/products/{pid}", headers=auth(org)).status_code == 200
    assert client.delete(f"/products/{pid}/permanent", headers=auth(org)).status_code == 200
    assert client.delete(f"/categories/{cid}", headers=auth(org)).status_code == 200
    assert client.delete(f"/categories/{cid}/permanent", headers=auth(org)).status_code == 200
    db.expire_all()
    assert db.get(Product, pid) is None and db.get(Category, cid) is None


# ---------------------------------------------------------------- admin notifications


def test_every_organization_change_notifies_the_admins_with_what_changed(client, db, admin, org, product):
    other_admin = make_user(db, role="admin")
    cid = client.post("/categories", headers=auth(org), json={"name": _unique("Drinks ")}).json()["id"]
    pid = client.post("/products", headers=auth(org), json={"category_id": cid, "name": "Mango Lassi", "sku": _unique("LAS-")}).json()["id"]
    open_round(db, db.get(Product, pid))  # admins see the product (and its changes) once a round has started
    client.put(f"/products/{pid}", headers=auth(org), json={"name": "Mango Lassi 200 ml", "category_id": product.category_id})
    client.delete(f"/products/{pid}", headers=auth(org))
    client.put(f"/products/{pid}", headers=auth(org), json={"is_active": True})
    client.put(f"/categories/{cid}", headers=auth(org), json={"name": "Dairy drinks"})

    for person in (admin, other_admin):
        bell = _bell(db, person)
        titles = [n.title for n in bell]
        assert titles == [
            f"Category added by {org.name}",
            # no "Product added": admins couldn't see the product before its first round
            f"Product updated by {org.name}",
            f"Product deactivated by {org.name}",
            f"Product reactivated by {org.name}",
            f"Category updated by {org.name}",
        ], titles
    update = _bell(db, admin)[1]
    assert "Name: Mango Lassi → Mango Lassi 200 ml" in update.data["changes"]
    assert any(c.startswith("Category: Drinks") for c in update.data["changes"])
    assert update.data["product_id"] == pid and update.data["action"] == "updated"
    assert "Mango Lassi 200 ml" in update.message


def test_deletions_are_reported_by_name(client, db, admin, org):
    cid = client.post("/categories", headers=auth(org), json={"name": _unique("Temp ")}).json()["id"]
    pid = client.post("/products", headers=auth(org), json={"category_id": cid, "name": "Typo Prodcut"}).json()["id"]
    open_round(db, db.get(Product, pid))  # the admins had seen it
    client.delete(f"/products/{pid}", headers=auth(org))
    client.delete(f"/products/{pid}/permanent", headers=auth(org))
    last = _bell(db, admin)[-1]
    assert last.title == f"Product permanently deleted by {org.name}" and "Typo Prodcut" in last.message


def test_no_notification_without_a_change_or_when_the_change_is_refused(client, db, admin, org, product):
    before = len(_bell(db, admin))
    # Saving the same values: nothing changed.
    assert client.put(f"/products/{product.id}", headers=auth(org), json={"name": product.name}).status_code == 200
    # A duplicate category name: refused, nothing saved.
    name = _unique("Dup ")
    client.post("/categories", headers=auth(org), json={"name": name})
    assert client.post("/categories", headers=auth(org), json={"name": name}).status_code == 409
    after = _bell(db, admin)
    assert len(after) == before + 1  # only the first category
    assert after[-1].title == f"Category added by {org.name}"


def test_only_active_admins_are_notified(client, db, admin, org, alice, product):
    retired = make_user(db, role="admin")
    retired.is_active = 0
    db.commit()
    open_round(db, product)
    client.put(f"/products/{product.id}", headers=auth(org), json={"description": "New pack"})
    assert len(_bell(db, admin)) == 1
    assert _bell(db, retired) == [] and _bell(db, alice) == [] and _bell(db, org) == []