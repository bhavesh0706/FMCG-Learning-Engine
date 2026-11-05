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


KINDS = ("required_ingredient", "ingredient_share", "max_cost_per_kg", "costs_complete", "ingredient_cost_per_kg")


def applies(rule, product_id: Optional[int], category_id: Optional[int]) -> bool:
    """A rule applies to its one product, its one category, or every product when chosen
    explicitly (applies_to_all). A rule with no scope applies to nothing."""
    if not getattr(rule, "is_active", 1):
        return False
    if getattr(rule, "product_id", None):
        return rule.product_id == product_id
    if getattr(rule, "category_id", None):
        return rule.category_id == category_id
    return bool(getattr(rule, "applies_to_all", 0))


def _money(v) -> str:
    return f"₹{v:,.2f}"


def check_rule(rule, cost: dict) -> dict:
    """One rule against a recipe's costing: expected, actual, MET / NOT MET and the reason."""
    rows = cost["ingredients"]
    kind, ing = rule.kind, (rule.ingredient or "").strip()
    lo, hi = rule.min_value, rule.max_value
    ok, expected, actual, reason = False, "", "", ""
    if kind == "required_ingredient":
        found = _find(rows, ing)
        expected = f"Contains {ing}"
        actual = ", ".join(r["name"] for r in found) if found else "Not in the recipe"
        ok = bool(found)
        reason = f"Contains {ing}" if ok else f"No ingredient named like “{ing}”"
    elif kind == "ingredient_share":
        found = _find(rows, ing)
        share = sum(r["usage_pct"] or 0 for r in found) if found else None
        expected = (f"{lo:g}% – {hi:g}%" if lo is not None and hi is not None else f"at least {lo:g}%" if lo is not None
                    else f"at most {hi:g}%" if hi is not None else "any")
        actual = f"{share:.1f}%" if share is not None else "Not in the recipe"
        ok = share is not None and (lo is None or share >= lo) and (hi is None or share <= hi)
        if share is None:
            reason = f"{ing} not found or has no Usage %"
        elif ok:
            reason = f"{ing}: {share:.1f}% ({expected})"
        else:
            reason = f"{ing} is {share:.1f}% of the recipe, " + (f"below the minimum {lo:g}%" if lo is not None and share < lo else f"above the maximum {hi:g}%")
    elif kind == "max_cost_per_kg":  # the WHOLE recipe's cost per kg
        value = cost["cost_per_kg"]
        expected = f"Recipe cost at most {_money(hi)}/kg" if hi is not None else "Recipe cost limit"
        actual = f"{_money(value)}/kg" if value is not None else "Unknown"
        ok = value is not None and hi is not None and value <= hi
        reason = (f"Recipe cost {_money(value)}/kg (max {_money(hi)}/kg)" if value is not None
                  else "Recipe cost unknown: add Cost per KG to the ingredients")
        if value is not None and not ok:
            reason = f"Recipe cost {_money(value)}/kg is {_money(value - hi)} above the maximum {_money(hi)}/kg"
        if cost["missing_costs"] and value is not None:
            reason += f" · {len(cost['missing_costs'])} ingredient(s) have no cost yet"
    elif kind == "ingredient_cost_per_kg":  # ONE ingredient's own Cost per KG
        found = _find(rows, ing)
        expected = f"{ing} at most {_money(hi)}/kg" if hi is not None else f"{ing} cost limit"
        if not found:
            actual, reason = "Not in the recipe", f"No ingredient named like “{ing}”"
        else:
            missing = [r["name"] for r in found if r.get("cost_per_kg") is None]
            if missing:
                actual, reason = "No Cost per KG", "No Cost per KG for " + ", ".join(missing)
            else:
                over = [r for r in found if hi is not None and r["cost_per_kg"] > hi]
                actual = ", ".join(f"{r['name']}: {_money(r['cost_per_kg'])}/kg" for r in found)
                ok = hi is not None and not over
                reason = (f"{found[0]['name']} costs {_money(found[0]['cost_per_kg'])}/kg (max {_money(hi)}/kg)" if ok
                          else "; ".join(f"{r['name']} costs {_money(r['cost_per_kg'])}/kg, {_money(r['cost_per_kg'] - hi)} above the maximum {_money(hi)}/kg" for r in over))
    elif kind == "costs_complete":
        missing = cost["missing_costs"]
        expected, ok = "Every ingredient has a Cost per KG", not missing
        actual = "All costed" if ok else f"{len(missing)} without a cost"
        reason = "Every ingredient has a cost" if ok else "Missing Cost per KG: " + ", ".join(missing)
    else:
        expected, actual, reason = "Known rule type", kind, f"Unknown rule type “{kind}”"
    return {"rule_id": rule.id, "name": rule.name, "kind": kind, "passed": ok, "status": "MET" if ok else "NOT MET",
            "expected": expected, "actual": actual, "reason": reason, "detail": reason}


def validate(recipe: Optional[dict], rules: list, category_id: Optional[int] = None, product_id: Optional[int] = None) -> dict:
    """{passed, failed_count, checked, results: [...]} for the rules that apply to this product."""
    cost = costing(recipe)
    results = []
    for rule in rules:
        if applies(rule, product_id, category_id):
            r = check_rule(rule, cost)
            r["scope"] = "product" if getattr(rule, "product_id", None) else "category" if getattr(rule, "category_id", None) else "all"
            results.append(r)
    failed = sum(1 for r in results if not r["passed"])
    return {"passed": failed == 0, "failed_count": failed, "checked": len(results), "results": results}
