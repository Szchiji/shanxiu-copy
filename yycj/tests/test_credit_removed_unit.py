"""Orchid-credit feature is fully retired: no calculation, display, or business gates."""
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MINI = ROOT / "miniapp"


def test_credit_service_module_removed():
    assert not (ROOT / "bot/services/credit_service.py").exists()
    assert not (ROOT / "bot/handlers/credit.py").exists()
    with pytest.raises(ModuleNotFoundError):
        __import__("bot.services.credit_service")


def test_daily_credit_job_and_menus_removed():
    import bot.main as main
    from bot.handlers import session as session_h
    from bot.handlers import search as search_h
    from bot.keyboards import session_end_kb

    assert not hasattr(main, "job_daily_credit")
    src = (ROOT / "bot/main.py").read_text(encoding="utf-8")
    assert "daily_credit" not in src
    assert "tick_shadow_daily" not in src
    for text in (getattr(search_h, "IGNORE_TEXTS", None),):
        pass
    search_src = (ROOT / "bot/handlers/search.py").read_text(encoding="utf-8")
    session_src = (ROOT / "bot/handlers/session.py").read_text(encoding="utf-8")
    assert "🌸 兰花信用" not in search_src and "🌸 口碑" not in search_src
    assert "本次口碑分变动" not in session_src
    markup = session_end_kb("sid-test")
    labels = [btn.text for row in markup.inline_keyboard for btn in row]
    assert "👍 好评结算" not in labels
    assert "👍 好评" in labels


def test_channel_card_and_user_serializer_strip_credit():
    from bot.services.search_service import format_lamp_card
    from bot.api.routes_core_base import _ser_user, _ser_lamp

    card = format_lamp_card({
        "title": "测试",
        "city": "深圳",
        "district": "南山",
        "price_text": "面议",
        "tags": ["a"],
        "authenticity_score": 99,
        "lamp_id": "L1",
        "description": "d",
    })
    assert "口碑分" not in card
    assert "99" not in card

    user = _ser_user({
        "user_id": 1,
        "role": "guest",
        "lanhua_score": 888,
        "tier": "金月",
        "is_shadowed": True,
        "shadow_days": 3,
        "shadow_reason": "x",
        "total_earned": 10,
        "total_deducted": 2,
        "recovery_progress": 1,
        "is_banned": False,
        "created_at": None,
        "updated_at": None,
        "last_recovery_at": "should-drop",
    })
    for k in ("lanhua_score", "tier", "is_shadowed", "shadow_days", "shadow_reason",
              "total_earned", "total_deducted", "recovery_progress", "last_recovery_at"):
        assert k not in user, k
    assert user["user_id"] == 1 and user["is_banned"] is False

    lamp = _ser_lamp({"lamp_id": "L1", "title": "t", "city": "c", "authenticity_score": 80, "credit_boost": 5, "photos": [], "media": []})
    assert "authenticity_score" not in lamp and "credit_boost" not in lamp


def test_frontend_and_admin_ui_have_no_credit_surfaces():
    go = (MINI / "go.html").read_text(encoding="utf-8")
    desk = (MINI / "desk.html").read_text(encoding="utf-8")
    app = (MINI / "app.js").read_text(encoding="utf-8")
    admin_js = (MINI / "admin.js").read_text(encoding="utf-8")
    ops = (MINI / "admin-ops.js").read_text(encoding="utf-8")
    assert "app.bundle.js?v=20261112b" in go and "app.bundle.css?v=20261112b" in go
    assert "兰花令" not in app
    assert "rulesSheet" not in go and "口碑分说明" not in go
    assert "调整口碑分" not in desk and "月影遮蔽" not in desk and "遮蔽名单" not in desk
    assert "/api/admin/credit/adjust" not in admin_js
    assert "/api/admin/shadow" not in admin_js
    assert "/api/admin/users/shadow" not in ops
    assert "口碑 " not in ops
    assert "填入遮蔽" not in ops
    for name in ("console.html", "desk-legacy.html"):
        html = (MINI / name).read_text(encoding="utf-8")
        assert "调整口碑分" not in html and "月影遮蔽" not in html
        assert f"admin-ops.js?v=20261112a" in html
        assert f"admin.js?v=20261112a" in html
