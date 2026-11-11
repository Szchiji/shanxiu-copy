// 左滑返回 (iOS-style edge swipe) + list scroll memory.
// - Start a touch within 24px of the left edge and drag right: the current screen follows the
//   finger; let go past ~1/3 of the width (or flick) to go back, otherwise it springs back.
// - "Back" is exactly what the existing buttons do: full-screen viewer → close it; sheets / 地区
//   panel / city list → close; 写评价 → the review top-bar back; detail → the detail top-bar back
//   (same as Telegram BackButton, returns to 首页 or 收藏); 改资料 → 我的; 上架 / 我的 / 收藏 → 首页.
//   Nothing on the home root.
// - Only edge starts count, so swiping photos in the detail gallery is unaffected.
// - Opening a detail from a list starts it at the top; going back restores the list position.
(() => {
  if (window.__yycjSwipeBack) return;
  window.__yycjSwipeBack = true;
  const $ = (s) => document.querySelector(s);
  const shown = (el) => !!el && !el.classList.contains("hidden");
  const EDGE = 24;
  const tg = window.Telegram && window.Telegram.WebApp;
  const home = $("#view-home");
  const detail = $("#view-detail");
  const review = $("#view-review");

  // ---------- scroll memory ----------
  let ret = null;      // where to come back to: {view, y}
  let wantTop = false; // a card was tapped: show the detail from its top
  document.addEventListener("click", (ev) => {
    const t = ev.target;
    const card = t && t.closest && t.closest("#feed [data-id], #pins [data-id], #favList [data-id]");
    if (!card || t.closest("[data-fav],[data-unfav],[data-share],a")) return;
    const v = card.closest(".view");
    ret = { view: v ? v.id : "view-home", y: window.scrollY || document.documentElement.scrollTop || 0 };
    wantTop = true;
  }, true);
  function onViews() {
    if (wantTop && shown(detail)) { wantTop = false; window.scrollTo(0, 0); }
    if (ret && !shown(detail) && !shown(review)) {
      const v = document.getElementById(ret.view);
      if (shown(v)) { const y = ret.y; ret = null; requestAnimationFrame(() => window.scrollTo(0, y)); }
      else if ([...document.querySelectorAll(".view")].some(shown)) ret = null;
    }
    syncListeners();
  }
  const vmo = new MutationObserver(onViews);
  document.querySelectorAll(".view").forEach((v) => vmo.observe(v, { attributes: true, attributeFilter: ["class"] }));

  // ---------- what "back" means right now ----------
  function sheetOpen() {
    for (const id of ["guideSheet", "citySheet"]) {
      const s = document.getElementById(id);
      if (s && shown(s) && getComputedStyle(s).display !== "none") return s;
    }
    return null;
  }
  const click = (sel) => () => { const b = $(sel); if (b) b.click(); };
  function target() {
    const viewer = $("#yycjViewer");
    if (viewer) return { move: [viewer], run: () => window.__yycjCloseViewer && window.__yycjCloseViewer() };
    const sh = sheetOpen();
    if (sh) return { move: [sh.querySelector(".sheet-panel") || sh], fade: sh, run: () => sh.classList.add("hidden") };
    const area = $("#areaPanel");
    if (shown(area) && shown(home)) return { move: [area], run: () => (window.__yycjCloseArea ? window.__yycjCloseArea() : area.classList.add("hidden")) };
    const drop = $("#cityDrop");
    if (shown(drop)) return { move: [drop], run: () => drop.classList.add("hidden") };
    if (window.__yycjMapOverlayOpen && window.__yycjMapOverlayOpen()) {
      const cho = $("#hubMapChooser");
      const sel = $("#hubMapSelection");
      const filt = $("#mapFullFilterPanel");
      const layer = (cho && shown(cho) && cho) || (sel && shown(sel) && sel) || (filt && shown(filt) && filt) || $("#view-map");
      return { move: [layer], run: () => window.__yycjCloseMapOverlays && window.__yycjCloseMapOverlays() };
    }
    if (window.__yycjMapFullOpen && window.__yycjMapFullOpen()) {
      const page = $("#view-map");
      return page ? { page, run: () => window.__yycjCloseMap && window.__yycjCloseMap() } : { move: [$("#app")], run: () => window.__yycjCloseMap && window.__yycjCloseMap() };
    }
    if (shown(review)) return { page: review, bars: ["#revTop"], run: click("#revTop .xt-back") };
    if (shown(detail)) return { page: detail, bars: ["#xhsTop"], run: () => { const b = $("#xhsTop .xt-back") || $("#btnBackHome"); if (b) b.click(); } };
    const pub = $("#view-publish");
    const edit = $("#editLampId");
    if (shown(pub) && edit && edit.value) return { page: pub, run: click('[data-nav="me"]') };
    for (const id of ["view-publish", "view-me", "view-fav"]) {
      const v = document.getElementById(id);
      if (shown(v)) return { page: v, run: click('[data-nav="home"]') };
    }
    return null;
  }
  window.__yycjBackTarget = () => { const t = target(); return t ? (t.page ? t.page.id : (t.move[0].id || t.move[0].className)) : ""; };
  window.__yycjGoBack = () => { const t = target(); if (t) t.run(); return !!t; };

  // Layers that slide. A transformed ancestor would re-anchor position:fixed children (the detail
  // bottom bar), so the view's children move individually and fixed bars move on their own.
  function layersOf(t) {
    if (!t.page) return t.move.slice();
    const out = [];
    const walk = (el) => {
      for (const c of el.children) {
        if (c.matches(".xhs-bar")) out.push(c);
        else if (c.querySelector(".xhs-bar")) walk(c);
        else out.push(c);
      }
    };
    walk(t.page);
    (t.bars || []).forEach((s) => { const b = $(s); if (b) out.push(b); });
    return out;
  }

  // ---------- visuals ----------
  const st = document.createElement("style");
  st.id = "yycj-swipe-css";
  st.textContent = `#swbScrim{position:fixed;top:0;bottom:0;left:0;width:100vw;background:rgba(0,0,0,.28);pointer-events:none;display:none;will-change:transform;}
#swbEdge{position:fixed;top:0;bottom:0;left:-14px;width:14px;background:linear-gradient(to left,rgba(0,0,0,.28),rgba(0,0,0,0));pointer-events:none;display:none;will-change:transform;}
html.swb-drag,html.swb-drag body{overscroll-behavior:none;}`;
  document.head.appendChild(st);
  const scrim = document.createElement("div");
  scrim.id = "swbScrim";
  const edgeEl = document.createElement("div");
  edgeEl.id = "swbEdge";
  // first in <body>: painted under the sliding layers, over the page background
  document.body.insertBefore(edgeEl, document.body.firstChild);
  document.body.insertBefore(scrim, document.body.firstChild);

  // ---------- gesture ----------
  let g = null; // {x0, y0, t, layers, lastX, lastT, vx, engaged, swipesOff}
  const W = () => window.innerWidth || document.documentElement.clientWidth || 390;
  function paintAt(dx, anim) {
    const tr = anim ? "transform .22s cubic-bezier(.2,.8,.2,1), opacity .22s" : "none";
    g.layers.forEach((el) => { el.style.transition = tr; el.style.transform = dx ? `translate3d(${dx}px,0,0)` : ""; });
    if (g.target.page) {
      const w = W();
      scrim.style.transition = tr;
      edgeEl.style.transition = tr;
      scrim.style.transform = `translate3d(${dx - w}px,0,0)`;
      scrim.style.opacity = String(Math.max(0, 1 - dx / w));
      edgeEl.style.transform = `translate3d(${dx}px,0,0)`;
    } else if (g.target.fade) {
      g.target.fade.style.transition = tr;
      g.target.fade.style.opacity = String(Math.max(0.2, 1 - dx / W()));
    }
  }
  function cleanup(state) {
    state.layers.forEach((el) => { el.style.transition = ""; el.style.transform = ""; el.style.willChange = ""; });
    if (state.target.fade) { state.target.fade.style.transition = ""; state.target.fade.style.opacity = ""; }
    scrim.style.display = "none";
    edgeEl.style.display = "none";
    scrim.style.transition = edgeEl.style.transition = "";
    document.documentElement.classList.remove("swb-drag");
    try { if (state.swipesOff && tg && tg.enableVerticalSwipes) tg.enableVerticalSwipes(); } catch (e) {}
  }
  function engage() {
    g.engaged = true;
    g.layers = layersOf(g.target);
    g.layers.forEach((el) => { el.style.willChange = "transform"; });
    if (g.target.page) { scrim.style.display = "block"; edgeEl.style.display = "block"; }
    document.documentElement.classList.add("swb-drag");
    try {
      // a sideways drag must not turn into Telegram's swipe-down-to-minimize
      if (tg && tg.disableVerticalSwipes && tg.isVerticalSwipesEnabled !== false) { tg.disableVerticalSwipes(); g.swipesOff = true; }
    } catch (e) {}
  }
  function onStart(ev) {
    if (g || ev.touches.length !== 1) { if (g && !g.engaged) g = null; return; }
    const p = ev.touches[0];
    if (p.clientX > EDGE) return;
    const t = target();
    if (!t) return;
    g = { x0: p.clientX, y0: p.clientY, lastX: p.clientX, lastT: ev.timeStamp, vx: 0, engaged: false, decided: false, target: t, layers: [] };
  }
  function onMove(ev) {
    if (!g) return;
    const p = ev.touches[0];
    const dx = p.clientX - g.x0;
    const dy = p.clientY - g.y0;
    if (!g.decided) {
      if (Math.abs(dx) < 1 && Math.abs(dy) < 1) return;
      g.decided = true;
      // decide on the very first move (still cancelable): mostly-rightward = back gesture
      if (dx > 0 && dx >= Math.abs(dy)) engage();
      else { g = null; return; }
    }
    if (!g.engaged) return;
    if (ev.cancelable) ev.preventDefault();
    const dt = Math.max(1, ev.timeStamp - g.lastT);
    g.vx = 0.7 * ((p.clientX - g.lastX) / dt) + 0.3 * g.vx;
    g.lastX = p.clientX;
    g.lastT = ev.timeStamp;
    paintAt(Math.max(0, dx), false);
  }
  function onEnd() {
    if (!g) return;
    const s = g;
    g = null;
    if (!s.engaged) return;
    const dx = Math.max(0, s.lastX - s.x0);
    const w = W();
    const commit = dx > w * 0.33 || (s.vx > 0.45 && dx > 30);
    g = s; // paintAt reads g
    paintAt(commit ? w : 0, true);
    g = null;
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      if (commit) { try { s.target.run(); } catch (e) {} }
      // same task as the view switch: no frame shows the old screen snapping back
      cleanup(s);
    };
    const last = s.layers[0];
    if (last) last.addEventListener("transitionend", finish, { once: true });
    setTimeout(finish, 260);
  }
  // touchmove has to be non-passive to stop scrolling once a back swipe is engaged. It is only
  // attached while there is somewhere to go back to, so the home feed keeps fully passive scrolling.
  let attached = false;
  function syncListeners() {
    const want = !!target();
    if (want === attached) return;
    attached = want;
    if (want) {
      document.addEventListener("touchstart", onStart, { passive: true });
      document.addEventListener("touchmove", onMove, { passive: false });
      document.addEventListener("touchend", onEnd, { passive: true });
      document.addEventListener("touchcancel", onEnd, { passive: true });
    } else if (!g) {
      document.removeEventListener("touchstart", onStart, { passive: true });
      document.removeEventListener("touchmove", onMove, { passive: false });
      document.removeEventListener("touchend", onEnd, { passive: true });
      document.removeEventListener("touchcancel", onEnd, { passive: true });
    } else attached = true;
  }
  // overlays (sheets, 地区 panel, city list, photo viewer) also change whether back exists
  const omo = new MutationObserver(() => syncListeners());
  ["guideSheet", "citySheet", "areaPanel", "cityDrop", "view-map", "app"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) omo.observe(el, { attributes: true, attributeFilter: ["class"] });
  });
  omo.observe(document.body, { childList: true });
  // the 地区 panel / guide sheet are created by home-ui.js (loaded earlier, but be safe)
  setTimeout(() => {
    ["guideSheet", "areaPanel"].forEach((id) => { const el = document.getElementById(id); if (el) omo.observe(el, { attributes: true, attributeFilter: ["class"] }); });
    syncListeners();
  }, 0);
  syncListeners();
})();
