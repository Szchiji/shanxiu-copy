(() => {
  if (document.getElementById("yycjTop")) return;
  const btn = document.createElement("button");
  btn.id = "yycjTop";
  btn.type = "button";
  btn.textContent = "↑";
  btn.style.cssText = "position:fixed;right:14px;bottom:118px;width:36px;height:36px;border-radius:18px;border:1px solid #3a4668;background:rgba(18,24,40,.86);color:#d7e0ff;z-index:90;display:none;pointer-events:auto;";
  document.body.appendChild(btn);
  function scroller() {
    return document.scrollingElement || document.documentElement;
  }
  function tick() {
    const y = window.scrollY || document.documentElement.scrollTop || 0;
    btn.style.display = y > 180 ? "block" : "none";
  }
  btn.onclick = () => window.scrollTo({ top: 0, behavior: "smooth" });
  window.addEventListener("scroll", tick, { passive: true });
})();
