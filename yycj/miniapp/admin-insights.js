(() => {
  // 后台增补：「数据看板」(按城市每日 新上架/浏览/匿名联系/评价 + 即将到期) 与「评价批量审核」。
  if (window.__yycjAdminInsights) return;
  window.__yycjAdminInsights = true;
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  const token = () => localStorage.getItem("yycj_token") || "";
  const city = () => localStorage.getItem("yycj_admin_city") || "";
  const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  async function api(path, opts = {}) {
    const headers = Object.assign({ "Content-Type": "application/json", Authorization: "Bearer " + token() }, opts.headers || {});
    const r = await fetch(path, { ...opts, headers, cache: "no-store" });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || r.statusText);
    return d;
  }
  function toast(m) { const t = $("#toast"); if (t) { t.textContent = m; t.classList.remove("hidden"); setTimeout(() => t.classList.add("hidden"), 2200); } }
  const q = (o) => { const p = new URLSearchParams(o); if (city()) p.set("city", city()); return p.toString(); };

  // ---------- 数据看板 pane ----------
  function mountPane() {
    const side = $("#adminSide");
    if (side && !$('[data-pane="stats"]', side)) {
      const b = document.createElement("button");
      b.type = "button";
      b.dataset.pane = "stats";
      b.textContent = "数据看板";
      const firstLabel = $(".side-label", side);
      (firstLabel || side.firstChild) && side.insertBefore(b, firstLabel ? firstLabel.nextSibling : side.firstChild);
    }
    const main = $(".admin-main");
    if (main && !$("#pane-stats")) {
      const sec = document.createElement("section");
      sec.className = "admin-pane";
      sec.id = "pane-stats";
      sec.innerHTML = `<h3>数据看板</h3>
        <div class="ai-bar"><span class="muted" id="aiScope"></span>
          <div class="ai-seg" role="tablist"><button type="button" data-ai-days="7">近7天</button><button type="button" class="on" data-ai-days="14">近14天</button><button type="button" data-ai-days="30">近30天</button></div></div>
        <div id="aiTotals" class="ai-totals"></div>
        <div class="card"><h4 class="ai-h">按天趋势</h4><div id="aiChart" class="ai-chart"></div></div>
        <div class="card"><h4 class="ai-h">每日明细</h4><div id="aiTable" class="ai-table-wrap"></div></div>
        <div class="card"><h4 class="ai-h">即将到期 <span class="muted">（7 天内 · 到期前 3 天 / 1 天自动提醒老师）</span></h4><div id="aiExpiring"></div></div>`;
      main.appendChild(sec);
    }
  }
  let days = 14;
  const KEYS = [["listings", "新上架"], ["views", "浏览"], ["contacts", "匿名联系"], ["reviews", "评价"]];
  async function loadStats() {
    try {
      const [d, ex] = await Promise.all([api("/api/admin/stats/daily?" + q({ days })), api("/api/admin/listings/expiring?" + q({ days: 7 }))]);
      paintStats(d);
      paintExpiring(ex.items || []);
    } catch (e) {
      $("#aiTotals").innerHTML = `<p class="muted">加载失败：${esc(e.message)}</p>`;
    }
  }
  function paintStats(d) {
    $("#aiScope").textContent = (city() || "全部城市") + " · " + d.days[0].slice(5) + " ~ " + d.days[d.days.length - 1].slice(5) + "（北京时间）";
    const sum = { listings: 0, views: 0, contacts: 0, reviews: 0 };
    Object.values(d.totals || {}).forEach((t) => KEYS.forEach(([k]) => { sum[k] += t[k] || 0; }));
    $("#aiTotals").innerHTML = KEYS.map(([k, l]) => `<div class="ai-kpi"><b>${sum[k]}</b><span>${l}</span></div>`).join("");
    const byDay = {};
    d.days.forEach((x) => { byDay[x] = { listings: 0, views: 0, contacts: 0, reviews: 0 }; });
    (d.rows || []).forEach((r) => { if (byDay[r.day]) KEYS.forEach(([k]) => { byDay[r.day][k] += r[k] || 0; }); });
    const max = Math.max(1, ...d.days.map((x) => byDay[x].views));
    const maxC = Math.max(1, ...d.days.map((x) => Math.max(byDay[x].contacts, byDay[x].listings, byDay[x].reviews)));
    $("#aiChart").innerHTML = `<div class="ai-legend"><i class="lv"></i>浏览 <i class="lc"></i>匿名联系 <i class="ll"></i>新上架 <i class="lr"></i>评价</div><div class="ai-bars">` + d.days.map((x) => {
      const v = byDay[x];
      return `<div class="ai-col" title="${x} 浏览${v.views} 联系${v.contacts} 上架${v.listings} 评价${v.reviews}">
        <div class="ai-stack"><span class="bv" style="height:${Math.round((v.views / max) * 100)}%"></span></div>
        <div class="ai-mini"><span class="bc" style="height:${Math.round((v.contacts / maxC) * 100)}%"></span><span class="bl" style="height:${Math.round((v.listings / maxC) * 100)}%"></span><span class="br" style="height:${Math.round((v.reviews / maxC) * 100)}%"></span></div>
        <em>${x.slice(8)}</em></div>`;
    }).join("") + "</div>";
    const cities = d.cities || [];
    const rows = [...(d.rows || [])].sort((a, b) => (a.day < b.day ? 1 : a.day > b.day ? -1 : a.city.localeCompare(b.city)));
    $("#aiTable").innerHTML = rows.length ? `<table class="ai-table"><thead><tr><th>日期</th><th>城市</th>${KEYS.map(([, l]) => `<th>${l}</th>`).join("")}</tr></thead><tbody>` +
      rows.map((r) => `<tr><td>${esc(r.day.slice(5))}</td><td>${esc(r.city)}</td>${KEYS.map(([k]) => `<td class="${r[k] ? "" : "z"}">${r[k]}</td>`).join("")}</tr>`).join("") +
      `</tbody><tfoot><tr><td colspan="2">合计（${cities.length} 城）</td>${KEYS.map(([k]) => `<td>${sum[k]}</td>`).join("")}</tr></tfoot></table>` : "<p class='muted'>这段时间没有数据</p>";
  }
  function paintExpiring(items) {
    $("#aiExpiring").innerHTML = items.length ? items.map((x) => {
      const h = x.hours_left;
      const left = h < 24 ? `${Math.max(0, h)} 小时` : `${Math.floor(h / 24)} 天`;
      const tag = (on, l) => `<span class="ai-tag${on ? " on" : ""}">${on ? "✓" : "·"} ${l}</span>`;
      return `<div class="ai-exp${h < 24 ? " urgent" : ""}"><div><strong>${esc(x.title || x.lamp_id)}</strong>
        <div class="muted">${esc(x.city || "")} ${esc(x.district || "")} · 老师 ${esc(x.user_id)} · 到期 ${esc(String(x.expires_at).slice(0, 10))}</div></div>
        <div class="ai-exp-r"><b>剩 ${left}</b><div>${tag(x.nudged_3d, "3天提醒")}${tag(x.nudged_1d, "1天提醒")}</div></div></div>`;
    }).join("") : "<p class='muted'>7 天内没有到期的资料</p>";
  }
  document.addEventListener("click", (ev) => {
    const p = ev.target.closest && ev.target.closest('#adminSide [data-pane="stats"]');
    if (p) { mountPane(); const c = $("#adminCrumb"); if (c) c.textContent = "数据看板"; loadStats(); }
    const dbtn = ev.target.closest && ev.target.closest("[data-ai-days]");
    if (dbtn) { days = Number(dbtn.dataset.aiDays) || 14; $$("[data-ai-days]").forEach((b) => b.classList.toggle("on", b === dbtn)); loadStats(); }
  });

  // ---------- 评价批量审核 ----------
  function decorateReviews() {
    const box = $("#adminReviews");
    if (!box) return;
    const cards = $$(".card", box).filter((c) => $('[data-admin="rev-ok"]', c));
    if (!cards.length) { $("#aiRevBar")?.remove(); return; }
    cards.forEach((c) => {
      if ($(".ai-chk", c)) return;
      const id = $('[data-admin="rev-ok"]', c).dataset.id;
      const lab = document.createElement("label");
      lab.className = "ai-chk";
      lab.innerHTML = `<input type="checkbox" data-ai-rev="${esc(id)}" /><span>选择</span>`;
      c.insertBefore(lab, c.firstChild);
      c.classList.add("ai-rev");
    });
    if (!$("#aiRevBar")) {
      const bar = document.createElement("div");
      bar.id = "aiRevBar";
      bar.className = "ai-revbar";
      bar.innerHTML = `<label class="ai-all"><input type="checkbox" id="aiRevAll" /> 全选</label><span id="aiRevN" class="muted">已选 0 条</span>
        <button type="button" class="btn primary" data-ai-bulk="approve" disabled>批量通过</button><button type="button" class="btn danger" data-ai-bulk="reject" disabled>批量拒绝</button>`;
      box.insertBefore(bar, box.firstChild);
    }
    syncBar();
  }
  function picked() { return $$("[data-ai-rev]:checked").map((x) => x.dataset.aiRev); }
  function syncBar() {
    const n = picked().length, all = $$("[data-ai-rev]").length;
    const nEl = $("#aiRevN"); if (nEl) nEl.textContent = `已选 ${n} 条 / 共 ${all} 条`;
    $$("[data-ai-bulk]").forEach((b) => { b.disabled = !n; });
    const a = $("#aiRevAll"); if (a) { a.checked = n && n === all; a.indeterminate = n > 0 && n < all; }
    $$(".ai-rev").forEach((c) => c.classList.toggle("picked", !!$("[data-ai-rev]:checked", c)));
  }
  document.addEventListener("change", (ev) => {
    if (ev.target.id === "aiRevAll") { $$("[data-ai-rev]").forEach((x) => { x.checked = ev.target.checked; }); syncBar(); }
    else if (ev.target.matches && ev.target.matches("[data-ai-rev]")) syncBar();
  });
  document.addEventListener("click", async (ev) => {
    const b = ev.target.closest && ev.target.closest("[data-ai-bulk]");
    if (!b) return;
    const ids = picked();
    const act = b.dataset.aiBulk;
    if (!ids.length || !confirm(`${act === "approve" ? "通过" : "拒绝"} ${ids.length} 条评价？`)) return;
    try {
      const d = await api("/api/admin/reviews/bulk", { method: "POST", body: JSON.stringify({ ids, action: act }) });
      const bad = Object.keys(d.failed || {}).length;
      toast(`已${act === "approve" ? "通过" : "拒绝"} ${d.done.length} 条` + (bad ? `，${bad} 条失败` : ""));
      $("#btnRefresh")?.click();
    } catch (e) { toast(e.message || "操作失败"); }
  });
  const box = $("#adminReviews");
  if (box) new MutationObserver(() => decorateReviews()).observe(box, { childList: true });
  mountPane();
  decorateReviews();
})();
