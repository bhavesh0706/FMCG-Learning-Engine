"""The connected setup workflow: readiness from saved data, enforced on the server, and the
Dashboard's pending setup (GET /org/setup) with the next step to resume from."""

from conftest import _unique, auth, make_user


def _keys(item, done=False):
    return [s["key"] for s in item["steps"] if s["done"] is done]


def _item(client, org, pid, project_id=None):
    items = [i for i in client.get("/org/setup", headers=auth(org)).json()["items"] if i["product"]["id"] == pid]
    return next((i for i in items if (i["project"] or {}).get("id") == project_id), None)


def test_pending_setup_follows_every_step_until_the_survey_starts(client, db, admin, alice):
    org = make_user(db, role="client")
    cid = client.post("/categories", headers=auth(org), json={"name": _unique("Snacks ")}).json()["id"]
    pid = client.post("/products", headers=auth(org), json={"category_id": cid, "name": _unique("Masala Peanuts ")}).json()["id"]
    # 1. product just created: recipe standards first, then the recipe, then the project
    it = _item(client, org, pid)
    assert [s["key"] for s in it["steps"]] == ["category", "product", "standards", "recipe", "project"]
    assert it["status"] == "Recipe standards pending" and it["next"]["key"] == "standards"
    assert it["next"]["action"] == {"kind": "product_tab", "product_id": pid, "tab": "standards"}
    assert _keys(it, done=True) == ["category", "product"]
    # a standard set: done before any recipe; next comes the recipe
    rule0 = client.post("/recipe-standards", headers=auth(org), json={"name": "Peanuts required", "kind": "required_ingredient",
                                                                    "ingredient": "Peanuts", "product_id": pid}).json()
    it = _item(client, org, pid)
    assert _keys(it, done=True) == ["category", "product", "standards"] and it["next"]["key"] == "recipe"
    client.delete(f"/recipe-standards/{rule0['id']}", headers=auth(org))
    # 2. recipe saved: project not created (Create project, with category and product)
    client.put(f"/products/{pid}/recipe", headers=auth(org), json={"batch_size": "100 kg",
               "ingredients": [{"name": "Peanuts", "quantity": "100", "unit": "kg"}]})
    it = _item(client, org, pid)
    assert it["status"] == "Project not created" and it["next"]["action"]["kind"] == "add_project"
    assert it["next"]["action"]["category_id"] == cid and it["next"]["action"]["version"] == 1
    # 3. a failing standard is shown as pending (and blocks)
    rule = client.post("/recipe-standards", headers=auth(org), json={"name": "Salt required", "kind": "required_ingredient",
                                                                   "ingredient": "Salt", "product_id": pid}).json()
    it = _item(client, org, pid)
    assert it["next"]["key"] == "standards" and "Salt required" in it["next"]["detail"]
    client.delete(f"/recipe-standards/{rule['id']}", headers=auth(org))
    # 4. project created (questions are needed first): survey plan pending
    for i in range(5):
        client.post(f"/products/{pid}/questions", headers=auth(org), json={"text": f"Question number {i + 1}?", "question_type": "rating"})
    proj = client.post("/org/projects", headers=auth(org), json={"project_name": "Launch", "category_id": cid, "product_id": pid,
                                                                  "total_target": 10}).json()
    assert _item(client, org, pid) is None, "no duplicate 'Create project' once it exists"
    it = _item(client, org, pid, proj["id"])
    assert it["status"] == "Survey plan pending" and it["next"]["action"] == {"kind": "project", "project_id": proj["id"]}
    # 5. one survey plan (even below the target, team not allocated): ready to start; the Super Admin
    #    doesn't wait for the Admin, who allocates the team afterwards
    plan = client.post(f"/org/projects/{proj['id']}/plans", headers=auth(org),
                       json={"name": "Delhi team", "target": 6, "cities": []}).json()["plans"][0]
    it = _item(client, org, pid, proj["id"])
    assert it["status"] == "Ready to start" and it["next"]["key"] == "start"
    steps = {s["key"]: s for s in it["steps"]}
    assert steps["allocation"]["optional"] and not steps["allocation"]["done"] and "Delhi team" in steps["allocation"]["detail"]
    assert steps["plans_cover"]["optional"]
    # 6. started: no longer pending; the Admin can still allocate the team now
    assert client.post(f"/org/projects/{proj['id']}/start", headers=auth(org), json={}).status_code == 200
    assert client.put(f"/admin/action-plans/{plan['id']}/members", headers=auth(admin),
                      json={"members": [{"user_id": alice.id, "target": 6}]}).status_code == 200
    assert _item(client, org, pid, proj["id"]) is None and _item(client, org, pid) is None


def test_the_direct_start_route_cant_bypass_a_projects_readiness(client, db):
    org = make_user(db, role="client")
    cid = client.post("/categories", headers=auth(org), json={"name": _unique("Snacks ")}).json()["id"]
    pid = client.post("/products", headers=auth(org), json={"category_id": cid, "name": _unique("Chips ")}).json()["id"]
    client.put(f"/products/{pid}/recipe", headers=auth(org), json={"batch_size": "100 kg",
               "ingredients": [{"name": "Potato", "quantity": "100", "unit": "kg"}]})
    for i in range(5):
        client.post(f"/products/{pid}/questions", headers=auth(org), json={"text": f"Question number {i + 1}?", "question_type": "rating"})
    proj = client.post("/org/projects", headers=auth(org), json={"project_name": "Launch", "category_id": cid, "product_id": pid,
                                                                  "total_target": 10}).json()
    r = client.post(f"/org/products/{pid}/rounds", headers=auth(org), json={"target": 10})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "use_project" and r.json()["detail"]["project_id"] == proj["id"]


def test_only_the_super_admin_and_admins_see_the_setup(client, db, alice):
    assert client.get("/org/setup", headers=auth(alice)).status_code == 403