from pathlib import Path

from bot.services import feed_filters as ff
from bot.services.places import snap


def test_snap_grid_and_labels():
    assert snap(22.54321, 114.05876) == (22.545, 114.06)
    assert snap(None, 1) is None and snap(91, 0) is None
    assert ff.distance_label(0.4, precise=True) == "1km内"
    assert ff.distance_label(0.4) == "<2km"
    assert ff.distance_label(1.2, precise=True) == "约1km"
    assert ff.point_of({"approx_lat": 22.5431, "approx_lng": 114.0588}) == (22.545, 114.06)
    flt = ff.Filter(lat=22.54, lng=114.06, radius=1, exact_center=False)
    assert not flt.precise({"approx_lat": 22.54, "approx_lng": 114.06})
    assert 1 in ff.RADII_PINNED and 1 not in ff.RADII


def test_leaflet_vendored_and_bundled():
    mini = Path(__file__).resolve().parent.parent / "miniapp"
    assert (mini / "vendor/leaflet/leaflet.js").stat().st_size > 100_000
    js = (mini / "app.bundle.js").read_text("utf-8")
    assert "window.yycjPlace" in js and "/api/me/place" in js
    assert "leaflet.js" not in (mini / "index.html").read_text("utf-8")   # lazy-loaded, not on the home page
