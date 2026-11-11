from app import create_app, db
import threading
import asyncio
import os
import sys
import time
import logging
from sqlalchemy import text

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

# Telegram API URLs contain the bot token; httpx logs every URL at INFO.
from app.log_redaction import install_log_redaction
install_log_redaction()

app = create_app()

def init_database(app):
    """Initialize database tables synchronously on startup."""
    with app.app_context():
        try:
            db.create_all()
            print("✅ 数据库表初始化完成", flush=True)
        except Exception as e:
            print(f"❌ 数据库初始化失败: {e}", flush=True)
            raise
        # Extra safety: create report-module tables in case db.create_all() missed them
        # (e.g. due to a PostgreSQL orphaned-sequence conflict on the primary key column).
        _ensure_report_tables(db)


def _ensure_report_tables(db):
    """
    Create system_config and user_reports tables if they do not exist.

    Using raw SERIAL DDL can silently fail on PostgreSQL when an orphaned sequence
    (system_config_id_seq / user_reports_id_seq) was left behind by a previously
    aborted table-creation attempt.  This function:
      1. Detects whether each table is missing.
      2. On PostgreSQL, drops any orphaned sequence before creating the table so
         that the SERIAL column does not trigger a "relation already exists" error.
      3. Uses SQLAlchemy's Table.create() which generates the correct
         dialect-specific DDL.
    """
    import re
    import logging
    from sqlalchemy import inspect as sa_inspect
    from app.models import SystemConfig, UserReport

    _logger = logging.getLogger(__name__)
    is_postgres = 'postgresql' in str(db.engine.url)
    inspector = sa_inspect(db.engine)

    for model_cls in (SystemConfig, UserReport):
        table_name = model_cls.__tablename__
        if inspector.has_table(table_name):
            continue  # table already exists – nothing to do

        if is_postgres:
            # Drop any orphaned sequence left over from a failed previous attempt.
            # Validate sequence name to prevent SQL injection (only allow safe identifier chars).
            seq_name = f'{table_name}_id_seq'
            if not re.match(r'^[a-zA-Z0-9_]+$', seq_name):
                _logger.error("Skipping sequence drop: unsafe name %r", seq_name)
            else:
                try:
                    with db.engine.connect() as conn:
                        conn.execute(text(f'DROP SEQUENCE IF EXISTS "{seq_name}"'))
                        conn.commit()
                except Exception as e:
                    _logger.warning("Could not drop orphaned sequence %r: %s", seq_name, e)

        try:
            # SQLAlchemy 2.0 requires a Connection object, not an Engine, for DDL.
            with db.engine.begin() as conn:
                model_cls.__table__.create(conn, checkfirst=True)
            print(f"✅ 创建表 {table_name} 成功", flush=True)
        except Exception as e:
            _logger.error("Failed to create table %r: %s", table_name, e)
            print(f"⚠️ 创建表 {table_name} 失败: {e}", flush=True)


def _fix_system_config_extra_columns(db):
    """
    Drop NOT NULL constraints from any columns in system_config that are not part of the
    current ORM model (id, key_name, value).

    Older deployments may have created this table with additional NOT NULL columns (e.g. a
    per-group chat_id) that are unknown to the current model.  When the ORM inserts a new
    row it only supplies the three model columns; PostgreSQL then rejects the statement
    because the extra column receives NULL, violating its NOT NULL constraint.  Making
    those legacy columns nullable (DROP NOT NULL) is safe: the column value will simply be
    NULL for all rows written by the current code.

    This repair is idempotent and only applies to PostgreSQL databases.
    """
    import re
    from sqlalchemy import inspect as sa_inspect

    _logger = logging.getLogger(__name__)

    if db.engine.dialect.name != 'postgresql':
        return  # SQLite and other dialects don't need this

    try:
        inspector = sa_inspect(db.engine)
        if not inspector.has_table('system_config'):
            return

        known_cols = {'id', 'key_name', 'value'}
        pk_cols = set(inspector.get_pk_constraint('system_config').get('constrained_columns', []))
        columns = inspector.get_columns('system_config')
        for col in columns:
            if col['name'] in known_cols:
                continue
            if col.get('nullable', True):
                continue  # already nullable, nothing to do
            col_name = col['name']
            if col_name in pk_cols:
                continue  # primary key columns cannot have NOT NULL dropped
            # Validate column name to prevent SQL injection (only allow safe identifier chars)
            if not re.match(r'^[a-zA-Z0-9_]+$', col_name):
                _logger.warning("system_config: skipping unsafe column name %r", col_name)
                continue
            try:
                with db.engine.connect() as conn:
                    conn.execute(text(f'ALTER TABLE system_config ALTER COLUMN "{col_name}" DROP NOT NULL'))
                    conn.commit()
                _logger.info("system_config: dropped NOT NULL on legacy column %r", col_name)
            except Exception as e:
                _logger.warning("system_config: could not drop NOT NULL on column %r: %s", col_name, e)
    except Exception as e:
        _logger.warning("system_config extra-column repair failed: %s", e)


def fix_database_schema(app):
    """Add missing columns to existing tables (synchronous, runs before server starts)."""
    with app.app_context():
        # List of ALTER TABLE statements to execute
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
            "ALTER TABLE scheduled_messages ADD COLUMN is_active BOOLEAN DEFAULT TRUE",
            "ALTER TABLE scheduled_messages ADD COLUMN last_sent_at TIMESTAMP",
            "ALTER TABLE scheduled_messages ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
            "ALTER TABLE scheduled_messages ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
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
            # User points table columns - add current_level_id to track member levels
            # Column is nullable, so existing records will have NULL until update_member_levels runs
            "ALTER TABLE user_points ADD COLUMN current_level_id INTEGER REFERENCES member_level(id)",
            # GroupMember: Set joined_at for existing records if NULL (use created_at as fallback)
            "UPDATE group_members SET joined_at = COALESCE(created_at, synced_at) WHERE joined_at IS NULL",
            # BotGroup: Add members_last_sync column to track last successful group member sync
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
            # ScheduledMessages: Add message_thread_id for topic/thread support
            "ALTER TABLE scheduled_messages ADD COLUMN IF NOT EXISTS message_thread_id INTEGER NULL",
            "ALTER TABLE scheduled_messages ADD COLUMN IF NOT EXISTS fail_count INTEGER DEFAULT 0",
            # PointsExchangeItem: Add announcement_msg_id to track per-item channel announcement
            "ALTER TABLE points_exchange_items ADD COLUMN IF NOT EXISTS announcement_msg_id BIGINT NULL",
            # system_config: Ensure id column exists.
            # Older deployments created this table without an id column, causing all ORM
            # queries (set_value / get_value) to fail silently and roll back, which made
            # report-system settings appear to vanish after saving.
            # SERIAL in PostgreSQL assigns a sequence default and fills existing rows automatically,
            # so this is safe to run on a populated table.
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
            # PostgreSQL raises a NotNullViolation.  Making the column nullable is safe: the
            # column is no longer used by any application code.  If the 'key' column does not
            # exist, PostgreSQL raises an error that is caught and ignored by the loop above.
            "ALTER TABLE system_config ALTER COLUMN key DROP NOT NULL",
            # user_reports: Store dynamic question answers as JSON
            "ALTER TABLE user_reports ADD COLUMN IF NOT EXISTS answers TEXT NULL",
            # sync_message_logs.via: "userbot" for messages relayed by the userbot listener
            "ALTER TABLE sync_message_logs ADD COLUMN IF NOT EXISTS via VARCHAR(20) NULL",
        ]
        
        # Execute each statement in its own transaction to handle PostgreSQL properly
        for stmt in alter_statements:
            try:
                with db.engine.connect() as conn:
                    conn.execute(text(stmt))
                    conn.commit()
            except Exception:
                # Column/index likely already exists, which is fine
                pass

        # system_config: Drop NOT NULL constraints from any columns that the current ORM model
        # does not define.  Older deployments may have created this table with extra NOT NULL
        # columns (e.g. a per-group chat_id) that cause ORM INSERTs to fail because the model
        # only supplies id, key_name, and value.  Making those legacy columns nullable lets the
        # current ORM write rows without knowing the original schema.
        _fix_system_config_extra_columns(db)

        # Ensure report module tables exist using SQLAlchemy (handles PostgreSQL sequences properly).
        # Raw SERIAL DDL can fail if an orphaned sequence was left behind by a previously aborted
        # table creation.  Using Table.create(checkfirst=True) lets SQLAlchemy generate the correct
        # dialect-specific DDL and avoids the sequence name conflict.
        _ensure_report_tables(db)

        print("✅ 数据库结构检查完成", flush=True)

def run_flask():
    port = int(os.getenv('PORT', 5000))
    app.run(host='0.0.0.0', port=port, use_reloader=False, threaded=True)

def start_bot_process_forever(flask_app):
    """
    启动一个永不退出的事件循环，供 Webhook 使用
    """
    time.sleep(3)
    from app.modules.core.routes import run_bot
    
    print("🤖 启动机器人后台循环...", flush=True)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # 1. 初始化 (传入 Flask App 实例)
    loop.run_until_complete(run_bot(flask_app))
    
    # 2. ⚡ 核心：让 Loop 永远跑下去，活着等待 Flask 的投喂
    print("✅ 机器人循环已启动，正在监听 Webhook 任务...", flush=True)
    loop.run_forever()

if __name__ == '__main__':
    domain = os.getenv('RAILWAY_PUBLIC_DOMAIN')
    mode = "Webhook" if domain else "Polling"
    print(f"🚀 系统启动中 ({mode} 模式)...", flush=True)

    # 1. 初始化数据库表 (同步执行，确保表存在后再启动服务)
    init_database(app)
    
    # 2. 数据库修复 (同步执行，确保列存在后再启动 Web 服务)
    fix_database_schema(app)
    
    # 3. 启动 Web (Flask)
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # 4. 启动机器人 (在独立线程中跑 loop_forever)
    # ⚡️ 修复点：将 app 传入机器人线程
    bot_thread = threading.Thread(target=start_bot_process_forever, args=(app,), daemon=True)
    bot_thread.start()

    # 4b. 小号监听（仅当已保存会话时启动；失败不影响主机器人）
    try:
        from app.userbot.manager import start_userbot_from_env, userbot_manager
        start_userbot_from_env(app)

        def _graceful_exit(signum, frame):
            # Disconnect the userbot first so the next container can reuse the session
            # without Telegram seeing it on two IPs at once (AUTH_KEY_DUPLICATED).
            try:
                userbot_manager.stop(timeout=5)
            finally:
                sys.exit(0)

        import signal
        signal.signal(signal.SIGTERM, _graceful_exit)
    except Exception as e:
        print(f"[userbot] init skipped: {type(e).__name__}", flush=True)
    
    # 5. 主线程死循环保活
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        sys.exit(0)