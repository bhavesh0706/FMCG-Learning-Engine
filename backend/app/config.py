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
import re
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
        is_capacitor_origin = origin == "capacitor://localhost"
        if not is_capacitor_origin and (
            parsed.scheme not in ("http", "https") or not parsed.netloc
        ):
            raise ConfigError(f"{name}: {origin!r} must be a valid HTTP(S) or Capacitor origin.")
        if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ConfigError(f"{name}: {origin!r} must be an origin only (no path).")
        origins.append(f"{parsed.scheme}://{parsed.netloc}")  # drops a trailing slash
    if not origins:
        raise ConfigError(f"{name} must list at least one origin.")
    return origins


def _first(*names: str) -> tuple[str, str] | tuple[None, None]:
    """The first of these variables that is set: (name, value). Used for name aliases."""
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return name, value
    return None, None


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


# ------------------------------------------------------------
# MySQL database (read by app/database/database.py)
# ------------------------------------------------------------
# Names: MYSQL_* (also used by docker-compose). The older FMCG_DB_* names are accepted
# too, so an existing backend/.env keeps working. If both are set, MYSQL_* wins.
#   MYSQL_HOST=127.0.0.1        (Docker: the MySQL container's name, e.g. mysql)
#   MYSQL_PORT=3306
#   MYSQL_DATABASE=fmcg_learning
#   MYSQL_USER=root
#   MYSQL_PASSWORD=...          required: there is deliberately no default password

_name, MYSQL_HOST = _first("MYSQL_HOST", "FMCG_DB_HOST")
MYSQL_HOST = MYSQL_HOST or "127.0.0.1"

_name, _raw_port = _first("MYSQL_PORT", "FMCG_DB_PORT")
if _raw_port:
    try:
        MYSQL_PORT = int(_raw_port)
    except ValueError:
        raise ConfigError(f"{_name} must be a number, got {_raw_port!r}.") from None
    if not 1 <= MYSQL_PORT <= 65535:
        raise ConfigError(f"{_name} must be between 1 and 65535, got {MYSQL_PORT}.")
else:
    MYSQL_PORT = 3306

_name, MYSQL_DATABASE = _first("MYSQL_DATABASE", "FMCG_DB_NAME")
MYSQL_DATABASE = MYSQL_DATABASE or "fmcg_learning"
if not re.fullmatch(r"[A-Za-z0-9_]+", MYSQL_DATABASE):
    raise ConfigError(f"{_name or 'MYSQL_DATABASE'} may only contain letters, digits and _, got {MYSQL_DATABASE!r}.")

_name, MYSQL_USER = _first("MYSQL_USER", "FMCG_DB_USER")
MYSQL_USER = MYSQL_USER or "root"

# The password is NOT stripped (spaces could be part of it) and has no default.
MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD") or os.environ.get("FMCG_DB_PASSWORD") or ""
if not MYSQL_PASSWORD:
    raise ConfigError(
        f"MYSQL_PASSWORD is not set. Add this line to {ENV_FILE} (with your MySQL password):\n"
        "    MYSQL_PASSWORD=your-mysql-password"
    )

del _name, _raw_port