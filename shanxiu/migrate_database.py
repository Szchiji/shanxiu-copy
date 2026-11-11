"""
Database migration script.
Run this script to add missing columns to existing tables before starting the application.

Usage:
    python migrate_database.py
"""
from app import create_app, db
from sqlalchemy import text
import re
import sys

app = create_app()


def run_migrations():
    """Add missing columns to existing tables."""
    alter_statements = [
        "ALTER TABLE bot_groups ADD COLUMN last_query_msg_id INTEGER",
        "ALTER TABLE group_users ADD COLUMN expiration_date TIMESTAMP",
        "ALTER TABLE group_users ADD COLUMN is_banned BOOLEAN DEFAULT FALSE",
        "ALTER TABLE group_users ADD COLUMN IF NOT EXISTS is_muted_permanent BOOLEAN DEFAULT FALSE",
        "ALTER TABLE group_users ADD COLUMN IF NOT EXISTS mute_reason VARCHAR(255)",
        "ALTER TABLE group_users ADD COLUMN last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        "ALTER TABLE group_users ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        # Custom member tags for authenticated users (JSON array of strings)
        "ALTER TABLE group_users ADD COLUMN IF NOT EXISTS member_tags TEXT DEFAULT '[]'",
        # Last Telegram nickname tag applied via setChatMemberTag
        "ALTER TABLE group_users ADD COLUMN IF NOT EXISTS applied_telegram_tag VARCHAR(16)",
        # Skip setChatMemberTag retries for admins / departed users
        "ALTER TABLE group_users ADD COLUMN IF NOT EXISTS telegram_tag_skip VARCHAR(16)",
        # Auto replies table columns
        "ALTER TABLE auto_replies ADD COLUMN group_id INTEGER REFERENCES bot_groups(id)",
        "CREATE INDEX IF NOT EXISTS ix_auto_replies_group_id ON auto_replies(group_id)",
        "ALTER TABLE auto_replies ADD COLUMN trigger_keyword VARCHAR(255) NOT NULL DEFAULT ''",
        "ALTER TABLE auto_replies ADD COLUMN media_type VARCHAR(20) DEFAULT 'text'",
        "ALTER TABLE auto_replies ADD COLUMN media_url TEXT",
        "ALTER TABLE auto_replies ADD COLUMN content TEXT",
        "ALTER TABLE auto_replies ADD COLUMN links TEXT DEFAULT '[]'",
        "ALTER TABLE auto_replies ADD COLUMN delete_after INTEGER DEFAULT 0",
        "ALTER TABLE auto_replies ADD COLUMN remark TEXT",
        "ALTER TABLE auto_replies ADD COLUMN is_active BOOLEAN DEFAULT TRUE",
        "ALTER TABLE auto_replies ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        "ALTER TABLE auto_replies ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        # Auto replies: multiple media URLs
        "ALTER TABLE auto_replies ADD COLUMN IF NOT EXISTS media_urls TEXT DEFAULT '[]'",
        # Scheduled messages table columns
        "ALTER TABLE scheduled_messages ADD COLUMN group_id INTEGER REFERENCES bot_groups(id)",
        "CREATE INDEX IF NOT EXISTS ix_scheduled_messages_group_id ON scheduled_messages(group_id)",
        "ALTER TABLE scheduled_messages ADD COLUMN media_type VARCHAR(20) DEFAULT 'text'",
        "ALTER TABLE scheduled_messages ADD COLUMN media_url TEXT",
        "ALTER TABLE scheduled_messages ADD COLUMN content TEXT",
        "ALTER TABLE scheduled_messages ADD COLUMN links TEXT DEFAULT '[]'",
        "ALTER TABLE scheduled_messages ADD COLUMN repeat_interval INTEGER DEFAULT 0",
        "ALTER TABLE scheduled_messages ADD COLUMN delete_previous BOOLEAN DEFAULT FALSE",
        "ALTER TABLE scheduled_messages ADD COLUMN last_message_id BIGINT",
        "ALTER TABLE scheduled_messages ADD COLUMN start_time TIMESTAMP",
        "ALTER TABLE scheduled_messages ADD COLUMN stop_time TIMESTAMP",
        "ALTER TABLE scheduled_messages ADD COLUMN remark TEXT",
        "ALTER TABLE scheduled_messages ADD COLUMN auto_pin BOOLEAN DEFAULT FALSE",
        "ALTER TABLE scheduled_messages ADD COLUMN IF NOT EXISTS message_thread_id INTEGER NULL",
        "ALTER TABLE scheduled_messages ADD COLUMN IF NOT EXISTS target_type VARCHAR(20) DEFAULT 'group'",
        "ALTER TABLE scheduled_messages ADD COLUMN IF NOT EXISTS target_channel_id VARCHAR(50)",
        "ALTER TABLE scheduled_messages ADD COLUMN is_active BOOLEAN DEFAULT TRUE",
        "ALTER TABLE scheduled_messages ADD COLUMN last_sent_at TIMESTAMP",
        "ALTER TABLE scheduled_messages ADD COLUMN IF NOT EXISTS next_send_at TIMESTAMP",
        "ALTER TABLE scheduled_messages ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        "ALTER TABLE scheduled_messages ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        # Scheduled messages: multiple media URLs
        "ALTER TABLE scheduled_messages ADD COLUMN IF NOT EXISTS media_urls TEXT DEFAULT '[]'",
        # Scheduled messages: all sent message IDs for delete_previous (media group support)
        "ALTER TABLE scheduled_messages ADD COLUMN IF NOT EXISTS last_message_ids TEXT",
        # Scheduled messages: consecutive send failure counter
        "ALTER TABLE scheduled_messages ADD COLUMN IF NOT EXISTS fail_count INTEGER DEFAULT 0",
        # Points auto reply: multiple media URLs
        "ALTER TABLE points_auto_reply ADD COLUMN IF NOT EXISTS media_urls TEXT DEFAULT '[]'",
        # Start messages table columns
        "ALTER TABLE start_messages ADD COLUMN group_id INTEGER REFERENCES bot_groups(id)",
        "CREATE INDEX IF NOT EXISTS ix_start_messages_group_id ON start_messages(group_id)",
        "ALTER TABLE start_messages ADD COLUMN message_type VARCHAR(20) DEFAULT 'user'",
        "ALTER TABLE start_messages ADD COLUMN media_type VARCHAR(20) DEFAULT 'text'",
        "ALTER TABLE start_messages ADD COLUMN media_url TEXT",
        "ALTER TABLE start_messages ADD COLUMN content TEXT",
        "ALTER TABLE start_messages ADD COLUMN links TEXT DEFAULT '[]'",
        "ALTER TABLE start_messages ADD COLUMN is_active BOOLEAN DEFAULT TRUE",
        "ALTER TABLE start_messages ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        "ALTER TABLE start_messages ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
        # Group bottom button table columns
        "ALTER TABLE group_bottom_button ADD COLUMN trigger_keyword VARCHAR(255) NULL",
        "ALTER TABLE group_bottom_button ADD COLUMN row_position INTEGER DEFAULT 0",
        "ALTER TABLE group_bottom_button ADD COLUMN input_field_placeholder VARCHAR(255) NULL",
        # Bot clones table columns
        "ALTER TABLE bot_clones ADD COLUMN owner_user_id BIGINT",
        "ALTER TABLE bot_clones ADD COLUMN admin_user_ids TEXT DEFAULT '[]'",
        # User points table columns
        "ALTER TABLE user_points ADD COLUMN current_level_id INTEGER REFERENCES member_level(id)",
        # GroupMember: Set joined_at for existing records if NULL
        "UPDATE group_members SET joined_at = COALESCE(created_at, synced_at) WHERE joined_at IS NULL",
        # BotGroup: Add members_last_sync column
        "ALTER TABLE bot_groups ADD COLUMN IF NOT EXISTS members_last_sync TIMESTAMP NULL",
        # BotGroup: Add clone_id to track which bot (main or clone) owns each group
        "ALTER TABLE bot_groups ADD COLUMN IF NOT EXISTS clone_id INTEGER NULL",
        # AuthSession: Add clone_id to track which clone a login session belongs to
        "ALTER TABLE auth_sessions ADD COLUMN IF NOT EXISTS clone_id INTEGER NULL",
        # AuthSession: Add user_name to display logged-in user identity
        "ALTER TABLE auth_sessions ADD COLUMN IF NOT EXISTS user_name VARCHAR(255) NULL",
        # BotGroup: Drop old unique index on chat_id and add composite unique constraint
        "ALTER TABLE bot_groups DROP CONSTRAINT IF EXISTS bot_groups_chat_id_key",
        "ALTER TABLE bot_groups DROP INDEX IF EXISTS ix_bot_groups_chat_id",
        "CREATE UNIQUE INDEX IF NOT EXISTS _bot_group_chat_clone_uc ON bot_groups(chat_id, clone_id)",
        # PointsExchangeItem / PointsExchangeRecord tables (created via db.create_all, listed here for clarity)
        "CREATE TABLE IF NOT EXISTS points_exchange_items (id INTEGER PRIMARY KEY AUTOINCREMENT, group_id INTEGER REFERENCES bot_groups(id), item_name VARCHAR(255) NOT NULL, item_description TEXT, points_cost INTEGER NOT NULL DEFAULT 100, stock INTEGER, redemption_info TEXT, is_active BOOLEAN DEFAULT TRUE, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "CREATE INDEX IF NOT EXISTS ix_points_exchange_items_group_id ON points_exchange_items(group_id)",
        "CREATE TABLE IF NOT EXISTS points_exchange_records (id INTEGER PRIMARY KEY AUTOINCREMENT, group_id INTEGER REFERENCES bot_groups(id), user_id BIGINT, item_id INTEGER REFERENCES points_exchange_items(id), points_spent INTEGER NOT NULL, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "CREATE INDEX IF NOT EXISTS ix_points_exchange_records_group_id ON points_exchange_records(group_id)",
        "CREATE INDEX IF NOT EXISTS ix_points_exchange_records_user_id ON points_exchange_records(user_id)",
        # PointsExchangeItem: Add announcement_msg_id to track per-item channel announcement
        "ALTER TABLE points_exchange_items ADD COLUMN IF NOT EXISTS announcement_msg_id BIGINT NULL",
        # PointsRule: Add max_daily_points to cap how many points a user can earn per day from a rule
        "ALTER TABLE points_rules ADD COLUMN IF NOT EXISTS max_daily_points INTEGER NULL",
        # system_config: Ensure id column exists (older deployments may have created the table without it)
        "ALTER TABLE system_config ADD COLUMN IF NOT EXISTS id SERIAL",
        # system_config: Ensure key_name and value columns exist (older deployments may be missing them)
        "ALTER TABLE system_config ADD COLUMN IF NOT EXISTS key_name VARCHAR(50) DEFAULT ''",
        "ALTER TABLE system_config ADD COLUMN IF NOT EXISTS value TEXT DEFAULT ''",
        # system_config: Move the primary key from the legacy 'key' column to the new 'id'
        # column.  Older deployments created this table with 'key VARCHAR PRIMARY KEY'.
        # PostgreSQL forbids DROP NOT NULL on a primary-key column, so we must first drop
        # the old PK constraint and make 'id' the primary key before we can make 'key'
        # nullable in the next step.  Both statements are caught-and-skipped if already
        # applied (e.g. PK already on 'id') or not applicable (SQLite).
        "ALTER TABLE system_config DROP CONSTRAINT IF EXISTS system_config_pkey",
        "ALTER TABLE system_config ADD PRIMARY KEY (id)",
        # system_config: Drop NOT NULL from the legacy 'key' column.
        # Older deployments created this table with a 'key' NOT NULL column.  The current ORM
        # model only knows about id/key_name/value, so an INSERT leaves 'key' as NULL and
        # PostgreSQL raises a NotNullViolation.  Making the column nullable is safe.
        # If the 'key' column does not exist, PostgreSQL raises an error that is caught
        # and ignored by the loop above.
        "ALTER TABLE system_config ALTER COLUMN key DROP NOT NULL",
        # Remove rows with no valid key_name before adding unique index to avoid duplicates
        "DELETE FROM system_config WHERE key_name IS NULL OR key_name = ''",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_system_config_key_name ON system_config(key_name)",
        # user_reports: Store dynamic question answers as JSON
        "ALTER TABLE user_reports ADD COLUMN IF NOT EXISTS answers TEXT NULL",
        # user_reports: Track whether the channel message has been deleted
        "ALTER TABLE user_reports ADD COLUMN IF NOT EXISTS channel_msg_deleted BOOLEAN DEFAULT FALSE",
        # pending_referrals: Track users who arrive via /mylink deep-links before they join the group
        "CREATE TABLE IF NOT EXISTS pending_referrals (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id BIGINT NOT NULL, inviter_id BIGINT NOT NULL, group_id INTEGER REFERENCES bot_groups(id), created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "CREATE INDEX IF NOT EXISTS ix_pending_referrals_user_id ON pending_referrals(user_id)",
        "CREATE INDEX IF NOT EXISTS ix_pending_referrals_group_id ON pending_referrals(group_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS _pending_referral_user_group_uc ON pending_referrals(user_id, group_id)",
        # invitation_activity: Add link_keyword for group keyword-triggered invite link generation
        "ALTER TABLE invitation_activity ADD COLUMN IF NOT EXISTS link_keyword VARCHAR(255) NULL",
        # other_settings: Add auto_delete_channel_discussion_msg to delete linked channel discussion posts
        "ALTER TABLE other_settings ADD COLUMN IF NOT EXISTS auto_delete_channel_discussion_msg BOOLEAN DEFAULT FALSE",
        # other_settings: Add channel_auto_buttons and channel_discussion_keyword_filter (feat PR #256)
        "ALTER TABLE other_settings ADD COLUMN IF NOT EXISTS channel_auto_buttons BOOLEAN DEFAULT FALSE",
        "ALTER TABLE other_settings ADD COLUMN IF NOT EXISTS channel_discussion_keyword_filter TEXT NULL",
        # group_lottery: Add announce_channel_id to support posting results to a channel
        "ALTER TABLE group_lottery ADD COLUMN IF NOT EXISTS announce_channel_id VARCHAR(50) NULL",
        # group_users: Add subscription tracking and join source (feat PR #256)
        "ALTER TABLE group_users ADD COLUMN IF NOT EXISTS is_channel_subscribed BOOLEAN NULL",
        "ALTER TABLE group_users ADD COLUMN IF NOT EXISTS join_source VARCHAR(50) NULL",
        # forced_channel_subscription: Add reward fields (feat PR #256)
        "ALTER TABLE forced_channel_subscription ADD COLUMN IF NOT EXISTS reward_points INTEGER DEFAULT 0",
        "ALTER TABLE forced_channel_subscription ADD COLUMN IF NOT EXISTS reward_given_user_ids TEXT DEFAULT '[]'",
        # sync_group_messages: multi-target title cache + unique (source, target)
        "ALTER TABLE sync_group_messages ADD COLUMN IF NOT EXISTS target_title VARCHAR(255)",
        "CREATE UNIQUE INDEX IF NOT EXISTS _sync_source_target_uc ON sync_group_messages(source_group_id, target_group_id)",
        # sync_group_messages: optional [发送者] prefix (default off for clean look)
        "ALTER TABLE sync_group_messages ADD COLUMN IF NOT EXISTS include_sender_prefix BOOLEAN DEFAULT FALSE",
        # sync_group_messages: sender header style (newline = 名字单独一行, forward = 原生“转发自”)
        "ALTER TABLE sync_group_messages ADD COLUMN IF NOT EXISTS sender_prefix_style VARCHAR(20) DEFAULT 'newline'",
        # sync_group_messages: 频道同步「同步主机器人发的帖子」(default on)
        "ALTER TABLE sync_group_messages ADD COLUMN IF NOT EXISTS sync_own_bot_posts BOOLEAN DEFAULT TRUE",
        # sync_message_logs.via: "userbot" for messages relayed by the userbot listener
        "ALTER TABLE sync_message_logs ADD COLUMN IF NOT EXISTS via VARCHAR(20) NULL",
        # 同步日志按时间清理（保留时长）需要 synced_at 索引
        "CREATE INDEX IF NOT EXISTS ix_sync_message_logs_synced_at ON sync_message_logs(synced_at)",
        # channel_post_button_configs: 频道帖自动加按钮（编辑原帖 reply_markup）
        "CREATE TABLE IF NOT EXISTS channel_post_button_configs (id SERIAL PRIMARY KEY, group_id INTEGER NOT NULL UNIQUE REFERENCES bot_groups(id), enabled BOOLEAN DEFAULT FALSE, links TEXT DEFAULT '[]', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "CREATE INDEX IF NOT EXISTS ix_channel_post_button_configs_group_id ON channel_post_button_configs(group_id)",
        # group_warnings: /warn + keyword action=warn persistence (created via db.create_all; listed for clarity)
        "CREATE TABLE IF NOT EXISTS group_warnings (id INTEGER PRIMARY KEY AUTOINCREMENT, group_id INTEGER NOT NULL REFERENCES bot_groups(id), user_id BIGINT NOT NULL, admin_id BIGINT, reason TEXT, source VARCHAR(20) DEFAULT 'command', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)",
        "CREATE INDEX IF NOT EXISTS ix_group_warnings_group_id ON group_warnings(group_id)",
        "CREATE INDEX IF NOT EXISTS ix_group_warnings_user_id ON group_warnings(user_id)",
        "CREATE INDEX IF NOT EXISTS ix_group_warnings_created_at ON group_warnings(created_at)",
        "CREATE INDEX IF NOT EXISTS ix_group_warnings_group_user ON group_warnings(group_id, user_id)",
    ]

    with app.app_context():
        # Ensure all tables exist first
        db.create_all()
        print("✅ 数据库表初始化完成", flush=True)

        success_count = 0
        skip_count = 0
        for stmt in alter_statements:
            try:
                with db.engine.connect() as conn:
                    conn.execute(text(stmt))
                    conn.commit()
                success_count += 1
            except Exception:
                # Column/index likely already exists, which is fine
                skip_count += 1

        print(
            f"✅ 数据库迁移完成 (执行: {success_count}, 跳过: {skip_count})",
            flush=True,
        )

        # system_config: Drop NOT NULL constraints from any legacy columns not in the current
        # ORM model so that new ORM inserts (which only supply id, key_name, value) succeed.
        if db.engine.dialect.name == 'postgresql':
            from sqlalchemy import inspect as sa_inspect
            try:
                inspector = sa_inspect(db.engine)
                if inspector.has_table('system_config'):
                    known_cols = {'id', 'key_name', 'value'}
                    pk_cols = set(inspector.get_pk_constraint('system_config').get('constrained_columns', []))
                    for col in inspector.get_columns('system_config'):
                        if col['name'] not in known_cols and not col.get('nullable', True):
                            col_name = col['name']
                            if col_name in pk_cols:
                                continue  # primary key columns cannot have NOT NULL dropped
                            # Validate column name to prevent SQL injection
                            if not re.match(r'^[a-zA-Z0-9_]+$', col_name):
                                continue
                            try:
                                with db.engine.connect() as conn:
                                    conn.execute(text(f'ALTER TABLE system_config ALTER COLUMN "{col_name}" DROP NOT NULL'))
                                    conn.commit()
                            except Exception:
                                pass
            except Exception:
                pass


if __name__ == '__main__':
    try:
        run_migrations()
    except Exception as e:
        print(f"❌ 数据库迁移失败: {e}", flush=True)
        sys.exit(1)
