(() => {
  if (window.__yycjKeyboardFix) return;
  window.__yycjKeyboardFix = true;
  const BG = "#0F2420";
  const root = document.documentElement;
  const body = document.body;
  root.style.background = BG;
  if (body) body.style.background = BG;
  const tg = window.Telegram && window.Telegram.WebApp;
  try {
    tg?.setBackgroundColor?.(BG);
    tg?.setHeaderColor?.(BG);
    tg?.setBottomBarColor?.(BG);
    tg?.expand?.();
  } catch (e) {}
  const isAdmin = !!(body && body.classList.contains("admin-body")) || !!document.getElementById("console");
  function apply() {
    if (isAdmin) {
      if (body) {
        body.style.height = "auto";
        body.style.overflow = "auto";
      }
      return;
    }
    ["app", "boot", "gate"].forEach((id) => {
      const el = document.getElementById(id);
      if (!el) return;
      el.style.transform = "none";
      el.style.height = "auto";
      el.style.maxHeight = "none";
      el.style.overflow = "visible";
    });
    if (body) {
      body.style.height = "auto";
      body.style.overflow = "auto";
    }
  }
  apply();
  window.visualViewport?.addEventListener("resize", apply);
  window.addEventListener("resize", apply);
})();
