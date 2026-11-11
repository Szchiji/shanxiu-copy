// 写评价页：固定顶栏（返回 + 标题，和详情页一致）+ Telegram BackButton，返回详情并恢复滚动位置。
(() => {
  if (window.__yycjReviewBack) return;
  window.__yycjReviewBack = true;
  const view = document.getElementById("view-review");
  const detail = document.getElementById("view-detail");
  if (!view) return;
  const st = document.createElement("style");
  st.id = "yycj-review-back-css";
  st.textContent = `:root{--rv-safe:max(env(safe-area-inset-top,0px),calc(var(--tg-safe-area-inset-top,0px) + var(--tg-content-safe-area-inset-top,0px)));--rv-top-h:calc(48px + var(--rv-safe));}
body.rev-open .topbar,body.rev-open .bottom-nav{display:none!important;}
#revTop{position:fixed;top:0;left:0;right:0;z-index:60;display:none;grid-template-columns:48px minmax(0,1fr) 48px;align-items:center;box-sizing:border-box;height:var(--rv-top-h);padding:var(--rv-safe) 4px 0;background:var(--t-bar,rgba(11,16,32,.96));border-bottom:1px solid var(--t-line,rgba(255,255,255,.07));}
body.rev-open #revTop{display:grid;}
#revTop .xt-back{width:44px;height:44px;margin:0 0 0 2px;padding:0;border:0;border-radius:22px;background:none;display:flex;align-items:center;justify-content:center;-webkit-tap-highlight-color:transparent;}
#revTop .xt-back::before{content:"";width:11px;height:11px;border-left:2px solid var(--t-text,#e8ecff);border-bottom:2px solid var(--t-text,#e8ecff);transform:translateX(3px) rotate(45deg);}
#revTop .xt-back:active{background:rgba(255,255,255,.08);}
#revTop .xt-name{min-width:0;text-align:center;font-size:16px;font-weight:700;color:var(--t-text,#e8ecff);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
body.rev-open #view-review{padding-top:calc(var(--rv-top-h) + 4px);}`;
  document.head.appendChild(st);
  const top = document.createElement("div");
  top.id = "revTop";
  top.innerHTML = `<button type="button" class="xt-back" aria-label="返回"></button><div class="xt-name">写评价</div><span></span>`;
  document.body.appendChild(top);
  const tg = window.Telegram && window.Telegram.WebApp;
  let backY = 0;
  const shown = (el) => el && !el.classList.contains("hidden");
  function title() {
    const n = (document.querySelector("#xhsTop .xt-name")?.textContent || "").trim();
    top.querySelector(".xt-name").textContent = n ? "评价 · " + n : "写评价";
  }
  function goBack() {
    if (!shown(view)) return;
    view.classList.add("hidden");
    if (detail) detail.classList.remove("hidden");
    document.querySelectorAll(".nav-item").forEach((b) => b.classList.remove("active"));
    const y = backY;
    requestAnimationFrame(() => window.scrollTo(0, y));
  }
  top.querySelector(".xt-back").addEventListener("click", (ev) => { ev.preventDefault(); goBack(); });
  // Remember where the detail page was; deep-link detail (deep-open.js) has no handler of its own.
  document.addEventListener("click", (ev) => {
    if (!(ev.target.closest && ev.target.closest("#detailReview"))) return;
    backY = window.scrollY || document.documentElement.scrollTop || 0;
    title();
    setTimeout(() => {
      if (!shown(view) && window.__yycjOpenLamp) {
        const id = document.getElementById("revLampId");
        if (id) id.value = window.__yycjOpenLamp;
        document.querySelectorAll(".view").forEach((v) => v.classList.add("hidden"));
        view.classList.remove("hidden");
      }
      if (shown(view)) window.scrollTo(0, 0);
    }, 0);
  }, true);
  function sync() {
    const on = shown(view);
    document.body.classList.toggle("rev-open", on);
    try {
      if (tg && tg.BackButton) {
        if (on) tg.BackButton.show();
        else if (!shown(detail)) tg.BackButton.hide();
      }
    } catch (e) {}
  }
  try { if (tg && tg.onEvent) tg.onEvent("backButtonClicked", () => { if (shown(view)) goBack(); }); } catch (e) {}
  new MutationObserver(sync).observe(view, { attributes: true, attributeFilter: ["class"] });
  sync();
})();
