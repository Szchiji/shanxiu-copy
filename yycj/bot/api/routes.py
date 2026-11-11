"""Mini App + Admin HTTP 路由（/api/...）。"""
from bot.api.routes_core import router  # noqa: F401
import bot.api.routes_posts  # noqa: F401
import bot.api.routes_admin  # noqa: F401
import bot.api.routes_admin_ops  # noqa: F401
import bot.api.routes_media  # noqa: F401
import bot.api.routes_listing  # noqa: F401
import bot.api.routes_pins_extra  # noqa: F401
import bot.api.routes_favorites  # noqa: F401
import bot.api.routes_operators  # noqa: F401
import bot.api.routes_shift  # noqa: F401
import bot.api.routes_city  # noqa: F401
import bot.api.routes_coupons  # noqa: F401
import bot.api.routes_place  # noqa: F401
import bot.api.routes_community  # noqa: F401
import bot.api.routes_client_log  # noqa: F401
import bot.api.routes_execution  # noqa: F401
import bot.api.routes_admin_insights  # noqa: F401
import bot.models_extra  # noqa: F401
import bot.services.role_lock  # noqa: F401
import bot.services.admin_acl  # noqa: F401

__all__ = ["router"]

from bot.api import routes_workspace  # noqa: F401,E402
