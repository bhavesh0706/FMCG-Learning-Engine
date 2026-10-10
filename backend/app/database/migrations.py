"""
Database setup that is safe to run on every startup.

create_all() creates NEW tables, but never changes tables that already exist.
So columns added to an existing table are added here, only if missing.
Each step can run again without harm.
"""

from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError, ProgrammingError

from app.database import models  # noqa: F401  (registers every table on Base)
from app.database.database import Base, engine


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


def run_migrations():
    # 1. New tables (categories, products, survey_questions, respondents,
    #    survey_responses, survey_answers, consolidated_reports).
    Base.metadata.create_all(bind=engine)

    # 2. Columns added to tables that existed before.
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


if __name__ == "__main__":
    run_migrations()
    print("Database is up to date.")