(() => {
  if (window.__yycjCover) return;
  window.__yycjCover = true;
  function mediaSrc(u) {
    const s = String(u || "").trim();
    if (!s) return "";
    if (s.toLowerCase().startsWith("file_id:")) return mediaSrc(s.slice(8));
    if (s.startsWith("http") || s.startsWith("/")) return s;
    return "/api/media/file/" + encodeURIComponent(s);
  }
  function isVideo(u) {
    const s = String(u || "");
    return s.startsWith("BAAC") || s.startsWith("BQAC") || /\.(mp4|mov|webm)(\?|$)/i.test(s);
  }
  function coverOf(item) {
    if (!item) return "";
    if (item.cover_url) return mediaSrc(item.cover_url);
    const media = item.media || [];
    for (const m of media) {
      const raw = (m && (m.file_id || m.url)) || "";
      if ((m && m.type) === "video" || isVideo(raw)) {
        const thumb = mediaSrc((m && (m.thumb_file_id || m.thumbnail || m.preview_url)) || "");
        if (thumb && !isVideo(String(m.thumb_file_id || m.thumbnail || ""))) return thumb;
        continue;
      }
      const u = mediaSrc((m && (m.preview_url || m.thumb_file_id || m.file_id || m.url)) || "");
      if (u) return u;
    }
    for (const p of item.photos || []) {
      if (isVideo(p)) continue;
      const u = mediaSrc(p);
      if (u) return u;
    }
    return "";
  }
  // Card-sized image: /api/media/file/<photo id> -> /api/media/thumb/<photo id>?w=480 (server
  // downscales + caches; falls back to the original by itself). Videos / external URLs unchanged.
  function thumbOf(src, w) {
    const s = String(src || "");
    const m = s.match(/^\/api\/media\/file\/([^?#/]+)$/);
    if (!m) return s;
    let id = m[1];
    try { id = decodeURIComponent(id); } catch (e) {}
    if (isVideo(id) || !/^[A-Za-z0-9_-]{8,200}$/.test(id)) return s;
    return "/api/media/thumb/" + encodeURIComponent(id) + "?w=" + (w || 480);
  }
  window.yycjThumb = thumbOf;
  window.yycjMediaSrc = mediaSrc;
  window.yycjIsVideo = isVideo;
  window.yycjCoverOf = coverOf;
})();
