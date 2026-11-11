// Non-browser DOM smoke of the served actual project HTML/assets; no screenshots.
const {JSDOM,ResourceLoader,VirtualConsole}=require('jsdom');
const assert=require('node:assert/strict');
const base=process.env.PREVIEW_URL || 'http://127.0.0.1:8874';
class LocalOnly extends ResourceLoader {fetch(url,opt){assert.equal(new URL(url).origin,base,'external resource forbidden in preview smoke');return super.fetch(url,opt);}}
const wait=ms=>new Promise(r=>setTimeout(r,ms));
async function check(path,fn){
 const errors=[];const vc=new VirtualConsole();vc.on('jsdomError',e=>errors.push(e.message));
 const dom=await JSDOM.fromURL(base+path,{runScripts:'dangerously',resources:new LocalOnly(),pretendToBeVisual:true,virtualConsole:vc,beforeParse(w){w.Response=Response;w.Request=Request;w.Headers=Headers;w.AbortController=AbortController;w.matchMedia=()=>({matches:false,addEventListener(){},removeEventListener(){},addListener(){},removeListener(){}});w.requestAnimationFrame=fn=>w.setTimeout(()=>fn(w.performance.now()),16);w.cancelAnimationFrame=id=>w.clearTimeout(id);w.scrollTo=()=>{};w.HTMLElement.prototype.scrollIntoView=()=>{};w.alert=()=>{};}});
 try{await wait(2400);await fn(dom.window,dom.window.document);assert.deepEqual(errors,[]);console.log('Served actual HTML DOM smoke passed:',path);}finally{dom.window.close();}
}
(async()=>{
 await check('/app/go.html?preview=home',(w,d)=>{assert(!d.querySelector('#app').classList.contains('hidden'));assert.equal(d.querySelectorAll('#feed .feed-card').length,12);assert(d.querySelector('.topbar > #hubHomeEntry'));assert.equal(d.querySelector('#btnSearch').nextElementSibling.id,'hubMapToggle');assert(d.querySelector('#pins').compareDocumentPosition(d.querySelector('.search-row')) & 4);assert(!d.querySelector('#entryAnnouncement'));assert(d.querySelector('#localPreviewMark'));});
 await check('/app/go.html?preview=popup&fresh=1',(w,d)=>{assert(d.querySelector('#entryAnnouncement [role="dialog"]'));assert(d.querySelector('#entryAnnouncementText').textContent.includes('深圳本城弹窗示例'));d.querySelector('[data-popup-close]').click();assert(!d.querySelector('#entryAnnouncement'));});
 await check('/app/desk.html?preview=admin&city=%E6%B7%B1%E5%9C%B3&section=popup',(w,d)=>{assert(d.querySelector('#pane-venue').classList.contains('active'));assert.equal(d.querySelector('#ccf_popup_announcement_title').value,'深圳公告 · 评审示例');assert(d.querySelector('#ccf_popup_announcement_text').closest('details').open);});
 await check('/app/desk.html?preview=admin&scope=global&section=popup',(w,d)=>{assert.equal(d.querySelector('#ship_popup_announcement_title').value,'本城公告 · 评审示例');assert(d.querySelector('#ship_popup_announcement_text').closest('details').open);});
})().catch(e=>{console.error(e);process.exitCode=1;});
