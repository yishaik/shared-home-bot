# Shared Home Bot 2.0

A premium shared-home experience inside Telegram: natural-language assistant plus a secure Telegram Mini App for shopping, tasks, events, household files, activity and settings.

## Product model

- **Telegram chat** for capture, questions, reminders and notifications.
- **Mini App** for visual overview, editing and household coordination.
- **One service layer and one database** shared by the bot, AI tools and REST API.
- **Dedicated Google account** for Calendar, Docs, Sheets and a managed household Drive folder.
- **Private by default** with Telegram Mini App signature validation, signed sessions, membership checks, Drive-root boundary checks and webhook secret validation.

## Main capabilities

- Premium Hebrew-first, RTL Mini App with Telegram theme support.
- Home dashboard, shopping mode, tasks, structured events, shared files, activity feed and household settings.
- Shared Google Drive browser with folder navigation, uploads, folder creation, direct Drive links and deletion.
- Inherited Google access: share the managed root folder once and all app-created children inherit access.
- Inline Telegram actions with completion and undo.
- Shared memory, notes, inventory and people through the AI assistant.
- Structured activity/audit trail.
- Backward-compatible SQLite extensions for household data.
- Railway multi-stage Docker deployment with readiness healthcheck.

## Local development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
# Fill the required secrets and IDs.
python -m app.main
```

Mini App development:

```bash
cd miniapp
npm install
npm run dev
```

The Mini App must normally be opened from Telegram because the backend validates `Telegram.WebApp.initData`.

## Google account and Drive setup

The runtime uses one dedicated Google account as the automation principal. Household members authenticate to the Mini App through Telegram; they do not grant the bot access to their personal Drives.

1. Enable Google Calendar, Drive, Docs and Sheets APIs in the Google Cloud project.
2. Configure `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_OAUTH_SETUP_SECRET` and the public service URL.
3. Open `/google/oauth/start?secret=YOUR_SETUP_SECRET` once and sign in with the Google account intended for the bot.
4. Set `GOOGLE_DRIVE_SHARED_EMAILS` to the household Google accounts that should receive editor access.
5. Leave `GOOGLE_DRIVE_FOLDER_ID` empty to create a managed root automatically, or set it to an existing folder owned by the bot account.
6. Remove `GOOGLE_OAUTH_SETUP_SECRET` after bootstrap.

The bot keeps the generated root folder ID in the persistent SQLite database. Changing the Google account later is therefore explicit: authorize the replacement account and either provide a folder it owns or clear the persisted `google_drive_folder_id` setting so a new root is created.

The default OAuth scope is `drive.file`, so the service manages files created or uploaded through the app without broad access to the bot account's entire Drive. Files manually added in Google Drive are not guaranteed to be visible to the API unless they were created/opened by the app; use the Files section for the managed workflow.

## Railway deployment

1. Deploy this repository as a Railway service.
2. Attach a persistent volume at `/data`.
3. Add the variables from `.env.example`. Seal `TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY`, `TELEGRAM_WEBHOOK_SECRET`, `APP_SESSION_SECRET` and the Google OAuth credentials.
4. Generate a public Railway domain. The app automatically derives its public URL from `RAILWAY_PUBLIC_DOMAIN`; explicit `PUBLIC_URL` and `MINI_APP_URL` remain available as overrides.
5. Configure the service healthcheck as `/health/ready` (`railway.json` already includes it).
6. In BotFather, configure the bot's Main Mini App URL as `https://YOUR_DOMAIN/app` and add screenshots/preview media.
7. Start the bot. It configures the Telegram menu button and webhook automatically.

## Required variables

```env
TELEGRAM_BOT_TOKEN=...
TELEGRAM_WEBHOOK_SECRET=...
ALLOWED_USER_IDS=111111111,222222222
OPENAI_API_KEY=...
APP_SESSION_SECRET=...
HOME_NAME=הבית שלנו
DATABASE_PATH=/data/home.db
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
GOOGLE_DRIVE_SHARED_EMAILS=member1@gmail.com,member2@gmail.com
```

`ALLOWED_USER_IDS` is fail-closed: the service will not start without at least one allowed Telegram user.

## API

Authenticated Mini App endpoints:

- `POST /api/auth/telegram`
- `GET /api/home`
- `GET|POST|PATCH|DELETE /api/shopping`
- `GET|POST|PATCH|DELETE /api/tasks`
- `GET|POST|DELETE /api/events`
- `GET /api/activity`
- `GET|PATCH /api/household`
- `GET /api/memory`
- `GET /api/notes`
- `GET /api/files/status`
- `GET /api/files`
- `POST /api/files/folders`
- `POST /api/files/upload`
- `DELETE /api/files/{file_id}`

## Validation

```bash
pytest -q
cd miniapp && npm install && npm run build
```
