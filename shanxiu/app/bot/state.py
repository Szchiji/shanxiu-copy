"""
app/bot/state.py
----------------
Shared global state for the Telegram bot.

These module-level variables are populated by ``run_bot()`` in
``app.modules.core.routes`` at startup.

**Correct usage** — import the *module*, then access attributes:

    from app.bot import state as _state

    if _state.global_flask_app:
        with _state.global_flask_app.app_context():
            ...

**Incorrect usage** — this creates a local binding that won't reflect
later mutations (stays ``None`` forever):

    from app.bot.state import global_flask_app  # ← DON'T do this
"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from telegram.ext import Application
    from flask import Flask

# Populated by run_bot(); consumers must import the *module*, not the names.
global_ptb_app: "Application | None" = None
global_bot_loop = None
global_flask_app: "Flask | None" = None
