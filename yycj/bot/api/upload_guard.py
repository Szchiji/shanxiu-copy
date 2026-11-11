"""ASGI guard for /api/media/upload: cap request body size while it streams in."""
from __future__ import annotations

import json

UPLOAD_PATH = "/api/media/upload"
# 9 files x 20MB plus multipart overhead.
MAX_BODY = 9 * 20 * 1024 * 1024 + 1024 * 1024


class _TooLarge(Exception):
    pass


async def _send_413(send) -> None:
    body = json.dumps({"detail": "上传内容过大（单个文件 ≤20MB）"}, ensure_ascii=False).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": 413,
        "headers": [(b"content-type", b"application/json; charset=utf-8"), (b"content-length", str(len(body)).encode())],
    })
    await send({"type": "http.response.body", "body": body})


class UploadSizeGuard:
    def __init__(self, app, max_body: int = MAX_BODY):
        self.app = app
        self.max_body = max_body

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("path") != UPLOAD_PATH or scope.get("method") != "POST":
            await self.app(scope, receive, send)
            return
        for k, v in scope.get("headers") or []:
            if k == b"content-length":
                try:
                    if int(v) > self.max_body:
                        await _send_413(send)
                        return
                except ValueError:
                    pass
        seen = 0
        started = False

        async def guarded_receive():
            nonlocal seen
            msg = await receive()
            if msg.get("type") == "http.request":
                seen += len(msg.get("body") or b"")
                if seen > self.max_body:
                    raise _TooLarge()
            return msg

        async def tracked_send(msg):
            nonlocal started
            if msg.get("type") == "http.response.start":
                started = True
            await send(msg)

        try:
            await self.app(scope, guarded_receive, tracked_send)
        except _TooLarge:
            if not started:
                await _send_413(send)
