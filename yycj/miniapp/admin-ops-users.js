(() => {
  if (window.__yycjOpsUsers) return;
  window.__yycjOpsUsers = true;
  const token = () => localStorage.getItem("yycj_token") || "";
  function box() {
    let el = document.getElementById("opsAdminBox");
    if (el) return el;
    const pane = document.getElementById("pane-users");
    if (!pane) return null;
    el = document.createElement("div");
    el.id = "opsAdminBox";
    el.className = "ops-card";
    el.innerHTML = `<h4>运营管理员</h4><p class="muted" id="opsAdminHint">一行一个电报 ID。只有环境变量里的超管能改这份名单。</p><textarea id="opsAdminIds" rows="4" placeholder="123456789"></textarea><button class="btn primary" type="button" id="btnSaveOps">保存运营名单</button>`;
    pane.appendChild(el);
    return el;
  }
  function lockVenue(on) {
    const save = document.getElementById("btnOpsSave");
    if (save) {
      save.disabled = !on;
      save.textContent = on ? "保存全部设置" : "仅超管可改站点设置";
    }
    ["listingFieldsEditor", "opsBroadcastTpl", "opsChannel", "opsMediaChannel"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.readOnly = !on;
    });
  }
  async function load() {
    const wrap = box();
    if (!wrap) return;
    try {
      const r = await fetch("/api/admin/operators", { headers: { Authorization: "Bearer " + token() } });
      const data = await r.json();
      const ta = document.getElementById("opsAdminIds");
      if (ta) ta.value = (data.extra_admin_ids || []).join("\n");
      const hint = document.getElementById("opsAdminHint");
      const btn = document.getElementById("btnSaveOps");
      lockVenue(!!data.is_super);
      if (!data.is_super) {
        if (hint) hint.textContent = "当前是运营号，可审核 / 代改 / 续期，不能改站点设置。";
        if (ta) ta.disabled = true;
        if (btn) btn.disabled = true;
      }
    } catch (e) {}
  }
  document.addEventListener("click", async (ev) => {
    if (!(ev.target && ev.target.id === "btnSaveOps")) return;
    const raw = (document.getElementById("opsAdminIds")?.value || "").split(/\s+/).map((x) => parseInt(x, 10)).filter((n) => n > 0);
    try {
      const r = await fetch("/api/admin/operators", {
        method: "POST",
        headers: { Authorization: "Bearer " + token(), "Content-Type": "application/json" },
        body: JSON.stringify({ user_ids: raw }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || "保存失败");
      alert("已保存运营 " + (data.extra_admin_ids || []).length + " 人，新加的会收到机器人通知");
    } catch (e) { alert(e.message || String(e)); }
  });
  setTimeout(load, 1200);
})();
