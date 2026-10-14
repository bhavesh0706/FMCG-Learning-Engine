# Code reference: environment configuration

Every file changed for the port / API URL / CORS configuration. New and small files are shown in full. The three large UI files only had error-message text changed, so they are shown as exact diffs (`-` removed, `+` added); their complete versions are in the zip.

**Delete:** `frontend/src/api.js` (unused, pointed at the old port 8001) and `frontend/capacitor.config.json` if it still exists.

## `backend/app/config.py`

```python
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
```

## `backend/run.py`

```python
"""
Start the backend API on the host and port from backend/.env.

    cd backend
    python run.py

Settings: BACKEND_HOST, BACKEND_PORT, BACKEND_RELOAD (see .env.example).
If the port is already in use, this stops with a clear message instead of
starting on a different port.
"""

import os
import socket
import sys

import uvicorn

from app import config


def port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
            return True
        except OSError:
            return False


def main() -> int:
    # Run from the backend folder, so "app.main" is found from any starting folder.
    os.chdir(config.BACKEND_DIR)
    host, port = config.BACKEND_HOST, config.BACKEND_PORT

    if not port_is_free(host, port):
        print(
            f"ERROR: port {port} on {host} is already in use.\n"
            f"Stop the program using it, or change BACKEND_PORT in backend/.env "
            f"(and VITE_API_BASE_URL in frontend/.env.local to match).",
            file=sys.stderr,
        )
        return 1

    print(f"Backend ({config.APP_ENV}) starting on http://{host}:{port}")
    print(f"Allowed frontend origins: {', '.join(config.CORS_ORIGINS)}")

    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=config.BACKEND_RELOAD,
        # Only watch the code, not uploaded recordings or the analysis queue.
        reload_dirs=[str(config.BACKEND_DIR / "app")] if config.BACKEND_RELOAD else None,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

## `backend/app/main.py`

```python
# Must be the first import: loads backend/.env before other modules read settings.
from app import config

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
from app.api.question_templates import router as question_templates_router
from app.api.access import install_access_control
from app.services import analysis_worker
from app.services import ai_client
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

    # The frontend's address(es), from CORS_ORIGINS in backend/.env.
    allow_origins=config.CORS_ORIGINS,

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

# Question templates: reusable starting questions for new products (stored in MySQL)
app.include_router(
    question_templates_router
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

    # The AI Model Service runs on its own port; report whether it answers.
    ai = ai_client.health()

    return {
        "status": "healthy",

        "service": "FastAPI",

        "ollama": ai.get("ollama", "unknown"),

        "ai_service": {
            "status": ai.get("status"),
            "whisper_model": ai.get("whisper_model"),
            "llm_model": ai.get("llm_model"),
            "busy": ai.get("busy"),
        },
    }
```

## `backend/app/worker.py`

```python
"""
Analysis worker process.

Started automatically by the API (FMCG_WORKER_MODE=auto, the default), or by hand:
    cd backend
    python -m app.worker              # one worker
    python -m app.worker --index 1    # a second one (only worker 0 also translates)

Runs separately from the API, so analyses never slow down the web or the app.
"""

import argparse

from app import config  # noqa: F401  (loads backend/.env first, also when started by hand)
from app.database.migrations import run_migrations
from app.services.analysis_worker import worker_main

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="FMCG analysis worker")
    parser.add_argument("--index", type=int, default=0, help="worker number (0 also translates survey answers)")
    args = parser.parse_args()
    run_migrations()  # safe to repeat; makes sure the job table is up to date
    try:
        worker_main(args.index)
    except KeyboardInterrupt:
        # Ctrl+C in the uvicorn window, or a --reload restart: stop quietly.
        print(f"Analysis worker {args.index} stopped.", flush=True)
```

## `frontend/src/config.js`

```javascript
// Frontend configuration: the ONE place that knows where the backend is.
//
// VITE_API_BASE_URL comes from frontend/.env.local (development) or
// .env.<mode>.local / the build environment (staging, production).
// vite.config.js checks it before the dev server starts or a build runs.
//
// Only VITE_* values reach this code, and they end up inside the JavaScript
// sent to every browser: never put passwords, keys or tokens in them.

const raw = import.meta.env.VITE_API_BASE_URL;

if (!raw) {
  throw new Error(
    "VITE_API_BASE_URL is not set. Add it to frontend/.env.local and restart `npm run dev`."
  );
}

// Without a trailing slash, so `${API_BASE_URL}/feedback/1` is always correct.
export const API_BASE_URL = raw.trim().replace(/\/+$/, "");
```

## `frontend/vite.config.js`

```javascript
import react from '@vitejs/plugin-react'
import { defineConfig, loadEnv } from 'vite'

// https://vite.dev/config/
//
// Settings come from frontend/.env.local (development) or .env.<mode>.local
// (e.g. `vite build --mode staging`). See .env.example.
//   VITE_PORT          port of the dev server (npm run dev)
//   VITE_API_BASE_URL  address of the backend API used by the browser

function readPort(env) {
  const raw = (env.VITE_PORT || '5173').trim()
  const port = Number(raw)
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    throw new Error(`VITE_PORT must be a number between 1 and 65535, got "${raw}".`)
  }
  return port
}

function checkApiBaseUrl(env, mode) {
  const value = (env.VITE_API_BASE_URL || '').trim()
  if (!value) {
    throw new Error(
      `VITE_API_BASE_URL is not set for mode "${mode}". ` +
        'Add it to frontend/.env.local (development) or .env.<mode>.local.',
    )
  }
  let url
  try {
    url = new URL(value)
  } catch {
    throw new Error(`VITE_API_BASE_URL must be a full URL like http://127.0.0.1:8000, got "${value}".`)
  }
  if (url.protocol !== 'http:' && url.protocol !== 'https:') {
    throw new Error(`VITE_API_BASE_URL must start with http:// or https://, got "${value}".`)
  }
}

export default defineConfig(({ mode }) => {
  // '' = read every variable, not only VITE_*, so the check sees the same
  // values Vite will use. Only VITE_* variables are ever sent to the browser.
  const env = loadEnv(mode, process.cwd(), '')
  checkApiBaseUrl(env, mode)

  return {
    plugins: [react()],
    server: {
      port: readPort(env),
      // Stop with an error if the port is taken, instead of silently using the
      // next free one (which the backend's CORS_ORIGINS would not allow).
      strictPort: true,
    },
  }
})
```

## `frontend/package.json`

```json
{
  "name": "frontend",
  "private": true,
  "version": "0.0.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "build:staging": "vite build --mode staging",
    "lint": "eslint .",
    "preview": "vite preview"
  },
  "dependencies": {
    "axios": "^1.20.0",
    "fix-webm-duration": "^1.0.6",
    "jspdf": "^4.2.1",
    "react": "^19.2.8",
    "react-dom": "^19.2.8",
    "react-dropzone": "^20.1.2",
    "react-router-dom": "^7.18.4",
    "recharts": "^3.10.1"
  },
  "devDependencies": {
    "@eslint/js": "^10.0.1",
    "@types/react": "^19.2.18",
    "@types/react-dom": "^19.2.7",
    "@vitejs/plugin-react": "^6.1.1",
    "eslint": "^10.10.0",
    "eslint-plugin-react-hooks": "^7.1.1",
    "eslint-plugin-react-refresh": "^0.5.6",
    "globals": "^17.12.0",
    "vite": "^8.3.0"
  }
}
```

## `frontend/src/services/feedbackApi.js`

```javascript
import axios from "axios";
import { API_BASE_URL } from "../config";

// All calls to the FastAPI backend live here, so App.jsx only deals with the UI.
// The backend address comes from VITE_API_BASE_URL (see src/config.js).
// Re-exported so existing imports from this file keep working.
export { API_BASE_URL };

const api = axios.create({
  baseURL: API_BASE_URL,
});

// ============================================================
// VIDEO / AUDIO FEEDBACK
// ============================================================

// POST /video/upload
// Upload media and optionally attach it to a SurveyResponse.
// onProgress receives the upload percentage (0–100).
export async function uploadVideo(
  file,
  onProgress,
  responseId = null
) {
  const formData = new FormData();

  formData.append("file", file);

  if (responseId !== null && responseId !== undefined) {
    formData.append(
      "response_id",
      String(responseId)
    );
  }

  const res = await api.post(
    "/video/upload",
    formData,
    {
      onUploadProgress: (e) => {
        if (
          e.total &&
          onProgress
        ) {
          onProgress(
            Math.round(
              (e.loaded / e.total) * 100
            )
          );
        }
      },
    }
  );

  return res.data;
}


// GET /feedback -> list of saved analyses
export async function getFeedbackHistory() {
  const res = await api.get("/feedback");

  return res.data?.feedback || [];
}


// GET /feedback/{id} -> one saved analysis
// with summary, chapters and transcript
export async function getFeedbackById(id) {
  const res = await api.get(
    `/feedback/${id}`
  );

  return res.data;
}


// URL for the <video> tag and download link.
// No request is made here.
export function getFeedbackVideoUrl(id) {
  return `${API_BASE_URL}/feedback/${id}/video`;
}


// DELETE /feedback/{id}
export async function deleteFeedback(id) {
  const res = await api.delete(
    `/feedback/${id}`
  );

  return res.data;
}


// ============================================================
// CATALOG
// ============================================================

// GET /categories
export async function getCategories() {
  const res = await api.get(
    "/categories"
  );

  return res.data || [];
}


// GET /products
export async function getProducts() {
  const res = await api.get(
    "/products"
  );

  return res.data || [];
}


// GET /products/{productId}
export async function getProduct(
  productId
) {
  const res = await api.get(
    `/products/${productId}`
  );

  return res.data;
}


// GET /products/{productId}/questions
export async function getProductQuestions(
  productId
) {
  const res = await api.get(
    `/products/${productId}/questions`
  );

  return res.data || [];
}


// ============================================================
// SURVEY RESPONDENT
// ============================================================

// POST /surveys/respondents
export async function createRespondent(
  respondentData
) {
  const res = await api.post(
    "/surveys/respondents",
    respondentData
  );

  return res.data;
}


// GET /surveys/respondents/{respondentId}
export async function getRespondent(
  respondentId
) {
  const res = await api.get(
    `/surveys/respondents/${respondentId}`
  );

  return res.data;
}


// ============================================================
// SURVEY RESPONSE
// ============================================================

// POST /surveys/responses
export async function createSurveyResponse(
  responseData
) {
  const res = await api.post(
    "/surveys/responses",
    responseData
  );

  return res.data;
}


// GET /surveys/responses/{responseId}
export async function getSurveyResponse(
  responseId
) {
  const res = await api.get(
    `/surveys/responses/${responseId}`
  );

  return res.data;
}


// PUT /surveys/responses/{responseId}/answers
export async function saveSurveyAnswers(
  responseId,
  answers
) {
  const res = await api.put(
    `/surveys/responses/${responseId}/answers`,
    {
      answers,
    }
  );

  return res.data;
}


// POST /surveys/responses/{responseId}/submit
export async function submitSurveyResponse(
  responseId
) {
  const res = await api.post(
    `/surveys/responses/${responseId}/submit`
  );

  return res.data;
}
```

## `frontend/src/services/auth.js`

```javascript
import axios from "axios";
import { API_BASE_URL } from "../config";

/*
  Web session: who is signed in, and the login token on every API request.

  This file must be the FIRST import in App.jsx: it equips axios before any
  other API file creates its client, so feedbackApi.js, teamApi.js, etc. all
  send the token without changes. If the server says the session is no longer
  valid (expired, or the account was disabled), the app returns to sign-in.
*/

/*
  The session lives in sessionStorage: it belongs to ONE browser tab.
    - Two tabs can be signed in as two different people (an admin and an executive)
      and stay that way through refreshes; they never take over each other.
    - Opening the app in a new tab or window always asks to sign in.
  Only "recently used accounts" (name, role, mobile/email: never passwords) are kept
  on the device, to make signing in quicker.
*/
const KEY = "fmcg.session";
const RECENT_KEY = "fmcg.recentAccounts";
const listeners = new Set();
let session = load();
let signOutReason = "";

function load() {
  try {
    // One-time clean-up: older versions kept the session for every tab.
    localStorage.removeItem(KEY);
  } catch {
    // ignore
  }
  try {
    const s = JSON.parse(sessionStorage.getItem(KEY) || "null");
    return s?.token && s?.user ? s : null;
  } catch {
    return null;
  }
}

function save(next) {
  session = next;
  try {
    if (next) sessionStorage.setItem(KEY, JSON.stringify(next));
    else sessionStorage.removeItem(KEY);
  } catch {
    // storage blocked: the session lasts until the page is reloaded
  }
  if (next?.user) rememberAccount(next.user);
  listeners.forEach((fn) => fn(session));
}

/* ---------- Recently used accounts on this device (no passwords) ---------- */

export function recentAccounts() {
  try {
    const list = JSON.parse(localStorage.getItem(RECENT_KEY) || "[]");
    return Array.isArray(list) ? list.slice(0, 6) : [];
  } catch {
    return [];
  }
}

function rememberAccount(user) {
  const identifier = user.mobile || user.email;
  if (!identifier) return;
  const entry = { identifier, name: user.name, role: user.role === "admin" ? "Admin" : "Survey person" };
  try {
    const rest = recentAccounts().filter((a) => a.identifier !== identifier);
    localStorage.setItem(RECENT_KEY, JSON.stringify([entry, ...rest].slice(0, 6)));
  } catch {
    // ignore
  }
}

export function forgetAccount(identifier) {
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify(recentAccounts().filter((a) => a.identifier !== identifier)));
  } catch {
    // ignore
  }
}

export const getSession = () => session;
export const getToken = () => session?.token || null;
export const isAdmin = (s = session) => s?.user?.role === "admin";

export function onSessionChange(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

/** Why the last session ended (shown once on the sign-in page). */
export function takeSignOutReason() {
  const r = signOutReason;
  signOutReason = "";
  return r;
}

export function signOut(reason = "") {
  signOutReason = reason;
  save(null);
}

/* ---------- Token on every request, from every axios client ---------- */

function equip(instance) {
  instance.interceptors.request.use((config) => {
    const token = getToken();
    if (token) {
      config.headers = config.headers || {};
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  });
  instance.interceptors.response.use(
    (res) => res,
    (err) => {
      const url = String(err?.config?.url || "");
      if (err?.response?.status === 401 && getToken() && !url.includes("/auth/login") && !url.includes("/auth/setup")) {
        signOut("Your session ended or your account was changed. Please sign in again.");
      }
      return Promise.reject(err);
    }
  );
  return instance;
}

equip(axios);
const originalCreate = axios.create.bind(axios);
axios.create = (...args) => equip(originalCreate(...args));

/** Media (audio/video elements) can't send headers: add the token to the address. */
export function withToken(url) {
  const token = getToken();
  if (!url || !token) return url;
  return `${url}${url.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}`;
}

/* ---------- Sign-in API (base URL passed in, to avoid importing other API files here) ---------- */

function message(err, fallback) {
  const d = err?.response?.data?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d) && d[0]?.msg) return String(d[0].msg).replace("Value error, ", "");
  if (err?.code === "ERR_NETWORK") return `Can't reach the server at ${API_BASE_URL}. Check that the backend is running.`;
  return fallback;
}

export async function getSetupStatus(base) {
  const res = await axios.get(`${base}/auth/setup-status`);
  return res.data;
}

export async function signIn(base, identifier, password) {
  try {
    const res = await axios.post(`${base}/auth/login`, { identifier: identifier.trim(), password });
    save({ token: res.data.token, user: res.data.user });
    return res.data.user;
  } catch (err) {
    throw new Error(message(err, "Sign-in failed. Try again."));
  }
}

export async function createFirstAdmin(base, name, identifier, password) {
  try {
    const res = await axios.post(`${base}/auth/setup`, { name: name.trim(), identifier: identifier.trim(), password });
    save({ token: res.data.token, user: res.data.user });
    return res.data.user;
  } catch (err) {
    throw new Error(message(err, "The admin account couldn't be created."));
  }
}

/** Latest name / role / permissions from the server (an admin may have changed them). */
export async function refreshMe(base) {
  if (!getToken()) return null;
  try {
    const res = await axios.get(`${base}/auth/me`);
    save({ token: getToken(), user: res.data.user });
    return res.data.user;
  } catch {
    return session?.user || null; // offline: keep what we have; 401 already signs out
  }
}
```

## `frontend/src/services/catalogAdminApi.js`

```javascript
import axios from "axios";
import { API_BASE_URL } from "./feedbackApi";
import { announceCatalogChanged } from "./appEvents";

// Catalog management for admins (backend: app/api/catalog.py and app/api/recipes.py).
const api = axios.create({ baseURL: API_BASE_URL });
const data = (p) => p.then((r) => r.data);

// Every successful change tells the whole app (survey lists, Home, reports),
// so no screen keeps an old copy of the catalog. Add new write calls with `change`.
const change = (p) =>
  p.then((r) => {
    announceCatalogChanged();
    return r.data;
  });

// Readable message from any API error.
export function catalogError(err, fallback = "Something went wrong. Try again.") {
  const d = err?.response?.data?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d) && d[0]?.msg) return String(d[0].msg).replace("Value error, ", "");
  if (err?.code === "ERR_NETWORK") return `Can't reach the backend at ${API_BASE_URL}.`;
  return fallback;
}

// Categories
export const listCategories = (includeInactive = true) =>
  data(api.get("/categories", { params: { include_inactive: includeInactive } }));
export const createCategory = (body) => change(api.post("/categories", body));
export const updateCategory = (id, body) => change(api.put(`/categories/${id}`, body));

// Products
export const listProducts = (categoryId, includeInactive = true) =>
  data(api.get("/products", { params: { category_id: categoryId, include_inactive: includeInactive } }));
export const createProduct = (body) => change(api.post("/products", body));
export const updateProduct = (id, body) => change(api.put(`/products/${id}`, body));

// Survey questions
export const listQuestions = (productId) => data(api.get(`/products/${productId}/questions`));
export const createQuestion = (productId, body) => change(api.post(`/products/${productId}/questions`, body));
export const updateQuestion = (id, body) => change(api.put(`/questions/${id}`, body));
export const removeQuestion = (id) => change(api.delete(`/questions/${id}`));
export const reorderQuestions = (productId, ids) =>
  change(api.put(`/products/${productId}/questions/order`, { question_ids: ids }));

// Recipe
export const getRecipe = (productId) => data(api.get(`/products/${productId}/recipe`));
export const saveRecipe = (productId, recipe) => change(api.put(`/products/${productId}/recipe`, recipe));

// Who may survey a product ([] = every executive)
export const getProductExecutives = (productId) => data(api.get(`/admin/products/${productId}/executives`));
export const saveProductExecutives = (productId, userIds) =>
  change(api.put(`/admin/products/${productId}/executives`, { user_ids: userIds }));
export const listTeam = () => data(api.get("/admin/users"));

// Question templates: reusable starting questions for new products (stored in MySQL)
export const listQuestionTemplates = (categoryId) =>
  data(api.get("/question-templates", { params: categoryId ? { category_id: categoryId } : {} }));
export const createQuestionTemplate = (body) => change(api.post("/question-templates", body));
export const updateQuestionTemplate = (id, body) => change(api.put(`/question-templates/${id}`, body));
export const removeQuestionTemplate = (id) => change(api.delete(`/question-templates/${id}`));

// Permanent deletion (only for deactivated items with no surveys / no products)
export const deleteProductPermanently = (id) => change(api.delete(`/products/${id}/permanent`));
export const deleteCategoryPermanently = (id) => change(api.delete(`/categories/${id}/permanent`));
```

## `backend/.env.example`

```bash
# ============================================================
# Backend settings. Copy to backend/.env and adjust.
# backend/.env is private to each computer/server: never commit it.
# Real environment variables (PowerShell $env:..., Docker) override this file.
# Restart the backend after changing anything here.
# ============================================================

# development | staging | production
APP_ENV=development

# --- Where the API listens (python run.py) ---
# 127.0.0.1 = this computer only. 0.0.0.0 = also reachable from other devices
# on the network (needed for the Flutter app on a real phone).
BACKEND_HOST=127.0.0.1
BACKEND_PORT=8000
# Auto-restart on code changes (default: true in development, false otherwise)
BACKEND_RELOAD=true

# --- CORS: the frontend's address(es) in the browser, comma separated ---
# Must match VITE_PORT in frontend/.env.local. localhost and 127.0.0.1 are
# different origins, so keep both. Staging/production: the real site, e.g.
#   CORS_ORIGINS=https://fmcg.example.com
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173

# --- AI Model Service (separate process, ai_service/) ---
AI_SERVICE_URL=http://127.0.0.1:8001
# Optional shared secret; must match AI_SERVICE_TOKEN of the AI service
AI_SERVICE_TOKEN=

# --- Secrets (fill in on each server; never put these in the frontend) ---
# Signs login tokens. Empty = a random key stored in backend/.secret_key.
FMCG_SECRET_KEY=

# --- MySQL ---
# Leave these commented out to use the values in app/database/database.py.
# Uncomment ALL you need; an uncommented empty line (MYSQL_PASSWORD=) counts as
# "empty password", not "use the default".
# MYSQL_HOST=127.0.0.1
# MYSQL_PORT=3306
# MYSQL_DATABASE=fmcg_learning
# MYSQL_USER=root
# MYSQL_PASSWORD=your-password
```

## `frontend/.env.example`

```bash
# ============================================================
# Frontend settings (Vite). Copy to the file for your environment:
#   development (npm run dev)            -> .env.local
#   staging     (npm run build:staging)  -> .env.staging.local
#   production  (npm run build)          -> .env.production.local
# Those files are private (git-ignored). Restart `npm run dev` after changes;
# for builds, the values are fixed when you build.
#
# Every VITE_* value is visible to anyone using the website. Never put
# passwords, API keys or tokens here; secrets belong in backend/.env.
# ============================================================

# Port of the development server. If it is busy, `npm run dev` stops with an
# error. After changing it, update CORS_ORIGINS in backend/.env.
VITE_PORT=5173

# Address of the backend API, as the BROWSER reaches it (no trailing slash).
# Must match BACKEND_PORT in backend/.env.
#   this computer:            http://127.0.0.1:8000
#   another PC on the LAN:    http://192.168.1.25:8000
#   staging / production:     https://api.example.com
VITE_API_BASE_URL=http://127.0.0.1:8000
```

## `frontend/.env.local`

```bash
# Local development settings for this computer (git-ignored).
# Template and explanations: .env.example
VITE_PORT=5173
VITE_API_BASE_URL=http://127.0.0.1:8000
```

## `.gitignore`

```gitignore
# Python
__pycache__/
*.py[cod]
.venv/
venv/
env/
.pytest_cache/

# Secrets / environment files (any folder). Only templates are committed.
.env
.env.*
!.env.example
*.local
.secret_key

# Frontend
node_modules/
dist/
build/

# Logs and temporary files
*.log
.DS_Store
Thumbs.db

# Local databases and vector stores
chroma/
chroma_db/
*.sqlite3

# Generated uploads and private data
uploads/*
!uploads/.gitkeep
backend/uploads/
ai_service/work/
data_exports/

# Model files
*.gguf
*.safetensors
*.pt
*.pth
*.onnx
ai_service/models/*
!ai_service/models/README.txt
```

## `frontend/.gitignore`

```gitignore
# Logs
logs
*.log
npm-debug.log*
yarn-debug.log*
yarn-error.log*
pnpm-debug.log*
lerna-debug.log*

node_modules
dist
dist-ssr
*.local

# Editor directories and files
.vscode/*
!.vscode/extensions.json
.idea
.DS_Store
*.suo
*.ntvs*
*.njsproj
*.sln
*.sw?

# Environment files: keep only the template in git
.env
.env.*
!.env.example
```

## `frontend/src/App.jsx` (changes only)

```diff
--- a/frontend/src/App.jsx
+++ b/frontend/src/App.jsx
@@ -2069,5 +2069,5 @@
       if (silent) return; // background refresh: keep what is on screen
       console.error(err);
-      setData((d) => ({ ...d, loading: false, error: "Survey history couldn't be loaded. Check that the backend is running on port 8000." }));
+      setData((d) => ({ ...d, loading: false, error: `Survey history couldn't be loaded. Check that the backend is running at ${API_BASE_URL}.` }));
     }
   }, []);
@@ -2307,5 +2307,5 @@
           err?.response?.status === 404
             ? "The backend doesn't have team management yet. Add the admin router to app/main.py."
-            : "The team couldn't be loaded. Check that the backend is running on port 8000.",
+            : `The team couldn't be loaded. Check that the backend is running at ${API_BASE_URL}.`,
       }));
     }
@@ -3214,5 +3214,5 @@
       if (silent) return; // background refresh: keep what is on screen
       console.error(err);
-      setData((d) => ({ ...d, loading: false, error: "The surveys couldn't be loaded. Check that the backend is running on port 8000." }));
+      setData((d) => ({ ...d, loading: false, error: `The surveys couldn't be loaded. Check that the backend is running at ${API_BASE_URL}.` }));
     }
   }, []);
@@ -4253,5 +4253,5 @@
     } catch (err) {
       console.error(err);
-      if (!silent) setHistoryError("Unable to load survey history. Make sure the backend is running on port 8000.");
+      if (!silent) setHistoryError(`Unable to load survey history. Make sure the backend is running at ${API_BASE_URL}.`);
     } finally {
       setHistoryLoading(false);
@@ -4606,5 +4606,5 @@
           ? `${err.message} Check the connection and try again; nothing was lost.`
           : noNetwork
-          ? "The connection dropped before the upload finished, or the backend isn't running on port 8000."
+          ? `The connection dropped before the upload finished, or the backend isn't running at ${API_BASE_URL}.`
           : serverReason ||
               "The video couldn't be processed on the server. Try again, or check the backend log for details."
```

## `frontend/src/components/SurveyWorkflow.jsx` (changes only)

```diff
--- a/frontend/src/components/SurveyWorkflow.jsx
+++ b/frontend/src/components/SurveyWorkflow.jsx
@@ -3,4 +3,5 @@
 import { CATALOG_CHANGED, DATA_CHANGED } from "../services/appEvents";
 import { getSession } from "../services/auth";
+import { API_BASE_URL } from "../config";
 
 /*
@@ -442,5 +443,5 @@
                 <p>
                   {catalogError
-                    ? `${catalogError} Check that the backend is running on port 8000.`
+                    ? `${catalogError} Check that the backend is running at ${API_BASE_URL}.`
                     : "The catalog is empty. An admin needs to add categories and products, or load the sample catalog with: python -m app.database.seed_catalog"}
                 </p>
```

## `frontend/src/components/TeamDialogs.jsx` (changes only)

```diff
--- a/frontend/src/components/TeamDialogs.jsx
+++ b/frontend/src/components/TeamDialogs.jsx
@@ -3,4 +3,5 @@
 import { addTeamMember, getMemberProducts, resetMemberPassword, saveMemberProducts } from "../services/teamApi";
 import { listProducts } from "../services/catalogAdminApi";
+import { API_BASE_URL } from "../config";
 
 /*
@@ -27,5 +28,5 @@
     return `${label ? `${label}: ` : ""}${String(d[0].msg).replace("Value error, ", "")}`;
   }
-  if (err?.code === "ERR_NETWORK") return "Can't reach the backend on port 8000.";
+  if (err?.code === "ERR_NETWORK") return `Can't reach the backend at ${API_BASE_URL}.`;
   return fallback;
 }
```
