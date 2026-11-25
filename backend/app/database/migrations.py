"""
Database setup that is safe to run on every startup.

create_all() creates NEW tables, but never changes tables that already exist.
So columns added to an existing table are added here, only if missing.
Each step can run again without harm.
"""

from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.database import models  # noqa: F401  (registers every table on Base)
from app.database.database import Base, SessionLocal, engine


def _has_column(table: str, column: str) -> bool:
    inspector = inspect(engine)
    if table not in inspector.get_table_names():
        return False
    return any(c["name"] == column for c in inspector.get_columns(table))


def _has_index(table: str, name: str) -> bool:
    return any(ix.get("name") == name for ix in inspect(engine).get_indexes(table))


def _already_exists(error: Exception) -> bool:
    # Another process (e.g. uvicorn --reload restarting while the seed script
    # runs) added it a moment earlier. MySQL: 1060 duplicate column,
    # 1061 duplicate index. SQLite says "duplicate column" / "already exists".
    message = str(getattr(error, "orig", error)).lower()
    return any(s in message for s in ("1060", "1061", "duplicate column", "duplicate key name", "already exists"))


def _add_column(table: str, column: str, ddl: str, index: bool = False):
    if not _has_column(table, column):
        try:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))
            print(f"Database updated: added {table}.{column}")
        except (OperationalError, ProgrammingError) as e:
            if not _already_exists(e):
                raise

    index_name = f"ix_{table}_{column}"
    if index and not _has_index(table, index_name):
        try:
            with engine.begin() as conn:
                conn.execute(text(f"CREATE INDEX {index_name} ON {table} ({column})"))
        except (OperationalError, ProgrammingError) as e:
            if not _already_exists(e):
                raise


def _add_unique_index(table: str, column: str) -> None:
    """A UNIQUE index on an existing column (several NULLs stay allowed)."""
    inspector = inspect(engine)
    for ix in inspector.get_indexes(table):
        if ix.get("unique") and ix.get("column_names") == [column]:
            return
    for uc in inspector.get_unique_constraints(table):
        if uc.get("column_names") == [column]:
            return
    try:
        with engine.begin() as conn:
            conn.execute(text(f"CREATE UNIQUE INDEX ux_{table}_{column} ON {table} ({column})"))
        print(f"Database updated: unique index on {table}.{column}")
    except (OperationalError, ProgrammingError) as e:
        if not _already_exists(e):
            raise


def _q(name: str) -> str:
    return engine.dialect.identifier_preparer.quote(name)


def _rename_column(table: str, old: str, new: str) -> None:
    try:
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {_q(table)} RENAME COLUMN {_q(old)} TO {_q(new)}"))
        print(f"Database updated: renamed {table}.{old} to {new}")
    except (OperationalError, ProgrammingError) as e:
        # Another process renamed it a moment earlier.
        if not _has_column(table, new):
            raise e


def record_no_and_audit_columns() -> None:
    """
    Every table (also tables the code doesn't use any more):
      - primary key id            -> record_no   (MySQL 8 updates foreign keys that point at it)
      - updated_at                -> mod_at
      - created_by holding a name -> created_by_name  (recipe_standards; created_by is now an account id)
      - adds created_by, mod_by (INT, users.record_no) and mod_at (DATETIME) where missing.
    Data is kept. Runs again without harm: renamed tables are skipped.
    """
    inspector = inspect(engine)
    for table in inspector.get_table_names():
        cols = {c["name"]: c for c in inspector.get_columns(table)}
        if "id" in cols and "record_no" in cols:
            _drop_empty_record_no(table)
            cols.pop("record_no")
        if "id" in cols and "record_no" not in cols:
            _rename_column(table, "id", "record_no")
        if "updated_at" in cols and "mod_at" not in cols:
            _rename_column(table, "updated_at", "mod_at")
        created_by = cols.get("created_by")
        if created_by is not None and _python_type(created_by["type"]) is str:
            if "created_by_name" in cols:
                _split_text_created_by(table)
            else:
                _rename_column(table, "created_by", "created_by_name")
        _add_column(table, "created_by", "INT NULL")
        _add_column(table, "mod_by", "INT NULL")
        _add_column(table, "mod_at", "DATETIME NULL")
    _revoked_tokens_record_no()


def _drop_empty_record_no(table: str) -> None:
    """
    A table with both id (the real key) and an empty record_no: sync_columns added
    record_no as a plain column when the models already said record_no but this
    rename step hadn't run yet. Drop the empty column so id can be renamed.
    Refuses (stops start-up) if record_no holds any value, so nothing is lost.
    """
    with engine.begin() as conn:
        filled = conn.execute(text(f"SELECT COUNT(record_no) FROM {_q(table)}")).scalar()
        if filled:
            raise RuntimeError(f"{table} has both id and record_no, and record_no holds {filled} values. "
                               "Not changed automatically; please check this table by hand.")
        conn.execute(text(f"ALTER TABLE {_q(table)} DROP COLUMN record_no"))
    print(f"Database updated: removed the empty {table}.record_no (id is renamed next)")


def _split_text_created_by(table: str) -> None:
    """
    created_by holds names (text) and created_by_name exists too (added empty by
    sync_columns): move the names to created_by_name, keep account ids (digits)
    in created_by, and make created_by a number column.
    """
    with engine.begin() as conn:
        rows = conn.execute(text(f"SELECT record_no, created_by, created_by_name FROM {_q(table)}")).all()
        for rid, by, by_name in rows:
            if by is None or str(by).strip().isdigit():
                continue
            conn.execute(text(f"UPDATE {_q(table)} SET created_by = NULL, created_by_name = :n WHERE record_no = :r"),
                         {"n": by_name if by_name else by, "r": rid})
        if engine.dialect.name == "mysql":
            conn.execute(text(f"ALTER TABLE {_q(table)} MODIFY COLUMN created_by INT NULL"))
    print(f"Database updated: {table}.created_by names moved to created_by_name; created_by is now an account id")


def category_project_id() -> None:
    """
    categories.project_id: added empty, existing categories get PRJ-0001, PRJ-0002 ...
    in record_no order, then the column becomes required and unique.
    """
    if not _has_column("categories", "record_no"):
        return
    added = not _has_column("categories", "project_id")
    _add_column("categories", "project_id", "VARCHAR(50) NULL")
    with engine.begin() as conn:
        empty = conn.execute(text("SELECT record_no FROM categories WHERE project_id IS NULL OR project_id = '' "
                                  "ORDER BY record_no")).scalars().all()
        if empty:
            used = {v for (v,) in conn.execute(text("SELECT project_id FROM categories WHERE project_id IS NOT NULL"))}
            n = 0
            for rid in empty:
                n += 1
                while f"CAT-{n:04d}" in used:
                    n += 1
                conn.execute(text("UPDATE categories SET project_id = :p WHERE record_no = :r"), {"p": f"CAT-{n:04d}", "r": rid})
            print(f"Database updated: project_id given to {len(empty)} existing categories")
    # Automatic category codes PRJ-#### -> CAT-#### (once; hand-typed codes are kept)
    import re as _re

    with engine.begin() as conn:
        rows = conn.execute(text("SELECT record_no, project_id FROM categories WHERE project_id LIKE 'PRJ-%'")).all()
        used = {v for (v,) in conn.execute(text("SELECT project_id FROM categories WHERE project_id IS NOT NULL"))}
        moved = 0
        for rid, code in rows:
            if _re.fullmatch(r"PRJ-\d{4,}", code or "") and f"CAT-{code[4:]}" not in used:
                conn.execute(text("UPDATE categories SET project_id = :p WHERE record_no = :r"), {"p": f"CAT-{code[4:]}", "r": rid})
                used.add(f"CAT-{code[4:]}")
                moved += 1
        if moved:
            print(f"Database updated: {moved} category code(s) PRJ-#### renamed to CAT-#### (PRJ- codes are for projects now)")
    category_codes_unique()
    if not _has_index("categories", "ix_categories_project_id"):
        try:
            with engine.begin() as conn:
                conn.execute(text("CREATE UNIQUE INDEX ix_categories_project_id ON categories (project_id)"))
        except (OperationalError, ProgrammingError) as e:
            if not _already_exists(e):
                raise
    if added and engine.dialect.name == "mysql":
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE categories MODIFY COLUMN project_id VARCHAR(50) NOT NULL"))


def _revoked_tokens_record_no() -> None:
    """revoked_tokens was keyed by token_hash: give it record_no, keep token_hash unique."""
    if not _has_column("revoked_tokens", "token_hash") or _has_column("revoked_tokens", "record_no"):
        return
    if engine.dialect.name != "mysql":
        print("WARNING: revoked_tokens has no record_no. On SQLite, drop the table and restart to recreate it.")
        return
    with engine.begin() as conn:
        conn.execute(text(
            "ALTER TABLE revoked_tokens DROP PRIMARY KEY, "
            "ADD COLUMN record_no INT NOT NULL AUTO_INCREMENT PRIMARY KEY FIRST, "
            "ADD UNIQUE INDEX ix_revoked_tokens_token_hash (token_hash)"
        ))
    print("Database updated: revoked_tokens now has record_no")


def run_migrations():
    # 0. Renames on existing tables (must come before the steps below, which use the new names).
    record_no_and_audit_columns()

    # 1. New tables (categories, products, survey_questions, respondents,
    #    survey_responses, survey_answers, consolidated_reports).
    Base.metadata.create_all(bind=engine)

    # 2. Columns added to tables that existed before.
    category_project_id()
    # Customer address: Country -> State -> City (LOV lists inside each other)
    _add_column("respondents", "state", "VARCHAR(120) NULL", index=True)
    _add_column("respondents", "city", "VARCHAR(120) NULL", index=True)
    _add_column("m_othermastername", "parent_master_id", "INT NULL")
    _add_column("m_othermasterdata", "parent_id", "INT NULL", index=True)
    #    Older uploads keep NULL: they aren't linked to a survey.
    _add_column("feedback_records", "response_id", "INT NULL", index=True)
    _add_column("survey_answers", "value_en", "TEXT NULL")
    _add_column("users", "can_view_history", "INT NOT NULL DEFAULT 0")
    _add_column("survey_responses", "user_id", "INT NULL", index=True)
    _add_column("products", "recipe", "JSON NULL")
    _add_column("analysis_jobs", "worker_id", "VARCHAR(64) NULL")
    _add_column("analysis_jobs", "heartbeat_at", "DATETIME NULL")
    _add_column("analysis_jobs", "attempts", "INT NOT NULL DEFAULT 0")
    _add_column("users", "age", "INT NULL")
    _add_column("users", "gender", "VARCHAR(20) NULL")
    _add_column("analysis_jobs", "kind", "VARCHAR(20) NOT NULL DEFAULT 'analyze'")
    _add_column("analysis_jobs", "target_feedback_id", "INT NULL", index=True)
    _add_column("survey_questions", "report_role", "VARCHAR(30) NULL")
    # R&D phase 1: record ownership of customers, stored tone of each result.
    _add_column("respondents", "created_by", "INT NULL", index=True)
    _add_column("feedback_records", "tone", "VARCHAR(20) NULL", index=True)
    # Phase 2: sign every device out at once (password reset, account disabled, ...)
    _add_column("users", "token_version", "INT NOT NULL DEFAULT 0")
    # Organization portal: survey rounds, forced password change, Updates page.
    _add_column("survey_responses", "round_id", "INT NULL", index=True)
    _add_column("survey_responses", "recipe_version", "INT NULL", index=True)
    _add_column("consolidated_reports", "round_id", "INT NULL", index=True)
    _add_column("users", "must_change_password", "INT NOT NULL DEFAULT 0")
    _add_column("users", "updates_seen_at", "DATETIME NULL")
    # Assignments per survey round (the Admin assigns every round the Super Admin starts).
    had_round_column = _has_column("product_assignments", "round_id")
    _add_column("product_assignments", "round_id", "INT NULL", index=True)
    _assignment_unique_per_round()
    if not had_round_column:
        scope_assignments_to_rounds()
    # Products already surveyed before rounds were required stay open as before.
    had_needs_round = _has_column("products", "needs_round")
    _add_column("products", "needs_round", "INT NOT NULL DEFAULT 1")
    _add_column("products", "start_version", "INT NOT NULL DEFAULT 1")  # existing products: v1, as before
    # Recipe plans and action plans (services/action_plans.py); the two plan tables are new
    _add_column("products", "recipe_plan", "JSON NULL")
    _add_column("categories", "default_plan", "JSON NULL")
    _add_column("survey_responses", "action_plan_id", "INT NULL", index=True)
    if not had_needs_round:
        with engine.begin() as conn:
            done = conn.execute(text(
                "UPDATE products SET needs_round = 0 WHERE record_no IN (SELECT DISTINCT product_id FROM survey_responses)"
            )).rowcount
        if done:
            print(f"Database updated: {done} existing products stay open without a round")
    # Next update (requirements PDF): respondent details, Internal/External, report
    # review status, survey form approval. Existing data keeps working:
    for column, ddl in (("country", "VARCHAR(80) NULL"), ("occupation", "VARCHAR(80) NULL"),
                        ("sec_class", "VARCHAR(40) NULL"), ("respondent_type", "VARCHAR(60) NULL")):
        _add_column("respondents", column, ddl)
    _add_column("survey_responses", "audience", "VARCHAR(20) NULL")
    had_report_status = _has_column("survey_responses", "report_status")
    _add_column("survey_responses", "report_status", "VARCHAR(30) NULL")
    _add_column("survey_responses", "report_status_at", "DATETIME NULL")
    if not had_report_status:  # surveys analyzed before the review chain start as "generated"
        with engine.begin() as conn:
            conn.execute(text("UPDATE survey_responses SET report_status = 'generated' WHERE status = 'analyzed'"))
    had_form_status = _has_column("products", "questions_status")
    _add_column("products", "questions_status", "VARCHAR(20) NOT NULL DEFAULT 'approved'")
    _add_column("products", "questions_note", "TEXT NULL")
    _add_column("products", "questions_reviewed_by", "VARCHAR(150) NULL")
    _add_column("products", "questions_reviewed_at", "DATETIME NULL")
    if not had_form_status:  # forms already in use stay usable: approved
        with engine.begin() as conn:
            conn.execute(text("UPDATE products SET questions_status = 'approved'"))
    # The Super Admin's survey questions are final (no Admin approval step): forms left
    # "pending" or "returned" by the earlier approval flow are usable again.
    with engine.begin() as conn:
        n = conn.execute(text("UPDATE products SET questions_status = 'approved' "
                              "WHERE questions_status IN ('pending', 'returned')")).rowcount
    if n:
        print(f"Database updated: {n} survey form(s) no longer wait for approval")
    # Recipe standards gained an explicit scope. Rules created before it, with no
    # category, applied to every product: keep exactly that, once, for those rows only.
    from sqlalchemy import inspect as _inspect

    _std_cols = {c["name"] for c in _inspect(engine).get_columns("recipe_standards")} if _inspect(engine).has_table("recipe_standards") else set()
    _std_scope_new = bool(_std_cols) and "applies_to_all" not in _std_cols

    # Tables of these features may exist from an earlier version with fewer columns:
    # add what's missing (create_all never changes an existing table).
    sync_columns(["product_checks", "report_review_events", "master_values", "recipe_standards",
                  "recipe_attachments", "password_reset_codes", "custom_report_requests"])

    if _std_scope_new:
        with engine.begin() as conn:
            n = conn.execute(text("UPDATE recipe_standards SET applies_to_all = 1 "
                                  "WHERE product_id IS NULL AND category_id IS NULL")).rowcount
        print(f"Database updated: {n} existing recipe standard(s) kept as 'all products'")

    from app.services.masters import seed_defaults

    seed_defaults()

    from app.services.lov import seed_lov

    seed_lov()

    # Offline mode: the phone's submission id makes retried uploads idempotent.
    for table in ("respondents", "survey_responses"):
        _add_column(table, "client_uuid", "VARCHAR(36) NULL")
        _add_unique_index(table, "client_uuid")

    # Offline GPS: accuracy, capture time and platform of a survey's GPS fix.
    _add_column("survey_responses", "location_accuracy", "FLOAT NULL")
    _add_column("survey_rounds", "total_at_close", "INT NULL")
    _add_column("action_plan_members", "target", "INT NULL")
    _add_column("projects", "closed_at", "DATETIME NULL")
    _add_column("projects", "closed_by_name", "VARCHAR(150) NULL")
    _add_column("notifications", "dismissed_at", "DATETIME NULL", index=True)
    project_totals()
    # Recipe codes (RCP-0001 ...): one per saved recipe version
    _add_column("recipe_versions", "recipe_code", "VARCHAR(20) NULL")
    _add_unique_index("recipe_versions", "recipe_code")
    recipe_codes()
    _add_column("survey_responses", "location_captured_at", "DATETIME NULL")
    _add_column("survey_responses", "location_source", "VARCHAR(20) NULL")

    # 3. Data steps (each one does nothing once done).
    backfill_feedback_tone()
    link_unclaimed_surveys()
    create_first_rounds()
    backfill_survey_versions()
    project_versions()
    drop_orphan_product_checks()
    plans_before_the_survey()
    # Encrypted recordings (services/recording_crypto.py)
    _add_column("feedback_records", "media_encrypted", "INT NOT NULL DEFAULT 0")
    _add_column("feedback_records", "media_key_id", "INT NULL")
    _add_column("feedback_records", "media_wrapped_key", "TEXT NULL")
    for table in ("upload_sessions", "analysis_jobs"):
        _add_column(table, "enc_key_id", "INT NULL")
        _add_column(table, "enc_wrapped_key", "TEXT NULL")


def plans_before_the_survey() -> None:
    """Action plans can be made in a project before its survey starts (round_id NULL)."""
    _add_column("action_plans", "project_id", "INT NULL", index=True)
    col = next((c for c in inspect(engine).get_columns("action_plans") if c["name"] == "round_id"), None)
    if col is not None and not col.get("nullable", True):
        try:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE action_plans MODIFY round_id INT NULL"))
            print("Database updated: action plans can be planned before their survey starts")
        except (OperationalError, ProgrammingError):
            pass  # SQLite (tests) builds the new schema from the models


def drop_orphan_product_checks() -> None:
    """Product verification rows left by surveys or products deleted before their check was
    removed with them (api/product_feedback.delete_survey, catalog product delete)."""
    with engine.begin() as conn:
        n = conn.execute(text(
            "DELETE FROM product_checks WHERE response_id NOT IN (SELECT record_no FROM survey_responses) "
            "OR selected_product_id NOT IN (SELECT record_no FROM products)")).rowcount
    if n:
        print(f"Database updated: removed {n} product verification row(s) of deleted surveys or products")


def project_versions() -> None:
    """
    projects.product_version: one project per product VERSION instead of per product.
    Once, when the column is added:
      - each existing project gets the version its product had when the project was
        made (the newest recipe version saved by then; else the first one, else 1);
      - the unique rule moves from (product_id) to (product_id, product_version);
      - every product version without a project gets one (new projects; existing
        projects and their history are not changed).
    """
    added = not _has_column("projects", "product_version")
    _add_column("projects", "product_version", "INT NOT NULL DEFAULT 1")
    if not _has_index("projects", "uq_project_product_version"):
        with engine.begin() as conn:
            if added:
                rows = conn.execute(text("SELECT record_no, product_id, created_at FROM projects")).all()
                for rid, pid, made in rows:
                    v = conn.execute(text("SELECT MAX(version) FROM recipe_versions WHERE product_id = :p AND created_at <= :t"),
                                     {"p": pid, "t": made}).scalar()
                    if v is None:
                        v = conn.execute(text("SELECT MIN(version) FROM recipe_versions WHERE product_id = :p"), {"p": pid}).scalar()
                    if v is None:
                        v = conn.execute(text("SELECT start_version FROM products WHERE record_no = :p"), {"p": pid}).scalar()
                    conn.execute(text("UPDATE projects SET product_version = :v WHERE record_no = :r"),
                                 {"v": int(v or 1), "r": rid})
            try:
                conn.execute(text("CREATE UNIQUE INDEX uq_project_product_version ON projects (product_id, product_version)"))
            except (OperationalError, ProgrammingError) as e:
                if not _already_exists(e):
                    raise
        print("Database updated: projects are per product version (product_id, product_version)")
    # The old one-project-per-product rule (MySQL names it after the column). The new
    # index starts with product_id, so the foreign key keeps an index.
    for ix in inspect(engine).get_indexes("projects"):
        if ix.get("unique") and ix.get("column_names") == ["product_id"]:
            try:
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE projects DROP INDEX {_q(ix['name'])}"))
                print(f"Database updated: dropped the one-project-per-product index {ix['name']}")
            except (OperationalError, ProgrammingError):
                pass  # SQLite keeps table constraints (tests build the new schema anyway)
    if not added:
        return
    from sqlalchemy.orm import Session as OrmSession

    from app.database.models import Product, RecipeVersion, product_first_version, project_for_version

    with OrmSession(engine) as session:
        made = 0
        for product in session.query(Product).order_by(Product.id).all():
            versions = sorted({v for (v,) in session.query(RecipeVersion.version).filter(RecipeVersion.product_id == product.id)})
            for version in versions or [product_first_version(product)]:
                before = len(session.new)
                project_for_version(session, product, version)
                made += len(session.new) - before
            session.flush()
        session.commit()
    if made:
        print(f"Database updated: {made} project(s) created for existing product versions")


def _assignment_unique_per_round():
    """One row per (round, survey person) instead of per (survey person, product)."""
    if _has_index("product_assignments", "uq_assignment_user_product"):
        try:
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE product_assignments DROP INDEX uq_assignment_user_product"))
        except (OperationalError, ProgrammingError):
            pass  # SQLite keeps table constraints; MySQL (production) drops it
    if not _has_index("product_assignments", "uq_assignment_round_user"):
        try:
            with engine.begin() as conn:
                conn.execute(text("CREATE UNIQUE INDEX uq_assignment_round_user ON product_assignments (round_id, user_id)"))
        except (OperationalError, ProgrammingError) as e:
            if not _already_exists(e):
                raise


def scope_assignments_to_rounds() -> int:
    """
    Runs once, when product_assignments gets round_id. Before, a product with no
    assignment was open to every executive and assignments were per product.
    So that fieldwork in progress isn't interrupted:
      - every LIVE round gets the people who could survey it until now: the product's
        assigned executives, or every active executive if it was open to everyone;
      - older assignment rows are kept as history on the product's latest round.
    New rounds after this need an Admin's assignment.
    """
    from app.database.models import ProductAssignment, SurveyRound, User

    db = SessionLocal()
    try:
        legacy = db.query(ProductAssignment).filter(ProductAssignment.round_id.is_(None)).all()
        by_product: dict = {}
        for row in legacy:
            by_product.setdefault(row.product_id, set()).add(row.user_id)
        active_execs = {uid for (uid,) in db.query(User.id).filter(User.role == "executive", User.is_active == 1).all()}
        existing = {(rid, uid) for rid, uid in db.query(ProductAssignment.round_id, ProductAssignment.user_id)
                    .filter(ProductAssignment.round_id.isnot(None)).all()}
        added = 0
        for r in db.query(SurveyRound).filter(SurveyRound.status == "live").all():
            if any(rid == r.id for rid, _ in existing):
                continue  # this round's people were already chosen
            people = by_product.get(r.product_id) or active_execs
            for uid in people:
                if (r.id, uid) not in existing:
                    db.add(ProductAssignment(user_id=uid, product_id=r.product_id, round_id=r.id))
                    existing.add((r.id, uid))
                    added += 1
        for row in legacy:
            db.delete(row)  # replaced above (live rounds) or kept below as history
        db.flush()
        for product_id, users in by_product.items():
            latest = (db.query(SurveyRound).filter(SurveyRound.product_id == product_id)
                      .order_by(SurveyRound.number.desc()).first())
            if latest is None or latest.status == "live":
                continue  # never started (no history) or already handled above
            for uid in users:
                if (latest.id, uid) not in existing:
                    db.add(ProductAssignment(user_id=uid, product_id=product_id, round_id=latest.id))
                    existing.add((latest.id, uid))
        db.commit()
        if added:
            print(f"Database updated: {added} live-round assignments kept for survey persons")
        return added
    finally:
        db.close()


def backfill_survey_versions() -> int:
    """
    Surveys made before survey_responses.recipe_version existed get the version of
    their round (once: only empty values are filled; nothing else is changed).
    """
    with engine.begin() as conn:
        result = conn.execute(text(
            "UPDATE survey_responses SET recipe_version = ("
            " SELECT survey_rounds.recipe_version FROM survey_rounds"
            " WHERE survey_rounds.record_no = survey_responses.round_id)"
            " WHERE recipe_version IS NULL AND round_id IS NOT NULL"
            " AND EXISTS (SELECT 1 FROM survey_rounds WHERE survey_rounds.record_no = survey_responses.round_id"
            " AND survey_rounds.recipe_version IS NOT NULL)"
        ))
    filled = result.rowcount or 0
    if filled > 0:
        print(f"Database updated: product version filled in for {filled} existing surveys")
    return filled


def create_first_rounds() -> int:
    """
    Products that already have surveys get Round 1 holding them (once). See
    services/rounds.py. Returns how many rounds were created.
    """
    from app.services.rounds import create_first_rounds as create

    db = SessionLocal()
    try:
        return create(db)
    finally:
        db.close()


def backfill_feedback_tone(batch: int = 500) -> int:
    """Fill feedback_records.tone for results saved before the column existed."""
    from sqlalchemy.orm import Session

    from app.services.consolidation import response_tone

    done = 0
    with Session(engine) as db:
        while True:
            rows = (
                db.query(models.FeedbackRecord.id, models.FeedbackRecord.overall_sentiment,
                         models.FeedbackRecord.chapters, models.FeedbackRecord.overall_rating)
                .filter(models.FeedbackRecord.tone.is_(None))
                .limit(batch)
                .all()
            )
            if not rows:
                break
            for rid, sentiment, chapters, rating in rows:
                db.query(models.FeedbackRecord).filter(models.FeedbackRecord.id == rid).update(
                    {models.FeedbackRecord.tone: response_tone(sentiment, chapters, rating)},
                    synchronize_session=False,
                )
            db.commit()
            done += len(rows)
    if done:
        print(f"Database updated: stored the tone of {done} existing results")
    return done


def _normal_name(name) -> str:
    return " ".join(str(name or "").split()).lower()


def link_unclaimed_surveys() -> int:
    """
    Surveys from before accounts existed (user_id empty) are linked to an account
    ONLY when exactly one account has that executive's name. Ambiguous names
    (two people called the same) stay unlinked for an admin to sort out, so
    nobody ever sees another person's surveys because of a shared name.
    """
    from collections import defaultdict

    from sqlalchemy.orm import Session

    linked = 0
    with Session(engine) as db:
        unclaimed = (
            db.query(models.SurveyResponse.id, models.SurveyResponse.executive_name)
            .filter(models.SurveyResponse.user_id.is_(None), models.SurveyResponse.executive_name.isnot(None))
            .all()
        )
        if not unclaimed:
            return 0
        accounts = defaultdict(list)
        # Client logins (brands) never take surveys, so surveys are never linked to them.
        for uid, name in db.query(models.User.id, models.User.name).filter(models.User.role != "client").all():
            accounts[_normal_name(name)].append(uid)
        by_owner = defaultdict(list)
        for sid, name in unclaimed:
            owners = accounts.get(_normal_name(name), [])
            if len(owners) == 1:
                by_owner[owners[0]].append(sid)
        for uid, ids in by_owner.items():
            linked += (
                db.query(models.SurveyResponse)
                .filter(models.SurveyResponse.id.in_(ids), models.SurveyResponse.user_id.is_(None))
                .update({models.SurveyResponse.user_id: uid}, synchronize_session=False)
            )
        db.commit()
    if linked:
        print(f"Database updated: linked {linked} older surveys to the only account with that name")
    return linked


if __name__ == "__main__":
    run_migrations()
    print("Database is up to date.")


def sync_columns(table_names) -> None:
    """
    Bring existing tables up to their model: add every missing column (data kept), and
    make old extra columns that are NOT NULL without a default optional (MySQL), so new
    rows can be saved. Never drops anything.
    """
    from sqlalchemy import inspect

    from app.database.database import Base

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    for name in table_names:
        table = Base.metadata.tables.get(name)
        if table is None or name not in existing_tables:
            continue
        present = {c["name"]: c for c in inspector.get_columns(name)}
        # Database names (table.columns is keyed by attribute: "id" for record_no).
        model_names = {c.name for c in table.columns}
        for column in table.columns:
            if column.name in present:
                continue
            ddl = column.type.compile(dialect=engine.dialect)
            default = getattr(column.default, "arg", None)
            if not column.nullable and isinstance(default, (int, float, str)) and not callable(default):
                value = default if isinstance(default, (int, float)) else "'" + str(default).replace("'", "''") + "'"
                ddl += f" NOT NULL DEFAULT {value}"
            else:
                ddl += " NULL"
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {name} ADD COLUMN {column.name} {ddl}"))
            print(f"Database updated: added {name}.{column.name}")
        if engine.dialect.name == "mysql":
            _fix_types(name, table, present)
            for col_name, info in present.items():
                if col_name in model_names or info.get("nullable", True) or info.get("default") is not None:
                    continue
                if info.get("autoincrement") is True:
                    continue
                ddl = info["type"].compile(dialect=engine.dialect)
                with engine.begin() as conn:
                    conn.execute(text(f"ALTER TABLE {name} MODIFY COLUMN {col_name} {ddl} NULL"))
                print(f"Database updated: {name}.{col_name} is now optional (not used any more)")


def _python_type(sql_type):
    try:
        return sql_type.python_type
    except (NotImplementedError, AttributeError):
        return None


def _fix_types(name: str, table, present: dict) -> None:
    """
    A column that exists with an older type (MySQL). Number -> text is lossless, so it is
    converted (e.g. recipe_attachments.uploaded_by was a user id, now the uploader's name).
    Any other mismatch could lose data: it is only reported, never changed.
    """
    for column in table.columns:
        info = present.get(column.name)
        if info is None or column.primary_key:
            continue
        want, have = _python_type(column.type), _python_type(info["type"])
        if want is None or have is None or want is have:
            continue
        if want is str and have in (int, float):
            ddl = column.type.compile(dialect=engine.dialect) + " NULL"
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {name} MODIFY COLUMN {column.name} {ddl}"))
            print(f"Database updated: {name}.{column.name} changed from a number to text ({ddl}); values kept")
        else:
            print(f"WARNING: {name}.{column.name} is {info['type']} in the database but the code expects "
                  f"{column.type}. Not changed automatically (it could lose data); please check it.")

def project_totals() -> None:
    """The response total moved from the product to each project: an existing project starts with
    its product's total (once, when the column is added)."""
    if _has_column("projects", "total_target"):
        return
    _add_column("projects", "total_target", "INT NULL")
    from sqlalchemy.orm import Session as OrmSession

    from app.database.models import Product, Project

    with OrmSession(engine) as session:
        made = 0
        for project in session.query(Project).all():
            product = session.get(Product, project.product_id)
            value = ((product.recipe_plan or {}) if product else {}).get("target")
            try:
                value = int(value) if value else None
            except (TypeError, ValueError):
                value = None
            if value:
                project.total_target = value
                made += 1
        session.commit()
    if made:
        print(f"Database updated: {made} project(s) given their product's response total")


def recipe_codes() -> None:
    """Recipe versions saved before recipe codes existed get RCP-#### once, oldest first
    (after any code already given). Does nothing once every version has a code."""
    with engine.begin() as conn:
        missing = conn.execute(text("SELECT record_no FROM recipe_versions WHERE recipe_code IS NULL "
                                    "ORDER BY created_at, record_no")).scalars().all()
        if not missing:
            return
        highest = 0
        for (code,) in conn.execute(text("SELECT recipe_code FROM recipe_versions WHERE recipe_code LIKE 'RCP-%'")):
            digits = (code or "")[4:]
            if digits.isdigit():
                highest = max(highest, int(digits))
        for rid in missing:
            highest += 1
            conn.execute(text("UPDATE recipe_versions SET recipe_code = :c WHERE record_no = :r"), {"c": f"RCP-{highest:04d}", "r": rid})
    print(f"Database updated: {len(missing)} recipe version(s) got a recipe code (RCP-####)")


def category_codes_unique() -> None:
    """Every category has its own CAT-#### number. Codes in another form (CAT-5, CAT-005) become
    CAT-0005; when two categories share a number (e.g. a hand-typed CAT-005 next to CAT-0005), the
    older category keeps it and the newer one gets the lowest free number. Safe to repeat."""
    import re as _re

    with engine.begin() as conn:
        rows = conn.execute(text("SELECT record_no, name, project_id FROM categories WHERE project_id IS NOT NULL "
                                 "ORDER BY record_no")).all()
        number_of = {}
        for rid, _name, code in rows:
            m = _re.fullmatch(r"(?i)CAT-0*(\d+)", (code or "").strip())
            if m and int(m.group(1)) > 0:
                number_of[rid] = int(m.group(1))
        kept, duplicates = {}, []
        for rid, name, code in rows:
            n = number_of.get(rid)
            if n is None:
                continue
            if n in kept.values():
                duplicates.append((rid, name, code))
            else:
                kept[rid] = n
        used = set(kept.values())
        changes = []
        for rid, name, code in duplicates:  # first move the duplicates to free numbers
            n = 1
            while n in used:
                n += 1
            used.add(n)
            conn.execute(text("UPDATE categories SET project_id = :p WHERE record_no = :r"), {"p": f"CAT-{n:04d}", "r": rid})
            changes.append(f"{name}: {code} -> CAT-{n:04d} (its number was used by an older category)")
        for rid, name, code in rows:  # then write the others in the standard form
            if rid in kept and code != f"CAT-{kept[rid]:04d}":
                conn.execute(text("UPDATE categories SET project_id = :p WHERE record_no = :r"), {"p": f"CAT-{kept[rid]:04d}", "r": rid})
                changes.append(f"{name}: {code} -> CAT-{kept[rid]:04d}")
    if changes:
        print("Database updated: category codes made unique (CAT-####): " + "; ".join(changes))