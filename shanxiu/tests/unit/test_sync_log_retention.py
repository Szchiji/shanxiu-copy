"""同步日志保留时长：按群设置、默认 1 天、永久保留、分批删除、后台页面与 API。"""

from datetime import timedelta

import pytest

from app.services import sync_log_retention as r


@pytest.fixture(autouse=True)
def _clean(flask_app, monkeypatch):
    from app import db
    from app.models import SyncMessageLog, SystemConfig
    monkeypatch.delenv('SYNC_LOG_RETENTION_HOURS', raising=False)
    with flask_app.app_context():
        SyncMessageLog.query.delete()
        SystemConfig.query.filter(SystemConfig.key_name.like(r.KEY_PREFIX + '%')).delete(synchronize_session=False)
        db.session.commit()
    yield


def _group(chat_id):
    from app import db
    from app.models import BotGroup
    g = BotGroup(chat_id=chat_id, title=chat_id, type='supergroup', is_active=True)
    db.session.add(g)
    db.session.commit()
    return g.id


def _log(gid, age_hours, now, mid=1):
    from app import db
    from app.models import SyncMessageLog
    db.session.add(SyncMessageLog(source_group_id=gid, target_group_id='-100900', source_message_id=mid,
                                  target_message_id=mid, status='success',
                                  synced_at=now - timedelta(hours=age_hours)))
    db.session.commit()


def test_normalize_and_default(monkeypatch):
    assert r.normalize_hours(0) == 0 and r.normalize_hours(-5) == 0
    assert r.normalize_hours('72') == 72
    assert r.normalize_hours(10 ** 9) == r.MAX_HOURS
    with pytest.raises(ValueError):
        r.normalize_hours('abc')
    assert r.default_hours() == 24
    monkeypatch.setenv('SYNC_LOG_RETENTION_HOURS', '48')
    assert r.default_hours() == 48
    assert r.describe_hours(0) == '永久保留' and r.describe_hours(72) == '3 天' and r.describe_hours(5) == '5 小时'


def test_cutoff_calendar_vs_rolling():
    from datetime import datetime
    now = datetime(2026, 10, 10, 16, 45, 0)
    assert r.cutoff_for(24, now) == datetime(2026, 10, 10, 0, 0, 0)
    assert r.cutoff_for(72, now) == datetime(2026, 10, 8, 0, 0, 0)
    assert r.cutoff_for(5, now) == datetime(2026, 10, 10, 11, 45, 0)
    assert r.cutoff_for(0, now) is None


def test_cleanup_default_one_day_and_group_overrides(flask_app, monkeypatch):
    from app.models import SyncMessageLog
    from app.utils import get_beijing_now
    with flask_app.app_context():
        now = get_beijing_now().replace(hour=16, minute=45, second=0, microsecond=0)
        g_default, g_week, g_forever = _group('-1001'), _group('-1002'), _group('-1003')
        r.set_group_retention_hours(g_week, 168)
        r.set_group_retention_hours(g_forever, 0)
        for gid in (g_default, g_week, g_forever, None):
            _log(gid, 1, now)      # today → keep
            _log(gid, 20, now)     # yesterday evening → drop for 1-day calendar
            _log(gid, 200, now)    # old → drop for 1-day and 7-day
        stats = r.cleanup_sync_logs(now=now)
        left = lambda gid: sorted(round((now - l.synced_at).total_seconds() / 3600)
                                  for l in SyncMessageLog.query.filter_by(source_group_id=gid).all())
        assert left(g_default) == [1]
        assert left(None) == [1]
        assert left(g_week) == [1, 20]
        assert left(g_forever) == [1, 20, 200]
        assert stats['deleted'] == 2 + 2 + 1
        assert r.get_group_retention_hours(g_week) == 168


def test_cleanup_batches(flask_app, monkeypatch):
    from app.models import SyncMessageLog
    from app.utils import get_beijing_now
    monkeypatch.setattr(r, 'BATCH_SIZE', 3)
    with flask_app.app_context():
        now = get_beijing_now()
        gid = _group('-1004')
        for i in range(10):
            _log(gid, 48, now, mid=i)
        _log(gid, 1, now, mid=99)
        assert r.cleanup_sync_logs(now=now)['deleted'] == 10
        assert SyncMessageLog.query.count() == 1


def test_env_forever_keeps_everything(flask_app, monkeypatch):
    from app.models import SyncMessageLog
    from app.utils import get_beijing_now
    monkeypatch.setenv('SYNC_LOG_RETENTION_HOURS', '0')
    with flask_app.app_context():
        now = get_beijing_now()
        _log(_group('-1005'), 5000, now)
        assert r.cleanup_sync_logs(now=now)['deleted'] == 0
        assert SyncMessageLog.query.count() == 1


def test_loop_guard_db_fallback_survives_retention(flask_app):
    """Fresh sync outputs (the only ones an echo can hit) are never deleted by retention."""
    from app.services import sync_loop_guard as g
    from app.utils import get_beijing_now
    with flask_app.app_context():
        now = get_beijing_now()
        gid = _group('-1006')
        r.set_group_retention_hours(gid, 1)  # most aggressive setting
        from app import db
        from app.models import SyncMessageLog
        db.session.add(SyncMessageLog(source_group_id=gid, target_group_id='-100777', target_message_id=55,
                                      status='success', synced_at=now - timedelta(seconds=5)))
        db.session.commit()
        r.cleanup_sync_logs(now=now)
        g.reset()  # simulate restart: memory registry empty
        assert g.is_sync_output_db(-100777, 55)


def test_page_and_api(flask_app, client):
    with flask_app.app_context():
        gid = _group('-1007')
    with client.session_transaction() as s:
        s['logged_in'] = True
        s.pop('clone_id', None)
    page = client.get(f'/core/group/{gid}/sync_message_logs')
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert '日志保留时长' in html and '1 天' in html and '永久保留' in html
    res = client.post('/core/api/sync_log_retention', json={'group_id': gid, 'hours': 72}).get_json()
    assert res == {'status': 'ok', 'hours': 72, 'label': '3 天'}
    res = client.post('/core/api/sync_log_retention', json={'group_id': gid, 'hours': 0}).get_json()
    assert res['label'] == '永久保留'
    res = client.post('/core/api/sync_log_retention', json={'group_id': gid, 'hours': 'x'}).get_json()
    assert res['status'] == 'error'
    with flask_app.app_context():
        assert r.get_group_retention_hours(gid) == 0


def test_api_requires_login(client):
    assert client.post('/core/api/sync_log_retention', json={'group_id': 1, 'hours': 24}).get_json()['status'] == 'error'


def test_job_runs_and_logs(flask_app, capsys):
    import asyncio
    from app.modules.core import routes
    from app.utils import get_beijing_now
    with flask_app.app_context():
        _log(_group('-1008'), 50, get_beijing_now())
    old = routes.global_flask_app
    routes.global_flask_app = flask_app
    try:
        asyncio.new_event_loop().run_until_complete(r.sync_log_cleanup_job(None))
    finally:
        routes.global_flask_app = old
    out = capsys.readouterr().out
    assert '[sync-log-retention] cleanup done: deleted=1' in out and 'userbot=' in out and 'cutoff=' in out


def test_userbot_rows_cleaned_by_calendar_day(flask_app):
    """via=userbot is NOT skipped; 1-day calendar cutoff clears yesterday evening rows."""
    from app import db
    from app.models import SyncMessageLog
    from app.utils import get_beijing_now
    with flask_app.app_context():
        now = get_beijing_now().replace(hour=16, minute=45, second=0, microsecond=0)
        gid = _group('-1009')
        for age, via in ((1, 'userbot'), (20, 'userbot'), (30, None), (20, None)):
            db.session.add(SyncMessageLog(
                source_group_id=gid, target_group_id='-100900', source_message_id=age,
                target_message_id=age, status='success', via=via,
                synced_at=now - timedelta(hours=age)))
        db.session.commit()
        stats = r.cleanup_sync_logs(now=now)
        left = SyncMessageLog.query.order_by(SyncMessageLog.synced_at.desc()).all()
        assert [(round((now - l.synced_at).total_seconds() / 3600), l.via) for l in left] == [(1, 'userbot')]
        assert stats['deleted'] == 3 and stats['deleted_userbot'] == 1
