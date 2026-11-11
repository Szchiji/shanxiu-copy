// Reusable private-message editor. Preview is computed by the actual server renderer, never sends.
(() => {
 const structuredClone=x=>JSON.parse(JSON.stringify(x));
 const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 function mount(box,opt){
  if(!box)return;
  let value=structuredClone(opt.value??(opt.city?null:opt.context==='approval'?{mode:'default',items:[]}:[])),timer,seq=0;
  const welcome=opt.context==='welcome',limit=welcome?12:16;
  const items=()=>welcome?value:(value?.items||[]),mode=()=>welcome?'custom':value?.mode||'inherit';
  function changed(){opt.onChange?.(structuredClone(value));preview();}
  function compactRow(b,i,editable){const disabled=!editable?' disabled':'';return `<div class="message-button-row ws-button-card" data-brow="${i}"${welcome?` data-wrow="${i}"`:''}>
   <div class="ws-button-head"><strong>按钮 ${String(i+1).padStart(2,'0')}</strong><label><input type="checkbox" data-bkey="enabled"${b.enabled!==false?' checked':''}${disabled}>启用</label><label><input type="checkbox" data-bkey="new_row"${(b.new_row??i%2===0)?' checked':''}${disabled}>另起一行</label><div class="ws-button-actions"><button class="btn small" type="button" data-bup="${i}" aria-label="上移按钮${i+1}"${!editable||!i?' disabled':''}>↑</button><button class="btn small" type="button" data-bdown="${i}" aria-label="下移按钮${i+1}"${!editable||i===items().length-1?' disabled':''}>↓</button><button class="btn small" type="button" data-bremove="${i}"${welcome?` data-wremove="${i}"`:''}${disabled}>删除</button></div></div>
   <div class="ws-button-fields"><label>按钮文字<input data-bkey="label" maxlength="32" value="${esc(b.label)}" placeholder="按钮文字"${disabled}></label><label>类型<select data-bkey="type"${disabled}><option value="webapp"${b.type==='webapp'?' selected':''}>小程序</option><option value="url"${b.type==='url'?' selected':''}>链接</option></select></label><label>目标${b.type==='webapp'?`<select data-bkey="target"${disabled}>${['home','me',...(welcome?[]:['detail'])].map(t=>`<option value="${t}"${b.target===t?' selected':''}>${({home:'首页',me:'我的',detail:'本条资料'})[t]}</option>`).join('')}</select>`:`<input data-bkey="target" value="${esc(b.target)}" placeholder="https:// 或 t.me/${welcome?'':'；{album}/{detail}'}"${disabled}>`}</label></div></div>`;}
  function render(){
   const m=mode(),editable=!opt.disabled && m==='custom';
   box.innerHTML=`<p class="muted">私聊按钮：${limit}个以内，每行最多4个；旧欢迎按钮保留原来每行2个。频道仍为纯文字链接，常驻「首页」不变。</p>`+
    (!welcome?`<label>按钮配置<select data-bmode${opt.disabled?' disabled':''}>${opt.city?`<option value="inherit"${m==='inherit'?' selected':''}>沿用全站按钮</option>`:''}<option value="default"${m==='default'?' selected':''}>兼容原有默认按钮与附加按钮开关</option><option value="custom"${m==='custom'?' selected':''}>自定义按钮（空列表＝不附按钮）</option></select></label>`:'')+
    (m==='custom'?items().map((b,i)=>opt.layout==='compact'?compactRow(b,i,editable):`<div class="message-button-row" data-brow="${i}"${welcome?` data-wrow="${i}"`:''}>
      <label><input type="checkbox" data-bkey="enabled"${b.enabled!==false?' checked':''}${!editable?' disabled':''}/>启用</label>
      <select data-bkey="type"${!editable?' disabled':''}><option value="webapp"${b.type==='webapp'?' selected':''}>小程序</option><option value="url"${b.type==='url'?' selected':''}>链接</option></select>
      <input data-bkey="label" maxlength="32" value="${esc(b.label)}" placeholder="按钮文字"${!editable?' disabled':''}/>
      ${b.type==='webapp'?`<select data-bkey="target"${!editable?' disabled':''}>${['home','me',...(welcome?[]:['detail'])].map(t=>`<option value="${t}"${b.target===t?' selected':''}>${({home:'首页',me:'我的',detail:'本条资料'})[t]}</option>`).join('')}</select>`:`<input data-bkey="target" value="${esc(b.target)}" placeholder="https:// 或 t.me/${welcome?'':'；{album}频道帖/{detail}深链'}"${!editable?' disabled':''}/>`}
      <label><input type="checkbox" data-bkey="new_row"${(b.new_row??i%2===0)?' checked':''}${!editable?' disabled':''}/>另起一行</label>
      <button class="btn" type="button" data-bup="${i}"${!editable||!i?' disabled':''}>上移</button><button class="btn" type="button" data-bdown="${i}"${!editable||i===items().length-1?' disabled':''}>下移</button><button class="btn" type="button" data-bremove="${i}"${welcome?` data-wremove="${i}"`:''}${!editable?' disabled':''}>删除</button></div>`).join('')+`<button class="btn" type="button" data-badd${welcome?' data-wadd="1"':''}${!editable||items().length>=limit?' disabled':''}>添加按钮</button>`:'')+
    `<div class="message-button-preview"${welcome?' id="shipWelcomePreview"':''}></div><p class="muted">预览不发送；无效URL/空文字/无动态目标会隐藏。保存仅影响后续消息，不自动修改历史消息。已打开小程序可能缓存10/60秒；目录设置仍下次北京时间12:00。</p>`;
   preview();
  }
  async function preview(){
   clearTimeout(timer);const current=++seq;
   timer=setTimeout(async()=>{
    try{
     const r=await fetch('/api/admin/buttons/preview',{method:'POST',headers:{Authorization:'Bearer '+(localStorage.getItem('yycj_token')||''),'Content-Type':'application/json'},body:JSON.stringify({context:opt.context,city:opt.city||null,items:welcome?items():[],config:welcome?null:(value||opt.inherited||{mode:'default',items:[]}),...(opt.getFlags?.()||{})})});
     const d=await r.json();if(current!==seq || !box.isConnected)return;
     const target=opt.previewTarget||box.querySelector('.message-button-preview');if(!target)return;
     target.innerHTML=r.ok?`<b>示例预览（与实际发送同一渲染器）</b>${(d.rows||[]).map(row=>`<div>${row.map(b=>`<span>${esc(b.label)}${b.type==='webapp'?' ↗':''}</span>`).join('')}</div>`).join('')||'<p>不附按钮</p>'}`:'<p>预览不可用，请检查权限与按钮配置</p>';
    }catch{const target=opt.previewTarget||box.querySelector('.message-button-preview');if(target)target.textContent='预览暂不可用，不影响当前已保存配置';}
   },220);
  }
  box.oninput=e=>{const row=e.target.closest('[data-brow]');if(row && e.target.dataset.bkey){const item=items()[+row.dataset.brow],key=e.target.dataset.bkey;item[key]=e.target.type==='checkbox'?e.target.checked:e.target.value;changed();}};
  box.onchange=e=>{
   if(e.target.matches('[data-bmode]')){value=e.target.value==='inherit'?null:{mode:e.target.value,items:[]};changed();render();}
   else if(e.target.dataset.bkey==='type'){const b=items()[+e.target.closest('[data-brow]').dataset.brow];b.type=e.target.value;b.target=b.type==='webapp'?'home':'';changed();render();}
  };
  box.onclick=e=>{const b=e.target.closest('[data-badd],[data-bremove],[data-bup],[data-bdown]');if(!b||b.disabled)return;e.preventDefault();
   const list=items();if(b.hasAttribute('data-badd')){if(list.length>=limit)return;list.push({type:'webapp',label:'新按钮',target:'home',enabled:true,new_row:list.length%2===0});}
   else if(b.dataset.bremove!==undefined)list.splice(+b.dataset.bremove,1);
   else{const i=+(b.dataset.bup??b.dataset.bdown),j=i+(b.dataset.bup!==undefined?-1:1);if(j<0||j>=list.length)return;[list[i],list[j]]=[list[j],list[i]];}
   changed();render();
  };
  box._buttonEditor={value:()=>structuredClone(value),preview};render();return box._buttonEditor;
 }
 window.yycjButtons={mount};
})();
