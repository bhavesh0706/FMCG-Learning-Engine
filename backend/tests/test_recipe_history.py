"""
Recipe version history, the admin bell when the organization changes a recipe,
and the "updated by the organization" badge data.

Recipes are changed only by the organization (its portal); admins read them.
Versions saved by admins before that rule exist in older databases: they are
created here directly in the database (_legacy_admin_version).
"""

from conftest import auth, make_user

from app.database.models import Notification, Product, RecipeVersion

BASE = {
    "batch_size": "10 kg",
    "ingredients": [{"name": "Oats", "quantity": "6", "unit": "kg"}, {"name": "Sugar", "quantity": "0.8", "unit": "kg"}],
    "steps": [{"title": "Mix"}, {"title": "Bake", "duration": "25 min", "temperature": "160 °C"}],
}


def _client_for(client, db, admin, product, name="Acme Foods"):
    """The organization's login: it sees every active product."""
    return make_user(db, role="client", name=name)


def _legacy_admin_version(db, product, admin, recipe):
    """A version an admin saved before recipes became organization-only (existing data)."""
    item = db.get(Product, product.id)
    item.recipe = recipe
    db.add(RecipeVersion(product_id=product.id, version=1, recipe=recipe, changed_by=admin.id,
                         changed_by_name=admin.name, changed_by_role="admin", changes=["First version"]))
    db.commit()


def _versions(client, user, product):
    r = client.get(f"/products/{product.id}/recipe/versions", headers=auth(user))
    assert r.status_code == 200, r.text
    return r.json()["versions"]


def test_admins_cannot_change_recipes(client, db, admin, alice, product):
    for person in (admin, alice):
        r = client.put(f"/products/{product.id}/recipe", headers=auth(person), json=BASE)
        assert r.status_code == 403 and r.json()["code"] == "organization_only"
    assert client.get(f"/products/{product.id}/recipe", headers=auth(admin)).status_code == 200  # reading is fine


def test_every_real_change_is_a_new_version(client, db, admin, product):
    brand = _client_for(client, db, admin, product, name="Acme Versions")
    assert client.put(f"/products/{product.id}/recipe", headers=auth(brand), json=BASE).json()["version"] == 1
    again = client.put(f"/products/{product.id}/recipe", headers=auth(brand), json=BASE).json()
    assert again["version"] is None  # nothing changed: no new version
    changed = {**BASE, "ingredients": [{"name": "Oats", "quantity": "6", "unit": "kg"},
                                        {"name": "Sugar", "quantity": "0.65", "unit": "kg"},
                                        {"name": "Cinnamon", "quantity": "20", "unit": "g"}],
               "steps": [{"title": "Mix"}, {"title": "Bake", "duration": "22 min", "temperature": "160 °C"}]}
    assert client.put(f"/products/{product.id}/recipe", headers=auth(brand), json=changed).json()["version"] == 2
    versions = _versions(client, admin, product)
    assert [v["version"] for v in versions] == [2, 1]
    assert versions[0]["changes"] == ["Sugar: 0.8 kg → 0.65 kg", "Added ingredient: Cinnamon (20 g)", "Step 2 Bake: time 25 min → 22 min"]
    assert versions[0]["changed_by_name"] == brand.name and versions[0]["changed_by_role"] == "client"
    assert versions[1]["recipe"]["ingredients"][1]["quantity"] == "0.8"  # the old version is kept in full


def test_removed_items_and_reordered_steps_are_described(client, db, admin, product):
    brand = _client_for(client, db, admin, product)
    client.put(f"/products/{product.id}/recipe", headers=auth(brand), json=BASE)
    changed = {**BASE, "ingredients": [{"name": "Oats", "quantity": "6", "unit": "kg"}],
               "steps": [{"title": "Bake", "duration": "25 min", "temperature": "160 °C"}, {"title": "Mix"}]}
    client.put(f"/products/{product.id}/recipe", headers=auth(brand), json=changed)
    assert _versions(client, admin, product)[0]["changes"] == ["Removed ingredient: Sugar", "Steps reordered"]


def test_recipe_from_before_history_becomes_version_1(client, db, admin, product):
    item = db.get(Product, product.id)
    item.recipe = {"ingredients": [{"name": "Oats", "quantity": "5", "unit": "kg"}], "steps": [{"title": "Mix"}]}
    db.commit()
    brand = _client_for(client, db, admin, product)
    client.put(f"/products/{product.id}/recipe", headers=auth(brand), json=BASE)
    versions = _versions(client, admin, product)
    assert [v["version"] for v in versions] == [2, 1]
    assert versions[1]["changes"] == ["Recipe as it was before version history"]
    assert "Oats: 5 kg → 6 kg" in versions[0]["changes"]


def test_organization_change_rings_the_admin_bell(client, db, admin, alice, product):
    other_admin = make_user(db, role="admin")
    brand = _client_for(client, db, admin, product, name="Priya (Acme Foods)")
    _legacy_admin_version(db, product, admin, BASE)  # an admin's (older) version: no bell
    db.expire_all()
    assert not db.query(Notification).filter(Notification.kind == "client_recipe_updated",
                                             Notification.dedupe_key.like(f"recipe:{product.id}:%")).count()

    changed = {**BASE, "ingredients": [{"name": "Oats", "quantity": "6", "unit": "kg"}, {"name": "Sugar", "quantity": "0.6", "unit": "kg"}]}
    assert client.put(f"/products/{product.id}/recipe", headers=auth(brand), json=changed).status_code == 200
    for person in (admin, other_admin):
        notes = client.get("/notifications", headers=auth(person)).json()["notifications"]
        note = next(n for n in notes if n["kind"] == "client_recipe_updated" and n["data"]["product_id"] == product.id)
        assert note["title"] == "Recipe updated by Priya (Acme Foods)"
        assert note["data"]["product_id"] == product.id and note["data"]["version"] == 2
        assert note["data"]["changes"] == ["Sugar: 0.8 kg → 0.6 kg"]
        assert not note["is_read"]
    assert not any(n["kind"] == "client_recipe_updated"
                   for n in client.get("/notifications", headers=auth(alice)).json()["notifications"])
    # The organization has no bell; it sees what happened on its Updates page.
    assert client.get("/notifications", headers=auth(brand)).status_code == 403


def test_catalog_shows_who_changed_the_recipe_last(client, db, admin, product):
    brand = _client_for(client, db, admin, product, name="Acme Badge")
    client.put(f"/products/{product.id}/recipe", headers=auth(brand), json=BASE)
    listed = {p["id"]: p for p in client.get("/products?include_inactive=true", headers=auth(admin)).json()["products"]}
    change = listed[product.id]["recipe_change"]
    assert change["by_role"] == "client" and change["by_name"] == "Acme Badge" and change["version"] == 1 and change["at"]
    one = client.get(f"/products/{product.id}", headers=auth(admin)).json()
    assert one["recipe_change"]["by_role"] == "client"


def test_organization_sees_history_with_research_team_names(client, db, admin, alice, product):
    from conftest import _unique
    from app.database.models import Category

    brand = _client_for(client, db, admin, product)
    _legacy_admin_version(db, product, admin, BASE)
    versions = _versions(client, brand, product)
    assert versions[0]["changed_by_name"] == "Research team"  # admins' names are not shown to the organization
    cat = Category(name=_unique("C "), is_active=1)
    db.add(cat)
    db.commit()
    old = Product(category_id=cat.id, name=_unique("Old "), is_active=0)
    db.add(old)
    db.commit()
    assert client.get(f"/products/{old.id}/recipe/versions", headers=auth(brand)).status_code == 404
    assert client.get(f"/products/{product.id}/recipe/versions", headers=auth(alice)).status_code == 403


def test_versions_are_stored_in_the_database(client, db, admin, product):
    brand = _client_for(client, db, admin, product)
    client.put(f"/products/{product.id}/recipe", headers=auth(brand), json=BASE)
    db.expire_all()
    row = db.query(RecipeVersion).filter_by(product_id=product.id).one()
    assert row.changed_by == brand.id and row.recipe["batch_size"] == "10 kg"
