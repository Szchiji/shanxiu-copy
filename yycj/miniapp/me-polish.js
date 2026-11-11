(() => {
  if (window.__yycjMePolish) return;
  window.__yycjMePolish = true;
  const st = document.getElementById("yycj-me-css") || document.createElement("style");
  st.id = "yycj-me-css";
  st.textContent = "#meGuide{display:none!important;}#topMeta{display:none!important;}";
  document.head.appendChild(st);
  function polish() {
    document.getElementById("meGuide")?.remove();
    document.querySelectorAll("#view-me h3").forEach((h) => {
      if (h.textContent.trim() === "我的身份") h.textContent = "我的昵称";
    });
  }
  document.addEventListener("click", (ev) => {
    if (ev.target.closest("[data-nav='me']")) setTimeout(polish, 80);
  });
  setTimeout(polish, 500);
})();
