# Offline Survey Mode (survey persons)

Survey persons (role `executive`) can sign in, take surveys and record audio/video
without internet. Everything is saved on the phone (encrypted) and uploaded
automatically when the connection returns, without duplicates. Admins, the
organization (Super Admin) and every web portal stay online-only.

## How it works

### Offline sign-in (`mobile/lib/services/offline_auth.dart`, `session.dart`)
- After a successful **online** sign-in by a survey person, the phone stores a
  password verifier in the secure store (iOS Keychain / Android Keystore):
  PBKDF2-HMAC-SHA256, random 16-byte salt, 100,000 rounds. The password itself
  is never stored. Admin / organization accounts never get a verifier.
- `Session.login()` tries the server first. Only when the server can't be reached
  does it check the verifier and sign in **offline** (`Session.offlineMode`). The
  home screen shows an offline banner.
- Refused offline: an account never signed in online on this phone; a wrong password
  (5 in a row lock offline sign-in until the next online sign-in); more than
  `OFFLINE_LOGIN_DAYS` (default 14, `--dart-define=OFFLINE_LOGIN_DAYS=n`) since
  the last online sign-in.
- The verifier is deleted when the server reports the account disabled (403) or
  the session ended (401 from `/auth/me`), or when the password no longer works online.
- Back online, the app signs in to the server with the password typed at the
  offline sign-in (kept in memory only). After an app restart that password is
  gone, so the app asks for it once (banner: "Sign in to upload").

### Local storage (`mobile/lib/services/local_db.dart`, `survey_queue.dart`)
- SQLite encrypted with **SQLCipher** (`sqflite_sqlcipher`). The 32-byte random
  database key lives in the secure store. File: app support folder `fmcg_offline.db`.
- Table `submissions`: one row per survey (customer details, answers, product /
  round references, surveyor id, client UUID, status, upload progress, timestamps).
- Table `offline_cache`: downloaded survey data per account.
- Recordings are copied into the app's private support folder `offline_media/`.
- Older app versions kept the queue in `Documents/surveys/index.json` (unencrypted);
  it is moved into the database once and deleted.
- After the server finishes the analysis, the customer's personal details and the
  recording are deleted from the phone (as before). Nothing is deleted earlier.

### Survey data for offline use (`mobile/lib/services/offline_catalog.dart`)
Downloaded automatically after sign-in / when back online, or with
**Download for offline use** on the Sync screen: categories, the products this
account may survey, their questions, master lists and the person's action plans.
Only what the server returned for this account is stored; the copy is replaced as
a whole, so products no longer assigned disappear.

### Synchronisation (`mobile/lib/services/survey_queue.dart`)
Order per survey: customer → survey → answers → submit → recording → analysis.
Each step the server confirms is saved before the next starts.
- **No duplicates:** every survey has a UUID (`client_uuid`) sent with the customer
  and the survey; the backend returns the existing record for a UUID it has seen.
- **Resumable recordings:** sent in 2 MB pieces under an upload id. After any
  interruption (network, app closed, phone restarted) the upload continues from
  the bytes the server confirmed. SHA-256 checked on completion. Completing an
  upload queues exactly one analysis.
- **When:** at app start, when the network returns (`connectivity_plus`), every
  minute while the app is open, after each new survey, and **Sync now**.
- **Retries:** no connection → waits for the network; server errors (5xx/429/408)
  → retried automatically after 1, 2, 4 … 60 minutes; refused (other 4xx) →
  "Upload failed", with **Try again** (nothing is collected again).
- Only the signed-in account's surveys are shown and uploaded.
- Statuses: Pending upload · Uploading · Synced (server confirmed; analysis may
  still run) · Upload failed. Sync screen: app bar cloud icon.

## Backend changes

| Change | Where |
|---|---|
| `respondents.client_uuid`, `survey_responses.client_uuid` (VARCHAR(36), UNIQUE, nullable) | `models.py`, `migrations.py` (added on startup) |
| `POST /surveys/respondents`, `POST /surveys/responses` accept optional `client_uuid`; same UUID → same record; another account's UUID → 409 | `api/surveys.py` |
| New table `upload_sessions` | `models.py` (created on startup) |
| `POST /uploads/resumable` · `GET /uploads/resumable/{id}` · `PUT /uploads/resumable/{id}?offset=N` (raw bytes) · `POST /uploads/resumable/{id}/complete` | `api/resumable_uploads.py` (new) |
| Unfinished uploads untouched for `FMCG_UPLOAD_STALE_DAYS` (default 14) are deleted | `api/resumable_uploads.py` |
| Max piece size `FMCG_UPLOAD_PIECE_MB` (default 8; the app sends 2) | `api/resumable_uploads.py` |

Requests without `client_uuid` behave exactly as before; older app versions keep working.
The new routes need a signed-in account and are not open to the organization login.

## Dependencies
Flutter: `sqflite_sqlcipher`, `connectivity_plus`, `cryptography`; tests:
`sqflite_common_ffi`, `path_provider_platform_interface`, `plugin_platform_interface`.
Backend: none new.

## Running

```bash
# Backend (MySQL as before; the new columns/table are added on startup)
cd backend && uvicorn app.main:app --reload

# App
cd mobile && flutter pub get && cd ios && pod install && cd ..
flutter run --dart-define=API_BASE_URL=http://<server>:8000

# Tests
cd backend && .venv/bin/python -m pytest tests/test_offline_sync.py
cd mobile && flutter test --dart-define=API_BASE_URL=http://127.0.0.1:8765
# End-to-end against the real backend (starts/stops it itself, SQLite):
cd mobile && RUN_E2E=1 flutter test test/e2e_offline_sync_test.dart --dart-define=API_BASE_URL=http://127.0.0.1:8765
```

## Known limitations
- Uploads run while the app is open (foreground, or briefly after it goes to the
  background). There is no OS background task (WorkManager / BGTaskScheduler);
  interrupted uploads resume the next time the app is opened.
- Recordings in the app's private folder are protected by the OS file encryption,
  not by SQLCipher (survey data is). They are deleted after the server's analysis.
- GPS → address lookup needs the server: offline, the coordinates are saved and the
  survey location is typed.
- A survey collected offline for a round that closed before it was uploaded is
  refused by the server ("Upload failed", with the server's reason).
- `sqflite_sqlcipher` doesn't support Swift Package Manager yet (CocoaPods works;
  Flutter warns this will become an error in a future version).
