"""
Question templates: reusable starting survey questions for new products.

Stored in MySQL (question_templates) and managed from the Catalog, so adding a
product never requires editing Python files. Creating a product can copy a
template's questions (POST /products with question_template_id); the product
then owns its own questions, so later template edits don't change it.

GET    /question-templates                  active templates (?category_id= puts that category's first)
GET    /question-templates/{id}
POST   /question-templates                  {name, description?, category_id?, questions[] | from_product_id}
PUT    /question-templates/{id}             {name?, description?, category_id?, questions?}
DELETE /question-templates/{id}             deactivate (kept for history, hidden from lists)

All of these are admin-only (see access.py), like other catalog writes.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.catalog import QuestionIn, _active_questions, _clean, question_dicts
from app.database.database import get_db
from app.database.models import Category, Product, QuestionTemplate
from app.services import audit

router = APIRouter(prefix="/question-templates", tags=["Question templates"])


class TemplateIn(BaseModel):
    name: str = Field(min_length=2, max_length=150)
    description: Optional[str] = Field(default=None, max_length=2000)
    category_id: Optional[int] = None
    questions: Optional[List[QuestionIn]] = None
    from_product_id: Optional[int] = None  # use this product's current questions

    _clean_text = field_validator("name", "description", mode="before")(_clean)

    @model_validator(mode="after")
    def one_source(self):
        if bool(self.questions) == bool(self.from_product_id):
            raise ValueError("Give either the questions or a product to take them from.")
        return self


class TemplateUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=150)
    description: Optional[str] = Field(default=None, max_length=2000)
    category_id: Optional[int] = None
    questions: Optional[List[QuestionIn]] = None

    _clean_text = field_validator("name", "description", mode="before")(_clean)


def _out(t: QuestionTemplate, category_names: dict) -> dict:
    questions = t.questions or []
    return {
        "id": t.id,
        "name": t.name,
        "description": t.description,
        "category_id": t.category_id,
        "category_name": category_names.get(t.category_id),
        "question_count": len(questions),
        "questions": questions,
        "is_active": bool(t.is_active),
        "updated_at": t.updated_at.isoformat() if t.updated_at else None,
    }


def _names(db: Session) -> dict:
    return dict(db.query(Category.id, Category.name).all())


def _check_name_free(db: Session, name: str, exclude_id: Optional[int] = None):
    q = db.query(QuestionTemplate).filter(func.lower(QuestionTemplate.name) == name.lower())
    if exclude_id:
        q = q.filter(QuestionTemplate.id != exclude_id)
    existing = q.first()
    if existing:
        hint = "" if existing.is_active else " (a removed template still uses it; choose another name)"
        raise HTTPException(status_code=409, detail=f'A question template named "{name}" already exists{hint}.')


def _check_category(db: Session, category_id: Optional[int]):
    if category_id is not None and not db.get(Category, category_id):
        raise HTTPException(status_code=404, detail="Category not found.")


def _commit(db: Session, conflict: str):
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail=conflict)


@router.get("")
def list_templates(category_id: Optional[int] = None, db: Session = Depends(get_db)):
    names = _names(db)
    rows = db.query(QuestionTemplate).filter(QuestionTemplate.is_active == 1).order_by(QuestionTemplate.name).all()
    items = [_out(t, names) for t in rows]
    if category_id is not None:
        # Templates made for this category first: the frontend suggests the first one.
        items.sort(key=lambda t: (t["category_id"] != category_id, t["category_id"] is not None, t["name"].lower()))
    return {"templates": items}


PLACEHOLDER = "{product}"


def _to_placeholder(text: str, product_name: str) -> str:
    import re
    name = (product_name or "").strip()
    return re.sub(re.escape(name), PLACEHOLDER, text, flags=re.I) if len(name) >= 2 else text


def _for_product(questions: list, product_name: str, source_name: Optional[str] = None) -> list:
    """Independent copies for a product: {product} (and the source product's name) become its name."""
    out = []
    for q in questions:
        text = q["text"].replace(PLACEHOLDER, product_name)
        if source_name and len(source_name.strip()) >= 2:
            import re
            text = re.sub(re.escape(source_name.strip()), product_name, text, flags=re.I)
        out.append({**q, "text": text})
    return out


@router.get("/products")
def question_sources(exclude_product_id: Optional[int] = None, db: Session = Depends(get_db)):
    """Earlier products with survey questions: each can be used as a starting set (copied, never linked)."""
    from app.database.models import SurveyQuestion
    names = _names(db)
    counts = dict(db.query(SurveyQuestion.product_id, func.count(SurveyQuestion.id))
                  .filter(SurveyQuestion.is_active == 1).group_by(SurveyQuestion.product_id).all())
    rows = (db.query(Product).filter(Product.id.in_(list(counts) or [0]), Product.is_active == 1)
            .order_by(Product.name).all())
    out = []
    for p in rows:
        if p.id == exclude_product_id:
            continue
        try:  # a product whose older questions no longer validate is skipped, not the whole list
            questions = question_dicts(_active_questions(db, p.id))
        except HTTPException:
            continue
        out.append({"id": p.id, "name": p.name, "category_name": names.get(p.category_id),
                    "question_count": len(questions), "questions": questions})
    return {"products": out}


class ApplyIn(BaseModel):
    product_id: int
    template_id: Optional[int] = None
    from_product_id: Optional[int] = None
    replace: bool = True  # replace the product's current questions (answers already collected are kept)

    @model_validator(mode="after")
    def one_source(self):
        if bool(self.template_id) == bool(self.from_product_id):
            raise ValueError("Choose a question template or a product to take the questions from.")
        return self


@router.post("/apply")
def apply_template(body: ApplyIn, request: Request, db: Session = Depends(get_db)):
    """Copies a template's (or another product's) questions into a product as its own, independent
    questions: the template and earlier surveys never change. Replacing deactivates the current
    questions (answers already collected are kept); adding keeps them. A product has 5 to 10."""
    from app.api.ownership import check_product_access
    from app.api.surveys import MAX_QUESTIONS
    from app.database.models import SurveyQuestion
    from app.services.rounds import live_survey_lock
    check_product_access(db, request, body.product_id)
    product = db.get(Product, body.product_id)
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")
    locked = live_survey_lock(db, [product.id])
    if locked:  # consumers are answering the current questions right now
        raise HTTPException(status_code=409, detail={"code": "survey_ongoing", "message": locked})
    source_name = None
    if body.template_id:
        t = db.get(QuestionTemplate, body.template_id)
        if not t or not t.is_active:
            raise HTTPException(status_code=404, detail="That question template no longer exists.")
        incoming = question_dicts(t.questions)
    else:
        src = db.get(Product, body.from_product_id)
        if not src:
            raise HTTPException(status_code=404, detail="Product not found.")
        incoming, source_name = question_dicts(_active_questions(db, src.id)), src.name
    incoming = _for_product(incoming, product.name, source_name)
    current = _active_questions(db, product.id)
    keep = [] if body.replace else current
    if len(keep) + len(incoming) > MAX_QUESTIONS:
        raise HTTPException(status_code=400, detail=f"That makes {len(keep) + len(incoming)} questions: a product can have at most "
                                                     f"{MAX_QUESTIONS}. Replace the current questions, or remove some first.")
    if body.replace:
        for q in current:
            q.is_active = 0
    for i, q in enumerate(incoming, start=len(keep) + 1):
        db.add(SurveyQuestion(product_id=product.id, position=i, text=q["text"], question_type=q["question_type"],
                              options=q.get("options"), required=1 if q.get("required", True) else 0,
                              report_role=q.get("report_role")))
    db.commit()
    audit.note(request, target_id=product.id, summary=(
        f"Survey questions of product #{product.id} {'replaced by' if body.replace else 'extended with'} "
        + (f"template #{body.template_id}" if body.template_id else f"the questions of product #{body.from_product_id}")))
    return {"product_id": product.id, "question_count": len(_active_questions(db, product.id)), "added": len(incoming)}


@router.get("/{template_id}")
def get_template(template_id: int, db: Session = Depends(get_db)):
    t = db.get(QuestionTemplate, template_id)
    if not t:
        raise HTTPException(status_code=404, detail="Question template not found.")
    return _out(t, _names(db))


@router.post("", status_code=201)
def create_template(body: TemplateIn, request: Request, db: Session = Depends(get_db)):
    _check_name_free(db, body.name)
    _check_category(db, body.category_id)
    if body.from_product_id:
        product = db.get(Product, body.from_product_id)
        if not product:
            raise HTTPException(status_code=404, detail="Product not found.")
        questions = question_dicts(_active_questions(db, product.id))
        if not questions:
            raise HTTPException(status_code=400, detail=f"{product.name} has no survey questions to save as a template.")
        # the product's name becomes {product}: applied to another product, it reads that product's name
        questions = [{**q, "text": _to_placeholder(q["text"], product.name)} for q in questions]
    else:
        questions = question_dicts([q.model_dump() for q in body.questions])
    t = QuestionTemplate(name=body.name, description=body.description, category_id=body.category_id, questions=questions)
    db.add(t)
    _commit(db, f'A question template named "{body.name}" already exists.')
    db.refresh(t)
    audit.note(request, target_id=t.id, summary=f"Added question template #{t.id} ({t.name})")
    return _out(t, _names(db))


@router.put("/{template_id}")
def update_template(template_id: int, body: TemplateUpdate, db: Session = Depends(get_db)):
    t = db.get(QuestionTemplate, template_id)
    if not t:
        raise HTTPException(status_code=404, detail="Question template not found.")
    data = body.model_dump(exclude_unset=True)
    if "name" in data and data["name"]:
        _check_name_free(db, data["name"], exclude_id=t.id)
        t.name = data["name"]
    if "description" in data:
        t.description = data["description"]
    if "category_id" in data:
        _check_category(db, data["category_id"])
        t.category_id = data["category_id"]
    if data.get("questions") is not None:
        questions = question_dicts(data["questions"])
        if not questions:
            raise HTTPException(status_code=400, detail="A template needs at least one question.")
        t.questions = questions
    _commit(db, "A question template with that name already exists.")
    db.refresh(t)
    return _out(t, _names(db))


@router.delete("/{template_id}")
def remove_template(template_id: int, db: Session = Depends(get_db)):
    t = db.get(QuestionTemplate, template_id)
    if not t:
        raise HTTPException(status_code=404, detail="Question template not found.")
    t.is_active = 0  # products created from it keep their questions
    db.commit()
    return {"id": template_id, "is_active": False, "message": "Template removed. Products created from it keep their questions."}