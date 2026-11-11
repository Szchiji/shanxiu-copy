"""老师「我的上架」三项统计开关：默认全开；关则前端不渲染对应格（管理台看板不受影响）。"""
from bot.services.home_feed import merge_ops


def _cells(show):
    parts = []
    if show.get("views", True):
        parts.append("浏览")
    if show.get("favs", True):
        parts.append("收藏")
    if show.get("contacts", True):
        parts.append("匿名联系")
    return parts


def test_teacher_stats_defaults_on():
    m = merge_ops({})
    show = {
        "views": bool(m.get("teacher_show_views", True)),
        "favs": bool(m.get("teacher_show_favs", True)),
        "contacts": bool(m.get("teacher_show_contacts", True)),
    }
    assert _cells(show) == ["浏览", "收藏", "匿名联系"]


def test_teacher_stats_hide_some():
    m = merge_ops({"teacher_show_views": False, "teacher_show_contacts": False})
    show = {
        "views": bool(m.get("teacher_show_views", True)),
        "favs": bool(m.get("teacher_show_favs", True)),
        "contacts": bool(m.get("teacher_show_contacts", True)),
    }
    assert _cells(show) == ["收藏"]
    assert _cells({"views": False, "favs": False, "contacts": False}) == []
