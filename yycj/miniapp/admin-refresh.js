(() => {
  if (window.__yycjAdminRefresh) return;
  window.__yycjAdminRefresh = true;
  const token = () => localStorage.getItem("yycj_token") || "";
  function esc(t) {
    return String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':'&quot;',"'":"&#39;" }[c]));
  }
  function zh(s) {
    return ({ active: "已上架", hidden: "已下架", pending: "待审", rejected: "已拒" })[s] || s || "-";
  }
  function paint(items) {
    const box = document.getElementById("adminListings");
    if (!box) return;
    window.__yycjListingCache = items || [];
    box.innerHTML = (items || []).map((x) => `
      <div class="pick-item">
        <div class="meta">
          <strong>${esc(x.title || "")}</strong>
          <div class="muted">${esc(x.city || "")} · ${zh(x.status)} · ${x.expires_at ? ("到期 " + String(x.expires_at).slice(0,10)) : "无截止"} · ${x.updated_at ? String(x.updated_at).slice(0,16) : ""}</div>
        </div>
        ${window.__yycjEditUI?.html(x.edit_state,"admin",{card:true})||""}
        <div class="row">
          <button class="btn primary" type="button" data-fill-proxy="${esc(x.lamp_id)}">代改</button>
          <button class="btn" data-list="renew" data-id="${esc(x.lamp_id)}">续期</button>
          <button class="btn" data-list="relist" data-id="${esc(x.lamp_id)}">重新上架</button>
          <button class="btn danger" data-list="unlist" data-id="${esc(x.lamp_id)}">下架</button>
          <button class="btn danger" data-list="delete" data-id="${esc(x.lamp_id)}" data-title="${esc(x.title || "")}">删除</button>
        </div>
      </div>`).join("") || "<p class='muted'>暂无资料</p>";
  }
  async function loadListings() {
    try {
      const r = await fetch("/api/admin/listings?_=" + Date.now(), {
        headers: { Authorization: "Bearer " + token() },
        cache: "no-store",
      });
      const data = await r.json();
      paint(data.items || []);
    } catch (e) {
      const box = document.getElementById("adminListings");
      if (box) box.innerHTML = "<p class='muted'>列表加载失败</p>";
    }
  }
  window.yycjLoadListings = loadListings;
  document.getElementById("btnRefresh")?.addEventListener("click", (ev) => {
    ev.preventDefault();
    loadListings();
  });
  document.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("[data-fill-proxy]");
    if (!btn) return;
    ev.preventDefault();
    const id = btn.getAttribute("data-fill-proxy");
    let lamp = (window.__yycjListingCache || []).find((x) => x.lamp_id === id) || { lamp_id: id };
    try {
      const r = await fetch("/api/lamps/" + encodeURIComponent(id) + "?_=" + Date.now(), {
        headers: { Authorization: "Bearer " + token() },
        cache: "no-store",
      });
      const data = await r.json();
      lamp = data.lamp || data.item || data;
    } catch (e) {}
    if (window.yycjFillProxy) window.yycjFillProxy(lamp);
    document.querySelector("#adminSide [data-pane='listings']")?.click();
  });
  setTimeout(loadListings, 700);
})();
