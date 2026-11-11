"""环境配置。生产默认关闭、禁止演示回退；演示模式仅允许回环地址。"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator

_WEAK_SECRETS = {"", "change-me", "demo", "secret", "password", "preview", "admin"}
PREVIEW_ACCESS_MIN_LENGTH = 12
# Session and other secrets keep the stronger default of 24.


def is_strong_secret(value: str, *, min_length: int = 24) -> bool:
    if not value or value.strip() != value:
        return False
    if value.strip().lower() in _WEAK_SECRETS:
        return False
    if len(value) < min_length:
        return False
    if len(set(value)) < 8:
        return False
    return True

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HETAI_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    mode: Literal["demo", "production", "preview"] = "production"
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
    preview_access_secret: str = ""

    run_scheduler: bool = False
    seed_demo: bool = True

    # local：演示强制、永不 HTTP；dry-run：生产默认，处理更新但不发送；
    # live：显式选择后才走真实 Bot API（仍不会自动 setWebhook）。
    bot_transport: str = ""


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
        "preview_access_secret",
        "host",
        "log_level",
        "bot_transport",
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
    def is_preview(self) -> bool:
        return self.mode == "preview"

    @property
    def uses_fixtures(self) -> bool:
        return self.is_demo or self.is_preview

    @property
    def bot_connected(self) -> bool:
        return bool(self.bot_token) and not self.bot_token.startswith("0:")

    @property
    def resolved_transport(self) -> str:
        """demo 永远 local；生产默认 dry-run，live 必须显式打开。"""
        raw = (self.bot_transport or "").strip().lower().replace("_", "-")
        if raw in {"dryrun", "dry-run", "dry"}:
            raw = "dry-run"
        if self.is_demo or self.is_preview:
            return "local"
        if raw in {"", "dry-run"}:
            return "dry-run"
        if raw in {"local", "live"}:
            return raw
        raise RuntimeError(
            "HETAI_BOT_TRANSPORT 只能是 local、dry-run 或 live（生产默认 dry-run，live 需显式配置）"
        )

    @property
    def outbound_live(self) -> bool:
        return self.resolved_transport == "live"

    def bot_token_for_runtime(self) -> str:
        if self.bot_token:
            return self.bot_token
        return "0:local-no-send"

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
        if self.is_preview and not url:
            return "sqlite+aiosqlite:///./var/hetai-preview.sqlite"
        if not url:
            raise RuntimeError("生产模式必须设置 HETAI_DATABASE_URL")
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://") :]
        if url.startswith("postgresql://") and "+asyncpg" not in url:
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url

    def bind_host(self) -> str:
        if self.is_preview and (self.host or "").lower() in {"", "127.0.0.1", "localhost", "::1"}:
            return "0.0.0.0"
        return self.host or "127.0.0.1"

    def bind_port(self) -> int:
        import os

        if os.environ.get("HETAI_PORT"):
            return int(self.port)
        rail = (os.environ.get("PORT") or "").strip()
        if rail.isdigit():
            return int(rail)
        return int(self.port)

    def validate_boot(self) -> None:
        if self.is_demo:
            host = (self.host or "").lower()
            if host not in {"127.0.0.1", "localhost", "::1"}:
                raise RuntimeError(
                    "演示模式只允许绑定 127.0.0.1 / localhost / ::1，"
                    "禁止在非回环地址以不安全方式启动。"
                )
            return
        if self.is_preview:
            if not is_strong_secret(self.preview_access_secret, min_length=PREVIEW_ACCESS_MIN_LENGTH):
                raise RuntimeError("预览模式必须设置足够强度的 HETAI_PREVIEW_ACCESS_SECRET，否则拒绝启动")
            if not is_strong_secret(self.session_secret):
                raise RuntimeError("预览模式必须设置足够强度的 HETAI_SESSION_SECRET，否则拒绝启动")
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
        try:
            transport = self.resolved_transport
        except RuntimeError:
            raise
        if transport == "live" and not self.bot_connected:
            raise RuntimeError("生产 live 传输必须设置真实 HETAI_BOT_TOKEN（不能使用 0: 本地占位）")


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
