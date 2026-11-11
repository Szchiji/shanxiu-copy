// Privacy-safe results map. Only server-published district centres are mapped, never listing coordinates.
(() => {
 'use strict';
 const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const valid=p=>Number.isFinite(p.lat)&&Number.isFinite(p.lng)&&Math.abs(p.lat)<=90&&Math.abs(p.lng)<=180;
 const same=(a,b)=>a&&b&&['south','west','north','east'].every(k=>Math.abs(a[k]-b[k])<0.0001);
 const inside=(p,b)=>!b||(p.lat>=b.south&&p.lat<=b.north&&p.lng>=b.west&&p.lng<=b.east);
 function points(data){
  const byId=new Map((data.items||[]).slice(0,600).map(x=>[String(x.lamp_id),x])),out=[],seen=new Set();
  for(const g of data.map_groups||[]){
   // Deliberately ignore any coordinate-like fields on listing cards.
   const p={lat:g.lat,lng:g.lng};if(!valid(p))continue;
   for(const id of g.lamp_ids||[]){const key=String(id),item=byId.get(key);if(item&&!seen.has(key)&&(!data.city||!item.city||item.city===data.city)){out.push({...p,item});seen.add(key);}}
  }
  return out;
 }
 function clusters(rows,project,radius=52){
  // Connected components of nearby projected pixels: no adjacent overlapping 44px targets.
  const pts=rows.map(p=>project([p.lat,p.lng])),parent=rows.map((_,i)=>i);
  const find=i=>parent[i]===i?i:(parent[i]=find(parent[i]));
  for(let i=0;i<rows.length;i++)for(let j=0;j<i;j++)if(Math.hypot(pts[i].x-pts[j].x,pts[i].y-pts[j].y)<radius)parent[find(i)]=find(j);
  const groups=new Map();rows.forEach((p,i)=>{const key=find(i);if(!groups.has(key))groups.set(key,[]);groups.get(key).push(p);});
  const result=[...groups.values()];
  // A connected ring can surround an isolated point: the marker centroids would
  // overlap even though no original points are close. Merge overlapping targets
  // until stable, using exactly the same geographic centroid as paint().
  const centre=g=>project([g.reduce((s,p)=>s+p.lat,0)/g.length,g.reduce((s,p)=>s+p.lng,0)/g.length]);
  let merged=true;while(merged){merged=false;const centres=result.map(centre);outer:for(let i=0;i<result.length;i++)for(let j=0;j<i;j++)if(Math.hypot(centres[i].x-centres[j].x,centres[i].y-centres[j].y)<radius){result[j].push(...result[i]);result.splice(i,1);merged=true;break outer;}}
  return result;
 }
 class ResultMap{
  constructor(host,{city,onSearch,onDetail,loadLeaflet}){
   this.host=host;this.city=city;this.onSearch=onSearch;this.onDetail=onDetail;this.loadLeaflet=loadLeaflet;this.seq=0;this.epoch=0;this.rows=[];this.selected='';this.visible=true;this.busy=false;
   host.innerHTML='<div class="hub-map-heading"><b>地图找资料</b><span id="hubMapCount"></span></div><details class="hub-map-privacy-fold"><summary>位置说明</summary><p class="hub-map-privacy">仅按公开行政区 / 镇中心展示，不代表资料具体位置，也不提供精确距离或导航承诺。</p></details><div class="hub-map-stage"><div id="hubResultMap" aria-label="本城资料区域地图"></div><button type="button" class="btn hub-search-area hidden" id="hubSearchArea">搜索此区域</button></div><div id="hubMapStatus" class="hub-map-status" role="status" aria-live="polite"></div><div id="hubMapTileStatus" class="hub-map-tile-status hidden" role="status"><span>底图暂不可用</span><button type="button" class="btn" data-map-tiles-retry>重试底图</button><button type="button" class="btn" data-map-list>查看列表</button></div><section id="hubMapSelection" class="hub-map-selection hidden" aria-label="已选资料"></section><section id="hubMapChooser" class="hub-map-chooser hidden" aria-label="同区域资料"></section><details id="hubMapUnlocated" class="hidden"><summary></summary><div></div></details>';
   this.$=s=>host.querySelector(s);
   this.click=e=>{const b=e.target.closest('button');if(!b)return;if(b.hasAttribute('data-map-tiles-retry'))this.retryTiles();else if(b.hasAttribute('data-map-list')){if(!this.busy&&this.rows.length)this.choose(this.rows,true);}else if(b.id==='hubSearchArea')this.search();else if(b.hasAttribute('data-map-retry'))this.retry();else if(b.hasAttribute('data-map-select'))this.select(b.dataset.mapSelect);else if(b.hasAttribute('data-map-detail')){if(this.visible&&!this.busy&&this.detailIds?.has(String(b.dataset.mapDetail)))this.onDetail(b.dataset.mapDetail);}else if(b.hasAttribute('data-map-close'))this.deselect();else if(b.hasAttribute('data-map-choose'))this.choose(this.chosen||[],this.chosenAsList);};
   host.addEventListener('click',this.click);
   this.onResize=()=>{if(!this.visible||!this.map)return;this.layoutDock();this.map.invalidateSize({pan:false});};
   this._onVV=()=>this.onResize();
   window.addEventListener('resize',this.onResize);
   window.visualViewport?.addEventListener('resize',this._onVV);
  }
  bounds(){if(!this.map||this.map.getZoom()==null)return null;const b=this.map.getBounds();return {south:b.getSouth(),west:b.getWest(),north:b.getNorth(),east:b.getEast()};}
  status(text,retry=false){this.$('#hubMapStatus').innerHTML='<span>'+esc(text)+'</span>'+(retry?'<button type="button" class="btn" data-map-retry>'+ (this.meta?.area?'重试此区域':'重试加载')+'</button>':'');}
  begin(meta){if(!this.visible)return;this.seq=meta.seq;this.meta=meta;this.busy=true;++this.epoch;this.rows=[];this.detailIds=new Set();this.$('#hubMapUnlocated').classList.add('hidden');this.$('#hubMapCount').textContent='读取中';this.deselect();this.$('#hubMapChooser').classList.add('hidden');this.layer?.clearLayers();this.status('正在读取本城资料…');this.updateSearch();}
  error(meta){if(!this.visible||meta.seq!==this.seq||meta.city!==this.city())return;this.busy=false;this.$('#hubMapCount').textContent='搜索失败';this.status('加载失败，请重试。未展示旧搜索结果。',true);this.updateSearch();}
  updateSearch(){const b=this.$('#hubSearchArea');const moved=this.map&&!same(this.bounds(),this.searched);b.classList.toggle('hidden',!moved||!this.visible);b.disabled=this.busy;b.textContent=this.busy?'搜索中…':'搜索此区域';this.tileNotice();}
  // Full-view mode sizes the map around the bottom dock so zoom and close stay reachable.
  // Isolated hosts still scroll the panel into view. No SDK/native viewport here.
  fullView(){return !!(this.host.closest('#view-map')||document.getElementById('app')?.classList.contains('map-full-open'));}
  layoutDock(){
   const sel=this.$('#hubMapSelection'),cho=this.$('#hubMapChooser'),miss=this.$('#hubMapUnlocated');
   let panel=null;
   if(cho&&!cho.classList.contains('hidden'))panel=cho;
   else if(sel&&!sel.classList.contains('hidden'))panel=sel;
   else if(miss&&!miss.classList.contains('hidden')&&miss.open)panel=miss;
   const h=panel&&this.fullView()?Math.ceil(panel.getBoundingClientRect().height||panel.offsetHeight||0):0;
   this.host.style.setProperty('--map-dock-h',h+'px');
   this.host.classList.toggle('has-dock',h>0);
   if(this.map&&this.fullView())this.map.invalidateSize({pan:false});
  }
  reveal(el){
   this.layoutDock();
   if(this.fullView())return;
   const r=el.getBoundingClientRect();if(!r.height)return;const nav=document.querySelector('.bottom-nav')?.getBoundingClientRect(),bottom=Math.min(window.innerHeight,nav?.height?nav.top:window.innerHeight)-12;const delta=r.bottom>bottom?r.bottom-bottom:r.top<12?r.top-12:0;if(delta)window.scrollBy({top:delta,behavior:'auto'});
  }
  tileNotice(){const e=this.$('#hubMapTileStatus');e.classList.toggle('hidden',!this.tileFailed);const retry=e.querySelector('[data-map-tiles-retry]');retry.disabled=!!this.tileRetrying;retry.textContent=this.tileRetrying?'重试中…':'重试底图';e.querySelector('[data-map-list]').disabled=this.busy||!this.rows.length;}
  retryTiles(){if(!this.tiles||this.tileRetrying||!this.visible)return;this.tileRetrying=true;this.tileLoadFailed=false;this.tileNotice();clearTimeout(this.tileRetryTimer);this.tileRetryTimer=setTimeout(()=>{this.tileRetrying=false;this.tileFailed=true;this.tileNotice();},10000);this.tiles.redraw();}
  search(){if(this.busy||!this.visible)return;this.onSearch(this.bounds());}
  retry(){if(!this.busy&&this.visible)this.onSearch(this.meta?.area||null);}
  async receive(data,meta={}){
   if(!this.visible||(meta.seq&&meta.seq!==this.seq)||(meta.city&&meta.city!==this.city())||(data.city&&data.city!==this.city()))return;
   const epoch=++this.epoch;this.meta=meta;this.busy=false;this.selected='';this.chosen=[];this.$('#hubMapSelection').classList.add('hidden');this.$('#hubMapChooser').classList.add('hidden');
   this.detailIds=new Set((data.items||[]).slice(0,600).filter(x=>!data.city||!x.city||x.city===data.city).map(x=>String(x.lamp_id)));
   this.all=points(data);this.rows=this.all.filter(p=>inside(p,meta.area));this.data=data;
   const mapped=new Set(this.all.map(p=>String(p.item.lamp_id))),missing=(data.items||[]).slice(0,600).filter(x=>!mapped.has(String(x.lamp_id))&&(!data.city||!x.city||x.city===data.city));
   const unknown=this.$('#hubMapUnlocated');unknown.classList.toggle('hidden',!missing.length);unknown.open=false;unknown.querySelector('summary').textContent=missing.length+' 份无公开区中心 · 不参与区域筛选';unknown.querySelector('div').innerHTML=missing.map(x=>this.rowButton(x,'data-map-detail')).join('');
   this.$('#hubMapCount').textContent=this.rows.length+' 份'+(meta.area?' · 此区域':missing.length?'可定位 / '+(this.rows.length+missing.length)+' 份结果':' · 本城');
   this.$('.hub-map-stage').classList.toggle('hidden',!this.all.length&&!meta.area);
   const suffix=data.map_truncated?' · 已达600份上限，请缩小现有筛选（区域搜索仅筛选这批结果）':'';
   this.status((this.rows.length?'点圆点选资料，数字表示聚合数量':meta.area?'此区域暂无匹配资料，可移动地图后再搜索':missing.length?'暂无可定位资料，可展开下方列表':'暂无匹配资料，请调整城市或现有筛选')+suffix);
   if(!this.all.length&&!this.map){this.$('#hubResultMap').classList.add('hidden');this.updateSearch();return;}
   try{
    const L=await this.loadLeaflet();if(epoch!==this.epoch||!this.visible)return;
    if(!this.map){
     this.L=L;this.map=L.map('hubResultMap',{scrollWheelZoom:false,zoomControl:false,maxZoom:15});L.control.zoom({position:'topright'}).addTo(this.map);
     this.layer=L.layerGroup().addTo(this.map);
     const tiles=this.tiles=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:15,updateWhenIdle:true,keepBuffer:1,attribution:'© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'}).addTo(this.map);
     tiles.on('loading',()=>{this.tileLoadFailed=false;});
     tiles.on('tileerror',()=>{this.tileLoadFailed=true;this.tileFailed=true;this.tileNotice();});
     tiles.on('load',()=>{clearTimeout(this.tileRetryTimer);this.tileRetrying=false;this.tileFailed=!!this.tileLoadFailed;this.tileNotice();});
     this.onMove=()=>{if(this.restoring){this.paint();this.updateSearch();return;}this.deselect();this.$('#hubMapChooser').classList.add('hidden');this.updateSearch();};this.map.on('moveend zoomend',this.onMove);this.map.on('click',()=>this.deselect());this.mapReady=true;
    }
    this.map.invalidateSize();
    if(!meta.area&&this.all.length)this.map.fitBounds(this.all.map(p=>[p.lat,p.lng]),{padding:[42,42],maxZoom:12,animate:false});
    // No invented city/point when nothing has a public centre. Retain the existing view if any.
    if(!this.map.getZoom()&&!this.all.length){this.$('#hubResultMap').classList.add('hidden');this.updateSearch();return;}
    this.$('#hubResultMap').classList.remove('hidden');this.searched=meta.area||this.bounds();this.paint();this.layoutDock();this.updateSearch();
   }catch(e){if(epoch===this.epoch){if(this.map&&!this.mapReady){this.map.remove();this.map=null;this.layer=null;this.tiles=null;}this.status('地图加载失败，可重试或使用资料列表。',true);this.choose(this.rows,true);}}
  }
  rowButton(item,attr='data-map-select'){return '<button type="button" class="hub-map-row" '+attr+'="'+esc(item.lamp_id)+'"><b>'+esc(item.title||'未命名资料')+'</b><span>'+esc(item.district||'未标注地区')+(item.open_text?' · '+esc(item.open_text):'')+' ›</span></button>';}
  paint(){if(!this.map)return;this.layer.clearLayers();if(this.busy||!this.visible||(this.data?.city&&this.data.city!==this.city()))return;
   for(const group of clusters(this.rows,p=>this.map.project(p,this.map.getZoom()))){
    const lat=group.reduce((s,p)=>s+p.lat,0)/group.length,lng=group.reduce((s,p)=>s+p.lng,0)/group.length;
    const one=group.length===1,item=group[0].item,selected=group.some(p=>String(p.item.lamp_id)===this.selected);
    const marker=this.L.marker([lat,lng],{keyboard:true,title:one?(item.title||'查看资料'):group.length+'份区域资料',icon:this.L.divIcon({className:'hub-map-marker'+(one?' is-single':'')+(selected?' is-selected':''),html:one?'<span aria-hidden="true">●</span>':'<b>'+group.length+'</b>',iconSize:[44,44],iconAnchor:[22,22]})}).addTo(this.layer);
    marker.on('click',()=>{
     if(one){this.select(item.lamp_id);return;}
     const distinct=new Set(group.map(p=>p.lat+','+p.lng));
     if(distinct.size>1&&this.map.getZoom()<15){const before=this.map.getZoom();this.map.fitBounds(group.map(p=>[p.lat,p.lng]),{padding:[56,56],maxZoom:Math.min(15,before+2),animate:false});if(this.map.getZoom()<=before)this.choose(group);}
     else this.choose(group);
    });
   }
  }
  choose(group,list=false){this.chosen=group;this.chosenAsList=list;this.selected='';this.$('#hubMapSelection').classList.add('hidden');const e=this.$('#hubMapChooser');e.classList.remove('hidden');e.innerHTML='<div class="hub-chooser-title"><b>'+(list?'当前结果 ':'区域内 ')+group.length+' 份资料</b><button type="button" class="btn" data-map-close aria-label="关闭区域资料">×</button></div><p class="hub-map-privacy">仅按公开区中心展示，不偏移到虚构位置，也不表示精确距离</p><div class="hub-map-options">'+group.map(p=>this.rowButton(p.item)).join('')+'</div>';this.paint();this.reveal(e);}
  select(id){const p=this.rows.find(p=>String(p.item.lamp_id)===String(id));if(!p)return;this.selected=String(id);this.$('#hubMapChooser').classList.add('hidden');const x=p.item,el=this.$('#hubMapSelection');el.classList.remove('hidden');
   // cover_url is supplied by the existing public card serializer; no synthetic portrait.
   const cover=String(x.cover_url||'');const media=Number(x.media_count)>0&&/^(https?:\/\/|\/)/.test(cover)?'<img src="'+esc(cover)+'" alt="" loading="lazy">':'';
   el.innerHTML=media+'<div class="hub-map-card-text"><b>'+esc(x.title||'未命名资料')+'</b><p>'+esc([x.city,x.district].filter(Boolean).join(' · '))+'</p>'+(x.open_text?'<span>'+esc(x.open_text)+'</span>':'')+'</div><button type="button" class="btn" data-map-detail="'+esc(id)+'">查看详情</button><button type="button" class="hub-map-close" data-map-close aria-label="关闭已选资料">×</button>'+(this.chosen?.length>1?'<button type="button" class="hub-map-siblings" data-map-choose>'+(this.chosenAsList?'当前结果 ':'同区域 ')+this.chosen.length+' 份 ›</button>':'');this.paint();this.reveal(el);
  }
  deselect(){this.selected='';this.$('#hubMapSelection').classList.add('hidden');this.$('#hubMapChooser').classList.add('hidden');this.paint();this.layoutDock();}
  cancel(){if(!this.busy)return;this.busy=false;this.$('#hubMapCount').textContent='未完成';this.status('搜索已取消，可重试。',true);this.updateSearch();}
  pendingChanges(){++this.epoch;this.busy=false;this.rows=[];this.detailIds=new Set();this.$('#hubMapUnlocated').classList.add('hidden');this.deselect();this.$('#hubMapCount').textContent='待应用';this.status('筛选已改，点击查看结果后更新');this.updateSearch();}
  snapshot(){
    const out={selected:this.selected,chosen:this.chosen,chosenAsList:this.chosenAsList,searched:this.searched};
    if(this.map&&this.map.getZoom()!=null){
     out.zoom=this.map.getZoom();
     out.box=this.bounds();
     try{const c=this.map.getCenter();if(c)out.center={lat:c.lat,lng:c.lng};}catch(e){}
    }
    return out;
  }
  restore(s){
    if(!s||!this.map)return;
    this.restoring=true;
    try{
     if(s.center&&typeof this.map.setView==='function')this.map.setView([s.center.lat,s.center.lng],s.zoom,{animate:false});
     else if(s.box&&typeof this.map.move==='function')this.map.move(s.box,s.zoom);
     this.searched=s.searched||this.searched;
     this.map.invalidateSize({pan:false});
     if(s.selected)this.select(s.selected);
     else if(s.chosen&&s.chosen.length)this.choose(s.chosen,s.chosenAsList);
     else{this.paint();this.layoutDock();}
     this.updateSearch();
    }finally{this.restoring=false;}
  }
  resume(){this.restoring=true;try{this.map?.invalidateSize({pan:false});this.layoutDock();}finally{this.restoring=false;}this.paint();this.updateSearch();}
  activate(on){this.visible=on;if(!on){++this.epoch;this.busy=false;this.deselect();}else{this.map?.invalidateSize({pan:false});this.layoutDock();}}
  destroy(){++this.epoch;clearTimeout(this.tileRetryTimer);this.host.removeEventListener('click',this.click);window.removeEventListener('resize',this.onResize);window.visualViewport?.removeEventListener('resize',this._onVV);this.map?.remove();this.map=null;this.layer=null;this.tiles=null;}
 }
 window.yycjResultMap={ResultMap,points,clusters,inside,same};
})();
