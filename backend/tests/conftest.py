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
        "FMCG_STORAGE_ROOT": str(_TMP / "storage"),  # tests never write into the project folder
        "FMCG_RECORDINGS_DIR": str(_TMP / "recordings"),
        "FMCG_QUEUE_DIR": str(_TMP / "queue"),
        "FMCG_ATTACHMENTS_DIR": str(_TMP / "recipe_files"),
        "CORS_ORIGINS": "http://localhost:5173",
        "LOG_LEVEL": "WARNING",
        # Recording encryption: a test master key (never the developer's ~/.fmcg/master.key)
        "FMCG_MASTER_KEY": "Ts0JpWCUgrhmz9P7I3G1y3D8n6QyVnZdEGo5bOuq3Xk=",
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


@pytest.fixture(autouse=True)
def _no_leftover_recipe_standards():
    """Recipe standards apply across products (and block rounds): none may leak into the next test."""
    yield
    from app.database.database import SessionLocal
    from app.database.models import RecipeStandard

    db = SessionLocal()
    try:
        db.query(RecipeStandard).delete()
        db.commit()
    finally:
        db.close()



def playback_token(db, user, feedback_id: int) -> str:
    """A playback token as POST /recordings/{id}/authorise issues after the passkey check
    (for tests about streaming itself; the passkey rules are in test_recording_security.py)."""
    import hashlib
    import secrets
    from datetime import datetime, timedelta

    from app.database.models import PlaybackToken

    token = secrets.token_urlsafe(32)
    db.add(PlaybackToken(token_hash=hashlib.sha256(token.encode()).hexdigest(), user_id=user.id, feedback_id=feedback_id,
                         expires_at=datetime.utcnow() + timedelta(minutes=10)))
    db.commit()
    return token


def end_live_surveys(db):
    """Ends every collecting survey left by earlier tests (the test database is shared): a standard
    for ALL products can't change while any product is being surveyed."""
    from app.database.models import SurveyRound

    db.query(SurveyRound).filter(SurveyRound.status == "live").update({"status": "closed"}, synchronize_session=False)
    db.commit()


def staff_project(client, db, project_id):
    """What the Admin does before a project's survey can start: every planned survey plan gets a team
    whose response allocations add up to the plan's target (existing members are kept)."""
    admin = make_user(db, role="admin")
    plans = client.get(f"/admin/projects/{project_id}/plans", headers=auth(admin)).json().get("plans", [])
    for p in plans:
        members = [m["user_id"] for m in p["members"]] or [make_user(db).id]
        share, extra = divmod(p["target"], len(members))
        body = [{"user_id": uid, "target": share + (1 if i < extra else 0)} for i, uid in enumerate(members)]
        body = [b for b in body if b["target"] > 0]
        r = client.put(f"/admin/action-plans/{p['id']}/members", headers=auth(admin), json={"members": body})
        assert r.status_code == 200, r.text


def start_project(client, db, project_id, headers, json=None):
    """What the screens require before Start survey: a response target (set to the planned total when
    missing), the plans staffed (staff_project); then the Super Admin starts the survey."""
    state = client.get(f"/org/projects/{project_id}/plans", headers=headers).json()
    if state.get("total_target") is None and state.get("planned"):
        client.put(f"/org/projects/{project_id}/target", headers=headers,
                   json={"total_target": (state.get("used") or 0) + state["planned"]})
    staff_project(client, db, project_id)
    return client.post(f"/org/projects/{project_id}/start", headers=headers, json=json if json is not None else {})