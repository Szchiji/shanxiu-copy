(() => {
  if (window.__yycjAdminCta) return;
  window.__yycjAdminCta = true;
  const input = document.getElementById("opsCta");
  if (input && !document.getElementById("opsShowCta")) {
    const lab = document.createElement("label");
    lab.className = "row";
    lab.innerHTML = '<input type="checkbox" id="opsShowCta" checked /> 显示聊天按钮';
    input.insertAdjacentElement("afterend", lab);
    const hint = document.createElement("p");
    hint.className = "muted";
    hint.textContent = "推送模板可用 {聊天按钮}，关开关后详情页不显示该按钮。";
    lab.insertAdjacentElement("afterend", hint);
  }
  const orig = window.fetch;
  window.fetch = function (url, opt) {
    try {
      const u = String(url || "");
      if (u.includes("/api/admin/settings/extra") && opt && typeof opt.body === "string") {
        const body = JSON.parse(opt.body);
        const box = document.getElementById("opsShowCta");
        if (box) body.show_chat_cta = !!box.checked;
        const cta = document.getElementById("opsCta");
        if (cta && cta.value.trim()) body.chat_cta_label = cta.value.trim().slice(0, 32);
        opt = Object.assign({}, opt, { body: JSON.stringify(body) });
      }
    } catch (e) {}
    return orig.apply(this, [url, opt]);
  };
  setTimeout(async () => {
    try {
      const token = localStorage.getItem("yycj_token") || "";
      const r = await fetch("/api/admin/homepage", { headers: { Authorization: "Bearer " + token } });
      const s = ((await r.json()).settings || {});
      const box = document.getElementById("opsShowCta");
      if (box && typeof s.show_chat_cta === "boolean") box.checked = s.show_chat_cta;
    } catch (e) {}
  }, 900);
})();
