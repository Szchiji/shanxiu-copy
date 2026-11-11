(() => {
  const box = document.getElementById("opsChats");
  if (box) {
    box.placeholder = "一行一个，例如\n-1001234567890\n@yourchannel";
    const label = box.previousElementSibling;
    if (label && label.tagName === "LABEL") label.textContent = "必订频道 ID（一行一个，可填 -100… 或 @用户名）";
  }
  const contact = document.getElementById("opsAdminContact");
  if (contact && !document.getElementById("opsAdminLabel")) {
    const lab = document.createElement("label");
    lab.textContent = "首页管理员按钮文案";
    const input = document.createElement("input");
    input.id = "opsAdminLabel";
    input.placeholder = "管理员";
    contact.parentElement.insertBefore(lab, contact);
    contact.parentElement.insertBefore(input, contact);
  }
  const token = () => localStorage.getItem("yycj_token") || "";
  async function fill() {
    try {
      const r = await fetch("/api/admin/homepage", { headers: { Authorization: "Bearer " + token() } });
      const data = await r.json();
      const settings = data.settings || data;
      const chats = settings.required_chats || [];
      const lines = chats.map((c) => typeof c === "string" ? c : (c.chat_id || "")).filter(Boolean);
      if (box && lines.length && !box.value.trim()) box.value = lines.join("\n");
      const labelEl = document.getElementById("opsAdminLabel");
      if (labelEl && settings.admin_btn_label) labelEl.value = settings.admin_btn_label;
    } catch (e) {}
  }
  document.addEventListener("click", async (ev) => {
    if (!ev.target || ev.target.id !== "btnOpsSave") return;
    const labelEl = document.getElementById("opsAdminLabel");
    if (!labelEl) return;
    try {
      await fetch("/api/admin/settings/extra", {
        method: "POST",
        headers: { Authorization: "Bearer " + token(), "Content-Type": "application/json" },
        body: JSON.stringify({ admin_btn_label: labelEl.value || "管理员" }),
      });
    } catch (e) {}
  }, true);
  setTimeout(fill, 1200);
})();
