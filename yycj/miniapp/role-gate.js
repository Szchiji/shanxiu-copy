(() => {
  if (window.__yycjRoleGate) return;
  window.__yycjRoleGate = true;
  function lockInfo(user) {
    const raw = user && (user.role_locked_until || user.role_set_at);
    if (!raw) return { locked: false, days: 0 };
    const end = user.role_locked_until ? new Date(user.role_locked_until) : new Date(new Date(user.role_set_at).getTime() + 7 * 86400000);
    if (Number.isNaN(end.getTime())) return { locked: false, days: 0 };
    const left = end.getTime() - Date.now();
    if (left <= 0) return { locked: false, days: 0 };
    return { locked: true, days: Math.max(1, Math.ceil(left / 86400000)) };
  }
  async function refresh() {
    try {
      const tk = token();
      if (!tk) return;
      const r = await fetch("/api/me", { headers: { Authorization: "Bearer " + tk } });
      if (!r.ok) return;
      const data = await r.json().catch(() => ({}));
      const user = data.user || {};
      localStorage.setItem("yycj_user", JSON.stringify(user));
      const lock = lockInfo(user);
      document.querySelectorAll("#view-me [data-switch]").forEach((b) => {
        b.disabled = !!lock.locked && !data.is_admin;
      });
    } catch (e) {}
  }
  function token() { return localStorage.getItem("yycj_token") || ""; }
  document.addEventListener("click", (ev) => {
    if (ev.target.closest && ev.target.closest("[data-nav='me']")) refresh();
    const btn = ev.target.closest && ev.target.closest("#view-me [data-switch]");
    if (!btn) return;
    const user = JSON.parse(localStorage.getItem("yycj_user") || "{}");
    const lock = lockInfo(user);
    if (lock.locked) {
      ev.preventDefault();
      ev.stopPropagation();
      alert("角色一周内只能换一次，还剩 " + lock.days + " 天");
    }
  }, true);
})();
