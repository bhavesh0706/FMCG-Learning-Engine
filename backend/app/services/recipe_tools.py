"""
Recipe costing and standards (requirements points 1 and 3).

Costing
  Usage %      each ingredient's share of the batch: from its weight (kg, g, mg, and
               l / ml counted like kg / g), or its quantity when the unit is "%".
  Cost in Use  = Cost per KG x Usage % / 100   (cost of that ingredient in 1 kg of product)
  Cost per KG of the recipe = sum of every ingredient's Cost in Use.

Standards (RecipeStandard)
  required_ingredient  the recipe contains an ingredient whose name includes X
  ingredient_share     X's Usage % is between min and max
  max_cost_per_kg      the recipe's cost per kg is at most max
  costs_complete       every ingredient has a Cost per KG
A rule with a category applies to that category's products only.
"""

from __future__ import annotations

import re
from typing import Optional

TO_KG = {"kg": 1.0, "kgs": 1.0, "g": 0.001, "gm": 0.001, "gms": 0.001, "gram": 0.001, "grams": 0.001,
         "mg": 0.000001, "l": 1.0, "ltr": 1.0, "litre": 1.0, "liter": 1.0, "ml": 0.001}


def _number(value) -> Optional[float]:
    m = re.search(r"-?\d+(?:\.\d+)?", str(value or "").replace(",", ""))
    return float(m.group()) if m else None


def costing(recipe: Optional[dict]) -> dict:
    """Per-ingredient Usage % and Cost in Use, and the recipe's cost per kg."""
    ingredients = (recipe or {}).get("ingredients") or []
    rows, kg = [], {}
    percent_given = {}
    for i, ing in enumerate(ingredients):
        qty, unit = _number(ing.get("quantity")), str(ing.get("unit") or "").strip().lower()
        if qty is not None and unit == "%":
            percent_given[i] = qty
        elif qty is not None and unit in TO_KG:
            kg[i] = qty * TO_KG[unit]
    total_kg = sum(kg.values())
    for i, ing in enumerate(ingredients):
        usage = percent_given.get(i)
        if usage is None and i in kg and total_kg > 0:
            usage = kg[i] / total_kg * 100
        cost = ing.get("cost_per_kg")
        cost = float(cost) if isinstance(cost, (int, float)) else _number(cost)
        rows.append({
            "name": ing.get("name"),
            "usage_pct": round(usage, 2) if usage is not None else None,
            "cost_per_kg": cost,
            "cost_in_use": round(cost * usage / 100, 2) if cost is not None and usage is not None else None,
        })
    known = [r["cost_in_use"] for r in rows if r["cost_in_use"] is not None]
    missing = [r["name"] for r in rows if r["cost_per_kg"] is None]
    return {
        "ingredients": rows,
        "cost_per_kg": round(sum(known), 2) if known else None,
        "batch_kg": round(total_kg, 3) if total_kg else None,
        # Only when some weighed ingredient has a cost (no misleading "₹0" batch)
        "batch_cost": (round(sum(r["cost_per_kg"] * kg[i] for i, r in enumerate(rows) if i in kg and r["cost_per_kg"] is not None), 2)
                       if any(i in kg and r["cost_per_kg"] is not None for i, r in enumerate(rows)) else None),
        "missing_costs": missing,
        "complete": bool(rows) and not missing and all(r["usage_pct"] is not None for r in rows),
    }


def _find(rows: list, name: str) -> list:
    name = (name or "").strip().lower()
    return [r for r in rows if name and name in str(r.get("name") or "").lower()]


def validate(recipe: Optional[dict], rules: list, category_id: Optional[int] = None) -> dict:
    """{passed, failed_count, results: [{rule_id, name, passed, detail}]} for the rules that apply."""
    cost = costing(recipe)
    rows = cost["ingredients"]
    results = []
    for rule in rules:
        if not rule.is_active or (rule.category_id and rule.category_id != category_id):
            continue
        ok, detail = True, ""
        if rule.kind == "required_ingredient":
            ok = bool(_find(rows, rule.ingredient))
            detail = f"Contains {rule.ingredient}" if ok else f"No ingredient named like “{rule.ingredient}”"
        elif rule.kind == "ingredient_share":
            found = _find(rows, rule.ingredient)
            share = sum(r["usage_pct"] or 0 for r in found) if found else None
            lo, hi = rule.min_value, rule.max_value
            ok = share is not None and (lo is None or share >= lo) and (hi is None or share <= hi)
            want = " – ".join(x for x in (f"{lo:g}%" if lo is not None else "", f"{hi:g}%" if hi is not None else "") if x) or "any"
            detail = (f"{rule.ingredient}: {share:.1f}% (allowed {want})" if share is not None else f"{rule.ingredient} not found or has no usage %")
        elif rule.kind == "max_cost_per_kg":
            value = cost["cost_per_kg"]
            ok = value is not None and rule.max_value is not None and value <= rule.max_value
            detail = (f"Cost per kg {value:.2f} (max {rule.max_value:g})" if value is not None else "Cost per kg unknown: add Cost per KG to the ingredients")
        elif rule.kind == "costs_complete":
            ok = not cost["missing_costs"]
            detail = "Every ingredient has a cost" if ok else "Missing Cost per KG: " + ", ".join(cost["missing_costs"][:6])
        results.append({"rule_id": rule.id, "name": rule.name, "kind": rule.kind, "passed": ok, "detail": detail})
    failed = sum(1 for r in results if not r["passed"])
    return {"passed": failed == 0, "failed_count": failed, "checked": len(results), "results": results}
