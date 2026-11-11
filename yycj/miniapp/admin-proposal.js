// Admin button-layout proposal (branch only). Re-arranges existing buttons; every button keeps its handlers.
(() => {
  if (window.__yycjAdminProposal) return;
  window.__yycjAdminProposal = true;
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  function actRow(row) {
    if (!row || row.dataset.pp || row.closest("form") || row.closest(".ad-more-menu")) return;
    const kids = [...row.children].filter((k) => k.matches("button, a.btn, .ad-more"));
    if (!kids.length || kids.length !== [...row.children].filter((k) => !k.classList.contains("a-gap")).length) return;
    row.dataset.pp = "1";
    row.classList.add("act-row");
    const host = row.parentNode;
    if (host && host.classList.contains("pick-item")) host.classList.add("stack");
    kids.forEach((k) => {
      const b = k.matches(".ad-more") ? k.querySelector(".btn") : k;
      const cls = k.classList.contains("danger") ? "a-danger" : (b && b.classList.contains("primary")) ? "a-primary" : "a-sec";
      k.classList.add(cls);
    });
    if (!row.querySelector(":scope > .a-gap")) { const g = document.createElement("span"); g.className = "a-gap"; row.appendChild(g); }
  }

  // ===== Unified list-row action bar =====
  // [危险(红, 左)] ……… [更多 ⋯] [主操作(右)]  — at most 2 visible actions; the rest live in 更多 (bottom sheet).
  // Real buttons never leave their list container, so every existing handler/API call still fires unchanged.
  function mk(tag, cls, html) { const e = document.createElement(tag); if (cls) e.className = cls; if (html != null) e.innerHTML = html; return e; }
  function lbar(host, spec, anchor) {
    const sig = spec.sig || "";
    let bar = host.querySelector(":scope > .lbar");
    if (bar && bar.dataset.sig === sig) return;
    if (!bar) { bar = mk("div", "lbar"); (anchor ? anchor.after(bar) : host.appendChild(bar)); }
    bar.dataset.sig = sig;
    const keep = [spec.danger, spec.main, ...(spec.more || [])].filter(Boolean);
    bar.innerHTML = "";
    if (spec.danger) { spec.danger.classList.add("lb-danger"); bar.appendChild(spec.danger); }
    bar.appendChild(mk("span", "lb-gap"));
    if (spec.more && spec.more.length) {
      const wrap = mk("div", "lb-morewrap");
      const btn = mk("button", "btn lb-more", "更多 <i>⋯</i>");
      btn.type = "button";
      const store = mk("div", "lb-store");
      spec.more.forEach((b) => { b.classList.remove("lb-danger", "lb-main"); store.appendChild(b); });
      wrap.appendChild(btn);
      wrap.appendChild(store);
      bar.appendChild(wrap);
    }
    if (spec.main) { spec.main.classList.add("lb-main"); bar.appendChild(spec.main); }
    keep.forEach((b) => { b.classList.remove("a-danger", "a-sec", "a-primary"); b.style.display = ""; });
    host.classList.add("lb-host");
  }
  const q = (root, sel) => root.querySelector(sel);
  function lists() {
    // 待审资料: 拒绝 | 通过 (同时上轮播 = switch above the bar)
    $$("#adminPosts > .card").forEach((c) => {
      const row = c.querySelector(":scope > .row, :scope > .lbar");
      const ok = q(c, '[data-admin="post-ok"]'), no = q(c, '[data-admin="post-no"]'), pin = q(c, '[data-admin="post-pin"]');
      if (!ok) return;
      lbar(c, { sig: "post", danger: no, main: ok, more: [] }, row);
      if (pin && !pin.closest(".lb-store")) { const st = c.querySelector(".lbar"); let s = st.querySelector(".lb-store"); if (!s) { s = mk("div", "lb-store"); st.appendChild(s); } s.appendChild(pin); }
      if (row && row.classList.contains("row") && !row.children.length) row.remove();
    });
    // 待审评价: 拒绝 | 更多(拒绝刷评) 通过
    $$("#adminReviews > .card").forEach((c) => {
      const row = c.querySelector(":scope > .row");
      const ok = q(c, '[data-admin="rev-ok"]'), no = q(c, '[data-admin="rev-no"]'), brush = q(c, '[data-admin="rev-brush"]');
      if (!ok) return;
      lbar(c, { sig: "rev", danger: no, main: ok, more: [brush].filter(Boolean) }, row);
      if (row && !row.children.length) row.remove();
    });
    // 举报: 驳回 | 采纳
    $$("#adminReports > .card").forEach((c) => {
      const row = c.querySelector(":scope > .row");
      const ok = q(c, '[data-admin="rep-ok"]'), no = q(c, '[data-admin="rep-no"]');
      if (!ok) return;
      lbar(c, { sig: "rep", danger: no, main: ok, more: [] }, row);
      if (row && !row.children.length) row.remove();
    });
    // 上架资料: 上架中 → 下架 | 更多(续期) 代改 ; 已下架 → 更多(代改, 续期) 重新上架
    $$("#adminListings > .pick-item").forEach((it) => {
      if (it.dataset.ux !== "1") return; // wait for admin-ux.js to finish its own pass first
      const on = !!it.querySelector(".st-active");
      const find = (sel) => it.querySelector(sel);
      const unlist = find('[data-list="unlist"]'), relist = find('[data-list="relist"]'), renew = find('[data-list="renew"]'), proxy = find("[data-fill-proxy]"), del = find('[data-list="delete"]');
      // drop admin-ux's own 更多 + duplicate buttons (a second 下架 lived in its menu)
      it.querySelectorAll('[data-list="unlist"]').forEach((b) => { if (b !== unlist) b.remove(); });
      const oldRow = it.querySelector(":scope > .row");
      if (renew && renew.textContent !== "续期") renew.textContent = "续期";
      if (proxy && proxy.textContent !== "代改") proxy.textContent = "代改";
      if (del && del.textContent !== "删除") del.textContent = "删除";
      const spec = on ? { sig: "L1d", danger: unlist, main: proxy, more: [renew, del] } : { sig: "L0d", danger: null, main: relist, more: [proxy, renew, del] };
      spec.more = spec.more.filter(Boolean);
      lbar(it, spec);
      if (oldRow) oldRow.remove();
    });
    // 卡片置顶 / 精选轮播 (current): 取消置顶 / 移出轮播 (red, left, already confirmed by admin-picks.js)
    $$("#adminFeedPins .pick-now, #adminPins .pick-now").forEach((it) => {
      const b = it.querySelector("[data-pin-act]");
      if (!b) return;
      const lbl = b.getAttribute("data-pin-act") === "feed-unpin" ? "取消置顶" : "移出轮播";
      if (b.textContent !== lbl) b.textContent = lbl;
      const acts = it.querySelector(":scope > .pick-actions");
      lbar(it, { sig: "pin", danger: b, main: null, more: [] });
      if (acts && !acts.children.length) acts.remove();
    });
    // 用户: 正常 → 拉黑 | 更多(解除拉黑, 解锁角色) ; 已拉黑 → 更多(解锁角色, 拉黑) 解除拉黑
    $$("#adminUsers > .pick-item").forEach((it) => {
      const ban = it.querySelector('[data-ban="1"]'), unban = it.querySelector('[data-ban="0"]'), unlock = it.querySelector("[data-unlock-role]");
      if (!ban && !unban) return;
      const banned = /已拉黑/.test(it.querySelector(".muted")?.textContent || "");
      const row = it.querySelector(":scope > .row");
      const spec = banned
        ? { sig: "U1" + (unlock ? "r" : ""), danger: null, main: unban, more: [unlock, ban] }
        : { sig: "U0" + (unlock ? "r" : ""), danger: ban, main: null, more: [unlock] };
      spec.more = spec.more.filter(Boolean);
      lbar(it, spec);
      if (row && !row.children.length) row.remove();
    });
  }
  // 更多 → bottom sheet with proxies of the real (hidden) buttons
  function sheet() {
    let s = document.getElementById("lbSheet");
    if (!s) {
      s = mk("div", "lb-sheet hidden", `<div class="lb-card"><div class="lb-title"></div><div class="lb-items"></div><button type="button" class="btn lb-cancel">取消</button></div>`);
      s.id = "lbSheet";
      document.body.appendChild(s);
      s.addEventListener("click", (e) => { if (e.target === s || e.target.closest(".lb-cancel")) s.classList.add("hidden"); });
    }
    return s;
  }
  document.addEventListener("click", (ev) => {
    const more = ev.target.closest(".lb-more");
    if (!more) return;
    ev.preventDefault();
    ev.stopPropagation();
    const store = more.parentNode.querySelector(".lb-store");
    const host = more.closest(".lb-host");
    const s = sheet();
    s.querySelector(".lb-title").textContent = (host?.querySelector("strong")?.textContent || "").trim() || "更多操作";
    const items = s.querySelector(".lb-items");
    items.innerHTML = "";
    [...(store?.children || [])].filter((b) => b.getAttribute("data-admin") !== "post-pin").forEach((real) => {
      const p = mk("button", "btn lb-item" + (real.classList.contains("danger") ? " danger" : ""), "");
      p.type = "button";
      p.textContent = real.textContent.trim();
      p.addEventListener("click", () => { s.classList.add("hidden"); real.click(); });
      items.appendChild(p);
    });
    s.classList.remove("hidden");
  }, true);
  // Confirm every destructive list action that didn't already have one.
  const NEEDS = '[data-admin="rev-no"], [data-admin="rev-brush"], [data-admin="rep-no"], [data-ban="1"]';
  const MSG = { "rev-no": "确定拒绝这条评价？", "rev-brush": "确定判为刷评并拒绝？", "rep-no": "确定驳回这条举报？" };
  window.addEventListener("click", async (ev) => {
    const b = ev.target.closest && ev.target.closest(NEEDS);
    if (!b || b.dataset.confirmed === "1" || !window.yycjConfirm) return;
    ev.preventDefault();
    ev.stopPropagation();
    const msg = MSG[b.getAttribute("data-admin")] || ("确定拉黑用户 " + (b.getAttribute("data-id") || "") + "？");
    if (!(await window.yycjConfirm(msg))) return;
    b.dataset.confirmed = "1";
    b.click();
    setTimeout(() => { delete b.dataset.confirmed; }, 500);
  }, true);
  function moveBadge(card) {
    const diff = card.querySelector(":scope > .edit-diff");
    if (!diff) return;
    let head = card.querySelector(":scope > .card-head");
    if (!head) {
      const idLine = card.firstElementChild;
      if (!idLine || idLine === diff) return;
      head = document.createElement("div");
      head.className = "card-head";
      idLine.before(head);
      head.appendChild(idLine);
    }
    head.appendChild(diff);
  }
  function postCard(card) {
    if (card.dataset.pp) return;
    const ok = card.querySelector('[data-admin="post-ok"]');
    if (!ok) return;
    card.dataset.pp = "1";
    card.classList.add("post-card");
    // media → compact strip (videos become tiles: tap = full screen)
    const media = [...card.children].filter((n) => n.matches("img, video"));
    if (media.length) {
      const strip = document.createElement("div");
      strip.className = "media-strip";
      media[0].before(strip);
      media.forEach((m) => {
        if (m.tagName === "VIDEO") {
          const tile = document.createElement("button");
          tile.type = "button";
          tile.className = "vtile";
          m.removeAttribute("controls");
          m.preload = "metadata";
          m.muted = true;
          tile.appendChild(m);
          tile.addEventListener("click", () => { m.controls = true; m.muted = false; (m.requestFullscreen || m.webkitEnterFullscreen || (() => {})).call(m); m.play().catch(() => {}); });
          m.addEventListener("fullscreenchange", () => { if (!document.fullscreenElement) { m.pause(); m.controls = false; } });
          strip.appendChild(tile);
        } else strip.appendChild(m);
      });
    }
    // 通过并上轮播 → a toggle; 通过 does the right call
    const pin = card.querySelector('[data-admin="post-pin"]');
    if (pin) {
      pin.style.display = "none";
      pin.classList.add("pp-hidden");
      const t = document.createElement("label");
      t.className = "pin-toggle";
      t.innerHTML = `<span>通过后同时上精选轮播（72 小时）</span><input type="checkbox" class="sw" />`;
      ok.parentNode.before(t);
      ok.textContent = "通过";
      t.querySelector("input").addEventListener("change", (e) => { ok.textContent = e.target.checked ? "通过并上轮播" : "通过"; });
    }
  }
  document.addEventListener("click", (ev) => {
    const ok = ev.target.closest('[data-admin="post-ok"]');
    if (!ok) return;
    const card = ok.closest(".card");
    const sw = card && card.querySelector(".pin-toggle input");
    const pin = card && card.querySelector('[data-admin="post-pin"]');
    if (sw && sw.checked && pin) { ev.preventDefault(); ev.stopPropagation(); pin.click(); }
  }, true);
  function listings() {
    const pane = $("#pane-listings");
    const list = $("#adminListings");
    if (!pane || !list) return;
    [["proxyBox", "代老师上架（免审）"], ["editBox", "代修改资料（免审）"]].forEach(([id, label]) => {
      const box = document.getElementById(id);
      if (!box || box.parentNode.matches("details.fold")) return;
      const d = document.createElement("details");
      d.className = "fold";
      d.id = id + "Fold";
      d.innerHTML = `<summary>${label}</summary>`;
      pane.appendChild(d);
      d.appendChild(box);
    });
  }
  document.addEventListener("click", (ev) => {
    if (!ev.target.closest("[data-fill-proxy]")) return;
    setTimeout(() => { const d = $("#editBoxFold"); if (d) { d.open = true; d.scrollIntoView({ block: "start", behavior: "smooth" }); } }, 60);
  }, true);
  // Settings: reuse the existing #adSaveBar (admin-ux.js) but keep it on screen for the whole 站点设置 pane,
  // with a clear state line; the in-page "保存全部设置" at the very bottom is no longer needed.
  function venue() {
    if ($("#ccGlobal")) return;
    const bar = $("#adSaveBar");
    const save = $("#btnOpsSave");
    if (!bar || !save) return;
    const label = bar.querySelector("span");
    const dirty = document.body.classList.contains("ops-dirty");
    const want = dirty ? "有未保存的修改" : "设置已是最新";
    if (label && label.textContent !== want) label.textContent = want;
    bar.classList.toggle("is-dirty", dirty);
    const now = $("#adSaveNow");
    if (now && now.textContent !== "保存全部设置") now.textContent = "保存全部设置";
    save.classList.add("pp-hidden");
    const sync = $("#btnSyncBot");
    const contact = $("#opsWelcome")?.closest(".ops-card");
    if (sync && contact && sync.parentNode !== contact) { sync.classList.add("block"); contact.appendChild(sync); }
  }
  function syncPane() {
    const act = $(".admin-pane.active");
    document.body.classList.toggle("pane-venue", !!act && act.id === "pane-venue");
  }
  function decorate() {
    $$("#adminPosts > .card").forEach((c) => { postCard(c); moveBadge(c); });
    $$(".admin-pane .ops-card > .row").forEach(actRow);
    $$(".pick-item").forEach((it) => {
      if (!it.classList.contains("pick-add") && it.querySelector(":scope > .hrs")) it.classList.add("pick-add");
    });
    lists();
    listings();
    venue();
    syncPane();
  }
  let queued = false;
  const root = document.getElementById("console") || document.body;
  new MutationObserver(() => {
    if (queued) return;
    queued = true;
    setTimeout(() => { queued = false; decorate(); }, 0);
  }).observe(root, { childList: true, subtree: true, attributes: true, attributeFilter: ["class"] });
  new MutationObserver(() => venue()).observe(document.body, { attributes: true, attributeFilter: ["class"] });
  decorate();
})();
