"""合台 ASGI 入口：唯一 HTTP 应用 + 唯一 Telegram webhook 入口。"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from hetai import APP_NAME
from hetai.auth import issue_access_token, validate_init_data
from hetai.bot.runtime import (
    bot_status,
    build_dispatcher,
    close_bot,
    setup_bot,
)
from hetai.config import Settings, get_settings, reset_settings_cache
from hetai.db import close_engine, get_engine, init_engine, session_scope
from hetai.jobs import scheduler_status, start_scheduler, stop_scheduler
from hetai.models import User
from hetai.seed import seed_demo_if_empty
from hetai.web.routes import router as web_router

logger = logging.getLogger("hetai")
TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "web" / "templates"))
TEMPLATES.env.autoescape = True


async def _run_migrations() -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    # alembic command is sync; run in thread if needed. For sqlite this is fine.
    def _upgrade():
        command.upgrade(cfg, "head")

    import asyncio

    await asyncio.to_thread(_upgrade)


@asynccontextmanager
async def lifespan(app: FastAPI):
    reset_settings_cache()
    settings = get_settings()
    settings.validate_boot()
    Path("var").mkdir(exist_ok=True)
    init_engine(settings)
    await _run_migrations()
    if settings.is_demo and settings.seed_demo:
        async with session_scope() as session:
            result = await seed_demo_if_empty(session)
            logger.info("demo seed: %s", result)
    build_dispatcher()
    await setup_bot(settings)
    start_scheduler(settings)
    yield
    await stop_scheduler()
    await close_bot()
    await close_engine()


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is not None:
        reset_settings_cache()
    app = FastAPI(title=APP_NAME, lifespan=lifespan, docs_url=None, redoc_url=None)
    app.include_router(web_router)
    static_dir = Path(__file__).resolve().parent / "web" / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/health")
    async def health():
        st = get_settings()
        db_ok = "ok"
        try:
            async with session_scope() as session:
                await session.execute(select(User).limit(1))
        except Exception as exc:
            db_ok = f"error:{type(exc).__name__}"
        return {
            "ok": db_ok == "ok",
            "app": APP_NAME,
            "mode": st.mode,
            "bot_connected": st.bot_connected,
            "single_bot": True,
            "db": db_ok,
            "scheduler": scheduler_status(),
            "ingress": st.webhook_path or "/telegram/webhook",
            "outbound": st.resolved_transport,
            "outbound_live": st.outbound_live,
            "bot": bot_status(st),
        }

    @app.post("/api/auth/telegram")
    async def auth_telegram(request: Request):
        st = get_settings()
        if st.is_demo and not st.bot_connected:
            raise HTTPException(
                status_code=401,
                detail="演示模式未连接真实 Bot，不提供 Telegram 登录。本机回环可直接使用示例身份。",
            )
        body = await request.json()
        init_data = (body or {}).get("initData") or ""
        payload = validate_init_data(init_data, st.bot_token)
        telegram_id = int(payload["user"]["id"])
        async with session_scope() as session:
            user = (
                await session.execute(select(User).where(User.telegram_id == telegram_id))
            ).scalar_one_or_none()
            if not user:
                user = User(
                    telegram_id=telegram_id,
                    username=payload["user"].get("username"),
                    display_name=payload["user"].get("first_name") or str(telegram_id),
                    role="super" if telegram_id in st.admin_id_list else "member",
                )
                session.add(user)
                await session.flush()
            elif user.is_banned:
                raise HTTPException(status_code=403, detail="账号已被封禁")
            token = issue_access_token(telegram_id, st.session_secret)
        resp = JSONResponse({"ok": True, "token": token, "telegram_id": telegram_id})
        resp.set_cookie("hetai_token", token, httponly=True, samesite="lax", secure=True)
        return resp

    @app.post(settings.webhook_path if settings else "/telegram/webhook")
    async def telegram_webhook(
        request: Request,
        x_telegram_bot_api_secret_token: str | None = Header(default=None),
    ):
        st = get_settings()
        expected = st.webhook_secret or ("demo-local-only" if st.is_demo else "")
        provided = x_telegram_bot_api_secret_token or ""
        from hetai.bot.ingress import process_telegram_payload, secrets_equal

        if not secrets_equal(provided, expected):
            raise HTTPException(status_code=401, detail="webhook secret 无效")
        payload = await request.json()
        return await process_telegram_payload(payload, st)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        accept = request.headers.get("accept") or ""
        if "text/html" in accept:
            return TEMPLATES.TemplateResponse(
                request,
                "error.html",
                {
                    "request": request,
                    "app_name": APP_NAME,
                    "actor": type("X", (), {"display_name": "访客", "role": "anonymous"})(),
                    "is_demo": get_settings().is_demo,
                    "bot_connected": get_settings().bot_connected,
                    "bot_status": bot_status(get_settings()),
                    "can_admin": False,
                    "can_super": False,
                    "demo_actors": {},
                    "demo_as": "",
                    "flash_error": None,
                    "flash_ok": None,
                    "nav": "",
                    "page_title": "无法访问",
                    "error_title": exc.detail,
                },
                status_code=exc.status_code,
            )
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    return app


app = create_app()


def run() -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stdout)
    settings = get_settings()
    settings.validate_boot()
    import uvicorn

    uvicorn.run(
        "hetai.app:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        reload=False,
        workers=1,
    )
