// 常用位置 / 地图选点. One map picker shared by:
//   · 上架: 区 → 「在地图上选点」 (replaces the old free-text 大致位置)
//   · 我的 → 常用位置 (客人: 附近 的默认中心; 老师: 资料显示的大致位置，改了立即生效)
// The server keeps the point on a ~500 m grid and only ever shows other people 「约Xkm」.
// Leaflet is vendored and loaded only when the map opens (home page untouched).
(() => {
  if (window.yycjPlace) return;
  const V = "20261101c";
  const $ = (s) => document.querySelector(s);
  const token = () => localStorage.getItem("yycj_token") || "";
  const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  function role() { try { return JSON.parse(localStorage.getItem("yycj_user") || "{}").role || localStorage.getItem("yycj_role") || "guest"; } catch (e) { return "guest"; } }
  function toast(msg) {
    const el = $("#toast"); if (!el) return;
    el.textContent = msg; el.classList.remove("hidden", "err"); clearTimeout(toast.t); toast.t = setTimeout(() => el.classList.add("hidden"), 2600);
  }
  async function api(path, opt = {}) {
    const r = await fetch(path, Object.assign({}, opt, { headers: { "Content-Type": "application/json", Authorization: "Bearer " + token() } }));
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || "请求失败");
    return d;
  }
  // WGS-84 (GPS) → GCJ-02 (the map tiles used in mainland China), so 「定位到我」 lands on the right street.
  function outOfChina(lat, lng) { return lng < 72.004 || lng > 137.8347 || lat < 0.8293 || lat > 55.8271; }
  function tLat(x, y) { let r = -100 + 2 * x + 3 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * Math.sqrt(Math.abs(x)); r += (20 * Math.sin(6 * x * Math.PI) + 20 * Math.sin(2 * x * Math.PI)) * 2 / 3; r += (20 * Math.sin(y * Math.PI) + 40 * Math.sin(y / 3 * Math.PI)) * 2 / 3; r += (160 * Math.sin(y / 12 * Math.PI) + 320 * Math.sin(y * Math.PI / 30)) * 2 / 3; return r; }
  function tLng(x, y) { let r = 300 + x + 2 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * Math.sqrt(Math.abs(x)); r += (20 * Math.sin(6 * x * Math.PI) + 20 * Math.sin(2 * x * Math.PI)) * 2 / 3; r += (20 * Math.sin(x * Math.PI) + 40 * Math.sin(x / 3 * Math.PI)) * 2 / 3; r += (150 * Math.sin(x / 12 * Math.PI) + 300 * Math.sin(x / 30 * Math.PI)) * 2 / 3; return r; }
  function wgs2gcj(lat, lng) {
    if (outOfChina(lat, lng)) return [lat, lng];
    const a = 6378245.0, ee = 0.00669342162296594323;
    let dLat = tLat(lng - 105.0, lat - 35.0), dLng = tLng(lng - 105.0, lat - 35.0);
    const rad = lat / 180.0 * Math.PI; let magic = Math.sin(rad); magic = 1 - ee * magic * magic; const sq = Math.sqrt(magic);
    dLat = (dLat * 180.0) / ((a * (1 - ee)) / (magic * sq) * Math.PI); dLng = (dLng * 180.0) / (a / sq * Math.cos(rad) * Math.PI);
    return [lat + dLat, lng + dLng];
  }
  function locate(signal) {
    return new Promise((res) => {
      let settled = false;
      const finish = p => { if (settled) return; settled = true; clearTimeout(timer); signal?.removeEventListener("abort", fail); res(p); };
      const fail = () => finish(null), timer = setTimeout(fail, 8000);
      if (signal?.aborted) { fail(); return; }
      signal?.addEventListener("abort", fail, { once: true });
      const done = (la, ln) => {
        if (settled) return;
        if (la == null || ln == null || !Number.isFinite(+la) || !Number.isFinite(+ln) || Math.abs(+la)>90 || Math.abs(+ln)>180) { fail(); return; }
        finish(wgs2gcj(+la, +ln));
      };
      try {
        const tg = window.Telegram && window.Telegram.WebApp;
        if (tg?.LocationManager?.init && tg.LocationManager.getLocation) {
          tg.LocationManager.init(() => {
            if (settled) return;
            try { tg.LocationManager.getLocation((l) => l ? done(l.latitude, l.longitude) : fail()); } catch (e) { fail(); }
          });
          return;
        }
        if (navigator.geolocation) navigator.geolocation.getCurrentPosition((p) => done(p.coords.latitude, p.coords.longitude), fail, { enableHighAccuracy: false, timeout: 8000 });
        else fail();
      } catch (e) { fail(); }
    });
  }
  let leaflet = null;
  function loadLeaflet() {
    if (window.L && window.L.map) return Promise.resolve(window.L);
    if (leaflet) return leaflet;
    leaflet = new Promise((res, rej) => {
      let settled = false;
      const s = document.createElement("script"); s.src = "./vendor/leaflet/leaflet.js?v=" + V;
      const finish = error => {
        if (settled) return; settled = true; clearTimeout(timer); s.onload = s.onerror = null;
        if (error) { s.remove(); leaflet = null; rej(new Error("地图加载失败")); } else res(window.L);
      };
      const timer = setTimeout(() => finish(true), 8000);
      if (!document.querySelector('link[data-yycj-leaflet]')) { const css = document.createElement("link"); css.dataset.yycjLeaflet = "1"; css.rel = "stylesheet"; css.href = "./vendor/leaflet/leaflet.css?v=" + V; document.head.appendChild(css); }
      s.onload = () => finish(!window.L?.map); s.onerror = () => finish(true);
      document.head.appendChild(s);
    });
    return leaflet;
  }
  window.__yycjLoadLeaflet = loadLeaflet;
  const centers = {};
  async function centerFor(city, district) {
    if (!centers[city]) { try { centers[city] = await api("/api/geo/centers?city=" + encodeURIComponent(city || "")); } catch (e) { centers[city] = { districts: {}, center: null }; } }
    const c = centers[city]; const d = String(district || "").replace(/(新区|区|县|市|镇)$/, "");
    if (d) { if (c.districts[d]) return { pt: c.districts[d], zoom: 14 }; const k = Object.keys(c.districts).find((n) => d.startsWith(n)); if (k) return { pt: c.districts[k], zoom: 14 }; }
    return c.center ? { pt: c.center, zoom: 11 } : { pt: [22.543, 114.058], zoom: 11 };
  }
  // pick({city, district, lat, lng, title, allowClear}) → Promise<{lat,lng} | "clear" | null>
  async function pick(o = {}) {
    let sheet = $("#ppSheet");
    if (!sheet) {
      sheet = document.createElement("div"); sheet.id = "ppSheet"; sheet.className = "pp-sheet hidden";
      sheet.innerHTML = `<div class="pp-panel"><div class="pp-head"><b id="ppTitle">选位置</b><button type="button" class="btn ghost" data-pp="cancel">取消</button></div>
        <div class="pp-mapwrap"><div id="ppMap" class="pp-map"></div><div class="pp-pin" aria-hidden="true"><i></i></div><div class="pp-ring" aria-hidden="true"></div>
        <button type="button" class="pp-me" data-pp="me">◎ 定位到我</button></div>
        <p class="pp-note">拖动地图，让金色标记对准<b>大致</b>位置。只保存约 500 米范围，别人只会看到「约 X km」。</p>
        <div class="pp-actions"><button type="button" class="btn hidden" data-pp="clear">清除</button><button type="button" class="btn primary" data-pp="ok">用这个位置</button></div></div>`;
      document.body.appendChild(sheet);
    }
    $("#ppTitle").textContent = o.title || "选位置";
    sheet.querySelector('[data-pp="clear"]').classList.toggle("hidden", !o.allowClear);
    sheet.classList.remove("hidden");
    let L;
    try { L = await loadLeaflet(); } catch (e) { sheet.classList.add("hidden"); toast("地图加载失败，请稍后再试"); return null; }
    const start = (o.lat != null && o.lng != null) ? { pt: [o.lat, o.lng], zoom: 15 } : await centerFor(o.city, o.district);
    if (!pick.map) {
      pick.map = L.map("ppMap", { zoomControl: false, attributionControl: true }).setView(start.pt, start.zoom);
      L.control.zoom({ position: "bottomright" }).addTo(pick.map);
      let errs = 0, loaded = 0;
      const amap = L.tileLayer("https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}",
        { subdomains: "1234", maxZoom: 18, minZoom: 4, attribution: "© 高德地图" });
      amap.on("tileload", () => { loaded++; });
      amap.on("tileerror", () => {   // fall back to OpenStreetMap if 高德 is unreachable from this network
        if (++errs >= 4 && !loaded && !pick.osm) {
          pick.osm = true; pick.map.removeLayer(amap);
          L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18, attribution: "© OpenStreetMap" }).addTo(pick.map);
        }
      });
      amap.addTo(pick.map);
    } else {
      pick.map.invalidateSize(); pick.map.setView(start.pt, start.zoom, { animate: false });
    }
    setTimeout(() => pick.map.invalidateSize(), 60);
    return new Promise((res) => {
      let closed = false, locCtl = null;
      const onClick = async (ev) => {
        const b = ev.target.closest && ev.target.closest("[data-pp]"); if (!b) return;
        const k = b.getAttribute("data-pp");
        if (k === "me") { if (b.disabled) return; b.disabled = true; locCtl = new AbortController(); const p = await locate(locCtl.signal); if (closed) return; b.disabled = false; if (p) pick.map.setView(p, 15); else toast("定位失败，请手动拖动地图"); return; }
        closed = true; locCtl?.abort(); sheet.querySelector('[data-pp="me"]').disabled = false; sheet.removeEventListener("click", onClick); sheet.classList.add("hidden");
        if (k === "ok") { const c = pick.map.getCenter(); res({ lat: +c.lat.toFixed(5), lng: +c.lng.toFixed(5) }); }
        else res(k === "clear" ? "clear" : null);
      };
      sheet.addEventListener("click", onClick);
    });
  }
  window.yycjPlace = { pick, wgs2gcj, locate };

  // ---- 我的 → 常用位置 ----
  let place = null;
  async function loadPlace(force) {
    if (place && !force) return place;
    try { place = (await api("/api/me/place")).place; } catch (e) { place = place || { set: false }; }
    return place;
  }
  function bustHome() {
    try { Object.keys(sessionStorage).forEach((k) => { if (k.startsWith("yycj_hc_")) sessionStorage.removeItem(k); }); } catch (e) {}
    try { window.__yycjPlaceChanged && window.__yycjPlaceChanged(); } catch (e) {}
  }
  async function paintMe() {
    const me = $("#view-me"); if (!me) return;
    let card = $("#placeCard");
    if (!card) {
      card = document.createElement("div"); card.id = "placeCard"; card.className = "card place-card";
      const first = me.querySelector("#meCard"); if (first) first.after(card); else me.prepend(card);
    }
    const p = await loadPlace(false);
    const teacher = role() === "teacher" || role() === "merchant";
    card.dataset.place = p.set ? (p.district ? p.district + "附近" : "已设置") : "";
    const lead = teacher ? "也是你资料上显示的大致位置。改了马上生效，不用重新审核。" : "首页「附近 / 距我近」默认按这里算距离。";
    card.innerHTML = `<h3>常用位置</h3>
      <div class="place-row"><span class="place-dot${p.set ? " on" : ""}"></span><span class="place-st">${p.set ? "已设置 · 约 500 米范围" : "还没设置"}</span>
      <button type="button" class="btn${p.set ? "" : " primary"}" data-place="edit">${p.set ? "修改" : "在地图上选"}</button>${p.set ? '<button type="button" class="btn" data-place="clear">清除</button>' : ""}</div>
      <p class="muted place-lead">${esc(lead)} 别人只看到「约 X km」，看不到具体位置。</p>`;
  }
  async function editPlace() {
    const p = await loadPlace(false);
    const city = p.city || localStorage.getItem("yycj_city") || ($("#pubCity") && $("#pubCity").value) || "";
    const r = await pick({ city, district: p.district || "", lat: p.set ? p.lat : null, lng: p.set ? p.lng : null, title: "常用位置", allowClear: !!p.set });
    if (!r) return;
    try {
      place = (r === "clear" ? await api("/api/me/place", { method: "DELETE" }) : await api("/api/me/place", { method: "POST", body: JSON.stringify(r) })).place;
      toast(r === "clear" ? "已清除常用位置" : (place.listings ? "已保存，资料位置已更新" : "已保存常用位置"));
      bustHome(); paintMe(); paintPub();
    } catch (e) { toast(e.message || "保存失败"); }
  }

  // ---- 上架: 区 → 地图选点 (replaces 大致位置) ----
  function editing() { return !!(($("#editLampId") && $("#editLampId").value) || /改资料/.test(($("#publishTitle") && $("#publishTitle").textContent) || "")); }
  function paintPub() {
    const form = $("#publishForm"), dist = $("#pubDistrict"); if (!form || !dist) return;
    const ap = $("#pubApprox");
    if (ap) { ap.classList.add("pp-hide"); const lab = ap.previousElementSibling; if (lab && lab.tagName === "LABEL") lab.classList.add("pp-hide"); }
    let row = $("#pubPlace");
    if (!row) {
      const lab = document.createElement("label"); lab.textContent = "地图选点"; lab.id = "pubPlaceLab";
      row = document.createElement("div"); row.id = "pubPlace"; row.className = "place-row pub-place";
      dist.after(lab); lab.after(row);
    }
    if (editing()) {
      const set = place && place.set;
      row.innerHTML = `<span class="place-dot${set ? " on" : ""}"></span><span class="place-st">${set ? "已选点" : "未选点"} · 在「我的 · 常用位置」修改</span><button type="button" class="btn" data-place="goto">去修改</button>`;
      return;
    }
    const lat = form.dataset.pinLat, from = form.dataset.pinFrom;
    row.innerHTML = lat ? `<span class="place-dot on"></span><span class="place-st">已选点${from === "saved" ? "（我的常用位置）" : ""} · 约 500 米范围</span><button type="button" class="btn" data-place="pub">重选</button>`
      : `<span class="place-dot"></span><span class="place-st">先填区，再在地图上标出大致位置</span><button type="button" class="btn primary" data-place="pub">在地图上选点</button>`;
  }
  async function prefillPub() {
    const form = $("#publishForm"); if (!form || form.dataset.pinLat) return paintPub();
    const p = await loadPlace(false);
    if (p && p.set) { form.dataset.pinLat = p.lat; form.dataset.pinLng = p.lng; form.dataset.pinFrom = "saved"; }
    paintPub();
  }
  async function pickPub() {
    const form = $("#publishForm");
    const r = await pick({ city: ($("#pubCity") && $("#pubCity").value) || "", district: ($("#pubDistrict") && $("#pubDistrict").value) || "",
      lat: form.dataset.pinLat ? +form.dataset.pinLat : null, lng: form.dataset.pinLng ? +form.dataset.pinLng : null, title: "标出大致位置" });
    if (r && r !== "clear") { form.dataset.pinLat = r.lat; form.dataset.pinLng = r.lng; form.dataset.pinFrom = "map"; paintPub(); }
  }
  window.__yycjPubPin = function () {   // read by publish-ui.js on submit
    const form = $("#publishForm"); if (!form || editing() || !form.dataset.pinLat) return null;
    return { lat: +form.dataset.pinLat, lng: +form.dataset.pinLng };
  };
  window.__yycjPubPinDone = function () { place = null; const form = $("#publishForm"); if (form) { delete form.dataset.pinLat; delete form.dataset.pinLng; delete form.dataset.pinFrom; } };

  document.addEventListener("click", (ev) => {
    const b = ev.target.closest && ev.target.closest("[data-place]");
    if (b) {
      const k = b.getAttribute("data-place");
      if (k === "edit") editPlace();
      else if (k === "clear") { api("/api/me/place", { method: "DELETE" }).then((d) => { place = d.place; toast("已清除常用位置"); bustHome(); paintMe(); }).catch((e) => toast(e.message)); }
      else if (k === "pub") pickPub();
      else if (k === "goto") { $('[data-nav="me"]') && $('[data-nav="me"]').click(); setTimeout(editPlace, 250); }
      return;
    }
    if (ev.target.closest && ev.target.closest('[data-nav="me"], [data-switch]')) setTimeout(() => { loadPlace(true).then(paintMe); }, 350);
    if (ev.target.closest && ev.target.closest('[data-nav="publish"], [data-edit]')) setTimeout(prefillPub, 350);
  }, true);
  const boot = () => {
    paintPub();
    const t = $("#publishTitle");
    if (t && window.MutationObserver) new MutationObserver(() => paintPub()).observe(t, { childList: true, characterData: true, subtree: true });
    if (token()) loadPlace(true).then(() => { paintMe(); paintPub(); });
    else setTimeout(boot2, 1500);
  };
  const boot2 = () => { if (token()) loadPlace(true).then(() => { paintMe(); paintPub(); }); };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})();
