(() => {
  ["admin-diff.js", "admin-ops-users.js", "back-top.js"].forEach((name) => {
    const id = "yycj-" + name.replace(".js", "");
    if (document.getElementById(id)) return;
    const s = document.createElement("script");
    s.id = id;
    s.src = "./" + name + "?v=20261104b";
    document.head.appendChild(s);
  });
  if (window.__yycjAdminKit) return;
  window.__yycjAdminKit = true;
  const token = () => localStorage.getItem("yycj_token") || "";
  const DEFAULT_TEXT = "称呼\n城市\n简介\n价位\n区域\n大致位置\n标签\n联系\n频道\n微信\n地点 仅模板\n链接 仅模板";
  function setVal(id, val) {
    const el = document.getElementById(id);
    if (!el || val == null) return;
    if (el.type === "checkbox") el.checked = !!val;
    else el.value = val;
  }
  function parse(text) {
    const out = [], seen = new Set();
    String(text || "").split("\n").forEach((line) => {
      const raw = line.trim();
      if (!raw) return;
      const only = /仅模板|\*$/.test(raw);
      const key = raw.replace(/仅模板|\*/g, "").trim().slice(0, 16);
      if (!key || seen.has(key)) return;
      seen.add(key);
      out.push({ key, label: key, form: !only && key !== "地点" && key !== "链接" && key !== "聊天按钮" });
    });
    return out;
  }
  function toText(fields) {
    return (fields || []).map((x) => x.form === false ? (x.key + " 仅模板") : x.key).join("\n");
  }
  function paintFields(text) {
    let el = document.getElementById("listingFieldBox");
    const tpl = document.getElementById("opsBroadcastTpl");
    if (!el && tpl && tpl.parentNode) {
      el = document.createElement("div");
      el.id = "listingFieldBox";
      tpl.parentNode.insertBefore(el, tpl.nextSibling);
    }
    if (!el) return;
    el.innerHTML = "<label>上架栏（一行一个，删掉那一行即删栏）</label><textarea id=\"listingFieldsEditor\" rows=\"10\"></textarea><p class=\"muted\" style=\"margin:6px 0 0\">改完后点保存全站设置。</p><div class=\"row\" style=\"margin-top:8px\"><button class=\"btn\" type=\"button\" id=\"btnResetFields\">恢复默认</button><button class=\"btn\" type=\"button\" id=\"btnTplPreview\">预览推送文案</button></div><pre id=\"tplPreview\" class=\"muted\" style=\"white-space:pre-wrap;background:rgba(0,0,0,.25);padding:10px;border-radius:10px;min-height:40px\">点预览看频道成品</pre>";
    const editor = document.getElementById("listingFieldsEditor");
    if (editor) editor.value = text || "";
    window.__listingFields = parse(editor ? editor.value : "");
    editor?.addEventListener("input", () => { window.__listingFields = parse(editor.value); });
  }
  async function hydrate() {
    const r = await fetch("/api/admin/homepage", { headers: { Authorization: "Bearer " + token() } });
    const s = ((await r.json()).settings) || {};
    setVal("annText", s.announcement_text);
    setVal("annOn", s.announcement_enabled);
    setVal("citiesInput", (s.enabled_cities || []).join(","));
    setVal("opsPageSize", s.home_feed_page_size);
    setVal("opsCta", s.chat_cta_label);
    setVal("opsWelcome", s.bot_welcome_text);
    setVal("opsMediaMax", s.media_max_count);
    setVal("opsMediaChannel", s.media_channel_id);
    setVal("opsListingDays", s.listing_days);
    setVal("opsCarouselSec", s.carousel_interval_sec);
    setVal("opsAdminContact", s.admin_contact);
    setVal("opsShowBot", s.show_bot_link);
    setVal("opsShowAdmin", s.show_admin_link);
    setVal("opsReviewAudit", s.review_require_audit);
    setVal("opsChats", (s.required_chats || []).map((c) => [c.chat_id, c.title, c.url].filter(Boolean).join("|")).join("\n"));
    setVal("opsPromo", s.approve_promo_text);
    setVal("opsChannel", s.broadcast_channel);
    setVal("opsBroadcastTpl", s.broadcast_template);
    setVal("opsLinkDetail", s.link_label_detail != null ? s.link_label_detail : "查看详情");
    setVal("opsLinkReviews", s.link_label_reviews != null ? s.link_label_reviews : "查看评价");
    setVal("opsLinkWrite", s.link_label_write != null ? s.link_label_write : "写评价");
    setVal("opsTeachViews", s.teacher_show_views !== false);
    setVal("opsTeachFavs", s.teacher_show_favs !== false);
    setVal("opsTeachContacts", s.teacher_show_contacts !== false);
    const saved = Array.isArray(s.listing_fields) ? s.listing_fields.filter((x) => x && x.key) : [];
    paintFields(saved.length ? toText(saved) : DEFAULT_TEXT);
  }
  function dueText(x) {
    if (x.days_left == null) return "无截止";
    if (x.days_left < 0) return "已过期 " + Math.abs(x.days_left) + " 天";
    if (x.days_left === 0) return "今天到期";
    return "剩 " + x.days_left + " 天";
  }
  function esc(t) {
    return String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':'&quot;',"'":"&#39;" }[c]));
  }
  async function paintListings() {
    const box = document.getElementById("adminListings");
    if (!box) return;
    try {
      const r = await fetch("/api/admin/listings", { headers: { Authorization: "Bearer " + token() } });
      const items = ((await r.json()).items) || [];
      window.__yycjListings = items;
      const ST = { active: "已上架", hidden: "已下架" };
      box.innerHTML = items.map((x) => "<div class=\"pick-item\"><div class=\"meta\"><strong>" + esc(x.title || "") + "</strong><div class=\"muted\">" + esc(x.city || "") + " · " + (ST[x.status] || x.status) + " · " + dueText(x) + "</div></div>"+(window.__yycjEditUI?.html(x.edit_state,"admin",{card:true})||"")+"<div class=\"row\"><button class=\"btn\" data-fill-proxy=\"" + esc(x.lamp_id) + "\">代改</button><button class=\"btn\" data-list=\"renew\" data-id=\"" + esc(x.lamp_id) + "\">续期</button><button class=\"btn\" data-list=\"relist\" data-id=\"" + esc(x.lamp_id) + "\">重新上架</button><button class=\"btn danger\" data-list=\"unlist\" data-id=\"" + esc(x.lamp_id) + "\">下架</button><button class=\"btn danger\" data-list=\"delete\" data-id=\"" + esc(x.lamp_id) + "\" data-title=\"" + esc(x.title || "") + "\">删除</button></div></div>").join("") || "<p class='muted'>暂无资料</p>";
    } catch (e) {}
  }
  document.addEventListener("click", async (ev) => {
    if (ev.target && ev.target.id === "btnResetFields") paintFields(DEFAULT_TEXT);
    if (ev.target && ev.target.id === "btnTplPreview") {
      const out = document.getElementById("tplPreview");
      if (out) out.textContent = "生成中...";
      try {
        const r = await fetch("/api/admin/broadcast/preview", {
          method: "POST",
          headers: { Authorization: "Bearer " + token(), "Content-Type": "application/json" },
          body: JSON.stringify({ template: document.getElementById("opsBroadcastTpl")?.value || "" }),
        });
        const data = await r.json();
        if (!r.ok) throw new Error(data.detail || "预览失败");
        const title = data.title ? ("[" + data.title + "]\n") : "";
        if (out) out.textContent = title + (data.text || "");
      } catch (e) { if (out) out.textContent = e.message || String(e); }
    }
    const unlock = ev.target.closest("[data-unlock-role]");
    if (unlock) {
      ev.preventDefault();
      await fetch("/api/admin/users/unlock-role", {
        method: "POST",
        headers: { Authorization: "Bearer " + token(), "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: parseInt(unlock.getAttribute("data-unlock-role"), 10) }),
      });
      alert("已解锁该用户角色");
    }
  }, true);
  const origFetch = window.fetch;
  window.fetch = function (url, opt) {
    try {
      if (String(url || "").includes("/api/admin/settings/extra") && opt && typeof opt.body === "string") {
        const body = JSON.parse(opt.body);
        const ch = (document.getElementById("opsChannel")?.value || "").trim();
        const tpl = document.getElementById("opsBroadcastTpl")?.value || "";
        const media = (document.getElementById("opsMediaChannel")?.value || "").trim();
        if (ch) body.broadcast_channel = ch;
        if (tpl.trim()) body.broadcast_template = tpl;
        if (media) body.media_channel_id = media;
        if (window.__listingFields && window.__listingFields.length) body.listing_fields = window.__listingFields;
        const ld = document.getElementById("opsLinkDetail");
        const lr = document.getElementById("opsLinkReviews");
        const lw = document.getElementById("opsLinkWrite");
        if (ld) body.link_label_detail = ld.value;
        if (lr) body.link_label_reviews = lr.value;
        if (lw) body.link_label_write = lw.value;
        const tv = document.getElementById("opsTeachViews");
        const tf = document.getElementById("opsTeachFavs");
        const tc = document.getElementById("opsTeachContacts");
        if (tv) body.teacher_show_views = !!tv.checked;
        if (tf) body.teacher_show_favs = !!tf.checked;
        if (tc) body.teacher_show_contacts = !!tc.checked;
        opt = Object.assign({}, opt, { body: JSON.stringify(body) });
      }
    } catch (e) {}
    return origFetch.apply(this, [url, opt]);
  };
  const users = document.getElementById("adminUsers");
  if (users) {
    new MutationObserver(() => {
      users.querySelectorAll(".pick-item .row").forEach((row) => {
        if (row.querySelector("[data-unlock-role]")) return;
        const ban = row.querySelector("[data-id]");
        if (!ban) return;
        const b = document.createElement("button");
        b.className = "btn";
        b.setAttribute("data-unlock-role", ban.getAttribute("data-id"));
        b.textContent = "解锁角色";
        row.appendChild(b);
      });
    }).observe(users, { childList: true, subtree: true });
  }
  document.getElementById("btnRefresh")?.addEventListener("click", () => {
    hydrate().catch(() => {});
    paintListings();
  });
  setTimeout(() => hydrate().catch(() => {}), 800);
  setTimeout(paintListings, 1600);
  window.yycjLoadListings = paintListings;
})();
