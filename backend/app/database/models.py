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
    event,
)
from sqlalchemy.orm import Session as SessionBase, synonym

from app.database.database import Base


# ============================================================
# COMMON COLUMNS OF EVERY TABLE
# ============================================================

def record_no():
    """
    The primary key. The column is called record_no in the database, but the
    Python attribute (and the "id" in API responses) stays `id`, so code and the
    web / mobile apps are unchanged: Model.id, db.get(Model, n) and
    ForeignKey("table.id") all work (a foreign key names the Python key; the
    database still gets REFERENCES table (record_no)).
    """
    return Column("record_no", Integer, primary_key=True, index=True, key="id")


class AuditMixin:
    """
    Who created a row, who last changed it and when (users.record_no).
    Filled automatically for every ORM insert / update by the listener in
    app/database/audit_columns.py, from the signed-in account of the request.
    NULL = done by the system (background worker, start-up, seed script) or
    by a request without a signed-in account (sign-in, password reset).
    """

    created_by = Column(Integer, nullable=True)
    mod_by = Column(Integer, nullable=True)
    mod_at = Column(DateTime, nullable=True, default=datetime.utcnow, onupdate=datetime.utcnow)


# ============================================================
# EXISTING: VIDEO / AUDIO ANALYSIS
# ============================================================

class FeedbackRecord(AuditMixin, Base):
    __tablename__ = "feedback_records"

    id = record_no()

    # Original uploaded video
    original_filename = Column(String(255), nullable=False)
    video_path = Column(Text, nullable=False)
    # Encrypted at rest (services/recording_crypto.py): the file at video_path is FMCGREC1,
    # its data key wrapped with recording_keys[media_key_id]. 0 = an older plaintext file.
    media_encrypted = Column(Integer, nullable=False, default=0)
    media_key_id = Column(Integer, nullable=True)
    media_wrapped_key = Column(Text, nullable=True)

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

    # positive | mixed | negative | neutral, computed from the rating, sentiment
    # and feedback items whenever the result is saved (see the listener at the
    # end of this file). Lets report pages count tones without loading the
    # chapters and transcript of every recording.
    tone = Column(String(20), nullable=True, index=True)


class FeedbackAspect(AuditMixin, Base):
    __tablename__ = "feedback_aspects"

    id = record_no()
    aspect_key = Column(String(150), unique=True, nullable=False, index=True)
    aspect_name = Column(String(255), nullable=False)
    category = Column(String(100), nullable=True, index=True)
    parent_id = Column(Integer, nullable=True, index=True)
    description = Column(Text, nullable=True)
    keywords = Column(JSON, nullable=True)
    is_active = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class FeedbackRatingRule(AuditMixin, Base):
    __tablename__ = "feedback_rating_rules"

    id = record_no()
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

class Category(AuditMixin, Base):
    """e.g. Food & Confectionery, Fragrance."""

    __tablename__ = "categories"

    id = record_no()
    # Project ID of the category: unique, e.g. PRJ-0001. Generated when left empty
    # (see _set_category_project_id below); the Super Admin may set or change it.
    project_id = Column(String(50), nullable=False, unique=True, index=True)
    # Default recipe plan for the products of this project (copied when a product is added)
    default_plan = Column(JSON, nullable=True)
    name = Column(String(150), nullable=False, unique=True)
    description = Column(Text, nullable=True)

    # Deactivate instead of deleting, so stored feedback keeps its category.
    is_active = Column(Integer, nullable=False, default=1)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = synonym("mod_at")  # the column is mod_at (AuditMixin); old name kept for the code


class Product(AuditMixin, Base):
    """e.g. Aam (Mango) Jelly in Food; Perfume, Deo, Itra, Oud, Attar in Fragrance."""

    __tablename__ = "products"
    __table_args__ = (UniqueConstraint("category_id", "name", name="uq_product_category_name"),)

    id = record_no()
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False, index=True)

    name = Column(String(200), nullable=False)
    sku = Column(String(80), nullable=True, unique=True)
    # Commercial targets (optional): judge whether a new recipe version stays viable before release
    selling_price_per_kg = Column(Float, nullable=True)
    target_cost_per_kg = Column(Float, nullable=True)   # cost limit per kg of product
    min_margin_pct = Column(Float, nullable=True)       # minimum acceptable gross margin %
    description = Column(Text, nullable=True)
    launch_date = Column(Date, nullable=True)

    # "Generate Consolidated Feedback" unlocks once this many surveys are analyzed.
    min_responses = Column(Integer, nullable=False, default=10)
    # How the product is made: {batch_size, ingredients: [...], steps: [...], notes}.
    # Used by the consolidated report to turn feedback into recipe changes.
    recipe = Column(JSON, nullable=True)

    is_active = Column(Integer, nullable=False, default=1)

    # 1 = the Super Admin must start a round before anyone surveys it (new products,
    # and any product after a new recipe version). 0 = an existing product surveyed
    # before rounds were required: it stays open as before (services/product_access.py).
    needs_round = Column(Integer, nullable=False, default=1)
    # The first recipe version number in the app (default 1): a product that already had
    # versions before it entered the app can start at e.g. 3; later saves continue from it.
    start_version = Column(Integer, nullable=False, default=1)
    # Recipe plan: the defaults every action plan of this product starts from
    # (target responses, survey persons needed, dates, cities, audience, notes ...).
    recipe_plan = Column(JSON, nullable=True)
    # Kept from the earlier survey form approval step. The Super Admin's survey questions are
    # final now, so this is always "approved" (start-up resets any old "pending" / "returned").
    questions_status = Column(String(20), nullable=False, default="approved")
    questions_note = Column(Text, nullable=True)
    questions_reviewed_by = Column(String(150), nullable=True)
    questions_reviewed_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = synonym("mod_at")  # the column is mod_at (AuditMixin); old name kept for the code


class SurveyQuestion(AuditMixin, Base):
    """The 5-10 product-specific questions the executive asks."""

    __tablename__ = "survey_questions"

    id = record_no()
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)

    # Order in which the executive asks it (1, 2, 3, ...).
    position = Column(Integer, nullable=False, default=1)

    text = Column(Text, nullable=False)

    # rating (1-5) | yes_no | single_choice | text
    question_type = Column(String(30), nullable=False, default="text")
    # The Question type chosen from Master when it isn't one of the six built-in ones (e.g.
    # "dropdown"): question_type then holds the built-in type it behaves like. NULL = built-in.
    type_code = Column(String(80), nullable=True)
    # Which section of the client summary this question feeds (optional):
    # preference | price | score | improvement | purchase_intent | ease
    report_role = Column(String(30), nullable=True)

    # Choices for single_choice questions, e.g. ["Too sweet", "Just right", ...]
    options = Column(JSON, nullable=True)

    required = Column(Integer, nullable=False, default=1)
    is_active = Column(Integer, nullable=False, default=1)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = synonym("mod_at")  # the column is mod_at (AuditMixin); old name kept for the code


# ============================================================
# NEW: FIELD SURVEYS (APIs in Phase 2)
# Respondent -> Survey response -> Answers (+ audio/video FeedbackRecord)
# ============================================================

class Respondent(AuditMixin, Base):
    """
    The customer. Contains personal data: never send these fields to the AI
    and never show them in consolidated reports (DPDP Act, 2023).
    """

    __tablename__ = "respondents"

    id = record_no()

    name = Column(String(150), nullable=False)
    mobile = Column(String(20), nullable=True, index=True)
    address = Column(Text, nullable=True)
    gender = Column(String(30), nullable=True)
    age = Column(Integer, nullable=True)
    location = Column(String(200), nullable=True, index=True)
    country = Column(String(80), nullable=True, index=True)
    # Country -> State -> City, picked from the LOV lists COUNTRY / STATE / CITY
    # (the names are stored). NULL for customers recorded before these existed.
    state = Column(String(120), nullable=True, index=True)
    city = Column(String(120), nullable=True, index=True)
    occupation = Column(String(80), nullable=True, index=True)       # from the Occupation master
    sec_class = Column(String(40), nullable=True, index=True)        # SEC A / B / C ... (Class master)
    respondent_type = Column(String(60), nullable=True, index=True)  # End consumer / Retailer ... (master)

    # Consent to collect and use the data, recorded before the survey.
    consent_given = Column(Integer, nullable=False, default=0)
    consent_at = Column(DateTime, nullable=True)

    # The account (users.record_no) that recorded this customer. Overrides
    # AuditMixin.created_by only to keep its index (ownership checks filter on it). Only that account,
    # the owner of a survey with them, and admins may read the record.
    # NULL for customers recorded before this column existed.
    created_by = Column(Integer, nullable=True, index=True)

    # Offline mode: the id the phone gave this customer before it was uploaded. A retried
    # upload with the same id returns this record instead of creating a duplicate.
    client_uuid = Column(String(36), nullable=True, unique=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class SurveyResponse(AuditMixin, Base):
    """One survey session: a respondent giving feedback on one product."""

    __tablename__ = "survey_responses"

    id = record_no()

    # Offline mode: the phone's submission id (see Respondent.client_uuid).
    client_uuid = Column(String(36), nullable=True, unique=True)

    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    respondent_id = Column(Integer, ForeignKey("respondents.id"), nullable=False, index=True)

    executive_name = Column(String(150), nullable=True, index=True)
    # The signed-in account that did the survey (mobile app). NULL for older surveys.
    user_id = Column(Integer, nullable=True, index=True)

    # The survey round this survey belongs to (survey_rounds.id). Set when the
    # survey is started: the product's live round (see services/rounds.py).
    round_id = Column(Integer, nullable=True, index=True)
    action_plan_id = Column(Integer, nullable=True, index=True)  # action_plans.id of the person who took it
    # The product (recipe) version the customer tasted: recipe_versions.version.
    # Chosen by the survey person when a product has several versions in fieldwork,
    # otherwise the only one. Filled from the round for surveys made before this existed.
    recipe_version = Column(Integer, nullable=True, index=True)

    # How long the customer has used the product: "last_week", "last_month", ...
    usage_period = Column(String(50), nullable=True)

    location = Column(String(200), nullable=True, index=True)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    # GPS fix details (offline GPS): accuracy radius in metres, when the phone/browser
    # took the fix (UTC, kept from an offline capture), and where: android | ios | web.
    location_accuracy = Column(Float, nullable=True)
    location_captured_at = Column(DateTime, nullable=True)
    location_source = Column(String(20), nullable=True)

    # draft -> submitted -> analyzed (or failed)
    status = Column(String(30), nullable=False, default="draft", index=True)
    # Internal / External survey (point 4): who the respondent is to the company.
    audience = Column(String(20), nullable=True, index=True)
    # Report review (point 9): generated -> pending_admin -> forwarded (approved by
    # admin) -> reviewed (by the Super Admin); returned = sent back to the survey person.
    report_status = Column(String(30), nullable=True, index=True)
    report_status_at = Column(DateTime, nullable=True)

    # Structured individual analysis (answers + audio/video combined):
    # positives, issues, neutral, suggestions, aspects with ratings, sentiment.
    # Consolidated reports are built from this, without reprocessing media.
    analysis = Column(JSON, nullable=True)
    overall_rating = Column(Float, nullable=True)
    overall_sentiment = Column(String(50), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    submitted_at = Column(DateTime, nullable=True)
    analyzed_at = Column(DateTime, nullable=True)


class SurveyAnswer(AuditMixin, Base):
    """A respondent's answer to one survey question."""

    __tablename__ = "survey_answers"

    id = record_no()

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

class QuestionTemplate(AuditMixin, Base):
    __tablename__ = "question_templates"

    id = record_no()
    name = Column(String(150), nullable=False, unique=True)
    description = Column(Text, nullable=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True, index=True)
    # [{"text", "question_type", "options", "required", "report_role"}, ...] in order
    questions = Column(JSON, nullable=False)
    is_active = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = synonym("mod_at")  # the column is mod_at (AuditMixin); old name kept for the code


# ============================================================
# PRODUCT ASSIGNMENTS: which survey persons survey which ROUND of a product.
# An Admin assigns each round after the Super Admin starts it; nothing is open
# to everyone. Rows of earlier rounds stay as history (services/product_access.py).
# ============================================================

class ProductAssignment(AuditMixin, Base):
    __tablename__ = "product_assignments"
    __table_args__ = (UniqueConstraint("round_id", "user_id", name="uq_assignment_round_user"),)

    id = record_no()
    user_id = Column(Integer, nullable=False, index=True)
    product_id = Column(Integer, nullable=False, index=True)
    round_id = Column(Integer, nullable=True, index=True)  # survey_rounds.id
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class SurveyRound(AuditMixin, Base):
    """
    A survey round of one product: consumers taste the product made with one
    recipe version, until the target number of surveys is reached. The
    organization starts rounds in its portal (after changing the recipe), so
    every round has its own report and two rounds can be compared.

    Round 1 of a product that already had surveys is created automatically and
    holds those surveys (migrations.py). Surveys are filed under the live round.
    """

    __tablename__ = "survey_rounds"
    __table_args__ = (UniqueConstraint("product_id", "number", name="uq_round_number"),)

    id = record_no()
    product_id = Column(Integer, nullable=False, index=True)
    number = Column(Integer, nullable=False)  # 1, 2, 3 ... per product
    recipe_version = Column(Integer, nullable=True)  # recipe_versions.version used in this round
    target = Column(Integer, nullable=False, default=30)  # consumers to survey
    due_date = Column(Date, nullable=True)  # "wanted by"
    note = Column(Text, nullable=True)  # what changed, for the research team
    status = Column(String(20), nullable=False, default="live", index=True)  # live | closed
    # Who started it: the organization login, an admin, or "system" (Round 1 of older surveys).
    started_by = Column(Integer, nullable=True)
    started_by_name = Column(String(150), nullable=True)
    started_by_role = Column(String(20), nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    closed_at = Column(DateTime, nullable=True)
    # "target" (enough surveys) or "ended" (ended early by the organization or an admin)
    closed_reason = Column(String(20), nullable=True)
    # The product's total consumer responses when this survey ended: the next survey of the
    # project can start only once the Super Admin raises the total above it (and plans the increase).
    total_at_close = Column(Integer, nullable=True)


class RecipeVersion(AuditMixin, Base):
    """
    Every saved version of a product's recipe (by an admin or the brand's client
    login), so changes can be compared and nothing is lost when a recipe is edited.
    products.recipe always holds the latest version.
    """

    __tablename__ = "recipe_versions"
    __table_args__ = (UniqueConstraint("product_id", "version", name="uq_recipe_version"),)

    id = record_no()
    product_id = Column(Integer, nullable=False, index=True)
    version = Column(Integer, nullable=False)
    recipe = Column(JSON, nullable=True)  # None = the recipe was cleared
    # The recipe's code: RCP-0001, RCP-0002 ... one per saved version, given when it is saved
    # (_set_recipe_code below). Shown in the portal; opening it shows that recipe.
    recipe_code = Column(String(20), nullable=True, unique=True, index=True)
    # A version added from a tested one: its cost comparison with that version and what was decided
    # about each consumer recommendation (history, kept with the version)
    cost_comparison = Column(JSON, nullable=True)
    decisions = Column(JSON, nullable=True)
    # Who saved it (name and role copied, so history still reads correctly later).
    changed_by = Column(Integer, nullable=True)
    changed_by_name = Column(String(150), nullable=True)
    changed_by_role = Column(String(20), nullable=True)
    # Short human-readable list of what changed compared with the previous version.
    changes = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


# ============================================================
# ANALYSIS JOBS: uploads waiting for / going through analysis
# ============================================================

class UploadSession(AuditMixin, Base):
    """
    A resumable recording upload (offline mode, api/resumable_uploads.py). The phone sends
    the file in pieces; after a dropped connection it asks how much arrived and continues
    from there. Completing it queues the analysis exactly once (job_id).
    """

    __tablename__ = "upload_sessions"

    id = record_no()
    upload_id = Column(String(36), nullable=False, unique=True)  # chosen by the phone (UUID)
    user_id = Column(Integer, nullable=True, index=True)
    response_id = Column(Integer, nullable=True, index=True)
    filename = Column(String(255), nullable=False)
    total_bytes = Column(Integer, nullable=False)
    received_bytes = Column(Integer, nullable=False, default=0)
    sha256 = Column(String(64), nullable=True)  # checked when the upload completes
    part_path = Column(Text, nullable=False)  # storage key of the partial file
    # An encrypted recording (from the phone or the browser): its wrapped data key
    enc_key_id = Column(Integer, nullable=True)
    enc_wrapped_key = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="receiving", index=True)  # receiving | complete
    job_id = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class AnalysisJob(AuditMixin, Base):
    __tablename__ = "analysis_jobs"

    id = record_no()
    status = Column(String(20), nullable=False, default="queued", index=True)  # queued | processing | done | failed
    filename = Column(String(255), nullable=False)
    stored_path = Column(Text, nullable=False)
    response_id = Column(Integer, nullable=True, index=True)
    user_id = Column(Integer, nullable=True, index=True)
    feedback_id = Column(Integer, nullable=True)
    # The queued file is encrypted (FMCGREC1) with this wrapped data key (decrypted only for the analysis)
    enc_key_id = Column(Integer, nullable=True)
    enc_wrapped_key = Column(Text, nullable=True)
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

class User(AuditMixin, Base):
    __tablename__ = "users"

    id = record_no()
    name = Column(String(150), nullable=False)
    # Sign in with a mobile number or an email (at least one is required).
    mobile = Column(String(20), unique=True, nullable=True, index=True)
    email = Column(String(190), unique=True, nullable=True, index=True)
    password_hash = Column(String(255), nullable=False)
    # executive | admin | client (the organization: created with python -m app.tools.organization)
    role = Column(String(20), nullable=False, default="executive")
    is_active = Column(Integer, nullable=False, default=1)
    # Set by an admin: may this executive see feedback content (history, reports)?
    can_view_history = Column(Integer, nullable=False, default=0)
    age = Column(Integer, nullable=True)
    gender = Column(String(20), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_login_at = Column(DateTime, nullable=True)
    # Raised to sign this account out on every device at once (password reset,
    # account disabled, role changed, "sign out everywhere"). Every sign-in
    # token carries the number it was issued with; an older number is refused.
    token_version = Column(Integer, nullable=False, default=0)
    # 1 = must choose a new password before doing anything else (the organization
    # login after its starting password was created or reset by the server command).
    must_change_password = Column(Integer, nullable=False, default=0)
    # Organization portal: when the Updates page was last opened (for the unread count).
    updates_seen_at = Column(DateTime, nullable=True)


# ============================================================
# NEW: CONSOLIDATED PRODUCT REPORTS (Phase 3)
# ============================================================

class ConsolidatedReport(AuditMixin, Base):
    """One generated product-level report. Each generation is a new version."""

    __tablename__ = "consolidated_reports"

    id = record_no()

    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    version = Column(Integer, nullable=False, default=1)
    # NULL: the report of all the product's surveys (admin dashboard).
    # Set: the report of one survey round (organization portal, round reports).
    round_id = Column(Integer, nullable=True, index=True)

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


# ============================================================
# NOTIFICATIONS: the bell in the web dashboard and the mobile app
# One row per recipient, so every person has their own read / unread state.
# ============================================================

class Notification(AuditMixin, Base):
    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("user_id", "dedupe_key", name="uq_notification_user_key"),)

    id = record_no()
    # Who sees it (users.id): admins for missing videos, the executive for new products.
    user_id = Column(Integer, nullable=False, index=True)
    # "survey_missing_video" | "product_assigned"
    kind = Column(String(40), nullable=False, index=True)
    title = Column(String(200), nullable=False)
    message = Column(Text, nullable=False)
    # Details shown when the notification is opened (survey, product, person, times).
    data = Column(JSON, nullable=True)
    # Stops the same event from being notified twice to the same person.
    dedupe_key = Column(String(120), nullable=True)
    is_read = Column(Integer, nullable=False, default=0, index=True)
    read_at = Column(DateTime, nullable=True)
    # Set when the reason went away (e.g. the missing video was uploaded later).
    resolved_at = Column(DateTime, nullable=True)
    # Cleared from the bell by its owner: hidden, but kept so it is never sent again (dedupe, syncs).
    dismissed_at = Column(DateTime, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

# ============================================================
# SIGN-IN PROTECTION: failed login attempts (auth.py)
# Kept in the database so the limit holds across several API processes.
# Rows older than a day are removed automatically.
# ============================================================

class RefreshToken(AuditMixin, Base):
    """A device's long-lived sign-in (the mobile app): exchanged at /auth/refresh for a new access
    token and a NEW refresh token (rotation). Only a hash is stored. It stops working when it is
    revoked (logout, rotation, reuse detected), expires, or the account's token_version changes
    (password changed, disabled, signed out everywhere)."""

    __tablename__ = "refresh_tokens"

    id = synonym("record_no")
    record_no = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    token_hash = Column(String(64), nullable=False, unique=True, index=True)
    token_version = Column(Integer, nullable=False, default=0)
    family = Column(String(32), nullable=False, index=True)  # one per device sign-in (rotations share it)
    device = Column(String(150), nullable=True)
    created_at = Column(DateTime, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)
    revoked_reason = Column(String(30), nullable=True)


class LoginFailure(AuditMixin, Base):
    __tablename__ = "login_failures"

    id = record_no()
    # "id:<mobile or email>" or "ip:<client address>"
    key = Column(String(200), nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


class RevokedToken(AuditMixin, Base):
    """A sign-in token ended by "Sign out" before its expiry (one device only)."""

    __tablename__ = "revoked_tokens"

    id = record_no()
    # SHA-256 of the token: the token itself is never stored.
    token_hash = Column(String(64), nullable=False, unique=True, index=True)
    user_id = Column(Integer, nullable=True, index=True)
    # When the token would have expired anyway; the row can be removed after that.
    expires_at = Column(DateTime, nullable=False, index=True)


class AuditLog(AuditMixin, Base):
    """
    Who changed what, and when (admin changes, catalog and recipe edits,
    deletions, survey submissions, sign-ins and sign-outs). Rows are only ever
    added, never edited. No passwords, tokens, request bodies or customer
    names, phone numbers or addresses are stored here.
    """

    __tablename__ = "audit_log"

    id = record_no()
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    # The account that did it (name and role copied, so the entry still reads
    # correctly after the account is renamed or deleted).
    user_id = Column(Integer, nullable=True, index=True)
    user_name = Column(String(150), nullable=True)
    user_role = Column(String(20), nullable=True)
    # Short machine name, e.g. "user.update", "recipe.update", "product.delete"
    action = Column(String(60), nullable=False, index=True)
    # What it was done to, e.g. ("product", 12)
    target_type = Column(String(40), nullable=True, index=True)
    target_id = Column(Integer, nullable=True, index=True)
    # Human-readable one-line summary shown on the Activity log page
    summary = Column(String(300), nullable=True)
    # Field-level changes, e.g. {"is_active": [true, false]} (never secrets)
    details = Column(JSON, nullable=True)
    method = Column(String(10), nullable=True)
    path = Column(String(300), nullable=True)
    status = Column(Integer, nullable=True)
    ip = Column(String(64), nullable=True)
    request_id = Column(String(128), nullable=True)


# ============================================================
# Keep FeedbackRecord.tone in step with the result it summarizes
# (new analyses, re-analyses and manual edits all pass through here).
# ============================================================

def _set_feedback_tone(mapper, connection, target):  # noqa: ARG001 (SQLAlchemy event signature)
    from app.services.consolidation import response_tone

    target.tone = response_tone(target.overall_sentiment, target.chapters, target.overall_rating)


event.listen(FeedbackRecord, "before_insert", _set_feedback_tone)
event.listen(FeedbackRecord, "before_update", _set_feedback_tone)


# ============================================================
# A new category without a project ID gets the next PRJ-0001, PRJ-0002, ...
# ============================================================

PROJECT_ID_PREFIX = "CAT-"  # category codes ("PRJ-" codes belong to projects)


def next_project_id(connection, taken=()) -> str:
    """The category code a new category gets: the lowest free CAT-#### number (not in use and not in
    `taken`). A deleted category's number is given again: with CAT-0001..CAT-0005 in use, the next
    is CAT-0006, also after CAT-0006 was deleted; a gap (CAT-0003 deleted) is filled first."""
    from sqlalchemy import select

    used = set()
    stored = connection.execute(select(Category.project_id).where(Category.project_id.like(f"{PROJECT_ID_PREFIX}%"))).scalars()
    for value in [*stored, *taken]:
        digits = (value or "")[len(PROJECT_ID_PREFIX):]
        if digits.isdigit():
            used.add(int(digits))
    number = 1
    while number in used:
        number += 1
    return f"{PROJECT_ID_PREFIX}{number:04d}"


def _set_category_project_id(mapper, connection, target):  # noqa: ARG001 (SQLAlchemy event signature)
    if (target.project_id or "").strip():
        return
    # Several categories saved together are all handed out before any is inserted:
    # remember the ones given in this session so each gets its own number.
    from sqlalchemy.orm import object_session

    session = object_session(target)
    taken = session.info.setdefault("project_ids_given", set()) if session is not None else set()
    target.project_id = next_project_id(connection, taken)
    taken.add(target.project_id)


event.listen(Category, "before_insert", _set_category_project_id)


# ============================================================
# PASSWORD RESET CODES (emailed to Admin and Super Admin accounts)
# Hashed, single use, 15 minutes, 5 tries (services/password_reset.py).
# ============================================================

class PasswordResetCode(AuditMixin, Base):
    __tablename__ = "password_reset_codes"

    id = record_no()
    user_id = Column(Integer, nullable=False, index=True)
    code_hash = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    attempts = Column(Integer, nullable=False, default=0)
    used_at = Column(DateTime, nullable=True)


# ============================================================
# PRODUCT CHECKS: does the recorded feedback talk about the product the survey
# person selected? One row per survey (services/product_check.py).
#   status:        match | mismatch | uncertain
#   review_status: none | pending | confirmed | corrected | invalid
# The survey keeps the selected product until an admin corrects it; the original
# product and round are kept here (audit trail: audit_log "product_check.*").
# ============================================================

class ProductCheck(AuditMixin, Base):
    __tablename__ = "product_checks"

    id = record_no()
    response_id = Column(Integer, nullable=False, unique=True, index=True)
    feedback_id = Column(Integer, nullable=True)
    selected_product_id = Column(Integer, nullable=False, index=True)
    selected_round_id = Column(Integer, nullable=True)
    detected_product_id = Column(Integer, nullable=True)
    status = Column(String(20), nullable=False, index=True)
    confidence = Column(Float, nullable=True)
    evidence = Column(JSON, nullable=True)
    checked_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    review_status = Column(String(20), nullable=False, default="none", index=True)
    reviewed_by = Column(Integer, nullable=True)
    reviewed_by_name = Column(String(255), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)
    corrected_product_id = Column(Integer, nullable=True)
    remarks = Column(Text, nullable=True)


# ============================================================
# MASTER LISTS: Occupation, Class (SEC) and Respondent type (points 4 and 5)
# ============================================================

class MasterValue(AuditMixin, Base):
    __tablename__ = "master_values"

    id = record_no()
    kind = Column(String(30), nullable=False, index=True)  # occupation | sec_class | respondent_type
    name = Column(String(80), nullable=False)
    position = Column(Integer, nullable=False, default=0)
    is_active = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


# ============================================================
# RECIPE STANDARDS (point 3): rules every recipe is checked against
# ============================================================

class RecipeStandard(AuditMixin, Base):
    __tablename__ = "recipe_standards"

    id = record_no()
    name = Column(String(160), nullable=False)
    # required_ingredient | ingredient_share | max_cost_per_kg | costs_complete
    kind = Column(String(30), nullable=False)
    ingredient = Column(String(120), nullable=True)
    min_value = Column(Float, nullable=True)
    max_value = Column(Float, nullable=True)
    # UOM of min/max (Other Master > Measurement unit, e.g. "g", "MT", "mL"): the quantity of a
    # custom rule, or the "per" of a cost rule (₹ per g). NULL = kg, as every rule before UOMs.
    unit = Column(String(16), nullable=True)
    # Scope (exactly one): one product, one category, or every product (explicit)
    product_id = Column(Integer, nullable=True, index=True)
    category_id = Column(Integer, nullable=True, index=True)
    applies_to_all = Column(Integer, nullable=False, default=0)
    is_active = Column(Integer, nullable=False, default=1)
    # Name of the person who created it (shown on the page). The account id is
    # created_by (AuditMixin); this column was called created_by before.
    created_by_name = Column(String(150), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


# ============================================================
# RECIPE ATTACHMENTS (point 2): supporting files of a recipe's process
# ============================================================

class RecipeAttachment(AuditMixin, Base):
    __tablename__ = "recipe_attachments"

    id = record_no()
    product_id = Column(Integer, nullable=False, index=True)
    recipe_version = Column(Integer, nullable=True)
    filename = Column(String(255), nullable=False)
    stored_path = Column(String(500), nullable=False)
    content_type = Column(String(120), nullable=True)
    size_bytes = Column(Integer, nullable=True)
    uploaded_by = Column(String(150), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


# ============================================================
# REPORT REVIEW HISTORY (point 9): every step of a survey report's review
# ============================================================

class ReportReviewEvent(AuditMixin, Base):
    __tablename__ = "report_review_events"

    id = record_no()
    response_id = Column(Integer, nullable=False, index=True)
    from_status = Column(String(30), nullable=True)
    to_status = Column(String(30), nullable=False)
    by_user_id = Column(Integer, nullable=True)
    by_name = Column(String(150), nullable=True)
    by_role = Column(String(20), nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


# ============================================================
# CUSTOM REPORTS: a prompt -> a checked plan -> an Excel / PowerPoint file
# (services/custom_reports.py). Also the log of every prompt.
# ============================================================

class CustomReportRequest(AuditMixin, Base):
    __tablename__ = "custom_report_requests"

    id = record_no()
    user_id = Column(Integer, nullable=True, index=True)
    user_name = Column(String(150), nullable=True)
    user_role = Column(String(20), nullable=True)
    format = Column(String(10), nullable=False)  # xlsx | pptx
    prompt = Column(Text, nullable=True)
    plan = Column(JSON, nullable=True)
    planned_by = Column(String(20), nullable=True)  # ai | keywords | standard
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)



# ============================================================
# LOV (list of values): every fixed list the screens show, kept in the database
# instead of in the code (services/lov.py, api/lov.py).
#   m_othermastername  one row per list    e.g. USAGE_PERIOD, GENDER, SENTIMENT
#   m_othermasterdata  one row per value   e.g. USAGE_PERIOD / last_week / "In the last week"
# ============================================================

class OtherMasterName(AuditMixin, Base):
    __tablename__ = "m_othermastername"

    id = record_no()
    master_code = Column(String(50), nullable=False, unique=True, index=True)  # USAGE_PERIOD
    master_name = Column(String(150), nullable=False)                          # "Usage period"
    description = Column(Text, nullable=True)
    # 1 = the program relies on the codes of this list (sentiments, question types ...):
    # admins may change labels and order, but not add or hide values.
    # 0 = an open list (usage period, gender): admins may also add and hide values.
    is_system = Column(Integer, nullable=False, default=1)
    # A list inside another list: STATE values belong to a COUNTRY value, CITY values
    # to a STATE value (m_othermasterdata.parent_id). NULL = a flat list.
    parent_master_id = Column(Integer, ForeignKey("m_othermastername.id"), nullable=True)
    # Other Master Data (api/other_masters.py): Active = the list can be used at all;
    # can_edit = its existing values may be changed; allow_add = new values may be added.
    is_active = Column(Integer, nullable=False, default=1)
    can_edit = Column(Integer, nullable=False, default=1)
    allow_add = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class OtherMasterData(AuditMixin, Base):
    __tablename__ = "m_othermasterdata"
    __table_args__ = (UniqueConstraint("master_id", "data_code", name="uq_othermasterdata_code"),)

    id = record_no()
    master_id = Column(Integer, ForeignKey("m_othermastername.id"), nullable=False, index=True)
    # The value of the parent list this value belongs to (a state's country, a city's state).
    parent_id = Column(Integer, ForeignKey("m_othermasterdata.id"), nullable=True, index=True)
    data_code = Column(String(80), nullable=False)    # stored / used by the program: "last_week"
    data_value = Column(String(255), nullable=False)  # shown on screen: "In the last week"
    sort_order = Column(Integer, nullable=False, default=0)
    # Anything else a screen needs for the value, e.g. {"tone": "warn"} or {"hint": "..."}
    extra = Column(JSON, nullable=True)
    is_active = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


# Fills created_by / mod_by / mod_at on every save (registers a Session listener).
from app.database import audit_columns  # noqa: E402, F401


# ============================================================
# ACTION PLANS: inside a survey round, each with its own team, cities,
# target and dates (services/action_plans.py). Several per round.
# ============================================================

class ActionPlan(AuditMixin, Base):
    __tablename__ = "action_plans"

    id = record_no()
    product_id = Column(Integer, nullable=False, index=True)
    # survey_rounds.id. NULL = planned in a project before its survey starts (the project's
    # "Recipe plan"); starting the survey attaches the project's plans to the new round.
    round_id = Column(Integer, nullable=True, index=True)
    project_id = Column(Integer, nullable=True, index=True)  # projects.id it was planned in
    recipe_version = Column(Integer, nullable=True)  # the round's recipe version (version tracking)
    name = Column(String(150), nullable=False)
    target = Column(Integer, nullable=False, default=10)  # consumer responses for this plan
    persons_needed = Column(Integer, nullable=True)  # survey persons planned
    start_date = Column(Date, nullable=True)
    end_date = Column(Date, nullable=True)
    cities = Column(JSON, nullable=True)  # ["Delhi", "Mumbai"]; empty = anywhere
    audience = Column(String(20), nullable=True)  # internal | external | None (both)
    respondent_types = Column(JSON, nullable=True)
    notes = Column(Text, nullable=True)
    copied_from_id = Column(Integer, nullable=True)  # the plan of the previous round it was copied from
    # PLA001, PLA002 ... numbered within its project (each project starts at PLA001); given automatically
    plan_code = Column(String(10), nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ActionPlanMember(AuditMixin, Base):
    """A team member in an action plan, with the cities they may survey (a subset of the plan's)."""

    __tablename__ = "action_plan_members"
    __table_args__ = (UniqueConstraint("action_plan_id", "user_id", name="uq_plan_member"),)

    id = record_no()
    action_plan_id = Column(Integer, nullable=False, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    cities = Column(JSON, nullable=True)  # empty = every city of the plan
    # Consumer responses this person must collect in this plan (set by the Admin). The plan's
    # members' targets add up to the plan's target; a person can't submit more than theirs.
    target = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ActionPlanAllocationLog(AuditMixin, Base):
    """Every change of a team member's response allocation: who changed it, when, from what to what."""

    __tablename__ = "action_plan_allocation_log"

    id = record_no()
    action_plan_id = Column(Integer, nullable=False, index=True)
    user_id = Column(Integer, nullable=False, index=True)  # the team member
    old_target = Column(Integer, nullable=True)
    new_target = Column(Integer, nullable=True)
    changed_by = Column(Integer, nullable=True)
    changed_by_name = Column(String(150), nullable=True)
    at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


# ============================================================
# PROJECTS: the study of ONE VERSION of a product (of one category).
# A product's version is its recipe version (recipe_versions.version, per product;
# survey_rounds.recipe_version says which version a round tests). So a project is
# identified by (product_id, product_version), unique:
#   - projects are made by the Super Admin (Projects > Add project), one per product
#     version; nothing creates them automatically when a product or recipe is saved,
#   - a version deleted by mistake takes its draft project with it (listeners below).
# The project's status and survey rounds are those of its version.
# ============================================================

class Project(AuditMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("product_id", "product_version", name="uq_project_product_version"),)

    id = record_no()
    project_code = Column(String(30), nullable=False, unique=True, index=True)  # PRJ-0001
    project_name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False, index=True)
    # recipe_versions.version of product_id this project studies (1 = the product as created)
    product_version = Column(Integer, nullable=False, default=1)
    # Total consumer responses this project (product version) needs, set when the project is
    # created; its survey plans can't add up to more. Raised with "Edit response target".
    total_target = Column(Integer, nullable=True)
    # Set when the Super Admin ends the project: its collecting survey ends with it, and nothing
    # more is planned, started or reopened (responses already collected are kept).
    closed_at = Column(DateTime, nullable=True)
    closed_by_name = Column(String(150), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


PROJECT_CODE_PREFIX = "PRJ-"


def next_project_code(connection, taken=()) -> str:
    """The next free PRJ-#### project code."""
    from sqlalchemy import select

    highest = 0
    stored = connection.execute(select(Project.project_code).where(Project.project_code.like(f"{PROJECT_CODE_PREFIX}%"))).scalars()
    for value in [*stored, *taken]:
        digits = value[len(PROJECT_CODE_PREFIX):]
        if digits.isdigit():
            highest = max(highest, int(digits))
    return f"{PROJECT_CODE_PREFIX}{highest + 1:04d}"


def _set_project_code(mapper, connection, target):  # noqa: ARG001 (SQLAlchemy event signature)
    if (target.project_code or "").strip():
        return
    from sqlalchemy.orm import object_session

    session = object_session(target)
    taken = session.info.setdefault("project_codes_given", set()) if session is not None else set()
    target.project_code = next_project_code(connection, taken)
    taken.add(target.project_code)


event.listen(Project, "before_insert", _set_project_code)


# ---------------------------------------------------------------- one project per product version

def product_first_version(product) -> int:
    """A product's first version number (products.start_version, default 1)."""
    return max(1, int(getattr(product, "start_version", None) or 1))


def project_for_version(session, product, version: int, created_by=None):
    """The project of this product version, created when missing (added to the session,
    not committed). Name: "<product> · v<version>"; the Super Admin can rename it."""
    existing = session.query(Project).filter(Project.product_id == product.id,
                                             Project.product_version == version).first()
    if existing is not None:
        return existing
    for pending in session.new:  # added earlier in this flush round
        if isinstance(pending, Project) and pending.product_id == product.id and pending.product_version == version:
            return pending
    project = Project(project_name=f"{product.name} · v{version}"[:200], category_id=product.category_id,
                      product_id=product.id, product_version=version,
                      description=f"Study of {product.name}, version {version}.")
    if created_by:
        project.created_by = created_by
    session.add(project)
    return project


def _collect_versions(session, flush_context):  # noqa: ARG001 (SQLAlchemy event signature)
    """After a flush: remember which product versions were just deleted. (Projects are made
    by hand on the Add project page, never automatically for a new product or recipe.)"""
    todo = session.info.setdefault("project_versions_todo", [])
    for obj in session.deleted:
        if isinstance(obj, RecipeVersion):
            todo.append(("drop", obj.product_id, obj.version))


def _apply_versions(session, flush_context):  # noqa: ARG001
    """Then remove the draft project of a version deleted by mistake (it never had a survey)."""
    todo = session.info.pop("project_versions_todo", [])
    if not todo:
        return
    with session.no_autoflush:
        for action, product_id, version in todo:
            if action == "drop":
                still_used = session.query(SurveyRound.id).filter(SurveyRound.product_id == product_id,
                                                                  SurveyRound.recipe_version == version).first()
                project = session.query(Project).filter(Project.product_id == product_id,
                                                        Project.product_version == version).first()
                if project is not None and still_used is None:
                    session.delete(project)


event.listen(SessionBase, "after_flush", _collect_versions)
event.listen(SessionBase, "after_flush_postexec", _apply_versions)


# ============================================================
# ENCRYPTED RECORDINGS AND RECORDING ACCESS (services/recording_crypto.py,
# services/recording_access.py). Consumer recordings are encrypted on the device
# (AES-256-GCM, a key per recording, wrapped with the server's RSA key). Only an
# Admin given access to the product, who enters the product's passkey, can play one.
# ============================================================

class RecordingKey(AuditMixin, Base):
    """The server's key pair for recordings. The private key is encrypted with the master key."""

    __tablename__ = "recording_keys"

    id = record_no()
    public_pem = Column(Text, nullable=False)
    private_enc = Column(Text, nullable=False)  # Fernet(master key) of the PKCS#8 PEM
    algorithm = Column(String(60), nullable=False)
    is_active = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ProductPasskey(AuditMixin, Base):
    """A product's recording passkey: only its scrypt hash is kept, never the passkey."""

    __tablename__ = "product_passkeys"

    id = record_no()
    product_id = Column(Integer, nullable=False, unique=True, index=True)
    passkey_hash = Column(String(200), nullable=False)  # scrypt$n$r$p$salt_b64$hash_b64
    hint = Column(String(8), nullable=True)  # the last 2 characters, to tell versions apart ("…7Q")
    version = Column(Integer, nullable=False, default=1)  # +1 at each rotation
    set_by = Column(Integer, nullable=True)
    set_by_name = Column(String(150), nullable=True)
    set_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class RecordingAccessGrant(AuditMixin, Base):
    """An Admin allowed to play the recordings of a product (given by the Super Admin)."""

    __tablename__ = "recording_access_grants"
    __table_args__ = (UniqueConstraint("product_id", "user_id", name="uq_recording_grant"),)

    id = record_no()
    product_id = Column(Integer, nullable=False, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    granted_by = Column(Integer, nullable=True)
    granted_by_name = Column(String(150), nullable=True)
    granted_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class PasskeyShare(AuditMixin, Base):
    """The passkey sent to an Admin inside the app: encrypted with the master key, shown to that
    Admin once, then deleted (or after 7 days)."""

    __tablename__ = "passkey_shares"

    id = record_no()
    product_id = Column(Integer, nullable=False, index=True)
    user_id = Column(Integer, nullable=False, index=True)
    sealed = Column(Text, nullable=True)  # Fernet(master key); NULL once revealed
    passkey_version = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    revealed_at = Column(DateTime, nullable=True)


class PlaybackToken(AuditMixin, Base):
    """A short-lived permission to stream ONE recording, for ONE user (only its hash is stored)."""

    __tablename__ = "playback_tokens"

    id = record_no()
    token_hash = Column(String(64), nullable=False, unique=True, index=True)  # sha256 hex
    user_id = Column(Integer, nullable=False)
    feedback_id = Column(Integer, nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class RecordingAccessLog(AuditMixin, Base):
    """Every attempt to play a recording: who, which recording, when and the outcome."""

    __tablename__ = "recording_access_log"

    id = record_no()
    user_id = Column(Integer, nullable=True, index=True)
    user_name = Column(String(150), nullable=True)
    role = Column(String(20), nullable=True)
    product_id = Column(Integer, nullable=True, index=True)
    feedback_id = Column(Integer, nullable=True, index=True)
    response_id = Column(Integer, nullable=True)
    # granted | wrong_passkey | not_authorised | locked | no_passkey | played
    outcome = Column(String(30), nullable=False, index=True)
    ip = Column(String(64), nullable=True)
    at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)


# ============================================================
# RECIPE CODES: every saved recipe version gets the next RCP-0001, RCP-0002, ...
# ============================================================
RECIPE_CODE_PREFIX = "RCP-"


def next_recipe_code(connection, taken=()) -> str:
    """The next free RCP-#### (one higher than the highest one in use or in `taken`)."""
    from sqlalchemy import select

    highest = 0
    stored = connection.execute(select(RecipeVersion.recipe_code).where(RecipeVersion.recipe_code.like(f"{RECIPE_CODE_PREFIX}%"))).scalars()
    for value in [*stored, *taken]:
        digits = (value or "")[len(RECIPE_CODE_PREFIX):]
        if digits.isdigit():
            highest = max(highest, int(digits))
    return f"{RECIPE_CODE_PREFIX}{highest + 1:04d}"


def _set_recipe_code(mapper, connection, target):  # noqa: ARG001 (SQLAlchemy event signature)
    if (target.recipe_code or "").strip():
        return
    from sqlalchemy.orm import object_session

    session = object_session(target)
    taken = session.info.setdefault("recipe_codes_given", set()) if session is not None else set()
    target.recipe_code = next_recipe_code(connection, taken)
    taken.add(target.recipe_code)


event.listen(RecipeVersion, "before_insert", _set_recipe_code)


PLAN_CODE_PREFIX = "PLA"


def next_plan_code(connection, project_id, product_id, recipe_version, taken=()) -> str:
    """The lowest free PLA### within the plan's project (plans without a project: within their
    product and recipe version). A deleted plan's number is given again."""
    from sqlalchemy import text as _text

    if project_id:
        rows = connection.execute(_text("SELECT plan_code FROM action_plans WHERE project_id = :p"), {"p": project_id})
    else:
        rows = connection.execute(_text("SELECT plan_code FROM action_plans WHERE project_id IS NULL AND product_id = :p "
                                        "AND (recipe_version = :v OR (recipe_version IS NULL AND :v IS NULL))"),
                                  {"p": product_id, "v": recipe_version})
    used = set()
    for code in [*(r[0] for r in rows), *taken]:
        digits = (code or "")[len(PLAN_CODE_PREFIX):]
        if (code or "").startswith(PLAN_CODE_PREFIX) and digits.isdigit():
            used.add(int(digits))
    n = 1
    while n in used:
        n += 1
    return f"{PLAN_CODE_PREFIX}{n:03d}"


def _set_plan_code(mapper, connection, target):  # noqa: ARG001 (SQLAlchemy event signature)
    if (target.plan_code or "").strip():
        return
    from sqlalchemy.orm import object_session

    session = object_session(target)
    scope = target.project_id or f"p{target.product_id}v{target.recipe_version}"
    given = session.info.setdefault("plan_codes_given", {}) if session is not None else {}
    taken = given.setdefault(scope, set())
    target.plan_code = next_plan_code(connection, target.project_id, target.product_id, target.recipe_version, taken)
    taken.add(target.plan_code)
    if not (target.name or "").strip():  # the plan's name is its code
        target.name = target.plan_code


event.listen(ActionPlan, "before_insert", _set_plan_code)