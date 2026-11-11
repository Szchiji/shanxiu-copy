"""Best-effort safe diagnostics. Retry authority ALWAYS lives in durable business checkpoints.
Never persist exception text, request bodies, tokens, URLs, initData or private DM contents.
"""
import logging
import re
import uuid
from datetime import datetime
from sqlalchemy import update
from bot.db import session_scope
from bot.models import ExecutionRecord
log=logging.getLogger(__name__)
REASONS={
 'already_processed':'已有引用 / 已处理，未新增发送',
 'no_data':'没有可处理内容，未发送',
 'config_unavailable':'配置暂不可读取，未发送；不会绕过关闭开关',
 'disabled':'功能开关已关闭，按设置跳过', 'telegram_forbidden':'机器人无权限或收件人已屏蔽',
 'telegram_bad_request':'Telegram明确拒绝请求，请检查目标/媒体/按钮配置',
 'telegram_rate_limit':'Telegram限流，请稍后仅重试安全失败步骤',
 'delivery_unknown':'送达结果不确定，禁止盲目重发；需要人工核对',
 'not_configured':'尚未配置发送目标', 'no_post':'无原频道帖，不自动新发',
 'failed':'步骤明确失败；具体可重试性以操作检查点为准', 'stale':'资料或步骤已被新版本替代',
 'running':'执行已开始；未完成时不得推定发送失败',
}


def reason(exc):
 from aiogram.exceptions import TelegramForbiddenError,TelegramBadRequest,TelegramRetryAfter
 if isinstance(exc,TelegramForbiddenError):return 'telegram_forbidden'
 if isinstance(exc,TelegramRetryAfter):return 'telegram_rate_limit'
 if isinstance(exc,TelegramBadRequest):return 'telegram_bad_request'
 return 'delivery_unknown'


def safe(value,n=64):
 value=str(value or '')
 if not value or not re.fullmatch(r'[\w:@.\-]{1,'+str(n)+'}',value,re.ASCII):return None
 if re.search(r'\d{6,}:[\w-]{20,}',value) or re.search(r'(token|password|initdata|bearer|session)',value,re.I):return None
 return value


async def begin(feature,step,*,city=None,listing_id=None,operation_id=None,actor_id=None,revision=None,target=None,retry_kind=None,source=None):
 key=str(uuid.uuid4())
 try:
  async with session_scope() as s:
   s.add(ExecutionRecord(record_id=key,feature=safe(feature,32) or 'operation',step=safe(step,24) or 'execute',
     city=str(city)[:32] if city else None,listing_id=safe(listing_id),operation_id=safe(operation_id),
     source=safe(source,32),actor_id=int(actor_id) if actor_id is not None else None,config_revision=int(revision) if revision is not None else None,
     target_ref=safe(target,80),retry_kind=safe(retry_kind,24),status='running',reason_code='running'))
  return key
 except Exception as exc:
  log.warning('execution metadata persistence failed (%s)',type(exc).__name__)
  return None


async def finish(key,status,code=None):
 if not key:return
 try:
  async with session_scope() as s:
   await s.execute(update(ExecutionRecord).where(ExecutionRecord.record_id==key).values(status=safe(status,24) or 'unknown',reason_code=code if code in REASONS else None,finished_at=datetime.utcnow()))
 except Exception as exc:log.warning('execution metadata finish failed (%s)',type(exc).__name__)


async def event(feature,step,status,code=None,**kwargs):
 key=await begin(feature,step,**kwargs);await finish(key,status,code);return key
