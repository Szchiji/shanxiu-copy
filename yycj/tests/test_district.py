"""首页地区筛选：区名归并。"""
import pytest

from bot.services.search_service import district_key, district_variants


@pytest.mark.parametrize("raw,want", [
    ("静安区", "静安"), ("静安", "静安"), (" 南山 区 ", "南山"), ("浦东新区", "浦东新区"),
    ("东区", "东区"), ("", ""), (None, ""),
])
def test_district_key(raw, want):
    assert district_key(raw) == want


def test_variants():
    assert district_variants("静安区") == ["静安", "静安区"]
    assert district_variants("") == []
