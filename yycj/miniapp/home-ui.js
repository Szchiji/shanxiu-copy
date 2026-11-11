(() => {
  if (window.__yycjHomeUi) return;
  window.__yycjHomeUi = true;
  const $ = (s) => document.querySelector(s);
  let offset = 0, q = "", lastItems = [], lastPins = [], cities = [], lastAreas = null;
  let carouselMs = 4000, carouselTimer = 0, paused = false;
  // 使用教程（「我的」底部）：按身份分两份，短句、一步一条
  const TUTORIAL = {
    guest: [
      ["选城市", "右上角切换城市。"],
      ["找人", "点「筛选」按区、价位、标签、营业中、有优惠来筛；或在搜索框搜花名、标签、城区、价位。"],
      ["附近", "在「我的 · 常用位置」地图上点一下设好位置，卡片会显示「区 · 约N km」，筛选里还能选「1km内」等。别人看不到你的具体位置。"],
      ["收藏", "进详情页，点底部「收藏」。收藏的都在「我的 · 我的收藏」。"],
      ["优惠券", "卡片上有「惠」字就是有优惠。进详情选一项点「领取」拿到券码（每批优惠每人限领 1 张）；点「想聊聊」约课时券码会自动复制并带给老师。券码在优惠结束前有效。"],
      ["写评价", "约课后在详情页点「评价」，按项目填写，并上传约课凭证（只给管理员审核，不公开），通过后展示。"],
    ],
    teacher: [
      ["上架", "点底部「上架」填资料：先选「区」，再在地图上点大致位置（客人只看到「约N km」），提交审核。"],
      ["营业时间", "在「我的上架 → 改资料」设营业时间，首页就会显示 营业中 / 未到点 / 已打烊；开工前 Bot 会问你今天开不开。"],
      ["到期提醒", "上架剩 3 天、剩 1 天时 Bot 会提醒你续期。"],
      ["我的优惠", "在「我的 · 我的优惠」发限时优惠：先选天数，再一项项添加内容和数量（一次最多 5 项，客人每人限领其中 1 张），会推送到本城频道。"],
      ["核销券码", "客人约课时会把券码发给你：在「我的优惠」输入券码 → 核对 → 标记已用。"],
      ["看数据", "「我的上架」里能看近 7 天浏览、收藏、匿名联系。"],
    ],
  };
  function tutorialHtml() {
    let r = "guest";
    try { r = JSON.parse(localStorage.getItem("yycj_user") || "{}").role || "guest"; } catch (e) {}
    const list = r === "guest" ? TUTORIAL.guest : TUTORIAL.teacher;
    return "<div class=\"sheet-panel tut\"><h3>使用教程 <small>" + (r === "guest" ? "客人" : "老师") + "</small></h3><ol class=\"tut-steps\">" +
      list.map((x, i) => "<li><span class=\"tut-n\">" + (i + 1) + "</span><div><b>" + esc(x[0]) + "</b><p>" + esc(x[1]) + "</p></div></li>").join("") +
      "</ol><button class=\"btn block\" id=\"guideClose\" type=\"button\">知道了</button></div>";
  }
  window.__yycjTutorialHtml = tutorialHtml;
  // 地区筛选：单选，本次会话内记住（按城市），切城市自动清空。只筛信息流，轮播不变。
  function curCity() { return localStorage.getItem("yycj_city") || ""; }
  function getArea() {
    try { const o = JSON.parse(sessionStorage.getItem("yycj_area") || "null"); return o && o.city === curCity() ? String(o.key || "") : ""; }
    catch (e) { return ""; }
  }
  function setArea(key) {
    try { if (key) sessionStorage.setItem("yycj_area", JSON.stringify({ city: curCity(), key })); else sessionStorage.removeItem("yycj_area"); } catch (e) {}
  }
  // 排序：最新上架（默认）/ 评价多。放在「地区」面板顶部，首页布局不变；本次会话内记住。
  function getSort() { try { const v = sessionStorage.getItem("yycj_sort") || ""; return (v === "reviews" || v === "near") ? v : ""; } catch (e) { return ""; } }
  function setSort(v) { try { if (v === "reviews" || v === "near") sessionStorage.setItem("yycj_sort", v); else sessionStorage.removeItem("yycj_sort"); } catch (e) {} }
  // 价格档 / 标签 / 营业中 / 距离：跟地区一样，按城市记在本次会话里
  function filtKey() { return "yycj_filt_" + curCity(); }
  function getFilt() {
    try { return JSON.parse(sessionStorage.getItem(filtKey()) || "{}") || {}; } catch (e) { return {}; }
  }
  function setFilt(patch) {
    const o = Object.assign({}, getFilt(), patch || {});
    ["price", "tags", "service", "open", "radius", "at", "lat", "lng", "np", "cpn"].forEach((k) => { if (!o[k]) delete o[k]; });
    try {
      if (Object.keys(o).length) sessionStorage.setItem(filtKey(), JSON.stringify(o));
      else sessionStorage.removeItem(filtKey());
    } catch (e) {}
  }
  function clearFilt() { locCancel?.(); try { sessionStorage.removeItem(filtKey()); } catch (e) {} setSort(""); setArea(""); }
  // 面板里改的选项只在「查看结果」/ 关闭面板 / 重置时生效；appliedSig 记住信息流当前用的是哪一组条件
  function filtSig() { return JSON.stringify([curCity(), getArea(), getSort(), getFilt()]); }
  let appliedSig = null;
  function activeFiltCount() {
    const f = getFilt();
    return [getArea(), f.price, f.tags, f.service && f.service!=="all" ? f.service : f.open, f.radius, f.cpn].filter(Boolean).length;
  }
  let lastFacets = null, facetsCity = "", qTimer = 0, locBusy = false, locCancel = null, hasPlace = false;
  // 常用位置 (我的) is the default centre: the server applies it when no lat/lng/区 is sent; np=1 turns it off for this session
  function placeOn(f) { f = f || getFilt(); return hasPlace && !f.np && !(f.lat && f.lng) && !f.at; }
  function token() { return localStorage.getItem("yycj_token") || ""; }
  function cacheKey(city, qq) {
    const f = getFilt();
    return "yycj_hc_" + (city || "") + "_" + (qq || "") + "_" + getArea() + "_" + getSort()
      + "_" + (f.price || "") + "_" + (f.tags || "") + "_" + (f.service || f.open || "") + "_" + (f.radius || "") + "_" + (f.at || "") + "_" + (f.np || "") + "_" + (f.cpn || "");
  }
  function readCache(city, qq) {
    try {
      const raw = sessionStorage.getItem(cacheKey(city, qq));
      if (!raw) return null;
      const o = JSON.parse(raw);
      if (!o || Date.now() - Number(o.t || 0) > 60000) return null;
      return o.d || null;
    } catch (e) { return null; }
  }
  function writeCache(city, qq, data) {
    try { sessionStorage.setItem(cacheKey(city, qq), JSON.stringify({ t: Date.now(), d: data })); } catch (e) {}
  }
  function homeVisible() {
    const view = $("#view-home");
    return view && !view.classList.contains("hidden") && !document.hidden;
  }
  let pinsSeen = true, touching = false;
  function stopTimer() { if (carouselTimer) { clearTimeout(carouselTimer); carouselTimer = 0; } }
  // The carousel timer only runs while it can actually be seen: home view shown, page visible,
  // carousel on screen, not being touched. Otherwise no timer is pending at all.
  function canRun() { return !window.__yycjMapMode && !paused && !touching && pinsSeen && homeVisible(); }
  function arm() {
    if (!canRun()) { stopTimer(); return; }
    if (!carouselTimer) carouselTimer = setTimeout(tick, carouselMs);
  }
  function pauseMedia() {
    document.querySelectorAll("video").forEach((v) => { try { v.pause(); } catch (e) {} });
    paused = true;
    stopTimer();
  }
  function resumeWork() {
    paused = false;
    arm();
  }
  window.__yycjPauseUi = pauseMedia;
  window.__yycjResumeUi = resumeWork;
  async function api(path, controller) {
    const ctl = controller || new AbortController();
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; ctl.abort(); }, 8000);
    try {
      const r = await fetch(path, { headers: { Authorization: "Bearer " + token() }, signal: ctl.signal });
      const data = await r.json().catch(e => { if (ctl.signal.aborted || path.includes("map_results=1")) throw e; return {}; });
      if (!r.ok) throw new Error(data.detail || r.statusText || ("HTTP " + r.status));
      if (path.includes("map_results=1") && (!Array.isArray(data?.items) || !Array.isArray(data?.map_groups))) throw new Error("地图结果无效，请重试");
      return data;
    } catch (e) {
      if (timedOut) { const error = new Error("请求超时，请重试"); error.name = "TimeoutError"; throw error; }
      throw e;
    } finally {
      clearTimeout(timer);
    }
  }
  function esc(t) {
    return String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':'&quot;',"'":"&#39;" }[c]));
  }
  function fullCover(item) {
    if (item && item.cover_url) {
      const s = String(item.cover_url);
      if (s.startsWith("http") || s.startsWith("/")) return s;
      return "/api/media/file/" + encodeURIComponent(s);
    }
    return window.yycjCoverOf ? window.yycjCoverOf(item) : "";
  }
  // cards are ~120–170 css px wide: ask for a 480 px thumbnail instead of the full photo
  function cover(item) {
    const s = fullCover(item);
    return window.yycjThumb ? window.yycjThumb(s, 480) : s;
  }
  function fillCities(list, current) {
    cities = list && list.length ? list : cities;
    const drop = $("#cityDrop");
    if (drop) {
      drop.innerHTML = (cities || []).map((c) =>
        "<button type=\"button\" class=\"city-opt" + (c === current ? " on" : "") + "\" data-city=\"" + esc(c) + "\">" + esc(c) + "</button>"
      ).join("") || "<div class='muted' style='padding:8px'>暂无城市</div>";
    }
    const btn = $("#btnCity");
    if (btn) btn.textContent = (current || cities[0] || "城市") + " ▾";
  }
  function imgTag(src, cls, eager) {
    if (!src) return "<div class=\"" + cls + " ph\"></div>";
    const load = eager ? "eager" : "lazy";
    return "<img class=\"" + cls + "\" src=\"" + esc(src) + "\" alt=\"\" width=\"400\" height=\"530\" loading=\"" + load + "\" decoding=\"async\"" + (eager ? " fetchpriority=\"high\"" : "") + " />";
  }
  // Card hours, compact (full label with 次日 stays on the detail page):
  // "10-22" -> "10–22点", "20:00–次日04:00" -> "20–04点", "9:30~21:00" -> "9:30–21点"; unparsable text is shown as-is.
  function fmtHours(raw) {
    const s = String(raw || "").trim().replace(/：/g, ":");
    if (!s) return "";
    const m = s.match(/^(\d{1,2})(?::(\d{2}))?\s*[-–—~～至到]\s*(?:次日)?\s*(\d{1,2})(?::(\d{2}))?$/);
    if (!m) return s;
    const a = m[2] && m[2] !== "00" ? m[1] + ":" + m[2] : m[1];
    const b = m[4] && m[4] !== "00" ? m[3] + ":" + m[4] : m[3];
    return a.padStart(2, "0") + "–" + b.padStart(2, "0") + "点";
  }
  function cardHtml(t, idx) {
    const img = cover(t);
    // 按距离时，右上角地区标签换成「区 · 约Nkm」（只显示取整后的距离，不暴露坐标）
    const loc = t.distance ? [t.district || t.city, t.distance].filter(Boolean).join("\u2009·\u2009") : [t.city, t.district].filter(Boolean).join("·");
    const tag = String((t.tags && t.tags[0]) || "").replace(/^#+/, "");
    const n = t.media_count || (t.media && t.media.length) || (t.photos && t.photos.length) || 0;
    const pin = t.feed_pinned ? "<span class=\"badge\">置顶</span>" : "";
    // 优惠 (option A): a small 「惠」 pill in the title row, like 置顶 — card size unchanged
    const cpn = t.coupon && window.__yycjCouponMode && window.__yycjCouponMode("badge") ? "<span class=\"badge-cpn\" title=\"" + esc(t.coupon.text || "") + "\">惠</span>" : "";
    const open = t.open_text ? "<span class=\"badge-open st-" + esc(t.open_code || "") + "\">" + esc(t.open_text) + "</span>" : "";
    return "<div class=\"feed-card compact cover-card\" data-id=\"" + esc(t.lamp_id) + "\"><div class=\"cover-wrap\">" +
      imgTag(img, "thumb", idx < 3) +
      // 收藏只在详情页；卡片左上角放营业状态书签
      open +
      (loc ? "<span class=\"badge-loc\">" + esc(loc) + "</span>" : "") +
      (tag ? "<span class=\"badge-tag\">" + esc(tag) + "</span>" : "") +
      (n ? "<span class=\"badge-n\">" + n + "图</span>" : "") +
      "</div><div class=\"body\"><h3>" + pin + cpn + esc(t.title || "") + "</h3><div class=\"muted price\">" + esc(t.price_text || "面议") + (fmtHours(t.hours_text) ? "<span class=\"st-hours\"> · " + esc(fmtHours(t.hours_text)) + "</span>" : "") + "</div></div></div>";
  }
  function pinHtml(p, idx) {
    const t = (p && p.lamp) || p || {};
    // 轮播只放封面 + 称呼，不显示营业状态 / 营业时间
    return "<div class=\"pin-card short\" data-id=\"" + esc(t.lamp_id || "") + "\">" +
      imgTag(cover(t), "pin-cover", idx === 0) +
      "<div class=\"pin-copy\"><b>" + esc(t.title || "") + "</b></div></div>";
  }
  function paint() {
    const feed = $("#feed");
    if (feed && !window.__yycjMapMode) {
      feed.classList.add("has-cover");
      feed.innerHTML = lastItems.length ? lastItems.map((t, i) => cardHtml(t, i)).join("") : "<p class='muted'>" + (activeFiltCount() ? "没有符合筛选的上架" : "暂无上架") + "</p>";
    }
    const pins = $("#pins");
    if (pins) {
      pins.innerHTML = lastPins.map((p, i) => pinHtml(p, i)).join("");
      pins.classList.toggle("hidden", !lastPins.length || !!q);
    }
    if (typeof window.__yycjPaintFav === "function") window.__yycjPaintFav();
  }
  function applyData(data, reset, mapRequest) {
    window.__yycjHomeData = data;
    mapOptions=data.map_filter_options||null;mapOptionsCity=data.city||curCity();
    if (data.city) localStorage.setItem("yycj_city", data.city);
    fillCities(data.enabled_cities || cities, data.city || localStorage.getItem("yycj_city") || "");
    carouselMs = Math.max(3000, Math.min(12000, Number(data.carousel_interval_sec || 4) * 1000));
    lastPins = q ? [] : (data.pins || []);
    if (data.districts) lastAreas = data.districts;
    if (data.filters) hasPlace = !!data.filters.has_place;
    paintArea();
    const batch = data.items || [];
    lastItems = reset || offset === 0 ? batch : lastItems.concat(batch);
    offset = lastItems.length;
    paint();
    $("#btnLoadMore")?.classList.toggle("hidden", !!window.__yycjMapMode || !data.has_more);
    const homeEvent=new CustomEvent("yycj:home-data", {detail:data});homeEvent.mapRequest=mapRequest;window.dispatchEvent(homeEvent);
    const ann = $("#announce");
    if (ann && data.announcement && data.announcement.text) {
      ann.classList.remove("hidden");
      ann.innerHTML = "<span>" + esc(data.announcement.text) + "</span>";
    }
  }
  let loadSequence = 0, loadCtl = null, mapBusy = false, mapDirty = false, mapOptions = null, mapOptionsCity = "";
  function cancelLoad(){if(!loadCtl)return;loadCtl.abort();loadCtl=null;++loadSequence;mapBusy=false;window.dispatchEvent(new CustomEvent('yycj:map-cancel'));paintMapFilters();}
  function markMapDirty(){if(!window.__yycjMapMode)return;if(!mapDirty){cancelLoad();mapDirty=true;window.dispatchEvent(new CustomEvent('yycj:map-pending'));}paintMapFilters();}
  function paintMapFilters(){
    const el=$('#hubMapFilters');if(!el)return;const drawer=$('#mapFullFilterPanel');el.classList.toggle('hidden',!window.__yycjMapMode||(!!drawer&&drawer.classList.contains('hidden')));const f=getFilt(),service=f.service||(f.open?'open':'all'),area=getArea();
    const opts=mapOptionsCity===curCity()&&mapOptions?.districts?mapOptions.districts:(lastAreas?.items||[]).map(x=>({key:x.key,label:x.key}));
    const list=[{key:'',label:'全部地区'},...opts];if(area&&!list.some(x=>x.key===area))list.push({key:area,label:area==='__missing__'?'未标注地区':area});
    el.innerHTML='<label><span class="sr-only">行政地区</span><select aria-label="行政地区" data-map-region>'+list.map(x=>'<option value="'+esc(x.key)+'"'+(x.key===area?' selected':'')+'>'+esc(x.label)+'</option>').join('')+'</select></label><label><span class="sr-only">服务状态</span><select aria-label="服务状态" data-map-service>'+[['all','全部状态'],['open','营业中'],['rest','休息中']].map(([key,label])=>'<option value="'+key+'"'+(service===key?' selected':'')+'>'+label+'</option>').join('')+'</select></label><button type="button" class="btn" data-map-filter-apply'+(mapBusy?' disabled':'')+'>'+(mapBusy?'搜索中…':'查看结果')+'</button>'+(mapDirty?'<p role="status">筛选已改 · 点击查看结果后更新</p>':'');
  }
  window.__yycjMapFilters={paint:paintMapFilters,signature:()=>JSON.stringify([q,filtSig()]),get:()=>({city:curCity(),district:getArea(),service:getFilt().service||(getFilt().open?'open':'all'),pending:mapDirty}),set:patch=>{if(patch.district!==undefined)setArea(patch.district);if(patch.service!==undefined)setFilt({service:patch.service,open:patch.service==='open'?'1':''});markMapDirty();paintArea();},apply:()=>{const mq=$('#mapFullQ');if(mq&&window.__yycjMapMode&&$('#homeQ'))$('#homeQ').value=mq.value;q=($('#homeQ')?.value||'').trim();appliedSig=null;toggleArea(false);return window.__yycjLoadHome();}};
  window.__yycjCancelHome=cancelLoad;
  window.addEventListener('yycj:home-data',ev=>{if(ev.detail.city&&ev.detail.city!==curCity())return;mapOptions=ev.detail.map_filter_options||null;mapOptionsCity=curCity();cities=ev.detail.enabled_cities||cities;fillCities(cities,curCity());if(ev.detail.districts)lastAreas=ev.detail.districts;mapDirty=false;mapBusy=false;paintMapFilters();});
  window.addEventListener('yycj:map-loading',()=>{mapDirty=false;mapBusy=true;paintMapFilters();});
  window.addEventListener('yycj:map-error',()=>{mapBusy=false;paintMapFilters();});
  async function loadFeed(reset, options = {}) {
    if (!token() || document.hidden || !homeVisible()) return;
    cancelLoad();const ctl=new AbortController();loadCtl=ctl;mapDirty=false;mapBusy=true;paintMapFilters();
    const seq = ++loadSequence, mapMode = !!window.__yycjMapMode;
    paused = false;
    arm();
    const requestCity=curCity(),requestSig=filtSig();
    const mapRequest={seq,city:requestCity,area:options.mapArea||null};
    if(mapMode)window.dispatchEvent(new CustomEvent("yycj:map-loading",{detail:mapRequest}));
    try {
      if (reset) offset = 0;
      const city = localStorage.getItem("yycj_city") || "";
      q = (($("#homeQ") && $("#homeQ").value.trim()) || q || "");
      if (reset && !window.__yycjMapMode) {
        const cached = readCache(city, q);
        if (cached && (cached.items || cached.pins)) applyData(cached, true);
      }
      const params = new URLSearchParams({ limit: "12", offset: String(offset && !reset ? offset : 0) });
      if (q) params.set("q", q);
      if (city) params.set("city", city);
      if (getArea()) params.set("district", getArea());
      if (getSort()) params.set("sort", getSort());
      const f = getFilt();
      if (f.price) params.set("price", f.price);
      if (f.tags) params.set("tags", f.tags);
      params.set("service", f.service || (f.open ? "open" : "all"));
      if (f.cpn) params.set("coupon", "1");
      if (f.radius) params.set("radius", String(f.radius));
      if (f.lat && f.lng) { params.set("lat", String(f.lat)); params.set("lng", String(f.lng)); }
      else if (f.at) params.set("at", f.at);
      if (f.np) params.set("place", "0");
      if(window.__yycjMapMode) params.set("map_results", "1");
      appliedSig = filtSig();
      const data = await api("/api/home?" + params.toString(), ctl);
      if(seq !== loadSequence || mapMode !== !!window.__yycjMapMode || city !== curCity() || requestSig !== filtSig()) return;
      applyData(data, reset, mapRequest);
      window.dispatchEvent(new CustomEvent("yycj:entry-announcement", {detail:data.popup_announcement}));
      if(!window.__yycjMapMode) writeCache(data.city || city, q, data);
    } catch (e) {
      if(ctl.signal.aborted && e.name!=="TimeoutError")return;
      if(seq!==loadSequence || mapMode!==!!window.__yycjMapMode || requestCity!==curCity() || requestSig!==filtSig())return;
      if(mapMode){window.dispatchEvent(new CustomEvent("yycj:map-error",{detail:{...mapRequest,message:e.message}}));return;}
      const feed = $("#feed");
      if (feed && !feed.querySelector("[data-id]")) feed.innerHTML = "<p class='muted'>加载失败，点底栏首页重试</p>";
    } finally {if(seq===loadSequence){loadCtl=null;mapBusy=false;paintMapFilters();}}
  }
  function areaLabel(k) { return k.length <= 3 ? k : k.slice(0, 2) + "…"; }
  function paintArea() {
    const btn = $("#btnArea");
    const k = getArea();
    const so = getSort();
    const f = getFilt();
    const nAct = activeFiltCount();
    if (btn) {
      const lbl = btn.querySelector(".area-lbl");
      if (lbl) {
        const word = nAct ? "筛选·" + nAct : so === "near" ? "附近" : so === "reviews" ? "评价多" : k ? areaLabel(k) : "筛选";
        lbl.innerHTML = "<i class=\"area-ic\" aria-hidden=\"true\"></i><span class=\"area-w\">" + esc(word) + "</span><i class=\"area-car\" aria-hidden=\"true\">▾</i>";
      }
      btn.classList.toggle("on", !!(nAct || so));
      btn.classList.toggle("long", true);
      btn.setAttribute("aria-label", "筛选" + (nAct ? "，已选" + nAct + "项" : ""));
    }
    if(window.__yycjMapMode&&appliedSig!==null&&appliedSig!==filtSig())markMapDirty();
    paintMapFilters();
    const panel = $("#areaPanel");
    if (!panel || panel.classList.contains("hidden")) return;
    const d = lastAreas || { total: 0, items: [] };
    const facets = lastFacets || { price_bands: [], tags: [], radii: [3, 5, 10], geo_districts: [], located: 0 };
    const pickedTags = new Set(String(f.tags || "").split(",").filter(Boolean));
    const sortRow = "<div class=\"sort-row\" role=\"radiogroup\" aria-label=\"排序\"><span class=\"sort-k\">排序</span>" +
      "<button type=\"button\" class=\"sort-opt" + (!so ? " on" : "") + "\" data-sort=\"\">最新</button>" +
      "<button type=\"button\" class=\"sort-opt" + (so === "reviews" ? " on" : "") + "\" data-sort=\"reviews\">评价多</button>" +
      "<button type=\"button\" class=\"sort-opt" + (so === "near" ? " on" : "") + "\" data-sort=\"near\">距我近</button></div>";
    const areaChips = ["<button type=\"button\" class=\"area-chip" + (k ? "" : " on") + "\" data-area=\"\">全部<i>" + (d.total || 0) + "</i></button>"]
      .concat((d.items || []).map((x) => "<button type=\"button\" class=\"area-chip" + (x.key === k ? " on" : "") + "\" data-area=\"" + esc(x.key) + "\">" + esc(x.key) + "<i>" + x.count + "</i></button>"));
    const priceChips = (facets.price_bands || []).map((b) =>
      "<button type=\"button\" class=\"area-chip" + (f.price === b.key ? " on" : "") + "\" data-price=\"" + esc(b.key) + "\">" + esc(b.label) + "<i>" + b.count + "</i></button>"
    ).join("") || "<span class=\"muted\">暂无标价</span>";
    const tagChips = (facets.tags || []).map((t) =>
      "<button type=\"button\" class=\"area-chip" + (pickedTags.has(t.tag) ? " on" : "") + "\" data-tag=\"" + esc(t.tag) + "\">" + esc(t.tag) + "<i>" + t.count + "</i></button>"
    ).join("") || "<span class=\"muted\">暂无标签</span>";
    const service=f.service||(f.open?'open':'all');
    const openChip = [['all','全部状态'],['open','营业中'],['rest','休息中']].map(([key,label])=>'<button type="button" class="area-chip'+(service===key?' on':'')+'" data-service="'+key+'">'+label+'</button>').join('') +
      (window.__yycjCouponMode && window.__yycjCouponMode("filter") ? '<button type="button" class="area-chip'+(f.cpn?' on':'')+'" data-cpn="1">有优惠</button>' : '');
    const usePlace = placeOn(f);
    const hasLoc = !!(f.lat && f.lng) || !!f.at || usePlace;
    const radii = (facets.radii && facets.radii.length) ? facets.radii : [3, 5, 10];
    const locLbl = f.at ? ("以" + f.at + "为中心") : (f.lat && f.lng) ? "已定位" : usePlace ? "常用位置" : "用我的位置";
    const nearRow = "<div class=\"filt-row\"><span class=\"sort-k\">附近</span>" +
      (hasPlace && !usePlace ? "<button type=\"button\" class=\"sort-opt\" data-loc=\"place\">常用位置</button>" : "") +
      "<button type=\"button\" class=\"sort-opt" + (hasLoc ? " on" : "") + "\" data-loc=\"1\">" + locLbl + "</button>" +
      (usePlace ? "<button type=\"button\" class=\"sort-opt\" data-loc=\"gps\">用当前定位</button>" : "") +
      (hasLoc ? "<button type=\"button\" class=\"sort-opt\" data-loc=\"0\">清除</button>" : "") + "</div>" +
      (hasLoc ? "<div class=\"area-chips\">" + radii.map((r) =>
        "<button type=\"button\" class=\"area-chip" + (String(f.radius) === String(r) ? " on" : "") + "\" data-radius=\"" + r + "\">" + r + "km" + (Number(r) === 1 ? "内" : "") + "</button>"
      ).join("") + "</div>" : (facets.geo_districts && facets.geo_districts.length
        ? "<div class=\"area-chips near-pick\"><span class=\"muted\" style=\"width:100%\">或选一个区作中心" + (hasPlace ? "" : "（也可以在「我的 · 常用位置」设一个）") + "：</span>" +
          facets.geo_districts.map((g) => "<button type=\"button\" class=\"area-chip" + (f.at === g.key ? " on" : "") + "\" data-at=\"" + esc(g.key) + "\">" + esc(g.key) + "</button>").join("") + "</div>"
        : ""));
    panel.innerHTML = sortRow +
      "<div class=\"filt-sec\"><div class=\"sort-k\">地区</div><div class=\"area-chips\">" + areaChips.join("") + "</div></div>" +
      "<div class=\"filt-sec\"><div class=\"sort-k\">价格</div><div class=\"area-chips\">" + priceChips + "</div></div>" +
      "<div class=\"filt-sec\"><div class=\"sort-k\">标签</div><div class=\"area-chips\">" + tagChips + "</div></div>" +
      "<div class=\"filt-sec\"><div class=\"sort-k\">状态</div><div class=\"area-chips\">" + openChip + "</div></div>" +
      "<div class=\"filt-sec\">" + nearRow + "</div>" +
      "<div class=\"filt-actions\"><button type=\"button\" class=\"btn\" data-filt-reset=\"1\">重置</button>" +
      "<button type=\"button\" class=\"btn primary\" data-filt-apply=\"1\">查看结果</button></div>";
  }
  async function ensureFacets() {
    const city = curCity();
    if (lastFacets && facetsCity === city) return;
    try {
      const data = await api("/api/home/facets?city=" + encodeURIComponent(city));
      lastFacets = data; facetsCity = city;
    } catch (e) { lastFacets = lastFacets || { price_bands: [], tags: [], radii: [3, 5, 10], geo_districts: [] }; }
  }
  function scrollTop() { try { window.scrollTo({ top: 0, behavior: "auto" }); } catch (e) { window.scrollTo(0, 0); } }
  function askLocation() {
    if (locBusy) return;
    locBusy = true;
    const city = curCity(); let settled = false;
    const finish = fn => {
      if (settled) return; settled = true; clearTimeout(timer); locBusy = false; locCancel = null;
      if (city === curCity() && homeVisible()) fn();
    };
    const fail = () => finish(() => { paintArea(); try { window.__yycjToast?.("定位失败，可选区作中心"); } catch (e) {} });
    const timer = setTimeout(fail, 8000);
    const cancel = () => finish(() => {}); locCancel = cancel;
    const done = (lat, lng) => {
      if (settled) return;
      if (city !== curCity() || !homeVisible()) { cancel(); return; }
      if (lat == null || lng == null || !Number.isFinite(+lat) || !Number.isFinite(+lng) || Math.abs(+lat)>90 || Math.abs(+lng)>180) { fail(); return; }
      try { const g = window.yycjPlace && window.yycjPlace.wgs2gcj(+lat, +lng); if (g) { lat = g[0]; lng = g[1]; } } catch (e) {}
      finish(() => { setFilt({ lat: +(+lat).toFixed(2), lng: +(+lng).toFixed(2), at: "", np: "" }); paintArea(); });
    };
    try {
      const tg = window.Telegram && window.Telegram.WebApp;
      if (tg?.LocationManager?.init && tg.LocationManager.getLocation) {
        tg.LocationManager.init(() => {
          if (settled || city !== curCity() || !homeVisible()) { cancel(); return; }
          try { tg.LocationManager.getLocation((loc) => loc ? done(loc.latitude, loc.longitude) : fail()); }
          catch (e) { fail(); }
        });
        return;
      }
      if (navigator.geolocation) navigator.geolocation.getCurrentPosition((p) => done(p.coords.latitude, p.coords.longitude), fail, { enableHighAccuracy: false, timeout: 8000 });
      else fail();
    } catch (e) { fail(); }
  }
  async function toggleArea(open) {
    const panel = $("#areaPanel");
    if (!panel) return;
    const on = open == null ? panel.classList.contains("hidden") : open;
    const wasOpen = !panel.classList.contains("hidden");
    panel.classList.toggle("hidden", !on);
    $("#btnArea")?.setAttribute("aria-expanded", on ? "true" : "false");
    if (on) await ensureFacets();
    paintArea();
    // closing the panel (tap outside / 筛选 again) applies what was picked, same as 查看结果
    if (wasOpen && !on && appliedSig !== null && appliedSig !== filtSig()) { scrollTop(); loadFeed(true); }
  }
  function mountGuide() {
    const row = document.querySelector(".search-row");
    if (row && !$("#btnArea")) {
      const btn = document.createElement("button");
      btn.id = "btnArea";
      btn.type = "button";
      btn.setAttribute("aria-expanded", "false");
      // invisible "教程" keeps the old button's exact size; the visible label sits on top
      btn.innerHTML = "<span class=\"area-sz\" aria-hidden=\"true\">教程</span><span class=\"area-lbl\">筛选</span>";
      row.insertBefore(btn, row.firstChild);
      const panel = document.createElement("div");
      panel.id = "areaPanel";
      panel.className = "area-panel hidden";
      // overlay inside the search row (absolute) so the page never shifts
      row.appendChild(panel);
    }
    const me = $("#view-me");
    if (me && !$("#meTutorial")) {
      const card = document.createElement("div");
      card.id = "meTutorial";
      card.className = "card";
      card.innerHTML = "<button class=\"btn block\" id=\"btnTutorial\" type=\"button\">使用教程</button>";
      me.appendChild(card);
    }
    if (!$("#guideSheet")) {
      const sheet = document.createElement("div");
      sheet.id = "guideSheet";
      sheet.className = "sheet hidden";
      sheet.innerHTML = tutorialHtml();
      document.body.appendChild(sheet);
    }
  }
  window.__yycjFeedUrl = function () {
    const params = new URLSearchParams({ limit: "12", offset: "0" });
    const qq = ($("#homeQ") && $("#homeQ").value.trim()) || q || "";
    const city = localStorage.getItem("yycj_city") || "";
    if (qq) params.set("q", qq);
    if (city) params.set("city", city);
    if (getArea()) params.set("district", getArea());
    if (getSort()) params.set("sort", getSort());
    const f = getFilt();
    if (f.price) params.set("price", f.price);
    if (f.tags) params.set("tags", f.tags);
    params.set("service", f.service || (f.open ? "open" : "all"));
    if (f.cpn) params.set("coupon", "1");
    if (f.radius) params.set("radius", String(f.radius));
    if (f.lat && f.lng) { params.set("lat", String(f.lat)); params.set("lng", String(f.lng)); }
    else if (f.at) params.set("at", f.at);
    if (f.np) params.set("place", "0");
    return "/api/home?" + params.toString();
  };
  window.__yycjCloseArea = function () { toggleArea(false); };
  window.__yycjPlaceChanged = function () { lastFacets = null; offset = 0; setFilt({ np: "" }); };
  window.__yycjLoadHome = function (options) { paused = false; return loadFeed(true, options); };
  window.__yycjReloadHome = window.__yycjLoadHome;
  document.addEventListener("click", (ev) => {
    if (ev.target.id === "btnSearch") { q = ($("#homeQ") && $("#homeQ").value.trim()) || ""; window.__yycjLoadHome(); }
    if (ev.target.id === "btnLoadMore") loadFeed(false);
    if (ev.target.closest && ev.target.closest("[data-nav='home']")) {
      if (lastItems.length) paint();
      else loadFeed(true);
    }
    if (ev.target.closest && ev.target.closest("#btnArea")) { ev.preventDefault(); toggleArea(); }
    else if (!(ev.target.closest && ev.target.closest("#areaPanel")) && $("#areaPanel") && !$("#areaPanel").classList.contains("hidden")) {
      // tap outside just closes the overlay (doesn't also open a card underneath)
      ev.preventDefault(); ev.stopPropagation(); toggleArea(false); return;
    }
    const sortBtn = ev.target.closest && ev.target.closest("#areaPanel [data-sort]");
    if (sortBtn) {
      const v = sortBtn.getAttribute("data-sort") || "";
      setSort(v);
      if (v === "near" && !(getFilt().lat || getFilt().at || placeOn())) askLocation();
      paintArea();
      return;
    }
    const chip = ev.target.closest && ev.target.closest("#areaPanel [data-area]");
    if (chip) { setArea(chip.getAttribute("data-area") || ""); paintArea(); return; }
    const priceChip = ev.target.closest && ev.target.closest("#areaPanel [data-price]");
    if (priceChip) {
      const key = priceChip.getAttribute("data-price") || "";
      setFilt({ price: getFilt().price === key ? "" : key }); paintArea(); return;
    }
    const tagChip = ev.target.closest && ev.target.closest("#areaPanel [data-tag]");
    if (tagChip) {
      const t = tagChip.getAttribute("data-tag") || "";
      const cur = new Set(String(getFilt().tags || "").split(",").filter(Boolean));
      if (cur.has(t)) cur.delete(t); else if (cur.size < 5) cur.add(t);
      setFilt({ tags: [...cur].join(",") }); paintArea(); return;
    }
    if (ev.target.closest && ev.target.closest("#areaPanel [data-cpn]")) {
      setFilt({ cpn: getFilt().cpn ? "" : "1" }); paintArea(); return;
    }
    const serviceChip=ev.target.closest?.('#areaPanel [data-service]');if(serviceChip){setFilt({service:serviceChip.dataset.service,open:serviceChip.dataset.service==='open'?'1':''});paintArea();return;}
    if (ev.target.closest && ev.target.closest("#areaPanel [data-open]")) {
      setFilt({ open: getFilt().open ? "" : "1" }); paintArea(); return;
    }
    if (ev.target.closest && ev.target.closest("#areaPanel [data-loc=\"1\"]")) { if (!placeOn()) askLocation(); return; }
    if (ev.target.closest && ev.target.closest("#areaPanel [data-loc=\"gps\"]")) { askLocation(); return; }
    if (ev.target.closest && ev.target.closest("#areaPanel [data-loc=\"place\"]")) {
      locCancel?.(); setFilt({ lat: "", lng: "", at: "", np: "" }); paintArea(); return;
    }
    if (ev.target.closest && ev.target.closest("#areaPanel [data-loc=\"0\"]")) {
      locCancel?.(); setFilt({ lat: "", lng: "", at: "", radius: "", np: hasPlace ? "1" : "" }); if (getSort() === "near") setSort(""); paintArea(); return;
    }
    const atChip = ev.target.closest && ev.target.closest("#areaPanel [data-at]");
    if (atChip) {
      locCancel?.(); setFilt({ at: atChip.getAttribute("data-at") || "", lat: "", lng: "", np: "", radius: getFilt().radius || "5" });
      paintArea(); return;
    }
    const radChip = ev.target.closest && ev.target.closest("#areaPanel [data-radius]");
    if (radChip) {
      const r = radChip.getAttribute("data-radius") || "";
      setFilt({ radius: getFilt().radius === r ? "" : r }); paintArea(); return;
    }
    if (ev.target.closest && ev.target.closest("#areaPanel [data-filt-reset]")) {
      // 重置 = 马上恢复默认：排序回「最新」，清地区 / 价格 / 标签 / 营业中 / 有优惠 / 附近（本次定位、半径）。
      // 不动「我的 · 常用位置」（它就是默认中心），也不清搜索框。价格、标签选项（facets）按城市缓存，不用丢。
      clearFilt(); paintArea(); scrollTop(); loadFeed(true); return;
    }
    if (ev.target.closest && ev.target.closest("#areaPanel [data-filt-apply]")) {
      appliedSig = null; toggleArea(false); scrollTop(); loadFeed(true); return;
    }
    if (ev.target.id === "btnTutorial" || (ev.target.closest && ev.target.closest("[data-tutorial]"))) {
      ev.preventDefault(); const sh = $("#guideSheet"); if (sh) { sh.innerHTML = tutorialHtml(); sh.classList.remove("hidden"); }
    }
    if (ev.target.id === "guideClose" || ev.target.id === "guideSheet") $("#guideSheet")?.classList.add("hidden");
    if (ev.target.id === "btnCity" || (ev.target.closest && ev.target.closest("#btnCity"))) {
      ev.preventDefault();
      ev.stopPropagation();
      if (!$("#cityDrop")?.innerHTML) fillCities(cities, localStorage.getItem("yycj_city") || "");
      $("#cityDrop")?.classList.toggle("hidden");
    }
    const opt = ev.target.closest && ev.target.closest("#cityDrop [data-city]");
    if (opt) {
      localStorage.setItem("yycj_city", opt.getAttribute("data-city") || "");
      $("#cityDrop")?.classList.add("hidden");
      setArea("");
      clearFilt();
      lastAreas = null;
      lastFacets = null;
      facetsCity = "";
      appliedSig = null;
      toggleArea(false);
      q = "";
      if ($("#homeQ")) $("#homeQ").value = "";
      window.__yycjLoadHome();
    }
  }, true);
  mountGuide();
  const toolbar=document.createElement('div');toolbar.id='hubMapFilters';toolbar.className='hub-map-filters hidden';$('#feed')?.insertAdjacentElement('beforebegin',toolbar);paintMapFilters();
  toolbar.addEventListener('change',ev=>{if(ev.target.matches('[data-map-region]'))window.__yycjMapFilters.set({district:ev.target.value});if(ev.target.matches('[data-map-service]'))window.__yycjMapFilters.set({service:ev.target.value});});
  toolbar.addEventListener('click',ev=>{if(ev.target.closest('[data-map-filter-apply]'))window.__yycjMapFilters.apply();});
  const qInput = $("#homeQ");
  if (qInput && !qInput.dataset.debounced) {
    qInput.dataset.debounced = "1";
    qInput.addEventListener("input", () => {
      clearTimeout(qTimer);
      qTimer = setTimeout(() => { q = (qInput.value || "").trim(); loadFeed(true); }, 320);
    });
    qInput.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") { ev.preventDefault(); clearTimeout(qTimer); q = (qInput.value || "").trim(); loadFeed(true); }
    });
  }
  function tick() {
    carouselTimer = 0;
    if (!canRun()) return;
    const pins = $("#pins");
    if (pins && !pins.classList.contains("hidden") && pins.querySelector(".pin-card")) {
      const card = pins.querySelector(".pin-card");
      const step = card ? card.offsetWidth + 10 : 160;
      if (pins.scrollLeft + pins.clientWidth >= pins.scrollWidth - 16) pins.scrollLeft = 0;
      else pins.scrollLeft += step;
    }
    arm();
  }
  (function watchCarousel() {
    document.addEventListener("visibilitychange", ()=>{if(document.hidden){cancelLoad();locCancel?.();}arm();});
    const view = $("#view-home");
    if (view && window.MutationObserver) new MutationObserver(()=>{if(!homeVisible()){cancelLoad();locCancel?.();}arm();}).observe(view, { attributes: true, attributeFilter: ["class"] });
    const pins = $("#pins");
    if (pins && window.IntersectionObserver) {
      new IntersectionObserver((es) => { pinsSeen = es.some((e) => e.isIntersecting); arm(); }).observe(pins);
    }
    if (pins) {
      pins.addEventListener("touchstart", () => { touching = true; stopTimer(); }, { passive: true });
      const up = () => { touching = false; stopTimer(); arm(); };
      pins.addEventListener("touchend", up, { passive: true });
      pins.addEventListener("touchcancel", up, { passive: true });
    }
    arm();
  })();
  async function boot() {
    if (window.__yycjHomeBooted) return;
    window.__yycjHomeBooted = true;
    for (let i = 0; i < 40 && !token(); i += 1) await new Promise((r) => setTimeout(r, 150));
    if (token()) await loadFeed(true);
  }
  if (document.readyState === "complete") boot();
  else window.addEventListener("load", boot);
})();
