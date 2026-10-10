from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.orm import declarative_base, sessionmaker


# ============================================================
# MYSQL DATABASE CONFIGURATION
# ============================================================

MYSQL_HOST = "127.0.0.1"
MYSQL_PORT = 3306
MYSQL_DATABASE = "fmcg_learning"
MYSQL_USERNAME = "root"

# Use the root password you created during MySQL installation.
# Do NOT send this password to anyone.
MYSQL_PASSWORD = "bhaveshmishra@3109"


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