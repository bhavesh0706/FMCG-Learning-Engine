from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import declarative_base, sessionmaker

# Settings come from app/config.py: real environment variables first (docker-compose,
# PowerShell $env:...), then backend/.env. There is no password in the code: set
# MYSQL_PASSWORD in backend/.env (see backend/.env.example).
from app.config import MYSQL_DATABASE, MYSQL_HOST, MYSQL_PASSWORD, MYSQL_PORT, MYSQL_USER


# ============================================================
# MYSQL DATABASE CONFIGURATION
# ============================================================

MYSQL_USERNAME = MYSQL_USER  # name kept for any code that imports it


# ============================================================
# DATABASE URL
# ============================================================

DATABASE_URL = URL.create(
    drivername="mysql+pymysql",
    username=MYSQL_USERNAME,
    password=MYSQL_PASSWORD,
    host=MYSQL_HOST,
    port=MYSQL_PORT,
    database=MYSQL_DATABASE,
)


# ============================================================
# SQLALCHEMY ENGINE
# ============================================================

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
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