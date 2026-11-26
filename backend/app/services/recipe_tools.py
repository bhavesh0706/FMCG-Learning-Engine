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

# Units of measure and their factors come from Other Master > Measurement unit (services/units.py):
# a live, read-only mapping {unit or spelling: kg per unit}. No unit is listed in code.
from app.services.units import TO_KG  # noqa: E402


def _number(value) -> Optional[float]:
    m = re.search(r"-?\d+(?:\.\d+)?", str(value or "").replace(",", ""))
    return float(m.group()) if m else None


# ---------------------------------------------------------------- batch weight and tolerance
# The weighed ingredients must weigh at least the batch size, and may weigh a little more, up
# to a tolerance: each 100 kg band has its own %, a bigger batch a smaller % (but more kg).
TOLERANCE_BANDS = [  # (batch up to kg, allowed % over the batch size)
    (100, 2.0), (200, 1.8), (300, 1.6), (400, 1.5), (500, 1.4),
    (600, 1.3), (700, 1.2), (800, 1.1), (900, 1.0), (1000, 0.9),
]
TOLERANCE_ABOVE = 0.8  # batches above 1000 kg


def tolerance_pct(batch_kg: float) -> float:
    for limit, pct in TOLERANCE_BANDS:
        if batch_kg <= limit:
            return pct
    return TOLERANCE_ABOVE


def _band_limit(batch_kg: float) -> str:
    for limit, _ in TOLERANCE_BANDS:
        if batch_kg <= limit:
            return f"batches up to {limit} kg"
    return f"batches over {TOLERANCE_BANDS[-1][0]} kg"




def batch_weight_kg(batch_size) -> Optional[float]:
    """The batch weight from its text: the first number with a weight unit
    ("100 kg batch = 500 bottles" -> 100; "1.5 tonnes" -> 1500). None if there is none."""
    # The unit: letters, including µ (µg, µL); its factor from the Measurement unit master.
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*([^\W\d_]+)", str(batch_size or "").lower().replace(",", "")):
        if m.group(2) in TO_KG:
            value = float(m.group(1)) * TO_KG[m.group(2)]
            return value if value > 0 else None
    return None


_QTY_WITH_UNIT = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*([a-zA-Z%]+)\s*$")


def normalize_ingredients(ingredients: list) -> list:
    """Quantity and unit as the recipe stores them: "65 kg" typed in Quantity becomes 65 + kg, and a
    number without a unit is kg (batches are weighed in kg). Other text is kept as it is."""
    out = []
    for ing in ingredients or []:
        ing = dict(ing)
        qty, unit = str(ing.get("quantity") or "").strip(), str(ing.get("unit") or "").strip()
        m = _QTY_WITH_UNIT.match(qty)
        if m and not unit:
            qty, unit = m.group(1).replace(",", "."), m.group(2).lower()
        if not unit and _number(qty) is not None and re.fullmatch(r"\d+(?:[.,]\d+)?", qty):
            unit = "kg"
        ing["quantity"], ing["unit"] = qty, unit
        out.append(ing)
    return out


def _unit_of(ing: dict) -> str:
    """The ingredient's unit; a plain number with no unit counts as kg (recipes saved before units
    were filled in automatically)."""
    unit = str(ing.get("unit") or "").strip().lower()
    return unit or ("kg" if _number(ing.get("quantity")) is not None else "")


def batch_check(recipe: Optional[dict]) -> dict:
    """Do the weighed ingredients fit the batch: at least the batch size, at most its tolerance over?"""
    recipe = recipe or {}
    batch = batch_weight_kg(recipe.get("batch_size"))
    grams = 0
    weighed = False
    for ing in recipe.get("ingredients") or []:
        if not str(ing.get("name") or "").strip():
            continue
        qty, unit = _number(ing.get("quantity")), _unit_of(ing)
        if qty is not None and unit in TO_KG:
            grams += round(qty * TO_KG[unit] * 1000, 3)
            weighed = True
    total = round(grams) / 1000  # whole grams: no floating-point dust (0.1 + 0.2)
    if batch is None or not weighed:
        return {"checked": False, "batch_kg": batch, "total_kg": total if weighed else None, "ok": True, "message": ""}
    pct = tolerance_pct(batch)
    allowed = round(batch * (1 + pct / 100), 3)
    short = round(max(0.0, batch - total), 3)
    over = round(max(0.0, total - allowed), 3)
    ok = short == 0 and over == 0
    if ok:
        message = f"Ingredients total {total:g} kg: within the {batch:g} kg batch (up to {allowed:g} kg allowed, +{pct:g}%)."
    elif short:
        message = (f"Ingredients total {total:g} kg, {short:g} kg less than the {batch:g} kg batch. "
                   f"Add {short:g} kg (up to {allowed:g} kg is allowed), or change the batch size.")
    else:
        message = (f"Ingredients total {total:g} kg, more than the {batch:g} kg batch allows: up to {allowed:g} kg "
                   f"(+{pct:g}% tolerance for {_band_limit(batch)}). Reduce the quantities or change the batch size.")
    return {"checked": True, "batch_kg": batch, "total_kg": total, "tolerance_pct": pct, "max_kg": allowed,
            "short_kg": short, "over_kg": over, "ok": ok, "message": message}


def costing(recipe: Optional[dict]) -> dict:
    """Per-ingredient Usage % and Cost in Use, and the recipe's cost per kg."""
    ingredients = (recipe or {}).get("ingredients") or []
    rows, kg = [], {}
    percent_given = {}
    for i, ing in enumerate(ingredients):
        qty, unit = _number(ing.get("quantity")), _unit_of(ing)
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
            "kg": round(kg[i], 9) if i in kg else None,  # its weight in the batch (custom quantity rules; µg-precise)
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
        "batch_check": batch_check(recipe),
        # Only when some weighed ingredient has a cost (no misleading "₹0" batch)
        "batch_cost": (round(sum(r["cost_per_kg"] * kg[i] for i, r in enumerate(rows) if i in kg and r["cost_per_kg"] is not None), 2)
                       if any(i in kg and r["cost_per_kg"] is not None for i, r in enumerate(rows)) else None),
        "missing_costs": missing,
        "complete": bool(rows) and not missing and all(r["usage_pct"] is not None for r in rows),
    }


def _plain(text: str) -> str:
    """A name without text in brackets: "Wheat Flour (Maida)" -> "wheat flour"."""
    return re.sub(r"\s+", " ", re.sub(r"\([^)]*\)", " ", (text or "").lower())).strip()


def _find(rows: list, name: str) -> list:
    """Ingredients named like `name`; text in brackets is optional on either side, so a standard for
    "Wheat Flour (Maida)" finds "Wheat Flour" and the other way round."""
    name = (name or "").strip().lower()
    plain = _plain(name)
    found = []
    for r in rows:
        have = str(r.get("name") or "").lower()
        if name and (name in have or (plain and (plain in have or plain == _plain(have)))):
            found.append(r)
    return found


KINDS = ("required_ingredient", "ingredient_share", "max_cost_per_kg", "costs_complete", "ingredient_cost_per_kg", "custom")


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


def unit_factor(unit: Optional[str]) -> tuple:
    """(kg per one `unit`, the unit's name as shown). None/unknown = kg (rules before UOMs)."""
    u = (unit or "").strip()
    factor = TO_KG.get(u.lower())
    return (factor, u) if u and factor else (1.0, "kg")


def _num(v: float) -> str:
    return f"{v:,.6f}".rstrip("0").rstrip(".") if v is not None else "—"


def check_rule(rule, cost: dict) -> dict:
    """One rule against a recipe's costing: expected, actual, MET / NOT MET and the reason.
    Quantity and cost limits are in the rule's UOM (rule.unit, default kg) and compared in kg."""
    rows = cost["ingredients"]
    kind, ing = rule.kind, (rule.ingredient or "").strip()
    lo, hi = rule.min_value, rule.max_value
    per_kg, u = unit_factor(getattr(rule, "unit", None))
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
    elif kind == "max_cost_per_kg":  # the WHOLE recipe's cost, per the rule's UOM (₹ per kg by default)
        value = cost["cost_per_kg"] * per_kg if cost["cost_per_kg"] is not None else None  # ₹ per UOM
        expected = f"Recipe cost at most {_money(hi)}/{u}" if hi is not None else "Recipe cost limit"
        actual = f"{_money(value)}/{u}" if value is not None else "Unknown"
        ok = value is not None and hi is not None and value <= hi + 1e-9
        reason = (f"Recipe cost {_money(value)}/{u} (max {_money(hi)}/{u})" if value is not None
                  else "Recipe cost unknown: add Cost per KG to the ingredients")
        if value is not None and not ok:
            reason = f"Recipe cost {_money(value)}/{u} is {_money(value - hi)} above the maximum {_money(hi)}/{u}"
        if cost["missing_costs"] and value is not None:
            reason += f" · {len(cost['missing_costs'])} ingredient(s) have no cost yet"
    elif kind == "ingredient_cost_per_kg":  # ONE ingredient's own Cost per KG
        found = _find(rows, ing)
        expected = f"{ing} at most {_money(hi)}/{u}" if hi is not None else f"{ing} cost limit"
        if not found:
            actual, reason = "Not in the recipe", f"No ingredient named like “{ing}”"
        else:
            missing = [r["name"] for r in found if r.get("cost_per_kg") is None]
            if missing:
                actual, reason = "No Cost per KG", "No Cost per KG for " + ", ".join(missing)
            else:
                # The ingredient's Cost per KG, expressed per the rule's UOM (₹/g = ₹/kg × 0.001).
                over = [r for r in found if hi is not None and r["cost_per_kg"] * per_kg > hi + 1e-9]
                actual = ", ".join(f"{r['name']}: {_money(r['cost_per_kg'] * per_kg)}/{u}" for r in found)
                ok = hi is not None and not over
                reason = (f"{found[0]['name']} costs {_money(found[0]['cost_per_kg'] * per_kg)}/{u} (max {_money(hi)}/{u})" if ok
                          else "; ".join(f"{r['name']} costs {_money(r['cost_per_kg'] * per_kg)}/{u}, "
                                         f"{_money(r['cost_per_kg'] * per_kg - hi)} above the maximum {_money(hi)}/{u}" for r in over))
    elif kind == "custom" and ing:  # custom rule: one ingredient's quantity within min and/or max, in the rule's UOM
        found = _find(rows, ing)
        range_text = (f"{_num(lo)}-{_num(hi)} {u}" if lo is not None and hi is not None else f"at least {_num(lo)} {u}" if lo is not None
                      else f"at most {_num(hi)} {u}" if hi is not None else "any quantity")
        expected = f"{ing}: {range_text}"
        weights = [r["kg"] for r in found if r.get("kg") is not None]
        if not found:
            actual, reason = "Not in the recipe", f"No ingredient named like “{ing}”"
        elif not weights:
            actual, reason = "No quantity", f"{ing} has no quantity in a weight or volume unit"
        else:
            total = sum(weights) / per_kg  # the recipe's quantity, in the rule's UOM
            eps = 1e-9 * max(1.0, abs(total))
            ok = (lo is None or total >= lo - eps) and (hi is None or total <= hi + eps)
            actual = f"{_num(total)} {u}"
            reason = (f"{ing} is {_num(total)} {u} (within {range_text})" if ok else
                      f"{ing} is {_num(total)} {u}, " + (f"above the maximum {_num(hi)} {u}" if hi is not None and total > hi
                                                          else f"below the minimum {_num(lo)} {u}"))
    elif kind == "custom":  # an older written rule (no ingredient): listed for people to check, never blocks
        ok, expected, actual, reason = True, rule.name, "Checked manually", "Written rule: checked by people, not by the system"
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