(() => {
  if (window.__yycjFav) return;
  window.__yycjFav = true;
  const token = () => localStorage.getItem("yycj_token") || "";
  let ids = new Set();
  let items = [];
  if (!document.getElementById("yycj-fav-css")) {
    const st = document.createElement("style");
    st.id = "yycj-fav-css";
    st.textContent = `.fav-btn.on,#detailFavTop.on{color:#ff6b8a!important;} .fav-btn{color:#fff;}`;
    document.head.appendChild(st);
  }
  function isGuest() {
    try { return (JSON.parse(localStorage.getItem("yycj_user") || "{}").role || "guest") === "guest"; }
    catch (e) { return true; }
  }
  function cover(item) {
    const s = window.yycjCoverOf ? window.yycjCoverOf(item) : "";
    return window.yycjThumb ? window.yycjThumb(s, 480) : s;
  }
  function showView(id) {
    document.querySelectorAll(".view").forEach((v) => v.classList.add("hidden"));
    document.getElementById(id)?.classList.remove("hidden");
  }
  function esc(t) {
    return String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':'&quot;',"'":"&#39;" }[c]));
  }
  function cardHtml(x) {
    const img = cover(x) || x.cover_url || "";
    const loc = [x.city, x.district].filter(Boolean).join("·");
    const tag = (x.tags && x.tags[0]) || "";
    const n = x.media_count || (x.media && x.media.length) || (x.photos && x.photos.length) || 0;
    return "<div class=\"feed-card compact cover-card\" data-id=\"" + esc(x.lamp_id) + "\"><div class=\"cover-wrap\">" +
      (img ? "<img class=\"thumb\" src=\"" + esc(img) + "\" alt=\"\" loading=\"lazy\" decoding=\"async\" />" : "<div class=\"thumb ph\"></div>") +
      (loc ? "<span class=\"badge-loc\">" + esc(loc) + "</span>" : "") +
      (tag ? "<span class=\"badge-tag\">" + esc(tag) + "</span>" : "") +
      (n ? "<span class=\"badge-n\">" + esc(n) + "图</span>" : "") +
      "</div><div class=\"body\"><h3>" + esc(x.title || "") + "</h3><div class=\"muted price\">" + esc(x.price_text || "") +
      "</div><button class=\"btn unfav\" type=\"button\" data-unfav=\"" + esc(x.lamp_id) + "\">取消收藏</button></div></div>";
  }
  function paintHearts() {
    document.querySelectorAll("[data-fav]").forEach((b) => {
      const on = ids.has(b.getAttribute("data-fav"));
      b.classList.toggle("on", on);
      b.textContent = on ? "♥" : "♡";
    });
  }
  function paintList() {
    const box = document.getElementById("favList");
    if (box) box.innerHTML = items.map(cardHtml).join("") || "<p class='muted'>还没有收藏</p>";
  }
  async function load() {
    if (!token() || !isGuest()) return;
    try {
      const r = await fetch("/api/me/favorites", { headers: { Authorization: "Bearer " + token() } });
      const data = await r.json();
      ids = new Set(data.ids || []);
      items = data.items || [];
      paintHearts();
      paintList();
    } catch (e) {}
  }
  async function toggle(id) {
    const r = await fetch("/api/favorites/" + encodeURIComponent(id), {
      method: "POST",
      headers: { Authorization: "Bearer " + token() },
    });
    const data = await r.json();
    if (data.favorited) ids.add(id);
    else {
      ids.delete(id);
      items = items.filter((x) => x.lamp_id !== id);
    }
    paintHearts();
    paintList();
    try { document.dispatchEvent(new CustomEvent("yycj:fav")); } catch (e) {}
  }
  window.__yycjPaintFav = paintHearts;
  window.__yycjFavCount = () => ids.size;
  window.__yycjFavLoad = load;
  // 收藏只在详情页：先拿一次收藏列表，让详情页的「收藏」一打开就是对的状态（♥ / ♡）
  (function boot(n) { if (token()) load().then(() => { try { document.dispatchEvent(new CustomEvent("yycj:fav")); } catch (e) {} }); else if (n < 20) setTimeout(() => boot(n + 1), 500); })(0);
  document.addEventListener("click", async (ev) => {
    if (ev.target.closest("[data-nav='fav']")) {
      ev.preventDefault();
      ev.stopPropagation();
      window.__yycjBack = "fav";
      showView("view-fav");
      document.querySelectorAll(".nav-item").forEach((n) => n.classList.toggle("active", n.dataset.nav === "fav"));
      load();
      return;
    }
    if (ev.target.closest("[data-nav='home']")) window.__yycjBack = "home";
    const un = ev.target.closest("[data-unfav]");
    if (un) {
      ev.preventDefault();
      ev.stopPropagation();
      try { await toggle(un.getAttribute("data-unfav")); } catch (e) {}
      return;
    }
    const heart = ev.target.closest("[data-fav]");
    if (heart) {
      ev.preventDefault();
      ev.stopPropagation();
      if (!isGuest()) return;
      try { await toggle(heart.getAttribute("data-fav")); } catch (e) {}
    }
  }, true);
  document.getElementById("btnBackHome")?.addEventListener("click", (ev) => {
    if (window.__yycjBack === "fav") {
      ev.preventDefault();
      ev.stopPropagation();
      showView("view-fav");
      load();
    } else {
      window.__yycjBack = "home";
    }
  }, true);
})();
