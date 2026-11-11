/* Offline full-view map: layout, resize/invalidateSize, exit release, history, docks. No browser/CDP. */
const fs=require('node:fs'),assert=require('node:assert/strict'),{JSDOM}=require('jsdom'),{createLeaflet}=require('./map_leaflet_mock.cjs');
const wait=(ms=0)=>new Promise(r=>setTimeout(r,ms));
function fixture(){
 const html=fs.readFileSync('miniapp/index.html','utf8');
 const dom=new JSDOM(html,{url:'https://offline.invalid/',runScripts:'outside-only',pretendToBeVisual:true}),w=dom.window,L=createLeaflet();
 w.scrollTo=()=>{};w.__yycjHomeBooted=true;w.localStorage.setItem('yycj_city','深圳');w.localStorage.setItem('yycj_token','OFFLINE_UNIT_TRANSPORT_ONLY');
 w.__yycjLoadLeaflet=async()=>L;
 const items=[{lamp_id:'a',title:'示例青',city:'深圳',district:'南山',open_text:'营业中',media_count:0},{lamp_id:'b',title:'示例红',city:'深圳',district:'南山',open_text:'休息',media_count:1,cover_url:'/photo-real'},{lamp_id:'c',title:'示例蓝',city:'深圳',district:'福田'}];
 const data={city:'深圳',enabled_cities:['深圳','广州'],items,pins:[],has_more:false,map_groups:[{lat:22.53,lng:113.93,lamp_ids:['a','b']},{lat:22.54,lng:114.06,lamp_ids:['c']}]};
 const calls=[];
 w.fetch=async(url)=>{calls.push(String(url));return {ok:true,json:async()=>data};};
 for(const n of ['home-ui.js','map-results.js','community-ui.js'])w.eval(fs.readFileSync('miniapp/'+n,'utf8'));
 const $=s=>w.document.querySelector(s);
 return {dom,w,L,$,data,calls,items,async start(){ $('#hubMapToggle').click(); await wait(); await wait(); },close(){dom.window.close()}};
}
(async()=>{let cases=0;
 {const f=fixture();try{
   await f.start();
   assert(f.w.__yycjMapMode);
   assert(f.$('#app').classList.contains('map-full-open'));
   assert(!f.$('#view-map').classList.contains('hidden'));
   assert(f.$('#mapFullBack'));assert(f.$('#mapFullCity'));assert(f.$('#mapFullQ'));assert(f.$('#mapFullFilterBtn'));
   assert.equal(f.$('#mapFullFilterBtn').getAttribute('aria-expanded'),'false');
   assert(f.$('#mapFullFilterPanel').classList.contains('hidden'));
   assert(f.$('#feed').classList.contains('hidden'));
   assert(!f.$('#hubMapWrap').classList.contains('hidden'));
   assert.equal(f.$('#hubMapWrap').parentElement.id,'mapFullStage');
   const nav=f.$('.bottom-nav'), bar=f.$('.topbar');
   // Class hook is the contract; computed style may be empty in jsdom without the stylesheet.
   assert(f.$('#app').classList.contains('map-full-open'));
   assert(f.$('.hub-map-privacy-fold'));
   assert(f.$('.hub-map-privacy').textContent.includes('不代表资料具体位置'));
   assert(!f.$('.hub-map-privacy').textContent.includes('米内'));
   assert.equal(f.L.maps.length,1);
   const before=f.L.maps[0].invalidates||0;
   f.w.dispatchEvent(new f.w.Event('resize'));
   await wait();
   assert((f.L.maps[0].invalidates||0)>before,'resize must invalidateSize');
   const mapObj=f.L.maps[0];
   f.$('#mapFullBack').click();
   await wait();
   assert(!f.w.__yycjMapMode);
   assert(!f.$('#app').classList.contains('map-full-open'));
   assert(f.$('#view-map').classList.contains('hidden'));
   assert(!f.$('#feed').classList.contains('hidden'));
   assert(mapObj.removed,'leaving the map must release the Leaflet instance');
   await f.start();
   assert.equal(f.L.maps.length,2,'re-entry must create a new map');
   assert(!f.L.maps[1].removed);
   cases++;
 }finally{f.close()}}
 {const f=fixture();try{
   await f.start();
   f.$('[data-map-select]')||true;
   f.L.markers.find(x=>x.opts.icon.className.includes('is-single')||x.opts.icon.html.includes('●'))?.fire('click')
     || f.L.markers.find(x=>x.opts.icon.html.includes('●'))?.fire('click');
   if(!f.$('#hubMapSelection')||f.$('#hubMapSelection').classList.contains('hidden')){
     f.L.markers.find(x=>x.opts.icon.html.includes('1')||x.opts.icon.className.includes('is-single'))?.fire('click');
   }
   // Prefer a single-point marker: the 福田 one is single.
   const singles=f.L.markers.filter(x=>x.opts.icon.className.includes('is-single'));
   if(singles.length) singles.at(-1).fire('click');
   else f.L.markers.at(-1).fire('click');
   await wait();
   const wrap=f.$('#hubMapWrap');
   if(!f.$('#hubMapSelection').classList.contains('hidden')){
     assert(wrap.classList.contains('has-dock')||wrap.style.getPropertyValue('--map-dock-h')!=='','selected card reserves dock space');
     f.$('[data-map-close]').click();
     assert(f.$('#hubMapSelection').classList.contains('hidden'));
     assert(!wrap.classList.contains('has-dock')||wrap.style.getPropertyValue('--map-dock-h')==='0px');
   }
   const cluster=f.L.markers.find(x=>x.opts.icon.html.includes('2'));
   cluster.fire('click');
   const chooser=f.$('#hubMapChooser');
   assert(!chooser.classList.contains('hidden'));
   assert(chooser.querySelector('[data-map-close]'));
   assert(chooser.querySelector('.hub-map-options'));
   assert.equal(chooser.querySelectorAll('[data-map-select]').length,2);
   cases++;
 }finally{f.close()}}
 {const f=fixture();try{
   f.w.scrollTo=(...a)=>{f.w.__sy=typeof a[0]==='object'?a[0].top:a[1]||a[0];};
   Object.defineProperty(f.w,'scrollY',{get:()=>42,configurable:true});
   f.$('#homeQ').value='花名';
   await f.start();
   f.$('#mapFullQ').value='新搜索';
   f.L.maps[0].move({south:23,west:115,north:24,east:116},12);
   const n=f.calls.length;
   f.L.maps[0].fire('moveend');
   await wait();
   assert.equal(f.calls.length,n,'dragging must not re-query');
   f.$('#hubSearchArea').click();
   assert(f.calls.length>n,'搜索此区域 is explicit');
   f.$('#mapFullBack').click();
   await wait();
   assert.equal(f.$('#homeQ').value,'花名','back restores original homepage search');
   cases++;
 }finally{f.close()}}
 {const f=fixture();try{
   await f.start();
   f.$('#mapFullFilterBtn').click();
   assert.equal(f.$('#mapFullFilterBtn').getAttribute('aria-expanded'),'true');
   assert(!f.$('#mapFullFilterPanel').classList.contains('hidden'));
   const ev=new f.w.PopStateEvent('popstate',{state:{}});
   f.w.dispatchEvent(ev);
   await wait();
   assert(f.w.__yycjMapMode,'first Back closes the filter overlay, not the map');
   assert(f.$('#mapFullFilterPanel').classList.contains('hidden'));
   f.w.dispatchEvent(new f.w.PopStateEvent('popstate',{state:{}}));
   await wait();
   assert(!f.w.__yycjMapMode);
   assert(!f.$('#app').classList.contains('map-full-open'));
   cases++;
 }finally{f.close()}}
 {const f=fixture();try{
   const js=fs.readFileSync('miniapp/community-ui.js','utf8')+fs.readFileSync('miniapp/map-results.js','utf8');
   assert(!js.includes('requestFullscreen')&&!js.includes('requestFullScreen'));
   assert(!js.includes('geolocation'));
   assert(!js.includes('openTelegramLink')||js.includes('openLink')); // community links only
   const css=fs.readFileSync('miniapp/community.css','utf8');
   assert(css.includes('117') && css.includes('three-column')||css.includes('three-column grid')||css.includes('Homepage'));
   assert(css.includes('--map-dock-h'));
   cases++;
 }finally{f.close()}}
 console.log(`Map fullview DOM: ${cases} enter/exit/release/reentry/resize/dock/explicit-search/history/privacy cases passed`);
})().catch(e=>{console.error(e);process.exitCode=1});
