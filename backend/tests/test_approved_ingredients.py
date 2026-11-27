"""Recipe standards list a product's approved ingredients; the recipe may use only those, each once."""
from conftest import _unique, auth, make_user

from app.database.models import Category, Product


def _product(db):
    cat = Category(name=_unique("AI cat "), is_active=1)
    db.add(cat)
    db.commit()
    p = Product(category_id=cat.id, name=_unique("AI product "), is_active=1)
    db.add(p)
    db.commit()
    return p


R = lambda *names: {"batch_size": "100 kg", "ingredients": [  # noqa: E731
    {"name": n, "quantity": str(100 / len(names)), "unit": "kg"} for n in names]}


def test_duplicates_are_refused_even_without_a_list(client, db):
    org, p = make_user(db, role="client"), _product(db)
    r = client.put(f"/products/{p.id}/recipe", headers=auth(org), json=R("Sugar", "sugar "))
    assert r.status_code == 400 and "twice" in r.json()["detail"]
    assert client.put(f"/products/{p.id}/recipe", headers=auth(org), json=R("Sugar", "Salt")).status_code == 200


def test_only_approved_ingredients_once_a_list_exists(client, db):
    org, admin, p = make_user(db, role="client"), make_user(db, role="admin"), _product(db)
    assert client.put(f"/products/{p.id}/approved-ingredients", headers=auth(org),
                      json={"ingredients": ["Sugar", " Salt ", "salt", "Turmeric Powder"]}).json()["ingredients"] == ["Sugar", "Salt", "Turmeric Powder"]
    assert client.put(f"/products/{p.id}/approved-ingredients", headers=auth(admin), json={"ingredients": []}).status_code == 403
    bad = client.put(f"/products/{p.id}/recipe", headers=auth(org), json=R("Sugar", "Pepper"))
    assert bad.status_code == 400 and "Pepper" in bad.json()["detail"]
    assert client.put(f"/products/{p.id}/recipe", headers=auth(org), json=R("Sugar", "salt")).status_code == 200
    got = client.get(f"/products/{p.id}/recipe", headers=auth(org)).json()
    assert got["approved_ingredients"] == ["Sugar", "Salt", "Turmeric Powder"]
    # One added by mistake can be removed even though the recipe uses it; the next save must drop it.
    assert client.put(f"/products/{p.id}/approved-ingredients", headers=auth(org), json={"ingredients": ["Sugar"]}).status_code == 200
    assert client.put(f"/products/{p.id}/recipe", headers=auth(org), json=R("Sugar", "Salt")).status_code == 400


def test_recipes_to_start_from(client, db):
    org, a, b = make_user(db, role="client"), _product(db), _product(db)
    client.put(f"/products/{a.id}/recipe", headers=auth(org), json=R("Corn", "Sugar"))
    sources = client.get(f"/products/{b.id}/recipe/sources", headers=auth(org)).json()["sources"]
    assert any(s["product_id"] == a.id and s["recipe"]["ingredients"] for s in sources)
    assert not any(s["product_id"] == b.id for s in sources)
