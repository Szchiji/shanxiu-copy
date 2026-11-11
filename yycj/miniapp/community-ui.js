// City community, shared-filter district map, and a light review ranking. No new bottom tab.
(() => {
  const $ = s => document.querySelector(s);
  const esc = x => String(x ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const city = () => localStorage.getItem('yycj_city') || '';
  const api = async path => { const r=await fetch(path,{headers:{Authorization:'Bearer '+(localStorage.getItem('yycj_token')||'')}}); const d=await r.json(); if(!r.ok) throw new Error(d.detail || '加载失败'); return d; };
  function show(name) { document.querySelectorAll('.view').forEach(v=>v.classList.toggle('hidden',v.id !== 'view-'+name)); }
  function openLink(url) { const tg=window.Telegram?.WebApp; if(/^https:\/\/t\.me\//.test(url) && tg?.openTelegramLink) tg.openTelegramLink(url); else if(tg?.openLink) tg.openLink(url); else window.open(url,'_blank','noopener'); }
  function panel() {
    let p=$('#view-community'); if(p) return p;
    p=document.createElement('section');p.id='view-community';p.className='view hidden';$('#view-home')?.parentNode.appendChild(p); return p;
  }
  let request=0, back='me';
  async function open(kind) {
    if(!$('#view-home')?.classList.contains('hidden')) back='home'; else if(!$('#view-me')?.classList.contains('hidden')) back='me';
    const p=panel(), seq=++request, c=city();
    p.innerHTML=`<button class="btn" data-hub-back>‹ 返回</button><div class="card"><h2>${esc(c)} · ${kind==='ranking'?'本城评价榜':'城市社区'}</h2><div id="hubBody">加载中…</div></div>`;show('community');
    try {
      const d=await api('/api/community'+(kind==='ranking'?'/ranking':'')+'?city='+encodeURIComponent(c)); if(seq!==request || c!==city()) return;
      const body=$('#hubBody'); if(!body) return;
      if(kind==='ranking') body.innerHTML=`<p class="muted">${esc(d.rule)}</p>`+(d.items.length?d.items.map(x=>`<button class="hub-row" data-hub-lamp="${esc(x.lamp_id)}"><b>${x.rank}. ${esc(x.title)}</b><span>${esc(x.district || '')} · ${x.review_count} 位评价</span></button>`).join(''):'<p class="muted">近30天暂无符合条件的评价，不编造榜单。</p>');
      else body.innerHTML=`<p class="muted">月影车姬 · ${esc(d.city)}，仅显示已配置的官方入口。</p>`+(d.links.length?d.links.map(x=>`<button class="hub-row" data-hub-url="${esc(x.url)}"><b>${esc(x.label)}</b><span>前往 ›</span></button>`).join(''):'<p class="muted">本城社区入口暂未配置。</p>')+`<p class="muted">${d.links.some(x=>x.key==='discussion')?'':'本城暂未开群。'}目录设置变更在12:00刷新后生效。</p><button class="hub-row" data-hub="ranking"><b>本城评价榜</b><span>近30天 ›</span></button>`;
    } catch(e) { if(seq===request && $('#hubBody')) $('#hubBody').innerHTML=`<p class="muted">${esc(e.message)}</p><button class="btn" data-hub="${kind}">重试</button>`; }
  }
  document.addEventListener('click',ev=>{
    const b=ev.target.closest('[data-hub],[data-hub-url],[data-hub-lamp],[data-hub-back]');if(!b)return;
    if(b.dataset.hub) open(b.dataset.hub);
    else if(b.dataset.hubUrl) openLink(b.dataset.hubUrl);
    else if(b.dataset.hubLamp) { show('detail');window.__yycjOpenLamp=b.dataset.hubLamp;fetchDetail(b.dataset.hubLamp); }
    else { ++request;show(back); }
  });
  async function fetchDetail(id) { try { const d=await api('/api/lamps/'+encodeURIComponent(id)); if(window.__yycjOpenLamp === id) window.__yycjRenderDetail?.(d,id); } catch(e) { window.__yycjToast?.(e.message); } }
  function mount() {
    const home=$('#view-home');if(!home) return;
    if(!$('#hubHomeEntry')) { const b=document.createElement('button');b.id='hubHomeEntry';b.type='button';b.className='top-admin';b.dataset.hub='community';b.textContent='本城社区';const old=$('#topAdmin');if(old)old.replaceWith(b);else $('.topbar')?.prepend(b); }
    if(!$('#hubMapToggle')) { const b=document.createElement('button');b.id='hubMapToggle';b.className='btn hub-map-toggle';b.type='button';b.textContent='地图';b.setAttribute('aria-pressed','false');const anchor=$('#btnSearch'); if(anchor) anchor.insertAdjacentElement('afterend',b); b.addEventListener('click',()=>{ if(window.__yycjMapMode) closeMap(); else openMap(); }); }
    ensureFullView();
  }
  function ensureFullView(){
    let v=$('#view-map');
    if(!v){
      v=document.createElement('div');
      v.id='view-map';
      v.className='map-fullview hidden';
      v.setAttribute('role','region');
      v.setAttribute('aria-label','资料地图');
      v.innerHTML=`<header class="map-full-top">
        <button type="button" class="btn map-full-back" id="mapFullBack" aria-label="返回首页">返回</button>
        <button type="button" class="chip map-full-city" id="mapFullCity">城市 ▾</button>
        <form class="map-full-search" id="mapFullSearch" action="#">
          <input id="mapFullQ" maxlength="64" placeholder="搜花名、标签、城区、价位" autocomplete="off" />
          <button class="btn" type="submit">搜索</button>
        </form>
        <button type="button" class="btn map-full-filter-btn" id="mapFullFilterBtn" aria-expanded="false" aria-controls="mapFullFilterPanel">筛选</button>
      </header>
      <div id="mapFullFilterPanel" class="map-full-filter-panel hidden"></div>
      <div id="mapFullStage" class="map-full-body"></div>`;
      ($('#app')||document.body).appendChild(v);
      v.addEventListener('click',ev=>{
        if(ev.target.closest('#mapFullBack')){closeMap();return;}
        if(ev.target.closest('#mapFullCity')){syncCityChip();const btn=$('#btnCity');if(btn)btn.click();else {$('#citySheet')?.classList.remove('hidden');}}
        if(ev.target.closest('#mapFullFilterBtn'))toggleFilters();
      });
      v.querySelector('#mapFullSearch').addEventListener('submit',ev=>{
        ev.preventDefault();
        const q=($('#mapFullQ')?.value||'').trim();
        if($('#homeQ')) $('#homeQ').value=q;
        window.__yycjMapFilters?.apply();
      });
    }
    if(!$('#hubMapWrap')) { const w=document.createElement('div');w.id='hubMapWrap';w.className='hidden';($('#mapFullStage')||v).appendChild(w); }
    else if($('#mapFullStage') && $('#hubMapWrap').parentElement!==$('#mapFullStage')) $('#mapFullStage').appendChild($('#hubMapWrap'));
    const bar=$('#hubMapFilters'), drawer=$('#mapFullFilterPanel');
    if(bar&&drawer&&bar.parentElement!==drawer) drawer.appendChild(bar);
    return v;
  }
  function syncCityChip(){
    const label=(city()||'城市')+' ▾';
    const chip=$('#mapFullCity'); if(chip) chip.textContent=label;
  }
  function filtersOpen(){return !$('#mapFullFilterPanel')?.classList.contains('hidden');}
  function toggleFilters(force){
    const panel=$('#mapFullFilterPanel'); if(!panel) return;
    const on=force!=null?!!force:!filtersOpen();
    panel.classList.toggle('hidden',!on);
    const btn=$('#mapFullFilterBtn');
    if(btn){btn.setAttribute('aria-expanded',String(on));btn.classList.toggle('is-open',on);}
    window.__yycjMapFilters?.paint();
    resultMap?.layoutDock();
    resultMap?.map?.invalidateSize({pan:false});
  }
  function overlayOpen(){
    if(shown($('#citySheet'))||shown($('#cityDrop'))) return true;
    if(filtersOpen()) return true;
    const cho=$('#hubMapChooser'), sel=$('#hubMapSelection');
    if(cho&&!cho.classList.contains('hidden')) return true;
    if(sel&&!sel.classList.contains('hidden')) return true;
    return false;
  }
  function shown(el){return !!el && !el.classList.contains('hidden') && (el=== $('#citySheet') ? getComputedStyle(el).display!=='none' : true);}
  function closeOverlays(){
    let closed=false;
    const sheet=$('#citySheet'), drop=$('#cityDrop');
    if(sheet&&!sheet.classList.contains('hidden')){sheet.classList.add('hidden');closed=true;}
    if(drop&&!drop.classList.contains('hidden')){drop.classList.add('hidden');closed=true;}
    if(filtersOpen()){toggleFilters(false);closed=true;}
    if(resultMap && ((!$('#hubMapChooser')?.classList.contains('hidden'))||(!$('#hubMapSelection')?.classList.contains('hidden')))){resultMap.deselect();closed=true;}
    return closed;
  }
  function saveHome(){
    return {
      scroll:window.scrollY||document.documentElement.scrollTop||0,
      city:city(),
      q:($('#homeQ')?.value||'').trim(),
      district:window.__yycjMapFilters?.get()?.district||'',
      service:window.__yycjMapFilters?.get()?.service||'all',
      sig:window.__yycjMapFilters?.signature()
    };
  }
  function restoreHome(saved){
    if(!saved) return;
    if(saved.city!=null){ localStorage.setItem('yycj_city',saved.city); const chip=$('#btnCity'); if(chip) chip.textContent=(saved.city||'城市')+' ▾'; }
    if($('#homeQ')) $('#homeQ').value=saved.q||'';
    if(window.__yycjMapFilters){
      const cur=window.__yycjMapFilters.get()||{};
      if(cur.district!==saved.district || (cur.service||'all')!==(saved.service||'all')){
        window.__yycjMapFilters.set({district:saved.district||'',service:saved.service||'all'});
      }
    }
    requestAnimationFrame(()=>{const y=saved.scroll||0;try{(document.scrollingElement||document.documentElement).scrollTop=y;}catch(e){}});
  }
  function syncMapMode(){
    window.__yycjMapFilters?.paint();
    const on=!!window.__yycjMapMode;
    $('#feed')?.classList.toggle('hidden',on);
    $('#pins')?.classList.toggle('hidden',on);
    $('#hubMapWrap')?.classList.toggle('hidden',!on);
    if(on) $('#btnLoadMore')?.classList.add('hidden');
    const toggle=$('#hubMapToggle');
    if(toggle){ toggle.textContent=on?'列表':'地图'; toggle.setAttribute('aria-pressed',String(on)); }
    const leavingDetail=!$('#view-detail')?.classList.contains('hidden');
    const app=$('#app');
    if(app) app.classList.toggle('map-full-open', on && !leavingDetail);
    const view=$('#view-map');
    if(view) view.classList.toggle('hidden', !(on && !leavingDetail));
    document.documentElement.classList.toggle('map-full-open', on && !leavingDetail);
    syncCityChip();
    if($('#mapFullQ') && on && $('#homeQ')) $('#mapFullQ').value=$('#homeQ').value||'';
  }
  let resultMap=null,mapReturn=null,savedHome=null,historyDepth=0,ignorePop=false,leavingToDetail=false;
  function pushHist(){
    try{ if(history.state?.yycj!=='map') history.pushState({yycj:'map'},''); }catch(e){}
  }
  function dropHist(){
    try{ if(history.state?.yycj==='map') history.replaceState({},''); }catch(e){}
  }
  function openMap(){
    if(window.__yycjMapMode) return;
    savedHome=saveHome();
    window.__yycjMapMode=true;
    ensureFullView();
    toggleFilters(false);
    syncMapMode();
    pushHist();
    controller()?.activate(true);
    window.__yycjLoadHome?.();
    requestAnimationFrame(()=>{resultMap?.resume(); resultMap?.layoutDock(); resultMap?.map?.invalidateSize({pan:false});});
  }
  function closeMap(opts={}){
    if(!window.__yycjMapMode && !$('#app')?.classList.contains('map-full-open')) return;
    closeOverlays();
    window.__yycjMapMode=false;
    window.__yycjCancelHome?.();
    resultMap?.destroy(); resultMap=null;
    mapReturn=null;
    leavingToDetail=false;
    syncMapMode();
    restoreHome(savedHome);
    savedHome=null;
    if(!opts.fromPop) dropHist();
    window.__yycjLoadHome?.();
  }
  function openMapDetail(id){
    const box=$('#detail');if(!box)return;
    mapReturn={city:city(),scope:window.__yycjMapFilters?.signature(),scroll:window.scrollY,snap:resultMap?.snapshot()};
    leavingToDetail=true;
    window.__yycjBack='home';
    window.__yycjOpenLamp=id;
    box.innerHTML='';
    const gal=$('#yycjGallery');if(gal){gal.innerHTML='';gal.dataset.lamp='';}$('#view-detail .xhs-bar')?.remove();const name=$('#xhsTop .xt-name');if(name)name.textContent='';
    if(window.openLamp){window.__yycjDropLampCache?.(id);window.openLamp(id);syncMapMode();return;}
    box.setAttribute('data-lamp',id);show('detail');syncMapMode();
    if(!window.__yycjEnsureDetail)fetchDetail(id);
  }
  const homeView=$('#view-home');
  if(homeView)new MutationObserver(()=>{
    if(homeView.classList.contains('hidden'))return;
    if(window.__yycjMapMode&&mapReturn&&mapReturn.city===city()&&mapReturn.scope===window.__yycjMapFilters?.signature()){
      leavingToDetail=false;
      syncMapMode();
      const snap=mapReturn.snap;
      requestAnimationFrame(()=>{
        resultMap?.resume();
        if(snap) resultMap?.restore(snap);
        resultMap?.map?.invalidateSize({pan:false});
      });
    }else{
      syncMapMode();
    }
    mapReturn=null;
  }).observe(homeView,{attributes:true,attributeFilter:['class']});
  function controller(){mount();if(!resultMap&&window.yycjResultMap)resultMap=new window.yycjResultMap.ResultMap($('#hubMapWrap'),{city,onSearch:area=>window.__yycjLoadHome?.({mapArea:area}),onDetail:openMapDetail,loadLeaflet:()=>window.__yycjLoadLeaflet()});return resultMap;}
  window.addEventListener('yycj:home-data',ev=>{syncMapMode();if(window.__yycjMapMode)controller()?.receive(ev.detail,ev.mapRequest||{});});
  window.addEventListener('yycj:map-loading',ev=>controller()?.begin(ev.detail));
  window.addEventListener('yycj:map-cancel',()=>resultMap?.cancel());
  window.addEventListener('yycj:map-pending',()=>resultMap?.pendingChanges());
  window.addEventListener('yycj:map-error',ev=>controller()?.error(ev.detail));
  window.addEventListener('pagehide',ev=>{if(!ev.persisted){resultMap?.destroy();resultMap=null;}});
  window.addEventListener('popstate',()=>{
    if(ignorePop) return;
    if(overlayOpen()){ closeOverlays(); try{ history.pushState({yycj:'map'},''); }catch(e){} return; }
    if(window.__yycjMapMode) closeMap({fromPop:true});
  });
  window.__yycjCloseMapOverlays=closeOverlays;
  window.__yycjCloseMap=closeMap;
  window.__yycjMapOverlayOpen=overlayOpen;
  window.__yycjMapFullOpen=()=>!!(window.__yycjMapMode && $('#app')?.classList.contains('map-full-open'));
  document.addEventListener('click',ev=>{
    const nav=ev.target.closest('.bottom-nav [data-nav]');
    if(!nav||!window.__yycjMapMode) return;
    const name=nav.getAttribute('data-nav');
    if(name && name!=='home') closeMap();
  },true);
  mount();
  // Welcome's known 「我的」 target, after authentication finishes.
  if(new URLSearchParams(location.search).get('page')==='me') { let tries=0;const go=()=>{if($('#app')&&!$('#app').classList.contains('hidden')) {$('[data-nav="me"]')?.click();} else if(++tries<80) setTimeout(go,150);};go(); }
})();
