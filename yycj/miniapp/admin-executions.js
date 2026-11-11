// Explicit, city-scoped operational diagnostics. GET/refresh never retries or sends.
(() => {
 const side=document.getElementById('adminSide'),main=document.querySelector('.admin-main');if(!side||!main)return;
 const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const nav=document.createElement('button');nav.type='button';nav.dataset.pane='executions';nav.textContent='执行日志';side.append(nav);
 const pane=document.createElement('section');pane.className='admin-pane';pane.id='pane-executions';main.append(pane);
 const features={welcome:'欢迎消息',approval_notification:'老师成功通知',listing_broadcast:'频道上架推送',renewal:'续期',listing_edit:'修改资料交付',label_sync:'频道链接同步'};
 const status={running:'执行中/待核对',sent:'已发送',synced:'已同步',failed:'明确失败',unknown:'送达不明',skipped:'按开关跳过',not_configured:'未配置目标',no_post:'无原帖',stale:'已失效'};
 pane.innerHTML=`<div class="card"><h2>执行日志</h2><p class="muted">记录欢迎、老师成功通知、上架/续期推送、资料修改交付和频道标签同步。优惠/精选评价/目录详细执行仍看运行日志，本页不假称全部覆盖。无历史自动补推。</p><form id="executionFilter"><div class="row"><label>城市<input id="executionCity" placeholder="留空＝有权查看的城市；全站私聊仅超管"/></label><label>功能<select id="executionFeature"><option value="">全部</option>${Object.entries(features).map(([k,v])=>`<option value="${k}">${v}</option>`).join('')}</select></label><label>状态<select id="executionStatus"><option value="">全部</option>${Object.entries(status).map(([k,v])=>`<option value="${k}">${v}</option>`).join('')}</select></label></div><div class="row"><input id="executionListing" placeholder="资料ID"/><input id="executionOp" placeholder="操作ID"/><label>从（北京时间）<input type="datetime-local" id="executionSince"/></label><label>至（北京时间）<input type="datetime-local" id="executionUntil"/></label><button class="btn primary" type="submit">查询/刷新</button></div></form><p id="executionNote" class="muted"></p><div id="executionRows"></div><div class="row"><button class="btn" type="button" id="executionPrev">上一页</button><span id="executionPage"></span><button class="btn" type="button" id="executionNext">下一页</button></div></div>`;
 let offset=0,more=false;
 const val=id=>document.getElementById(id).value.trim(),headers=()=>({Authorization:'Bearer '+(localStorage.getItem('yycj_token')||'')});
 const time=t=>t?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(t))+' 北京时间':'—';
 async function load(){
  const box=document.getElementById('executionRows');box.textContent='读取中…';
  const q=new URLSearchParams({offset:String(offset),limit:'25'});
  for(const [key,id] of [['city','executionCity'],['feature','executionFeature'],['status','executionStatus'],['listing_id','executionListing'],['operation_id','executionOp']])if(val(id))q.set(key,val(id));
  for(const [key,id] of [['since','executionSince'],['until','executionUntil']])if(val(id))q.set(key,new Date(val(id)+'+08:00').toISOString());
  try{const r=await fetch('/api/admin/executions?'+q,{headers:headers(),cache:'no-store'}),d=await r.json();if(!r.ok)throw Error(d.detail||'查询失败');more=d.has_more;
   box.innerHTML=(d.items||[]).map(x=>`<article class="execution-row"><strong>${esc(features[x.feature]||x.feature)} · ${esc(status[x.status]||x.status)}</strong><p>${esc(x.city||'全站/私聊')} · ${esc(x.step)} · ${esc(time(x.started_at))}</p><p>资料 ${esc(x.listing_id||'—')} · 操作 ${esc(x.operation_id||'—')} · 配置版本 ${esc(x.config_revision??'未记录')}</p><p>${esc(x.reason||'')}</p>${x.retryable?`<button type="button" class="btn" data-execution-retry="${esc(x.record_id)}">仅重试失败步骤</button>`:'<p class="muted">不提供通用重发；成功、开关跳过或不确定送达不可盲目重试。</p>'}</article>`).join('')||'<p class="muted">暂无符合条件的记录；上线前历史执行不会伪造补录。</p>';
   document.getElementById('executionPage').textContent=`${d.total?offset+1:0}–${Math.min(offset+25,d.total)} / ${d.total}`;document.getElementById('executionPrev').disabled=!offset;document.getElementById('executionNext').disabled=!more;
  }catch(e){box.textContent=e.message||'读取失败';}
 }
 pane.querySelector('form').onsubmit=e=>{e.preventDefault();offset=0;load();};
 document.getElementById('executionPrev').onclick=()=>{offset=Math.max(0,offset-25);load();};document.getElementById('executionNext').onclick=()=>{if(more){offset+=25;load();}};
 pane.addEventListener('click',async e=>{const b=e.target.closest('[data-execution-retry]');if(!b)return;if(!confirm('只重试可安全重试的失败步骤，不重新保存资料、不续期、不重复频道成功发送。继续？'))return;b.disabled=true;
  try{const r=await fetch('/api/admin/executions/'+encodeURIComponent(b.dataset.executionRetry)+'/retry',{method:'POST',headers:{...headers(),'Content-Type':'application/json'},body:'{}'}),d=await r.json();if(!r.ok)throw Error(d.detail||'不可重试');await load();}catch(e){document.getElementById('executionNote').textContent=e.message||'处理失败';b.disabled=false;}
 });
 nav.addEventListener('click',load);
})();
