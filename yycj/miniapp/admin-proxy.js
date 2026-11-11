(() => {
  const pane = document.getElementById("pane-listings");
  if (!pane) return;
  const CORE = new Set(["称呼", "城市", "价位", "简介", "地点", "链接", "聊天按钮"]);
  const ID_MAP = { "区域": "District", "大致位置": "Approx", "标签": "Tags" };
  function esc(t) {
    return String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':'&quot;',"'":"&#39;" }[c]));
  }
  function extraHtml(prefix, keys) {
    return keys.map((k) => {
      const id = ID_MAP[k] ? (prefix + ID_MAP[k]) : "";
      const extra = ID_MAP[k] ? "" : ` data-extra="${esc(k)}"`;
      const ph = k === "标签" ? " placeholder=\"空格分隔\"" : "";
      return `<label>${esc(k)}</label><input${id ? ` id="${id}"` : ""}${extra}${ph} maxlength="64" />`;
    }).join("");
  }
  if (!document.getElementById("proxyBox")) {
    const box = document.createElement("div");
    box.id = "proxyBox";
    box.className = "card";
    box.innerHTML = `<h3>代老师上架（免审）</h3>
      <p class="muted">资料挂在老师 ID 上，客人点联系会打给老师。</p>
      <label>老师用户 ID</label><input id="proxyUid" type="number" />
      <label>称呼</label><input id="proxyTitle" />
      <label>城市</label><input id="proxyCity" value="深圳" />
      <label>价位</label><input id="proxyPrice" />
      <label>简介</label><textarea id="proxyDesc" rows="3"></textarea>
      <div id="proxyExtras"></div>
      <div id="proxyHours"></div>
      <label>相册</label>
      <input id="proxyFiles" type="file" accept="image/*,video/*" multiple />
      <div id="proxyStatus" class="muted"></div>
      <div id="proxyPreview" class="media-preview"></div>
      <button class="btn primary" type="button" id="btnProxy">直接上架</button>`;
    pane.prepend(box);
  }
  if (!document.getElementById("editBox")) {
    const box = document.createElement("div");
    box.id = "editBox";
    box.className = "card";
    box.innerHTML = `<h3>代修改资料（免审）</h3><p class="edit-review-note">授权城市内免审保存。文字同步原帖；替换媒体后，仍上架的资料重新推送完整帖并通知老师。未上架仅保存，不续期、不恢复上架。</p><div id="editOperationState"></div>
      <p class="muted">从下方资料列表点「代改」填入，保存后覆盖资料并更新频道文案。</p>
      <input id="editLampId" type="hidden" />
      <label>资料</label><input id="editLampLabel" readonly placeholder="先在列表点代改" />
      <label>称呼</label><input id="editTitle" />
      <label>城市</label><input id="editCity" />
      <label>价位</label><input id="editPrice" />
      <label>简介</label><textarea id="editDesc" rows="3"></textarea>
      <div id="editExtras"></div>
      <div id="editHours"></div>
      <label>相册（可选，选了就替换）</label>
      <input id="editFiles" type="file" accept="image/*,video/*" multiple />
      <div id="editStatus" class="muted"></div>
      <div id="editPreview" class="media-preview"></div>
      <button class="btn primary" type="button" id="btnEditSave">保存修改</button>`;
    const proxy = document.getElementById("proxyBox");
    if (proxy && proxy.nextSibling) pane.insertBefore(box, proxy.nextSibling);
    else pane.appendChild(box);
  }
  if (window.yycjHours) {
    window.yycjHours.mount(document.getElementById("proxyHours"));
    window.yycjHours.mount(document.getElementById("editHours"));
  }
  const hoursOf = (id) => (window.yycjHours ? window.yycjHours.get(document.getElementById(id)) : undefined);
  const token = () => localStorage.getItem("yycj_token") || "";
  const uploadedP = [];
  const uploadedE = [];
  function dirty() {
    if(window.yycjWorkspace){window.yycjWorkspace.legacyDirty=false;window.yycjWorkspace.dirty=false;}
    localStorage.setItem("yycj_bust", String(Date.now()));
    if (window.yycjRefreshViews) window.yycjRefreshViews();
    document.getElementById("btnRefresh")?.click();
  }
  function collectExtras(boxId) {
    const out = {};
    document.querySelectorAll("#" + boxId + " [data-extra]").forEach((el) => {
      const k = (el.getAttribute("data-extra") || "").trim();
      const v = (el.value || "").trim();
      if (k && v) out[k] = v;
    });
    return out;
  }
  async function syncExtras() {
    let fields = [];
    try {
      const r = await fetch("/api/home?limit=1", { headers: { Authorization: "Bearer " + token() } });
      fields = ((await r.json()).listing_fields) || [];
    } catch (e) {}
    const keys = (fields || []).filter((f) => f && f.key && f.form !== false)
      .map((f) => String(f.key))
      .filter((k) => !CORE.has(k));
    const proxyWrap = document.getElementById("proxyExtras");
    const editWrap = document.getElementById("editExtras");
    if (proxyWrap) proxyWrap.innerHTML = extraHtml("proxy", keys);
    if (editWrap) editWrap.innerHTML = extraHtml("edit", keys);
  }
  function preview(elId, list) {
    const el = document.getElementById(elId);
    if (!el) return;
    el.innerHTML = list.map((m) => {
      if (m.pending) return `<div class="up-thumb" style="height:88px;display:flex;align-items:center;justify-content:center;background:#111;border-radius:8px">上传中</div>`;
      const src = m.local || m.preview_url || "";
      return m.type === "video"
        ? `<video class="up-thumb" src="${esc(src)}" muted playsinline preload="metadata" style="width:100%;height:88px;object-fit:cover;border-radius:8px;background:#111"></video>`
        : `<img class="up-thumb" src="${esc(src)}" style="width:100%;height:88px;object-fit:cover;border-radius:8px" alt="" />`;
    }).join("");
  }
  async function uploadFiles(files, bucket, previewId, statusId) {
    const status = document.getElementById(statusId);
    const arr = [...files];
    for (let i = 0; i < arr.length; i++) {
      const file = arr[i];
      const local = URL.createObjectURL(file);
      const item = { type: file.type.startsWith("video") ? "video" : "image", local, pending: true };
      bucket.push(item);
      if (status) status.textContent = "正在上传 " + (i + 1) + "/" + arr.length;
      preview(previewId, bucket);
      try {
        const fd = new FormData();
        fd.append("files", file);
        const r = await fetch("/api/media/upload", { method: "POST", headers: { Authorization: "Bearer " + token() }, body: fd });
        const data = await r.json().catch(() => ({}));
        const one = (data.items && data.items[0]) || data;
        item.pending = false;
        item.file_id = one.file_id || "";
        item.preview_url = one.preview_url || one.thumb_url || local;
        item.url = one.url || item.file_id;
      } catch (e) {
        item.pending = false;
        item.failed = true;
        if (status) status.textContent = "上传失败";
      }
      preview(previewId, bucket);
    }
    if (status) status.textContent = "上传完成";
  }
  function mediaOf(bucket) {
    return bucket.filter((m) => !m.failed && !m.pending).map((m) => ({
      type: m.type, file_id: m.file_id, url: m.url || m.file_id, preview_url: m.preview_url || ""
    }));
  }
  function val(id) { return (document.getElementById(id) || {}).value || ""; }
  document.getElementById("proxyFiles")?.addEventListener("change", (ev) => {
    uploadFiles([...(ev.target.files || [])], uploadedP, "proxyPreview", "proxyStatus");
    ev.target.value = "";
  });
  document.getElementById("editFiles")?.addEventListener("change", (ev) => {
    uploadFiles([...(ev.target.files || [])], uploadedE, "editPreview", "editStatus");
    ev.target.value = "";
  });
  function lockEditor(id) {
    const w=window.yycjWorkspace;if(w?.editorBusy)return null;
    if(w)w.editorBusy=true;
    const nodes=[...document.querySelectorAll('#'+id+' input,#'+id+' textarea,#'+id+' select,#'+id+' button')],states=nodes.map(n=>n.disabled);
    nodes.forEach(n=>n.disabled=true);
    return ()=>{if(w)w.editorBusy=false;nodes.forEach((n,i)=>n.disabled=states[i])};
  }
  window.yycjAdminEditors={reset(){
    uploadedP.length=0;uploadedE.length=0;
    for(const prefix of ['proxy','edit']){
      const box=document.getElementById(prefix==='proxy'?'proxyBox':'editBox');
      box?.querySelectorAll('input,textarea').forEach(el=>{if(el.type==='checkbox')el.checked=el.defaultChecked;else el.value=''});
      if(window.yycjHours)window.yycjHours.set(document.getElementById(prefix+'Hours'),[]);
      preview(prefix+'Preview',[]);document.getElementById(prefix+'Status').textContent='';
    }
    document.getElementById('editOperationState').innerHTML='';for(const id of ['workspaceProxyDetails','workspaceEditDetails']){const d=document.getElementById(id);if(d)d.open=false;}
    document.getElementById('proxyCity').value=window.yycjWorkspace?.mode==='city'?window.yycjWorkspace.city:'';
  }};
  document.getElementById("btnProxy")?.addEventListener("click", async () => {
    const uid = parseInt(val("proxyUid"), 10);
    if (!uid) return alert("请填老师用户 ID");
    if(uploadedP.some(x=>x.pending))return alert("媒体仍在上传，请完成后再上架");
    const unlock=lockEditor("proxyBox");if(!unlock)return;
    try {
      const r = await fetch("/api/admin/listings/proxy", {
        method: "POST",
        headers: { Authorization: "Bearer " + token(), "Content-Type": "application/json" },
        body: JSON.stringify({
          target_user_id: uid,
          title: val("proxyTitle"),
          city: val("proxyCity"),
          price_text: val("proxyPrice"),
          description: val("proxyDesc"),
          district: val("proxyDistrict"),
          approx_label: val("proxyApprox"),
          tags: val("proxyTags").split(/[,，\s]+/).filter(Boolean),
          extras: collectExtras("proxyBox"),
          hours: hoursOf("proxyHours"),
          media: mediaOf(uploadedP),
          publisher_role: "teacher",
        }),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || "失败");
      alert("已代上架并按模板推送：" + ((data.lamp || {}).title || ""));
      uploadedP.length = 0; preview("proxyPreview", uploadedP);
      dirty();
    } catch (e) { alert(e.message || String(e)); } finally { unlock(); }
  });
  document.getElementById("btnEditSave")?.addEventListener("click", async () => {
    const id = val("editLampId");
    if (!id) return alert("请先在资料列表点「代改」");
    if(uploadedE.some(x=>x.pending))return alert("媒体仍在上传，请完成后再保存");
    const unlock=lockEditor("editBox");if(!unlock)return;
    try {
      const r = await fetch("/api/admin/listings/" + encodeURIComponent(id) + "/proxy-edit", {
        method: "POST",
        headers: { Authorization: "Bearer " + token(), "Content-Type": "application/json" },
        body: JSON.stringify(window.__yycjEditUI.request({
          title: val("editTitle"),
          city: val("editCity"),
          price_text: val("editPrice"),
          description: val("editDesc"),
          district: val("editDistrict"),
          approx_label: val("editApprox"),
          tags: val("editTags").split(/[,，\s]+/).filter(Boolean),
          extras: collectExtras("editBox"),
          hours: hoursOf("editHours"),
          media: mediaOf(uploadedE),
        },"admin:"+id)),
      });
      const data = await r.json();
      if (!r.ok) throw new Error(data.detail || "失败");
      window.__yycjEditUI.clear("admin:"+id);
      document.getElementById("editOperationState").innerHTML=window.__yycjEditUI.html(data.operation,"admin");
      uploadedE.length = 0; preview("editPreview", uploadedE);
      dirty();
    } catch (e) { alert(e.message || String(e)); } finally { unlock(); }
  });
  window.yycjFillProxy = function (item) {
    if (!item) return;
    const details=document.getElementById("workspaceEditDetails");if(details)details.open=true;
    const set = (id, v) => { const el = document.getElementById(id); if (el) el.value = v || ""; };
    const extras = item.extras || {};
    const resultBox=document.getElementById('editOperationState');if(resultBox)resultBox.innerHTML=window.__yycjEditUI?.html(item.edit_state,'admin')||'';
    fetch('/api/admin/listings/'+encodeURIComponent(item.lamp_id)+'/edit-state',{headers:{Authorization:'Bearer '+token()}}).then(r=>r.ok?r.json():null).then(d=>{if(d && val('editLampId')===item.lamp_id && resultBox)resultBox.innerHTML=window.__yycjEditUI.html(d.operation,'admin');}).catch(()=>{});
    set("editLampId", item.lamp_id);
    set("editLampLabel", (item.title || "") + " · " + (item.city || ""));
    set("editTitle", item.title);
    set("editCity", item.city);
    set("editPrice", item.price_text);
    set("editDesc", item.description);
    set("editDistrict", item.district || extras["区域"]);
    set("editApprox", item.approx_label || extras["大致位置"]);
    set("editTags", (item.tags || []).join(" "));
    document.querySelectorAll("#editBox [data-extra]").forEach((el) => {
      el.value = extras[el.getAttribute("data-extra")] || "";
    });
    if (window.yycjHours) window.yycjHours.set(document.getElementById("editHours"), (item.hours && item.hours.length ? item.hours : null) || extras._hours || item.hours_text || extras["营业时间"] || []);
    document.getElementById("editBox")?.scrollIntoView({ behavior: "smooth", block: "start" });
  };
  // Desk and legacy console share the same reliable editor selection path.
  document.addEventListener('click',async ev=>{
    const btn=ev.target.closest('[data-fill-proxy]');if(!btn)return;
    ev.preventDefault();ev.stopImmediatePropagation();
    const id=btn.dataset.fillProxy;
    let item=[...(window.__yycjListings||[]),...(window.__yycjListingCache||[])].find(x=>x.lamp_id===id)||{lamp_id:id};
    try{const r=await fetch('/api/lamps/'+encodeURIComponent(id),{headers:{Authorization:'Bearer '+token()}});if(r.ok)item=(await r.json()).lamp||item;}catch{}
    window.yycjFillProxy(item);
    document.querySelector('#adminSide [data-pane="listings"]')?.click();
  },true);
  syncExtras();
  setTimeout(syncExtras, 1200);
})();
