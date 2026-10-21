# Changelog

## 1.4.0 — Organization portal and survey rounds (19 Oct 2026)

The client is the organization (the company whose products are surveyed). It now has
its own portal. The admin dashboard has no client options: it updates by itself when
the organization changes a recipe or starts a new survey round.

### The organization's login (made on the server, not on the dashboard)
- `python -m app.tools.organization create --name "..." --email ...` prints a starting password once.
  Also `reset-password`, `list`, `rename`, `disable`, `enable`.
- **First sign-in:** the organization must choose its own password before anything else
  (`POST /auth/change-password`; every other sign-in then ends). After that only the organization knows it.
- The organization sees **every active product**, but only aggregated results: no customer names,
  phone numbers or addresses, no survey persons, no recordings, no single surveys. Admins appear as "Research team".
- The mobile app refuses the organization's login (it is for survey persons).

### Organization portal (web, same address, its own pages)
- **Overview:** products, consumer responses, average rating, open rounds; products with their current
  round, progress and rating change; latest updates.
- **Products:** cards with round status; filters Collecting / Report ready / Not started; search.
- **Product:** Consumer report (per round, with the PDF) · Recipe (edit, version history, compare
  versions, "tested in Round N") · Rounds (timeline, start a round, end a round early) · Compare rounds
  (key numbers, what consumers said, recipe changes, Comparison PDF).
- **Start new round** dialog: recipe version, consumers to survey, wanted-by date, note for the research team.
- **Updates:** everything that happened, newest first, with an unread count in the menu.
- **Account:** organization details, change password, sign out everywhere.

### Survey rounds
- New `survey_rounds` table; every survey and every report belongs to a round (`round_id`).
- One live round per product. New surveys from the field app are filed under it automatically; the round
  closes by itself when its target is reached. Products that already had surveys get **Round 1** holding them.
- Every round has its own report (rebuilt by the worker like the product report; a round with the same
  surveys as the product report reuses it, so the AI is not asked twice).

### Admin dashboard (no client options; it updates by itself)
- **Bell:** "Recipe updated by ‹organization›", "‹organization› started Round N", "‹organization› ended Round N".
- **Badges:** live round ("Round 2 · 6 of 30") and recipe changed by the organization, on Home, Survey history and Catalog.
- **Catalog → product → Rounds** tab: every round (recipe, progress, who started it, note, rating), Open report, End round.
- **Survey history → product:** All rounds / Round 1 / Round 2 picker; the report and the customer list follow it.
- Survey persons get "New round: ‹product›" in the mobile app's bell (no app update needed).
- Activity log: round starts and ends, password changes.

### Settings (backend `.env`, optional)
- `FMCG_ROUND_TARGET` (default 30): consumers per round when none is given.
- `FMCG_ORG_CONTACT`: the contact shown on the organization's Account page.

## 1.3.1 — Button and icon size standard (19 Oct 2026)

- Buttons have three sizes: small 32 px, medium 40 px, large 48 px. Large is defined but not used anywhere.
- Medium: page-header actions, dialog footers, form submits, empty-state actions, top bar. Small: actions inside cards, panels, table rows and toolbars.
- Icons have three sizes: small 16 px (inside buttons, chips, tables), medium 20 px (top bar, icon tiles in lists), large 24 px (empty states, feature tiles).
- Icon-only buttons are 32 px (small) or 40 px (medium); icon tiles are 32, 40 or 48 px.
- Removed a duplicate `.btn-sm` rule and conflicting per-screen button heights (36, 42, 46 px).
- Android app: buttons 40 px (the touch area stays 48), icons 16 / 20 / 24.

## 1.3.0 — Phase 2 quick wins: sign-out, activity log, Play Store app ID (16 Oct 2026)

### Sign-in and sign-out (SEC-06)
- **Shorter sign-ins.** Survey persons stay signed in for 7 days (`FMCG_TOKEN_DAYS`) and admins for 24 hours (`FMCG_ADMIN_TOKEN_HOURS`), down from 30 days for everyone. While the app is used, `GET /auth/me` hands out a fresh token once the current one is a day old, so regular users are not asked to sign in again. The web app checks every 30 minutes while it is open, and the mobile app checks each time it opens.
- **Real sign-out.** "Sign out" now ends the token on the server (`POST /auth/logout`, new `revoked_tokens` table), on the web and on the phone. Before, a copied token kept working until it expired. `POST /auth/logout-all` ends every sign-in of the account.
- **Lost phone.** A new **Sign out everywhere** button on the Team page (`POST /admin/users/{id}/sign-out`) signs a person out of every device without changing their password. A password reset, switching **Active** off, or a role change now does the same automatically (new `users.token_version` column).
- **Compatible.** Tokens issued by 1.2.0 keep working and are swapped for a new 7-day token the next time the app opens. No one is signed out by the upgrade.
- **Fix.** Two sign-ins in the same second used to get an identical token, so signing out on one device would have ended the other. Each token now has a random part.

### Activity log (audit trail, proposal section 3)
- **New `audit_log` table.** Every successful change is recorded automatically: team, catalog, recipes, question templates, surveys, recordings, reports and deletions. Sign-ins, failed sign-ins (with a masked number), sign-outs and first-admin setup are recorded as well. Each entry holds who, when, what, the target, its name, an address and a request ID.
- **Field-level changes.** Team edits show old → new (for example, Survey history On → Off), recipe saves show ingredient and step counts, and product assignments show the names added or removed.
- **Never recorded.** Passwords, tokens, request bodies, and customer names, phone numbers or addresses.
- **New Activity log tab on the Team page.** You can search, filter by area and period, and page through entries; filtering and paging run on the server (`GET /admin/audit`). Entries can't be edited, and entries older than `FMCG_AUDIT_KEEP_DAYS` (default 3 years) are removed.

### Mobile: Play Store readiness
- **New `scripts/set_android_app_id.ps1`.** It changes the Android app ID from `com.example.mobile` (which the Play Store refuses) to your own ID (default `com.ioneit.recipesurvey`) and sets the app name (default "Recipe Survey"). It has a `-WhatIf` preview and saves a `.bak` copy of each changed file.

### Fixes
- **Web headings in dark mode.** When Windows or macOS was in dark mode, page headings were almost invisible, because Vite's starter `index.css` switched heading colours while the app stays light. The colours are now pinned in `App.css`.

### Upgrade notes
1. Restart the backend. It adds `users.token_version` and the `audit_log` and `revoked_tokens` tables by itself.
2. Mobile: copy the new `lib/services/session.dart`. To change the app ID, run `.\scripts\set_android_app_id.ps1`, then `adb uninstall com.example.mobile` once on test phones.
3. Optional settings (`backend/.env`): `FMCG_TOKEN_DAYS`, `FMCG_ADMIN_TOKEN_HOURS`, `FMCG_TOKEN_RENEW_HOURS`, `FMCG_AUDIT_KEEP_DAYS`.

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
