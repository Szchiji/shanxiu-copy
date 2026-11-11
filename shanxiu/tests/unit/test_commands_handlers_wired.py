"""
tests/unit/test_commands_handlers_wired.py
------------------------------------------
Ensure moderation/rank/clone command handlers live in commands.py and are
re-exported on routes for run_bot / setup_clone_handlers registration.
"""

import inspect

import pytest


WIRED = (
    'cmd_kick',
    'cmd_ban',
    'cmd_unban',
    'cmd_mute',
    'cmd_unmute',
    'cmd_pin',
    'cmd_unpin',
    'cmd_warn',
    'cmd_userinfo',
    'cmd_rank',
    'cmd_invite_rank',
    'cmd_active',
    'cmd_clones',
)


class TestCommandsModuleExports:
    def test_all_handlers_exist_and_are_async(self):
        from app.bot.handlers import commands as cmds

        for name in WIRED:
            fn = getattr(cmds, name)
            assert inspect.iscoroutinefunction(fn), name

    def test_plugin_gate_and_sched_helpers_present(self):
        from app.bot.handlers import commands as cmds

        assert hasattr(cmds, '_ensure_plugin_enabled')
        assert hasattr(cmds, '_sched_del')
        assert hasattr(cmds, '_is_unrestrictable_member')


class TestRoutesRebindsHandlers:
    def test_routes_cmd_names_are_commands_module(self):
        from app.bot.handlers import commands as cmds
        import app.modules.core.routes as routes

        for name in WIRED:
            assert getattr(routes, name) is getattr(cmds, name), name
