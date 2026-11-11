#!/usr/bin/env python3
"""Export actual map modules + vendored Leaflet with labelled schematic tiles; no auth/SDK/network."""
import argparse
import json
from pathlib import Path
import re
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from bot.services.feed_filters import center_of


def export(dest, live_tiles=False):
 dest.mkdir(parents=True,exist_ok=True)
 (dest/'LAYOUT-REVIEW.md').write_text((ROOT/'docs/map-upgrade-review.md').read_text())
 items=[];groups=[]
 for district,count in [('南山',6),('福田',3),('罗湖',2),('宝安',1)]:
  ids=[]
  for i in range(count):
   ident=district+'-'+str(i);ids.append(ident);items.append({'lamp_id':ident,'title':['小青','小红','小蓝','小荷','小晴','小宁'][i],'city':'深圳','district':district,'open_code':'open' if i%2==0 else 'rest','open_text':'营业中' if i%2==0 else '休息中','media_count':1 if i==1 else 0,'cover_url':'/offline-fixture-photo.svg' if i==1 else ''})
  lat,lng=center_of('深圳',district);groups.append({'district':district,'count':count,'lat':lat,'lng':lng,'lamp_ids':ids})
 items.append({'lamp_id':'unknown','title':'未标注区中心示例','city':'深圳','district':'未标注地区','media_count':0})
 data={'city':'深圳','enabled_cities':['深圳','广州'],'items':items,'map_groups':groups,'map_truncated':False,'map_filter_options':{'districts':[{'key':g['district'],'label':g['district']} for g in groups]+[{'key':'__missing__','label':'未标注地区'}]}}

 base=(ROOT/'miniapp/index.html').read_text()
 base=re.sub(r'<script\b[^>]*>[\s\S]*?</script>','',base)
 base=re.sub(r'<link\b[^>]*rel="stylesheet"[^>]*>',lambda m:'<style>'+(ROOT/'miniapp/app.bundle.css').read_text()+'</style>',base)
 base=base.replace('<head>','<head><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src \'unsafe-inline\'; style-src \'unsafe-inline\'; img-src data: blob: file:; connect-src \'none\'; form-action \'none\'">')
 base=base.replace('</head>','<style>'+(ROOT/'miniapp/vendor/leaflet/leaflet.css').read_text()+'''\n#mapReviewCaption{background:#2b342a;color:#ddcb90;font:11px/1.6 sans-serif;padding:5px 12px;text-align:center;border-bottom:1px solid #53654a}#boot,#gate{display:none!important}#app{display:block!important}.schematic-tile{background:#1c332d;color:#4b6b56;font:11px sans-serif}.map-review-photo{display:none}</style></head>''')
 base=base.replace('<body>','<body><div id="mapReviewCaption">离线审核 · 示例资料 / 示意底图（非真实道路） · 未发布</div>',1)
 leaf=(ROOT/'miniapp/vendor/leaflet/leaflet.js').read_text()
 setup='''
localStorage.setItem('yycj_city','深圳');
const $=s=>document.querySelector(s);$('#app').classList.remove('hidden');$('#boot')?.classList.add('hidden');$('#gate')?.classList.add('hidden');$('#btnCity').textContent='深圳 ▾';
let lastMap;const realMap=L.map;L.map=(...args)=>(lastMap=realMap(...args));
// Only tile rendering is replaced. Real Leaflet projection, bounds, zoom, markers and events run.
const Schematic=L.GridLayer.extend({createTile(coords){const c=document.createElement('canvas');c.width=c.height=256;c.className='schematic-tile';const g=c.getContext('2d');g.fillStyle='#eceee7';g.fillRect(0,0,256,256);g.strokeStyle='#b8c5b7';g.lineWidth=7;g.beginPath();g.moveTo(-10,40);g.lineTo(270,160);g.moveTo(60,-10);g.lineTo(180,270);g.stroke();g.strokeStyle='#c4d0be';g.lineWidth=1;g.strokeRect(0,0,256,256);g.fillStyle='#67786c';g.font='11px sans-serif';g.fillText('示意底图 · 非真实道路',18,220);return c;}});
L.tileLayer=()=>new Schematic({attribution:'离线示意图 · 非真实地图',maxZoom:15});window.__yycjLoadLeaflet=async()=>L;
const data=DATA;
// Explicitly illustrative sample media, not a real person's photo.
const photo='data:image/svg+xml,'+encodeURIComponent('<svg xmlns="http://www.w3.org/2000/svg" width="60" height="75"><rect width="60" height="75" fill="#3a604f"/><circle cx="30" cy="24" r="13" fill="#8ba38d"/><path d="M8 67 Q30 24 52 67" fill="#8ba38d"/><text x="30" y="73" text-anchor="middle" font-size="9" fill="#f0d69a">示例图</text></svg>');// Media response adapter: inline the known sample asset without changing product JS.
new MutationObserver(()=>{document.querySelectorAll('#hubMapSelection img[src="/offline-fixture-photo.svg"]').forEach(img=>img.src=photo)}).observe(document.body,{subtree:true,childList:true});
let seq=0,failNext=false,hold=false;
const gz=GZ;
function fixtureResults(){const f=window.__yycjMapFilters.get(),base=f.city==='广州'?gz:data,query=$('#homeQ').value.trim();return {...base,items:base.items.filter(x=>(!f.district||(f.district==='__missing__'?!x.district||x.district==='未标注地区':x.district===f.district))&&(f.service==='all'||x.open_code===f.service)&&(!query||x.title.includes(query)))};}window.__mapReviewCalls=0;
window.__yycjLoadHome=({mapArea=null}={})=>{const meta={seq:++seq,city:localStorage.getItem('yycj_city'),area:mapArea};window.__mapReviewCalls++;window.dispatchEvent(new CustomEvent('yycj:map-loading',{detail:meta}));
 if(hold)return;
 setTimeout(()=>{if(failNext){failNext=false;window.dispatchEvent(new CustomEvent('yycj:map-error',{detail:meta}));return;}const ev=new CustomEvent('yycj:home-data',{detail:fixtureResults()});ev.mapRequest=meta;window.dispatchEvent(ev);},80);
};
window.fetch=async url=>{if(String(url).startsWith('/api/lamps/'))return{ok:true,json:async()=>({lamp:{title:'离线详情示例'}})};throw Error('offline only')};
window.__yycjRenderDetail=(d,id)=>{$('#detail').textContent='离线详情占位（实际导航由集成测试验证） · '+id};$('#btnBackHome').addEventListener('click',()=>{document.querySelectorAll('.view').forEach(v=>v.classList.toggle('hidden',v.id!=='view-home'));});
'''.replace('DATA',json.dumps(data,ensure_ascii=False)).replace('GZ',json.dumps({'city':'广州','enabled_cities':['深圳','广州'],'items':[{'lamp_id':'广州-示例','title':'广州示例资料','city':'广州','district':'天河','open_code':'rest','open_text':'休息中','media_count':0}], 'map_groups':[{'district':'天河','lat':center_of('广州','天河')[0],'lng':center_of('广州','天河')[1],'lamp_ids':['广州-示例']}],'map_filter_options':{'districts':[{'key':'天河','label':'天河'},{'key':'__missing__','label':'未标注地区'}]}},ensure_ascii=False))
 if live_tiles:
  base=base.replace('img-src data: blob: file:;', 'img-src data: blob: file: https://tile.openstreetmap.org;')
  base=base.replace('<head>', '<head><meta name="referrer" content="strict-origin-when-cross-origin">')
  base=base.replace('离线审核 · 示例资料 / 示意底图（非真实道路） · 未发布', '本地文件审核 · 示例资料 · 未发布 · 非Telegram实机（手动请求OSM，file来源可能受限）')
  setup=setup.replace('// Only tile rendering is replaced. Real Leaflet projection, bounds, zoom, markers and events run.', '// Manual live fixture: actual OSM layer; never auto-started by the exporter.')
  setup=re.sub(r'const Schematic=L.GridLayer.extend.*?window.__yycjLoadLeaflet=async\(\)=>L;', 'window.__yycjLoadLeaflet=async()=>L;', setup, flags=re.S)
 source=''.join('<script>'+s.replace('</script','<\\/script')+'</script>' for s in [leaf,'window.__yycjHomeBooted=true;',(ROOT/'miniapp/home-ui.js').read_text(),setup,(ROOT/'miniapp/map-results.js').read_text(),(ROOT/'miniapp/community-ui.js').read_text()])
 for case in (['live'] if live_tiles else ['overview','selected','chooser','empty','error','loading','desktop','tile-error','dense','filters','filters-pending','filter-empty']):
  after='''
const fixtureCase=CASE;
if(fixtureCase==='dense'){
 const g=data.map_groups[0];data.items=Array.from({length:600},(_,i)=>({lamp_id:'dense-'+i,title:'同区示例资料 '+String(i+1).padStart(3,'0'),city:'深圳',district:g.district,media_count:0,open_text:'营业中',open_code:'open'}));data.map_groups=[{...g,lamp_ids:data.items.map(x=>x.lamp_id),count:600}];data.map_truncated=true;
}
$('#hubMapToggle').click();
let ticks=0;const timer=setInterval(()=>{if(++ticks>100){clearInterval(timer);return}if(!lastMap||!document.querySelector('.hub-map-marker'))return;clearInterval(timer);
 setTimeout(()=>{
 const action=CASE;
 if(action==='selected'||action==='chooser'||action==='dense'){
  const centre=data.map_groups.find(g=>g.district==='南山');lastMap.setView([centre.lat,centre.lng],14,{animate:false});
  const marker=[...document.querySelectorAll('.hub-map-marker')].find(e=>e.getAttribute('title')===(action==='dense'?'600':'6')+'份区域资料');
  marker?.click();if(action==='dense'){const options=document.querySelector('.hub-map-options');options.scrollTop=options.scrollHeight;}if(action==='selected')document.querySelector('[data-map-select="南山-1"]')?.click();
 }
 if(['filters','filters-pending','filter-empty'].includes(action)){window.__yycjMapFilters.set({district:'南山',service:'rest'});if(action==='filter-empty')$('#homeQ').value='不存在的示例';if(action!=='filters-pending')window.__yycjMapFilters.apply();}
 if(action==='tile-error'){lastMap.eachLayer(layer=>{if(layer instanceof L.GridLayer)layer.fire('tileerror')});}
 if(['empty','error','loading'].includes(action)){
  lastMap.setView([22.1,113.7],13,{animate:false});if(action==='error')failNext=true;if(action==='loading')hold=true;
  setTimeout(()=>document.querySelector('#hubSearchArea')?.click(),60);
 }
 setTimeout(()=>{document.body.dataset.mapReviewReady='1'},220);
 },120);
},40);
'''.replace('CASE',json.dumps(case))
  if live_tiles:
   after="const liveButton=document.createElement('button');liveButton.className='btn';liveButton.textContent='手动加载真实OSM底图';document.querySelector('#mapReviewCaption').appendChild(liveButton);liveButton.onclick=()=>{liveButton.disabled=true;if(!window.__yycjMapMode)document.querySelector('#hubMapToggle').click();};"
  content=base.replace('</body>',source+'<script>'+after+'</script></body>')
  (dest/('map-'+case+'.html')).write_text(content)
  if live_tiles:(dest/'map-live-file.html').write_text(content)
 if not live_tiles:(dest/'index.html').write_text('<!doctype html><meta charset="utf-8"><title>地图首批升级 · 离线审核</title><body style="font:16px sans-serif;background:#10251e;color:#dbc894;padding:24px"><h1>地图首批升级 · 未发布</h1><p>真实本地CSS/JS与Leaflet；示意底图和示例资料，不是真实道路/线上数据。无Telegram SDK/鉴权/定位权限。live页须人工打开并点击按钮，才加载真实OSM瓦片，其他页完全离线。</p>'+''.join('<p><a style="color:#e2ca83" href="map-'+c+'.html">'+c+'</a></p>' for c in ['overview','selected','chooser','empty','error','loading','desktop','tile-error','dense','filters','filters-pending','filter-empty','live'])+'<h2>短屏与故障截图</h2>'+''.join('<p><a style="color:#e2ca83" href="map-'+c+'.png">'+c+'</a></p>' for c in ['dense-360x640','dense-390x640','dense-360x568','dense-390x900','dense-selected-360x640','selected-360x568','tile-error','tile-list-360x640'])+'</body>')
 print(str(dest))

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--live-tiles',action='store_true');args=p.parse_args();export(args.output.resolve(),args.live_tiles)
