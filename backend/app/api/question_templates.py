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
