"""Admin workbench schema + patch save. Reuses existing settings and authorization, no new store."""
from typing import Any,Optional
from fastapi import Depends,HTTPException
from pydantic import BaseModel,ValidationError
from bot.api.routes_core import router
from bot.api.deps import get_admin_user_id
from bot.services import home_service,city_scope
from bot.services.admin_acl import is_super
from bot.services.home_feed import CITY_KEYS,DEFAULT_OPS

SITE={
 'announcement_text':('首页公告','textarea','announcement'),'announcement_enabled':('显示首页公告','bool','announcement'),
 'enabled_cities':('已开通城市','lines','system'),'bot_welcome_enabled':('发送欢迎消息','bool','welcome'),
 'bot_welcome_text':('欢迎正文','textarea','welcome'),'bot_welcome_buttons':('欢迎按钮','welcome_buttons','welcome'),
 'media_channel_id':('媒体存储频道','text','system'),'required_chats':('必加入群配置','json','system'),
 'listing_fields':('上架字段展示','json','channel'),'teacher_show_views':('老师端显示浏览','bool','teacher'),
 'teacher_show_favs':('老师端显示收藏','bool','teacher'),'teacher_show_contacts':('老师端显示匿名联系','bool','teacher'),
 'show_chat_cta':('显示聊天按钮','bool','cards'),
 'home_feed_page_size':('首页每次加载条数','int','cards'),'carousel_interval_sec':('轮播间隔秒','int','cards'),
 'media_max_count':('上架媒体数量上限','int','rules'),'review_require_audit':('评价需审核后展示','bool','rules'),
 'show_bot_link':('首页显示机器人','bool','community'),'bot_btn_label':('机器人按钮文案','text','community'),
}

def section(key):
 if key.startswith('popup_'):return 'popup'
 if key.startswith('approval_') or key.startswith('approve_'):return 'approval'
 if key.startswith('community_') or key in ('admin_contact','admin_btn_label','show_admin_link'):return 'community'
 if key.startswith('link_label') or key in ('broadcast_channel','broadcast_template','channel_directory','directory_channel','broadcast_reviews','review_channel','broadcast_coupons','coupon_channel'):return 'channel'
 if key in ('listing_days',):return 'rules'
 return 'cards'

def schema():
 out={m['key']:{**m,'section':section(m['key'])} for m in CITY_KEYS}
 for k,(label,kind,group) in SITE.items():out[k]={**out.get(k,{}),'key':k,'label':label,'type':kind,'section':group,'site_only':True,'super':True}
 return out

@router.get('/admin/workspace/config')
async def workspace_config(city:Optional[str]=None,admin_id:int=Depends(get_admin_user_id)):
 from bot.api.routes_city import _city_edit_rights
 if not city and not is_super(admin_id):raise HTTPException(403,'全站配置仅超管可查看')
 if city:await city_scope.ensure_city(admin_id,city)
 site=await home_service.get_or_create_settings();rights=await _city_edit_rights(admin_id,city) if city else {'can_edit':True,'can_edit_super':True}
 over=(site.get('city_overrides') or {}).get(city,{}) if city else {}
 fields=[]
 for key,meta in schema().items():
  if city and meta.get('site_only'):continue
  protected=bool(meta.get('super') and not is_super(admin_id))
  value=over.get(key) if city else site.get(key,DEFAULT_OPS.get(key))
  fields.append({**meta,'value':None if protected else value,'global':None if protected else site.get(key,DEFAULT_OPS.get(key)),
   'effective':None if protected else (site.get(key,DEFAULT_OPS.get(key)) if city and value is None else value),
   'editable':rights['can_edit'] and not protected,'protected':protected})
 effective_announcement=None
 if city:
  ann=(site.get('city_announcements') or {}).get(city,{})
  local=bool(ann.get('enabled') and str(ann.get('text') or '').strip())
  displayed=ann.get('text') if local else site.get('announcement_text') if site.get('announcement_enabled') else ''
  effective_announcement={'text':displayed or '', 'enabled':bool(displayed),'source':'city' if local else 'global'}
  for key,label,kind in [('announcement_text','本城公告正文','textarea'),('announcement_enabled','启用本城公告覆盖','bool')]:
   value=ann.get('text','') if kind=='textarea' else ann.get('enabled',False)
   fields.append({'key':key,'label':label,'type':kind,'section':'announcement','value':value,'global':site.get(key),'effective':displayed if kind=='textarea' else value,'editable':rights['can_edit'],'allow_blank':True,'inherit_supported':False,'hint':'关闭本城覆盖或清空正文＝回落全站公告；不能藉此隐藏全站公告。'})
 return {'fields':fields,'announcement_effective':effective_announcement,'city':city,'is_super':is_super(admin_id),'config_revision':site.get('config_revision'),**rights}

class Patch(BaseModel):
 city:Optional[str]=None
 section:str
 changes:dict[str,Any]

@router.post('/admin/workspace/config')
async def workspace_save(body:Patch,admin_id:int=Depends(get_admin_user_id)):
 from bot.api.routes_city import api_city_settings_save,CitySettingsBody
 from bot.api.routes_city import _city_edit_rights
 if body.city:
  await city_scope.ensure_city(admin_id,body.city)
  if not (await _city_edit_rights(admin_id,body.city))['can_edit']:raise HTTPException(403,'只有超管或明确负责本城的运营可修改城市设置')
 elif not is_super(admin_id):raise HTTPException(403,'全站配置仅超管可修改')
 metas=schema()
 for key in body.changes:
  if key not in metas or metas[key]['section']!=body.section:raise HTTPException(400,'包含当前页面以外字段，未保存')
 for key,value in body.changes.items():
  if value is None:
   if key=='announcement_enabled':raise HTTPException(400,'城市公告显示开关必须明确开启或关闭')
   if not body.city:raise HTTPException(400,'全站字段不能设为继承')
   continue
  kind=metas[key]['type']
  if kind=='bool' and not isinstance(value,bool):raise HTTPException(400,'开关必须为布尔值')
  if kind=='int' and (not isinstance(value,int) or isinstance(value,bool)):raise HTTPException(400,'数值字段格式不正确')
  if kind in ('text','textarea','url') and not isinstance(value,str):raise HTTPException(400,'文案字段格式不正确')
  if kind=='lines' and (not isinstance(value,list) or any(not isinstance(x,str) for x in value)):raise HTTPException(400,'城市应为字符串列表')
 if not body.changes:return {'ok':True,'message':'没有字段变化'}
 if body.city:
  if body.section=='announcement':
   await home_service.patch_city_announcement(body.city,body.changes,actor_id=admin_id)
  else:
   if any(metas[k].get('site_only') for k in body.changes):raise HTTPException(400,'全站字段不可按城市设置')
   await api_city_settings_save(CitySettingsBody(city=body.city,overrides=body.changes),admin_id)
 else:
  if not is_super(admin_id):raise HTTPException(403,'全站配置仅超管可修改')
  from bot.api.routes_admin_ops import ExtraOpsBody
  from bot.services import channel_features,label_sync
  from bot.services.community import safe_url
  from bot.services.broadcast import valid_chat
  changes=dict(body.changes)
  try:
   extras=ExtraOpsBody(**{k:v for k,v in changes.items() if k in ExtraOpsBody.model_fields}).model_dump(exclude_none=True)
  except ValidationError as exc:raise HTTPException(400,'字段格式不正确，未保存') from exc
  for k,v in extras.items():
   if k.startswith('community_') and k.endswith('_url') and v and not safe_url(v):raise HTTPException(400,'社区入口只支持HTTPS或t.me链接')
  if extras.get('admin_contact'):
   from bot.services.bot_info import normalize_contact
   contact=normalize_contact(extras['admin_contact'])
   if not contact:raise HTTPException(400,'管理员联系链接格式不正确')
   extras['admin_contact']=contact
  if changes.get('broadcast_channel') and not valid_chat(str(changes['broadcast_channel'])):raise HTTPException(400,'推送频道格式不正确')
  feature_keys={'broadcast_channel','channel_directory','directory_channel','broadcast_reviews','review_channel','broadcast_coupons','coupon_channel'}
  try:features=await channel_features.validate({k:v for k,v in changes.items() if k in feature_keys},city=None) if feature_keys.intersection(changes) else {}
  except ValueError as exc:raise HTTPException(400,str(exc)) from exc
  before=await home_service.get_or_create_settings()
  await home_service.update_settings(announcement_text=changes.get('announcement_text'),announcement_enabled=changes.get('announcement_enabled'),enabled_cities=changes.get('enabled_cities'),ops_config={**extras,**features} or None,actor_id=admin_id)
  after=await home_service.get_or_create_settings()
  try:await label_sync.schedule_after_save(before,after,city=None)
  except Exception:
   import logging
   logging.getLogger(__name__).exception('workbench label-sync scheduling failed after saved config')
   return {'ok':True,'message':'设置已保存，但频道链接同步调度未确认；请检查执行日志，不要重复保存。','config':await workspace_config(body.city,admin_id)}
 return {'ok':True,'message':'当前页面设置已保存；消息影响后续发送，目录仍下次北京时间12:00；小程序可能缓存10/60秒。','config':await workspace_config(body.city,admin_id)}

@router.get('/admin/workspace/posts/{post_id}')
async def workspace_post(post_id:str,admin_id:int=Depends(get_admin_user_id)):
 from bot.db import session_scope
 from bot.models import Post
 from sqlalchemy import select
 from bot.services import search_service
 await city_scope.guard(admin_id,'post',post_id)
 async with session_scope() as s:
  row=(await s.execute(select(Post).where(Post.post_id==post_id))).scalar_one_or_none()
  if not row:raise HTTPException(404,'待审资料不存在')
  data=dict(row.lamp_data or {});status=row.status
 current=None
 if data.get('edit_lamp_id'):
  await city_scope.guard(admin_id,'lamp',data['edit_lamp_id'])
  current=await search_service.get_lamp(data['edit_lamp_id'])
 return {'post_id':post_id,'status':status,'pending':data,'current':current}
