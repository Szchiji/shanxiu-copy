#!/usr/bin/env python3
"""LOCAL-ONLY UI fixture server. Real project markup/assets; fake APIs, never production.

Run with the repo venv; no credentials, Telegram calls, DB, or production endpoints.
Only binds loopback. All /api requests are blocked at the server as a second barrier.
"""
import argparse
import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
import mimetypes

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bot.services.home_feed import DEFAULT_OPS, CITY_KEYS


class PreviewHandler(BaseHTTPRequestHandler):
    def reply(self, body, content_type="text/html; charset=utf-8", status=200):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        # No external scripts/frames, form submits, fetch, or WebSocket connections.
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; frame-src 'none'; object-src 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        self.reply('{"detail":"LOCAL PREVIEW: server-side mutations disabled"}', "application/json", 403)

    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith("/api/"):
            self.reply('{"detail":"LOCAL PREVIEW: APIs are browser-memory fixtures only"}', "application/json", 403)
            return
        if path == "/":
            self.reply('''<!doctype html><meta charset="utf-8"><title>月影车姬 · 本地评审</title><style>body{background:#0F2420;color:#F1EEE4;font:18px sans-serif;padding:40px}a{color:#D4B26A;display:block;margin:20px}</style><h1>月影车姬 · 本地评审</h1><p>示例数据 · 未连接生产 · 不会发送 Telegram</p><a href="/app/go.html?preview=home">手机首页（无弹窗）</a><a href="/app/go.html?preview=popup&fresh=1">手机进入弹窗（重置今日隐藏）</a><a href="/app/desk.html?preview=admin&city=深圳&section=popup">深圳弹窗后台设置</a><a href="/app/desk.html?preview=admin&scope=global&section=popup">全站弹窗后台设置</a><p>手机请设置浏览器视口 390×844；后台 1440×1000。页面使用实际项目 HTML/CSS/JS，只有 API 和登录是本地模拟。</p>''')
            return
        if path == "/preview-fixtures.js":
            bootstrap = (ROOT / "scripts/preview/fixtures.js").read_text()
            config = json.dumps({"settings": DEFAULT_OPS, "keys": CITY_KEYS}, ensure_ascii=False)
            self.reply("window.__PREVIEW_CONFIG=" + config + ";\n" + bootstrap, "text/javascript; charset=utf-8")
            return
        if path.startswith("/preview-assets/"):
            try:
                n = int(Path(path).stem.removeprefix("cover"))
            except ValueError:
                n = 1
            colors = ["#244D43", "#305446", "#294A4C", "#3B5140"]
            self.reply(f'''<svg xmlns="http://www.w3.org/2000/svg" width="400" height="600" viewBox="0 0 400 600"><rect width="400" height="600" fill="{colors[n % 4]}"/><circle cx="200" cy="190" r="85" fill="#D4B26A" opacity=".22"/><path d="M45 525 Q50 300 200 305 Q350 300 355 525" fill="#D4B26A" opacity=".15"/><text x="200" y="225" text-anchor="middle" fill="#F1EEE4" font-family="sans-serif" font-size="48">示例 {n}</text><text x="200" y="565" text-anchor="middle" fill="#D4B26A" font-family="sans-serif" font-size="25">评审占位图 · 非真实资料</text></svg>''', "image/svg+xml")
            return
        if not path.startswith("/app/"):
            self.reply("Not found", status=404)
            return
        base = (ROOT / "miniapp").resolve()
        file = (base / path[len("/app/"):]).resolve()
        if not file.is_relative_to(base) or not file.is_file():
            self.reply("Not found", status=404)
            return
        if file.suffix == ".html":
            text = file.read_text()
            # No real Telegram SDK in preview. Actual app scripts execute against our safe stub.
            text = re.sub(r'<script\b[^>]*src=["\'][^"\']*telegram-web-app\.js[^"\']*["\'][^>]*>\s*</script>', "", text, flags=re.I)
            text = text.replace("<head>", '<head><script src="/preview-fixtures.js"></script>', 1)
            self.reply(text)
        else:
            self.reply(file.read_bytes(), mimetypes.guess_type(file.name)[0] or "application/octet-stream")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8874)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), PreviewHandler)
    print(f"LOCAL REVIEW ONLY — http://127.0.0.1:{args.port}/ — example data, no production/Telegram", flush=True)
    server.serve_forever()
