"""Review export contains actual listing-card markup and the unchanged relist message."""
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]

def _export(tmp_path):
    spec = importlib.util.spec_from_file_location("export_listing_layout_review", ROOT / "scripts/export_listing_layout_review.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = tmp_path / "review"
    mod.export(out)
    return out

def test_export_uses_real_assets_and_keeps_relist_message(tmp_path):
    out = _export(tmp_path)
    mobile = (out / "mobile-listings.html").read_text()
    assert "function listingActions" in mobile
    assert "listing-actions" in mobile
    assert "续期…" not in mobile
    assert ">续期</button>" in mobile or "续期</button>" in mobile
    assert "flex-direction: column" in mobile
    assert "min-height: 44px" in mobile
    assert "资料已经上架；未重发频道或通知，有效期不变。" in mobile
    assert "小红" in mobile and "小明" in mobile
    assert "telegram.org" not in mobile
    assert "LOCAL_PREVIEW_ONLY" in mobile
    assert 'data-workspace="true"' in mobile
    assert (out / "review-index.html").is_file()
    assert (out / "mobile-relist-alert.html").is_file()

def test_admin_css_prevents_edit_result_auto_column():
    css = (ROOT / "miniapp/admin.css").read_text()
    assert "#adminListings > .pick-item" in css
    assert "flex-direction: column" in css
    # The old two-column rule must not win for listing cards.
    assert "display: flex !important" in css
