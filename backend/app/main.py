# Must be the first import: loads backend/.env before other modules read settings.
from app import config

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.video import router as video_router
from app.api.feedback import router as feedback_router
from app.api.catalog import router as catalog_router
from app.api.surveys import router as surveys_router
from app.api.product_feedback import router as product_feedback_router
from app.api.auth import router as auth_router
from app.api.admin import router as admin_router
from app.api.analysis import router as analysis_router, reanalyze_router
from app.api.recipes import router as recipes_router
from app.api.question_templates import router as question_templates_router
from app.api.notifications import router as notifications_router
from app.api.org import router as org_router
from app.api.rounds import router as rounds_router
from app.api.geo import router as geo_router
from app.api.product_checks import router as product_checks_router
from app.api.recipe_master import router as recipe_master_router
from app.api.masters import router as masters_router
from app.api.survey_forms import router as survey_forms_router
from app.api.report_reviews import router as report_reviews_router
from app.api.report_exports import router as report_exports_router
from app.api.custom_reports import router as custom_reports_router
from app.api.access import install_access_control
from app.api.observability import install_observability
from app.services.audit import install_audit
from app.services import analysis_worker
from app.services import ai_client
from app.database.database import engine
from app.database.migrations import run_migrations
from sqlalchemy import text


# ============================================================
# DATABASE
# ============================================================

# Creates new tables and adds new columns to existing ones (safe to repeat).
run_migrations()


# ============================================================
# FASTAPI APPLICATION
# ============================================================

# Interactive API docs only outside production (they list every route and schema).
_DOCS = config.APP_ENV != "production"

app = FastAPI(
    title="FMCG Video Learning Engine API",
    version="1.2.0",
    description=(
        "AI-powered FMCG customer feedback "
        "video analysis platform."
    ),
    docs_url="/docs" if _DOCS else None,
    redoc_url="/redoc" if _DOCS else None,
    openapi_url="/openapi.json" if _DOCS else None,
)


# ============================================================
# CORS
# ============================================================

# Sign-in and role checks for every endpoint (before CORS, so CORS stays outermost
# and error responses still carry CORS headers for the browser).
install_access_control(app)

# Audit log: one entry per successful change (runs outside access control, so it
# sees the signed-in account).
install_audit(app)

# Request IDs, one JSON access-log line per request, API security headers.
install_observability(app)


# Unexpected server errors: a JSON answer that passes through CORS (registered before
# CORSMiddleware, so it sits inside it). Without this the browser only says
# "Network Error". The traceback goes to the backend log; outside production the
# message includes the error itself, so it can be reported and fixed.
@app.middleware("http")
async def errors_as_json(request, call_next):
    try:
        return await call_next(request)
    except Exception as error:  # noqa: BLE001 (last-resort handler)
        import logging

        from fastapi.responses import JSONResponse

        logging.getLogger("fmcg.errors").exception("Unhandled error on %s %s", request.method, request.url.path)
        detail = "Server error. Please try again."
        if config.APP_ENV != "production":
            detail = f"Server error on {request.url.path}: {type(error).__name__}: {str(error)[:400]}"
        return JSONResponse(status_code=500, content={"detail": detail})


app.add_middleware(
    CORSMiddleware,

    # The frontend's address(es), from CORS_ORIGINS in backend/.env.
    allow_origins=config.CORS_ORIGINS,

    allow_credentials=True,

    allow_methods=["*"],

    allow_headers=["*"],

    # Downloads keep their names (Excel, PowerPoint, attachments): the browser may read this header
    expose_headers=["Content-Disposition"],
)


# ============================================================
# API ROUTERS
# ============================================================

app.include_router(
    video_router
)

app.include_router(
    feedback_router
)

# Categories, products and survey questions
app.include_router(
    catalog_router
)

app.include_router(
    surveys_router
)

# Product-wise feedback history and consolidated reports
app.include_router(
    product_feedback_router
)

# Accounts for the mobile app (sign up, log in)
app.include_router(
    auth_router
)

# Team management (web "Team" page)
app.include_router(
    admin_router
)

# Analysis queue: uploads return at once and are analyzed in the background
app.include_router(
    analysis_router
)

# Re-analysis of an existing recording: POST /feedback/{id}/reanalyze
app.include_router(
    reanalyze_router
)

# Product recipes (used by consolidated reports)
app.include_router(
    recipes_router
)

# Question templates: reusable starting questions for new products (stored in MySQL)
app.include_router(
    question_templates_router
)

# Notification bell (web dashboard and mobile app)
app.include_router(
    notifications_router
)

# Organization portal (the company whose products are surveyed)
app.include_router(
    org_router
)

# Survey rounds in the admin dashboard
app.include_router(
    rounds_router
)

# GPS position -> readable "Survey location" (web and mobile app)
app.include_router(
    geo_router
)

# Product checks: feedback about another product than the one selected (admin review)
app.include_router(
    product_checks_router
)

# Next update: Recipe Master, master lists, survey form approval, report review chain, exports
for _router in (recipe_master_router, masters_router, survey_forms_router, report_reviews_router, report_exports_router,
                custom_reports_router):
    app.include_router(_router)


# Start the background analysis worker (works on every FastAPI version).
analysis_worker.start()


# ============================================================
# ROOT
# ============================================================

@app.get("/")
def root():

    return {
        "message": (
            "FMCG Video Learning Engine "
            "API is running"
        ),

        "status": "healthy",

        "version": "1.2.0"
    }


def _database_ok() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@app.get("/health/ready")
def ready():
    """Readiness for load balancers and Docker: 200 only when the database answers."""
    from fastapi.responses import JSONResponse

    if _database_ok():
        return {"status": "ready", "database": "ok"}
    return JSONResponse(status_code=503, content={"status": "not ready", "database": "unreachable"})


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/health")
def health():

    # The AI Model Service runs on its own port; report whether it answers.
    ai = ai_client.health()
    database = "ok" if _database_ok() else "unreachable"
    ai_ok = ai.get("status") in ("ok", "healthy")

    return {
        "status": "healthy" if database == "ok" and ai_ok else "degraded",

        "database": database,

        "service": "FastAPI",

        "ollama": ai.get("ollama", "unknown"),

        "ai_service": {
            "status": ai.get("status"),
            "whisper_model": ai.get("whisper_model"),
            "llm_model": ai.get("llm_model"),
            "busy": ai.get("busy"),
        },
    }
