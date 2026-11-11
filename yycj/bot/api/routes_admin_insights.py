"""后台：数据看板（按城市每日）、即将到期、评价批量审核。All additive, read-mostly."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, Field

from bot.api.deps import get_admin_user_id
from bot.api.routes_core import router
from bot.models import ReviewStatus
from bot.services import city_scope, home_service, listing_stats


@router.get("/admin/stats/daily")
async def api_admin_daily_stats(
    days: int = Query(default=14, ge=1, le=90),
    city: Optional[str] = None,
    admin_id: int = Depends(get_admin_user_id),
) -> Dict[str, Any]:
    cities = await city_scope.effective(admin_id, city)
    data = await listing_stats.daily_dashboard(days=days, cities=cities)
    return {"ok": True, **data}


@router.get("/admin/listings/expiring")
async def api_admin_expiring(
    days: int = Query(default=7, ge=1, le=30),
    city: Optional[str] = None,
    admin_id: int = Depends(get_admin_user_id),
) -> Dict[str, Any]:
    cities = await city_scope.effective(admin_id, city)
    items = await listing_stats.expiring_listings(days=days, cities=cities)
    return {"ok": True, "items": items, "days": days}


class ReviewBulkBody(BaseModel):
    ids: List[str] = Field(min_length=1, max_length=100)
    action: Literal["approve", "reject"]


@router.post("/admin/reviews/bulk")
async def api_admin_reviews_bulk(body: ReviewBulkBody, admin_id: int = Depends(get_admin_user_id)) -> Dict[str, Any]:
    status = ReviewStatus.APPROVED.value if body.action == "approve" else ReviewStatus.REJECTED.value
    done: List[str] = []
    failed: Dict[str, str] = {}
    for rid in dict.fromkeys(str(x).strip() for x in body.ids if str(x).strip()):
        try:
            await city_scope.guard(admin_id, "review", rid)
            await home_service.set_review_status(rid, status)
            done.append(rid)
        except HTTPException as exc:
            failed[rid] = str(exc.detail)
        except LookupError:
            failed[rid] = "不存在"
        except RuntimeError as exc:
            failed[rid] = str(exc) or "已处理"
    if done:
        from bot.services import review_notify
        review_notify.sync_soon(done, status, admin_id)
    return {"ok": True, "action": body.action, "done": done, "failed": failed}
