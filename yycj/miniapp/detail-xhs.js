// 小红书-style detail layout. Restyles/re-positions the existing detail DOM only:
// every button keeps its original element + handlers (想聊聊 / 收藏 / 分享 / 写评价 / 举报).
(() => {
  if (window.__yycjDetailXhs) return;
  window.__yycjDetailXhs = true;
  const view = document.getElementById("view-detail");
  if (!view) return;
  if (!document.getElementById("yycj-xhs-css")) {
    const st = document.createElement("style");
    st.id = "yycj-xhs-css";
    st.textContent = `:root{--xhs-safe:max(env(safe-area-inset-top,0px),calc(var(--tg-safe-area-inset-top,0px) + var(--tg-content-safe-area-inset-top,0px)));--xhs-top-h:calc(48px + var(--xhs-safe));}
body.xhs-detail .bottom-nav{display:none!important;}
body.xhs-detail .topbar{display:none!important;}
#xhsTop{position:fixed;top:0;left:0;right:0;z-index:60;display:none;grid-template-columns:48px minmax(0,1fr) 48px;align-items:center;box-sizing:border-box;height:var(--xhs-top-h);padding:var(--xhs-safe) 4px 0;background:rgba(11,16,32,.96);border-bottom:1px solid rgba(255,255,255,.07);}
body.xhs-detail #xhsTop{display:grid;}
#xhsTop .xt-back{width:44px;height:44px;margin:0 0 0 2px;padding:0;border:0;border-radius:22px;background:none;display:flex;align-items:center;justify-content:center;-webkit-tap-highlight-color:transparent;}
#xhsTop .xt-back::before{content:"";width:11px;height:11px;border-left:2px solid #e8ecff;border-bottom:2px solid #e8ecff;transform:translateX(3px) rotate(45deg);}
#xhsTop .xt-back:active{background:rgba(255,255,255,.08);}
#xhsTop .xt-name{min-width:0;text-align:center;font-size:16px;font-weight:700;color:#e8ecff;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
#view-detail.xhs{padding-top:var(--xhs-top-h);}
#view-detail.xhs #btnBackHome{display:none!important;}
#view-detail.xhs{position:relative;padding-bottom:calc(76px + env(safe-area-inset-bottom));}
#view-detail.xhs #btnBackHome{position:absolute;top:10px;left:10px;z-index:6;width:36px;height:36px;min-height:0;padding:0;margin:0;border-radius:18px;border:0;background:rgba(0,0,0,.45);color:#fff;font-size:0;line-height:0;display:flex;align-items:center;justify-content:center;}
#view-detail.xhs #btnBackHome::before{content:"";width:10px;height:10px;border-left:2px solid #fff;border-bottom:2px solid #fff;transform:translateX(2px) rotate(45deg);}
#view-detail.xhs #yycjGallery{margin:-8px -12px 0;}
#view-detail.xhs #yycjGallery .yc-track{border-radius:0;background:#000;}
#view-detail.xhs #yycjGallery .yc-slide{height:min(calc(min(100vw,720px) * var(--yc-r,1.3333)),72vh);background:#000;}
#view-detail.xhs #yycjGallery .yc-slide img{background:#000;}
#view-detail.xhs #yycjGallery .yc-count{top:12px;right:12px;}
#view-detail.xhs #yycjGallery .yc-dots{margin:10px 0 0;}
#view-detail.xhs #yycjGallery .yc-dot{width:5px;height:5px;background:rgba(255,255,255,.28);}
#view-detail.xhs #yycjGallery .yc-dot.on{width:5px;background:#ff2e4d;}
#view-detail.xhs #detail>.card{display:flex;flex-direction:column;background:none;border:0;box-shadow:none;border-radius:0;padding:12px 4px 4px;margin:0;}
#view-detail.xhs #detail>.card>*{order:5;margin-left:0;margin-right:0;}
#view-detail.xhs .xhs-pub{order:0!important;display:flex;align-items:center;gap:10px;margin:0 0 10px;}
#view-detail.xhs .xhs-av{flex:0 0 36px;width:36px;height:36px;border-radius:18px;overflow:hidden;background:#2a3352;color:#fff;display:flex;align-items:center;justify-content:center;font-size:15px;font-weight:600;}
#view-detail.xhs .xhs-av img{width:100%;height:100%;object-fit:cover;display:block;}
#view-detail.xhs .xhs-pub h3{flex:1;min-width:0;margin:0;font-size:17px;font-weight:700;line-height:1.3;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
#view-detail.xhs .xhs-open{flex:0 0 auto;padding:3px 10px;border-radius:999px;font-size:12px;border:1px solid rgba(61,214,140,.5);color:#3dd68c;}
#view-detail.xhs .xhs-open.off{border-color:#2a3352;color:#9aa3c7;}
#view-detail.xhs #detail>.card>p{order:2;font-size:15px;line-height:1.65;margin:2px 0 8px;white-space:pre-wrap;word-break:break-word;color:#e8ecff;}
#view-detail.xhs .xhs-price{order:3!important;font-size:18px!important;font-weight:700;color:#ff6b8a!important;margin:2px 0 4px;}
#view-detail.xhs #yycjTags{order:4;margin:6px 0 6px;gap:8px;}
#view-detail.xhs #yycjTags .tag{background:none;padding:0;border-radius:0;color:#8ec8ff;font-size:14px;}
#view-detail.xhs #yycjExtras{order:5;margin:6px 0;padding:10px 12px;border-radius:10px;background:rgba(255,255,255,.04);}
#view-detail.xhs .xhs-meta{order:6!important;display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin:10px 0 4px;font-size:12px;color:#7d86a8;}
#view-detail.xhs .xhs-meta .muted{margin:0;font-size:12px;color:#7d86a8;}
#view-detail.xhs .xhs-meta #detailReport{margin-left:auto;background:none;border:0;padding:6px 0 6px 10px;min-height:0;color:#7d86a8;font-size:12px;text-decoration:underline;}
#view-detail.xhs #detail>.card>.row{display:none;}
#view-detail.xhs #yycjReviews{margin:12px -12px 0;padding:12px 16px 4px;border-top:8px solid rgba(255,255,255,.035);}
#view-detail.xhs #yycjReviews h3{font-size:14px!important;color:#9aa3c7;font-weight:600;}
#view-detail.xhs #yycjReviews .card{background:none;border:0;border-bottom:1px solid rgba(255,255,255,.06);border-radius:0;padding:8px 0;margin:0!important;}
#view-detail.xhs .xhs-bar{position:fixed;left:0;right:0;bottom:0;z-index:50;display:flex;align-items:center;gap:6px;padding:8px 12px calc(8px + env(safe-area-inset-bottom));background:rgba(11,16,32,.96);border-top:1px solid rgba(255,255,255,.07);}
#view-detail.xhs .xhs-bar .xhs-ic{flex:0 0 50px!important;width:50px!important;height:48px;min-height:0;margin:0;padding:0;border:0;border-radius:10px;background:none;color:#e8ecff;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:1px;}
#view-detail.xhs .xhs-bar .xhs-ic .xi{font-style:normal;font-size:20px;line-height:24px;height:24px;}
#view-detail.xhs .xhs-bar .xhs-ic .xl{font-weight:400;font-size:10px;line-height:12px;color:#9aa3c7;}
#view-detail.xhs .xhs-bar .xhs-ic .xi.on{color:#ff6b8a;}
#view-detail.xhs .xhs-bar #detailChat{flex:1;height:44px;min-height:0;margin:0 0 0 6px;border-radius:22px;font-size:16px;font-weight:700;background:#ff2e4d;color:#fff;border:0;}`;
    document.head.appendChild(st);
  }
  const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':'&quot;',"'":"&#39;" }[c]));
  const isGuest = () => { try { return (JSON.parse(localStorage.getItem("yycj_user") || "{}").role || "guest") === "guest"; } catch (e) { return true; } };
  function lampData(id) {
    const c = (window.__yycjLampCache || {})[id];
    return (c && (c.lamp || c.item || c)) || {};
  }
  function avatarSrc(lamp) {
    // 36px avatar: the same 480px thumbnail the card used (usually already in the browser cache)
    try { if (window.yycjCoverOf) { const u = window.yycjCoverOf(lamp); if (u) return window.yycjThumb ? window.yycjThumb(u, 480) : u; } } catch (e) {}
    if (lamp.cover_url) return lamp.cover_url;
    const img = document.querySelector('#yycjGallery .yc-slide[data-type="image"] img');
    return img ? img.getAttribute("src") : "";
  }
  function mmdd(s) {
    const d = s ? new Date(s) : null;
    if (!d || isNaN(d)) return "";
    return String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  }
  function decorate() {
    const detail = document.getElementById("detail");
    const card = detail && detail.querySelector(":scope > .card");
    if (!card) return;
    view.classList.add("xhs");
    const id = detail.getAttribute("data-lamp") || window.__yycjOpenLamp || "";
    const lamp = lampData(id);
    // publisher row: avatar + 花名 (the existing h3) + open status
    const h3 = card.querySelector(":scope > h3, :scope > .xhs-pub > h3");
    let pub = card.querySelector(":scope > .xhs-pub");
    if (!pub && h3) {
      pub = document.createElement("div");
      pub.className = "xhs-pub";
      pub.innerHTML = `<span class="xhs-av"></span>`;
      card.insertBefore(pub, card.firstChild);
      pub.appendChild(h3);
    }
    if (pub) {
      const av = pub.querySelector(".xhs-av");
      const src = avatarSrc(lamp);
      if (src && av.dataset.src !== src) { av.dataset.src = src; av.innerHTML = `<img src="${esc(src)}" alt="" />`; }
      else if (!src && !av.textContent) av.textContent = ((h3 && h3.textContent) || "·").trim().slice(0, 1);
      let open = pub.querySelector(".xhs-open");
      const ot = String(lamp.open_text || "").trim();
      if (ot && !open) { open = document.createElement("span"); open.className = "xhs-open"; pub.appendChild(open); }
      if (open) { if (!ot) open.remove(); else { if (open.textContent !== ot) open.textContent = ot; const off = lamp.open_code !== "open"; if (open.classList.contains("off") !== off) open.classList.toggle("off", off); if (open.dataset.code !== String(lamp.open_code || "")) open.dataset.code = String(lamp.open_code || ""); } }
    }
    // price / location lines are plain .muted lines written by app.js
    const lines = [...card.querySelectorAll(":scope > .muted, :scope > p.muted, :scope > .xhs-meta .muted")];
    const price = lines.find((x) => (x.textContent || "").includes("💰"));
    if (price) price.classList.add("xhs-price");
    const loc = lines.find((x) => (x.textContent || "").includes("📍"));
    let meta = card.querySelector(":scope > .xhs-meta");
    if (!meta) { meta = document.createElement("div"); meta.className = "xhs-meta"; card.appendChild(meta); }
    if (loc && loc.parentNode !== meta) meta.insertBefore(loc, meta.firstChild);
    let tm = meta.querySelector(".xhs-time");
    const when = [lamp.hours_text ? "营业 " + lamp.hours_text : "", mmdd(lamp.updated_at || lamp.created_at)].filter(Boolean).join(" · ");
    if (when && !tm) { tm = document.createElement("span"); tm.className = "xhs-time"; meta.appendChild(tm); }
    if (tm && tm.textContent !== when) tm.textContent = when;
    const rep = document.getElementById("detailReport");
    if (rep && meta.lastElementChild !== rep) meta.appendChild(rep);
    // fixed bottom bar: 收藏 / 分享 / 写评价 icons + 想聊聊 primary
    let bar = detail.querySelector(":scope > .xhs-bar");
    if (!bar) { bar = document.createElement("div"); bar.className = "xhs-bar"; detail.appendChild(bar); }
    let fav = bar.querySelector(".xhs-fav");
    if (!fav && id && isGuest()) {
      fav = document.createElement("button");
      fav.type = "button";
      fav.className = "xhs-ic xhs-fav";
      fav.innerHTML = `<i class="xi" data-fav="${esc(id)}">♡</i><b class="xl">收藏</b>`;
      // fav.js handles clicks on [data-fav]; forward taps on the label/padding to it.
      fav.addEventListener("click", (ev) => { const i = fav.querySelector("[data-fav]"); if (i && !ev.target.closest("[data-fav]")) i.click(); });
      bar.insertBefore(fav, bar.firstChild);
      if (window.__yycjPaintFav) setTimeout(window.__yycjPaintFav, 0);
    }
    const fi = fav && fav.querySelector("[data-fav]");
    if (fi && id && fi.getAttribute("data-fav") !== id) { fi.setAttribute("data-fav", id); if (window.__yycjPaintFav) setTimeout(window.__yycjPaintFav, 0); }
    const iconize = (btn, glyph, label) => {
      btn.classList.add("xhs-ic");
      if (!btn.querySelector(".xi")) btn.innerHTML = `<i class="xi">${glyph}</i><b class="xl">${label}</b>`;
    };
    const share = document.getElementById("detailShare");
    if (share && detail.contains(share)) { iconize(share, "↗", "分享"); if (share.parentNode !== bar) bar.insertBefore(share, bar.querySelector("#detailReview, #detailChat")); }
    const review = document.getElementById("detailReview");
    if (review) { iconize(review, "✎", "评价"); if (review.parentNode !== bar) bar.insertBefore(review, bar.querySelector("#detailChat")); }
    const chat = document.getElementById("detailChat");
    if (chat && chat.parentNode !== bar) bar.appendChild(chat);
  }
  // Fixed detail top bar: back (same as the existing 返回) + 花名 centered.
  let top = document.getElementById("xhsTop");
  if (!top) {
    top = document.createElement("div");
    top.id = "xhsTop";
    top.innerHTML = `<button type="button" class="xt-back" aria-label="返回"></button><div class="xt-name"></div><span></span>`;
    document.body.appendChild(top);
  }
  function goBack() {
    if (document.getElementById("yycjViewer") && window.__yycjCloseViewer) { window.__yycjCloseViewer(); return; }
    document.getElementById("btnBackHome")?.click();
  }
  top.querySelector(".xt-back").addEventListener("click", (ev) => { ev.preventDefault(); goBack(); });
  function syncName() {
    const n = (document.querySelector("#detail .xhs-pub h3, #detail > .card h3")?.textContent || "").trim();
    const el = top.querySelector(".xt-name");
    if (el.textContent !== n) { el.textContent = n; el.title = n; }
  }
  const tg = window.Telegram && window.Telegram.WebApp;
  function syncBody() {
    const on = !view.classList.contains("hidden");
    document.body.classList.toggle("xhs-detail", on);
    try { if (tg && tg.BackButton) { if (on) tg.BackButton.show(); else tg.BackButton.hide(); } } catch (e) {}
  }
  // Telegram BackButton: closes the full-screen viewer first (detail-fix.js), otherwise goes back like 返回.
  try {
    if (tg && tg.onEvent) tg.onEvent("backButtonClicked", () => {
      if (Date.now() - (window.__yycjViewerClosedAt || 0) < 400) return;
      if (!view.classList.contains("hidden")) goBack();
    });
  } catch (e) {}
  // Re-run on every change; never drop one. The old "busy" flag ignored any mutation that landed in
  // the same microtask burst as a decorate pass, so when the info card was re-rendered right after
  // (app.js rendering after another script painted), the new card stayed undecorated: no avatar
  // row, no bottom bar. decorate() is idempotent, so its own changes cost one no-op pass.
  let queued = false, passes = 0, since = 0, trail = 0;
  function run() {
    queued = false;
    try { decorate(); syncName(); } catch (e) { try { window.__yycjReport && window.__yycjReport({ ev: "js_error", msg: String(e && e.message || e), src: "detail-xhs.js:decorate" }); } catch (x) {} }
  }
  function schedule() {
    if (queued) return;
    const now = Date.now();
    if (now - since > 1000) { since = now; passes = 0; }
    if (++passes > 25) { clearTimeout(trail); trail = setTimeout(run, 300); return; } // loop safety
    queued = true;
    Promise.resolve().then(run);
  }
  new MutationObserver(schedule).observe(view, { childList: true, subtree: true });
  window.__yycjDecorateDetail = run;
  new MutationObserver(syncBody).observe(view, { attributes: true, attributeFilter: ["class"] });
  syncBody();
})();
