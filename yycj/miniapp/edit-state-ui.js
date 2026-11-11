// Shared, truthful edit/save/delivery feedback. No automatic retries or sends on refresh.
(() => {
  const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const uid=()=>typeof crypto.randomUUID==='function'?crypto.randomUUID():Array.from(crypto.getRandomValues(new Uint8Array(20)),x=>x.toString(16).padStart(2,'0')).join('');
  function request(body,key) {
    const payload=JSON.stringify(body);let old;
    try{old=JSON.parse(sessionStorage.getItem('yycj_edit_request:'+key)||'null');}catch{}
    if(!old || old.payload!==payload)old={payload,id:uid()};
    sessionStorage.setItem('yycj_edit_request:'+key,JSON.stringify(old));return {...body,operation_id:old.id};
  }
  function clear(key){sessionStorage.removeItem('yycj_edit_request:'+key);}
  const CHANNEL_ATTENTION=new Set(['pending','sending','failed','unknown']);
  const NOTICE_ATTENTION=new Set(['pending','sending','failed','unknown']);
  const REVIEW_ATTENTION=new Set(['pending','rejected','stale']);
  function selfActionable(op){
    if(!op)return false;
    if(op.channel_retryable||op.notification_retryable||op.superseded)return true;
    if(REVIEW_ATTENTION.has(op.review_status))return true;
    if(op.media_status==='pending')return true;
    if(CHANNEL_ATTENTION.has(op.channel_status)||NOTICE_ATTENTION.has(op.notification_status))return true;
    return false;
  }
  function actionable(op){
    return selfActionable(op)||(op.outstanding||[]).some(actionable);
  }
  function maps(op){
    const review={pending:'修改待审核',approved:'审核已通过',rejected:'修改未通过；原媒体保留',stale:'申请已失效，请重新提交'};
    const channel={pending:'等待同步',sending:'处理中或结果待核对，不可重发',synced:['repost','relist'].includes(op.action)?'完整新频道帖已发送':'原频道文字已同步',failed:'频道同步明确失败',unknown:'频道送达结果不明，不可盲目重发',not_configured:'未配置频道，尚未同步',no_post:'没有原频道帖；改文字不会自动新发',not_required:'当前不需要频道推送',stale:'媒体已有新版本，旧操作不会重发'};
    const notice={skipped:'老师成功通知已按当前开关跳过',pending:'等待通知（先完成频道步骤）',sending:'处理中或结果待核对，不可重发',sent:'老师通知已发送',failed:'老师通知明确失败',unknown:'通知结果不明，不可盲目重发',not_required:'无需老师通知',stale:'媒体已有更新，旧通知不再发送'};
    return {review,channel,notice};
  }
  function issueLines(op){
    const {review,channel,notice}=maps(op);
    const relist=op.action==='relist';
    const lines=[];
    if(relist&&op.superseded)lines.push('旧操作已失效，不会重发或恢复状态');
    if(op.review_status==='pending')lines.push(op.text_saved?'修改待审核':'整份修改待审核，原资料尚未替换');
    else if(REVIEW_ATTENTION.has(op.review_status))lines.push(review[op.review_status]||op.review_status);
    if(op.media_status==='pending')lines.push('新图片/视频待审；原媒体保留，当前上架状态不变。');
    if(CHANNEL_ATTENTION.has(op.channel_status))lines.push('频道：'+(channel[op.channel_status]||op.channel_status));
    if(NOTICE_ATTENTION.has(op.notification_status))lines.push('通知：'+(notice[op.notification_status]||op.notification_status));
    return lines;
  }
  function retryButtons(op){
    const relist=op.action==='relist';
    const button=(step,label)=>`<button class="btn" type="button" ${relist?'data-relist-retry':'data-edit-retry'}="${esc(op.operation_id)}" data-edit-step="${step}">${label}</button>`;
    let out='';
    if(op.channel_retryable)out+=button('channel','仅重试频道同步');
    if(op.notification_retryable && (['synced','not_required'].includes(op.channel_status)||op.review_status==='rejected'))out+=button('notification','仅重试老师通知');
    return out;
  }
  function fullHtml(op,scope){
    const {review,channel,notice}=maps(op);
    const relist=op.action==='relist';
    let out=`<div class="edit-result" data-edit-scope="${esc(scope)}"><strong>${relist?'本次重新上架状态':'本次修改状态'}</strong><p>${relist?(op.superseded?'旧操作已失效，不会重发或恢复状态':'资料已恢复上架；有效期不变'):op.text_saved?'文字已保存；有效期不变':(op.review_status==='pending'?'整份修改待审核，原资料尚未替换':'原资料保留；本申请未替换资料')}</p>`;
    if(op.review_status!=='none')out+=`<p>${esc(review[op.review_status]||op.review_status)}</p>`;
    if(op.media_status==='pending')out+='<p>新图片/视频待审；原媒体保留，当前上架状态不变。</p>';
    if(op.media_status==='approved')out+='<p>新媒体审核通过，已整组替换；有效期不变。</p>';
    if(op.media_status==='approved_admin')out+='<p>管理员已免审替换媒体'+(op.superseded_pending?'；旧待审申请失效':'')+'。</p>';
    out+=`<p>频道：${esc(channel[op.channel_status]||op.channel_status)}</p><p>通知：${esc(notice[op.notification_status]||op.notification_status)}</p>`;
    out+=retryButtons(op);
    if(/^https:\/\/t\.me\//.test(op.album||''))out+=`<p><a href="${esc(op.album)}" target="_blank" rel="noopener">查看本次频道帖</a></p>`;
    if(op.outstanding?.length)out+=`<details class="edit-outstanding"><summary>其他待处理修改（${op.outstanding.length}）</summary>${op.outstanding.map(x=>html(x,scope)).join('')}</details>`;
    return out+'<p class="muted">重试不重新保存资料、不续期、不重复已成功步骤。结果不明需人工核对。</p></div>';
  }
  function cardHtml(op,scope){
    if(!actionable(op))return '';
    const relist=op.action==='relist';
    const lines=selfActionable(op)?issueLines(op):[];
    const title=relist?'重新上架需要处理':'修改需要处理';
    const kids=(op.outstanding||[]).filter(actionable);
    let out=`<div class="edit-result edit-result-card" data-edit-scope="${esc(scope)}" data-edit-surface="card"><strong>${title}</strong>${lines.map(t=>`<p>${esc(t)}</p>`).join('')}${retryButtons(op)}`;
    if(kids.length)out+=`<details class="edit-outstanding"><summary>其他待处理修改（${kids.length}）</summary>${kids.map(x=>html(x,scope,{card:true})).join('')}</details>`;
    out+='<details class="edit-result-more"><summary>说明</summary><p class="muted">重试不重新保存资料、不续期、不重复已成功步骤。结果不明需人工核对。</p></details></div>';
    return out;
  }
  function html(op,scope='me',opts={}) {
    if(!op)return '';
    if(opts.card && !actionable(op))return '';
    return opts.card?cardHtml(op,scope):fullHtml(op,scope);
  }

  document.addEventListener('click',async ev=>{
    const b=ev.target.closest('[data-edit-retry],[data-relist-retry]');if(!b)return;ev.preventDefault();ev.stopPropagation();b.disabled=true;
    const box=b.closest('.edit-result'),scope=box.dataset.editScope==='admin'?'admin':'me';
    try {
      const r=await fetch(`/api/${scope}/${b.dataset.relistRetry?'relist-operations':'edit-operations'}/${encodeURIComponent(b.dataset.relistRetry||b.dataset.editRetry)}/retry`,{method:'POST',headers:{Authorization:'Bearer '+(localStorage.getItem('yycj_token')||''),'Content-Type':'application/json'},body:JSON.stringify({step:b.dataset.editStep})});
      const d=await r.json();if(!r.ok)throw Error(d.detail||'操作失败，请稍后查看状态');const next=html(d.operation,scope,box.dataset.editSurface==='card'?{card:true}:{});box.outerHTML=next;
    }catch(e){let msg=box.querySelector('.edit-retry-error');if(!msg){msg=document.createElement('p');msg.className='edit-retry-error';box.append(msg);}msg.textContent=e.message||'操作失败';b.disabled=false;}
  });
  window.__yycjEditUI={html,request,clear,actionable};
})();
