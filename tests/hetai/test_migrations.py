from pathlib import Path

from alembic import command
from alembic.config import Config


def test_alembic_upgrade_and_downgrade(tmp_path, monkeypatch):
    db = tmp_path / "migrate.sqlite"
    monkeypatch.setenv("HETAI_MODE", "demo")
    monkeypatch.setenv("HETAI_DATABASE_URL", f"sqlite+aiosqlite:///{db}")
    monkeypatch.setenv("HETAI_HOST", "127.0.0.1")
    from hetai.config import reset_settings_cache

    reset_settings_cache()
    root = Path(__file__).resolve().parents[2]
    cfg = Config(str(root / "alembic.ini"))
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    assert db.exists() or db.stat().st_size >= 0
