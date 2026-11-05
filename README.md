# FMCG Learning Engine

An end-to-end FMCG product research and consumer-feedback platform for
product/catalog management, recipe management and costing, configurable
surveys, AI-assisted feedback, multilingual translation, audio
generation and reporting.

> **Implementation status:** This README records established project
> scope and development decisions. Items marked as required checks must
> be verified against the current branch before being claimed as
> complete. In particular, strict recipe-standard enforcement before
> survey-round creation is a mandatory requirement.

## Contents

-   [Overview](#overview)
-   [Features](#features)
-   [Architecture](#architecture)
-   [Technology stack](#technology-stack)
-   [Repository layout](#repository-layout)
-   [Prerequisites](#prerequisites)
-   [Installation](#installation)
-   [Web application](#web-application)
-   [Flutter mobile application](#flutter-mobile-application)
-   [Workflows](#workflows)
-   [Recipe costing and standards](#recipe-costing-and-standards)
-   [AI, translation and audio](#ai-translation-and-audio)
-   [Reporting and exports](#reporting-and-exports)
-   [Database and catalog
    initialization](#database-and-catalog-initialization)
-   [Configuration](#configuration)
-   [Testing](#testing)
-   [Deployment direction](#deployment-direction)
-   [Security](#security)
-   [Release checklist](#release-checklist)
-   [Troubleshooting](#troubleshooting)
-   [Version matrix](#version-matrix)

## Overview

FMCG Learning Engine supports structured FMCG product research and
consumer feedback. It combines a React web portal, FastAPI backend,
MySQL database, Ollama-hosted AI models and a Flutter mobile application
for Android and iOS.

The main workflow is: configure products and categories → maintain
product recipes and standards → configure survey questions → collect
consumer responses → generate and translate feedback → generate audio
where configured → review reports and round comparisons.

## Features

### Product and catalog management

-   Product master records, SKU and category/subcategory associations.
-   Database-driven categories, products, recipes and survey questions.
-   Product examples used during development: Kellogg's Corn Flakes, Aam
    (Mango) Jelly, Dark Chocolate, Honey Nut Granola, Dove Daily Shine
    Shampoo and Mango Lassi.
-   Adding catalog records should not require manually editing a
    hard-coded seed catalog.

### Recipe management

-   Product-linked recipes and recipe versions.
-   Ingredient quantities, usage percentages and cost per kilogram.
-   Ingredient cost-in-use, total recipe cost per kg and batch cost.
-   Recipe process steps, supporting attachments and version comparison.

### Recipe standards

The rule types implemented/discussed are: - `required_ingredient` -- a
named ingredient must exist. - `ingredient_share` -- ingredient usage
percentage must be within configured minimum/maximum. -
`max_cost_per_kg` -- maximum total recipe cost per kilogram. -
`ingredient_cost_per_kg` -- maximum cost per kilogram for one selected
ingredient. - `costs_complete` -- every ingredient must have a cost per
kilogram.

Scope rules: - Product standards apply only to that product. - Category
standards apply only to products in that category. - Unscoped standards
must not silently become global. - Product rules must not leak to other
products in the same category.

**Mandatory survey gate:** A survey round must be strictly blocked if
any applicable mandatory recipe standard fails. No Super Admin override
is allowed. The backend must revalidate the latest recipe immediately
before creating a round and return HTTP `409 Conflict` with
failed-standard details. Frontend-only validation is insufficient.
Verify this behaviour in the current code before describing it as
released.

### Surveys

-   Product-linked survey questions and configurable questionnaires.
-   Survey rounds, respondent assignment and response collection.
-   Round history, completion and product/round comparisons.

### Organization portal

The organization-facing portal includes Overview, Products, consumer
reports, product recipes, product-specific standards, survey questions,
rounds, round comparisons, Surveys, Analytics, report downloads, Updates
and Account management. The portal is intended to show aggregated
insights rather than individual consumer contact details or raw personal
data.

### AI feedback and language support

-   AI-assisted text feedback generation.
-   Multilingual feedback and translation.
-   Audio generation and playback where configured.
-   Speech/transcription-related processing where enabled by the
    backend.
-   Languages previously requested for examples: English, Hindi,
    Gujarati (Latin transliteration), Marathi, Tamil, Telugu, Bengali,
    Kannada, Malayalam, Punjabi, Yoruba and Maithili.

### Reports and exports

-   Product and survey-round consumer reports.
-   Round comparison reports.
-   PDF downloads where implemented.
-   Analytics views and charts.
-   MySQL-to-Excel export utility.

## Architecture

``` text
Web browser ──> React + Vite ──┐
                               ├──> FastAPI REST API ──> MySQL (fmcg_learning)
Flutter Android/iOS ───────────┘             │
                                            ├──> Ollama: qwen2.5:7b
                                            ├──> Ollama: nomic-embed-text
                                            └──> File storage (recipe/report/audio)
```

Web and mobile clients communicate with FastAPI. They should not connect
directly to MySQL or Ollama.

## Technology stack

  Layer             Technology
  ----------------- -----------------------------------------
  Web               React, Vite, JavaScript/JSX, CSS
  Backend           Python, FastAPI, Pydantic, SQLAlchemy
  Database          MySQL, database `fmcg_learning`
  LLM runtime       Ollama
  Text model        `qwen2.5:7b`
  Embedding model   `nomic-embed-text`
  Mobile            Flutter, Dart
  iOS               Xcode, CocoaPods
  Android           Android Studio, Android SDK, JDK/Gradle
  API docs          FastAPI OpenAPI / Swagger UI

Additional Python packages previously observed include
`python-multipart`, `requests`, `numpy`, `faster-whisper` and
`onnxruntime`. The committed requirements/lock file is authoritative for
the complete package set.

## Repository layout

The exact tree may evolve. Use the current checkout as the source of
truth.

``` text
FMCG-Learning-Engine/
├── backend/
│   ├── app/
│   │   ├── api/                 # FastAPI routes and schemas
│   │   ├── database/            # SQLAlchemy models and DB connection
│   │   ├── services/            # Business logic, recipe and AI services
│   │   └── tools/               # Admin and utility scripts
│   ├── requirements.txt         # If present
│   ├── seed.py                  # If present
│   └── seed_catalog.py           # If present
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   └── services/
│   ├── package.json
│   └── package-lock.json
├── <Flutter app directory>/
│   ├── lib/
│   ├── android/
│   ├── ios/
│   ├── pubspec.yaml
│   └── pubspec.lock
└── README.md
```

Files discussed during development: -
`backend/app/services/recipe_tools.py` -- recipe costing and standards
validation. - `backend/app/api/recipe_master.py` -- recipe master,
standards and attachment APIs. - `frontend/src/components/OrgPortal.jsx`
-- organization portal and product-specific standards UI. -
`frontend/src/components/ReportPrompt.jsx` -- report/analysis prompt UI
and safe rendering of API errors. - `seed.py` and `seed_catalog.py` --
database/catalog initialization utilities.

Confirm exact paths against the checked-out branch; optional files may
move.

## Prerequisites

Previously recorded development environment:

  Software                                Recorded version / status
  --------------------------------------- -------------------------------
  Git                                     2.53.0
  Python                                  3.12 target in CI
  Node.js                                 22.21.1
  npm                                     10.9.4
  MySQL                                   9.6.0 (ARM64 Mac environment)
  Flutter                                 3.47.8
  Dart                                    3.13.7
  Xcode                                   27.1
  CocoaPods                               1.17.0
  Ollama application                      Exact version not recorded
  Android Studio / SDK                    Exact version not recorded
  Java / Gradle / Android Gradle Plugin   Read project build files

Android builds can run on Windows, macOS or Linux. Native iOS builds,
simulator runs, signing and App Store archives require macOS with Xcode.
Windows alone cannot build the iOS application.

## Installation

### 1. Clone

``` bash
git clone <repository-url>
cd FMCG-Learning-Engine
git status
```

Use the actual repository clone URL.

### 2. MySQL

Start MySQL and create the development database:

``` sql
CREATE DATABASE fmcg_learning
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;
```

Configure a MySQL user with the privileges required by the backend.
Apply the migration/schema procedure included in the repository. Do not
assume Alembic or another migration system exists unless its
configuration is present.

### 3. Backend (FastAPI)

``` bash
cd backend
python3.12 -m venv .venv

# macOS / Linux
source .venv/bin/activate

# Windows PowerShell
.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
pip install -r requirements.txt
```

Use the actual committed dependency file if its name/location differs.

Create the local `.env` from the repository's example file, if provided.
Typical database values:

``` dotenv
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=<mysql_user>
MYSQL_PASSWORD=<mysql_password>
MYSQL_DATABASE=fmcg_learning
```

Also configure the exact variables expected by backend settings for
authentication/JWT, Ollama URL and model names, storage paths,
translation/audio providers and CORS. Do not assume the illustrative
names above match every current code path. Never commit real secrets.

Previously used development command:

``` bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Use the actual FastAPI entry point if it differs. API documentation is
normally available at `http://localhost:8000/docs`.

### 4. Ollama and models

Install Ollama, then:

``` bash
ollama pull qwen2.5:7b
ollama pull nomic-embed-text
ollama list

# Start the service if it is not already running
ollama serve
```

Configure the backend to use the correct Ollama URL and model tags. A
common local endpoint is `http://localhost:11434`; verify the backend
configuration. Model downloads require network access and disk space.
Performance depends on CPU, RAM, GPU and VRAM.

### 5. React + Vite frontend

In another terminal:

``` bash
cd frontend
node --version
npm --version
npm ci
npm run dev
```

Use `npm ci` when `package-lock.json` is committed. Create the local
frontend environment file from the project's example and set the API
base URL using the exact variable expected by the frontend config. Use
scripts defined in `package.json`. Use `npm install` only if no lockfile
exists.

### 6. Flutter mobile application

Locate the directory containing `pubspec.yaml`:

``` bash
cd <flutter-app-directory>
flutter --version
flutter doctor -v
flutter pub get
```

The mobile app calls FastAPI. A previously used configuration pattern
is:

``` bash
flutter run --dart-define=API_BASE_URL=http://<computer-lan-ip>:8000
```

Confirm the exact compile-time key in the Flutter source. A physical
phone cannot normally use `localhost` to reach the development computer.
Use the computer's LAN IP and put both devices on the same network.
Configure firewall and backend CORS as required.

#### Android

1.  Install Android Studio.
2.  Install the Android SDK platform and Build-Tools required by the
    app's Gradle configuration.
3.  Install Platform-Tools and an emulator if needed.
4.  Install the JDK version required by the Gradle wrapper and Android
    Gradle Plugin.
5.  Accept SDK licences.

``` bash
flutter doctor --android-licenses
flutter devices

# Run
flutter run

# Release APK
flutter build apk --release

# Play Store bundle
flutter build appbundle --release
```

Find the exact Java, Gradle, Android Gradle Plugin, `compileSdk` and
`targetSdk` values in
`android/gradle/wrapper/gradle-wrapper.properties`,
`android/settings.gradle` or `android/build.gradle`, and
`android/app/build.gradle` (or `.kts`). These versions were not captured
in the available project history.

#### iOS (macOS only)

Install Xcode and CocoaPods. Previously recorded developer versions:
Xcode 27.1 and CocoaPods 1.17.0.

``` bash
sudo xcode-select --switch /Applications/Xcode.app/Contents/Developer
sudo xcodebuild -runFirstLaunch

# From Flutter app directory
flutter pub get
cd ios
pod install
cd ..

flutter devices
flutter run

# Release build; signing must be configured
flutter build ios --release
```

Physical-device release builds require a valid Apple Developer Team,
bundle identifier, provisioning profile and signing entitlements. The
iOS deployment target must be read from `ios/Podfile` and Xcode project
settings.

## Workflows

1.  Configure categories and products.
2.  Maintain product details, ingredients and survey questions.
3.  Create a product recipe and enter quantities/costs.
4.  Configure product- or category-scoped recipe standards.
5.  Validate the recipe.
6.  Create a survey round only if all applicable mandatory standards
    pass.
7.  Assign respondents and collect responses.
8.  Process responses and generate AI-assisted feedback.
9.  Translate feedback and generate audio where configured.
10. Review consumer reports, round comparisons and exports.

Exact role permissions and API routes are defined by backend
authorization and route code; do not infer permissions from UI
visibility alone.

## Recipe costing and standards

Recipe costing uses ingredient quantities, unit conversions and
ingredient cost/kg to calculate usage, cost contribution, total recipe
cost/kg and batch cost. Verify units and conversion rules before using
financial results.

Illustrative rule examples:

  -----------------------------------------------------------------------
  Rule                    Example                 Expected result
  ----------------------- ----------------------- -----------------------
  Required ingredient     Mango pulp required     Ingredient must exist

  Ingredient share        Sugar 7--10%            Actual share within
                                                  range

  Maximum recipe cost     Total recipe ≤ ₹90/kg   Total cost within
                                                  maximum

  Ingredient cost         Cardamom powder ≤       Selected ingredient
                          ₹2,500/kg               cost within maximum

  Complete costing        All ingredients have    No missing ingredient
                          cost/kg                 costs
  -----------------------------------------------------------------------

These values are development examples, not statutory food standards.
Client-approved internal standards and applicable Codex/Ghana Standards
Authority requirements must be supplied and verified separately before
being represented as compliance criteria.

### Mango Lassi development example

A 100 kg recipe was previously discussed with these cost inputs:

  Ingredient                Quantity   Cost/kg
  ----------------------- ---------- ---------
  Set curd / dahi              55 kg       ₹68
  Alphonso mango pulp          20 kg      ₹125
  Toned milk                 14.5 kg       ₹56
  Refined sugar               8.5 kg       ₹46
  RO water                   1.62 kg     ₹0.50
  High-methoxyl pectin       0.30 kg    ₹1,850
  Green cardamom powder      0.05 kg    ₹2,600
  Saffron extract            0.03 kg    ₹9,500

The recorded total was 100 kg, approximately ₹84.14/kg and approximately
₹8,413.81 batch cost. Recalculate from live database values before
operational use.

Test case: Cardamom Powder at ₹2,600/kg against a maximum ₹2,500/kg must
return **NOT MET**. This is independent of total recipe cost validation.

## AI, translation and audio

The configured model identifiers are `qwen2.5:7b` for text generation
and `nomic-embed-text` for embeddings where invoked by backend services.

Multilingual feedback and translation have been explored for English,
Hindi, Gujarati transliteration, Marathi, Tamil, Telugu, Bengali,
Kannada, Malayalam, Punjabi, Yoruba and Maithili. Translation quality
must be tested against approved source examples; no library/model should
be described as 100% accurate for every language and domain.

Audio generation may use a configured provider or local processing.
Confirm provider, voice, output format and credentials in current
backend configuration. Amazon Polly was discussed as a possible AWS
option; do not assume it is enabled unless configured.

## Reporting and exports

Reporting work includes product-level consumer reports, survey-round
reports, round comparisons, PDF downloads where enabled, analytics
views/charts and Excel exports.

A utility named `export_mysql_to_excel.py` was used to export MySQL
tables. A previous export included:

`analysis_jobs`, `categories`, `consolidated_reports`,
`feedback_aspects`, `feedback_rating_rules`, `feedback_records`,
`product_assignments`, `products`, `respondents`, `survey_answers`,
`survey_questions`, `survey_responses` and `users`.

The previously used export script read `MYSQL_USER`, `MYSQL_PASSWORD`
and `MYSQL_DATABASE` environment variables. Confirm the current script
path and required settings before use.

Example PowerShell configuration:

``` powershell
$env:MYSQL_USER = "<mysql_user>"
$env:MYSQL_PASSWORD = "<mysql_password>"
$env:MYSQL_DATABASE = "fmcg_learning"
python export_mysql_to_excel.py
```

Final report fields and layout should follow client-approved templates.

## Database and catalog initialization

The database used during development is `fmcg_learning`. Initialization
utilities discussed include `seed.py` and `seed_catalog.py`.

Catalog records must be database-driven. Adding a product, category,
recipe or survey question should not require manual edits to a
hard-coded seed catalog.

Before running a seed script: 1. Read whether it inserts, updates,
deletes or truncates records. 2. Confirm whether it is intended for an
empty database or an existing one. 3. Back up important data. 4. Apply
schema changes through migrations where supported. 5. Keep seed
operations idempotent where practical.

Never run a destructive reset against production data.

## Configuration

Exact environment variable names are defined by the current
backend/frontend/mobile source and example files.

  -----------------------------------------------------------------------
  Area                                Required configuration
  ----------------------------------- -----------------------------------
  MySQL                               Host, port, username, password,
                                      database

  Authentication                      Secret key, token settings and
                                      related auth configuration

  Web frontend                        Backend API base URL

  Flutter                             API base URL / compile-time define

  Ollama                              Service URL, LLM model, embedding
                                      model

  File storage                        Storage root and attachment
                                      directories

  Translation/audio                   Provider, endpoint, credentials,
                                      voice settings if external services
                                      are enabled

  CORS                                Allowed frontend origins
  -----------------------------------------------------------------------

Commit sanitized `.env.example` files, never actual `.env` secrets,
private keys, production database dumps or respondent data.

## Testing

Backend (if `pytest` and tests are configured):

``` bash
cd backend
python -m compileall app
pytest
```

Frontend:

``` bash
cd frontend
npm ci
npm run build
```

Flutter:

``` bash
flutter pub get
flutter analyze
flutter test
```

Android:

``` bash
flutter build apk --release
flutter build appbundle --release
```

iOS (macOS only, signing/configuration required):

``` bash
flutter build ios --release
```

A successful compile does not prove database connectivity, model
availability, signing or end-to-end workflow correctness.

## Deployment direction

AWS deployment has been discussed for browser-accessible operation and
higher concurrency. Topics in the proposed design include: - Amazon
CloudFront for frontend delivery. - Application Load Balancer for API
traffic. - FastAPI application instances/services. - Managed MySQL
hosting. - GPU-backed inference workers for Ollama/Qwen. - Queue-based
asynchronous processing, retries and a Dead Letter Queue (DLQ). - Amazon
Polly as a possible managed text-to-speech service.

A planning workload discussed was 300 simultaneous survey respondents
with text feedback, translation and audio generation targeted within a
one-minute processing SLA. GPU worker counts, throughput and monthly
cost require benchmarking against prompt sizes, output lengths,
quantization, GPU type and concurrency. These are architecture/planning
topics, not proof of a deployed AWS environment.

For production, separate survey submission from expensive AI processing
where appropriate. Use bounded queues, worker concurrency limits,
retries, monitoring and a DLQ for failed asynchronous jobs.

## Security

-   Store secrets in environment variables or a secrets manager.
-   Restrict database access to the backend.
-   Use HTTPS in deployed environments.
-   Enforce role-based authorization in backend endpoints.
-   Check organization/product ownership on relevant API operations.
-   Do not expose respondent personal data in organization-facing
    aggregate reports.
-   Validate uploaded file types and sizes.
-   Back up MySQL and uploaded files.
-   Separate development, test and production configuration.
-   Do not expose local Ollama endpoints directly to the public
    internet.

## Release checklist

-   [ ] All standard kinds are accepted, stored, edited, retrieved and
    evaluated by the API.
-   [ ] `ingredient_cost_per_kg` validates the selected ingredient's
    cost, not total recipe cost.
-   [ ] Product standards apply only to their product.
-   [ ] Category standards apply only to matching products.
-   [ ] Unscoped standards are not treated as global.
-   [ ] Recipe results show standard name, actual value, expected value
    and failure reason.
-   [ ] Backend strictly blocks survey-round creation if any applicable
    mandatory standard fails.
-   [ ] No Super Admin override or bypass exists.
-   [ ] Backend revalidates the latest recipe at round creation and
    returns HTTP 409 with failed-standard details.
-   [ ] Frontend and Flutter clients handle the rejection and do not
    create/assign a round.
-   [ ] New catalog data is database-driven; seed files need no manual
    catalog edits.
-   [ ] AI feedback, translation and audio work with configured
    providers/models.
-   [ ] Excel/PDF/report exports work against the current schema.
-   [ ] Backend tests, frontend build and Flutter analysis/build pass.

## Troubleshooting

  -----------------------------------------------------------------------
  Problem                             Checks
  ----------------------------------- -----------------------------------
  Backend cannot connect to MySQL     MySQL service, host/port,
                                      credentials, database name and
                                      firewall

  Python installation fails           Activate virtual environment; use
                                      CI Python target; install committed
                                      requirements

  Frontend API errors                 Backend status, API URL, CORS and
                                      browser network console

  Ollama model missing                `ollama list`; pull the configured
                                      model tag

  Phone cannot reach backend          Use computer LAN IP; check same
                                      network and firewall

  Android build failure               SDK components, licences,
                                      JDK/Gradle/AGP compatibility

  iOS CocoaPods/build failure         Xcode selection, CocoaPods, Podfile
                                      deployment target, `pod install`

  iOS install/signing failure         Apple Team, bundle ID, provisioning
                                      profile, entitlements and device
                                      trust

  React object-rendering error        Convert API/Pydantic error objects
                                      to readable strings before
                                      rendering
  -----------------------------------------------------------------------

## Version matrix

Previously recorded development environment:

  Component         Version
  ----------------- -------------------------------
  Git               2.53.0
  Python target     3.12
  Node.js           22.21.1
  npm               10.9.4
  MySQL             9.6.0 (ARM64 Mac environment)
  Flutter           3.47.8
  Dart              3.13.7
  Xcode             27.1
  CocoaPods         1.17.0
  LLM               `qwen2.5:7b`
  Embedding model   `nomic-embed-text`

Previously observed Python package versions include FastAPI `0.141.1`,
Uvicorn `0.54.0`, Pydantic `2.13.5`, `python-multipart` `0.0.32`,
`requests` `2.34.2`, NumPy `2.5.3`, `faster-whisper` `1.2.1` and
`onnxruntime` `1.30.0`. These are historical observations; the committed
dependency lock/requirements file is authoritative.

Exact Android SDK/build-tools, Java/JDK, Gradle, Android Gradle Plugin,
iOS deployment target, Ollama application version, web app version and
Flutter app release version were not confirmed in the available project
context. Read them from Android build files, `ios/Podfile`,
`frontend/package.json` and Flutter `pubspec.yaml`. Do not publish
guessed versions.

## Contributing and change discipline

-   Keep backend schemas, database models, API responses, React clients
    and Flutter models synchronized.
-   Add migrations for persistent schema changes.
-   Add tests for recipe costing, standard scope, standard validation
    and survey-round gating.
-   Do not commit local secrets, database dumps, build outputs,
    generated model files or personal respondent data.
-   Update this README when setup steps, dependency versions, workflows
    or major capabilities change.
