(() => {
  if (window.__yycjAdminDiff) return;
  window.__yycjAdminDiff = true;
  const token = () => localStorage.getItem("yycj_token") || "";
  function esc(t) {
    return String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':'&quot;',"'":"&#39;" }[c]));
  }
  function pack(d) {
    const out = {
      "称呼": d.title || "",
      "城市": d.city || "",
      "价位": d.price_text || "",
      "区域": d.district || "",
      "大致位置": d.approx_label || "",
      "标签": Array.isArray(d.tags) ? d.tags.join(" ") : (d.tags || ""),
      "简介": d.description || "",
    };
    const extras = d.extras && typeof d.extras === "object" ? d.extras : {};
    Object.keys(extras).forEach((k) => {
      if (!String(k).startsWith("_")) out[k] = String(extras[k] || "");
    });
    return out;
  }
  function diffHtml(oldPack, neu) {
    const keys = Array.from(new Set([...Object.keys(oldPack), ...Object.keys(neu)]));
    const rows = keys.map((k) => {
      const a = String(oldPack[k] || "");
      const b = String(neu[k] || "");
      if (a === b) return "";
      return `<div class="ex-line"><span class="ex-k">${esc(k)}</span><span class="ex-v"><s>${esc(a) || "—"}</s> → <b>${esc(b) || "—"}</b></span></div>`;
    }).filter(Boolean);
    if (!rows.length) return "<p class='muted'>字段没变，可能只改了媒体</p>";
    return rows.join("");
  }
  async function decorate() {
    const box = document.getElementById("adminPosts");
    if (!box) return;
    const cards = [...box.querySelectorAll(".card")];
    if (!cards.length) return;
    let items = [];
    try {
      const r = await fetch("/api/admin/posts/pending", { headers: { Authorization: "Bearer " + token() } });
      items = ((await r.json()).items) || [];
    } catch (e) { return; }
    for (let i = 0; i < cards.length && i < items.length; i += 1) {
      const p = items[i];
      const d = p.lamp_data || {};
      const editId = d.edit_lamp_id;
      if (cards[i].querySelector(".edit-diff")) continue;
      const badge = document.createElement("div");
      badge.className = "muted edit-diff";
      if (!editId) {
        badge.innerHTML = "<strong>新上架</strong>";
        cards[i].insertBefore(badge, cards[i].querySelector(".row"));
        continue;
      }
      badge.innerHTML = "对比加载中…";
      cards[i].insertBefore(badge, cards[i].querySelector(".row"));
      try {
        const r = await fetch("/api/lamps/" + encodeURIComponent(editId), { headers: { Authorization: "Bearer " + token() } });
        const data = await r.json();
        const lamp = data.lamp || data;
        badge.innerHTML = `<strong>改稿对比</strong>${diffHtml(pack(lamp), pack(d))}`;
      } catch (e) {
        badge.textContent = "改稿 · 无法读原资料";
      }
    }
  }
  const box = document.getElementById("adminPosts");
  if (box) new MutationObserver(() => setTimeout(decorate, 50)).observe(box, { childList: true });
  setTimeout(decorate, 1200);
})();
