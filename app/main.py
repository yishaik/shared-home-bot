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
from app.drive_service import DriveService
from app.files_api import build_files_router
from app.memory_control import ensure_memory_control_schema
from app.proactive import ProactiveEngine
from app.store_v2 import Store
from app.services import HomeService

settings = get_settings()
logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("homebot")


def create_app() -> FastAPI:
    store = Store(settings.db_path, settings.household_id)
    service = HomeService(store)
    drive = DriveService(settings, store)
    state: dict = {"tg_app": None, "agent": None, "ready": False}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.require_runtime()
        await store.connect()
        await ensure_memory_control_schema(store)
        await store.bootstrap_household(settings.home_name, settings.household_timezone, settings.allowed_user_ids)
        try:
            await store.attach_memory(settings)
        except Exception:
            log.exception("memory engine attach failed — continuing with lexical fallback")

        stored_google_refresh = await store.get_setting("google_refresh_token")
        if settings.google_client_id and settings.google_client_secret and (settings.google_refresh_token or stored_google_refresh):
            try:
                await drive.ensure_root()
            except Exception:
                log.exception("Google Drive root initialization failed — files section remains unavailable")

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

        engine = ProactiveEngine(settings, store, service, tg_app.bot)
        try:
            await engine.start()
        except Exception:
            log.exception("proactive engine failed to start — bot stays reactive")

        app.state.store = store
        app.state.service = service
        app.state.drive = drive
        app.state.tg_app = tg_app
        state["ready"] = True
        yield
        state["ready"] = False
        await engine.stop()
        if tg_app.updater and tg_app.updater.running:
            await tg_app.updater.stop()
        await tg_app.stop()
        await agent.shutdown()
        await tg_app.shutdown()
        await store.close()

    api = FastAPI(title="Shared Home Bot", version="2.3.0", lifespan=lifespan)
    api.include_router(build_api_router(settings, store, service))
    api.include_router(build_files_router(settings, store, drive))

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

    def _setup_ok(secret: str) -> bool:
        return bool(settings.google_oauth_setup_secret) and secret == settings.google_oauth_setup_secret

    @api.get("/google/oauth/start")
    async def google_oauth_start(secret: str = ""):
        if not _setup_ok(secret):
            return Response(status_code=404)
        if not settings.google_client_id or not settings.resolved_public_url:
            return JSONResponse({"detail": "GOOGLE_CLIENT_ID / public URL not set"}, status_code=400)
        from urllib.parse import urlencode
        from app.google_client import SCOPES
        from fastapi.responses import RedirectResponse
        params = {
            "client_id": settings.google_client_id,
            "redirect_uri": f"{settings.resolved_public_url}/google/oauth/callback",
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
            "state": settings.google_oauth_setup_secret,
        }
        return RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params))

    @api.get("/google/oauth/callback")
    async def google_oauth_callback(code: str = "", state: str = "", error: str = ""):
        if not _setup_ok(state):
            return Response(status_code=404)
        if error or not code:
            return JSONResponse({"detail": f"oauth error: {error or 'missing code'}"}, status_code=400)
        import httpx
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post("https://oauth2.googleapis.com/token", data={
                "code": code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": f"{settings.resolved_public_url}/google/oauth/callback",
                "grant_type": "authorization_code",
            })
        data = resp.json()
        refresh = data.get("refresh_token")
        if not refresh:
            return JSONResponse({"detail": "no refresh_token (re-consent with prompt=consent)", "response": data}, status_code=400)
        await store.set_setting("google_refresh_token", refresh)
        settings.google_refresh_token = refresh
        drive.reset_credentials()
        log.info("google oauth: refresh token stored (len=%s)", len(refresh))
        return Response(content="<html><body style='font-family:system-ui;padding:2rem'>"
                        "<h2>✅ Google מחובר</h2><p>אפשר לסגור את הדף. Shared Home Bot ישלים מכאן.</p></body></html>",
                        media_type="text/html")

    @api.get("/google/oauth/token")
    async def google_oauth_token(secret: str = ""):
        if not _setup_ok(secret):
            return Response(status_code=404)
        return {"refresh_token": await store.get_setting("google_refresh_token")}

    return api


app = create_app()


def run() -> None:
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=int(os.environ.get("PORT", settings.port)), reload=False)


if __name__ == "__main__":
    run()
