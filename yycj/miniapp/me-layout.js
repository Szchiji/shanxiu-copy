// 「我的」页分组（墨绿金卡片组）：按身份分区，长卡片折叠成一行 + 右侧摘要，点开再看详情。
// 只移动 / 包装各模块已有的卡片（常用位置、优惠券、上架、昵称…），不改它们自己的逻辑。
(() => {
  if (window.__yycjMeLayout) return;
  window.__yycjMeLayout = true;
  const $ = (s) => document.querySelector(s);
  const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const token = () => localStorage.getItem("yycj_token") || "";
  function role() { try { return JSON.parse(localStorage.getItem("yycj_user") || "{}").role || "guest"; } catch (e) { return "guest"; } }
  const isTeacher = () => role() === "teacher" || role() === "merchant";
  const ROLE = { guest: "客人", teacher: "老师", merchant: "商家" };

  // section → ordered item ids (cards get an id below; rows are built here)
  const LAYOUT = {
    guest: [
      ["我的", ["meTodos", "meRowFav", "myClaims", "meRevCard"]],
      ["本城", ["meRowCommunity", "meRowRanking", "cityCoupons"]],
      ["设置", ["placeCard", "meNickCard"]],
      ["帮助", ["meRowTut", "meRowContact", "meRowAdmin"]],
    ],
    teacher: [
      ["本城", ["meRowCommunity", "meRowRanking"]],
      ["经营", ["meTodos", "meListCard", "myCoupon"]],
      ["设置", ["placeCard", "meNickCard", "meRevCard"]],
      ["帮助", ["meRowTut", "meRowContact", "meRowAdmin"]],
    ],
  };
  const FOLD = { myClaims: 1, cityCoupons: 1, placeCard: 1, meNickCard: 1, meRevCard: 1, myCoupon: 1, meListCard: 1 };
  const OPEN_DEFAULT = { meListCard: 1 };
  function isOpen(id) { try { const v = sessionStorage.getItem("yycj_me_open_" + id); return v == null ? !!OPEN_DEFAULT[id] : v === "1"; } catch (e) { return !!OPEN_DEFAULT[id]; } }
  function setOpen(id, on) { try { sessionStorage.setItem("yycj_me_open_" + id, on ? "1" : "0"); } catch (e) {} }

  let revs = null, revLoading = false;
  async function loadRevs() {
    if (revLoading || !token()) return; revLoading = true;
    try { const r = await fetch("/api/reviews/mine", { headers: { Authorization: "Bearer " + token() } }); if (r.ok) revs = (await r.json()).items || []; } catch (e) {}
    revLoading = false; paintRevs(); summarize();
  }
  const REV_ST = { pending: "审核中", approved: "已展示", rejected: "未通过", brush: "未通过" };
  function paintRevs() {
    const box = $("#myReviews"); if (!box || revs == null) return;
    box.classList.remove("hidden");
    box.innerHTML = revs.length ? "<ul class='me-revs'>" + revs.slice(0, 20).map((r) => `<li data-cpn-open="${esc(r.lamp_id || "")}">
      <span class="me-stars">${"★".repeat(Math.max(0, Math.min(5, +r.stars || 0)))}</span><span class="me-rt">${esc(String(r.text || r.summary || "（无文字）").slice(0, 40))}</span>
      <em class="st-${esc(r.status || "")}">${esc(REV_ST[r.status] || r.status || "")}</em></li>`).join("") + "</ul>"
      : "<p class='muted'>还没有写过评价。约课后在详情页点「评价」。</p>";
  }

  function tagCards(me) {
    const pick = (sel) => { const e = me.querySelector(sel); return e && e.closest(".card"); };
    const set = (el, id) => { if (el && !el.id) el.id = id; };
    set(pick("#myListings"), "meListCard");
    set(pick("#guestNick"), "meNickCard");
    const rv = $("#myReviews"); const rc = rv && rv.closest(".card"); if (rc && rc.querySelector("h3")) set(rc, "meRevCard");
    const nick = $("#meNickCard h3"); if (nick && nick.textContent.trim() !== "昵称与身份") nick.textContent = "昵称与身份";
    const hints = $("#adminEntry"); const hc = hints && hints.closest(".card"); if (hc) hc.classList.add("me-hide");
    const tut = $("#meTutorial"); if (tut) tut.classList.add("me-hide");
    const mc = $("#meCard"); if (mc) mc.classList.add("me-hide");
  }
  function row(id, icon, label, attrs) {
    let r = document.getElementById(id);
    if (!r) {
      r = document.createElement("button"); r.type = "button"; r.id = id; r.className = "me-row";
      r.innerHTML = `<b>${esc(label)}</b><span class="me-sum"></span><i class="me-chev">›</i>`;
      Object.entries(attrs || {}).forEach(([k, v]) => r.setAttribute(k, v));
    }
    return r;
  }
  function rows() {
    const home = window.__yycjHomeData || {};
    const ct = home.contacts || {};
    const out = {
      meRowCommunity: row("meRowCommunity", "◈", "城市社区", {"data-hub":"community"}),
      meRowRanking: row("meRowRanking", "☆", "本城评价榜", {"data-hub":"ranking"}),
      meRowFav: !isTeacher() ? row("meRowFav", "♡", "我的收藏", { "data-me-fav": "1" }) : null,
      meRowTut: row("meRowTut", "?", "使用教程", { "data-tutorial": "1" }),
      meRowContact: ct.show_admin ? row("meRowContact", "✉", "联系" + (ct.admin_label || "管理员"), { "data-me-contact": "1" }) : null,
      meRowAdmin: $("#adminEntry") && !$("#adminEntry").classList.contains("hidden") ? row("meRowAdmin", "⚙", "管理后台", { "data-me-admin": "1" }) : null,
    };
    return out;
  }
  let todos = null, todoLoading = false;
  async function loadTodos() {
    if (todoLoading || !token()) return;
    todoLoading = true;
    try {
      const r = await fetch("/api/me/todos", { headers: { Authorization: "Bearer " + token() } });
      if (r.ok) todos = await r.json();
    } catch (e) {}
    todoLoading = false;
    paintTodos();
  }
  function openFold(id, also) {
    const c = document.getElementById(id);
    if (c) { c.classList.add("open"); setOpen(c.id, true); c.scrollIntoView({ block: "center", behavior: "smooth" }); }
    if (also) also();
  }
  function paintTodos() {
    const me = $("#view-me"); if (!me) return;
    let el = $("#meTodos");
    if (!el) {
      el = document.createElement("div");
      el.id = "meTodos";
      el.className = "card me-todos";
      el.innerHTML = "<h3>待办</h3><div class=\"me-todo-row\"></div>";
    }
    const d = todos || {};
    const cells = isTeacher()
      ? [["expiring", d.expiring || 0, "3 天内到期"], ["pending", d.pending || 0, "待核销优惠"]]
      : [["coupons", d.coupons || 0, "可用优惠券"], ["favs", d.favs || 0, "收藏"]];
    const hint = isTeacher()
      ? "快到期会打开「我的上架」；待核销打开「我的优惠」。续期仍由管理员处理。"
      : "点一下就打开原来的优惠券或收藏。约课是在详情页点「想聊聊」，没有单独的课表，所以这里不列待上课。";
    const row = el.querySelector(".me-todo-row") || el;
    if (!el.querySelector(".me-todo-row")) el.innerHTML = "<h3>待办</h3><div class=\"me-todo-row\"></div><p class=\"muted me-todo-hint\"></p>";
    let hintEl = el.querySelector(".me-todo-hint");
    if (!hintEl) { hintEl = document.createElement("p"); hintEl.className = "muted me-todo-hint"; el.appendChild(hintEl); }
    hintEl.textContent = hint;
    const box = el.querySelector(".me-todo-row");
    box.innerHTML = cells.map(([k, n, lab]) => `<button type="button" class="me-todo" data-todo="${k}"><b>${esc(n)}</b><span class="muted">${esc(lab)}</span></button>`).join("");
  }
  function head(me) {
    let h = $("#meHead");
    if (!h) { h = document.createElement("div"); h.id = "meHead"; h.className = "card me-head"; me.prepend(h); }
    const nick = (localStorage.getItem("yycj_guest_nick") || "").trim();
    const r = role();
    const name = nick || (ROLE[r] || "我");
    const city = localStorage.getItem("yycj_city") || "";
    const html = `<div class="me-av">${esc(name.slice(0, 1))}</div><div class="me-hn"><b>${esc(name)}</b>
      <div><span class="me-pill">${esc(ROLE[r] || "客人")}</span>${city ? `<span class="me-city">📍 ${esc(city)}</span>` : ""}${nick ? "" : `<button type="button" class="me-link" data-me-open="meNickCard">设昵称</button>`}</div></div>`;
    if (h.dataset.k !== html) { h.innerHTML = html; h.dataset.k = html; }
    if (me.firstElementChild !== h) me.prepend(h);
  }
  let busy = false;
  function arrange() {
    const me = $("#view-me"); if (!me || busy) return;
    busy = true;
    try {
      tagCards(me);
      head(me);
      const r = isTeacher() ? "teacher" : "guest";
      const rw = rows();
      let after = $("#meHead");
      LAYOUT[r].forEach(([title, ids], i) => {
        const key = r + i;
        let sec = me.querySelector(`.me-sec[data-k="${key}"]`);
        if (!sec) { sec = document.createElement("div"); sec.className = "me-sec"; sec.dataset.k = key; sec.innerHTML = `<h5>${esc(title)}</h5><div class="me-grp"></div>`; }
        if (after.nextElementSibling !== sec) after.after(sec);
        after = sec;
        const grp = sec.querySelector(".me-grp");
        let prev = null;
        ids.forEach((id) => {
          const el = rw[id] !== undefined ? rw[id] : document.getElementById(id);
          if (!el) { const stale = document.getElementById(id); if (stale && rw[id] === null) stale.remove(); return; }
          if (FOLD[id] && !el.classList.contains("me-fold")) { el.classList.add("me-fold"); el.classList.toggle("open", isOpen(id)); }
          if (prev ? prev.nextElementSibling !== el : grp.firstElementChild !== el) { if (prev) prev.after(el); else grp.prepend(el); }
          prev = el;
        });
        sec.classList.toggle("hidden", !grp.children.length);
      });
      // sections of the other role (after a 身份 switch)
      me.querySelectorAll(".me-sec").forEach((s) => { if (!s.dataset.k.startsWith(r)) { s.querySelectorAll(".card").forEach((c) => me.appendChild(c)); s.remove(); } });
      if (revs == null) loadRevs(); else paintRevs();
      paintTodos();
      if (todos == null) loadTodos();
      summarize();
    } finally { setTimeout(() => { busy = false; }, 0); }
  }
  function sum(id, text) { const e = document.getElementById(id); if (!e) return; if (e.classList.contains("me-row")) { const s = e.querySelector(".me-sum"); if (s && s.textContent !== text) s.textContent = text; } else if (e.dataset.sum !== text) e.dataset.sum = text; }
  function summarize() {
    const fc = window.__yycjFavCount ? window.__yycjFavCount() : null;
    sum("meRowFav", fc ? fc + " 个" : fc === 0 ? "在详情页点「收藏」" : "");
    const mc = $("#myClaims");
    if (mc) { const n = mc.querySelectorAll(".cpn-copy").length; const all = mc.querySelectorAll("li").length; sum("myClaims", n ? n + " 张可用" : all ? "暂无可用" : "还没有"); }
    const cc = $("#cityCoupons"); if (cc) { const n = cc.querySelectorAll("li").length; sum("cityCoupons", n ? n + " 个优惠" : "暂无"); }
    const pc = $("#placeCard"); if (pc) { const st = (pc.querySelector(".place-st") || {}).textContent || ""; sum("placeCard", /还没设置/.test(st) ? "未设置" : pc.dataset.place || "已设置"); }
    const nk = (localStorage.getItem("yycj_guest_nick") || "").trim(); sum("meNickCard", (nk ? nk + " · " : "") + (ROLE[role()] || "客人"));
    if (revs != null) sum("meRevCard", revs.length ? revs.length + " 条" + (revs.some((x) => x.status === "pending") ? " · 有审核中" : "") : "还没有");
    const cp = $("#myCoupon");
    if (cp) { const live = cp.querySelector("[data-cpn-end]"); const sm = cp.querySelector(".cpn-block.mine small"); sum("myCoupon", live && sm ? "进行中 · " + (sm.textContent.split("·").pop() || "").trim() : "未发优惠"); }
    const lc = $("#meListCard"); if (lc) { const n = lc.querySelectorAll("[data-edit]").length; sum("meListCard", n ? n + " 条上架" : ""); }
    const ct = (window.__yycjHomeData || {}).contacts || {};
    sum("meRowContact", ""); sum("meRowTut", isTeacher() ? "老师版" : "客人版");
  }
  let t = null;
  const soon = () => { clearTimeout(t); t = setTimeout(arrange, 60); };
  function watch() {
    const me = $("#view-me"); if (!me) return setTimeout(watch, 300);
    new MutationObserver(soon).observe(me, { childList: true });
    new MutationObserver(() => { clearTimeout(t); t = setTimeout(summarize, 80); }).observe(me, { childList: true, subtree: true, characterData: true });
    arrange();
  }
  document.addEventListener("yycj:fav", () => summarize());
  document.addEventListener("click", (ev) => {
    const tg = ev.target;
    if (tg.closest && tg.closest("[data-nav='me']")) { setTimeout(arrange, 120); setTimeout(arrange, 900); revs = null; todos = null; }
    const h3 = tg.closest && tg.closest(".me-fold > h3");
    if (h3 && !tg.closest("button,a,input,select")) { const c = h3.parentElement; const on = !c.classList.contains("open"); c.classList.toggle("open", on); setOpen(c.id, on); return; }
    const op = tg.closest && tg.closest("[data-me-open]");
    if (op) { const c = document.getElementById(op.getAttribute("data-me-open")); if (c) { c.classList.add("open"); setOpen(c.id, true); c.scrollIntoView({ block: "center", behavior: "smooth" }); const i = c.querySelector("input"); if (i) setTimeout(() => i.focus(), 300); } return; }
    const td = tg.closest && tg.closest("[data-todo]");
    if (td) {
      const k = td.getAttribute("data-todo");
      if (k === "favs") { const b = $("nav [data-nav='fav']"); if (b) b.click(); }
      else if (k === "coupons") openFold("myClaims");
      else if (k === "expiring") openFold("meListCard");
      else if (k === "pending") openFold("myCoupon");
      return;
    }
    if (tg.closest && tg.closest("[data-me-fav]")) { const b = $("nav [data-nav='fav']"); if (b) b.click(); return; }
    if (tg.closest && tg.closest("[data-me-contact]")) { window.__yycjContactAdmin?.(); return; }
    if (tg.closest && tg.closest("[data-me-admin]")) { const a = $("#adminEntry a"); if (a) location.href = a.href; return; }
    if (tg.closest && tg.closest("[data-switch],#btnSaveNick")) setTimeout(arrange, 400);
  });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", watch); else watch();
})();
