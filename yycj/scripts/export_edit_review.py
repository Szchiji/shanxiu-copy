#!/usr/bin/env python3
"""Export actual changed project HTML/assets with isolated, marked fixtures for file:// review.
Not a screenshot renderer; no browser, DB, credentials or production APIs involved.
"""
import argparse
import json
import re
import shutil
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from bot.services.home_feed import DEFAULT_OPS,CITY_KEYS


def export(out):
    out.mkdir(parents=True,exist_ok=True)
    source=ROOT/'miniapp'
    # Lazy-loaded desk scripts stay available under file:// too. No raw production HTML/SDK.
    for asset in source.iterdir():
        if asset.is_file() and asset.suffix in ('.js','.css','.svg','.png','.jpg','.webp') and asset.name!='telegram-web-app.js':
            shutil.copy2(asset,out/asset.name)
    review_settings={**DEFAULT_OPS,'bot_welcome_buttons':[{'type':'webapp','label':'示例 · 首页','target':'home','new_row':True},{'type':'webapp','label':'示例 · 我的','target':'me','new_row':False},{'type':'url','label':'示例 · 使用说明','target':'https://example.invalid/help','new_row':True}]}
    cfg=json.dumps({'settings':review_settings,'keys':CITY_KEYS},ensure_ascii=False).replace('</','<\\/')
    bootstrap=(ROOT/'scripts/preview/fixtures.js').read_text()
    bootstrap=bootstrap.replace("mode=q.get('preview') || 'home'","mode=window.__EDIT_REVIEW_MODE || q.get('preview') || 'home'")
    bootstrap=bootstrap.replace("admin=mode==='admin'","admin=mode.startsWith('admin')")
    bootstrap=bootstrap.replace('location.href)',"'https://review.invalid/')").replace('url.origin!==location.origin',"url.origin!=='https://review.invalid'")
    bootstrap=bootstrap.replace("    if(!admin)return;","    if(window.__EDIT_REVIEW_MODE)return;\n    if(!admin)return;")
    bootstrap=bootstrap.replace("'/preview-assets/cover'+(i+1)+'.svg'", "'data:image/svg+xml;charset=utf-8,'+encodeURIComponent('<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"360\" height=\"480\"><rect width=\"360\" height=\"480\" fill=\"%23244D43\"/><text x=\"40\" y=\"220\" fill=\"%23D4B26A\" font-size=\"25\">评审占位 · 非真实资料</text></svg>')")
    extension=(ROOT/'scripts/preview/edit-fixtures.js').read_text()
    previews=[('teacher-edit','go.html','老师修改资料表单'),('teacher-statuses','go.html','老师我的上架 / 分步状态'),('admin-edit','desk.html','管理员免审编辑 / 通知重试'),('admin-review','desk.html','管理员媒体审核'),('admin-buttons','desk.html','统一按钮编辑 / 独立开关'),('admin-executions','desk.html','城市执行日志 / 安全步骤重试')]
    csp="default-src 'none'; script-src 'unsafe-inline' file:; style-src 'unsafe-inline' file:; img-src data: file: blob:; media-src data: file: blob:; connect-src 'none'; object-src 'none'; form-action 'none'"
    for mode,page,label in previews:
        html=(source/page).read_text()
        # Inline real scripts/styles, eliminating cross-origin fetch/CORS dependencies.
        def js(match):
            name=match.group(1).split('?')[0].removeprefix('./')
            if 'telegram-web-app.js' in name:return ''
            file=source/name
            return '<script>'+re.sub(r'</script',r'<\\/script',file.read_text(),flags=re.I)+'</script>' if file.is_file() else ''
        html=re.sub(r'<script\b[^>]*src=["\']([^"\']+)["\'][^>]*>\s*</script>',js,html,flags=re.I)
        html=re.sub(r'<script>\s*if \(!\(window.Telegram[\s\S]*?</script>','',html,count=1)
        def css(match):
            file=source/match.group(1).split('?')[0].removeprefix('./')
            return '<style>'+file.read_text()+'</style>' if file.is_file() else ''
        html=re.sub(r'<link[^>]*rel="stylesheet"[^>]*href="([^"]+)"[^>]*/?>',css,html)
        html=re.sub(r'<link[^>]*rel="preconnect"[^>]*/?>','',html)
        fixture='window.__EDIT_REVIEW_MODE='+json.dumps(mode)+';window.__PREVIEW_CONFIG='+cfg+';\n'+bootstrap+'\n'+extension
        injection='<meta http-equiv="Content-Security-Policy" content="'+csp+'"><script>'+fixture+'</script>'
        html=html.replace('<head>','<head>'+injection,1)
        (out/(mode+'.html')).write_text(html)
    links=''.join(f'<li><a href="{m}.html">{label}</a></li>' for m,_,label in previews)
    (out/'OPEN-FIRST.html').write_text('<!doctype html><meta charset="utf-8"><title>月影车姬 · 改资料升级评审</title><style>body{background:#0F2420;color:#F1EEE4;font:18px sans-serif;padding:40px}a{color:#D4B26A}li{margin:24px}</style><h1>月影车姬 · 改资料升级（未上线）</h1><p>实际项目 HTML/CSS/JS，只有 API/登录为浏览器内存示例。非截图、非真实资料；禁止外网，不会发送 Telegram。刷新重置模拟操作。</p><ul>'+links+'</ul><p>老师页面使用手机 390×844（桌面浏览器请手动切换窄视口），后台使用1440×1000。不会改变首页117×224三列规则。点重试/审核仅更新本页模拟状态。</p>')
    (out/'README.txt').write_text('打开 OPEN-FIRST.html，再打开其中的4个实际UI页面。浏览器 file:// 即可，不需要服务器/登录。\n这是明确标记示例数据的实际代码评审包，不是截图或已上线证明。\n老师页面建议390×844；后台1440×1000。频道/通知为模拟状态，不连接外网。\n'+ '\n'.join(f'{m}.html — {label}' for m,_,label in previews)+'\n')
    shutil.make_archive(str(out),'zip',out)
    print(out/'OPEN-FIRST.html');print(str(out)+'.zip')

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True);export(parser.parse_args().out)
