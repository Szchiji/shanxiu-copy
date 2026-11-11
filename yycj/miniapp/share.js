(() => {
  if (window.__yycjShare) return;
  window.__yycjShare = true;
  function botUser() {
    return (window.__yycjBot || localStorage.getItem("yycj_bot") || "").replace(/^@/, "");
  }
  function startLink(lampId) {
    const bot = botUser();
    const payload = "s_" + String(lampId || "").replace(/-/g, "");
    return bot ? ("https://t.me/" + bot + "?start=" + payload) : location.href;
  }
  function toast(msg) {
    let el = document.getElementById("yycjToast");
    if (!el) {
      el = document.createElement("div");
      el.id = "yycjToast";
      el.style.cssText = "position:fixed;left:50%;bottom:80px;transform:translateX(-50%);background:#1b2436;color:#fff;padding:8px 14px;border-radius:999px;z-index:99;font-size:13px";
      document.body.appendChild(el);
    }
    el.textContent = msg;
    el.style.display = "block";
    setTimeout(() => { el.style.display = "none"; }, 1800);
  }
  async function copy(text) {
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
        return true;
      }
    } catch (e) {}
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.left = "-999px";
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand("copy");
    ta.remove();
    return ok;
  }
  document.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("[data-share], #detailShare");
    if (!btn) return;
    ev.preventDefault();
    ev.stopPropagation();
    const card = btn.closest("[data-id], #detail") || document.getElementById("detail");
    const lampId = btn.getAttribute("data-share") || (card && card.getAttribute && (card.getAttribute("data-lamp") || card.getAttribute("data-id"))) || "";
    const link = startLink(lampId);
    const ok = await copy(link);
    toast(ok ? "链接已复制，去粘贴给好友" : link);
  }, true);
  async function rememberBot() {
    try {
      const data = window.__yycjHomeLite
        ? await window.__yycjHomeLite()
        : await (await fetch("/api/home?limit=1", { headers: { Authorization: "Bearer " + (localStorage.getItem("yycj_token") || "") } })).json();
      const name = ((data.contacts || {}).bot_username) || "";
      if (name) {
        window.__yycjBot = String(name).replace(/^@/, "");
        localStorage.setItem("yycj_bot", window.__yycjBot);
      }
    } catch (e) {}
  }
  rememberBot();
})();
