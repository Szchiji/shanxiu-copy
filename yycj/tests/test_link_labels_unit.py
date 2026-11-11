"""频道帖链接文案：自定义 / 留空隐藏 / 分隔符用 Unicode NBSP，避免 Telegram iOS 把三个链接粘在一起。"""
import html
from datetime import datetime
from types import SimpleNamespace as NS

from bot.services.broadcast import (
    LINK_SEP_HTML, caption_html, footer_text, link_labels_from_site, links_html, start_url,
)
from bot.services.coupon_push import render


def test_sep_is_real_nbsp_not_named_entity():
    assert LINK_SEP_HTML == "\u00a0·\u00a0"
    assert "&nbsp;" not in LINK_SEP_HTML and "&amp;nbsp;" not in LINK_SEP_HTML


def test_links_html_default_uses_nbsp_sep():
    lid = "88618ee0-6c79-49d2-8fc3-9259b6a4f5eb"
    out = links_html("MoonBot", lid)
    assert LINK_SEP_HTML in out
    assert out.count(LINK_SEP_HTML) == 2
    assert out.count("<a ") == 3
    assert "查看详情" in out and "查看评价" in out and "写评价" in out
    # named HTML entity must not appear: Telegram would show it literally
    assert "&nbsp;" not in out and "&amp;nbsp;" not in out
    # ordinary space-dot-space must NOT be the only separator (iOS collapses it)
    assert " · ".join(["x", "y"]) not in out or LINK_SEP_HTML in out
    assert start_url("MoonBot", lid, "s") in out.replace("&amp;", "&") or "start=s_" in out


def test_links_html_custom_and_empty_hides():
    lid = "88618ee0-6c79-49d2-8fc3-9259b6a4f5eb"
    labels = link_labels_from_site({
        "link_label_detail": "详情",
        "link_label_reviews": "",
        "link_label_write": " 写评 ",
    })
    assert labels == [("s", "详情"), ("w", "写评")]
    out = links_html("MoonBot", lid, labels=labels)
    assert out.count("<a ") == 2 and "查看评价" not in out and "详情" in out
    assert LINK_SEP_HTML in out and "&nbsp;" not in out
    # all hidden
    assert links_html("MoonBot", lid, labels=link_labels_from_site({
        "link_label_detail": "", "link_label_reviews": "", "link_label_write": "",
    })) == ""


def test_caption_keeps_sep_visible():
    lid = "88618ee0-6c79-49d2-8fc3-9259b6a4f5eb"
    cap = caption_html("你好", links_html("B", lid))
    assert LINK_SEP_HTML in cap and "查看详情" in cap
    assert "&nbsp;" not in cap and "&amp;nbsp;" not in cap
    assert html.escape(LINK_SEP_HTML, quote=False) == LINK_SEP_HTML


def test_footer_text_matches_labels_and_hide_rules():
    assert footer_text(None) == LINK_SEP_HTML.join(["查看详情", "查看评价", "写评价"])
    hidden = footer_text({"link_label_detail": "详情", "link_label_reviews": "", "link_label_write": " 写评 "})
    assert hidden == LINK_SEP_HTML.join(["详情", "写评"])
    assert footer_text({"link_label_detail": "", "link_label_reviews": "", "link_label_write": ""}) == ""
    with_loc = footer_text(None, extra="地图")
    assert with_loc.endswith(LINK_SEP_HTML + "地图") and with_loc.startswith("查看详情")
    assert "&nbsp;" not in with_loc


def test_coupon_footer_uses_same_real_nbsp():
    item = NS(lamp_id="12345678-1234-1234-1234-1234567890ab", city="深圳", text="P100",
              quota=10, status="active", claimed=0, ends_at=datetime(2026, 11, 3, 4, 0))
    t = render(item, NS(title="小月", district="南山"), "MoonBot")
    assert LINK_SEP_HTML in t and "&nbsp;" not in t
    assert t.count(LINK_SEP_HTML) == 1  # 查看详情 · 领取优惠


def test_allow_blank_override_cleaner():
    from bot.services.home_feed import clean_override_value, merge_ops
    assert clean_override_value("link_label_detail", "") == ""
    assert clean_override_value("link_label_detail", "详情") == "详情"
    assert clean_override_value("broadcast_channel", "") is None  # empty still clears non-blank keys
    m = merge_ops({"link_label_detail": "", "link_label_reviews": "评", "teacher_show_views": False})
    assert m["link_label_detail"] == "" and m["link_label_reviews"] == "评"
    assert m["teacher_show_views"] is False and m["teacher_show_favs"] is True
