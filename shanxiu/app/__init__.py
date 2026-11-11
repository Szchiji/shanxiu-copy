from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import os
import json
from datetime import timedelta

db = SQLAlchemy()
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],
    storage_uri=os.getenv("REDIS_URL"),
)

# 全局变量
global_bot = None
global_loop = None

def get_bot_instance_role():
    """
    获取机器人实例角色
    从环境变量 BOT_INSTANCE_ROLE 读取，默认为 'main'
    """
    return os.getenv('BOT_INSTANCE_ROLE', 'main').lower()

def is_clone_instance():
    """
    判断当前实例是否为克隆实例
    """
    return get_bot_instance_role() == 'clone'

def is_main_instance():
    """
    判断当前实例是否为主实例
    """
    return get_bot_instance_role() == 'main'

def create_app():
    # Keep Telegram bot tokens out of logs for every entrypoint (run.py, migrate_database.py, ...).
    # httpx/httpcore → WARNING, redaction filter on all handlers, stdout/stderr scrubbed.
    import sys as _sys
    from app.log_redaction import install_log_redaction
    install_log_redaction(wrap_std_streams='pytest' not in _sys.modules)

    app = Flask(__name__)
    
    # 数据库配置
    db_uri = os.getenv('DATABASE_URL', 'sqlite:///bot.db')
    if db_uri and db_uri.startswith("postgres://"):
        db_uri = db_uri.replace("postgres://", "postgresql://", 1)
        
    app.config['SQLALCHEMY_DATABASE_URI'] = db_uri
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    
    # SECRET_KEY 配置 - 生产环境必须设置
    secret_key = os.getenv('SECRET_KEY', 'default_secret_key')
    if secret_key == 'default_secret_key':
        print("⚠️ WARNING: Using default SECRET_KEY. Please set SECRET_KEY environment variable for production!", flush=True)
    app.config['SECRET_KEY'] = secret_key
    
    # Session configuration for persistent login
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)  # Session lasts 7 days
    app.config['SESSION_COOKIE_SECURE'] = os.getenv('RAILWAY_PUBLIC_DOMAIN') is not None  # Only use HTTPS in production
    app.config['SESSION_COOKIE_HTTPONLY'] = True  # Prevent JavaScript access to session cookie
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'  # CSRF protection
    
    db.init_app(app)
    limiter.init_app(app)
    
    # 注册过滤器
    @app.template_filter('from_json')
    def from_json_filter(value):
        try: return json.loads(value)
        except: return {}

    # 注册上下文处理器 - 注入实例角色信息
    @app.context_processor
    def inject_bot_instance_role():
        """
        向所有模板注入机器人实例角色信息
        用于前端判断当前实例是主实例还是克隆实例
        """
        role = get_bot_instance_role()
        return {
            'bot_instance_role': role,
            'is_clone_instance': role == 'clone',
            'is_main_instance': role == 'main'
        }

    # 📦 注册模块
    from app.modules.core.routes import core_bp
    app.register_blueprint(core_bp)

    from app.modules.report import report_admin_bp
    app.register_blueprint(report_admin_bp)
    
    return app
