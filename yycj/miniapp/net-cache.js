// Request de-duplication for the Mini App (loaded right after cover.js, before app.js).
// Several independent scripts ask for the same data at boot and on every tab tap:
//   GET /api/home  — app.js (limit=30, meta only), home-ui.js (the feed), detail-kit.js /
//                    share.js / publish-ui.js / admin-go.js (limit=1, contacts / settings only)
//   GET /api/me    — app.js, role-nav.js, admin-go.js, role-gate.js
// This wrapper shares in-flight requests and reuses a fresh answer for a few seconds, so each
// distinct question goes over the network once. Every caller still gets its own Response clone.
(() => {
  if (window.__yycjNetCache) return;
  window.__yycjNetCache = true;
  const raw = window.fetch.bind(window);
  const FEED_TTL = 10000;   // identical feed request
  const META_TTL = 60000;   // limit=1 / limit=30 callers only read settings, contacts, cities
  const ME_TTL = 4000;
  const homes = [];         // {key, city, t, p, ok}
  let me = null;            // {tok, t, p, ok}
  const stats = (window.__yycjNetStats = { home: 0, homeShared: 0, me: 0, meShared: 0 });
  function authOf(init, input) {
    try {
      const h = new Headers((init && init.headers) || (input && typeof input !== "string" && input.headers) || {});
      return h.get("Authorization") || "";
    } catch (e) { return ""; }
  }
  function track(entry) {
    entry.p.then((r) => { entry.ok = r.ok; entry.t = Date.now(); if (!r.ok) entry.dead = true; }, () => { entry.dead = true; });
    return entry;
  }
  function serve(entry, init) {
    const sig = init && init.signal;
    const out = entry.p.then((r) => r.clone());
    if (!sig) return out;
    if (sig.aborted) return Promise.reject(new DOMException("Aborted", "AbortError"));
    return new Promise((res, rej) => {
      sig.addEventListener("abort", () => rej(new DOMException("Aborted", "AbortError")), { once: true });
      out.then(res, rej);
    });
  }
  const fresh = (e, ttl) => e && !e.dead && (e.ok === undefined || Date.now() - e.t < ttl);
  function homeKey(u) {
    const q = u.searchParams;
    return ["city", "q", "district", "sort", "offset", "limit", "lat", "lng", "at", "radius", "price", "tags", "open", "service", "map_results", "coupon", "place"].map((k) => k + "=" + (q.get(k) || (k === "offset" ? "0" : ""))).join("&");
  }
  // home-ui.js exposes the URL of its current first page; a meta caller with nothing to reuse
  // asks for exactly that page so the feed load right after it is served from the same request.
  function canonicalFeed(city) {
    try {
      const f = window.__yycjFeedUrl && window.__yycjFeedUrl();
      if (!f) return "";
      const u = new URL(f, location.origin);
      if (city && (u.searchParams.get("city") || "") !== city) return "";
      return u.pathname + "?" + u.searchParams.toString();
    } catch (e) { return ""; }
  }
  window.fetch = function (input, init) {
    try {
      const url = typeof input === "string" ? input : (input && input.url) || "";
      const method = String((init && init.method) || (input && typeof input !== "string" && input.method) || "GET").toUpperCase();
      const u = new URL(url, location.origin);
      if (u.origin === location.origin) {
        if (method !== "GET") {
          // anything that changes the user (role, nick, auth) invalidates /api/me; posts/edits/favs
          // can change the feed, so drop cached home pages too.
          if (/^\/api\/(auth|me)/.test(u.pathname)) me = null;
          if (/^\/api\/(auth|posts|me\/listings|favorites|reviews|admin)/.test(u.pathname)) homes.length = 0;
          return raw(input, init);
        }
        const auth = authOf(init, input);
        if (u.pathname === "/api/me") {
          stats.me += 1;
          if (me && me.tok === auth && fresh(me, ME_TTL)) { stats.meShared += 1; return serve(me, init); }
          me = track({ tok: auth, t: Date.now(), p: raw(u.pathname + u.search, { headers: { Authorization: auth } }) });
          return serve(me, init);
        }
        if (u.pathname === "/api/home") {
          stats.home += 1;
          const q = u.searchParams;
          // Explicit map searches need fresh, cancellable transport, never a list/meta cache hit.
          if(q.get("map_results") === "1") return raw(input, init);
          const city = q.get("city") || "";
          const lim = q.get("limit") || "";
          const meta = (lim === "1" || lim === "30") && !q.get("q") && !q.get("offset");
          for (let i = homes.length - 1; i >= 0; i -= 1) if (homes[i].dead || Date.now() - homes[i].t > META_TTL) homes.splice(i, 1);
          const key = homeKey(u);
          let hit = null;
          for (let i = homes.length - 1; i >= 0 && !hit; i -= 1) {
            const e = homes[i];
            if (e.auth !== auth) continue;
            if (e.key === key && fresh(e, FEED_TTL)) hit = e;
            else if (meta && fresh(e, META_TTL) && (!city || e.city === city || (lim === "1" && !city))) hit = e;
          }
          if (hit) { stats.homeShared += 1; return serve(hit, init); }
          let target = u.pathname + u.search;
          let tkey = key;
          if (meta) {
            const c = canonicalFeed(city);
            if (c) { target = c; tkey = homeKey(new URL(c, location.origin)); }
          }
          // The feed is the same for every user, so a 200 answered with an older token is fine
          // to share; a failed request is never reused.
          const e = track({ auth, key: tkey, city: new URL(target, location.origin).searchParams.get("city") || city, t: Date.now(), p: raw(target, { headers: { Authorization: auth } }) });
          homes.push(e);
          if (homes.length > 12) homes.shift();
          return serve(e, init);
        }
      }
    } catch (e) {}
    return raw(input, init);
  };
  window.__yycjDropHomeCache = () => { homes.length = 0; };
})();
