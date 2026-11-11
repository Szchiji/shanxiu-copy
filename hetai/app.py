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
from hetai.auth import (
    DEMO_ACTORS,
    FIXTURE_TELEGRAM_IDS,
    can_admin,
    can_super,
    issue_access_token,
    real_role_for_telegram_id,
    validate_init_data,
)
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
from hetai.preview_auth import PreviewAccessMiddleware
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
    if settings.uses_fixtures and settings.seed_demo:
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
    app.add_middleware(PreviewAccessMiddleware)
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
        if st.is_preview:
            return {"ok": db_ok == "ok", "app": APP_NAME, "mode": "preview"}
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
        if not st.bot_connected:
            raise HTTPException(
                status_code=401,
                detail="未连接真实 Bot，不提供 Telegram 登录。示例夹具入口请使用预览口令，不能代替实名登录。",
            )
        body = await request.json()
        init_data = (body or {}).get("initData") or ""
        payload = validate_init_data(init_data, st.bot_token)
        telegram_id = int(payload["user"]["id"])
        if telegram_id in FIXTURE_TELEGRAM_IDS:
            raise HTTPException(status_code=401, detail="不能使用示例身份进行 Telegram 登录")
        # start_param / chat / chat_instance never grant membership or admin scope.
        async with session_scope() as session:
            from hetai.services.telegram_groups import upsert_real_user, verify_membership_via_api
            from hetai.auth import Actor
            from hetai.services.context import hinted_ids_from_mapping, load_actor_memberships

            try:
                user = await upsert_real_user(
                    session,
                    st,
                    telegram_id,
                    username=payload["user"].get("username"),
                    display_name=payload["user"].get("first_name") or str(telegram_id),
                )
            except ValueError as exc:
                raise HTTPException(status_code=401, detail=str(exc)) from exc
            if user.is_banned:
                raise HTTPException(status_code=403, detail="账号已被封禁")
            role = real_role_for_telegram_id(st, telegram_id, user.role)
            user.role = role
            token = issue_access_token(telegram_id, st.session_secret)
            actor = Actor(
                user_id=user.id,
                telegram_id=user.telegram_id,
                display_name=user.display_name or str(telegram_id),
                role=role,
                is_demo=False,
                source="telegram-token",
            )
            hint_map = {
                "start_param": payload.get("start_param") or "",
                "startapp": payload.get("start_param") or "",
                "chat_id": (payload.get("chat") or {}).get("id") if payload.get("chat") else None,
            }
            hints = hinted_ids_from_mapping(hint_map)
            if hints.get("chat_id"):
                await verify_membership_via_api(session, st, actor, int(hints["chat_id"]))
            _, accessible, _ = await load_actor_memberships(session, actor)
            allowed_ids = {g.id for g in accessible}
            chat_ids = {g.chat_id: g for g in accessible}
            hint_group = None
            if hints.get("group_id") in allowed_ids:
                hint_group = next(g for g in accessible if g.id == hints["group_id"])
            elif hints.get("chat_id") in chat_ids:
                hint_group = chat_ids[hints["chat_id"]]
            context_hint = None
            if hint_group is not None:
                context_hint = {"group_id": hint_group.id, "title": hint_group.title}
        resp = JSONResponse(
            {
                "ok": True,
                "telegram_id": telegram_id,
                "is_owner": st.is_configured_owner(telegram_id),
                "context_hint": context_hint,
                "chat_instance_bound": False,
            }
        )
        # token stays in HttpOnly cookie; not returned in the body
        secure = request.url.scheme == "https" or (request.headers.get("x-forwarded-proto") or "").startswith("https")
        resp.set_cookie("hetai_token", token, httponly=True, samesite="lax", secure=secure, path="/")
        return resp

    @app.post(settings.webhook_path if settings else "/telegram/webhook")
    async def telegram_webhook(
        request: Request,
        x_telegram_bot_api_secret_token: str | None = Header(default=None),
    ):
        st = get_settings()
        expected = st.webhook_secret or ("demo-local-only" if st.is_demo and not st.bot_connected else "")
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
            settings = get_settings()
            actor = getattr(request.state, "actor", None)
            safe_ctx = getattr(request.state, "safe_context", None)
            authenticated = actor is not None
            path = request.url.path or ""
            if not authenticated and (path == "/tg" or path.startswith("/tg/")) and exc.status_code in {401, 403}:
                return TEMPLATES.TemplateResponse(
                    request,
                    "tg_gate.html",
                    {
                        "request": request,
                        "app_name": APP_NAME,
                        "bot_username": settings.bot_username or "",
                        "error_title": exc.detail,
                        "page_title": "请在 Telegram 中打开",
                    },
                    status_code=exc.status_code,
                )
            if not authenticated:
                actor = type("X", (), {"display_name": "访客", "role": "anonymous", "is_demo": False})()
            return TEMPLATES.TemplateResponse(
                request,
                "error.html",
                {
                    "request": request,
                    "app_name": APP_NAME,
                    "actor": actor,
                    "is_demo": settings.is_demo,
                    "is_preview": settings.is_preview,
                    "bot_connected": settings.bot_connected,
                    "bot_status": bot_status(settings),
                    "can_admin": can_admin(actor.role) if authenticated else False,
                    "can_inbox": bool(safe_ctx and safe_ctx.can_inbox) if authenticated else False,
                    "can_super": can_super(actor.role) if authenticated else False,
                    "context": safe_ctx if authenticated else None,
                    "demo_actors": DEMO_ACTORS if authenticated and getattr(actor, "is_demo", False) else {},
                    "demo_as": (request.cookies.get("hetai_demo_as") or "") if authenticated and getattr(actor, "is_demo", False) else "",
                    "show_fixtures": bool(authenticated and getattr(actor, "is_demo", False)),
                    "app_root": "/tg" if request.url.path.startswith("/tg") else "/app",
                    "p": (lambda path, root=("/tg" if request.url.path.startswith("/tg") else "/app"): (root + path[4:] if path.startswith("/app") else path)),
                    "live_test": bool(authenticated and settings.is_preview and settings.bot_connected and not getattr(actor, "is_demo", False)),
                    "bot_username": settings.bot_username or "",
                    "moderation_enabled": bool(settings.test_chat_id_list()),

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
        host=settings.bind_host(),
        port=settings.bind_port(),
        log_level=settings.log_level.lower(),
        reload=False,
        workers=1,
    )
