// Entry-only popup: configured independently from the ordinary inline announcement.
(() => {
  const shown = new Set();
  const $ = s => document.querySelector(s);
  const day = () => new Intl.DateTimeFormat('en-CA', {timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
  let pending=null, active=null, before=null;
  function key(p) { return 'yycj_popup_day_v1:'+encodeURIComponent(p.scope_user)+':'+encodeURIComponent(p.scope_city || '*'); }
  function storageRead(k) { try {return localStorage.getItem(k);} catch {return null;} }
  function close(today) {
    if(!active)return;
    if(today) {try {localStorage.setItem(key(active),day());} catch { /* still dismiss this opening */ }}
    $('#entryAnnouncement')?.remove();document.body.classList.remove('entry-announcement-open');
    active=null;before?.focus?.();
  }
  function attempt() {
    const p=pending;
    if(!p || !p.scope_user || !String(p.text || '').trim() || $('#app')?.classList.contains('hidden'))return;
    if(active)return;
    pending=null;const k=key(p);
    if(shown.has(k) || storageRead(k) === day())return;
    shown.add(k);active=p;before=document.activeElement;
    const overlay=document.createElement('div');overlay.id='entryAnnouncement';overlay.className='entry-announcement';
    overlay.innerHTML='<section class="entry-announcement-panel" role="dialog" aria-modal="true" aria-labelledby="entryAnnouncementTitle" tabindex="-1"><header><h2 id="entryAnnouncementTitle"></h2><button class="btn" type="button" data-popup-close aria-label="关闭公告">关闭</button></header><div id="entryAnnouncementText" class="entry-announcement-text"></div><footer><button class="btn" type="button" data-popup-today>今日不再弹出</button><button class="btn primary" type="button" data-popup-close>关闭</button></footer></section>';
    // Server/admin text is always text, never HTML; keep newlines without interpreting tags.
    overlay.querySelector('h2').textContent=String(p.title || '本城公告');overlay.querySelector('#entryAnnouncementText').textContent=String(p.text);
    overlay.addEventListener('click',ev=>{if(ev.target.closest('[data-popup-today]'))close(true);else if(ev.target.closest('[data-popup-close]'))close(false);});
    overlay.addEventListener('keydown',ev=>{
      if(ev.key==='Escape'){ev.preventDefault();close(false);}
      if(ev.key==='Tab') {const buttons=[...overlay.querySelectorAll('button')];const first=buttons[0],last=buttons.at(-1);if(ev.shiftKey && (document.activeElement===first || document.activeElement===overlay.firstElementChild)){ev.preventDefault();last.focus();}else if(!ev.shiftKey && document.activeElement===last){ev.preventDefault();first.focus();}}
    });
    document.body.appendChild(overlay);document.body.classList.add('entry-announcement-open');overlay.querySelector('[data-popup-close]').focus();
  }
  window.addEventListener('yycj:entry-announcement',ev=>{const p=ev.detail;if(!p){pending=null;return;}pending=p;attempt();});
  const app=$('#app');if(app)new MutationObserver(attempt).observe(app,{attributes:true,attributeFilter:['class']});
})();
