"""Map-first-batch contracts. Existing map endpoint tests cover server filter/privacy rules."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_map_only_uses_published_district_group_points():
    src = (ROOT / 'miniapp/map-results.js').read_text()
    assert 'const p={lat:g.lat,lng:g.lng}' in src
    assert 'approx_lat' not in src and 'approx_lng' not in src
    assert 'geolocation' not in src and 'Telegram' not in src
    assert '.slice(0,600)' in src and '不偏移到虚构位置' in src


def test_guest_assets_versioned_without_cancelled_native_integration():
    for name in ['index.html', 'go.html']:
        text = (ROOT / 'miniapp' / name).read_text()
        assert 'app.bundle.js?v=20261112b' in text
        assert 'app.bundle.css?v=20261112b' in text
        assert 'telegram-native' not in text
    manifest = (ROOT / 'scripts/build_miniapp.py').read_text()
    assert manifest.index('"edit-history-ui.js"') < manifest.index('"map-results.js"') < manifest.index('"community-ui.js"')
    assert '"edit-history-ui.css"' in manifest
    assert not (ROOT / 'miniapp/telegram-native.js').exists()


def test_no_new_backend_bounds_or_coordinate_exposure():
    home = (ROOT / 'miniapp/home-ui.js').read_text()
    assert 'area:options.mapArea||null' in home
    assert 'requestSig !== filtSig()' in home
    assert 'if(seq!==loadSequence || mapMode!==!!window.__yycjMapMode' in home
    assert 'return !window.__yycjMapMode && !paused' in home


def test_map_query_still_requires_authentication(monkeypatch):
    import asyncio
    from fastapi import FastAPI
    import httpx
    from unittest.mock import AsyncMock
    from bot.api import routes_core_home as rh
    from bot.services import search_service

    query = AsyncMock()
    monkeypatch.setattr(search_service, "search_lamps", query)
    app = FastAPI()
    app.include_router(rh.router)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
            response = await client.get("/api/home?city=深圳&map_results=1")
            assert response.status_code == 401
            query.assert_not_awaited()
    asyncio.run(run())
