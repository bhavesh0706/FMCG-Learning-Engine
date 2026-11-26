"""
Recipe plans and action plans (services/action_plans.py).

GET  /products/{id}/recipe-plan              the product's recipe plan (+ its project's default)
PUT  /products/{id}/recipe-plan              Super Admin
PUT  /categories/{id}/default-plan           Super Admin: the project's default recipe plan
GET  /products/{id}/action-plans[?round=N]   the plans of the live (or chosen) round
POST /products/{id}/action-plans             Super Admin: a plan in the live round
PUT  /action-plans/{id}                      Super Admin
DELETE /action-plans/{id}                    Super Admin (only while it has no surveys)
PUT  /admin/action-plans/{id}/members        Admin: team members and their cities
GET  /my/action-plans                        survey person: their plans and cities
GET  /admin/products/{id}/plan-surveys[?round=N]  Admin: each plan's team and the surveys
                                             each member took (with the analysis to open)
"""

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.ownership import account
from app.database.database import get_db
from app.database.models import ActionPlan, Category, Product, SurveyResponse, SurveyRound
from app.services import action_plans as P
from app.services import audit
from app.services import rounds as R

router = APIRouter(tags=["Recipe and action plans"])


def _role(request: Request, *roles) -> dict:
    me = account(request)
    if me.get("role") not in roles:
        raise HTTPException(status_code=403, detail="You can't change plans.")
    return me


def _product(db: Session, product_id: int) -> Product:
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found.")
    return product


def _plan(db: Session, plan_id: int) -> ActionPlan:
    plan = db.get(ActionPlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Action plan not found.")
    return plan


def _fail(e: P.PlanError):
    raise HTTPException(status_code=e.status, detail=str(e))


class PlanBody(BaseModel):
    # Any JSON value: services/action_plans._int accepts only positive whole numbers (no 5.5, -5, "5e1").
    target: Any = None
    persons_needed: Optional[int] = None
    start_date: Optional[str] = Field(default=None, max_length=10)
    end_date: Optional[str] = Field(default=None, max_length=10)
    # Survey locations are the Admin's (team allocation): the Super Admin's plan doesn't send cities,
    # and a plan saved without them keeps the ones it has (older plans)
    cities: Optional[list] = None
    audience: Optional[str] = None
    respondent_types: list = Field(default_factory=list)
    notes: Optional[str] = Field(default=None, max_length=2000)


class ActionPlanBody(PlanBody):
    # The plan's name is its code (PLA001 ...), given automatically: no name needs to be sent
    name: Optional[str] = Field(default=None, max_length=150)


class MembersBody(BaseModel):
    members: list = Field(default_factory=list)  # [{user_id, cities: [...]}]


# ---------------------------------------------------------------- recipe plans
@router.get("/products/{product_id}/recipe-plan")
def get_recipe_plan(product_id: int, request: Request, db: Session = Depends(get_db)):
    _role(request, "client", "admin")
    product = _product(db, product_id)
    category = db.get(Category, product.category_id)
    return {"product_id": product.id, "recipe_plan": product.recipe_plan,
            "project_default": category.default_plan if category else None}


@router.put("/products/{product_id}/recipe-plan")
def save_recipe_plan(product_id: int, body: PlanBody, request: Request, db: Session = Depends(get_db)):
    _role(request, "client")
    product = _product(db, product_id)
    try:
        product.recipe_plan = P.clean_plan(body.model_dump())
    except P.PlanError as e:
        _fail(e)
    db.commit()
    audit.note(request, summary=f"Saved the recipe plan of product #{product.id} ({product.name})")
    # The response target lives on each project now (PUT /org/projects/{id}/target).
    return {"product_id": product.id, "recipe_plan": product.recipe_plan, "reopened": None}


@router.put("/categories/{category_id}/default-plan")
def save_project_plan(category_id: int, body: PlanBody, request: Request, db: Session = Depends(get_db)):
    _role(request, "client")
    category = db.get(Category, category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="Category not found.")
    try:
        category.default_plan = P.clean_plan(body.model_dump())
    except P.PlanError as e:
        _fail(e)
    db.commit()
    audit.note(request, summary=f"Saved the default recipe plan of category #{category.id} ({category.name})")
    return {"category_id": category.id, "default_plan": category.default_plan}


# ---------------------------------------------------------------- action plans
@router.get("/products/{product_id}/action-plans")
def list_action_plans(product_id: int, request: Request, round: Optional[int] = None, db: Session = Depends(get_db)):
    me = _role(request, "client", "admin")
    product = _product(db, product_id)
    rnd = R.round_by_number(db, product.id, round) if round else (R.live_round(db, product.id) or R.latest_round(db, product.id))
    if rnd is None:
        return {"product_id": product.id, "round": None, "plans": [], "can_edit": False, "can_allocate": False}
    live = rnd.status == R.LIVE
    return {"product_id": product.id, "round": {"id": rnd.id, "number": rnd.number, "recipe_version": rnd.recipe_version,
                                                "status": rnd.status, "target": rnd.target},
            "plans": P.round_plans_out(db, rnd.id), "can_edit": live and me.get("role") == "client",
            "can_allocate": live and me.get("role") == "admin"}


def _live_round(db: Session, product: Product) -> SurveyRound:
    rnd = R.live_round(db, product.id)
    if rnd is None:
        raise HTTPException(status_code=409, detail="Start a survey first: action plans belong to the live survey.")
    return rnd


@router.post("/products/{product_id}/action-plans", status_code=201)
def create_action_plan(product_id: int, body: ActionPlanBody, request: Request, db: Session = Depends(get_db)):
    _role(request, "client")
    product = _product(db, product_id)
    rnd = _live_round(db, product)
    plan = ActionPlan(product_id=product.id, round_id=rnd.id, recipe_version=rnd.recipe_version, created_at=datetime.utcnow())
    try:
        P.apply_fields(plan, body.model_dump())
        P.check_total(P.total_for(db, product.id, rnd.recipe_version), product.name, P.sibling_target(db, plan), 0, plan.target)
    except P.PlanError as e:
        _fail(e)
    db.add(plan)
    db.flush()
    P.sync_round(db, rnd.id)
    db.commit()
    audit.note(request, summary=f"Added the action plan “{plan.name}” to Survey {rnd.number} of {product.name}")
    return {"plan": P.round_plans_out(db, rnd.id)[-1]}


def _plan_now(db: Session, plan: ActionPlan, rnd: Optional[SurveyRound]) -> dict:
    rows = P.round_plans_out(db, rnd.id) if rnd else P.plans_out(db, [plan])
    return next(p for p in rows if p["id"] == plan.id)


def _where(rnd: Optional[SurveyRound]) -> str:
    return f"Survey {rnd.number}" if rnd else "planned, survey not started"


def _editable(db: Session, plan: ActionPlan) -> Optional[SurveyRound]:
    """The plan's live round; None for a plan made in a project before its survey started."""
    if plan.round_id is None:
        return None
    rnd = db.get(SurveyRound, plan.round_id)
    if rnd is None or rnd.status != R.LIVE:
        raise HTTPException(status_code=409, detail="This survey has ended; its action plans are kept as they ran.")
    return rnd


def _used_before(db: Session, plan: ActionPlan) -> int:
    """For a plan of a project's next survey (not started yet): what the earlier, ended survey covered."""
    if plan.round_id is not None or not plan.project_id:
        return 0
    from app.api.projects import _plans_state
    from app.database.models import Project

    project = db.get(Project, plan.project_id)
    return _plans_state(db, project)["used"] if project else 0


def _plan_total(db: Session, plan: ActionPlan) -> Optional[int]:
    """The response target of the plan's project."""
    from app.database.models import Project

    if plan.project_id:
        project = db.get(Project, plan.project_id)
        if project is not None:
            return project.total_target
    rnd = db.get(SurveyRound, plan.round_id) if plan.round_id else None
    return P.total_for(db, plan.product_id, plan.recipe_version or (rnd.recipe_version if rnd else None))


@router.put("/action-plans/{plan_id}")
def update_action_plan(plan_id: int, body: ActionPlanBody, request: Request, db: Session = Depends(get_db)):
    _role(request, "client")
    plan = _plan(db, plan_id)
    rnd = _editable(db, plan)
    old_target = plan.target or 0
    try:
        P.apply_fields(plan, body.model_dump())
        P.check_total(_plan_total(db, plan), db.get(Product, plan.product_id).name, P.sibling_target(db, plan), old_target,
                      plan.target, used=_used_before(db, plan))
        if plan.target != old_target:
            members = P.members_of(db, [plan.id]).get(plan.id, [])
            done = P.member_done(db, [plan.id])
            floor = sum(done.get((plan.id, m.user_id), 0) for m in members)
            if plan.target < floor:
                raise P.PlanError(f"The team already submitted {floor} response(s) in this plan: its target can't go below {floor}.", 409)
    except P.PlanError as e:
        db.rollback()
        _fail(e)
    if rnd:
        P.sync_round(db, rnd.id)
    if plan.target != old_target and P.members_of(db, [plan.id]).get(plan.id):
        notify_reallocation(db, plan, f"Its target changed from {old_target} to {plan.target}.")
    db.commit()
    audit.note(request, summary=f"Changed the action plan “{plan.name}” ({_where(rnd)})")
    return {"plan": _plan_now(db, plan, rnd)}


@router.delete("/action-plans/{plan_id}")
def delete_action_plan(plan_id: int, request: Request, db: Session = Depends(get_db)):
    _role(request, "client")
    plan = _plan(db, plan_id)
    rnd = _editable(db, plan)
    if db.query(SurveyResponse).filter(SurveyResponse.action_plan_id == plan.id).count():
        raise HTTPException(status_code=409, detail="This plan already has responses: change it instead of deleting it.")
    P.set_members(db, plan, [])
    db.delete(plan)
    db.flush()
    if rnd:
        P.sync_round(db, rnd.id)
    db.commit()
    audit.note(request, summary=f"Deleted the action plan “{plan.name}” ({_where(rnd)})")
    return {"deleted": plan_id}


def _tell_assigned(db: Session, plan: ActionPlan, changes: list) -> None:
    """Each survey person given (or given a new) share in a plan gets a notification: product, category,
    project, cities and their own response target. One per assignment event (dedupe key)."""
    from app.database.models import Category, Project
    from app.services.notifications import notify

    product = db.get(Product, plan.product_id)
    category = db.get(Category, product.category_id) if product else None
    project = db.get(Project, plan.project_id) if plan.project_id else P.project_of(db, plan.product_id, plan.recipe_version)
    for uid, old, new in changes:
        if new is None:
            continue
        notify(db, [uid], "plan_assigned",
               title=f"Survey assignment: {product.name if product else 'a product'}",
               message=(f"You're in “{plan.name}”{f' ({project.project_code})' if project else ''}: collect {new} consumer "
                        f"response{'s' if new != 1 else ''}" + (f" in {', '.join(plan.cities)}" if plan.cities else "") + "."),
               data={"product_id": plan.product_id, "product_name": product.name if product else None,
                     "category_name": category.name if category else None, "category_id": category.id if category else None, "plan_id": plan.id, "plan_name": plan.name,
                     "project_id": project.id if project else None, "project_code": project.project_code if project else None,
                     "target": new, "previous_target": old, "cities": plan.cities or []},
               dedupe_key=f"plan_assigned:{plan.id}:{uid}:{new}")


@router.put("/admin/action-plans/{plan_id}/members")
def allocate_members(plan_id: int, body: MembersBody, request: Request, db: Session = Depends(get_db)):
    me = _role(request, "admin")
    # Two Admins saving at once: the second waits for the first (row lock), then sees its result.
    plan = db.query(ActionPlan).filter(ActionPlan.id == plan_id).with_for_update().first() or _plan(db, plan_id)
    rnd = _editable(db, plan)
    try:
        changes = P.set_members(db, plan, body.members, me)
    except P.PlanError as e:
        db.rollback()
        _fail(e)
    if rnd:
        P.sync_round(db, rnd.id)  # a planned (not started) plan's team joins the survey when it starts
    db.commit()
    audit.note(request, summary=f"Allocated {len(body.members)} team member(s) to the action plan “{plan.name}”",
               details={"members": body.members})
    _tell_assigned(db, plan, changes)
    return {"plan": _plan_now(db, plan, rnd)}


@router.get("/admin/action-plans/{plan_id}/allocation-history")
def allocation_history(plan_id: int, request: Request, db: Session = Depends(get_db)):
    """Who changed which team member's response allocation, when, from what to what."""
    _role(request, "admin", "client")
    _plan(db, plan_id)
    from app.database.models import ActionPlanAllocationLog, User

    rows = (db.query(ActionPlanAllocationLog).filter(ActionPlanAllocationLog.action_plan_id == plan_id)
            .order_by(ActionPlanAllocationLog.at.desc(), ActionPlanAllocationLog.id.desc()).limit(200).all())
    names = {u.id: u.name for u in db.query(User).filter(User.id.in_({r.user_id for r in rows} or {-1}))}
    return {"history": [{"at": r.at.isoformat(timespec="seconds") + "Z", "member": names.get(r.user_id, f"#{r.user_id}"),
                         "old": r.old_target, "new": r.new_target, "by": r.changed_by_name} for r in rows]}


def notify_reallocation(db: Session, plan: ActionPlan, why: str) -> None:
    """Tell the Admins that a plan's response target changed: its team allocation needs a review."""
    from app.database.models import User
    from app.services.notifications import notify

    admins = [uid for (uid,) in db.query(User.id).filter(User.role == "admin", User.is_active == 1)]
    notify(db, admins, "allocation_needed", title=f"Reallocate responses: {plan.name}",
           message=f"{why} Share the new target among the team of “{plan.name}”.",
           data={"plan_id": plan.id, "project_id": plan.project_id, "product_id": plan.product_id, "target": plan.target})


@router.get("/my/action-plans")
def my_action_plans(request: Request, db: Session = Depends(get_db)):
    """A survey person's plans in live rounds: product, plan, cities and dates."""
    me = account(request)
    out = []
    for rnd in db.query(SurveyRound).filter(SurveyRound.status == R.LIVE):
        mine = P.cities_for(db, rnd.id, me.get("id"))
        if mine:
            product = db.get(Product, rnd.product_id)
            out.append({"product_id": rnd.product_id, "product_name": product.name if product else "", "round": rnd.number, **mine})
    return {"plans": out}


@router.get("/admin/products/{product_id}/plan-surveys")
def plan_surveys(product_id: int, request: Request, round: Optional[int] = None, db: Session = Depends(get_db)):
    """Read-only, for the report page: the round's action plans with each team member's surveys."""
    _role(request, "admin")
    product = _product(db, product_id)
    rnd = R.round_by_number(db, product.id, round) if round else (R.live_round(db, product.id) or R.latest_round(db, product.id))
    if rnd is None:
        return {"product_id": product.id, "round": None, "plans": [], "unplanned": []}
    return {"product_id": product.id, **P.plan_surveys(db, rnd)}