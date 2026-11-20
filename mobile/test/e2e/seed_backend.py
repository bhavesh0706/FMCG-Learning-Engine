"""
Seeds a throw-away SQLite backend for the offline-mode end-to-end test
(mobile/test/e2e_offline_sync_test.dart): one survey person, one product with
questions, a live round assigned to that person. Prints the ids as JSON.

Environment (set by the test): DATABASE_URL, FMCG_STORAGE_ROOT, FMCG_SECRET_KEY, ...
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))

import app.main  # noqa: E402,F401  (creates the tables)
from app.api.auth import hash_password  # noqa: E402
from app.database.database import SessionLocal  # noqa: E402
from app.database.models import (  # noqa: E402
    Category,
    Product,
    ProductAssignment,
    SurveyQuestion,
    SurveyRound,
    User,
)

db = SessionLocal()
user = User(name="Field Surveyor", mobile="9800000001", password_hash=hash_password("Field#2026"),
            role="executive", is_active=1, can_view_history=0)
db.add(user)
category = Category(name="Breakfast cereals", is_active=1)
db.add(category)
db.commit()
product = Product(category_id=category.id, name="Corn Flakes Classic", is_active=1, min_responses=1)
db.add(product)
db.commit()
# A product is "survey ready" with 5 to 10 questions (api/catalog.py MIN_QUESTIONS).
questions = [("How would you rate the taste?", "rating"), ("Would you buy it again?", "yes_no"),
             ("What did you like?", "text"), ("What would you change?", "text"),
             ("How would you rate the crunch?", "rating")]
for i, (text, kind) in enumerate(questions, start=1):
    db.add(SurveyQuestion(product_id=product.id, position=i, text=text, question_type=kind, required=1, is_active=1))
round_ = SurveyRound(product_id=product.id, number=1, target=30, status="live",
                     started_by_name="Super Admin", started_by_role="client")
db.add(round_)
db.commit()
db.add(ProductAssignment(user_id=user.id, product_id=product.id, round_id=round_.id))
db.commit()
print(json.dumps({"user_id": user.id, "product_id": product.id}))
