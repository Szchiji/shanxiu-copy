"""The Mini App bundles must be rebuilt from the sources and keep the page order."""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_miniapp as bm  # noqa: E402


def test_bundles_are_up_to_date():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_miniapp.py"), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_pages_load_only_the_bundles():
    for page in ("go.html", "index.html"):
        html = (ROOT / "miniapp" / page).read_text("utf-8")
        local_js = re.findall(r'<script src="\./([^"?]+)', html)
        local_css = re.findall(r'<link rel="stylesheet" href="\./([^"?]+)', html)
        assert local_js == ["app.bundle.js"] and local_css == ["app.bundle.css"]
        assert "<style>" not in html  # the inline block lives in home-inline.css, at the same cascade position


def test_go_and_index_share_bundle_version():
    vers = []
    for page in ("go.html", "index.html"):
        html = (ROOT / "miniapp" / page).read_text("utf-8")
        js = re.search(r"app\.bundle\.js\?v=([A-Za-z0-9]+)", html)
        css = re.search(r"app\.bundle\.css\?v=([A-Za-z0-9]+)", html)
        assert js and css, page
        vers.append((js.group(1), css.group(1)))
    assert vers[0] == vers[1]


def test_every_source_is_one_iife_and_wrapped():
    js = bm.build_js()
    for f in bm.JS:
        src = (ROOT / "miniapp" / f).read_text("utf-8")
        body = re.sub(r"^\s*(//[^\n]*\n|/\*.*?\*/\s*)*", "", src, flags=re.S).strip()
        assert body.startswith("(") and body.rstrip(";").endswith(")()") or body.rstrip(";").endswith("})()"), f
        assert f"/* ---- {f} ---- */\ntry {{" in js
    assert js.index("resilience.js") < js.index("app.js ----") < js.index("detail-kit.js ----") < js.index("detail-xhs.js ----")
