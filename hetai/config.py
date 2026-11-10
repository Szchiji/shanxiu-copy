"""环境配置。生产默认关闭、禁止演示回退；演示模式仅允许回环地址。"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HETAI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    mode: Literal["demo", "production"] = "production"
    host: str = "127.0.0.1"
    port: int = 8765
    log_level: str = "INFO"

    database_url: str = ""
    redis_url: str = ""

    bot_token: str = ""
    webhook_host: str = ""
    webhook_path: str = "/telegram/webhook"
    webhook_secret: str = ""
    webapp_url: str = ""

    admin_ids: str = ""
    session_secret: str = ""

    run_scheduler: bool = False
    seed_demo: bool = True

    @field_validator(
        "bot_token",
        "webhook_host",
        "webhook_path",
        "webhook_secret",
        "webapp_url",
        "database_url",
        "redis_url",
        "admin_ids",
        "session_secret",
        "host",
        "log_level",
        mode="before",
    )
    @classmethod
    def strip_str(cls, v):
        if v is None:
            return ""
        return str(v).strip()

    @property
    def is_demo(self) -> bool:
        return self.mode == "demo"

    @property
    def bot_connected(self) -> bool:
        return bool(self.bot_token) and not self.bot_token.startswith("0:")

    @property
    def admin_id_list(self) -> list[int]:
        out: list[int] = []
        for part in self.admin_ids.split(","):
            part = part.strip()
            if not part:
                continue
            if part.isdigit() or (part.startswith("-") and part[1:].isdigit()):
                out.append(int(part))
        return out

    def mask_secret(self, value: str) -> str:
        if not value:
            return "（未设置）"
        if len(value) <= 8:
            return "••••"
        return f"{value[:2]}••••{value[-4:]}"

    def resolved_database_url(self) -> str:
        url = self.database_url
        if self.is_demo and not url:
            return "sqlite+aiosqlite:///./var/hetai-demo.sqlite"
        if not url:
            raise RuntimeError("生产模式必须设置 HETAI_DATABASE_URL")
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://") :]
        if url.startswith("postgresql://") and "+asyncpg" not in url:
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url

    def validate_boot(self) -> None:
        if self.is_demo:
            host = (self.host or "").lower()
            if host not in {"127.0.0.1", "localhost", "::1"}:
                raise RuntimeError(
                    "演示模式只允许绑定 127.0.0.1 / localhost / ::1，"
                    "禁止在非回环地址以不安全方式启动。"
                )
            return
        # production: fail closed
        if self.mode != "production":
            raise RuntimeError("未知 HETAI_MODE，已拒绝启动")
        url = self.resolved_database_url()
        if "sqlite" in url:
            raise RuntimeError("生产模式禁止使用 SQLite，请配置 PostgreSQL")
        if not self.webhook_secret:
            raise RuntimeError("生产模式必须设置 HETAI_WEBHOOK_SECRET")
        if not self.admin_ids:
            raise RuntimeError("生产模式必须设置 HETAI_ADMIN_IDS")
        if not self.session_secret or self.session_secret in {"change-me", "demo", "secret"}:
            raise RuntimeError("生产模式必须设置足够强度的 HETAI_SESSION_SECRET")
        if not self.bot_token:
            raise RuntimeError("生产模式必须设置 HETAI_BOT_TOKEN")


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
