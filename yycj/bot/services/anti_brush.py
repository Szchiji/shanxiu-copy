"""防刷：频率限制 + 文本相似度。

优先 Redis（REDIS_URL）；未配置时回退进程内内存并打警告。
速率限制语义尽量贴近原滑动窗口：limit 次 / window_sec。
"""

from __future__ import annotations

import logging
import re
import time
from collections import defaultdict, deque
from typing import Deque, Dict, List

from bot.config import get_settings

logger = logging.getLogger(__name__)

# 内存级滑动窗口（无 Redis 时使用）
_hits: Dict[str, Deque[float]] = defaultdict(deque)
_recent_text: Dict[int, Deque[str]] = defaultdict(lambda: deque(maxlen=20))

_redis = None
_redis_failed = False
_warned_memory = False


async def _get_redis():
    """懒加载 redis asyncio 客户端；失败则永久回退内存。"""
    global _redis, _redis_failed, _warned_memory
    if _redis_failed:
        return None
    if _redis is not None:
        return _redis

    url = get_settings().normalized_redis_url()
    if not url:
        if not _warned_memory:
            logger.warning(
                "REDIS_URL 未设置：anti_brush 使用进程内内存限流（多实例不共享）"
            )
            _warned_memory = True
        return None

    try:
        import redis.asyncio as redis_async

        client = redis_async.from_url(url, decode_responses=True)
        await client.ping()
        _redis = client
        logger.info("anti_brush Redis connected")
        return _redis
    except Exception:
        _redis_failed = True
        logger.exception("Redis 连接失败，anti_brush 回退内存限流")
        if not _warned_memory:
            _warned_memory = True
        return None


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        try:
            await _redis.aclose()
        except Exception:
            try:
                await _redis.close()
            except Exception:
                pass
        _redis = None


def _prune(q: Deque[float], window: float) -> None:
    now = time.time()
    while q and now - q[0] > window:
        q.popleft()


def _allow_memory(key: str, limit: int, window_sec: float) -> bool:
    q = _hits[key]
    _prune(q, window_sec)
    if len(q) >= limit:
        return False
    q.append(time.time())
    return True


async def _allow_redis(r, key: str, limit: int, window_sec: float) -> bool:
    """ZSET 滑动窗口，与内存版语义一致。"""
    now = time.time()
    rkey = f"yycj:rl:{key}"
    try:
        pipe = r.pipeline()
        pipe.zremrangebyscore(rkey, 0, now - window_sec)
        pipe.zcard(rkey)
        results = await pipe.execute()
        count = int(results[1] or 0)
        if count >= limit:
            return False
        member = f"{now}:{count}"
        pipe = r.pipeline()
        pipe.zadd(rkey, {member: now})
        pipe.expire(rkey, int(window_sec) + 1)
        await pipe.execute()
        return True
    except Exception:
        logger.exception("Redis allow failed, fallback memory for key=%s", key)
        return _allow_memory(key, limit, window_sec)


async def allow(key: str, limit: int, window_sec: float) -> bool:
    """滑动窗口限流：窗口内最多 limit 次。"""
    r = await _get_redis()
    if r is not None:
        return await _allow_redis(r, key, limit, window_sec)
    return _allow_memory(key, limit, window_sec)


async def check_post_rate(user_id: int) -> bool:
    return await allow(f"post:{user_id}", limit=3, window_sec=3600)


async def check_report_rate(user_id: int) -> bool:
    return await allow(f"report:{user_id}", limit=5, window_sec=3600)


async def check_search_rate(user_id: int) -> bool:
    return await allow(f"search:{user_id}", limit=30, window_sec=60)


async def check_session_request_rate(user_id: int) -> bool:
    return await allow(f"sessreq:{user_id}", limit=5, window_sec=3600)


def _normalize(text: str) -> str:
    t = text.lower().strip()
    t = re.sub(r"\s+", "", t)
    return t


def _grams(s: str) -> set:
    return {s[i : i + 2] for i in range(len(s) - 1)} or {s}


async def _load_recent_texts(user_id: int) -> List[str]:
    r = await _get_redis()
    if r is not None:
        try:
            items = await r.lrange(f"yycj:sim:{user_id}", 0, 19)
            return list(items or [])
        except Exception:
            logger.exception("Redis lrange recent text failed")
    return list(_recent_text[user_id])


async def _remember_text(user_id: int, norm: str) -> None:
    r = await _get_redis()
    if r is not None:
        try:
            key = f"yycj:sim:{user_id}"
            pipe = r.pipeline()
            pipe.lpush(key, norm)
            pipe.ltrim(key, 0, 19)
            pipe.expire(key, 86400)
            await pipe.execute()
            return
        except Exception:
            logger.exception("Redis remember text failed")
    _recent_text[user_id].append(norm)


async def text_too_similar(user_id: int, text: str, threshold: float = 0.85) -> bool:
    """字符 bigram Jaccard 近似。"""
    norm = _normalize(text)
    if len(norm) < 8:
        await _remember_text(user_id, norm)
        return False

    g1 = _grams(norm)
    for old in await _load_recent_texts(user_id):
        g2 = _grams(old)
        inter = len(g1 & g2)
        union = len(g1 | g2) or 1
        if inter / union >= threshold:
            return True
    await _remember_text(user_id, norm)
    return False
