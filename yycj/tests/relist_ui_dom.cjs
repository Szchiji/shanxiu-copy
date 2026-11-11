/* Actual teacher/admin relist controls; offline JSDOM, no browser/network. */
const assert=require('node:assert/strict'),fs=require('node:fs');const {JSDOM}=require('jsdom');
const wait=ms=>new Promise(r=>setTimeout(r,ms));async function until(fn){for(let i=0;i<100;i++){if(fn())return;await wait(25)}throw Error('relist DOM timeout')}
const source=n=>fs.readFileSync('miniapp/'+n,'utf8');
function page(html){const dom=new JSDOM(html,{url:'https://offline.invalid',runScripts:'outside-only',pretendToBeVisual:true});const w=dom.window;w.alert=()=>{};w.scrollTo=()=>{};w.localStorage.setItem('yycj_token','isolated');w.eval(source('edit-state-ui.js'));return {w,dom,$:s=>w.document.querySelector(s)}}
(async()=>{
 const state={operation_id:'fixture-operation-123456',restored:true,action:'relist',review_status:'none',media_status:'unchanged',channel_status:'failed',channel_retryable:true,notification_status:'pending',notification_retryable:true};
 {const a=page('<button data-nav="me">我的</button><main id="view-me"><div class="card"></div></main>');try{
  const calls=[],item={lamp_id:'fixture-lamp',title:'离线示例',status:'hidden',unlist_reason:'teacher',updated_at:'2026-11-11T10:00:00',expires_at:'2026-12-01',media:[]};a.w.localStorage.setItem('yycj_user',JSON.stringify({role:'teacher'}));
  a.w.fetch=async(p,opt={})=>{calls.push({p,opt});let data={items:[item]};if(p.endsWith('/relist')){const body=JSON.parse(opt.body);assert(body.operation_id&&body.expected_updated_at===item.updated_at);item.status='active';item.relist_state={...state,operation_id:body.operation_id};data={operation:item.relist_state,message:'状态已恢复；频道明确失败'};}else if(p.includes('/relist-operations/'))data={operation:{...state,channel_status:'synced',notification_status:'sent',channel_retryable:false,notification_retryable:false}};return new Response(JSON.stringify(data),{status:200})};
  a.w.eval(source('me-listings.js'));a.$('[data-nav]').click();await until(()=>a.$('[data-relist]'));a.$('[data-relist]').click();await until(()=>a.$('[data-relist-retry]'));assert(a.$('.edit-result').textContent.includes('重新上架需要处理'));assert(a.$('.edit-result').textContent.includes('频道同步明确失败'));
  const n=calls.length;a.$('[data-relist-retry]').click();await until(()=>!a.$('[data-relist-retry]'));const req=calls.slice(n).find(x=>x.p.includes('/relist-operations/'));assert.deepEqual(JSON.parse(req.opt.body),{step:'channel'});assert(!calls.some(x=>x.p.includes('/renew')));
  assert(!a.w.__yycjEditUI.html({...state,channel_status:'unknown',channel_retryable:false,notification_retryable:false}).includes('data-relist-retry='));
 }finally{a.dom.window.close()}}
 {const a=page('<button data-list="relist" data-id="fixture-lamp">重新上架</button><button id="btnRefresh"></button>');try{
  const bodies=[],messages=[];a.w.yycjToast=x=>messages.push(x);a.w.__yycjListings=[{lamp_id:'fixture-lamp',updated_at:'2026-11-11T10:00:00'}];a.w.fetch=async(p,opt)=>{bodies.push(JSON.parse(opt.body));return new Response(JSON.stringify(bodies.length===1?{detail:'isolated response lost'}:{message:'已恢复；频道结果待核对'}),{status:bodies.length===1?500:200})};a.w.eval(source('admin-list.js'));
  a.$('[data-list]').click();await until(()=>!a.$('[data-list]').disabled&&bodies.length===1);a.$('[data-list]').click();await until(()=>messages.includes('已恢复；频道结果待核对'));assert.equal(bodies[0].operation_id,bodies[1].operation_id);assert.equal(bodies[1].expected_updated_at,'2026-11-11T10:00:00');assert(messages.includes('已恢复；频道结果待核对'));
 }finally{a.dom.window.close()}}
 console.log('Relist UI DOM: teacher/admin versioned replay IDs, persisted step feedback, known-step retry and unknown no-retry PASS');
})().catch(e=>{console.error(e);process.exitCode=1});
