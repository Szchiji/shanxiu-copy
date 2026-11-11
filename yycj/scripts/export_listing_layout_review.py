#!/usr/bin/env python3
"""Export actual unreleased listings-page assets plus explicit in-memory fixtures.
Review-only: no production APIs, credentials, Telegram, or deploy.
"""
import argparse
import json
import re
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LISTINGS = [
    {
        "lamp_id": "xiaoming",
        "title": "小明",
        "city": "深圳",
        "status": "active",
        "expires_at": "2026-12-04T16:00:00Z",
        "updated_at": "2026-11-11T10:00:00Z",
        "unlist_reason": None,
    },
    {
        "lamp_id": "xiaohong",
        "title": "小红",
        "city": "深圳",
        "status": "active",
        "expires_at": "2026-12-11T16:00:00Z",
        "updated_at": "2026-11-11T11:00:00Z",
        "unlist_reason": None,
        "edit_state": {
            "operation_id": "example-edit-xiaohong",
            "text_saved": True,
            "review_status": "none",
            "media_status": "unchanged",
            "action": "text",
            "channel_status": "synced",
            "channel_retryable": False,
            "notification_status": "not_required",
            "notification_retryable": False,
            "album": "https://t.me/example/1001",
        },
    },
    {
        "lamp_id": "xiaolan",
        "title": "小兰",
        "city": "深圳",
        "status": "active",
        "expires_at": "2026-12-15T16:00:00Z",
        "updated_at": "2026-11-11T12:00:00Z",
        "unlist_reason": None,
        "edit_state": {
            "operation_id": "example-edit-xiaolan",
            "text_saved": True,
            "review_status": "none",
            "media_status": "unchanged",
            "action": "text",
            "channel_status": "failed",
            "channel_retryable": True,
            "notification_status": "not_required",
            "notification_retryable": False,
            "album": "https://t.me/example/1002",
        },
    },
    {
        "lamp_id": "xiaoqing",
        "title": "小青",
        "city": "深圳",
        "status": "hidden",
        "unlist_reason": "teacher",
        "expires_at": "2026-12-20T16:00:00Z",
        "updated_at": "2026-11-11T09:00:00Z",
    },
]

RELIST_ALREADY_ACTIVE = "资料已经上架；未重发频道或通知，有效期不变。"

BOOTSTRAP = r'''
(()=>{
  const memory=()=>{const values={};return {getItem:k=>values[k]??null,setItem:(k,v)=>{values[k]=String(v)},removeItem:k=>delete values[k],clear:()=>Object.keys(values).forEach(k=>delete values[k])}};
  Object.defineProperty(window,'localStorage',{value:memory()});
  Object.defineProperty(window,'sessionStorage',{value:memory()});
  localStorage.setItem('yycj_admin_city','深圳');
  localStorage.setItem('yycj_token','LOCAL_PREVIEW_ONLY');
  window.Telegram={WebApp:{initData:'OFFLINE_UI_FIXTURE',ready(){},expand(){},setHeaderColor(){},initDataUnsafe:{user:{id:1}},openLink(){},openTelegramLink(){},showAlert(msg,cb){window.alert(msg);cb&&cb();},showConfirm(msg,cb){cb&&cb(window.confirm(msg));}}};
  FIXTURE_SOURCE
  const metadata=METADATA;
  const listings=LISTINGS;
  const relistAlreadyActive=RELIST_MSG;
  const fixture=createFixture(metadata);
  const start=START;
  function json(data,status=200){return new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json'}});}
  window.fetch=async(input,opt={})=>{
    const url=new URL(typeof input==='string'?input:input.url,'https://review.invalid/');
    const method=String((opt&&opt.method)||'GET').toUpperCase();
    const path=url.pathname;
    if(path==='/api/admin/listings' && method==='GET') return json({items:listings,total:listings.length,has_more:false});
    if(method==='POST' && /\/api\/admin\/listings\/([^/]+)\/relist$/.test(path)){
      const id=path.split('/').at(-2);
      const item=listings.find(x=>x.lamp_id===id);
      if(item && item.status==='active') return json({ok:true,restored:false,message:relistAlreadyActive,channel_status:'not_required',notification_status:'not_required'});
      if(item){item.status='active';item.unlist_reason=null;}
      return json({ok:true,restored:true,message:'资料已恢复上架；有效期不变。'});
    }
    const result=fixture.handle(input,opt);
    return json(result.data,result.status);
  };
  document.addEventListener('click',e=>{if(e.target.closest('a[href]'))e.preventDefault()},true);
  function htmlDialog(text,okLabel,cancel){
    return new Promise(resolve=>{
      const wrap=document.createElement('div');
      wrap.className='listing-review-dialog';
      wrap.innerHTML='<div class="listing-review-dialog-card"><p></p><div class="listing-review-dialog-btns">'+(cancel?'<button type="button" data-no>取消</button>':'')+'<button type="button" data-ok>'+okLabel+'</button></div></div>';
      wrap.querySelector('p').textContent=text;
      const done=v=>{wrap.remove();resolve(v);};
      wrap.querySelector('[data-ok]').onclick=()=>done(true);
      const no=wrap.querySelector('[data-no]');if(no)no.onclick=()=>done(false);
      document.body.append(wrap);
    });
  }
  window.alert=msg=>{const p=htmlDialog(String(msg),'确定',false);window.__listingReviewAlert=msg;return undefined;};
  const realConfirm=window.confirm;
  window.confirm=msg=>{
    if(start.autoConfirm){window.__listingReviewConfirm=msg;return true;}
    window.__listingReviewConfirm=msg;
    return true;
  };
  let tick=0;const timer=setInterval(()=>{
    if(++tick>150){clearInterval(timer);return;}
    if(!window.yycjWorkspace?.ready||!document.querySelector('#workspaceView h1'))return;
    clearInterval(timer);
    document.querySelector('[data-work="review"]')?.click();
    setTimeout(()=>{
      document.querySelector('[data-wtab="listings"]')?.click();
      setTimeout(()=>{
        document.body.dataset.listingReviewReady='1';
        if(start.openMore){
          const card=document.querySelector('[data-fill-proxy="xiaohong"]')?.closest('.listing-card');
          const more=card?.querySelector('.listing-more');
          if(more) more.open=true;
        }
        if(start.focusFail){
          document.querySelector('[data-fill-proxy="xiaolan"]')?.closest('.listing-card')?.scrollIntoView({block:'center'});
        }
        if(start.relistAlert){
          const btn=document.querySelector('[data-fill-proxy="xiaohong"]')?.closest('.listing-card')?.querySelector('.listing-more-menu [data-list="relist"]');
          if(btn){
            const host=btn.closest('.listing-more');if(host)host.open=true;
            setTimeout(()=>btn.click(),80);
          }
        }
      },400);
    },200);
  },100);
})();
'''


def export(destination: Path):
    destination = destination.resolve()
    if destination == ROOT / "miniapp" or (ROOT / "miniapp") in destination.parents:
        raise ValueError("Review export must stay outside production static directory")
    destination.mkdir(parents=True, exist_ok=True)
    metadata = runpy.run_path(str(ROOT / "tests/workspace_fixture.py"))["fixture"]()
    fixture = (ROOT / "tests/workspace_fixture.cjs").read_text().split("if(typeof module!==")[0]
    escape_script = lambda s: s.replace("</script", "<\\/script")

    def page(start):
        bootstrap = (
            BOOTSTRAP.replace("FIXTURE_SOURCE", fixture)
            .replace("METADATA", json.dumps(metadata, ensure_ascii=False))
            .replace("LISTINGS", json.dumps(LISTINGS, ensure_ascii=False))
            .replace("RELIST_MSG", json.dumps(RELIST_ALREADY_ACTIVE, ensure_ascii=False))
            .replace("START", json.dumps(start, ensure_ascii=False))
        )
        source = (ROOT / "miniapp/desk.html").read_text()
        source = re.sub(r"<script>\s*if\s*\(!window\.Telegram[\s\S]*?</script>", "", source)
        source = re.sub(r"<script>\s*if\s*\([^<]*?document\.write\([\s\S]*?</script>", "", source)

        def script(match):
            name = match.group(1).split("?")[0].removeprefix("./")
            if name == "telegram-web-app.js" or name.startswith("https:"):
                return ""
            code = (ROOT / "miniapp" / name).read_text()
            prefix = ""
            if name == "admin-boot.js":
                prefix = '<script id="yycj-admin-chats"></script><script id="yycj-admin-cta"></script>'
            result = prefix + "<script>" + escape_script(code) + "</script>"
            if name == "admin-boot.js":
                result += "".join(
                    "<script>" + escape_script((ROOT / "miniapp" / p).read_text()) + "</script>"
                    for p in ["admin-chats.js", "admin-cta.js"]
                )
            return result

        source = re.sub(r'<script\b[^>]*\bsrc="([^"]+)"[^>]*>\s*</script>', script, source)
        source = re.sub(
            r'<link\b[^>]*rel="stylesheet"[^>]*href="([^"]+)"[^>]*>',
            lambda m: "<style>" + (ROOT / "miniapp" / m.group(1).split("?")[0].removeprefix("./")).read_text() + "</style>",
            source,
        )
        dialog_css = """<style>
#offlineReviewBanner{position:relative;z-index:80;padding:8px 18px 8px 230px;background:#30291f;color:#e5c494;font-size:12px;border-bottom:1px solid #655130}
@media(max-width:760px){#offlineReviewBanner{padding:10px 18px}}
.listing-review-dialog{position:fixed;inset:0;z-index:200;background:rgba(0,0,0,.45);display:flex;align-items:center;justify-content:center;padding:24px}
.listing-review-dialog-card{width:min(320px,100%);background:#ececee;color:#111;border-radius:14px;padding:18px 16px 12px;box-shadow:0 10px 40px rgba(0,0,0,.35)}
.listing-review-dialog-card p{margin:0 8px 14px;font-size:15px;line-height:1.55;text-align:left}
.listing-review-dialog-btns{display:flex;gap:8px}
.listing-review-dialog-btns button{flex:1;min-height:44px;border:0;border-radius:12px;background:#d8d8dc;color:#111;font-size:17px;font-weight:600}
</style>"""
        source = source.replace(
            "<head>",
            "<head><meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; media-src data: blob:; connect-src 'none'; frame-src 'self'; base-uri 'none'; form-action 'none'\">\n<script>"
            + escape_script(bootstrap)
            + "</script>",
            1,
        )
        banner = '<div id="offlineReviewBanner">真实未合并界面 · 示例数据 · 仅离线内存操作，不是生产截图；重新上架提示文案未改。</div>'
        source = source.replace(
            '<body class="admin-body workspace-body" data-workspace="true">',
            '<body class="admin-body workspace-body" data-workspace="true">' + banner,
            1,
        )
        source = source.replace("</head>", dialog_css + "</head>")
        source = source.replace("<title>", "<title>上架资料布局评审 · ", 1)
        return source

    pages = {
        "mobile-listings.html": {"openMore": False, "autoConfirm": True},
        "desktop-listings.html": {"openMore": False, "autoConfirm": True},
        "mobile-more.html": {"openMore": True, "autoConfirm": True},
        "mobile-failure.html": {"focusFail": True, "autoConfirm": True},
        "mobile-relist-alert.html": {"openMore": True, "relistAlert": True, "autoConfirm": True},
        "desktop-hidden.html": {"openMore": False, "autoConfirm": True},
    }
    for name, start in pages.items():
        (destination / name).write_text(page(start))

    (destination / "review-index.html").write_text(
        """<!doctype html><meta charset="utf-8"><title>月影车姬 · 上架资料布局评审</title>
<style>body{background:#0F2420;color:#F1EEE4;font:16px/1.6 sans-serif;padding:40px;max-width:720px}a{color:#D4B26A}li{margin:16px 0}</style>
<h1>上架资料卡片布局（未上线）</h1>
<p>实际 desk.html / CSS / JS，数据为标明的内存示例。不连接生产，不发送 Telegram。手机页请用 390×844 查看；桌面页 1280×900。</p>
<ul>
<li><a href="mobile-listings.html">手机 · 成功修改后的小红（无大块状态栏，与小明同样紧凑）</a></li>
<li><a href="mobile-failure.html">手机 · 频道同步失败的小兰（紧凑可重试状态）</a></li>
<li><a href="mobile-more.html">手机 · 小红「更多」展开</a></li>
<li><a href="desktop-listings.html">桌面 · 紧凑列表</a></li>
<li><a href="mobile-relist-alert.html">手机 · 已上架点重新上架（提示文案保持原样）</a></li>
</ul>
<p>截图 PNG 与 LAYOUT-REVIEW.md 在本目录。本包不是线上验收证明。</p>"""
    )
    print(destination / "review-index.html")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    export(parser.parse_args().output)
