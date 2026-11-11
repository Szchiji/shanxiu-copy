"""Scheduled messages: cadence, failure cap, empty-content validation,
one-shot re-enable and multi-media album delivery."""

import asyncio
import json
import itertools
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

_ids = itertools.count(1)
BASE_NOW = datetime(2026, 10, 8, 10, 30, 0)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _make_bot(fail=False):
    counter = itertools.count(1000)
    bot = AsyncMock()

    async def _send_message(**kwargs):
        if fail:
            raise RuntimeError('Forbidden: bot was kicked')
        return SimpleNamespace(message_id=next(counter))

    async def _send_media_group(**kwargs):
        return [SimpleNamespace(message_id=next(counter)) for _ in kwargs['media']]

    async def _single(**kwargs):
        return SimpleNamespace(message_id=next(counter))

    async def _copy_messages(**kwargs):
        return tuple(SimpleNamespace(message_id=next(counter)) for _ in kwargs['message_ids'])

    bot.send_message = AsyncMock(side_effect=_send_message)
    bot.send_media_group = AsyncMock(side_effect=_send_media_group)
    bot.send_photo = AsyncMock(side_effect=_single)
    bot.send_video = AsyncMock(side_effect=_single)
    bot.copy_messages = AsyncMock(side_effect=_copy_messages)
    return bot


@pytest.fixture()
def sched(flask_app, monkeypatch):
    """Helpers bound to routes with a controllable Beijing clock."""
    from app import db
    from app.models import BotGroup, ScheduledMessage
    from app.modules.core import routes

    monkeypatch.setattr(routes, 'global_flask_app', flask_app)
    clock = {'now': BASE_NOW}
    monkeypatch.setattr(routes, 'get_beijing_now', lambda: clock['now'])
    # Shared in-memory DB: park rows left by other tests so each test only sees its own.
    with flask_app.app_context():
        ScheduledMessage.query.update({'is_active': False})
        db.session.commit()

    def make_msg(**kw):
        with flask_app.app_context():
            n = next(_ids)
            group = BotGroup(chat_id=f'-100777{n:04d}', title=f'g{n}', type='supergroup', is_active=True)
            db.session.add(group)
            db.session.commit()
            fields = dict(group_id=group.id, media_type='text', content='hello',
                          repeat_interval=0, is_active=True, links='[]')
            fields.update(kw)
            msg = ScheduledMessage(**fields)
            db.session.add(msg)
            db.session.commit()
            return msg.id, group.id

    def get(msg_id):
        with flask_app.app_context():
            m = ScheduledMessage.query.get(msg_id)
            db.session.expunge(m)
            return m

    def tick(bot):
        _run(routes.check_scheduled_messages(SimpleNamespace(bot=bot)))

    return SimpleNamespace(routes=routes, clock=clock, make_msg=make_msg, get=get, tick=tick)


def _sends_to(bot, chat_id):
    return [c for c in bot.send_message.await_args_list if str(c.kwargs.get('chat_id')) == str(chat_id)]


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_advance_keeps_future_slot(self):
        from app.modules.core.routes import advance_next_send_at
        prev = datetime(2026, 10, 8, 12, 0)
        assert advance_next_send_at(prev, 180, datetime(2026, 10, 8, 10, 31)) == prev

    def test_advance_moves_past_slot_once(self):
        from app.modules.core.routes import advance_next_send_at
        prev = datetime(2026, 10, 8, 12, 0)
        assert advance_next_send_at(prev, 180, datetime(2026, 10, 8, 12, 0, 30)) == datetime(2026, 10, 8, 15, 0)

    def test_should_send_waits_for_future_next(self):
        from app.modules.core.routes import scheduled_should_send
        now = BASE_NOW
        assert scheduled_should_send(None, now + timedelta(minutes=90), 180, now) is False
        assert scheduled_should_send(None, None, 180, now) is True
        assert scheduled_should_send(None, now - timedelta(seconds=1), 180, now) is True
        assert scheduled_should_send(now - timedelta(hours=1), None, 0, now) is False

    def test_has_payload(self):
        from app.modules.core.routes import scheduled_message_has_payload
        assert not scheduled_message_has_payload('text', [], None)
        assert not scheduled_message_has_payload('text', [], '<p><br></p>&nbsp;')
        assert not scheduled_message_has_payload('text', ['http://x/a.jpg'], '')
        assert scheduled_message_has_payload('image', ['http://x/a.jpg'], '')
        assert scheduled_message_has_payload('text', [], '<b>hi</b>')

    def test_split_media_url_entry(self):
        from app.modules.core.routes import split_media_url_entry
        assert split_media_url_entry('https://a/1.jpg\nhttps://a/2.jpg https://a/3.jpg') == [
            'https://a/1.jpg', 'https://a/2.jpg', 'https://a/3.jpg']
        assert split_media_url_entry('https://a/1.jpg,https://a/2.jpg，t.me/c/1/2') == [
            'https://a/1.jpg', 'https://a/2.jpg', 't.me/c/1/2']
        # commas inside a single URL are preserved
        assert split_media_url_entry('https://a/x?w=1,2') == ['https://a/x?w=1,2']


# ---------------------------------------------------------------------------
# Scheduler cadence
# ---------------------------------------------------------------------------

class TestCadence:
    def test_past_start_recurring_waits_for_next_send_at(self, sched):
        start = BASE_NOW - timedelta(minutes=90)           # 09:00
        slot = start + timedelta(minutes=180)               # 12:00
        msg_id, gid = sched.make_msg(repeat_interval=180, start_time=start, next_send_at=slot)
        chat = sched.get(msg_id).group_id
        bot = _make_bot()

        sched.tick(bot)                                     # 10:30 → must NOT send
        assert bot.send_message.await_count == 0
        assert sched.get(msg_id).last_sent_at is None

        sched.clock['now'] = slot + timedelta(seconds=20)   # 12:00:20 → send
        sched.tick(bot)
        assert bot.send_message.await_count == 1
        m = sched.get(msg_id)
        assert m.next_send_at == slot + timedelta(minutes=180)  # 15:00, nothing skipped

    def test_regular_send_advances_exactly_one_interval(self, sched):
        slot = BASE_NOW - timedelta(seconds=30)
        msg_id, _ = sched.make_msg(repeat_interval=60, start_time=BASE_NOW - timedelta(hours=5),
                                   last_sent_at=slot - timedelta(minutes=60), next_send_at=slot)
        bot = _make_bot()
        sched.tick(bot)
        assert bot.send_message.await_count == 1
        assert sched.get(msg_id).next_send_at == slot + timedelta(minutes=60)

    def test_reenable_never_sent_recurring_does_not_fire_immediately(self, sched, client):
        start = BASE_NOW - timedelta(minutes=90)
        msg_id, _ = sched.make_msg(repeat_interval=180, start_time=start, is_active=False)
        with client.session_transaction() as s:
            s['logged_in'] = True
            s.pop('clone_id', None)
        r = client.post('/core/api/toggle_scheduled_message', json={'id': msg_id}).get_json()
        assert r['status'] == 'ok'
        m = sched.get(msg_id)
        assert m.is_active and m.next_send_at == start + timedelta(minutes=180)
        bot = _make_bot()
        sched.tick(bot)
        assert bot.send_message.await_count == 0


# ---------------------------------------------------------------------------
# Failure cap
# ---------------------------------------------------------------------------

class TestFailureCap:
    def test_one_shot_disabled_after_max_failures(self, sched):
        msg_id, _ = sched.make_msg(repeat_interval=0)
        bot = _make_bot(fail=True)
        maxf = sched.routes.SCHEDULED_MSG_MAX_FAILURES
        for i in range(1, maxf):
            sched.tick(bot)
            m = sched.get(msg_id)
            assert m.is_active is True and m.fail_count == i and m.last_sent_at is None
        sched.tick(bot)
        m = sched.get(msg_id)
        assert m.is_active is False and m.fail_count == maxf
        # no more attempts once disabled
        calls = bot.send_message.await_count
        sched.tick(bot)
        assert bot.send_message.await_count == calls

    def test_success_resets_fail_count(self, sched):
        msg_id, _ = sched.make_msg(repeat_interval=0, fail_count=3)
        sched.tick(_make_bot())
        m = sched.get(msg_id)
        assert m.fail_count == 0 and m.is_active is False and m.last_sent_at is not None

    def test_empty_legacy_row_counts_as_failure(self, sched):
        msg_id, _ = sched.make_msg(repeat_interval=0, content=None)
        bot = _make_bot()
        sched.tick(bot)
        m = sched.get(msg_id)
        assert m.fail_count == 1 and m.last_sent_at is None and m.is_active


# ---------------------------------------------------------------------------
# Save / send-now validation and one-shot re-enable
# ---------------------------------------------------------------------------

@pytest.fixture()
def admin(client):
    with client.session_transaction() as s:
        s['logged_in'] = True
        s.pop('clone_id', None)
    return client


class TestApi:
    def test_save_rejects_empty_content(self, sched, admin):
        _, gid = sched.make_msg()
        r = admin.post('/core/api/save_scheduled_message', json={
            'group_id': gid, 'media_type': 'image', 'media_urls': ['  '], 'content': '<p><br></p>',
        }).get_json()
        assert r['status'] == 'error' and '消息内容' in r['msg']

    def test_send_now_rejects_empty_content(self, sched, admin):
        msg_id, gid = sched.make_msg()
        r = admin.post(f'/core/api/send_scheduled_message_now/{msg_id}', json={
            'group_id': gid, 'media_type': 'text', 'content': '',
        }).get_json()
        assert r['status'] == 'error' and '消息内容' in r['msg']
        assert sched.get(msg_id).content == 'hello'   # nothing persisted

    def test_save_splits_pasted_urls(self, sched, admin):
        _, gid = sched.make_msg()
        r = admin.post('/core/api/save_scheduled_message', json={
            'group_id': gid, 'media_type': 'image',
            'media_urls': ['https://a/1.jpg\nhttps://a/2.jpg', 'https://a/3.jpg'], 'content': '',
        }).get_json()
        assert r['status'] == 'ok'
        from app.models import ScheduledMessage
        with sched.routes.global_flask_app.app_context():
            m = ScheduledMessage.query.filter_by(group_id=gid).order_by(ScheduledMessage.id.desc()).first()
            assert m.get_media_url_list() == ['https://a/1.jpg', 'https://a/2.jpg', 'https://a/3.jpg']

    def test_reenable_sent_one_shot_with_future_start_fires(self, sched, admin):
        start = BASE_NOW + timedelta(hours=1)
        msg_id, _ = sched.make_msg(repeat_interval=0, start_time=start, is_active=False,
                                   last_sent_at=BASE_NOW - timedelta(days=1), fail_count=5)
        r = admin.post('/core/api/toggle_scheduled_message', json={'id': msg_id}).get_json()
        assert r['status'] == 'ok'
        m = sched.get(msg_id)
        assert m.is_active and m.last_sent_at is None and m.next_send_at == start and m.fail_count == 0

        bot = _make_bot()
        sched.tick(bot)                                   # before start: nothing
        assert bot.send_message.await_count == 0
        sched.clock['now'] = start + timedelta(seconds=5)
        sched.tick(bot)
        assert bot.send_message.await_count == 1
        assert sched.get(msg_id).is_active is False      # one-shot deactivates after send

    def test_batch_enable_one_shot_future_start(self, sched, admin):
        start = BASE_NOW + timedelta(hours=2)
        msg_id, _ = sched.make_msg(repeat_interval=0, start_time=start, is_active=False,
                                   last_sent_at=BASE_NOW - timedelta(days=1))
        r = admin.post('/core/api/batch_scheduled_messages', json={'ids': [msg_id], 'action': 'enable'}).get_json()
        assert r['status'] == 'ok'
        m = sched.get(msg_id)
        assert m.last_sent_at is None and m.next_send_at == start


# ---------------------------------------------------------------------------
# Multi-media album delivery
# ---------------------------------------------------------------------------

class TestAlbum:
    def test_multiple_urls_sent_as_one_media_group_with_caption(self):
        from app.modules.core.routes import send_multi_media
        bot = _make_bot()
        ids = []
        last = _run(send_multi_media(bot, -1001, 'image', ['u1', 'u2', 'u3'], content='<b>hi</b>', collected_ids=ids))
        bot.send_media_group.assert_awaited_once()
        media = bot.send_media_group.await_args.kwargs['media']
        assert len(media) == 3
        assert media[0].caption == '<b>hi</b>' and media[0].parse_mode == 'HTML'
        assert media[1].caption is None
        bot.send_message.assert_not_awaited()
        assert len(ids) == 3 and last.message_id == ids[-1]

    def test_buttons_go_to_follow_message(self):
        from telegram import InlineKeyboardMarkup, InlineKeyboardButton
        from app.modules.core.routes import send_multi_media
        bot = _make_bot()
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('go', url='https://x')]])
        ids = []
        _run(send_multi_media(bot, -1001, 'media', ['u1', 'u2'], content='txt', reply_markup=kb,
                              media_url_types=['image', 'video'], collected_ids=ids))
        media = bot.send_media_group.await_args.kwargs['media']
        assert [type(m).__name__ for m in media] == ['InputMediaPhoto', 'InputMediaVideo']
        assert all(m.caption is None for m in media)
        follow = bot.send_message.await_args.kwargs
        assert follow['text'] == 'txt' and follow['reply_markup'] is kb
        assert len(ids) == 3

    def test_more_than_ten_split_into_groups(self):
        from app.modules.core.routes import send_multi_media
        bot = _make_bot()
        urls = [f'u{i}' for i in range(11)]
        ids = []
        _run(send_multi_media(bot, -1001, 'image', urls, content='c', collected_ids=ids))
        assert bot.send_media_group.await_count == 1
        assert len(bot.send_media_group.await_args.kwargs['media']) == 10
        bot.send_photo.assert_awaited_once()          # leftover single item
        assert bot.send_photo.await_args.kwargs['caption'] is None
        assert len(ids) == 11

    def test_tme_links_unresolvable_fall_back_to_copy_messages(self, monkeypatch):
        from app.modules.core import routes
        monkeypatch.setattr(routes, '_TME_MEDIA_CACHE', {})
        monkeypatch.setattr(routes, 'resolve_tme_media', AsyncMock(return_value=None))
        bot = _make_bot()
        ids = []
        _run(routes.send_multi_media(bot, -1001, 'image',
                                     ['https://t.me/mychan/12', 'https://t.me/mychan/11', 'https://t.me/mychan/13'],
                                     content='cap', collected_ids=ids))
        bot.send_media_group.assert_not_awaited()
        kw = bot.copy_messages.await_args.kwargs
        assert kw['from_chat_id'] == '@mychan' and kw['message_ids'] == [11, 12, 13]
        assert kw['remove_caption'] is True
        assert bot.send_message.await_args.kwargs['text'] == 'cap'
        assert len(ids) == 4


class TestTmeAlbum:
    """Separate t.me posts (not originally one album) must be rebuilt into ONE album."""

    @pytest.fixture(autouse=True)
    def _isolate(self, monkeypatch):
        from app.modules.core import routes
        monkeypatch.setattr(routes, '_TME_MEDIA_CACHE', {})
        monkeypatch.delenv('MEDIA_STAGING_CHAT_ID', raising=False)
        self.scraped = []

        def _fake_scrape(username, msg_id):
            self.scraped.append((username, msg_id))
            return {'kind': 'image', 'media': b'\xff\xd8\xffjpeg' + str(msg_id).encode(),
                    'filename': f'{username}_{msg_id}.jpg'}

        monkeypatch.setattr(routes, '_scrape_public_tme_media', _fake_scrape)
        self.routes = routes

    @staticmethod
    def _album_bot():
        bot = _make_bot()
        counter = itertools.count(5000)

        async def _send_media_group(**kwargs):
            return [SimpleNamespace(message_id=next(counter),
                                    photo=[SimpleNamespace(file_id=f'FID{next(counter)}')], video=None)
                    for _ in kwargs['media']]

        bot.send_media_group = AsyncMock(side_effect=_send_media_group)
        bot.forward_message = AsyncMock(side_effect=AssertionError('should not probe'))
        return bot

    LINKS = ['https://t.me/seriouslyaz/1419', 'https://t.me/seriouslyaz/1418', 'https://t.me/seriouslyaz/1417']

    def test_three_separate_tme_posts_sent_as_one_album(self):
        bot = self._album_bot()
        ids = []
        last = _run(self.routes.send_multi_media(bot, -1001, 'image', self.LINKS, content='<b>hi</b>',
                                                 collected_ids=ids))
        bot.send_media_group.assert_awaited_once()
        bot.copy_messages.assert_not_awaited()
        bot.copy_message.assert_not_awaited()
        bot.send_photo.assert_not_awaited()
        media = bot.send_media_group.await_args.kwargs['media']
        assert [type(m).__name__ for m in media] == ['InputMediaPhoto'] * 3
        # order preserved as configured (1419, 1418, 1417)
        assert [m.media.filename for m in media] == ['seriouslyaz_1419.jpg', 'seriouslyaz_1418.jpg',
                                                     'seriouslyaz_1417.jpg']
        assert media[0].caption == '<b>hi</b>' and media[0].parse_mode == 'HTML'
        assert media[1].caption is None and media[2].caption is None
        bot.send_message.assert_not_awaited()
        assert len(ids) == 3 and last.message_id == ids[-1]

    def test_file_ids_cached_for_next_send(self):
        bot = self._album_bot()
        _run(self.routes.send_multi_media(bot, -1001, 'image', self.LINKS, content='x'))
        assert len(self.scraped) == 3
        _run(self.routes.send_multi_media(bot, -1001, 'image', self.LINKS, content='x'))
        assert len(self.scraped) == 3  # no re-download
        media = bot.send_media_group.await_args.kwargs['media']
        assert all(isinstance(m.media, str) and m.media.startswith('FID') for m in media)

    def test_tme_album_with_buttons_has_follow_message(self):
        from telegram import InlineKeyboardMarkup, InlineKeyboardButton
        bot = self._album_bot()
        kb = InlineKeyboardMarkup([[InlineKeyboardButton('go', url='https://x')]])
        ids = []
        _run(self.routes.send_multi_media(bot, -1001, 'image', self.LINKS, content='txt', reply_markup=kb,
                                          collected_ids=ids))
        bot.send_media_group.assert_awaited_once()
        media = bot.send_media_group.await_args.kwargs['media']
        assert len(media) == 3 and all(m.caption is None for m in media)
        follow = bot.send_message.await_args.kwargs
        assert follow['text'] == 'txt' and follow['reply_markup'] is kb
        assert len(ids) == 4

    def test_tme_mixed_with_https_urls_single_album(self):
        bot = self._album_bot()
        _run(self.routes.send_multi_media(bot, -1001, 'media',
                                          ['https://a/1.jpg', 'https://t.me/seriouslyaz/1418', 'https://a/2.mp4'],
                                          content='c', media_url_types=['image', 'image', 'video']))
        bot.send_media_group.assert_awaited_once()
        media = bot.send_media_group.await_args.kwargs['media']
        assert [type(m).__name__ for m in media] == ['InputMediaPhoto', 'InputMediaPhoto', 'InputMediaVideo']
        assert media[0].media == 'https://a/1.jpg' and media[2].media == 'https://a/2.mp4'
        assert media[0].caption == 'c'

    def test_private_channel_link_resolved_via_forward_probe(self):
        bot = self._album_bot()
        fwd_ids = itertools.count(9000)

        async def _fwd(**kwargs):
            return SimpleNamespace(message_id=next(fwd_ids), video=None, caption='orig', caption_entities=(),
                                   photo=[SimpleNamespace(file_id='small'),
                                          SimpleNamespace(file_id=f"big{kwargs['message_id']}")])

        bot.forward_message = AsyncMock(side_effect=_fwd)
        _run(self.routes.send_multi_media(bot, -1001, 'image',
                                          ['https://t.me/c/123/5', 'https://t.me/c/123/6'], content=None))
        assert self.scraped == []  # private links cannot be scraped
        assert bot.forward_message.await_count == 2
        assert bot.forward_message.await_args.kwargs['disable_notification'] is True
        assert bot.delete_message.await_count == 2  # probe messages cleaned up
        media = bot.send_media_group.await_args.kwargs['media']
        assert [m.media for m in media] == ['big5', 'big6']
        assert media[0].caption == 'orig'  # original caption kept when no custom text
        bot.copy_messages.assert_not_awaited()

    def test_staging_chat_used_before_target_chat(self, monkeypatch):
        monkeypatch.setenv('MEDIA_STAGING_CHAT_ID', '-100999')
        bot = self._album_bot()

        async def _fwd(**kwargs):
            return SimpleNamespace(message_id=1, video=None, caption=None,
                                   photo=[SimpleNamespace(file_id=f"s{kwargs['message_id']}")])

        bot.forward_message = AsyncMock(side_effect=_fwd)
        _run(self.routes.send_multi_media(bot, -1001, 'image', self.LINKS))
        assert {c.kwargs['chat_id'] for c in bot.forward_message.await_args_list} == {'-100999'}
        assert self.scraped == []
        media = bot.send_media_group.await_args.kwargs['media']
        assert [m.media for m in media] == ['s1419', 's1418', 's1417']

    def test_album_failure_retries_then_sends_resolved_media(self):
        bot = self._album_bot()
        # First send_media_group fails; after bytes→file_id pre-upload, retry also fails;
        # final path sends already-resolved media via send_photo (not copy_message).
        bot.send_media_group = AsyncMock(side_effect=RuntimeError('Bad Request'))
        ids = []
        _run(self.routes.send_multi_media(bot, -1001, 'image', self.LINKS, content='cap', collected_ids=ids))
        assert bot.send_media_group.await_count >= 2
        assert bot.send_photo.await_count >= 3  # pre-upload +/or final singles
        assert bot.copy_message.await_count == 0
        assert bot.send_message.await_args.kwargs['text'] == 'cap'

    def test_scheduler_three_tme_links_one_album(self, sched):
        msg_id, _ = sched.make_msg(media_type='image', content='hi', media_urls=json.dumps(self.LINKS))
        bot = self._album_bot()
        sched.tick(bot)
        bot.send_media_group.assert_awaited_once()
        assert len(bot.send_media_group.await_args.kwargs['media']) == 3
        bot.copy_messages.assert_not_awaited()
        m = sched.get(msg_id)
        assert len(json.loads(m.last_message_ids)) == 3


class TestAlbumScheduler:
    def test_scheduler_sends_multi_media_as_album(self, sched):
        msg_id, _ = sched.make_msg(media_type='image', content='hi',
                                   media_urls=json.dumps(['https://a/1.jpg', 'https://a/2.jpg']))
        bot = _make_bot()
        sched.tick(bot)
        bot.send_media_group.assert_awaited_once()
        assert len(bot.send_media_group.await_args.kwargs['media']) == 2
        m = sched.get(msg_id)
        assert len(json.loads(m.last_message_ids)) == 2


class TestBackupRoundTrip:
    def test_export_import_keeps_all_media(self, sched, admin):
        _, gid = sched.make_msg(media_type='image', content='x',
                                media_urls=json.dumps(['https://a/1.jpg', 'https://a/2.jpg']),
                                media_url='https://a/1.jpg')
        exported = admin.get(f'/core/group/{gid}/backup/export')
        assert exported.status_code == 200
        cfg = exported.get_json()
        sm = cfg['scheduled_messages'][0]
        assert sm['media_urls'] == ['https://a/1.jpg', 'https://a/2.jpg']

        _, gid2 = sched.make_msg()
        r = admin.post(f'/core/group/{gid2}/backup/import', json={'scheduled_messages': [sm]})
        assert r.status_code == 200
        from app.models import ScheduledMessage
        with sched.routes.global_flask_app.app_context():
            rows = ScheduledMessage.query.filter_by(group_id=gid2).all()
            imported = [x for x in rows if x.content == 'x']
            assert imported and imported[0].get_media_url_list() == ['https://a/1.jpg', 'https://a/2.jpg']


class TestCloneSkipWarning:
    def test_warning_is_rate_limited(self, sched, capsys, monkeypatch):
        routes = sched.routes
        monkeypatch.setattr(routes, '_clone_skip_last_warned', {})
        routes._warn_clone_not_running_for_scheduled(42, 1)
        routes._warn_clone_not_running_for_scheduled(42, 2)
        out = capsys.readouterr().out
        assert out.count('克隆机器人 42 未运行') == 1
