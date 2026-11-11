from . import db
from datetime import datetime
import json

class BotGroup(db.Model):
    __tablename__ = 'bot_groups'
    id = db.Column(db.Integer, primary_key=True)
    chat_id = db.Column(db.String(50), index=True)
    title = db.Column(db.String(255))
    type = db.Column(db.String(50))
    is_active = db.Column(db.Boolean, default=True)
    config = db.Column(db.Text, default='{}')
    fields_config = db.Column(db.Text)
    last_query_msg_id = db.Column(db.Integer, nullable=True)
    members_last_sync = db.Column(db.DateTime, nullable=True)  # Track last successful member sync
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    clone_id = db.Column(db.Integer, nullable=True)  # None = main bot group, set = clone bot group
    __table_args__ = (
        db.UniqueConstraint('chat_id', 'clone_id', name='_bot_group_chat_clone_uc'),
    )

class GroupUser(db.Model):
    __tablename__ = 'group_users'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('bot_groups.id'), index=True)
    tg_id = db.Column(db.BigInteger)
    profile_data = db.Column(db.Text, default='{}')
    # Custom admin-assigned tags for authenticated users, stored as JSON array of strings.
    # The first tag is also applied as the Telegram nickname tag via setChatMemberTag.
    member_tags = db.Column(db.Text, default='[]')
    # Last tag successfully applied to Telegram (for skip-if-unchanged); empty string = cleared.
    applied_telegram_tag = db.Column(db.String(16), nullable=True)
    # Skip retry marker: 'admin' (bot cannot tag other admins) or 'left' (user departed).
    telegram_tag_skip = db.Column(db.String(16), nullable=True)
    expiration_date = db.Column(db.DateTime, nullable=True)  # Consider adding composite index: (expiration_date, is_banned)
    is_banned = db.Column(db.Boolean, default=False)
    is_muted_permanent = db.Column(db.Boolean, default=False)  # Track if user needs admin to unlock
    mute_reason = db.Column(db.String(255), nullable=True)  # Reason for permanent mute
    checkin_time = db.Column(db.DateTime)
    last_activity = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)  # Track last message activity
    online = db.Column(db.Boolean, default=False)
    is_channel_subscribed = db.Column(db.Boolean, nullable=True)  # None=未检测, True=已订阅, False=未订阅
    join_source = db.Column(db.String(50), nullable=True)  # invite, channel, direct, etc.
    created_at = db.Column(db.DateTime, default=datetime.now)  # Track when user joined the group
    __table_args__ = (db.UniqueConstraint('group_id', 'tg_id', name='_group_user_uc'),)
    
    # Relationship to BotGroup for efficient querying
    group = db.relationship('BotGroup', backref='users', lazy=True)


class AuthSession(db.Model):
    __tablename__ = 'auth_sessions'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.BigInteger, index=True)
    user_name = db.Column(db.String(255), nullable=True)  # Display name of the Telegram user
    session_token = db.Column(db.String(100), unique=True, index=True)
    verification_code = db.Column(db.String(10))
    is_verified = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    expires_at = db.Column(db.DateTime)
    clone_id = db.Column(db.Integer, nullable=True)  # None = main bot admin, set = clone bot admin

class AutoReply(db.Model):
    """自动回复规则"""
    __tablename__ = 'auto_replies'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('bot_groups.id'), index=True)
    trigger_keyword = db.Column(db.String(255), nullable=False)  # 触发关键词
    media_type = db.Column(db.String(20), default='text')  # text, image, video, media
    media_url = db.Column(db.Text, nullable=True)  # 多媒体链接（单个，向后兼容）
    media_urls = db.Column(db.Text, default='[]')  # JSON数组，多个多媒体链接
    content = db.Column(db.Text, nullable=True)  # 富文本内容
    links = db.Column(db.Text, default='[]')  # JSON格式的链接数组
    delete_after = db.Column(db.Integer, default=0)  # 删除上一条消息的时间(秒)，0表示不删除
    remark = db.Column(db.Text, nullable=True)  # 备注
    is_active = db.Column(db.Boolean, default=True)  # 是否启用
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    group = db.relationship('BotGroup', backref='auto_replies', lazy=True)

    def get_media_url_list(self):
        """获取多媒体链接列表，兼容旧数据"""
        import json
        try:
            urls = json.loads(self.media_urls or '[]')
            if urls:
                result = []
                for u in urls:
                    if isinstance(u, dict):
                        url = u.get('url', '')
                        if url:
                            result.append(url)
                    elif u:
                        result.append(u)
                return result
        except (json.JSONDecodeError, TypeError):
            pass
        # 兼容旧数据：从单个 media_url 字段读取
        if self.media_url:
            return [self.media_url]
        return []

    def get_media_url_with_types(self):
        """获取多媒体链接及其类型列表，返回 [(url, type), ...]"""
        import json
        try:
            urls = json.loads(self.media_urls or '[]')
            if urls:
                result = []
                for u in urls:
                    if isinstance(u, dict):
                        url = u.get('url', '')
                        mtype = u.get('type', 'image')
                        if url:
                            result.append((url, mtype))
                    elif u:
                        # 旧格式：使用 media_type 字段作为类型
                        result.append((u, self.media_type if self.media_type in ('image', 'video') else 'image'))
                return result
        except (json.JSONDecodeError, TypeError):
            pass
        if self.media_url:
            return [(self.media_url, self.media_type if self.media_type in ('image', 'video') else 'image')]
        return []


class ScheduledMessage(db.Model):
    """定时消息"""
    __tablename__ = 'scheduled_messages'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('bot_groups.id'), index=True)
    media_type = db.Column(db.String(20), default='text')  # text, image, video, media
    media_url = db.Column(db.Text, nullable=True)  # 多媒体链接（单个，向后兼容）
    media_urls = db.Column(db.Text, default='[]')  # JSON数组，多个多媒体链接
    content = db.Column(db.Text, nullable=True)  # 富文本内容
    links = db.Column(db.Text, default='[]')  # JSON格式的链接数组
    repeat_interval = db.Column(db.Integer, default=0)  # 重复间隔(分钟)，0表示不重复
    delete_previous = db.Column(db.Boolean, default=False)  # 是否删除上一条
    last_message_id = db.Column(db.BigInteger, nullable=True)  # 上一条消息ID，用于删除
    last_message_ids = db.Column(db.Text, nullable=True)  # JSON数组，上一次发送的所有消息ID（媒体组含多条）
    start_time = db.Column(db.DateTime, nullable=True)  # 开始时间
    stop_time = db.Column(db.DateTime, nullable=True)  # 停止时间
    remark = db.Column(db.Text, nullable=True)  # 备注
    auto_pin = db.Column(db.Boolean, default=False)  # 是否自动置顶
    message_thread_id = db.Column(db.Integer, nullable=True)  # 话题ID (用于论坛话题群)
    target_type = db.Column(db.String(20), default='group')  # group, channel
    target_channel_id = db.Column(db.String(50), nullable=True)  # 目标频道ID
    is_active = db.Column(db.Boolean, default=True)  # 是否启用
    last_sent_at = db.Column(db.DateTime, nullable=True)  # 上次发送时间
    next_send_at = db.Column(db.DateTime, nullable=True)  # 下次发送时间
    fail_count = db.Column(db.Integer, default=0)  # 连续发送失败次数（成功后清零；达上限则自动停用）
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    group = db.relationship('BotGroup', backref='scheduled_messages', lazy=True)

    def get_media_url_list(self):
        """获取多媒体链接列表，兼容旧数据"""
        import json
        try:
            urls = json.loads(self.media_urls or '[]')
            if urls:
                result = []
                for u in urls:
                    if isinstance(u, dict):
                        url = u.get('url', '')
                        if url:
                            result.append(url)
                    elif u:
                        result.append(u)
                return result
        except (json.JSONDecodeError, TypeError):
            pass
        # 兼容旧数据：从单个 media_url 字段读取
        if self.media_url:
            return [self.media_url]
        return []

    def get_media_url_with_types(self):
        """获取多媒体链接及其类型列表，返回 [(url, type), ...]"""
        import json
        try:
            urls = json.loads(self.media_urls or '[]')
            if urls:
                result = []
                for u in urls:
                    if isinstance(u, dict):
                        url = u.get('url', '')
                        mtype = u.get('type', 'image')
                        if url:
                            result.append((url, mtype))
                    elif u:
                        # 旧格式：使用 media_type 字段作为类型
                        result.append((u, self.media_type if self.media_type in ('image', 'video') else 'image'))
                return result
        except (json.JSONDecodeError, TypeError):
            pass
        if self.media_url:
            return [(self.media_url, self.media_type if self.media_type in ('image', 'video') else 'image')]
        return []


class StartMessage(db.Model):
    """自定义 /start 消息"""
    __tablename__ = 'start_messages'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('bot_groups.id'), index=True)
    message_type = db.Column(db.String(20), default='user')  # 'user' or 'admin'
    media_type = db.Column(db.String(20), default='text')  # text, image, video
    media_url = db.Column(db.Text, nullable=True)  # 多媒体链接
    content = db.Column(db.Text, nullable=True)  # 富文本内容
    links = db.Column(db.Text, default='[]')  # JSON格式的链接按钮数组
    is_active = db.Column(db.Boolean, default=True)  # 是否启用
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    group = db.relationship('BotGroup', backref='start_messages', lazy=True)


class GroupEntryExitSettings(db.Model):
    """进退群设置"""
    __tablename__ = 'group_entry_exit_settings'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('bot_groups.id'), index=True)
    # 进群验证
    entry_verification_enabled = db.Column(db.Boolean, default=False)
    verification_type = db.Column(db.String(20), default='question')  # question, captcha, emoji, multiple_choice
    verification_question = db.Column(db.Text, nullable=True)
    verification_answer = db.Column(db.Text, nullable=True)
    verification_options = db.Column(db.Text, nullable=True)  # JSON array for multiple choice options
    verification_timeout = db.Column(db.Integer, default=60)  # 验证超时时间(秒)
    # 进群欢迎
    welcome_enabled = db.Column(db.Boolean, default=False)
    welcome_message = db.Column(db.Text, nullable=True)
    welcome_media_type = db.Column(db.String(20), default='text')
    welcome_media_url = db.Column(db.Text, nullable=True)
    # 退群拉黑
    exit_ban_enabled = db.Column(db.Boolean, default=False)
    exit_ban_duration = db.Column(db.Integer, default=0)  # 0=永久
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    group = db.relationship('BotGroup', backref='entry_exit_settings', lazy=True)


class SpamProtection(db.Model):
    """垃圾防护"""
    __tablename__ = 'spam_protection'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('bot_groups.id'), index=True)
    enabled = db.Column(db.Boolean, default=False)
    # 防护规则
    max_messages_per_minute = db.Column(db.Integer, default=10)
    block_links = db.Column(db.Boolean, default=False)
    block_forwards = db.Column(db.Boolean, default=False)
    block_stickers = db.Column(db.Boolean, default=False)
    # 惩罚措施
    punishment_type = db.Column(db.String(20), default='mute')  # mute, kick, ban, mute_permanent (requires admin to unlock)
    punishment_duration = db.Column(db.Integer, default=60)  # 分钟
    # 白名单
    whitelist_users = db.Column(db.Text, default='[]')  # JSON array of user IDs
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    group = db.relationship('BotGroup', backref='spam_protection', lazy=True)


class TimedGroupControl(db.Model):
    """定时开关群"""
    __tablename__ = 'timed_group_control'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('bot_groups.id'), index=True)
    enabled = db.Column(db.Boolean, default=False)
    open_time = db.Column(db.Time, nullable=True)  # 开群时间
    close_time = db.Column(db.Time, nullable=True)  # 关群时间
    timezone = db.Column(db.String(50), default='Asia/Shanghai')
    close_message = db.Column(db.Text, nullable=True)  # 关群提示消息
    open_message = db.Column(db.Text, nullable=True)  # 开群提示消息
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    group = db.relationship('BotGroup', backref='timed_group_control', lazy=True)


class InvitationActivity(db.Model):
    """邀请活动"""
    __tablename__ = 'invitation_activity'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('bot_groups.id'), index=True)
    enabled = db.Column(db.Boolean, default=False)
    reward_points = db.Column(db.Integer, default=10)  # 每邀请一人获得的积分
    minimum_invites = db.Column(db.Integer, default=1)  # 最少邀请人数
    activity_start = db.Column(db.DateTime, nullable=True)
    activity_end = db.Column(db.DateTime, nullable=True)
    description = db.Column(db.Text, nullable=True)
    announce_in_group = db.Column(db.Boolean, default=False)  # 是否在群内公告邀请成功
    link_keyword = db.Column(db.String(255), nullable=True)  # 触发专属链接的关键词（逗号分隔多个）
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    group = db.relationship('BotGroup', backref='invitation_activity', lazy=True)


class InvitationRecord(db.Model):
    """邀请记录 - 跟踪谁邀请了谁"""
    __tablename__ = 'invitation_records'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('bot_groups.id'), index=True)
    inviter_id = db.Column(db.BigInteger, index=True)  # 邀请人的 Telegram ID
    invited_id = db.Column(db.BigInteger, index=True)  # 被邀请人的 Telegram ID
    invited_name = db.Column(db.String(255), nullable=True)  # 被邀请人的名称
    points_awarded = db.Column(db.Integer, default=0)  # 奖励的积分
    created_at = db.Column(db.DateTime, default=datetime.now)
    
    __table_args__ = (
        db.UniqueConstraint('group_id', 'invited_id', name='_group_invited_uc'),  # 每个被邀请人只能被记录一次
    )
    
    group = db.relationship('BotGroup', backref='invitation_records', lazy=True)


class PendingReferral(db.Model):
    """待处理的邀请链接记录 - 通过机器人专属深链接邀请时临时存储"""
    __tablename__ = 'pending_referrals'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.BigInteger, index=True)      # 点击链接的用户 TG ID
    inviter_id = db.Column(db.BigInteger)               # 邀请人 TG ID
    group_id = db.Column(db.Integer, db.ForeignKey('bot_groups.id'), index=True)
    created_at = db.Column(db.DateTime, default=datetime.now)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'group_id', name='_pending_referral_user_group_uc'),
    )

    group = db.relationship('BotGroup', backref='pending_referrals', lazy=True)


class ForcedChannelSubscription(db.Model):
    """强制订阅频道"""
    __tablename__ = 'forced_channel_subscription'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('bot_groups.id'), index=True)
    enabled = db.Column(db.Boolean, default=False)
    channel_id = db.Column(db.String(50), nullable=True)  # 必须订阅的频道ID
    channel_username = db.Column(db.String(255), nullable=True)  # 频道用户名
    check_interval = db.Column(db.Integer, default=3600)  # 检查间隔(秒)
    unsubscribe_action = db.Column(db.String(20), default='kick')  # kick, ban, mute
    verification_message = db.Column(db.Text, nullable=True)
    reward_points = db.Column(db.Integer, default=0)  # 订阅成功奖励积分
    reward_given_user_ids = db.Column(db.Text, default='[]')  # JSON: 已发放奖励的用户tg_id列表
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    group = db.relationship('BotGroup', backref='forced_channel_subscription', lazy=True)


class PointsRule(db.Model):
    """积分规则"""
    __tablename__ = 'points_rules'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('bot_groups.id'), index=True)
    rule_name = db.Column(db.String(255), nullable=False)
    rule_type = db.Column(db.String(50), nullable=False)  # checkin, message, invite, etc.
    points_amount = db.Column(db.Integer, default=1)
    max_daily_points = db.Column(db.Integer, nullable=True)  # NULL = unlimited daily cap
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    group = db.relationship('BotGroup', backref='points_rules', lazy=True)


class PointsAutoReply(db.Model):
    """积分自动回复"""
    __tablename__ = 'points_auto_reply'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('bot_groups.id'), index=True)
    trigger_keyword = db.Column(db.String(255), nullable=False)
    points_cost = db.Column(db.Integer, default=0)  # 消耗积分
    content = db.Column(db.Text, nullable=True)
    media_type = db.Column(db.String(20), default='text')
    media_url = db.Column(db.Text, nullable=True)
    media_urls = db.Column(db.Text, default='[]')  # JSON数组，多个多媒体链接
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    group = db.relationship('BotGroup', backref='points_auto_reply', lazy=True)

    def get_media_url_list(self):
        """获取多媒体链接列表，兼容旧数据"""
        import json
        try:
            urls = json.loads(self.media_urls or '[]')
            if urls:
                result = []
                for u in urls:
                    if isinstance(u, dict):
                        url = u.get('url', '')
                        if url:
                            result.append(url)
                    elif u:
                        result.append(u)
                return result
        except (json.JSONDecodeError, TypeError):
            pass
        if self.media_url:
            return [self.media_url]
        return []

    def get_media_url_with_types(self):
        """获取多媒体链接及其类型列表，返回 [(url, type), ...]"""
        import json
        try:
            urls = json.loads(self.media_urls or '[]')
            if urls:
                result = []
                for u in urls:
                    if isinstance(u, dict):
                        url = u.get('url', '')
                        mtype = u.get('type', 'image')
                        if url:
                            result.append((url, mtype))
                    elif u:
                        result.append((u, self.media_type if self.media_type in ('image', 'video') else 'image'))
                return result
        except (json.JSONDecodeError, TypeError):
            pass
        if self.media_url:
            return [(self.media_url, self.media_type if self.media_type in ('image', 'video') else 'image')]
        return []


class PointsAuction(db.Model):
    """积分竞拍"""
    __tablename__ = 'points_auction'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('bot_groups.id'), index=True)
    item_name = db.Column(db.String(255), nullable=False)
    item_description = db.Column(db.Text, nullable=True)
    starting_price = db.Column(db.Integer, default=100)
    current_bid = db.Column(db.Integer, default=0)
    current_bidder_id = db.Column(db.BigInteger, nullable=True)
    auction_start = db.Column(db.DateTime, nullable=True)
    auction_end = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), default='pending')  # pending, active, ended
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    group = db.relationship('BotGroup', backref='points_auction', lazy=True)


class PointsLog(db.Model):
    """积分日志"""
    __tablename__ = 'points_log'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('bot_groups.id'), index=True)
    user_id = db.Column(db.BigInteger, index=True)
    points_change = db.Column(db.Integer, nullable=False)  # 正数=获得，负数=消耗
    reason = db.Column(db.String(255), nullable=True)
    balance_after = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.now)
    
    group = db.relationship('BotGroup', backref='points_log', lazy=True)


class UserPoints(db.Model):
    """用户积分"""
    __tablename__ = 'user_points'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('bot_groups.id'), index=True)
    user_id = db.Column(db.BigInteger, index=True)
    points_balance = db.Column(db.Integer, default=0)
    current_level_id = db.Column(db.Integer, db.ForeignKey('member_level.id'), nullable=True)  # Track current member level
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    __table_args__ = (db.UniqueConstraint('group_id', 'user_id', name='_group_user_points_uc'),)
    
    group = db.relationship('BotGroup', backref='user_points', lazy=True)
    current_level = db.relationship('MemberLevel', backref='users_at_level', lazy=True)


class PointsExchangeItem(db.Model):
    """积分兑换商品"""
    __tablename__ = 'points_exchange_items'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('bot_groups.id'), index=True)
    item_name = db.Column(db.String(255), nullable=False)
    item_description = db.Column(db.Text, nullable=True)
    points_cost = db.Column(db.Integer, nullable=False, default=100)  # 兑换所需积分
    stock = db.Column(db.Integer, nullable=True)  # NULL = unlimited stock
    redemption_info = db.Column(db.Text, nullable=True)  # 兑换后显示给用户的信息
    is_active = db.Column(db.Boolean, default=True)
    announcement_msg_id = db.Column(db.BigInteger, nullable=True)  # individual "new item" announcement message ID in channel
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    group = db.relationship('BotGroup', backref='exchange_items', lazy=True)


class PointsExchangeRecord(db.Model):
    """积分兑换记录"""
    __tablename__ = 'points_exchange_records'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('bot_groups.id'), index=True)
    user_id = db.Column(db.BigInteger, index=True)
    item_id = db.Column(db.Integer, db.ForeignKey('points_exchange_items.id'), index=True)
    points_spent = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)

    group = db.relationship('BotGroup', backref='exchange_records', lazy=True)
    item = db.relationship('PointsExchangeItem', backref='records', lazy=True)


class GroupLottery(db.Model):
    """群抽奖"""
    __tablename__ = 'group_lottery'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('bot_groups.id'), index=True)
    lottery_name = db.Column(db.String(255), nullable=False)
    lottery_type = db.Column(db.String(50), nullable=False)  # message_count, message_rank
    prize_description = db.Column(db.Text, nullable=True)
    # 发言数量抽奖
    min_messages = db.Column(db.Integer, default=10)  # 最少发言数
    # 发言排行抽奖
    top_n_winners = db.Column(db.Integer, default=3)  # 前N名获奖
    # 通用设置
    start_time = db.Column(db.DateTime, nullable=True)
    end_time = db.Column(db.DateTime, nullable=True)
    announce_channel_id = db.Column(db.String(50), nullable=True)  # 公告频道ID
    winner_ids = db.Column(db.Text, default='[]')  # JSON array of winner user IDs
    status = db.Column(db.String(20), default='pending')  # pending, active, ended
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    group = db.relationship('BotGroup', backref='group_lottery', lazy=True)


class MemberLevel(db.Model):
    """群成员等级"""
    __tablename__ = 'member_level'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('bot_groups.id'), index=True)
    level_name = db.Column(db.String(255), nullable=False)
    required_points = db.Column(db.Integer, default=0)
    permissions = db.Column(db.Text, default='{}')  # JSON格式的权限配置
    badge_emoji = db.Column(db.String(10), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    group = db.relationship('BotGroup', backref='member_level', lazy=True)


class UserNameChange(db.Model):
    """用户改名监控"""
    __tablename__ = 'user_name_change'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('bot_groups.id'), index=True)
    user_id = db.Column(db.BigInteger, index=True)
    old_name = db.Column(db.String(255), nullable=True)
    new_name = db.Column(db.String(255), nullable=True)
    changed_at = db.Column(db.DateTime, default=datetime.now)
    
    group = db.relationship('BotGroup', backref='user_name_change', lazy=True)


class GroupBottomButton(db.Model):
    """群底部按钮"""
    __tablename__ = 'group_bottom_button'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('bot_groups.id'), index=True)
    button_text = db.Column(db.String(255), nullable=False)
    button_url = db.Column(db.Text, nullable=True)
    button_callback = db.Column(db.String(255), nullable=True)  # Callback data for inline button
    trigger_keyword = db.Column(db.String(255), nullable=True)  # 触发关键词，支持逗号分隔多个
    input_field_placeholder = db.Column(db.String(255), nullable=True)  # 输入框提示文案（显示在群输入框）
    button_order = db.Column(db.Integer, default=0)  # 显示顺序
    row_position = db.Column(db.Integer, default=0)  # 行号，同一行的按钮会并排显示
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    group = db.relationship('BotGroup', backref='group_bottom_button', lazy=True)


class SyncGroupMessages(db.Model):
    """同步配置：群成员消息或频道帖（由 chat 类型决定走哪条 handler）。

    One row per (source_group_id, target_group_id) — multi-target via multiple rows.
    """
    __tablename__ = 'sync_group_messages'
    id = db.Column(db.Integer, primary_key=True)
    source_group_id = db.Column(db.Integer, db.ForeignKey('bot_groups.id'), index=True)
    target_group_id = db.Column(db.String(50), nullable=False)  # Telegram chat_id of target
    target_title = db.Column(db.String(255), nullable=True)  # Cached title from getChat
    enabled = db.Column(db.Boolean, default=False)
    sync_media = db.Column(db.Boolean, default=True)  # 是否同步媒体文件
    sync_forwards = db.Column(db.Boolean, default=True)  # 是否同步转发消息
    include_sender_prefix = db.Column(db.Boolean, default=False)  # 群同步是否加 发送者名前缀；默认关
    sender_prefix_style = db.Column(db.String(20), default='newline')  # newline=名字单独一行 | forward=原生转发
    filter_keywords = db.Column(db.Text, default='[]')  # JSON blacklist: messages containing these are not synced
    sync_own_bot_posts = db.Column(db.Boolean, default=True)  # 频道同步：是否同步本机器人自己发的帖子
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        db.UniqueConstraint('source_group_id', 'target_group_id', name='_sync_source_target_uc'),
    )

    group = db.relationship('BotGroup', backref='sync_group_messages', lazy=True, foreign_keys=[source_group_id])


class SyncMessageLog(db.Model):
    """同步消息日志"""
    __tablename__ = 'sync_message_logs'
    id = db.Column(db.Integer, primary_key=True)
    source_group_id = db.Column(db.Integer, db.ForeignKey('bot_groups.id'), index=True)
    target_group_id = db.Column(db.String(50), nullable=False)
    source_message_id = db.Column(db.BigInteger, nullable=True)
    target_message_id = db.Column(db.BigInteger, nullable=True)
    user_id = db.Column(db.BigInteger, nullable=True)  # 消息发送者ID
    username = db.Column(db.String(255), nullable=True)  # 消息发送者用户名
    message_type = db.Column(db.String(20), default='text')  # text, photo, video, document, etc.
    content_preview = db.Column(db.Text, nullable=True)  # 内容预览（前100字符）
    status = db.Column(db.String(20), default='success')  # success, failed, filtered
    error_message = db.Column(db.Text, nullable=True)  # 错误信息（如果同步失败）
    synced_at = db.Column(db.DateTime, default=datetime.now, index=True)
    via = db.Column(db.String(20), nullable=True)  # None = 机器人自身收到；'userbot' = 小号监听转入

    group = db.relationship('BotGroup', backref='sync_message_logs', lazy=True, foreign_keys=[source_group_id])


class UserbotAccount(db.Model):
    """小号（Telegram 用户账号）— 只监听，不发送。secrets are Fernet-encrypted (app.userbot.crypto)."""
    __tablename__ = 'userbot_accounts'
    id = db.Column(db.Integer, primary_key=True)
    api_id = db.Column(db.Integer, nullable=True)
    api_hash_enc = db.Column(db.Text, nullable=True)
    session_enc = db.Column(db.Text, nullable=True)
    phone_masked = db.Column(db.String(32), nullable=True)
    tg_user_id = db.Column(db.BigInteger, nullable=True)
    username = db.Column(db.String(255), nullable=True)
    display_name = db.Column(db.String(255), nullable=True)
    # none | active | invalid | stopped
    status = db.Column(db.String(20), default='none')
    last_error = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)


class UserbotSyncRule(db.Model):
    """小号同步规则：小号所在的源群 → 主机器人发送到目标群/频道。"""
    __tablename__ = 'userbot_sync_rules'
    id = db.Column(db.Integer, primary_key=True)
    source_chat_id = db.Column(db.String(50), nullable=False, index=True)  # Bot-API style, e.g. -100123
    source_title = db.Column(db.String(255), nullable=True)
    # bots = 仅机器人消息（默认）| selected = 指定机器人 | users = 仅用户 | all = 全部消息
    sender_mode = db.Column(db.String(20), default='bots')
    sender_filter = db.Column(db.Text, default='')  # comma/newline separated @usernames or numeric ids
    target_chat_ids = db.Column(db.Text, default='[]')  # JSON list of chat ids
    include_sender_prefix = db.Column(db.Boolean, default=False)
    sender_prefix_style = db.Column(db.String(20), default='newline')
    sync_media = db.Column(db.Boolean, default=True)
    filter_keywords = db.Column(db.Text, default='[]')
    enabled = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)


class OtherSettings(db.Model):
    """其他设置"""
    __tablename__ = 'other_settings'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('bot_groups.id'), index=True)
    # 自动删除消息开关
    auto_delete_join_msg = db.Column(db.Boolean, default=False)  # 自动删除进群消息
    auto_delete_leave_msg = db.Column(db.Boolean, default=False)  # 自动删除退群消息
    auto_delete_promote_msg = db.Column(db.Boolean, default=False)  # 自动删除互推消息
    auto_delete_pin_msg = db.Column(db.Boolean, default=False)  # 自动删除置顶提示消息
    cancel_channel_pin = db.Column(db.Boolean, default=False)  # 取消频道消息置顶
    auto_delete_channel_discussion_msg = db.Column(db.Boolean, default=False)  # 自动删除频道关联留言
    channel_auto_buttons = db.Column(db.Boolean, default=False)  # 是否自动发送频道优惠券按钮
    channel_discussion_keyword_filter = db.Column(db.Text, nullable=True)  # 频道讨论关键词过滤（逗号分隔）
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    group = db.relationship('BotGroup', backref='other_settings', lazy=True)


class BotClone(db.Model):
    """机器人克隆"""
    __tablename__ = 'bot_clones'
    id = db.Column(db.Integer, primary_key=True)
    clone_name = db.Column(db.String(255), nullable=False)  # 克隆机器人名称
    bot_token = db.Column(db.String(512), nullable=False)  # Bot Token (stored encrypted)
    owner_user_id = db.Column(db.BigInteger, nullable=True)  # 克隆机器人拥有者的用户ID
    admin_user_ids = db.Column(db.Text, default='[]')  # 管理员用户ID列表，JSON格式
    is_active = db.Column(db.Boolean, default=True)  # 是否启用
    expiration_date = db.Column(db.DateTime, nullable=True)  # 有效期
    webhook_url = db.Column(db.String(500), nullable=True)  # Webhook URL
    description = db.Column(db.Text, nullable=True)  # 描述
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    def get_bot_token(self) -> str:
        """Return the decrypted bot token (handles both encrypted and legacy plaintext values)."""
        from app.utils import decrypt_token
        return decrypt_token(self.bot_token)

class ChannelForwardRule(db.Model):
    """频道转发规则"""
    __tablename__ = 'channel_forward_rules'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('bot_groups.id'), index=True)
    rule_name = db.Column(db.String(255), nullable=False)
    source_keywords = db.Column(db.Text, nullable=True)  # 逗号/换行分隔关键词
    target_chat_id = db.Column(db.String(50), nullable=False)
    target_thread_id = db.Column(db.Integer, nullable=True)
    forward_mode = db.Column(db.String(20), default='copy')  # copy, forward
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    group = db.relationship('BotGroup', backref='channel_forward_rules', lazy=True)


class ChannelMessageTemplate(db.Model):
    """频道消息模板"""
    __tablename__ = 'channel_message_templates'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('bot_groups.id'), index=True)
    template_name = db.Column(db.String(255), nullable=False)
    trigger_keywords = db.Column(db.Text, nullable=True)  # 逗号/换行分隔关键词
    media_type = db.Column(db.String(20), default='text')
    media_url = db.Column(db.Text, nullable=True)
    content = db.Column(db.Text, nullable=True)
    links = db.Column(db.Text, default='[]')
    send_as_reply = db.Column(db.Boolean, default=True)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    group = db.relationship('BotGroup', backref='channel_message_templates', lazy=True)


class ChannelPostButtonConfig(db.Model):
    """频道帖自动加按钮（编辑原帖 reply_markup，与讨论区优惠券无关）"""
    __tablename__ = 'channel_post_button_configs'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('bot_groups.id'), unique=True, index=True, nullable=False)
    enabled = db.Column(db.Boolean, default=False)  # 总开关
    links = db.Column(db.Text, default='[]')  # JSON: [{text, url, row?, order?}, ...]
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    group = db.relationship('BotGroup', backref='channel_post_button_config', lazy=True, uselist=False)

    def get_links_list(self):
        try:
            data = json.loads(self.links or '[]')
            return data if isinstance(data, list) else []
        except (json.JSONDecodeError, TypeError):
            return []


class ChannelCoupon(db.Model):
    """频道优惠券按钮"""
    __tablename__ = 'channel_coupons'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('bot_groups.id'), index=True)
    coupon_name = db.Column(db.String(255), nullable=False)
    coupon_code = db.Column(db.String(100), nullable=True)
    button_text = db.Column(db.String(255), nullable=True)
    button_url = db.Column(db.Text, nullable=True)
    trigger_keywords = db.Column(db.Text, nullable=True)
    sort_order = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    group = db.relationship('BotGroup', backref='channel_coupons', lazy=True)


class ChannelMessageStats(db.Model):
    """频道消息统计"""
    __tablename__ = 'channel_message_stats'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('bot_groups.id'), index=True)
    channel_chat_id = db.Column(db.String(50), nullable=True)
    channel_message_id = db.Column(db.BigInteger, index=True)
    discussion_chat_id = db.Column(db.String(50), nullable=True)
    discussion_message_id = db.Column(db.BigInteger, nullable=True)
    media_type = db.Column(db.String(20), default='text')
    message_text = db.Column(db.Text, nullable=True)
    matched_rules = db.Column(db.Text, default='[]')
    created_at = db.Column(db.DateTime, default=datetime.now, index=True)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    group = db.relationship('BotGroup', backref='channel_message_stats', lazy=True)


class LotteryMessageCount(db.Model):
    """抽奖消息计数 - 跟踪用户在抽奖期间发送的消息数"""
    __tablename__ = 'lottery_message_count'
    id = db.Column(db.Integer, primary_key=True)
    lottery_id = db.Column(db.Integer, db.ForeignKey('group_lottery.id'), index=True)
    group_id = db.Column(db.Integer, db.ForeignKey('bot_groups.id'), index=True)
    user_id = db.Column(db.BigInteger, nullable=False)  # Telegram user ID
    message_count = db.Column(db.Integer, default=0)  # Number of messages sent
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    __table_args__ = (
        db.UniqueConstraint('lottery_id', 'user_id', name='_lottery_user_uc'),
        db.Index('ix_lottery_message_count_lookup', 'lottery_id', 'group_id', 'user_id'),
    )
    
    lottery = db.relationship('GroupLottery', backref='message_counts', lazy=True)
    group = db.relationship('BotGroup', backref='lottery_message_counts', lazy=True)


class InactiveUserSettings(db.Model):
    """不活跃用户设置"""
    __tablename__ = 'inactive_user_settings'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('bot_groups.id'), index=True)
    enabled = db.Column(db.Boolean, default=False)
    inactivity_days = db.Column(db.Integer, default=30)  # 不活跃天数阈值
    action_type = db.Column(db.String(20), default='kick')  # kick, ban, mute, mute_permanent (requires admin to unlock)
    check_interval = db.Column(db.Integer, default=86400)  # 检查间隔(秒)，默认24小时
    warning_enabled = db.Column(db.Boolean, default=False)  # 是否提前警告
    warning_days = db.Column(db.Integer, default=7)  # 提前警告天数
    warning_message = db.Column(db.Text, nullable=True)  # 警告消息
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    group = db.relationship('BotGroup', backref='inactive_user_settings', lazy=True)


class KeywordFilter(db.Model):
    """关键词过滤"""
    __tablename__ = 'keyword_filter'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('bot_groups.id'), index=True)
    keyword = db.Column(db.String(255), nullable=False)
    filter_type = db.Column(db.String(20), default='blacklist')  # blacklist, whitelist
    match_type = db.Column(db.String(20), default='contains')  # contains, exact, regex
    action = db.Column(db.String(20), default='delete')  # delete, warn, mute, kick, ban
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    group = db.relationship('BotGroup', backref='keyword_filters', lazy=True)


class MessageStatistics(db.Model):
    """消息统计"""
    __tablename__ = 'message_statistics'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('bot_groups.id'), index=True)
    user_id = db.Column(db.BigInteger, index=True)
    date = db.Column(db.Date, index=True, default=datetime.now)
    message_count = db.Column(db.Integer, default=0)
    text_count = db.Column(db.Integer, default=0)
    photo_count = db.Column(db.Integer, default=0)
    video_count = db.Column(db.Integer, default=0)
    sticker_count = db.Column(db.Integer, default=0)
    document_count = db.Column(db.Integer, default=0)
    voice_count = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    __table_args__ = (
        db.UniqueConstraint('group_id', 'user_id', 'date', name='_group_user_date_uc'),
    )
    
    group = db.relationship('BotGroup', backref='message_statistics', lazy=True)


class GroupVote(db.Model):
    """群投票"""
    __tablename__ = 'group_vote'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('bot_groups.id'), index=True)
    title = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    options = db.Column(db.Text, default='[]')  # JSON array of options
    vote_type = db.Column(db.String(20), default='single')  # single, multiple
    max_choices = db.Column(db.Integer, default=1)  # 多选时最多选择数
    is_anonymous = db.Column(db.Boolean, default=False)
    allow_revote = db.Column(db.Boolean, default=True)  # 允许改投
    start_time = db.Column(db.DateTime, nullable=True)
    end_time = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), default='pending')  # pending, active, ended
    message_id = db.Column(db.BigInteger, nullable=True)  # 投票消息ID
    created_by = db.Column(db.BigInteger, nullable=True)  # 创建者用户ID
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    group = db.relationship('BotGroup', backref='group_votes', lazy=True)


class VoteRecord(db.Model):
    """投票记录"""
    __tablename__ = 'vote_record'
    id = db.Column(db.Integer, primary_key=True)
    vote_id = db.Column(db.Integer, db.ForeignKey('group_vote.id'), index=True)
    user_id = db.Column(db.BigInteger, index=True)
    choices = db.Column(db.Text, default='[]')  # JSON array of option indices
    voted_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    __table_args__ = (
        db.UniqueConstraint('vote_id', 'user_id', name='_vote_user_uc'),
    )
    
    vote = db.relationship('GroupVote', backref='vote_records', lazy=True)


class QuizGame(db.Model):
    """问答游戏"""
    __tablename__ = 'quiz_game'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('bot_groups.id'), index=True)
    question = db.Column(db.Text, nullable=False)
    answers = db.Column(db.Text, default='[]')  # JSON array of answers
    correct_answer_index = db.Column(db.Integer, nullable=False)
    explanation = db.Column(db.Text, nullable=True)  # 答案解析
    points_reward = db.Column(db.Integer, default=10)  # 答对奖励积分
    time_limit = db.Column(db.Integer, default=60)  # 答题时限(秒)
    difficulty = db.Column(db.String(20), default='medium')  # easy, medium, hard
    category = db.Column(db.String(50), nullable=True)  # 题目分类
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    group = db.relationship('BotGroup', backref='quiz_games', lazy=True)


class QuizSession(db.Model):
    """问答会话"""
    __tablename__ = 'quiz_session'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('bot_groups.id'), index=True)
    quiz_id = db.Column(db.Integer, db.ForeignKey('quiz_game.id'), index=True)
    message_id = db.Column(db.BigInteger, nullable=True)  # 问题消息ID
    start_time = db.Column(db.DateTime, default=datetime.now)
    end_time = db.Column(db.DateTime, nullable=True)
    status = db.Column(db.String(20), default='active')  # active, ended
    created_at = db.Column(db.DateTime, default=datetime.now)
    
    group = db.relationship('BotGroup', backref='quiz_sessions', lazy=True)
    quiz = db.relationship('QuizGame', backref='quiz_sessions', lazy=True)


class QuizAnswer(db.Model):
    """问答答案记录"""
    __tablename__ = 'quiz_answer'
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey('quiz_session.id'), index=True)
    user_id = db.Column(db.BigInteger, index=True)
    answer_index = db.Column(db.Integer, nullable=False)
    is_correct = db.Column(db.Boolean, default=False)
    points_awarded = db.Column(db.Integer, default=0)
    answered_at = db.Column(db.DateTime, default=datetime.now)
    
    __table_args__ = (
        db.UniqueConstraint('session_id', 'user_id', name='_session_user_uc'),
    )
    
    session = db.relationship('QuizSession', backref='quiz_answers', lazy=True)


class RedPacket(db.Model):
    """红包"""
    __tablename__ = 'red_packet'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('bot_groups.id'), index=True)
    creator_id = db.Column(db.BigInteger, index=True)  # 发红包的用户
    packet_type = db.Column(db.String(20), default='random')  # random=拼手气, equal=普通
    total_points = db.Column(db.Integer, nullable=False)  # 总积分
    packet_count = db.Column(db.Integer, nullable=False)  # 红包数量
    remaining_count = db.Column(db.Integer, nullable=False)  # 剩余数量
    remaining_points = db.Column(db.Integer, nullable=False)  # 剩余积分
    message = db.Column(db.Text, nullable=True)  # 红包祝福语
    message_id = db.Column(db.BigInteger, nullable=True)  # 红包消息ID
    expire_time = db.Column(db.DateTime, nullable=True)  # 过期时间
    status = db.Column(db.String(20), default='active')  # active, expired, claimed
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    group = db.relationship('BotGroup', backref='red_packets', lazy=True)


class RedPacketClaim(db.Model):
    """红包领取记录"""
    __tablename__ = 'red_packet_claim'
    id = db.Column(db.Integer, primary_key=True)
    packet_id = db.Column(db.Integer, db.ForeignKey('red_packet.id'), index=True)
    user_id = db.Column(db.BigInteger, index=True)
    points_received = db.Column(db.Integer, nullable=False)
    claimed_at = db.Column(db.DateTime, default=datetime.now)
    
    __table_args__ = (
        db.UniqueConstraint('packet_id', 'user_id', name='_packet_user_uc'),
    )
    
    packet = db.relationship('RedPacket', backref='red_packet_claims', lazy=True)


class AdminActionLog(db.Model):
    """管理员操作日志"""
    __tablename__ = 'admin_action_log'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('bot_groups.id'), index=True)
    admin_id = db.Column(db.BigInteger, nullable=False)
    admin_name = db.Column(db.String(255))
    action_type = db.Column(db.String(50), nullable=False)  # kick, ban, mute, settings_change, etc.
    target_user_id = db.Column(db.BigInteger, nullable=True)
    target_user_name = db.Column(db.String(255), nullable=True)
    details = db.Column(db.Text, nullable=True)  # JSON 格式的详细信息
    created_at = db.Column(db.DateTime, default=datetime.now, index=True)
    
    group = db.relationship('BotGroup', backref='admin_action_logs', lazy=True)


class GroupWarning(db.Model):
    """群组用户警告记录（/warn 与关键词 action=warn 落库计次）

    当前无后台「警告阈值 → 自动处罚」配置；仅持久化并在回复中展示累计次数。
    自动升级处罚留待后续 UI/设置接入（见 warning_service.resolve_escalation）。
    """
    __tablename__ = 'group_warnings'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('bot_groups.id'), index=True, nullable=False)
    user_id = db.Column(db.BigInteger, nullable=False, index=True)  # Telegram user ID
    admin_id = db.Column(db.BigInteger, nullable=True)  # 发出警告的管理员；关键词自动警告可为 None
    reason = db.Column(db.Text, nullable=True)
    source = db.Column(db.String(20), default='command')  # command, keyword
    created_at = db.Column(db.DateTime, default=datetime.now, index=True)

    __table_args__ = (
        db.Index('ix_group_warnings_group_user', 'group_id', 'user_id'),
    )

    group = db.relationship('BotGroup', backref='group_warnings', lazy=True)


class GroupMember(db.Model):
    """群内成员（从 Telegram 同步）"""
    __tablename__ = 'group_members'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('bot_groups.id'), index=True)
    user_id = db.Column(db.BigInteger, nullable=False, index=True)  # Telegram user ID
    username = db.Column(db.String(255), nullable=True)
    first_name = db.Column(db.String(255), nullable=True)
    last_name = db.Column(db.String(255), nullable=True)
    status = db.Column(db.String(50), default='member')  # creator, administrator, member, restricted, left, kicked
    is_bot = db.Column(db.Boolean, default=False)
    joined_at = db.Column(db.DateTime, nullable=True)  # 加入时间（如果可获取）
    synced_at = db.Column(db.DateTime, default=datetime.now)  # 最后同步时间
    created_at = db.Column(db.DateTime, default=datetime.now)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)
    
    __table_args__ = (
        db.UniqueConstraint('group_id', 'user_id', name='_group_member_uc'),
    )
    
    group = db.relationship('BotGroup', backref='group_members', lazy=True)


class SystemConfig(db.Model):
    __tablename__ = 'system_config'
    id = db.Column(db.Integer, primary_key=True)
    key_name = db.Column(db.String(50), unique=True, nullable=False)
    value = db.Column(db.Text, nullable=False)

    @classmethod
    def get_value(cls, key_name, default_value=""):
        from app import db as _db
        from sqlalchemy.exc import InternalError, OperationalError, ProgrammingError
        try:
            config = cls.query.filter_by(key_name=key_name).first()
            return config.value if config else default_value
        except (InternalError, OperationalError, ProgrammingError):
            _db.session.rollback()
            return default_value

    @classmethod
    def _repair_extra_not_null_columns(cls, _db, _logger):
        """
        Drop NOT NULL from any system_config columns not known to the current ORM model.

        Older deployments may have created this table with extra NOT NULL columns (e.g. a
        legacy ``key`` column) that cause INSERTs to fail because the model only supplies
        id, key_name, and value.  This helper is idempotent and safe to call at runtime.

        Returns True if at least one column was successfully repaired, False otherwise.
        """
        import re
        from sqlalchemy import inspect as sa_inspect, text as sa_text
        if _db.engine.dialect.name != 'postgresql':
            return False
        known_cols = {'id', 'key_name', 'value'}
        repaired = False
        try:
            inspector = sa_inspect(_db.engine)
            pk_cols = set(inspector.get_pk_constraint('system_config').get('constrained_columns', []))
            for col in inspector.get_columns('system_config'):
                if col['name'] in known_cols or col.get('nullable', True):
                    continue
                col_name = col['name']
                if col_name in pk_cols:
                    continue  # primary key columns cannot have NOT NULL dropped
                # Validate column name before interpolating into DDL to prevent SQL injection.
                # ALTER COLUMN does not support parameterised identifiers, so regex guard is
                # the standard approach here.
                if not re.match(r'^[a-zA-Z0-9_]+$', col_name):
                    _logger.warning("system_config: skipping unsafe column name %r", col_name)
                    continue
                try:
                    with _db.engine.connect() as conn:
                        conn.execute(sa_text(
                            f'ALTER TABLE system_config ALTER COLUMN "{col_name}" DROP NOT NULL'
                        ))
                        conn.commit()
                    _logger.info("system_config: dropped NOT NULL on legacy column %r", col_name)
                    repaired = True
                except Exception as alter_err:
                    _logger.warning(
                        "system_config: could not drop NOT NULL on column %r: %s", col_name, alter_err
                    )
        except Exception as inspect_err:
            _logger.warning("system_config: schema inspection failed: %s", inspect_err)
        return repaired

    @classmethod
    def set_value(cls, key_name, value):
        from app import db as _db
        from sqlalchemy.exc import InternalError, OperationalError, ProgrammingError, IntegrityError
        import logging
        _logger = logging.getLogger(__name__)
        try:
            with _db.session.no_autoflush:
                obj = cls.query.filter_by(key_name=key_name).first()
            if obj:
                obj.value = value
            else:
                # Use a savepoint so that a NOT NULL violation on a legacy column only
                # rolls back this single insert, leaving the rest of the session intact.
                try:
                    with _db.session.begin_nested():
                        _db.session.add(cls(key_name=key_name, value=value))
                except IntegrityError as insert_err:
                    err_lower = str(insert_err).lower()
                    if 'notnullviolation' in err_lower or 'not null' in err_lower:
                        _logger.warning(
                            "system_config: NOT NULL violation on insert for key %r — "
                            "repairing legacy schema and retrying",
                            key_name,
                        )
                        repaired = cls._repair_extra_not_null_columns(_db, _logger)
                        if repaired:
                            # After repairing, add the row to the outer session for the caller's commit.
                            _db.session.add(cls(key_name=key_name, value=value))
                        else:
                            _logger.error(
                                "system_config: schema repair failed; key %r will not be saved",
                                key_name,
                            )
                    else:
                        raise
        except (InternalError, OperationalError, ProgrammingError, IntegrityError) as e:
            _db.session.rollback()
            _logger.warning("system_config table unavailable (%s), attempting db.create_all() and retrying", e)
            # Table may not exist yet — create it and retry once
            try:
                _db.create_all()
                with _db.session.no_autoflush:
                    obj = cls.query.filter_by(key_name=key_name).first()
                if obj:
                    obj.value = value
                else:
                    _db.session.add(cls(key_name=key_name, value=value))
            except Exception as retry_err:
                _db.session.rollback()
                _logger.error("Failed to create system_config table and retry set_value: %s", retry_err)


class GroupPluginSettings(db.Model):
    """每个群组可独立开启/关闭的插件设置。

    plugin_name 对应各功能模块的 PLUGIN_META['name']。
    若某群组没有对应记录，则使用插件注册表的 default_enabled
    （多数插件默认开；sync 等显式 default_enabled=False 的默认关）。
    """
    __tablename__ = 'group_plugin_settings'
    id = db.Column(db.Integer, primary_key=True)
    group_id = db.Column(db.Integer, db.ForeignKey('bot_groups.id'), index=True, nullable=False)
    plugin_name = db.Column(db.String(50), nullable=False)
    enabled = db.Column(db.Boolean, default=True, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.now, onupdate=datetime.now)

    __table_args__ = (
        db.UniqueConstraint('group_id', 'plugin_name', name='_group_plugin_uc'),
    )

    group = db.relationship('BotGroup', backref='plugin_settings', lazy=True)

    @classmethod
    def is_enabled(cls, group_id: int, plugin_name: str) -> bool:
        """Return True if the plugin is enabled for *group_id*.

        When no setting row exists, uses registry default_enabled
        (True for unknown plugins).
        """
        setting = cls.query.filter_by(
            group_id=group_id, plugin_name=plugin_name
        ).first()
        if setting is not None:
            return setting.enabled
        from app.plugins import get_plugin_default_enabled
        return get_plugin_default_enabled(plugin_name)

    @classmethod
    def set_enabled(cls, db_session, group_id: int, plugin_name: str, enabled: bool):
        """Upsert the enabled state for a plugin in a group."""
        setting = cls.query.filter_by(
            group_id=group_id, plugin_name=plugin_name
        ).first()
        if setting is None:
            setting = cls(group_id=group_id, plugin_name=plugin_name, enabled=enabled)
            db_session.add(setting)
        else:
            setting.enabled = enabled
        db_session.commit()
        return setting


class UserReport(db.Model):
    __tablename__ = 'user_reports'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.BigInteger, nullable=False)
    submitter_id = db.Column(db.BigInteger, nullable=True)
    fault_time = db.Column(db.String(100))
    fault_desc = db.Column(db.Text)
    process_result = db.Column(db.Text)
    photo_file_id = db.Column(db.String(255))
    channel_msg_id = db.Column(db.Integer, nullable=True)
    channel_msg_deleted = db.Column(db.Boolean, default=False, nullable=False, server_default='0')
    status = db.Column(db.String(20), default='pending')
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    # Stores all dynamic question answers as JSON: [{"question": "...", "answer": "..."}, ...]
    answers = db.Column(db.Text, nullable=True)


DEFAULT_FIELDS = [
    {"key": "name", "label": "昵称", "type": "text"},
    {"key": "region", "label": "地区", "type": "select", "options": ["福田","南山"]},
]

DEFAULT_SYSTEM = {
    "checkin_open": True, "checkin_cmd": "打卡", 
    "query_open": True, "query_cmd": "查询", # 🆕 普通查询开关
    "query_filter_open": True,             # 🆕 筛选查询开关
    "checkin_del_time": 30, 
    "query_del_time": 60,
    "page_size": 10,
    "auto_like": True, "like_emoji": "❤",  # Telegram reaction form (no FE0F); ❤️ is normalized at runtime
    # Auto-apply Telegram nickname member tag (setChatMemberTag) for authenticated users
    "auto_member_tag": True,
    "default_member_tag": "认证",  # used when user has no custom member_tags; max 16 chars, no emoji
    "auto_reply_open": True,  # 自动回复开关
    "scheduled_msg_open": True,  # 定时消息开关
    "start_msg_open": True,  # /start 消息开关
    "push_channel_id": "",
    "msg_checkin_success": "✅ <b>打卡成功！</b>", 
    "msg_not_registered": "⚠️ <b>未认证用户</b>",
    "msg_repeat_checkin": "🔄 <b>今天已打卡</b>", 
    "msg_query_header": "🔍 <b>今日在线用户：</b>\n",
    "msg_filter_header": "🔍 <b>筛选结果：</b>\n",
    "msg_expired_ban": "⛔️ <b>您的认证已过期，已被暂时禁言。请联系管理员续费。</b>",
    "msg_private_start": "👋 你好！我是散修群管机器人。",  # 私聊 /start 消息
    "template": "{onlineEmoji} {昵称} | {地区}",
    "push_template": "<b>👤 名片推送</b>\n昵称：{昵称}\n<a href='tg://user?id={tg_id}'>联系我</a>",
    "auto_push_on_add": False,  # 添加认证用户时自动推送
    "custom_buttons": "[]", # 🆕 初始化为空数组
    # 积分签到（独立于认证用户打卡）
    "signin_open": False, "signin_cmd": "签到",
    "signin_del_time": 30,
    "msg_signin_success": "🎉 <b>签到成功！获得 {积分} 积分，当前余额：{余额}</b>",
    "msg_repeat_signin": "🔄 <b>今日已签到，当前余额：{余额}</b>",
    # 积分商城总开关（兑换 + 竞拍）
    "points_shop_enabled": True,     # 关闭后群成员无法使用兑换/竞拍
    # 积分兑换频道商城
    "exchange_channel_id": "",       # 展示商品列表的频道ID
    "exchange_catalog_msg_id": None, # 已发布的商品目录消息ID（用于编辑更新）
    "exchange_command_word": "兑换",  # 群内触发兑换商城的命令词
    # 积分竞拍频道
    "auction_channel_id": "",        # 竞拍通知推送频道ID
    "auction_command_word": "竞拍",   # 群内触发查看竞拍的命令词
    # 红包设置
    "red_packet_enabled": True,      # 是否允许群成员发红包
    "red_packet_max_count": 50,      # 单次红包最多个数
    "red_packet_min_total": 1,       # 单次红包最少总积分
    "red_packet_max_total": 0,       # 单次红包最多总积分（0=不限）
    "red_packet_expire_hours": 24,   # 红包有效期（小时）
}
