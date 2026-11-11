/* Actual default HTML + actual operational JS. JSDOM assertions, NOT browser/pixel screenshots. */
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),cp=require('node:child_process');
const {JSDOM,VirtualConsole}=require('jsdom');
const {createFixture}=require('./workspace_fixture.cjs');
const ROOT=path.resolve(__dirname,'..');
const metadata=JSON.parse(cp.execFileSync(process.env.PYTHON||'python3',['tests/workspace_fixture.py'],{cwd:ROOT,env:{...process.env,PYTHONPATH:ROOT},encoding:'utf8'}));
const wait=ms=>new Promise(r=>setTimeout(r,ms));
async function until(fn,msg){for(let i=0;i<100;i++){if(fn())return;await wait(25)}throw Error('timeout: '+msg)}
async function create(options={}){
 const fixture=createFixture(metadata,options),errors=[];
 const html=fs.readFileSync(path.join(ROOT,'miniapp/desk.html'),'utf8');
 const dom=new JSDOM(html,{url:'https://offline.invalid/app/desk.html',runScripts:'outside-only',pretendToBeVisual:true,virtualConsole:new VirtualConsole().on('jsdomError',e=>{if(!e.message.includes('navigation'))errors.push(e.message)})});
 const w=dom.window;w.scrollTo=()=>{};w.HTMLElement.prototype.scrollIntoView=function(){};w.confirm=()=>true;w.prompt=()=>null;w.alert=()=>{};w.Response=Response;w.Request=Request;w.URL.createObjectURL=()=>'';w.Telegram={WebApp:{initData:'OFFLINE_FIXTURE',ready(){},expand(){},setHeaderColor(){},initDataUnsafe:{user:{id:1}}}};
 w.fetch=async(input,opt)=>{const result=await fixture.handle(input,opt);return new Response(JSON.stringify(result.data),{status:result.status,headers:{'Content-Type':'application/json'}})};
 const names=[...w.document.querySelectorAll('script[src]')].map(s=>s.getAttribute('src').split('?')[0].replace('./','')).filter(n=>n!=='telegram-web-app.js'&&!n.startsWith('https:'));
 for(const name of names)w.eval(fs.readFileSync(path.join(ROOT,'miniapp',name),'utf8')+'\n//# sourceURL='+name);
 // admin-boot normally inserts these two local scripts, which outside-only JSDOM does not fetch.
 for(const name of ['admin-chats.js','admin-cta.js'])w.eval(fs.readFileSync(path.join(ROOT,'miniapp',name),'utf8'));
 await until(()=>w.yycjWorkspace?.ready&&w.document.querySelector('#workspaceView h1'),'authenticated workbench');
 return {w,dom,fixture,errors,$:s=>w.document.querySelector(s),close:()=>dom.window.close(),async nav(page,tab){w.document.querySelector(`[data-work="${page}"]`).click();await until(()=>w.yycjWorkspace.page===page&&w.document.querySelector('#workspaceNative'),'page');if(tab){await until(()=>w.document.querySelector(`[data-wtab="${tab}"]`),'tab');w.document.querySelector(`[data-wtab="${tab}"]`).click()}await wait(100)},async city(value){const s=w.document.querySelector('#workspaceScope');s.value=value;s.dispatchEvent(new w.Event('change',{bubbles:true}));await wait(150)},input(sel,value){const e=w.document.querySelector(sel);assert(e,sel);if(e.type==='checkbox')e.checked=value;else e.value=value;e.dispatchEvent(new w.Event('input',{bubbles:true}));return e}};
}
(async()=>{
 let cases=0;
 {const a=await create();try{
  assert.equal(a.$('#workspaceParking').contains(a.$('#workspaceView')),false);
  assert.equal(a.w.document.querySelectorAll('#workspaceScope').length,1);
  assert.equal(a.w.document.querySelectorAll('[data-work]').length,6);
  assert.equal(a.$('#workspaceView h1').textContent,'运营总览');
  assert(a.$('#workspaceView').textContent.includes('真实')||a.$('#workspaceView').textContent.includes('按流程'));
  assert(a.fixture.state.calls.filter(x=>x.method==='POST').every(x=>x.path==='/api/auth'));
  await a.city('深圳');await a.nav('review','listings');await until(()=>a.$('[data-fill-proxy]'),'real proxy action');
  for(const act of ['renew','unlist','delete','relist'])assert(a.$(`[data-list="${act}"]`));
  assert(a.$('#workspaceLegacy').contains(a.$('#pane-listings')));
  assert(a.$('#workspaceProxyDetails')&&a.$('#workspaceEditDetails'));
  assert(a.$('#wsListingsToolbar')&&a.$('#wsListingsToolbar').contains(a.$('#wsListingStatus'))&&a.$('#wsListingsToolbar').contains(a.$('#listingQ'))&&a.$('#wsListingsToolbar').contains(a.$('#btnListingSearch')),'listings status and search share one toolbar');
  assert(a.$('.ws-editor-row')&&a.$('.ws-editor-row').contains(a.$('#workspaceProxyDetails'))&&a.$('.ws-editor-row').contains(a.$('#workspaceEditDetails')));
  assert(a.fixture.state.calls.filter(x=>x.path==='/api/admin/listings').at(-1).query.city==='深圳');
  a.$('[data-fill-proxy]').click();await until(()=>a.$('#editLampId').value,'fill existing editor');assert(a.$('#workspaceEditDetails').open);assert.equal(a.$('#editTitle').value,'示例资料 A');
  const n=a.fixture.state.calls.filter(x=>x.method==='POST').length;a.w.prompt=()=>null;a.$('[data-list="delete"]').click();await wait(30);assert.equal(a.fixture.state.calls.filter(x=>x.method==='POST').length,n);
  a.w.confirm=()=>false;a.$('[data-list="unlist"]').click();await wait(30);assert.equal(a.fixture.state.calls.filter(x=>x.method==='POST').length,n);
  a.w.confirm=()=>true;await a.nav('review','media');await until(()=>a.$('[data-wpost="post-media"]'),'media comparison');a.$('[data-wpost="post-media"]').click();await until(()=>a.$('#workspaceOverlay')?.textContent.includes('整组替换'),'actual comparison drawer');assert(a.$('#workspaceOverlay').textContent.includes('当前最新文字'));assert(a.$('#workspaceOverlay').textContent.includes('不延长期限'));a.$('[data-wclose]').click();
  await a.nav('review','reviews');assert(a.$('#workspaceLegacy').contains(a.$('#adminReviews')));
  await a.nav('review','coupons');await until(()=>a.$('#cpnAdmin'),'coupon module');assert(!a.$('#cpnAdmin').classList.contains('ws-legacy-hide'));
  for(const tab of ['pin','carousel']){await a.nav('review',tab);assert(a.$('#workspaceLegacy').contains(a.$('#pane-'+tab)))}
  for(const tab of ['users','more','stats']){await a.nav('system',tab);assert(a.$('#workspaceLegacy').contains(a.$('#pane-'+tab)))}
  assert.equal(a.errors.length,0,a.errors.join('\n'));cases++;
 }finally{a.close()}}
 {const a=await create();try{
  await a.city('深圳');await a.nav('settings','popup');await until(()=>a.$('#ws_popup_announcement_title'),'popup config');
  assert.equal(a.w.document.querySelectorAll('#workspaceSave').length,1);
  const inherit=a.$('[data-winherit="popup_announcement_title"]');assert(inherit.checked);inherit.checked=false;inherit.dispatchEvent(new a.w.Event('change',{bubbles:true}));a.input('#ws_popup_announcement_title','只改标题');
  a.w.confirm=()=>false;a.$('[data-work="logs"]').click();await wait(80);assert.equal(a.w.yycjWorkspace.page,'settings');assert.equal(a.$('#ws_popup_announcement_title').value,'只改标题');
  a.w.confirm=()=>true;a.$('#workspaceSave').click();await until(()=>!a.w.yycjWorkspace.saving,'save completes');await wait(60);
  const save=a.fixture.state.calls.filter(x=>x.path==='/api/admin/workspace/config'&&x.method==='POST').at(-1);assert.deepEqual(save.body.changes,{popup_announcement_title:'只改标题'});assert.equal(save.body.city,'深圳');
  await a.nav('settings','channel');await until(()=>a.$('#ws_link_label_reviews'),'label field');const inh=a.$('[data-winherit="link_label_reviews"]');inh.checked=false;inh.dispatchEvent(new a.w.Event('change',{bubbles:true}));a.input('#ws_link_label_reviews','');a.$('#workspaceSave').click();await wait(160);assert.equal(a.fixture.state.calls.filter(x=>x.path==='/api/admin/workspace/config'&&x.method==='POST').at(-1).body.changes.link_label_reviews,'');
  await a.nav('messages','approval');await until(()=>a.$('[data-bmode]'),'shared buttons');a.$('[data-bmode]').value='custom';a.$('[data-bmode]').dispatchEvent(new a.w.Event('change',{bubbles:true}));a.$('[data-badd]').click();a.$('[data-badd]').click();const row=a.$('[data-brow="0"] [data-bkey="label"]');row.value='按钮 A';row.dispatchEvent(new a.w.Event('input',{bubbles:true}));a.$('[data-bdown="0"]').click();assert.equal(a.$('#wsButtons_approval_buttons')._buttonEditor.value().items[1].label,'按钮 A');await wait(300);assert(a.$('#wsKeyboardPreview').textContent.includes('按钮 A'));assert(a.fixture.state.calls.some(x=>x.path==='/api/admin/buttons/preview'));
  a.w.confirm=()=>true;await a.nav('messages','welcome');await until(()=>a.$('[data-wadd]'),'site welcome');assert.equal(a.w.yycjWorkspace.mode,'global');assert(a.$('#ws_bot_welcome_enabled'));await a.city('深圳');assert.equal(a.w.yycjWorkspace.tab,'approval');
  cases++;
 }finally{a.close()}}
 {const a=await create({super:false,scope:['深圳']});try{
  assert.equal(a.w.yycjWorkspace.city,'深圳');assert.equal(a.$('#workspaceRole').textContent,'城市运营');assert(!a.$('#workspaceScope option[value="__global"]'));
  await a.nav('settings','channel');await until(()=>a.$('#workspaceNative').textContent.includes('受保护字段'),'protected fields');assert(!a.$('#ws_broadcast_channel'));assert(a.$('#workspaceNative').textContent.includes('受保护字段'));
  await a.nav('messages');assert(a.$('[data-wtab="welcome"]').disabled);
  await a.nav('system','permissions');assert(!a.$('#wsOperatorIds'));assert(!a.$('#workspaceSave'));assert(a.$('#workspaceNative').textContent.includes('仅超级管理员'));
  await a.nav('logs');await until(()=>a.$('[data-wlog="log-unknown"]'),'scope logs');assert(!a.$('[data-wlog="log-skipped"]'));a.$('[data-wlog="log-unknown"]').click();await until(()=>a.$('#workspaceOverlay')?.textContent.includes('禁止盲目重发'),'unknown detail');assert(!a.$('[data-wretry]'));
  cases++;
 }finally{a.close()}}
 {const a=await create();try{
  await a.nav('logs');await until(()=>a.$('#wsLogQuery'),'log filters');a.$('#wsLogSearchBy').value='operation_id';a.$('#wsLogOp').value='33333333-3333-4333-8333-333333333333';a.$('#wsLogQuery').click();await wait(80);assert.equal(a.fixture.state.calls.filter(x=>x.path==='/api/admin/executions').at(-1).query.operation_id,'33333333-3333-4333-8333-333333333333');
  a.$('[data-wlog="log-notification"]').click();await until(()=>a.$('[data-wretry]'),'safe step retry');a.$('[data-wretry]').click();await wait(120);assert.equal(a.fixture.state.calls.filter(x=>x.method==='POST'&&x.path.endsWith('/retry')).length,1);assert(!a.$('[data-wretry]'));
  assert(!a.fixture.state.calls.some(x=>x.path.includes('/renew')||x.path.includes('/proxy-edit')));
  const css=fs.readFileSync(path.join(ROOT,'miniapp/workspace.css'),'utf8');assert(css.includes('@media(max-width:760px)'));assert(css.includes('grid-template-columns:1fr'));assert(css.includes('padding-bottom:'));assert(css.includes('min-height:44px'));assert(css.includes('position:fixed'));
  cases++;
 }finally{a.close()}}
 {const a=await create();try{
  await a.city('深圳');await a.nav('review','listings');await until(()=>a.$('[data-fill-proxy]'),'list initially loaded');
  const original=a.fixture.handle;let release,started=false;
  a.fixture.handle=async(input,opt)=>{const result=original(input,opt),u=new URL(String(input),'https://offline.invalid');if(u.pathname==='/api/admin/listings'&&u.searchParams.get('city')==='深圳'){started=true;await new Promise(r=>release=r)}return result};
  const previous=a.w.yycjAdminListings.reload();await until(()=>started,'old city request held');await a.city('广州');release();await previous;await wait(80);assert(a.$('#adminListings').textContent.includes('示例资料 B'));assert(!a.$('#adminListings').textContent.includes('示例资料 A'));assert(a.w.__yycjListings.every(x=>x.city==='广州'));
  a.fixture.handle=original;await a.nav('settings','popup');await until(()=>a.$('#ws_popup_announcement_title'),'editable popup');const inherit=a.$('[data-winherit="popup_announcement_title"]');inherit.checked=false;inherit.dispatchEvent(new a.w.Event('change',{bubbles:true}));a.input('#ws_popup_announcement_title','失败后草稿保留');a.fixture.state.failOnce['POST /api/admin/workspace/config']=true;a.$('#workspaceSave').click();await wait(100);assert.equal(a.$('#ws_popup_announcement_title').value,'失败后草稿保留');assert(!a.$('#ws_popup_announcement_title').disabled);assert(a.w.yycjWorkspace.dirty);
  a.fixture.handle=async(input,opt)=>{const result=original(input,opt);if(String(input)==='/api/admin/workspace/config'&&opt?.method==='POST')await new Promise(r=>release=r);return result};
  a.$('#workspaceSave').click();await until(()=>a.w.yycjWorkspace.saving,'save busy');assert(a.$('#ws_popup_announcement_title').disabled);const select=a.$('#workspaceScope');select.value='深圳';select.dispatchEvent(new a.w.Event('change',{bubbles:true}));assert.equal(a.w.yycjWorkspace.city,'广州');a.$('[data-work="logs"]').click();assert.equal(a.w.yycjWorkspace.page,'settings');release();await until(()=>!a.w.yycjWorkspace.saving,'save released');assert(!a.w.yycjWorkspace.dirty);
  cases++;
 }finally{a.close()}}
 {const a=await create();try{
  await a.city('深圳');await a.nav('messages','approval');await until(()=>a.$('[data-bmode]'),'compact message editor');
  assert(!a.$('.pagehead .breadcrumb'),'do not repeat page title in breadcrumb');
  assert.deepEqual([...a.w.document.querySelectorAll('.ws-config-group>h2')].map(x=>x.textContent),['通知内容','消息按钮']);
  assert.equal(a.w.document.querySelectorAll('#workspaceScope').length,1);assert.equal(a.w.document.querySelectorAll('#workspaceSave').length,1);
  a.$('[data-bmode]').value='custom';a.$('[data-bmode]').dispatchEvent(new a.w.Event('change',{bubbles:true}));a.$('[data-badd]').click();
  assert(a.$('.ws-button-card .ws-button-head'));assert(a.$('.ws-button-card .ws-button-fields [data-bkey="target"]'));assert(a.$('[aria-label="上移按钮1"]'));
  const editor=a.$('#wsButtons_approval_buttons')._buttonEditor,previous=JSON.stringify(editor.value()),calls=a.fixture.state.calls.filter(x=>x.method==='POST').length;
  a.$('.ws-preview-panel').open=false;await wait(40);assert.equal(JSON.stringify(editor.value()),previous);assert.equal(a.fixture.state.calls.filter(x=>x.method==='POST').length,calls);
  a.w.confirm=()=>false;a.$('[data-work="settings"]').click();await wait(40);assert.equal(a.w.yycjWorkspace.page,'messages');
  a.$('#btnMenu').click();assert.equal(a.$('#btnMenu').getAttribute('aria-expanded'),'true');assert(a.$('#sideMask').classList.contains('open'));a.w.document.dispatchEvent(new a.w.KeyboardEvent('keydown',{key:'Escape'}));assert.equal(a.$('#btnMenu').getAttribute('aria-expanded'),'false');assert(!a.$('#sideMask').classList.contains('open'));
  assert.equal(a.errors.length,0,a.errors.join('\n'));cases++;
 }finally{a.close()}}
 {const a=await create();try{
  await a.city('深圳');await a.nav('settings','community');await until(()=>a.$('#ws_community_enabled'),'grouped community settings');
  assert.deepEqual([...a.w.document.querySelectorAll('.ws-config-group>h2')].map(x=>x.textContent),['社区入口','位置入口','首页联系入口']);
  for(const f of a.w.yycjWorkspace.config.fields.filter(x=>x.section==='community'))assert(a.$('#ws_'+f.key),'field preserved: '+f.key);
  assert(a.$('.ws-type-bool>.toggle'));assert(a.$('.ws-effective:not([open])'));assert.equal(a.w.yycjWorkspace.changed.size,0);
  await a.city('__global');await a.nav('settings','channel');await until(()=>a.$('#ws_listing_fields'),'advanced field stays mounted');
  const advanced=a.$('.ws-advanced');assert(advanced&&!advanced.open);assert(advanced.contains(a.$('#ws_listing_fields')));
  for(const f of a.w.yycjWorkspace.config.fields.filter(x=>x.section==='channel'))assert(a.$('#ws_'+f.key),'channel field preserved: '+f.key);
  advanced.open=true;a.input('#ws_listing_fields','[]');a.$('#workspaceSave').click();await wait(140);
  const saved=a.fixture.state.calls.filter(x=>x.method==='POST'&&x.path==='/api/admin/workspace/config').at(-1);assert.deepEqual(saved.body.changes,{listing_fields:[]});assert.equal(saved.body.city,null);
  assert(!a.fixture.state.calls.some(x=>x.path.endsWith('/retry')||x.path.includes('/renew')));cases++;
 }finally{a.close()}}
 {const a=await create();try{
  await a.city('深圳');await a.nav('logs');await until(()=>a.$('#wsLogQuery'),'compact log filter');
  assert(a.$('.ws-log-primary #wsLogFeature'));assert(a.$('.ws-log-id #wsLogSearchBy'));assert(!a.$('.ws-log-advanced').open);
  a.$('.ws-log-advanced').open=true;a.input('#wsLogSince','2026-11-10T12:00');a.input('#wsLogUntil','2026-11-11T12:00');a.$('#wsLogQuery').click();await wait(80);
  assert(a.$('#wsLogTimeSummary').textContent.includes('2026-11-10 12:00'));const query=a.fixture.state.calls.filter(x=>x.path==='/api/admin/executions').at(-1).query;assert.equal(query.since,'2026-11-10T04:00:00.000Z');assert.equal(query.until,'2026-11-11T04:00:00.000Z');
  a.$('[data-wlog="log-unknown"]').click();await until(()=>a.$('.ws-detail-meta'),'structured log detail');assert(a.$('.ws-detail-meta').textContent.includes('coupon-example-op'));assert(a.$('.ws-stage-head'));assert(!a.$('[data-wretry]'));
  const css=fs.readFileSync(path.join(ROOT,'miniapp/workspace.css'),'utf8');assert(css.includes('.workspace-body .ws-config-groups{display:grid;grid-template-columns:repeat(2,minmax(0,1fr))'));assert(css.includes('.workspace-body .ws-button-fields{grid-template-columns:1fr'));assert(css.includes('.workspace-body #sideMask.open'));assert(css.includes('calc(112px + env(safe-area-inset-bottom))'));assert(css.includes('font-size:16px;min-height:44px'));
  assert.equal(a.w.yycjWorkspace.changed.size,0);assert(a.fixture.state.calls.filter(x=>x.method==='POST').every(x=>x.path==='/api/auth'));cases++;
 }finally{a.close()}}
 {
  const os=require('node:os');const dir=fs.mkdtempSync(path.join(os.tmpdir(),'yycj-workspace-export-'));
  cp.execFileSync(process.env.PYTHON||'python3',['scripts/export_workspace_review.py','--output',path.join(dir,'review')],{cwd:ROOT,env:{...process.env,PYTHONPATH:ROOT}});
  const markup=fs.readFileSync(path.join(dir,'review/index.html'),'utf8'),errors=[];
  const dom=new JSDOM(markup,{url:'file://'+path.join(dir,'review/index.html'),runScripts:'dangerously',pretendToBeVisual:true,virtualConsole:new VirtualConsole().on('jsdomError',e=>errors.push(e.message)),beforeParse(w){w.Response=Response;w.Request=Request;w.scrollTo=()=>{};w.HTMLElement.prototype.scrollIntoView=()=>{};w.confirm=()=>false;w.alert=()=>{};}});
  try{await until(()=>dom.window.yycjWorkspace?.ready&&dom.window.document.querySelector('[data-work="messages"]'),'actual standalone export');assert.equal(dom.window.document.querySelectorAll('script[src]').length,0);assert.equal(dom.window.document.querySelectorAll('link[rel="stylesheet"]').length,0);assert(dom.window.document.querySelector('#offlineReviewBanner').textContent.includes('不是生产截图'));assert.equal(errors.length,0,errors.join('\n'));cases++;}finally{dom.window.close()}
 }
 console.log(`workspace DOM: ${cases} integrated scenarios passed (real HTML/scripts; no pixel/browser inspection)`);
})().catch(e=>{console.error(e);process.exitCode=1});
