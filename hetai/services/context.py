"""可信群上下文：URL / startapp / query 只作提示，授权以服务端成员关系为准。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import parse_qs, unquote

from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hetai.auth import Actor, can_super
from hetai.models import Group, GroupMember

GROUP_ADMIN_ROLES = {"owner", "admin"}
COOKIE_CTX = "hetai_ctx"
HINT_KEYS = ("group", "group_id", "chat_id", "startapp", "start_param", "tgWebAppStartParam")


@dataclass(frozen=True)
class GroupHint:
    """Session-independent group label for chrome and error pages."""

    id: int
    title: str
    is_demo_fixture: bool = False


def snapshot_groups(groups: list[Group]) -> list[GroupHint]:
    return [
        GroupHint(id=g.id, title=g.title, is_demo_fixture=bool(getattr(g, "is_demo_fixture", False)))
        for g in groups
    ]


@dataclass
class GroupContext:
    view: str  # personal | group | global
    group: Optional[Group] = None
    membership: Optional[GroupMember] = None
    accessible: list[Group] = field(default_factory=list)
    admin_groups: list[Group] = field(default_factory=list)
    is_super: bool = False
    can_admin_current: bool = False
    can_inbox: bool = False

    @property
    def group_id(self) -> Optional[int]:
        return self.group.id if self.group else None

    def qs(self, **extra) -> str:
        parts = []
        if self.view == "global":
            parts.append("view=global")
        elif self.view == "group" and self.group is not None:
            parts.append(f"group={self.group.id}")
        else:
            parts.append("view=personal")
        for k, v in extra.items():
            if v is None or v == "":
                continue
            parts.append(f"{k}={v}")
        return "&".join(parts)

    def href(self, path: str, **extra) -> str:
        q = self.qs(**extra)
        sep = "&" if "?" in path else "?"
        return f"{path}{sep}{q}" if q else path


def membership_is_admin(membership: Optional[GroupMember]) -> bool:
    return bool(membership and membership.role in GROUP_ADMIN_ROLES)


def actor_can_admin_group(actor: Actor, membership: Optional[GroupMember]) -> bool:
    return can_super(actor.role) or membership_is_admin(membership)


def _parse_int(raw: Optional[str]) -> Optional[int]:
    if raw is None or raw == "":
        return None
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def hinted_ids_from_mapping(data: dict) -> dict[str, Optional[int]]:
    """Extract untrusted hints. chat_instance is intentionally ignored."""
    data = {str(k): v for k, v in (data or {}).items()}
    group_pk = _parse_int(data.get("group") or data.get("group_id"))
    chat_id = _parse_int(data.get("chat_id"))
    start = data.get("startapp") or data.get("start_param") or data.get("tgWebAppStartParam") or ""
    start = unquote(str(start))
    if start and not chat_id:
        # accepted hint shapes: g-100123, chat_-100123, -100123
        compact = start.replace("chat_", "").replace("g", "", 1) if start.startswith("g") else start
        compact = compact.replace("chat_", "")
        chat_id = _parse_int(compact) or chat_id
    return {"group_id": group_pk, "chat_id": chat_id, "chat_instance": None}


def hints_from_init_data_fields(parsed: dict) -> dict[str, Optional[int]]:
    """initData chat / start_param are hints only; never bind chat_instance to chat_id."""
    raw = dict(parsed or {})
    if "chat_instance" in raw:
        raw = {k: v for k, v in raw.items() if k != "chat_instance"}
    chat = raw.get("chat")
    if isinstance(chat, dict) and "id" in chat:
        raw["chat_id"] = chat.get("id")
    elif isinstance(chat, str) and "id" in chat:
        raw["chat_id"] = None
    return hinted_ids_from_mapping(raw)


def _hints_from_request(request: Request) -> tuple[str, Optional[int], Optional[int], bool]:
    """Returns view_hint, group_pk, chat_id, explicit."""
    q = request.query_params
    formish = {k: q.get(k) for k in HINT_KEYS if q.get(k) is not None}
    view = (q.get("view") or "").strip().lower()
    explicit = bool(view or formish)
    if not explicit:
        cookie = (request.cookies.get(COOKIE_CTX) or "").strip()
        if cookie == "personal":
            view = "personal"
        elif cookie == "global":
            view = "global"
        elif cookie.startswith("g:"):
            formish["group"] = cookie[2:]
            view = "group"
    hints = hinted_ids_from_mapping(formish)
    if hints["group_id"] or hints["chat_id"]:
        view = view or "group"
    if view not in {"personal", "group", "global"}:
        view = "personal"
    return view, hints["group_id"], hints["chat_id"], explicit


def personal_shell_context(
    actor: Actor,
    accessible: list[Group],
    admin_groups: list[Group],
) -> GroupContext:
    """Personal view of groups the actor may already see. Never includes a denied group."""
    is_super = can_super(actor.role)
    return GroupContext(
        view="personal",
        accessible=snapshot_groups(accessible),
        admin_groups=snapshot_groups(admin_groups),
        is_super=is_super,
        can_admin_current=False,
        can_inbox=bool(admin_groups) or is_super,
    )


async def attach_actor_shell(request: Request, session: AsyncSession, actor: Actor) -> GroupContext:
    """Store actor and a safe personal context for chrome, including error pages."""
    request.state.actor = actor
    existing = getattr(request.state, "safe_context", None)
    if existing is not None:
        return existing
    _, accessible, admin_groups = await load_actor_memberships(session, actor)
    ctx = personal_shell_context(actor, accessible, admin_groups)
    request.state.safe_context = ctx
    return ctx


async def load_actor_memberships(
    session: AsyncSession, actor: Actor
) -> tuple[list[GroupMember], list[Group], list[Group]]:
    rows = (
        await session.execute(select(GroupMember).where(GroupMember.user_id == actor.user_id))
    ).scalars().all()
    if not rows:
        return [], [], []
    groups = {
        g.id: g
        for g in (
            await session.execute(select(Group).where(Group.id.in_([m.group_id for m in rows])))
        ).scalars()
    }
    want_fixture = bool(actor.is_demo)
    accessible = [
        groups[m.group_id]
        for m in rows
        if m.group_id in groups and bool(groups[m.group_id].is_demo_fixture) == want_fixture
    ]
    admin_groups = [
        groups[m.group_id]
        for m in rows
        if m.group_id in groups
        and bool(groups[m.group_id].is_demo_fixture) == want_fixture
        and (membership_is_admin(m) or can_super(actor.role))
    ]
    if can_super(actor.role):
        admin_groups = list(accessible)
    return list(rows), accessible, admin_groups


async def resolve_group_context(
    session: AsyncSession,
    actor: Actor,
    request: Request,
    *,
    require_group: bool = False,
    require_admin: bool = False,
) -> GroupContext:
    members, accessible, admin_groups = await load_actor_memberships(session, actor)
    by_group = {m.group_id: m for m in members}
    is_super = can_super(actor.role)
    request.state.actor = actor
    if getattr(request.state, "safe_context", None) is None:
        request.state.safe_context = personal_shell_context(actor, accessible, admin_groups)
    view, group_pk, chat_id, explicit = _hints_from_request(request)

    if view == "global":
        if not is_super:
            raise HTTPException(status_code=403, detail="只有超级管理员可以打开全局视图")
        if require_group:
            raise HTTPException(status_code=403, detail="此操作需要选择一个具体的群")
        ctx = GroupContext(
            view="global",
            accessible=accessible,
            admin_groups=accessible,
            is_super=True,
            can_admin_current=True,
            can_inbox=bool(accessible) or is_super,
        )
        return ctx

    group: Optional[Group] = None
    if group_pk is not None:
        group = await session.get(Group, group_pk)
        if not group:
            raise HTTPException(status_code=403, detail="无权访问该群或群不存在")
    elif chat_id is not None:
        group = (
            await session.execute(select(Group).where(Group.chat_id == chat_id))
        ).scalar_one_or_none()
        if not group:
            raise HTTPException(status_code=403, detail="无权访问该群或群不存在")

    if group is not None:
        if bool(group.is_demo_fixture) != bool(actor.is_demo):
            raise HTTPException(status_code=403, detail="无权访问该群")
        membership = by_group.get(group.id)
        if membership is None and not is_super:
            raise HTTPException(status_code=403, detail="无权访问该群")
        can_admin = actor_can_admin_group(actor, membership)
        if require_admin and not can_admin:
            raise HTTPException(status_code=403, detail="没有该群的管理权限")
        return GroupContext(
            view="group",
            group=group,
            membership=membership,
            accessible=accessible,
            admin_groups=admin_groups,
            is_super=is_super,
            can_admin_current=can_admin,
            can_inbox=bool(admin_groups) or is_super,
        )

    if require_group or view == "group":
        raise HTTPException(status_code=403, detail="请选择一个有权访问的群")
    return GroupContext(
        view="personal",
        accessible=accessible,
        admin_groups=admin_groups,
        is_super=is_super,
        can_admin_current=False,
        can_inbox=bool(admin_groups) or is_super,
    )


def set_context_cookie(response, ctx: GroupContext) -> None:
    if ctx.view == "global":
        value = "global"
    elif ctx.view == "group" and ctx.group is not None:
        value = f"g:{ctx.group.id}"
    else:
        value = "personal"
    response.set_cookie(COOKIE_CTX, value, httponly=True, samesite="lax", path="/")


async def require_visible_submission_group(
    session: AsyncSession, actor: Actor, group_id: int
) -> tuple[Group, Optional[GroupMember]]:
    members, _, _ = await load_actor_memberships(session, actor)
    by_group = {m.group_id: m for m in members}
    group = await session.get(Group, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="条目不存在或未通过审核")
    if can_super(actor.role):
        return group, by_group.get(group_id)
    if group_id not in by_group:
        raise HTTPException(status_code=403, detail="无权查看该群的内容")
    return group, by_group[group_id]
