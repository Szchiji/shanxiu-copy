(() => {
  const $ = (s) => document.querySelector(s);
  const token = () => localStorage.getItem("yycj_token") || "";
  const ST = { active: "已上架", hidden: "已下架", pending: "待审", rejected: "已拒", gray: "灰色" };
  const ROLE = { teacher: "老师", guest: "客人", merchant: "商家" };
  let listingCache = [];
  async function api(path, opt={}) {
    const r = await fetch(path, Object.assign({
      headers: { Authorization: "Bearer " + token(), "Content-Type": "application/json" },
    }, opt));
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || r.statusText);
    return data;
  }
  function esc(t) {
    return String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':'&quot;',"'":"&#39;" }[c]));
  }
  function zhStatus(s) { return ST[s] || s || "-"; }
  function zhRole(s) { return ROLE[s] || s || "-"; }
  function matchQ(item, q) {
    if (!q) return true;
    const blob = `${item.title || ""} ${item.city || ""} ${item.lamp_id || ""} ${zhStatus(item.status)}`.toLowerCase();
    return blob.includes(q.toLowerCase());
  }

  document.getElementById("adminSide")?.addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-pane]");
    if (!btn) return;
    document.querySelectorAll("#adminSide [data-pane]").forEach((b) => b.classList.toggle("active", b === btn));
    document.querySelectorAll(".admin-pane").forEach((p) => p.classList.toggle("active", p.id === "pane-" + btn.dataset.pane));
  });

  // 管理员联系链接: "@name" / "name" / "t.me/name" -> https://t.me/name; returns null when invalid.
  function normContact(raw) {
    let s = String(raw || "").trim();
    if (!s) return "";
    if (/^tg:\/\//i.test(s)) return s;
    if (/^(t\.me|telegram\.me)\//i.test(s)) s = "https://" + s;
    if (/^https?:\/\//i.test(s)) return /^https?:\/\/(t\.me|telegram\.me)\/\S+$/i.test(s) ? s : null;
    if (s.startsWith("@")) s = s.slice(1);
    if (/^-?\d+$/.test(s)) return "tg://user?id=" + s;
    return /^[A-Za-z0-9_]{3,64}$/.test(s) ? "https://t.me/" + s : null;
  }
  document.addEventListener("click", async (ev) => {
    if (ev.target && ev.target.id === "btnOpsSave") {
      const contactEl = $("#opsAdminContact");
      const contact = normContact(contactEl ? contactEl.value : "");
      if (contact === null) {
        ev.preventDefault();
        ev.stopImmediatePropagation();
        alert("管理员联系链接格式不对，示例 https://t.me/用户名 或 @用户名");
        return;
      }
      if (contactEl) contactEl.value = contact;
      try {
        const chats = (($("#opsChats") && $("#opsChats").value) || "").split("\n").map((line) => {
          const p = line.split("|").map((x) => x.trim());
          if (!p[0]) return null;
          return { chat_id: p[0], title: p[1] || "", url: p[2] || "", required: true };
        }).filter(Boolean);
        await api("/api/admin/settings/extra", {
          method: "POST",
          body: JSON.stringify({
            listing_days: parseInt($("#opsListingDays")?.value, 10) || 30,
            carousel_interval_sec: parseInt($("#opsCarouselSec")?.value, 10) || 4,
            admin_contact: contact,
            show_bot_link: $("#opsShowBot") ? $("#opsShowBot").checked : true,
            show_admin_link: $("#opsShowAdmin") ? $("#opsShowAdmin").checked : true,
            required_chats: chats,
            chat_cta_label: $("#opsCta")?.value || "想聊聊",
            home_feed_page_size: parseInt($("#opsPageSize")?.value, 10) || 3,
          }),
        });
      } catch (e) { console.warn(e); }
    }
  }, true);

  $("#btnSyncBot")?.addEventListener("click", async () => {
    try {
      const r = await api("/api/admin/bot-identity/refresh", { method: "POST", body: "{}" });
      alert("已同步 @" + ((r.identity || {}).username || ""));
    } catch (e) { alert(e.message || String(e)); }
  });

  function listingActions(x) {
    const id = esc(x.lamp_id);
    const on = x.status === "active";
    const proxy = `<button class="btn" type="button" data-fill-proxy="${id}">修改资料</button>`;
    const renew = `<button class="btn" type="button" data-list="renew" data-id="${id}">续期</button>`;
    const relist = `<button class="btn${on ? "" : " primary"}" type="button" data-list="relist" data-id="${id}">重新上架</button>`;
    const unlist = on ? `<button class="btn danger" type="button" data-list="unlist" data-id="${id}">下架</button>` : "";
    const del = `<button class="btn danger" type="button" data-list="delete" data-id="${id}" data-title="${esc(x.title || "")}">删除</button>`;
    // Active cards keep 重新上架 in 更多 so it is not beside 下架 / 续期 (easy mis-tap). Hidden cards show it as a primary action. Handlers stay on data-list / data-fill-proxy.
    const history = `<button class="btn" type="button" data-history="${id}" data-history-scope="admin">修改记录</button>`;
    const extra = (on ? relist : "") + history + del;
    return `<div class="listing-actions">${proxy}${renew}${on ? unlist : relist}<details class="listing-more"><summary>更多</summary><div class="listing-more-menu">${extra}</div></details></div>`;
  }
  function renderListingCards(items) {
    const status=window.yycjWorkspace?.listingStatus;if(status)items=(items||[]).filter(x=>x.status===status);
    const box = $("#adminListings");
    if (!box) return;
    box.innerHTML = (items || []).map((x) => `
      <div class="pick-item listing-card${x.status === "active" ? " is-active" : " is-hidden"}">
        <div class="meta">
          <strong>${esc(x.title || "")}</strong>
          <div class="muted">${esc(x.city || "")} · ${zhStatus(x.status)} · ${x.expires_at ? ("到期 " + String(x.expires_at).slice(0,10)) : "无截止"}</div>
        </div>
        ${window.__yycjEditUI?.html(x.edit_state,"admin",{card:true})||""}${window.__yycjEditUI?.html(x.relist_state,"admin",{card:true})||""}
        ${listingActions(x)}
      </div>`).join("") || "<p class='muted'>暂无资料</p>";
  }
  function renderPick(boxId, items, action) {
    const box = $(boxId);
    if (!box) return;
    box.innerHTML = (items || []).map((x) => `
      <div class="pick-item">
        <div class="meta">
          <strong>${esc(x.title || "")}</strong>
          <div class="muted">${esc(x.city || "")} · ${zhStatus(x.status)}</div>
        </div>
        <input class="hrs" type="number" min="0" placeholder="小时，空=长久" style="width:88px" />
        <button class="btn primary" data-pick="${action}" data-id="${esc(x.lamp_id)}">${action === "pin" ? "置顶" : "上轮播"}</button>
      </div>`).join("") || "<p class='muted'>无匹配</p>";
  }
  const workspaceScope=()=>window.yycjWorkspace?window.yycjWorkspace.mode+":"+window.yycjWorkspace.city:"legacy";
  async function loadListings() {
    const scope=workspaceScope();
    try {
      const listed = await api("/api/admin/listings");
      if(scope!==workspaceScope())return;
      listingCache = listed.items || [];window.__yycjListings=listingCache;
      renderListingCards(listingCache);
      renderPick("#feedPinResults", listingCache.filter((x) => x.status === "active"), "pin");
      renderPick("#pinResults", listingCache.filter((x) => x.status === "active"), "carousel");
    } catch (e) {
      if(scope!==workspaceScope())return;
      if ($("#adminListings")) $("#adminListings").innerHTML = "<p class='muted'>列表加载失败</p>";
    }
  }
  window.yycjAdminListings={reload:loadListings,render:renderListingCards,clear(){listingCache=[];window.__yycjListings=[];for(const id of ["adminListings","feedPinResults","pinResults"]){const box=document.getElementById(id);if(box)box.innerHTML='<p class="muted">正在读取当前范围…</p>';}}};
  $("#btnListingSearch")?.addEventListener("click", () => {
    renderListingCards(listingCache.filter((x) => matchQ(x, ($("#listingQ")?.value || "").trim())));
  });
  $("#btnFeedPinSearch")?.addEventListener("click", () => {
    renderPick("#feedPinResults", listingCache.filter((x) => x.status === "active" && matchQ(x, ($("#feedPinQ")?.value || "").trim())), "pin");
  });
  $("#btnPinSearch")?.addEventListener("click", () => {
    renderPick("#pinResults", listingCache.filter((x) => x.status === "active" && matchQ(x, ($("#pinQ")?.value || "").trim())), "carousel");
  });
  function rowHours(btn) {
    const input = btn.parentElement && btn.parentElement.querySelector(".hrs");
    const raw = input && input.value;
    if (raw === "" || raw == null) return null;
    const n = parseInt(raw, 10);
    return Number.isFinite(n) ? n : null;
  }
  $("#feedPinResults")?.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("[data-pick='pin']");
    if (!btn) return;
    try {
      await api("/api/admin/homepage/feed-pin", {
        method: "POST",
        body: JSON.stringify({ lamp_id: btn.getAttribute("data-id"), pinned: true, expires_hours: rowHours(btn) }),
      });
      document.getElementById("btnRefresh")?.click();
      await loadListings();
    } catch (e) { alert(e.message || String(e)); }
  });
  $("#pinResults")?.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("[data-pick='carousel']");
    if (!btn) return;
    try {
      await api("/api/admin/homepage/pins", {
        method: "POST",
        body: JSON.stringify({ lamp_id: btn.getAttribute("data-id"), sort_order: 0, expires_hours: rowHours(btn) }),
      });
      document.getElementById("btnRefresh")?.click();
      await loadListings();
    } catch (e) { alert(e.message || String(e)); }
  });
  $("#adminListings")?.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("[data-list]");
    if (!btn) return;
    const id = btn.getAttribute("data-id");
    const act = btn.getAttribute("data-list");
    try {
      if (act === "renew") {
        // The capture handler in admin-list.js owns renewal and its stable operation key.
        alert("请刷新后台后再续期"); return;
      }
      if (act === "unlist") await api(`/api/admin/listings/${id}/unlist`, { method: "POST", body: JSON.stringify({ reason: "admin" }) });
      if (act === "relist") await api(`/api/admin/listings/${id}/relist`, { method: "POST", body: "{}" });
      if (act === "delete") await api(`/api/admin/listings/${id}/delete`, { method: "POST", body: "{}" });
      await loadListings();
    } catch (e) { alert(e.message || String(e)); }
  });
  async function renderUsers() {
    const data = await api("/api/admin/users?q=" + encodeURIComponent($("#userQ")?.value || ""));
    if ($("#adminUsers")) {
      $("#adminUsers").innerHTML = (data.items || []).map((u) => `
        <div class="pick-item">
          <div class="meta">
            <div><strong>${u.user_id}</strong> @${esc(u.username || "-")} · ${esc(u.full_name || "")}</div>
            <div class="muted">${zhRole(u.role)} · ${u.is_banned ? "已拉黑" : "正常"}</div>
          </div>
          <div class="row">
            <button class="btn danger" data-ban="1" data-id="${u.user_id}">拉黑</button>
            <button class="btn" data-ban="0" data-id="${u.user_id}">解除拉黑</button>
          </div>
        </div>`).join("") || "<p class='muted'>无结果</p>";
    }
  }
  $("#btnUserSearch")?.addEventListener("click", () => renderUsers().catch((e) => alert(e.message || e)));
  $("#adminUsers")?.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("[data-ban]");
    if (!btn) return;
    try {
      await api("/api/admin/users/ban", { method: "POST", body: JSON.stringify({ user_id: parseInt(btn.getAttribute("data-id"), 10), banned: btn.getAttribute("data-ban") === "1", reason: "admin" }) });
      await renderUsers();
    } catch (e) { alert(e.message || String(e)); }
  });
  setTimeout(() => { loadListings().catch(() => {}); }, 1600);
})();
