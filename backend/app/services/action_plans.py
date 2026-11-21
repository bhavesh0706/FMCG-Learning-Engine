"""
Recipe plans and action plans.

Recipe plan (products.recipe_plan, JSON): the product's planning defaults, set by the
Super Admin when the product is added (a project's categories.default_plan is copied in):
    target            consumer responses wanted
    persons_needed    survey persons needed
    start_date, end_date
    cities            where to survey (empty = anywhere)
    audience          internal | external | None (both)
    respondent_types  e.g. ["End consumer", "Retailer"]
    notes             other execution requirements

Action plans (action_plans): several inside each survey round (the round keeps one recipe
version and the reports). Each has its own team, cities, target and dates. A team member
belongs to at most one plan per round and may survey only their cities (a subset of the
plan's; empty = all of the plan's). Members are mirrored into product_assignments, so who
sees the product works exactly as before. A round with plans has target = sum of targets.
Starting the next round copies the previous round's plans (teams, cities, targets).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database.models import (
    ActionPlan,
    ActionPlanAllocationLog,
    ActionPlanMember,
    ProductAssignment,
    SurveyResponse,
    SurveyRound,
    User,
)
from app.services.rounds import DONE_STATUSES  # the same "done" as round progress

AUDIENCES = ("internal", "external")
MAX_TARGET = 10000


class PlanError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------- values
def _date(value) -> Optional[date]:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        raise PlanError(f"Dates are written as YYYY-MM-DD (got “{value}”).")


def _cities(value) -> list:
    out = []
    for c in value or []:
        c = " ".join(str(c).split())[:120]
        if c and c.lower() not in {x.lower() for x in out}:
            out.append(c)
    return out


def _int(value, name, low=1, high=MAX_TARGET) -> Optional[int]:
    """A positive whole number: 1, 5, 100. Never 5.5, -5, +5, 0, "5e1" or text."""
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise PlanError(f"{name} must be a whole number from {low} (1, 2, 3 …).")
    if isinstance(value, float):
        if not value.is_integer():
            raise PlanError(f"{name} must be a whole number from {low} (1, 2, 3 …), not a decimal.")
        value = int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text.isdigit() or not text.isascii():
            raise PlanError(f"{name} must be a whole number from {low} (1, 2, 3 …).")
        value = int(text)
    if not isinstance(value, int):
        raise PlanError(f"{name} must be a whole number from {low} (1, 2, 3 …).")
    n = value
    if n < low:
        raise PlanError(f"{name} must be a whole number from {low} (1, 2, 3 …).")
    if n > high:
        raise PlanError(f"{name} must be between {low} and {high}.")
    return n


def clean_plan(data: Optional[dict]) -> Optional[dict]:
    """A recipe plan (product or project default) checked and tidied; None when empty."""
    if not data:
        return None
    start, end = _date(data.get("start_date")), _date(data.get("end_date"))
    if start and end and end < start:
        raise PlanError("The end date is before the start date.")
    audience = data.get("audience") or None
    if audience not in (None, *AUDIENCES):
        raise PlanError("Audience is internal, external, or empty for both.")
    plan = {
        "target": _int(data.get("target"), "Consumer responses"),
        "persons_needed": _int(data.get("persons_needed"), "Survey persons needed", 1, 500),
        "start_date": start.isoformat() if start else None,
        "end_date": end.isoformat() if end else None,
        "cities": _cities(data.get("cities")),
        "audience": audience,
        "respondent_types": _cities(data.get("respondent_types")),
        "notes": (" ".join(str(data.get("notes") or "").split()) or None) if data.get("notes") else None,
    }
    return plan if any(v not in (None, [], "") for v in plan.values()) else None


def apply_fields(plan: ActionPlan, data: dict) -> None:
    """Set an action plan's fields from a request (name, target, dates, cities ...)."""
    name = " ".join(str(data.get("name") or "").split())[:150]
    if len(name) < 2:
        raise PlanError("Give the action plan a name, e.g. “Delhi team”.")
    clean = clean_plan({k: data.get(k) for k in ("target", "persons_needed", "start_date", "end_date", "cities", "audience",
                                                  "respondent_types", "notes")}) or {}
    if not clean.get("target"):
        raise PlanError("Set how many consumer responses this plan needs.")
    start, end = _date(clean.get("start_date")), _date(clean.get("end_date"))
    # A date chosen now can't be in the past (a plan made earlier keeps its own dates).
    today = date.today()
    if start and start != plan.start_date and start < today:
        raise PlanError("The start date can't be in the past.")
    if end and end != plan.end_date and end < today:
        raise PlanError("The end date can't be in the past.")
    plan.name, plan.target = name, clean["target"]
    plan.persons_needed = clean.get("persons_needed")
    plan.start_date = start
    plan.end_date = end
    plan.cities, plan.audience = clean.get("cities") or [], clean.get("audience")
    plan.respondent_types, plan.notes = clean.get("respondent_types") or [], clean.get("notes")


# ---------------------------------------------------------------- the product's total
def product_total(product) -> Optional[int]:
    """Consumer responses the product needs in a survey: set on the product (its plan's
    "Consumer responses"). The survey plans of one survey may not add up to more."""
    value = (getattr(product, "recipe_plan", None) or {}).get("target")
    return int(value) if value else None


def project_of(db: Session, product_id: int, version: Optional[int]):
    """The project of this product version (rounds saved without a version belong to the first)."""
    from app.database.models import Product, Project, product_first_version

    if version is None:
        product = db.get(Product, product_id)
        version = product_first_version(product) if product else 1
    return db.query(Project).filter(Project.product_id == product_id, Project.product_version == version).first()


def total_for(db: Session, product_id: int, version: Optional[int]) -> Optional[int]:
    """The response target of this product version's project (None: no project or no target)."""
    project = project_of(db, product_id, version)
    return project.total_target if project is not None else None


def check_total(total: Optional[int], name: str, others: int, old_target: int, new_target: int, used: int = 0) -> None:
    """Refuses a plan target that takes the survey's plans above the project's response target (less
    `used`: what earlier, ended surveys already covered). Plans already above it can still be lowered."""
    if not total and not used:
        return
    available = (total or 0) - used
    new_sum, old_sum = others + new_target, others + old_target
    if new_sum > available and new_sum > old_sum:
        room = max(0, available - others)
        why = f"the project's response target is {total or 0}" + (f" and earlier surveys covered {used}" if used else "")
        raise PlanError(f"This plan can have at most {room} consumers: {why}. "
                        "To plan more, raise the target first (Edit response target).", 409)


# ---------------------------------------------------------------- queries
def plans_of_round(db: Session, round_id: int) -> list:
    return db.query(ActionPlan).filter(ActionPlan.round_id == round_id).order_by(ActionPlan.id).all()


def members_of(db: Session, plan_ids) -> dict:
    out = {}
    for m in db.query(ActionPlanMember).filter(ActionPlanMember.action_plan_id.in_(list(plan_ids) or [-1])).order_by(ActionPlanMember.id):
        out.setdefault(m.action_plan_id, []).append(m)
    return out


def done_by_plan(db: Session, plan_ids) -> dict:
    rows = (db.query(SurveyResponse.action_plan_id, func.count(SurveyResponse.id))
            .filter(SurveyResponse.action_plan_id.in_(list(plan_ids) or [-1]), SurveyResponse.status.in_(DONE_STATUSES))
            .group_by(SurveyResponse.action_plan_id).all())
    return {pid: n for pid, n in rows}


def member_done(db: Session, plan_ids) -> dict:
    """{(plan_id, user_id): valid responses submitted} for these plans."""
    rows = (db.query(SurveyResponse.action_plan_id, SurveyResponse.user_id, func.count(SurveyResponse.id))
            .filter(SurveyResponse.action_plan_id.in_(list(plan_ids) or [-1]), SurveyResponse.status.in_(DONE_STATUSES))
            .group_by(SurveyResponse.action_plan_id, SurveyResponse.user_id).all())
    return {(pid, uid): n for pid, uid, n in rows}


def allocation_state(plan: ActionPlan, members: list) -> dict:
    """How the plan's target is shared among its team: allocated, left, and whether it's complete."""
    allocated = sum(m.target or 0 for m in members)
    complete = bool(members) and all(m.target for m in members) and allocated == (plan.target or 0)
    return {"allocated": allocated, "unallocated": max(0, (plan.target or 0) - allocated), "allocation_complete": complete,
            "needs_allocation": not complete}


def plan_out(plan: ActionPlan, members: list, done: int, names: dict, member_counts: Optional[dict] = None) -> dict:
    counts = member_counts or {}
    return {
        **allocation_state(plan, members),
        "id": plan.id, "product_id": plan.product_id, "round_id": plan.round_id, "recipe_version": plan.recipe_version,
        "name": plan.name, "target": plan.target, "done": done, "persons_needed": plan.persons_needed,
        "start_date": plan.start_date.isoformat() if plan.start_date else None,
        "end_date": plan.end_date.isoformat() if plan.end_date else None,
        "cities": plan.cities or [], "audience": plan.audience, "respondent_types": plan.respondent_types or [],
        "notes": plan.notes, "copied_from_id": plan.copied_from_id,
        "members": [{"user_id": m.user_id, "name": names.get(m.user_id, f"#{m.user_id}"), "cities": m.cities or plan.cities or [],
                     "target": m.target, "done": counts.get((plan.id, m.user_id), 0)}
                    for m in members],
    }


def sibling_target(db: Session, plan: ActionPlan) -> int:
    """Consumers planned in the other plans of the same survey (or of the same project, before it starts)."""
    q = db.query(func.coalesce(func.sum(ActionPlan.target), 0)).filter(ActionPlan.id != (plan.id or -1))
    if plan.round_id is not None:
        q = q.filter(ActionPlan.round_id == plan.round_id)
    else:
        q = q.filter(ActionPlan.project_id == plan.project_id, ActionPlan.round_id.is_(None))
    return int(q.scalar() or 0)


def draft_plans(db: Session, project_id: int) -> list:
    """A project's plans made before its survey started (not in a round yet)."""
    return (db.query(ActionPlan).filter(ActionPlan.project_id == project_id, ActionPlan.round_id.is_(None))
            .order_by(ActionPlan.id).all())


def round_plans_out(db: Session, round_id: int) -> list:
    return plans_out(db, plans_of_round(db, round_id))


def plans_out(db: Session, plans: list) -> list:
    members = members_of(db, [p.id for p in plans])
    ids = {m.user_id for ms in members.values() for m in ms}
    names = {u.id: u.name for u in db.query(User).filter(User.id.in_(ids or {-1}))}
    done = done_by_plan(db, [p.id for p in plans])
    counts = member_done(db, [p.id for p in plans])
    return [plan_out(p, members.get(p.id, []), done.get(p.id, 0), names, counts) for p in plans]


# ---------------------------------------------------------------- keeping rounds in step
def sync_round(db: Session, round_id: int) -> None:
    """With plans: the round's target is their sum and its assignments are their members."""
    plans = plans_of_round(db, round_id)
    if not plans:
        return
    rnd = db.get(SurveyRound, round_id)
    if rnd is not None:
        rnd.target = sum(p.target for p in plans)
    people = {m.user_id for ms in members_of(db, [p.id for p in plans]).values() for m in ms}
    current = {a.user_id: a for a in db.query(ProductAssignment).filter(ProductAssignment.round_id == round_id)}
    for uid in people - set(current):
        db.add(ProductAssignment(user_id=uid, product_id=rnd.product_id, round_id=round_id))
    for uid, a in current.items():
        if uid not in people:
            db.delete(a)


def set_members(db: Session, plan: ActionPlan, members: list, who: Optional[dict] = None) -> list:
    """[{user_id, cities, target}]: survey persons, each with cities from the plan's (empty = all) and
    the consumer responses they must collect (a positive whole number). Their targets may add up to
    at most the plan's target, and none below what that person already submitted in the plan.
    A person may be in several plans of a survey: each survey counts in the plan of the customer's
    city (plan_for_survey). Every allocation change is logged. Returns the changes."""
    plan_cities = {c.lower(): c for c in plan.cities or []}
    wanted = {}
    targets = {}
    names = {}
    for item in members or []:
        uid = int(item.get("user_id"))
        user = db.get(User, uid)
        if user is None or user.role != "executive" or not user.is_active:
            raise PlanError(f"#{uid} is not an active survey person.")
        names[uid] = user.name
        if item.get("target") not in (None, ""):
            targets[uid] = _int(item.get("target"), f"Responses for {user.name}")
        cities = _cities(item.get("cities"))
        if plan_cities:
            outside = [c for c in cities if c.lower() not in plan_cities]
            if outside:
                raise PlanError(f"{', '.join(outside)} is not a city of “{plan.name}” ({', '.join(plan.cities)}).")
            cities = [plan_cities[c.lower()] for c in cities]
        wanted[uid] = cities
    total = sum(targets.values())
    if total > (plan.target or 0):
        raise PlanError(f"The team's responses add up to {total}, more than the {plan.target} of “{plan.name}”. "
                        f"Share at most {plan.target}.", 409)
    counts = member_done(db, [plan.id])
    current = {m.user_id: m for m in db.query(ActionPlanMember).filter(ActionPlanMember.action_plan_id == plan.id)}
    for uid, m in current.items():
        submitted = counts.get((plan.id, uid), 0)
        if uid not in wanted and submitted:
            raise PlanError(f"{db.get(User, uid).name if db.get(User, uid) else uid} already submitted {submitted} response(s) in "
                            f"“{plan.name}”: keep them in the team (their responses are never moved).", 409)
    for uid, t in targets.items():
        submitted = counts.get((plan.id, uid), 0)
        if t < submitted:
            raise PlanError(f"{names[uid]} already submitted {submitted} response(s) in “{plan.name}”: give them at least {submitted}.", 409)
    changes = []
    for uid, m in current.items():
        if uid not in wanted:
            changes.append((uid, m.target, None))
            db.delete(m)
        else:
            m.cities = wanted.pop(uid) or None
            new = targets.get(uid, m.target)
            if new != m.target:
                changes.append((uid, m.target, new))
                m.target = new
    for uid, cities in wanted.items():
        db.add(ActionPlanMember(action_plan_id=plan.id, user_id=uid, cities=cities or None, target=targets.get(uid)))
        if targets.get(uid) is not None:
            changes.append((uid, None, targets.get(uid)))
    for uid, old, new in changes:
        db.add(ActionPlanAllocationLog(action_plan_id=plan.id, user_id=uid, old_target=old, new_target=new,
                                       changed_by=(who or {}).get("id"), changed_by_name=(who or {}).get("name")))
    db.flush()
    return changes


def quota_problem(db: Session, plan_id: int, user_id: int, exclude_response: Optional[int] = None) -> Optional[str]:
    """Why this person can't add a response to the plan: their allocation is used up. None when they can."""
    member = db.query(ActionPlanMember).filter(ActionPlanMember.action_plan_id == plan_id, ActionPlanMember.user_id == user_id).first()
    if member is None or not member.target:
        return None  # no allocation set (older plans): no personal limit
    q = db.query(func.count(SurveyResponse.id)).filter(SurveyResponse.action_plan_id == plan_id, SurveyResponse.user_id == user_id,
                                                       SurveyResponse.status.in_(DONE_STATUSES))
    if exclude_response:
        q = q.filter(SurveyResponse.id != exclude_response)
    done = int(q.scalar() or 0)
    if done >= member.target:
        plan = db.get(ActionPlan, plan_id)
        return (f"You've collected all {member.target} responses allocated to you in “{plan.name if plan else 'this plan'}”. "
                "Ask your Admin if more are needed.")
    return None


# ---------------------------------------------------------------- the city rule
def _memberships(db: Session, round_id: int, user_id: int) -> list:
    """[(plan, cities the person may survey in it)] of the round, oldest plan first."""
    rows = (db.query(ActionPlanMember, ActionPlan).join(ActionPlan, ActionPlan.id == ActionPlanMember.action_plan_id)
            .filter(ActionPlan.round_id == round_id, ActionPlanMember.user_id == user_id).order_by(ActionPlan.id).all())
    return [(plan, list(member.cities or plan.cities or [])) for member, plan in rows]


def plan_for_survey(db: Session, round_id: Optional[int], user, city: Optional[str]) -> Optional[int]:
    """The action plan a new survey by this person belongs to (None without plans): the plan of
    the customer's city, else one of their plans without a city limit. A person whose plans all
    limit the cities may survey only customers of those cities: anything else raises PlanError."""
    if round_id is None or user is None or getattr(user, "role", None) != "executive":
        return None
    mine = _memberships(db, round_id, user.id)
    if not mine:
        return None
    here = " ".join(str(city or "").split()).lower()
    fits = [plan for plan, allowed in mine if here and here in {c.lower() for c in allowed}]
    fits += [plan for plan, allowed in mine if not allowed and plan not in fits]  # plans that may survey anywhere
    full = None
    for plan in fits:
        problem = quota_problem(db, plan.id, user.id)
        if problem is None:
            return plan.id
        full = full or problem
    if full:
        raise PlanError(full, 409)
    cities = sorted({c for _, allowed in mine for c in allowed})
    names = ", ".join(f"“{p.name}”" for p, _ in mine)
    if not here:
        raise PlanError(f"Choose the customer's city: you survey in {', '.join(cities)} only (action plan {names}).", 403)
    raise PlanError(f"You survey customers in {', '.join(cities)} only (action plan {names}); this customer is in {city.strip()}.", 403)


def cities_for(db: Session, round_id: int, user_id: int) -> Optional[dict]:
    """A survey person's plans in a round, merged: every plan's cities (empty = anywhere)."""
    mine = _memberships(db, round_id, user_id)
    if not mine:
        return None
    anywhere = any(not allowed for _, allowed in mine)
    cities = [] if anywhere else list(dict.fromkeys(c for _, allowed in mine for c in allowed))
    starts = [p.start_date for p, _ in mine if p.start_date]
    ends = [p.end_date for p, _ in mine if p.end_date]
    counts = member_done(db, [p.id for p, _ in mine])
    targets = {m.action_plan_id: m.target for m in db.query(ActionPlanMember).filter(
        ActionPlanMember.action_plan_id.in_([p.id for p, _ in mine]), ActionPlanMember.user_id == user_id)}
    my_target = sum(t or 0 for t in targets.values())
    return {"action_plan_id": mine[0][0].id, "plan_name": " + ".join(p.name for p, _ in mine), "cities": cities,
            "my_target": my_target or None, "my_done": sum(counts.get((p.id, user_id), 0) for p, _ in mine),
            "plans": [{"id": p.id, "name": p.name, "cities": allowed, "my_target": targets.get(p.id),
                       "my_done": counts.get((p.id, user_id), 0)} for p, allowed in mine],
            "start_date": min(starts).isoformat() if starts else None,
            "end_date": max(ends).isoformat() if ends else None}


# ---------------------------------------------------------------- new rounds (new recipe versions)
def start_plans(db: Session, product, new_round: SurveyRound, previous: Optional[SurveyRound], copy: bool = True) -> int:
    """The plans of a new round: the previous round's, copied (teams, cities, targets; dates
    shifted to start today), or one plan from the product's recipe plan. Returns how many."""
    made = 0
    old = plans_of_round(db, previous.id) if (copy and previous is not None) else []
    if old:
        members = members_of(db, [p.id for p in old])
        today = date.today()
        for p in old:
            span = (p.end_date - p.start_date) if (p.start_date and p.end_date) else None
            plan = ActionPlan(product_id=product.id, round_id=new_round.id, recipe_version=new_round.recipe_version, name=p.name,
                              target=p.target, persons_needed=p.persons_needed, start_date=today if p.start_date else None,
                              end_date=(today + span) if span is not None else None, cities=list(p.cities or []),
                              audience=p.audience, respondent_types=list(p.respondent_types or []), notes=p.notes,
                              copied_from_id=p.id, created_at=datetime.utcnow())
            db.add(plan)
            db.flush()
            for m in members.get(p.id, []):
                user = db.get(User, m.user_id)
                if user is not None and user.is_active:
                    db.add(ActionPlanMember(action_plan_id=plan.id, user_id=m.user_id, cities=m.cities))
            made += 1
    elif (product.recipe_plan or {}).get("target"):
        rp = product.recipe_plan
        start, end = _date(rp.get("start_date")), _date(rp.get("end_date"))
        if start and start < date.today() and end:
            end, start = date.today() + (end - start), date.today()
        db.add(ActionPlan(product_id=product.id, round_id=new_round.id, recipe_version=new_round.recipe_version, name="Main plan",
                          target=rp["target"], persons_needed=rp.get("persons_needed"), start_date=start, end_date=end,
                          cities=list(rp.get("cities") or []), audience=rp.get("audience"),
                          respondent_types=list(rp.get("respondent_types") or []), notes=rp.get("notes"), created_at=datetime.utcnow()))
        made = 1
    db.flush()
    if made:
        sync_round(db, new_round.id)
    return made



# ---------------------------------------------------------------- surveys by plan and team member
SURVEY_STATE = {"draft": "Not finished", "submitted": "Waiting for analysis", "analyzed": "Analyzed"}


def plan_surveys(db: Session, rnd: SurveyRound) -> dict:
    """The round's action plans, each team member with the surveys they took, and the
    surveys of the round that belong to no plan (taken before plans existed, or by an admin)."""
    from app.database.models import FeedbackRecord, Respondent

    responses = (db.query(SurveyResponse).filter(SurveyResponse.round_id == rnd.id)
                 .order_by(SurveyResponse.created_at.desc()).all())
    ids = [r.id for r in responses]
    feedback = {}
    for f in db.query(FeedbackRecord).filter(FeedbackRecord.response_id.in_(ids or [-1])).order_by(FeedbackRecord.id):
        feedback[f.response_id] = f  # the latest recording of each survey
    people = {p.id: p for p in db.query(Respondent).filter(Respondent.id.in_({r.respondent_id for r in responses} or {-1}))}

    def survey_out(r) -> dict:
        f, who = feedback.get(r.id), people.get(r.respondent_id)
        return {
            "response_id": r.id, "created_at": r.created_at.isoformat() + "Z" if r.created_at else None,
            "status": r.status, "status_label": SURVEY_STATE.get(r.status, r.status),
            "customer": who.name if who else None, "city": (who.city or who.location) if who else None,
            "executive_name": r.executive_name, "user_id": r.user_id,
            "feedback_id": f.id if f else None, "rating": f.overall_rating if f else None,
            "tone": f.tone if f else None, "summary": f.overall_summary if f else None,
        }

    plans = round_plans_out(db, rnd.id)
    by_plan_user = {}
    unplanned = []
    for r in responses:
        if r.action_plan_id is not None:
            by_plan_user.setdefault((r.action_plan_id, r.user_id), []).append(survey_out(r))
        else:
            unplanned.append(survey_out(r))
    for p in plans:
        known = {m["user_id"] for m in p["members"]}
        for m in p["members"]:
            m["surveys"] = by_plan_user.pop((p["id"], m["user_id"]), [])
        # surveys of people who were later taken off this plan stay visible under it
        for (pid, uid), rows in list(by_plan_user.items()):
            if pid == p["id"] and uid not in known:
                p["members"].append({"user_id": uid, "name": rows[0]["executive_name"] or f"#{uid}", "cities": [],
                                     "surveys": rows, "former": True})
                by_plan_user.pop((pid, uid))
        p["survey_count"] = sum(len(m["surveys"]) for m in p["members"])
    return {"round": {"id": rnd.id, "number": rnd.number, "recipe_version": rnd.recipe_version, "status": rnd.status,
                      "target": rnd.target},
            "plans": plans, "unplanned": unplanned}


# ---------------------------------------------------------------- reopening an ended survey
def reopen_for_new_total(db: Session, project) -> Optional[SurveyRound]:
    """The Super Admin raised the project's response target after its latest survey ended: that survey
    reopens (collecting again) for the extra responses. The increase goes to its survey plan
    (the first one when it has several), so the same team collects them. None when nothing
    reopens: a survey of the product is still collecting, or the target isn't above what the
    project's ended survey covered. Each project is independent: an older recipe's project
    reopens its own survey even when a newer recipe exists."""
    from app.database.models import Product, product_first_version
    from app.services import rounds as R

    product = db.get(Product, project.product_id)
    if product is None or R.live_round(db, product.id) is not None:
        return None  # one collecting survey per product at a time
    first = product_first_version(product)
    mine = [r for r in db.query(SurveyRound).filter(SurveyRound.product_id == product.id).order_by(SurveyRound.number)
            if (r.recipe_version or first) == project.product_version]
    last = mine[-1] if mine else None
    if last is None or last.status != R.CLOSED:
        return None
    total = project.total_target
    baseline = R.reopen_baseline(last, R.done_counts(db, [last.id]).get(last.id, 0))
    if not total or total <= baseline:
        return None
    # The survey's plans grow so that they add up to the new target (the first plan takes the extra).
    plans = plans_of_round(db, last.id)
    planned = sum(p.target or 0 for p in plans) if plans else (last.target or 0)
    diff = total - planned  # e.g. ended at 3 of 3, new target 4: +1; ended early at 3 of 5, target 5: no change
    if plans:
        first_done = done_by_plan(db, [plans[0].id]).get(plans[0].id, 0)
        plans[0].target = max((plans[0].target or 0) + diff, first_done, 1)
    else:
        last.target = max(total, baseline + 1)
    last.status, last.closed_at, last.closed_reason, last.total_at_close = R.LIVE, None, None, None
    db.flush()
    sync_round(db, last.id)
    return last
