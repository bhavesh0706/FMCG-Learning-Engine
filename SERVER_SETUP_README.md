# FMCG Learning Engine: Fresh Ubuntu Server Setup

Step-by-step guide for installing the whole stack on a **brand-new Ubuntu server**
(22.04 or 24.04), then building the **Android** and **iOS** apps that talk to it.

What runs on the server:

| Part            | How it runs                     | Port                        |
|-----------------|---------------------------------|-----------------------------|
| MySQL 8.0       | Docker container `fmcg-mysql`   | 3307 (localhost only)       |
| Backend API     | PM2 → `python run.py` (FastAPI) | 8000                        |
| AI service      | PM2 → uvicorn (optional)        | 8001 (localhost only)       |
| Ollama (LLM)    | systemd service (optional)      | 11434 (localhost only)      |
| Web frontend    | PM2 → `pm2 serve dist`          | 5174 (or 5173)              |

> Commands assume you log in as a normal sudo user (for example `ubuntu`).
> Replace every `CHANGE_ME...` value with your own.

---

## Contents

1. [Before you start (cloud firewall)](#1-before-you-start-cloud-firewall)
2. [Update the server and install basic tools](#2-update-the-server-and-install-basic-tools)
3. [Install Docker](#3-install-docker)
4. [Install Python 3.12](#4-install-python-312)
5. [Install Node.js 22 and PM2](#5-install-nodejs-22-and-pm2)
6. [Install ffmpeg](#6-install-ffmpeg)
7. [Start MySQL in Docker](#7-start-mysql-in-docker)
8. [Copy the project to the server](#8-copy-the-project-to-the-server)
9. [Backend setup](#9-backend-setup)
10. [Database: seed a new one, or restore a dump](#10-database-seed-a-new-one-or-restore-a-dump)
11. [Frontend build](#11-frontend-build)
12. [AI service + Ollama (optional)](#12-ai-service--ollama-optional)
13. [PM2 config and start everything](#13-pm2-config-and-start-everything)
14. [Auto-start after reboot](#14-auto-start-after-reboot)
15. [Ubuntu firewall (ufw)](#15-ubuntu-firewall-ufw)
16. [Check that everything works](#16-check-that-everything-works)
17. [Optional: domain + HTTPS with Nginx](#17-optional-domain--https-with-nginx)
18. [Updating the app later](#18-updating-the-app-later)
19. [Backups](#19-backups)
20. [Troubleshooting](#20-troubleshooting)
21. [Android app (Flutter)](#21-android-app-flutter)
22. [iOS app (Flutter)](#22-ios-app-flutter)

---

## 1. Before you start (cloud firewall)

In your cloud console (AWS Security Group / Oracle Security List / etc.) allow inbound:

| Port | Why                                      |
|------|------------------------------------------|
| 22   | SSH                                      |
| 8000 | Backend API (browser + mobile apps)      |
| 5174 | Web frontend                             |
| 80, 443 | Only if you set up Nginx + HTTPS (step 17) |

Do **not** open 3307 (MySQL), 8001 (AI service) or 11434 (Ollama) to the internet.

Recommended server size: 2 vCPU / 4 GB RAM without AI.
With the AI service + `qwen2.5:7b`: at least 8 vCPU / 16 GB RAM (or a GPU).

---

## 2. Update the server and install basic tools

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y git curl wget unzip zip vim nano htop ca-certificates gnupg \
    lsb-release software-properties-common build-essential pkg-config \
    libmysqlclient-dev mysql-client
sudo timedatectl set-timezone Asia/Kolkata     # optional
```

---

## 3. Install Docker

```bash
# Remove any old versions (safe if nothing is installed)
for p in docker.io docker-doc docker-compose podman-docker containerd runc; do sudo apt remove -y $p; done

# Add Docker's official repository
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
 | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Start on boot, and let your user run docker without sudo
sudo systemctl enable --now docker
sudo usermod -aG docker $USER
newgrp docker            # or log out and back in

docker --version
docker run --rm hello-world
```

---

## 4. Install Python 3.12

**Ubuntu 24.04** already ships Python 3.12:

```bash
sudo apt install -y python3.12 python3.12-venv python3.12-dev python3-pip
```

**Ubuntu 22.04** needs the deadsnakes PPA:

```bash
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update
sudo apt install -y python3.12 python3.12-venv python3.12-dev
```

Check:

```bash
python3.12 --version
```

---

## 5. Install Node.js 22 and PM2

```bash
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs
node -v && npm -v

sudo npm install -g pm2
pm2 -v
```

---

## 6. Install ffmpeg

Needed for audio/video processing.

```bash
sudo apt install -y ffmpeg
ffmpeg -version | head -1
```

(If you need the newest static build instead, use the johnvansickle build:
`cd /tmp && curl -LO https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz && tar -xf ffmpeg-release-amd64-static.tar.xz && sudo cp ffmpeg-*-amd64-static/ffmpeg ffmpeg-*-amd64-static/ffprobe /usr/local/bin/`.)

---

## 7. Start MySQL in Docker

Always use a **real password**. An empty `MYSQL_ROOT_PASSWORD=''` makes the
container refuse to start.

```bash
export MYSQL_ROOT_PW='CHANGE_ME_StrongDbPassword'

docker run -d --name fmcg-mysql --restart unless-stopped \
  -e MYSQL_ROOT_PASSWORD="$MYSQL_ROOT_PW" \
  -e MYSQL_DATABASE=fmcg_learning \
  -p 127.0.0.1:3307:3306 \
  -v fmcg_mysql_data:/var/lib/mysql \
  mysql:8.0 \
  --character-set-server=utf8mb4 --collation-server=utf8mb4_unicode_ci

# Wait until it is ready (look for "ready for connections")
sleep 25 && docker logs fmcg-mysql --tail 3

# Test the login
docker exec -it fmcg-mysql mysql -uroot -p"$MYSQL_ROOT_PW" -e "SHOW DATABASES;"
```

If you get *"container name already in use"*: `docker rm -f fmcg-mysql` and run again
(your data stays in the `fmcg_mysql_data` volume).

---

## 8. Copy the project to the server

Pick **one** option.

**A. From Git**

```bash
mkdir -p ~/fmcg-learning && cd ~/fmcg-learning
git clone <YOUR_REPO_URL> FMCG-Learning-Engine
cd FMCG-Learning-Engine
```

**B. From a zip** (upload from your PC first)

```bash
# On your PC:
scp -i your-key.pem FMCG-Learning-Engine-main.zip ubuntu@SERVER_IP:~/

# On the server:
mkdir -p ~/fmcg-learning && cd ~/fmcg-learning
mv ~/FMCG-Learning-Engine-main.zip . && unzip FMCG-Learning-Engine-main.zip
cd FMCG-Learning-Engine-main
```

Set two helper variables (re-run these in every new SSH session):

```bash
export APP_DIR=$(pwd)
export PUBLIC_IP=$(curl -s https://checkip.amazonaws.com)
echo $APP_DIR $PUBLIC_IP

# Remove anything copied from another machine
rm -rf backend/venv ai_service/venv frontend/node_modules frontend/dist
```

---

## 9. Backend setup

### 9.1 Create `backend/.env`

```bash
cd $APP_DIR/backend
cp .env.example .env
nano .env
```

Set at least these values:

```ini
APP_ENV=production

BACKEND_HOST=0.0.0.0
BACKEND_PORT=8000
BACKEND_RELOAD=false

# Web address(es) of the frontend, comma separated
CORS_ORIGINS=http://YOUR_PUBLIC_IP:5174

AI_SERVICE_URL=http://127.0.0.1:8001
# Required when APP_ENV=production. Generate: openssl rand -hex 32
AI_SERVICE_TOKEN=CHANGE_ME_long_random_token

# Generate: openssl rand -hex 32
FMCG_SECRET_KEY=CHANGE_ME_long_random_secret

MYSQL_HOST=127.0.0.1
MYSQL_PORT=3307
MYSQL_DATABASE=fmcg_learning
MYSQL_USER=root
MYSQL_PASSWORD=CHANGE_ME_StrongDbPassword

FMCG_MAX_UPLOAD_MB=500
FMCG_TRUST_PROXY=false
LOG_LEVEL=INFO
```

Quick way to set CORS from the shell:

```bash
sed -i "s#^CORS_ORIGINS=.*#CORS_ORIGINS=http://${PUBLIC_IP}:5174#" .env
grep -E "CORS|MYSQL|APP_ENV|BACKEND" .env
```

> Keep `FMCG_SECRET_KEY` the same forever. If it changes, every user is signed out.

### 9.2 Python virtual environment

```bash
cd $APP_DIR/backend
python3.12 -m venv venv
source venv/bin/activate
pip install --upgrade pip wheel
pip install -r requirements.txt
deactivate
```

---

## 10. Database: seed a new one, or restore a dump

### Option A: Brand-new empty database

```bash
cd $APP_DIR/backend
source venv/bin/activate

# Optional: create the first admin now (else create it on the website's
# "Set up your workspace" page)
export FMCG_SEED_ADMIN_NAME="Admin Name"
export FMCG_SEED_ADMIN_IDENTIFIER="9876543210"      # mobile or email
export FMCG_SEED_ADMIN_PASSWORD="CHANGE_ME_AdminPassword"

python -m app.database.seed --dry-run   # preview, writes nothing
python -m app.database.seed             # create tables + baseline data
python -m app.database.seed --check     # show what is in the database
deactivate
```

### Option B: Restore an existing dump (from the old server)

```bash
# On the OLD server: create the dump
docker exec fmcg-mysql mysqldump -uroot -p'OLD_PASSWORD' --routines --triggers fmcg_learning > Dump.sql

# Copy it to the new server (from your PC or old server)
scp Dump.sql ubuntu@NEW_SERVER_IP:~/fmcg-learning/

# On the NEW server: import it
cd ~/fmcg-learning
docker exec -i fmcg-mysql mysql -uroot -p"$MYSQL_ROOT_PW" fmcg_learning < Dump.sql
docker exec -it fmcg-mysql mysql -uroot -p"$MYSQL_ROOT_PW" fmcg_learning -e "SHOW TABLES;"

# Bring the schema up to date (insert-only, safe to run any time)
cd $APP_DIR/backend
source venv/bin/activate && python -m app.database.seed --check && deactivate
```

### Copy uploaded recordings (only when moving from an old server)

The database stores relative keys like `videos/123_x.mp4`, so just copy the folders:

```bash
# Run on the OLD server
rsync -avz ~/fmcg-learning/FMCG-Learning-Engine-main/uploads/ \
    ubuntu@NEW_SERVER_IP:~/fmcg-learning/FMCG-Learning-Engine-main/uploads/
rsync -avz ~/fmcg-learning/FMCG-Learning-Engine-main/backend/uploads/ \
    ubuntu@NEW_SERVER_IP:~/fmcg-learning/FMCG-Learning-Engine-main/backend/uploads/
```

---

## 11. Frontend build

The variable name is **`VITE_API_BASE_URL`** (the build stops with an error if it is missing).
It is the backend address **as the browser sees it**.

```bash
cd $APP_DIR/frontend
echo "VITE_API_BASE_URL=http://${PUBLIC_IP}:8000" > .env.production.local
cat .env.production.local

npm ci
npm run build

# Confirm the IP is baked into the build
grep -o "${PUBLIC_IP}:8000" dist/assets/*.js | head -1
```

> Every time the server IP / domain changes you must **rebuild** the frontend.

---

## 12. AI service + Ollama (optional)

Skip this whole step if you don't need AI analysis, translation or transcription.
The rest of the app still works.

### 12.1 Install Ollama and pull the model

```bash
curl -fsSL https://ollama.com/install.sh | sh
sudo systemctl enable --now ollama
ollama pull qwen2.5:7b
ollama list
curl -s http://127.0.0.1:11434/api/tags | head -c 200; echo
```

### 12.2 AI service virtual environment

```bash
cd $APP_DIR/ai_service
cp .env.example .env
nano .env
#   AI_SERVICE_TOKEN = same value as backend/.env
#   USE_OLLAMA=true  -> Ollama (step 12.1 needed)
#   USE_OLLAMA=false -> OpenAI: set OPENAI_API_KEY=sk-... (skip step 12.1)

python3.12 -m venv venv
source venv/bin/activate
pip install --upgrade pip wheel
pip install -r requirements.txt

# Only if you need translation (CPU PyTorch, large download):
# pip install torch --index-url https://download.pytorch.org/whl/cpu
# pip install -r requirements-translation.txt
deactivate
```

---

## 13. PM2 config and start everything

Create `ecosystem.config.js` in the project root (`$APP_DIR`). Replace the
paths if your folder is different, or run the `sed` line below.

```bash
cd $APP_DIR
cat > ecosystem.config.js <<'EOF'
const APP_DIR = '/home/ubuntu/fmcg-learning/FMCG-Learning-Engine-main';

module.exports = {
  apps: [
    {
      name: 'fmcg-backend',
      cwd: `${APP_DIR}/backend`,
      script: `${APP_DIR}/backend/venv/bin/python`,
      args: 'run.py',
      interpreter: 'none',
      autorestart: true,
      max_memory_restart: '1G',
      env: { PYTHONUNBUFFERED: '1' },
    },
    {
      // Remove this block if you skipped step 12
      name: 'fmcg-ai',
      cwd: `${APP_DIR}/ai_service`,
      script: `${APP_DIR}/ai_service/venv/bin/uvicorn`,
      args: 'main:app --host 127.0.0.1 --port 8001',
      interpreter: 'none',
      autorestart: true,
      // Other settings (token, USE_OLLAMA, OPENAI_API_KEY...) come from ai_service/.env
      env: { PYTHONUNBUFFERED: '1', APP_ENV: 'production' },
    },
    {
      name: 'fmcg-frontend',
      script: 'serve',
      env: {
        PM2_SERVE_PATH: `${APP_DIR}/frontend/dist`,
        PM2_SERVE_PORT: 5174,
        PM2_SERVE_SPA: 'true',
        PM2_SERVE_HOMEPAGE: '/index.html',
      },
    },
  ],
};
EOF

# Put your real folder path into the file
sed -i "s#/home/ubuntu/fmcg-learning/FMCG-Learning-Engine-main#${APP_DIR}#" ecosystem.config.js
head -3 ecosystem.config.js
```

> The AI service reads `ai_service/.env` at startup. Values in PM2's `env:` block win over it.

Check the frontend port is free, then start:

```bash
sudo ss -ltnp | grep -E ':(5173|5174|8000|8001)\b'    # should print nothing

pm2 start ecosystem.config.js
pm2 status
pm2 logs fmcg-backend --lines 30 --nostream
```

Useful PM2 commands:

```bash
pm2 status                       # list apps
pm2 logs                         # live logs (Ctrl+C to exit)
pm2 logs fmcg-backend --lines 50 --nostream
pm2 restart fmcg-backend --update-env   # after editing backend/.env
pm2 restart all
pm2 delete fmcg-frontend && pm2 start ecosystem.config.js --only fmcg-frontend
```

---

## 14. Auto-start after reboot

```bash
pm2 startup systemd
# PM2 prints a "sudo env PATH=... pm2 startup ..." command: copy and run it.
pm2 save
```

Docker MySQL already has `--restart unless-stopped`, Docker itself is enabled
(step 3), and Ollama is a systemd service. Test with `sudo reboot`, then `pm2 status`.

Optional log rotation:

```bash
pm2 install pm2-logrotate
pm2 set pm2-logrotate:max_size 20M
pm2 set pm2-logrotate:retain 10
```

---

## 15. Ubuntu firewall (ufw)

```bash
sudo ufw allow OpenSSH
sudo ufw allow 8000/tcp
sudo ufw allow 5174/tcp
# sudo ufw allow 80/tcp && sudo ufw allow 443/tcp     # if using Nginx
sudo ufw enable
sudo ufw status
```

> Docker publishes MySQL on `127.0.0.1:3307` only, so it stays private.

---

## 16. Check that everything works

```bash
curl -s http://127.0.0.1:8000/health; echo
curl -s http://127.0.0.1:8000/health/ready; echo
curl -s http://127.0.0.1:8001/health; echo          # if AI service installed
curl -sI http://127.0.0.1:5174 | head -1             # HTTP/1.1 200 OK
```

From your PC browser:

- Website: `http://SERVER_PUBLIC_IP:5174`
- API: `http://SERVER_PUBLIC_IP:8000/health`

Database console:

```bash
docker exec -it fmcg-mysql mysql -uroot -p"$MYSQL_ROOT_PW" fmcg_learning
docker exec -it fmcg-mysql mysql -uroot -p"$MYSQL_ROOT_PW" fmcg_learning \
   -e "SELECT id, name, mobile, role, is_active FROM users;"
```

---

## 17. Optional: domain + HTTPS with Nginx

Recommended for production, and **required for iOS App Store builds**
(Apple rejects plain `http://` without a reason).

Point two DNS A-records to the server IP, e.g. `app.example.com` and `api.example.com`.

```bash
sudo apt install -y nginx certbot python3-certbot-nginx

sudo tee /etc/nginx/sites-available/fmcg > /dev/null <<'EOF'
server {
    server_name app.example.com;
    location / { proxy_pass http://127.0.0.1:5174; proxy_set_header Host $host; }
}
server {
    server_name api.example.com;
    client_max_body_size 500M;
    proxy_read_timeout 600s;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF
sudo ln -s /etc/nginx/sites-available/fmcg /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d app.example.com -d api.example.com
```

Then update and rebuild:

```bash
# backend/.env
CORS_ORIGINS=https://app.example.com
FMCG_TRUST_PROXY=true

# frontend
echo "VITE_API_BASE_URL=https://api.example.com" > $APP_DIR/frontend/.env.production.local
cd $APP_DIR/frontend && npm run build
pm2 restart all --update-env
```

You can now close ports 8000 and 5174 in the cloud firewall / ufw.

---

## 18. Updating the app later

```bash
cd $APP_DIR
git pull                       # or upload and unzip the new code

# Backend
cd backend && source venv/bin/activate
pip install -r requirements.txt
python -m app.database.seed    # insert-only, safe every deployment
deactivate

# Frontend
cd ../frontend && npm ci && npm run build

pm2 restart all --update-env
```

---

## 19. Backups

Daily database dump at 2 AM, keeping 14 days:

```bash
mkdir -p ~/backups
crontab -e
```

Add (put your DB password in place of `CHANGE_ME_StrongDbPassword`):

```cron
0 2 * * * docker exec fmcg-mysql mysqldump -uroot -pCHANGE_ME_StrongDbPassword --routines --triggers fmcg_learning | gzip > ~/backups/fmcg_$(date +\%F).sql.gz && find ~/backups -name 'fmcg_*.sql.gz' -mtime +14 -delete
```

Also back up the `uploads/` and `backend/uploads/` folders (recordings), and
`backend/.env`.

---

## 20. Troubleshooting

| Problem | Fix |
|---|---|
| `container name "/fmcg-mysql" is already in use` | `docker rm -f fmcg-mysql`, run the `docker run` again (data is kept in the volume) |
| MySQL container exits right away | Password empty → use a real `MYSQL_ROOT_PASSWORD`. Check `docker logs fmcg-mysql` |
| `permission denied ... docker.sock` | `sudo usermod -aG docker $USER`, then log out and in |
| Backend: `port 8000 is already in use` | `sudo ss -ltnp \| grep 8000`, stop that process, or change `BACKEND_PORT` |
| Frontend port busy (5173 used by another app) | Change `PM2_SERVE_PORT` in `ecosystem.config.js` + `CORS_ORIGINS` in `backend/.env`, then `pm2 delete fmcg-frontend && pm2 start ecosystem.config.js --only fmcg-frontend && pm2 restart fmcg-backend --update-env` |
| Browser shows CORS error | `CORS_ORIGINS` must exactly match the address in the browser bar (scheme + IP/domain + port) |
| Build: `VITE_API_BASE_URL is not set` | Create `frontend/.env.production.local` (step 11). The name is `VITE_API_BASE_URL`, not `VITE_API_URL` |
| Website opens but login/API fails | Wrong IP baked into the build → fix `.env.production.local`, `npm run build` |
| `Access denied for user 'root'` | `MYSQL_PASSWORD` / `MYSQL_PORT=3307` in `backend/.env` don't match the container |
| Upload fails with 413 | Raise `FMCG_MAX_UPLOAD_MB`, and `client_max_body_size` in Nginx |
| Apps don't come back after reboot | Run `pm2 startup systemd` + the printed command, then `pm2 save` |
| AI features fail | `pm2 logs fmcg-ai`, `systemctl status ollama`, `ollama list`; tokens in both `.env` files must match |

---

## 21. Android app (Flutter)

The mobile app is in `mobile/` (Flutter). The **server address is set at build time**
with `--dart-define=API_BASE_URL=...`. Build it on your **PC/laptop**
(Windows, macOS or Linux), not on the server.

### 21.1 Install the tools (once)

1. **Git**: https://git-scm.com
2. **Flutter SDK** (stable): https://docs.flutter.dev/get-started/install
   Add `flutter/bin` to your PATH.
3. **Android Studio**: https://developer.android.com/studio
   - SDK Manager → install *Android SDK Platform (latest, API 37)*,
     *Android SDK Build-Tools*, *Android SDK Command-line Tools*, *Platform-Tools*.
4. **JDK 17** (Android Studio's bundled JDK is fine).
5. Accept licenses and check:

```bash
flutter doctor --android-licenses
flutter doctor
```

### 21.2 Get dependencies

```bash
cd FMCG-Learning-Engine/mobile
flutter clean
flutter pub get
```

### 21.3 Run on a phone (testing)

Turn on *Developer options → USB debugging* on the phone, connect via USB:

```bash
flutter devices
flutter run --dart-define=API_BASE_URL=http://SERVER_PUBLIC_IP:8000
```

(Android emulator talking to a backend on the same PC: `http://10.0.2.2:8000`, which is the default.)

### 21.4 Build an APK to share directly

```bash
flutter build apk --release --dart-define=API_BASE_URL=http://SERVER_PUBLIC_IP:8000
# Output: build/app/outputs/flutter-apk/app-release.apk
```

Smaller per-CPU APKs:

```bash
flutter build apk --release --split-per-abi --dart-define=API_BASE_URL=http://SERVER_PUBLIC_IP:8000
```

Send `app-release.apk` to the phone and install it (allow *Install unknown apps*).

> `http://` works on Android because `usesCleartextTraffic="true"` is set in
> `android/app/src/main/AndroidManifest.xml`. With HTTPS (step 17) use
> `API_BASE_URL=https://api.example.com`.

### 21.5 Play Store release (signed AAB)

The app ID is `com.ioneit.recipesurvey`. Right now `release` is signed with the
**debug key**, which the Play Store rejects. Set up a real key once:

```bash
# Create the keystore. KEEP THIS FILE + PASSWORDS SAFE FOREVER.
keytool -genkey -v -keystore ~/fmcg-upload-key.jks -keyalg RSA -keysize 2048 \
        -validity 10000 -alias upload
```

Create `mobile/android/key.properties` (don't commit it):

```properties
storePassword=CHANGE_ME
keyPassword=CHANGE_ME
keyAlias=upload
storeFile=/full/path/to/fmcg-upload-key.jks
```

In `mobile/android/app/build.gradle.kts`, load it and use it for `release`:

```kotlin
import java.util.Properties
import java.io.FileInputStream

val keystoreProperties = Properties()
val keystorePropertiesFile = rootProject.file("key.properties")
if (keystorePropertiesFile.exists()) {
    keystoreProperties.load(FileInputStream(keystorePropertiesFile))
}

android {
    // ...existing settings...
    signingConfigs {
        create("release") {
            keyAlias = keystoreProperties["keyAlias"] as String
            keyPassword = keystoreProperties["keyPassword"] as String
            storeFile = file(keystoreProperties["storeFile"] as String)
            storePassword = keystoreProperties["storePassword"] as String
        }
    }
    buildTypes {
        release {
            signingConfig = signingConfigs.getByName("release")
        }
    }
}
```

Bump the version in `mobile/pubspec.yaml` for every upload (`version: 1.0.1+2`; the
number after `+` must always increase), then:

```bash
flutter build appbundle --release --dart-define=API_BASE_URL=https://api.example.com
# Output: build/app/outputs/bundle/release/app-release.aab
```

Upload the `.aab` in **Google Play Console** → your app → *Testing / Production* → *Create release*.

---

## 22. iOS app (Flutter)

iOS apps can **only be built on a Mac** with Xcode.

### 22.1 Install the tools (once)

1. **Xcode** from the Mac App Store, open it once, accept the license.
2. Command-line tools:
   ```bash
   sudo xcode-select --switch /Applications/Xcode.app/Contents/Developer
   sudo xcodebuild -runFirstLaunch
   ```
3. **Homebrew** (https://brew.sh), then **CocoaPods**:
   ```bash
   brew install cocoapods
   ```
4. **Flutter SDK**: https://docs.flutter.dev/get-started/install/macos
5. Check:
   ```bash
   flutter doctor
   ```
6. An **Apple Developer account** ($99/year) for real devices beyond 7-day
   free provisioning, TestFlight and the App Store.

### 22.2 Get dependencies

```bash
cd FMCG-Learning-Engine/mobile
flutter clean
flutter pub get
cd ios && pod install --repo-update && cd ..
```

(Minimum iOS version is **15.0**, from `ios/Podfile`.)

### 22.3 Signing in Xcode

```bash
open ios/Runner.xcworkspace        # always the .xcworkspace, not .xcodeproj
```

In Xcode: *Runner* target → **Signing & Capabilities**

- Tick *Automatically manage signing*
- **Team**: your Apple Developer team
- **Bundle Identifier**: `com.ioneit.recipesurvey` (must be unique under your account)

Camera, microphone, location and local-network permission texts are already in
`ios/Runner/Info.plist`.

### 22.4 Run on iPhone / Simulator (testing)

```bash
flutter devices
# iPhone (on the iPhone: Settings → Privacy & Security → Developer Mode → On)
flutter run --release --dart-define=API_BASE_URL=http://SERVER_PUBLIC_IP:8000

# Simulator with a backend on the same Mac (default):
flutter run    # uses http://127.0.0.1:8000
```

The first time, trust the developer on the phone:
*Settings → General → VPN & Device Management → your Apple ID → Trust*.

### 22.5 TestFlight / App Store release

1. Use **HTTPS** for the server (step 17). `Info.plist` currently has
   `NSAllowsArbitraryLoads = true` (allows `http://`); App Review may ask why.
   With HTTPS you can remove that key.
2. Bump the version in `mobile/pubspec.yaml` (`version: 1.0.1+2`).
3. Create the app once in **App Store Connect** (https://appstoreconnect.apple.com)
   → *My Apps → +* → bundle ID `com.ioneit.recipesurvey`.
4. Build the archive:
   ```bash
   flutter build ipa --release --dart-define=API_BASE_URL=https://api.example.com
   # Output: build/ios/ipa/*.ipa
   ```
5. Upload with one of:
   - **Transporter** app (Mac App Store): drag the `.ipa` in → *Deliver*
   - or Xcode: `open build/ios/archive/Runner.xcarchive` → *Distribute App* → *App Store Connect* → *Upload*
6. In App Store Connect → **TestFlight**: add testers (internal testers get it in
   minutes). For public release: fill in screenshots, privacy details, then
   *Submit for Review*.

### 22.6 Common iOS issues

| Problem | Fix |
|---|---|
| `CocoaPods not installed` / pod errors | `brew install cocoapods`, then `cd ios && pod deintegrate && pod install --repo-update` |
| `No profiles for 'com.ioneit.recipesurvey'` | Pick your Team in Signing & Capabilities; change the bundle ID if it's taken |
| App can't reach server | Wrong `API_BASE_URL`, server port closed, or using `127.0.0.1` on a real iPhone (use the server IP/domain) |
| `Untrusted Developer` on iPhone | Settings → General → VPN & Device Management → Trust |
| Build fails after Flutter upgrade | `flutter clean && flutter pub get && cd ios && pod install` |

---

### Quick reference: values that must match

| Setting | Where | Must match |
|---|---|---|
| `MYSQL_PASSWORD`, `MYSQL_PORT=3307` | `backend/.env` | `docker run` of `fmcg-mysql` |
| `CORS_ORIGINS` | `backend/.env` | Website address in browser (`http://IP:5174`) |
| `VITE_API_BASE_URL` | `frontend/.env.production.local` | Backend address (`http://IP:8000`) |
| `PM2_SERVE_PORT` | `ecosystem.config.js` | Port in `CORS_ORIGINS` |
| `AI_SERVICE_TOKEN` | `backend/.env` + `ai_service/.env` | Each other |
| `USE_OLLAMA` / `OPENAI_API_KEY` | `ai_service/.env` | `false` needs a key |
| `API_BASE_URL` | `flutter build ... --dart-define` | Backend address |
