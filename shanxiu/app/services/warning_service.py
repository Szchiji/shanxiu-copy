"""
app/services/warning_service.py
--------------------------------
Helpers for group warning (/warn and keyword action=warn) persistence.

Pure helpers are unit-testable without DB. ``record_warning`` / ``count_warnings``
require an active Flask application context.
"""

from __future__ import annotations

from typing import Optional, Tuple


# No GroupSettings / UI fields exist for warn thresholds today.
# Keep a stub so future settings can plug in without rewriting call sites.
# Returns (action_or_None, human_label). action in {'mute','kick','ban'} or None.
def resolve_escalation(warn_count: int) -> Tuple[Optional[str], Optional[str]]:
    """Decide whether *warn_count* should trigger auto-punishment.

    Currently always returns ``(None, None)`` because the admin UI and models
    have no warn-threshold configuration. Callers must not invent mute/kick/ban
    from this stub (would change production behavior unexpectedly).
    """
    _ = warn_count
    return None, None


def format_command_warn_reply(user_name: str, reason: str, count: int) -> str:
    """Chinese reply text for ``/warn`` after a successful record."""
    name = user_name or '该用户'
    reason_text = reason or '违反群规'
    return (
        f"⚠️ 警告\n\n"
        f"用户: {name}\n"
        f"原因: {reason_text}\n"
        f"累计警告: {count} 次\n\n"
        f"请遵守群规，避免再次违规！"
    )


def format_keyword_warn_reply(count: int) -> str:
    """Chinese reply text for keyword-filter ``action=warn``."""
    return f"⚠️ 警告：消息包含禁止关键词（累计警告 {count} 次）"


def count_warnings(group_id: int, user_id: int) -> int:
    """Return number of stored warnings for *user_id* in *group_id*."""
    from app.models import GroupWarning

    return GroupWarning.query.filter_by(group_id=group_id, user_id=user_id).count()


def record_warning(
    group_id: int,
    user_id: int,
    *,
    admin_id: Optional[int] = None,
    reason: Optional[str] = None,
    source: str = 'command',
) -> int:
    """Persist a warning and return the user's new cumulative count.

    Requires an active Flask application context.
    """
    from app import db
    from app.models import GroupWarning

    row = GroupWarning(
        group_id=group_id,
        user_id=user_id,
        admin_id=admin_id,
        reason=reason or '违反群规',
        source=source or 'command',
    )
    db.session.add(row)
    db.session.commit()
    return count_warnings(group_id, user_id)
