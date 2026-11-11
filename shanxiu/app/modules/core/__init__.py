from flask import Blueprint

# 定义核心功能模块的蓝图
# template_folder 指向当前模块下的 templates 目录
core_bp = Blueprint('core', __name__, template_folder='templates', url_prefix='')

# 导入路由以激活
from . import routes
