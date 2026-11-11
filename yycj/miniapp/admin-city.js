// 后台城市分区：顶部城市切换（全部 + 每个城市，带待审数），每条记录显示城市标签，
// 切换城市时列表接口加 ?city=（服务器过滤，运营只拿到自己负责的城市）。
(() => {
  if (window.__yycjAdminCity) return;
  window.__yycjAdminCity = true;
  const KEY = "yycj_admin_city";
  const token = () => localStorage.getItem("yycj_token") || "";
  const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const S = { sel: localStorage.getItem(KEY) || "", sum: null, scope: null, isSuper: false, ops: null, sync: {} };
  const drafts={};
  document.addEventListener("input",ev=>{if(!S.sel || !ev.target.closest("#ccDyn"))return;const el=ev.target;if(!el.id)return;(drafts[S.sel] ||= {})[el.id]=el.type === "checkbox" ? el.checked : el.value;});
  document.addEventListener("change",ev=>{if(!S.sel || !ev.target.closest("#ccDyn"))return;const el=ev.target;if(!el.id)return;(drafts[S.sel] ||= {})[el.id]=el.type === "checkbox" ? el.checked : el.value;});
  const reg = { post: {}, review: {}, report: {}, lamp: {}, pin: {}, user: {} };
  const palette = {};
  const tagClass = (c) => {
    if (!c || c === "未知") return "city-tag unknown";
    if (!(c in palette)) palette[c] = Object.keys(palette).length % 6;
    return "city-tag" + (palette[c] ? " c" + palette[c] : "");
  };
  const tagHtml = (c) => `<span class="${tagClass(c)}">${esc(c || "未知")}</span>`;

  // ---- fetch wrapper: add ?city= to admin list reads, learn row → city from responses
  const LIST = /^\/api\/admin\/(posts\/pending|reviews\/pending|reports\/pending|listings|homepage|shadow|users)$/;
  const rawFetch = window.fetch.bind(window);
  function learn(path, data) {
    if (!data || typeof data !== "object") return;
    const items = data.items || [];
    if (path.endsWith("/posts/pending")) items.forEach((x) => { reg.post[x.post_id] = x.city || (x.lamp_data || {}).city || "未知"; });
    else if (path.endsWith("/reviews/pending")) items.forEach((x) => { reg.review[x.review_id] = x.city; if (x.city) reg.lamp[x.lamp_id] = x.city; });
    else if (path.endsWith("/reports/pending")) items.forEach((x) => { reg.report[x.report_id] = x.city; if (x.city) reg.lamp[x.lamp_id] = x.city; });
    else if (path.endsWith("/listings")) items.forEach((x) => { reg.lamp[x.lamp_id] = x.city; });
    else if (path.endsWith("/users")) items.forEach((x) => { reg.user[x.user_id] = x.cities || []; });
    else if (path.endsWith("/homepage")) {
      (data.pins || []).forEach((p) => { const c = (p.lamp || {}).city; reg.pin[p.id] = c; if (c) reg.lamp[p.lamp_id] = c; });
      (data.feed_pins || []).forEach((x) => { reg.lamp[x.lamp_id] = x.city; });
      (data.approved_lamps || []).forEach((x) => { reg.lamp[x.lamp_id] = x.city; });
    }
    schedulePaint();
  }
  window.fetch = function (input, init) {
    let url = typeof input === "string" ? input : (input && input.url) || "";
    const method = String((init && init.method) || "GET").toUpperCase();
    let u = null;
    try { u = new URL(url, location.href); } catch (e) {}
    if (u && u.origin === location.origin && u.pathname.startsWith("/api/admin/")) {
      const isList = method === "GET" && LIST.test(u.pathname);
      if (isList && S.sel && !u.searchParams.has("city")) {
        u.searchParams.set("city", S.sel);
        url = u.pathname + u.search;
        if (typeof input !== "string") input = new Request(url, input); else input = url;
      }
      const p = rawFetch(input, init);
      p.then((r) => {
        if (!r.ok) return;
        if (isList) r.clone().json().then((d) => learn(u.pathname, d)).catch(() => {});
        else if (method !== "GET") scheduleSummary();
      }).catch(() => {});
      return p;
    }
    return rawFetch(input, init);
  };

  // ---- summary (per-city counts) -------------------------------------------------
  let sumT = 0;
  function scheduleSummary() { clearTimeout(sumT); sumT = setTimeout(loadSummary, 500); }
  async function loadSummary() {
    if (!token()) return;
    try {
      const r = await rawFetch("/api/admin/cities/summary", { headers: { Authorization: "Bearer " + token() }, cache: "no-store" });
      if (!r.ok) return;
      const d = await r.json();
      S.sum = d; S.scope = d.scope; S.isSuper = !!d.is_super;
      S.cfg = {}; // global values may have changed: reload the open city's settings
      const names = (d.cities || []).map((x) => x.city);
      names.forEach((c) => tagClass(c));
      if (S.sel && !names.includes(S.sel)) setSel("", false);
      if (S.scope && S.scope.length === 1 && S.sel !== S.scope[0]) setSel(S.scope[0], true);
      renderBar();
      renderCard();
    } catch (e) {}
  }

  // ---- city bar --------------------------------------------------------------------
  function ensureBar() {
    const top = document.querySelector(".admin-body .topbar");
    if (!top) return null;
    let bar = document.getElementById("cityBar");
    if (!bar) {
      bar = document.createElement("div");
      bar.id = "cityBar";
      bar.setAttribute("role", "tablist");
      top.appendChild(bar);
      const sum = document.createElement("div");
      sum.id = "citySum";
      top.appendChild(sum);
      top.classList.add("has-city");
      bar.addEventListener("click", (ev) => {
        const b = ev.target.closest("[data-city]");
        if (b) setSel(b.dataset.city, true);
      });
    }
    return bar;
  }
  function renderBar() {
    const bar = ensureBar();
    if (!bar || !S.sum) return;
    const cities = S.sum.cities || [];
    const single = S.scope && S.scope.length === 1;
    const chip = (city, label, n) => `<button type="button" role="tab" class="cb-chip${S.sel === city ? " on" : ""}" data-city="${esc(city)}" aria-selected="${S.sel === city}">${esc(label)}<span class="cb-n${n ? "" : " zero"}">${n}</span></button>`;
    const html = (S.scope ? `<span class="cb-scope">负责</span>` : "") +
      (single ? "" : chip("", S.scope ? "我的城市" : "全部", (S.sum.total || {}).pending || 0)) +
      cities.map((x) => chip(x.city, x.city, x.pending)).join("");
    if (bar.innerHTML !== html) bar.innerHTML = html;
    const cur = S.sel ? cities.find((x) => x.city === S.sel) : S.sum.total;
    const sum = document.getElementById("citySum");
    if (sum && cur) {
      const t = `${esc(S.sel || (S.scope ? "我的城市" : "全部城市"))} · 待审 资料 <b>${cur.posts}</b> 评价 <b>${cur.reviews}</b> 举报 <b>${cur.reports}</b> ｜ 上架 <b>${cur.listings}</b> 置顶 <b>${cur.feed_pins}</b> 轮播 <b>${cur.carousel}</b>`;
      if (sum.innerHTML !== t) sum.innerHTML = t;
    }
    const top = document.querySelector(".admin-body .topbar");
    if (top) document.documentElement.style.setProperty("--ad-top", Math.round(top.getBoundingClientRect().height) + "px");
  }
  function setSel(city, reload) {
    S.sel = city || "";
    if (S.sel) localStorage.setItem(KEY, S.sel); else localStorage.removeItem(KEY);
    renderBar();
    renderCard();
    schedulePaint();
    if (!reload) return;
    document.getElementById("btnRefresh")?.click();
    const users = document.getElementById("adminUsers");
    if (users && users.children.length) document.getElementById("btnUserSearch")?.click();
  }

  // ---- row tags + client-side safety filter ---------------------------------------
  const LISTS = [
    ["#adminPosts", "post"], ["#adminReviews", "review"], ["#adminReports", "report"], ["#adminListings", "lamp"],
    ["#adminFeedPins", "lamp"], ["#adminPins", "pin"], ["#feedPinResults", "lamp"], ["#pinResults", "lamp"], ["#adminUsers", "user"],
  ];
  function rowCity(row, kind) {
    const lampEl = row.querySelector("[data-lamp]");
    const idEl = row.querySelector("[data-id]");
    if (kind === "pin" || kind === "lamp") {
      const lid = lampEl && lampEl.getAttribute("data-lamp");
      if (lid && reg.lamp[lid]) return reg.lamp[lid];
      const id = idEl && idEl.getAttribute("data-id");
      if (id == null) return undefined;
      return kind === "pin" ? (reg.pin[id] || reg.lamp[id]) : reg.lamp[id];
    }
    const id = idEl && idEl.getAttribute("data-id");
    return id == null ? undefined : reg[kind][id];
  }
  function paint() {
    LISTS.forEach(([sel, kind]) => {
      const box = document.querySelector(sel);
      if (!box) return;
      let shown = 0, rows = 0;
      [...box.children].forEach((row) => {
        if (row.classList.contains("city-empty") || !row.querySelector("[data-id],[data-lamp]")) return;
        rows++;
        const c = rowCity(row, kind);
        if (c === undefined) { shown++; return; }
        const list = kind === "user" ? (c || []) : [c || "未知"];
        const html = list.map(tagHtml).join("");
        // other admin scripts may move the card's children into wrappers: find our tag anywhere in the row
        const tags = row.querySelectorAll(".city-tag-row");
        for (let k = 1; k < tags.length; k++) tags[k].remove();
        let tag = tags[0] || null;
        if (html && !tag) { tag = document.createElement("div"); tag.className = "city-tag-row"; row.insertBefore(tag, row.firstChild); }
        if (tag && tag.innerHTML !== html) tag.innerHTML = html;
        if (tag && !html) tag.remove();
        const off = kind !== "user" && !!S.sel && (c || "未知") !== S.sel;
        if (row.classList.contains("city-off") !== off) row.classList.toggle("city-off", off);
        if (!off) shown++;
      });
      let empty = box.querySelector(":scope > .city-empty");
      const needEmpty = rows > 0 && shown === 0;
      if (needEmpty && !empty) { empty = document.createElement("p"); empty.className = "muted city-empty"; empty.textContent = `${S.sel} 暂无`; box.appendChild(empty); }
      if (!needEmpty && empty) empty.remove();
    });
  }
  let paintQ = false;
  function schedulePaint() {
    if (paintQ) return;
    paintQ = true;
    requestAnimationFrame(() => { paintQ = false; paint(); });
  }
  new MutationObserver(schedulePaint).observe(document.body, { childList: true, subtree: true });

  // ---- 站点设置: 城市公告 + 运营负责城市 -------------------------------------------
  async function loadOps() {
    if (!S.isSuper || S.ops) return;
    try {
      const r = await rawFetch("/api/admin/operators", { headers: { Authorization: "Bearer " + token() } });
      if (r.ok) { S.ops = await r.json(); renderCard(); }
    } catch (e) {}
  }
  // 按区目录 / 精选评价: site-wide default + per-city overview (super admin, 全站默认)
  async function loadFeat(force) {
    if (!S.isSuper || (S.feat && !force) || S.featLoading) return;
    S.featLoading = true;
    try {
      const r = await rawFetch("/api/admin/channel-features", { headers: { Authorization: "Bearer " + token() }, cache: "no-store" });
      if (r.ok) S.feat = await r.json();
    } catch (e) {}
    S.featLoading = false;
    renderCard();
  }
  const VIA = { own: "本城频道", city_push: "本城推送频道", site: "全站设的频道", site_push: "全站推送频道" };
  function featCell(x) {
    if (!x) return "";
    const st = x.on ? `<b class="cf-on">开</b>` : `<span class="cf-off">关</span>`;
    const src = x.own_switch ? `<span class="cc-badge own">本城市</span>` : `<span class="cc-badge">沿用全站</span>`;
    const ch = x.on ? (x.chat ? `<code>${esc(x.chat)}</code> <span class="muted">${esc(VIA[x.via] || "")}</span>` : `<span class="cf-warn">没有频道</span>`) : "";
    return `${st} ${src}<div class="cf-ch">${ch}</div>`;
  }
  // Asia/Shanghai: before 12:00 → 今天 12:00, else 明天 12:00 (same rule as the server)
  function nextNoon() {
    const bj = new Date(Date.now() + 8 * 3600e3);
    return bj.getUTCHours() < 12 ? "今天 12:00" : "明天 12:00";
  }
  window.__yycjNextNoon = nextNoon;
  function warnHtml(list) {
    if (!list || !list.length) return "";
    return `<div class="cf-warns"><b>已保存，但这些城市沿用的频道有问题（不影响保存）：</b><ul>` +
      list.map((w) => `<li>${tagHtml(w.city)} ${esc(w.feature)}${w.chat ? ` <code>${esc(w.chat)}</code>` : ""}：${esc(w.error)}</li>`).join("") + "</ul></div>";
  }
  function featHtml() {
    const f = S.feat;
    if (!f) { setTimeout(() => loadFeat(), 0); return `<h4 style="margin-top:14px">频道按区目录 · 精选评价</h4><p class="muted">读取中…</p>`; }
    const g = f.global || {};
    const push = g.broadcast_channel ? `推送频道 ${g.broadcast_channel}` : "推送频道（未设）";
    const row = (on, onId, ch, chId, label, hint) => `<div class="cc-f"><label class="row"><input type="checkbox" id="${onId}"${on ? " checked" : ""} /> ${label}</label>
      <div class="cc-fh"><label for="${chId}">发到的频道</label></div><input id="${chId}" maxlength="128" placeholder="${esc("空 = 用" + push)}" value="${esc(ch || "")}" />
      <p class="cc-hint">${hint}</p></div>`;
    const table = (f.cities || []).map((c) => `<tr><td>${tagHtml(c.city)}</td><td>${featCell(c.channel_directory)}</td><td>${featCell(c.broadcast_reviews)}</td><td>${featCell(c.broadcast_coupons)}</td>
      <td><button class="btn cf-edit" type="button" data-cc="city" data-city="${esc(c.city)}">改</button></td></tr>`).join("");
    return `<h4 style="margin-top:14px">频道按区目录 · 精选评价 · 优惠推送</h4>
      <p class="muted cc-lead">全站默认：没单独设的城市都用这里。每个城市的目录只列它自己的区和在架资料。机器人必须是频道管理员，保存时会检查。</p>
      ${row(g.channel_directory, "cfDirOn", g.directory_channel, "cfDirCh", "频道按区目录", "每天 12:00 更新同一条；关掉或换频道会删掉旧目录。")}
      <p class="cf-when">⏱ 按区目录的改动保存后不会马上发到频道，将在${esc(nextNoon())}生效。</p>
      ${row(g.broadcast_reviews, "cfRevOn", g.review_channel, "cfRevCh", "精选评价推送到频道", "审核通过的 4–5 星评价，只发文字，不发凭证。")}
      ${row(g.broadcast_coupons !== false, "cfCpnOn", g.coupon_channel, "cfCpnCh", "优惠推送到频道", "老师发优惠时发一条文字帖（查看详情 · 领取优惠）；结束 / 过期 / 停用 / 领完时改成「已结束」。默认开。")}
      <p class="muted">随全站设置一起保存；目录改动仍等12:00。</p>
      ${warnHtml(S.featWarn)}
      ${table ? `<table class="cf-table"><thead><tr><th>城市</th><th>按区目录</th><th>精选评价</th><th>优惠推送</th><th></th></tr></thead><tbody>${table}</tbody></table>` : ""}`;
  }
  // per-city overrides: GET/POST /api/admin/cities/settings
  S.cfg = {};
  async function loadCfg(city) {
    if (!city || S.cfg[city] === "loading") return;
    S.cfg[city] = "loading";
    try {
      const r = await rawFetch("/api/admin/cities/settings?city=" + encodeURIComponent(city), { headers: { Authorization: "Bearer " + token() }, cache: "no-store" });
      S.cfg[city] = r.ok ? await r.json() : { error: r.status };
    } catch (e) { S.cfg[city] = { error: 0 }; }
    renderCard();
  }
  const shortVal = (v) => {
    if (v === true) return "开"; if (v === false) return "关";
    const t = String(v ?? "").replace(/\s+/g, " ").trim();
    return t ? (t.length > 28 ? t.slice(0, 28) + "…" : t) : "（空）";
  };
  function fieldHtml(k, cfg) {
    const has = k.override !== null && k.override !== undefined;
    const lock = !cfg.can_edit || (k.super && !cfg.can_edit_super);
    const dis = lock ? " disabled" : "";
    const id = "ccf_" + k.key;
    const ph = "全站：" + shortVal(k.global);
    let input;
    if (k.type === "bool") {
      const v = has ? (k.override ? "1" : "0") : "";
      input = `<select id="${id}" data-k="${k.key}"${dis}><option value=""${v === "" ? " selected" : ""}>沿用全站（${shortVal(k.global)}）</option><option value="1"${v === "1" ? " selected" : ""}>开</option><option value="0"${v === "0" ? " selected" : ""}>关</option></select>`;
    } else if (k.type === "buttons") {
      input=`<input type="hidden" id="${id}" data-k="${k.key}" value="${esc(has?JSON.stringify(k.override):'')}"${dis}/><div data-city-button-editor="${k.key}"></div>`;
    } else if (k.type === "textarea") {
      input = `<textarea id="${id}" data-k="${k.key}" rows="3" maxlength="${k.max || 3000}" placeholder="${esc("全站：" + String(k.global ?? ""))}"${dis}>${esc(has ? k.override : "")}</textarea>`;
    } else {
      const num = k.type === "int" ? ` type="number" min="${k.min}" max="${k.max}" inputmode="numeric"` : ` maxlength="${k.max || 128}"`;
      input = `<input id="${id}" data-k="${k.key}"${num} placeholder="${esc(ph)}" value="${esc(has ? k.override : "")}"${dis} />`;
    }
    const badge = has ? `<span class="cc-badge own">本城市</span>` : `<span class="cc-badge">沿用全站</span>`;
    const clear = has && !lock ? `<button class="btn cc-clear" type="button" data-cc="clear" data-k="${k.key}">恢复全站</button>` : "";
    const note = k.super && !cfg.can_edit_super ? `<span class="cc-lock">只有超管能改</span>` : "";
    return `<div data-field="${esc(k.key)}" class="cc-f${has ? " own" : ""}"><div class="cc-fh"><label for="${id}">${esc(k.label)}</label>${badge}${note}${clear}</div>${input}${k.hint ? `<p class="cc-hint">${esc(k.hint)}</p>` : ""}${k.key === "channel_directory" || k.key === "directory_channel" ? `<p class="cf-when">⏱ 保存后不会马上发到频道，将在${esc(nextNoon())}生效</p>` : ""}</div>`;
  }
  // ---- merged 站点设置 layout -------------------------------------------------------
  // The 12 city-capable settings live in ONE place: the 城市设置 card. Under 全站默认 the card holds the
  // ORIGINAL site-wide inputs (same element ids, so every existing hydrate / save handler keeps writing the
  // same keys); under a city chip it shows that city's overrides. The old cards keep only the settings that
  // can't vary by city, under 「全站专用（不分城市）」, shown to the super admin under 全站默认 only.
  const GLOBAL_FIELDS = [
    { id: "annText", label: "首页公告", on: { id: "annOn", label: "显示公告" } },
    { id: "opsChannel", label: "推送频道", hint: "新上架资料推送到这个频道（@频道名 或 -100…）" },
    { id: "opsBroadcastTpl", label: "推送文案模板" },
    { id: "opsLinkDetail", label: "频道链接 · 详情", hint: "默认「查看详情」。留空 = 频道帖不显示这一项", make: () => { const i = document.createElement("input"); i.id = "opsLinkDetail"; i.maxLength = 16; i.placeholder = "查看详情"; return i; } },
    { id: "opsLinkReviews", label: "频道链接 · 评价", hint: "默认「查看评价」。留空 = 不显示", make: () => { const i = document.createElement("input"); i.id = "opsLinkReviews"; i.maxLength = 16; i.placeholder = "查看评价"; return i; } },
    { id: "opsLinkWrite", label: "频道链接 · 写评价", hint: "默认「写评价」。留空 = 不显示", make: () => { const i = document.createElement("input"); i.id = "opsLinkWrite"; i.maxLength = 16; i.placeholder = "写评价"; return i; } },
    { id: "opsPromo", label: "上架通过后发给老师的话", make: () => { const t = document.createElement("textarea"); t.id = "opsPromo"; t.rows = 4; t.placeholder = "审核通过后私信发给老师的话"; return t; } },
    { id: "opsCta", label: "聊天按钮文字" },
    { id: "opsAdminContact", label: "管理员联系链接", hint: "首页「管理员」按钮打开的链接，https://t.me/用户名 或 @用户名" },
    { id: "opsAdminLabel", label: "管理员按钮文字" },
    { id: "opsShowAdmin", label: "首页显示管理员按钮", row: true },
    { id: "opsReviewAudit", label: "评价需审核", row: true, hint: "关 = 新评价直接发布，不进待审" },
    { id: "opsPageSize", label: "首页每次加载卡片数" },
    { id: "opsCarouselSec", label: "轮播间隔（秒）" },
    { id: "opsListingDays", label: "上架天数" },
  ];
  GLOBAL_FIELDS.push({id:"ship_link_label_location",label:"频道链接 · 位置",hint:"有本城位置目标时才显示；留空隐藏，不生成空链接。",make:()=>{const i=document.createElement("input");i.id="ship_link_label_location";i.maxLength=16;return i;}});
  const NEW_FIELDS = {
    popup_announcement_enabled: ["启用进入小程序弹窗公告", "bool"],
    popup_announcement_title: ["弹窗公告标题（独立于普通公告）", "text"],
    popup_announcement_text: ["弹窗公告内容 · 纯文字，最多3000字", "textarea"],
    link_label_location: ["频道链接 · 位置", "text"],
    community_discussion_url: ["官方讨论群", "text"], community_backup_url: ["防失联导航", "text"],
    community_location_url: ["本城位置链接", "text"], community_location_label: ["位置入口文案", "text"],
    community_enabled: ["显示本城社区外链", "bool"],
    bot_welcome_enabled: ["发送欢迎消息（普通/start）", "bool"],
    approval_notify_enabled: ["发送老师上架成功通知", "bool"],
    approve_attach_detail: ["附带资料详情链接和小程序按钮", "bool"],
    approve_attach_channel_btn: ["附带查看频道帖按钮（有相册时）", "bool"]
  };
  Object.entries(NEW_FIELDS).filter(([key])=>key!=="link_label_location").forEach(([key, [label, type]]) => GLOBAL_FIELDS.push({
    id: "ship_" + key, label, row: type === "bool", make: () => {
      const i = document.createElement(type === "textarea" ? "textarea" : "input"); i.id = "ship_" + key;
      if(type === "textarea") { i.rows=6; i.maxLength=3000; return i; }
      if (type !== "bool") { i.maxLength = key === "popup_announcement_title" ? 40 : 512; return i; }
      i.type = "checkbox"; i.checked = key !== "popup_announcement_enabled"; const l = document.createElement("label"); l.className = "row"; l.append(i, " " + label); return i;
    }
  }));
  [ ["opsWelcome", "Bot /start 欢迎语"], ["opsMediaMax", "上架媒体上限"], ["opsShowCta", "首页显示聊天按钮", true],
    ["opsShowBot", "首页显示机器人", true], ["citiesInput", "开放城市（逗号分隔）"],
    ["opsMediaChannel", "媒体存储频道 ID"], ["opsChats", "必订频道 / 群"],
    ["opsTeachViews", "显示浏览", true], ["opsTeachFavs", "显示收藏", true], ["opsTeachContacts", "显示匿名联系", true]
  ].forEach(([id, label, row]) => GLOBAL_FIELDS.push({id, label, row}));
  const GROUPS = ["本城社区", "频道帖文案", "首页展示", "规则与通知", "老师「我的上架」数据", "系统"];
  function groupOf(key) {
    if (/^(community_|broadcast_channel|channel_directory|directory_channel|broadcast_reviews|review_channel|broadcast_coupons|coupon_channel)/.test(key)) return 0;
    if (/^(link_label|broadcast_template|listing_fields)/.test(key)) return 1;
    if (/^(approve_|review_require|listing_days|bot_welcome|media_max)/.test(key)) return 3;
    if (/^teacher_/.test(key)) return 4;
    if (/^(media_channel|required_chats|operator_cities)/.test(key)) return 5;
    return 2;
  }
  const ID_KEYS = {annText:"announcement_text", opsChannel:"broadcast_channel", opsBroadcastTpl:"broadcast_template",
    opsLinkDetail:"link_label_detail", opsLinkReviews:"link_label_reviews", opsLinkWrite:"link_label_write", opsPromo:"approve_promo_text",
    opsCta:"chat_cta_label", opsAdminContact:"admin_contact", opsAdminLabel:"admin_btn_label", opsShowAdmin:"show_admin_link",
    opsReviewAudit:"review_require_audit", opsPageSize:"home_feed_page_size", opsCarouselSec:"carousel_interval_sec", opsListingDays:"listing_days",
    opsWelcome:"bot_welcome_text", opsMediaMax:"media_max_count", opsShowCta:"show_chat_cta", opsShowBot:"show_bot_link", citiesInput:"enabled_cities",
    opsMediaChannel:"media_channel_id", opsChats:"required_chats", opsTeachViews:"teacher_show_views", opsTeachFavs:"teacher_show_favs", opsTeachContacts:"teacher_show_contacts"};
  function sections(prefix) {
    return GROUPS.map((title, i) => `<details class="ship-group" id="${prefix}${i}"${i < 2 ? " open" : ""}><summary>${title}</summary><div class="ship-fields"></div></details>`).join("");
  }
  function approvalPreview() {
    const val = key => { const el = document.getElementById(S.sel ? "ccf_"+key : "ship_"+key); if(!el) return true; return el.tagName === "SELECT" ? el.value === "" ? (S.cfg[S.sel]?.keys || []).find(x=>x.key===key)?.global !== false : el.value === "1" : el.checked; };
    const promo = document.getElementById(S.sel ? "ccf_approve_promo_text" : "opsPromo")?.value || "";
    const box = document.getElementById(S.sel ? "shipCityApproval" : "shipApproval");if(!box)return;
    const html=val('approval_notify_enabled')?`<b>预览 · 老师私聊（示例，不发送）</b><p>你的资料「示例资料」已通过审核并上架。</p><p>${esc(promo)}</p><p>按钮布局请看下方统一编辑器的示例预览；默认模式兼容原附加按钮开关。</p><p>频道相册、机器人文字链接保留；无频道相册不显示相册入口。</p>`:'<b>老师成功通知已关闭</b><p>后续审核、续期和媒体通过不发送成功通知；续期频道重发仍照常执行。</p>';
    if(box.innerHTML !== html) box.innerHTML=html;
  }
  document.addEventListener("input", approvalPreview); document.addEventListener("change",()=>{approvalPreview();document.getElementById("shipApprovalButtons")?._buttonEditor?.preview();document.querySelectorAll("[data-city-button-editor]").forEach(b=>b._buttonEditor?.preview());});
  function groupHtml(keys, cfg, announcement) {
    return GROUPS.map((title, i) => `<details class="ship-group"${i < 2 ? " open" : ""}><summary>${title}</summary><div class="ship-fields">${i === 2 ? announcement : ""}${keys.filter(k => groupOf(k.key) === i).map(k => fieldHtml(k, cfg)).join("")}${i === 1 ? syncHtml(S.sel) : ""}${i === 3 ? '<div id="shipCityApproval" class="ship-preview"></div>' : ""}${i > 3 ? '<p class="muted">全站专用，在全站默认中设置。</p>' : ''}</div></details>`).join("");
  }
  let welcomeButtons = [];
  function welcomeEditor(){const box=document.getElementById('shipWelcome');if(!box)return;window.yycjButtons?.mount(box,{context:'welcome',value:welcomeButtons,onChange:v=>{welcomeButtons=v;}});}
  function previewWelcome(){document.getElementById('shipWelcome')?._buttonEditor?.preview();}
  function mountApprovalEditor(box,input,value,inherited,city,disabled){
    if(!box||box._buttonEditor)return;
    window.yycjButtons?.mount(box,{context:'approval',value,inherited,city,disabled,getFlags:()=>{const get=(key)=>{const el=document.getElementById(city?'ccf_'+key:'ship_'+key);if(!el)return undefined;return el.tagName==='SELECT'?(el.value===''?(S.cfg[city]?.keys||[]).find(x=>x.key===key)?.global!==false:el.value==='1'):el.checked;};return{attach_detail:get('approve_attach_detail'),attach_channel:get('approve_attach_channel_btn')};},onChange:v=>{input.value=v===null?'':JSON.stringify(v);input.dispatchEvent(new Event('input',{bubbles:true}));}});
  }

  // labels the old cards used for the moved inputs (removed so nothing is shown twice / mislabelled)
  const OLD_LABELS = new Set(["公告", "首页每次加载条数", "轮播间隔秒", "聊天按钮文案", "管理员联系链接", "首页管理员按钮文案",
    "审核通过推送频道（@频道名 或 -100…）", "推送文案模板", "上架有效天数"]);
  let promoReady = false;
  function skeleton() {
    const pane = document.getElementById("pane-venue");
    if (!pane) return null;
    let card = document.getElementById("cityCard");
    if (!card) {
      card = document.createElement("div");
      card.id = "cityCard";
      card.className = "ops-card";
      card.innerHTML = `<h4>城市设置</h4><div id="ccTabs" class="cc-tabs"></div><div id="ccGlobal" class="cc-hide"><p class="muted cc-lead">全站默认：所有城市都用这里的值（城市单独设的项除外）。</p><div id="ccGlobalFields">${sections("shipG")}</div><button class="btn primary cc-save" type="button" data-cc="save-global">保存全站设置</button></div><div id="ccDyn"></div>`;
      const h = pane.querySelector("h3");
      if (h) h.insertAdjacentElement("afterend", card); else pane.prepend(card);
      card.addEventListener("click", onCardClick);
    }
    let site = document.getElementById("ccSiteOnly");
    if (!site) {
      site = document.createElement("div");
      site.id = "ccSiteOnly";
      site.className = "cc-hide";
      site.innerHTML = `<h3 class="cc-site-h">全站专用（不分城市）</h3>
        <div class="ops-card" id="opsTeacherStatsCard"><h4>老师「我的上架」数据</h4>
          <p class="muted">关掉后，老师端「我的上架」卡片上不再显示这一项；管理台数据看板不受影响。全站生效。</p>
          <label class="row"><input type="checkbox" id="opsTeachViews" checked /> 显示浏览</label>
          <label class="row"><input type="checkbox" id="opsTeachFavs" checked /> 显示收藏</label>
          <label class="row"><input type="checkbox" id="opsTeachContacts" checked /> 显示匿名联系</label>
        </div>`;
      card.insertAdjacentElement("afterend", site);
      // admin-kit puts the 上架栏 editor right after the template box; pin it to 媒体与推送 before the template moves
      const tpl = document.getElementById("opsBroadcastTpl");
      if (tpl && !document.getElementById("listingFieldBox")) {
        const box = document.createElement("div");
        box.id = "listingFieldBox";
        tpl.parentNode.insertBefore(box, tpl.nextSibling);
      }
    }
    pane.querySelectorAll(":scope > .ops-card:not(#cityCard)").forEach((c) => site.appendChild(c));
    return card;
  }
  function relayout() {
    const card = skeleton();
    if (!card) return;
    const host = document.getElementById("ccGlobalFields");
    const site = document.getElementById("ccSiteOnly");
    GLOBAL_FIELDS.forEach((f) => {
      let el = document.getElementById(f.id);
      if (!el && f.make) el = f.make();
      if (!el) return; // created later by another admin script; picked up on the next pass
      let wrap = host.querySelector(`.cc-f[data-gf="${f.id}"]`);
      if (!wrap) {
        wrap = document.createElement("div");
        wrap.className = "cc-f";
        wrap.dataset.gf = f.id;
        wrap.innerHTML = f.row ? "" : `<div class="cc-fh"><label for="${f.id}">${esc(f.label)}</label></div>`;
        const key = ID_KEYS[f.id] || f.id.replace(/^ship_/, "");
        host.querySelector("#shipG" + groupOf(key) + " > .ship-fields").appendChild(wrap);
        // keep the GLOBAL_FIELDS order even when fields arrive late

      }
      const node = f.row ? el.closest("label.row") || el : el;
      if (node.parentNode !== wrap) {
        wrap.appendChild(node);
        if (f.hint) { const p = document.createElement("p"); p.className = "cc-hint"; p.textContent = f.hint; wrap.appendChild(p); }
      }
      if (f.row && node.tagName === "LABEL") {
        const txt = [...node.childNodes].find((n) => n.nodeType === 3 && n.textContent.trim());
        if (txt && txt.textContent.trim() !== f.label) txt.textContent = " " + f.label;
      }
      if (f.on) {
        const on = document.getElementById(f.on.id);
        const row = on && on.closest("label.row");
        if (row && row.parentNode !== wrap) wrap.appendChild(row);
        const txt = row && [...row.childNodes].find((n) => n.nodeType === 3 && n.textContent.trim());
        if (txt && txt.textContent.trim() !== f.on.label) txt.textContent = " " + f.on.label;
      }
    });
    // old labels of moved inputs, and anything other scripts dropped next to a moved input
    site.querySelectorAll(".ops-card > label:not(.row)").forEach((l) => { if (OLD_LABELS.has(l.textContent.trim())) l.remove(); });
    // other scripts insert their own label next to an input that already moved (e.g. 管理员按钮文案): drop it
    host.querySelectorAll(".cc-f > label:not(.row)").forEach((l) => l.remove());
    const move = (node, group) => { const target = host.querySelector("#shipG" + group + " > .ship-fields"); if(node && target && node.parentNode !== target) target.appendChild(node); };
    move(document.getElementById("listingFieldBox"), 1);
    move(document.getElementById("btnSyncBot"), 5);
    move(document.getElementById("opsAdminBox"), 5);
    for(const [id, group] of [["shipFeatures",0],["shipOperators",5]]) if(!document.getElementById(id)) { const w=document.createElement("div");w.id=id;move(w,group); }
    if (!document.getElementById("shipApproval")) { const w=document.createElement("div");w.id="shipApproval";w.className="ship-preview";move(w,3); }
    if (!document.getElementById('shipApprovalButtons')){const w=document.createElement('div');w.innerHTML='<h4>老师成功通知按钮</h4><input type="hidden" id="ship_approval_buttons"/><div id="shipApprovalButtons"></div>';move(w,3);}
    if (!document.getElementById("shipWelcome")) { const w = document.createElement("div"); w.id = "shipWelcome"; move(w, 3); welcomeEditor(); }
    // cards left empty after the move (only a heading) are hidden
    site.querySelectorAll(".ops-card").forEach((c) => c.classList.toggle("cc-hide", !c.querySelector("input, textarea, select, button")));
    showMode();
  }
  function showMode() {
    const g = document.getElementById("ccGlobal");
    const site = document.getElementById("ccSiteOnly");
    const globalMode = !!S.sum && S.isSuper && !S.sel;
    if (g) g.classList.toggle("cc-hide", !globalMode);
    if (site) site.classList.toggle("cc-hide", !globalMode);
    // the bottom 保存全部设置 bar only saves site-wide values: hide it unless they are on screen
    document.body.classList.add("cc-no-sitebar");
  }
  async function hydratePromo() {
    if (promoReady || !S.isSuper) return;
    const el = document.getElementById("opsPromo");
    if (!el) return;
    try {
      const r = await rawFetch("/api/admin/homepage", { headers: { Authorization: "Bearer " + token() } });
      if (!r.ok) return;
      const st = ((await r.json()).settings) || {};
      Object.keys(NEW_FIELDS).forEach(key => { const el = document.getElementById("ship_" + key); if (!el) return; if (el.type === "checkbox") el.checked = st[key] !== false; else el.value = st[key] || ""; });
      welcomeButtons = st.bot_welcome_buttons || []; welcomeEditor(); approvalPreview();
      const approvalInput=document.getElementById('ship_approval_buttons'),approvalBox=document.getElementById('shipApprovalButtons');
      if(approvalInput && approvalBox){approvalInput.value=JSON.stringify(st.approval_buttons||{mode:'default',items:[]});approvalBox._buttonEditor=null;mountApprovalEditor(approvalBox,approvalInput,JSON.parse(approvalInput.value),null,null,false);}
      if (typeof st.approve_promo_text === "string") { el.value = st.approve_promo_text; promoReady = true; }
    } catch (e) {}
  }
  // the site-wide save (admin-ops etc.) posts /api/admin/settings/extra: add 上架通过后发给老师的话 once loaded
  const fetchBeforePromo = window.fetch;
  window.fetch = function (url, opt) {
    try {
      if (promoReady && String(url || "").includes("/api/admin/settings/extra") && opt && typeof opt.body === "string") {
        const body = JSON.parse(opt.body);
        const el = document.getElementById("opsPromo");
        if (el && !("approve_promo_text" in body)) {
          body.approve_promo_text = el.value;
          opt = Object.assign({}, opt, { body: JSON.stringify(body) });
        }
      }
    } catch (e) {}
    return fetchBeforePromo.apply(this, [url, opt]);
  };
  let layoutQ = false;
  new MutationObserver(() => {
    if (layoutQ) return;
    layoutQ = true;
    requestAnimationFrame(() => { layoutQ = false; relayout(); });
  }).observe(document.getElementById("pane-venue") || document.body, { childList: true, subtree: true });
  relayout();

  function beijing(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    const s = new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }).format(d);
    return s + "（北京时间）";
  }
  function syncHtml(city) {
    const d = S.sync[city];
    if (!d || d === "loading") {
      return `<div class="cc-sync"><div class="line"><h4 style="margin:14px 0 0">历史频道帖</h4></div><p class="muted">读取同步进度…</p></div>`;
    }
    if (d.error) return `<div class="cc-sync"><div class="line"><h4 style="margin:14px 0 0">历史频道帖</h4></div><p class="muted">读不到进度（${esc(d.error)}）</p></div>`;
    const stMap = { idle: "尚未同步", queued: "排队更新中", running: "正在更新", done: "上次已完成" };
    const tag = stMap[d.status] || d.status || "";
    const when = beijing(d.finished_at || d.started_at || d.queued_at);
    const failN = (d.failed || []).length || d.retry || 0;
    const rows = (d.failed || []).slice(0, 8).map((x) => `<tr><td>${esc(x.kind === "coupon" ? "优惠帖" : "上架帖")}</td><td>${esc(x.error || "")}</td><td>${esc((x.detail || x.id || "").slice(0, 40))}</td></tr>`).join("");
    const table = `<table class="cf-table"><tr><th>类型</th><th>状态</th><th>说明</th></tr>
      <tr><td>上架 / 优惠帖</td><td>已更新 ${esc(d.updated || 0)}${failN ? " · 待重试 " + failN : ""}</td><td>${when ? "上次 " + esc(when) : "保存链接文案后约 30 秒自动改文字"}</td></tr>
      <tr><td>精选评价</td><td>${d.reviews_legacy ? "旧帖不可定位 " + d.reviews_legacy : "可定位 " + (d.reviews_stored || 0)}</td><td>旧帖没有存消息编号，这次改不到；以后新发的会记下。</td></tr>
      ${rows}</table>`;
    return `<div class="cc-sync">
      <div class="line" style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-top:14px">
        <h4 style="margin:0">历史频道帖</h4><span class="cc-badge${d.status === "running" || d.status === "queued" ? " own" : ""}">${esc(tag)}</span>
      </div>
      <p class="muted">只改能找到原消息的上架帖和优惠帖；消失的跳过，不补发。保存链接文案后约 30 秒自动改文字，不必等到中午。按区目录的设置改动仍是中午再生效。</p>
      <div class="cc-sync-nums">
        <div><b>${esc(d.updated || 0)}</b><span class="muted">已更新</span></div>
        <div><b>${esc(d.retry || 0)}</b><span class="muted">待重试</span></div>
        <div><b>${esc(d.missing || 0)}</b><span class="muted">找不到原帖</span></div>
        <div><b>${esc(d.gone || 0)}</b><span class="muted">已消失</span></div>
      </div>
      <button class="btn primary" type="button" data-cc="sync-run">立即同步本城旧帖</button>
      <button class="btn" type="button" data-cc="sync-retry"${failN ? "" : " disabled"}>重试失败</button>
      ${table}
    </div>`;
  }
  async function loadSync(city, force) {
    if (!city) return;
    if (!force && S.sync[city] && S.sync[city] !== "loading") return;
    if (!S.sync[city]) S.sync[city] = "loading";
    try {
      const r = await rawFetch("/api/admin/label-sync?city=" + encodeURIComponent(city), { headers: { Authorization: "Bearer " + token() }, cache: "no-store" });
      const d = await r.json().catch(() => ({}));
      S.sync[city] = r.ok ? d : { error: d.detail || "读取失败" };
      renderCard();
      if (d.status === "queued" || d.status === "running") {
        clearTimeout(loadSync._t);
        loadSync._t = setTimeout(() => loadSync(city, true), 2500);
      }
    } catch (e) { S.sync[city] = { error: e.message || String(e) }; renderCard(); }
  }
  function renderCard() {

    const card = skeleton();
    if (!card || !S.sum) return;
    relayout();
    hydratePromo();
    const anns = S.sum.city_announcements || {};
    const counts = S.sum.city_override_counts || {};
    const cities = (S.sum.cities || []).map((x) => x.city);
    const canEdit = (c) => S.isSuper || (S.scope && S.scope.includes(c));
    const single = S.scope && S.scope.length === 1;
    const tab = (c, label) => `<button type="button" class="cc-tab${S.sel === c ? " on" : ""}" data-cc="city" data-city="${esc(c)}">${esc(label)}${c && counts[c] ? `<span class="cc-cnt">${counts[c]}</span>` : ""}</button>`;
    const tabs = `${single ? "" : tab("", S.scope ? "我的城市" : "全站默认")}${cities.map((c) => tab(c, c)).join("")}`;
    const tabsEl = document.getElementById("ccTabs");
    if (tabsEl.innerHTML !== tabs) tabsEl.innerHTML = tabs;
    let html = "";
    if (S.sel) {
      const a = anns[S.sel] || {};
      html += `<p class="muted cc-lead">只改${esc(S.sel)}；空着的项沿用全站默认（灰字是全站现在的值）。</p>
        <div class="cc-f${a.text ? " own" : ""}"><div class="cc-fh"><label for="ccAnnText">首页公告</label>${a.text ? `<span class="cc-badge own">本城市</span>` : `<span class="cc-badge">沿用全站</span>`}</div>
        <textarea id="ccAnnText" rows="2" placeholder="全站：${esc(S.sum.global_announcement || "")}"${canEdit(S.sel) ? "" : " disabled"}>${esc(a.text || "")}</textarea>
        <label class="row"><input type="checkbox" id="ccAnnOn"${a.text ? (a.enabled ? " checked" : "") : " checked"}${canEdit(S.sel) ? "" : " disabled"} /> 显示${esc(S.sel)}公告</label>
        </div>`;
      const cfg = S.cfg[S.sel];
      if (!cfg || cfg === "loading") { html += `<p class="muted">读取${esc(S.sel)}设置…</p>`; if (!cfg) setTimeout(() => loadCfg(S.sel), 0); }
      else if (cfg.error !== undefined) html += `<p class="muted">读不到${esc(S.sel)}设置（${esc(cfg.error)}）</p>`;
      else {
        const announcement = html; html = `<p class="muted cc-lead">当前编辑：${esc(S.sel)} · 空值沿用全站；频道链接文案留空则隐藏。恢复全站请点对应按钮。</p>` + groupHtml(cfg.keys || [], cfg, announcement);
        html += cfg.can_edit ? `<button class="btn primary cc-save" type="button" data-cc="save-cfg">保存${esc(S.sel)}设置</button>` : `<p class="muted">只能查看：只有超管或负责${esc(S.sel)}的运营能改</p>`;

        if (!S.sync[S.sel] || S.sync[S.sel] === "loading") setTimeout(() => loadSync(S.sel), 0);
      }
      if (S.isSuper) html += `<p class="muted cc-foot">全站默认值和不分城市的设置在「全站默认」里改。</p>`;
    } else {
      if (S.isSuper) {
        const update=(id, content)=>{const el=document.getElementById(id);if(el && el._content !== content){el.innerHTML=content;el._content=content;}};
        update("shipFeatures", featHtml() + `<h4>各城市</h4>` + cities.map(c=>`<div class="cc-row">${tagHtml(c)}<span class="cc-ann">${counts[c] ? "单独设置 "+counts[c]+" 项" : "全部沿用全站"}</span></div>`).join(""));
        const ops=(S.ops && S.ops.extra_admin_ids) || [], map=(S.ops && S.ops.operator_cities) || {};
        update("shipOperators", `<h4>运营负责城市</h4><p class="muted">空着 = 全部城市。随全站设置保存。</p>` + ops.map(uid=>`<div class="cc-op"><code>${esc(uid)}</code><input data-op="${esc(uid)}" placeholder="全部城市" value="${esc((map[String(uid)] || []).join("，"))}"/></div>`).join(""));
      } else html += `<p class="muted">全站设置只有超管能改；点城市查看本城设置。</p>`;
    }
    const dyn = document.getElementById("ccDyn");
    const active = document.activeElement;
    showMode();
    if (active && dyn.contains(active) && /TEXTAREA|INPUT|SELECT/.test(active.tagName)) return; // don't clobber typing
    if (dyn._template !== html) {dyn.innerHTML = html;dyn._template=html;}
    Object.entries(drafts[S.sel] || {}).forEach(([id,v])=>{const el=document.getElementById(id);if(el){if(el.type === "checkbox") el.checked=v;else el.value=v;}});
    dyn.querySelectorAll('[data-city-button-editor]').forEach(box=>{const k=(S.cfg[S.sel]?.keys||[]).find(k=>k.key===box.dataset.cityButtonEditor),input=document.getElementById('ccf_'+k?.key);if(k && input){let value=null;try{value=input.value?JSON.parse(input.value):null;}catch{}mountApprovalEditor(box,input,value,k.global,S.sel,input.disabled);}});
    approvalPreview();
    if (S.isSuper && !S.ops) loadOps();
  }
  async function post(path, body) {
    const r = await rawFetch(path, { method: "POST", headers: { "Content-Type": "application/json", Authorization: "Bearer " + token() }, body: JSON.stringify(body) });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || "保存失败");
    return d;
  }
  function afterCfg(d) {
    S.cfg[S.sel] = d;
    const n = Object.keys(d.overrides || {}).length;
    S.sum.city_override_counts = Object.assign({}, S.sum.city_override_counts, { [S.sel]: n });
  }
  async function onCardClick(ev) {
    const b = ev.target.closest("[data-cc]");
    if (!b) return;
    if (b.dataset.cc === "city") { setSel(b.dataset.city, true); return; }
    b.disabled = true;
    try {
      if (b.dataset.cc === "save-global") {
        if (!promoReady || !S.feat) throw new Error("设置尚未加载完成，请稍后重试");
        const val = id => document.getElementById(id)?.value || "";
        const checked = id => !!document.getElementById(id)?.checked;
        const ops = {bot_welcome_buttons: welcomeButtons,approval_buttons:JSON.parse(document.getElementById("ship_approval_buttons")?.value||'{"mode":"default","items":[]}')};
        Object.entries(ID_KEYS).forEach(([id,key]) => {
          const el=document.getElementById(id); if(!el || ["announcement_text","enabled_cities"].includes(key)) return;
          ops[key]=el.type === "checkbox" ? el.checked : el.type === "number" ? +el.value : el.value;
        });
        Object.entries(NEW_FIELDS).forEach(([key, [,type]]) => ops[key]=type === "bool" ? checked("ship_"+key) : val("ship_"+key));
        ops.required_chats = val("opsChats").split("\n").map(line => { const p=line.split("|").map(x=>x.trim()); return p[0] ? {chat_id:p[0], title:p[1]||"",url:p[2]||"",required:true} : null; }).filter(Boolean);
        if(window.__listingFields) ops.listing_fields=window.__listingFields;
        await post("/api/admin/settings/extra", ops);
        await post("/api/admin/homepage/ops", {announcement_text: val("annText"),announcement_enabled:checked("annOn"), cities:val("citiesInput").split(/[,，\s]+/).filter(Boolean),home_feed_page_size:+val("opsPageSize"),chat_cta_label:val("opsCta"),bot_welcome_text:val("opsWelcome"),media_max_count:+val("opsMediaMax"),review_require_audit:checked("opsReviewAudit")});
        const d = await post("/api/admin/channel-features", {channel_directory:checked("cfDirOn"), directory_channel:val("cfDirCh"), broadcast_reviews:checked("cfRevOn"),review_channel:val("cfRevCh"), broadcast_coupons:checked("cfCpnOn"),coupon_channel:val("cfCpnCh")});
        S.feat=d; S.featWarn=d.warnings || [];
        const map={}; document.querySelectorAll("#cityCard [data-op]").forEach(el=>{ const list=el.value.split(/[,，、\s]+/).filter(Boolean); if(list.length) map[el.dataset.op]=list; });
        if(S.ops) { const d=await post("/api/admin/operators/cities",{operator_cities:map}); S.ops=Object.assign({},S.ops,d); }
        document.body.classList.remove("ops-dirty"); alert("全站设置已保存。目录改动在下一次12:00生效。" + (S.featWarn.length ? "有频道配置警告，请查看概览。" : ""));
        S.cfg={};
      } else if (b.dataset.cc === "save-ann") {
        const d = await post("/api/admin/cities/announcement", { city: S.sel, text: document.getElementById("ccAnnText").value, enabled: document.getElementById("ccAnnOn").checked });
        S.sum.city_announcements = d.city_announcements || {};
        alert(`${S.sel} 公告已保存`);
      } else if (b.dataset.cc === "save-cfg") {
        const cfg = S.cfg[S.sel] || {};
        const overrides = {};
        document.querySelectorAll("#cityCard [data-k]:not(button)").forEach((el) => {
          if (el.disabled) return;
          const k = (cfg.keys || []).find((x) => x.key === el.dataset.k);
          if (!k) return;
          if(k.allow_blank && k.override == null && el.value === "" && !Object.prototype.hasOwnProperty.call(drafts[S.sel] || {},el.id)) return;
          let v = el.value;
          if (k.type === "buttons") v = v ? JSON.parse(v) : null;
          else if (k.type === "bool") v = v === "" ? null : v === "1";
          else if (String(v).trim() === "") v = k.allow_blank ? "" : null;
          const cur = k.override === undefined ? null : k.override;
          if (k.type === "buttons" ? JSON.stringify(v)!==JSON.stringify(cur) : String(v) !== String(cur)) overrides[k.key] = v;
        });
        if (Object.keys(overrides).length) afterCfg(await post("/api/admin/cities/settings", { city: S.sel, overrides }));
        const a = await post("/api/admin/cities/announcement", {city:S.sel, text:document.getElementById("ccAnnText").value, enabled:document.getElementById("ccAnnOn").checked});
        delete drafts[S.sel];
        S.sum.city_announcements=a.city_announcements || {}; S.feat=null; S.sync[S.sel]="loading";
        setTimeout(()=>loadSync(S.sel,true),400); alert(`${S.sel} 设置已保存`);
      } else if (b.dataset.cc === "clear") {
        if(drafts[S.sel]) delete drafts[S.sel]["ccf_"+b.dataset.k];
        afterCfg(await post("/api/admin/cities/settings", { city: S.sel, overrides: { [b.dataset.k]: null } }));
        S.feat = null;
      } else if (b.dataset.cc === "save-feat") {
        const v = (id) => (document.getElementById(id)?.value || "").trim();
        const d = await post("/api/admin/channel-features", {
          channel_directory: !!document.getElementById("cfDirOn")?.checked, directory_channel: v("cfDirCh"),
          broadcast_reviews: !!document.getElementById("cfRevOn")?.checked, review_channel: v("cfRevCh"),
          broadcast_coupons: !!document.getElementById("cfCpnOn")?.checked, coupon_channel: v("cfCpnCh"),
        });
        S.feat = d; S.cfg = {}; S.featWarn = d.warnings || [];
        alert("已保存。按区目录将在" + (d.directory_applies_at || nextNoon()) + "生效" + (S.featWarn.length ? "；有 " + S.featWarn.length + " 个城市的频道需要检查，见下方" : ""));
      } else if (b.dataset.cc === "sync-run") {
        S.sync[S.sel] = await post("/api/admin/label-sync/run", { city: S.sel });
        setTimeout(() => loadSync(S.sel, true), 1200);
      } else if (b.dataset.cc === "sync-retry") {
        S.sync[S.sel] = await post("/api/admin/label-sync/retry", { city: S.sel });
        setTimeout(() => loadSync(S.sel, true), 800);
      } else if (b.dataset.cc === "save-ops") {
        const map = {};
        document.querySelectorAll("#cityCard [data-op]").forEach((inp) => {
          const list = inp.value.split(/[,，、\s]+/).map((x) => x.trim()).filter(Boolean);
          if (list.length) map[inp.dataset.op] = list;
        });
        const d = await post("/api/admin/operators/cities", { operator_cities: map });
        S.ops = Object.assign({}, S.ops, { operator_cities: d.operator_cities || {} });
        alert("负责城市已保存");
      }
    } catch (e) { alert(e.message || String(e)); }
    b.disabled = false;
    if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
    renderCard();
  }

  // boot: wait for the admin token, then load counts; refresh counts on 刷新
  document.getElementById("btnRefresh")?.addEventListener("click", scheduleSummary);
  let tries = 0;
  (function boot() {
    if (token() && document.getElementById("console") && !document.getElementById("console").classList.contains("hidden")) { loadSummary(); return; }
    if (++tries < 60) setTimeout(boot, 250);
  })();
})();
