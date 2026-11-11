(function () {
  "use strict";
  var back = document.querySelector(".js-back");
  var tg = window.Telegram && window.Telegram.WebApp;
  if (tg && typeof tg.ready === "function") {
    try {
      tg.ready();
      if (typeof tg.expand === "function") tg.expand();
    } catch (e) {}
    var isRoot = location.pathname === "/app" && /view=personal|group=/.test(location.search) === false || location.pathname === "/app" && /view=personal/.test(location.search);
    // Prefer a conservative rule: treat /app personal as root.
    var pathRoot = location.pathname === "/tg" || location.pathname === "/app";
    isRoot = pathRoot && (!location.search || /view=personal/.test(location.search)) && !/[?&]group=/.test(location.search);
    if (tg.BackButton) {
      if (!isRoot) {
        tg.BackButton.show();
        tg.BackButton.onClick(function () {
          if (window.history.length > 1) {
            window.history.back();
          } else if (back && back.href) {
            location.href = back.href;
          } else {
            location.href = (document.body.getAttribute("data-surface") || "/app") + "?view=personal";
          }
        });
      } else {
        tg.BackButton.hide();
      }
    }
  }
  if (back) {
    back.addEventListener("click", function (event) {
      if (window.history.length > 1) {
        event.preventDefault();
        window.history.back();
      }
    });
  }

  var form = document.querySelector(".context-switch");
  if (form) {
    form.addEventListener("submit", function () {
      var sel = form.querySelector("select[name='group']");
      if (sel && !sel.value) {
        var view = document.createElement("input");
        view.type = "hidden";
        view.name = "view";
        view.value = "personal";
        form.appendChild(view);
        sel.disabled = true;
      }
    });
    var sel = form.querySelector("select[name='group']");
    if (sel) {
      sel.addEventListener("change", function () {
        if (!sel.value) {
          var view = document.createElement("input");
          view.type = "hidden";
          view.name = "view";
          view.value = "personal";
          form.appendChild(view);
          sel.disabled = true;
        }
        form.submit();
      });
    }
  }

  function applyTelegramTheme(tg) {
    try {
      if (tg.setHeaderColor) tg.setHeaderColor("secondary_bg_color");
      if (tg.setBackgroundColor) tg.setBackgroundColor("bg_color");
      var tp = tg.themeParams || {};
      if (tp.bg_color) document.documentElement.style.backgroundColor = tp.bg_color;
    } catch (e) {}
  }

  function authFromTelegram(tg) {
    if (!tg || !tg.initData) return;
    var surface = document.body && document.body.getAttribute("data-surface");
    var gate = document.body && document.body.getAttribute("data-tg-gate");
    if (surface !== "/tg" && !gate) return;
    fetch("/api/auth/telegram", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", "Accept": "application/json" },
      body: JSON.stringify({ initData: tg.initData })
    }).then(function (res) {
      if (!res.ok) return;
      if (gate) {
        location.replace("/tg?view=personal");
      }
    }).catch(function () {});
  }

  if (tg) {
    applyTelegramTheme(tg);
    if (document.body && document.body.getAttribute("data-tg-gate")) {
      authFromTelegram(tg);
    }
  }
})();
