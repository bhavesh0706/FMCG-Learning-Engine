# AI model on its own port

## What changed

```
Frontend ──► Backend API :8000 ──HTTP──► AI Model Service :8001 ──► Ollama :11434 (Qwen)
               │  (queue, auth,            (FFmpeg, noise cleanup,
               │   aspect matching,         speaker isolation, Whisper,
               │   ratings, saving)         translation, Qwen summaries)
               ▼
             MySQL
```

The upload flow, API routes, request and response formats, and database writes are all unchanged:

1. The frontend uploads to `POST /analysis/jobs` (or `/video/upload`), exactly as before.
2. The backend worker picks up the job and runs `upload_video`.
3. `upload_video` now **sends the recording to the AI service** (`POST :8001/v1/analyze/media`) instead of running Whisper and Qwen itself.
4. The AI service returns the raw results: duration, transcript, Qwen summary per 3-minute part, and the overall summary.
5. The backend matches aspects against `feedback_aspects`, applies the rating rules, calculates the overall rating, saves to MySQL and returns the same response as before.

Consolidated reports and the translation of survey answers also go through the AI service now (`POST :8001/v1/llm/generate`). The CSV/Excel upload (`/upload`) uses `POST :8001/v1/analyze/feedback`. The backend never loads a model or calls Ollama directly.

## Folders

```
FMCG-LEARNING-ENGINE/
├── frontend/
├── backend/                       Backend API, port 8000 (no AI code any more)
│   ├── app/
│   │   ├── api/video.py           sends the recording to the AI service, then matches
│   │   │                          aspects/ratings and saves to MySQL (unchanged)
│   │   ├── api/upload.py          CSV analysis through the AI service
│   │   └── services/
│   │       ├── ai_client.py       NEW: HTTP client for the AI service
│   │       ├── consolidation.py   reports: LLM calls go through the AI service
│   │       ├── analysis_worker.py (unchanged) product_access.py (unchanged)
│   └── requirements.txt           backend libraries only
│
├── ai_service/                    NEW: AI Model Service, port 8001 (moved out of backend)
│   ├── main.py                    API: /health, /v1/analyze/media, /v1/analyze/feedback, /v1/llm/generate
│   ├── pipeline.py                the AI steps taken out of the old upload_video
│   ├── services/                  moved from backend/app/services (code unchanged, imports updated)
│   │   ├── ffmpeg_service.py      whisper_service.py   audio_cleanup.py
│   │   ├── speaker_isolation.py   translation_runner.py translation_service.py
│   │   └── ollama_service.py
│   ├── tools/                     moved from backend/app/tools: diagnose_audio.py, identify_language_mms.py
│   ├── models/                    MOVE the contents of backend/models here
│   └── requirements.txt           AI libraries (+ requirements-translation.txt, optional)
│
├── docker/                        backend-ai.Dockerfile, start.sh
└── docker-compose.yml
```

The only edits inside the moved files are import paths (`app.services.X` → `services.X`) and the models folder (`backend/models` → `ai_service/models`).

## How to apply it to your project

1. Copy `ai_service/`, `docker/`, `docker-compose.yml`, `.env.example` and `.gitattributes` into the project root.
2. Copy `backend/app/...` and `backend/requirements*.txt` over your backend (replaces 6 files, adds `ai_client.py`).
3. **Delete from the backend**: `app/services/` ffmpeg_service.py, whisper_service.py, audio_cleanup.py, speaker_isolation.py, translation_runner.py, translation_service.py, ollama_service.py, and `app/tools/` diagnose_audio.py, identify_language_mms.py.
4. **Move** everything in `backend/models/` to `ai_service/models/`.
5. Remove the old `backend/Dockerfile` and `backend/docker/` if you copied them from the previous version.

## Run locally (Windows, two terminals)

```powershell
# Terminal 1: AI model service (loads Whisper, then waits)
cd ai_service
python -m venv venv; .\venv\Scripts\activate      # first time
pip install -r requirements.txt                      # first time
uvicorn main:app --host 127.0.0.1 --port 8001

# Terminal 2: backend API
cd backend
python run.py        # host/port from backend\.env (BACKEND_HOST, BACKEND_PORT)
```

You can also keep using your current venv for both; it already has everything.
Check http://127.0.0.1:8001/health and http://127.0.0.1:8000/health (the backend shows `"ai_service": {"status": "healthy", ...}`).

## Run with Docker (3 containers)

```powershell
copy .env.example .env        # then set MYSQL_ROOT_PASSWORD and FMCG_SECRET_KEY
docker compose up -d --build
```

- Frontend: http://localhost:3000. Backend: http://localhost:8000. AI service: http://localhost:8001 (from this computer only).
- The backend container is built from `docker/backend-ai.Dockerfile` and runs `backend/` and `ai_service/` as two processes.
- Ollama keeps running on your computer. The container reaches it at `host.docker.internal:11434`.
- The MySQL container starts empty. To bring your current data across, put a dump in `db-init/` before the first start:
  `mysqldump -u root -p fmcg_learning > db-init\fmcg_learning.sql`
- `deep-filter.exe` is Windows-only. Inside the Linux container, the code downloads the Linux build of DeepFilterNet on first use.
- To give the AI service its own fourth container later, only `ai_service/` and its requirements are needed (`uvicorn main:app --port 8001`); set `AI_SERVICE_URL=http://ai:8001` on the backend.

## Settings

| Variable | Where | Default |
|---|---|---|
| `AI_SERVICE_URL` | backend | `http://127.0.0.1:8001` |
| `AI_SERVICE_TOKEN` | both | empty. When set, the backend must send the same value |
| `AI_SERVICE_TIMEOUT` | backend | `3600` seconds per recording |
| `AI_MAX_CONCURRENT` | AI service | `1` analysis at a time (like 1 worker before). For more, run uvicorn with `--workers N` |
| `OLLAMA_URL` | AI service | `http://127.0.0.1:11434/api/generate` |
| `MYSQL_HOST` / `MYSQL_PORT` / `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_DATABASE` | backend | your current values |
| `AI_WORK_DIR` | AI service | `ai_service/work` (temporary uploads and audio parts) |

All the existing `FMCG_*` settings (Whisper model, languages, brand hints, denoise, report model…) work as before. Set them on the AI service (terminal 1).

## How it was tested

The original code and the new split code (with `ai_service` running from its own folder) were run on the same 6 min 40 s recording (3 parts, one of them silent), with fixed stand-ins for Whisper and Ollama. The results matched:

- API response: identical, every field
- Saved `feedback_records` row: identical
- Too-short and damaged files: same error messages, and the same files kept or deleted
- Queue worker job (`/analysis/jobs` path): same result and status
- CSV upload: same result
- Report and translation requests to Ollama: byte-for-byte identical (model, options, keep-alive)
- AI service stopped: the backend retries, then returns `503 "The AI service ... isn't reachable"`
- The backend starts and serves requests without loading Whisper or any AI library

Not tested here: the real Whisper and Qwen models, the real MySQL, and the Docker image build (the sandbox couldn't download base images).
