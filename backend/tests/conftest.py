"""
Test setup: the real FastAPI app on a throw-away SQLite database.

Run from the backend folder:
    pip install -r requirements-dev.txt
    pytest

No MySQL, Ollama or AI service is needed: the AI service URL points at a closed
port, and the analysis workers are not started.
"""

import os
import sys
import tempfile
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

_TMP = Path(tempfile.mkdtemp(prefix="fmcg-tests-"))

# Must be set before the app (and its database module) is imported.
os.environ.update(
    {
        "APP_ENV": "development",
        "DATABASE_URL": f"sqlite:///{_TMP / 'test.db'}",
        "FMCG_WORKER_MODE": "off",
        "FMCG_SECRET_KEY": "test-secret-key-not-for-production",
        "FMCG_PBKDF2_ROUNDS": "1000",  # fast hashing in tests; production default is 600,000
        "AI_SERVICE_URL": "http://127.0.0.1:9",  # nothing listens here
        "AI_CONNECT_RETRIES": "0",
        "FMCG_RECORDINGS_DIR": str(_TMP / "recordings"),
        "FMCG_QUEUE_DIR": str(_TMP / "queue"),
        "CORS_ORIGINS": "http://localhost:5173",
        "LOG_LEVEL": "WARNING",
    }
)

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.api.auth import hash_password, make_token  # noqa: E402
from app.database.database import SessionLocal  # noqa: E402
from app.database.models import Category, Product, SurveyQuestion, User  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


_counter = {"n": 0}


def _unique(prefix: str) -> str:
    _counter["n"] += 1
    return f"{prefix}{_counter['n']}"


def make_user(db, *, role="executive", name=None, password="Passw0rd!", can_view_history=1) -> User:
    _counter["n"] += 1
    n = _counter["n"]
    user = User(
        name=name or f"Person {n}",
        mobile=f"9{n:09d}",
        password_hash=hash_password(password),
        role=role,
        is_active=1,
        can_view_history=can_view_history,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def auth(user: User) -> dict:
    return {"Authorization": f"Bearer {make_token(user)}"}


@pytest.fixture()
def admin(db):
    return make_user(db, role="admin", name=_unique("Admin "))


@pytest.fixture()
def alice(db):
    return make_user(db, name=_unique("Alice "))


@pytest.fixture()
def bob(db):
    return make_user(db, name=_unique("Bob "))


@pytest.fixture()
def product(db):
    category = Category(name=_unique("Category "), is_active=1)
    db.add(category)
    db.commit()
    item = Product(category_id=category.id, name=_unique("Product "), is_active=1, min_responses=1)
    db.add(item)
    db.commit()
    for position in range(1, 6):
        db.add(SurveyQuestion(product_id=item.id, position=position, text=f"Question {position}?",
                              question_type="text", required=1, is_active=1))
    db.commit()
    db.refresh(item)
    return item


def start_survey(client, user: User, product: Product) -> dict:
    """Respondent + survey created through the API, as the apps do."""
    r = client.post(
        "/surveys/respondents",
        headers=auth(user),
        json={"name": "Customer Test", "mobile": "9876543210", "location": "Andheri, Mumbai", "consent_given": True},
    )
    assert r.status_code == 201, r.text
    respondent_id = r.json()["id"]
    r = client.post(
        "/surveys/responses",
        headers=auth(user),
        json={"product_id": product.id, "respondent_id": respondent_id, "location": "D-Mart"},
    )
    assert r.status_code == 201, r.text
    return {"respondent_id": respondent_id, "response_id": r.json()["id"]}


def answers_payload(db, product: Product) -> dict:
    questions = db.query(SurveyQuestion).filter_by(product_id=product.id).order_by(SurveyQuestion.position).all()
    return {"answers": [{"question_id": q.id, "value": "Good"} for q in questions]}
