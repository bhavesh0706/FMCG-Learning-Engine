"""
Projects (Super Admin portal, Projects tab).

A project is the study of ONE VERSION of one product: (product_id, product_version),
where the version is the product's recipe version (recipe_versions.version). A new
product version has at most one project, made by the Super Admin on the Add project page
(never automatically when a product or recipe is saved). The product's standards, recipe
plan and survey questions are made in the Catalog; the project shows them linked,
with the recipe OF ITS VERSION, and its own survey rounds (rounds of that version).

GET    /org/projects            KPIs (total, ongoing, completed, draft) and every project
GET    /org/projects/options    categories with their product versions that have no project
GET    /org/projects/product-summary?product_id=&version=   a short overview of a product recipe
                                (recipe, standards, questions, plan, surveys) for the Add project page
POST   /org/projects            {project_name, description, category_id, product_id, product_version?}
GET    /org/projects/{id}       one project, with the projects of the product's other versions
PUT    /org/projects/{id}       {project_name, description}
DELETE /org/projects/{id}       the whole project: its plans, and its surveys that have no responses
                                (collected responses are never deleted: they stay with the product)
GET    /org/projects/{id}/plans the project's Survey plan: its action plans (planned before the
                                survey starts, then the live survey's), and whether it can start
POST   /org/projects/{id}/plans add a plan (ActionPlanBody; to the live survey once it runs)
POST   /org/projects/{id}/start {due_date?, note?}: start the survey of the project's version with
                                its plans (target = their sum). Surveys start only from a project.

Status (never stored, always from the rounds of the project's version):
    draft      no survey round yet
    ongoing    a round is collecting surveys
    completed  rounds were run and none is collecting
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.action_plans import ActionPlanBody
from app.api.ownership import account
from app.database.database import get_db
from app.database.models import (
    ActionPlan,
    Category,
    Product,
    Project,
    RecipeVersion,
    SurveyResponse,
    SurveyRound,
    User,
    product_first_version,
)
from app.services import action_plans as P
from app.services import audit
from app.services import rounds as R

router = APIRouter(prefix="/org/projects", tags=["Projects"])

STATUS_LABEL = {"draft": "Draft", "ongoing": "Ongoing", "completed": "Completed"}


def _client(request: Request) -> dict:
    me = account(request)
    if me.get("role") != "client":
        raise HTTPException(status_code=403, detail="Projects are managed by the Super Admin.")
    return me


def _clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = " ".join(str(value).split())
    return value or None


class ProjectIn(BaseModel):
    project_name: str = Field(min_length=2, max_length=200)
    description: Optional[str] = Field(default=None, max_length=4000)
    category_id: int
    product_id: int
    # The product version (recipe version) to study; default: the product's latest.
    product_version: Optional[int] = Field(default=None, ge=1)
    # Total consumer responses this project needs (a positive whole number); default: the
    # product's recipe-plan responses, if set. Survey plans can't add up to more.
    total_target: Any = None

    _c = field_validator("project_name", "description", mode="before")(_clean)


class TargetIn(BaseModel):
    total_target: Any = None


class ProjectUpdate(BaseModel):
    project_name: Optional[str] = Field(default=None, min_length=2, max_length=200)
    description: Optional[str] = Field(default=None, max_length=4000)

    _c = field_validator("project_name", "description", mode="before")(_clean)


def _iso(value) -> Optional[str]:
    return value.isoformat(timespec="seconds") + "Z" if value else None


def _versions(db: Session, product_ids) -> dict:
    """{product_id: {version: RecipeVersion}} for many products at once."""
    out: dict = {pid: {} for pid in product_ids}
    if product_ids:
        for v in db.query(RecipeVersion).filter(RecipeVersion.product_id.in_(list(product_ids))):
            out.setdefault(v.product_id, {})[v.version] = v
    return out


def _round_info(db: Session, projects: list) -> dict:
    """{project.id: {"rounds": n, "live": {...} | None, "latest_round": n | None}}: the rounds
    of each project's product VERSION. Rounds saved before versions existed (no
    recipe_version) belong to the product's first version."""
    out = {p.id: {"rounds": 0, "live": None, "latest_round": None, "responses": 0} for p in projects}
    if not projects:
        return out
    by_key = {(p.product_id, p.product_version): p.id for p in projects}
    pids = {p.product_id for p in projects}
    firsts = {x.id: product_first_version(x) for x in db.query(Product).filter(Product.id.in_(pids))}
    rows = db.query(SurveyRound).filter(SurveyRound.product_id.in_(pids)).all()
    live = [r for r in rows if r.status == R.LIVE]
    done = R.done_counts(db, [r.id for r in live])
    from sqlalchemy import func as _f

    answered = dict(db.query(SurveyResponse.round_id, _f.count(SurveyResponse.id))
                    .filter(SurveyResponse.round_id.in_([r.id for r in rows] or [-1])).group_by(SurveyResponse.round_id))
    for r in sorted(rows, key=lambda r: r.number):
        key = (r.product_id, r.recipe_version if r.recipe_version is not None else firsts.get(r.product_id, 1))
        pid = by_key.get(key)
        if pid is None:
            continue
        info = out[pid]
        info["rounds"] += 1
        info["latest_round"] = r.number
        info["responses"] += answered.get(r.id, 0)
        if r.status == R.LIVE:
            info["live"] = {"number": r.number, "done": done.get(r.id, 0), "target": r.target,
                            "recipe_version": r.recipe_version}
    return out


def _status(info: dict) -> str:
    return "ongoing" if info["live"] else "completed" if info["rounds"] else "draft"


def _out(p: Project, category: Optional[Category], product: Optional[Product], info: dict, names: dict,
         version: Optional[RecipeVersion], latest_version: Optional[int]) -> dict:
    status = _status(info)
    return {
        "id": p.id,
        "project_code": p.project_code,
        "project_name": p.project_name,
        "description": p.description,
        "category": {"id": p.category_id, "name": category.name if category else None,
                     "code": category.project_id if category else None},
        "product": {"id": p.product_id, "name": product.name if product else None, "sku": product.sku if product else None,
                    "is_active": bool(product.is_active) if product else False},
        # Which version of the product this project studies.
        "product_version": p.product_version,
        "version_label": f"v{p.product_version}",
        "is_latest_version": latest_version is None or p.product_version >= latest_version,
        "latest_version": latest_version,
        "version": {
            "saved_at": _iso(version.created_at) if version else None,
            "saved_by": version.changed_by_name if version else None,
            "changes": (version.changes or []) if version else [],
            "has_recipe": bool(version and version.recipe),
        },
        "status": status,
        "status_label": STATUS_LABEL[status],
        "rounds": info["rounds"],
        "total_target": p.total_target,
        # Deletable while no consumer feedback has come in for it (its survey plans go first).
        "can_delete": info["responses"] == 0,
        "responses": info["responses"],
        "live_round": info["live"],
        "latest_round": info["latest_round"],
        "created_at": _iso(p.created_at),
        "created_by": names.get(p.created_by),
        "mod_at": _iso(p.mod_at),
        "mod_by": names.get(p.mod_by),
    }


def _many_out(db: Session, projects: list) -> list:
    cats = {c.id: c for c in db.query(Category).filter(Category.id.in_({p.category_id for p in projects} or {-1}))}
    prods = {x.id: x for x in db.query(Product).filter(Product.id.in_({p.product_id for p in projects} or {-1}))}
    users = {u for p in projects for u in (p.created_by, p.mod_by) if u}
    names = {u.id: u.name for u in db.query(User).filter(User.id.in_(users or {-1}))}
    info = _round_info(db, projects)
    versions = _versions(db, {p.product_id for p in projects})
    out = []
    for p in projects:
        mine = versions.get(p.product_id, {})
        out.append(_out(p, cats.get(p.category_id), prods.get(p.product_id), info[p.id], names,
                        mine.get(p.product_version), max(mine) if mine else None))
    return out


def _get(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    return project


@router.get("")
def list_projects(request: Request, db: Session = Depends(get_db)):
    _client(request)
    projects = db.query(Project).order_by(Project.created_at.desc(), Project.product_version.desc(), Project.id.desc()).all()
    rows = _many_out(db, projects)
    kpis = {"total": len(rows), **{k: sum(1 for r in rows if r["status"] == k) for k in STATUS_LABEL}}
    return {"kpis": kpis, "projects": rows}


@router.get("/options")
def project_options(request: Request, db: Session = Depends(get_db)):
    """Active categories with their active products and the versions that have no project
    (projects are made automatically, so this lists only ones deleted while still drafts)."""
    _client(request)
    taken = {(pid, v) for (pid, v) in db.query(Project.product_id, Project.product_version)}
    cats = db.query(Category).filter(Category.is_active == 1).order_by(Category.name).all()
    products = db.query(Product).filter(Product.is_active == 1).order_by(Product.name).all()
    versions = _versions(db, [p.id for p in products])
    out = []
    for c in cats:
        free = []
        for p in products:
            if p.category_id != c.id:
                continue
            numbers = sorted(versions.get(p.id) or {}) or [product_first_version(p)]
            open_versions = [v for v in numbers if (p.id, v) not in taken]
            if open_versions:
                free.append({"id": p.id, "name": p.name, "sku": p.sku, "versions": open_versions})
        out.append({"id": c.id, "name": c.name, "code": c.project_id, "products": free})
    return {"categories": out}


@router.get("/product-summary")
def product_summary(product_id: int, request: Request, version: Optional[int] = None, db: Session = Depends(get_db)):
    """What the Super Admin should know about a product recipe before making its project."""
    from sqlalchemy import or_

    from app.database.models import RecipeStandard, SurveyQuestion

    _client(request)
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found.")
    category = db.get(Category, product.category_id)
    versions = {v.version: v for v in db.query(RecipeVersion).filter(RecipeVersion.product_id == product.id)}
    number = version or (max(versions) if versions else product_first_version(product))
    saved = versions.get(number)
    recipe = (saved.recipe if saved else product.recipe) or {}
    ingredients = [i for i in (recipe.get("ingredients") or []) if isinstance(i, dict) and (i.get("name") or "").strip()]

    def amount(i):
        qty, unit = str(i.get("quantity") or "").strip(), str(i.get("unit") or "").strip()
        return f"{qty} {unit}".strip()

    standards = (db.query(RecipeStandard).filter(
        RecipeStandard.is_active == 1,
        or_(RecipeStandard.product_id == product.id, RecipeStandard.category_id == product.category_id,
            RecipeStandard.applies_to_all == 1)).order_by(RecipeStandard.id).all())
    failed = R.standards_failures(db, product, number) if saved else []
    questions = (db.query(SurveyQuestion).filter(SurveyQuestion.product_id == product.id, SurveyQuestion.is_active == 1)
                 .order_by(SurveyQuestion.position).all())
    plan = product.recipe_plan or {}
    rounds = db.query(SurveyRound).filter(SurveyRound.product_id == product.id).order_by(SurveyRound.number).all()
    done = R.done_counts(db, [r.id for r in rounds]) if rounds else {}
    return {
        "product": {"id": product.id, "name": product.name, "sku": product.sku, "is_active": bool(product.is_active),
                    "category": category.name if category else None, "description": (product.description or "").strip() or None},
        "recipe": {
            "version": number, "versions": sorted(versions), "saved_at": _iso(saved.created_at) if saved else None,
            "saved_by": saved.changed_by_name if saved else None, "changes": (saved.changes or [])[:3] if saved else [],
            "batch_size": recipe.get("batch_size"), "ingredient_count": len(ingredients),
            "ingredients": [{"name": i["name"].strip(), "amount": amount(i)} for i in ingredients[:6]],
            "steps": len(recipe.get("steps") or []), "has_recipe": bool(ingredients),
        },
        "standards": {"count": len(standards), "failed": len(failed),
                      "names": [s.name for s in standards[:4]]},
        "questions": {"count": len(questions), "sample": [q.text for q in questions[:3]]},
        "plan": {"target": plan.get("target"), "persons_needed": plan.get("persons_needed"),
                 "start_date": plan.get("start_date"), "end_date": plan.get("end_date"),
                 "cities": plan.get("cities") or [], "audience": plan.get("audience"), "notes": plan.get("notes")},
        "surveys": [{"number": r.number, "recipe_version": r.recipe_version, "status": r.status,
                     "done": done.get(r.id, 0), "target": r.target} for r in rounds[-3:]],
    }


@router.post("", status_code=201)
def create_project(body: ProjectIn, request: Request, db: Session = Depends(get_db)):
    _client(request)
    category = db.get(Category, body.category_id)
    if category is None or not category.is_active:
        raise HTTPException(status_code=400, detail="Choose an active category.")
    product = db.get(Product, body.product_id)
    if product is None or not product.is_active:
        raise HTTPException(status_code=400, detail="Choose an active product.")
    if product.category_id != category.id:
        raise HTTPException(status_code=400, detail=f"{product.name} is not in the category {category.name}.")
    numbers = sorted(_versions(db, [product.id]).get(product.id) or {}) or [product_first_version(product)]
    version = body.product_version or numbers[-1]
    if version not in numbers:
        raise HTTPException(status_code=400, detail=f"{product.name} has no version {version} "
                                                    f"(versions: {', '.join(f'v{n}' for n in numbers)}).")
    existing = db.query(Project).filter(Project.product_id == product.id, Project.product_version == version).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"{product.name} v{version} already has a project: {existing.project_code} "
                                                    f"“{existing.project_name}”. Each product version has one project.")
    try:
        total = P._int(body.total_target, "Total consumer responses") if body.total_target not in (None, "") else P.product_total(product)
    except P.PlanError as e:
        raise HTTPException(status_code=400, detail=str(e))
    project = Project(project_name=body.project_name, description=body.description, category_id=category.id,
                      product_id=product.id, product_version=version, total_target=total)
    db.add(project)
    try:
        db.commit()
    except IntegrityError:  # the same product version chosen twice at the same moment
        db.rollback()
        raise HTTPException(status_code=409, detail=f"{product.name} v{version} already has a project. Each product version has one project.")
    db.refresh(project)
    audit.note(request, target_id=project.id,
               summary=f"Created project {project.project_code} “{project.project_name}” for {product.name} v{version} ({category.name})")
    _tell_admins_new_project(db, project, product)
    return _many_out(db, [project])[0]


def _tell_admins_new_project(db: Session, project: Project, product: Product) -> None:
    """Every active Admin (they all run the field teams): a new project to review and staff."""
    from app.services.notifications import notify

    admins = [uid for (uid,) in db.query(User.id).filter(User.role == "admin", User.is_active == 1)]
    notify(db, admins, "project_created", title=f"New project {project.project_code}: {project.project_name}",
           message=(f"The Super Admin created a project for {product.name} (recipe v{project.product_version})"
                    + (f", {project.total_target} consumer responses" if project.total_target else "")
                    + ". Review it and allocate the survey team to its plans."),
           data={"project_id": project.id, "project_code": project.project_code, "product_id": product.id,
                 "product_name": product.name, "recipe_version": project.product_version, "target": project.total_target},
           dedupe_key=f"project_created:{project.id}")


def _linked(db: Session, product: Optional[Product], project: Project) -> dict:
    """What the project shows: the recipe OF ITS VERSION; the product's standards, recipe
    plan and questions as they are now (linked, never copied); action plans of its version's
    latest survey round."""
    from sqlalchemy import or_

    from app.database.models import ActionPlan, RecipeStandard, SurveyQuestion

    if product is None:
        return {}
    saved = db.query(RecipeVersion).filter(RecipeVersion.product_id == product.id,
                                           RecipeVersion.version == project.product_version).first()
    recipe = (saved.recipe if saved else product.recipe) or {}
    first = product_first_version(product)
    rq = db.query(SurveyRound).filter(SurveyRound.product_id == product.id)
    rq = rq.filter(SurveyRound.recipe_version == project.product_version) if project.product_version != first else \
        rq.filter(or_(SurveyRound.recipe_version == project.product_version, SurveyRound.recipe_version.is_(None)))
    rnd = rq.order_by(SurveyRound.number.desc()).first()
    return {
        "recipe_ingredients": len(recipe.get("ingredients") or []),
        "recipe_steps": len(recipe.get("steps") or []),
        "standards": db.query(RecipeStandard).filter(
            RecipeStandard.is_active == 1,
            or_(RecipeStandard.product_id == product.id, RecipeStandard.category_id == product.category_id,
                RecipeStandard.applies_to_all == 1)).count(),
        "has_recipe_plan": bool(product.recipe_plan),
        "action_plans": db.query(ActionPlan).filter(ActionPlan.round_id == rnd.id).count() if rnd else 0,
        "action_plans_round": rnd.number if rnd else None,
        "questions": db.query(SurveyQuestion).filter(SurveyQuestion.product_id == product.id, SurveyQuestion.is_active == 1).count(),
    }


@router.get("/{project_id}")
def get_project(project_id: int, request: Request, db: Session = Depends(get_db)):
    _client(request)
    project = _get(db, project_id)
    siblings = (db.query(Project).filter(Project.product_id == project.product_id)
                .order_by(Project.product_version).all())
    versions = [{"id": s["id"], "project_code": s["project_code"], "project_name": s["project_name"],
                 "product_version": s["product_version"], "status": s["status"], "status_label": s["status_label"]}
                for s in _many_out(db, siblings)]
    return {**_many_out(db, [project])[0], "linked": _linked(db, db.get(Product, project.product_id), project),
            "versions": versions}


@router.put("/{project_id}/target")
def edit_response_target(project_id: int, body: TargetIn, request: Request, db: Session = Depends(get_db)):
    """Edit response target (Super Admin only): a positive whole number, never below the responses
    already collected or the responses already committed to the project's survey plans. Raised
    after the project's survey ended, that survey reopens for the extra responses."""
    _client(request)
    project = _get(db, project_id)
    try:
        new = P._int(body.total_target, "Response target")
    except P.PlanError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if new is None:
        raise HTTPException(status_code=400, detail="Enter the response target (a whole number from 1).")
    state = _plans_state(db, project)
    rounds = _version_rounds(db, project, db.get(Product, project.product_id))
    collected = db.query(SurveyResponse.id).filter(SurveyResponse.round_id.in_([r.id for r in rounds] or [-1])).count()
    committed = (state.get("used") or 0) + (state.get("planned") or 0)
    floor = max(collected, committed)
    if new < floor:
        why = (f"{collected} consumer response{'s' if collected != 1 else ''} already collected" if collected >= committed
               else f"{committed} consumer responses already committed to its survey plans")
        raise HTTPException(status_code=409, detail=f"The response target can't go below {floor}: {why}.")
    old = project.total_target
    project.total_target = new
    db.flush()
    # Raised above what it was (and above what the completed survey covered): the survey reopens.
    reopened = P.reopen_for_new_total(db, project) if state["stage"] == "ended" and new > (state.get("reopen_above") or 0) else None
    db.commit()
    audit.note(request, target_id=project.id,
               summary=f"Response target of {project.project_code}: {old if old is not None else 'not set'} → {new}"
                       + (f"; Survey {reopened.number} reopened for {reopened.target} consumers" if reopened else ""))
    return {**_plans_state(db, project), "total_target": new, "previous_target": old,
            "reopened": {"number": reopened.number, "target": reopened.target} if reopened else None}


@router.put("/{project_id}")
def update_project(project_id: int, body: ProjectUpdate, request: Request, db: Session = Depends(get_db)):
    _client(request)
    project = _get(db, project_id)
    data = body.model_dump(exclude_unset=True)
    if data.get("project_name"):
        project.project_name = data["project_name"]
    if "description" in data:
        project.description = data["description"]
    db.commit()
    audit.note(request, target_id=project.id, summary=f"Changed project {project.project_code} “{project.project_name}”")
    return _many_out(db, [project])[0]


def _version_rounds(db: Session, project: Project, product: Optional[Product]) -> list:
    """The survey rounds of the project's version (oldest first); rounds from before versions
    existed (no recipe_version) belong to the first version."""
    from sqlalchemy import or_

    q = db.query(SurveyRound).filter(SurveyRound.product_id == project.product_id)
    if product is not None and project.product_version == product_first_version(product):
        q = q.filter(or_(SurveyRound.recipe_version == project.product_version, SurveyRound.recipe_version.is_(None)))
    else:
        q = q.filter(SurveyRound.recipe_version == project.product_version)
    return q.order_by(SurveyRound.number).all()


def _plans_state(db: Session, project: Project) -> dict:
    """The Survey plan page: the project's plans and what can be done now."""
    product = db.get(Product, project.product_id)
    rounds = _version_rounds(db, project, product)
    live = next((r for r in rounds if r.status == R.LIVE), None)
    numbers = sorted({v for (v,) in db.query(RecipeVersion.version).filter(RecipeVersion.product_id == project.product_id)})
    last = rounds[-1] if rounds else None
    done = R.done_counts(db, [r.id for r in rounds]) if rounds else {}

    def round_info(r):
        return {"number": r.number, "status": r.status, "target": r.target, "done": done.get(r.id, 0),
                "recipe_version": r.recipe_version, "started_at": _iso(r.started_at), "closed_at": _iso(r.closed_at)}

    total = project.total_target  # the project's own response target (never another version's)
    if live is not None:
        planned = sum(p.target or 0 for p in P.plans_of_round(db, live.id))
        return {"stage": "live", "round": round_info(live), "plans": P.round_plans_out(db, live.id),
                "can_edit": True, "can_add_plans": True, "can_start": False, "start_problem": None, "target": live.target,
                "total_target": total, "used": 0, "available": total, "planned": planned, "surveys": [round_info(r) for r in rounds]}
    drafts = P.draft_plans(db, project.id)
    # After an ended survey the project is closed until the Super Admin raises the product's
    # total above what it was then; the next survey plans only that increase.
    used = R.reopen_baseline(last, done.get(last.id, 0)) if last else 0
    available = None if (total is None and not used) else (total or 0) - used
    # A completed survey stays completed: the project surveys again only by reopening it, which
    # happens when the Super Admin raises the response target above what it is now.
    reopened = last is None
    reopen_above = max(used, total or 0) if last else 0
    problem = None
    other_live = R.live_round(db, project.product_id) if product else None
    if product is None:
        problem = "The product of this project no longer exists."
    elif other_live is not None:
        problem = f"Survey {other_live.number} of {product.name} is still collecting responses."
    elif not reopened:
        problem = (f"Survey {last.number} is completed. To survey again, raise the project's response target above {reopen_above} "
                   f"(Edit response target): Survey {last.number} reopens for the extra responses.")
    elif not numbers and not product.recipe:
        problem = "Add the product's recipe first (Products > Recipe), so the survey knows what consumers taste."
    elif not drafts:
        problem = "Add at least one survey plan: how many consumers, where and when."
    target = sum(p.target or 0 for p in drafts)
    if drafts and not problem and not R.MIN_TARGET <= target <= R.MAX_TARGET:
        problem = f"The plans add up to {target} consumers; a survey needs {R.MIN_TARGET} to {R.MAX_TARGET}."
    if drafts and not problem and available is not None and target > available:
        problem = (f"The plans add up to {target} consumers, more than the {available} the project's response target leaves"
                   f"{' after the earlier survey' if used else ''}. Lower a plan, or raise the response target.")
    return {"stage": "ended" if last else "draft", "round": round_info(last) if last else None,
            # Each project is independent: an older recipe's project keeps its own plans and survey
            # (a newer recipe has its own project), so it is managed the same way.
            "plans": P.plans_out(db, drafts), "can_edit": product is not None,
            "can_add_plans": product is not None and reopened,
            # Survey numbers run across the product (all its recipes): the number Start survey gives.
            "next_number": ((R.latest_round(db, project.product_id).number if R.latest_round(db, project.product_id) else 0) + 1),
            "can_start": problem is None, "start_problem": problem, "target": target,
            "total_target": total, "used": used, "available": available, "reopen_above": reopen_above,
            "planned": target, "surveys": [round_info(r) for r in rounds]}


@router.get("/{project_id}/plans")
def project_plans(project_id: int, request: Request, db: Session = Depends(get_db)):
    _client(request)
    return _plans_state(db, _get(db, project_id))


@router.post("/{project_id}/plans", status_code=201)
def add_project_plan(project_id: int, body: ActionPlanBody, request: Request, db: Session = Depends(get_db)):
    _client(request)
    project = _get(db, project_id)
    data = body.model_dump()
    state = _plans_state(db, project)
    if not state["can_edit"] or not state["can_add_plans"]:
        raise HTTPException(status_code=409, detail=state["start_problem"] or "This project's plans can't be changed.")
    live = next((r for r in _version_rounds(db, project, db.get(Product, project.product_id)) if r.status == R.LIVE), None)
    plan = ActionPlan(product_id=project.product_id, project_id=project.id, round_id=live.id if live else None,
                      recipe_version=project.product_version, created_at=datetime.utcnow())
    try:
        P.apply_fields(plan, data)
        P.check_total(project.total_target, project.project_name, P.sibling_target(db, plan), 0, plan.target,
                      used=0 if live else state["used"])
    except P.PlanError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    db.add(plan)
    db.flush()
    if live:
        P.sync_round(db, live.id)
    db.commit()
    audit.note(request, target_id=project.id,
               summary=f"Added the survey plan “{plan.name}” to project {project.project_code}"
                       + (f" (Survey {live.number})" if live else ""))
    return _plans_state(db, project)


class StartIn(BaseModel):
    due_date: Optional[str] = Field(default=None, max_length=10)  # YYYY-MM-DD
    note: Optional[str] = Field(default=None, max_length=1000)


@router.post("/{project_id}/start")
def start_project_survey(project_id: int, body: StartIn, request: Request, db: Session = Depends(get_db)):
    """Starts the survey of the project's version with the project's plans."""
    me = _client(request)
    project = _get(db, project_id)
    state = _plans_state(db, project)
    if not state["can_start"]:
        raise HTTPException(status_code=409, detail=state["start_problem"] or "This survey can't start now.")
    product = db.get(Product, project.product_id)
    drafts = P.draft_plans(db, project.id)
    try:
        rnd = R.start_round(db, product, me, project.product_version, state["target"], body.due_date, body.note)
    except R.StandardsNotMet as e:
        raise HTTPException(status_code=409, detail={
            "code": "recipe_standards_not_met", "message": str(e), "recipe_version": e.version, "failed_standards": e.failures})
    except R.RoundError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    for p in drafts:
        p.round_id = rnd.id
        p.recipe_version = rnd.recipe_version
    db.flush()
    P.sync_round(db, rnd.id)
    db.commit()
    audit.note(request, target_id=project.id,
               summary=f"Started Survey {rnd.number} of {product.name} (v{rnd.recipe_version}) from project {project.project_code} "
                       f"with {len(drafts)} plan(s), {rnd.target} consumers")
    return {**_plans_state(db, project), "started": {"number": rnd.number, "target": rnd.target}}


@router.delete("/{project_id}")
def delete_project(project_id: int, request: Request, db: Session = Depends(get_db)):
    """The whole project, while no consumer feedback has come in for it. Its survey plans must be
    deleted first (Survey plan > Delete); then its surveys (started, still without a response)
    go with it, with their team assignments, so they leave the Admin dashboard too."""
    _client(request)
    project = _get(db, project_id)
    code, name = project.project_code, project.project_name
    rounds = _version_rounds(db, project, db.get(Product, project.product_id))
    ids = [r.id for r in rounds]
    responses = db.query(SurveyResponse.id).filter(SurveyResponse.round_id.in_(ids or [-1])).count()
    if responses:
        raise HTTPException(status_code=409, detail={
            "code": "has_feedback",
            "message": f"{code} can't be deleted: {responses} consumer response{'s have' if responses != 1 else ' has'} "
                       "already come in for it. A project with feedback is kept with its reports."})
    live_ids = [r.id for r in rounds if r.status == R.LIVE]
    plans = (db.query(ActionPlan).filter((ActionPlan.project_id == project.id) & ActionPlan.round_id.is_(None)).all()
             + (db.query(ActionPlan).filter(ActionPlan.round_id.in_(live_ids)).all() if live_ids else []))
    if plans:
        n = len(plans)
        raise HTTPException(status_code=409, detail={
            "code": "plans_exist", "plans": [p.name for p in plans],
            "message": f"To delete {code}, first delete its active survey plan{'s' if n != 1 else ''}: "
                       f"{', '.join(p.name for p in plans)}. Then delete the project."})
    removed = []
    for r in reversed(rounds):  # newest first: only a product's latest survey can be deleted
        try:
            R.delete_round(db, r)  # its (ended) plans, team assignments and empty report go with it
        except R.RoundError as e:
            raise HTTPException(status_code=409, detail={"code": "survey_kept", "message": f"{code} can't be deleted yet: {e}"})
        removed.append(r.number)
    db.delete(project)
    db.commit()
    message = f"{code} “{name}” was deleted" + (f", with Survey {', '.join(map(str, sorted(removed)))} (no responses)." if removed else ".")
    audit.note(request, target_id=project_id, summary=message)
    return {"id": project_id, "deleted": True, "message": message, "removed_surveys": sorted(removed)}


# ---------------------------------------------------------------- Admin: read-only projects, team allocation
admin_router = APIRouter(prefix="/admin/projects", tags=["Projects (Admin)"])


def _admin(request: Request) -> dict:
    me = account(request)
    if me.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Only an Admin can open this.")
    return me


@admin_router.get("")
def admin_projects(request: Request, db: Session = Depends(get_db)):
    """Every project, read-only: the Admin reviews them and allocates the survey team to their plans."""
    _admin(request)
    projects = db.query(Project).order_by(Project.created_at.desc(), Project.product_version.desc(), Project.id.desc()).all()
    rows = _many_out(db, projects)
    for r in rows:
        r.pop("can_delete", None)  # never offered to the Admin
    kpis = {"total": len(rows), **{k: sum(1 for r in rows if r["status"] == k) for k in STATUS_LABEL}}
    return {"kpis": kpis, "projects": rows}


@admin_router.get("/{project_id}")
def admin_project(project_id: int, request: Request, db: Session = Depends(get_db)):
    _admin(request)
    project = _get(db, project_id)
    out = {**_many_out(db, [project])[0], "linked": _linked(db, db.get(Product, project.product_id), project)}
    out.pop("can_delete", None)
    return out


@admin_router.get("/{project_id}/plans")
def admin_project_plans(project_id: int, request: Request, db: Session = Depends(get_db)):
    """This project's survey plans with their teams. The Admin allocates people to a plan (planned or
    collecting) with PUT /admin/action-plans/{plan_id}/members; nothing else here can be changed."""
    _admin(request)
    state = _plans_state(db, _get(db, project_id))
    can_allocate = state["stage"] in ("draft", "live") and bool(state.get("can_edit"))
    return {**state, "can_allocate": can_allocate, "can_edit": False, "can_add_plans": False, "can_start": False}
