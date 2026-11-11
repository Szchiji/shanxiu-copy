"""Offline transport stays outside served assets. No browser or production network."""
from pathlib import Path
import importlib.util
import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('workspace_export',ROOT/'scripts/export_workspace_review.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)

def test_review_export_is_actual_assets_but_offline_and_outside_static(tmp_path):
    module.export(tmp_path/'review')
    text=(tmp_path/'review/index.html').read_text()
    assert 'workspaceNav' in text and 'window.yycjAdminRefresh=refresh' in text
    assert 'OFFLINE_UI_FIXTURE' in text and '所有API' in text
    assert 'connect-src \'none\'' in text and 'form-action \'none\'' in text
    assert '<script src=' not in text and '<link rel="stylesheet"' not in text
    assert 'OFFLINE_UI_FIXTURE' not in (ROOT/'miniapp/desk.html').read_text()
    assert 'srcdoc=' in (tmp_path/'review/mobile-city.html').read_text()
    assert (tmp_path/'review.zip').is_file()

def test_export_refuses_production_static_output():
    with pytest.raises(ValueError):module.export(ROOT/'miniapp/review-fixture')


def test_layout_review_covers_compact_desktop_and_mobile_variants(tmp_path):
    module.export(tmp_path/'review')
    for name in ['desktop-messages.html','desktop-community.html','desktop-channel.html','desktop-logs-list.html','desktop-logs.html']:
        text=(tmp_path/'review'/name).read_text()
        assert 'ws-config-groups' in text and 'ws-button-card' in text and 'ws-log-primary' in text
        assert "connect-src 'none'" in text and '<script src=' not in text
    for name in ['mobile-community.html','mobile-messages.html','mobile-logs-list.html']:
        text=(tmp_path/'review'/name).read_text()
        assert 'width:390px' in text and 'srcdoc=' in text
    launcher=(tmp_path/'review/review-index.html').read_text()
    assert 'desktop-messages.html' in launcher and '不是线上截图' in launcher
