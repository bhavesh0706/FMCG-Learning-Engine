"""
Backend configuration, read once at startup.

Values come from (highest priority first):
    1. real environment variables (PowerShell $env:..., Docker, the hosting platform)
    2. backend/.env          (this computer / this server; never committed)
    3. the defaults below    (same behaviour as before this file existed)

Import this module before anything that reads os.environ at import time
(database, AI client, worker), so values from backend/.env are visible there.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = BACKEND_DIR / ".env"

# override=False: a variable already set in the environment wins over the file.
load_dotenv(ENV_FILE, override=False)


class ConfigError(RuntimeError):
    """A setting in backend/.env (or the environment) is invalid."""


def _str(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value or default


def _bool(name: str, default: bool) -> bool:
    value = os.environ.get(name, "").strip().lower()
    if not value:
        return default
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{name} must be true or false, got {value!r}.")


def _port(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        port = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got {raw!r}.") from None
    if not 1 <= port <= 65535:
        raise ConfigError(f"{name} must be between 1 and 65535, got {port}.")
    return port


def _origins(name: str, default: str) -> list[str]:
    """Comma-separated browser origins, e.g. http://localhost:5173 (no path, no trailing slash)."""
    origins = []
    for item in _str(name, default).split(","):
        origin = item.strip()
        if not origin:
            continue
        if origin == "*":
            raise ConfigError(
                f"{name} can't be '*': the API sends credentials, so browsers require exact origins."
            )
        parsed = urlparse(origin)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ConfigError(f"{name}: {origin!r} must look like http://host:port.")
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ConfigError(f"{name}: {origin!r} must be an origin only (no path).")
        origins.append(f"{parsed.scheme}://{parsed.netloc}")  # drops a trailing slash
    if not origins:
        raise ConfigError(f"{name} must list at least one origin.")
    return origins


# ------------------------------------------------------------
# Settings
# ------------------------------------------------------------

APP_ENV = _str("APP_ENV", "development").lower()
if APP_ENV not in ("development", "staging", "production"):
    raise ConfigError(f"APP_ENV must be development, staging or production, got {APP_ENV!r}.")

# Where the backend API listens (used by run.py).
BACKEND_HOST = _str("BACKEND_HOST", "127.0.0.1")
BACKEND_PORT = _port("BACKEND_PORT", 8000)

# Auto-restart on code changes: on by default only in development.
BACKEND_RELOAD = _bool("BACKEND_RELOAD", APP_ENV == "development")

# Browser origins allowed to call the API (the frontend's address).
# localhost and 127.0.0.1 are different origins for the browser, so list both.
CORS_ORIGINS = _origins("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
