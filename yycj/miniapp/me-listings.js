(() => {
  if (window.__yycjMeListings) return;
  window.__yycjMeListings = true;
  const token = () => localStorage.getItem("yycj_token") || "";
  function role() {
    try { return JSON.parse(localStorage.getItem("yycj_user") || "{}").role || "guest"; }
    catch (e) { return "guest"; }
  }
  function mount() {
    const me = document.getElementById("view-me");
    if (!me) return;
    if (!document.getElementById("myReviews")) {
      const hold = document.createElement("div");
      hold.id = "myReviews";
      hold.className = "hidden";
      me.appendChild(hold);
    }
    if (role() === "guest") {
      const old = document.getElementById("myListings");
      if (old && old.parentElement) old.parentElement.remove();
      return;
    }
    if (document.getElementById("myListings")) return;
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = "<h3>我的上架</h3><div id=\"myListings\" class=\"muted\">加载中…</div>";
    const first = me.querySelector(".card");
    if (first) first.after(card);
    else me.prepend(card);
  }
  function esc(t) {
    return String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&":"&amp;","<":"&lt;",">":"&gt;",'"':'&quot;',"'":"&#39;" }[c]));
  }
  function shiftHtml(sh, lead) {
    const code = sh.code || "unset";
    if (code === "unset" || !sh.hours) {
      return `<div class="shift-head"><span class="shift-pill" data-code="unset">未设营业时间</span></div>
        <p class="shift-note muted">点「改资料」设置营业时间后，首页才会显示 营业中 / 未到点 / 已打烊。</p>`;
    }
    const word = sh.shift_word || "今天";
    const when = sh.shift_start ? `${word} ${sh.shift_start} 这一班` : "今天";
    let note;
    if (sh.auto && sh.choice !== "rest") note = `自动营业：到点自动显示营业中，不用每天确认。`;
    else if (sh.choice === "open") note = `已确认 ${when} 开课。`;
    else if (sh.choice === "rest") note = sh.missed ? `${when}没回复提醒，按休息算；${sh.remind_on ? "明天照常提醒" : ""}` : `已设 ${when} 休息，只管这一次。`;
    else note = sh.remind_on ? `${when}还没确认，开工前 ${lead} 分钟机器人会问你。` : `${when}还没确认（每日提醒已关）。`;
    const on = (a) => (sh.choice === a ? " on" : "");
    if (sh.lasting) {
      note = "已设持续休息，不会按营业时间自动开课。";
    }
    const btns = sh.lasting
      ? `<button class="btn" type="button" data-act="open">今日开课</button><button class="btn primary" type="button" data-act="resume">恢复营业</button>`
      : `<button class="btn${on("open")}" type="button" data-act="open">今日开课</button><button class="btn${on("rest")}" type="button" data-act="rest">今日休息</button><button class="btn" type="button" data-act="pause">持续休息</button>`;
    return `<div class="shift-head"><span class="shift-pill" data-code="${esc(code)}">${esc(sh.text || "")}</span><span class="muted">营业 ${esc(sh.hours)}</span></div>
      <p class="shift-note muted">${esc(note)}</p>
      <div class="shift-btns">${btns}</div>
      <label class="shift-sw"><span>每日开课提醒<small>开工前 ${lead} 分钟私聊问你，关掉就不再问</small></span><input type="checkbox" data-sw="remind"${sh.remind_on ? " checked" : ""}${sh.auto ? " disabled" : ""} /></label>
      <label class="shift-sw"><span>按营业时间自动营业<small>不用每天确认，休息时点「今日休息」或「持续休息」</small></span><input type="checkbox" data-sw="auto"${sh.auto ? " checked" : ""} /></label>`;
  }
  async function postShift(id, action) {
    const r = await fetch("/api/me/listings/" + encodeURIComponent(id) + "/shift", {
      method: "POST", headers: { "Content-Type": "application/json", Authorization: "Bearer " + token() },
      body: JSON.stringify({ action }),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || "操作失败");
    return data;
  }
  function toast(msg) {
    const el = document.getElementById("toast");
    if (!el) return;
    el.textContent = msg;
    el.classList.remove("hidden");
    setTimeout(() => el.classList.add("hidden"), 2600);
  }
  function confirmBox({ title, body, ok, danger }) {
    return new Promise((resolve) => {
      let wrap = document.getElementById("yycjDlg");
      if (!wrap) {
        wrap = document.createElement("div");
        wrap.id = "yycjDlg";
        wrap.className = "yycj-dlg hidden";
        document.body.appendChild(wrap);
      }
      wrap.innerHTML = `<div class="yycj-dlg-card"><h3>${esc(title)}</h3>${body}<div class="yycj-dlg-btns">
        <button type="button" class="btn${danger ? " danger" : " primary"}" data-ok="1">${esc(ok)}</button>
        <button type="button" class="btn" data-cancel="1">取消</button></div></div>`;
      wrap.classList.remove("hidden");
      const done = (v) => { wrap.classList.add("hidden"); wrap.innerHTML = ""; resolve(v); };
      wrap.querySelector("[data-ok]").onclick = () => done(true);
      wrap.querySelector("[data-cancel]").onclick = () => done(false);
      wrap.onclick = (e) => { if (e.target === wrap) done(false); };
    });
  }
  async function confirmPause() {
    return confirmBox({
      title: "要一直休息到你自己恢复吗？",
      body: `<p class="muted">资料仍在首页和目录里，状态显示休息，不会按营业时间自动变成营业中。已有的会话照常处理。</p>
        <p class="muted">如果只想歇今天，请关掉这个窗口，改点「今日休息」。</p>`,
      ok: "确认持续休息",
    });
  }
  async function confirmUnlist(title, exp) {
    const when = exp ? String(exp).slice(0, 10) : "";
    return confirmBox({
      title: "确定把「" + (title || "这条资料") + "」下架？",
      body: `<p class="muted">下架后，首页、筛选和按区目录都看不到，也不能再发起新的「想聊聊」。资料、评价、信用和已有会话都会保留。</p>
        <p class="muted">${when ? "到期时间还是 " + esc(when) + "，" : ""}下架期间天数照常计算，过期后要请管理员续期才能再上。管理员或平台下架的条目，不能自己恢复。</p>`,
      ok: "确认下架",
      danger: true,
    });
  }
  async function postUnlist(id, relist, payload) {
    const r = await fetch("/api/me/listings/" + encodeURIComponent(id) + (relist ? "/relist" : "/unlist"), {
      method: "POST", headers: { "Content-Type": "application/json", Authorization: "Bearer " + token() },
      ...(relist?{body:JSON.stringify(payload)}:{}),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || "操作失败");
    return data;
  }
  function listStatus(x) {
    if (x.status === "active") return "已上架";
    if (x.status === "hidden") {
      const r = x.unlist_reason || "";
      if (r === "teacher") return "自己下架";
      if (r === "expired") return "已到期";
      if (r === "banned") return "已下架（平台）";
      return "已下架（管理员）";
    }
    return x.status || "";
  }
  function bindShift(el, lead) {
    const id = el.getAttribute("data-shift");
    el.addEventListener("click", async (ev) => {
      const b = ev.target.closest("[data-act]");
      if (!b) return;
      const act = b.getAttribute("data-act");
      try {
        if (act === "pause" && !(await confirmPause())) return;
        const d = await postShift(id, act); el.innerHTML = shiftHtml(d.shop || {}, lead); toast(d.message || "已保存");
      }
      catch (e) { toast(e.message || String(e)); }
    });
    el.addEventListener("change", async (ev) => {
      const sw = ev.target.closest("[data-sw]");
      if (!sw) return;
      const action = sw.getAttribute("data-sw") + (sw.checked ? "_on" : "_off");
      try { const d = await postShift(id, action); el.innerHTML = shiftHtml(d.shop || {}, lead); toast(d.message || "已保存"); }
      catch (e) { sw.checked = !sw.checked; toast(e.message || String(e)); }
    });
  }
  // 近 7 天 / 累计：浏览（每人每天算一次，自己看不算）· 收藏 · 匿名联系（可由管理台全站开关隐藏）
  function statsHtml(x, show) {
    const s = x && x.stats;
    if (!s || x.status !== "active") return "";
    const n = (v) => Number(v || 0);
    const cell = (k, a, b) => `<span class="ls-cell"><b>${n(a)}</b><i>${k}</i><em>累计 ${n(b)}</em></span>`;
    const flags = show || { views: true, favs: true, contacts: true };
    const parts = [];
    if (flags.views !== false) parts.push(cell("浏览", s.views_7d, s.views));
    if (flags.favs !== false) parts.push(cell("收藏", s.favs_7d, s.favs));
    if (flags.contacts !== false) parts.push(cell("匿名联系", s.contacts_7d, s.contacts));
    if (!parts.length) return "";
    return `<div class="ls-stats" style="grid-template-columns:auto repeat(${parts.length},minmax(0,1fr))" aria-label="近7天数据"><span class="ls-k">近 7 天</span>${parts.join("")}</div>`;
  }
  async function load() {
    mount();
    const box = document.getElementById("myListings");
    if (!box || !token() || role() === "guest") return;
    try {
      const r = await fetch("/api/me/listings", { headers: { Authorization: "Bearer " + token() } });
      const data = await r.json();
      const items = data.items || [];
      if (!items.length) {
        box.innerHTML = "<p class='muted'>还没有上架记录</p>";
        return;
      }
      const lead = data.remind_lead_min || 30;
      const show = data.stats_show || { views: true, favs: true, contacts: true };
      box.innerHTML = items.map((x) => {
        const st = listStatus(x);
        const exp = x.expires_at ? ("到期 " + esc(String(x.expires_at).slice(0, 10))) : "";
        const canRelist = x.status === "hidden" && x.unlist_reason === "teacher";
        const hiddenNote = canRelist
          ? `<p class="shift-note muted">首页和目录已隐藏。有效期内资料没改，可以直接恢复上架。</p>`
          : (x.status === "hidden" && x.unlist_reason === "expired"
            ? `<p class="shift-note muted">已到期，请联系管理员续期后再上架。</p>`
            : (x.status === "hidden" ? `<p class="shift-note muted">管理员或平台下架的条目，不能自己恢复。</p>` : ""));
        const acts = `<div class="ls-acts">${x.status === "active" ? `<button class="btn danger" type="button" data-unlist="${esc(x.lamp_id)}">下架</button>` : ""}${canRelist ? `<button class="btn primary" type="button" data-relist="${esc(x.lamp_id)}">重新上架</button>` : ""}
          <button class="btn" type="button" data-edit="${esc(x.lamp_id)}">修改资料</button>
          <details class="ls-more"><summary>更多</summary><div class="ls-more-menu"><button class="btn" type="button" data-history="${esc(x.lamp_id)}" data-history-scope="me">修改记录</button></div></details></div>${window.__yycjEditUI?.html(x.edit_state,"me",{card:true})||""}${window.__yycjEditUI?.html(x.relist_state,"me",{card:true})||""}`;
        return `<div class="pick-item" style="margin:8px 0" data-lamp="${esc(x.lamp_id)}">
          <div><strong>${esc(x.title || "")}</strong>
          <div class="muted">${esc(x.city || "")} · ${esc(st)} · ${exp}</div>${statsHtml(x, show)}${hiddenNote}${acts}</div>
        </div>${x.status === "active" ? `<div class="shift-box" data-shift="${esc(x.lamp_id)}">${shiftHtml(x.shop || {}, lead)}</div>` : ""}`;
      }).join("");
      box.querySelectorAll("[data-shift]").forEach((el) => bindShift(el, lead));
      box.querySelectorAll("[data-unlist]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          const id = btn.getAttribute("data-unlist");
          const item = items.find((x) => x.lamp_id === id);
          try {
            if (!(await confirmUnlist(item && item.title, item && item.expires_at))) return;
            await postUnlist(id, false);
            toast("已下架");
            load();
          } catch (e) { toast(e.message || String(e)); }
        });
      });
      box.querySelectorAll("[data-relist]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          try { const id=btn.getAttribute("data-relist"),item=items.find(x=>x.lamp_id===id);btn.disabled=true;
            const key='relist:'+id,body=window.__yycjEditUI.request({expected_updated_at:item.updated_at},key);
            const result=await postUnlist(id,true,body);window.__yycjEditUI.clear(key);toast(result.message||'重新上架已处理，请查看分步交付状态');load(); }
          catch (e) { btn.disabled=false;toast(e.message || String(e)); }
        });
      });
      box.querySelectorAll("[data-edit]").forEach((btn) => {
        btn.addEventListener("click", () => {
          const id = btn.getAttribute("data-edit");
          const item = items.find((x) => x.lamp_id === id);
          document.querySelector('[data-nav="publish"]')?.click();
          const form = document.getElementById("publishForm");
          if (!form || !item) return;
          let h = document.getElementById("editLampId");
          if (!h) { h = document.createElement("input"); h.type = "hidden"; h.id = "editLampId"; form.appendChild(h); }
          h.value = item.lamp_id;
          const set = (id, v) => { const el = document.getElementById(id); if (el) el.value = v || ""; };
          set("pubTitle", item.title);
          set("pubCity", item.city);
          set("pubDesc", item.description);
          set("pubPrice", item.price_text);
          set("pubDistrict", item.district);
          set("pubApprox", item.approx_label);
          set("pubTags", (item.tags || []).join(" "));
          const extras = item.extras || {};
          const hp = document.getElementById("pubHours");
          const hv = (item.hours && item.hours.length) ? item.hours : (extras["营业时间"] || []);
          if (window.yycjHours && hp) { window.yycjHours.set(hp, hv); setTimeout(() => window.yycjHours.set(hp, hv), 200); }
          document.querySelectorAll("[data-extra]").forEach((inp) => {
            const k = inp.getAttribute("data-extra");
            if (k && extras[k] != null) inp.value = extras[k];
          });
          const t = document.getElementById("publishTitle");
          if (t) t.textContent = "修改资料";
          let note=form.querySelector('.edit-review-note');if(!note){note=document.createElement('p');note.className='edit-review-note';form.prepend(note);}
          note.textContent=(item.status==='active' && item.shop?.code!=='off')?'文字保存立即生效；新图片/视频整组替换须审核，审核前原媒体继续展示。修改不续期。':'当前未上架：修改须审核，通过后仍保持原状态，不自动上架或续期。';
        });
      });
    } catch (e) {
      box.textContent = "加载失败";
    }
  }
  document.addEventListener("click", (ev) => {
    // closest(): a tap usually lands on the <span> label inside the nav button
    if (ev.target && ev.target.closest && ev.target.closest("[data-nav='me']")) setTimeout(load, 200);
  });
  if (document.readyState === "complete") { mount(); setTimeout(load, 1200); }
  else window.addEventListener("load", () => { mount(); setTimeout(load, 1200); });
})();
