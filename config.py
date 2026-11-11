"""整合版配置：一套环境变量，喂给两个子系统。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _with_driver(url: str, driver: str) -> str:
    """把 postgresql:// 换成 postgresql+driver://（已带驱动的原样保留）。"""
    if not url:
        return url
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if not url.startswith("postgresql://"):
        return url  # sqlite 等其它驱动不去动
    return "postgresql+" + driver + "://" + url[len("postgresql://"):]


def _strip_driver(url: str) -> str:
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql://" + url[len("postgresql+asyncpg://"):]
    if url.startswith("postgresql+psycopg2://"):
        return "postgresql://" + url[len("postgresql+psycopg2://"):]
    return url


@dataclass(frozen=True)
class Settings:
    bot_token: str
    public_url: str
    webhook_path: str
    webhook_secret: str
    database_url: str        # 同步（闪秀 / Flask-SQLAlchemy）
    yy_database_url: str     # 异步（月影 / asyncpg）
    admin_ids: str
    webapp_url: str
    port: int

    @property
    def webhook_url(self) -> str:
        base = self.public_url.rstrip("/")
        path = self.webhook_path if self.webhook_path.startswith("/") else "/" + self.webhook_path
        return f"{base}{path}" if base else ""


def load_settings() -> Settings:
    token = _env("BOT_TOKEN")
    public_url = _env("PUBLIC_URL") or _env("WEBHOOK_HOST")
    sync_url = _env("DATABASE_URL") or "postgresql://postgres:postgres@127.0.0.1:5432/merged"
    # 默认两边共用一个库；要分开就在 YY_DATABASE_URL 里给月影单独一个（同样写 postgresql://）
    yy_url = _env("YY_DATABASE_URL") or sync_url
    webapp = _env("WEBAPP_URL") or (f"{public_url.rstrip('/')}/app" if public_url else "")

    settings = Settings(
        bot_token=token,
        public_url=public_url,
        webhook_path=_env("WEBHOOK_PATH", "/webhook"),
        webhook_secret=_env("WEBHOOK_SECRET"),
        database_url=_strip_driver(sync_url),
        yy_database_url=_strip_driver(yy_url),
        admin_ids=_env("ADMIN_IDS"),
        webapp_url=webapp,
        port=int(_env("PORT", "8080") or 8080),
    )
    return settings


def apply_to_environ(settings: Settings) -> None:
    """在两个子系统 import / 初始化之前，把它们各自读的环境变量摆正。

    两边都读 DATABASE_URL，但一个要 asyncpg 一个要 psycopg2，所以这里统一写死
    各自需要的形态，避免后 import 的那个把前一个的配置覆盖掉。
    """
    os.environ["DATABASE_URL"] = settings.database_url       # 闪秀（同步）先读这个
    os.environ["BOT_TOKEN"] = settings.bot_token             # 月影
    os.environ["TG_BOT_TOKEN"] = settings.bot_token          # 闪秀
    os.environ.setdefault("ADMIN_IDS", settings.admin_ids)
    os.environ["WEBHOOK_HOST"] = settings.public_url
    os.environ["WEBHOOK_PATH"] = settings.webhook_path
    os.environ["WEBAPP_URL"] = settings.webapp_url
    if settings.webhook_secret:
        os.environ["WEBHOOK_SECRET"] = settings.webhook_secret
    # 闪秀靠这个变量判断「有公网域名 → 走 webhook，不 polling」。整合版永远是 webhook，
    # 不然两边会抢 getUpdates。空域名时塞一个占位值，它的 set_webhook 也会被拦掉。
    os.environ["RAILWAY_PUBLIC_DOMAIN"] = (
        urlsplit(settings.public_url).netloc if settings.public_url else "merged.invalid"
    )
    # 月影的 ASGI 端口（整合层自己起 uvicorn，用 settings.port）
    os.environ["PORT"] = str(settings.port)
    # 月影用不用独立库，由主入口在 import 月影之前决定（见 main.py）


def redacted(url: str) -> str:
    """日志里打印数据库地址时去掉密码。"""
    try:
        parts = urlsplit(url)
        if parts.password:
            netloc = f"{parts.username}:***@{parts.hostname}"
            if parts.port:
                netloc += f":{parts.port}"
            return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    except Exception:
        pass
    return url
