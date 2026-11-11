(() => {
  const pane = document.getElementById("pane-venue");
  if (pane && !document.getElementById("opsPromo")) {
    const box = document.createElement("div");
    box.className = "ops-card";
    box.innerHTML = `<h4>过审话术</h4>
      <label>审核通过后发给老师</label>
      <textarea id="opsPromo" rows="4" placeholder="发给老师的推广文案"></textarea>`;
    const save = document.getElementById("btnOpsSave");
    if (save) pane.insertBefore(box, save);
    else pane.appendChild(box);
  }
  document.getElementById("btnOpsSave")?.addEventListener("click", async () => {
    const promo = (document.getElementById("opsPromo") || {}).value || "";
    if (!promo.trim()) return;
    try {
      const token = localStorage.getItem("yycj_token") || "";
      await fetch("/api/admin/settings/extra", {
        method: "POST",
        headers: { Authorization: "Bearer " + token, "Content-Type": "application/json" },
        body: JSON.stringify({ approve_promo_text: promo }),
      });
    } catch (e) {}
  });
})();
