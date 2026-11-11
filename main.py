"""整合版唯一入口：一个 FastAPI 应用，一个 webhook，两条消息通道。

    python merged/main.py

环境变量（全部在 .env.example 里）：

    BOT_TOKEN       两边共用同一个 Bot Token
    DATABASE_URL    Postgres 连接串（尽量别用 SQLite，月影用到 JSONB/ARRAY）
    PUBLIC_URL      公网地址，如 https://bot.example.com
    ADMIN_IDS       管理员 Telegram 数字 ID，逗号分隔
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from config import apply_to_environ, load_settings, redacted  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("merged")

settings = load_settings()
apply_to_environ(settings)  # 必须在 import 两个子系统之前

if not settings.bot_token:
    logger.error("缺少 BOT_TOKEN，机器人无法启动")
    raise SystemExit(1)

import boot  # noqa: E402
from fastapi import Header, HTTPException, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402

# ---------- 闪秀的 Flask 管理后台，挂在同一个端口上 ----------
from a2wsgi import WSGIMiddleware  # noqa: E402

from app import create_app as create_sx_flask_app  # noqa: E402

# 顺序要紧：闪秀的 create_app 会把当时的 DATABASE_URL 记进 Flask 配置（同步驱动），
# 之后如果给了月影单独的库，再把 DATABASE_URL 换掉，月影 import 时才读得到。
flask_app = create_sx_flask_app()
if settings.yy_database_url and settings.yy_database_url != settings.database_url:
    import os  # noqa: PLC0415

    os.environ["DATABASE_URL"] = settings.yy_database_url

import bot.main as ycm  # noqa: E402  （月影的 app / api_router / 中间件都在这里）

bridge = boot.bridge
bridge.flask_app = flask_app


def _webhook_route_path() -> str:
    path = settings.webhook_path or "/webhook"
    return path if path.startswith("/") else "/" + path


async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> JSONResponse:
    """Telegram 的唯一入口。按 dispatch 规则分给月影或闪秀。"""
    if settings.webhook_secret and not hmac.compare_digest(
        (x_telegram_bot_api_secret_token or "").encode("utf-8"),
        settings.webhook_secret.encode("utf-8"),
    ):
        raise HTTPException(status_code=403, detail="invalid secret")
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="bad json")
    if not isinstance(data, dict):
        return JSONResponse({"ok": True})
    try:
        await asyncio.wait_for(bridge.handle_update(data), timeout=120)
    except asyncio.TimeoutError:
        logger.warning("update 处理超时，已丢弃")
    except Exception:
        logger.exception("update 处理异常")
    # 永远回 200：回非 2xx 会让 Telegram 重投同一条消息，造成重复回复。
    return JSONResponse({"ok": True})


# 月影自带的 webhook 路由只喂 aiogram，必须摘掉换成整合层的
_wh_path = _webhook_route_path()
ycm.app.router.routes = [
    r for r in ycm.app.router.routes
    if not (getattr(r, "path", None) == _wh_path and "POST" in (getattr(r, "methods", None) or set()))
]
ycm.app.add_api_route(_wh_path, telegram_webhook, methods=["POST"], name="merged_webhook")

# 闪秀的 Flask 管理后台（/core/... 、/report/... 、/static/...）挂在最后，
# FastAPI 自己的 /api /app /health 先生效，剩下的才落给 Flask。
ycm.app.mount("/", WSGIMiddleware(flask_app))

app = ycm.app


@asynccontextmanager
async def lifespan(_app):
    """两个子系统串行启动；任何一边起不来都能在日志里看清是哪一步。"""
    logger.info("数据库（同步）：%s", redacted(settings.database_url))
    logger.info("数据库（异步）：%s", redacted(settings.yy_database_url))

    await bridge.start_yy(settings.webapp_url)
    await bridge.start_sx(flask_app)
    ok = await bridge.register_webhook(settings.webhook_url, settings.webhook_secret)
    if ok:
        logger.info("整合完成：一个 webhook，两条通道（月影 + 闪秀）")
    yield
    await bridge.shutdown()


app.router.lifespan_context = lifespan


def main() -> None:
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
