"""Pure helpers for channel link-label sync (no database)."""
from bot.services.label_sync import classify_edit_error, retry_after_sec, cities_whose_labels_changed, effective_labels


class _E(Exception):
    def __init__(self, msg, retry_after=None):
        super().__init__(msg)
        if retry_after is not None:
            self.retry_after = retry_after


def test_classify_edit_error():
    assert classify_edit_error(_E("Bad Request: message is not modified")) == "updated"
    assert classify_edit_error(_E("message to edit not found")) == "missing"
    assert classify_edit_error(_E("Message to edit not found")) == "missing"
    assert classify_edit_error(_E("chat not found")) == "gone"
    assert classify_edit_error(_E("Forbidden: bot was kicked from the channel")) == "gone"
    assert classify_edit_error(_E("Flood control exceeded. Retry after 12")) == "retry"
    assert classify_edit_error(_E("timed out")) == "retry"
    assert classify_edit_error(_E("Bad Gateway")) == "retry"
    assert classify_edit_error(_E("mystery")) == "retry"


def test_retry_after_sec():
    assert retry_after_sec(_E("x", retry_after=12)) == 12
    assert retry_after_sec(_E("Flood: retry after 20")) == 20
    assert retry_after_sec(_E("nope")) == 0


def _site(**kw):
    out = {
        "enabled_cities": ["上海", "深圳"],
        "link_label_detail": "查看详情",
        "link_label_reviews": "查看评价",
        "link_label_write": "写评价",
        "city_overrides": {},
    }
    out.update(kw)
    return out


def test_global_change_skips_city_with_own_labels():
    before = _site()
    after = _site(link_label_detail="详情")
    assert cities_whose_labels_changed(before, after) == ["上海", "深圳"]
    after["city_overrides"] = {"上海": {"link_label_detail": "查看详情", "link_label_reviews": "查看评价", "link_label_write": "写评价"}}
    # 上海 override still equals the old global (and after global changed, 上海 keeps old labels)
    # effective 上海 stays 查看详情, 深圳 becomes 详情
    assert cities_whose_labels_changed(before, after) == ["深圳"]


def test_city_save_only_schedules_that_city_when_labels_change():
    before = _site()
    after = _site(city_overrides={"上海": {"link_label_write": "去评价"}})
    assert cities_whose_labels_changed(before, after, city="上海") == ["上海"]
    assert cities_whose_labels_changed(before, after, city="深圳") == []
    # directory-only change
    same = _site(city_overrides={"上海": {"channel_directory": True}})
    assert cities_whose_labels_changed(before, same, city="上海") == []


def test_blank_label_is_a_real_change():
    before = _site()
    after = _site(link_label_reviews="")
    assert effective_labels(after, "上海")[1] == ""
    assert cities_whose_labels_changed(before, after) == ["上海", "深圳"]
