"""Cross-process, reentrant per-listing lock, held across durable send checkpoints."""
from contextlib import asynccontextmanager
from contextvars import ContextVar
from sqlalchemy import text
from bot.db import get_engine

_held = ContextVar("listing_mutation_locks", default=frozenset())


@asynccontextmanager
async def listing_lock(lamp_id):
    key = str(lamp_id)
    if key in _held.get():
        yield
        return
    async with get_engine().connect() as conn:
        locked = await conn.scalar(text("SELECT pg_try_advisory_lock(hashtextextended(:key, 18141))"), {"key":key})
        await conn.commit()
        if not locked:
            raise ValueError("这条资料正在处理，请稍后重试同一操作")
        token = _held.set(_held.get() | {key})
        try:
            yield
        finally:
            _held.reset(token)
            try:
                await conn.execute(text("SELECT pg_advisory_unlock(hashtextextended(:key, 18141))"), {"key":key})
                await conn.commit()
            except BaseException:
                # Never return a connection carrying a session lock to the pool.
                await conn.invalidate()
                raise


def serialized(func):
    from functools import wraps
    @wraps(func)
    async def wrapped(lamp_id,*args,**kwargs):
        async with listing_lock(lamp_id):
            return await func(lamp_id,*args,**kwargs)
    return wrapped
