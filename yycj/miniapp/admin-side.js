(() => {
  if (!document.getElementById("yycj-back-top")) {
    const s = document.createElement("script");
    s.id = "yycj-back-top";
    s.src = "./back-top.js?v=20261029d";
    document.body.appendChild(s);
  }
  if (window.__yycjAdminSide) return;
  window.__yycjAdminSide = true;
  const titles = { audit: "审核", listings: "上架资料", pin: "卡片置顶", carousel: "精选轮播", venue: "站点设置", users: "用户", more: "风控" };
  function crumb(pane) {
    const el = document.getElementById("adminCrumb");
    if (el) el.textContent = titles[pane] || "管理";
  }
  function openSide(on) {
    document.body.classList.toggle("side-open", on === undefined ? !document.body.classList.contains("side-open") : !!on);
  }
  document.addEventListener("click", (ev) => {
    const menu = ev.target.closest("#btnMenu");
    if (menu) {
      ev.preventDefault();
      ev.stopPropagation();
      openSide();
      return;
    }
    if (ev.target.closest("#sideMask")) {
      openSide(false);
      return;
    }
    const pane = ev.target.closest("#adminSide [data-pane]");
    if (pane) {
      crumb(pane.getAttribute("data-pane"));
      openSide(false);
    }
  }, true);
  const on = document.querySelector("#adminSide .active");
  if (on) crumb(on.getAttribute("data-pane"));
})();
