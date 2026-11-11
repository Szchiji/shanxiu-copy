// Detail page safety net: whenever the detail view is open and its info card is missing, load the
// listing (sharing the request app.js / deep-open.js already made) and draw the card itself.
// Before, any failure left the page empty with at most a 3s toast:
//  - opening from 收藏 (app.js only renders taps on the home feed / carousel)
//  - a listing that went offline (404), an expired token (401), a slow or dropped request
//  - a share link whose request failed (deep-open.js just returned)
// It also shows 加载中… on slow loads, and 已下架 / 重试 states, and reports failures to the server log.
(() => {
  if (window.__yycjDetailGuard) return;
  window.__yycjDetailGuard = true;
  const view = document.getElementById("view-detail");
  const box = document.getElementById("detail");
  if (!view || !box) return;
  if (!document.getElementById("yycj-guard-css")) {
    const st = document.createElement("style");
    st.id = "yycj-guard-css";
    st.textContent = `#detail .yd-state{padding:48px 16px;text-align:center;}#detail .yd-state p{margin:0 0 14px;}#detail .yd-state .btn{min-width:120px;}`;
    document.head.appendChild(st);
  }
  const report = (e) => { try { if (window.__yycjReport) window.__yycjReport(e); } catch (x) {} };
  const token = () => localStorage.getItem("yycj_token") || "";
  const shown = () => !view.classList.contains("hidden");
  const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const hasCard = () => !!box.querySelector(":scope > .card h3");
  const currentId = () => box.getAttribute("data-lamp") || window.__yycjOpenLamp || "";
  let via = "deep";
  document.addEventListener("click", (ev) => {
    const c = ev.target.closest && ev.target.closest("#feed [data-id], #pins [data-id], #favList [data-id]");
    if (c) via = c.closest("#feed") ? "feed" : c.closest("#pins") ? "pin" : "fav";
  }, true);
  document.addEventListener("click", (ev) => {
    if (ev.target.closest && ev.target.closest("#detail [data-yd-back]")) { ev.preventDefault(); goBack(); return; }
    const r = ev.target.closest && ev.target.closest("#detail [data-yd-retry]");
    if (r) { ev.preventDefault(); const id = currentId(); if (id) ensure(id, Number(r.getAttribute("data-yd-retry")) + 1); }
  });
  function goBack() {
    if (window.__yycjGoBack) { try { window.__yycjGoBack(); return; } catch (e) {} }
    document.getElementById("btnBackHome")?.click();
  }
  function state(kind, attempt, msg) {
    if (hasCard()) return;
    if (kind === "loading") {
      if (!box.querySelector(":scope > .yd-state")) box.innerHTML = `<div class="yd-state muted">加载中…</div>`;
      return;
    }
    box.innerHTML = kind === "gone"
      ? `<div class="yd-state"><p class="muted">这条资料已下架或不存在</p><button class="btn" type="button" data-yd-back>返回</button></div>`
      : `<div class="yd-state"><p class="muted">${esc(msg)}</p><button class="btn primary" type="button" data-yd-retry="${attempt}">重试</button></div>`;
  }
  function wireReport(lamp) {
    const b = document.getElementById("detailReport");
    if (!b) return;
    b.onclick = async () => {
      const reason = prompt("举报原因") || "";
      if (!reason) return;
      try {
        await fetch("/api/reports", { method: "POST", headers: { "Content-Type": "application/json", Authorization: "Bearer " + token() }, body: JSON.stringify({ lamp_id: lamp.lamp_id, reason, description: "" }) });
        const t = document.getElementById("toast");
        if (t) { t.textContent = "举报已提交"; t.classList.remove("hidden", "err"); setTimeout(() => t.classList.add("hidden"), 2800); }
      } catch (e) {}
    };
  }
  // same markup as app.js (so the xhs layout, tags, extras and share placement apply unchanged)
  function render(data, id) {
    if (window.__yycjRenderDetail && window.__yycjRenderDetail(data, id)) {
      try { if (!document.querySelector("#yycjGallery .yc-slide") && window.__yycjPaintGallery) window.__yycjPaintGallery(id); } catch (e) {}
      return;
    }
    const l = data.lamp || data.item || {};
    box.innerHTML = `<div class="card"><h3>${esc(l.title || "")}</h3><div class="muted">📍 ${esc(l.city || "")}${l.district ? " · " + esc(l.district) : ""}</div><div class="muted">💰 ${esc(l.price_text || "面议")}</div><p>${esc(l.description || "")}</p><div class="row"><button class="btn primary" id="detailChat" type="button">想聊聊</button><button class="btn" id="detailReport" type="button">举报</button><button class="btn" id="detailReview" type="button">写评价</button></div></div>`;
    box.setAttribute("data-lamp", id);
    window.__yycjOpenLamp = id;
    wireReport(l);
    try { if (!document.querySelector("#yycjGallery .yc-slide") && window.__yycjPaintGallery) window.__yycjPaintGallery(id); } catch (e) {}
    try { if (window.__yycjLoadDetailExtras) window.__yycjLoadDetailExtras(id); } catch (e) {}
  }
  let seq = 0, controller=null;
  async function ensure(id, attempt) {
    controller?.abort();controller=new AbortController();const ctl=controller;const timeout=setTimeout(()=>ctl.abort(),8000);
    const my = ++seq;
    const t0 = Date.now();
    const live = () => my === seq && currentId() === id && shown();
    if (attempt > 1) state("loading");
    const slow = setTimeout(() => { if (live() && shown()) state("loading"); }, 700);
    let data = null, status = "";
    try {
      const r = await fetch("/api/lamps/" + encodeURIComponent(id), { headers: { Authorization: "Bearer " + token() }, signal:ctl.signal });
      status = String(r.status);
      if (r.ok) data = await r.json();
    } catch (e) { status = e && e.name === "AbortError" ? "timeout" : "net"; }
    clearTimeout(timeout);clearTimeout(slow);
    if (!live()) return;
    const ms = Date.now() - t0;
    if (data && (data.lamp || data.item)) {
      // app.js / deep-open.js render from the same response; give them a moment first
      await new Promise((r) => setTimeout(r, 150));
      if (!live()) return;
      if (!hasCard()) { render(data, id); report({ ev: "detail_blank", lamp: id, via, ms }); }
      else if (ms > 5000) report({ ev: "detail_slow", lamp: id, via, ms });
      return;
    }
    report({ ev: "detail_fail", lamp: id, via, status, ms, try: attempt });
    if (hasCard()) return;
    if (status === "404") state("gone");
    else if (status === "401") state("fail", attempt, "登录已过期，请关闭小程序后重新打开");
    else if (status === "403") state("fail", attempt, "账号受限，暂时无法查看");
    else state("fail", attempt, "网络不稳定，资料没加载出来");
  }
  let last = "";
  function check() {
    if (!shown()) { last = "";++seq;controller?.abort();controller=null;return; }
    const id = currentId();
    if (!id || id === last) return;
    last = id;
    if (!hasCard() || box.getAttribute("data-lamp") !== id) ensure(id, 1);
  }
  new MutationObserver(() => setTimeout(check, 0)).observe(view, { attributes: true, attributeFilter: ["class"] });
  new MutationObserver(() => setTimeout(check, 0)).observe(box, { attributes: true, attributeFilter: ["data-lamp"] });
  window.__yycjEnsureDetail = (id) => ensure(id || currentId(), 1);
  check();
})();
