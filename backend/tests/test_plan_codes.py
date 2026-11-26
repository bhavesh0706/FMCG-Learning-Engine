"""Survey plans get a PLA### code numbered within their project: each project starts at PLA001."""

from conftest import _unique, auth, make_user


def _project(client, org):
    cid = client.post("/categories", headers=auth(org), json={"name": _unique("Dairy ")}).json()["id"]
    pid = client.post("/products", headers=auth(org), json={"category_id": cid, "name": _unique("Buttermilk ")}).json()["id"]
    client.put(f"/products/{pid}/recipe", headers=auth(org), json={"batch_size": "100 kg", "ingredients": [{"name": "Curd", "quantity": "100", "unit": "kg"}]})
    for t in ("Taste?", "Salt?", "Thickness?", "Price?", "Buy again?"):
        client.post(f"/products/{pid}/questions", headers=auth(org), json={"text": t, "question_type": "rating"})
    return client.post("/org/projects", headers=auth(org), json={"project_name": _unique("Taste test "), "category_id": cid,
                                                                  "product_id": pid, "total_target": 30}).json()["id"]


def _add(client, org, prj, _label=None):
    """Adds a plan without a name (as the screens do): its name is its code."""
    before = {p["id"] for p in client.get(f"/org/projects/{prj}/plans", headers=auth(org)).json()["plans"]}
    r = client.post(f"/org/projects/{prj}/plans", headers=auth(org), json={"target": 5, "cities": []})
    assert r.status_code in (200, 201), r.text
    plan = next(p for p in r.json()["plans"] if p["id"] not in before)
    assert plan["name"] == plan["code"], "the plan's name is its code"
    return plan


def test_each_project_numbers_its_plans_from_pla001(client, db):
    org = make_user(db, role="client")
    first = _project(client, org)
    assert _add(client, org, first, "Noida team")["code"] == "PLA001"
    b = _add(client, org, first, "Delhi team")
    assert b["code"] == "PLA002"
    second = _project(client, org)
    assert _add(client, org, second, "Pune team")["code"] == "PLA001", "a new project starts again at PLA001"
    # a deleted plan's number is given again within its project
    assert client.delete(f"/action-plans/{b['id']}", headers=auth(org)).status_code == 200
    assert _add(client, org, first, "Gurgaon team")["code"] == "PLA002"
    codes = [p["code"] for p in client.get(f"/org/projects/{first}/plans", headers=auth(org)).json()["plans"]]
    assert sorted(codes) == ["PLA001", "PLA002"]