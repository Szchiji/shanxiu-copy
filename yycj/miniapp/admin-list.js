(() => {
  if (window.__yycjAdminList) return;
  window.__yycjAdminList = true;
  const token = () => localStorage.getItem("yycj_token") || "";
  const note = (msg) => (window.yycjToast || alert)(msg);
  async function call(path, body) {
    const r = await fetch(path, {
      method: "POST",
      headers: { Authorization: "Bearer " + token(), "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || r.statusText || "请求失败");
    return data;
  }
  document.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("[data-list]");
    if (!btn) return;
    ev.preventDefault();
    ev.stopPropagation();
    ev.stopImmediatePropagation();
    const id = btn.getAttribute("data-id");
    const act = btn.getAttribute("data-list");
    if (!id || !act) return;
    try {
      btn.disabled = true;
      if (act === "renew") {
        const raw = prompt("续期天数", "30");
        if (raw == null) return;
        const days = parseInt(raw, 10);
        if (!days || days < 1) return note("请输入有效天数");
        const key = "yycj_renew_pending:" + id;
        let pending; try { pending = JSON.parse(sessionStorage.getItem(key) || "null"); } catch {}
        if(!pending || pending.days !== days) pending = { days, operation_id: crypto.randomUUID() };
        sessionStorage.setItem(key, JSON.stringify(pending));
        const result = await call("/api/admin/listings/" + encodeURIComponent(id) + "/renew", pending);
        if(result.ok) sessionStorage.removeItem(key);
        note(result.message || "续期已处理，请核对频道和老师通知状态");
      } else if (act === "unlist") {
        await call("/api/admin/listings/" + encodeURIComponent(id) + "/unlist", { reason: "admin" });
        note("已下架");
      } else if (act === "relist") {
        const item=(window.__yycjListings||[]).find(x=>x.lamp_id===id);if(!item?.updated_at)throw Error('请刷新资料后重新上架');
        const key='relist:'+id,body=window.__yycjEditUI.request({expected_updated_at:item.updated_at},key);
        const result=await call("/api/admin/listings/"+encodeURIComponent(id)+"/relist",body);window.__yycjEditUI.clear(key);
        note(result.message||'重新上架已处理，请查看分步交付状态');
      } else if (act === "delete") {
        await call("/api/admin/listings/" + encodeURIComponent(id) + "/delete", {});
        note("已永久删除");
      } else return;
      document.getElementById("btnRefresh")?.click();
    } catch (e) {
      note(e.message || String(e));
    } finally {
      btn.disabled = false;
    }
  }, true);
})();
