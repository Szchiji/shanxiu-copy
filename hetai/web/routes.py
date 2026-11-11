"""统一界面路由：同一壳层下的应用（用户）与管理（角色分区），不是两套产品。"""

from __future__ import annotations

from datetime import datetime
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
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
from hetai.services.credit import apply_credit
from hetai.services.errors import ForbiddenError, HetaiError, NotFoundError
from hetai.services.points import apply_points, list_group_accounts, list_group_ledger

import hetai
from pathlib import Path

templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
templates.env.autoescape = True

router = APIRouter()


def _ctx(request: Request, actor, **extra):
    settings = get_settings()
    return {
        "request": request,
        "app_name": hetai.APP_NAME,
        "actor": actor,
        "is_demo": settings.is_demo,
        "is_preview": settings.is_preview,
        "bot_connected": settings.bot_connected,
        "bot_status": bot_status(settings),
        "can_admin": can_admin(actor.role) if actor else False,
        "can_super": can_super(actor.role) if actor else False,
        "demo_actors": DEMO_ACTORS if settings.uses_fixtures else {},
        "demo_as": (request.cookies.get("hetai_demo_as") or "admin") if settings.uses_fixtures else "",
        "query": request.query_params,
        "flash_error": request.query_params.get("error"),
        "flash_ok": request.query_params.get("ok"),
        **extra,
    }


def _redirect(path: str, **params):
    if params:
        path = f"{path}?{urlencode({k: v for k, v in params.items() if v is not None})}"
    return RedirectResponse(path, status_code=303)


@router.get("/", include_in_schema=False)
async def root():
    return RedirectResponse("/app", status_code=302)


@router.get("/app", response_class=HTMLResponse)
async def app_home(request: Request, q: str = "", group_id: int = 0):
    async with session_scope() as session:
        actor = await resolve_actor(request, get_settings(), session)
        stmt = select(Submission).where(Submission.status == "approved").order_by(desc(Submission.id))
        if group_id:
            stmt = stmt.where(Submission.group_id == group_id)
        if q:
            like = f"%{q}%"
            stmt = stmt.where(or_(Submission.title.like(like), Submission.body.like(like)))
        items = (await session.execute(stmt)).scalars().all()
        groups = (await session.execute(select(Group).order_by(Group.id))).scalars().all()
        group_map = {g.id: g for g in groups}
        authors = {}
        if items:
            ids = {i.author_id for i in items}
            for u in (await session.execute(select(User).where(User.id.in_(ids)))).scalars():
                authors[u.id] = u
        return templates.TemplateResponse(
            request,
            "app_home.html",
            _ctx(
                request,
                actor,
                nav="app",
                items=items,
                groups=groups,
                group_map=group_map,
                authors=authors,
                q=q,
                group_id=group_id,
                page_title="目录",
            ),
        )


@router.get("/app/listings/{item_id}", response_class=HTMLResponse)
async def app_listing(request: Request, item_id: int):
    async with session_scope() as session:
        actor = await resolve_actor(request, get_settings(), session)
        item = await session.get(Submission, item_id)
        if not item or item.status != "approved":
            return templates.TemplateResponse(
                request,
                "error.html",
                _ctx(request, actor, nav="app", page_title="未找到", error_title="条目不存在或未通过审核"),
                status_code=404,
            )
        group = await session.get(Group, item.group_id)
        author = await session.get(User, item.author_id)
        return templates.TemplateResponse(
            request,
            "app_listing.html",
            _ctx(request, actor, nav="app", item=item, group=group, author=author, page_title=item.title),
        )


@router.get("/app/me", response_class=HTMLResponse)
async def app_me(request: Request):
    async with session_scope() as session:
        actor = await resolve_actor(request, get_settings(), session)
        user = await session.get(User, actor.user_id)
        credit = await session.scalar(select(CreditAccount).where(CreditAccount.user_id == user.id))
        credit_rows = (
            await session.execute(
                select(CreditLedger).where(CreditLedger.user_id == user.id).order_by(desc(CreditLedger.id)).limit(20)
            )
        ).scalars().all()
        memberships = (
            await session.execute(select(GroupMember).where(GroupMember.user_id == user.id))
        ).scalars().all()
        points = []
        for m in memberships:
            group = await session.get(Group, m.group_id)
            acc = await session.scalar(
                select(PointAccount).where(PointAccount.group_id == m.group_id, PointAccount.user_id == user.id)
            )
            points.append({"group": group, "role": m.role, "balance": acc.balance if acc else 0})
        my_subs = (
            await session.execute(
                select(Submission).where(Submission.author_id == user.id).order_by(desc(Submission.id))
            )
        ).scalars().all()
        return templates.TemplateResponse(
            request,
            "app_me.html",
            _ctx(
                request,
                actor,
                nav="me",
                user=user,
                credit=credit,
                credit_rows=credit_rows,
                points=points,
                my_subs=my_subs,
                page_title="我的",
            ),
        )


@router.get("/app/submit", response_class=HTMLResponse)
async def app_submit_form(request: Request):
    async with session_scope() as session:
        actor = await resolve_actor(request, get_settings(), session)
        memberships = (
            await session.execute(select(GroupMember).where(GroupMember.user_id == actor.user_id))
        ).scalars().all()
        groups = []
        for m in memberships:
            g = await session.get(Group, m.group_id)
            if g and g.is_active:
                groups.append(g)
        return templates.TemplateResponse(
            request,
            "app_submit.html",
            _ctx(request, actor, nav="submit", groups=groups, page_title="投稿"),
        )


@router.post("/app/submit")
async def app_submit(request: Request, group_id: int = Form(...), title: str = Form(...), body: str = Form("")):
    async with session_scope() as session:
        actor = await resolve_actor(request, get_settings(), session)
        title = title.strip()
        if not title:
            return _redirect("/app/submit", error="标题不能为空")
        member = await session.scalar(
            select(GroupMember).where(GroupMember.group_id == group_id, GroupMember.user_id == actor.user_id)
        )
        if not member:
            return _redirect("/app/submit", error="只能向自己所在的群投稿")
        session.add(
            Submission(
                group_id=group_id,
                author_id=actor.user_id,
                title=title[:128],
                body=(body or "").strip(),
                status="pending",
                is_demo_fixture=get_settings().is_demo,
            )
        )
    return _redirect("/app/me", ok="已提交，等待审核")


@router.get("/admin", response_class=HTMLResponse)
async def admin_home(request: Request):
    async with session_scope() as session:
        actor = await resolve_actor(request, get_settings(), session, require_admin=True)
        users_n = await session.scalar(select(func.count()).select_from(User))
        groups_n = await session.scalar(select(func.count()).select_from(Group))
        pending_n = await session.scalar(select(func.count()).select_from(Submission).where(Submission.status == "pending"))
        approved_n = await session.scalar(select(func.count()).select_from(Submission).where(Submission.status == "approved"))
        points_sum = await session.scalar(select(func.coalesce(func.sum(PointAccount.balance), 0)))
        credit_sum = await session.scalar(select(func.coalesce(func.sum(CreditAccount.balance), 0)))
        recent = (
            await session.execute(select(Submission).order_by(desc(Submission.id)).limit(8))
        ).scalars().all()
        return templates.TemplateResponse(
            request,
            "admin_home.html",
            _ctx(
                request,
                actor,
                nav="dashboard",
                page_title="概览",
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


@router.get("/admin/groups", response_class=HTMLResponse)
async def admin_groups(request: Request, q: str = ""):
    async with session_scope() as session:
        actor = await resolve_actor(request, get_settings(), session, require_admin=True)
        stmt = select(Group).order_by(Group.id)
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
        return templates.TemplateResponse(
            request,
            "admin_groups.html",
            _ctx(request, actor, nav="groups", groups=groups, counts=counts, q=q, page_title="群组"),
        )


@router.get("/admin/groups/{group_id}", response_class=HTMLResponse)
async def admin_group_detail(request: Request, group_id: int):
    async with session_scope() as session:
        actor = await resolve_actor(request, get_settings(), session, require_admin=True)
        group = await session.get(Group, group_id)
        if not group:
            return templates.TemplateResponse(
                request,
                "error.html",
                _ctx(request, actor, nav="groups", page_title="未找到", error_title="群组不存在"),
                status_code=404,
            )
        members = (await session.execute(select(GroupMember).where(GroupMember.group_id == group_id))).scalars().all()
        users = {u.id: u for u in (await session.execute(select(User).where(User.id.in_([m.user_id for m in members] or [0])))).scalars()}
        accounts = await list_group_accounts(session, group_id)
        ledger = await list_group_ledger(session, group_id)
        return templates.TemplateResponse(
            request,
            "admin_group.html",
            _ctx(
                request,
                actor,
                nav="groups",
                group=group,
                members=members,
                users=users,
                accounts=accounts,
                ledger=ledger,
                page_title=group.title,
            ),
        )


@router.post("/admin/groups/{group_id}/settings")
async def admin_group_settings(
    request: Request,
    group_id: int,
    title: str = Form(...),
    description: str = Form(""),
    is_active: Optional[str] = Form(None),
    points_enabled: Optional[str] = Form(None),
    directory_enabled: Optional[str] = Form(None),
):
    async with session_scope() as session:
        actor = await resolve_actor(request, get_settings(), session, require_admin=True)
        group = await session.get(Group, group_id)
        if not group:
            return _redirect("/admin/groups", error="群组不存在")
        group.title = title.strip()[:128] or group.title
        group.description = (description or "").strip()
        group.is_active = bool(is_active)
        settings = dict(group.settings or {})
        settings["points_enabled"] = bool(points_enabled)
        settings["directory_enabled"] = bool(directory_enabled)
        group.settings = settings
    return _redirect(f"/admin/groups/{group_id}", ok="设置已保存")


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
        await resolve_actor(request, get_settings(), session, require_admin=True)
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
            return _redirect(f"/admin/groups/{group_id}", error=exc.message)
        except ValueError as exc:
            return _redirect(f"/admin/groups/{group_id}", error=str(exc))
    return _redirect(f"/admin/groups/{group_id}", ok="积分已更新")


@router.get("/admin/users", response_class=HTMLResponse)
async def admin_users(request: Request, q: str = "", role: str = ""):
    async with session_scope() as session:
        actor = await resolve_actor(request, get_settings(), session, require_admin=True)
        stmt = select(User).order_by(User.id)
        if q:
            like = f"%{q}%"
            stmt = stmt.where(or_(User.display_name.like(like), User.username.like(like)))
        if role:
            stmt = stmt.where(User.role == role)
        users = (await session.execute(stmt)).scalars().all()
        credits = {
            a.user_id: a.balance
            for a in (await session.execute(select(CreditAccount))).scalars()
        }
        return templates.TemplateResponse(
            request,
            "admin_users.html",
            _ctx(request, actor, nav="users", users=users, credits=credits, q=q, role=role, page_title="成员"),
        )


@router.get("/admin/users/{user_id}", response_class=HTMLResponse)
async def admin_user_detail(request: Request, user_id: int):
    async with session_scope() as session:
        actor = await resolve_actor(request, get_settings(), session, require_admin=True)
        user = await session.get(User, user_id)
        if not user:
            return templates.TemplateResponse(
                request,
                "error.html",
                _ctx(request, actor, nav="users", page_title="未找到", error_title="成员不存在"),
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
        return templates.TemplateResponse(
            request,
            "admin_user.html",
            _ctx(
                request,
                actor,
                nav="users",
                user=user,
                credit=credit,
                credit_rows=credit_rows,
                point_rows=point_rows,
                page_title=user.display_name,
            ),
        )


@router.post("/admin/users/{user_id}/role")
async def admin_user_role(request: Request, user_id: int, role: str = Form(...)):
    async with session_scope() as session:
        actor = await resolve_actor(request, get_settings(), session, require_admin=True)
        if not can_super(actor.role):
            return _redirect(f"/admin/users/{user_id}", error="只有超级管理员可以改角色")
        if role not in {"super", "operator", "member"}:
            return _redirect(f"/admin/users/{user_id}", error="无效角色")
        user = await session.get(User, user_id)
        if not user:
            return _redirect("/admin/users", error="成员不存在")
        user.role = role
    return _redirect(f"/admin/users/{user_id}", ok="角色已更新")


@router.post("/admin/users/{user_id}/credit")
async def admin_user_credit(
    request: Request,
    user_id: int,
    delta: int = Form(...),
    reason: str = Form(""),
    idempotency_key: str = Form(""),
):
    async with session_scope() as session:
        await resolve_actor(request, get_settings(), session, require_admin=True)
        try:
            await apply_credit(
                session,
                user_id=user_id,
                delta=delta,
                reason=reason or "管理调整",
                idempotency_key=idempotency_key.strip() or None,
            )
        except HetaiError as exc:
            return _redirect(f"/admin/users/{user_id}", error=exc.message)
        except ValueError as exc:
            return _redirect(f"/admin/users/{user_id}", error=str(exc))
    return _redirect(f"/admin/users/{user_id}", ok="信用已更新")


@router.get("/admin/submissions", response_class=HTMLResponse)
async def admin_submissions(request: Request, status: str = "pending", q: str = "", group_id: int = 0):
    async with session_scope() as session:
        actor = await resolve_actor(request, get_settings(), session, require_admin=True)
        stmt = select(Submission).order_by(desc(Submission.id))
        if status:
            stmt = stmt.where(Submission.status == status)
        if group_id:
            stmt = stmt.where(Submission.group_id == group_id)
        if q:
            like = f"%{q}%"
            stmt = stmt.where(or_(Submission.title.like(like), Submission.body.like(like)))
        items = (await session.execute(stmt)).scalars().all()
        groups = {g.id: g for g in (await session.execute(select(Group))).scalars()}
        authors = {u.id: u for u in (await session.execute(select(User))).scalars()}
        return templates.TemplateResponse(
            request,
            "admin_submissions.html",
            _ctx(
                request,
                actor,
                nav="submissions",
                items=items,
                groups=groups,
                authors=authors,
                status=status,
                q=q,
                group_id=group_id,
                all_groups=list(groups.values()),
                page_title="目录审核",
            ),
        )


@router.get("/admin/submissions/{item_id}", response_class=HTMLResponse)
async def admin_submission_detail(request: Request, item_id: int):
    async with session_scope() as session:
        actor = await resolve_actor(request, get_settings(), session, require_admin=True)
        item = await session.get(Submission, item_id)
        if not item:
            return templates.TemplateResponse(
                request,
                "error.html",
                _ctx(request, actor, nav="submissions", page_title="未找到", error_title="投稿不存在"),
                status_code=404,
            )
        group = await session.get(Group, item.group_id)
        author = await session.get(User, item.author_id)
        return templates.TemplateResponse(
            request,
            "admin_submission.html",
            _ctx(request, actor, nav="submissions", item=item, group=group, author=author, page_title=item.title),
        )


@router.post("/admin/submissions/{item_id}/review")
async def admin_submission_review(
    request: Request,
    item_id: int,
    action: str = Form(...),
    review_note: str = Form(""),
):
    async with session_scope() as session:
        actor = await resolve_actor(request, get_settings(), session, require_admin=True)
        item = await session.get(Submission, item_id)
        if not item:
            return _redirect("/admin/submissions", error="投稿不存在")
        if action not in {"approve", "reject"}:
            return _redirect(f"/admin/submissions/{item_id}", error="无效操作")
        item.status = "approved" if action == "approve" else "rejected"
        item.review_note = (review_note or "").strip()[:256]
        item.reviewed_by = actor.user_id
        item.reviewed_at = datetime.utcnow()
    return _redirect(f"/admin/submissions/{item_id}", ok="审核已保存")


@router.get("/admin/tasks", response_class=HTMLResponse)
async def admin_tasks(request: Request):
    async with session_scope() as session:
        actor = await resolve_actor(request, get_settings(), session, require_admin=True)
        tasks = (await session.execute(select(TaskConfig).order_by(TaskConfig.id))).scalars().all()
        return templates.TemplateResponse(
            request,
            "admin_tasks.html",
            _ctx(request, actor, nav="tasks", tasks=tasks, scheduler=scheduler_status(), page_title="任务"),
        )


@router.post("/admin/tasks/{task_id}")
async def admin_task_update(
    request: Request,
    task_id: int,
    enabled: Optional[str] = Form(None),
    interval_seconds: int = Form(3600),
    run_now: Optional[str] = Form(None),
):
    async with session_scope() as session:
        await resolve_actor(request, get_settings(), session, require_admin=True)
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
    if not get_settings().uses_fixtures:
        return _redirect("/app", error="当前模式不可切换示例身份")
    if key not in DEMO_ACTORS:
        key = "admin"
    dest = request.query_params.get("next") or "/app"
    if not dest.startswith("/"):
        dest = "/app"
    resp = RedirectResponse(dest, status_code=303)
    resp.set_cookie("hetai_demo_as", key, httponly=True, samesite="lax")
    return resp


@router.get("/preview/login", response_class=HTMLResponse)
async def preview_login_page(request: Request):
    from hetai.preview_auth import (
        COOKIE_CSRF,
        issue_csrf_token,
        public_origin,
        safe_next,
        set_auth_cookies,
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
    return resp
