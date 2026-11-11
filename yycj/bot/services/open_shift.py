"""营业时间 + 开课提醒（Asia/Shanghai）。

状态只看三样：营业时间 _hours、当班日的选择 _open_date/_open_today、自动营业 _auto_open。
- 跨夜班（如 20:00–次日04:00）凌晨那段仍算前一天那一班，不会在 0 点被重置。
- 提醒在每位老师当天第一段开工前 REMIND_LEAD_MIN 分钟发；没回复 = 只当天按休息，提醒照常。
- 「今日休息」只管当天；只有老师自己关掉「每日提醒」才不再提醒。
"""
from __future__ import annotations

from html import escape as _h

import logging
import re
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from bot.db import session_scope
from bot.models import Lamp, LampStatus
from bot.services.extras_store import load_extras, load_extras_bulk, save_extras

logger = logging.getLogger(__name__)
TZ = ZoneInfo("Asia/Shanghai")
REMIND_LEAD_MIN = 30   # 开工前多久提醒
REMIND_LATE_MIN = 30   # 任务晚跑（重启）时，开工后这么久内仍补发
TIMEOUT_MIN = 180      # 发出后多久没回复算「今日休息」
MAX_RANGES = 3

_FW = str.maketrans("０１２３４５６７８９：～－", "0123456789:~-")
_RANGE_SEP = re.compile(r"\s*(?:-|–|—|―|~|〜|至|到)\s*")
_LIST_SEP = re.compile(r"[,，、;；/\n]+|\s{1,}")
_TIME = re.compile(r"^(?:次日|翌日|凌晨)?(\d{1,2})(?:[:.点时](\d{1,2})?)?(?:分)?(半)?$")


def now_sh() -> datetime:
    return datetime.now(TZ)


def today_str(now: Optional[datetime] = None) -> str:
    return (now or now_sh()).astimezone(TZ).strftime("%Y-%m-%d")


def _to_min(raw: Any) -> Optional[int]:
    s = str(raw or "").strip().translate(_FW).replace(" ", "")
    if not s:
        return None
    m = _TIME.match(s)
    if not m:
        return None
    h = int(m.group(1))
    mi = int(m.group(2) or 0)
    if m.group(3):
        mi = 30
    if h == 24 and mi == 0:
        return 24 * 60
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    return h * 60 + mi


def _fmt(m: int) -> str:
    return f"{m // 60:02d}:{m % 60:02d}"


def _pair(a: Any, b: Any) -> Optional[Tuple[str, str]]:
    sa, sb = _to_min(a), _to_min(b)
    if sa is None or sb is None or sa == 24 * 60 or sa == sb:
        return None
    return _fmt(sa), _fmt(sb)


def parse_hours(raw: Any) -> List[Tuple[str, str]]:
    """Lenient parse → canonical [("HH:MM","HH:MM"), ...]. Accepts dict/list/str forms."""
    out: List[Tuple[str, str]] = []
    items: List[Any]
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        items = [raw]
    else:
        text = str(raw or "").strip().translate(_FW)
        if not text:
            return []
        text = _RANGE_SEP.sub("-", text)
        items = [x for x in _LIST_SEP.split(text) if x]
    for it in items:
        p = None
        if isinstance(it, dict):
            p = _pair(it.get("start"), it.get("end"))
        elif isinstance(it, (list, tuple)) and len(it) == 2:
            p = _pair(it[0], it[1])
        elif isinstance(it, str):
            s = _RANGE_SEP.sub("-", it.strip().translate(_FW))
            if s.count("-") == 1:
                a, b = s.split("-", 1)
                p = _pair(a, b)
        if p and p not in out:
            out.append(p)
        if len(out) >= MAX_RANGES:
            break
    return out


def normalize_hours(raw: Any) -> List[Dict[str, str]]:
    return [{"start": a, "end": b} for a, b in parse_hours(raw)]


def _mins(hours: List[Tuple[str, str]]) -> List[Tuple[int, int]]:
    out = []
    for a, b in hours:
        sa, sb = _to_min(a), _to_min(b)
        if sa is None or sb is None:
            continue
        out.append((sa, sb))
    return out


def _overnight(sa: int, sb: int) -> bool:
    return sb <= sa


def hours_label(hours: List[Tuple[str, str]]) -> str:
    parts = []
    for sa, sb in _mins(hours):
        parts.append(f"{_fmt(sa)}–次日{_fmt(sb)}" if _overnight(sa, sb) else f"{_fmt(sa)}–{_fmt(sb)}")
    return "、".join(parts)


def _at(day: date, minute: int) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=TZ) + timedelta(minutes=minute)


def shifts_on(hours: List[Tuple[str, str]], day: date) -> List[Tuple[datetime, datetime]]:
    """Shifts that START on `day`, as aware datetimes (overnight ends next day)."""
    out = []
    for sa, sb in _mins(hours):
        start = _at(day, sa)
        end = _at(day + timedelta(days=1), sb) if _overnight(sa, sb) else _at(day, sb)
        out.append((start, end))
    return sorted(out)


def current_shift(hours, now: Optional[datetime] = None) -> Optional[Tuple[date, datetime, datetime]]:
    now = (now or now_sh()).astimezone(TZ)
    today = now.date()
    for d in (today - timedelta(days=1), today):
        for start, end in shifts_on(hours, d):
            if start <= now < end:
                return d, start, end
    return None


def next_shift(hours, now: Optional[datetime] = None) -> Optional[Tuple[date, datetime, datetime]]:
    """Current shift if inside one, else the next one to start (today or tomorrow)."""
    now = (now or now_sh()).astimezone(TZ)
    cur = current_shift(hours, now)
    if cur:
        return cur
    today = now.date()
    for d in (today, today + timedelta(days=1)):
        for start, end in shifts_on(hours, d):
            if start > now:
                return d, start, end
    return None


def in_hours(hours: List[Tuple[str, str]], when: Optional[datetime] = None) -> bool:
    return current_shift(hours, when) is not None


def _day_word(d: date, now: datetime) -> str:
    t = now.astimezone(TZ).date()
    if d == t:
        return "今天"
    if d == t + timedelta(days=1):
        return "明天"
    if d == t - timedelta(days=1):
        return "昨天"
    return d.strftime("%m-%d")


def reminders_on(extras: Dict[str, Any]) -> bool:
    """Explicit switch (_remind_off) wins; otherwise a legacy _remind_on="0" (old logic) stays off;
    no stored value → on."""
    ex = extras or {}
    off = ex.get("_remind_off")
    if off in ("0", "1"):
        return off == "0"
    return ex.get("_remind_on") != "0"


def auto_open(extras: Dict[str, Any]) -> bool:
    """按营业时间自动营业: default ON unless the teacher explicitly turned it off."""
    return (extras or {}).get("_auto_open") != "0"


def shop_status(
    extras: Dict[str, Any] | None,
    status: str | None = None,
    expires_at: Any = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    extras = extras or {}
    now = (now or now_sh()).astimezone(TZ)
    hours = parse_hours(extras.get("_hours") or extras.get("hours"))
    label = hours_label(hours)
    remind_on = reminders_on(extras)
    auto = auto_open(extras)
    base = {"hours": label, "remind_on": remind_on, "auto": auto}
    if status and status != LampStatus.ACTIVE.value:
        return {**base, "code": "off", "text": ""}
    if expires_at:
        exp = expires_at
        if getattr(exp, "tzinfo", None):
            exp = exp.replace(tzinfo=None)
        if isinstance(exp, datetime) and exp <= datetime.utcnow():
            return {**base, "code": "off", "text": ""}
    if extras.get("_lasting_rest") == "1":
        return {**base, "code": "rest", "text": "休息中", "lasting": True}
    if not hours:
        return {**base, "code": "unset", "text": "", "hours": ""}

    def choice_for(d: date) -> Optional[str]:
        if extras.get("_open_date") != d.isoformat():
            return None
        return "open" if extras.get("_open_today") == "1" else "rest"

    nxt = next_shift(hours, now)
    if nxt:
        base["shift_day"] = nxt[0].isoformat()
        base["shift_word"] = _day_word(nxt[0], now)
        base["shift_start"] = nxt[1].strftime("%H:%M")
        base["choice"] = choice_for(nxt[0])
        base["missed"] = extras.get("_remind_missed") == nxt[0].isoformat()
    cur = current_shift(hours, now)
    if cur:
        ch = choice_for(cur[0])
        if ch == "open" or (auto and ch != "rest"):
            return {**base, "code": "open", "text": "营业中"}
        return {**base, "code": "rest", "text": "休息中"}
    today = now.date()
    if choice_for(today) == "rest":
        return {**base, "code": "rest", "text": "休息中"}
    later_today = any(start > now for start, _ in shifts_on(hours, today))
    if later_today:
        return {**base, "code": "wait", "text": "未到点"}
    return {**base, "code": "closed", "text": "已打烊"}


def _kb(lamp_id: str, day: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="今日开课", callback_data=f"shift:open:{lamp_id}:{day}"),
                InlineKeyboardButton(text="今日休息", callback_data=f"shift:rest:{lamp_id}:{day}"),
            ]
        ]
    )


def remind_text(title: str, hours, day: date, start: datetime, now: Optional[datetime] = None) -> str:
    now = now or now_sh()
    return (
        f"「{_h(str(title or ''))}」{_day_word(day, now)} {start.strftime('%H:%M')} 开课吗？\n"
        f"营业时间 {hours_label(hours)}\n"
        f"点「今日开课」首页到点显示营业中；不回复按今日休息，明天照常提醒。"
    )


_ACTIONS = {"open", "rest", "remind", "remind_on", "remind_off", "auto_on", "auto_off", "pause", "resume"}


async def apply_choice(
    lamp_id: str, user_id: int, action: str, day: Optional[str] = None, now: Optional[datetime] = None
) -> str:
    now = (now or now_sh()).astimezone(TZ)
    if action not in _ACTIONS:
        return "不支持的操作"
    async with session_scope() as s:
        row = (await s.execute(select(Lamp).where(Lamp.lamp_id == lamp_id))).scalar_one_or_none()
        if not row:
            return "找不到资料"
        if int(row.user_id) != int(user_id):
            return "这不是你的资料"
        if row.status != LampStatus.ACTIVE.value:
            return "资料未上架"
        if row.expires_at and row.expires_at <= datetime.utcnow():
            return "资料已到期"
        title = row.title
    extras = await load_extras(lamp_id)
    if action == "pause":
        extras["_lasting_rest"] = "1"
        await save_extras(lamp_id, extras)
        return f"已记：{title} · 持续休息，直到你点「恢复营业」。首页仍展示，显示休息。"
    if action == "resume":
        await save_extras(lamp_id, extras, drop=("_lasting_rest",))
        return f"已记：{title} · 已恢复营业，按现在的营业时间显示"
    if action in ("open", "rest") and extras.get("_lasting_rest") == "1":
        if action == "rest":
            return f"已在持续休息中：{title} · 若只想歇今天，请先点「恢复营业」再设今日休息"
        await save_extras(lamp_id, extras, drop=("_lasting_rest",))
        extras = await load_extras(lamp_id)
    if action in ("remind", "remind_on"):
        extras["_remind_off"] = "0"
        await save_extras(lamp_id, extras)
        return f"已开启：{title} · 每日开工前 {REMIND_LEAD_MIN} 分钟提醒"
    if action == "remind_off":
        extras["_remind_off"] = "1"
        await save_extras(lamp_id, extras)
        return f"已关闭：{title} · 不再每日提醒（可随时在「我的」打开）"
    if action in ("auto_on", "auto_off"):
        extras["_auto_open"] = "1" if action == "auto_on" else "0"
        await save_extras(lamp_id, extras)
        if action == "auto_on":
            return f"已开启：{title} · 按营业时间自动营业，不用每天确认"
        return f"已关闭自动营业：{title} · 改回每天确认"
    hours = parse_hours(extras.get("_hours") or extras.get("hours"))
    if not hours:
        return "请先在「改资料」里设置营业时间"
    nxt = next_shift(hours, now)
    target = nxt[0] if nxt else now.date()
    if day:
        try:
            asked = date.fromisoformat(str(day))
        except ValueError:
            return "这条提醒已过期"
        # Accept the shift the prompt was for while it is still current or upcoming.
        if asked < target or asked > now.date() + timedelta(days=1):
            return "这条提醒已过期"
        target = asked
    extras["_open_date"] = target.isoformat()
    extras["_open_today"] = "1" if action == "open" else "0"
    if extras.get("_remind_sent_date") == target.isoformat():
        extras["_remind_pending"] = "0"
    drop = ("_remind_missed",) if extras.get("_remind_missed") == target.isoformat() else ()
    await save_extras(lamp_id, extras, drop=drop)
    word = _day_word(target, now)
    if action == "open":
        return f"已记：{title} · {word}开课，到点首页显示营业中"
    tail = "，明天照常提醒" if reminders_on(extras) and not auto_open(extras) else ""
    return f"已记：{title} · {word}休息{tail}"


async def resume_remind(lamp_id: str, user_id: int) -> str:
    return await apply_choice(lamp_id, user_id, "remind_on")


def due_prompt(extras: Dict[str, Any], now: datetime) -> Optional[Tuple[date, datetime]]:
    """(day, first_start) if this lamp should get its prompt now, else None."""
    hours = parse_hours(extras.get("_hours") or extras.get("hours"))
    if not hours or auto_open(extras) or not reminders_on(extras) or extras.get("_lasting_rest") == "1":
        return None
    today = now.astimezone(TZ).date()
    for d in (today, today + timedelta(days=1)):
        shifts = shifts_on(hours, d)
        if not shifts:
            continue
        first = shifts[0][0]
        if not (first - timedelta(minutes=REMIND_LEAD_MIN) <= now < first + timedelta(minutes=REMIND_LATE_MIN)):
            continue
        ds = d.isoformat()
        if extras.get("_remind_sent_date") == ds or extras.get("_open_date") == ds:
            continue
        return d, first
    return None


async def _live_lamps() -> List[Dict[str, Any]]:
    async with session_scope() as s:
        rows = (await s.execute(select(Lamp).where(Lamp.status == LampStatus.ACTIVE.value))).scalars().all()
        lamps = [
            {"lamp_id": r.lamp_id, "user_id": r.user_id, "title": r.title, "expires_at": r.expires_at}
            for r in rows
        ]
    now_utc = datetime.utcnow()
    return [x for x in lamps if not (x.get("expires_at") and x["expires_at"] <= now_utc)]


async def send_due(now: Optional[datetime] = None, bot=None) -> int:
    now = (now or now_sh()).astimezone(TZ)
    if bot is None:
        try:
            from bot.main import bot  # type: ignore[no-redef]
        except Exception:
            return 0
    live = await _live_lamps()
    all_extras = await load_extras_bulk([x["lamp_id"] for x in live])
    sent = 0
    for lamp in live:
        extras = dict(all_extras.get(lamp["lamp_id"]) or {})
        due = due_prompt(extras, now)
        if not due:
            continue
        day, first = due
        hours = parse_hours(extras.get("_hours") or extras.get("hours"))
        extras["_remind_pending"] = "1"
        extras["_remind_sent_at"] = now.isoformat()
        extras["_remind_sent_date"] = day.isoformat()
        await save_extras(lamp["lamp_id"], extras)
        try:
            await bot.send_message(
                int(lamp["user_id"]),
                remind_text(lamp["title"], hours, day, first, now),
                reply_markup=_kb(lamp["lamp_id"], day.isoformat()),
            )
            sent += 1
        except Exception:
            logger.exception("shift remind failed lamp=%s", lamp["lamp_id"])
    return sent


send_daily = send_due  # old job name


async def expire_unanswered(now: Optional[datetime] = None) -> int:
    """No reply in time → that day only counts as rest. Reminders stay on."""
    now = (now or now_sh()).astimezone(TZ)
    cutoff = now - timedelta(minutes=TIMEOUT_MIN)
    n = 0
    async with session_scope() as s:
        rows = (await s.execute(select(Lamp).where(Lamp.status == LampStatus.ACTIVE.value))).scalars().all()
        ids = [r.lamp_id for r in rows]
    all_extras = await load_extras_bulk(ids)
    for lid in ids:
        extras = dict(all_extras.get(lid) or {})
        if extras.get("_lasting_rest") == "1":
            if extras.get("_remind_pending") == "1":
                extras["_remind_pending"] = "0"
                await save_extras(lid, extras)
            continue
        if extras.get("_remind_pending") != "1":
            continue
        try:
            sent_at = datetime.fromisoformat(str(extras.get("_remind_sent_at") or ""))
            if sent_at.tzinfo is None:
                sent_at = sent_at.replace(tzinfo=TZ)
        except Exception:
            continue
        if sent_at > cutoff:
            continue
        day = str(extras.get("_remind_sent_date") or sent_at.astimezone(TZ).date().isoformat())
        extras["_remind_pending"] = "0"
        if extras.get("_open_date") != day:
            extras["_open_date"] = day
            extras["_open_today"] = "0"
            extras["_remind_missed"] = day
        await save_extras(lid, extras)
        n += 1
    return n
