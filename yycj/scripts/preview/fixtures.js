// Preview-only bootstrap. Not included in production HTML or bundles.
(() => {
  const q=new URLSearchParams(location.search), mode=q.get('preview') || 'home';
  const cfg=window.__PREVIEW_CONFIG, admin=mode==='admin';
  const user={user_id:900001,role:'guest',nickname:'评审示例用户',username:'local_preview',tier:'示例',orchid_points:0};
  const noop=()=>{}, blocked=()=>alert('本地评审：不会打开外部链接或发送 Telegram。');
  const button={show:noop,hide:noop,onClick:noop,offClick:noop,setText:noop,setParams:noop,enable:noop,disable:noop};
  window.Telegram={WebApp:{initData:'LOCAL_PREVIEW_NOT_SIGNED',initDataUnsafe:{user},platform:'unknown',version:'8.0',colorScheme:'dark',ready:noop,expand:noop,close:noop,onEvent:noop,offEvent:noop,setHeaderColor:noop,setBackgroundColor:noop,enableClosingConfirmation:noop,disableClosingConfirmation:noop,isVersionAtLeast:()=>true,openTelegramLink:blocked,openLink:blocked,showAlert:blocked,showConfirm:(_,cb)=>cb?.(false),MainButton:{...button},BackButton:{...button},HapticFeedback:{impactOccurred:noop,notificationOccurred:noop,selectionChanged:noop}}};
  localStorage.setItem('yycj_token','LOCAL_PREVIEW_ONLY');localStorage.setItem('yycj_user',JSON.stringify(user));localStorage.setItem('yycj_role','guest');localStorage.setItem('yycj_city',q.get('city') || '深圳');
  if(q.get('fresh')==='1')Object.keys(localStorage).filter(k=>k.startsWith('yycj_popup_day_v1:')).forEach(k=>localStorage.removeItem(k));
  let settings={...cfg.settings,enabled_cities:['深圳','广州'],home_feed_page_size:12,announcement_enabled:true,announcement_text:'示例普通公告：本页为本地评审，不代表线上资料。',popup_announcement_enabled:mode==='popup' || admin,popup_announcement_title:'本城公告 · 评审示例',popup_announcement_text:'欢迎来到月影车姬。\n\n这是后台配置的独立弹窗公告示例，不是普通公告栏的复用。\n\n你可以选择「关闭」，仅关闭本次进入的公告；也可以选择「今日不再弹出」，按北京时间当天隐藏。\n\n本页所有人物、图片、数据均为评审占位，不连接生产，也不会发送 Telegram。',community_enabled:true,community_discussion_url:'',community_backup_url:'',community_location_url:'',bot_welcome_text:'欢迎来到月影车姬（评审示例）',bot_welcome_buttons:[]};
  const cityOverrides={'深圳':{popup_announcement_enabled:true,popup_announcement_title:'深圳公告 · 评审示例',popup_announcement_text:'深圳本城弹窗示例。\n\n公告内容和开关均由后台「首页展示」独立设置；普通公告栏仍保留自己的内容。\n\n关闭仅隐藏本次进入；今日不再弹出按北京时间计算。\n\n本地示例数据 · 未连接生产。'}};
  let cityAnnouncements={};
  const items=Array.from({length:12},(_,i)=>({lamp_id:'preview-'+(i+1),title:'示例资料 '+String.fromCharCode(65+i),city:'深圳',district:['南山','福田','宝安'][i%3],price_text:'示例价位',hours_text:'10:00–22:00',cover_url:'/preview-assets/cover'+(i+1)+'.svg',photos:['/preview-assets/cover'+(i+1)+'.svg'],media_count:1,tags:['示例标签'],status:'active',open_code:i===2?'rest':'open',open_text:i===2?'休息中':'营业中',extras:{},description:'评审示例，非真实上架资料。'}));
  const reply=(data,status=200)=>new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json'}});
  function cityConfig(city) {const own=cityOverrides[city] || {};return {can_edit:true,can_edit_super:true,overrides:own,keys:cfg.keys.map(k=>({...k,global:settings[k.key],override:own[k.key]??null}))};}
  function home(url) {const city=url.searchParams.get('city') || localStorage.getItem('yycj_city') || '深圳';const enabled=mode==='popup';const own=cityOverrides[city] || {};const count=Number(url.searchParams.get('offset') || 0);const search=url.searchParams.get('q');const list=items.map(x=>({...x,city})).filter(x=>!search || x.title.includes(search));return {city,enabled_cities:settings.enabled_cities,items:count?[]:list,pins:items.slice(0,4).map(lamp=>({lamp:{...lamp,city}})),has_more:false,total:list.length,announcement:{text:settings.announcement_text},carousel_interval_sec:8,home_feed_page_size:12,contacts:{admin_url:'',bot_url:''},districts:['南山','福田','宝安'],filters:{active:0,has_place:false},scoring_rules:{},map_groups:[],popup_announcement:enabled?{title:own.popup_announcement_title || settings.popup_announcement_title,text:own.popup_announcement_text || settings.popup_announcement_text,scope_user:String(user.user_id),scope_city:city}:null};}
  const allowedSettings=new Set(['/api/admin/settings/extra','/api/admin/homepage/ops','/api/admin/channel-features','/api/admin/operators/cities','/api/admin/cities/settings','/api/admin/cities/announcement']);
  window.fetch=async (input,opt={})=>{
    const url=new URL(typeof input==='string'?input:input.url,location.href),p=url.pathname,method=String(opt.method || input?.method || 'GET').toUpperCase();
    if(url.origin!==location.origin || !p.startsWith('/api/'))throw Error('本地评审禁止外部网络请求');
    await new Promise(r=>setTimeout(r,12));
    if(p==='/api/auth')return reply({token:'LOCAL_PREVIEW_ONLY',user,is_admin:admin,is_super:admin});
    if(method!=='GET' && !allowedSettings.has(p))return reply({detail:'本地评审：此操作已禁用，不会触发续期、发帖或其他真实变更。'},403);
    if(method!=='GET') {
      let b={};try{b=JSON.parse(opt.body || '{}');}catch{}
      if(p==='/api/admin/cities/settings'){cityOverrides[b.city]={...(cityOverrides[b.city] || {}),...b.overrides};return reply(cityConfig(b.city));}
      if(p==='/api/admin/cities/announcement'){cityAnnouncements[b.city]=b;return reply({city_announcements:cityAnnouncements});}
      settings={...settings,...b};return reply({ok:true,settings,global:settings,operator_cities:{}});
    }
    if(p==='/api/me')return reply({user,is_admin:admin,is_super:admin});
    if(p==='/api/home')return reply(home(url));
    if(p==='/api/admin/homepage')return reply({settings,feed_pins:[],pins:[]});
    if(p==='/api/admin/cities/summary')return reply({cities:settings.enabled_cities.map(city=>({city,posts:0,reviews:0,reports:0,listings:0,feed_pins:0,carousel:0})),total:{posts:0,reviews:0,reports:0,listings:0,feed_pins:0,carousel:0},is_super:true,scope:null,city_announcements:cityAnnouncements,city_override_counts:{'深圳':3}});
    if(p==='/api/admin/cities/settings')return reply(cityConfig(url.searchParams.get('city')));
    if(p==='/api/admin/operators')return reply({extra_admin_ids:[],operator_cities:{}});
    if(p==='/api/admin/channel-features')return reply({global:settings,cities:[]});
    if(p==='/api/admin/label-sync')return reply({status:'idle',pending:0,errors:[]});
    if(p==='/api/admin/stats/daily')return reply({days:[],rows:[],totals:{}});
    if(p==='/api/me/place')return reply({place:{set:false}});
    if(p==='/api/me/todos')return reply({items:[],summary:{},expiring:0,pending:0,claims:0});
    if(p==='/api/community')return reply({city:localStorage.getItem('yycj_city'),links:[]});
    if(p==='/api/community/ranking')return reply({city:localStorage.getItem('yycj_city'),items:[],rule:'评审示例：未生成虚构评价榜。'});
    if(p.startsWith('/api/lamps/'))return reply({lamp:items.find(x=>x.lamp_id===p.split('/').at(-1)) || items[0],reviews:[],reputation:{review_count:0},community_links:[],show_chat_cta:false});
    return reply({ok:true,items:[],rows:[],settings});
  };
  window.open=blocked;
  document.addEventListener('click',e=>{const a=e.target.closest('a[href]');if(a){const u=new URL(a.href,location.href);if(u.origin!==location.origin){e.preventDefault();e.stopImmediatePropagation();blocked();}}},true);
  document.addEventListener('DOMContentLoaded',()=>{
    const mark=document.createElement('div');mark.id='localPreviewMark';mark.textContent='评审示例数据 · 未连接生产 · 未上线';mark.style.cssText='position:fixed;bottom:68px;right:8px;z-index:11000;font:10px/1.4 sans-serif;color:#F1EEE4;background:#0F2420e8;border:1px solid #D4B26A;border-radius:5px;padding:3px 6px;pointer-events:none';document.body.append(mark);
    if(!admin)return;
    let attempts=0,selected=false,paneSelected=false,lastTarget=null;
    const focusPopup=()=>{
      if(!selected || q.get('section')!=='popup')return;
      const target=document.querySelector(q.get('scope')==='global'?'#ship_popup_announcement_text':'#ccf_popup_announcement_text');
      if(!target)return;
      target.closest('details').open=true;
      if(target!==lastTarget){target.closest('details').scrollIntoView({block:'start'});lastTarget=target;}
    };
    new MutationObserver(focusPopup).observe(document.body,{childList:true,subtree:true});
    const timer=setInterval(()=>{
      if(++attempts>100){clearInterval(timer);return;}
      if(document.querySelector('#console')?.classList.contains('hidden'))return;
      if(!paneSelected){document.querySelector('#adminSide [data-pane="venue"]')?.click();paneSelected=true;}
      const tabs=document.querySelector('#ccTabs');if(!tabs?.children.length)return;
      if(!selected){const city=q.get('scope')==='global'?'':q.get('city') || '深圳';[...tabs.querySelectorAll('[data-city]')].find(x=>x.dataset.city===city)?.click();selected=true;return;}
      const target=document.querySelector(q.get('scope')==='global'?'#ship_popup_announcement_text':'#ccf_popup_announcement_text');
      if(!target)return;
      focusPopup();
      clearInterval(timer);
    },100);
  });
})();
