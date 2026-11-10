"""单一调度器：默认不在多进程下重复启动；任务不发送通知。"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import func, select

from hetai.config import Settings
from hetai.db import session_scope
from hetai.models import PointAccount, PointLedger, Submission, TaskConfig

logger = logging.getLogger("hetai.jobs")
_scheduler = None


async def run_named_task(key: str) -> str:
    async with session_scope() as session:
        res = await session.execute(select(TaskConfig).where(TaskConfig.key == key))
        task = res.scalar_one_or_none()
        if not task:
            return "missing"
        if not task.enabled:
            task.last_run_at = datetime.utcnow()
            task.last_status = "skipped"
            task.last_detail = "已停用"
            return "skipped"
        if key == "ledger_integrity":
            accounts = (await session.execute(select(PointAccount))).scalars().all()
            mismatch = 0
            for acc in accounts:
                total = await session.scalar(
                    select(func.coalesce(func.sum(PointLedger.delta), 0)).where(
                        PointLedger.group_id == acc.group_id,
                        PointLedger.user_id == acc.user_id,
                    )
                )
                if int(total or 0) != int(acc.balance):
                    mismatch += 1
            task.last_detail = f"检查 {len(accounts)} 个积分账户，不一致 {mismatch} 个。未发送通知。"
            task.last_status = "ok" if mismatch == 0 else "attention"
        elif key == "submission_aging":
            pending = await session.scalar(
                select(func.count()).select_from(Submission).where(Submission.status == "pending")
            )
            task.last_detail = f"待审投稿 {int(pending or 0)} 条。未发送通知。"
            task.last_status = "ok"
        else:
            task.last_status = "deferred"
            task.last_detail = "该任务在本版本未实现业务，仅记录状态，未发送通知。"
        task.last_run_at = datetime.utcnow()
        return task.last_status


def start_scheduler(settings: Settings):
    global _scheduler
    if not settings.run_scheduler:
        logger.info("scheduler disabled (HETAI_RUN_SCHEDULER is false)")
        return None
    if _scheduler is not None:
        return _scheduler
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
    except Exception:
        logger.warning("APScheduler not installed; scheduler not started")
        return None
    scheduler = AsyncIOScheduler()

    async def _tick():
        async with session_scope() as session:
            rows = (await session.execute(select(TaskConfig).where(TaskConfig.enabled.is_(True)))).scalars().all()
            keys = [r.key for r in rows]
        for key in keys:
            try:
                await run_named_task(key)
            except Exception:
                logger.exception("task failed: %s", key)

    scheduler.add_job(_tick, "interval", seconds=60, id="hetai_tick", replace_existing=True, max_instances=1, coalesce=True)
    scheduler.start()
    _scheduler = scheduler
    logger.info("started single scheduler")
    return scheduler


async def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def scheduler_status() -> dict:
    running = bool(_scheduler is not None and getattr(_scheduler, "running", False))
    return {"configured": _scheduler is not None, "running": running, "duplicated": False}
