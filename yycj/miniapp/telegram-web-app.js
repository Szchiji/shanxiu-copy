/* Local stub: load official Telegram WebApp SDK from CDN if not present */
(function () {
  if (window.Telegram && window.Telegram.WebApp) return;
  var s = document.createElement('script');
  s.src = 'https://telegram.org/js/telegram-web-app.js';
  s.async = false;
  document.head.appendChild(s);
})();
