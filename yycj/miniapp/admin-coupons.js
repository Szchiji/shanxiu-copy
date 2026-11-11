(() => {
  // 后台「优惠券」：按当前城市（顶部城市切换）列出老师发的优惠（一批一行，列出每一项），可整批停用。运营只看到自己负责的城市。
  if (window.__yycjAdminCoupons) return;
  window.__yycjAdminCoupons = true;
  const $ = (s, r = document) => r.querySelector(s);
  const token = () => localStorage.getItem("yycj_token") || "";
  const city = () => localStorage.getItem("yycj_admin_city") || "";
  const esc = (t) => String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const ST = { active: "进行中", expired: "已过期", sold_out: "已领完", ended: "老师已结束", stopped: "后台停用" };
  let all = false;
  async function api(path, opts = {}) {
    const r = await fetch(path, { ...opts, headers: { "Content-Type": "application/json", Authorization: "Bearer " + token() }, cache: "no-store" });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.detail || r.statusText);
    return d;
  }
  function card() {
    const pane = $("#pane-listings"); if (!pane) return null;
    let c = $("#cpnAdmin");
    if (!c) { c = document.createElement("div"); c.id = "cpnAdmin"; c.className = "ops-card cpn-admin"; pane.appendChild(c); }
    return c;
  }
  async function load() {
    const c = card(); if (!c) return;
    const p = new URLSearchParams({ status: all ? "all" : "active" }); if (city()) p.set("city", city());
    let items = [];
    try { items = (await api("/api/admin/coupons?" + p)).items || []; }
    catch (e) { c.innerHTML = `<h4>优惠券</h4><p class="muted">${esc(e.message)}</p>`; return; }
    // one row per batch; the 优惠 cell lists each item with its own 已领 / 数量
    const itemsCell = (x) => (x.items && x.items.length > 1)
      ? x.items.map((i) => `<div class="cpn-a-it">${esc(i.text)} <span class="muted">${i.claimed}${i.quota ? "/" + i.quota : ""}${i.status === "sold_out" ? " 已领完" : ""}</span></div>`).join("")
      : esc(x.text);
    const rows = items.map((x) => `<tr><td>${esc(x.city)}</td><td>${esc(x.title)}<div class="muted">id ${esc(x.user_id)}</div></td><td class="cpn-a-text">${itemsCell(x)}</td>
      <td>${esc(x.live || x.status === "sold_out" ? x.left : "—")}</td><td>${x.claimed}${x.quota ? "/" + x.quota : ""}</td><td>${esc(ST[x.status] || x.status)}</td>
      <td>${x.status === "active" || x.status === "sold_out" ? `<button class="btn danger" type="button" data-cpn-stop="${esc(x.coupon_id)}">停用</button>` : ""}</td></tr>`).join("");
    c.innerHTML = `<h4>优惠券 <small class="muted">${esc(city() || "全部城市")}</small></h4>
      <p class="muted">老师在「我的 · 我的优惠」自己发，发出即显示。不合适的可以停用（已领的券码一并失效）。</p>
      <label class="cpn-a-all"><input type="checkbox" id="cpnAll"${all ? " checked" : ""}/> 显示已结束 / 已停用</label>
      ${rows ? `<div class="cpn-a-wrap"><table class="cpn-a-table"><thead><tr><th>城市</th><th>老师</th><th>优惠</th><th>剩余时间</th><th>已领</th><th>状态</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>` : `<p class="muted">${all ? "没有记录" : "现在没有进行中的优惠"}</p>`}`;
  }
  document.addEventListener("click", async (ev) => {
    const b = ev.target.closest && ev.target.closest("[data-cpn-stop]");
    if (b) {
      if (!confirm("停用这批优惠？（这一批的所有项一起停用）")) return;
      try { await api(`/api/admin/coupons/${encodeURIComponent(b.getAttribute("data-cpn-stop"))}/stop`, { method: "POST" }); load(); } catch (e) { alert(e.message); }
      return;
    }
    if (ev.target.id === "cpnAll") { all = ev.target.checked; load(); return; }
    if (ev.target.closest && ev.target.closest('#adminSide [data-pane="listings"], #cityBar [data-city]')) setTimeout(load, 500);
  });
  document.addEventListener('workspace:scope',load);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => setTimeout(load, 1500)); else setTimeout(load, 1500);
})();
