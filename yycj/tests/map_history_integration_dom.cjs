// Combined local version contracts: both features present, layout/native bounds held. No browser.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const {JSDOM}=require('jsdom');
const root=path.resolve(__dirname,'..');
const read=(p)=>fs.readFileSync(path.join(root,p),'utf8');

(async()=>{
  for(const page of ['miniapp/index.html','miniapp/go.html']){
    const html=read(page);
    assert.match(html,/app\.bundle\.js\?v=20261112b/);
    assert.match(html,/app\.bundle\.css\?v=20261112b/);
    assert.doesNotMatch(html,/20261111h|20261111k/);
    assert.doesNotMatch(html,/telegram-native/);
  }
  assert.equal(fs.existsSync(path.join(root,'miniapp/telegram-native.js')),false);
  const manifest=read('scripts/build_miniapp.py');
  assert(manifest.indexOf('"edit-history-ui.js"')<manifest.indexOf('"map-results.js"'));
  assert(manifest.indexOf('"map-results.js"')<manifest.indexOf('"community-ui.js"'));
  const bundle=read('miniapp/app.bundle.js');
  assert.match(bundle,/__yycjHistoryUI/);
  assert.match(bundle,/__yycjMapMode/);
  assert.match(bundle,/loadSequence/);
  assert.match(bundle,/requestSig !== filtSig\(\)/);
  assert.match(bundle,/seq!==loadSequence \|\| mapMode!==!!window\.__yycjMapMode/);
  assert.doesNotMatch(bundle,/telegram-native/);
  const me=read('miniapp/me-listings.js');
  assert.match(me,/data-history/);
  assert.match(me,/ls-more/);
  const home=read('miniapp/home-ui.js');
  assert.doesNotMatch(home,/data-history/);
  const map=read('miniapp/map-results.js');
  assert.doesNotMatch(map,/data-history|__yycjHistoryUI/);
  assert.match(map,/不偏移到虚构位置/);
  const histCss=read('miniapp/edit-history-ui.css');
  assert.doesNotMatch(histCss,/#feed|feed-card|117px|224px/);
  const community=read('miniapp/community.css');
  assert.match(community,/117x224/);
  const styles=read('miniapp/styles.css');
  assert.match(styles,/\.feed\.feed-3[\s\S]*repeat\(3,\s*1fr\)/);

  const dom=new JSDOM('<div id="feed" class="feed feed-3"></div><div id="myListings"></div>',{url:'https://test.invalid/',runScripts:'outside-only'});
  const w=dom.window,d=w.document;
  w.localStorage.setItem('yycj_token','test');
  w.fetch=async()=>({ok:true,json:async()=>({items:[],notice:'仅展示本功能上线后可证明的版本',current:{updated_at:'2026-11-11T12:00:00'}})});
  w.eval(read('miniapp/edit-history-ui.js'));
  d.getElementById('myListings').innerHTML='<button type="button" data-history="lamp-1" data-history-scope="me">修改记录</button>';
  d.getElementById('feed').innerHTML='<article class="feed-card" data-id="lamp-1"><h3>首页卡</h3></article>';
  assert.equal(d.querySelectorAll('#feed [data-history]').length,0);
  d.querySelector('[data-history]').click();
  await new Promise(r=>setTimeout(r,30));
  const overlay=d.getElementById('yycjHistory');
  assert(overlay && !overlay.classList.contains('hidden'));
  assert(d.getElementById('feed').querySelector('.feed-card'));
  w.__yycjHistoryUI.close();
  assert(overlay.classList.contains('hidden'));
  console.log('map+history integration DOM: unified 20261112b, both features, no native, feed cards unchanged, stale callbacks still guarded PASS');
  w.close();
})().catch(e=>{console.error(e);process.exit(1)});
