"""
Backend -> AI Model Service, over HTTP.

The backend no longer loads Whisper or calls Ollama itself. Every AI step goes
through the AI service (ai_service/main.py), which runs on its own port.

Settings (environment variables):
    AI_SERVICE_URL       default http://127.0.0.1:8001
                         (Docker, same container: http://127.0.0.1:8001;
                          Docker, separate container: http://ai:8001)
    AI_SERVICE_TOKEN     optional shared secret, must match the AI service
    AI_SERVICE_TIMEOUT   seconds to wait for one recording's analysis (default 3600;
                         a 35-minute recording on a CPU can take a long time)
    AI_CONNECT_RETRIES   how many times to retry while the AI service is starting
                         up or restarting (default 10, 3 seconds apart)
"""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path
from typing import Optional

import httpx

AI_SERVICE_URL = (os.environ.get("AI_SERVICE_URL", "").strip() or "http://127.0.0.1:8001").rstrip("/")
AI_SERVICE_TOKEN = os.environ.get("AI_SERVICE_TOKEN", "").strip()
ANALYSIS_TIMEOUT = float(os.environ.get("AI_SERVICE_TIMEOUT", "3600") or 3600)
CONNECT_RETRIES = max(0, int(os.environ.get("AI_CONNECT_RETRIES", "10") or 10))
RETRY_PAUSE = 3.0


class AIServiceError(Exception):
    """The AI service answered with an error, or couldn't be reached."""

    def __init__(self, status_code: int, detail: str, code: Optional[str] = None):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.code = code


def _headers() -> dict:
    return {"X-AI-Token": AI_SERVICE_TOKEN} if AI_SERVICE_TOKEN else {}


def _error_from(response: httpx.Response) -> AIServiceError:
    try:
        body = response.json()
        detail = body.get("detail") or response.text
        code = body.get("code")
    except Exception:
        detail, code = response.text or f"AI service error {response.status_code}", None
    if not isinstance(detail, str):
        detail = str(detail)
    return AIServiceError(response.status_code, detail, code)


def _unreachable(error: Exception) -> AIServiceError:
    return AIServiceError(
        503,
        f"The AI service at {AI_SERVICE_URL} isn't reachable ({error.__class__.__name__}). "
        "Check that it is running.",
        "ai_unreachable",
    )


def _timeout(seconds: float) -> httpx.Timeout:
    return httpx.Timeout(seconds, connect=10.0)


# ============================================================
# Recording analysis (used by /video/upload and the worker)
# ============================================================

async def analyze_media(path: str | Path, filename: str) -> dict:
    """
    Send one saved recording to the AI service and return its raw results:
    duration, per-part Whisper transcript + Qwen summary, overall summary.
    """
    path = Path(path)
    attempt = 0
    async with httpx.AsyncClient(timeout=_timeout(ANALYSIS_TIMEOUT), headers=_headers()) as client:
        while True:
            try:
                with open(path, "rb") as handle:
                    response = await client.post(
                        f"{AI_SERVICE_URL}/v1/analyze/media",
                        files={"file": (filename, handle, "application/octet-stream")},
                    )
                break
            except httpx.ConnectError as e:
                # Only retry when nothing was sent yet (service starting / restarting).
                attempt += 1
                if attempt > CONNECT_RETRIES:
                    raise _unreachable(e)
                print(f"AI service not reachable yet, retrying in {RETRY_PAUSE:.0f}s ({attempt}/{CONNECT_RETRIES})...")
                await asyncio.sleep(RETRY_PAUSE)
            except httpx.TimeoutException:
                raise AIServiceError(
                    504,
                    f"The AI analysis took longer than {ANALYSIS_TIMEOUT:.0f} seconds.",
                    "ai_timeout",
                )
            except httpx.HTTPError as e:
                raise _unreachable(e)

    if response.status_code != 200:
        raise _error_from(response)
    return response.json()


# ============================================================
# Text feedback analysis (CSV / Excel upload)
# ============================================================

async def analyze_feedback(feedback) -> dict:
    """Same result as ollama_service.analyze_feedback(feedback), computed by the AI service."""
    try:
        async with httpx.AsyncClient(timeout=_timeout(600), headers=_headers()) as client:
            response = await client.post(f"{AI_SERVICE_URL}/v1/analyze/feedback", json={"feedback": feedback})
    except httpx.TimeoutException:
        raise AIServiceError(504, "The AI analysis took too long.", "ai_timeout")
    except httpx.HTTPError as e:
        raise _unreachable(e)
    if response.status_code != 200:
        raise _error_from(response)
    return response.json()["analysis"]


# ============================================================
# Plain LLM text (consolidated reports, survey-answer translation)
# ============================================================

def llm_generate(prompt: str, num_predict: int, json_mode: bool = True, timeout: int = 300) -> str:
    """Synchronous: used by report code that runs in the worker and in API threads."""
    body = {"prompt": prompt, "num_predict": num_predict, "json_mode": json_mode, "timeout": timeout}
    try:
        response = httpx.post(
            f"{AI_SERVICE_URL}/v1/llm/generate",
            json=body,
            headers=_headers(),
            timeout=_timeout(timeout + 30),
        )
    except httpx.TimeoutException:
        raise AIServiceError(504, "The language model took too long to answer.", "ai_timeout")
    except httpx.HTTPError as e:
        raise _unreachable(e)
    if response.status_code != 200:
        raise _error_from(response)
    return response.json().get("response", "")


# ============================================================
# Health
# ============================================================

def health(timeout: float = 3.0) -> dict:
    try:
        response = httpx.get(f"{AI_SERVICE_URL}/health", headers=_headers(), timeout=timeout)
        if response.status_code == 200:
            return response.json()
        return {"status": "error", "http_status": response.status_code}
    except Exception as e:
        return {"status": "unreachable", "error": e.__class__.__name__}


def wait_until_ready(max_seconds: float = 0) -> bool:
    """Optional: block until the AI service answers /health (used by tools, not the API)."""
    deadline = time.time() + max_seconds
    while True:
        if health().get("status") == "healthy":
            return True
        if time.time() >= deadline:
            return False
        time.sleep(2)
