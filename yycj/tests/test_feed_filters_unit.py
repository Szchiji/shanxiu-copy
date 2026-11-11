from bot.services import feed_filters as ff
from bot.services.search_service import clean_price


def test_parse_price():
    assert ff.parse_price("800P 1500PP") == 800
    assert ff.parse_price("6000+") == 6000
    assert ff.parse_price("1.2k/次") == 1200
    assert ff.parse_price("套餐详询") is None
    assert ff.parse_price("1P 600") == 600  # 1 is not a price
    assert clean_price(8001500, "800p 1500pp") == 800
    assert clean_price(700, "面议") == 700
    assert clean_price(8001500, "") is None


def test_bands_from_data_and_default():
    b = ff.price_bands([500, 600, 700, 800, 900, 1000, 1200, 1500, 2000])
    assert b[0]["key"].startswith("-") and b[-1]["key"].endswith("-")
    assert sum(x["count"] for x in b) == 9
    d = ff.price_bands([800])
    assert [x["key"] for x in d] == ["-600", "600-1000", "1000-1500", "1500-"]
    assert ff.parse_band("600-1000") == (600, 1000)
    assert ff.parse_band("-600") == (None, 600)
    assert ff.parse_band("junk") == (None, None)


def test_tags_and_distance():
    assert ff.parse_tags("#新人,温柔,新人") == ["新人", "温柔"]
    t = ff.top_tags([["#新人", "温柔"], ["新人"], []])
    assert t[0] == {"tag": "新人", "count": 2}
    assert ff.center_of("深圳", "南山区") == ff.CENTERS["深圳"]["南山"]
    assert ff.center_of("深圳", "南山蛇口") == ff.CENTERS["深圳"]["南山"]
    assert ff.center_of("深圳", "火星") is None
    f = ff.Filter(lat=22.533, lng=113.930, radius=5, sort="near")
    assert f.distance({"city": "深圳", "district": "南山"}) < 1
    assert ff.distance_label(0.4) == "<2km" and ff.distance_label(4.4) == "约4km" and ff.distance_label(23) == "约25km"
    # near without a centre falls back to newest
    assert ff.Filter(sort="near").sort == "new"
    # listing coords are snapped to a ~500 m grid before any distance is computed
    assert ff.point_of({"approx_lat": 22.53337, "approx_lng": 113.93049}) == (22.535, 113.93)


def test_directory_render():
    from bot.services.channel_directory import render
    lid = "88618ee0-6c79-49d2-8fc3-9259b6a4f5eb"
    t = render("深圳", [{"lamp_id": lid, "title": "小<南>", "district": "南山区"}, {"lamp_id": lid, "title": "b", "district": ""}], "MoonBot")
    assert "南山" in t and "其他" in t and "start=s_88618ee06c7949d28fc39259b6a4f5eb" in t and "小&lt;南&gt;" in t


def test_net_cache_key_includes_filters():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "miniapp" / "net-cache.js").read_text(encoding="utf-8")
    line = [l for l in src.splitlines() if '"city", "q", "district"' in l][0]
    for k in ("at", "radius", "price", "tags", "open"):
        assert f'"{k}"' in line


def test_start_payload_kinds():
    from bot.services.share_gate import card_page_url, parse_start_payload
    lid = "88618ee0-6c79-49d2-8fc3-9259b6a4f5eb"
    assert parse_start_payload("s_88618ee06c7949d28fc39259b6a4f5eb") == ("s", lid)
    assert parse_start_payload("r_88618ee06c7949d28fc39259b6a4f5eb") == ("r", lid)
    assert parse_start_payload("w_" + lid) == ("w", lid)
    assert parse_start_payload(lid) == ("s", lid)
    assert parse_start_payload("home") == ("s", None)
    assert parse_start_payload("x_88618ee06c7949d28fc39259b6a4f5eb")[1] is None


def test_caption_html_escapes_and_links():
    from bot.services.broadcast import caption_html, links_html, start_url
    lid = "88618ee0-6c79-49d2-8fc3-9259b6a4f5eb"
    assert start_url("B", lid, "w") == "https://t.me/B?start=w_88618ee06c7949d28fc39259b6a4f5eb"
    out = caption_html("小<b>&" + "字" * 2000, links_html("B", lid))
    assert out.startswith("小&lt;b&gt;&amp;") and out.endswith("写评价</a>") and "<b>" not in out
