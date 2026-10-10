from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.upload import router as upload_router
from app.api.video import router as video_router
from app.api.feedback import router as feedback_router
from app.api.catalog import router as catalog_router
from app.api.surveys import router as surveys_router
from app.api.product_feedback import router as product_feedback_router
from app.api.auth import router as auth_router
from app.api.admin import router as admin_router
from app.api.analysis import router as analysis_router, reanalyze_router
from app.api.recipes import router as recipes_router
from app.api.access import install_access_control
from app.services import analysis_worker
from app.database.migrations import run_migrations


# ============================================================
# DATABASE
# ============================================================

# Creates new tables and adds new columns to existing ones (safe to repeat).
run_migrations()


# ============================================================
# FASTAPI APPLICATION
# ============================================================

app = FastAPI(
    title="FMCG Video Learning Engine API",
    version="1.1.0",
    description=(
        "AI-powered FMCG customer feedback "
        "video analysis platform."
    )
)


# ============================================================
# CORS
# ============================================================

# Sign-in and role checks for every endpoint (before CORS, so CORS stays outermost
# and error responses still carry CORS headers for the browser).
install_access_control(app)

app.add_middleware(
    CORSMiddleware,

    allow_origins=[
        "http://localhost:5173",
        "https://localhost",
    ],

    allow_credentials=True,

    allow_methods=["*"],

    allow_headers=["*"],
)


# ============================================================
# API ROUTERS
# ============================================================

app.include_router(
    upload_router
)

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

        "version": "1.1.0"
    }


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/health")
def health():

    return {
        "status": "healthy",

        "service": "FastAPI",

        "ollama": "connected"
    }