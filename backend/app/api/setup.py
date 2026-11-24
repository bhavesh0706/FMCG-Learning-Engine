"""
Pending setup (Super Admin Dashboard): every product and project whose setup isn't finished,
with its steps, progress and the next action, computed from saved data (never from screens).

GET /org/setup   {"items": [...], "counts": {...}}

An item is one product recipe version:
  - no project yet for the product's latest recipe version: the product's own steps
    (category, product, recipe, recipe standards) and "Project" pending (Create project);
  - a project whose survey hasn't started (draft): the project's readiness checklist
    (api/projects.py _project_checks, the same one Start survey enforces) and "Start survey".
Projects whose survey is collecting, completed or ended are set up, so they are not listed.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.ownership import account
from app.api.projects import _plans_state, _step, recipe_steps
from app.database.database import get_db
from app.database.models import Category, Product, Project, RecipeVersion, product_first_version

router = APIRouter(prefix="/org/setup", tags=["Setup"])

STATUS = {  # the first step not done -> how the item is described
    "category": "Configuration pending", "product": "Configuration pending", "recipe": "Recipe pending",
    "standards": "Recipe standards pending", "project": "Project not created", "questions": "Survey questions pending",
    "target": "Response target pending", "plan": "Survey plan pending", "plans_cover": "Survey plan incomplete",
    "allocation": "Allocation pending", "start": "Ready to start",
}


def _next_step(steps: list):
    """The step to continue with: the first required one not done. Recipe standards come before the
    recipe, so a product without standards is first sent to set them (they stay optional)."""
    nxt = next((s for s in steps if not s["done"] and not s.get("optional")), None)
    if nxt is not None and nxt["key"] == "recipe":
        nxt = next((s for s in steps if s["key"] == "standards" and not s["done"]), nxt)
    return nxt


def _item(product: Product, category, version, project, steps: list) -> dict:
    required = [s for s in steps if not s.get("optional")]
    nxt = _next_step(steps)
    return {
        "key": f"{product.id}-v{version}-{project.id if project else 0}",
        "product": {"id": product.id, "name": product.name, "code": product.sku},
        "category": {"id": product.category_id, "name": category.name if category else None,
                     "code": category.project_id if category else None},
        "version": version,
        "project": {"id": project.id, "code": project.project_code, "name": project.project_name} if project else None,
        "done": sum(1 for s in required if s["done"]), "total": len(required),
        "status": STATUS.get(nxt["key"], "Pending") if nxt else "Ready to start",
        "next": nxt, "steps": steps,
    }


@router.get("")
def pending_setup(request: Request, db: Session = Depends(get_db)):
    if account(request).get("role") not in ("client", "admin"):
        raise HTTPException(status_code=403, detail="Only the Super Admin and Admins can see the setup.")
    cats = {c.id: c for c in db.query(Category)}
    products = (db.query(Product).filter(Product.is_active == 1).order_by(Product.created_at.desc(), Product.id.desc()).all())
    versions: dict = {}
    for pid, v in db.query(RecipeVersion.product_id, RecipeVersion.version):
        versions.setdefault(pid, []).append(v)
    projects: dict = {}
    for pr in db.query(Project).filter(Project.closed_at.is_(None)).order_by(Project.product_version):
        projects.setdefault(pr.product_id, []).append(pr)
    items = []
    for product in products:
        category = cats.get(product.category_id)
        if category is None or not category.is_active:
            continue
        mine = sorted(versions.get(product.id, []))
        latest = mine[-1] if mine else product_first_version(product)
        own = projects.get(product.id, [])
        if not any(p.product_version == latest for p in own) and \
                not db.query(Project.id).filter(Project.product_id == product.id, Project.product_version == latest).first():
            steps = recipe_steps(db, product, latest if mine else None)
            steps.append(_step("project", "Project", False, f"No project for recipe v{latest} yet: create it to plan its survey.",
                               {"kind": "add_project", "product_id": product.id, "category_id": product.category_id, "version": latest}))
            items.append(_item(product, category, latest, None, steps))
        for pr in own:
            state = _plans_state(db, pr)
            if state["stage"] != "draft":
                continue  # collecting, completed or ended: set up
            steps = list(state.get("checklist") or [])
            steps.append(_step("start", "Start survey", False,
                               "Everything is ready: start the survey." if state["can_start"] else
                               "Complete the steps above, then start the survey.",
                               {"kind": "project", "project_id": pr.id}))
            items.append(_item(product, category, pr.product_version, pr, steps))
    return {"items": items, "counts": {"items": len(items), "ready": sum(1 for i in items if i["status"] == "Ready to start"),
                                       "products": len({i["product"]["id"] for i in items})}}