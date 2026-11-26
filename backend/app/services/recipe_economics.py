"""
Recipe economics: what a proposed recipe version costs compared with the current one, and whether
it stays commercially viable (all figures computed here, on the server, from recipe_tools.costing).

- compare(old, new, targets): ingredient by ingredient (quantity kg, cost per kg, cost in use = cost
  per kg x share of the batch, batch cost = kg x cost per kg), the totals (batch cost, cost per kg of
  product) and, for both recipes, the gross profit per kg and the margin at the selling price; flags
  when the proposed recipe exceeds the cost limit or brings the margin below the minimum.
- recommendations(...): the latest consumer report's recipe recommendations, each with the recipe
  ingredient it refers to and the quantity it proposes ("... to 2 kg", "... by 2 kg"), so a screen can
  apply it to the new version (the Super Admin accepts, rejects or changes it).
"""
from __future__ import annotations

import re
from typing import Optional

from app.services.recipe_tools import TO_KG, _find, _number, costing  # TO_KG: Other Master units

UNIT = r"(kg|kgs|kilograms?|g|gm|gms|grams?)"


def targets_of(product) -> dict:
    """The product's commercial targets (all optional)."""
    return {"selling_price_per_kg": product.selling_price_per_kg, "target_cost_per_kg": product.target_cost_per_kg,
            "min_margin_pct": product.min_margin_pct}


def _money(v: Optional[float]) -> Optional[float]:
    return None if v is None else round(v, 2)


def _profit(cost_per_kg: Optional[float], t: dict) -> dict:
    price = t.get("selling_price_per_kg")
    if cost_per_kg is None or not price:
        return {"gross_profit_per_kg": None, "margin_pct": None}
    gp = price - cost_per_kg
    return {"gross_profit_per_kg": _money(gp), "margin_pct": round(100 * gp / price, 1)}


def _lines(recipe: Optional[dict]) -> dict:
    """Ingredient name (lower case) -> its line: kg, cost per kg, cost in use, batch cost."""
    c = costing(recipe)
    out = {}
    for r in c["ingredients"]:
        name = str(r.get("name") or "").strip()
        if not name:
            continue
        kg, cpk = r.get("kg"), r.get("cost_per_kg")
        out[name.lower()] = {"name": name, "kg": kg, "cost_per_kg": cpk, "cost_in_use": r.get("cost_in_use"),
                             "batch_cost": _money(kg * cpk) if kg is not None and cpk is not None else None}
    return out


def compare(old: Optional[dict], new: Optional[dict], t: dict) -> dict:
    a, b = costing(old), costing(new)
    la, lb = _lines(old), _lines(new)
    rows = []
    for key in list(la) + [k for k in lb if k not in la]:
        x, y = la.get(key), lb.get(key)
        status = "added" if x is None else "removed" if y is None else "changed" if (
            x["kg"] != y["kg"] or x["cost_per_kg"] != y["cost_per_kg"]) else "same"
        bx, by = (x or {}).get("batch_cost"), (y or {}).get("batch_cost")
        rows.append({
            "ingredient": (y or x)["name"], "status": status,
            "old_kg": (x or {}).get("kg"), "new_kg": (y or {}).get("kg"),
            "old_cost_per_kg": (x or {}).get("cost_per_kg"), "new_cost_per_kg": (y or {}).get("cost_per_kg"),
            "old_cost_in_use": (x or {}).get("cost_in_use"), "new_cost_in_use": (y or {}).get("cost_in_use"),
            "old_batch_cost": bx, "new_batch_cost": by,
            "batch_cost_change": _money((by or 0) - (bx or 0)) if (bx is not None or by is not None) else None,
        })
    old_cpk, new_cpk = a.get("cost_per_kg"), b.get("cost_per_kg")
    po, pn = _profit(old_cpk, t), _profit(new_cpk, t)
    limit, min_margin = t.get("target_cost_per_kg"), t.get("min_margin_pct")
    warnings = []
    if limit is not None and new_cpk is not None and new_cpk > limit:
        warnings.append({"code": "over_cost_limit",
                         "message": f"The proposed recipe costs ₹{new_cpk:,.2f} per kg, above the cost limit of ₹{limit:,.2f} per kg."})
    if min_margin is not None and pn["margin_pct"] is not None and pn["margin_pct"] < min_margin:
        warnings.append({"code": "margin_below_minimum",
                         "message": f"The margin is {pn['margin_pct']}%, below the minimum of {min_margin:g}%."})
    if not b.get("complete"):
        warnings.append({"code": "costs_incomplete",
                         "message": "Some ingredients have no cost per kg or no weight: the cost of the proposed recipe is incomplete."})
    return {
        "rows": rows,
        "old": {"batch_cost": a.get("batch_cost"), "cost_per_kg": old_cpk, **po},
        "new": {"batch_cost": b.get("batch_cost"), "cost_per_kg": new_cpk, **pn},
        "change": {"batch_cost": _money((b.get("batch_cost") or 0) - (a.get("batch_cost") or 0)) if a.get("batch_cost") is not None and b.get("batch_cost") is not None else None,
                   "cost_per_kg": _money(new_cpk - old_cpk) if old_cpk is not None and new_cpk is not None else None,
                   "margin_pct": round(pn["margin_pct"] - po["margin_pct"], 1) if pn["margin_pct"] is not None and po["margin_pct"] is not None else None},
        "targets": t,
        "warnings": warnings,
        # these two need the Super Admin's explicit approval before a new version is released
        "needs_approval": any(w["code"] in ("over_cost_limit", "margin_below_minimum") for w in warnings),
    }


def _kg(qty: str, unit: str) -> Optional[float]:
    q = _number(qty)
    u = unit.strip().lower()  # a master unit or one of its spellings (services/units.py)
    return round(q * TO_KG[u], 4) if q is not None and u in TO_KG else None


def proposal(rec: dict, recipe: Optional[dict]) -> dict:
    """The recipe change a recommendation proposes: which ingredient, from and to how many kg."""
    rows = costing(recipe)["ingredients"]
    name = (rec.get("ingredient") or "").strip()
    found = _find(rows, name) if name else []
    if not found:  # the ingredient named in the text
        found = [r for r in rows if r.get("name") and str(r["name"]).lower() in str(rec.get("change") or "").lower()]
    if not found:
        return {"applicable": False, "reason": "No ingredient of the recipe is named in this recommendation."}
    row = found[0]
    text = str(rec.get("change") or "")
    current = row.get("kg")
    to = re.search(rf"\bto\s+(\d+(?:[.,]\d+)?)\s*{UNIT}\b", text, re.I)
    by = re.search(rf"\bby\s+(\d+(?:[.,]\d+)?)\s*{UNIT}\b", text, re.I)
    proposed = None
    if to:
        proposed = _kg(to.group(1), to.group(2))
    elif by and current is not None:
        step = _kg(by.group(1), by.group(2))
        down = re.search(r"\b(reduce|decrease|lower|less|cut)\b", text, re.I)
        proposed = None if step is None else round(max(current - step, 0) if down else current + step, 4)
    if proposed is None:
        return {"applicable": False, "ingredient": row["name"], "current_kg": current,
                "reason": "The recommendation gives no new quantity: change the ingredient yourself."}
    return {"applicable": True, "ingredient": row["name"], "current_kg": current, "proposed_kg": proposed}


def recommendations(db, product) -> dict:
    """The latest consumer report's recipe recommendations for this product, ready to apply."""
    from app.database.models import ConsolidatedReport
    rep = (db.query(ConsolidatedReport).filter(ConsolidatedReport.product_id == product.id)
           .order_by(ConsolidatedReport.id.desc()).first())
    items = ((rep.narrative or {}).get("recipe_recommendations") if rep else None) or []
    out = []
    for i, rec in enumerate(items):
        if isinstance(rec, dict) and rec.get("change"):
            out.append({"id": i, "change": rec["change"], "reason": rec.get("reason") or "", "customers": rec.get("customers"),
                        "step": rec.get("step") or "", **proposal(rec, product.recipe)})
    return {"report_id": rep.id if rep else None, "responses": rep.response_count if rep else 0, "recommendations": out}


def report_cost_comparison(db, report) -> Optional[dict]:
    """For a consolidated report: what its recipe recommendations would cost if applied to the recipe
    version that report surveyed (an estimate shown beside the recommendations; nothing is changed)."""
    import copy

    from app.database.models import Product, RecipeVersion, SurveyRound
    recs = [r for r in ((report.narrative or {}).get("recipe_recommendations") or []) if isinstance(r, dict)]
    product = db.get(Product, report.product_id)
    if not recs or product is None:
        return None
    version, base = None, product.recipe
    rnd = db.get(SurveyRound, report.round_id) if report.round_id else None
    if rnd is not None and rnd.recipe_version:
        row = (db.query(RecipeVersion).filter(RecipeVersion.product_id == product.id, RecipeVersion.version == rnd.recipe_version).first())
        if row is not None:
            version, base = row.version, row.recipe
    if not base or not base.get("ingredients"):
        return None
    proposed = copy.deepcopy(base)
    applied = []
    for rec in recs:
        p = proposal(rec, base)
        if not p.get("applicable"):
            continue
        for ing in proposed["ingredients"]:
            if str(ing.get("name") or "").strip().lower() == p["ingredient"].lower():
                ing["quantity"], ing["unit"] = f"{p['proposed_kg']:g}", "kg"
                applied.append({"ingredient": p["ingredient"], "from_kg": p["current_kg"], "to_kg": p["proposed_kg"], "change": rec.get("change")})
                break
    if not applied:
        return None
    return {**compare(base, proposed, targets_of(product)), "recipe_version": version, "applied": applied}


def with_cost_comparison(report, narrative):
    """The report's narrative with its cost comparison added (never fails the report)."""
    try:
        from sqlalchemy.orm import object_session
        db = object_session(report)
        cc = report_cost_comparison(db, report) if db is not None else None
    except Exception:  # noqa: BLE001 (an estimate must never break the report)
        cc = None
    if not cc:
        return narrative
    return {**(narrative or {}), "cost_comparison": cc}