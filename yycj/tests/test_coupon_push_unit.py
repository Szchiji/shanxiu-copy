from datetime import datetime, timedelta
from types import SimpleNamespace as NS

from bot.services.coupon_push import render
from bot.services.coupons import code_valid

LID = "12345678-1234-1234-1234-1234567890ab"


def _c(**kw):
    d = dict(lamp_id=LID, city="深圳", text="P100 <PP200> & 茶", quota=10, status="active",
             ends_at=datetime(2026, 11, 3, 4, 0))
    d.update(kw)
    return NS(**d)


def test_render_open_escaped_with_text_links():
    t = render(_c(), NS(title="<i>小月</i>", district="南山"), "MoonBot")
    assert "&lt;i&gt;小月&lt;/i&gt;" in t and "P100 &lt;PP200&gt; &amp; 茶" in t
    assert "深圳 · 南山" in t and "11月3日 12:00（北京时间）" in t and "限10张" in t
    hexid = LID.replace("-", "")
    assert f'href="https://t.me/MoonBot?start=s_{hexid}">查看详情</a>' in t
    assert f'href="https://t.me/MoonBot?start=q_{hexid}">领取优惠</a>' in t


def test_render_closed_has_no_links():
    t = render(_c(quota=None), NS(title="小月", district=""), "MoonBot", closed="已结束")
    assert "已结束" in t and "<s>" in t and "start=" not in t and "<a " not in t and "张" not in t


def test_code_valid_until_coupon_end_not_claim_time():
    now = datetime(2026, 11, 2)
    cl = NS(used_at=None)
    assert code_valid(_c(status="ended"), cl, now)          # teacher ended early → still valid
    assert code_valid(_c(status="sold_out"), cl, now)
    assert not code_valid(_c(status="stopped"), cl, now)    # admin stop voids
    assert not code_valid(_c(ends_at=now - timedelta(seconds=1)), cl, now)
    assert not code_valid(_c(), NS(used_at=now), now)


def test_render_batch_lists_items_and_marks_sold_out():
    items = [_c(text="100P", quota=2, claimed=2), _c(text="200<PP>", quota=3, claimed=1), _c(text="500夜课", quota=4, claimed=0)]
    t = render(items, NS(title="阿紫", district="福田"), "MoonBot")
    assert "💰 <s>100P</s> · 已领完" in t and "💰 200&lt;PP&gt; · 限3张" in t and "💰 500夜课 · 限4张" in t
    assert t.count("⏳") == 1 and "start=q_" in t
    t = render(items, NS(title="阿紫", district="福田"), "", closed="已结束")
    assert "<s>100P</s>" in t and "<s>500夜课</s>" in t and "<a " not in t and "张" not in t


def test_clean_items_rules():
    from bot.services.coupons import CouponError, MAX_ITEMS, clean_items
    assert clean_items([{"text": " 100P ", "quota": "2"}]) == [{"text": "100P", "quota": 2}]
    for bad in ([], [{"text": "a", "quota": 0}], [{"text": "a", "quota": 1}, {"text": "A", "quota": 1}],
                [{"text": f"x{i}", "quota": 1} for i in range(MAX_ITEMS + 1)], [{"text": "t.me/x", "quota": 1}]):
        try:
            clean_items(bad)
            raise AssertionError(bad)
        except CouponError:
            pass
    assert clean_items([{"text": "老的", "quota": 0}], legacy=True) == [{"text": "老的", "quota": 0}]
