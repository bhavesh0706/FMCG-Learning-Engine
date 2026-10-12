"""
Baseline data for a NEW installation: loads and validates seed_data/baseline.json.

MySQL (fmcg_learning) is the single source of truth for live data: everything is
created, edited and deleted in the app, which saves it to MySQL through the API.
The baseline is only what an EMPTY database needs so the app works exactly like the
reference system:

  rating_rules        sentiment + severity -> star rating (the analysis needs these,
                      otherwise feedback gets no ratings)
  feedback_aspects    canonical aspects ("Sweetness", "Packaging"...) the analysis maps
                      what customers say onto, with categories and keywords
  categories          with their products, survey questions and recipes
  question_templates  starting questions offered when adding a product

Keep it up to date from the reference system (reads only; never copies surveys,
customers, recordings or accounts):
    python -m app.database.seed --export-baseline     # rewrites seed_data/baseline.json
    git add backend/app/database/seed_data/baseline.json && git commit -m "Update baseline"

Load it into a database (insert-only, safe to repeat):
    python -m app.database.seed_catalog               # same as: python -m app.database.seed
"""

import json
from pathlib import Path

BASELINE_PATH = Path(__file__).resolve().parent / "seed_data" / "baseline.json"
BASELINE_FORMAT = "fmcg-baseline/1"

QUESTION_TYPES = {"rating", "single_choice", "yes_no", "text"}
REPORT_ROLES = {None, "preference", "price", "score", "improvement", "purchase_intent", "ease"}


class BaselineError(ValueError):
    pass


def _question(q, where):
    if not isinstance(q, dict) or not str(q.get("text") or "").strip():
        raise BaselineError(f"{where}: every question needs a 'text'.")
    qtype = q.get("question_type")
    if qtype not in QUESTION_TYPES:
        raise BaselineError(f"{where}: question_type must be one of {sorted(QUESTION_TYPES)}, not {qtype!r}.")
    options = q.get("options")
    if qtype == "single_choice":
        if not isinstance(options, list) or len([o for o in options if str(o).strip()]) < 2:
            raise BaselineError(f"{where}: a single_choice question needs at least 2 options.")
        options = [str(o).strip() for o in options if str(o).strip()]
    else:
        options = None
    role = q.get("report_role")
    if role not in REPORT_ROLES:
        raise BaselineError(f"{where}: unknown report_role {role!r}.")
    return {
        "text": str(q["text"]).strip(),
        "question_type": qtype,
        "options": options,
        "required": bool(q.get("required", True)),
        "report_role": role,
    }


def load_baseline(path: Path = BASELINE_PATH) -> dict:
    """Read and validate the baseline file. Raises BaselineError with the exact problem."""
    path = Path(path)
    if not path.is_file():
        raise BaselineError(f"Baseline file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise BaselineError(f"{path.name} is not valid JSON (line {e.lineno}): {e.msg}")
    if data.get("format") != BASELINE_FORMAT:
        raise BaselineError(f"{path.name}: expected format {BASELINE_FORMAT!r}, found {data.get('format')!r}.")

    rules = []
    for i, r in enumerate(data.get("rating_rules") or [], start=1):
        where = f"rating_rules[{i}]"
        try:
            rating = float(r["rating"])
        except (KeyError, TypeError, ValueError):
            raise BaselineError(f"{where}: 'rating' must be a number.")
        if not (0 <= rating <= 5):
            raise BaselineError(f"{where}: rating must be between 0 and 5.")
        for k in ("rule_key", "sentiment", "severity"):
            if not str(r.get(k) or "").strip():
                raise BaselineError(f"{where}: '{k}' is required.")
        rules.append({
            "rule_key": str(r["rule_key"]).strip(), "sentiment": str(r["sentiment"]).strip(),
            "severity": str(r["severity"]).strip(), "rating": rating,
            "weight": float(r.get("weight", 1.0) or 1.0), "description": r.get("description"),
            "is_active": bool(r.get("is_active", True)),
        })

    aspects = []
    for i, a in enumerate(data.get("feedback_aspects") or [], start=1):
        where = f"feedback_aspects[{i}]"
        if not str(a.get("aspect_key") or "").strip() or not str(a.get("aspect_name") or "").strip():
            raise BaselineError(f"{where}: 'aspect_key' and 'aspect_name' are required.")
        keywords = a.get("keywords")
        if keywords is not None and not isinstance(keywords, list):
            raise BaselineError(f"{where}: 'keywords' must be a list of words.")
        aspects.append({
            "aspect_key": str(a["aspect_key"]).strip(), "aspect_name": str(a["aspect_name"]).strip(),
            "category": a.get("category"), "parent_key": a.get("parent_key"), "description": a.get("description"),
            "keywords": keywords, "is_active": bool(a.get("is_active", True)),
        })
    keys = {a["aspect_key"] for a in aspects}
    for a in aspects:
        if a["parent_key"] and a["parent_key"] not in keys:
            raise BaselineError(f"feedback_aspects: '{a['aspect_key']}' has unknown parent_key '{a['parent_key']}'.")

    categories, skus = [], set()
    for ci, c in enumerate(data.get("categories") or [], start=1):
        if not str(c.get("name") or "").strip():
            raise BaselineError(f"categories[{ci}]: 'name' is required.")
        products = []
        for pi, p in enumerate(c.get("products") or [], start=1):
            where = f"{c['name']} > product {pi}"
            if not str(p.get("name") or "").strip():
                raise BaselineError(f"{where}: 'name' is required.")
            sku = (p.get("sku") or "").strip() or None
            if sku:
                if sku.upper() in skus:
                    raise BaselineError(f"{where}: SKU {sku} is used twice in the baseline.")
                skus.add(sku.upper())
            questions = [_question(q, f"{p['name']} > question {qi}") for qi, q in enumerate(p.get("questions") or [], start=1)]
            if len(questions) > 10:
                raise BaselineError(f"{p['name']}: at most 10 questions are allowed.")
            recipe = p.get("recipe")
            if recipe is not None and not isinstance(recipe, dict):
                raise BaselineError(f"{p['name']}: 'recipe' must be an object.")
            products.append({
                "name": str(p["name"]).strip(), "sku": sku, "description": p.get("description"),
                "launch_date": p.get("launch_date"), "min_responses": int(p.get("min_responses") or 10),
                "questions": questions, "recipe": recipe,
            })
        categories.append({"name": str(c["name"]).strip(), "description": c.get("description"), "products": products})

    templates = []
    for ti, t in enumerate(data.get("question_templates") or [], start=1):
        if not str(t.get("name") or "").strip():
            raise BaselineError(f"question_templates[{ti}]: 'name' is required.")
        questions = [_question(q, f"template {t['name']} > question {qi}") for qi, q in enumerate(t.get("questions") or [], start=1)]
        if not questions:
            raise BaselineError(f"template {t['name']}: needs at least one question.")
        templates.append({"name": str(t["name"]).strip(), "category": t.get("category"),
                          "description": t.get("description"), "questions": questions})

    return {
        "format": data["format"], "generated_at": data.get("generated_at"), "source": data.get("source"),
        "rating_rules": rules, "feedback_aspects": aspects, "categories": categories, "question_templates": templates,
    }


def seed(argv=None):
    """Kept so `python -m app.database.seed_catalog` keeps working."""
    from app.database.seed import main

    return main(argv)


if __name__ == "__main__":
    import sys

    sys.exit(seed(sys.argv[1:]))