"""Entry point: local long-poll OR Railway webhook via FastAPI."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from telegram import Update

from app.agent import HomeAgent
from app.bot import build_application
from app.config import get_settings
from app.db import Store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("homebot")


def create_app() -> FastAPI:
    settings = get_settings()
    store = Store(settings.db_path)
    # Agent/bot built after settings validate in lifespan so imports work without secrets.
    state: dict = {"tg_app": None, "agent": None}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.require_runtime()
        agent = HomeAgent(settings, store)
        tg_app = build_application(settings, store, agent)
        state["tg_app"] = tg_app
        state["agent"] = agent

        await store.connect()
        await tg_app.initialize()
        await tg_app.start()

        webhook = settings.webhook_url
        if webhook:
            await tg_app.bot.set_webhook(
                url=webhook,
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=False,
            )
            log.info("webhook set → %s", webhook)
        else:
            # local / no PUBLIC_URL: long polling in background
            await tg_app.bot.delete_webhook(drop_pending_updates=False)
            asyncio.create_task(
                tg_app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
            )
            log.info("long-polling started (no PUBLIC_URL)")

        app.state.settings = settings
        app.state.store = store
        app.state.tg_app = tg_app
        yield

        tg_app = state["tg_app"]
        if tg_app is not None:
            if tg_app.updater and tg_app.updater.running:
                await tg_app.updater.stop()
            await tg_app.stop()
            await tg_app.shutdown()
        await store.close()

    api = FastAPI(title="Shared Home Bot", lifespan=lifespan)

    @api.get("/")
    async def root():
        return {
            "ok": True,
            "bot": settings.bot_display_name,
            "home": settings.home_name,
            "mode": "webhook" if settings.webhook_url else "polling",
            "model": settings.openai_model,
        }

    @api.get("/health")
    async def health():
        return {"status": "ok"}

    @api.post("/telegram/webhook")
    async def telegram_webhook(request: Request):
        tg_app = state["tg_app"]
        if tg_app is None:
            return Response(status_code=503)
        data = await request.json()
        update = Update.de_json(data, tg_app.bot)
        await tg_app.process_update(update)
        return Response(status_code=200)

    return api


app = create_app()


def run() -> None:
    import uvicorn

    settings = get_settings()
    port = int(os.environ.get("PORT", settings.port))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=False)


if __name__ == "__main__":
    run()
