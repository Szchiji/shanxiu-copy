// EXPORTED REVIEW ONLY. Browser-memory examples; never included in production assets.
(() => {
 const mode=window.__EDIT_REVIEW_MODE,admin=mode.startsWith('admin'),baseFetch=window.fetch;
 const user={user_id:900001,role:'teacher',nickname:'评审示例老师',username:'example_only'};
 const reply=data=>new Response(JSON.stringify(data),{status:200,headers:{'Content-Type':'application/json'}});
 if(!admin){localStorage.setItem('yycj_user',JSON.stringify(user));localStorage.setItem('yycj_role','teacher');window.Telegram.WebApp.initDataUnsafe.user=user;}
 const initial={operation_id:'example-edit-operation-0001',text_saved:true,media_status:'pending',review_status:'pending',action:'text',channel_status:'synced',channel_retryable:false,notification_status:'not_required',notification_retryable:false,post_id:'example-media-review-1',album:null};
 const states=[initial,{...initial,operation_id:'example-edit-operation-0002',media_status:'approved',review_status:'approved',action:'repost',channel_status:'synced',notification_status:'failed',notification_retryable:true,album:'https://t.me/example/900'},
 {...initial,operation_id:'example-edit-operation-0003',media_status:'unchanged',review_status:'none',channel_status:'not_configured',channel_retryable:true},
 {...initial,operation_id:'example-edit-operation-0004',media_status:'approved',review_status:'approved',action:'repost',channel_status:'unknown',notification_status:'pending'},
 {...initial,operation_id:'example-edit-operation-0005',media_status:'rejected',review_status:'rejected',notification_status:'sent'}];
 const exampleImage=()=>{const svg='<svg xmlns="http://www.w3.org/2000/svg" width="360" height="480"><rect width="360" height="480" fill="#244D43"/><text x="180" y="230" text-anchor="middle" fill="#D4B26A" font-size="24">旧媒体 · 评审占位</text><text x="180" y="280" text-anchor="middle" fill="#F1EEE4" font-size="18">非真实人物或上架资料</text></svg>';return 'data:image/svg+xml;charset=utf-8,'+encodeURIComponent(svg);};
 const items=states.map((edit_state,i)=>({lamp_id:'preview-'+(i+1),user_id:user.user_id,title:'评审示例资料 '+(i+1),city:'深圳',district:'南山',status:'active',description:'这是一条本地示例资料，原媒体继续展示。不是线上人物，不会发送消息。',price_text:'示例价位',tags:['示例'],media:[{type:'image',url:exampleImage()}],photos:[exampleImage()],cover_url:exampleImage(),expires_at:'2026-12-10T12:00:00Z',shop:{code:'unset',remind_on:true,auto:true},extras:{},edit_state}));
 const post={post_id:'example-media-review-1',user_id:user.user_id,lamp_data:{title:'提交时文字（仅供参考）',city:'深圳',description:'媒体审核只替换整组新媒体，不覆盖后续文字。',media:[{type:'image',url:exampleImage()}],edit_lamp_id:'preview-1',edit_workflow_v2:'media'}};
 let pending=true;
 const records=[
 {record_id:'example-log-1',operation_id:'example-edit-operation-0002',feature:'listing_edit',step:'notification',city:'深圳',listing_id:'preview-2',config_revision:7,status:'failed',reason:'示例：频道已成功，仅老师通知明确失败，可单独重试通知',retryable:true},
 {record_id:'example-log-2',operation_id:'example-edit-operation-0004',feature:'listing_broadcast',step:'channel',city:'深圳',listing_id:'preview-4',config_revision:7,status:'unknown',reason:'示例：送达不确定，禁止盲目重发',retryable:false},
 {record_id:'example-log-3',operation_id:'example-notification',feature:'approval_notification',step:'notification',city:'深圳',listing_id:'preview-5',config_revision:8,status:'skipped',reason:'示例：老师成功通知开关关闭，跳过不是失败',retryable:false}
 ].map(x=>({...x,started_at:'2026-11-10T11:20:00Z'}));
 window.fetch=async(input,opt={})=>{
  const u=new URL(typeof input==='string'?input:input.url,'https://review.invalid/'),p=u.pathname,method=String(opt.method||'GET').toUpperCase();
  if(u.origin!=='https://review.invalid')throw Error('离线评审：禁止外部网络请求');
  if(p==='/api/auth' || p==='/api/me')return reply({token:'LOCAL_PREVIEW_ONLY',user,is_admin:admin,is_super:admin});
  if(method==='GET') {
   if(p==='/api/admin/executions'){const filtered=records.filter(x=>['city','feature','status','listing_id','operation_id'].every(k=>!u.searchParams.get(k)||x[k]===u.searchParams.get(k)));return reply({items:filtered,total:filtered.length,has_more:false});}
   if(p==='/api/me/listings')return reply({items,remind_lead_min:60,stats_show:{}});
   if(p==='/api/admin/listings')return reply({items,total:items.length,has_more:false});
   if(p==='/api/admin/posts/pending')return reply({items:pending?[post]:[]});
   if(/\/listings\/[^/]+\/edit-state$/.test(p))return reply({operation:items.find(x=>x.lamp_id===p.split('/').at(-2))?.edit_state});
   if(p.startsWith('/api/lamps/'))return reply({lamp:items.find(x=>x.lamp_id===p.split('/').at(-1))||items[0],reviews:[],reputation:{review_count:0}});
  }
  // Offline fixture renderer is for example-only layout; production preview uses the real Python renderer.
  if(method==='POST' && p==='/api/admin/buttons/preview'){
   const b=JSON.parse(opt.body),config=b.config||{};let list=b.context==='welcome'?b.items:config.mode==='custom'?config.items:[{type:'webapp',label:'查看我的资料',target:'detail',new_row:true},{type:'url',label:'查看频道帖',target:'{album}',new_row:true}];
   const rows=[];(list||[]).forEach((x,i)=>{if(x.enabled===false||!x.label||!x.target)return;if(!rows.length||(x.new_row??i%2===0)||rows.at(-1).length>=4)rows.push([]);rows.at(-1).push({type:x.type,label:x.label,url:'https://example.invalid/'});});return reply({rows,limits:{total:b.context==='welcome'?12:16,columns:4}});
  }
  if(method==='POST' && /^\/api\/admin\/executions\/[^/]+\/retry$/.test(p)){const item=records.find(x=>x.record_id===p.split('/').at(-2));if(item?.retryable){item.status='sent';item.retryable=false;item.reason='仅浏览器内示例状态改变，没有发送任何消息';}return reply({ok:true});}
  if(method==='POST' && /\/edit-operations\/[^/]+\/retry$/.test(p)) {
   const op=states.find(x=>x.operation_id===p.split('/').at(-2));const step=JSON.parse(opt.body).step;
   if(op){op[step+'_status']=step==='channel'?'synced':'sent';op[step+'_retryable']=false;return reply({operation:op});}
  }
  if(method==='POST' && /\/posts\/[^/]+\/(approve|reject)$/.test(p)){
   pending=false;Object.assign(initial,p.endsWith('/approve')?{media_status:'approved',review_status:'approved',action:'repost',channel_status:'synced',notification_status:'sent'}:{media_status:'rejected',review_status:'rejected',notification_status:'sent'});return reply({ok:true,operation:initial});
  }
  if(method==='POST' && /\/listings\/[^/]+\/(edit|proxy-edit)$/.test(p))return reply({ok:true,operation:{...initial,media_status:admin?'approved_admin':'pending'},instant:true});
  return baseFetch(input,opt);
 };
 document.addEventListener('DOMContentLoaded',()=>{
  let count=0,selected=false;
  const timer=setInterval(()=>{
   if(++count>100){clearInterval(timer);return;}
   if(admin){
    if(document.getElementById('console')?.classList.contains('hidden'))return;
    const pane=mode==='admin-review'?'audit':mode==='admin-buttons'?'venue':mode==='admin-executions'?'executions':'listings';document.querySelector(`#adminSide [data-pane="${pane}"]`)?.click();
    if(mode==='admin-edit' && !selected){const button=document.querySelector('[data-fill-proxy="preview-2"]');if(button){button.click();selected=true;}}
    if(mode==='admin-edit' && document.getElementById('editLampId')?.value==='preview-2')clearInterval(timer);
    else if(mode==='admin-buttons' && document.getElementById('shipWelcome')){document.querySelector('#ccTabs [data-city=""]')?.click();document.querySelectorAll('#ccGlobal details').forEach(x=>x.open=true);document.getElementById('shipWelcome')?.scrollIntoView?.();clearInterval(timer);}
    else if(mode==='admin-executions' && document.querySelector('.execution-row'))clearInterval(timer);
    else if(mode==='admin-review' && document.querySelector('[data-admin="post-ok"]'))clearInterval(timer);
   }else {
    if(!selected){document.querySelector('[data-nav="me"]')?.click();selected=true;}
    if(!document.querySelector('#myListings [data-edit]'))return;
    if(mode==='teacher-edit'){document.querySelector('#myListings [data-edit="preview-1"]')?.click();}
    clearInterval(timer);
   }
  },150);
 });
})();
