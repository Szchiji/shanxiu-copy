"""首页 / 我的 改版的静态检查：卡片无爱心、营业书签在左上、筛选按钮、我的分组、教程覆盖当前功能、资源版本一致。"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MINI = ROOT / "miniapp"


def _read(name):
    return (MINI / name).read_text(encoding="utf-8")


def test_card_has_no_heart_and_open_badge_top_left():
    js = _read("home-ui.js")
    card = js[js.index("function cardHtml"):js.index("function pinHtml")]
    assert "fav-btn" not in card and "data-fav" not in card
    assert "badge-open" in card
    pin = js[js.index("function pinHtml"):js.index("function paint()")]
    assert "badge-open" not in pin  # 轮播不显示营业状态
    css = _read("status-badge.css")
    assert re.search(r"\.badge-open\{position:absolute!important;top:7px;left:0;", css)
    assert "#pins .pin-card .badge-open{display:none" in css


def test_fav_only_on_detail():
    assert 'data-fav="${esc(id)}"' in _read("detail-xhs.js")
    fav = _read("fav.js")
    assert "__yycjFavLoad" in fav and "yycj:fav" in fav


def test_filter_button_roomy_label():
    js = _read("home-ui.js")
    assert '"筛选·" + nAct' in js and '"筛选"' in js and "筛选▾" not in js
    css = _read("area-filter.css")
    assert "#btnArea{flex:none;min-width:76px" in css and "#btnArea .area-sz{display:none;}" in css


def test_me_layout_sections_by_role():
    js = _read("me-layout.js")
    for k in ("meRowFav", "myClaims", "cityCoupons", "placeCard", "meNickCard", "meListCard", "myCoupon", "meRowTut", "meRowContact"):
        assert k in js
    assert '["经营", ["meTodos", "meListCard", "myCoupon"]]' in js
    assert 'data-nav": "fav"' not in js  # must not shadow the bottom-nav button for role-nav.js


def test_tutorial_mentions_current_features():
    js = _read("home-ui.js")
    tut = js[js.index("const TUTORIAL"):js.index("function tutorialHtml")]
    for w in ("筛选", "常用位置", "收藏", "领取", "想聊聊", "约课凭证", "地图", "营业时间", "续期", "核对", "标记已用"):
        assert w in tut, w


def test_assets_built_and_versioned():
    b = (ROOT / "scripts" / "build_miniapp.py").read_text(encoding="utf-8")
    assert '"me-layout.js"' in b and '"me-layout.css"' in b
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_miniapp.py"), "--check"], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    vs = set()
    for page in ("index.html", "go.html"):
        vs |= set(re.findall(r"app\.bundle\.(?:js|css)\?v=(\w+)", _read(page)))
    assert vs == {"20261112b"}
    for page in ("index.html", "go.html"):
        assert "app.bundle.js?v=20261112b" in _read(page)
        assert "app.bundle.css?v=20261112b" in _read(page)


def test_filter_reset_applies_immediately_and_keeps_facets():
    js = _read("home-ui.js")
    i = js.index('#areaPanel [data-filt-reset]')
    block = js[i:i + 400]
    assert "clearFilt()" in block and "loadFeed(true)" in block and "scrollTop()" in block
    assert "lastFacets = null" not in block  # 价格 / 标签选项不能在重置后变成「暂无」
    clr = js[js.index("function clearFilt"):js.index("function clearFilt") + 200]
    assert 'setSort("")' in clr and 'setArea("")' in clr and "removeItem(filtKey())" in clr
    assert "place" not in clr  # 不清「我的 · 常用位置」


def test_closing_panel_applies_changes():
    js = _read("home-ui.js")
    t = js[js.index("async function toggleArea"):js.index("function mountGuide")]
    assert "appliedSig !== filtSig()" in t and "loadFeed(true)" in t
    assert "appliedSig = filtSig();" in js


def test_coupon_lists_fold_done_items():
    js = _read("coupons.js")
    assert 'fold(foldKey + it.coupon_id, "已核销"' in js and 'fold("hist", `往期优惠' in js and 'fold("gdone", "已用 / 已失效"' in js
    assert "已用 ↺" not in js and "撤销" in js


def test_coupon_multi_item_form_and_strip():
    js = _read("coupons.js")
    # 发布表单：有效期 → 一项项添加（继续添加 / 编辑 / 删除）→ 发布优惠，items 一次提交
    assert "data-cpn-add" in js and "继续添加" in js and "data-cpn-ied" in js and "data-cpn-idel" in js
    assert "JSON.stringify({ days: draft.days, items })" in js and "整批共用" in js
    # 详情页每项一行，各自领取；想聊聊带上全部券码
    assert "cpn-its" in js and "remainTxt" in js and "bookTextAll(held.codes)" in js
    # 每批每人限领 1 张
    assert "每人限领1张，选一种领取" in js and "本批已领 · 每人限领1张" in js and "cpn-claim\" disabled" in js


def test_coupon_strip_sits_above_reviews():
    js = _read("coupons.js")
    assert 'rv.before(el)' in js and ':scope > #yycjReviews' in js and 'price.after(el)' not in js
