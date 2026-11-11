// 优惠券 (inspired by 繁花's 优惠): 老师在「我的 · 我的优惠」发限时优惠（一批最多 5 项，各自有数量，共用有效期）；
// 客人在详情页选其中一项「领取」（每批每人限领 1 张），拿到 6 位码，到店出示。
// Where guests see coupons is a site setting (coupon_display): badge = 卡片称呼前小「惠」字,
// filter = 筛选面板「有优惠」, list = 「我的 · 本城优惠」. The detail-page block is always on. Card size/grid unchanged.
(() => {
  if (window.__yycjCoupons) return;
  window.__yycjCoupons = true;
  const $ = (s) => document.querySelector(s);
  const token = () => localStorage.getItem("yycj_token") || "";
  const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  function role() { try { return JSON.parse(localStorage.getItem("yycj_user") || "{}").role || localStorage.getItem("yycj_role") || "guest"; } catch (e) { return "guest"; } }
  const isTeacher = () => role() === "teacher" || role() === "merchant";
  function display() { const d = (window.__yycjHomeData || {}).coupon_display; return d == null ? "badge,filter,list" : String(d); }
  window.__yycjCouponMode = (k) => display().split(",").includes(k);
  function toast(msg) { const el = $("#toast"); if (!el) return; el.textContent = msg; el.classList.remove("hidden", "err"); clearTimeout(toast.t); toast.t = setTimeout(() => el.classList.add("hidden"), 2600); }
  async function api(path, opt = {}) {
    const r = await fetch(path, Object.assign({}, opt, { headers: { "Content-Type": "application/json", Authorization: "Bearer " + token() } }));
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || "请求失败");
    return d;
  }
  // 约课用优惠: the guest sends the code to the teacher when booking
  const bookText = (code, text) => `你好，我想约课，使用优惠券：${code}（${text}）`;
  // all codes the guest holds for this teacher: 「A（100P）、B（200PP）」
  const bookTextAll = (list) => "你好，我想约课，使用优惠券：" + list.map((h) => `${h.code}（${h.text}）`).join("、");
  function copy(text) {
    try { if (navigator.clipboard && navigator.clipboard.writeText) { navigator.clipboard.writeText(text).catch(() => legacyCopy(text)); return true; } } catch (e) {}
    return legacyCopy(text);
  }
  function legacyCopy(text) {
    try { const t = document.createElement("textarea"); t.value = text; t.setAttribute("readonly", ""); t.style.cssText = "position:fixed;left:-9999px;top:0";
      document.body.appendChild(t); t.select(); const ok = document.execCommand("copy"); t.remove(); return ok; } catch (e) { return false; }
  }
  let held = null;   // {lamp, codes:[{code, text}]} — unused, valid codes for the open listing
  // contact links that are a plain t.me/<username> accept ?text= — prefill the booking line there
  function prefillLinks() {
    const box = $("#detail"); if (!box) return;
    box.querySelectorAll("a.tg-link").forEach((a) => {
      const raw = a.getAttribute("data-orig") || a.getAttribute("href") || "";
      if (!a.getAttribute("data-orig")) a.setAttribute("data-orig", raw);
      const m = /^https:\/\/t\.me\/([A-Za-z][A-Za-z0-9_]{3,31})\/?$/.exec(raw);
      const url = held && m && !/bot$/i.test(m[1]) ? raw.replace(/\/$/, "") + "?text=" + encodeURIComponent(bookTextAll(held.codes)) : raw;
      a.setAttribute("href", url); if (a.hasAttribute("data-tg")) a.setAttribute("data-tg", url);
    });
  }
  const quotaTxt = (c) => (c.n_items || 1) > 1 ? `${c.n_items}项 · ${c.remain != null ? "剩" + c.remain + "张" : "不限量"}` : c.quota ? `限${c.quota}张 · 剩${c.remain}张` : "不限量";
  const itemsOf = (c) => (c && c.items && c.items.length ? c.items : c ? [c] : []);
  const remainTxt = (x) => x.status === "sold_out" ? "已领完" : x.quota ? `剩${x.remain}张` : "不限量";
  const STATUS = { expired: "已结束", sold_out: "已领完", ended: "已结束", stopped: "已停用" };

  // ---- detail page block ----
  async function paintDetail() {
    const box = $("#detail"); if (!box) return;
    const id = box.getAttribute("data-lamp") || ""; if (!id) return;
    // 优惠条放在「评价」正上方：#detail 里资料卡片之后、#yycjReviews 之前（评价后加载时也在它前面）
    const card = box.querySelector(":scope > .card") || box.querySelector(".card") || box;
    let el = box.querySelector(".cpn-block");
    if (el && el.dataset.lamp === id && el.dataset.ok) { placeBlock(box, card, el); return; }
    let c = null;
    try { c = (await api("/api/lamps/" + encodeURIComponent(id) + "/coupon")).coupon; } catch (e) { return; }
    if ((box.getAttribute("data-lamp") || "") !== id) return;
    el = box.querySelector(".cpn-block");
    if (!c) { held = null; prefillLinks(); if (el) el.remove(); return; }
    if (!el) {
      el = document.createElement("div"); el.className = "cpn-block";
    }
    placeBlock(box, card, el);
    el.dataset.lamp = id; el.dataset.ok = "1";
    const its = itemsOf(c);
    const codes = c.own ? [] : its.filter((x) => x.my_code && x.my_valid).map((x) => ({ code: x.my_code, text: x.text }));
    held = codes.length ? { lamp: id, codes } : null;
    prefillLinks();
    // 每批每人限领 1 张：领过一项后，其他项的「领取」变灰
    const got = its.some((x) => x.my_code);
    const act = (x) => c.own ? ""
      : x.my_code ? `<div class="cpn-code"><b>${esc(x.my_code)}</b>${x.my_used ? "<i>已用</i>" : (x.my_valid ? `<button type="button" class="cpn-copy" data-cpn-copy="${esc(x.my_code)}" data-cpn-t="${esc(x.text)}">复制券码</button>` : "<i>已失效</i>")}</div>`
      : got && x.live ? `<button type="button" class="btn cpn-claim" disabled>领取</button>`
      : x.live ? `<button type="button" class="btn primary cpn-claim" data-cpn-claim="${esc(x.coupon_id)}">领取</button>`
      : `<span class="cpn-off">${esc(STATUS[x.status] || "已结束")}</span>`;
    const note = c.own ? "" : got ? (its.length > 1 ? "本批已领 · 每人限领1张" : "") : its.length > 1 ? "每人限领1张，选一种领取" : "";
    el.classList.add("multi");
    el.innerHTML = `<div class="cpn-l"><span class="cpn-tag">惠</span><div><b>限时优惠${its.length > 1 ? ` · ${its.length} 项` : ""}</b><small>${esc(c.left)}${c.own ? " · 你发的优惠" : ""}</small></div></div>
      ${note ? `<p class="cpn-note${got ? " got" : ""}">${esc(note)}</p>` : ""}
      <ul class="cpn-its">${its.map((x) => `<li class="${x.my_code ? "mine" : x.live && !got ? "" : x.live ? "dim" : "off"}"><div><b>${esc(x.text)}</b><small>${esc(remainTxt(x))}</small></div>${act(x)}</li>`).join("")}</ul>
      ${codes.length ? `<p class="cpn-tip">约课时把券码发给老师（点「${esc(ctaLabel())}」会自动复制好）。有效至优惠结束：${esc(c.left)}。</p>` : ""}`;
  }
  function placeBlock(box, card, el) {
    const rv = box.querySelector(":scope > #yycjReviews");
    if (rv) { if (el.nextElementSibling !== rv) rv.before(el); }
    else if (card !== box) { if (card.nextElementSibling !== el) card.after(el); }
    else if (!el.parentNode) box.appendChild(el);
  }
  function ctaLabel() { return String((window.__yycjHome || {}).chat_cta_label || "想聊聊").slice(0, 12); }
  const watchDetail = () => {
    const box = $("#detail"); if (!box || !window.MutationObserver) return;
    let t = 0;
    new MutationObserver(() => { clearTimeout(t); t = setTimeout(() => { if (held && held.lamp !== box.getAttribute("data-lamp")) held = null; paintDetail(); prefillLinks(); }, 120); }).observe(box, { childList: true, subtree: true, attributes: true, attributeFilter: ["data-lamp"] });
  };

  // ---- 我的 ----
  function ensureCard(id, after) {
    const me = $("#view-me"); if (!me) return null;
    let card = document.getElementById(id);
    if (!card) {
      card = document.createElement("div"); card.id = id; card.className = "card cpn-card";
      const a = after && document.querySelector(after);
      if (a) a.after(card); else { const f = me.querySelector("#meCard"); if (f) f.after(card); else me.prepend(card); }
    }
    return card;
  }
  // 发布表单：先选有效期（整批共用），再一项一项加（内容 + 数量），「继续添加」或「发布优惠」
  let draft = { days: 2, items: [], text: "", qty: "", edit: -1 };
  const MAX_ITEMS_DEF = 5;
  // 折叠状态（已核销 / 往期优惠 / 已用·已失效），重画时保持
  const openFolds = new Set();
  const fold = (key, label, n, body) => !n ? "" : `<div class="cpn-fold${openFolds.has(key) ? " open" : ""}" data-cpn-fold="${esc(key)}">
    <button type="button" class="cpn-fold-h" data-cpn-fold-t="${esc(key)}"><span>${label}</span><b>${n}</b><i>›</i></button><div class="cpn-fold-b">${body}</div></div>`;
  const claimRow = (x) => `<li class="${x.used ? "done" : ""}"><b>${esc(x.code)}</b><span>${esc(x.who)}${x.state === "void" ? " · 已失效" : ""}</span>
    ${x.used ? (x.can_undo === false ? "<em class='cpn-done'>已用</em>" : `<button type="button" class="btn cpn-use" data-cpn-use="${esc(x.code)}" data-used="0">撤销</button>`)
      : x.state === "usable" ? `<button type="button" class="btn primary cpn-use" data-cpn-use="${esc(x.code)}" data-used="1">标记已用</button>` : ""}</li>`;
  // one item of a batch: header, 待使用 list, 已核销 fold (key per item)
  function itemHtml(it, claims, foldKey, emptyTxt) {
    const act = claims.filter((x) => x.state === "usable"), used = claims.filter((x) => x.used);
    const cnt = it.quota ? `已领 ${it.claimed}/${it.quota}` + (it.claimed >= it.quota ? " · 已领完" : ` · 剩 ${it.quota - it.claimed} 张`) : `已领 ${it.claimed}`;
    return `<div class="cpn-item" data-cpn-item="${esc(it.coupon_id)}"><div class="cpn-item-h"><b>${esc(it.text)}</b><small>${esc(cnt)}${used.length ? " · 核销 " + used.length : ""}</small></div>
      ${act.length ? `<ul class="cpn-claims">${act.map(claimRow).join("")}</ul>` : emptyTxt ? `<p class="muted cpn-empty">${emptyTxt(claims)}</p>` : ""}
      ${fold(foldKey + it.coupon_id, "已核销", used.length, `<ul class="cpn-claims">${used.map(claimRow).join("")}</ul>`)}</div>`;
  }
  const claimsOf = (all, cid) => all.filter((x) => (x.coupon_id || "") === cid);
  function historyHtml(d) {
    const h = d.history || [];
    const body = h.map((b) => {
      const its = b.items && b.items.length ? b.items : [{ coupon_id: b.coupon_id, text: b.text, quota: 0, claimed: b.claimed, claims: b.claims || [] }];
      return `<div class="cpn-hist"><div class="cpn-hist-h"><b>${its.length > 1 ? its.length + " 项优惠" : esc(b.text)}</b><small>${esc(b.status)} · 领 ${b.claimed} · 核销 ${b.used}${b.pending ? " · 待使用 " + b.pending : ""}</small></div>
        ${its.map((it) => itemHtml(it, it.claims || [], "hu_", null)).join("")}</div>`;
    }).join("");
    return fold("hist", `往期优惠 <small>近 ${d.history_days || 30} 天</small>`, h.length, body);
  }
  function formHtml(d) {
    const max = d.max_items || MAX_ITEMS_DEF;
    const n = draft.items.length, editing = draft.edit >= 0;
    const showEditor = editing || n < max;
    const list = n ? `<ul class="cpn-items">${draft.items.map((x, i) => `<li class="${draft.edit === i ? "on" : ""}"><i>${i + 1}</i><b>${esc(x.text)}</b><span>${x.quota} 张</span>
      <button type="button" class="cpn-mini" data-cpn-ied="${i}">编辑</button><button type="button" class="cpn-mini del" data-cpn-idel="${i}">删除</button></li>`).join("")}</ul>` : "";
    const editor = !showEditor ? `<p class="muted cpn-tip">一次最多 ${max} 项，可以直接发布。</p>` : `<div class="cpn-editor">
      <div class="cpn-sub">${editing ? `修改第 ${draft.edit + 1} 项` : `第 ${n + 1} 项`}</div>
      <input id="cpnText" maxlength="${d.max_text}" placeholder="优惠内容，比如 100P" value="${esc(draft.text)}" />
      <div class="cpn-row"><span>数量</span><input id="cpnQty" type="number" min="1" max="${d.max_quota}" inputmode="numeric" placeholder="1–${d.max_quota}" value="${esc(draft.qty)}" /><em>张</em></div></div>`;
    const total = n + (!editing && draft.text.trim() ? 1 : 0);
    return `<p class="muted cpn-lead">一次可以发几项优惠（最多 ${max} 项），每项写好数量；客人每人限领其中 1 张，到店出示券码。同一时间只能有 1 批。</p>
      <div class="cpn-step"><i>1</i><span>选有效期</span><small>整批共用</small></div>
      <div class="cpn-row cpn-days">${d.presets.map((k) => `<button type="button" class="area-chip${draft.days === k ? " on" : ""}" data-cpn-days="${k}">${k}天</button>`).join("")}</div>
      <div class="cpn-step"><i>2</i><span>添加优惠</span><small>已添加 ${n}/${max}</small></div>
      ${list}${editor}
      <div class="cpn-btns">${showEditor ? `<button type="button" class="btn" data-cpn-add="1">${editing ? "保存这一项" : "继续添加"}</button>` : ""}
        <button type="button" class="btn primary" data-cpn-new="1">发布优惠${total > 1 ? `（${total} 项）` : ""}</button></div>
      <p class="muted cpn-tip">发出后立即显示；违规内容后台会停用。</p>`;
  }
  let lastD = null;
  async function paintTeacher(reuse) {
    const card = ensureCard("myCoupon"); if (!card) return;
    let d = reuse && lastD;
    if (!d) { try { d = await api("/api/me/coupon"); } catch (e) { card.innerHTML = "<h3>我的优惠</h3><p class='muted'>加载失败</p>"; return; } }
    lastD = d;
    if (!d.enabled) { card.remove(); return; }
    if (d.current) {
      const c = d.current, its = itemsOf(c), all = d.claims || [];
      const single = its.length === 1;
      const pend = all.filter((x) => x.state === "usable").length, used = all.filter((x) => x.used).length;
      card.innerHTML = `<h3>我的优惠</h3>
        <div class="cpn-block mine"><div class="cpn-l"><span class="cpn-tag">惠</span><div><b>${single ? esc(c.text) : its.length + " 项优惠"}</b><small>${esc(c.left)} · 已领 ${c.claimed}${c.quota ? "/" + c.quota : ""}</small></div></div>
        <button type="button" class="btn" data-cpn-end="1">提前结束</button></div>
        ${findHtml()}
        <div class="cpn-sub">待使用 ${pend} · 已核销 ${used}（客人约课时会把券码发给你）</div>
        ${its.map((it) => itemHtml(it, claimsOf(all, it.coupon_id), "used_", (cl) => cl.length ? "领的券都核销完了" : "还没有人领取")).join("")}
        ${historyHtml(d)}`;
      return;
    }
    if (!d.can_issue) { card.innerHTML = "<h3>我的优惠</h3><p class='muted'>资料上架后，可以在这里发限时优惠，比如「100P 2张、200PP 3张」。</p>"; return; }
    card.innerHTML = `<h3>我的优惠</h3>${d.has_claims ? findHtml() : ""}${formHtml(d)}${historyHtml(d)}`;
  }
  // editor → {text, quota}; "" when empty; throws a message when invalid (mirrors the server rules)
  function readEditor(maxText, maxQ) {
    const text = String(($("#cpnText") || {}).value || draft.text || "").split(/\s+/).filter(Boolean).join(" ");
    const raw = String(($("#cpnQty") || {}).value || draft.qty || "").trim();
    if (!text && !raw) return "";
    if (!text) throw new Error("请写优惠内容，比如「100P」");
    if (text.length > maxText) throw new Error(`优惠内容最多 ${maxText} 个字`);
    if (/http|t\.me|@/i.test(text)) throw new Error("优惠内容里不能放链接或 @账号");
    const q = /^\d+$/.test(raw) ? parseInt(raw, 10) : 0;
    if (q < 1 || q > maxQ) throw new Error(`数量填 1–${maxQ} 张`);
    const dup = draft.items.findIndex((x, i) => i !== draft.edit && x.text.toLowerCase() === text.toLowerCase());
    if (dup >= 0) throw new Error(`和第 ${dup + 1} 项重复了`);
    return { text, quota: q };
  }
  const syncEditor = () => { const t = $("#cpnText"), q = $("#cpnQty"); if (t) draft.text = t.value; if (q) draft.qty = q.value; };
  const limits = () => [(lastD && lastD.max_text) || 40, (lastD && lastD.max_quota) || 200, (lastD && lastD.max_items) || MAX_ITEMS_DEF];
  let found = null;
  function findHtml() {
    const r = found;
    const res = !r ? "" : r.err ? `<p class="cpn-find-r muted">${esc(r.err)}</p>`
      : `<div class="cpn-find-r"><b>${esc(r.code)}</b><span>${esc(r.who)} · ${esc(r.text)} · ${r.used ? "已用" : r.valid ? "有效" : "已失效"}</span>
        ${r.valid || r.used ? `<button type="button" class="btn${r.used ? "" : " primary"}" data-cpn-use="${esc(r.code)}" data-used="${r.used ? 0 : 1}">${r.used ? "撤销" : "标记已用"}</button>` : ""}</div>`;
    return `<div class="cpn-find"><input id="cpnFind" maxlength="12" placeholder="输入客人发来的券码" value="${esc((r && r.q) || "")}" autocapitalize="characters" /><button type="button" class="btn" data-cpn-find="1">核对</button></div>${res}`;
  }
  async function paintGuest() {
    let d = { items: [] };
    try { d = await api("/api/me/claims"); } catch (e) {}
    const items = d.items || [];
    const card = ensureCard("myClaims");
    if (!card) return;
    const li = (c) => `<li data-cpn-open="${esc(c.lamp_id)}" class="${c.state === "usable" ? "" : "done"}">
      <div><b>${esc(c.title)}</b><span>${esc(c.text)}</span><small>${c.state === "used" ? "已用" : c.state === "usable" ? "有效 · " + esc(c.left) : "已失效"}</small></div>
      <div class="cpn-mc"><code class="${c.state === "usable" ? "" : "used"}">${esc(c.my_code)}</code>${c.state === "usable" ? `<button type="button" class="cpn-copy" data-cpn-copy="${esc(c.my_code)}" data-cpn-t="${esc(c.text)}">复制券码</button>` : ""}</div></li>`;
    const use = items.filter((c) => c.state === "usable"), done = items.filter((c) => c.state !== "usable");
    const days = d.hide_days || 7;
    if (!items.length && !d.hidden) { card.innerHTML = "<h3>我的优惠券</h3><p class='muted'>在老师详情页领取的优惠会在这里，约课时把券码发给老师即可。</p>"; return; }
    card.innerHTML = "<h3>我的优惠券</h3>" +
      (use.length ? "<ul class='cpn-mine'>" + use.map(li).join("") + "</ul>" : "<p class='muted'>暂时没有可用的券。</p>") +
      fold("gdone", "已用 / 已失效", done.length, "<ul class='cpn-mine'>" + done.map(li).join("") + "</ul>" +
        `<p class='muted cpn-tip'>已用、已失效的券 ${days} 天后自动不再显示。</p>`) +
      (use.length ? "<p class='muted cpn-tip'>约课时把券码发给老师，老师在「我的优惠」里核销。券码在优惠结束时间前有效。</p>" : "");
  }
  async function paintCityList() {
    const card = ensureCard("cityCoupons", "#myClaims");
    if (!card) return;
    if (!window.__yycjCouponMode("list")) { card.remove(); return; }
    const city = localStorage.getItem("yycj_city") || "";
    let items = [];
    try { items = (await api("/api/coupons?city=" + encodeURIComponent(city))).items || []; } catch (e) {}
    card.innerHTML = `<h3>本城优惠 <small class="muted">${esc(city)}</small></h3>` + (items.length ? "<ul class='cpn-list'>" + items.map((c) => `<li data-cpn-open="${esc(c.lamp_id)}">
      <span class="cpn-tag">惠</span><div><b>${esc(c.title)}<i>${esc(c.district)}</i></b><span>${esc(c.text)}</span><small>${esc(c.left)} · ${esc(quotaTxt(c))}</small></div><em>›</em></li>`).join("") + "</ul>"
      : "<p class='muted'>这个城市暂时没有优惠</p>");
  }
  async function paintMe() {
    if (isTeacher()) { paintTeacher(); const g = $("#myClaims"); if (g) g.remove(); const l = $("#cityCoupons"); if (l) l.remove(); }
    else { const t = $("#myCoupon"); if (t) t.remove(); await paintGuest(); paintCityList(); }
  }
  function openLamp(id) {
    if (window.__yycjOpenLampById) { window.__yycjOpenLampById(id); return; }
    const fake = document.createElement("div"); fake.setAttribute("data-id", id); fake.style.display = "none";
    const feed = $("#feed"); if (!feed) return;
    feed.appendChild(fake); fake.click(); fake.remove();
  }

  document.addEventListener("click", async (ev) => {
    const t = ev.target;
    const ft = t.closest && t.closest("[data-cpn-fold-t]");
    if (ft) { const k = ft.getAttribute("data-cpn-fold-t"); const box = ft.parentElement; const on = !box.classList.contains("open");
      box.classList.toggle("open", on); if (on) openFolds.add(k); else openFolds.delete(k); return; }
    const cl = t.closest && t.closest("[data-cpn-claim]");
    if (cl) {
      cl.disabled = true;
      try { await api("/api/coupons/" + encodeURIComponent(cl.getAttribute("data-cpn-claim")) + "/claim", { method: "POST" }); toast("领取成功，到店出示券码"); const b = $("#detail .cpn-block"); if (b) delete b.dataset.ok; paintDetail(); }
      catch (e) { toast(e.message); cl.disabled = false; }
      return;
    }
    const dy = t.closest && t.closest("[data-cpn-days]");
    if (dy) { syncEditor(); draft.days = +dy.getAttribute("data-cpn-days"); paintTeacher(true); return; }
    if (t.closest && t.closest("[data-cpn-add]")) {
      const [mt, mq, mi] = limits();
      let it;
      try { it = readEditor(mt, mq); } catch (e) { syncEditor(); return toast(e.message); }
      if (!it) { syncEditor(); return toast("先写好这一项的内容和数量"); }
      if (draft.edit >= 0) draft.items[draft.edit] = it;
      else if (draft.items.length >= mi) return toast(`一次最多 ${mi} 项`);
      else draft.items.push(it);
      draft.text = ""; draft.qty = ""; draft.edit = -1;
      paintTeacher(true); setTimeout(() => { const i = $("#cpnText"); if (i) i.focus(); }, 30);
      return;
    }
    const ied = t.closest && t.closest("[data-cpn-ied]");
    if (ied) {
      syncEditor();
      if (draft.edit < 0 && (draft.text.trim() || String(draft.qty).trim())) return toast("先「继续添加」或清空正在写的这一项");
      const i = +ied.getAttribute("data-cpn-ied"), x = draft.items[i]; if (!x) return;
      draft.edit = i; draft.text = x.text; draft.qty = String(x.quota); paintTeacher(true); return;
    }
    const idel = t.closest && t.closest("[data-cpn-idel]");
    if (idel) {
      syncEditor();
      const i = +idel.getAttribute("data-cpn-idel");
      draft.items.splice(i, 1);
      if (draft.edit === i) { draft.edit = -1; draft.text = ""; draft.qty = ""; } else if (draft.edit > i) draft.edit -= 1;
      paintTeacher(true); return;
    }
    if (t.closest && t.closest("[data-cpn-new]")) {
      const [mt, mq] = limits();
      let it;
      try { it = readEditor(mt, mq); } catch (e) { syncEditor(); return toast(e.message); }
      const items = draft.items.slice();
      if (it) { if (draft.edit >= 0) items[draft.edit] = it; else items.push(it); }
      if (!items.length) { syncEditor(); return toast("先添加一项优惠，写好内容和数量"); }
      try {
        await api("/api/me/coupon", { method: "POST", body: JSON.stringify({ days: draft.days, items }) });
        draft = { days: 2, items: [], text: "", qty: "", edit: -1 }; toast(items.length > 1 ? `已发布 ${items.length} 项优惠` : "优惠已发布"); paintTeacher();
      } catch (e) { syncEditor(); toast(e.message); }
      return;
    }
    if (t.closest && t.closest("[data-cpn-end]")) {
      if (!confirm("提前结束这个优惠？不能再领；已领的券码在原定结束时间前仍然有效。")) return;
      try { await api("/api/me/coupon/end", { method: "POST" }); toast("已结束"); paintTeacher(); } catch (e) { toast(e.message); }
      return;
    }
    const use = t.closest && t.closest("[data-cpn-use]");
    if (use) {
      try {
        const code = use.getAttribute("data-cpn-use");
        await api("/api/me/coupon/use", { method: "POST", body: JSON.stringify({ code, used: use.getAttribute("data-used") === "1" }) });
        if (found && found.code === code) { try { found = Object.assign((await api("/api/me/coupon/code?code=" + encodeURIComponent(code))).claim, { q: found.q }); } catch (e) {} }
        toast(use.getAttribute("data-used") === "1" ? "已核销" : "已撤销核销"); paintTeacher();
      } catch (e) { toast(e.message); }
      return;
    }
    if (t.closest && t.closest("[data-cpn-find]")) {
      const q = (($("#cpnFind") || {}).value || "").trim().toUpperCase();
      if (!q) return toast("请输入券码");
      try { found = Object.assign((await api("/api/me/coupon/code?code=" + encodeURIComponent(q))).claim, { q }); }
      catch (e) { found = { err: e.message, q }; }
      paintTeacher(); return;
    }
    const cp = t.closest && t.closest("[data-cpn-copy]");
    if (cp) { copy(bookText(cp.getAttribute("data-cpn-copy"), cp.getAttribute("data-cpn-t") || "")); toast("已复制券码，发给老师即可"); return; }
    // 想聊聊 / 约课 with a valid code: copy the booking line first (the relay chat can't be prefilled)
    if (held && t.closest && t.closest("#detailChat")) {
      copy(bookTextAll(held.codes)); toast(held.codes.length > 1 ? `已复制 ${held.codes.length} 个券码，发给老师即可` : "已复制券码，发给老师即可");
    }
    const tl = held && t.closest && t.closest("#detail a.tg-link");
    if (tl) { copy(bookTextAll(held.codes)); }
    const op = t.closest && t.closest("[data-cpn-open]");
    if (op) { openLamp(op.getAttribute("data-cpn-open")); return; }
    if (t.closest && t.closest('[data-nav="me"], [data-switch]')) setTimeout(paintMe, 400);
  }, true);
  // keep 「发布优惠（N 项）」 in step with what is being typed
  document.addEventListener("input", (ev) => {
    const id = ev.target && ev.target.id;
    if (id !== "cpnText" && id !== "cpnQty") return;
    syncEditor();
    const b = $("[data-cpn-new]"); if (!b) return;
    const total = draft.items.length + (draft.edit < 0 && draft.text.trim() ? 1 : 0);
    b.textContent = "发布优惠" + (total > 1 ? `（${total} 项）` : "");
  });
  const boot = () => { watchDetail(); };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})();
