"""营业时间解析 / 状态 / 提醒时机（纯函数，不需要数据库）。"""
from datetime import datetime, timedelta

import pytest

from bot.services import open_shift as o

TZ = o.TZ


def at(d, hm):
    h, m = [int(x) for x in hm.split(":")]
    return datetime(2026, 10, d, h, m, tzinfo=TZ)


@pytest.mark.parametrize("raw,want", [
    ("10-22", [("10:00", "22:00")]),
    ("10:00~22:00", [("10:00", "22:00")]),
    ("10：00—22：00", [("10:00", "22:00")]),
    ("20:00 – 次日04:00", [("20:00", "04:00")]),
    ("１０：３０～２２：００", [("10:30", "22:00")]),
    ("10点-22点半", [("10:00", "22:30")]),
    ("10:00-14:00、18:00-02:00", [("10:00", "14:00"), ("18:00", "02:00")]),
    ("12至24", [("12:00", "24:00")]),
    ([{"start": "9:00", "end": "21:00"}], [("09:00", "21:00")]),
    ({"start": "20:00", "end": "04:00"}, [("20:00", "04:00")]),
    ("全天", []), ("10-10", []), ("25-26", []), ("", []), (None, []),
])
def test_parse_lenient(raw, want):
    assert o.parse_hours(raw) == want


def test_normalize_caps_and_canonical():
    raw = "1-2 3-4 5-6 7-8"
    assert o.normalize_hours(raw) == [{"start": "01:00", "end": "02:00"}, {"start": "03:00", "end": "04:00"},
                                      {"start": "05:00", "end": "06:00"}]


def test_label_next_day_only_when_overnight():
    # old bug: string compare made "9:00-21:00" look overnight
    assert o.hours_label(o.parse_hours("9:00-21:00")) == "09:00–21:00"
    assert o.hours_label(o.parse_hours("9-10")) == "09:00–10:00"
    assert o.hours_label(o.parse_hours("20-4")) == "20:00–次日04:00"
    assert o.hours_label(o.parse_hours("12-24")) == "12:00–24:00"


H_NIGHT = [{"start": "20:00", "end": "04:00"}]
H_DAY = [{"start": "10:00", "end": "22:00"}]


def st(extras, now):
    return o.shop_status(extras, "active", None, now=now)


def test_status_day_shift():
    ex = {"_hours": H_DAY, "_open_date": "2026-10-30", "_open_today": "1"}
    assert st(ex, at(30, "09:00"))["code"] == "wait"
    assert st(ex, at(30, "12:00"))["code"] == "open"
    c = st(ex, at(30, "22:30"))
    assert c["code"] == "closed" and c["text"] == "已打烊"
    # not confirmed during hours → 休息中; before hours → 未到点
    ex2 = {"_hours": H_DAY, "_auto_open": "0"}
    assert st(ex2, at(30, "09:00"))["code"] == "wait"
    assert st(ex2, at(30, "12:00"))["code"] == "rest"
    # 今日休息 → 休息中 all day, next day back to normal
    ex3 = {"_hours": H_DAY, "_open_date": "2026-10-30", "_open_today": "0"}
    assert st(ex3, at(30, "09:00"))["text"] == "休息中"
    assert st(ex3, at(31, "09:00"))["code"] == "wait"


def test_overnight_shift_survives_midnight():
    ex = {"_hours": H_NIGHT, "_open_date": "2026-10-30", "_open_today": "1"}
    assert st(ex, at(30, "21:00"))["code"] == "open"
    assert st(ex, at(31, "01:30"))["code"] == "open"     # still last night's shift
    assert st(ex, at(31, "05:00"))["code"] == "wait"     # tonight 20:00 next
    assert st({**ex, "_auto_open": "0"}, at(31, "20:30"))["code"] == "rest"  # new shift, not confirmed


def test_auto_mode():
    # default ON: nothing stored → auto; explicit "0" → manual
    assert st({"_hours": H_DAY}, at(30, "12:00"))["code"] == "open"
    assert st({"_hours": H_DAY}, at(30, "12:00"))["auto"] is True
    assert st({"_hours": H_DAY, "_auto_open": "0"}, at(30, "12:00"))["auto"] is False
    ex = {"_hours": H_DAY, "_auto_open": "1"}
    assert st(ex, at(30, "12:00"))["code"] == "open"
    ex["_open_date"], ex["_open_today"] = "2026-10-30", "0"
    assert st(ex, at(30, "12:00"))["code"] == "rest"      # 今日休息 beats auto, today only
    assert st(ex, at(31, "12:00"))["code"] == "open"


def test_unset_and_off():
    assert o.shop_status({}, "active")["code"] == "unset"
    assert o.shop_status({"_hours": H_DAY}, "hidden")["code"] == "off"
    assert o.shop_status({"_hours": H_DAY}, "active", datetime.utcnow() - timedelta(hours=1))["code"] == "off"


def test_next_shift_target():
    h = o.parse_hours(H_NIGHT)
    assert o.next_shift(h, at(31, "01:00"))[0].isoformat() == "2026-10-30"
    assert o.next_shift(h, at(31, "05:00"))[0].isoformat() == "2026-10-31"
    h2 = o.parse_hours(H_DAY)
    assert o.next_shift(h2, at(30, "23:00"))[0].isoformat() == "2026-10-31"


def test_due_prompt_window():
    assert o.REMIND_LEAD_MIN == 30 and o.TIMEOUT_MIN == 180
    ex = {"_hours": H_NIGHT, "_auto_open": "0"}
    assert o.due_prompt(ex, at(30, "19:29")) is None
    d = o.due_prompt(ex, at(30, "19:30"))
    assert d and d[0].isoformat() == "2026-10-30" and d[1].strftime("%H:%M") == "20:00"
    assert o.due_prompt(ex, at(30, "20:25"))              # late job still sends
    assert o.due_prompt(ex, at(30, "20:31")) is None
    assert o.due_prompt({**ex, "_remind_sent_date": "2026-10-30"}, at(30, "19:40")) is None
    assert o.due_prompt({**ex, "_open_date": "2026-10-30"}, at(30, "19:40")) is None  # already decided
    assert o.due_prompt({**ex, "_remind_off": "1"}, at(30, "19:40")) is None
    assert o.due_prompt({"_hours": H_NIGHT}, at(30, "19:40")) is None           # auto is the default
    assert o.due_prompt({**ex, "_auto_open": "1"}, at(30, "19:40")) is None
    # legacy reminders switched off by the old logic stay off; explicit switch wins
    assert o.due_prompt({**ex, "_remind_on": "0"}, at(30, "19:40")) is None
    assert o.due_prompt({**ex, "_remind_on": "0", "_remind_off": "0"}, at(30, "19:40"))
    assert o.due_prompt({**ex, "_remind_on": "1"}, at(30, "19:40"))
    # rest yesterday does not stop today's prompt
    assert o.due_prompt({**ex, "_open_date": "2026-10-29", "_open_today": "0"}, at(30, "19:40"))


def test_due_prompt_after_midnight_start_and_first_range_only():
    ex = {"_hours": [{"start": "00:30", "end": "06:00"}], "_auto_open": "0"}
    assert o.due_prompt(ex, at(30, "23:55")) is None
    d = o.due_prompt(ex, at(31, "00:05"))
    assert d and d[0].isoformat() == "2026-10-31"
    ex = {"_hours": [{"start": "00:10", "end": "06:00"}], "_auto_open": "0"}
    d = o.due_prompt(ex, at(30, "23:45"))                 # window crosses midnight → tomorrow's shift
    assert d and d[0].isoformat() == "2026-10-31"
    two = {"_hours": "10:00-14:00 18:00-23:00", "_auto_open": "0"}
    assert o.due_prompt(two, at(30, "09:40"))
    assert o.due_prompt(two, at(30, "17:40")) is None     # one prompt per day


def test_remind_text():
    h = o.parse_hours(H_NIGHT)
    day = at(30, "19:00").date()
    txt = o.remind_text("小雅<b>", h, day, at(30, "20:00"), now=at(30, "19:00"))
    assert "今天 20:00 开课吗" in txt and "20:00–次日04:00" in txt and "&lt;b&gt;" in txt


def test_lasting_rest_beats_auto_and_hours():
    ex = {"_hours": H_DAY, "_lasting_rest": "1"}
    s = st(ex, at(30, "12:00"))
    assert s["code"] == "rest" and s["text"] == "休息中" and s.get("lasting") is True
    # still shows hours; auto flag remains, but status is rest
    assert "10:00" in (s.get("hours") or "")
    # next day still rest until manually cleared
    assert st(ex, at(31, "12:00"))["code"] == "rest"
    # hidden / expired still off
    assert o.shop_status(ex, "hidden")["code"] == "off"


def test_due_prompt_skips_lasting_rest():
    ex = {"_hours": H_NIGHT, "_auto_open": "0", "_lasting_rest": "1"}
    assert o.due_prompt(ex, at(30, "19:30")) is None
