"""Isolated local PG only. No real Telegram, no production database writes."""
import asyncio
import os
import tempfile
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select, delete

PG = os.environ.get('YYCJ_TEST_PG')
pytestmark = pytest.mark.skipif(not PG, reason='YYCJ_TEST_PG not set')


@asynccontextmanager
async def setup(monkeypatch, status='active', expired=False, owner=None):
    assert PG and ('127.0.0.1:55441/' in PG) and 'yycj_edit_test' in PG
    os.environ['DATABASE_URL'] = PG
    from bot import db
    from bot.models import Lamp, User, Post, ListingEditOperation as Op, ListingEditVersion as Ver
    from bot.services import listing_edits as svc, listing_notify, city_scope, edit_delivery, listing_history as hist
    await db.connect_db()
    lid = 'EH' + uuid.uuid4().hex
    owner = owner or (82000000 + int(uuid.uuid4().hex[:6], 16))
    until = datetime.utcnow() + timedelta(days=-1 if expired else 10)
    channel = AsyncMock(return_value=edit_delivery.outcome('synced', album='https://t.me/test/901'))
    notify = AsyncMock(return_value=edit_delivery.outcome('sent'))
    monkeypatch.setattr(edit_delivery, 'channel', channel)
    monkeypatch.setattr(edit_delivery, 'notify', notify)
    monkeypatch.setattr(listing_notify, 'notify_admins_new_post', AsyncMock())
    monkeypatch.setattr(city_scope, 'scope_for', AsyncMock(return_value=None))
    cache = Path(tempfile.mkdtemp(prefix='yycj-hist-media-'))
    monkeypatch.setattr(hist, '_DISK_DIR', cache)
    async with db.session_scope() as s:
        if not (await s.execute(select(User).where(User.user_id == owner))).scalar_one_or_none():
            s.add(User(user_id=owner, role='teacher'))
        s.add(Lamp(lamp_id=lid, user_id=owner, city='深圳', title='原文字', status=status, expires_at=until,
                   media=[{'type': 'image', 'url': 'old-photo'}], photos=['old-photo'], description='原介绍'))
    async def read():
        async with db.session_scope() as s:
            return (await s.execute(select(Lamp).where(Lamp.lamp_id == lid))).scalar_one()
    async def versions():
        async with db.session_scope() as s:
            return list((await s.execute(select(Ver).where(Ver.lamp_id == lid).order_by(Ver.sequence))).scalars())
    body = {'city': '深圳', 'title': '新文字', 'tags': [], 'description': '新介绍', 'price_text': '100', 'extras': {}, 'hours': None, 'media': []}
    try:
        yield svc, hist, lid, owner, until, body, read, versions, channel, notify
    finally:
        async with db.session_scope() as s:
            await s.execute(delete(Post).where(Post.lamp_data['edit_lamp_id'].astext == lid))
            await s.execute(delete(Op).where(Op.lamp_id == lid))
            await s.execute(delete(Ver).where(Ver.lamp_id == lid))
            await s.execute(delete(Lamp).where(Lamp.lamp_id == lid))
            await s.execute(delete(User).where(User.user_id == owner))
        import shutil
        shutil.rmtree(cache, ignore_errors=True)
        await db.close_db()


def key():
    return 'op-' + uuid.uuid4().hex


def test_no_history_until_first_proven_apply(monkeypatch):
    async def run():
        async with setup(monkeypatch) as (svc, hist, lid, owner, until, body, read, versions, channel, notify):
            assert (await versions()) == []
            listed = await hist.history(lid, owner)
            assert listed['items'] == [] and '不会补造' in listed['notice']
    asyncio.run(run())


def test_first_edit_records_baseline_and_applied_without_touching_expiry(monkeypatch):
    async def run():
        async with setup(monkeypatch) as (svc, hist, lid, owner, until, body, read, versions, channel, notify):
            await svc.submit(lid, owner, body, key())
            rows = await versions()
            assert [r.source for r in rows] == ['baseline', 'teacher_edit']
            assert rows[0].actor_id is None and rows[0].snapshot['title'] == '原文字'
            assert rows[1].snapshot['title'] == '新文字' and 'title' in rows[1].changed_fields
            lamp = await read()
            assert lamp.expires_at == until and lamp.status == 'active' and lamp.title == '新文字'
    asyncio.run(run())


def test_teacher_can_preview_and_restore_text_as_new_edit(monkeypatch):
    async def run():
        async with setup(monkeypatch) as (svc, hist, lid, owner, until, body, read, versions, channel, notify):
            await svc.submit(lid, owner, body, key())
            rows = await versions()
            baseline = rows[0]
            lamp = await read()
            preview = await hist.preview(lid, baseline.version_id, owner)
            assert preview['version']['can_restore_text'] and preview['version']['snapshot']['title'] == '原文字'
            result = await hist.restore(
                lid, baseline.version_id, owner, key(),
                expected_updated_at=lamp.updated_at.isoformat(), confirm=True, include_media=False,
            )
            assert result['text_saved']
            lamp = await read()
            assert lamp.title == '原文字' and lamp.description == '原介绍'
            assert lamp.expires_at == until and lamp.status == 'active'
            assert lamp.media[0]['url'] == 'old-photo'
            sources = [r.source for r in await versions()]
            assert 'restore' in sources
    asyncio.run(run())


def test_restore_requires_confirm_and_fresh_version(monkeypatch):
    async def run():
        async with setup(monkeypatch) as (svc, hist, lid, owner, until, body, read, versions, channel, notify):
            await svc.submit(lid, owner, body, key())
            baseline = (await versions())[0]
            lamp = await read()
            with pytest.raises(svc.EditConflict, match='确认'):
                await hist.restore(lid, baseline.version_id, owner, key(), expected_updated_at=lamp.updated_at.isoformat(), confirm=False)
            with pytest.raises(svc.EditConflict, match='刷新'):
                await hist.restore(lid, baseline.version_id, owner, key(), expected_updated_at='2000-01-01T00:00:00', confirm=True)
            assert (await read()).title == '新文字'
    asyncio.run(run())


def test_foreign_teacher_and_out_of_scope_admin_cannot_read_or_restore(monkeypatch):
    async def run():
        async with setup(monkeypatch) as (svc, hist, lid, owner, until, body, read, versions, channel, notify):
            await svc.submit(lid, owner, body, key())
            baseline = (await versions())[0]
            other = owner + 17
            from bot import db
            from bot.models import User
            from bot.services import city_scope
            async with db.session_scope() as s:
                s.add(User(user_id=other, role='teacher'))
            with pytest.raises(svc.EditConflict):
                await hist.history(lid, other)
            with pytest.raises(svc.EditConflict):
                await hist.restore(lid, baseline.version_id, other, key(), expected_updated_at=(await read()).updated_at.isoformat(), confirm=True)
            from fastapi import HTTPException
            monkeypatch.setattr(city_scope, 'scope_for', AsyncMock(return_value={'广州'}))
            with pytest.raises(HTTPException):
                await hist.history(lid, 1, admin=True)
            monkeypatch.setattr(city_scope, 'scope_for', AsyncMock(return_value={'深圳'}))
            listed = await hist.history(lid, 1, admin=True)
            assert listed['items'] and all('管理员' in x['actor_label'] or '老师' in x['actor_label'] or '原文' in x['actor_label'] for x in listed['items'])
            async with db.session_scope() as s:
                await s.execute(delete(User).where(User.user_id == other))
    asyncio.run(run())


def test_missing_media_restore_fails_without_clearing_current(monkeypatch):
    async def run():
        async with setup(monkeypatch) as (svc, hist, lid, owner, until, body, read, versions, channel, notify):
            first = await svc.submit(lid, owner, {**body, 'media': [{'type': 'image', 'url': 'new-photo'}]}, key())
            await svc.review(first['post_id'], True)
            assert (await read()).media[0]['url'] == 'new-photo'
            baseline = [r for r in await versions() if r.source == 'baseline'][0]
            prev = await hist.preview(lid, baseline.version_id, owner)
            assert prev['version']['media_available'] is False
            lamp = await read()
            with pytest.raises(svc.EditConflict, match='不可用'):
                await hist.restore(lid, baseline.version_id, owner, key(), expected_updated_at=lamp.updated_at.isoformat(), confirm=True, include_media=True)
            assert (await read()).media[0]['url'] == 'new-photo'
            result = await hist.restore(lid, baseline.version_id, owner, key(), expected_updated_at=(await read()).updated_at.isoformat(), confirm=True, include_media=False)
            assert result['text_saved']
            lamp = await read()
            assert lamp.title == '原文字' and lamp.media[0]['url'] == 'new-photo' and lamp.expires_at == until
    asyncio.run(run())


def test_teacher_media_restore_goes_through_review(monkeypatch):
    async def run():
        async with setup(monkeypatch) as (svc, hist, lid, owner, until, body, read, versions, channel, notify):
            first = await svc.submit(lid, owner, {**body, 'media': [{'type': 'image', 'url': 'new-photo'}]}, key())
            await svc.review(first['post_id'], True)
            assert (await read()).media[0]['url'] == 'new-photo'
            # Make baseline media available via disk cache so a media restore can be attempted safely.
            import hashlib
            hist._DISK_DIR.mkdir(parents=True, exist_ok=True)
            (hist._DISK_DIR / hashlib.sha256(b'old-photo').hexdigest()[:40]).write_bytes(b'fake-old')
            baseline = [r for r in await versions() if r.source == 'baseline'][0]
            prev = await hist.preview(lid, baseline.version_id, owner)
            assert prev['version']['can_restore_media'] is True
            result = await hist.restore(
                lid, baseline.version_id, owner, key(),
                expected_updated_at=(await read()).updated_at.isoformat(), confirm=True, include_media=True,
            )
            lamp = await read()
            assert lamp.media[0]['url'] == 'new-photo'  # not applied until review
            assert result['review_status'] == 'pending' and result['media_status'] == 'pending'
            assert lamp.expires_at == until and lamp.status == 'active'
            await svc.review(result['post_id'], True)
            lamp = await read()
            assert lamp.media[0]['url'] == 'old-photo' and lamp.expires_at == until
    asyncio.run(run())


def test_restore_is_idempotent_on_operation_id_and_ignores_injected_system_fields(monkeypatch):
    async def run():
        async with setup(monkeypatch) as (svc, hist, lid, owner, until, body, read, versions, channel, notify):
            await svc.submit(lid, owner, body, key())
            from bot import db
            from bot.models import ListingEditVersion as Ver
            baseline = (await versions())[0]
            async with db.session_scope() as s:
                row = await s.get(Ver, baseline.version_id)
                snap = dict(row.snapshot)
                snap.update(status='hidden', expires_at='1999-01-01T00:00:00', authenticity_score=1, user_id=1)
                row.snapshot = snap
            token = key()
            first = await hist.restore(lid, baseline.version_id, owner, token, expected_updated_at=(await read()).updated_at.isoformat(), confirm=True)
            second = await hist.restore(lid, baseline.version_id, owner, token, expected_updated_at=(await read()).updated_at.isoformat(), confirm=True)
            assert first == second
            lamp = await read()
            assert lamp.status == 'active' and lamp.expires_at == until and lamp.title == '原文字'
    asyncio.run(run())


def test_rejected_media_does_not_record_media_version(monkeypatch):
    async def run():
        async with setup(monkeypatch) as (svc, hist, lid, owner, until, body, read, versions, channel, notify):
            r = await svc.submit(lid, owner, {**body, 'media': [{'type': 'image', 'url': 'rejected-photo'}]}, key())
            await svc.review(r['post_id'], False)
            sources_fields = [(row.source, row.changed_fields) for row in await versions()]
            assert all('media' not in (fields or []) or source == 'baseline' for source, fields in sources_fields if source != 'baseline')
            # teacher_edit may include only text fields
            applied_media = [row for row in await versions() if row.source in ('teacher_edit', 'review_approved') and 'media' in (row.changed_fields or [])]
            assert applied_media == []
            assert (await read()).media[0]['url'] == 'old-photo'
    asyncio.run(run())


def test_hard_delete_removes_versions(monkeypatch):
    async def run():
        async with setup(monkeypatch) as (svc, hist, lid, owner, until, body, read, versions, channel, notify):
            await svc.submit(lid, owner, body, key())
            assert await versions()
            from bot.services import listing_ops
            await listing_ops.delete_lamp(lid)
            assert (await versions()) == []
    asyncio.run(run())
