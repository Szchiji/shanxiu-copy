#!/usr/bin/env python3
"""Export isolated file:// review pages for edit history. No production APIs or Telegram."""
import argparse, json, re, shutil, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

FIXTURE=r'''
window.__EDIT_HISTORY_REVIEW=true;
window.Telegram=window.Telegram||{WebApp:{initData:'',ready:function(){},expand:function(){},close:function(){},MainButton:{hide:function(){},show:function(){},setText:function(){}},BackButton:{hide:function(){},show:function(){},onClick:function(){}},themeParams:{},colorScheme:'dark'}};
const HISTORY={
  notice:'仅展示本功能上线后可证明的版本；更早修改不会补造，也不能按记忆恢复。',
  media_restore_note:'只有当前资料仍在使用、或本机缓存里还能读到的旧媒体才可恢复。',
  current:{title:'新文字',description:'新介绍',city:'深圳',district:'南山',price_text:'100',tags:['示例'],approx_label:'示例区域中心',extras:{身高:'170'},hours:[{start:'10:00',end:'18:00'}],media:[{type:'image',url:'new-photo'}],status:'active',expires_at:'2026-11-21T00:00:00',updated_at:'2026-11-11T12:00:00',edit_revision:2,media_revision:1},
  items:[
    {version_id:'ver-now',recorded_at:'2026-11-11T12:00:00Z',source:'teacher_edit',source_label:'老师修改',actor_label:'老师本人',changed_fields:['title','description'],changed_labels:['称呼','介绍'],restorable:false,can_restore_text:false,can_restore_media:false,media_available:true,current_changed_fields:[],current_changed_labels:[]},
    {version_id:'ver-old',recorded_at:'2026-11-11T04:00:00Z',source:'baseline',source_label:'记录起点（当时的资料原文）',actor_label:'记录时的资料原文',changed_fields:[],changed_labels:[],restorable:true,can_restore_text:true,can_restore_media:false,media_available:false,current_changed_fields:['title','description'],current_changed_labels:['称呼','介绍']}
  ]
};
const PREVIEWS={
  'ver-now':{notice:HISTORY.notice,current:HISTORY.current,version:Object.assign({},HISTORY.items[0],{snapshot:HISTORY.current,current_changed_fields:[],current_changed_labels:[]})},
  'ver-old':{notice:HISTORY.notice,current:HISTORY.current,version:Object.assign({},HISTORY.items[1],{snapshot:{title:'原文字',description:'原介绍',city:'深圳',district:'南山',price_text:'80',tags:[],approx_label:'示例区域中心',extras:{身高:'168'},hours:[{start:'11:00',end:'19:00'}],media:[{type:'image',url:'old-photo'}]}})}
};
const origFetch=window.fetch;
window.fetch=async function(url,opt){
  const u=String(url||'');
  if(u.includes('/edit-history') && u.includes('/restore')){
    const body=JSON.parse((opt&&opt.body)||'{}');
    if(!body.confirm) return {ok:false,json:async()=>({detail:'请先预览并明确确认后再恢复'})};
    return {ok:true,json:async()=>({operation:{operation_id:body.operation_id||'review-restore',text_saved:true,review_status:'none',media_status:'unchanged',action:'text',channel_status:'synced',channel_retryable:false,notification_status:'not_required',notification_retryable:false}})};
  }
  const m=u.match(/edit-history\/([^/?]+)$/);
  if(m && PREVIEWS[decodeURIComponent(m[1])]) return {ok:true,json:async()=>PREVIEWS[decodeURIComponent(m[1])]};
  if(u.includes('/edit-history')) return {ok:true,json:async()=>HISTORY};
  if(typeof origFetch==='function') return origFetch.apply(this,arguments);
  return {ok:true,json:async()=>({})};
};
document.addEventListener('DOMContentLoaded',()=>{
  const mode=window.__EDIT_HISTORY_MODE;
  if(mode==='teacher-history' || mode==='teacher-preview'){
    document.querySelector('[data-nav="me"]')?.click();
    setTimeout(()=>{
      if(window.__yycjHistoryUI) window.__yycjHistoryUI.open('me','preview-lamp');
      if(mode==='teacher-preview') setTimeout(()=>{
        document.querySelector('[data-hist-ver="ver-old"]')?.click();
      },200);
    },400);
  }
  if(mode==='admin-history'){
    setTimeout(()=>{
      if(window.__yycjHistoryUI) window.__yycjHistoryUI.open('admin','preview-lamp');
    },400);
  }
});
'''

def export(out:Path):
    out.mkdir(parents=True,exist_ok=True)
    source=ROOT/'miniapp'
    for asset in source.iterdir():
        if asset.is_file() and asset.suffix in ('.js','.css','.svg','.png','.jpg','.webp') and asset.name!='telegram-web-app.js':
            shutil.copy2(asset,out/asset.name)
    csp="default-src 'none'; script-src 'unsafe-inline' file:; style-src 'unsafe-inline' file:; img-src data: file: blob:; media-src data: file: blob:; connect-src 'none'; object-src 'none'; form-action 'none'"
    pages=[('teacher-history','go.html','老师 · 修改记录列表'),('teacher-preview','go.html','老师 · 预览并确认恢复'),('admin-history','desk.html','管理员 · 修改记录')]
    for mode,page,label in pages:
        html=(source/page).read_text()
        def js(match):
            name=match.group(1).split('?')[0].removeprefix('./')
            if 'telegram-web-app.js' in name: return ''
            file=source/name
            return '<script>'+re.sub(r'</script',r'<\\/script',file.read_text(),flags=re.I)+'</script>' if file.is_file() else ''
        html=re.sub(r'<script\b[^>]*src=["\']([^"\']+)["\'][^>]*>\s*</script>',js,html,flags=re.I)
        html=re.sub(r'<script>\s*if \(!\(window.Telegram[\s\S]*?</script>','',html,count=1)
        def css(match):
            file=source/match.group(1).split('?')[0].removeprefix('./')
            return '<style>'+file.read_text()+'</style>' if file.is_file() else ''
        html=re.sub(r'<link[^>]*rel="stylesheet"[^>]*href="([^"]+)"[^>]*/?>',css,html)
        html=re.sub(r'<link[^>]*rel="preconnect"[^>]*/?>','',html)
        fixture='window.__EDIT_HISTORY_MODE='+json.dumps(mode)+';\n'+FIXTURE
        html=html.replace('<head>','<head><meta http-equiv="Content-Security-Policy" content="'+csp+'"><script>'+fixture+'</script>',1)
        (out/(mode+'.html')).write_text(html)
    links=''.join(f'<li><a href="{m}.html">{label}</a></li>' for m,_,label in pages)
    (out/'OPEN-FIRST.html').write_text('<!doctype html><meta charset="utf-8"><title>月影车姬 · 修改记录评审</title><style>body{background:#0F2420;color:#F1EEE4;font:18px sans-serif;padding:40px}a{color:#D4B26A}li{margin:24px}</style><h1>月影车姬 · 修改记录与恢复上一版（未上线）</h1><p>实际项目 HTML/CSS/JS，接口为浏览器内存示例。禁止外网，不会发送 Telegram，也不会改生产数据。老师页建议 390×844，后台 1440×1000。</p><ul>'+links+'</ul>')
    (out/'README.txt').write_text('打开 OPEN-FIRST.html。这是离线评审包，不是上线证明。\n恢复必须在预览页勾选确认后才会发出模拟请求。\n')
    print(out/'OPEN-FIRST.html')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,required=True);export(parser.parse_args().out)
