# Shared Home Bot 2.0

A premium shared-home experience inside Telegram: natural-language assistant plus a secure Telegram Mini App for shopping, tasks, events, household activity and settings.

## Product model

- **Telegram chat** for capture, questions, reminders and notifications.
- **Mini App** for visual overview, editing and household coordination.
- **One service layer and one database** shared by the bot, AI tools and REST API.
- **Private by default** with Telegram Mini App signature validation, signed sessions, membership checks and webhook secret validation.

## Main capabilities

- Premium Hebrew-first, RTL Mini App with Telegram theme support.
- Home dashboard, shopping mode, tasks, structured events, activity feed and household settings.
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

## Railway deployment

1. Deploy this repository as a Railway service.
2. Attach a persistent volume at `/data`.
3. Add the variables from `.env.example`. Seal `TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY`, `TELEGRAM_WEBHOOK_SECRET` and `APP_SESSION_SECRET`.
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

## Validation

```bash
pytest -q
cd miniapp && npm install && npm run build
```
