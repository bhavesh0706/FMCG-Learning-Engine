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
from typing import Any, Optional, Union

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
    cities: list = Field(default_factory=list)
    audience: Optional[str] = None
    respondent_types: list = Field(default_factory=list)
    notes: Optional[str] = Field(default=None, max_length=2000)


class ActionPlanBody(PlanBody):
    name: str = Field(min_length=1, max_length=150)


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
        for m in P.members_of(db, [plan.id]).get(plan.id, []):  # keep members' cities inside the plan's
            if plan.cities and m.cities:
                kept = [c for c in m.cities if c.lower() in {x.lower() for x in plan.cities}]
                m.cities = kept or None
    except P.PlanError as e:
        db.rollback()
        _fail(e)
    if rnd:
        P.sync_round(db, rnd.id)
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


@router.put("/admin/action-plans/{plan_id}/members")
def allocate_members(plan_id: int, body: MembersBody, request: Request, db: Session = Depends(get_db)):
    _role(request, "admin")
    plan = _plan(db, plan_id)
    rnd = _editable(db, plan)
    try:
        P.set_members(db, plan, body.members)
    except P.PlanError as e:
        db.rollback()
        _fail(e)
    if rnd:
        P.sync_round(db, rnd.id)  # a planned (not started) plan's team joins the survey when it starts
    db.commit()
    audit.note(request, summary=f"Allocated {len(body.members)} team member(s) to the action plan “{plan.name}”",
               details={"members": body.members})
    return {"plan": _plan_now(db, plan, rnd)}


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