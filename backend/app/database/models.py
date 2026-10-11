from datetime import datetime

from sqlalchemy import (
    JSON,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from app.database.database import Base


# ============================================================
# EXISTING: VIDEO / AUDIO ANALYSIS
# ============================================================

class FeedbackRecord(Base):
    __tablename__ = "feedback_records"

    id = Column(Integer, primary_key=True, index=True)

    # Original uploaded video
    original_filename = Column(String(255), nullable=False)
    video_path = Column(Text, nullable=False)

    # Video information
    duration_seconds = Column(Float, nullable=False)
    uploaded_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # Processing information
    processing_time_seconds = Column(Float, nullable=True)

    # AI analysis
    overall_summary = Column(Text, nullable=True)
    overall_sentiment = Column(String(50), nullable=True)

    # Final weighted product rating (1.0 to 5.0)
    overall_rating = Column(Float, nullable=True)

    # Complete transcript
    transcript = Column(Text, nullable=True)

    # Detailed part/chapter analysis
    chapters = Column(JSON, nullable=True)

    # NEW: the survey this recording belongs to (NULL for older uploads made
    # before surveys existed). Added to existing databases by migrations.py.
    response_id = Column(Integer, ForeignKey("survey_responses.id"), nullable=True, index=True)


class FeedbackAspect(Base):
    __tablename__ = "feedback_aspects"

    id = Column(Integer, primary_key=True, index=True)
    aspect_key = Column(String(150), unique=True, nullable=False, index=True)
    aspect_name = Column(String(255), nullable=False)
    category = Column(String(100), nullable=True, index=True)
    parent_id = Column(Integer, nullable=True, index=True)
    description = Column(Text, nullable=True)
    keywords = Column(JSON, nullable=True)
    is_active = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class FeedbackRatingRule(Base):
    __tablename__ = "feedback_rating_rules"

    id = Column(Integer, primary_key=True, index=True)
    rule_key = Column(String(150), unique=True, nullable=False, index=True)
    sentiment = Column(String(50), nullable=False, index=True)
    severity = Column(String(50), nullable=False, index=True)
    rating = Column(Float, nullable=False)
    weight = Column(Float, nullable=False, default=1.0)
    description = Column(Text, nullable=True)
    is_active = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, nullable=False)


# ============================================================
# NEW: PRODUCT CATALOG
# Category -> Product -> Survey questions
# ============================================================

class Category(Base):
    """e.g. Food & Confectionery, Fragrance."""

    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(150), nullable=False, unique=True)
    description = Column(Text, nullable=True)

    # Deactivate instead of deleting, so stored feedback keeps its category.
    is_active = Column(Integer, nullable=False, default=1)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class Product(Base):
    """e.g. Aam (Mango) Jelly in Food; Perfume, Deo, Itra, Oud, Attar in Fragrance."""

    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("category_id", "name", name="uq_product_category_name"),)

    id = Column(Integer, primary_key=True, index=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False, index=True)

    name = Column(String(200), nullable=False)
    sku = Column(String(80), nullable=True, unique=True)
    description = Column(Text, nullable=True)
    launch_date = Column(Date, nullable=True)

    # "Generate Consolidated Feedback" unlocks once this many surveys are analyzed.
    min_responses = Column(Integer, nullable=False, default=10)
    # How the product is made: {batch_size, ingredients: [...], steps: [...], notes}.
    # Used by the consolidated report to turn feedback into recipe changes.
    recipe = Column(JSON, nullable=True)

    is_active = Column(Integer, nullable=False, default=1)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class SurveyQuestion(Base):
    """The 5-10 product-specific questions the executive asks."""

    __tablename__ = "survey_questions"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)

    # Order in which the executive asks it (1, 2, 3, ...).
    position = Column(Integer, nullable=False, default=1)

    text = Column(Text, nullable=False)

    # rating (1-5) | yes_no | single_choice | text
    question_type = Column(String(30), nullable=False, default="text")
    # Which section of the client summary this question feeds (optional):
    # preference | price | score | improvement | purchase_intent | ease
    report_role = Column(String(30), nullable=True)

    # Choices for single_choice questions, e.g. ["Too sweet", "Just right", ...]
    options = Column(JSON, nullable=True)

    required = Column(Integer, nullable=False, default=1)
    is_active = Column(Integer, nullable=False, default=1)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


# ============================================================
# NEW: FIELD SURVEYS (APIs in Phase 2)
# Respondent -> Survey response -> Answers (+ audio/video FeedbackRecord)
# ============================================================

class Respondent(Base):
    """
    The customer. Contains personal data: never send these fields to the AI
    and never show them in consolidated reports (DPDP Act, 2023).
    """

    __tablename__ = "respondents"

    id = Column(Integer, primary_key=True, index=True)

    name = Column(String(150), nullable=False)
    mobile = Column(String(20), nullable=True, index=True)
    address = Column(Text, nullable=True)
    gender = Column(String(30), nullable=True)
    age = Column(Integer, nullable=True)
    location = Column(String(200), nullable=True, index=True)

    # Consent to collect and use the data, recorded before the survey.
    consent_given = Column(Integer, nullable=False, default=0)
    consent_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class SurveyResponse(Base):
    """One survey session: a respondent giving feedback on one product."""

    __tablename__ = "survey_responses"

    id = Column(Integer, primary_key=True, index=True)

    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    respondent_id = Column(Integer, ForeignKey("respondents.id"), nullable=False, index=True)

    executive_name = Column(String(150), nullable=True, index=True)
    # The signed-in account that did the survey (mobile app). NULL for older surveys.
    user_id = Column(Integer, nullable=True, index=True)

    # How long the customer has used the product: "last_week", "last_month", ...
    usage_period = Column(String(50), nullable=True)

    location = Column(String(200), nullable=True, index=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)

    # draft -> submitted -> analyzed (or failed)
    status = Column(String(30), nullable=False, default="draft", index=True)

    # Structured individual analysis (answers + audio/video combined):
    # positives, issues, neutral, suggestions, aspects with ratings, sentiment.
    # Consolidated reports are built from this, without reprocessing media.
    analysis = Column(JSON, nullable=True)
    overall_rating = Column(Float, nullable=True)
    overall_sentiment = Column(String(50), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    submitted_at = Column(DateTime, nullable=True)
    analyzed_at = Column(DateTime, nullable=True)


class SurveyAnswer(Base):
    """A respondent's answer to one survey question."""

    __tablename__ = "survey_answers"

    id = Column(Integer, primary_key=True, index=True)

    response_id = Column(Integer, ForeignKey("survey_responses.id"), nullable=False, index=True)
    question_id = Column(Integer, ForeignKey("survey_questions.id"), nullable=False, index=True)

    # Snapshot of the question as it was asked, so editing a question later
    # doesn't change the meaning of answers already collected.
    question_text = Column(Text, nullable=False)
    question_type = Column(String(30), nullable=False)

    value = Column(Text, nullable=True)

    # English version of a free-text answer, translated once and reused by
    # every consolidated report (NULL until translated).
    value_en = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


# ============================================================
# QUESTION TEMPLATES: reusable starting questions for new products.
# Managed in the app (Catalog); a template with a category is suggested
# for products of that category. Copying a template never links the
# product to it: the product's questions are its own afterwards.
# ============================================================

class QuestionTemplate(Base):
    __tablename__ = "question_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(150), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True, index=True)
    # [{"text", "question_type", "options", "required", "report_role"}, ...] in order
    questions = Column(JSON, nullable=False)
    is_active = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


# ============================================================
# PRODUCT ASSIGNMENTS: which executives may survey which product.
# A product with no rows here is open to every executive.
# ============================================================

class ProductAssignment(Base):
    __tablename__ = "product_assignments"
    __table_args__ = (UniqueConstraint("user_id", "product_id", name="uq_assignment_user_product"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    product_id = Column(Integer, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


# ============================================================
# ANALYSIS JOBS: uploads waiting for / going through analysis
# ============================================================

class AnalysisJob(Base):
    __tablename__ = "analysis_jobs"

    id = Column(Integer, primary_key=True, index=True)
    status = Column(String(20), nullable=False, default="queued", index=True)  # queued | processing | done | failed
    filename = Column(String(255), nullable=False)
    stored_path = Column(Text, nullable=False)
    response_id = Column(Integer, nullable=True, index=True)
    user_id = Column(Integer, nullable=True, index=True)
    feedback_id = Column(Integer, nullable=True)
    error = Column(Text, nullable=True)
    # The full analysis, exactly as /video/upload returns it.
    result = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    # Which worker process holds the job, and when it last said it was alive.
    # A processing job whose heartbeat stops is put back in the queue.
    # "analyze" (new upload) or "reanalyze" (run an existing recording again)
    kind = Column(String(20), nullable=False, default="analyze")
    target_feedback_id = Column(Integer, nullable=True, index=True)  # record updated by a re-analysis
    worker_id = Column(String(64), nullable=True)
    heartbeat_at = Column(DateTime, nullable=True)
    attempts = Column(Integer, nullable=False, default=0)


# ============================================================
# USERS: survey executives and admins (mobile app login)
# ============================================================

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(150), nullable=False)
    # Sign in with a mobile number or an email (at least one is required).
    mobile = Column(String(20), unique=True, nullable=True, index=True)
    email = Column(String(190), unique=True, nullable=True, index=True)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False, default="executive")  # executive | admin
    is_active = Column(Integer, nullable=False, default=1)
    # Set by an admin: may this executive see feedback content (history, reports)?
    can_view_history = Column(Integer, nullable=False, default=0)
    age = Column(Integer, nullable=True)
    gender = Column(String(20), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_login_at = Column(DateTime, nullable=True)


# ============================================================
# NEW: CONSOLIDATED PRODUCT REPORTS (Phase 3)
# ============================================================

class ConsolidatedReport(Base):
    """One generated product-level report. Each generation is a new version."""

    __tablename__ = "consolidated_reports"

    id = Column(Integer, primary_key=True, index=True)

    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False, default=1)

    # Filters used (date range, location, executive) and the exact responses
    # included, so any report can be reproduced later.
    filters = Column(JSON, nullable=True)
    response_ids = Column(JSON, nullable=False)
    response_count = Column(Integer, nullable=False, default=0)

    # Numbers computed by code: rating, sentiment distribution, aspect counts...
    stats = Column(JSON, nullable=True)

    # Wording written by the AI: themes, conclusion, recommendations.
    narrative = Column(JSON, nullable=True)

    overall_rating = Column(Float, nullable=True)

    generated_at = Column(DateTime, default=datetime.utcnow, nullable=False)