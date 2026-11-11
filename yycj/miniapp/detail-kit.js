(() => {
  if (window.__yycjDetailKit) return;
  window.__yycjDetailKit = true;
  const token = () => localStorage.getItem("yycj_token") || "";
  function toast(msg, err) {
    const el = document.getElementById("toast");
    if (!el) return alert(msg);
    el.textContent = msg || "";
    el.classList.toggle("hidden", !msg);
    el.classList.toggle("err", !!err);
    if (msg) setTimeout(() => el.classList.add("hidden"), 3600);
  }
  if (!document.getElementById("yycj-extras-css")) {
    const st = document.createElement("style");
    st.id = "yycj-extras-css";
    st.textContent = `#yycjExtras{margin:6px 0 2px;padding:0;background:none;border:none;}
#yycjExtras .ex-line{display:flex;gap:8px;align-items:baseline;margin:4px 0;font-size:.92rem;}
#yycjExtras .ex-k{opacity:.72;min-width:3em;}
#yycjExtras .ex-v{word-break:break-all;}
#yycjExtras a, #detail a.tg-link{color:#8ec8ff;text-decoration:underline;}
#yycjTags{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0 4px;}
#yycjTags .tag{display:inline-block;padding:2px 8px;border-radius:999px;background:#6d4aff;color:#fff;font-size:11px;}`;
    document.head.appendChild(st);
  }
  function tgHref(raw) {
    const s = String(raw || "").trim();
    if (!s) return "";
    if (/^(https?:\/\/|tg:\/\/)/i.test(s)) return s;
    if (s.startsWith("t.me/")) return "https://" + s;
    if (s.startsWith("@")) return "https://t.me/" + s.slice(1).replace(/^\+/, "+");
    if (/^[A-Za-z][A-Za-z0-9_]{3,31}$/.test(s)) return "https://t.me/" + s;
    return "";
  }
  function esc(t) {
    return String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':'&quot;',"'":"&#39;" }[c]));
  }
  function linkable(key) {
    return /联系|频道|机器人|链接|用户名|telegram|bot/i.test(String(key || ""));
  }
  function valueHtml(key, val) {
    const safe = esc(String(val).replace(/[<>]/g, ""));
    const href = tgHref(val) || (linkable(key) ? tgHref(val.replace(/^@/, "@")) : "");
    if (!href) return safe;
    const h = esc(href.replace(/"/g, ""));
    return `<a class="tg-link" href="${h}" data-tg="${h}">${safe}</a>`;
  }
  function openTg(href) {
    const tg = window.Telegram && window.Telegram.WebApp;
    if (!href) return;
    if (tg && /t\.me\/|tg:\/\//i.test(href) && tg.openTelegramLink) tg.openTelegramLink(href);
    else if (tg && tg.openLink) tg.openLink(href);
    else location.href = href;
  }
  function ctaOn() {
    const v = (window.__yycjHome || {}).show_chat_cta;
    return !(v === false || v === 0 || v === "0" || v === "false");
  }
  function ctaLabel() {
    return String((window.__yycjHome || {}).chat_cta_label || "想聊聊").slice(0, 32);
  }
  function applyCta() {
    const on = ctaOn();
    const text = ctaLabel();
    document.querySelectorAll("#detailChat, [data-cta='chat']").forEach((btn) => {
      const rest = last?.data?.lamp?.open_code === "rest";
      if(rest) { btn.disabled = true; btn.dataset.restBlocked = "1"; }
      else if(btn.dataset.restBlocked) { btn.disabled = false; delete btn.dataset.restBlocked; }
      if(rest && btn.textContent !== "休息中 · 暂不联系") btn.textContent = "休息中 · 暂不联系";
      if (!rest && on && btn.textContent !== text) btn.textContent = text;
      const disp = on ? "" : "none";
      if (btn.style.display !== disp) btn.style.display = disp;
    });
  }
  function paintApprox(lamp) {
    const label = String((lamp && lamp.approx_label) || "").trim();
    const card = document.querySelector("#detail .card") || document.getElementById("detail");
    if (!card || !label) return;
    const loc = [...card.querySelectorAll(".muted")].find((x) => (x.textContent || "").includes("📍"));
    if (loc && !loc.dataset.approx) {
      loc.textContent = loc.textContent.replace(/\s+$/, "") + " · " + label;
      loc.dataset.approx = "1";
    }
  }
  function paintTags(lamp) {
    const tags = ((lamp && lamp.tags) || []).map((t) => String(t || "").trim()).filter(Boolean);
    const card = document.querySelector("#detail .card") || document.getElementById("detail");
    if (!card) return;
    let el = document.getElementById("yycjTags");
    if (!tags.length) { if (el) el.remove(); return; }
    if (!el) {
      el = document.createElement("div");
      el.id = "yycjTags";
      const price = [...card.querySelectorAll(".muted")].find((x) => (x.textContent || "").includes("💰"));
      if (price) price.insertAdjacentElement("afterend", el);
      else card.insertBefore(el, card.querySelector("p") || card.querySelector(".row"));
    }
    el.innerHTML = tags.map((t) => `<span class="tag">#${t.replace(/[<>#]/g, "")}</span>`).join("");
  }
  function paintExtras(extras) {
    const detail = document.getElementById("detail");
    if (!detail) return;
    const data = extras && typeof extras === "object" ? extras : {};
    const keys = Object.keys(data).filter((k) => !String(k).startsWith("_") && String(data[k] || "").trim() && k !== "标签");
    let box = document.getElementById("yycjExtras");
    if (!keys.length) { if (box) box.remove(); return; }
    const card = detail.querySelector(".card") || detail;
    const row = card.querySelector(".row");
    if (!box) { box = document.createElement("div"); box.id = "yycjExtras"; }
    if (row && row.parentNode === card) card.insertBefore(box, row);
    else if (box.parentNode !== card) card.appendChild(box);
    box.innerHTML = keys.map((k) => `<div class="ex-line"><span class="ex-k">${esc(k)}</span><span class="ex-v">${valueHtml(k, data[k])}</span></div>`).join("");
  }
  function placeShare(id) {
    document.querySelector("#yycjGallery #detailShare, #yycjGallery [data-share]")?.remove();
    const rev = document.getElementById("detailReview");
    if (!rev) return;
    let btn = document.getElementById("detailShare");
    if (!btn) {
      btn = document.createElement("button");
      btn.id = "detailShare";
      btn.className = "btn";
      btn.type = "button";
      btn.textContent = "分享";
      rev.insertAdjacentElement("afterend", btn);
    }
    if (id) btn.setAttribute("data-share", id);
  }
  function paintReviews(list, id) {
    const detail = document.getElementById("detail");
    if (!detail) return;
    let box = document.getElementById("yycjReviews");
    if (!box) { box = document.createElement("div"); box.id = "yycjReviews"; detail.appendChild(box); }
    const rows = list && list.length ? list.map((t) => {
      const stars = "★".repeat(t.stars || 0) + "☆".repeat(Math.max(0, 5 - (t.stars || 0)));
      const ph = (Array.isArray(t.photos) ? t.photos : []).slice(0, 3).map((u) => String(u || "")).filter((u) => /^[A-Za-z0-9_\-:./]+$/.test(u))
        .map((u) => { const src = /^https?:|^\//.test(u) ? u : "/api/media/file/" + encodeURIComponent(u); return `<img class="rv-ph" loading="lazy" src="${src}" alt="" />`; }).join("");
      const sec = (t.sections && typeof t.sections === "object") ? t.sections : {};
      const L = [["diff", "人照课表差异"], ["pros", "优点"], ["cons", "不足"], ["extra", "补充"]];
      const clean = (x) => String(x || "").replace(/[<>]/g, "");
      // structured reviews get section headers; old plain-text reviews render as before
      const body = L.some(([k]) => sec[k])
        ? `<dl class="rv-sec">${L.filter(([k]) => sec[k]).map(([k, l]) => `<dt>${l}</dt><dd>${clean(sec[k])}</dd>`).join("")}</dl>`
        : `<p>${clean(t.text)}</p>`;
      return `<div class="card" style="margin:8px 0"><div class="muted">${stars}${t.audit_verified ? " · 已审核" : ""}${t.proof_count > 0 ? " · 已提交凭证" : ""}</div>${body}${ph ? `<div class="rv-phs">${ph}</div>` : ""}</div>`;
    }).join("") : "<p class='muted'>暂无评价</p>";
    box.innerHTML = `<h3 style="font-size:1rem;margin:12px 0 6px">评价</h3>${rows}`;
    placeShare(id);
  }
  function tune(v) {
    // Carousel videos are user-started with sound + controls (detail-fix.js); don't force muted autoplay loops.
    if (!v || v.dataset.tuned || v.closest("#yycjGallery")) return;
    v.dataset.tuned = "1";
    v.muted = true;
    v.autoplay = true;
    v.loop = true;
    v.controls = false;
    v.playsInline = true;
    v.preload = "metadata";
    v.play().catch(() => {});
  }
  // Tags / contacts / reviews are painted into the info card. The card can be (re)rendered after
  // they were painted (app.js and this file read the same response in either order), which wiped
  // them; so remember the data and repaint whenever a new card node shows up for the same listing.
  let last = null, paintedCard = null;
  function paintAll(data, id) {
    const lamp = data.lamp || data;
    paintApprox(lamp);
    paintTags(lamp);
    paintExtras(lamp.extras || data.extras || {});
    paintReviews(data.reviews || data.approved_reviews || lamp.reviews || [], id);
    applyCta();
    const card = document.querySelector("#detail > .card");
    if(card) {
      let trust=document.getElementById("detailTrust");if(!trust){trust=document.createElement("div");trust.id="detailTrust";trust.className="detail-trust";card.querySelector("h3")?.insertAdjacentElement("afterend",trust);}
      trust.textContent = "资料已审核 · " + (data.reputation?.review_count ?? (data.reviews || []).length) + " 条评价 · 审核不代表体验担保";
      let links=document.getElementById("detailCommunity");if(!links){links=document.createElement("div");links.id="detailCommunity";links.className="detail-community";card.appendChild(links);}
      links.innerHTML=(data.community_links || []).filter(x=>["discussion","backup","location"].includes(x.key)).map(x=>`<button class="btn" type="button" data-hub-url="${esc(x.url)}">${esc(x.label)}</button>`).join("");
    }
    paintedCard = document.querySelector("#detail > .card");
  }
  function repaintIfNewCard() {
    if (!last) return;
    const box = document.getElementById("detail");
    const card = box && box.querySelector(":scope > .card");
    if (!card || card === paintedCard) return;
    const id = box.getAttribute("data-lamp") || window.__yycjOpenLamp || "";
    if (id && id === last.id) { try { paintAll(last.data, id); } catch (e) {} }
  }
  async function loadLamp(id) {
    if (!id) return;
    window.__yycjOpenLamp = id;
    try {
      const r = await fetch("/api/lamps/" + encodeURIComponent(id), { headers: { Authorization: "Bearer " + token() } });
      if (!r.ok) return;
      const data = await r.json();
      if ((window.__yycjOpenLamp || id) !== id) return;
      last = { id, data };
      const card = document.querySelector("#detail > .card");
      if (card && rendered.card === card && rendered.id === id && rendered.sig === sigOf(data)) return;
      paintAll(data, id);
    } catch (e) {}
  }
  window.__yycjLoadDetailExtras = loadLamp;
  // The one detail renderer. app.js, deep-open.js and detail-guard.js all hand their response here,
  // and it builds the whole page in a single synchronous pass: info card (same markup app.js used),
  // approx / tags / contacts / reviews / share, then the XHS layout (detail-xhs.js). The same
  // listing data is rendered once; a second copy of the same response is a no-op.
  let rendered = { id: "", sig: "", card: null };
  function sigOf(data) { try { return JSON.stringify([data.lamp || data.item || data, data.reviews || null]); } catch (e) { return String(Math.random()); } }
  function showView(name) {
    document.querySelectorAll(".view").forEach((v) => v.classList.add("hidden"));
    document.getElementById("view-" + name)?.classList.remove("hidden");
    document.querySelectorAll(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.nav === name));
  }
  function wireButtons(l) {
    const rep = document.getElementById("detailReport");
    if (rep) rep.onclick = async () => {
      const reason = prompt("举报原因") || "";
      if (!reason) return;
      try {
        const r = await fetch("/api/reports", { method: "POST", headers: { "Content-Type": "application/json", Authorization: "Bearer " + token() }, body: JSON.stringify({ lamp_id: l.lamp_id, reason, description: "" }) });
        const d = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(d.detail || "举报失败");
        toast("举报已提交");
      } catch (e) { toast(e.message || String(e), true); }
    };
    const rev = document.getElementById("detailReview");
    if (rev) rev.onclick = () => { const h = document.getElementById("revLampId"); if (h) h.value = l.lamp_id; showView("review"); };
  }
  function renderDetail(data, id) {
    const box = document.getElementById("detail");
    const l = data && (data.lamp || data.item);
    if (!box || !l) return false;
    id = String(id || l.lamp_id || "");
    const sig = sigOf(data);
    const card = box.querySelector(":scope > .card");
    if (rendered.id === id && rendered.sig === sig && card && card === rendered.card && box.getAttribute("data-lamp") === id) return true;
    box.innerHTML = `<div class="card"><h3>${esc(l.title || "")}</h3><div class="muted">📍 ${esc(l.city || "")}${l.district ? " · " + esc(l.district) : ""}</div><div class="muted">💰 ${esc(l.price_text || "面议")}</div><p>${esc(l.description || "")}</p><div class="row"><button class="btn primary" id="detailChat" type="button">想聊聊</button><button class="btn" id="detailReport" type="button">举报</button><button class="btn" id="detailReview" type="button">写评价</button></div></div>`;
    box.setAttribute("data-lamp", id);
    window.__yycjOpenLamp = id;
    wireButtons(l);
    last = { id, data };
    try { paintAll(data, id); } catch (e) {}
    try { placeShare(id); } catch (e) {}
    try { if (window.__yycjDecorateDetail) window.__yycjDecorateDetail(); } catch (e) {}
    rendered = { id, sig, card: box.querySelector(":scope > .card") };
    return true;
  }
  window.__yycjRenderDetail = renderDetail;
  // Coalesce identical concurrent GET /api/home?limit=1 calls (same token, still in flight)
  // so detail-kit and share.js share one request at boot. Each caller still parses the same JSON.
  if (!window.__yycjHomeLite) {
    const inflight = {};
    let waiting = null;
    window.__yycjHomeLite = () => {
      const t = token();
      // before login there is no token yet: wait for it (up to 6s) instead of sending a request
      // that is wasted; with a token the answer is shared with the feed request (net-cache.js)
      if (!t) {
        if (!waiting) waiting = (async () => { for (let i = 0; i < 60 && !token(); i += 1) await new Promise((r) => setTimeout(r, 100)); waiting = null; return token() ? window.__yycjHomeLite() : fetch("/api/home?limit=1").then((r) => r.json()); })();
        return waiting;
      }
      if (inflight[t]) return inflight[t];
      const p = fetch("/api/home?limit=1", { headers: { Authorization: "Bearer " + t } }).then((r) => r.json());
      inflight[t] = p;
      const clear = () => { if (inflight[t] === p) delete inflight[t]; };
      p.then(clear, clear);
      return p;
    };
  }
  async function loadHomeOnce() {
    if (window.__yycjHome && window.__yycjHome.chat_cta_label) { applyCta(); return; }
    try {
      window.__yycjHome = Object.assign({}, window.__yycjHome || {}, await window.__yycjHomeLite());
      applyCta();
    } catch (e) {}
  }
  document.addEventListener("click", (ev) => {
    const a = ev.target.closest("a[data-tg], #detail a.tg-link, #yycjExtras a");
    if (a) {
      ev.preventDefault();
      ev.stopPropagation();
      openTg(a.getAttribute("data-tg") || a.getAttribute("href") || "");
      return;
    }
    const card = ev.target.closest("#feed [data-id], #pins [data-id], #favList [data-id]");
    if (!card) return;
    const id = card.getAttribute("data-id");
    window.__yycjOpenLamp = id;
    setTimeout(() => loadLamp(id), 180);
  }, true);
  document.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("#detailChat");
    if (!btn) return;
    ev.preventDefault();
    ev.stopPropagation();
    if(last?.data?.lamp?.open_code === "rest") return toast("休息中，暂不联系");
    const id = window.__yycjOpenLamp || btn.getAttribute("data-lamp") || "";
    if (!id) return toast("找不到这条资料", true);
    btn.disabled = true;
    try {
      const nick = ((document.getElementById("guestNick") && document.getElementById("guestNick").value) || localStorage.getItem("yycj_guest_nick") || "").trim().slice(0, 16);
      const payload = { lamp_id: id };
      if (nick) payload.guest_alias = nick;
      const r = await fetch("/api/sessions/request", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: "Bearer " + token() },
        body: JSON.stringify(payload),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.detail || "发起失败");
      toast(data.message || "已发送想聊聊");
    } catch (e) {
      toast(e.message || String(e), true);
    } finally {
      btn.disabled = last?.data?.lamp?.open_code === "rest";
    }
  }, true);
  const view = document.getElementById("view-detail");
  if (view) {
    new MutationObserver(() => {
      view.querySelectorAll("video").forEach(tune);
      applyCta();
      const id = document.getElementById("detail")?.getAttribute("data-lamp") || "";
      if (id && document.getElementById("detailReview")) placeShare(id);
      repaintIfNewCard();
    }).observe(view, { childList: true, subtree: true });
  }
  loadHomeOnce();
})();
