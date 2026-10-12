"""
Initialize a database for the FMCG Learning Engine, and keep the baseline up to date.

New installation (empty MySQL server is fine; the database is created if missing):
    python -m app.database.seed                    # create DB + tables, insert the baseline
    python -m app.database.seed --dry-run          # preview, writes nothing
    python -m app.database.seed --check            # show what the database contains

Reference system (where the app is set up the way it should be):
    python -m app.database.seed --export-baseline  # rewrite seed_data/baseline.json from this database

The baseline (seed_data/baseline.json, loaded by seed_catalog.py) holds what an empty
database needs to work like the reference system: rating rules, feedback aspects,
categories with products / survey questions / recipes, and question templates.

Safety rules (safe to run on every deployment):
  - INSERT ONLY: nothing that exists is updated, deleted or reset.
  - Records are matched by stable keys: rating rules by rule_key, aspects by aspect_key,
    categories by name, products by SKU (then category + name), templates by name.
  - Questions are added only to a product that has none; a recipe only to a product
    that has none. Changes made in the app are never touched.
  - Deactivated records count as existing (they're not re-created).
  - One transaction: if anything fails, nothing is written.
  - The export reads only, and never includes surveys, customers, recordings or accounts.

Optional first admin, for a brand-new server only (skipped when any admin exists):
    $env:FMCG_SEED_ADMIN_NAME = "Vipin Gupta"
    $env:FMCG_SEED_ADMIN_IDENTIFIER = "9876543210"     # mobile or email
    $env:FMCG_SEED_ADMIN_PASSWORD = "a-strong-password"
Without these, create the first admin on the web's "Set up your workspace" page.
"""

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timezone

from sqlalchemy import create_engine, func, text
from sqlalchemy.engine import URL

from app.database.database import SessionLocal, engine
from app.database.seed_catalog import BASELINE_FORMAT, BASELINE_PATH, BaselineError, load_baseline


# ----------------------------------------------------------------------------
# Database creation (a new user's MySQL server may not have the database yet)
# ----------------------------------------------------------------------------

def ensure_database():
    url = engine.url
    if url.get_backend_name() != "mysql" or not url.database:
        return False
    if not re.fullmatch(r"[A-Za-z0-9_]+", url.database):
        raise SystemExit(f"Unexpected database name {url.database!r}.")
    # Connect to the MySQL SERVER (no database yet). Note: url.set(database=None) would keep
    # the database in SQLAlchemy 2.x, so the address is rebuilt without it.
    server_url = URL.create(url.drivername, username=url.username, password=url.password,
                            host=url.host, port=url.port, query=url.query)
    server = create_engine(server_url, pool_pre_ping=True)
    try:
        with server.connect() as conn:
            exists = conn.execute(text("SHOW DATABASES LIKE :n"), {"n": url.database}).first()
            if not exists:
                conn.execute(text(f"CREATE DATABASE `{url.database}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"))
                return True
    finally:
        server.dispose()
    return False


# ----------------------------------------------------------------------------
# Seeding (insert-only)
# ----------------------------------------------------------------------------

class Report:
    KINDS = ("rating_rules", "aspects", "categories", "products", "questions", "recipes", "templates", "admins")

    def __init__(self):
        self.lines = []
        self.added = {k: 0 for k in self.KINDS}

    def add(self, kind, text, n=1):
        self.added[kind] += n
        self.lines.append(f"  + {text}")

    def skip(self, text):
        self.lines.append(f"  = {text}")


def seed_rating_rules(db, rules, rep):
    from app.database.models import FeedbackRatingRule

    existing = {k.lower() for (k,) in db.query(FeedbackRatingRule.rule_key).all()}
    added = 0
    for r in rules:
        if r["rule_key"].lower() in existing:
            continue
        db.add(FeedbackRatingRule(
            rule_key=r["rule_key"], sentiment=r["sentiment"], severity=r["severity"], rating=r["rating"],
            weight=r["weight"], description=r["description"], is_active=1 if r["is_active"] else 0,
            created_at=datetime.utcnow(),  # this column has no default in the model
        ))
        existing.add(r["rule_key"].lower())
        added += 1
    if added:
        rep.add("rating_rules", f"{added} rating rules", added)
    if len(rules) - added:
        rep.skip(f"{len(rules) - added} rating rules (exist, left as they are)")


def seed_aspects(db, aspects, rep):
    from app.database.models import FeedbackAspect

    by_key = {a.aspect_key.lower(): a for a in db.query(FeedbackAspect).all()}
    inserted = []  # (spec, row) for aspects added in THIS run only
    for a in aspects:
        if a["aspect_key"].lower() in by_key:
            continue
        row = FeedbackAspect(
            aspect_key=a["aspect_key"], aspect_name=a["aspect_name"], category=a["category"],
            description=a["description"], keywords=a["keywords"], is_active=1 if a["is_active"] else 0,
        )
        db.add(row)
        by_key[a["aspect_key"].lower()] = row
        inserted.append((a, row))
    db.flush()  # gives the new aspects their ids
    # Parents are stored as portable keys in the baseline; link only the NEW aspects
    # (existing aspects are never modified).
    for a, row in inserted:
        if a["parent_key"]:
            parent = by_key.get(a["parent_key"].lower())
            if parent is not None:
                row.parent_id = parent.id
    if inserted:
        rep.add("aspects", f"{len(inserted)} feedback aspects", len(inserted))
    if len(aspects) - len(inserted):
        rep.skip(f"{len(aspects) - len(inserted)} feedback aspects (exist, left as they are)")


def _find_category(db, name):
    from app.database.models import Category

    return db.query(Category).filter(func.lower(Category.name) == name.lower()).first()


def _find_product(db, category, spec):
    from app.database.models import Product

    if spec.get("sku"):
        by_sku = db.query(Product).filter(Product.sku == spec["sku"]).first()
        if by_sku:
            return by_sku
    if category is None:
        return None
    return (
        db.query(Product)
        .filter(Product.category_id == category.id, func.lower(Product.name) == spec["name"].lower())
        .first()
    )


def seed_catalog(db, categories, rep):
    from app.database.models import Category, Product, SurveyQuestion

    for group in categories:
        category = _find_category(db, group["name"])
        if category is None:
            category = Category(name=group["name"], description=group.get("description"))
            db.add(category)
            db.flush()
            rep.add("categories", f"category  {group['name']}")
        else:
            rep.skip(f"category  {category.name} (exists{'' if category.is_active else ', inactive'})")

        for spec in group["products"]:
            product = _find_product(db, category, spec)
            if product is None:
                launch = spec.get("launch_date")
                product = Product(
                    category_id=category.id, name=spec["name"], sku=spec.get("sku"),
                    description=spec.get("description"), min_responses=spec.get("min_responses") or 10,
                    launch_date=date.fromisoformat(launch) if launch else None,
                )
                db.add(product)
                db.flush()
                rep.add("products", f"product   {spec['name']} ({spec.get('sku') or 'no SKU'})")
            else:
                rep.skip(f"product   {product.name} (exists as #{product.id}{'' if product.is_active else ', inactive'})")

            count = db.query(func.count(SurveyQuestion.id)).filter(SurveyQuestion.product_id == product.id).scalar()
            if count == 0 and spec["questions"]:
                for position, q in enumerate(spec["questions"], start=1):
                    row = SurveyQuestion(product_id=product.id, position=position, text=q["text"],
                                         question_type=q["question_type"], options=q["options"],
                                         required=1 if q["required"] else 0)
                    if hasattr(SurveyQuestion, "report_role"):
                        row.report_role = q["report_role"]
                    db.add(row)
                rep.add("questions", f"{len(spec['questions'])} questions for {product.name}", len(spec["questions"]))
            elif count:
                rep.skip(f"questions for {product.name} ({count} exist, left as they are)")

            if spec.get("recipe") and hasattr(Product, "recipe"):
                if not product.recipe:
                    product.recipe = spec["recipe"]
                    rep.add("recipes", f"recipe    for {product.name}")
                else:
                    rep.skip(f"recipe    for {product.name} (exists, left as it is)")


def seed_templates(db, templates, rep):
    from app.database.models import QuestionTemplate

    for spec in templates:
        exists = db.query(QuestionTemplate).filter(func.lower(QuestionTemplate.name) == spec["name"].lower()).first()
        if exists:
            rep.skip(f"template  {exists.name} (exists, left as it is)")
            continue
        category = _find_category(db, spec["category"]) if spec.get("category") else None
        db.add(QuestionTemplate(name=spec["name"], description=spec.get("description"),
                                category_id=category.id if category else None, questions=spec["questions"]))
        rep.add("templates", f"template  {spec['name']} ({len(spec['questions'])} questions)")


def seed_first_admin(db, rep):
    """Only for a brand-new server: when FMCG_SEED_ADMIN_* are set and no admin exists."""
    from app.database.models import User

    name = (os.environ.get("FMCG_SEED_ADMIN_NAME") or "").strip()
    identifier = (os.environ.get("FMCG_SEED_ADMIN_IDENTIFIER") or "").strip()
    password = os.environ.get("FMCG_SEED_ADMIN_PASSWORD") or ""
    if db.query(User.id).filter(User.role == "admin").first():
        rep.skip("admin     an admin already exists (none created)")
        return
    if not (name and identifier and password):
        rep.skip("admin     none yet: create it on the web's 'Set up your workspace' page, or set FMCG_SEED_ADMIN_*")
        return
    if len(password) < 8:
        raise SystemExit("FMCG_SEED_ADMIN_PASSWORD must be at least 8 characters.")

    from app.api.auth import _parse_identifier, hash_password  # the same rules as sign-in

    kind, value = _parse_identifier(identifier)
    taken = db.query(User).filter((User.mobile == value) if kind == "mobile" else (User.email == value)).first()
    if taken:
        rep.skip(f"admin     {value} is already used by '{taken.name}' (none created)")
        return
    user = User(name=name, password_hash=hash_password(password), role="admin", is_active=1, can_view_history=1)
    setattr(user, kind, value)
    db.add(user)
    rep.add("admins", f"admin     {name} ({value})")


# ----------------------------------------------------------------------------
# Export the reference system's baseline (reads only)
# ----------------------------------------------------------------------------

def _question_out(q):
    return {
        "text": q.text, "question_type": q.question_type, "options": q.options or None,
        "required": bool(q.required), "report_role": getattr(q, "report_role", None),
    }


def export_baseline(db, path=BASELINE_PATH):
    """Write this database's reference + catalog data to the baseline file (no personal data)."""
    from app.database.models import (Category, FeedbackAspect, FeedbackRatingRule, Product,
                                     QuestionTemplate, SurveyQuestion)

    rules = [
        {"rule_key": r.rule_key, "sentiment": r.sentiment, "severity": r.severity, "rating": float(r.rating),
         "weight": float(r.weight if r.weight is not None else 1.0), "description": r.description,
         "is_active": bool(r.is_active)}
        for r in db.query(FeedbackRatingRule).order_by(FeedbackRatingRule.id).all()
    ]
    aspect_rows = db.query(FeedbackAspect).order_by(FeedbackAspect.id).all()
    key_of = {a.id: a.aspect_key for a in aspect_rows}
    aspects = [
        {"aspect_key": a.aspect_key, "aspect_name": a.aspect_name, "category": a.category,
         "parent_key": key_of.get(a.parent_id), "description": a.description,
         "keywords": a.keywords if isinstance(a.keywords, list) else (json.loads(a.keywords) if isinstance(a.keywords, str) and a.keywords.strip().startswith("[") else ([k.strip() for k in a.keywords.split(",") if k.strip()] if isinstance(a.keywords, str) else None)),
         "is_active": bool(a.is_active)}
        for a in aspect_rows
    ]
    categories = []
    for c in db.query(Category).filter(Category.is_active == 1).order_by(Category.name).all():
        products = []
        for p in db.query(Product).filter(Product.category_id == c.id, Product.is_active == 1).order_by(Product.name).all():
            qs = (db.query(SurveyQuestion)
                  .filter(SurveyQuestion.product_id == p.id, SurveyQuestion.is_active == 1)
                  .order_by(SurveyQuestion.position, SurveyQuestion.id).all())
            products.append({
                "name": p.name, "sku": p.sku, "description": p.description,
                "launch_date": p.launch_date.isoformat() if getattr(p, "launch_date", None) else None,
                "min_responses": p.min_responses or 10,
                "questions": [_question_out(q) for q in qs],
                "recipe": getattr(p, "recipe", None) or None,
            })
        categories.append({"name": c.name, "description": c.description, "products": products})
    cat_names = dict(db.query(Category.id, Category.name).all())
    templates = [
        {"name": t.name, "category": cat_names.get(t.category_id), "description": t.description,
         "questions": [{"text": q.get("text"), "question_type": q.get("question_type"), "options": q.get("options"),
                        "required": bool(q.get("required", True)), "report_role": q.get("report_role")}
                       for q in (t.questions or [])]}
        for t in db.query(QuestionTemplate).filter(QuestionTemplate.is_active == 1).order_by(QuestionTemplate.name).all()
    ]
    baseline = {
        "format": BASELINE_FORMAT,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source": f"Exported from database '{engine.url.database}' with --export-baseline (reference and catalog data only).",
        "rating_rules": rules, "feedback_aspects": aspects, "categories": categories, "question_templates": templates,
    }
    # Validate what we're about to write with the same loader the seed uses, then write atomically.
    tmp = path.with_suffix(".json.tmp")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(baseline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    try:
        load_baseline(tmp)
    except BaselineError as e:
        tmp.unlink(missing_ok=True)
        raise SystemExit(f"Export stopped, the data didn't pass validation: {e}")
    tmp.replace(path)
    n_products = sum(len(c["products"]) for c in categories)
    n_questions = sum(len(p["questions"]) for c in categories for p in c["products"])
    n_recipes = sum(1 for c in categories for p in c["products"] if p["recipe"])
    print(f"Baseline written to {path}")
    print(f"  {len(rules)} rating rules, {len(aspects)} feedback aspects, {len(categories)} categories, "
          f"{n_products} products, {n_questions} questions, {n_recipes} recipes, {len(templates)} question templates.")
    print("  Not included: surveys, customers, answers, recordings, reports, accounts.")
    print("Commit it so new installations get this baseline:")
    print("  git add backend/app/database/seed_data/baseline.json && git commit -m \"Update seed baseline\"")
    if not rules:
        print("  WARNING: this database has no rating rules; analyses on new installations would get no ratings.")


# ----------------------------------------------------------------------------
# Status
# ----------------------------------------------------------------------------

def print_counts(db, title):
    from app.database.models import (Category, ConsolidatedReport, FeedbackAspect, FeedbackRatingRule,
                                     FeedbackRecord, Product, QuestionTemplate, SurveyAnswer, SurveyQuestion,
                                     SurveyResponse, User)

    rows = [
        ("Rating rules", FeedbackRatingRule), ("Feedback aspects", FeedbackAspect), ("Categories", Category),
        ("Products", Product), ("Survey questions", SurveyQuestion), ("Question templates", QuestionTemplate),
        ("Users", User), ("Surveys", SurveyResponse), ("Survey answers", SurveyAnswer),
        ("Analyzed recordings", FeedbackRecord), ("Consolidated reports", ConsolidatedReport),
    ]
    print(f"\n{title}")
    for label, model in rows:
        print(f"  {label:<22}{db.query(func.count(model.id)).scalar():>6}")
    products = (
        db.query(Product.name, Product.sku, Category.name, func.count(SurveyQuestion.id))
        .join(Category, Category.id == Product.category_id)
        .outerjoin(SurveyQuestion, (SurveyQuestion.product_id == Product.id) & (SurveyQuestion.is_active == 1))
        .group_by(Product.id, Product.name, Product.sku, Category.name)
        .order_by(Category.name, Product.name)
        .all()
    )
    if products:
        print("\n  Product                         SKU                    Category               Questions")
        for name, sku, cat, q in products:
            print(f"  {name[:30]:<32}{(sku or '-')[:22]:<23}{cat[:22]:<23}{q:>5}")


# ----------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description="Initialize the database with the baseline (insert-only), or export the baseline.")
    parser.add_argument("--dry-run", action="store_true", help="show what would be inserted; write nothing")
    parser.add_argument("--check", action="store_true", help="only show what the database contains")
    parser.add_argument("--export-baseline", action="store_true",
                        help="write this database's reference + catalog data to seed_data/baseline.json (reads only)")
    args = parser.parse_args(argv)

    print(f"Database: {engine.url.render_as_string(hide_password=True)}")
    if args.export_baseline or args.check:
        created = False
    else:
        created = ensure_database()
        if created:
            print(f"Created database '{engine.url.database}'.")

    from app.database.migrations import run_migrations

    run_migrations()  # creates missing tables/columns only

    db = SessionLocal()
    try:
        if args.check:
            print_counts(db, "Current contents:")
            return 0
        if args.export_baseline:
            export_baseline(db)
            return 0

        try:
            baseline = load_baseline()
        except BaselineError as e:
            print(f"\nThe baseline file has a problem, nothing was written: {e}", file=sys.stderr)
            return 1
        print(f"Baseline: {BASELINE_PATH.name} ({baseline.get('generated_at') or 'no date'})")

        rep = Report()
        seed_rating_rules(db, baseline["rating_rules"], rep)
        seed_aspects(db, baseline["feedback_aspects"], rep)
        seed_catalog(db, baseline["categories"], rep)
        seed_templates(db, baseline["question_templates"], rep)
        seed_first_admin(db, rep)
        print("\n".join(["", "Plan (+ insert, = already there):"] + rep.lines))

        total = sum(rep.added.values())
        if args.dry_run:
            db.rollback()
            print(f"\nDry run: {total} record(s) would be inserted. Nothing was written.")
            return 0
        db.commit()
        a = rep.added
        print(
            f"\nSeed complete: inserted {a['rating_rules']} rating rules, {a['aspects']} feedback aspects, "
            f"{a['categories']} categories, {a['products']} products, {a['questions']} questions, {a['recipes']} recipes, "
            f"{a['templates']} question templates, {a['admins']} admin."
            + ("" if total else " (Everything was already there.)")
        )
        print_counts(db, "Database now contains:")
        return 0
    except SystemExit:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        print(f"\nSeed failed, nothing was written: {e.__class__.__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())