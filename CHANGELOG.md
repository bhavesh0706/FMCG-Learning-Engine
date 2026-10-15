# Changelog

## 1.2.0 — R&D phase 1: security, data safety, performance (15 Oct 2026)

Finding IDs refer to the problem register in the R&D Assessment & Roadmap document.

### Security
- **SEC-01 (critical) — record-level access.** A survey person can now reach only their own surveys, customers, recordings and analysis jobs; admins reach everything; anyone else gets 404. New `backend/app/api/ownership.py`, used by `surveys.py`, `feedback.py`, `analysis.py` and `video.py`. New surveys belong to the signed-in account from the moment they are created (the separate "claim" call still works). Customers record which account created them (`respondents.created_by`).
- **SEC-02 (critical) — path traversal removed.** The unused `POST /upload/` (CSV import) wrote files using the client's file name. Endpoint, `upload.py`, `pandas` and `openpyxl` removed.
- **SEC-03 (critical) — HTTPS.** Caddy reverse proxy (`docker/Caddyfile`) with automatic Let's Encrypt certificates, HSTS, HTTP→HTTPS redirect, a 520 MB body limit, and `/api` routed to the backend. Backend, frontend and MySQL ports are bound to localhost only.
- **SEC-04 — login throttling.** After 5 wrong passwords for one account, or 30 from one address, within 15 minutes, login returns 429 with Retry-After. Failures are stored in the new `login_failures` table, so the limit holds across processes. The same throttling applies when first-admin setup reuses an existing account.
- **SEC-05 — no password in code.** The MySQL password default is gone from `database.py` (`MYSQL_PASSWORD` is required). Compose uses a dedicated application user and a read-only backup user instead of root. `DATABASE_URL` is supported. The connection pool is configurable.
- **SEC-07 — AI service token required in production.** The AI service refuses to start with `APP_ENV=production` and no `AI_SERVICE_TOKEN`. The token comparison is constant-time.
- **SEC-08 — upload limits.** `FMCG_MAX_UPLOAD_MB` (default 500): larger uploads stop with 413 and the partial file is removed. Uploads are copied in 1 MB chunks, and file names are cleaned (`uploads_limit.py`).
- **SEC-09 — password hashing.**
  - PBKDF2-SHA256 at 600,000 rounds (OWASP); older hashes are upgraded at the next login.
  - Unknown accounts take the same time as wrong passwords.
- **SEC-11 — headers.**
  - Content-Security-Policy and other security headers on the web app (`frontend/nginx.conf`), checked with 0 violations.
  - nosniff, DENY framing and no-referrer on API responses.
  - API docs are off in production.

### Data
- **DATA-01 — no name-based survey matching.** "My surveys" and team statistics now use `user_id` only. Older unlinked surveys are linked once, and only when exactly one account has that name; shared names stay unlinked for an admin.
- **OPS-01 — backups.**
  - Nightly consistent MySQL dump plus a copy of recordings (`backup` service, `docker/backup.sh`), with restore steps in `docker/restore.sh`.
  - A read-only backup user (`db-init/01_backup_user.sh`).
  - A Windows script for the non-Docker setup (`scripts/backup_mysql.ps1`).

### Performance
- **PERF-01 — report pages.** The new stored `feedback_records.tone` column (kept current by a model listener and backfilled at startup) lets the report lists load only light columns, never transcripts or chapters. At 50,000 surveys the admin product list went from 4.7 s to 0.27 s. Eight concurrent loads went from 44 s and 2.8 GB peak memory to 4.3 s and 0.3 GB.
- **PERF-02 — worker staleness check.** It runs every 10 s when idle and went from 3.9 s / 730 MB to 0.22 s / 94 MB at 50,000 surveys.

### Operations
- **OPS-03 — logs and health checks.**
  - An `X-Request-ID` header on every response, and one JSON access-log line per request (no tokens or bodies).
  - New `/health/ready` readiness check (database), used by the Docker health check.
  - `/health` now reports database status.
- **OPS-02 — tests and CI.**
  - 36 backend tests covering the access matrix, sign-in hardening, uploads, migrations, notifications and operations.
  - A GitHub Actions workflow with lint, tests, dependency audits, the frontend build, `flutter analyze` and a compose check.

### Web and mobile
- **UX-01 — warning colour.** Darkened to `#8a560c` (5.5:1 contrast, WCAG AA) on web and mobile.
- **UX-08 — lint errors.** The existing frontend lint errors are fixed (unused variables, error causes, intentional control-character regexes).
- **MOB-01 — mobile token.** The sign-in token is stored in the iOS Keychain / Android Keystore (`flutter_secure_storage`); existing installs move it automatically.

### Fixes
- **AI-08 — duration message.** The error for too-short recordings now states the real minimum (10 seconds, not 30).
- **Dead code.** Removed the commented-out old code in `feedback.py`.

### Upgrade notes (action needed)
1. Add `MYSQL_PASSWORD=<your MySQL password>` to `backend/.env` before starting the backend. There is no default any more.
2. Docker: copy the new `.env.example` to `.env` and fill in DOMAIN, ACME_EMAIL, MYSQL_APP_PASSWORD, MYSQL_BACKUP_PASSWORD and AI_SERVICE_TOKEN.
3. Rotate the MySQL password that used to be in `database.py`; it is in the repository history.
4. Mobile: run `flutter pub get` (new package `flutter_secure_storage`).
5. Mobile release builds must use the HTTPS address: `--dart-define=API_BASE_URL=https://<DOMAIN>/api`.
