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
    # R&D phase 1: record ownership of customers, stored tone of each result.
    _add_column("respondents", "created_by", "INT NULL", index=True)
    _add_column("feedback_records", "tone", "VARCHAR(20) NULL", index=True)
    # Phase 2: sign every device out at once (password reset, account disabled, ...)
    _add_column("users", "token_version", "INT NOT NULL DEFAULT 0")

    # 3. Data steps (each one does nothing once done).
    backfill_feedback_tone()
    link_unclaimed_surveys()


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
        for uid, name in db.query(models.User.id, models.User.name).all():
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
