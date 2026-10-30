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
        # config.py insists on this even though tests use SQLite; CI has no backend/.env.
        "MYSQL_PASSWORD": "test-password-unused-with-sqlite",
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
from app.database.models import Category, Product, ProductAssignment, SurveyQuestion, SurveyRound, User  # noqa: E402


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


def open_round(db, product: Product, number: int = 1, recipe_version=None, target: int = 30, status: str = "live") -> SurveyRound:
    """The Super Admin starts a round (round workflow: services/product_access.py)."""
    r = SurveyRound(product_id=product.id, number=number, recipe_version=recipe_version, target=target, status=status,
                    started_by_name="Super Admin", started_by_role="client")
    db.add(r)
    db.commit()
    db.refresh(r)
    return r


def assign(db, product: Product, *users: User) -> None:
    """An Admin assigns the product's live round to these survey persons."""
    r = (db.query(SurveyRound).filter(SurveyRound.product_id == product.id, SurveyRound.status == "live")
         .order_by(SurveyRound.number.desc()).first())
    assert r is not None, "start a round first (open_round)"
    for u in users:
        if not db.query(ProductAssignment).filter_by(round_id=r.id, user_id=u.id).first():
            db.add(ProductAssignment(user_id=u.id, product_id=product.id, round_id=r.id))
    db.commit()


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
    return item  # no round yet: start_survey opens one, round tests start their own


def start_survey(client, user: User, product: Product) -> dict:
    """Respondent + survey created through the API, as the apps do (the survey
    person is assigned to the live round first, as an Admin would)."""
    if user.role == "executive":
        from app.database.database import SessionLocal

        s = SessionLocal()
        try:
            if not s.query(SurveyRound).filter(SurveyRound.product_id == product.id, SurveyRound.status == "live").first():
                numbers = [n for (n,) in s.query(SurveyRound.number).filter(SurveyRound.product_id == product.id).all()]
                open_round(s, product, number=max(numbers, default=0) + 1)  # the Super Admin starts a round
            assign(s, product, user)
        finally:
            s.close()
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