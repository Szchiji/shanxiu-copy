// Network resilience for /api calls + client error reports (to the server log, no token / PII).
// - 401: get a fresh token from Telegram initData once (shared), then retry with it. The token used
//   to stay dead until the app was closed, so a detail opened after that showed nothing.
// - GET /api/lamps/<id>: 6s timeout, retried on network error / 5xx (twice, 0.5s + 1.5s) and once
//   after a timeout, so a stuck request ends in a 重试 state in ~13s instead of loading forever.
(() => {
  if (window.__yycjResilience) return;
  window.__yycjResilience = true;
  const me = document.currentScript && document.currentScript.src || "";
  const V = (me.match(/[?&]v=(\w+)/) || [])[1] || "";
  const tg = () => (window.Telegram && window.Telegram.WebApp) || null;
  const raw = window.fetch.bind(window);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  let sent = 0;
  function report(ev) {
    if (sent >= 20) return;
    sent += 1;
    try {
      const t = tg() || {};
      const body = JSON.stringify(Object.assign({ v: V, plat: t.platform || "", tgv: t.version || "" }, ev));
      if (navigator.sendBeacon && navigator.sendBeacon("/api/client-log", new Blob([body], { type: "application/json" }))) return;
      raw("/api/client-log", { method: "POST", headers: { "Content-Type": "application/json" }, body, keepalive: true }).catch(() => {});
    } catch (e) {}
  }
  window.__yycjReport = report;
  window.addEventListener("error", (e) => {
    const f = String((e && e.filename) || "").split("/").pop().split("?")[0];
    if (!e || !e.message || !/\.js$/.test(f) || f === "telegram-web-app.js") return;
    report({ ev: "js_error", msg: e.message, src: f + ":" + (e.lineno || 0) });
  });

  const getTok = () => { try { return localStorage.getItem("yycj_token") || ""; } catch (e) { return ""; } };
  const dead = new Set();
  let refreshing = null;
  function refresh() {
    if (refreshing) return refreshing;
    const init = (tg() && tg().initData) || "";
    if (!init) return Promise.resolve("");
    refreshing = raw("/api/auth", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ initData: init }) })
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        const tok = (d && d.token) || "";
        if (tok) { try { localStorage.setItem("yycj_token", tok); } catch (e) {} }
        report({ ev: "auth_refresh", status: tok ? "ok" : "fail" });
        return tok;
      })
      .catch(() => "");
    refreshing.then(() => setTimeout(() => { refreshing = null; }, 5000));
    return refreshing;
  }
  const bearerOf = (init) => {
    try { return (new Headers((init && init.headers) || {}).get("Authorization") || "").replace(/^Bearer\s+/i, ""); } catch (e) { return ""; }
  };
  const withTok = (init, tok) => {
    const h = new Headers((init && init.headers) || {});
    h.set("Authorization", "Bearer " + tok);
    return Object.assign({}, init || {}, { headers: h });
  };
  const API = /^\/api\//;
  const SKIP = /^\/api\/(auth|client-log)\b/;
  const LAMP = /^\/api\/lamps\/[^/?#]+(\?[^#]*)?$/;
  window.fetch = async function (input, init) {
    if (typeof input !== "string") return raw(input, init);
    const rel = input.replace(location.origin, "");
    if (!API.test(rel) || SKIP.test(rel)) return raw(input, init);
    const method = String((init && init.method) || "GET").toUpperCase();
    const isLamp = method === "GET" && LAMP.test(rel);
    let opts = init;
    // a token that already got a 401 is replaced by the refreshed one up front
    const b = bearerOf(opts);
    if (b && dead.has(b)) { const cur = getTok(); if (cur && cur !== b) opts = withTok(opts, cur); }
    let res = null, err = null, timeouts = 0;
    for (let k = 0; k < (isLamp ? 3 : 1); k += 1) {
      if(init?.signal?.aborted)throw new DOMException("Aborted", "AbortError");
      if (timeouts >= 2) break;
      if (k) await sleep(k === 1 ? 500 : 1500);
      let o = opts, timer = 0;
      if (isLamp && typeof AbortController !== "undefined" && !(init && init.signal)) {
        const ctl = new AbortController();
        timer = setTimeout(() => { timeouts += 1; ctl.abort(); }, 6000);
        o = Object.assign({}, opts || {}, { signal: ctl.signal });
      }
      try { res = await raw(input, o); err = null; } catch (e) { if(init?.signal?.aborted)throw e;res = null; err = e; } finally { clearTimeout(timer); }
      if (res && res.status === 401) {
        const sentTok = bearerOf(o);
        if (sentTok) dead.add(sentTok);
        const cur = getTok();
        const tok = cur && cur !== sentTok && !dead.has(cur) ? cur : await refresh();
        if (tok && tok !== sentTok) {
          opts = withTok(opts, tok);
          try { res = await raw(input, Object.assign({}, o, { headers: opts.headers })); err = null; } catch (e) { res = null; err = e; }
        }
      }
      if (res && !(isLamp && res.status >= 500)) return res;
    }
    if (res) return res;
    if (!isLamp) throw err || new TypeError("Failed to fetch");
    // readable message for the toast app.js shows (was "signal is aborted without reason")
    const e = new Error(timeouts ? "网络超时，请重试" : "网络不稳定，请重试");
    e.name = timeouts ? "AbortError" : "TypeError";
    throw e;
  };
})();
