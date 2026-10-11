"""
Product catalog API: Category -> Product -> Survey questions.

Nothing is hard-deleted: "delete" deactivates, so feedback already collected
for a product or question keeps its meaning. Pass include_inactive=true to see
deactivated items, and PUT is_active=true to restore them.
"""

from datetime import date
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.auth import optional_user
from app.database.database import get_db
from app.services.product_access import hidden_product_ids
from app.database.models import Category, Product, SurveyQuestion, SurveyResponse

router = APIRouter(tags=["Catalog"])

MIN_QUESTIONS = 5   # a product is "survey ready" with at least this many questions
MAX_QUESTIONS = 10  # the executive asks at most this many

QuestionType = Literal["rating", "yes_no", "single_choice", "text"]


# ============================================================
# SCHEMAS
# ============================================================

def _clean(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = " ".join(str(value).split())
    return value or None


class CategoryIn(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    description: Optional[str] = Field(default=None, max_length=2000)

    _clean_name = field_validator("name", "description", mode="before")(_clean)


class CategoryUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=150)
    description: Optional[str] = Field(default=None, max_length=2000)
    is_active: Optional[bool] = None

    _clean_name = field_validator("name", "description", mode="before")(_clean)


class ProductIn(BaseModel):
    category_id: int
    name: str = Field(min_length=2, max_length=200)
    sku: Optional[str] = Field(default=None, max_length=80)
    description: Optional[str] = Field(default=None, max_length=2000)
    launch_date: Optional[date] = None
    min_responses: int = Field(default=10, ge=1, le=1000)
    # Optional starting survey questions (copied; the product owns them afterwards):
    question_template_id: Optional[int] = None          # from a question template
    copy_questions_from_product_id: Optional[int] = None  # from another product

    _clean_text = field_validator("name", "sku", "description", mode="before")(_clean)

    @model_validator(mode="after")
    def one_question_source(self):
        if self.question_template_id and self.copy_questions_from_product_id:
            raise ValueError("Choose either a question template or a product to copy questions from, not both.")
        return self


# Fields of ProductIn that are not Product columns.
STARTING_QUESTION_FIELDS = {"question_template_id", "copy_questions_from_product_id"}


class ProductUpdate(BaseModel):
    category_id: Optional[int] = None
    name: Optional[str] = Field(default=None, min_length=2, max_length=200)
    sku: Optional[str] = Field(default=None, max_length=80)
    description: Optional[str] = Field(default=None, max_length=2000)
    launch_date: Optional[date] = None
    min_responses: Optional[int] = Field(default=None, ge=1, le=1000)
    is_active: Optional[bool] = None

    _clean_text = field_validator("name", "sku", "description", mode="before")(_clean)


# What a question means for the client summary, and which answer types fit each.
REPORT_ROLES = {
    "preference": ("single_choice", "yes_no"),       # this product vs the alternative; first option = this product
    "price": ("single_choice",),                     # price points, e.g. 3 ghc / 4 ghc / 5 ghc
    "score": ("rating", "single_choice"),            # 1-5 stars, or options like "7 marks"..."10 marks"
    "improvement": ("single_choice", "text"),        # what to change
    "purchase_intent": ("yes_no", "single_choice"),  # would buy / buy again
    "ease": ("rating", "yes_no", "single_choice"),   # ease of use / preparation
}
ReportRole = Literal["preference", "price", "score", "improvement", "purchase_intent", "ease"]


class QuestionIn(BaseModel):
    text: str = Field(min_length=5, max_length=500)
    question_type: QuestionType = "text"
    options: Optional[List[str]] = None
    required: bool = True
    report_role: Optional[ReportRole] = None

    _clean_text = field_validator("text", mode="before")(_clean)

    @model_validator(mode="after")
    def check_options(self):
        if self.question_type == "single_choice":
            opts = [o.strip() for o in (self.options or []) if o and o.strip()]
            if len(opts) < 2 or len(opts) > 8:
                raise ValueError("A single-choice question needs 2 to 8 options.")
            if len({o.lower() for o in opts}) != len(opts):
                raise ValueError("Options must be different from each other.")
            self.options = opts
        else:
            self.options = None  # rating is always 1-5; yes/no and text have no options
        if self.report_role and self.question_type not in REPORT_ROLES[self.report_role]:
            kinds = " or ".join(t.replace("single_choice", "choice").replace("yes_no", "yes/no") for t in REPORT_ROLES[self.report_role])
            raise ValueError(f"'{self.report_role.replace('_', ' ')}' needs a {kinds} question.")
        return self


class QuestionUpdate(QuestionIn):
    text: Optional[str] = Field(default=None, min_length=5, max_length=500)
    question_type: Optional[QuestionType] = None
    required: Optional[bool] = None
    is_active: Optional[bool] = None

    @model_validator(mode="after")
    def check_options(self):
        # Options are validated against the final type in the endpoint.
        return self


class QuestionOrder(BaseModel):
    question_ids: List[int] = Field(min_length=1)


# ============================================================
# HELPERS
# ============================================================

def _category_out(c: Category, product_count: int = 0) -> dict:
    return {
        "id": c.id,
        "name": c.name,
        "description": c.description,
        "is_active": bool(c.is_active),
        "product_count": product_count,
        "created_at": c.created_at,
        "updated_at": c.updated_at,
    }


def _question_out(q: SurveyQuestion) -> dict:
    return {
        "id": q.id,
        "product_id": q.product_id,
        "position": q.position,
        "text": q.text,
        "question_type": q.question_type,
        "options": q.options,
        "required": bool(q.required),
        "is_active": bool(q.is_active),
        "report_role": getattr(q, "report_role", None),
    }


def _active_questions(db: Session, product_id: int) -> List[SurveyQuestion]:
    return (
        db.query(SurveyQuestion)
        .filter(SurveyQuestion.product_id == product_id, SurveyQuestion.is_active == 1)
        .order_by(SurveyQuestion.position, SurveyQuestion.id)
        .all()
    )


def _responses_collected(db: Session, product_id: int) -> int:
    # Only fully analyzed surveys count toward consolidation.
    return (
        db.query(func.count(SurveyResponse.id))
        .filter(SurveyResponse.product_id == product_id, SurveyResponse.status == "analyzed")
        .scalar()
        or 0
    )


def _batch_counts(db: Session, product_ids: List[int]) -> tuple:
    """
    Question and response counts for many products in two grouped queries,
    instead of two queries per product (1,000 products = 2 queries, not 2,000).
    """
    if not product_ids:
        return {}, {}
    question_counts = dict(
        db.query(SurveyQuestion.product_id, func.count(SurveyQuestion.id))
        .filter(SurveyQuestion.product_id.in_(product_ids), SurveyQuestion.is_active == 1)
        .group_by(SurveyQuestion.product_id)
        .all()
    )
    response_counts = dict(
        db.query(SurveyResponse.product_id, func.count(SurveyResponse.id))
        .filter(SurveyResponse.product_id.in_(product_ids), SurveyResponse.status == "analyzed")
        .group_by(SurveyResponse.product_id)
        .all()
    )
    return question_counts, response_counts


def _product_out(
    db: Session,
    p: Product,
    category: Optional[Category] = None,
    with_questions: bool = False,
    counts: Optional[tuple] = None,
) -> dict:
    category = category or db.get(Category, p.category_id)
    if counts is not None and not with_questions:
        question_counts, response_counts = counts
        questions = None
        question_count = question_counts.get(p.id, 0)
        collected = response_counts.get(p.id, 0)
    else:
        questions = _active_questions(db, p.id)
        question_count = len(questions)
        collected = _responses_collected(db, p.id)
    out = {
        "id": p.id,
        "category_id": p.category_id,
        "category_name": category.name if category else None,
        "name": p.name,
        "sku": p.sku,
        "description": p.description,
        "launch_date": p.launch_date,
        "min_responses": p.min_responses,
        "is_active": bool(p.is_active),
        "question_count": question_count,
        # Ready to use in the field once it has 5-10 questions.
        "survey_ready": MIN_QUESTIONS <= question_count <= MAX_QUESTIONS,
        # Recipe status for the Catalog page (the recipe itself: GET /products/{id}/recipe).
        "has_recipe": bool(getattr(p, "recipe", None)),
        "recipe_steps": len((getattr(p, "recipe", None) or {}).get("steps") or []),
        "responses_collected": collected,
        "survey_count": _all_surveys(db, p.id) if not p.is_active else None,  # only needed for inactive products
        # Drives the "Generate Consolidated Feedback" button.
        "can_consolidate": collected >= p.min_responses,
        "created_at": p.created_at,
        "updated_at": p.updated_at,
    }
    if with_questions:
        out["questions"] = [_question_out(q) for q in questions]
    return out


def _get_or_404(db: Session, model, obj_id: int, label: str):
    obj = db.get(model, obj_id)
    if not obj:
        raise HTTPException(status_code=404, detail=f"{label} not found.")
    return obj


def _commit(db: Session, conflict_message: str):
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=conflict_message)


def _renumber(db: Session, product_id: int):
    for i, q in enumerate(_active_questions(db, product_id), start=1):
        q.position = i


# ============================================================
# CATEGORIES
# ============================================================

@router.get("/categories")
def list_categories(include_inactive: bool = False, user=Depends(optional_user), db: Session = Depends(get_db)):
    query = db.query(Category)
    if not include_inactive:
        query = query.filter(Category.is_active == 1)
    hidden = hidden_product_ids(db, user)  # products assigned to other executives
    counted = db.query(Product.category_id, func.count(Product.id)).filter(Product.is_active == 1)
    if hidden:
        counted = counted.filter(Product.id.notin_(hidden))
    counts = dict(counted.group_by(Product.category_id).all())
    categories = query.order_by(Category.name).all()
    if hidden and not include_inactive:
        # An executive only sees categories that still have products for them.
        categories = [c for c in categories if counts.get(c.id, 0) > 0]
    return {"categories": [_category_out(c, counts.get(c.id, 0)) for c in categories]}


@router.post("/categories", status_code=201)
def create_category(body: CategoryIn, db: Session = Depends(get_db)):
    category = Category(name=body.name, description=body.description)
    db.add(category)
    _commit(db, f'A category named "{body.name}" already exists.')
    db.refresh(category)
    return _category_out(category)


@router.get("/categories/{category_id}")
def get_category(category_id: int, db: Session = Depends(get_db)):
    category = _get_or_404(db, Category, category_id, "Category")
    products = (
        db.query(Product)
        .filter(Product.category_id == category_id, Product.is_active == 1)
        .order_by(Product.name)
        .all()
    )
    out = _category_out(category, len(products))
    counts = _batch_counts(db, [p.id for p in products])
    out["products"] = [_product_out(db, p, category, counts=counts) for p in products]
    return out


@router.put("/categories/{category_id}")
def update_category(category_id: int, body: CategoryUpdate, db: Session = Depends(get_db)):
    category = _get_or_404(db, Category, category_id, "Category")
    data = body.model_dump(exclude_unset=True)
    if "name" in data and data["name"]:
        category.name = data["name"]
    if "description" in data:
        category.description = data["description"]
    if "is_active" in data and data["is_active"] is not None:
        category.is_active = 1 if data["is_active"] else 0
    _commit(db, f'A category named "{data.get("name")}" already exists.')
    db.refresh(category)
    return _category_out(category)


@router.delete("/categories/{category_id}")
def deactivate_category(category_id: int, db: Session = Depends(get_db)):
    category = _get_or_404(db, Category, category_id, "Category")
    category.is_active = 0
    db.commit()
    return {"id": category_id, "is_active": False, "message": "Category deactivated. Collected feedback is kept."}


@router.delete("/categories/{category_id}/permanent")
def delete_category_permanently(category_id: int, db: Session = Depends(get_db)):
    """
    Permanently delete a category created by mistake. It must be deactivated and have
    no products left (move them, or permanently delete products created by mistake).
    Question templates suggested for it are kept and become "any category".
    """
    from app.database.models import QuestionTemplate

    category = _get_or_404(db, Category, category_id, "Category")
    if category.is_active:
        raise HTTPException(status_code=400, detail="Deactivate the category first, then delete it permanently.")
    products = db.query(func.count(Product.id)).filter(Product.category_id == category.id).scalar() or 0
    if products:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{category.name} still has {products} {'product' if products == 1 else 'products'} (including inactive ones). "
                "Move them to another category, or delete products created by mistake, first."
            ),
        )
    name = category.name
    try:
        db.query(QuestionTemplate).filter(QuestionTemplate.category_id == category.id).update(
            {QuestionTemplate.category_id: None}, synchronize_session=False
        )
        db.delete(category)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"{name} is still referenced by other records, so it was not deleted.")
    return {"id": category_id, "deleted": True, "message": f"{name} was permanently deleted."}


# ============================================================
# PRODUCTS
# ============================================================

@router.get("/products")
def list_products(
    category_id: Optional[int] = None,
    q: Optional[str] = Query(default=None, max_length=100),
    include_inactive: bool = False,
    user=Depends(optional_user),
    db: Session = Depends(get_db),
):
    query = db.query(Product, Category).join(Category, Category.id == Product.category_id)
    hidden = hidden_product_ids(db, user)  # products assigned to other executives
    if hidden:
        query = query.filter(Product.id.notin_(hidden))
    if category_id:
        query = query.filter(Product.category_id == category_id)
    if not include_inactive:
        query = query.filter(Product.is_active == 1, Category.is_active == 1)
    if q:
        query = query.filter(Product.name.ilike(f"%{q.strip()}%"))
    rows = query.order_by(Category.name, Product.name).all()
    counts = _batch_counts(db, [p.id for p, _ in rows])
    return {"products": [_product_out(db, p, c, counts=counts) for p, c in rows]}


def question_dicts(questions) -> List[dict]:
    """
    Validate a list of questions (from a template, a product or a request) with the
    same rules as adding a question one by one, and return them as plain dicts.
    """
    out = []
    for i, q in enumerate(questions or [], start=1):
        data = q if isinstance(q, dict) else {
            "text": q.text, "question_type": q.question_type, "options": q.options,
            "required": bool(q.required), "report_role": getattr(q, "report_role", None),
        }
        try:
            checked = QuestionIn(**{k: data.get(k) for k in ("text", "question_type", "options", "required", "report_role") if data.get(k) is not None})
        except ValueError as e:
            raise HTTPException(status_code=422, detail=f"Question {i}: {e}")
        out.append(checked.model_dump())
    if len(out) > MAX_QUESTIONS:
        raise HTTPException(status_code=422, detail=f"At most {MAX_QUESTIONS} questions are allowed; this has {len(out)}.")
    return out


def _starting_questions(db: Session, body: ProductIn) -> List[dict]:
    if body.question_template_id:
        from app.database.models import QuestionTemplate

        template = db.get(QuestionTemplate, body.question_template_id)
        if not template or not template.is_active:
            raise HTTPException(status_code=404, detail="That question template no longer exists.")
        return question_dicts(template.questions)
    if body.copy_questions_from_product_id:
        source = _get_or_404(db, Product, body.copy_questions_from_product_id, "Product to copy questions from")
        return question_dicts(_active_questions(db, source.id))
    return []


@router.post("/products", status_code=201)
def create_product(body: ProductIn, db: Session = Depends(get_db)):
    category = _get_or_404(db, Category, body.category_id, "Category")
    if not category.is_active:
        raise HTTPException(status_code=400, detail="This category is deactivated. Restore it first.")
    starting = _starting_questions(db, body)  # validated before anything is written

    # One transaction: the product and its starting questions are saved together, or not at all.
    product = Product(**body.model_dump(exclude=STARTING_QUESTION_FIELDS))
    db.add(product)
    conflict = f'"{body.name}" already exists in {category.name}, or the SKU is already used.'
    try:
        db.flush()  # assigns product.id; duplicates are detected here
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=conflict)
    for position, q in enumerate(starting, start=1):
        db.add(SurveyQuestion(product_id=product.id, position=position, **q))
    _commit(db, conflict)
    db.refresh(product)
    return _product_out(db, product, category, with_questions=True)


def _check_assigned(db: Session, user, product_id: int):
    if product_id in hidden_product_ids(db, user):
        raise HTTPException(status_code=403, detail="This product isn't assigned to you. Ask your admin.")


@router.get("/products/{product_id}")
def get_product(product_id: int, user=Depends(optional_user), db: Session = Depends(get_db)):
    _check_assigned(db, user, product_id)
    product = _get_or_404(db, Product, product_id, "Product")
    return _product_out(db, product, with_questions=True)


@router.put("/products/{product_id}")
def update_product(product_id: int, body: ProductUpdate, db: Session = Depends(get_db)):
    product = _get_or_404(db, Product, product_id, "Product")
    data = body.model_dump(exclude_unset=True)
    if data.get("category_id"):
        _get_or_404(db, Category, data["category_id"], "Category")
    for field in ("category_id", "name", "sku", "description", "launch_date", "min_responses"):
        if field in data and (data[field] is not None or field in ("sku", "description", "launch_date")):
            setattr(product, field, data[field])
    if data.get("is_active") is not None:
        product.is_active = 1 if data["is_active"] else 0
    _commit(db, "Another product in this category has that name, or the SKU is already used.")
    db.refresh(product)
    return _product_out(db, product, with_questions=True)


@router.delete("/products/{product_id}")
def deactivate_product(product_id: int, db: Session = Depends(get_db)):
    product = _get_or_404(db, Product, product_id, "Product")
    product.is_active = 0
    db.commit()
    return {"id": product_id, "is_active": False, "message": "Product deactivated. Collected feedback is kept."}


def _all_surveys(db: Session, product_id: int) -> int:
    """Every survey of a product, in any status (drafts and unanalyzed ones too)."""
    return db.query(func.count(SurveyResponse.id)).filter(SurveyResponse.product_id == product_id).scalar() or 0


@router.delete("/products/{product_id}/permanent")
def delete_product_permanently(product_id: int, db: Session = Depends(get_db)):
    """
    Permanently delete a product created by mistake, with its questions, recipe,
    executive assignments and any (empty) report, in one transaction.

    Safety rules:
      - it must be deactivated first (two deliberate steps);
      - it must have NO surveys: collected answers, recordings and reports are never
        destroyed. A product with surveys stays deactivated instead.
    """
    from app.database.models import ConsolidatedReport, ProductAssignment

    product = _get_or_404(db, Product, product_id, "Product")
    if product.is_active:
        raise HTTPException(status_code=400, detail="Deactivate the product first, then delete it permanently.")
    surveys = _all_surveys(db, product.id)
    if surveys:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{product.name} has {surveys} {'survey' if surveys == 1 else 'surveys'}, so it can't be deleted: "
                "that would remove customers' feedback. It stays deactivated (hidden from new surveys) instead."
            ),
        )
    name = product.name
    try:
        questions = db.query(SurveyQuestion).filter(SurveyQuestion.product_id == product.id).delete(synchronize_session=False)
        db.query(ProductAssignment).filter(ProductAssignment.product_id == product.id).delete(synchronize_session=False)
        db.query(ConsolidatedReport).filter(ConsolidatedReport.product_id == product.id).delete(synchronize_session=False)
        db.delete(product)  # the recipe is stored on the product row
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=f"{name} is still referenced by other records, so it was not deleted.")
    return {"id": product_id, "deleted": True, "questions_deleted": questions, "message": f"{name} was permanently deleted."}


# ============================================================
# SURVEY QUESTIONS
# ============================================================

@router.get("/products/{product_id}/questions")
def list_questions(product_id: int, user=Depends(optional_user), db: Session = Depends(get_db)):
    _check_assigned(db, user, product_id)
    _get_or_404(db, Product, product_id, "Product")
    return {"questions": [_question_out(q) for q in _active_questions(db, product_id)]}


@router.post("/products/{product_id}/questions", status_code=201)
def add_question(product_id: int, body: QuestionIn, db: Session = Depends(get_db)):
    _get_or_404(db, Product, product_id, "Product")
    existing = _active_questions(db, product_id)
    if len(existing) >= MAX_QUESTIONS:
        raise HTTPException(status_code=400, detail=f"A product can have at most {MAX_QUESTIONS} questions.")
    question = SurveyQuestion(
        product_id=product_id,
        position=len(existing) + 1,
        text=body.text,
        question_type=body.question_type,
        options=body.options,
        required=1 if body.required else 0,
        report_role=body.report_role,
    )
    db.add(question)
    db.commit()
    db.refresh(question)
    return _question_out(question)


@router.put("/questions/{question_id}")
def update_question(question_id: int, body: QuestionUpdate, db: Session = Depends(get_db)):
    question = _get_or_404(db, SurveyQuestion, question_id, "Question")
    data = body.model_dump(exclude_unset=True)

    if data.get("is_active") is True and not question.is_active:
        if len(_active_questions(db, question.product_id)) >= MAX_QUESTIONS:
            raise HTTPException(status_code=400, detail=f"A product can have at most {MAX_QUESTIONS} questions.")

    # Validate type + options together, using the final values.
    try:
        final = QuestionIn(
            text=data.get("text") or question.text,
            question_type=data.get("question_type") or question.question_type,
            options=data["options"] if "options" in data else question.options,
            required=data["required"] if data.get("required") is not None else bool(question.required),
            report_role=data["report_role"] if "report_role" in data else getattr(question, "report_role", None),
        )
    except ValidationError as e:
        message = e.errors()[0].get("msg", "Invalid question.").replace("Value error, ", "")
        raise HTTPException(status_code=422, detail=message)
    question.text = final.text
    question.question_type = final.question_type
    question.options = final.options
    question.required = 1 if final.required else 0
    question.report_role = final.report_role
    if data.get("is_active") is not None:
        question.is_active = 1 if data["is_active"] else 0
    db.flush()
    _renumber(db, question.product_id)
    db.commit()
    db.refresh(question)
    return _question_out(question)


@router.delete("/questions/{question_id}")
def deactivate_question(question_id: int, db: Session = Depends(get_db)):
    question = _get_or_404(db, SurveyQuestion, question_id, "Question")
    question.is_active = 0
    db.flush()
    _renumber(db, question.product_id)
    db.commit()
    return {"id": question_id, "is_active": False, "message": "Question removed. Answers already collected are kept."}


@router.put("/products/{product_id}/questions/order")
def reorder_questions(product_id: int, body: QuestionOrder, db: Session = Depends(get_db)):
    _get_or_404(db, Product, product_id, "Product")
    questions = {q.id: q for q in _active_questions(db, product_id)}
    if sorted(body.question_ids) != sorted(questions):
        raise HTTPException(status_code=400, detail="Send every active question of this product exactly once.")
    for position, qid in enumerate(body.question_ids, start=1):
        questions[qid].position = position
    db.commit()
    return {"questions": [_question_out(q) for q in _active_questions(db, product_id)]}