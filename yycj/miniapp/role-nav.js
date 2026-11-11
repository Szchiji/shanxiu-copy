(() => {
  if (window.__yycjRoleNav) return;
  window.__yycjRoleNav = true;
  function role() {
    try {
      const u = JSON.parse(localStorage.getItem("yycj_user") || "{}");
      return u.role || localStorage.getItem("yycj_role") || "guest";
    } catch (e) { return localStorage.getItem("yycj_role") || "guest"; }
  }
  function apply() {
    const guest = role() === "guest";
    document.querySelector('[data-nav="publish"]')?.classList.toggle("hidden", guest);
    document.querySelector('[data-nav="fav"]')?.classList.toggle("hidden", !guest);
    document.body.classList.toggle("role-guest", guest);
    document.body.classList.toggle("role-host", !guest);
  }
  async function sync() {
    const tk = localStorage.getItem("yycj_token") || "";
    if (!tk) return false;
    try {
      const r = await fetch("/api/me", { headers: { Authorization: "Bearer " + tk } });
      if (!r.ok) return false;
      const data = await r.json().catch(() => ({}));
      if (data && data.user) {
        localStorage.setItem("yycj_user", JSON.stringify(data.user));
        apply();
      }
      return true;
    } catch (e) { return false; }
  }
  apply();
  setTimeout(apply, 800);
  // First open: yycj_user is not cached yet, so wait for the auth token and read the real role.
  // One request at a time: under a slow network every 500ms tick used to start another /api/me
  // while the previous one was still in flight (14 calls on one open).
  let tries = 0, busy = false;
  const timer = setInterval(async () => {
    if (busy) return;
    busy = true;
    tries += 1;
    try { if ((await sync()) || tries >= 20) clearInterval(timer); } finally { busy = false; }
  }, 500);
  document.addEventListener("click", (ev) => {
    const t = ev.target;
    if (t && t.closest && t.closest("[data-switch], [data-role]")) setTimeout(sync, 1200);
  });
})();
