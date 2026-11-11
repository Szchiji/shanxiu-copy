(() => {
  const st = document.getElementById("yycj-admin-scroll") || document.createElement("style");
  st.id = "yycj-admin-scroll";
  st.textContent = `html{height:auto!important;max-height:none!important;overflow:auto!important;overflow-y:auto!important;-webkit-overflow-scrolling:touch!important;position:static!important;}
body,body.admin-body{height:auto!important;max-height:none!important;overflow:visible!important;position:static!important;}
#app,#boot,#gate,#console,.admin-shell,.admin-main{height:auto!important;max-height:none!important;overflow:visible!important;}
.admin-main{padding-bottom:80px!important;}`;
  document.head.appendChild(st);
  ["admin-chats.js", "admin-cta.js"].forEach((name) => {
    const id = "yycj-" + name.replace(".js", "");
    if (document.getElementById(id)) return;
    const s = document.createElement("script");
    s.id = id;
    s.src = "./" + name + "?v=20261029d";
    document.head.appendChild(s);
  });
})();
