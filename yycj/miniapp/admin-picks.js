(() => {
  if (window.__yycjAdminPicks) return;
  window.__yycjAdminPicks = true;
  const $ = (s) => document.querySelector(s);
  const token = () => localStorage.getItem("yycj_token") || "";
  if (!document.getElementById("yycj-pick-css")) {
    const st = document.createElement("style");
    st.id = "yycj-pick-css";
    st.textContent = `
.pick-head{display:flex;align-items:center;justify-content:space-between;margin:2px 0 8px;}
.pick-head h4{margin:0;font-size:13px;}
.pick-count{font-size:12px;opacity:.6;}
.pick-now{display:grid;grid-template-columns:52px 1fr auto;gap:10px;align-items:center;padding:8px 10px;border:1px solid #2a3352;border-radius:14px;margin:0 0 8px;background:#151b2f;}
.admin-thumb{width:52px;height:52px;object-fit:cover;border-radius:10px;background:#0d1326;display:block;}
.admin-thumb.ph{background:#1a2140;}
.pick-now .meta,.pick-item .meta{min-width:0;}
.pick-now .meta strong,.pick-item .meta strong{display:block;font-size:14px;line-height:1.25;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
.pick-now .meta .muted,.pick-item .meta .muted{margin-top:2px;font-size:11px;line-height:1.3;}
.pick-now .pick-actions{display:flex;align-items:center;}
.pick-item{display:grid;grid-template-columns:minmax(0,1fr) 64px auto;gap:8px;align-items:center;padding:8px 10px;border:1px solid #2a3352;border-radius:14px;margin:0 0 8px;background:#151b2f;}
.pick-item.listing-item{grid-template-columns:minmax(0,1fr) auto;}
.pick-item .hrs{width:64px;height:32px;margin:0;padding:0 6px;font-size:12px;grid-column:auto;}
.pick-item .pick-actions,.pick-item .row{display:flex;gap:6px;flex-wrap:nowrap;margin:0;}
.pick-item .btn{white-space:nowrap;padding:6px 10px;min-height:32px;}
#pane-pin .ops-card,#pane-carousel .ops-card,#pane-listings .ops-card{margin-top:8px;}
#btnSide{display:none!important;}`;
    document.head.appendChild(st);
  }
  function esc(t) {
    return String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':'&quot;',"'":"&#39;" }[c]));
  }
  function note(msg, kind) {
    if (window.yycjToast) window.yycjToast(msg, kind || "ok");
    else alert(msg);
  }
  function mediaSrc(item) {
    const lamp = (item && item.lamp) || item || {};
    for (const m of lamp.media || []) {
      if ((m && m.type) === "video") continue;
      const raw = String((m && (m.preview_url || m.thumb_url || m.file_id || m.url)) || "").trim();
      if (!raw) continue;
      if (raw.startsWith("http") || raw.startsWith("/")) return raw;
      return "/api/media/file/" + encodeURIComponent(raw);
    }
    for (const p of lamp.photos || item.photos || []) {
      const raw = String(p || "");
      if (!raw || raw.startsWith("BAAC")) continue;
      if (raw.startsWith("http") || raw.startsWith("/")) return raw;
      return "/api/media/file/" + encodeURIComponent(raw);
    }
    return "";
  }
  async function api(path, opt) {
    const r = await fetch(path, Object.assign({
      headers: { Authorization: "Bearer " + token(), "Content-Type": "application/json" },
    }, opt || {}));
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || r.statusText || "请求失败");
    return data;
  }
  function card(item, extraMuted, btnHtml) {
    const src = mediaSrc(item);
    const loc = [item.city, item.district].filter(Boolean).join(" · ");
    return `<div class="pick-now">
      ${src ? `<img class="admin-thumb" src="${esc(src)}" alt="" />` : `<div class="admin-thumb ph"></div>`}
      <div class="meta">
        <strong>${esc(item.title || "未命名")}</strong>
        <div class="muted">${esc(loc || item.city || "")}${extraMuted ? " · " + extraMuted : ""}</div>
      </div>
      <div class="pick-actions">${btnHtml}</div>
    </div>`;
  }
  async function paintCurrent() {
    const home = await api("/api/admin/homepage");
    const lamps = {};
    (home.approved_lamps || []).forEach((x) => { lamps[x.lamp_id] = x; });
    (home.pins || []).forEach((p) => { if (p.lamp) lamps[p.lamp.lamp_id || p.lamp_id] = p.lamp; });
    const feed = home.feed_pins || [];
    if ($("#adminFeedPins")) {
      const rows = feed.map((fp) => {
        const item = Object.assign({}, lamps[fp.lamp_id] || {}, fp);
        return card(item, "置顶中", `<button class="btn danger" type="button" data-pin-act="feed-unpin" data-lamp="${esc(fp.lamp_id)}">取消置顶</button>`);
      }).join("");
      $("#adminFeedPins").innerHTML = `<div class="pick-head"><h4>当前置顶</h4><span class="pick-count">${feed.length} 条</span></div>` +
        (rows || "<p class='muted'>还没有卡片置顶</p>");
    }
    const pins = home.pins || home.carousel_pins || [];
    if ($("#adminPins")) {
      const rows = pins.map((p, i) => {
        const lamp = p.lamp || lamps[p.lamp_id] || {};
        const item = Object.assign({}, lamp, { lamp_id: lamp.lamp_id || p.lamp_id, title: lamp.title || p.title });
        const exp = p.expires_at ? ("到期 " + String(p.expires_at).slice(0, 16).replace("T", " ")) : "长期";
        return card(item, `#${p.sort_order || i + 1} · ${exp}`,
          `<button class="btn danger" type="button" data-pin-act="pin-del" data-id="${p.id || ""}" data-lamp="${esc(p.lamp_id || lamp.lamp_id || "")}">移除</button>`);
      }).join("");
      $("#adminPins").innerHTML = `<div class="pick-head"><h4>当前轮播</h4><span class="pick-count">${pins.length} 条</span></div>` +
        (rows || "<p class='muted'>还没有轮播</p>");
    }
  }
  document.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("[data-pin-act]");
    if (!btn) return;
    ev.preventDefault();
    ev.stopPropagation();
    const act = btn.getAttribute("data-pin-act");
    const pinId = btn.getAttribute("data-id") || "";
    const lampId = btn.getAttribute("data-lamp") || "";
    if (window.yycjConfirm) {
      const ok = await window.yycjConfirm(act === "feed-unpin" ? "取消这张卡片置顶？" : "从轮播里移除？");
      if (!ok) return;
    }
    try {
      btn.disabled = true;
      if (act === "feed-unpin") {
        await api("/api/admin/homepage/feed-pins", {
          method: "POST",
          body: JSON.stringify({ lamp_id: lampId, pinned: false }),
        });
        note("已取消置顶");
      } else {
        await api("/api/admin/homepage/pins/remove", {
          method: "POST",
          body: JSON.stringify({
            pin_id: pinId && /^\d+$/.test(pinId) ? parseInt(pinId, 10) : null,
            lamp_id: lampId || null,
          }),
        });
        note("已移出轮播");
      }
      await paintCurrent();
    } catch (e) {
      note(e.message || String(e), "err");
    } finally {
      btn.disabled = false;
    }
  }, true);
  document.getElementById("btnRefresh")?.addEventListener("click", () => setTimeout(() => paintCurrent().catch(() => {}), 400));
  setTimeout(() => paintCurrent().catch(() => {}), 1200);
})();
