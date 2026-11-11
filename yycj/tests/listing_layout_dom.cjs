// Node/jsdom only: listing-card markup and action placement. No network or screenshots.
const {JSDOM}=require('jsdom'),fs=require('node:fs'),assert=require('node:assert/strict'),path=require('node:path');
const root=path.resolve(__dirname,'..');
const src=name=>fs.readFileSync(path.join(root,'miniapp',name),'utf8');
(async()=>{
  const dom=new JSDOM('<!doctype html><div id="adminListings"></div>',{url:'https://test.invalid/',runScripts:'outside-only'});
  const w=dom.window,d=w.document;
  w.eval(src('edit-state-ui.js'));
  w.eval(src('admin-ops.js'));
  assert.equal(typeof w.yycjAdminListings.render,'function');
  const edit={operation_id:'op-1',text_saved:true,review_status:'none',action:'text',channel_status:'synced',channel_retryable:false,notification_status:'not_required',notification_retryable:false,album:'https://t.me/example/1001'};
  const fail={operation_id:'op-fail',text_saved:true,review_status:'none',action:'text',channel_status:'failed',channel_retryable:true,notification_status:'not_required',notification_retryable:false,album:'https://t.me/example/1002'};
  const items=[
    {lamp_id:'xiaoming',title:'小明',city:'深圳',status:'active',expires_at:'2026-12-04T00:00:00Z',updated_at:'2026-11-11T10:00:00Z'},
    {lamp_id:'xiaohong',title:'小红',city:'深圳',status:'active',expires_at:'2026-12-11T00:00:00Z',updated_at:'2026-11-11T10:00:00Z',edit_state:edit},
    {lamp_id:'xiaolan',title:'小兰',city:'深圳',status:'active',expires_at:'2026-12-15T00:00:00Z',updated_at:'2026-11-11T10:00:00Z',edit_state:fail},
    {lamp_id:'xiaoqing',title:'小青',city:'深圳',status:'hidden',unlist_reason:'teacher',expires_at:'2026-12-20T00:00:00Z',updated_at:'2026-11-11T10:00:00Z'}
  ];
  w.yycjAdminListings.render(items);
  const cards=[...d.querySelectorAll('#adminListings .listing-card')];
  assert.equal(cards.length,4);
  const hong=d.querySelector('[data-fill-proxy="xiaohong"]').closest('.listing-card');
  const ming=d.querySelector('[data-fill-proxy="xiaoming"]').closest('.listing-card');
  // Successful text-edit/sync must not pin a banner (or album link / retry disclaimer) on the card.
  assert(!hong.querySelector('.edit-result'), '成功修改不应占据卡片空间');
  assert(!ming.querySelector('.edit-result'));
  assert(!hong.textContent.includes('本次修改状态'));
  assert(!hong.textContent.includes('文字已保存；有效期不变'));
  assert(!hong.textContent.includes('查看本次频道帖'));
  assert(!hong.textContent.includes('重试不重新保存资料'));
  assert.equal(hong.querySelector('.meta').nextElementSibling,hong.querySelector('.listing-actions'));
  // Failure / retry stays compact, full-width, and actionable.
  const lan=d.querySelector('[data-fill-proxy="xiaolan"]').closest('.listing-card');
  const status=lan.querySelector('.edit-result');
  assert(status && status.classList.contains('edit-result-card'));
  assert(status.textContent.includes('修改需要处理'));
  assert(status.textContent.includes('频道同步明确失败'));
  assert.equal(status.querySelectorAll('[data-edit-retry]').length,1);
  assert(!status.querySelector('a'), '成功后的频道帖链接不出现在卡片上');
  assert.equal(lan.querySelector('.meta').nextElementSibling,status);
  assert.equal(status.nextElementSibling,lan.querySelector('.listing-actions'));
  const disclaimer=status.querySelector('.edit-result-more .muted');
  assert(disclaimer && disclaimer.textContent.includes('重试不重新保存资料'));
  assert(w.__yycjEditUI.actionable(fail) && !w.__yycjEditUI.actionable(edit));
  // Primary row for an already-active card: 修改资料 / 续期 / 下架 / 更多. 重新上架 is in 更多, 续期 is not truncated.
  const primary=[...hong.querySelector('.listing-actions').children].filter(el=>el.matches('button, details'));
  assert.deepEqual(primary.map(el=>el.tagName==='DETAILS'?'更多':el.textContent.trim()),['修改资料','续期','下架','更多']);
  assert(!hong.querySelector(':scope > .listing-actions > [data-list="relist"]'));
  const moreRelist=hong.querySelector('.listing-more-menu [data-list="relist"]');
  assert(moreRelist && moreRelist.textContent.trim()==='重新上架');
  assert(hong.querySelector('.listing-more-menu [data-list="delete"]'));
  assert(!d.body.textContent.includes('续期…'));
  assert(!d.body.textContent.includes('续期...'));
  // Hidden card: 重新上架 is the primary action, not tucked away only.
  const qing=d.querySelector('[data-fill-proxy="xiaoqing"]').closest('.listing-card');
  const qingPrimary=qing.querySelector(':scope > .listing-actions > [data-list="relist"]');
  assert(qingPrimary && qingPrimary.classList.contains('primary'));
  assert(!qing.querySelector('[data-list="unlist"]'));
  // Existing data-list / data-fill-proxy contract is unchanged for handlers.
  for (const sel of ['[data-fill-proxy]','[data-list="renew"]','[data-list="relist"]','[data-list="delete"]']) {
    assert(d.querySelector(sel), sel);
  }
  const css=src('admin.css');
  assert(css.includes('#adminListings > .pick-item'));
  assert(css.includes('flex-direction: column'));
  assert(css.includes('min-height: 44px'));
  assert(css.includes('top: calc(100% + 8px)'),'更多 menu opens below the action row');
  assert(!css.includes('bottom: calc(100% + 6px)'),'do not open 更多 over the card title');
  assert(css.includes(':has(.listing-more[open])'));
  console.log('listing layout DOM: success status hidden, failure compact, 续期 label, relist placement PASS');
  w.close();
})().catch(e=>{console.error(e);process.exit(1)});
