import os

from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import declarative_base, sessionmaker

# Loads backend/.env (if present) before the settings below are read.
from app import config  # noqa: F401


# ============================================================
# DATABASE CONFIGURATION
# ============================================================
# Settings come from the environment or backend/.env (never from this file):
#
#   DATABASE_URL     optional, a full SQLAlchemy URL. Used by the tests
#                    (sqlite:///...) and by hosting platforms that give one URL.
#   MYSQL_HOST       default 127.0.0.1   (mysql inside Docker Compose)
#   MYSQL_PORT       default 3306
#   MYSQL_DATABASE   default fmcg_learning
#   MYSQL_USER       default root (use a dedicated app user in production)
#   MYSQL_PASSWORD   required: there is no default password in the code.
#
#   DB_POOL_SIZE / DB_MAX_OVERFLOW / DB_POOL_RECYCLE_SECONDS  connection pool


class DatabaseConfigError(RuntimeError):
    """The database settings are missing or invalid."""


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise DatabaseConfigError(f"{name} must be a whole number, got {raw!r}.") from None


def _database_url():
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        return make_url(url)
    if "MYSQL_PASSWORD" not in os.environ:
        raise DatabaseConfigError(
            "MYSQL_PASSWORD is not set. Add a line MYSQL_PASSWORD=<your MySQL password> "
            "to backend/.env (or set it in the environment), then start the backend again."
        )
    return URL.create(
        drivername="mysql+pymysql",
        username=os.environ.get("MYSQL_USER", "root").strip() or "root",
        password=os.environ["MYSQL_PASSWORD"],
        host=os.environ.get("MYSQL_HOST", "127.0.0.1").strip() or "127.0.0.1",
        port=_int_env("MYSQL_PORT", 3306),
        database=os.environ.get("MYSQL_DATABASE", "fmcg_learning").strip() or "fmcg_learning",
        query={"charset": "utf8mb4"},
    )


DATABASE_URL = _database_url()
IS_SQLITE = DATABASE_URL.get_backend_name() == "sqlite"


# ============================================================
# SQLALCHEMY ENGINE
# ============================================================

if IS_SQLITE:
    # Tests and local experiments only.
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,  # drop connections MySQL closed while idle
        pool_size=_int_env("DB_POOL_SIZE", 10),
        max_overflow=_int_env("DB_MAX_OVERFLOW", 20),
        pool_recycle=_int_env("DB_POOL_RECYCLE_SECONDS", 1800),  # below MySQL wait_timeout
    )


# ============================================================
# SESSION
# ============================================================

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


# ============================================================
# BASE MODEL
# ============================================================

Base = declarative_base()


# ============================================================
# DATABASE SESSION HELPER
# ============================================================

def get_db():
    """
    Create a database session.

    The session is automatically closed after
    the request finishes.
    """

    db = SessionLocal()

    try:
        yield db

    finally:
        db.close()


# ============================================================
# INITIALIZE DATABASE
# ============================================================

def init_db():
    """
    Create all database tables defined by SQLAlchemy models.
    """

    Base.metadata.create_all(
        bind=engine
    )