"""Integration tests for editable authenticated-user TG ID."""

import json


def _login(client):
    with client.session_transaction() as sess:
        sess['logged_in'] = True
        sess['login_user_id'] = 1


def test_edit_user_can_change_tg_id(client, db):
    from app.models import BotGroup, GroupUser, UserPoints, PointsLog

    group = BotGroup(chat_id='-1001', title='g1', type='supergroup', is_active=True)
    db.session.add(group)
    db.session.commit()

    user = GroupUser(group_id=group.id, tg_id=111, profile_data=json.dumps({'name': 'A'}))
    db.session.add(user)
    db.session.add(UserPoints(group_id=group.id, user_id=111, points_balance=30))
    db.session.add(PointsLog(group_id=group.id, user_id=111, points_change=30, reason='test', balance_after=30))
    db.session.commit()
    user_id = user.id

    _login(client)
    resp = client.post('/core/api/save_user', json={
        'id': user_id,
        'group_id': group.id,
        'tg_id': '222',
        'expiration_date': None,
        'profile': {'name': 'A2'},
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['status'] == 'ok'

    updated = GroupUser.query.get(user_id)
    assert updated.tg_id == 222
    assert json.loads(updated.profile_data)['name'] == 'A2'

    assert UserPoints.query.filter_by(group_id=group.id, user_id=111).first() is None
    points = UserPoints.query.filter_by(group_id=group.id, user_id=222).first()
    assert points is not None
    assert points.points_balance == 30
    assert PointsLog.query.filter_by(group_id=group.id, user_id=222).count() == 1


def test_edit_user_rejects_duplicate_tg_id(client, db):
    from app.models import BotGroup, GroupUser

    group = BotGroup(chat_id='-1002', title='g2', type='supergroup', is_active=True)
    db.session.add(group)
    db.session.commit()

    u1 = GroupUser(group_id=group.id, tg_id=100, profile_data='{}')
    u2 = GroupUser(group_id=group.id, tg_id=200, profile_data='{}')
    db.session.add_all([u1, u2])
    db.session.commit()

    _login(client)
    resp = client.post('/core/api/save_user', json={
        'id': u1.id,
        'group_id': group.id,
        'tg_id': 200,
        'profile': {},
    })
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['status'] == 'error'
    assert '已被其他认证用户使用' in body['msg']
    assert GroupUser.query.get(u1.id).tg_id == 100


def test_save_user_custom_member_tags(client, db):
    from app.models import BotGroup, GroupUser
    from app.utils import parse_member_tags

    group = BotGroup(chat_id='-1003', title='g3', type='supergroup', is_active=True)
    db.session.add(group)
    db.session.commit()

    _login(client)
    resp = client.post('/core/api/save_user', json={
        'group_id': group.id,
        'tg_id': 333,
        'expiration_date': None,
        'profile': {'name': 'Tagged'},
        'member_tags': 'VIP, 核心, #管理',
    })
    assert resp.status_code == 200
    assert resp.get_json()['status'] == 'ok'

    user = GroupUser.query.filter_by(group_id=group.id, tg_id=333).first()
    assert user is not None
    assert parse_member_tags(user.member_tags) == ['VIP', '核心', '管理']

    # Update tags on existing user
    resp2 = client.post('/core/api/save_user', json={
        'id': user.id,
        'group_id': group.id,
        'tg_id': 333,
        'profile': {'name': 'Tagged'},
        'member_tags': ['金牌'],
    })
    assert resp2.status_code == 200
    assert resp2.get_json()['status'] == 'ok'
    updated = GroupUser.query.get(user.id)
    assert parse_member_tags(updated.member_tags) == ['金牌']
