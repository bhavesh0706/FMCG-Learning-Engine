"""
FMCG AI Model Service: Whisper + a language model (Ollama/Qwen or OpenAI) behind its own API and port.

The backend calls this service over HTTP; it never loads the models itself.
This service never touches MySQL.

Run (from the ai_service folder):
    cd ai_service
    uvicorn main:app --host 0.0.0.0 --port 8001

Endpoints
    GET  /health                 is the service up, which models, which LLM provider, is Ollama reachable
    POST /v1/analyze/media       multipart "file" -> full recording analysis (raw AI results)
    POST /v1/analyze/feedback    {"feedback": "..." | ["...", ...]} -> overall Qwen analysis
    POST /v1/llm/generate        {"prompt", "num_predict", "json_mode", "timeout"} -> {"response"}
                                 (consolidated reports and survey-answer translation)

Settings (environment variables)
    AI_SERVICE_TOKEN      optional shared secret; when set, callers must send
                          the header  X-AI-Token: <value>
    AI_MAX_CONCURRENT     analyses run at the same time in this process (default 1,
                          like one worker before). For more, start uvicorn with
                          --workers N (each worker loads its own Whisper model).
    AI_WORK_DIR           folder for temporary uploads and audio parts
                          (default: ai_service/work)
    USE_OLLAMA            true (default) = local Ollama, false = OpenAI
    OPENAI_API_KEY        required when USE_OLLAMA=false
    OPENAI_MODEL          default gpt-4o-mini
    OLLAMA_URL            default http://127.0.0.1:11434/api/generate
    FMCG_REPORT_MODEL, FMCG_OLLAMA_KEEP_ALIVE, FMCG_WHISPER_MODEL, FMCG_LANGUAGES,
    FMCG_BRAND_HINTS, FMCG_DENOISE, ... work exactly as before.
"""

from __future__ import annotations

import hmac
import os
import shutil
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import List, Optional, Union

import requests
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel

AI_SERVICE_DIR = Path(__file__).resolve().parent


def _load_env_file(path: Path) -> None:
    """Read KEY=VALUE lines from ai_service/.env. Real environment variables win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        os.environ.setdefault(key, value)


_load_env_file(AI_SERVICE_DIR / ".env")

WORK_DIR = Path(os.environ.get("AI_WORK_DIR") or (AI_SERVICE_DIR / "work"))
INCOMING_DIR = WORK_DIR / "incoming"
AUDIO_DIR = WORK_DIR / "audio"
for _d in (INCOMING_DIR, AUDIO_DIR):
    _d.mkdir(parents=True, exist_ok=True)

TOKEN = os.environ.get("AI_SERVICE_TOKEN", "").strip()
# In production the shared token is required: without it anyone who can reach
# port 8001 could run analyses or use /v1/llm/generate as a free LLM endpoint.
if os.environ.get("APP_ENV", "development").strip().lower() == "production" and not TOKEN:
    raise SystemExit(
        "AI_SERVICE_TOKEN is required when APP_ENV=production. Set the same long random "
        "value for the backend and the AI service (see .env.example)."
    )
MAX_CONCURRENT = max(1, int(os.environ.get("AI_MAX_CONCURRENT", "1") or 1))

# Loading these imports loads Whisper once, at startup (not on the first upload).
from services import ollama_service  # noqa: E402
from services.ollama_service import analyze_feedback  # noqa: E402
from services.whisper_service import MODEL_NAME as WHISPER_MODEL  # noqa: E402

from pipeline import MediaError, analyze_media  # noqa: E402

# Report / translation model settings (moved here from consolidation.py).
REPORT_MODEL = os.environ.get("FMCG_REPORT_MODEL", "").strip() or None
KEEP_ALIVE = os.environ.get("FMCG_OLLAMA_KEEP_ALIVE", "30m")

_slots = threading.BoundedSemaphore(MAX_CONCURRENT)
_busy = 0
_busy_lock = threading.Lock()

app = FastAPI(
    title="FMCG AI Model Service",
    version="1.0.0",
    description="Whisper transcription and Qwen analysis for the FMCG Video Learning Engine.",
)


def check_token(x_ai_token: Optional[str] = Header(default=None)):
    if TOKEN and not hmac.compare_digest((x_ai_token or "").encode(), TOKEN.encode()):
        raise HTTPException(status_code=401, detail="Invalid or missing AI service token.")


class _Slot:
    """One analysis at a time per slot; others wait their turn (like the old queue)."""

    def __enter__(self):
        global _busy
        _slots.acquire()
        with _busy_lock:
            _busy += 1

    def __exit__(self, *exc):
        global _busy
        with _busy_lock:
            _busy -= 1
        _slots.release()


# ============================================================
# HEALTH
# ============================================================

@app.get("/health")
def health():
    if ollama_service.USE_OLLAMA:
        ollama_ok = False
        try:
            base = ollama_service.OLLAMA_URL.split("/api/")[0]
            ollama_ok = requests.get(f"{base}/api/tags", timeout=3).ok
        except Exception:
            pass
        ollama_state = "connected" if ollama_ok else "unreachable"
        report_model = REPORT_MODEL or ollama_service.MODEL
    else:
        ollama_state = "not used (OpenAI)"
        report_model = ollama_service.MODEL
    return {
        "status": "healthy",
        "service": "ai",
        "whisper_model": WHISPER_MODEL,
        "llm_provider": ollama_service.PROVIDER,
        "llm_model": ollama_service.MODEL,
        "report_model": report_model,
        "ollama": ollama_state,
        "busy": _busy,
        "max_concurrent": MAX_CONCURRENT,
    }


# ============================================================
# RECORDING ANALYSIS
# ============================================================

# A plain "def": FastAPI runs it in a thread, so /health keeps answering meanwhile.
@app.post("/v1/analyze/media", dependencies=[Depends(check_token)])
def analyze_media_endpoint(file: UploadFile = File(...)):
    safe_name = Path(file.filename or "recording").name
    stored = INCOMING_DIR / f"{uuid.uuid4().hex[:12]}_{safe_name}"
    try:
        with open(stored, "wb") as out:
            shutil.copyfileobj(file.file, out, length=1024 * 1024)

        print("\n" + "=" * 70)
        print(f"AI SERVICE: NEW ANALYSIS  {safe_name}")
        print("=" * 70)

        with _Slot():
            started = time.time()
            result = analyze_media(stored, AUDIO_DIR)
        print(f"AI SERVICE: analysis finished in {time.time() - started:.0f}s.")
        return result

    except MediaError as e:
        return JSONResponse(status_code=e.status_code, content={"detail": e.message, "code": e.code})

    except Exception as error:
        print("\n" + "=" * 70)
        print("AI SERVICE: PROCESSING ERROR")
        print("=" * 70)
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"detail": str(error) or error.__class__.__name__, "code": "analysis_failed"})

    finally:
        try:
            file.file.close()
        except Exception:
            pass
        stored.unlink(missing_ok=True)


# ============================================================
# TEXT FEEDBACK ANALYSIS (CSV / Excel upload)
# ============================================================

class FeedbackIn(BaseModel):
    feedback: Union[str, List[str]]


@app.post("/v1/analyze/feedback", dependencies=[Depends(check_token)])
def analyze_feedback_endpoint(body: FeedbackIn):
    try:
        with _Slot():
            return {"analysis": analyze_feedback(body.feedback)}
    except Exception as error:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(error) or error.__class__.__name__)


# ============================================================
# GENERIC LLM CALL (reports, translation of survey answers)
# ============================================================

class GenerateIn(BaseModel):
    prompt: str
    num_predict: int = 700
    json_mode: bool = True
    timeout: int = 300


@app.post("/v1/llm/generate", dependencies=[Depends(check_token)])
def llm_generate(body: GenerateIn):
    # Ollama or OpenAI, depending on USE_OLLAMA (see services/ollama_service.py).
    try:
        text = ollama_service.call_llm(
            prompt=body.prompt,
            num_predict=body.num_predict,
            temperature=0.1,
            timeout=body.timeout,
            json_mode=body.json_mode,
            ollama_model=REPORT_MODEL,
            ollama_extra={"keep_alive": KEEP_ALIVE, "options": {"num_ctx": 4096}},
        )
        return {"response": text}
    except requests.Timeout:
        raise HTTPException(status_code=504, detail="The language model took too long to answer.")
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"Language model error: {error}")
