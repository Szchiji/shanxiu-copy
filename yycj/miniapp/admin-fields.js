(() => {
  if (!document.getElementById("yycj-admin-refresh")) {
    const s = document.createElement("script");
    s.id = "yycj-admin-refresh";
    s.src = "./admin-refresh.js?v=20261111f";
    document.head.appendChild(s);
  }
  if (window.__yycjAdminFields) return;
  window.__yycjAdminFields = true;
  const token = () => localStorage.getItem("yycj_token") || "";
  const DEFAULT_TEXT = "称呼\n城市\n简介\n价位\n区域\n大致位置\n标签\n联系\n频道\n微信\n地点 仅模板\n链接 仅模板";
  function parse(text) {
    const out = [];
    const seen = new Set();
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
    if (!fields || !fields.length) return "";
    return fields.map((x) => x.form === false ? (x.key + " 仅模板") : x.key).join("\n");
  }
  function readEditor() {
    const ta = document.getElementById("listingFieldsEditor");
    window.__listingFields = parse(ta ? ta.value : "");
    return window.__listingFields;
  }
  function box() {
    let el = document.getElementById("listingFieldBox");
    if (el) return el;
    const tpl = document.getElementById("opsBroadcastTpl");
    if (!tpl || !tpl.parentNode) return null;
    el = document.createElement("div");
    el.id = "listingFieldBox";
    tpl.parentNode.insertBefore(el, tpl.nextSibling);
    return el;
  }
  function paint(text) {
    const el = box();
    if (!el) return;
    el.innerHTML = `<label>上架栏（一行一个，删掉那一行即删栏）</label><textarea id="listingFieldsEditor" rows="10"></textarea><p class="muted" style="margin:6px 0 0">改完后必点「保存全部设置」。模板里的 {字} 不会再自动把栏加回来。</p><div class="row" style="margin-top:8px"><button class="btn" type="button" id="btnResetFields">恢复默认</button></div>`;
    const editor = document.getElementById("listingFieldsEditor");
    if (editor) editor.value = text || "";
    editor?.addEventListener("input", readEditor);
    readEditor();
  }
  document.addEventListener("click", (ev) => {
    if (ev.target && ev.target.id === "btnResetFields") paint(DEFAULT_TEXT);
    readEditor();
  }, true);
  setTimeout(async () => {
    let text = DEFAULT_TEXT;
    try {
      const r = await fetch("/api/admin/homepage", { headers: { Authorization: "Bearer " + token() } });
      const s = (await r.json()).settings || {};
      const saved = Array.isArray(s.listing_fields) ? s.listing_fields.filter((x) => x && x.key) : [];
      if (saved.length) text = toText(saved);
      const ta = document.getElementById("opsBroadcastTpl");
      if (ta && s.broadcast_template) ta.value = s.broadcast_template;
      const ch = document.getElementById("opsChannel");
      if (ch && s.broadcast_channel && !ch.value) ch.value = s.broadcast_channel;
    } catch (e) {}
    paint(text);
  }, 800);
})();
