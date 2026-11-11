// Pure DOM contract test, no browser, real account, network, or Telegram sends.
const {JSDOM}=require('jsdom');
const fs=require('node:fs');
const assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const fixtures=JSON.parse(execFileSync(process.env.PYTHON || 'python3',['-c','import json;from bot.services.home_feed import DEFAULT_OPS,CITY_KEYS; print(json.dumps({"settings":DEFAULT_OPS,"keys":CITY_KEYS}))'],{encoding:'utf8'}));
const wait=ms=>new Promise(r=>setTimeout(r,ms));
const response=data=>({ok:true,status:200,json:async()=>JSON.parse(JSON.stringify(data)),clone(){return response(data);}});
async function admin() {
 const dom=new JSDOM(fs.readFileSync('miniapp/desk-legacy.html','utf8'),{url:'https://test.invalid/desk.html',runScripts:'outside-only',pretendToBeVisual:true});
 const w=dom.window,d=w.document, posts=[], alerts=[];
 w.localStorage.setItem('yycj_token','test');d.querySelector('#console').classList.remove('hidden');w.alert=x=>alerts.push(x);
 let settings={...fixtures.settings,enabled_cities:['深圳','广州'],bot_welcome_buttons:[{type:'webapp',label:'进入小程序',target:'home'}]};
 let overrides={};
 w.fetch=async(path,opt={})=>{
   const u=new URL(typeof path==='string'?path:path.url,'https://test.invalid'); const p=u.pathname;
   if(opt.method==='POST'){const b=JSON.parse(opt.body);if(p==='/api/admin/buttons/preview'){const items=b.items||b.config?.items||[];const rows=[];for(let i=0;i<items.length;i++){const x=items[i];if(x.enabled===false)continue;if(!rows.length || (x.new_row??i%2===0)||rows.at(-1).length>=4)rows.push([]);rows.at(-1).push({label:x.label,type:x.type,url:'https://example.invalid/'});}return response({rows});}posts.push([p,b]);if(p==='/api/admin/cities/settings') {overrides={...overrides,...b.overrides};return response(cfg());} if(p==='/api/admin/cities/announcement')return response({city_announcements:{'深圳':b}}); if(p==='/api/admin/settings/extra')settings={...settings,...b}; if(p==='/api/admin/operators/cities')return response({operator_cities:b.operator_cities});return response({settings,global:settings});}
   if(p==='/api/admin/cities/summary')return response({cities:[{city:'深圳',pending:0},{city:'广州',pending:0}],total:{},is_super:true,scope:null,city_announcements:{},city_override_counts:{}});
   if(p==='/api/admin/cities/settings')return response(cfg());
   if(p==='/api/admin/homepage')return response({settings});
   if(p==='/api/admin/operators')return response({extra_admin_ids:[600],operator_cities:{'600':['深圳']}});
   if(p==='/api/admin/channel-features')return response({global:settings,cities:[]});
   if(p==='/api/admin/label-sync')return response({status:'idle'});
   return response({items:[],settings});
 };
 function cfg(){return {can_edit:true,can_edit_super:true,overrides,keys:fixtures.keys.map(k=>({...k,global:settings[k.key],override:overrides[k.key]??null}))};}
 // Same relevant execution order as the authenticated legacy fallback, without its login / Telegram glue.
 for(const f of ['admin-cta.js','admin-kit.js','admin-proposal.js','admin-buttons.js','admin-city.js','admin-ops-users.js']) w.eval(fs.readFileSync('miniapp/'+f,'utf8'));
 await wait(1000);
 assert.equal(d.querySelectorAll('#ccGlobalFields > .ship-group').length,6);
 for(const [id,group] of [['opsWelcome',3],['opsChats',5],['opsTeachViews',4],['opsChannel',0],['opsLinkDetail',1],['opsShowCta',2]]) {
  // opsShowCta is normally lazy-loaded by admin-boot.

  assert(d.querySelector('#shipG'+group).contains(d.getElementById(id)),id+' wrong group');
 }
 assert(d.querySelector('#shipG5').contains(d.querySelector('#shipOperators')));
 assert(d.querySelector('#shipG0').contains(d.querySelector('#cfDirCh')));
 assert(d.querySelector('#shipWelcomePreview').textContent.includes('进入小程序'));
 d.querySelector('[data-wadd]').click();assert.equal(d.querySelectorAll('[data-wrow]').length,2);
 d.querySelector('[data-wremove="1"]').click();
 assert.equal(d.querySelector('#ship_popup_announcement_text').tagName,'TEXTAREA');
 d.querySelector('#ship_popup_announcement_enabled').checked=true;
 d.querySelector('#ship_popup_announcement_text').value='独立弹窗内容';
 d.querySelector('#ship_community_discussion_url').value='https://t.me/city_test';
 d.querySelector('[data-cc="save-global"]').click();await wait(80);
 const extra=posts.find(x=>x[0]==='/api/admin/settings/extra')[1];
 assert.equal(extra.community_discussion_url,'https://t.me/city_test');assert.equal(extra.bot_welcome_buttons.length,1);assert.equal(extra.approve_attach_detail,true);assert.equal(extra.popup_announcement_text,'独立弹窗内容');assert.equal(extra.popup_announcement_enabled,true);
 assert(posts.some(x=>x[0]==='/api/admin/channel-features'));
 assert(posts.some(x=>x[0]==='/api/admin/operators/cities'));
 d.querySelector('#ccTabs [data-city="深圳"]').click();await wait(150);
 assert.equal(d.querySelectorAll('#ccDyn > .ship-group').length,6);
 d.querySelector('#ccf_popup_announcement_enabled').value='0';
 d.querySelector('#ccf_popup_announcement_text').value='深圳专属弹窗';
 assert(!d.querySelector('#ccDyn [data-cc="save-ann"]'));
 d.querySelector('#ccAnnText').value='城市公告';d.querySelector('#ccf_community_discussion_url').value='https://t.me/shenzhen_test';
 d.querySelector('#ccf_link_label_detail').value='';d.querySelector('#ccf_link_label_detail').dispatchEvent(new w.Event('input',{bubbles:true}));d.querySelector('[data-cc="save-cfg"]').click();await wait(100);
 assert(posts.some(x=>x[0]==='/api/admin/cities/announcement'&&x[1].text==='城市公告'));
 const cityPost=posts.findLast(x=>x[0]==='/api/admin/cities/settings')[1];assert.equal(cityPost.overrides.community_discussion_url,'https://t.me/shenzhen_test');assert.equal(cityPost.overrides.link_label_detail,'');assert.equal(cityPost.overrides.popup_announcement_enabled,false);assert.equal(cityPost.overrides.popup_announcement_text,'深圳专属弹窗');assert(!('link_label_reviews' in cityPost.overrides));assert(!('link_label_write' in cityPost.overrides));
 assert(alerts.some(x=>x.includes('深圳 设置已保存')));

}
async function guest() {
 const dom=new JSDOM(fs.readFileSync('miniapp/go.html','utf8'),{url:'https://test.invalid/go.html',runScripts:'outside-only',pretendToBeVisual:true});const w=dom.window,d=w.document;w.localStorage.setItem('yycj_token','test');w.localStorage.setItem('yycj_city','深圳');
 w.fetch=async path=>response(String(path).includes('ranking')?{items:[],rule:'测试规则',city:'深圳'}:{city:'深圳',links:[{key:'discussion',label:'官方讨论群',url:'https://t.me/city_test'}]});
 w.eval(fs.readFileSync('miniapp/map-results.js','utf8'));w.eval(fs.readFileSync('miniapp/community-ui.js','utf8'));
 assert(d.querySelector('.topbar > #hubHomeEntry'));assert.equal(d.querySelectorAll('#hubHomeEntry').length,1);assert(!d.querySelector('#topAdmin'));assert.equal(d.querySelector('#btnSearch').nextElementSibling.id,'hubMapToggle');assert(d.querySelector('#pins').compareDocumentPosition(d.querySelector('.search-row')) & 4);assert(d.querySelector('#hubMapToggle'));let loads=0;w.__yycjLoadHome=()=>loads++;
 d.querySelector('#hubMapToggle').click();assert(w.__yycjMapMode);assert.equal(loads,1);
 w.dispatchEvent(new w.CustomEvent('yycj:home-data',{detail:{items:[{lamp_id:'x',title:'测试',district:'南山'}],map_groups:[]}}));
 assert(d.querySelector('#feed').classList.contains('hidden'));assert(d.querySelector('#hubMapUnlocated').textContent.includes('测试'));
 d.querySelector('#hubMapToggle').click();assert(!d.querySelector('#feed').classList.contains('hidden'));
 d.querySelector('#hubHomeEntry').click();await wait(30);assert(d.querySelector('#hubBody').textContent.includes('官方讨论群'));assert(!d.querySelector('#hubBody').textContent.includes('防失联导航'));
 d.querySelector('[data-hub="ranking"]').click();await wait(30);assert(d.querySelector('#hubBody').textContent.includes('不编造榜单'));

}
async function detail() {
 const dom=new JSDOM(fs.readFileSync('miniapp/go.html','utf8'),{url:'https://test.invalid/go.html',runScripts:'outside-only',pretendToBeVisual:true});const w=dom.window,d=w.document;
 w.localStorage.setItem('yycj_token','test');w.fetch=async()=>response({show_chat_cta:true,chat_cta_label:'想聊聊'});
 let mutations=0;const Native=w.MutationObserver;w.MutationObserver=class extends Native{constructor(cb){super((m,o)=>{if(++mutations>50){o.disconnect();return;}cb(m,o);});}};
 w.eval(fs.readFileSync('miniapp/detail-kit.js','utf8'));
 const lamp={lamp_id:'rest_test',city:'深圳',title:'休息资料',open_code:'rest',status:'active',extras:{}};
 w.__yycjRenderDetail({lamp,reviews:[{stars:5,text:'真实评价',audit_verified:true,proof_count:0}],reputation:{review_count:1},community_links:[]},lamp.lamp_id);
 await wait(40);
 assert(mutations<50,'detail mutation observer did not settle');assert(d.querySelector('#detailChat').disabled);assert(d.querySelector('#detailChat').textContent.includes('暂不联系'));
 assert(d.querySelector('#detailTrust').textContent.includes('1 条评价'));assert(d.querySelector('#yycjReviews').textContent.includes('已审核'));assert(!d.querySelector('#yycjReviews').textContent.includes('凭证'));
}
async function popup() {
 const source=fs.readFileSync('miniapp/entry-announcement.js','utf8');
 const data={title:'公告 <b>不是HTML</b>',text:'<img src=x onerror=alert(1)>\n纯文字公告',scope_user:'900001',scope_city:'深圳'};
 function setup(storage={},at='2026-11-10T15:59:00Z',hidden=false) {
  const dom=new JSDOM('<div id="app" class="'+(hidden?'hidden':'')+'"><button id="origin">入口</button></div>',{url:'https://test.invalid',runScripts:'outside-only',pretendToBeVisual:true});
  const w=dom.window;const NativeDate=w.Date;w.Date=class extends NativeDate{constructor(...a){super(...(a.length?a:[at]));}static now(){return +new NativeDate(at);}};
  for(const [k,v] of Object.entries(storage))w.localStorage.setItem(k,v);w.eval(source);
  return {w,d:w.document,emit:p=>w.dispatchEvent(new w.CustomEvent('yycj:entry-announcement',{detail:p}))};
 }
 let t=setup();t.emit(null);assert(!t.d.querySelector('#entryAnnouncement'));t.emit({...data,text:' '});assert(!t.d.querySelector('#entryAnnouncement'));
 t.d.querySelector('#origin').focus();t.emit(data);assert(t.d.querySelector('[role="dialog"]'));assert(!t.d.querySelector('#entryAnnouncement img'));assert(t.d.querySelector('#entryAnnouncementText').textContent.includes('<img'));
 t.d.querySelector('[data-popup-close]').click();assert(!t.d.querySelector('#entryAnnouncement'));assert.equal(t.d.activeElement.id,'origin');t.emit(data);assert(!t.d.querySelector('#entryAnnouncement'),'internal navigation must not reopen');
 t=setup();t.emit(data);assert(t.d.querySelector('#entryAnnouncement'),'new opening after close must show');t.d.querySelector('[data-popup-today]').click();const saved={...t.w.localStorage};
 t=setup(saved);t.emit(data);assert(!t.d.querySelector('#entryAnnouncement'),'same Shanghai calendar day suppressed');t.emit({...data,scope_city:'广州'});assert(t.d.querySelector('#entryAnnouncement'),'city override has separate daily scope');
 t=setup(saved,'2026-11-10T16:01:00Z');t.emit(data);assert(t.d.querySelector('#entryAnnouncement'),'Shanghai midnight resets today suppression');
 t=setup(saved);t.emit({...data,scope_user:'900002'});assert(t.d.querySelector('#entryAnnouncement'),'different viewer never shares suppression');
 t=setup({},undefined,true);t.emit(data);assert(!t.d.querySelector('#entryAnnouncement'));t.d.querySelector('#app').classList.remove('hidden');await wait(10);assert(t.d.querySelector('#entryAnnouncement'));t.d.querySelector('[role="dialog"]').dispatchEvent(new t.w.KeyboardEvent('keydown',{key:'Escape',bubbles:true}));assert(!t.d.querySelector('#entryAnnouncement'));
}
(async()=>{await admin();await guest();await detail();await popup();console.log('DOM contracts passed: admin six groups, scoped saves, welcome editor, city hub/map/ranking');process.exit(0);})().catch(e=>{console.error(e);process.exit(1);});
