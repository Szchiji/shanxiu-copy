"""媒体上传与 Telegram file_id 预览代理。"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import mimetypes
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

import httpx
from aiogram.types import BufferedInputFile
from fastapi import Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse

from bot.api.deps import get_current_user_id
from bot.api.routes_core import router
from bot.config import get_settings

logger = logging.getLogger(__name__)

MAX_FILES = 9
REVIEW_MAX_FILES = 3
MAX_BYTES = 20 * 1024 * 1024
_DISK_MAX = 2 * 1024 * 1024
_DISK_DIR = Path("/tmp/yycj-media")
_PATH_CACHE: Dict[str, Tuple[str, float]] = {}
_PATH_TTL = 50 * 60
_POSTER: Dict[str, str] = {}
_CACHE_HDR = {
    "Cache-Control": "public, max-age=604800, immutable",
    "Accept-Ranges": "bytes",
    "Content-Disposition": "inline",
}


def _guess_type(filename: str, content_type: str | None) -> str:
    ct = (content_type or "").lower()
    name = (filename or "").lower()
    if ct.startswith("video/") or name.endswith((".mp4", ".mov", ".webm", ".mkv")):
        return "video"
    return "image"


_IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic", ".heif")
_VIDEO_EXT = (".mp4", ".mov", ".webm", ".mkv", ".m4v", ".3gp")
_RATE_WINDOW = 600.0
_RATE_MAX_FILES = 60
_RATE: Dict[int, List[float]] = {}


def _allowed_upload(filename: str, content_type: str | None) -> bool:
    ct = (content_type or "").lower().split(";")[0].strip()
    name = (filename or "").lower()
    if ct == "image/svg+xml" or name.endswith(".svg"):
        return False
    if ct.startswith("image/") or ct.startswith("video/"):
        return True
    if ct in ("", "application/octet-stream"):
        return name.endswith(_IMAGE_EXT + _VIDEO_EXT)
    return False


def _rate_ok(user_id: int, n: int) -> bool:
    now = time.monotonic()
    hits = [t for t in _RATE.get(user_id, []) if now - t < _RATE_WINDOW]
    if len(hits) + n > _RATE_MAX_FILES:
        _RATE[user_id] = hits
        return False
    hits.extend([now] * n)
    _RATE[user_id] = hits
    if len(_RATE) > 5000:
        for uid in [u for u, v in _RATE.items() if not v or now - v[-1] >= _RATE_WINDOW]:
            _RATE.pop(uid, None)
    return True


async def _read_capped(uf: UploadFile, limit: int) -> bytes:
    chunks: List[bytes] = []
    total = 0
    while True:
        chunk = await uf.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise HTTPException(status_code=400, detail=f"文件过大：{uf.filename or 'unknown'}（≤20MB）")
        chunks.append(chunk)
    return b"".join(chunks)


async def _require_uploader(user_id: int) -> bool:
    """Only publishers (teacher/merchant) and admins may upload. Returns is_admin."""
    from bot.services.admin_acl import is_admin, warm
    await warm()
    if is_admin(user_id):
        return True
    from bot.services import users
    u = await users.ensure_user(user_id)
    if u.get("role") not in ("teacher", "merchant"):
        raise HTTPException(status_code=403, detail="仅老师或商家可上传，请先在「我的」切换身份")
    return False


def _mime_for(file_id: str, path: str) -> str:
    fid = str(file_id or "")
    low = str(path or "").lower()
    mime, _ = mimetypes.guess_type(path)
    if fid.startswith(("BAAC", "BQAC")) or low.endswith((".mp4", ".mov", ".webm", ".mkv")):
        return "video/mp4"
    if fid.startswith("AgAC") or low.endswith((".jpg", ".jpeg", ".png", ".webp")):
        return mime or "image/jpeg"
    return mime or "application/octet-stream"


def _disk_path(file_id: str) -> Path:
    name = hashlib.sha256(file_id.encode("utf-8")).hexdigest()[:40]
    return _DISK_DIR / name


@router.post("/media/upload")
async def api_media_upload(
    files: List[UploadFile] = File(...),
    purpose: str = "",
    user_id: int = Depends(get_current_user_id),
) -> Dict[str, Any]:
    if not files:
        raise HTTPException(status_code=400, detail="请选择文件")
    review = purpose in ("review", "review_proof")
    cap = (5 if purpose == "review_proof" else REVIEW_MAX_FILES) if review else MAX_FILES
    if len(files) > cap:
        raise HTTPException(status_code=400, detail=f"一次最多上传 {cap} 个文件")
    # 评价晒图: any signed-in user (guests write reviews), photos only, max 3, normal rate limit.
    admin = False if review else await _require_uploader(user_id)
    for uf in files:
        if not _allowed_upload(uf.filename or "", uf.content_type):
            raise HTTPException(status_code=400, detail=f"只支持图片或视频：{uf.filename or 'unknown'}")
        if review and _guess_type(uf.filename or "", uf.content_type) != "image":
            raise HTTPException(status_code=400, detail="评价只能上传图片")
    if not admin and not _rate_ok(user_id, len(files)):
        raise HTTPException(status_code=429, detail="上传过于频繁，请稍后再试")
    settings = get_settings()
    from bot.services.broadcast import resolve_media_chat
    chat_id = await resolve_media_chat() or settings.media_storage_chat_id
    if not chat_id:
        raise HTTPException(status_code=503, detail="未配置媒体存储频道，无法存媒体")
    from bot.main import bot
    results: List[Dict[str, str]] = []
    for uf in files:
        data = await _read_capped(uf, MAX_BYTES)
        if not data:
            continue
        thumb_id = None
        kind = _guess_type(uf.filename or "", uf.content_type)
        safe_name = (uf.filename or ("video.mp4" if kind == "video" else "image.jpg")).replace("/", "_")
        buf = BufferedInputFile(data, filename=safe_name)
        try:
            if kind == "video":
                try:
                    msg = await bot.send_video(chat_id, buf, disable_notification=True)
                    file_id = (msg.video.file_id if msg.video else None) or (
                        msg.document.file_id if msg.document else None
                    )
                    if msg.video and getattr(msg.video, "thumbnail", None):
                        thumb_id = msg.video.thumbnail.file_id
                except Exception:
                    msg = await bot.send_document(chat_id, buf, disable_notification=True)
                    file_id = msg.document.file_id if msg.document else None
            else:
                msg = await bot.send_photo(chat_id, buf, disable_notification=True)
                file_id = msg.photo[-1].file_id if msg.photo else None
                if msg.photo:
                    thumb_id = msg.photo[0].file_id
        except Exception as exc:
            logger.exception("upload to telegram failed")
            raise HTTPException(status_code=502, detail=f"上传失败：{exc}") from exc
        if not file_id:
            continue
        item = {"type": kind, "file_id": file_id, "url": file_id, "preview_url": f"/api/media/file/{file_id}"}
        if thumb_id:
            item["thumb_file_id"] = thumb_id
            item["preview_url"] = f"/api/media/file/{thumb_id}"
            _POSTER[file_id] = thumb_id
        results.append(item)
    if not results:
        raise HTTPException(status_code=400, detail="没有有效文件")
    return {"ok": True, "items": results, "count": len(results)}


async def _resolve_poster(file_id: str) -> str:
    if file_id in _POSTER:
        return _POSTER[file_id]
    from bot.main import bot
    from bot.services.broadcast import resolve_media_chat
    chat = await resolve_media_chat() or get_settings().media_storage_chat_id
    if not chat:
        raise HTTPException(status_code=404, detail="no media chat")
    msg = await bot.send_video(chat, file_id, disable_notification=True)
    thumb = None
    if msg.video and getattr(msg.video, "thumbnail", None):
        thumb = msg.video.thumbnail.file_id
    if not thumb:
        raise HTTPException(status_code=404, detail="no thumb")
    _POSTER[file_id] = thumb
    return thumb


_POSTER_MISS: Dict[str, float] = {}
_POSTER_HITS: List[float] = []


async def _known_media(file_id: str) -> bool:
    """Poster generation re-sends a video to Telegram, so only do it for media on a real listing/post."""
    from sqlalchemy import text as _sql
    from bot.db import session_scope
    async with session_scope() as s:
        res = await s.execute(
            _sql(
                "SELECT 1 FROM lamps WHERE strpos(CAST(media AS text), :fid) > 0 "
                "UNION ALL SELECT 1 FROM posts WHERE strpos(CAST(lamp_data AS text), :fid) > 0 LIMIT 1"
            ),
            {"fid": file_id},
        )
        return res.first() is not None


@router.get("/media/poster/{file_id:path}")
async def api_media_poster(file_id: str):
    # <img src> cannot carry the Bearer header, so instead of a login check this endpoint only
    # serves file_ids that belong to an existing listing/post, with a global rate limit.
    if file_id not in _POSTER:
        fid = str(file_id or "")
        if not fid or len(fid) > 200 or not all(c.isalnum() or c in "-_" for c in fid):
            raise HTTPException(status_code=404, detail="no poster")
        now = time.monotonic()
        if _POSTER_MISS.get(fid, 0) > now:
            raise HTTPException(status_code=404, detail="no poster")
        _POSTER_HITS[:] = [t for t in _POSTER_HITS if now - t < 60]
        if len(_POSTER_HITS) >= 30:
            raise HTTPException(status_code=429, detail="too many poster requests")
        _POSTER_HITS.append(now)
        try:
            known = await _known_media(fid)
        except Exception:
            logger.warning("poster lookup failed", exc_info=True)
            known = False
        if not known:
            if len(_POSTER_MISS) > 5000:
                _POSTER_MISS.clear()
            _POSTER_MISS[fid] = now + 600
            raise HTTPException(status_code=404, detail="no poster")
    try:
        thumb = await _resolve_poster(file_id)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("poster failed %s", exc)
        raise HTTPException(status_code=404, detail="no poster") from exc
    return RedirectResponse(url=f"/api/media/file/{thumb}", status_code=307)


_HTTP: httpx.AsyncClient | None = None
_DL: Dict[str, "asyncio.Future[bool]"] = {}
_BG: Dict[str, "asyncio.Task[bool]"] = {}
_CACHE_CAP = 1024 * 1024 * 1024  # total bytes kept in /tmp/yycj-media
_FETCH_MAX = 25 * 1024 * 1024  # Bot API files are <= 20MB
_CHUNK = 256 * 1024


def _http() -> httpx.AsyncClient:
    """One pooled client: no new TLS handshake to Telegram for every (range) request."""
    global _HTTP
    if _HTTP is None or _HTTP.is_closed:
        _HTTP = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=10.0),
            follow_redirects=True,
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
        )
    return _HTTP


def _parse_range(header: str | None, size: int):
    """Return (start, end) inclusive, None for no/ignored range, or "bad" for unsatisfiable."""
    if not header:
        return None
    h = header.strip().lower()
    if not h.startswith("bytes=") or "," in h:
        return None
    spec = h[6:].strip()
    try:
        a, _, b = spec.partition("-")
        if a == "":
            n = int(b)
            if n <= 0:
                return "bad"
            return (max(0, size - n), size - 1)
        start = int(a)
        end = int(b) if b else size - 1
    except ValueError:
        return None
    if start >= size or start < 0:
        return "bad"
    return (start, min(end, size - 1))


def _serve_local(local: Path, mime: str, rng_header: str | None):
    size = local.stat().st_size
    try:
        os.utime(local, None)  # LRU touch
    except OSError:
        pass
    rng = _parse_range(rng_header, size)
    if rng == "bad":
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}", "Accept-Ranges": "bytes"})
    start, end = rng if rng else (0, size - 1)
    length = end - start + 1

    def body():
        with local.open("rb") as fh:
            fh.seek(start)
            left = length
            while left > 0:
                chunk = fh.read(min(_CHUNK, left))
                if not chunk:
                    break
                left -= len(chunk)
                yield chunk

    headers = dict(_CACHE_HDR)
    headers["Content-Length"] = str(length)
    if rng:
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
    return StreamingResponse(body(), status_code=206 if rng else 200, media_type=mime, headers=headers)


def _evict() -> None:
    try:
        files = [e for e in os.scandir(_DISK_DIR) if e.is_file() and not e.name.endswith(".part")]
    except OSError:
        return
    total = sum(e.stat().st_size for e in files)
    if total <= _CACHE_CAP:
        return
    for e in sorted(files, key=lambda x: x.stat().st_mtime):
        try:
            total -= e.stat().st_size
            os.remove(e.path)
        except OSError:
            pass
        if total <= _CACHE_CAP * 0.8:
            break


async def _download(file_id: str, url: str, local: Path) -> bool:
    """Fetch the whole file once into the disk cache (shared by concurrent requests)."""
    fut = _DL.get(file_id)
    if fut is not None:
        try:
            if await asyncio.wait_for(asyncio.shield(fut), timeout=60):
                return True
        except Exception:
            pass
        if local.is_file():
            return True
        if file_id in _DL:
            return False
    fut = asyncio.get_running_loop().create_future()
    _DL[file_id] = fut
    ok = False
    tmp = local.with_name(local.name + f".{os.getpid()}.{int(time.time() * 1000)}.part")
    try:
        _DISK_DIR.mkdir(parents=True, exist_ok=True)
        async with _http().stream("GET", url) as resp:
            if resp.status_code == 200:
                n = 0
                with tmp.open("wb") as fh:
                    async for chunk in resp.aiter_bytes(_CHUNK):
                        n += len(chunk)
                        if n > _FETCH_MAX:
                            raise ValueError("file too large for cache")
                        fh.write(chunk)
                tmp.replace(local)
                ok = True
    except Exception:
        logger.warning("media cache download failed", exc_info=False)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass
        _DL.pop(file_id, None)
        if not fut.done():
            fut.set_result(ok)
    if ok:
        _evict()
    return ok


async def _tee_stream(file_id: str, url: str, local: Path, mime: str, ranged: bool):
    """First open (no range / bytes=0-): stream from Telegram to the client immediately
    while writing the disk cache, so playback starts without waiting for the full file."""
    try:
        req = _http().build_request("GET", url)
        resp = await _http().send(req, stream=True)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="拉取 Telegram 文件失败") from exc
    if resp.status_code != 200:
        await resp.aclose()
        _PATH_CACHE.pop(file_id, None)
        raise HTTPException(status_code=502, detail="拉取 Telegram 文件失败")
    try:
        size = int(resp.headers.get("content-length") or 0)
    except ValueError:
        size = 0
    cacheable = 0 < size <= _FETCH_MAX and file_id not in _DL
    fut = None
    tmp = local.with_name(local.name + f".{os.getpid()}.{int(time.time() * 1000)}.part")
    if cacheable:
        fut = asyncio.get_running_loop().create_future()
        _DL[file_id] = fut

    async def body():
        ok = False
        fh = None
        try:
            if cacheable:
                _DISK_DIR.mkdir(parents=True, exist_ok=True)
                fh = tmp.open("wb")
            n = 0
            async for chunk in resp.aiter_bytes(_CHUNK):
                n += len(chunk)
                if fh:
                    fh.write(chunk)
                yield chunk
            if fh:
                fh.close()
                fh = None
                if n == size:
                    tmp.replace(local)
                    ok = True
        finally:
            if fh:
                fh.close()
            await resp.aclose()
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
            if fut is not None:
                _DL.pop(file_id, None)
                if not fut.done():
                    fut.set_result(ok)
            if ok:
                _evict()

    headers = dict(_CACHE_HDR)
    if size:
        headers["Content-Length"] = str(size)
        if ranged:
            headers["Content-Range"] = f"bytes 0-{size - 1}/{size}"
    elif ranged:
        headers.pop("Accept-Ranges", None)
    return StreamingResponse(body(), status_code=206 if (ranged and size) else 200, media_type=mime, headers=headers)


@router.get("/media/file/{file_id:path}")
async def api_media_file(file_id: str, request: Request):
    settings = get_settings()
    if not settings.bot_token:
        raise HTTPException(status_code=503, detail="BOT_TOKEN 未配置")
    rng = request.headers.get("range")
    local = _disk_path(file_id)
    if local.is_file() and local.stat().st_size > 0:
        return _serve_local(local, _mime_for(file_id, str(local)), rng)
    now = time.time()
    cached = _PATH_CACHE.get(file_id)
    path = cached[0] if cached and cached[1] > now else ""
    if not path:
        from bot.main import bot
        try:
            tg_file = await bot.get_file(file_id)
        except Exception as exc:
            logger.warning("get_file failed: %s", type(exc).__name__)
            raise HTTPException(status_code=404, detail="文件不存在或已失效") from exc
        path = tg_file.file_path or ""
        if not path:
            raise HTTPException(status_code=404, detail="无 file_path")
        _PATH_CACHE[file_id] = (path, now + _PATH_TTL)
    url = f"https://api.telegram.org/file/bot{settings.bot_token}/{path}"
    mime = _mime_for(file_id, path)
    open_ended = (rng or "").replace(" ", "").lower() == "bytes=0-"
    if (not rng or open_ended) and file_id not in _DL:
        return await _tee_stream(file_id, url, local, mime, ranged=open_ended)
    # Specific byte range (e.g. Safari's bytes=0-1 probe, or seeking). Warm the disk cache in
    # the background; if Telegram answers this range with a real 206, pass it straight through,
    # otherwise wait for the cached copy and answer the range locally.
    task = _BG.get(file_id)
    if task is None or task.done():
        task = asyncio.create_task(_download(file_id, url, local))
        _BG[file_id] = task
        task.add_done_callback(lambda _t, k=file_id: _BG.pop(k, None) if _BG.get(k) is _t else None)
    try:
        req = _http().build_request("GET", url, headers={"Range": rng})
        resp = await _http().send(req, stream=True)
    except Exception:
        resp = None
    if resp is not None and resp.status_code == 206 and resp.headers.get("content-range"):
        headers = dict(_CACHE_HDR)
        headers["Content-Range"] = resp.headers["content-range"]
        if resp.headers.get("content-length"):
            headers["Content-Length"] = resp.headers["content-length"]

        async def passthrough():
            try:
                async for chunk in resp.aiter_bytes(_CHUNK):
                    yield chunk
            finally:
                await resp.aclose()

        return StreamingResponse(passthrough(), status_code=206, media_type=mime, headers=headers)
    if resp is not None:
        await resp.aclose()
    try:
        ok = await asyncio.wait_for(asyncio.shield(task), timeout=60)
    except Exception:
        ok = False
    if (ok or local.is_file()) and local.is_file():
        return _serve_local(local, mime, rng)
    raise HTTPException(status_code=502, detail="拉取 Telegram 文件失败")


# ---- Card thumbnails -----------------------------------------------------------------------
# Home / carousel / favourites cards are ~120–170 css px wide but used to load the full Telegram
# photo (up to 1280 px, often 150–300 KB each). /api/media/thumb/<file_id>?w=480 serves a
# downscaled JPEG, cached on disk next to the originals (same LRU cap). Anything that can't be
# thumbnailed (video ids, Pillow missing, decode error, already small) falls back to the original.
_THUMB_WIDTHS = (240, 360, 480, 720)
_THUMB_LOCKS: Dict[str, asyncio.Lock] = {}


def thumb_width(w: int | None) -> int:
    try:
        n = int(w or 480)
    except (TypeError, ValueError):
        n = 480
    for cand in _THUMB_WIDTHS:
        if n <= cand:
            return cand
    return _THUMB_WIDTHS[-1]


def make_thumb(data: bytes, width: int) -> bytes | None:
    """Downscale an image to `width` px wide (JPEG). None = keep the original."""
    try:
        import io

        from PIL import Image, ImageOps
    except Exception:
        return None
    try:
        im = Image.open(io.BytesIO(data))
        if im.width * im.height > 50_000_000:
            return None
        if getattr(im, "is_animated", False):
            return None
        try:
            im.draft("RGB", (width, width * 4))  # fast JPEG DCT downscale before the real resize
        except Exception:
            pass
        im = ImageOps.exif_transpose(im)
        if im.width <= width:
            return None
        height = max(1, round(im.height * width / im.width))
        im = im.convert("RGB").resize((width, height), Image.LANCZOS)
        out = io.BytesIO()
        im.save(out, "JPEG", quality=78, optimize=True, progressive=True)
        body = out.getvalue()
        return body if len(body) < len(data) else None
    except Exception:
        return None


def _valid_file_id(fid: str) -> bool:
    return bool(fid) and len(fid) <= 200 and all(c.isalnum() or c in "-_" for c in fid)


async def _ensure_original(file_id: str) -> Path | None:
    local = _disk_path(file_id)
    if local.is_file() and local.stat().st_size > 0:
        return local
    settings = get_settings()
    if not settings.bot_token:
        return None
    now = time.time()
    cached = _PATH_CACHE.get(file_id)
    path = cached[0] if cached and cached[1] > now else ""
    if not path:
        from bot.main import bot
        try:
            tg_file = await bot.get_file(file_id)
        except Exception:
            return None
        path = tg_file.file_path or ""
        if not path:
            return None
        _PATH_CACHE[file_id] = (path, now + _PATH_TTL)
    url = f"https://api.telegram.org/file/bot{settings.bot_token}/{path}"
    ok = await _download(file_id, url, local)
    return local if ok and local.is_file() else None


@router.get("/media/thumb/{file_id:path}")
async def api_media_thumb(file_id: str, request: Request, w: int = 480):
    fid = str(file_id or "")
    fallback = RedirectResponse(url=f"/api/media/file/{fid}", status_code=307)
    if not _valid_file_id(fid) or fid.startswith(("BAAC", "BQAC")):
        return fallback
    width = thumb_width(w)
    thumb = _disk_path(f"thumb:{width}:{fid}")
    if thumb.is_file() and thumb.stat().st_size > 0:
        return _serve_local(thumb, "image/jpeg", request.headers.get("range"))
    lock = _THUMB_LOCKS.setdefault(f"{width}:{fid}", asyncio.Lock())
    try:
        async with lock:
            if not (thumb.is_file() and thumb.stat().st_size > 0):
                local = await _ensure_original(fid)
                if local is None:
                    return fallback
                data = await asyncio.to_thread(local.read_bytes)
                body = await asyncio.to_thread(make_thumb, data, width)
                if body is None:
                    # already small / not an image we can shrink: serve the original as-is
                    return _serve_local(local, _mime_for(fid, str(local)), request.headers.get("range"))
                tmp = thumb.with_name(thumb.name + f".{os.getpid()}.part")
                tmp.write_bytes(body)
                tmp.replace(thumb)
                _evict()
    except Exception:
        logger.warning("thumb failed", exc_info=False)
        return fallback
    finally:
        if not lock.locked():
            _THUMB_LOCKS.pop(f"{width}:{fid}", None)
    return _serve_local(thumb, "image/jpeg", request.headers.get("range"))
