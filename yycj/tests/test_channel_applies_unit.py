from datetime import datetime, timezone

from bot.services.channel_features import applies_at


def test_directory_applies_at_noon_beijing():
    assert applies_at(datetime(2026, 11, 1, 3, 59, tzinfo=timezone.utc)) == "今天 12:00"   # 11:59 北京
    assert applies_at(datetime(2026, 11, 1, 4, 0, tzinfo=timezone.utc)) == "明天 12:00"    # 12:00 北京
    assert applies_at(datetime(2026, 10, 31, 17, 0, tzinfo=timezone.utc)) == "今天 12:00"  # 01:00 北京
