"""首页多层筛选：价格档 / 标签 / 营业中 / 距离（按区中心或资料自带的大致坐标估算）。

All pure helpers live here so the feed, the carousel and the facets endpoint apply exactly the
same rules. Listing coordinates are never sent to the client: only a rounded distance label.
"""
from __future__ import annotations

import math
import re
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

# --- price ---------------------------------------------------------------------------------

_NUM = re.compile(r"(\d+(?:\.\d+)?)\s*([kK千wW万])?")


def parse_price(text: Any) -> Optional[int]:
    """First sensible number in a free-text price: "800P 1500PP" → 800, "6000+" → 6000,
    "1.2k/次" → 1200, "套餐详询" → None. (The publish form used to glue every digit together,
    so "800p 1500pp" was stored as 8001500.)"""
    s = str(text or "").replace(",", "").replace("，", "")
    for m in _NUM.finditer(s):
        try:
            v = float(m.group(1))
        except ValueError:
            continue
        unit = (m.group(2) or "").lower()
        if unit in ("k", "千"):
            v *= 1000
        elif unit in ("w", "万"):
            v *= 10000
        if 50 <= v <= 200000:
            return int(round(v))
    return None


def _nice(v: float) -> int:
    step = 100 if v < 2000 else 500 if v < 6000 else 1000
    return max(step, int(round(v / step)) * step)


DEFAULT_CUTS = [600, 1000, 1500]


def price_bands(prices: Iterable[int]) -> List[Dict[str, Any]]:
    """3–4 bands from the city's own prices (quartile cut points, rounded to nice numbers).
    Fewer than 6 priced listings → fixed cuts. Every band carries its live count."""
    vals = sorted(int(p) for p in prices if p)
    cuts: List[int] = []
    if len(vals) >= 6:
        for q in (0.25, 0.5, 0.75):
            c = _nice(vals[min(len(vals) - 1, int(len(vals) * q))])
            if c not in cuts and c > vals[0]:
                cuts.append(c)
    if len(cuts) < 2:
        cuts = list(DEFAULT_CUTS)
    cuts = sorted(set(cuts))
    bands: List[Dict[str, Any]] = []
    edges = [None] + cuts + [None]
    for lo, hi in zip(edges[:-1], edges[1:]):
        if lo is None:
            label, key = f"{hi}以下", f"-{hi}"
        elif hi is None:
            label, key = f"{lo}以上", f"{lo}-"
        else:
            label, key = f"{lo}–{hi}", f"{lo}-{hi}"
        n = sum(1 for v in vals if (lo is None or v >= lo) and (hi is None or v < hi))
        bands.append({"key": key, "label": label, "min": lo, "max": hi, "count": n})
    return bands


def parse_band(key: Optional[str]) -> Tuple[Optional[int], Optional[int]]:
    """"600-1000" → (600, 1000) meaning 600 ≤ p < 1000; "-600" / "1500-" open-ended."""
    m = re.fullmatch(r"\s*(\d{0,6})\s*-\s*(\d{0,6})\s*", str(key or ""))
    if not m or not (m.group(1) or m.group(2)):
        return None, None
    lo = int(m.group(1)) if m.group(1) else None
    hi = int(m.group(2)) if m.group(2) else None
    return lo, hi


# --- tags ----------------------------------------------------------------------------------

def norm_tag(t: Any) -> str:
    return re.sub(r"\s+", "", str(t or "")).lstrip("#＃")[:16]


def parse_tags(raw: Optional[str]) -> List[str]:
    out: List[str] = []
    for t in re.split(r"[,，]", str(raw or "")):
        n = norm_tag(t)
        if n and n not in out:
            out.append(n)
    return out[:8]


def tag_variants(tags: List[str]) -> List[str]:
    out: List[str] = []
    for t in tags:
        out += [t, "#" + t]
    return out


def top_tags(tag_lists: Iterable[Iterable[Any]], n: int = 12) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for tags in tag_lists:
        seen = set()
        for t in tags or []:
            k = norm_tag(t)
            if k and k not in seen:
                seen.add(k)
                counts[k] = counts.get(k, 0) + 1
    items = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    return [{"tag": k, "count": c} for k, c in items[:n] if c >= 1]


# --- distance ------------------------------------------------------------------------------

# Approximate district / town centres (public map data, ~1 km accuracy). Distance is an estimate
# from the user's point to the centre of the listing's district, unless the listing carries its
# own approximate coordinates. Nothing here is ever sent to the client.
CENTERS: Dict[str, Dict[str, Tuple[float, float]]] = {
    "深圳": {"福田": (22.541, 114.055), "罗湖": (22.548, 114.131), "南山": (22.533, 113.930), "宝安": (22.555, 113.884),
           "龙岗": (22.720, 114.247), "龙华": (22.697, 114.045), "盐田": (22.557, 114.236), "坪山": (22.708, 114.346),
           "光明": (22.749, 113.936), "大鹏": (22.593, 114.479)},
    "广州": {"天河": (23.125, 113.361), "越秀": (23.129, 113.267), "海珠": (23.084, 113.317), "荔湾": (23.126, 113.244),
           "白云": (23.200, 113.273), "黄埔": (23.181, 113.480), "番禺": (22.938, 113.384), "花都": (23.404, 113.220),
           "南沙": (22.801, 113.525), "从化": (23.548, 113.586), "增城": (23.261, 113.810)},
    "东莞": {"莞城": (23.021, 113.751), "南城": (23.003, 113.742), "东城": (23.030, 113.780), "万江": (23.043, 113.722),
           "虎门": (22.820, 113.670), "长安": (22.810, 113.800), "厚街": (22.940, 113.670), "常平": (22.970, 114.030),
           "塘厦": (22.800, 114.080), "松山湖": (22.920, 113.880), "寮步": (22.990, 113.870), "大朗": (22.940, 113.940),
           "凤岗": (22.740, 114.140), "樟木头": (22.910, 114.070), "黄江": (22.880, 113.990), "清溪": (22.840, 114.160)},
    "佛山": {"禅城": (23.023, 113.122), "南海": (23.029, 113.143), "顺德": (22.805, 113.293), "三水": (23.156, 112.897),
           "高明": (22.900, 112.892)},
    "珠海": {"香洲": (22.271, 113.543), "斗门": (22.209, 113.296), "金湾": (22.147, 113.363), "横琴": (22.120, 113.530)},
    "惠州": {"惠城": (23.084, 114.383), "惠阳": (22.789, 114.456), "博罗": (23.172, 114.289), "惠东": (22.985, 114.720),
           "大亚湾": (22.750, 114.530), "仲恺": (23.030, 114.300)},
    "上海": {"黄浦": (31.231, 121.484), "徐汇": (31.188, 121.437), "长宁": (31.220, 121.424), "静安": (31.229, 121.448),
           "普陀": (31.249, 121.396), "虹口": (31.265, 121.505), "杨浦": (31.259, 121.526), "闵行": (31.113, 121.382),
           "宝山": (31.405, 121.489), "嘉定": (31.375, 121.266), "浦东": (31.221, 121.544), "浦东新区": (31.221, 121.544),
           "松江": (31.032, 121.228), "青浦": (31.151, 121.124), "奉贤": (30.918, 121.474), "金山": (30.742, 121.342)},
    "北京": {"朝阳": (39.921, 116.443), "海淀": (39.959, 116.298), "东城": (39.928, 116.416), "西城": (39.912, 116.366),
           "丰台": (39.858, 116.287), "石景山": (39.906, 116.223), "通州": (39.909, 116.657), "昌平": (40.220, 116.231),
           "大兴": (39.727, 116.341), "顺义": (40.130, 116.654)},
}

RADII = (3, 5, 10)
RADII_PINNED = (1, 3, 5, 10)  # 「1km 内」 only once listings carry their own map pin


def _dkey(raw: Any) -> str:
    from bot.services.search_service import district_key
    return district_key(raw)


def center_of(city: Any, district: Any) -> Optional[Tuple[float, float]]:
    table = CENTERS.get(str(city or "")) or {}
    k = _dkey(district)
    if not k:
        return None
    if k in table:
        return table[k]
    for name, pt in table.items():  # "南山蛇口" / "龙岗布吉" → 南山 / 龙岗
        if k.startswith(name):
            return pt
    return None


def point_of(lamp: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    la, ln = lamp.get("approx_lat"), lamp.get("approx_lng")
    try:
        if la is not None and ln is not None:
            return (round(round(float(la) / 0.005) * 0.005, 3), round(round(float(ln) / 0.005) * 0.005, 3))  # ~500 m grid
    except (TypeError, ValueError):
        pass
    return center_of(lamp.get("city"), lamp.get("district"))


def haversine_km(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    r = 6371.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dphi, dl = math.radians(b[0] - a[0]), math.radians(b[1] - a[1])
    h = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def has_pin(lamp: Dict[str, Any]) -> bool:
    return lamp.get("approx_lat") is not None and lamp.get("approx_lng") is not None


def distance_label(km: Optional[float], precise: bool = False) -> str:
    """precise = both ends are real (rounded) points, not a district-centre estimate."""
    if km is None:
        return ""
    if precise and km < 1.0:
        return "1km内"
    if precise and km < 1.5:
        return "约1km"
    if km < 1.5:
        return "<2km"
    if km < 10:
        return f"约{int(round(km))}km"
    return f"约{int(round(km / 5.0) * 5)}km"


def round_center(lat: Optional[float], lng: Optional[float]) -> Optional[Tuple[float, float]]:
    try:
        if lat is None or lng is None:
            return None
        la, ln = float(lat), float(lng)
    except (TypeError, ValueError):
        return None
    if not (-90 <= la <= 90 and -180 <= ln <= 180):
        return None
    return (round(round(la / 0.005) * 0.005, 3), round(round(ln / 0.005) * 0.005, 3))  # ~500 m grid


# --- one predicate for feed + carousel -----------------------------------------------------

class Filter:
    def __init__(self, *, price: Optional[str] = None, tags: Optional[str] = None, open_only: bool = False,
                 lat: Optional[float] = None, lng: Optional[float] = None, radius: Optional[float] = None,
                 sort: str = "new", exact_center: bool = True, service: Optional[str] = None, public_only: bool = False) -> None:
        self.band = parse_band(price)
        self.coupon_ids = None  # set of lamp_ids with a live coupon when 「有优惠」 is on
        self.exact_center = bool(exact_center)  # False when the centre is a picked 区
        self.tags = parse_tags(tags)
        self.service = service if service in ("all", "open", "rest") else ("open" if open_only else "all")
        self.open_only = self.service == "open"
        self.public_only = bool(public_only)
        self.center = round_center(lat, lng)
        r = None
        try:
            r = float(radius) if radius else None
        except (TypeError, ValueError):
            r = None
        self.radius = r if (r and self.center and 0 < r <= 50) else None
        self.sort = sort if sort in ("new", "reviews", "near") else "new"
        if self.sort == "near" and not self.center:
            self.sort = "new"

    @property
    def has_price(self) -> bool:
        return self.band != (None, None)

    @property
    def needs_python(self) -> bool:
        """营业中 / 距离 are computed per listing, not in SQL."""
        return self.service != "all" or self.radius is not None or self.sort == "near" or self.coupon_ids is not None

    def active_count(self, district: Optional[str] = None) -> int:
        return sum([bool(district), self.has_price, bool(self.tags), self.service != "all", self.radius is not None,
                    self.coupon_ids is not None])

    def match_sql_side(self, lamp: Dict[str, Any]) -> bool:
        """Price + tags, for lists that did not come from SQL (the carousel)."""
        lo, hi = self.band
        if self.has_price:
            p = lamp.get("price")
            if p is None:
                return False
            if lo is not None and p < lo:
                return False
            if hi is not None and p >= hi:
                return False
        if self.tags:
            have = {norm_tag(t) for t in (lamp.get("tags") or [])}
            if not have.intersection(self.tags):
                return False
        return True

    def distance(self, lamp: Dict[str, Any]) -> Optional[float]:
        if not self.center:
            return None
        pt = point_of(lamp)
        return haversine_km(self.center, pt) if pt else None

    def precise(self, lamp: Dict[str, Any]) -> bool:
        return bool(self.center) and self.exact_center and has_pin(lamp)

    def label(self, lamp: Dict[str, Any]) -> str:
        return distance_label(self.distance(lamp), self.precise(lamp))

    def match_python_side(self, lamp: Dict[str, Any]) -> bool:
        """营业中 / 半径. `lamp` already went through _with_shop (open_code)."""
        if self.service != "all" and lamp.get("open_code") != self.service:
            return False
        if self.coupon_ids is not None and lamp.get("lamp_id") not in self.coupon_ids:
            return False
        if self.radius is not None:
            km = self.distance(lamp)
            if km is None or km > self.radius:
                return False
        return True


_FACETS: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def facets_cached(city: str) -> Optional[Dict[str, Any]]:
    hit = _FACETS.get(city or "")
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    return None


def facets_store(city: str, data: Dict[str, Any]) -> None:
    _FACETS[city or ""] = (time.time(), data)
