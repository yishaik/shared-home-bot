# Shared Home Bot

Telegram home assistant for a **couple** — **one shared brain**, deployable on **Railway**.

| | |
|---|---|
| **Repo** | Create with `gh` (private recommended) |
| **Deploy** | Railway + volume at `/data` |
| **LLM** | OpenAI API key (Codex/ChatGPT sub alone ≠ API) |

Full click-by-click guide: open **`docs/deploy-guide.html`** (or the hosted copy after deploy).

---

## Features

- Shared **memory**, **todos**, **shopping list**, **notes**, **events**, **inventory**, **people**, **settings**
- Model tool-calling so both partners benefit from the same state
- Webhook on Railway / long-poll locally
- Allowlist: only your Telegram user IDs

### Tools

`remember` `recall` `forget` `search_home` · `todo_*` · `shop_*` · `note_*` · `event_*` · `inventory_*` · `person_*` · `setting_*`

### Commands

`/start` `/help` `/whoami` `/memory` `/todos` `/shop` `/notes` `/events` `/inventory` `/people`

---

## 5-minute Railway deploy

1. **BotFather** → `/newbot` → copy token  
2. Message the bot → `/whoami` (both partners) → copy IDs  
3. [platform.openai.com/api-keys](https://platform.openai.com/api-keys) → API key + billing  
4. Push this repo to GitHub (private)  
5. [railway.app/new](https://railway.app/new) → Deploy from GitHub  
6. **Volume** → mount path `/data`  
7. **Variables:**

```env
TELEGRAM_BOT_TOKEN=...
ALLOWED_USER_IDS=111111111,222222222
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o
DATABASE_PATH=/data/home.db
PUBLIC_URL=https://YOUR-SERVICE.up.railway.app
HOME_NAME=Our Home
BOT_DISPLAY_NAME=Home
```

8. Settings → Generate domain → paste into `PUBLIC_URL` → redeploy  
9. Both open the bot in Telegram and chat

---

## Local run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill values
python -m app.main
```

---

## Codex note

ChatGPT/Codex **app** subscription does not automatically power this bot.  
Use an **OpenAI API key** with usage enabled. If your org exposes a Codex model id on the API, set `OPENAI_MODEL` to it.

Optional: `OPENAI_BASE_URL` for OpenAI-compatible proxies.

---

## Security

- Always set `ALLOWED_USER_IDS` (both partners)  
- Prefer a **private** GitHub repo  
- Backup `/data/home.db` from the Railway volume occasionally  

---

## License

MIT
