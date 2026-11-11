// Node/jsdom only: history entry stays out of listing cards' main actions, overlay requires confirm.
const {JSDOM}=require('jsdom'),fs=require('node:fs'),assert=require('node:assert/strict'),path=require('node:path');
const wait=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{
  const root=path.resolve(__dirname,'..');
  const css=fs.readFileSync(path.join(root,'miniapp/edit-history-ui.css'),'utf8');
  assert.match(css,/min-height:\s*44px/);
  assert.doesNotMatch(css,/#feed|feed-card|117px|224px/);
  const me=fs.readFileSync(path.join(root,'miniapp/me-listings.js'),'utf8');
  assert.match(me,/data-history/);
  assert.match(me,/ls-more/);
  assert.doesNotMatch(me,/data-history="\$\{esc\(x\.lamp_id\)\}"[^>]*>修改记录<\/button><\/div>\$\{window\.__yycjEditUI/);
  const admin=fs.readFileSync(path.join(root,'miniapp/admin-ops.js'),'utf8');
  assert.match(admin,/data-history-scope="admin"/);
  assert.match(admin,/listing-more-menu/);

  const dom=new JSDOM('<div id="host"></div>',{url:'https://test.invalid/',runScripts:'outside-only'});
  const w=dom.window,d=w.document;
  w.localStorage.setItem('yycj_token','test');
  const calls=[];
  const history={
    notice:'仅展示本功能上线后可证明的版本',
    current:{title:'新文字',description:'新介绍',updated_at:'2026-11-11T12:00:00',media:[{type:'image',url:'new'}],hours:[],extras:{}},
    items:[{version_id:'ver-1',recorded_at:'2026-11-11T04:00:00Z',source:'baseline',source_label:'记录起点（当时的资料原文）',actor_label:'记录时的资料原文',changed_labels:[],changed_fields:[],restorable:true,can_restore_text:true,can_restore_media:false,media_available:false,current_changed_fields:['title'],current_changed_labels:['称呼']}]
  };
  const preview={
    notice:history.notice,
    current:history.current,
    version:{...history.items[0],snapshot:{title:'原文字',description:'原介绍',media:[{type:'image',url:'old'}],hours:[],extras:{}},can_restore_media:false,media_available:false,current_changed_fields:['title','description'],current_changed_labels:['称呼','介绍']}
  };
  w.fetch=async(url,opt={})=>{
    calls.push([url,opt.method||'GET',opt.body?JSON.parse(opt.body):null]);
    if(String(url).endsWith('/restore'))return{ok:true,json:async()=>({operation:{operation_id:'restored-1',text_saved:true,review_status:'none',media_status:'unchanged',action:'text',channel_status:'synced',channel_retryable:false,notification_status:'not_required'}})};
    if(String(url).includes('/edit-history/ver-1'))return{ok:true,json:async()=>preview};
    return{ok:true,json:async()=>history};
  };
  w.eval(fs.readFileSync(path.join(root,'miniapp/edit-state-ui.js'),'utf8'));
  w.eval(fs.readFileSync(path.join(root,'miniapp/edit-history-ui.js'),'utf8'));
  d.body.insertAdjacentHTML('beforeend','<button type="button" data-history="lamp-1" data-history-scope="me">修改记录</button>');
  d.querySelector('[data-history]').click();
  await wait(30);
  const overlay=d.getElementById('yycjHistory');
  assert(overlay && !overlay.classList.contains('hidden'));
  assert(overlay.textContent.includes('仅展示本功能上线后可证明的版本'));
  assert(overlay.textContent.includes('中国时区 UTC+8'));
  d.querySelector('[data-hist-ver]').click();
  await wait(30);
  assert(overlay.textContent.includes('预览后再决定是否恢复'));
  assert(overlay.textContent.includes('不会回滚上架状态'));
  const restore=d.querySelector('#histRestore');
  assert(restore.disabled);
  restore.click();
  await wait(20);
  assert.equal(calls.filter(x=>x[1]==='POST').length,0);
  d.querySelector('#histConfirm').checked=true;
  d.querySelector('#histConfirm').dispatchEvent(new w.Event('change',{bubbles:true}));
  assert.equal(restore.disabled,false);
  restore.click();
  await wait(30);
  const posted=calls.filter(x=>x[1]==='POST');
  assert.equal(posted.length,1);
  assert.equal(posted[0][2].confirm,true);
  assert.equal(posted[0][2].include_media,false);
  assert(posted[0][2].operation_id);
  assert(overlay.textContent.includes('恢复已作为新修改提交')||overlay.textContent.includes('文字已保存'));
  console.log('edit history DOM: more-menu entry, preview confirm gate, no auto-restore, Asia/Shanghai labels PASS');
  w.close();
})().catch(e=>{console.error(e);process.exit(1)});
