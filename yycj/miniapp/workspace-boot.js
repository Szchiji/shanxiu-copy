// Installed before legacy operational modules: one shared scope, no mutation during hydration.
(() => {
 const W=window.yycjWorkspace={city:'',mode:'all',page:'overview',tab:'',cache:{},dirty:false};
 const native=window.fetch.bind(window);
 const lists=/^\/api\/admin\/(posts\/pending|reviews\/pending|reports\/pending|listings|homepage|shadow|users|coupons|executions|stats\/overview)$/;
 window.fetch=function(input,opt={}){
  let url=typeof input==='string'?input:input?.url;const method=String(opt.method||input?.method||'GET').toUpperCase();let u;
  try{u=new URL(url,location.href)}catch{return native(input,opt)}
  if(u.origin===location.origin&&method==='GET'&&lists.test(u.pathname)){
   if(W.mode==='city')u.searchParams.set('city',W.city);else u.searchParams.delete('city');
   input=typeof input==='string'?u.pathname+u.search:new Request(u,input);
  }
  const scopeKey=W.mode+':'+W.city;
  const scoped=u.origin===location.origin&&method==='GET'&&lists.test(u.pathname);
  const promise=native(input,opt).then(r=>{if(scoped&&scopeKey!==W.mode+':'+W.city)throw new Error('城市范围已变化，请读取当前范围');return r;});
  if(u.origin===location.origin&&method==='GET'&&/^\/api\/admin\//.test(u.pathname))promise.then(r=>{if(!r.ok)return;r.clone().json().then(data=>{if(scoped&&scopeKey!==W.mode+':'+W.city)return;
   W.cache[u.pathname]=data;document.dispatchEvent(new CustomEvent('workspace:data',{detail:{path:u.pathname,data,city:u.searchParams.get('city')||'',mode:W.mode}}));
  }).catch(()=>{})}).catch(()=>{});
  return promise;
 };
 // Do not discard text/media drafts by browser refresh without an explicit choice.
 window.addEventListener('beforeunload',e=>{if(W.dirty||W.saving||W.editorBusy){e.preventDefault();e.returnValue='';}});
})();
