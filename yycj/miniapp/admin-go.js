(() => {
  if (window.__yycjAdminGo) return;
  window.__yycjAdminGo = true;
  const DESK = "./desk.html?v=20261029d";
  let isAdmin = null;
  let pending = null;
  function go(ev) {
    const t = ev.target.closest("#adminEntry, #adminEntry a, a[href*='admin.html'], a[href*='console.html']");
    if (!t) return;
    ev.preventDefault();
    ev.stopPropagation();
    location.href = DESK;
  }
  function paint(admin) {
    const entry = document.getElementById("adminEntry");
    const a = entry && entry.querySelector("a");
    if (a) {
      a.setAttribute("href", DESK);
      a.textContent = "管理后台";
    }
    if (entry && admin) entry.classList.remove("hidden");
  }
  function check() {
    if (pending) return pending;
    pending = (async () => {
      try {
        const token = localStorage.getItem("yycj_token") || "";
        const r = await fetch("/api/me", { headers: { Authorization: "Bearer " + token } });
        const data = await r.json();
        isAdmin = !!data.is_admin;
      } catch (e) {
        isAdmin = false;
      }
      paint(isAdmin);
      return isAdmin;
    })().finally(() => { pending = null; });
    return pending;
  }
  function toast(msg) {
    const el = document.getElementById("toast");
    if (!el) return;
    el.textContent = msg;
    el.classList.remove("hidden");
    el.classList.remove("err");
    setTimeout(() => el.classList.add("hidden"), 3600);
  }
  function openUrl(url) {
    let s = String(url || "").trim();
    if (!s) return false;
    if (/^(t\.me|telegram\.me)\//i.test(s)) s = "https://" + s;
    const tg = window.Telegram && window.Telegram.WebApp;
    try {
      if (/^https?:\/\/(www\.)?(t\.me|telegram\.me)\//i.test(s)) {
        const tme = s.replace(/^http:/i, "https:").replace(/\/\/(www\.)?telegram\.me\//i, "//t.me/");
        if (tg && tg.openTelegramLink) tg.openTelegramLink(tme);
        else if (tg && tg.openLink) tg.openLink(tme);
        else location.href = tme;
      } else if (/^tg:\/\//i.test(s)) {
        location.href = s;
      } else if (tg && tg.openLink) {
        tg.openLink(s);
      } else {
        location.href = s;
      }
      return true;
    } catch (e) {
      return false;
    }
  }
  async function contacts() {
    const have = (window.__yycjHomeData && window.__yycjHomeData.contacts) || (window.__yycjHome && window.__yycjHome.contacts);
    if (have) return have;
    try {
      const data = window.__yycjHomeLite
        ? await window.__yycjHomeLite()
        : await (await fetch("/api/home?limit=1", { headers: { Authorization: "Bearer " + (localStorage.getItem("yycj_token") || "") } })).json();
      return (data && data.contacts) || {};
    } catch (e) {
      return {};
    }
  }
  // Top-left 管理员 link (#topAdmin): for everyone (admins included) open the admin's Telegram chat
  // configured in 运营设置 → 管理员联系链接 (ops_config.admin_contact → /api/home contacts.admin_url).
  // Admins enter the console via the bot's /admin button, not from here.
  async function topAdmin(ev) {
    if (!ev.target.closest("#topAdmin")) return;
    ev.preventDefault();
    ev.stopPropagation();
    await contactAdmin();
  }
  async function contactAdmin() {
    const c = await contacts();
    if (!openUrl(c.admin_url)) toast("后台还没填管理员联系");
  }
  window.__yycjContactAdmin = contactAdmin;
  document.addEventListener("click", go, true);
  document.addEventListener("click", topAdmin, true);
  document.addEventListener("click", (ev) => {
    if (ev.target.closest("[data-nav='me']")) check();
  });
  setTimeout(check, 900);
})();
