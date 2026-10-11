"""
OPTIONAL, insert-only initialization for a NEW server's database (fmcg_learning).

MySQL is the single source of truth: everything is created and edited in the app
(Catalog), which saves it to MySQL through the API. This seed is NOT needed for
that; it only fills an empty server with optional starter/demo data defined in
seed_catalog.py (products, questions, recipes and starter question templates).

Run from the backend folder, with the same backend/.env the API uses:

    python -m app.database.seed              # insert missing defaults
    python -m app.database.seed --dry-run    # show what would be inserted; writes nothing
    python -m app.database.seed --check      # only show what is in the database
    python -m app.database.seed --export-recipes   # print DB recipes to paste into seed_catalog.py

Safety rules (the seed can run on every deployment):
  - INSERT ONLY: it never updates, deletes or resets anything that exists.
  - Idempotent: running it again inserts nothing new.
  - Categories match by name; products by SKU first, then by category + name
    (a renamed or moved product is recognised, never duplicated).
  - Survey questions are added only to a product that has NO questions, and a
    recipe only to a product that has no recipe: admins' own edits are never touched.
  - Deactivated records count as existing (they are not re-created).
  - One transaction: if anything fails, nothing is written.
  - Tables/columns are created with run_migrations(), which also never drops or changes data.

Optional first admin, for a brand-new server only (skipped when any admin exists):
    $env:FMCG_SEED_ADMIN_NAME = "Vipin Gupta"
    $env:FMCG_SEED_ADMIN_IDENTIFIER = "9876543210"     # mobile or email
    $env:FMCG_SEED_ADMIN_PASSWORD = "a-strong-password"
"""

import argparse
import os
import sys

from sqlalchemy import func

from app.database.database import SessionLocal, engine
from app.database.migrations import run_migrations
from app.database.models import (
    Category,
    QuestionTemplate,
    ConsolidatedReport,
    FeedbackRecord,
    Product,
    SurveyAnswer,
    SurveyQuestion,
    SurveyResponse,
    User,
)

# ----------------------------------------------------------------------------
# The default data lives in seed_catalog.py (CATALOG). This file is the engine.
# ----------------------------------------------------------------------------

def load_catalog():
    """seed_catalog.CATALOG  {(category, description): [product, ...]}  ->  list of groups."""
    from app.database.seed_catalog import CATALOG

    return [
        {"category": name, "description": description, "products": products}
        for (name, description), products in CATALOG.items()
    ]


# ----------------------------------------------------------------------------
# Seeding (insert-only)
# ----------------------------------------------------------------------------

class Report:
    def __init__(self):
        self.lines = []
        self.added = {"categories": 0, "products": 0, "questions": 0, "recipes": 0, "templates": 0, "admins": 0}

    def add(self, kind, text):
        self.added[kind] += 1
        self.lines.append(f"  + {text}")

    def skip(self, text):
        self.lines.append(f"  = {text}")


def _find_category(db, name):
    return db.query(Category).filter(func.lower(Category.name) == name.lower()).first()


def _find_product(db, category, spec):
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


def seed_catalog(db, catalog, rep: Report):
    for group in catalog:
        category = _find_category(db, group["category"])
        if category is None:
            category = Category(name=group["category"], description=group.get("description"))
            db.add(category)
            db.flush()
            rep.add("categories", f"category  {group['category']}")
        else:
            rep.skip(f"category  {category.name} (exists{'' if category.is_active else ', inactive'})")

        for spec in group["products"]:
            product = _find_product(db, category, spec)
            if product is None:
                product = Product(
                    category_id=category.id,
                    name=spec["name"],
                    sku=spec.get("sku"),
                    description=spec.get("description"),
                )
                db.add(product)
                db.flush()
                rep.add("products", f"product   {spec['name']} ({spec.get('sku') or 'no SKU'})")
            else:
                rep.skip(f"product   {product.name} (exists as #{product.id}{'' if product.is_active else ', inactive'})")

            # Questions: only for a product that has none at all (active or not).
            count = db.query(func.count(SurveyQuestion.id)).filter(SurveyQuestion.product_id == product.id).scalar()
            if count == 0:
                for position, question in enumerate(spec.get("questions", []), start=1):
                    # (text, type, options) or (text, type, options, report_role)
                    text, qtype, options = question[0], question[1], question[2]
                    role = question[3] if len(question) > 3 else None
                    q = SurveyQuestion(
                        product_id=product.id,
                        position=position,
                        text=text,
                        question_type=qtype,
                        options=options,
                        required=1,
                    )
                    if hasattr(SurveyQuestion, "report_role"):
                        q.report_role = role
                    db.add(q)
                n = len(spec.get("questions", []))
                if n:
                    rep.added["questions"] += n
                    rep.lines.append(f"  + {n} questions for {product.name}")
            else:
                rep.skip(f"questions for {product.name} ({count} exist, left as they are)")

            # Recipe: only when the product has none.
            if spec.get("recipe") and hasattr(Product, "recipe"):
                if not product.recipe:
                    product.recipe = spec["recipe"]
                    rep.add("recipes", f"recipe    for {product.name}")
                else:
                    rep.skip(f"recipe    for {product.name} (exists, left as it is)")


def seed_templates(db, rep: Report):
    """Starter question templates: inserted only when no template has that name (active or not)."""
    from app.database.seed_catalog import STARTER_QUESTION_TEMPLATES

    for spec in STARTER_QUESTION_TEMPLATES:
        exists = db.query(QuestionTemplate).filter(func.lower(QuestionTemplate.name) == spec["name"].lower()).first()
        if exists:
            rep.skip(f"template  {exists.name} (exists, left as it is)")
            continue
        category = _find_category(db, spec["category"]) if spec.get("category") else None
        questions = [
            {
                "text": q[0], "question_type": q[1], "options": q[2], "required": True,
                "report_role": q[3] if len(q) > 3 else None,
            }
            for q in spec["questions"]
        ]
        db.add(QuestionTemplate(
            name=spec["name"], description=spec.get("description"),
            category_id=category.id if category else None, questions=questions,
        ))
        rep.add("templates", f"template  {spec['name']} ({len(questions)} questions)")


def seed_first_admin(db, rep: Report):
    """Only for a brand-new server: when FMCG_SEED_ADMIN_* are set and no admin exists."""
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


def print_counts(db, title):
    rows = [
        ("Categories", db.query(func.count(Category.id)).scalar()),
        ("Products", db.query(func.count(Product.id)).scalar()),
        ("Survey questions", db.query(func.count(SurveyQuestion.id)).scalar()),
        ("Question templates", db.query(func.count(QuestionTemplate.id)).scalar()),
        ("Users", db.query(func.count(User.id)).scalar()),
        ("Surveys", db.query(func.count(SurveyResponse.id)).scalar()),
        ("Survey answers", db.query(func.count(SurveyAnswer.id)).scalar()),
        ("Analyzed recordings", db.query(func.count(FeedbackRecord.id)).scalar()),
        ("Consolidated reports", db.query(func.count(ConsolidatedReport.id)).scalar()),
    ]
    print(f"\n{title}")
    for label, n in rows:
        print(f"  {label:<22}{n:>6}")
    products = (
        db.query(Product.name, Product.sku, Category.name, func.count(SurveyQuestion.id))
        .join(Category, Category.id == Product.category_id)
        .outerjoin(SurveyQuestion, SurveyQuestion.product_id == Product.id)
        .group_by(Product.id, Product.name, Product.sku, Category.name)
        .order_by(Category.name, Product.name)
        .all()
    )
    if products:
        print("\n  Product                         SKU                    Category               Questions")
        for name, sku, cat, q in products:
            print(f"  {name[:30]:<32}{(sku or '-')[:22]:<23}{cat[:22]:<23}{q:>5}")


def export_recipes(db):
    """Print every product's recipe from the database, ready to paste into seed_catalog.py."""
    import pprint
    import re

    products = db.query(Product).order_by(Product.id).all()
    found = 0
    print("\n# ---- Recipes from the database: paste into seed_catalog.py ----\n")
    for p in products:
        recipe = p.recipe if hasattr(Product, "recipe") else None
        if isinstance(recipe, str):
            import json

            try:
                recipe = json.loads(recipe)
            except ValueError:
                recipe = None
        if not recipe:
            continue
        found += 1
        clean = {k: recipe.get(k) for k in ("batch_size", "ingredients", "steps", "notes")}
        var = re.sub(r"[^A-Z0-9]+", "_", (p.sku or p.name).upper()).strip("_") + "_RECIPE"
        print(f"# {p.name}  (SKU {p.sku or '-'}): in CATALOG use  \"recipe\": {var},")
        print(f"{var} = " + pprint.pformat(clean, width=110, sort_dicts=False) + "\n")
    print(f"# {found} recipe(s) exported. Nothing was changed in the database.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Insert missing default data. Never changes or deletes existing data.")
    parser.add_argument("--dry-run", action="store_true", help="show what would be inserted; write nothing")
    parser.add_argument("--check", action="store_true", help="only show what is in the database")
    parser.add_argument("--export-recipes", action="store_true",
                        help="print the recipes stored in the database, ready to paste into seed_catalog.py")
    args = parser.parse_args(argv)

    # Which database this is (from the same backend/.env the API uses; password hidden).
    # (stderr while exporting, so the exported text stays pure, paste-ready Python)
    print(f"Database: {engine.url.render_as_string(hide_password=True)}", file=sys.stderr if args.export_recipes else sys.stdout)
    run_migrations()  # creates missing tables/columns only

    db = SessionLocal()
    try:
        if args.check:
            print_counts(db, "Current contents:")
            return 0
        if args.export_recipes:
            export_recipes(db)
            return 0

        rep = Report()
        seed_catalog(db, load_catalog(), rep)
        seed_templates(db, rep)
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
            f"\nSeed complete: inserted {a['categories']} categories, {a['products']} products, "
            f"{a['questions']} questions, {a['recipes']} recipes, {a['templates']} question templates, {a['admins']} admin."
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