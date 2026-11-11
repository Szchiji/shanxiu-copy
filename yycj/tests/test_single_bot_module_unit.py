"""`python -m bot.main` must not create a second copy of bot.main (second Bot, its aiohttp session never closed)."""
import os
import runpy
import sys

os.environ.setdefault("BOT_TOKEN", "123456789:TESTTOKENxxxxxxxxxxxxxxxxxxxxxxxxxxx")


def test_dash_m_hands_over_to_the_real_module(monkeypatch):
    import bot.main as real
    called = []
    monkeypatch.setattr(real, "main", lambda: called.append("main"))
    before = id(sys.modules["bot.main"])
    try:
        runpy.run_module("bot.main", run_name="__main__", alter_sys=False)
        raise AssertionError("should SystemExit")
    except SystemExit as e:
        assert e.code == 0
    assert called == ["main"]                       # ran the real module's main()
    assert id(sys.modules["bot.main"]) == before    # no second copy; the only Bot is bot.main.bot


def test_shutdown_closes_the_services_bot():
    src = open(os.path.join(os.path.dirname(__file__), "..", "bot", "main.py"), encoding="utf-8").read()
    assert 'import_module("bot.main").main()' in src and "await bot.session.close()" in src
