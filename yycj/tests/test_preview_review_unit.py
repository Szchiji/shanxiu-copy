"""Safe fixture server serves real assets but has no live API route or mutation path."""
import importlib.util
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("local_review_server", ROOT / "scripts/preview_review.py")
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)


@pytest.fixture(scope="module")
def server():
    http = preview.ThreadingHTTPServer(("127.0.0.1", 0), preview.PreviewHandler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    yield "http://127.0.0.1:" + str(http.server_port)
    http.shutdown()
    thread.join(timeout=2)
    http.server_close()


def test_preview_serves_actual_html_and_bundle_with_separate_bootstrap(server):
    with urlopen(server + "/app/go.html?preview=home") as r:
        html = r.read().decode()
        assert '<script src="/preview-fixtures.js"></script>' in html
        assert 'id="hubHomeEntry"' in html and 'app.bundle.js?v=20261112b' in html
        assert 'src="./telegram-web-app.js' not in html
        assert "connect-src 'self'" in r.headers["Content-Security-Policy"]
    with urlopen(server + "/app/app.bundle.js?v=20261112b") as r:
        assert r.read() == (ROOT / "miniapp/app.bundle.js").read_bytes()
    # Production HTML is unchanged by preview injection and has no preview bootstrap.
    assert "preview-fixtures" not in (ROOT / "miniapp/go.html").read_text()


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_preview_never_forwards_api_requests_or_mutations(server, method):
    req = Request(server + "/api/admin/listings/example/renew", method=method)
    with pytest.raises(HTTPError) as exc:
        urlopen(req)
    assert exc.value.code == 403


def test_preview_images_are_explicit_example_placeholders(server):
    with urlopen(server + "/preview-assets/cover1.svg") as r:
        assert "非真实资料" in r.read().decode()
    with urlopen(server + "/preview-fixtures.js") as r:
        js = r.read().decode()
        assert "LOCAL_PREVIEW_NOT_SIGNED" in js
        assert "本地评审禁止外部网络请求" in js
