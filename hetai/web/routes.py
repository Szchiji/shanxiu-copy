"""统一界面路由：移动优先的同一壳层，授权以服务端群成员关系为准。"""

from __future__ import annotations

from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, func, or_, select

from hetai.auth import DEMO_ACTORS, can_admin, can_super, resolve_actor
from hetai.bot.runtime import bot_status
from hetai.config import get_settings
from hetai.db import session_scope
from hetai.jobs import run_named_task, scheduler_status
from hetai.models import (
    CreditAccount,
    CreditLedger,
    Group,
    GroupMember,
    PointAccount,
    PointLedger,
    Submission,
    TaskConfig,
    User,
)
from hetai.services.context import (
    COOKIE_CTX,
    GroupContext,
    resolve_group_context,
    require_visible_submission_group,
    set_context_cookie,
)
from hetai.services.credit import apply_credit
from hetai.services.errors import ForbiddenError, HetaiError, NotFoundError
from hetai.services.points import apply_points, list_group_accounts, list_group_ledger
from hetai.services.review import (
    list_review_events,
    publish_reward_label,
    publish_reward_points,
    review_submission,
)

import hetai
from pathlib import Path

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
templates.env.autoescape = True
templates.env.globals["publish_reward_points"] = publish_reward_points
templates.env.globals["publish_reward_label"] = publish_reward_label

router = APIRouter()


def _app_root(request: Request) -> str:
    path = request.url.path or ""
    return "/tg" if path == "/tg" or path.startswith("/tg/") else "/app"


def _surface_path(root: str, path: str) -> str:
    if path.startswith("/app"):
        return root + path[4:]
    return path


def _ctx(request: Request, actor, context: GroupContext | None = None, **extra):
    settings = get_settings()
    context = context or GroupContext(view="personal")
    root = _app_root(request)
    show_fixtures = bool(actor and getattr(actor, "is_demo", False))

    def p(path: str) -> str:
        return _surface_path(root, path)

    return {
        "request": request,
        "app_name": hetai.APP_NAME,
        "actor": actor,
        "context": context,
        "is_demo": settings.is_demo,
        "is_preview": settings.is_preview,
        "bot_connected": settings.bot_connected,
        "bot_status": bot_status(settings),
        "can_admin": (context.can_inbox if context else False) or (can_admin(actor.role) if actor else False),
        "can_inbox": bool(context and context.can_inbox),
        "can_super": can_super(actor.role) if actor else False,
        "show_fixtures": show_fixtures,
        "demo_actors": DEMO_ACTORS if show_fixtures else {},
        "demo_as": (request.cookies.get("hetai_demo_as") or "admin") if show_fixtures else "",
        "app_root": root,
        "p": p,
        "query": request.query_params,
        "flash_error": request.query_params.get("error"),
        "flash_ok": request.query_params.get("ok"),
        "live_test": bool(settings.is_preview and settings.bot_connected and not show_fixtures),
        "bot_username": settings.bot_username or "",
        "moderation_enabled": bool(settings.test_chat_id_list()),
        **extra,
    }


def _redirect(path: str, **params):
    if params:
        path = f"{path}?{urlencode({k: v for k, v in params.items() if v is not None})}"
    return RedirectResponse(path, status_code=303)


def _with_ctx(response: Response, ctx: GroupContext) -> Response:
    set_context_cookie(response, ctx)
    return response



def _force_group_query(request: Request, group_id: int) -> None:
    from starlette.datastructures import QueryParams

    data = dict(request.query_params)
    data["group"] = str(group_id)
    data.pop("view", None)
    request._query_params = QueryParams(data)


def _ctx_params(ctx: GroupContext, **extra) -> dict:
    params = dict(extra)
    if ctx.view == "global":
        params["view"] = "global"
    elif ctx.view == "group" and ctx.group is not None:
        params["group"] = ctx.group.id
    else:
        params["view"] = "personal"
    return params


def _redirect_ctx(path: str, ctx: GroupContext, **params):
    return _with_ctx(_redirect(path, **_ctx_params(ctx, **params)), ctx)


@router.get("/", include_in_schema=False)
async def root():
    return RedirectResponse("/app", status_code=302)


@router.get("/tg", response_class=HTMLResponse)
@router.get("/app", response_class=HTMLResponse)
async def app_home(request: Request, q: str = ""):
    async with session_scope() as session:
        actor = await resolve_actor(request, get_settings(), session)
        ctx = await resolve_group_context(session, actor, request)
        stmt = select(Submission).where(Submission.status == "approved").order_by(desc(Submission.id))
        if ctx.view == "group" and ctx.group is not None:
            stmt = stmt.where(Submission.group_id == ctx.group.id)
            visible_ids = {ctx.group.id}
        elif ctx.view == "global" and ctx.is_super:
            visible_ids = None
        else:
            visible_ids = {g.id for g in ctx.accessible}
            if not visible_ids:
                items = []
                return _page_directory(request, actor, ctx, items, {}, {}, q)
            stmt = stmt.where(Submission.group_id.in_(visible_ids))
        if q:
            like = f"%{q}%"
            stmt = stmt.where(or_(Submission.title.like(like), Submission.body.like(like)))
        items = (await session.execute(stmt)).scalars().all()
        group_ids = {i.group_id for i in items} | set(visible_ids or [])
        groups = []
        if group_ids:
            groups = (await session.execute(select(Group).where(Group.id.in_(group_ids)))).scalars().all()
        group_map = {g.id: g for g in groups}
        authors = {}
        if items:
            ids = {i.author_id for i in items}
            for u in (await session.execute(select(User).where(User.id.in_(ids)))).scalars():
                authors[u.id] = u
        return _page_directory(request, actor, ctx, items, group_map, authors, q)


def _page_directory(request, actor, ctx, items, group_map, authors, q):
    resp = templates.TemplateResponse(
        request,
        "app_home.html",
        _ctx(
            request,
            actor,
            ctx,
            nav="directory",
            items=items,
            group_map=group_map,
            authors=authors,
            q=q,
            page_title="目录",
        ),
    )
    return _with_ctx(resp, ctx)


@router.get("/tg/listings/{item_id}", response_class=HTMLResponse)
@router.get("/app/listings/{item_id}", response_class=HTMLResponse)
async def app_listing(request: Request, item_id: int):
    async with session_scope() as session:
        actor = await resolve_actor(request, get_settings(), session)
        ctx = await resolve_group_context(session, actor, request)
        item = await session.get(Submission, item_id)
        if not item or item.status != "approved":
            return templates.TemplateResponse(
                request,
                "error.html",
                _ctx(request, actor, ctx, nav="directory", page_title="未找到", error_title="条目不存在或未通过审核"),
                status_code=404,
            )
        await require_visible_submission_group(session, actor, item.group_id)
        group = await session.get(Group, item.group_id)
        author = await session.get(User, item.author_id)
        events = await list_review_events(session, item.id)
        resp = templates.TemplateResponse(
            request,
            "app_listing.html",
            _ctx(
                request,
                actor,
                ctx,
                nav="directory",
                item=item,
                group=group,
                author=author,
                events=events,
                page_title=item.title,
            ),
        )
        return _with_ctx(resp, ctx)


@router.get("/tg/me", response_class=HTMLResponse)
@router.get("/app/me", response_class=HTMLResponse)
async def app_me(request: Request):
    async with session_scope() as session:
        actor = await resolve_actor(request, get_settings(), session)
        ctx = await resolve_group_context(session, actor, request)
        user = await session.get(User, actor.user_id)
        credit = await session.scalar(select(CreditAccount).where(CreditAccount.user_id == user.id))
        credit_rows = (
            await session.execute(
                select(CreditLedger).where(CreditLedger.user_id == user.id).order_by(desc(CreditLedger.id)).limit(20)
            )
        ).scalars().all()
        points = []
        for g in ctx.accessible:
            m = next((x for x in (await session.execute(select(GroupMember).where(GroupMember.user_id == user.id, GroupMember.group_id == g.id))).scalars().all()), None)
            acc = await session.scalar(
                select(PointAccount).where(PointAccount.group_id == g.id, PointAccount.user_id == user.id)
            )
            ledgers = (
                await session.execute(
                    select(PointLedger)
                    .where(PointLedger.group_id == g.id, PointLedger.user_id == user.id)
                    .order_by(desc(PointLedger.id))
                    .limit(8)
                )
            ).scalars().all()
            points.append({"group": g, "role": m.role if m else "", "balance": acc.balance if acc else 0, "ledger": ledgers})
        my_subs = (
            await session.execute(
                select(Submission).where(Submission.author_id == user.id).order_by(desc(Submission.id))
            )
        ).scalars().all()
        sub_groups = {
            g.id: g
            for g in (
                await session.execute(select(Group).where(Group.id.in_([s.group_id for s in my_subs] or [0])))
            ).scalars()
        }
        resp = templates.TemplateResponse(
            request,
            "app_me.html",
            _ctx(
                request,
                actor,
                ctx,
                nav="me",
                user=user,
                credit=credit,
                credit_rows=credit_rows,
                points=points,
                my_subs=my_subs,
                sub_groups=sub_groups,
                page_title="我的",
            ),
        )
        return _with_ctx(resp, ctx)


@router.get("/tg/submit", response_class=HTMLResponse)
@router.get("/app/submit", response_class=HTMLResponse)
async def app_submit_form(request: Request):
    async with session_scope() as session:
        actor = await resolve_actor(request, get_settings(), session)
        ctx = await resolve_group_context(session, actor, request)
        groups = [g for g in ctx.accessible if g.is_active]
        resp = templates.TemplateResponse(
            request,
            "app_submit.html",
            _ctx(request, actor, ctx, nav="submit", groups=groups, page_title="投稿"),
        )
        return _with_ctx(resp, ctx)


@router.post("/tg/submit")
@router.post("/app/submit")
async def app_submit(request: Request, group_id: int = Form(...), title: str = Form(...), body: str = Form("")):
    async with session_scope() as session:
        actor = await resolve_actor(request, get_settings(), session)
        ctx = await resolve_group_context(session, actor, request)
        title = title.strip()
        if not title:
            return _redirect_ctx("/app/submit", ctx, error="标题不能为空")
        allowed = {g.id for g in ctx.accessible}
        if group_id not in allowed:
            return _redirect_ctx("/app/submit", ctx, error="只能向自己所在的群投稿")
        member = await session.scalar(
            select(GroupMember).where(GroupMember.group_id == group_id, GroupMember.user_id == actor.user_id)
        )
        if not member:
            return _redirect_ctx("/app/submit", ctx, error="只能向自己所在的群投稿")
        session.add(
            Submission(
                group_id=group_id,
                author_id=actor.user_id,
                title=title[:128],
                body=(body or "").strip(),
                status="pending",
                is_demo_fixture=get_settings().uses_fixtures,
            )
        )
    dest_ctx = GroupContext(view="group", group=type("G", (), {"id": group_id})(), accessible=ctx.accessible, admin_groups=ctx.admin_groups, is_super=ctx.is_super)
    # keep actual ctx cookie as personal or selected; redirect to me
    return _redirect_ctx("/app/me", ctx, ok="已提交，等待审核")


@router.get("/tg/inbox", response_class=HTMLResponse)
@router.get("/app/inbox", response_class=HTMLResponse)
async def app_inbox(request: Request, status: str = "pending"):
    async with session_scope() as session:
        actor = await resolve_actor(request, get_settings(), session)
        ctx = await resolve_group_context(session, actor, request)
        if not ctx.can_inbox:
            return templates.TemplateResponse(
                request,
                "error.html",
                _ctx(request, actor, ctx, nav="inbox", page_title="无权访问", error_title="没有可审核的群"),
                status_code=403,
            )
        admin_ids = {g.id for g in ctx.admin_groups}
        if ctx.view == "group" and ctx.group is not None:
            if ctx.group.id not in admin_ids and not ctx.is_super:
                return templates.TemplateResponse(
                    request,
                    "error.html",
                    _ctx(request, actor, ctx, nav="inbox", page_title="无权访问", error_title="没有该群的审核权限"),
                    status_code=403,
                )
            admin_ids = {ctx.group.id}
        elif ctx.view == "global" and ctx.is_super:
            admin_ids = {g.id for g in (await session.execute(select(Group))).scalars().all()}
        stmt = select(Submission).order_by(desc(Submission.id))
        if admin_ids:
            stmt = stmt.where(Submission.group_id.in_(admin_ids))
        else:
            items = []
            groups = {}
            authors = {}
            return _inbox_page(request, actor, ctx, items, groups, authors, status)
        if status:
            stmt = stmt.where(Submission.status == status)
        items = (await session.execute(stmt)).scalars().all()
        groups = {g.id: g for g in (await session.execute(select(Group).where(Group.id.in_(admin_ids or [0])))).scalars()}
        author_ids = {i.author_id for i in items}
        authors = {u.id: u for u in (await session.execute(select(User).where(User.id.in_(author_ids or [0])))).scalars()}
        return _inbox_page(request, actor, ctx, items, groups, authors, status)


def _inbox_page(request, actor, ctx, items, groups, authors, status):
    resp = templates.TemplateResponse(
        request,
        "app_inbox.html",
        _ctx(
            request,
            actor,
            ctx,
            nav="inbox",
            items=items,
            groups=groups,
            authors=authors,
            status=status,
            page_title="管理员待办",
        ),
    )
    return _with_ctx(resp, ctx)


@router.get("/tg/inbox/{item_id}", response_class=HTMLResponse)
@router.get("/app/inbox/{item_id}", response_class=HTMLResponse)
async def app_inbox_item(request: Request, item_id: int):
    async with session_scope() as session:
        actor = await resolve_actor(request, get_settings(), session)
        ctx = await resolve_group_context(session, actor, request)
        item = await session.get(Submission, item_id)
        if not item:
            return templates.TemplateResponse(
                request,
                "error.html",
                _ctx(request, actor, ctx, nav="inbox", page_title="未找到", error_title="投稿不存在"),
                status_code=404,
            )
        admin_ids = {g.id for g in ctx.admin_groups}
        if ctx.view == "global" and ctx.is_super:
            admin_ids.add(item.group_id)
        if item.group_id not in admin_ids and not (ctx.is_super and ctx.view == "global"):
            # super may review any group; group admin only own groups
            if not (ctx.is_super and item.group_id in {g.id for g in (await session.execute(select(Group))).scalars()}):
                return templates.TemplateResponse(
                    request,
                    "error.html",
                    _ctx(request, actor, ctx, nav="inbox", page_title="无权访问", error_title="没有该群的审核权限"),
                    status_code=403,
                )
            if not ctx.is_super:
                return templates.TemplateResponse(
                    request,
                    "error.html",
                    _ctx(request, actor, ctx, nav="inbox", page_title="无权访问", error_title="没有该群的审核权限"),
                    status_code=403,
                )
        group = await session.get(Group, item.group_id)
        author = await session.get(User, item.author_id)
        events = await list_review_events(session, item.id)
        resp = templates.TemplateResponse(
            request,
            "app_inbox_item.html",
            _ctx(
                request,
                actor,
                ctx,
                nav="inbox",
                item=item,
                group=group,
                author=author,
                events=events,
                reward_points=publish_reward_points(group),
                reward_label=publish_reward_label(group),
                page_title=item.title,
            ),
        )
        return _with_ctx(resp, ctx)


@router.post("/tg/inbox/{item_id}/review")
@router.post("/app/inbox/{item_id}/review")
async def app_inbox_review(request: Request, item_id: int, action: str = Form(...), review_note: str = Form("")):
    return await _do_review(request, item_id, action, review_note, back="/app/inbox")


@router.get("/tg/groups/{group_id}", response_class=HTMLResponse)
@router.get("/app/groups/{group_id}", response_class=HTMLResponse)
async def app_group_home(request: Request, group_id: int):
    async with session_scope() as session:
        actor = await resolve_actor(request, get_settings(), session)
        # force this group as the requested context via a shallow query merge
        request.scope.setdefault("query_string", b"")
        # resolve using explicit group: simulate by calling internals
        _force_group_query(request, group_id)
        ctx = await resolve_group_context(session, actor, request)
        pending_n = 0
        if ctx.can_admin_current:
            pending_n = int(
                await session.scalar(
                    select(func.count()).select_from(Submission).where(
                        Submission.group_id == group_id, Submission.status == "pending"
                    )
                )
                or 0
            )
        approved = (
            await session.execute(
                select(Submission)
                .where(Submission.group_id == group_id, Submission.status == "approved")
                .order_by(desc(Submission.id))
                .limit(8)
            )
        ).scalars().all()
        members_n = int(
            await session.scalar(select(func.count()).select_from(GroupMember).where(GroupMember.group_id == group_id)) or 0
        )
        resp = templates.TemplateResponse(
            request,
            "app_group.html",
            _ctx(
                request,
                actor,
                ctx,
                nav="directory",
                pending_n=pending_n,
                approved=approved,
                members_n=members_n,
                page_title=ctx.group.title if ctx.group else "群",
            ),
        )
        return _with_ctx(resp, ctx)


@router.get("/tg/groups/{group_id}/settings", response_class=HTMLResponse)
@router.get("/app/groups/{group_id}/settings", response_class=HTMLResponse)
async def app_group_settings(request: Request, group_id: int):
    async with session_scope() as session:
        actor = await resolve_actor(request, get_settings(), session)
        _force_group_query(request, group_id)
        ctx = await resolve_group_context(session, actor, request, require_admin=True)
        resp = templates.TemplateResponse(
            request,
            "app_group_settings.html",
            _ctx(request, actor, ctx, nav="inbox", page_title="群设置"),
        )
        return _with_ctx(resp, ctx)


@router.get("/tg/groups/{group_id}/ledger", response_class=HTMLResponse)
@router.get("/app/groups/{group_id}/ledger", response_class=HTMLResponse)
async def app_group_ledger(request: Request, group_id: int):
    async with session_scope() as session:
        actor = await resolve_actor(request, get_settings(), session)
        _force_group_query(request, group_id)
        ctx = await resolve_group_context(session, actor, request)
        if not ctx.can_admin_current and not any(g.id == group_id for g in ctx.accessible):
            return templates.TemplateResponse(
                request,
                "error.html",
                _ctx(request, actor, ctx, nav="me", page_title="无权访问", error_title="无权查看该群账本"),
                status_code=403,
            )
        accounts = await list_group_accounts(session, group_id) if ctx.can_admin_current else []
        if ctx.can_admin_current:
            ledger = await list_group_ledger(session, group_id, limit=80)
            users = {
                u.id: u
                for u in (
                    await session.execute(select(User).where(User.id.in_({a.user_id for a in accounts} | {r.user_id for r in ledger} or [0])))
                ).scalars()
            }
        else:
            ledger = (
                await session.execute(
                    select(PointLedger)
                    .where(PointLedger.group_id == group_id, PointLedger.user_id == actor.user_id)
                    .order_by(desc(PointLedger.id))
                    .limit(50)
                )
            ).scalars().all()
            users = {actor.user_id: await session.get(User, actor.user_id)}
        resp = templates.TemplateResponse(
            request,
            "app_group_ledger.html",
            _ctx(
                request,
                actor,
                ctx,
                nav="me",
                accounts=accounts,
                ledger=ledger,
                users=users,
                page_title="积分账本",
            ),
        )
        return _with_ctx(resp, ctx)


@router.get("/admin", response_class=HTMLResponse)
async def admin_home(request: Request):
    async with session_scope() as session:
        actor = await resolve_actor(request, get_settings(), session, require_admin=True)
        ctx = await resolve_group_context(session, actor, request)
        if not can_super(actor.role):
            return _redirect_ctx("/app/inbox", ctx)
        users_n = await session.scalar(select(func.count()).select_from(User))
        groups_n = await session.scalar(select(func.count()).select_from(Group))
        pending_n = await session.scalar(select(func.count()).select_from(Submission).where(Submission.status == "pending"))
        approved_n = await session.scalar(select(func.count()).select_from(Submission).where(Submission.status == "approved"))
        points_sum = await session.scalar(select(func.coalesce(func.sum(PointAccount.balance), 0)))
        credit_sum = await session.scalar(select(func.coalesce(func.sum(CreditAccount.balance), 0)))
        recent = (await session.execute(select(Submission).order_by(desc(Submission.id)).limit(8))).scalars().all()
        resp = templates.TemplateResponse(
            request,
            "admin_home.html",
            _ctx(
                request,
                actor,
                ctx,
                nav="tools",
                page_title="全局工具",
                stats={
                    "users": int(users_n or 0),
                    "groups": int(groups_n or 0),
                    "pending": int(pending_n or 0),
                    "approved": int(approved_n or 0),
                    "points_sum": int(points_sum or 0),
                    "credit_sum": int(credit_sum or 0),
                },
                recent=recent,
                scheduler=scheduler_status(),
            ),
        )
        return _with_ctx(resp, ctx)


@router.get("/admin/groups", response_class=HTMLResponse)
async def admin_groups(request: Request, q: str = ""):
    async with session_scope() as session:
        actor = await resolve_actor(request, get_settings(), session, require_admin=True)
        ctx = await resolve_group_context(session, actor, request)
        if can_super(actor.role) and ctx.view == "global":
            stmt = select(Group).order_by(Group.id)
        else:
            ids = {g.id for g in ctx.admin_groups} or {g.id for g in ctx.accessible}
            stmt = select(Group).where(Group.id.in_(ids or [0])).order_by(Group.id)
        if q:
            like = f"%{q}%"
            stmt = stmt.where(or_(Group.title.like(like), Group.description.like(like)))
        groups = (await session.execute(stmt)).scalars().all()
        counts = {}
        for g in groups:
            counts[g.id] = {
                "members": int(await session.scalar(select(func.count()).select_from(GroupMember).where(GroupMember.group_id == g.id)) or 0),
                "pending": int(await session.scalar(select(func.count()).select_from(Submission).where(Submission.group_id == g.id, Submission.status == "pending")) or 0),
            }
        resp = templates.TemplateResponse(
            request,
            "admin_groups.html",
            _ctx(request, actor, ctx, nav="tools", groups=groups, counts=counts, q=q, page_title="群组"),
        )
        return _with_ctx(resp, ctx)


@router.get("/admin/groups/{group_id}", response_class=HTMLResponse)
async def admin_group_detail(request: Request, group_id: int):
    async with session_scope() as session:
        actor = await resolve_actor(request, get_settings(), session, require_admin=True)
        _force_group_query(request, group_id)
        ctx = await resolve_group_context(session, actor, request, require_admin=True)
        group = ctx.group
        members = (await session.execute(select(GroupMember).where(GroupMember.group_id == group_id))).scalars().all()
        users = {u.id: u for u in (await session.execute(select(User).where(User.id.in_([m.user_id for m in members] or [0])))).scalars()}
        accounts = await list_group_accounts(session, group_id)
        ledger = await list_group_ledger(session, group_id)
        resp = templates.TemplateResponse(
            request,
            "admin_group.html",
            _ctx(
                request,
                actor,
                ctx,
                nav="tools",
                group=group,
                members=members,
                users=users,
                accounts=accounts,
                ledger=ledger,
                page_title=group.title if group else "群",
            ),
        )
        return _with_ctx(resp, ctx)


@router.post("/tg/groups/{group_id}/settings")
@router.post("/app/groups/{group_id}/settings")
@router.post("/admin/groups/{group_id}/settings")
async def admin_group_settings(
    request: Request,
    group_id: int,
    title: str = Form(...),
    description: str = Form(""),
    is_active: Optional[str] = Form(None),
    points_enabled: Optional[str] = Form(None),
    directory_enabled: Optional[str] = Form(None),
    publish_reward_points_value: str = Form("0"),
    publish_reward_label_value: str = Form(""),
):
    async with session_scope() as session:
        actor = await resolve_actor(request, get_settings(), session, require_admin=True)
        _force_group_query(request, group_id)
        ctx = await resolve_group_context(session, actor, request, require_admin=True)
        group = ctx.group
        if not group:
            return _redirect("/admin/groups", error="群组不存在")
        group.title = title.strip()[:128] or group.title
        group.description = (description or "").strip()
        group.is_active = bool(is_active)
        settings = dict(group.settings or {})
        settings["points_enabled"] = bool(points_enabled)
        settings["directory_enabled"] = bool(directory_enabled)
        try:
            settings["publish_reward_points"] = max(0, int(publish_reward_points_value or 0))
        except ValueError:
            settings["publish_reward_points"] = 0
        settings["publish_reward_label"] = (publish_reward_label_value or "").strip()[:256]
        group.settings = settings
    return _redirect_ctx(f"/admin/groups/{group_id}", ctx, ok="设置已保存")


@router.post("/admin/groups/{group_id}/points")
async def admin_group_points(
    request: Request,
    group_id: int,
    user_id: int = Form(...),
    delta: int = Form(...),
    reason: str = Form(""),
    idempotency_key: str = Form(""),
):
    async with session_scope() as session:
        actor = await resolve_actor(request, get_settings(), session, require_admin=True)
        _force_group_query(request, group_id)
        ctx = await resolve_group_context(session, actor, request, require_admin=True)
        try:
            await apply_points(
                session,
                group_id=group_id,
                user_id=user_id,
                delta=delta,
                reason=reason or "管理调整",
                idempotency_key=idempotency_key.strip() or None,
            )
        except HetaiError as exc:
            return _redirect_ctx(f"/admin/groups/{group_id}", ctx, error=exc.message)
        except ValueError as exc:
            return _redirect_ctx(f"/admin/groups/{group_id}", ctx, error=str(exc))
    return _redirect_ctx(f"/admin/groups/{group_id}", ctx, ok="积分已更新")


@router.get("/admin/users", response_class=HTMLResponse)
async def admin_users(request: Request, q: str = "", role: str = ""):
    async with session_scope() as session:
        actor = await resolve_actor(request, get_settings(), session, require_admin=True)
        ctx = await resolve_group_context(session, actor, request)
        if not can_super(actor.role):
            return templates.TemplateResponse(
                request,
                "error.html",
                _ctx(request, actor, ctx, nav="tools", page_title="无权访问", error_title="只有超级管理员可以查看全部成员"),
                status_code=403,
            )
        stmt = select(User).order_by(User.id)
        if q:
            like = f"%{q}%"
            stmt = stmt.where(or_(User.display_name.like(like), User.username.like(like)))
        if role:
            stmt = stmt.where(User.role == role)
        users = (await session.execute(stmt)).scalars().all()
        credits = {a.user_id: a.balance for a in (await session.execute(select(CreditAccount))).scalars()}
        resp = templates.TemplateResponse(
            request,
            "admin_users.html",
            _ctx(request, actor, ctx, nav="tools", users=users, credits=credits, q=q, role=role, page_title="成员"),
        )
        return _with_ctx(resp, ctx)


@router.get("/admin/users/{user_id}", response_class=HTMLResponse)
async def admin_user_detail(request: Request, user_id: int):
    async with session_scope() as session:
        actor = await resolve_actor(request, get_settings(), session, require_admin=True)
        ctx = await resolve_group_context(session, actor, request)
        if not can_super(actor.role):
            return templates.TemplateResponse(
                request,
                "error.html",
                _ctx(request, actor, ctx, nav="tools", page_title="无权访问", error_title="只有超级管理员可以查看全部成员"),
                status_code=403,
            )
        user = await session.get(User, user_id)
        if not user:
            return templates.TemplateResponse(
                request,
                "error.html",
                _ctx(request, actor, ctx, nav="tools", page_title="未找到", error_title="成员不存在"),
                status_code=404,
            )
        credit = await session.scalar(select(CreditAccount).where(CreditAccount.user_id == user.id))
        credit_rows = (
            await session.execute(select(CreditLedger).where(CreditLedger.user_id == user.id).order_by(desc(CreditLedger.id)).limit(30))
        ).scalars().all()
        memberships = (await session.execute(select(GroupMember).where(GroupMember.user_id == user.id))).scalars().all()
        point_rows = []
        for m in memberships:
            group = await session.get(Group, m.group_id)
            acc = await session.scalar(
                select(PointAccount).where(PointAccount.group_id == m.group_id, PointAccount.user_id == user.id)
            )
            point_rows.append({"group": group, "role": m.role, "balance": acc.balance if acc else 0})
        resp = templates.TemplateResponse(
            request,
            "admin_user.html",
            _ctx(
                request,
                actor,
                ctx,
                nav="tools",
                user=user,
                credit=credit,
                credit_rows=credit_rows,
                point_rows=point_rows,
                page_title=user.display_name,
            ),
        )
        return _with_ctx(resp, ctx)


@router.post("/admin/users/{user_id}/role")
async def admin_user_role(request: Request, user_id: int, role: str = Form(...)):
    async with session_scope() as session:
        actor = await resolve_actor(request, get_settings(), session, require_admin=True)
        ctx = await resolve_group_context(session, actor, request)
        if not can_super(actor.role):
            return _redirect_ctx(f"/admin/users/{user_id}", ctx, error="只有超级管理员可以改角色")
        if role not in {"super", "operator", "member"}:
            return _redirect_ctx(f"/admin/users/{user_id}", ctx, error="无效角色")
        user = await session.get(User, user_id)
        if not user:
            return _redirect("/admin/users", error="成员不存在")
        user.role = role
    return _redirect_ctx(f"/admin/users/{user_id}", ctx, ok="角色已更新")


@router.post("/admin/users/{user_id}/credit")
async def admin_user_credit(
    request: Request,
    user_id: int,
    delta: int = Form(...),
    reason: str = Form(""),
    idempotency_key: str = Form(""),
):
    async with session_scope() as session:
        actor = await resolve_actor(request, get_settings(), session, require_admin=True)
        ctx = await resolve_group_context(session, actor, request)
        if not can_super(actor.role):
            return _redirect_ctx(f"/admin/users/{user_id}", ctx, error="只有超级管理员可以调整全局信用")
        try:
            await apply_credit(
                session,
                user_id=user_id,
                delta=delta,
                reason=reason or "管理调整",
                idempotency_key=idempotency_key.strip() or None,
            )
        except HetaiError as exc:
            return _redirect_ctx(f"/admin/users/{user_id}", ctx, error=exc.message)
        except ValueError as exc:
            return _redirect_ctx(f"/admin/users/{user_id}", ctx, error=str(exc))
    return _redirect_ctx(f"/admin/users/{user_id}", ctx, ok="信用已更新")


@router.get("/admin/submissions", response_class=HTMLResponse)
async def admin_submissions(request: Request, status: str = "pending", q: str = "", group_id: int = 0):
    # compatibility alias of the mobile inbox
    return await app_inbox(request, status=status)


@router.get("/admin/submissions/{item_id}", response_class=HTMLResponse)
async def admin_submission_detail(request: Request, item_id: int):
    return await app_inbox_item(request, item_id)


@router.post("/admin/submissions/{item_id}/review")
async def admin_submission_review(request: Request, item_id: int, action: str = Form(...), review_note: str = Form("")):
    return await _do_review(request, item_id, action, review_note, back="/admin/submissions")


async def _do_review(request: Request, item_id: int, action: str, review_note: str, back: str):
    try:
        async with session_scope() as session:
            actor = await resolve_actor(request, get_settings(), session, require_admin=True)
            item = await session.get(Submission, item_id)
            if not item:
                ctx = await resolve_group_context(session, actor, request)
                return _redirect_ctx(back, ctx, error="投稿不存在")
            # authorize against the submission's group, not a spoofed query group
            _force_group_query(request, item.group_id)
            try:
                ctx = await resolve_group_context(session, actor, request, require_admin=True)
            except Exception:
                return _redirect(back, view="personal", error="没有该群的审核权限")
            result = await review_submission(
                session,
                actor=actor,
                membership=ctx.membership,
                item_id=item_id,
                action=action,
                review_note=review_note,
            )
            ok = "审核已保存"
            if result.get("replayed"):
                ok = "审核已处理（重复提交未重复奖励）"
            elif result.get("reward_delta"):
                ok = f"审核已保存，积分变动 {result['reward_delta']}"
            return _redirect_ctx(f"{back}/{item_id}", ctx, ok=ok)
    except (HetaiError, ValueError) as exc:
        msg = getattr(exc, "message", str(exc))
        return _redirect(f"{back}/{item_id}", error=msg)


@router.get("/admin/tasks", response_class=HTMLResponse)
async def admin_tasks(request: Request):
    async with session_scope() as session:
        actor = await resolve_actor(request, get_settings(), session, require_admin=True)
        ctx = await resolve_group_context(session, actor, request)
        if not can_super(actor.role):
            return templates.TemplateResponse(
                request,
                "error.html",
                _ctx(request, actor, ctx, nav="tools", page_title="无权访问", error_title="只有超级管理员可以管理全局任务"),
                status_code=403,
            )
        tasks = (await session.execute(select(TaskConfig).order_by(TaskConfig.id))).scalars().all()
        resp = templates.TemplateResponse(
            request,
            "admin_tasks.html",
            _ctx(request, actor, ctx, nav="tools", tasks=tasks, scheduler=scheduler_status(), page_title="任务"),
        )
        return _with_ctx(resp, ctx)


@router.post("/admin/tasks/{task_id}")
async def admin_task_update(
    request: Request,
    task_id: int,
    enabled: Optional[str] = Form(None),
    interval_seconds: int = Form(3600),
    run_now: Optional[str] = Form(None),
):
    async with session_scope() as session:
        actor = await resolve_actor(request, get_settings(), session, require_admin=True)
        ctx = await resolve_group_context(session, actor, request)
        if not can_super(actor.role):
            return _redirect_ctx("/app/inbox", ctx, error="只有超级管理员可以管理全局任务")
        task = await session.get(TaskConfig, task_id)
        if not task:
            return _redirect("/admin/tasks", error="任务不存在")
        task.enabled = bool(enabled)
        task.interval_seconds = max(60, int(interval_seconds or 3600))
        key = task.key
    if run_now:
        await run_named_task(key)
        return _redirect("/admin/tasks", ok="已执行一次（不发送通知）")
    return _redirect("/admin/tasks", ok="任务配置已保存")


@router.get("/demo/as/{key}")
async def demo_switch(request: Request, key: str):
    settings = get_settings()
    if not settings.uses_fixtures:
        return _redirect("/app", error="当前模式不可切换示例身份")
    if settings.is_preview:
        from hetai.preview_auth import valid_preview_session

        if not valid_preview_session(request, settings):
            return _redirect("/preview/login", next="/app")
    if key not in DEMO_ACTORS:
        key = "admin"
    dest = request.query_params.get("next") or "/app"
    if not dest.startswith("/") or dest.startswith("//") or dest.startswith("/tg"):
        dest = "/app"
    resp = RedirectResponse(dest, status_code=303)
    resp.set_cookie("hetai_demo_as", key, httponly=True, samesite="lax")
    # drop previous user's group context
    resp.delete_cookie(COOKIE_CTX, path="/")
    return resp


@router.get("/preview/login", response_class=HTMLResponse)
async def preview_login_page(request: Request):
    from hetai.preview_auth import (
        COOKIE_CSRF,
        issue_csrf_token,
        public_origin,
        safe_next,
    )

    settings = get_settings()
    if not settings.is_preview:
        return _redirect("/app")
    csrf = request.cookies.get(COOKIE_CSRF) or issue_csrf_token(settings)
    err = request.query_params.get("error")
    messages = {
        "csrf": "请求已过期或校验失败，请重试。",
        "auth": "口令不正确。",
        "rate": "尝试次数过多，请稍后再试。",
    }
    html = templates.TemplateResponse(
        request,
        "preview_login.html",
        {
            "app_name": hetai.APP_NAME,
            "csrf_token": csrf,
            "next_path": safe_next(request.query_params.get("next")),
            "flash_error": messages.get(err or "", None) if err else None,
        },
    )
    secure = public_origin(request).startswith("https://")
    html.set_cookie(COOKIE_CSRF, csrf, httponly=True, samesite="lax", secure=secure, max_age=3600, path="/")
    return html


@router.post("/preview/login")
async def preview_login_submit(request: Request):
    from hetai.preview_auth import (
        COOKIE_CSRF,
        issue_csrf_token,
        issue_preview_session,
        limiter,
        safe_next,
        set_auth_cookies,
        valid_csrf_token,
        verify_preview_password,
        _client_key,
    )
    import hmac

    settings = get_settings()
    if not settings.is_preview:
        return _redirect("/app")
    key = _client_key(request)
    if limiter.too_many(key):
        return RedirectResponse("/preview/login?error=rate", status_code=303)
    form = await request.form()
    csrf_token = str(form.get("csrf_token") or "")
    cookie_csrf = request.cookies.get(COOKIE_CSRF) or ""
    if not (
        valid_csrf_token(csrf_token, settings)
        and valid_csrf_token(cookie_csrf, settings)
        and hmac.compare_digest(csrf_token, cookie_csrf)
    ):
        return RedirectResponse("/preview/login?error=csrf", status_code=303)
    password = str(form.get("password") or "")
    if not verify_preview_password(password, settings):
        limiter.fail(key)
        return RedirectResponse("/preview/login?error=auth", status_code=303)
    limiter.clear(key)
    dest = safe_next(str(form.get("next") or "/app"))
    resp = RedirectResponse(dest, status_code=303)
    set_auth_cookies(resp, request, settings, issue_preview_session(settings), issue_csrf_token(settings))
    return resp


@router.post("/preview/logout")
async def preview_logout(request: Request):
    from hetai.preview_auth import clear_auth_cookies

    resp = RedirectResponse("/preview/login", status_code=303)
    clear_auth_cookies(resp)
    resp.delete_cookie(COOKIE_CTX, path="/")
    return resp
