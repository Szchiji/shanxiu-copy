// Node/jsdom only: executes actual changed UI code, no browser/network or screenshots.
const {JSDOM}=require('jsdom'),fs=require('node:fs'),assert=require('node:assert/strict');
const wait=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{
 const dom=new JSDOM('<div id="host"></div>',{url:'https://test.invalid/',runScripts:'outside-only'}),w=dom.window,d=w.document;
 w.eval(fs.readFileSync('miniapp/edit-state-ui.js','utf8'));const ui=w.__yycjEditUI;
 const a=ui.request({title:'A'},'one'),b=ui.request({title:'A'},'one'),c=ui.request({title:'B'},'one');
 assert.equal(a.operation_id,b.operation_id);assert.notEqual(a.operation_id,c.operation_id);
 ui.clear('one');assert.notEqual(c.operation_id,ui.request({title:'B'},'one').operation_id);
 const op={operation_id:'test-edit-operation-0001',text_saved:true,review_status:'pending',media_status:'pending',action:'text',channel_status:'failed',channel_retryable:true,notification_status:'not_required',notification_retryable:false,album:'javascript:alert(1)'};
 d.querySelector('#host').innerHTML=ui.html(op,'admin');assert(d.body.textContent.includes('文字已保存'));
 assert(d.body.textContent.includes('新图片/视频待审'));assert(!d.querySelector('a'));assert.equal(d.querySelectorAll('[data-edit-retry]').length,1);
 const calls=[];w.fetch=async(path,options)=>{calls.push([path,JSON.parse(options.body)]);return{ok:true,json:async()=>({operation:{...op,channel_status:'synced',channel_retryable:false}})}};w.localStorage.setItem('yycj_token','test');
 d.querySelector('[data-edit-retry]').click();await wait(20);assert.deepEqual(calls,[['/api/admin/edit-operations/test-edit-operation-0001/retry',{step:'channel'}]]);assert.equal(d.querySelectorAll('[data-edit-retry]').length,0);
 assert(d.body.textContent.includes('原频道文字已同步'));
 for(const status of ['unknown','sending'])assert(!ui.html({...op,channel_status:status,channel_retryable:false}).includes('data-edit-retry='));
 const safe=ui.html({...op,channel_status:'<img src=x onerror=alert(1)>',channel_retryable:false});assert(safe.includes('&lt;img'));assert(!safe.includes('<img'));
 const nested=ui.html({...op,review_status:'none',outstanding:[{...op,channel_status:'unknown',channel_retryable:false}]});assert(nested.includes('其他待处理修改（1）') && nested.includes('修改待审核'));
  const quiet={operation_id:'quiet-1',text_saved:true,review_status:'none',media_status:'unchanged',action:'text',channel_status:'synced',channel_retryable:false,notification_status:'not_required',notification_retryable:false,album:'https://t.me/example/1'};
 assert.equal(ui.html(quiet,'admin',{card:true}),'');
 assert(ui.html(quiet,'admin').includes('文字已保存；有效期不变'));
 assert(ui.html(quiet,'admin').includes('查看本次频道帖'));
 const failCard=ui.html({...op,album:'https://t.me/example/2'},'admin',{card:true});
 assert(failCard.includes('edit-result-card')&&failCard.includes('频道同步明确失败')&&failCard.includes('data-edit-retry='));
 assert(!failCard.includes('查看本次频道帖'));
 assert(ui.actionable(op)&&!ui.actionable(quiet));
 console.log('edit UI DOM: stable request IDs, truthful statuses, scoped step-only retry, escaping/backlog PASS');w.close();
})().catch(e=>{console.error(e);process.exit(1)});
