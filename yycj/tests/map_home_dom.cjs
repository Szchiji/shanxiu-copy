/* Fully offline transport mock; no SDK/auth initialization or production connections. */
const fs=require('node:fs'),assert=require('node:assert/strict'),{JSDOM}=require('jsdom'),{createLeaflet}=require('./map_leaflet_mock.cjs');
const wait=()=>new Promise(r=>setTimeout(r,0));
(async()=>{
 const dom=new JSDOM(fs.readFileSync('miniapp/index.html','utf8'),{url:'https://offline.invalid/',runScripts:'outside-only',pretendToBeVisual:true}),w=dom.window,L=createLeaflet(),queue=[],calls=[];
 try{
  w.scrollTo=()=>{};w.__yycjHomeBooted=true;w.localStorage.setItem('yycj_city','深圳');w.localStorage.setItem('yycj_token','OFFLINE_UNIT_TRANSPORT_ONLY');
  w.__yycjLoadLeaflet=async()=>L;
  w.fetch=(url,opt)=>{calls.push({url,opt});return new Promise(resolve=>queue.push({url,opt,resolve}));};
  for(const n of ['home-ui.js','map-results.js','community-ui.js'])w.eval(fs.readFileSync('miniapp/'+n,'utf8'));
  const $=s=>w.document.querySelector(s),data={city:'深圳',enabled_cities:['深圳','广州'],items:[{lamp_id:'a',title:'测试',city:'深圳',district:'南山'}],map_groups:[{lat:22.53,lng:113.93,lamp_ids:['a']}],pins:[],has_more:false};
  $('#hubMapToggle').click();assert.equal(calls.length,1);assert(new URL(calls[0].url,'https://offline.invalid').searchParams.get('map_results')==='1');assert(!calls[0].url.includes('bounds'));
  queue.shift().resolve({ok:true,json:async()=>data});await wait();await wait();assert(L.maps.length===1);
  const m=L.maps[0];m.move({south:23,west:115,north:24,east:116});await wait();assert.equal(calls.length,1);$('#hubSearchArea').click();assert.equal(calls.length,2);assert($('#hubSearchArea').disabled);queue.shift().resolve({ok:true,json:async()=>data});await wait();await wait();assert($('#hubMapStatus').textContent.includes('此区域暂无'));
  const old=w.__yycjLoadHome();const newer=w.__yycjLoadHome();assert.equal(queue.length,2);queue[1].resolve({ok:true,json:async()=>({...data,items:[],map_groups:[]})});await newer;queue[0].resolve({ok:false,json:async()=>({detail:'旧错误'})});await old;await wait();assert(!$('#hubMapStatus').textContent.includes('失败'));queue.length=0;
  const fail=w.__yycjLoadHome();queue.shift().resolve({ok:false,status:503,json:async()=>({detail:'地图资料暂时无法加载，请重试'})});await fail;assert($('#hubMapStatus').textContent.includes('加载失败'));assert($('#hubMapStatus [data-map-retry]'));assert(!$('#hubMapStatus').textContent.includes('暂无'),'503 must not become an empty result');
  $('#hubMapStatus [data-map-retry]').click();queue.shift().resolve({ok:true,json:async()=>data});await wait();await wait();
  const cross=w.__yycjLoadHome();w.localStorage.setItem('yycj_city','广州');queue.shift().resolve({ok:true,json:async()=>data});await cross;assert(!$('#hubMapSelection').textContent.includes('测试')); // stale city's response not committed

  w.localStorage.setItem('yycj_city','深圳');const pending=w.__yycjLoadHome(),oldMap=queue.shift();$('#hubMapToggle').click();assert(oldMap.opt.signal.aborted);queue.shift().resolve({ok:true,json:async()=>data});oldMap.resolve({ok:true,json:async()=>data});await pending;await wait();assert($('#hubMapWrap').classList.contains('hidden'));
  $('#hubMapToggle').click();const loadingMap=queue.shift();w.__yycjMapFilters.set({district:'南山',service:'rest'});assert(loadingMap.opt.signal.aborted);const nCalls=calls.length;assert.equal($('#hubMapCount').textContent,'待应用');loadingMap.resolve({ok:true,json:async()=>data});await wait();assert.equal($('#hubMapCount').textContent,'待应用');assert.equal(calls.length,nCalls,'pending combined filters must not fetch');
  const applied=w.__yycjMapFilters.apply(),req=queue.shift();assert(req.url.includes('service=rest')&&new URL(req.url,'https://offline.invalid').searchParams.get('district')==='南山');req.resolve({ok:true,json:async()=>data});await applied;assert.equal(w.__yycjMapFilters.get().pending,false);
  const stale=w.__yycjLoadHome(),beforeCity=queue.shift();w.localStorage.setItem('yycj_city','广州');const current=w.__yycjLoadHome(),afterCity=queue.shift();assert(beforeCity.opt.signal.aborted);afterCity.resolve({ok:true,json:async()=>({...data,city:'广州',items:[],map_groups:[]})});await current;beforeCity.resolve({ok:false,status:503,json:async()=>({detail:'old city failure'})});await stale;assert(!$('#hubMapStatus').textContent.includes('加载失败'));
  // Actual deadline aborts must show retry, not be swallowed as a user cancellation.
  w.localStorage.setItem('yycj_city','深圳');let deadline,transport=0,recovery;
  const realTimer=w.setTimeout.bind(w);w.setTimeout=(fn,ms,...args)=>ms===8000?(deadline=fn,98765):realTimer(fn,ms,...args);
  w.fetch=(url,opt)=>{transport++;return new Promise((resolve,reject)=>{recovery=resolve;opt.signal.addEventListener('abort',()=>reject(new w.DOMException('Aborted','AbortError')),{once:true});});};
  const timeout=w.__yycjLoadHome();deadline();await timeout;assert($('#hubMapStatus').textContent.includes('加载失败'));assert($('#hubMapStatus [data-map-retry]'));assert(!$('#hubSearchArea').disabled,'deadline must release map busy state');
  const retry=$('#hubMapStatus [data-map-retry]');retry.click();retry.click();assert.equal(transport,2,'rapid retry must submit once');recovery({ok:true,json:async()=>data});await wait();await wait();assert(!$('#hubMapStatus').textContent.includes('失败'));
  w.fetch=async()=>{throw new TypeError('NetworkError')};await w.__yycjLoadHome();assert($('#hubMapStatus [data-map-retry]'),'network rejection must expose safe retry');assert(!$('#hubMapStatus').textContent.includes('暂无'));
  w.fetch=async()=>({ok:true,json:async()=>data});await w.__yycjLoadHome();await wait();await wait();assert(!$('#hubMapStatus').textContent.includes('失败'));
  w.fetch=async(url,opt)=>({ok:true,json:()=>new Promise((resolve,reject)=>opt.signal.addEventListener('abort',()=>reject(new w.DOMException('Aborted','AbortError')),{once:true}))});
  const bodyTimeout=w.__yycjLoadHome();await wait();deadline();await bodyTimeout;assert($('#hubMapStatus [data-map-retry]'),'body-read deadline must not become 200-empty');assert(!$('#hubMapStatus').textContent.includes('暂无'));
  for(const broken of [async()=>{throw new SyntaxError('truncated JSON')},async()=>({unexpected:'proxy JSON'})]){w.fetch=async()=>({ok:true,json:broken});await w.__yycjLoadHome();assert($('#hubMapStatus [data-map-retry]'),'invalid map body must be retryable, not empty');assert(!$('#hubMapStatus').textContent.includes('暂无'));}
  w.fetch=async()=>({ok:true,json:async()=>data});await w.__yycjLoadHome();await wait();await wait();assert(!$('#hubMapStatus').textContent.includes('失败'));
  w.setTimeout=realTimer;
  assert(!fs.readFileSync('miniapp/map-results.js','utf8').includes('geolocation'));
  console.log('Map home DOM: scoped request lifecycle, explicit-only area search, stale success/error/city suppression; headers/body deadlines/offline/invalid payload/recovery/duplicate retry PASS');
 }finally{dom.window.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
