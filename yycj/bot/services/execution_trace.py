"""Observational delivery adapters. Never retries, changes return values, or replays a send."""
from contextvars import ContextVar
from functools import wraps
from uuid import uuid4
from bot.services import execution_log as log

_current=ContextVar('delivery_trace',default=None)
_origin=ContextVar('delivery_origin',default=None)

def origin(source):
 def decorate(fn):
  @wraps(fn)
  async def wrapped(*a,**kw):
   token=_origin.set(source)
   try:return await fn(*a,**kw)
   finally:_origin.reset(token)
  return wrapped
 return decorate

class Trace:
 def __init__(self,feature,step,source):
  self.feature,self.step,self.source=feature,step,source;self.operation_id=f'{feature}-{uuid4()}';self.record=None
  self.meta={};self.status=None;self.code=None;self.uncertain=False;self.attempted=False;self.ack=False
 async def identify(self,*,city=None,listing_id=None,revision=None,target='channel'):
  self.meta=dict(city=city,listing_id=listing_id,revision=revision,target=target,operation_id=self.operation_id,source=self.source)
  self.record=await log.begin(self.feature,self.step,**self.meta)
 async def revision(self,value):
  self.meta['revision']=value
  if self.record:
   try:
    from bot.db import session_scope
    from bot.models import ExecutionRecord
    from sqlalchemy import update
    async with session_scope() as s:await s.execute(update(ExecutionRecord).where(ExecutionRecord.record_id==self.record).values(config_revision=value))
   except Exception:pass
 def mark(self,status,code=None):self.status,self.code=status,code
 async def call(self,fn,step,*,send=False):
  key=await log.begin(self.feature,step,**self.meta) if self.meta else None
  self.attempted=True
  try:
   result=await fn();self.ack=True
   self.status='sent' if send else 'synced';self.code=None
   if send and not getattr(result,'message_id',None):self.uncertain=True;self.status='unknown';self.code='delivery_unknown'
   if key and getattr(result,'message_id',None):
    try:
     from bot.db import session_scope
     from bot.models import ExecutionRecord
     from sqlalchemy import update
     async with session_scope() as s:await s.execute(update(ExecutionRecord).where(ExecutionRecord.record_id==key).values(target_ref='message:'+str(int(result.message_id))))
    except Exception:pass
   await log.finish(key,self.status,self.code)
   return result
  except Exception as exc:
   if not send and 'not modified' in str(exc).lower():status,code='synced',None
   else:
    code=log.reason(exc);status='unknown' if code=='delivery_unknown' else 'failed'
    if send and status=='unknown':self.uncertain=True
   self.status,self.code=status,code;await log.finish(key,status,code);raise
 async def finish(self,exc=None):
  if not self.record:return
  if exc is not None:
   self.code='delivery_unknown' if self.ack else log.reason(exc) if self.attempted else 'config_unavailable'
   self.status='unknown' if self.ack or self.attempted and self.code=='delivery_unknown' else 'failed'
  if self.uncertain:self.status,self.code='unknown','delivery_unknown'
  await log.finish(self.record,self.status or 'skipped',self.code or ('no_data' if not self.status else None))

def current():return _current.get()
def traced(feature,step,source='business'):
 def decorate(fn):
  @wraps(fn)
  async def wrapped(*a,**kw):
   trace=Trace(feature,step,_origin.get() or source);token=_current.set(trace)
   try:
    result=await fn(*a,**kw)
   except BaseException as exc:
    # Cancellation leaves a durable running/unknown marker; it never causes a resend.
    if isinstance(exc,Exception):await trace.finish(exc)
    raise
   else:await trace.finish();return result
   finally:_current.reset(token)
  return wrapped
 return decorate
