(() => {
  if (window.__yycjAdminUx) return;
  window.__yycjAdminUx = true;
  function ensureUi() {
    if (!document.getElementById("adToast")) {
      const t = document.createElement("div");
      t.id = "adToast"; t.className = "ad-toast hidden"; document.body.appendChild(t);
    }
    if (!document.getElementById("adConfirm")) {
      const m = document.createElement("div");
      m.id = "adConfirm"; m.className = "ad-modal hidden";
      m.innerHTML = `<div class="ad-modal-card"><p id="adConfirmText"></p><div class="row"><button class="btn" type="button" id="adConfirmNo">取消</button><button class="btn primary" type="button" id="adConfirmOk">确定</button></div></div>`;
      document.body.appendChild(m);
    }
    if (!document.getElementById("adSaveBar")) {
      const bar = document.createElement("div");
      bar.id = "adSaveBar"; bar.className = "ad-savebar hidden";
      bar.innerHTML = `<span>设置已修改，尚未保存</span><button class="btn primary" type="button" id="adSaveNow">保存</button>`;
      document.body.appendChild(bar);
    }
  }
  ensureUi();
  let toastTimer = 0;
  function toast(msg, kind) {
    const el = document.getElementById("adToast");
    if (!el) return alert(msg);
    el.textContent = msg; el.className = "ad-toast " + (kind || "ok");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { el.className = "ad-toast hidden"; }, 2400);
  }
  function confirmBox(text) {
    return new Promise((resolve) => {
      const modal = document.getElementById("adConfirm");
      document.getElementById("adConfirmText").textContent = text;
      modal.classList.remove("hidden");
      const ok = document.getElementById("adConfirmOk");
      const no = document.getElementById("adConfirmNo");
      const done = (v) => { modal.classList.add("hidden"); ok.onclick = no.onclick = modal.onclick = null; resolve(v); };
      ok.onclick = () => done(true);
      no.onclick = () => done(false);
      modal.onclick = (e) => { if (e.target === modal) done(false); };
    });
  }
  window.yycjToast = toast; window.yycjConfirm = confirmBox;
  function polishListings() {
    document.querySelectorAll("#adminListings .pick-item").forEach((card) => {
      const row = card.querySelector(".row");
      const muted = card.querySelector(".muted");
      if (!row || !muted) return;
      if (card.dataset.ux === "1") {
        const menu = card.querySelector(".ad-more-menu");
        const leftover = row.querySelector("[data-fill-proxy]");
        if (leftover && menu && !menu.contains(leftover)) menu.prepend(leftover);
        return;
      }
      card.dataset.ux = "1";
      const on = /已上架/.test(muted.textContent || "");
      const strong = card.querySelector("strong");
      if (strong && !strong.querySelector(".st-badge")) {
        const badge = document.createElement("span");
        badge.className = "st-badge " + (on ? "st-active" : "st-hidden");
        badge.textContent = on ? "已上架" : "已下架";
        strong.appendChild(badge);
      }
      const unlist = row.querySelector("[data-list='unlist']");
      const relist = row.querySelector("[data-list='relist']");
      const renew = row.querySelector("[data-list='renew']");
      const del = row.querySelector("[data-list='delete']");
      const proxy = row.querySelector("[data-fill-proxy]");
      const more = document.createElement("div");
      more.className = "ad-more";
      more.innerHTML = `<button class="btn" type="button" data-more>更多</button><div class="ad-more-menu hidden"></div>`;
      const menu = more.querySelector(".ad-more-menu");
      [proxy, renew, on ? null : relist, on ? unlist : null, del].forEach((b) => {
        if (!b) return;
        if (b.getAttribute("data-list") === "renew") b.textContent = "续期";
        menu.appendChild(b);
      });
      const primary = document.createElement("button");
      primary.className = "btn " + (on ? "danger" : "primary");
      primary.type = "button";
      if (on) { primary.textContent = "下架"; primary.setAttribute("data-list", "unlist"); if (unlist) primary.setAttribute("data-id", unlist.getAttribute("data-id")); }
      else { primary.textContent = "重新上架"; primary.setAttribute("data-list", "relist"); if (relist) primary.setAttribute("data-id", relist.getAttribute("data-id")); }
      row.innerHTML = ""; row.appendChild(primary); row.appendChild(more);
    });
  }
  document.addEventListener("click", (ev) => {
    const more = ev.target.closest("[data-more]");
    if (more) {
      ev.preventDefault(); ev.stopPropagation();
      const menu = more.parentElement.querySelector(".ad-more-menu");
      document.querySelectorAll(".ad-more-menu").forEach((x) => { if (x !== menu) x.classList.add("hidden"); });
      menu?.classList.toggle("hidden");
      return;
    }
    if (!ev.target.closest(".ad-more")) document.querySelectorAll(".ad-more-menu").forEach((x) => x.classList.add("hidden"));
  }, true);
  const listBox = document.getElementById("adminListings");
  if (listBox) new MutationObserver(() => polishListings()).observe(listBox, { childList: true, subtree: true });
  setTimeout(polishListings, 1500);
  document.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("[data-list='unlist'], [data-list='delete'], [data-admin='post-no']");
    if (!btn || btn.dataset.confirmed === "1") return;
    ev.preventDefault(); ev.stopPropagation();
    let msg = "确定下架这份资料？";
    if (btn.getAttribute("data-admin") === "post-no") msg = "确定拒绝这份投稿？";
    else if (btn.getAttribute("data-list") === "delete") {
      const t = btn.getAttribute("data-title") || "这份资料";
      msg = "确定永久删除「" + t + "」？删掉后不能恢复，只是想藏起来请用「下架」。";
    }
    if (!(await confirmBox(msg))) return;
    btn.dataset.confirmed = "1"; btn.click();
    setTimeout(() => { delete btn.dataset.confirmed; }, 500);
  }, true);
  const venue = document.getElementById("pane-venue");
  function markDirty() {
    document.body.classList.add("ops-dirty");
    document.getElementById("adSaveBar")?.classList.remove("hidden");
  }
  venue?.querySelectorAll("input, textarea, select").forEach((el) => {
    el.addEventListener("input", markDirty); el.addEventListener("change", markDirty);
  });
  document.getElementById("adSaveNow")?.addEventListener("click", () => document.getElementById("btnOpsSave")?.click());
  document.getElementById("btnOpsSave")?.addEventListener("click", () => {
    setTimeout(() => {
      document.body.classList.remove("ops-dirty");
      document.getElementById("adSaveBar")?.classList.add("hidden");
      toast("设置已保存");
    }, 400);
  });
  const origFetch = window.fetch;
  window.fetch = async function (url, opt) {
    const res = await origFetch.apply(this, arguments);
    try {
      const u = String(url || "");
      const method = String((opt && opt.method) || "GET").toUpperCase();
      if (res.ok && method === "POST") {
        if (u.includes("/approve-pin")) toast("已通过并上轮播");
        else if (u.includes("/posts/") && u.includes("/approve")) toast("已通过审核");
        else if (u.includes("/posts/") && u.includes("/reject")) toast("已拒绝");
        else if (u.includes("/reviews/") && u.includes("/approve")) toast("评价已通过");
      }
    } catch (e) {}
    return res;
  };
})();
