/* Isolated fixture transport for tests/offline review ONLY. Never loaded by production HTML. */
function createFixture(metadata,options={}){
 const copy=x=>JSON.parse(JSON.stringify(x));
 const state={calls:[],super:options.super!==false,scope:options.scope??null,settings:copy(metadata.settings),failOnce:{},records:[],pending:[],listings:[]};
 const image=text=>'data:image/svg+xml;charset=utf-8,'+encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="420" height="220"><rect width="420" height="220" fill="#253952"/><text x="210" y="110" text-anchor="middle" fill="#b5d2fa" font-size="22">${text}</text><text x="210" y="150" text-anchor="middle" fill="#96abc8" font-size="14">离线示例 · 不是老师照片</text></svg>`);
 const media=text=>[{type:'image',url:'fixture-'+text,preview_url:image(text)}];
 state.listings=[{lamp_id:'11111111-1111-4111-8111-111111111111',city:'深圳',district:'南山',user_id:81001,title:'示例资料 A',description:'当前最新文字（示例）',status:'active',updated_at:'2026-11-11T10:00:00',price_text:'示例价位',expires_at:'2026-11-25T05:00:00Z',media:media('当前旧媒体'),photos:[],tags:['示例'],extras:{}},{lamp_id:'22222222-2222-4222-8222-222222222222',city:'广州',user_id:81002,title:'示例资料 B',status:'hidden',updated_at:'2026-11-11T10:00:00',description:'已下架资料示例',media:[],tags:[],extras:{}},{lamp_id:'33333333-4444-4555-8666-777777777777',city:'深圳',district:'福田',user_id:81003,title:'示例资料 C',description:'频道同步失败示例',status:'active',updated_at:'2026-11-11T12:00:00',price_text:'示例价位',expires_at:'2026-12-15T16:00:00Z',media:[],photos:[],tags:['示例'],extras:{},edit_state:{operation_id:'example-edit-c',text_saved:true,review_status:'none',media_status:'unchanged',action:'text',channel_status:'failed',channel_retryable:true,notification_status:'not_required',notification_retryable:false}}];
 state.pending=[{post_id:'post-media',user_id:81001,status:'pending',lamp_data:{city:'深圳',title:'提交时文字（示例）',description:'旧快照，不覆盖后续文字',edit_lamp_id:state.listings[0].lamp_id,edit_workflow_v2:'media',media:media('待审核新媒体'),tags:[]}},{post_id:'post-new',user_id:81002,status:'pending',lamp_data:{city:'广州',title:'首次上架示例',description:'待审文字示例',media:[],tags:[]}}];
 const now=new Date().toISOString();
 state.records=[{record_id:'log-notification',operation_id:'33333333-3333-4333-8333-333333333333',feature:'listing_edit',city:'深圳',listing_id:state.listings[0].lamp_id,step:'notification',status:'failed',source:'business',reason:'Telegram明确拒绝请求，请检查目标/媒体/按钮配置',config_revision:7,target_ref:'publisher',retryable:true,started_at:now,finished_at:now},{record_id:'log-unknown',operation_id:'coupon-example-op',feature:'coupon_push',city:'深圳',step:'create',source:'business',status:'unknown',reason:'送达结果不确定，禁止盲目重发；需要人工核对',retryable:false,started_at:now,finished_at:now},{record_id:'log-skipped',operation_id:'review-example-op',feature:'featured_review',city:'广州',step:'publish',source:'review_approval',status:'skipped',reason:'功能开关已关闭，按设置跳过',retryable:false,started_at:now,finished_at:now},{record_id:'log-directory',operation_id:'directory-example-op',feature:'directory',city:'深圳',step:'update',source:'scheduled',status:'synced',reason:'',retryable:false,started_at:now,finished_at:now}];
 function permitted(city){return state.super||state.scope==null||state.scope.includes(city)}
 function scoped(items,city){return items.filter(x=>permitted(x.city??x.lamp_data?.city)&&(!city||(x.city??x.lamp_data?.city)===city))}
 function config(city){const fields=metadata.schema.filter(f=>!city||!f.site_only).map(f=>{const protectedField=!!(city&&f.super&&!state.super),v=city?(state.settings.city_overrides?.[city]?.[f.key]??null):state.settings[f.key],g=state.settings[f.key];return {...copy(f),value:protectedField?null:v,global:protectedField?null:g,effective:protectedField?null:v==null?g:v,protected:protectedField,editable:!protectedField&&(state.super||!!state.scope?.includes(city))}});if(city){for(const [key,type,label]of[['announcement_text','textarea','本城公告正文'],['announcement_enabled','bool','启用本城公告覆盖']])fields.push({key,type,label,section:'announcement',value:type==='bool'?false:'',global:state.settings[key],effective:state.settings[key],editable:state.super||!!state.scope?.includes(city),allow_blank:true,inherit_supported:false});}return {fields,city,is_super:state.super,can_edit:state.super||!!state.scope?.includes(city),can_edit_super:state.super,config_revision:7,announcement_effective:city?{text:state.settings.announcement_text,enabled:true,source:'global'}:null}}
 function handle(input,opt={}){
  const u=new URL(typeof input==='string'?input:input.url,'https://offline.invalid/app/desk.html'),path=u.pathname,method=String(opt.method||'GET').toUpperCase(),body=typeof opt.body==='string'?JSON.parse(opt.body||'{}'):opt.body||{};
  state.calls.push({path,method,query:Object.fromEntries(u.searchParams),body:copy(body)});
  const key=method+' '+path;if(state.failOnce[key]){delete state.failOnce[key];return {status:503,data:{detail:'示例：本次保存失败，未转发网络'}}}
  const city=u.searchParams.get('city');if(city&&!permitted(city))return {status:403,data:{detail:'无权操作该城市'}};
  const ok=data=>({status:200,data});
  if(path==='/api/auth')return ok({token:'OFFLINE_FIXTURE_ONLY',user:{user_id:1,role:'teacher'},is_admin:true});
  if(path==='/api/me')return ok({user:{user_id:1,role:'teacher'},is_admin:true});
  if(path==='/api/home')return ok({listing_fields:state.settings.listing_fields,enabled_cities:['深圳','广州']});
  if(path==='/api/admin/workspace/config'){
   const c=method==='POST'?body.city:city;
   if(!c&&!state.super)return {status:403,data:{detail:'全站配置仅超管'}};
   if(method==='POST'){if(c){state.settings.city_overrides[c]??={};Object.assign(state.settings.city_overrides[c],body.changes)}else Object.assign(state.settings,body.changes);return ok({ok:true,message:'离线示例已保存在本页内存，不影响生产',config:config(c)})}
   return ok(config(c));
  }
  if(path==='/api/admin/cities/summary'){
   const cities=['深圳','广州'].filter(permitted).map(city=>({city,posts:state.pending.filter(x=>x.lamp_data.city===city).length,reviews:0,reports:0,listings:state.listings.filter(x=>x.city===city&&['active','hidden'].includes(x.status)).length,pending:0,feed_pins:0,carousel:0}));
   const total={};for(const c of cities)for(const key of ['posts','reviews','reports','listings','pending','feed_pins','carousel'])total[key]=(total[key]||0)+c[key];
   return ok({is_super:state.super,scope:state.scope,cities,total,enabled_cities:cities.map(c=>c.city)});
  }
  if(path==='/api/admin/homepage')return ok({settings:state.settings,pins:[],feed_pins:[]});
  if(path==='/api/admin/posts/pending')return ok({items:scoped(state.pending,city)});
  if(path==='/api/admin/listings')return ok({items:scoped(state.listings,city)});
  if(path.startsWith('/api/admin/listings/')&&path.endsWith('/edit-state'))return ok({operation:null});
  if(path.startsWith('/api/lamps/'))return ok({lamp:state.listings.find(x=>x.lamp_id===decodeURIComponent(path.split('/').pop()))||null});
  if(path.startsWith('/api/admin/workspace/posts/')){const p=state.pending.find(x=>x.post_id===path.split('/').pop());return ok({post_id:p.post_id,status:p.status,pending:p.lamp_data,current:state.listings.find(x=>x.lamp_id===p.lamp_data.edit_lamp_id)||null})}
  if(path==='/api/admin/operators')return ok({is_super:state.super,super_ids:[1],extra_admin_ids:[82001],operator_cities:{82001:['深圳']}});
  if(path==='/api/admin/buttons/preview'){
   const source=body.context==='welcome'?body.items:body.config?.mode==='custom'?body.config.items:[{label:'查看资料',type:'webapp',new_row:true},{label:'查看频道',type:'url'}];
   const rows=[];for(const b of source||[]){if(b.enabled===false)continue;if(!rows.length||b.new_row||rows.at(-1).length>=4)rows.push([]);rows.at(-1).push({label:b.label,type:b.type})}return ok({rows});
  }
  if(path==='/api/admin/executions'){
   let rows=scoped(state.records,city);if(!state.super)rows=rows.filter(x=>x.city);for(const key of ['status','feature','listing_id','operation_id'])if(u.searchParams.get(key))rows=rows.filter(x=>x[key]===u.searchParams.get(key));
   const total=rows.length,offset=Number(u.searchParams.get('offset')||0),limit=Number(u.searchParams.get('limit')||25);return ok({items:rows.slice(offset,offset+limit),total,has_more:offset+limit<total});
  }
  if(path.startsWith('/api/admin/executions/')){const id=path.split('/')[4],record=state.records.find(x=>x.record_id===id);if(method==='POST'){if(!record?.retryable)return {status:409,data:{detail:'示例：不可安全重试'}};record.status='sent';record.retryable=false;return ok({ok:true})}return ok(record)}
  if(path==='/api/admin/coupons')return ok({items:[],total:0});
  if(path==='/api/admin/stats/overview'){const days=Array.from({length:7},(_,i)=>new Date(Date.now()-(6-i)*864e5).toISOString().slice(0,10));return ok({days,totals:{},daily:[],cities:[],sources:[]})}
  if(method==='POST'&&/^\/api\/admin\/posts\/[^/]+\/(approve|reject)$/.test(path)){const p=state.pending.find(x=>x.post_id===path.split('/')[4]);if(p)p.status=path.endsWith('/approve')?'approved':'rejected';return ok({ok:true})}
  if(method==='POST'&&/^\/api\/admin\/listings\/[^/]+\/(delete|unlist|relist|renew|proxy-edit)$/.test(path))return ok({ok:true,message:'离线示例操作；未调用生产服务',operation:{action:'text',channel_status:'synced',notification_status:'skipped'}});
  if(method==='GET'&&path.startsWith('/api/admin/'))return ok({items:[]});
  if(method!=='GET')return {status:403,data:{detail:'离线包未实现此示例操作，已阻止；不会请求生产'}};
  return {status:404,data:{detail:'离线示例无此资源'}};
 }
 return {state,handle,config};
}
if(typeof module!=='undefined')module.exports={createFixture};
