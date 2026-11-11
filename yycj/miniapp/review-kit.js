(() => {
  // 写评价（结构化）：人照课表差异 / 优点 / 不足 / 补充 + 星级 + 约课凭证（必填 1–5 张，仅管理员可见）。
  // 公开晒图已取消（老评价里已有的公开图照常显示）。
  // 提交有加载状态，成功/失败一定有提示。旧的 #revText 保留为隐藏兜底。
  if (window.__yycjReviewKit) return;
  window.__yycjReviewKit = true;
  const MAX = { photo: 3, proof: 5 };
  const LABELS = ["", "很差", "一般", "还行", "满意", "超赞"];
  const MIN = 5;
  const FIELDS = [
    { k: "diff", label: "人照课表差异", max: 200, ph: "人照几成像，服务与课表描述是否有差异", req: true },
    { k: "pros", label: "优点", max: 200, ph: "颜值、皮肤、身材、性格、态度、服务或环境", req: true },
    { k: "cons", label: "不足", max: 200, ph: "客观说明体验中不满意或需要注意的地方", req: true },
    { k: "extra", label: "补充", max: 1000, ph: "其他想补充的细节（选填）", req: false },
  ];
  const bucket = { photo: [], proof: [] };
  const busy = { photo: 0, proof: 0 };
  let sending = false;
  const token = () => localStorage.getItem("yycj_token") || "";
  const $ = (s) => document.querySelector(s);
  function cfg() {
    return { publicPhotos: false, proofRequired: true };
  }
  function report(ev, extra) { try { window.__yycjReport && window.__yycjReport(Object.assign({ ev }, extra || {})); } catch (e) {} }
  function toast(msg, err) {
    const t = document.getElementById("toast");
    if (!t) return;
    t.textContent = msg;
    t.classList.remove("hidden");
    t.classList.toggle("err", !!err);
    clearTimeout(t._rk);
    t._rk = setTimeout(() => t.classList.add("hidden"), err ? 4000 : 2800);
  }
  function say(msg, kind) {
    const el = $("#rkMsg");
    if (!el) return;
    el.textContent = msg || "";
    el.className = "rk-msg" + (kind ? " " + kind : "");
  }
  function paintStars() {
    const box = $("#rkStars");
    const inp = $("#revStars");
    if (!box || !inp) return;
    const raw = parseInt(inp.value, 10) || 0;
    const v = Math.max(0, Math.min(5, raw));
    box.querySelectorAll("[data-star]").forEach((b) => {
      b.classList.toggle("on", Number(b.getAttribute("data-star")) <= v);
      b.setAttribute("aria-checked", Number(b.getAttribute("data-star")) === v ? "true" : "false");
    });
    const l = $("#rkStarLbl");
    if (l) l.textContent = v ? LABELS[v] : "请选择";
  }
  function paintBucket(kind) {
    const box = document.getElementById(kind === "proof" ? "rkProofs" : "rkPhotos");
    if (!box) return;
    const list = bucket[kind];
    const cells = list.map((p, i) => `<div class="rk-ph"><img src="${p.preview}" alt="" /><button type="button" class="rk-x" data-rk-del="${kind}:${i}" aria-label="删除">×</button></div>`);
    if (busy[kind]) cells.push(`<div class="rk-add busy"><span>上传中…</span></div>`);
    else if (list.length < MAX[kind]) cells.push(`<label class="rk-add"><input type="file" accept="image/*" multiple hidden data-rk-file="${kind}" /><span>${kind === "proof" ? "+ 凭证" : "+ 晒图"}</span><i>${list.length}/${MAX[kind]}</i></label>`);
    box.innerHTML = cells.join("");
    const cnt = document.getElementById(kind === "proof" ? "rkProofCnt" : "rkPhotoCnt");
    if (cnt) cnt.textContent = `已选择 ${list.length}/${MAX[kind]} 张`;
  }
  function paintBtn() {
    const btn = $("#reviewForm button[type=submit]");
    if (!btn) return;
    if (!btn.dataset.label) btn.dataset.label = "提交";
    btn.disabled = sending;
    btn.classList.toggle("rk-sending", sending);
    btn.textContent = sending ? "提交中…" : btn.dataset.label;
  }
  async function shrink(file) {
    try {
      if (!file || !/^image\//.test(file.type || "image/") || file.size < 600 * 1024) return file;
      const url = URL.createObjectURL(file);
      try {
        const img = await new Promise((ok, bad) => { const i = new Image(); i.onload = () => ok(i); i.onerror = bad; i.src = url; });
        const k = Math.min(1, 1600 / Math.max(img.naturalWidth || 1, img.naturalHeight || 1));
        const c = document.createElement("canvas");
        c.width = Math.max(1, Math.round(img.naturalWidth * k));
        c.height = Math.max(1, Math.round(img.naturalHeight * k));
        c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
        const blob = await new Promise((ok) => c.toBlob(ok, "image/jpeg", 0.85));
        if (!blob || blob.size >= file.size) return file;
        return new File([blob], (file.name || "photo").replace(/\.[^.]+$/, "") + ".jpg", { type: "image/jpeg" });
      } finally { URL.revokeObjectURL(url); }
    } catch (e) { return file; }
  }
  async function upload(kind, files) {
    const list = [...files].slice(0, MAX[kind] - bucket[kind].length);
    if (!list.length) return;
    busy[kind] = list.length;
    paintBucket(kind);
    say("");
    try {
      const fd = new FormData();
      for (const f of list) { const g = await shrink(f); fd.append("files", g, g.name || "photo.jpg"); }
      const ctl = typeof AbortController !== "undefined" ? new AbortController() : null;
      const tm = setTimeout(() => ctl && ctl.abort(), 60000);
      let r;
      try { r = await fetch("/api/media/upload?purpose=" + (kind === "proof" ? "review_proof" : "review"), { method: "POST", headers: { Authorization: "Bearer " + token() }, body: fd, signal: ctl ? ctl.signal : undefined }); }
      finally { clearTimeout(tm); }
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof d.detail === "string" ? d.detail : "图片上传失败（" + r.status + "）");
      (d.items || []).forEach((it) => { if (bucket[kind].length < MAX[kind]) bucket[kind].push({ url: it.url || it.file_id, preview: it.preview_url || "" }); });
      if (!(d.items || []).length) throw new Error("图片上传失败，请换一张试试");
    } catch (e) {
      const m = e && e.name === "AbortError" ? "图片上传超时，请检查网络后重试" : (e && e.message) || "图片上传失败";
      toast(m, true); say(m, "err");
      report("review_upload_fail", { msg: String(m).slice(0, 120), kind });
    } finally {
      busy[kind] = 0;
      paintBucket(kind);
    }
  }
  function errText(status, detail) {
    const d = typeof detail === "string" ? detail : "";
    if (status === 403 && /客人/.test(d)) return "评价需以客人身份提交：请到「我的」切换为「客人」后再评价";
    if (status === 401) return "登录已过期，请关闭小程序后重新打开";
    if (status === 429) return "提交太频繁了，请稍后再试";
    if (status === 404) return "这条资料已下架，无法评价";
    if (status >= 500) return "服务器开小差了，评价没提交成功，请稍后重试";
    if (d) return "提交失败：" + d;
    return "提交失败（" + status + "），请重试";
  }
  function sections() {
    const o = {};
    FIELDS.forEach((f) => { const el = document.getElementById("rk_" + f.k); o[f.k] = el ? el.value.trim() : ""; });
    return o;
  }
  function validate() {
    const sec = sections();
    for (const f of FIELDS) {
      if (f.req && sec[f.k].length < MIN) return { msg: `「${f.label}」至少写 ${MIN} 个字`, focus: "rk_" + f.k };
    }
    const v = parseInt(($("#revStars") || {}).value, 10) || 0;
    if (v < 1) return { msg: "请先点星打分" };
    if (cfg().proofRequired && !bucket.proof.length) return { msg: "请上传约课凭证（1–5 张）：只发给管理员审核，不会公开" };
    return null;
  }
  async function submit(ctx) {
    if (sending) return;
    if (busy.photo || busy.proof) { const m = "图片还在上传，请稍等几秒再提交"; toast(m, true); say(m, "err"); return; }
    const lamp = ($("#revLampId") && $("#revLampId").value) || "";
    if (!lamp) { const m = "没有找到要评价的资料，请返回重试"; toast(m, true); say(m, "err"); return; }
    const bad = validate();
    if (bad) { toast(bad.msg, true); say(bad.msg, "err"); if (bad.focus) try { document.getElementById(bad.focus).focus(); } catch (e) {} return; }
    sending = true; paintBtn(); say("");
    const sec = sections();
    const body = {
      lamp_id: lamp,
      stars: parseInt(($("#revStars") || {}).value, 10) || 5,
      text: "",
      sections: sec,
      photos: cfg().publicPhotos ? bucket.photo.map((p) => p.url).filter(Boolean) : [],
      proofs: bucket.proof.map((p) => p.url).filter(Boolean),
    };
    try {
      const ctl = typeof AbortController !== "undefined" ? new AbortController() : null;
      const tm = setTimeout(() => ctl && ctl.abort(), 20000);
      let r;
      try {
        r = await fetch("/api/reviews", {
          method: "POST",
          headers: { "Content-Type": "application/json", Authorization: "Bearer " + token() },
          body: JSON.stringify(body),
          signal: ctl ? ctl.signal : undefined,
        });
      } finally { clearTimeout(tm); }
      const d = await r.json().catch(() => ({}));
      if (!r.ok) {
        const m = errText(r.status, d && d.detail);
        toast(m, true); say(m, "err");
        report("review_fail", { status: r.status, msg: String(m).slice(0, 120) });
        return;
      }
      const pending = (d.review && d.review.status) === "pending";
      const m = pending ? "已提交，需管理员审核后展示" : "评价已提交";
      reset();
      sending = false; paintBtn();
      toast(m);
      try { ctx && ctx.go && ctx.go("me"); } catch (e) {}
      try { ctx && ctx.refresh && (await ctx.refresh()); } catch (e) {}
    } catch (e) {
      const m = e && e.name === "AbortError" ? "网络超时，评价没提交成功，请重试" : "网络不稳定，评价没提交成功，请重试";
      toast(m, true); say(m, "err");
      report("review_fail", { status: 0, msg: String((e && e.message) || e).slice(0, 120) });
    } finally {
      sending = false; paintBtn();
    }
  }
  function reset() {
    bucket.photo = []; bucket.proof = [];
    const inp = $("#revStars"); if (inp) inp.value = "0";
    FIELDS.forEach((f) => { const el = document.getElementById("rk_" + f.k); if (el) { el.value = ""; } });
    const t = $("#revText"); if (t) t.value = "";
    paintCounts();
    say("");
    paintStars(); paintBucket("photo"); paintBucket("proof");
  }
  function paintCounts() {
    FIELDS.forEach((f) => {
      const el = document.getElementById("rk_" + f.k);
      const c = document.getElementById("rkc_" + f.k);
      if (el && c) c.textContent = `${el.value.length}/${f.max}`;
    });
  }
  function applyCfg() {
    const c = cfg();
    const pub = $("#rkPubBox");
    if (pub) pub.classList.toggle("hidden", !c.publicPhotos);
    const req = $("#rkProofReq");
    if (req) req.textContent = c.proofRequired ? "必填 1–5 张，仅管理员可见" : "1–5 张，仅管理员可见";
  }
  function mount() {
    const form = $("#reviewForm");
    const inp = $("#revStars");
    if (!form || !inp || $("#rkStars")) return;
    form.classList.add("rk-form");
    const h = form.querySelector("h3"); if (h) h.textContent = "写评价";
    inp.classList.add("rk-hide");
    inp.setAttribute("aria-hidden", "true");
    inp.tabIndex = -1;
    inp.value = "0";
    inp.removeAttribute("min");
    const txt = $("#revText");
    if (txt) { txt.classList.add("rk-hide"); txt.setAttribute("aria-hidden", "true"); txt.tabIndex = -1; }
    const fields = document.createElement("div");
    fields.className = "rk-fields";
    fields.innerHTML = FIELDS.map((f) => `
      <div class="rk-field">
        <div class="rk-fh"><label for="rk_${f.k}">${f.label}${f.req ? "" : ""}</label><span class="rk-cnt" id="rkc_${f.k}">0/${f.max}</span></div>
        <textarea id="rk_${f.k}" maxlength="${f.max}" rows="${f.k === "extra" ? 3 : 2}" placeholder="${f.ph}"></textarea>
      </div>`).join("");
    (h || form.firstChild) && (h ? h.insertAdjacentElement("afterend", fields) : form.prepend(fields));
    const starRow = document.createElement("div");
    starRow.className = "rk-star-row";
    starRow.innerHTML = `<span class="rk-k">评分</span><div id="rkStars" class="rk-stars" role="radiogroup" aria-label="打分">${[1, 2, 3, 4, 5].map((n) => `<button type="button" role="radio" data-star="${n}" aria-label="${n} 星">★</button>`).join("")}<span id="rkStarLbl" class="rk-lbl"></span></div>`;
    fields.insertAdjacentElement("afterend", starRow);
    const pub = document.createElement("div");
    pub.id = "rkPubBox";
    pub.className = "rk-box";
    pub.innerHTML = `<div class="rk-bh"><b>公开晒图</b><span class="muted">选填，最多 3 张，审核后公开</span></div><div id="rkPhotos" class="rk-photos"></div>`;
    starRow.insertAdjacentElement("afterend", pub);
    const proof = document.createElement("div");
    proof.id = "rkProofBox";
    proof.className = "rk-box rk-proof";
    proof.innerHTML = `<div class="rk-bh"><b>🔒 约课凭证</b><span class="muted" id="rkProofReq">1–5 张，仅管理员可见</span></div><div id="rkProofs" class="rk-photos"></div><div class="rk-cnt2" id="rkProofCnt">已选择 0/5 张</div>`;
    pub.insertAdjacentElement("afterend", proof);
    const btn = form.querySelector("button[type=submit]");
    if (btn) { btn.textContent = "提交"; btn.dataset.label = "提交"; }
    const note = document.createElement("p");
    note.className = "rk-note";
    note.textContent = "凭证只发送给管理员审核，不会显示在公开评价中，敏感信息请自行打码。";
    const msg = document.createElement("div");
    msg.id = "rkMsg";
    msg.className = "rk-msg";
    msg.setAttribute("role", "status");
    msg.setAttribute("aria-live", "polite");
    form.appendChild(msg);
    form.appendChild(note);
    applyCfg();
    paintStars();
    paintBucket("photo");
    paintBucket("proof");
  }
  document.addEventListener("input", (ev) => { if (ev.target && /^rk_/.test(ev.target.id || "")) paintCounts(); });
  document.addEventListener("click", (ev) => {
    const s = ev.target.closest && ev.target.closest("#rkStars [data-star]");
    if (s) { const inp = $("#revStars"); if (inp) inp.value = s.getAttribute("data-star"); paintStars(); return; }
    const del = ev.target.closest && ev.target.closest("[data-rk-del]");
    if (del) { const [kind, i] = String(del.getAttribute("data-rk-del")).split(":"); bucket[kind].splice(Number(i), 1); paintBucket(kind); }
  });
  document.addEventListener("change", (ev) => {
    const kind = ev.target && ev.target.getAttribute && ev.target.getAttribute("data-rk-file");
    if (kind) { const f = [...(ev.target.files || [])]; ev.target.value = ""; upload(kind, f); }
  });
  // show the right variant whenever the form opens
  const obs = () => { const v = document.getElementById("view-review"); if (v && window.MutationObserver) new MutationObserver(applyCfg).observe(v, { attributes: true, attributeFilter: ["class"] }); };
  window.__yycjSubmitReview = submit;
  window.__yycjRevPhotos = () => bucket.photo.map((p) => p.url).filter(Boolean);
  window.__yycjRevReset = reset;
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", () => { mount(); obs(); }); else { mount(); obs(); }
})();
