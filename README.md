# Shared Home Bot 3.0

A premium shared-home experience inside Telegram: a topic-aware multi-agent assistant plus a secure Telegram Mini App for shopping, tasks, events, household activity and settings.

## Product model

- **Telegram private chats, groups and topics** for capture, coordination, questions, reminders and notifications.
- **Topic-aware sub-agents** for tasks, shopping, calendar, memory and cross-domain household coordination.
- **Telegram Mini App** for visual overview, editing and household workflows.
- **One service layer and one database** shared by the bot, agents, Mini App and REST API.
- **Shared household state with isolated conversation context** per Telegram chat/topic.
- **Private by default** with user and chat allow-lists, group-safe context filtering, Telegram Mini App signature validation, signed sessions and webhook secret validation.

## Main capabilities

- Hebrew-first Telegram assistant and RTL Mini App with Telegram theme support.
- Private chats, groups, supergroups and forum/private-chat topics.
- Create, list, rename, close, reopen and delete Telegram topics.
- Bind a topic to a specialist agent or use automatic intent routing.
- Scoped transcript and rolling summary per `chat_id + topic_id + agent_id`.
- Configurable group attention modes: all, mentions, bound topics, or mention/topic hybrid.
- Private memory, people, notes, settings and Google Docs/Sheets blocked in groups unless explicitly enabled.
- Chat membership and topic lifecycle tracking.
- Atomic idempotent webhook processing with retry-safe update state.
- Inline Telegram actions with completion and undo.
- Home dashboard, shopping mode, tasks, Google-backed events, activity feed and household settings.
- Shared memory, notes, inventory and people through the AI tools.
- Structured activity/audit trail and hybrid memory retrieval.
- Feature-gated adapter for Telegram Bot API capabilities newer than the installed PTB release.
- Railway multi-stage Docker deployment with readiness healthcheck.

## Telegram commands

| Command | Purpose |
|---|---|
| `/agents` | List available specialist agents |
| `/agent tasks` | Bind the current topic to an agent |
| `/agent auto` | Restore automatic routing |
| `/topic Name \| calendar` | Create a new topic and optionally bind an agent |
| `/topics` | List known topics in the current chat |
| `/topic_rename Name` | Rename the current topic |
| `/topic_close` | Close the current topic |
| `/topic_open` | Reopen the current topic |
| `/topic_delete` | Delete the current topic |
| `/chatid` | Show the current chat and topic IDs |

See [Telegram Platform v3](docs/telegram-platform.md) for architecture, BotFather configuration, permissions and compatibility details.

## Local development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
# Fill the required secrets, users and allowed chat IDs.
python -m app.main
```

Mini App development:

```bash
cd miniapp
npm install
npm run dev
```

The Mini App must normally be opened from Telegram because the backend validates `Telegram.WebApp.initData`.

## Telegram setup

1. Add household members to `ALLOWED_USER_IDS`.
2. Add approved groups/supergroups to `ALLOWED_CHAT_IDS`.
3. Add topic administrators to `TELEGRAM_ADMIN_USER_IDS`.
4. Keep `TELEGRAM_ALLOW_UNLISTED_GROUPS=false` unless broad group access is intentional.
5. Keep `TELEGRAM_GROUP_ALLOW_PRIVATE_CONTEXT=false` unless every group member should see private household context.
6. For ambient group operation, disable BotFather Privacy Mode or promote the bot to administrator.
7. In forum groups, grant **Manage Topics**.
8. Choose `TELEGRAM_GROUP_RESPONSE_MODE=mention_or_topic` as the recommended default.

## Railway deployment

1. Deploy this repository as a Railway service.
2. Attach a persistent volume at `/data`.
3. Add the variables from `.env.example`. Seal `TELEGRAM_BOT_TOKEN`, `OPENAI_API_KEY`, `TELEGRAM_WEBHOOK_SECRET` and `APP_SESSION_SECRET`.
4. Generate a public Railway domain. The app automatically derives its public URL from `RAILWAY_PUBLIC_DOMAIN`; explicit `PUBLIC_URL` and `MINI_APP_URL` remain available as overrides.
5. Configure the service healthcheck as `/health/ready` (`railway.json` already includes it).
6. In BotFather, configure the bot's Main Mini App URL as `https://YOUR_DOMAIN/app` and add screenshots/preview media.
7. Configure groups, Privacy Mode and topic permissions as described above.
8. Start the bot. It configures scoped commands, the Telegram menu button and webhook automatically.

## Required variables

```env
TELEGRAM_BOT_TOKEN=...
TELEGRAM_WEBHOOK_SECRET=...
ALLOWED_USER_IDS=111111111,222222222
ALLOWED_CHAT_IDS=-1001234567890
TELEGRAM_ADMIN_USER_IDS=111111111
TELEGRAM_GROUP_RESPONSE_MODE=mention_or_topic
TELEGRAM_GROUP_ALLOW_PRIVATE_CONTEXT=false
OPENAI_API_KEY=...
APP_SESSION_SECRET=...
HOME_NAME=הבית שלנו
DATABASE_PATH=/data/home.db
```

`ALLOWED_USER_IDS` is fail-closed: the service will not start without at least one allowed Telegram user. Groups are also fail-closed unless explicitly allow-listed or `TELEGRAM_ALLOW_UNLISTED_GROUPS=true` is intentionally configured.

## API

Authenticated Mini App endpoints:

- `POST /api/auth/telegram`
- `GET /api/home`
- `GET|POST|PATCH|DELETE /api/shopping`
- `GET|POST|PATCH|DELETE /api/tasks`
- `GET|POST|PATCH|DELETE /api/events`
- `POST /api/events/sync`
- `GET /api/events/status`
- `GET /api/activity`
- `GET|PATCH /api/household`
- `GET /api/memory`
- `GET /api/notes`

## Validation

```bash
pytest -q
cd miniapp && npm install && npm run build
```
