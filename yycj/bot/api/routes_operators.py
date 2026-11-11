"""超管可在后台加减运营管理员。"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import Depends, HTTPException
from pydantic import BaseModel

from bot.api.deps import get_admin_user_id
from bot.api.routes_core import router
from bot.config import get_settings
from bot.services import city_scope
from bot.services.admin_acl import extra_ids, is_super, save, warm

logger = logging.getLogger(__name__)


class OperatorsBody(BaseModel):
    user_ids: List[int] = []


@router.get("/admin/operators")
async def api_list_operators(admin_id: int = Depends(get_admin_user_id)) -> Dict[str, Any]:
    await warm()
    return {
        "ok": True,
        "is_super": is_super(admin_id),
        "super_ids": get_settings().admin_id_list,
        "extra_admin_ids": sorted(extra_ids()),
        "operator_cities": await city_scope.operator_cities(),
    }


@router.post("/admin/operators")
async def api_save_operators(
    body: OperatorsBody,
    admin_id: int = Depends(get_admin_user_id),
) -> Dict[str, Any]:
    if not is_super(admin_id):
        raise HTTPException(status_code=403, detail="只有环境变量 ADMIN_IDS 里的超管能改运营名单")
    before = extra_ids()
    ids = await save(body.user_ids)
    added = [i for i in ids if i not in before]
    if added:
        try:
            from bot.main import bot
            from bot.services.bot_info import bot_tme_url, get_bot_identity
            ident = await get_bot_identity()
            link = bot_tme_url(ident.get("username") or "")
            text = "你已被设为月影车姬运营管理员。发 /admin 或开小程序「我的 → 管理后台」。"
            if link:
                text += "\n" + link
            for uid in added:
                try:
                    await bot.send_message(uid, text)
                except Exception:
                    logger.exception("notify operator %s failed", uid)
        except Exception:
            logger.exception("notify operators failed")
    return {"ok": True, "extra_admin_ids": ids, "notified": added}
