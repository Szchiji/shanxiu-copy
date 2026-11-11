from bot.api.routes_core_home import entry_announcement
from bot.services.home_feed import merge_ops, ops_for_city, clean_city_overrides
from bot.api.routes_admin_ops import ExtraOpsBody


def test_entry_popup_disabled_or_blank_is_not_returned():
    base = merge_ops({"popup_announcement_text": "有内容但未开启"})
    assert entry_announcement(base, base, "深圳", 42) is None
    base = merge_ops({"popup_announcement_enabled": True, "popup_announcement_text": "  "})
    assert entry_announcement(base, base, "深圳", 42) is None


def test_popup_scope_inherits_globally_and_false_city_override_disables():
    base = merge_ops({"popup_announcement_enabled": True, "popup_announcement_text": "全站弹窗",
                      "city_overrides": {"深圳": {"popup_announcement_enabled": False},
                                         "广州": {"popup_announcement_text": "广州弹窗"}}})
    assert entry_announcement(base, ops_for_city(base, "深圳"), "深圳", 42) is None
    local = entry_announcement(base, ops_for_city(base, "广州"), "广州", 42)
    assert local == {"title": "本城公告", "text": "广州弹窗", "scope_user": "42", "scope_city": "广州"}
    inherited = entry_announcement(base, ops_for_city(base, "杭州"), "杭州", 42)
    assert inherited["text"] == "全站弹窗" and inherited["scope_city"] == "*"


def test_popup_sanitization_and_blank_inheritance_do_not_change_announcements_or_labels():
    base = merge_ops({"popup_announcement_enabled": True, "popup_announcement_text": "x" * 4000,
                      "popup_announcement_title": "x" * 80, "announcement_text": "普通公告",
                      "city_overrides": {"深圳": {"popup_announcement_text": "", "link_label_detail": ""}}})
    assert len(base["popup_announcement_text"]) == 3000 and len(base["popup_announcement_title"]) == 40
    city = ops_for_city(base, "深圳")
    assert len(city["popup_announcement_text"]) == 3000
    assert city["link_label_detail"] == ""  # labels still hide, unlike empty city popup text
    assert not clean_city_overrides({"深圳": {"popup_announcement_text": ""}})
    body = ExtraOpsBody(popup_announcement_enabled=True, popup_announcement_text="独立弹窗")
    assert body.model_dump(exclude_none=True)["popup_announcement_text"] == "独立弹窗"
