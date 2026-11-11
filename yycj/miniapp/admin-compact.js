(() => {
  if (window.__yycjAdminCompact) return;
  window.__yycjAdminCompact = true;
  function flatten() {
    document.querySelectorAll("#adminListings .pick-item").forEach((el) => el.classList.add("listing-item"));
    document.querySelectorAll(".hrs").forEach((el) => {
      el.placeholder = "小时";
      el.title = "有效小时，空=长期";
      el.removeAttribute("style");
    });
    const inp = document.getElementById("pinHours");
    if (inp) {
      const lab = inp.previousElementSibling;
      if (lab && lab.tagName === "LABEL") lab.remove();
      inp.remove();
    }
  }
  ["adminListings", "feedPinResults", "pinResults", "adminFeedPins", "adminPins"].forEach((id) => {
    const el = document.getElementById(id);
    if (el) new MutationObserver(flatten).observe(el, { childList: true, subtree: true });
  });
  setTimeout(flatten, 400);
  setTimeout(flatten, 1800);
})();
