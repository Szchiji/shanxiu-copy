"""Admin pages / JSON APIs for 小号登录 (registered on core_bp; global admins only)."""

from __future__ import annotations

import json
import re

from flask import jsonify, redirect, render_template, request, session

from app import db, is_clone_instance, limiter
from app.models import BotGroup, SyncMessageLog, UserbotAccount, UserbotSyncRule
from app.modules.core.routes import core_bp
from app.userbot.crypto import encrypt_secret, decrypt_secret, normalize_phone
from app.userbot.manager import UserbotError, userbot_manager
from app.userbot.my_telegram import MyTelegramError

_HEX32 = re.compile(r'^[0-9a-fA-F]{32}$')
_SENDER_MODES = ('bots', 'selected', 'users', 'all')


def _is_global_admin() -> bool:
    return bool(session.get('logged_in')) and not session.get('clone_id') and not is_clone_instance()


def _deny():
    if not session.get('logged_in'):
        return jsonify({'status': 'error', 'msg': '请先登录后台'}), 401
    return jsonify({'status': 'error', 'msg': '仅主后台管理员可使用小号功能'}), 403


def _ok(**kw):
    return jsonify({'status': 'ok', **kw})


def _err(msg, code=200):
    return jsonify({'status': 'error', 'msg': msg}), code


def _manager():
    from flask import current_app
    if userbot_manager.flask_app is None:
        userbot_manager.flask_app = current_app._get_current_object()
    return userbot_manager


def _rule_dict(r: UserbotSyncRule) -> dict:
    try:
        targets = json.loads(r.target_chat_ids or '[]')
    except ValueError:
        targets = []
    try:
        kws = json.loads(r.filter_keywords or '[]')
    except ValueError:
        kws = []
    return {
        'id': r.id, 'source_chat_id': r.source_chat_id, 'source_title': r.source_title or '',
        'sender_mode': r.sender_mode or 'bots', 'sender_filter': r.sender_filter or '',
        'target_chat_ids': targets, 'include_sender_prefix': bool(r.include_sender_prefix),
        'sender_prefix_style': r.sender_prefix_style or 'newline', 'sync_media': bool(r.sync_media),
        'filter_keywords': kws, 'enabled': bool(r.enabled),
    }


@core_bp.route('/userbot')
def page_userbot():
    if not session.get('logged_in'):
        return redirect('/core')
    if not _is_global_admin():
        return redirect('/core/select_group')
    rules = [_rule_dict(r) for r in UserbotSyncRule.query.order_by(UserbotSyncRule.id.desc()).all()]
    main_groups = [
        {'chat_id': g.chat_id, 'title': g.title or g.chat_id, 'type': g.type}
        for g in BotGroup.query.filter(BotGroup.clone_id.is_(None)).order_by(BotGroup.title).all()
        if g.is_active
    ]
    return render_template('userbot.html', page='userbot', status=_manager().status(),
                           rules=rules, main_groups=main_groups)


@core_bp.route('/api/userbot/status')
def api_userbot_status():
    if not _is_global_admin():
        return _deny()
    return _ok(data=_manager().status())


# -- Step A: api_id / api_hash ---------------------------------------------

@core_bp.route('/api/userbot/apikey/send_code', methods=['POST'])
@limiter.limit('5 per minute')
def api_userbot_apikey_send_code():
    if not _is_global_admin():
        return _deny()
    try:
        phone = normalize_phone((request.json or {}).get('phone'))
        fid = _manager().mt_send_code(phone)
    except (ValueError, MyTelegramError, UserbotError) as e:
        return _err(str(e))
    session['userbot_mt_flow'] = fid
    return _ok(msg='验证码已发送到该账号的 Telegram 应用（来自「Telegram」官方账号，不是短信）')


@core_bp.route('/api/userbot/apikey/verify', methods=['POST'])
@limiter.limit('10 per minute')
def api_userbot_apikey_verify():
    if not _is_global_admin():
        return _deny()
    code = ((request.json or {}).get('code') or '').strip()
    if not code:
        return _err('请输入验证码')
    try:
        api_id, api_hash, _phone = _manager().mt_verify(session.get('userbot_mt_flow') or '', code)
    except (MyTelegramError, UserbotError) as e:
        return _err(str(e))
    _save_api(api_id, api_hash)
    session.pop('userbot_mt_flow', None)
    return _ok(msg='已自动获取 API 并加密保存', api_id=api_id)


@core_bp.route('/api/userbot/apikey/manual', methods=['POST'])
def api_userbot_apikey_manual():
    if not _is_global_admin():
        return _deny()
    d = request.json or {}
    raw_id = str(d.get('api_id') or '').strip()
    api_hash = str(d.get('api_hash') or '').strip()
    if not raw_id.isdigit() or not (3 <= len(raw_id) <= 12):
        return _err('api_id 应为纯数字')
    if not _HEX32.match(api_hash):
        return _err('api_hash 应为 32 位十六进制字符串')
    _save_api(int(raw_id), api_hash.lower())
    return _ok(msg='API 已加密保存', api_id=int(raw_id))


def _save_api(api_id: int, api_hash: str):
    acct = UserbotAccount.query.first() or UserbotAccount(status='none')
    acct.api_id = int(api_id)
    acct.api_hash_enc = encrypt_secret(api_hash)
    db.session.add(acct)
    db.session.commit()


# -- Step B: Telethon login -------------------------------------------------

@core_bp.route('/api/userbot/login/send_code', methods=['POST'])
@limiter.limit('5 per minute')
def api_userbot_login_send_code():
    if not _is_global_admin():
        return _deny()
    acct = UserbotAccount.query.first()
    api_hash = decrypt_secret(acct.api_hash_enc) if acct else None
    if not acct or not acct.api_id or not api_hash:
        return _err('请先完成第一步：获取或填写 api_id / api_hash')
    try:
        phone = normalize_phone((request.json or {}).get('phone'))
        fid = _manager().login_send_code(acct.api_id, api_hash, phone)
    except (ValueError, UserbotError) as e:
        return _err(str(e))
    session['userbot_login_flow'] = fid
    return _ok(msg='登录验证码已发送（一般在 Telegram 应用里收到）')


@core_bp.route('/api/userbot/login/verify_code', methods=['POST'])
@limiter.limit('10 per minute')
def api_userbot_login_verify_code():
    if not _is_global_admin():
        return _deny()
    code = ((request.json or {}).get('code') or '').strip()
    if not code:
        return _err('请输入验证码')
    try:
        info = _manager().login_verify_code(session.get('userbot_login_flow') or '', code)
    except UserbotError as e:
        return _err(str(e))
    if info is None:
        return _ok(need_password=True, msg='该账号开启了两步验证，请输入密码')
    session.pop('userbot_login_flow', None)
    return _ok(msg='小号登录成功', data=_manager().status())


@core_bp.route('/api/userbot/login/verify_password', methods=['POST'])
@limiter.limit('10 per minute')
def api_userbot_login_verify_password():
    if not _is_global_admin():
        return _deny()
    password = (request.json or {}).get('password') or ''
    if not password:
        return _err('请输入两步验证密码')
    try:
        _manager().login_verify_password(session.get('userbot_login_flow') or '', password)
    except UserbotError as e:
        return _err(str(e))
    session.pop('userbot_login_flow', None)
    return _ok(msg='小号登录成功', data=_manager().status())


@core_bp.route('/api/userbot/logout', methods=['POST'])
def api_userbot_logout():
    if not _is_global_admin():
        return _deny()
    forget = bool((request.json or {}).get('forget_api'))
    _manager().logout(forget_api=forget)
    return _ok(msg='已退出并删除小号会话' + ('及 API 信息' if forget else ''))


@core_bp.route('/api/userbot/restart', methods=['POST'])
def api_userbot_restart():
    if not _is_global_admin():
        return _deny()
    m = _manager()
    acct = UserbotAccount.query.first()
    if not acct or not acct.session_enc:
        return _err('还没有登录小号')
    m.restart()
    return _ok(msg='已重新连接小号')


@core_bp.route('/api/userbot/dialogs')
def api_userbot_dialogs():
    if not _is_global_admin():
        return _deny()
    try:
        return _ok(data=_manager().list_dialogs())
    except UserbotError as e:
        return _err(str(e))


# -- Rules ---------------------------------------------------------------------

def _clean_chat_id(v) -> str:
    s = str(v or '').strip()
    return s if re.fullmatch(r'-?\d{3,20}', s) else ''


@core_bp.route('/api/userbot/rules')
def api_userbot_rules():
    if not _is_global_admin():
        return _deny()
    return _ok(data=[_rule_dict(r) for r in UserbotSyncRule.query.order_by(UserbotSyncRule.id.desc()).all()])


@core_bp.route('/api/userbot/rules/save', methods=['POST'])
def api_userbot_rules_save():
    if not _is_global_admin():
        return _deny()
    d = request.json or {}
    src = _clean_chat_id(d.get('source_chat_id'))
    if not src:
        return _err('请选择或填写源群 ID（例如 -1001234567890）')
    raw_targets = d.get('target_chat_ids') or []
    if isinstance(raw_targets, str):
        raw_targets = re.split(r'[\s,，]+', raw_targets)
    targets = []
    for t in raw_targets:
        c = _clean_chat_id(t)
        if c and c != src and c not in targets:
            targets.append(c)
    if not targets:
        return _err('请至少选择一个目标群/频道')
    mode = d.get('sender_mode') if d.get('sender_mode') in _SENDER_MODES else 'bots'
    sender_filter = str(d.get('sender_filter') or '').strip()[:2000]
    if mode == 'selected' and not sender_filter:
        return _err('「指定机器人」模式下请填写机器人用户名或 ID')
    kws = d.get('filter_keywords') or []
    if isinstance(kws, str):
        kws = [k.strip() for k in re.split(r'[,，\n]+', kws) if k.strip()]
    style = d.get('sender_prefix_style') if d.get('sender_prefix_style') in ('newline', 'forward') else 'newline'

    rule = UserbotSyncRule.query.get(int(d['id'])) if str(d.get('id') or '').isdigit() else None
    if rule is None:
        rule = UserbotSyncRule()
        db.session.add(rule)
    rule.source_chat_id = src
    rule.source_title = str(d.get('source_title') or '')[:255]
    rule.sender_mode = mode
    rule.sender_filter = sender_filter
    rule.target_chat_ids = json.dumps(targets)
    rule.include_sender_prefix = bool(d.get('include_sender_prefix'))
    rule.sender_prefix_style = style
    rule.sync_media = bool(d.get('sync_media', True))
    rule.filter_keywords = json.dumps(kws, ensure_ascii=False)
    rule.enabled = bool(d.get('enabled', True))
    db.session.commit()
    _manager().invalidate_rules()
    return _ok(msg='规则已保存', data=_rule_dict(rule))


@core_bp.route('/api/userbot/rules/<int:rid>/delete', methods=['POST'])
def api_userbot_rules_delete(rid):
    if not _is_global_admin():
        return _deny()
    rule = UserbotSyncRule.query.get(rid)
    if rule:
        db.session.delete(rule)
        db.session.commit()
    _manager().invalidate_rules()
    return _ok(msg='规则已删除')


@core_bp.route('/api/userbot/rules/<int:rid>/toggle', methods=['POST'])
def api_userbot_rules_toggle(rid):
    if not _is_global_admin():
        return _deny()
    rule = UserbotSyncRule.query.get(rid)
    if not rule:
        return _err('规则不存在')
    rule.enabled = not rule.enabled
    db.session.commit()
    _manager().invalidate_rules()
    return _ok(enabled=rule.enabled)


@core_bp.route('/api/userbot/logs')
def api_userbot_logs():
    if not _is_global_admin():
        return _deny()
    rows = SyncMessageLog.query.filter_by(via='userbot').order_by(SyncMessageLog.id.desc()).limit(50).all()
    return _ok(data=[{
        'time': r.synced_at.strftime('%m-%d %H:%M:%S') if r.synced_at else '',
        'target': r.target_group_id, 'sender': r.username or (str(r.user_id) if r.user_id else ''),
        'type': r.message_type, 'status': r.status, 'preview': (r.content_preview or '')[:60],
        'error': (r.error_message or '')[:120],
    } for r in rows])
