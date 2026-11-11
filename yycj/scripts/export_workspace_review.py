#!/usr/bin/env python3
"""Export ACTUAL unreleased workspace assets + explicit in-memory fixture. Never run in app startup.
No HTTP server, browser automation, production settings, credentials or network are used.
"""
import argparse
import html
import json
from pathlib import Path
import re
import runpy
import sys
import zipfile
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def export(destination:Path):
    destination=destination.resolve()
    if destination==ROOT/'miniapp' or (ROOT/'miniapp') in destination.parents:raise ValueError('Review export must stay outside production static directory')
    destination.mkdir(parents=True,exist_ok=True)
    metadata=runpy.run_path(str(ROOT/'tests/workspace_fixture.py'))['fixture']()
    fixture=(ROOT/'tests/workspace_fixture.cjs').read_text().split("if(typeof module!==")[0]
    escape_script=lambda s:s.replace('</script','<\\/script')
    def page(start):
        bootstrap='''(()=>{
 const memory=()=>{const values={};return {getItem:k=>values[k]??null,setItem:(k,v)=>{values[k]=String(v)},removeItem:k=>delete values[k],clear:()=>Object.keys(values).forEach(k=>delete values[k])}};
 Object.defineProperty(window,'localStorage',{value:memory()});Object.defineProperty(window,'sessionStorage',{value:memory()});
 localStorage.setItem('yycj_admin_city','深圳');
 window.Telegram={WebApp:{initData:'OFFLINE_UI_FIXTURE',ready(){},expand(){},setHeaderColor(){},initDataUnsafe:{user:{id:1}},openLink(){},openTelegramLink(){}}};
 FIXTURE_SOURCE
 const fixture=createFixture(METADATA);window.__workspaceOffline=fixture;
 window.fetch=async(input,opt)=>{const result=fixture.handle(input,opt);return new Response(JSON.stringify(result.data),{status:result.status,headers:{'Content-Type':'application/json'}})};
 document.addEventListener('click',e=>{if(e.target.closest('a'))e.preventDefault()},true);
 const start=START;
 let tick=0;const timer=setInterval(()=>{if(++tick>120){clearInterval(timer);return}if(!window.yycjWorkspace?.ready||!document.querySelector('#workspaceView h1'))return;clearInterval(timer);
  const go=()=>{
    const finish=()=>{
      if(start.openMore){
        const card=document.querySelector('[data-fill-proxy]')?.closest('.listing-card, .pick-item');
        const more=card?.querySelector('.listing-more');
        if(more) more.open=true;
      }
      if(start.focusFail){
        const title=[...document.querySelectorAll('#adminListings .pick-item')].find(el=>el.textContent.includes('示例资料 C'));
        title?.scrollIntoView({block:'center'});
      }
      document.body.dataset.workspaceReviewReady='1';
    };
    if(start.log)setTimeout(()=>{document.querySelector('[data-wlog="'+start.log+'"]')?.click();setTimeout(finish,200);},350);
    else if(start.post)setTimeout(()=>{document.querySelector('[data-wpost="'+start.post+'"]')?.click();setTimeout(finish,200);},350);
    else if(start.page==='review'&&(!start.tab||start.tab==='listings')){
      let n=0;const w=setInterval(()=>{if(document.querySelector('#adminListings .pick-item')||++n>40){clearInterval(w);finish();}},50);
    } else setTimeout(finish,120);
  };
  if(start.page!=='overview'){
    document.querySelector('[data-work="'+start.page+'"]').click();
    setTimeout(()=>{if(start.tab)document.querySelector('[data-wtab="'+start.tab+'"]')?.click();setTimeout(go,180);},180);
  } else { setTimeout(go,120); }
 },100);
})();'''.replace('FIXTURE_SOURCE',fixture).replace('METADATA',json.dumps(metadata,ensure_ascii=False)).replace('START',json.dumps(start))
        source=(ROOT/'miniapp/desk.html').read_text()
        # The real app's SDK fallback is replaced by the fixture before any product script.
        source=re.sub(r'<script>\s*if\s*\([^<]*?document\.write\([\s\S]*?</script>', '',source)
        # Simpler exact removal also handles the existing SDK fallback block with embedded markup.
        source=re.sub(r'<script>\s*if\s*\(!window\.Telegram[\s\S]*?</script>', '',source)
        def script(match):
            name=match.group(1).split('?')[0].removeprefix('./')
            if name=='telegram-web-app.js' or name.startswith('https:'):return ''
            code=(ROOT/'miniapp'/name).read_text()
            prefix=''
            if name=='admin-boot.js':prefix='<script id="yycj-admin-chats"></script><script id="yycj-admin-cta"></script>'
            result=prefix+'<script>'+escape_script(code)+'</script>'
            if name=='admin-boot.js':
                result+=''.join('<script>'+escape_script((ROOT/'miniapp'/p).read_text())+'</script>' for p in ['admin-chats.js','admin-cta.js'])
            return result
        source=re.sub(r'<script\b[^>]*\bsrc="([^"]+)"[^>]*>\s*</script>',script,source)
        source=re.sub(r'<link\b[^>]*rel="stylesheet"[^>]*href="([^"]+)"[^>]*>',lambda m:'<style>'+(ROOT/'miniapp'/m.group(1).split('?')[0].removeprefix('./')).read_text()+'</style>',source)
        # Global CSP also prevents accidental transport regressions in this offline artifact.
        source=source.replace('<head>','<head><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src \'unsafe-inline\'; style-src \'unsafe-inline\'; img-src data: blob:; media-src data: blob:; connect-src \'none\'; frame-src \'self\'; base-uri \'none\'; form-action \'none\'">\n<script>'+escape_script(bootstrap)+'</script>',1)
        banner='<div id="offlineReviewBanner">真实未合并界面 · 示例数据 · 仅离线内存操作，不是生产截图；所有API（含按钮预览）均为示例响应，不连接后台。</div>'
        source=source.replace('<body class="admin-body workspace-body" data-workspace="true">','<body class="admin-body workspace-body" data-workspace="true">'+banner,1)
        source=source.replace('</head>','<style>#offlineReviewBanner{position:relative;z-index:80;padding:8px 18px 8px 230px;background:#30291f;color:#e5c494;font-size:12px;border-bottom:1px solid #655130}@media(max-width:760px){#offlineReviewBanner{padding:10px 18px}}</style></head>')
        source=source.replace('<title>','<title>离线审核 · ',1)
        return source
    starts={
      'index.html':{'page':'overview'},
      'desktop-overview.html':{'page':'overview'},
      'desktop-review.html':{'page':'review','tab':'media','post':'post-media'},
      'desktop-review-media.html':{'page':'review','tab':'media'},
      'desktop-listings.html':{'page':'review','tab':'listings'},
      'desktop-listings-more.html':{'page':'review','tab':'listings','openMore':True},
      'desktop-messages.html':{'page':'messages','tab':'approval'},
      'desktop-city.html':{'page':'settings','tab':'popup'},
      'desktop-community.html':{'page':'settings','tab':'community'},
      'desktop-channel.html':{'page':'settings','tab':'channel'},
      'desktop-logs-list.html':{'page':'logs'},
      'desktop-logs.html':{'page':'logs','log':'log-unknown'},
      'desktop-permissions.html':{'page':'system','tab':'permissions'},
    }
    for name,start in starts.items():(destination/name).write_text(page(start))
    for name,start in [('mobile-city.html',{'page':'settings','tab':'popup'}),('mobile-messages.html',{'page':'messages','tab':'approval'}),('mobile-logs.html',{'page':'logs','log':'log-unknown'}),('mobile-logs-list.html',{'page':'logs'}),('mobile-community.html',{'page':'settings','tab':'community'})]:
        inner=page(start)
        outer='<!DOCTYPE html><html lang="zh-CN"><meta charset="utf-8"><title>390px 实现界面审核 · 示例</title><style>body{margin:0;background:#080c14;color:#b5c9e5;font:14px sans-serif;text-align:center}iframe{width:390px;max-width:100vw;height:1000px;border:1px solid #34445d}p{margin:12px}</style><p>390px 真实CSS审核容器 · 示例数据 · 非截图 / 未视觉验收</p><iframe title="390px 离线实际界面" srcdoc="'+html.escape(inner,quote=True)+'"></iframe></html>'
        (destination/name).write_text(outer)
    (destination/'README.md').write_text('''# 月影车姬新版后台：真实资产离线审核包

这不是第二份设计稿，也不是线上截图：HTML/CSS/JS 来自当前未合并实现；所有数据、鉴权与接口响应都是隔离的示例适配器。**不会访问生产、不会发 Telegram、不会保存线上设置。** CSP 额外禁止网络连接；本页存储只存在内存，关闭即消失。

- `index.html`：完整六页导航，总览 → 审核与上架 → 消息与按钮 → 站点与城市配置 → 执行日志 → 系统与权限。
- `desktop-review.html`：真实旧/新媒体比较抽屉；`desktop-messages.html`：实际按钮编辑器；`desktop-city.html`：公告配置；`desktop-logs.html`：未知送达详情；`desktop-permissions.html`：权限工作页。
- `desktop-community.html`：分组双栏社区配置；`desktop-channel.html`：频道正文、链接标签与任务分区；`desktop-logs-list.html`：日志筛选列表（不自动打开详情）。
- `mobile-community.html` / `mobile-logs-list.html`：390px社区配置与日志筛选列表。
- `mobile-city.html` / `mobile-messages.html` / `mobile-logs.html`：390px iframe 真实响应式CSS容器，不是图片。
- 也可直接缩小 `index.html` 的浏览器窗口检查响应式排版。

下载后用本机浏览器打开，无需登录。若当前宿主禁止 file/srcdoc，这属于宿主限制；此包未通过浏览器像素/视觉验收，不应宣称已截图验证。允许用仅绑定127.0.0.1的本地静态服务打开审核目录；这不启用真实API。未外接任何API，也不代表浏览器视觉验收。

按钮预览在正式应用使用既有后台渲染器；离线包用明确标注的示例响应，不是后台集成测试。部分旧操作在本包会提示“未实现示例操作，已阻止”，正式UI仍连接原有受鉴权API。操作后刷新页面会恢复全部示例。
''')
    # Simple network-free launcher, separate from fixture pages whose anchors are intentionally blocked.
    links=[
      ('总览','index.html'),
      ('总览 · 桌面','desktop-overview.html'),
      ('审核与上架 · 上架资料','desktop-listings.html'),
      ('上架资料 · 更多展开','desktop-listings-more.html'),
      ('媒体审核队列','desktop-review-media.html'),
      ('媒体审核抽屉','desktop-review.html'),
      ('消息与按钮 · 桌面','desktop-messages.html'),
      ('社区配置 · 桌面','desktop-community.html'),
      ('频道配置 · 桌面','desktop-channel.html'),
      ('进入弹窗 · 桌面','desktop-city.html'),
      ('日志列表 · 桌面','desktop-logs-list.html'),
      ('日志详情 · 桌面','desktop-logs.html'),
      ('系统与权限 · 桌面','desktop-permissions.html'),
      ('消息与按钮 · 390px iframe','mobile-messages.html'),
      ('城市弹窗 · 390px iframe','mobile-city.html'),
      ('社区配置 · 390px iframe','mobile-community.html'),
      ('日志列表 · 390px iframe','mobile-logs-list.html'),
      ('日志详情 · 390px iframe','mobile-logs.html'),
    ]
    launcher='<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>后台整体布局评审</title><style>body{background:#0b0f18;color:#dfeaff;font:16px/1.8 sans-serif;max-width:900px;margin:40px auto;padding:0 20px}a{display:block;padding:12px 16px;background:#171e2c;color:#9fc6ff;border:1px solid #293448;border-radius:8px;margin:10px 0}p{color:#98a7bd;font-size:14px}</style><h1>月影车姬 · 整体管理后台布局评审</h1><p>真实未合并HTML/CSS/JS，示例数据、内存操作、网络隔离。不是线上截图；生产未变。桌面建议1280×900，手机直接把同一HTML缩到390×844（不必用iframe页）。可直接导航六页；关闭/刷新会恢复示例。</p>'+''.join('<a href="'+url+'">'+label+'</a>' for label,url in links)+'</html>'
    (destination/'review-index.html').write_text(launcher)
    archive=destination.parent/(destination.name+'.zip')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for p in sorted(destination.iterdir()):z.write(p,arcname=destination.name+'/'+p.name)
    print(json.dumps({'directory':str(destination),'zip':str(archive),'files':[p.name for p in sorted(destination.iterdir())]},ensure_ascii=False))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);export(parser.parse_args().output)
