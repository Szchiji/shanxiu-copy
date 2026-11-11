"""Pure offline-review metadata; no DB, auth service, Telegram or network calls."""
import json
import os
os.environ.setdefault('BOT_TOKEN','123456789:TESTTOKENxxxxxxxxxxxxxxxxxxxxxxxxxxx')
os.environ.setdefault('ADMIN_IDS','1')
from bot.api.routes_workspace import schema
from bot.services.home_feed import DEFAULT_OPS

def fixture():
    settings={**DEFAULT_OPS,'enabled_cities':['深圳','广州'],'announcement_enabled':True,'announcement_text':'示例：运营通知，非线上公告',
      'community_discussion_url':'https://example.invalid/community','community_backup_url':'https://example.invalid/backup',
      'popup_announcement_enabled':True,'popup_announcement_title':'本城公告 · 示例','popup_announcement_text':'这是审核包的示例公告，不会保存到生产。',
      'bot_welcome_text':'欢迎来到月影车姬。请选择下方入口。\n（离线示例）',
      'bot_welcome_buttons':[{'type':'webapp','label':'进入首页','target':'home','enabled':True,'new_row':True},{'type':'webapp','label':'我的资料','target':'me','enabled':True,'new_row':False}],
      'approval_buttons':{'mode':'custom','items':[{'type':'webapp','label':'查看我的资料','target':'detail','enabled':True,'new_row':True},{'type':'url','label':'查看频道帖','target':'{album}','enabled':True,'new_row':False}]},
      'broadcast_channel':'@example_channel','city_overrides':{}}
    return {'schema':list(schema().values()),'settings':settings}

if __name__=='__main__':print(json.dumps(fixture(),ensure_ascii=False))
