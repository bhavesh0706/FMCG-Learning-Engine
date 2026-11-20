#!/usr/bin/env bash
# =============================================================================
# FMCG Learning Engine: upload the code from this computer to the Ubuntu server
# and update everything there (backend, AI service, web frontend, PM2).
#
# Run from the project folder on your Mac / PC:
#   ./scripts/deploy.sh                 # upload + update + restart
#   ./scripts/deploy.sh --dry-run       # only show which files would be uploaded
#   ./scripts/deploy.sh --no-upload     # code already on the server (git pull): update only
#   ./scripts/deploy.sh --skip-frontend # don't rebuild the web app
#   ./scripts/deploy.sh --skip-ai       # don't touch the AI service
#
# Settings: edit the four lines below, or pass them as environment variables:
#   SERVER=ubuntu@1.2.3.4 SSH_KEY=~/keys/server.pem ./scripts/deploy.sh
#
# Never uploaded / never overwritten on the server: every .env file, Python venvs,
# node_modules, the built frontend, recordings (uploads/), ecosystem.config.js,
# the mobile app and logs. Each run first backs up the server's .env files and
# (when the fmcg-mysql Docker container exists) the database.
# =============================================================================
set -euo pipefail

SERVER="${SERVER:-ubuntu@CHANGE_ME_SERVER_IP}"                                   # user@ip of the server
SSH_KEY="${SSH_KEY:-}"                                                            # e.g. ~/keys/server.pem (empty = your default SSH key)
APP_DIR="${APP_DIR:-/home/ubuntu/fmcg-learning/FMCG-Learning-Engine-main}"        # project folder on the server
SSH_PORT="${SSH_PORT:-22}"

DRY_RUN=0; UPLOAD=1; FRONTEND=1; AI=1
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=1 ;;
    --no-upload) UPLOAD=0 ;;
    --skip-frontend) FRONTEND=0 ;;
    --skip-ai) AI=0 ;;
    -h|--help) sed -n 2,21p "$0"; exit 0 ;;
    *) echo "Unknown option: $arg (see --help)"; exit 1 ;;
  esac
done

if [[ "$SERVER" == *CHANGE_ME* ]]; then
  echo "Set SERVER first: edit scripts/deploy.sh or run  SERVER=ubuntu@<server-ip> ./scripts/deploy.sh"
  exit 1
fi

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# A full rsync (3.x) is needed: macOS ships "openrsync", which lacks the filters that
# protect the server's .env files and recordings.
RSYNC=""
for r in /opt/homebrew/bin/rsync /usr/local/bin/rsync "$(command -v rsync || true)"; do
  if [[ -x "$r" ]] && "$r" --version 2>/dev/null | head -1 | grep -qE 'rsync +version +3'; then RSYNC="$r"; break; fi
done
if [[ $UPLOAD -eq 1 && -z "$RSYNC" ]]; then
  echo "rsync 3 is needed. On a Mac:  brew install rsync   (Ubuntu/WSL: sudo apt install -y rsync)"
  exit 1
fi
SSH_OPTS=(-p "$SSH_PORT" -o ServerAliveInterval=30)
[[ -n "$SSH_KEY" ]] && SSH_OPTS+=(-i "${SSH_KEY/#\~/$HOME}")
SSH_CMD="ssh ${SSH_OPTS[*]}"

say() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

say "Checking the connection to $SERVER"
ssh "${SSH_OPTS[@]}" "$SERVER" "mkdir -p '$APP_DIR' && echo connected"

# ----------------------------------------------------------------------------- upload
if [[ $UPLOAD -eq 1 ]]; then
  say "Uploading code to $SERVER:$APP_DIR"
  RSYNC_FLAGS=(-az --delete --human-readable --info=stats1)
  [[ $DRY_RUN -eq 1 ]] && RSYNC_FLAGS+=(--dry-run --itemize-changes)
  "$RSYNC" "${RSYNC_FLAGS[@]}" -e "$SSH_CMD" --rsync-path="mkdir -p '$APP_DIR' && rsync" \
    --filter='P ai_service/models/***' \
    --exclude='.git/' --exclude='.idea/' --exclude='.vscode/' --exclude='.DS_Store' \
    --include='.env.example' --exclude='.env' --exclude='.env.*' \
    --exclude='venv/' --exclude='.venv/' --exclude='__pycache__/' --exclude='*.pyc' --exclude='.pytest_cache/' \
    --exclude='node_modules/' --exclude='frontend/dist/' \
    --exclude='/uploads/' --exclude='/uploads_backup_*/' --exclude='backend/uploads/' --exclude='storage/' \
    --exclude='ai_service/work/' --exclude='/mobile/' --exclude='/build/' \
    --exclude='/ecosystem.config.js' --exclude='*.log' --exclude='logs/' \
    "$PROJECT_DIR/" "$SERVER:$APP_DIR/"
  if [[ $DRY_RUN -eq 1 ]]; then
    echo; echo "Dry run: nothing was uploaded or changed on the server."
    exit 0
  fi
fi

# ----------------------------------------------------------------------------- update on the server
say "Updating the server"
ssh "${SSH_OPTS[@]}" "$SERVER" "bash -s" -- "$APP_DIR" "$FRONTEND" "$AI" <<'REMOTE'
set -euo pipefail
APP_DIR="$1"; FRONTEND="$2"; AI="$3"
cd "$APP_DIR"
say()  { printf '\n\033[1;32m--> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33mWARNING: %s\033[0m\n' "$*"; }
fail() { printf '\033[1;31mERROR: %s\033[0m\n' "$*"; exit 1; }

PY="$(command -v python3.12 || command -v python3 || true)"
[[ -n "$PY" ]] || fail "Python 3 is not installed (sudo apt install -y python3.12 python3.12-venv)."
command -v pm2 >/dev/null || fail "PM2 is not installed (sudo npm install -g pm2)."

# 1. Backups ------------------------------------------------------------------
STAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_DIR="$HOME/fmcg-backups/$STAMP"
mkdir -p "$BACKUP_DIR"
say "Backing up .env files to $BACKUP_DIR"
for f in backend/.env ai_service/.env frontend/.env.production.local ecosystem.config.js; do
  if [[ -f "$f" ]]; then cp -p "$f" "$BACKUP_DIR/$(echo "$f" | tr / _)"; fi
done
if command -v docker >/dev/null && docker ps --format '{{.Names}}' 2>/dev/null | grep -qx fmcg-mysql; then
  # The same account the backend uses (backend/.env)
  DB_PASS="$(grep -E '^MYSQL_PASSWORD=' backend/.env 2>/dev/null | head -1 | cut -d= -f2- || true)"
  DB_NAME="$(grep -E '^MYSQL_DATABASE=' backend/.env 2>/dev/null | cut -d= -f2- || true)"; DB_NAME="${DB_NAME:-fmcg_learning}"
  DB_USER="$(grep -E '^MYSQL_USER=' backend/.env 2>/dev/null | cut -d= -f2- || true)"; DB_USER="${DB_USER:-root}"
  if docker exec -e MYSQL_PWD="$DB_PASS" fmcg-mysql mysqldump -u"$DB_USER" --single-transaction --routines --triggers "$DB_NAME" 2>/dev/null \
      | gzip > "$BACKUP_DIR/${DB_NAME}.sql.gz" && [[ -s "$BACKUP_DIR/${DB_NAME}.sql.gz" ]]; then
    echo "Database backed up: $BACKUP_DIR/${DB_NAME}.sql.gz"
  else
    rm -f "$BACKUP_DIR/${DB_NAME}.sql.gz"; warn "Database backup failed; continuing (check MySQL credentials in backend/.env)."
  fi
else
  echo "(no fmcg-mysql Docker container: database backup skipped)"
fi
ls -1dt "$HOME"/fmcg-backups/*/ 2>/dev/null | tail -n +11 | xargs -r rm -rf   # keep the last 10

# 2. Backend -------------------------------------------------------------------
say "Backend"
[[ -f backend/.env ]] || fail "backend/.env is missing. Create it: cp backend/.env.example backend/.env && nano backend/.env"
[[ -d backend/venv ]] || "$PY" -m venv backend/venv
backend/venv/bin/pip install -q --upgrade pip wheel
backend/venv/bin/pip install -q -r backend/requirements.txt
# Insert-only seed (safe on every deployment); new columns/tables are added when the backend starts.
(cd backend && venv/bin/python -m app.database.seed) || warn "Seed step reported a problem (see above)."

# 3. AI service ----------------------------------------------------------------
if [[ "$AI" == "1" && -d ai_service ]]; then
  say "AI service"
  command -v ffmpeg >/dev/null || warn "ffmpeg is not installed: sudo apt install -y ffmpeg (needed to read recordings)."
  if [[ ! -f ai_service/.env ]]; then
    warn "ai_service/.env is missing: cp ai_service/.env.example ai_service/.env && nano ai_service/.env"
  else
    [[ -d ai_service/venv ]] || "$PY" -m venv ai_service/venv
    ai_service/venv/bin/pip install -q --upgrade pip wheel
    ai_service/venv/bin/pip install -q -r ai_service/requirements.txt
    # OpenAI key stored encrypted: the master key must exist on THIS server too.
    if grep -qiE '^USE_OLLAMA=(false|0|no|off)' ai_service/.env && grep -qE '^OPENAI_API_KEY_ENC=.+' ai_service/.env; then
      if [[ ! -s "$HOME/.fmcg/master.key" ]] && ! grep -q 'FMCG_MASTER_KEY' ecosystem.config.js 2>/dev/null \
          && ! grep -qE '^FMCG_MASTER_KEY_FILE=.+' ai_service/.env; then
        warn "No master key on this server to unlock OPENAI_API_KEY_ENC. Copy it from your Mac:
         scp ~/.fmcg/master.key $USER@<this-server>:~/.fmcg/master.key && chmod 600 ~/.fmcg/master.key
         (or run 'ai_service/venv/bin/python ai_service/tools/encrypt_openai_key.py' here)."
      fi
    fi
  fi
fi

# 4. Web frontend ----------------------------------------------------------------
if [[ "$FRONTEND" == "1" ]]; then
  say "Web frontend"
  command -v npm >/dev/null || fail "Node.js/npm is not installed."
  [[ -f frontend/.env.production.local ]] || fail "frontend/.env.production.local is missing. Create it:
       echo \"VITE_API_BASE_URL=http://<server-ip-or-domain>:8000\" > frontend/.env.production.local"
  (cd frontend && npm ci --no-audit --no-fund --loglevel=error && npm run build)
fi

# 5. PM2 -------------------------------------------------------------------------
say "PM2"
if [[ ! -f ecosystem.config.js ]]; then
  echo "Creating ecosystem.config.js"
  AI_BLOCK=""
  if [[ -f ai_service/.env && -d ai_service/venv ]]; then
    AI_BLOCK="
    {
      name: 'fmcg-ai',
      cwd: \`\${APP_DIR}/ai_service\`,
      script: \`\${APP_DIR}/ai_service/venv/bin/uvicorn\`,
      args: 'main:app --host 127.0.0.1 --port 8001',
      interpreter: 'none',
      autorestart: true,
      env: { PYTHONUNBUFFERED: '1', APP_ENV: 'production' },
    },"
  fi
  cat > ecosystem.config.js <<EOF
const APP_DIR = '$APP_DIR';

module.exports = {
  apps: [
    {
      name: 'fmcg-backend',
      cwd: \`\${APP_DIR}/backend\`,
      script: \`\${APP_DIR}/backend/venv/bin/python\`,
      args: 'run.py',
      interpreter: 'none',
      autorestart: true,
      max_memory_restart: '1G',
      env: { PYTHONUNBUFFERED: '1' },
    },$AI_BLOCK
    {
      name: 'fmcg-frontend',
      script: 'serve',
      env: {
        PM2_SERVE_PATH: \`\${APP_DIR}/frontend/dist\`,
        PM2_SERVE_PORT: 5174,
        PM2_SERVE_SPA: 'true',
        PM2_SERVE_HOMEPAGE: '/index.html',
      },
    },
  ],
};
EOF
fi
APPS=(fmcg-backend fmcg-frontend)
[[ "$AI" == "1" ]] && grep -q "fmcg-ai" ecosystem.config.js && APPS+=(fmcg-ai)
for app in "${APPS[@]}"; do
  pm2 startOrReload ecosystem.config.js --only "$app" --update-env >/dev/null
done
pm2 save >/dev/null
pm2 status

# 6. Health checks -----------------------------------------------------------------
say "Health checks"
check() {  # name url
  for _ in $(seq 1 30); do
    if curl -fsS -m 5 "$2" >/dev/null 2>&1; then echo "OK    $1  ($2)"; return 0; fi
    sleep 2
  done
  warn "$1 did not answer at $2. Logs: pm2 logs $3 --lines 50 --nostream"
  return 1
}
STATUS=0
check "Backend API " "http://127.0.0.1:8000/health" fmcg-backend || STATUS=1
if grep -q "fmcg-ai" ecosystem.config.js && [[ "$AI" == "1" ]]; then
  check "AI service  " "http://127.0.0.1:8001/health" fmcg-ai || STATUS=1
fi
if [[ "$FRONTEND" == "1" ]]; then
  check "Web frontend" "http://127.0.0.1:5174/" fmcg-frontend || STATUS=1
fi
echo
echo "Backups of this run: $BACKUP_DIR"
[[ $STATUS -eq 0 ]] && echo "Deployment finished." || { echo "Deployment finished with warnings (see above)."; exit 1; }
REMOTE
