"""
Organization portal: the company whose products are surveyed (users.role = "client").

GET  /org/overview                              numbers, products, latest updates
GET  /org/products                              every active product with its current round
GET  /org/products/{id}                         product, rounds, recipe versions
GET  /org/products/{id}/report?round=N          the report of one round (default: the latest)
GET  /org/products/{id}/compare?a=1&b=2         two rounds side by side, with the recipe changes
POST /org/products/{id}/rounds                  start a new round {recipe_version, target, due_date, note}
POST /org/products/{id}/rounds/{number}/end     end the live round early
GET  /org/updates                               everything that happened, newest first
GET  /org/updates/unread                        {unread} for the menu badge
POST /org/updates/seen                          the Updates page was opened
GET  /org/account                               name, sign-in, number of products

Recipes are read and saved with GET/PUT /products/{id}/recipe and
GET /products/{id}/recipe/versions (recipes.py).

The organization's login is created on the server (python -m app.tools.organization),
never on the admin dashboard. It sees every active product, but only aggregated
results: no customer names, phone numbers or addresses, no survey person names, no
recordings and no individual surveys. The research team's admins appear as
"Research team". Admins may call these routes too (to preview the portal).
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.ownership import account, check_product_access
from app.api.product_feedback import _iso, round_report, round_report_is_stale, round_reports
from app.database.database import get_db
from app.database.models import Category, Product, RecipeVersion, SurveyResponse, SurveyRound, User
from app.services import rounds as R
from app.services.recipe_versions import changes_between, version_out

router = APIRouter(prefix="/org", tags=["Super Admin portal"])

UPDATES_LIMIT = 200
NEW_ACCOUNT_UNREAD_DAYS = 14


# ============================================================
# Helpers
# ============================================================

def _role(request: Request) -> str:
    return account(request).get("role") or "client"


def _product_or_404(db: Session, request: Request, product_id: int) -> Product:
    check_product_access(db, request, product_id)
    return db.get(Product, product_id)


def _active_products(db: Session) -> list[Product]:
    return db.query(Product).filter(Product.is_active == 1).order_by(Product.name).all()


def _categories(db: Session) -> dict:
    return {c.id: c.name for c in db.query(Category).all()}


def _safe_report(report) -> Optional[dict]:
    """The consolidated report without internal survey ids."""
    if report is None:
        return None
    return {
        "id": report.id,
        "product_id": report.product_id,
        "version": report.version,
        "response_count": report.response_count,
        "overall_rating": report.overall_rating,
        "stats": report.stats,
        "narrative": report.narrative,
        "generated_at": _iso(report.generated_at),
    }


def _latest_versions(db: Session, product_ids) -> dict:
    from app.services.recipe_versions import latest_changes

    return latest_changes(db, list(product_ids))


def _recipe_info(change: Optional[dict], viewer: str) -> Optional[dict]:
    if not change:
        return None
    by = change.get("by_name")
    if change.get("by_role") != "client":
        by = R.RESEARCH_TEAM if viewer == "client" or not by else by
    return {"version": change["version"], "by_role": change.get("by_role"), "by_name": by, "at": change.get("at")}


def _product_rows(db: Session, products: list[Product], viewer: str) -> list[dict]:
    ids = [p.id for p in products]
    by_product = R.rounds_by_product(db, ids)
    all_rounds = [r for rs in by_product.values() for r in rs]
    done = R.done_counts(db, [r.id for r in all_rounds])
    reports = round_reports(db, [r.id for r in all_rounds])
    versions = _latest_versions(db, ids)
    cats = _categories(db)
    rows = []
    for p in products:
        rs = by_product.get(p.id, [])
        current = rs[-1] if rs else None
        previous = rs[-2] if len(rs) > 1 else None
        rows.append({
            "id": p.id,
            "name": p.name,
            "sku": p.sku,
            "category_name": cats.get(p.category_id),
            "has_recipe": bool(p.recipe),
            "recipe": _recipe_info(versions.get(p.id), viewer),
            "rounds": len(rs),
            "responses": sum(done.get(r.id, 0) for r in rs),
            "current_round": R.round_out(current, done.get(current.id, 0), viewer, reports.get(current.id)) if current else None,
            "previous_rating": reports[previous.id].overall_rating if previous and previous.id in reports else None,
        })
    return rows


# ============================================================
# Updates: built from what happened (recipe versions, rounds, reports)
# ============================================================

def _updates(db: Session, viewer_user: Optional[User], viewer: str, limit: int = UPDATES_LIMIT) -> list[dict]:
    names = {p.id: p.name for p in db.query(Product).filter(Product.is_active == 1).all()}
    events = []
    for v in (
        db.query(RecipeVersion)
        .filter(RecipeVersion.product_id.in_(list(names) or [0]))
        .order_by(RecipeVersion.created_at.desc())
        .limit(limit)
        .all()
    ):
        if v.changed_by_role is None:  # the snapshot of a recipe from before version history
            continue
        own = v.changed_by_role == "client"
        name = names[v.product_id]
        changes = v.changes or []
        events.append({
            "kind": "recipe",
            "own": own,
            "at": v.created_at,
            "title": f"You saved recipe v{v.version} of {name}" if own
            else f"{R.RESEARCH_TEAM} updated the recipe of {name} (v{v.version})",
            "text": ", ".join(changes[:2]) + (f" and {len(changes) - 2} more" if len(changes) > 2 else ""),
            "product_id": v.product_id,
            "tab": "recipe",
            "round": None,
        })
    rounds = (
        db.query(SurveyRound)
        .filter(SurveyRound.product_id.in_(list(names) or [0]))
        .order_by(SurveyRound.started_at.desc())
        .limit(limit)
        .all()
    )
    done = R.done_counts(db, [r.id for r in rounds])
    reports = round_reports(db, [r.id for r in rounds])
    for r in rounds:
        name = names[r.product_id]
        if r.started_by_role != "system":
            own = r.started_by_role == "client"
            events.append({
                "kind": "round_started",
                "own": own,
                "at": r.started_at,
                "title": f"You started Round {r.number} of {name}" if own else f"{R.RESEARCH_TEAM} started Round {r.number} of {name}",
                "text": f"Recipe v{r.recipe_version} · {r.target} consumers"
                + (f" · wanted by {r.due_date.strftime('%d %b %Y')}" if r.due_date else ""),
                "product_id": r.product_id,
                "tab": "rounds",
                "round": r.number,
            })
        if r.status == R.CLOSED and r.closed_at:
            report = reports.get(r.id)
            n = done.get(r.id, 0)
            if r.closed_reason == "ended":
                title, text = f"{name}: Round {r.number} was ended", f"{n} of {r.target} consumers surveyed"
            else:
                title = f"{name}: Round {r.number} report is ready" if report else f"{name}: Round {r.number} is complete"
                text = f"{n} consumers"
            if report and report.overall_rating:
                text += f" · rating {report.overall_rating:.1f}★"
            events.append({
                "kind": "round_completed",
                "own": False,
                "at": r.closed_at,
                "title": title,
                "text": text,
                "product_id": r.product_id,
                "tab": "report",
                "round": r.number,
            })
    events.sort(key=lambda e: e["at"] or datetime.min, reverse=True)
    seen = getattr(viewer_user, "updates_seen_at", None) if viewer_user else None
    since = seen or (datetime.utcnow() - timedelta(days=NEW_ACCOUNT_UNREAD_DAYS))
    out = []
    for e in events[:limit]:
        out.append({**e, "at": _iso(e["at"]), "unread": bool(not e["own"] and e["at"] and e["at"] > since)})
    return out


def _unread(db: Session, request: Request) -> int:
    user = db.get(User, account(request).get("id")) if account(request).get("id") else None
    return sum(1 for e in _updates(db, user, _role(request)) if e["unread"])


@router.get("/updates")
def updates(request: Request, limit: int = Query(default=50, ge=1, le=UPDATES_LIMIT), db: Session = Depends(get_db)):
    user = db.get(User, account(request).get("id")) if account(request).get("id") else None
    items = _updates(db, user, _role(request))
    return {"updates": items[:limit], "unread": sum(1 for e in items if e["unread"])}


@router.get("/updates/unread")
def updates_unread(request: Request, db: Session = Depends(get_db)):
    return {"unread": _unread(db, request)}


@router.post("/updates/seen")
def updates_seen(request: Request, db: Session = Depends(get_db)):
    user = db.get(User, account(request).get("id")) if account(request).get("id") else None
    if user is not None and user.role == "client":
        user.updates_seen_at = datetime.utcnow()
        db.commit()
    return {"unread": 0}


# ============================================================
# Overview and products
# ============================================================

@router.get("/overview")
def overview(request: Request, db: Session = Depends(get_db)):
    viewer = _role(request)
    rows = _product_rows(db, _active_products(db), viewer)
    ratings = [r["current_round"]["rating"] for r in rows if r["current_round"] and r["current_round"]["rating"]]
    user = db.get(User, account(request).get("id")) if account(request).get("id") else None
    items = _updates(db, user, viewer)
    return {
        "numbers": {
            "products": len(rows),
            "surveyed": sum(1 for r in rows if r["responses"]),
            "responses": sum(r["responses"] for r in rows),
            "avg_rating": round(sum(ratings) / len(ratings), 1) if ratings else None,
            "open_rounds": sum(1 for r in rows if r["current_round"] and r["current_round"]["status"] == R.LIVE),
        },
        "products": rows,
        "updates": items[:4],
        "unread": sum(1 for e in items if e["unread"]),
    }


@router.get("/products")
def products(request: Request, db: Session = Depends(get_db)):
    return {"products": _product_rows(db, _active_products(db), _role(request))}


def _versions_out(db: Session, product_id: int, viewer: str, rounds: list) -> list[dict]:
    rows = db.query(RecipeVersion).filter(RecipeVersion.product_id == product_id).order_by(RecipeVersion.version.desc()).all()
    used = {}
    for r in rounds:
        used.setdefault(r["recipe_version"], []).append({"number": r["number"], "status": r["status"]})
    return [{**version_out(v, viewer), "rounds": used.get(v.version, [])} for v in rows]


def _rounds_out(db: Session, product_id: int, viewer: str) -> list[dict]:
    rs = R.rounds_of(db, product_id)
    done = R.done_counts(db, [r.id for r in rs])
    reports = round_reports(db, [r.id for r in rs])
    return [R.round_out(r, done.get(r.id, 0), viewer, reports.get(r.id)) for r in rs]


@router.get("/products/{product_id}")
def product_detail(product_id: int, request: Request, db: Session = Depends(get_db)):
    viewer = _role(request)
    product = _product_or_404(db, request, product_id)
    rounds = _rounds_out(db, product_id, viewer)
    versions = _versions_out(db, product_id, viewer, rounds)
    live = next((r for r in rounds if r["status"] == R.LIVE), None)
    return {
        "product": {
            "id": product.id,
            "name": product.name,
            "sku": product.sku,
            "description": product.description,
            "category_name": _categories(db).get(product.category_id),
        },
        "recipe": product.recipe,
        "latest_version": versions[0]["version"] if versions else (1 if product.recipe else None),
        "versions": versions,
        "rounds": rounds,
        "live_round": live,
        # Starting a round needs a recipe and no round collecting surveys.
        "can_start_round": bool(product.recipe) and live is None,
        "default_target": R.DEFAULT_TARGET,
    }


@router.get("/products/{product_id}/report")
def product_report(
    product_id: int,
    request: Request,
    round_number: Optional[int] = Query(default=None, ge=1, alias="round"),
    db: Session = Depends(get_db),
):
    viewer = _role(request)
    product = _product_or_404(db, request, product_id)
    rounds = _rounds_out(db, product_id, viewer)
    if not rounds:
        return {"product_id": product.id, "rounds": [], "round": None, "report": None, "updating": False, "pending": 0}
    selected = rounds[-1] if round_number is None else next((r for r in rounds if r["number"] == round_number), None)
    if selected is None:
        raise HTTPException(status_code=404, detail=f"Round {round_number} not found.")
    report = round_report(db, selected["id"])
    pending = (
        db.query(SurveyResponse.id)
        .filter(SurveyResponse.round_id == selected["id"], SurveyResponse.status == "submitted")
        .count()
    )
    return {
        "product_id": product.id,
        "rounds": rounds,
        "round": selected,
        "report": _safe_report(report),
        "updating": round_report_is_stale(db, selected["id"], report),
        "pending": pending,  # submitted surveys still being analyzed
    }


# ============================================================
# Comparing two rounds
# ============================================================

def _pct(part, whole) -> Optional[int]:
    return round(100 * part / whole) if whole else None


def _numbers(report) -> dict:
    stats = (report.stats or {}) if report is not None else {}
    n = stats.get("response_count") or (report.response_count if report is not None else 0) or 0
    sentiment = stats.get("sentiment_distribution") or {}
    summary = stats.get("client_summary") or {}
    return {
        "consumers": n,
        "rating": stats.get("overall_rating"),
        "positive_pct": _pct(sentiment.get("positive", 0), n),
        "negative_pct": _pct(sentiment.get("negative", 0), n),
        "would_buy_pct": (summary.get("purchase_intent") or {}).get("positive_pct"),
        "preferred_pct": (summary.get("preference") or {}).get("preferred_pct"),
        "top_score_pct": (summary.get("score") or {}).get("top_two_pct"),
        "ideal_price": (summary.get("price") or {}).get("ideal"),
    }


def _aspects(report) -> dict:
    stats = (report.stats or {}) if report is not None else {}
    n = stats.get("response_count") or 0
    out = {}
    for a in stats.get("aspects") or []:
        out[a.get("key") or a.get("name")] = {
            "name": a.get("name"),
            "concern_pct": _pct(a.get("negative", 0), n),
            "praise_pct": _pct(a.get("positive", 0), n),
        }
    return out


def _fmt(x) -> str:
    return f"{x:.1f}" if isinstance(x, (int, float)) else "—"


@router.get("/products/{product_id}/compare")
def compare_rounds(
    product_id: int,
    request: Request,
    a: Optional[int] = Query(default=None, ge=1),
    b: Optional[int] = Query(default=None, ge=1),
    db: Session = Depends(get_db),
):
    viewer = _role(request)
    _product_or_404(db, request, product_id)
    rounds = _rounds_out(db, product_id, viewer)
    if len(rounds) < 2:
        return {"enough_rounds": False, "rounds": rounds}
    numbers = [r["number"] for r in rounds]
    a = a or numbers[-2]
    b = b or numbers[-1]
    ra = next((r for r in rounds if r["number"] == a), None)
    rb = next((r for r in rounds if r["number"] == b), None)
    if ra is None or rb is None:
        raise HTTPException(status_code=404, detail="Round not found.")
    rep_a, rep_b = round_report(db, ra["id"]), round_report(db, rb["id"])
    na, nb = _numbers(rep_a), _numbers(rep_b)
    aa, ab = _aspects(rep_a), _aspects(rep_b)
    keys = set(aa) | set(ab)
    aspects = []
    for k in keys:
        x, y = aa.get(k, {}), ab.get(k, {})
        aspects.append({
            "name": (y or x).get("name"),
            "a": {"concern_pct": x.get("concern_pct"), "praise_pct": x.get("praise_pct")},
            "b": {"concern_pct": y.get("concern_pct"), "praise_pct": y.get("praise_pct")},
        })
    aspects.sort(key=lambda r: -max(r["a"]["concern_pct"] or 0, r["b"]["concern_pct"] or 0, r["a"]["praise_pct"] or 0, r["b"]["praise_pct"] or 0))

    va = db.query(RecipeVersion).filter(RecipeVersion.product_id == product_id, RecipeVersion.version == ra["recipe_version"]).first()
    vb = db.query(RecipeVersion).filter(RecipeVersion.product_id == product_id, RecipeVersion.version == rb["recipe_version"]).first()
    recipe_changes = changes_between(va.recipe, vb.recipe) if va and vb and va.version != vb.version else []

    headline = None
    if rep_a is not None and rep_b is not None and na["rating"] is not None and nb["rating"] is not None:
        direction = "up" if nb["rating"] > na["rating"] else "down" if nb["rating"] < na["rating"] else "unchanged"
        headline = (f"Rating {direction} from {_fmt(na['rating'])} to {_fmt(nb['rating'])}."
                    if direction != "unchanged" else f"Rating unchanged at {_fmt(nb['rating'])}.")
        moved = [r for r in aspects if r["a"]["concern_pct"] is not None and r["b"]["concern_pct"] is not None]
        if moved:
            biggest = max(moved, key=lambda r: abs(r["b"]["concern_pct"] - r["a"]["concern_pct"]))
            if abs(biggest["b"]["concern_pct"] - biggest["a"]["concern_pct"]) >= 5:
                headline += (f" Concern about {biggest['name'].lower()} went from {biggest['a']['concern_pct']}% "
                             f"to {biggest['b']['concern_pct']}% of consumers.")
    return {
        "enough_rounds": True,
        "rounds": rounds,
        "a": {**ra, "numbers": na, "has_report": rep_a is not None},
        "b": {**rb, "numbers": nb, "has_report": rep_b is not None},
        "aspects": aspects[:10],
        "recipe_changes": recipe_changes,
        "same_recipe": ra["recipe_version"] == rb["recipe_version"],
        "headline": headline,
    }


# ============================================================
# Starting and ending rounds
# ============================================================

class NewRound(BaseModel):
    recipe_version: Optional[int] = Field(default=None, ge=1)
    target: Optional[int] = Field(default=None, ge=R.MIN_TARGET, le=R.MAX_TARGET)
    due_date: Optional[str] = Field(default=None, max_length=10)  # YYYY-MM-DD
    note: Optional[str] = Field(default=None, max_length=1000)


def start_round_for(db: Session, request: Request, product_id: int, body: NewRound) -> dict:
    from app.services import audit

    product = _product_or_404(db, request, product_id)
    try:
        r = R.start_round(db, product, account(request), body.recipe_version, body.target, body.due_date, body.note)
    except R.RoundError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    audit.note(request, summary=f"Started Round {r.number} of product #{product.id} ({product.name}) with recipe v{r.recipe_version}",
               details={"round": r.number, "recipe_version": r.recipe_version, "target": r.target,
                        "due_date": _iso(r.due_date), "note": r.note})
    return {"round": R.round_out(r, 0, _role(request))}


def end_round_for(db: Session, request: Request, product_id: int, number: int) -> dict:
    from app.services import audit

    product = _product_or_404(db, request, product_id)
    r = R.round_by_number(db, product_id, number)
    if r is None:
        raise HTTPException(status_code=404, detail=f"Round {number} not found.")
    try:
        R.end_round(db, r)
    except R.RoundError as e:
        raise HTTPException(status_code=e.status, detail=str(e))
    R.notify_round_ended(db, product, r, account(request))
    audit.note(request, summary=f"Ended Round {r.number} of product #{product.id} ({product.name})")
    return {"round": R.round_out(r, R.done_counts(db, [r.id]).get(r.id, 0), _role(request))}


@router.post("/products/{product_id}/rounds", status_code=201)
def start_round(product_id: int, body: NewRound, request: Request, db: Session = Depends(get_db)):
    return start_round_for(db, request, product_id, body)


@router.post("/products/{product_id}/rounds/{number}/end")
def end_round(product_id: int, number: int, request: Request, db: Session = Depends(get_db)):
    return end_round_for(db, request, product_id, number)


# ============================================================
# Account
# ============================================================

@router.get("/account")
def org_account(request: Request, db: Session = Depends(get_db)):
    user = db.get(User, account(request).get("id")) if account(request).get("id") else None
    if user is None:
        raise HTTPException(status_code=404, detail="Account not found.")
    return {
        "name": user.name,
        "email": user.email,
        "mobile": user.mobile,
        "products": db.query(Product.id).filter(Product.is_active == 1).count(),
        # Shown on the Account page: who to ask to change the name or sign-in email.
        "contact": (os.environ.get("FMCG_ORG_CONTACT") or "").strip() or "your ione account manager",
        "last_login_at": _iso(user.last_login_at),
    }