"""Survey locations are the Admin's: the Super Admin's plan has no cities; the Admin assigns each survey
person a city in the team, and the survey person may survey only there."""

from conftest import _unique, auth, make_user


def _project(client, org):
    cid = client.post("/categories", headers=auth(org), json={"name": _unique("Snacks ")}).json()["id"]
    pid = client.post("/products", headers=auth(org), json={"category_id": cid, "name": _unique("Masala Peanuts ")}).json()["id"]
    client.put(f"/products/{pid}/recipe", headers=auth(org), json={"batch_size": "100 kg", "ingredients": [{"name": "Peanuts", "quantity": "100", "unit": "kg"}]})
    for t in ("Taste?", "Spice?", "Crunch?", "Price?", "Buy again?"):
        client.post(f"/products/{pid}/questions", headers=auth(org), json={"text": t, "question_type": "rating"})
    return pid, client.post("/org/projects", headers=auth(org), json={"project_name": "Study", "category_id": cid, "product_id": pid,
                                                                       "total_target": 10}).json()["id"]


def test_the_admin_assigns_cities_and_the_super_admins_edits_keep_them(client, db, admin):
    org = make_user(db, role="client")
    pid, prj = _project(client, org)
    # the Super Admin's plan: responses, dates, audience; no cities
    plan = client.post(f"/org/projects/{prj}/plans", headers=auth(org), json={"target": 10}).json()["plans"][0]
    assert plan["cities"] == []
    # the Admin assigns two survey persons to two cities
    a, b = make_user(db, name=_unique("Ishaan ")), make_user(db, name=_unique("Suresh "))
    r = client.put(f"/admin/action-plans/{plan['id']}/members", headers=auth(admin), json={"members": [
        {"user_id": a.id, "cities": ["Greater Noida"], "target": 6}, {"user_id": b.id, "cities": ["Chhindwara"], "target": 4}]})
    assert r.status_code == 200, r.text
    view = next(p for p in client.get(f"/org/projects/{prj}/plans", headers=auth(org)).json()["plans"] if p["id"] == plan["id"])
    assert set(view["cities"]) == {"Greater Noida", "Chhindwara"}, "the plan shows where the Admin sent its team"
    assert {m["name"]: m["cities"] for m in view["members"]} == {a.name: ["Greater Noida"], b.name: ["Chhindwara"]}
    # the Super Admin edits the plan (no cities sent): the Admin's cities are kept
    e = client.put(f"/action-plans/{plan['id']}", headers=auth(org), json={"target": 10, "notes": "Only modern trade stores"})
    assert e.status_code == 200, e.text
    view = next(p for p in client.get(f"/org/projects/{prj}/plans", headers=auth(org)).json()["plans"] if p["id"] == plan["id"])
    assert {m["name"]: m["cities"] for m in view["members"]} == {a.name: ["Greater Noida"], b.name: ["Chhindwara"]}
    # started: each survey person may survey only in the Admin's city for them
    assert client.post(f"/org/projects/{prj}/start", headers=auth(org), json={}).status_code == 200
    resp = client.post("/surveys/respondents", headers=auth(a), json={"name": "Priya", "consent_given": True, "city": "Chhindwara"})
    if resp.status_code in (200, 201):
        made = client.post("/surveys/responses", headers=auth(a), json={"product_id": pid, "respondent_id": resp.json()["id"]})
        assert made.status_code == 403 and "Greater Noida" in made.json()["detail"], made.text