from __future__ import annotations

import asyncio
import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from telegram import BotCommand, MenuButtonWebApp, Update, WebAppInfo

from app.agent import HomeAgent
from app.api import build_api_router
from app.bot import build_application
from app.config import get_settings
from app.store_v2 import Store
from app.services import HomeService

settings = get_settings()
logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("homebot")


def create_app() -> FastAPI:
    store = Store(settings.db_path, settings.household_id)
    service = HomeService(store)
    state: dict = {"tg_app": None, "agent": None, "ready": False}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.require_runtime()
        await store.connect()
        await store.bootstrap_household(settings.home_name, settings.household_timezone, settings.allowed_user_ids)
        agent = HomeAgent(settings, store, service)
        tg_app = build_application(settings, store, service, agent)
        state.update(tg_app=tg_app, agent=agent)
        await tg_app.initialize()
        await tg_app.start()

        if settings.resolved_mini_app_url:
            await tg_app.bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="הבית", web_app=WebAppInfo(url=settings.resolved_mini_app_url)))
        await tg_app.bot.set_my_commands([
            BotCommand("start", "פתיחת הבית"),
            BotCommand("app", "אפליקציית הבית"),
            BotCommand("todos", "משימות"),
            BotCommand("shop", "קניות"),
            BotCommand("events", "אירועים"),
            BotCommand("help", "עזרה"),
        ])

        if settings.webhook_url:
            await tg_app.bot.set_webhook(
                url=settings.webhook_url,
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=False,
                secret_token=settings.telegram_webhook_secret,
            )
            log.info("webhook configured")
        else:
            await tg_app.bot.delete_webhook(drop_pending_updates=False)
            asyncio.create_task(tg_app.updater.start_polling(allowed_updates=Update.ALL_TYPES))
            log.info("long polling started")

        app.state.store = store
        app.state.service = service
        app.state.tg_app = tg_app
        state["ready"] = True
        yield
        state["ready"] = False
        if tg_app.updater and tg_app.updater.running:
            await tg_app.updater.stop()
        await tg_app.stop()
        await tg_app.shutdown()
        await store.close()

    api = FastAPI(title="Shared Home Bot", version="2.0.1", lifespan=lifespan)
    api.include_router(build_api_router(settings, store, service))

    @api.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled request error id=%s path=%s", request_id, request.url.path)
            return JSONResponse({"detail": "Internal error", "request_id": request_id}, status_code=500)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    @api.get("/")
    async def root():
        return {"ok": True, "bot": settings.bot_display_name, "home": settings.home_name, "mode": "webhook" if settings.webhook_url else "polling", "mini_app": bool(settings.resolved_mini_app_url)}

    @api.get("/health/live")
    async def live():
        return {"status": "ok"}

    @api.get("/health/ready")
    async def ready():
        if not state["ready"]:
            return JSONResponse({"status": "starting"}, status_code=503)
        try:
            await store.get_household()
        except Exception:
            return JSONResponse({"status": "database_unavailable"}, status_code=503)
        return {"status": "ready"}

    @api.get("/health")
    async def health():
        return await ready()

    @api.post("/telegram/webhook")
    async def telegram_webhook(request: Request):
        received_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not settings.telegram_webhook_secret or received_secret != settings.telegram_webhook_secret:
            return Response(status_code=403)
        tg_app = state["tg_app"]
        if tg_app is None:
            return Response(status_code=503)
        update = Update.de_json(await request.json(), tg_app.bot)
        await tg_app.process_update(update)
        return Response(status_code=200)

    frontend_dir = Path(__file__).resolve().parent.parent / "miniapp" / "dist"

    @api.get("/app")
    @api.get("/app/{path:path}")
    async def mini_app(path: str = ""):
        index = frontend_dir / "index.html"
        if not index.exists():
            return JSONResponse({"detail": "Mini App is not built"}, status_code=503)
        requested = (frontend_dir / path).resolve() if path else index
        if path and requested.is_file() and frontend_dir.resolve() in requested.parents:
            return FileResponse(requested)
        return FileResponse(index)

    return api


app = create_app()


def run() -> None:
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.environ.get("PORT", settings.port)), reload=False)


if __name__ == "__main__":
    run()
