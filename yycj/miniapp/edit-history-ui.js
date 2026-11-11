// Compact modification history / restore preview. No automatic restore.
(() => {
  if (window.__yycjHistoryUI) return;
  const esc = (x) => String(x ?? '').replace(/[&<>"']/g, (c) => ({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c]));
  const token = () => localStorage.getItem('yycj_token') || '';
  const FIELD = {city:'城市',title:'称呼',tags:'标签',price:'价位数字',price_text:'价位',description:'介绍',district:'区域',approx_label:'大致位置',extras:'自定义栏',hours:'营业时间',media:'图片/视频'};

  function panel() {
    let el = document.getElementById('yycjHistory');
    if (el) return el;
    el = document.createElement('div');
    el.id = 'yycjHistory';
    el.className = 'hist-overlay hidden';
    el.innerHTML = '<div class="hist-panel" role="dialog" aria-modal="true" aria-labelledby="histTitle"></div>';
    document.body.appendChild(el);
    el.addEventListener('click', (ev) => { if (ev.target === el) close(); });
    return el;
  }
  function box() { return panel().querySelector('.hist-panel'); }
  function close() { panel().classList.add('hidden'); box().innerHTML = ''; }
  function shanghai(iso) {
    if (!iso) return '时间未记录';
    const d = new Date(String(iso).endsWith('Z') || /[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z');
    if (Number.isNaN(d.getTime())) return esc(iso);
    return new Intl.DateTimeFormat('zh-CN', { timeZone:'Asia/Shanghai', year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', hour12:false }).format(d) + '（中国时区 UTC+8）';
  }
  function show(v) {
    if (v == null || v === '') return '（空）';
    if (Array.isArray(v)) {
      if (!v.length) return '（空）';
      if (v.every((x) => x && (x.file_id || x.url || x.type))) return v.map((x) => (x.type === 'video' ? '视频' : '图片') + ' ' + String(x.file_id || x.url || '').slice(0, 10)).join('、');
      if (v.every((x) => typeof x === 'string')) return v.join('、');
      return v.map((x) => (x && (x.start || x.label || JSON.stringify(x)))).join('；');
    }
    if (typeof v === 'object') {
      const parts = Object.entries(v).filter(([, val]) => val != null && val !== '').map(([k, val]) => k + '：' + val);
      return parts.join('；') || '（空）';
    }
    return String(v);
  }
  async function api(scope, path, opt) {
    const r = await fetch('/api/' + scope + '/listings/' + path, {
      method: (opt && opt.method) || 'GET',
      headers: { Authorization:'Bearer ' + token(), 'Content-Type':'application/json' },
      body: opt && opt.body ? JSON.stringify(opt.body) : undefined,
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '操作失败');
    return data;
  }
  function renderList(scope, lampId, data) {
    const items = data.items || [];
    const rows = items.map((x) => {
      const fields = (x.current_changed_labels || x.changed_labels || []).join('、') || (x.source === 'baseline' ? '当时原文' : '无字段变化');
      const media = x.media_available ? '' : (x.changed_fields || []).includes('media') || (x.current_changed_fields || []).includes('media') ? ' · 旧媒体不可用' : '';
      return `<button type="button" class="hist-item" data-hist-ver="${esc(x.version_id)}" ${x.restorable ? '' : 'data-same="1"'}>
        <strong>${esc(shanghai(x.recorded_at))}</strong>
        <span>${esc(x.source_label || '')} · ${esc(x.actor_label || '')}</span>
        <span>${esc(fields)}${esc(media)}${x.restorable ? '' : ' · 与当前相同'}</span>
      </button>`;
    }).join('') || `<p class="hist-empty muted">还没有可证明的修改记录。上线前的改动不会补造，也就不能恢复。</p>`;
    box().innerHTML = `<div class="hist-actions" style="margin-top:0"><h3 id="histTitle" style="flex:1">修改记录</h3><button type="button" class="btn hist-close">关闭</button></div>
      <p class="hist-note">${esc(data.notice || '')}</p>
      ${rows}`;
    box().querySelector('.hist-close').onclick = close;
    box().querySelectorAll('[data-hist-ver]').forEach((b) => {
      b.onclick = () => preview(scope, lampId, b.getAttribute('data-hist-ver')).catch((e) => { toast(e.message); });
    });
  }
  async function open(scope, lampId) {
    const host = panel();
    host.classList.remove('hidden');
    box().innerHTML = '<p class="muted">正在读取可证明的修改记录…</p>';
    try { renderList(scope, lampId, await api(scope, encodeURIComponent(lampId) + '/edit-history')); }
    catch (e) { box().innerHTML = `<p class="hist-empty">${esc(e.message || '读取失败')}</p><div class="hist-actions"><button type="button" class="btn hist-close">关闭</button></div>`; box().querySelector('.hist-close').onclick = close; }
  }
  async function preview(scope, lampId, versionId) {
    box().innerHTML = '<p class="muted">正在读取这一版预览…</p>';
    const data = await api(scope, encodeURIComponent(lampId) + '/edit-history/' + encodeURIComponent(versionId));
    const v = data.version || {};
    const cur = data.current || {};
    const snap = v.snapshot || {};
    const keys = v.current_changed_fields && v.current_changed_fields.length ? v.current_changed_fields : Object.keys(FIELD);
    const diffs = keys.map((k) => `<li><b>${esc(FIELD[k] || k)}</b>当前：${esc(show(cur[k]))}<br>这一版：${esc(show(snap[k]))}</li>`).join('');
    const mediaBlock = (v.can_restore_media || (v.current_changed_fields || []).includes('media'))
      ? (v.media_available
        ? '<label class="hist-check"><input type="checkbox" id="histMedia"><span>同时恢复图片/视频。老师恢复新媒体仍须按现有规则审核，不会绕过审核，也不会改有效期或上架状态。</span></label>'
        : `<p class="hist-warn">旧图片/视频已不可用，不能恢复该版本媒体，也不会清空现有媒体。可仅恢复文字和营业时间。</p>`)
      : '';
    const can = v.restorable;
    box().innerHTML = `<div class="hist-actions" style="margin-top:0"><button type="button" class="btn hist-back">返回记录</button><button type="button" class="btn hist-close">关闭</button></div>
      <h3 id="histTitle">预览后再决定是否恢复</h3>
      <p class="hist-note">${esc(shanghai(v.recorded_at))} · ${esc(v.source_label || '')} · ${esc(v.actor_label || '')}</p>
      <p class="hist-note">恢复会作为一次新的修改提交，走现有校验、权限、审核、频道同步和通知重试；不会回滚上架状态、有效期、信用、统计或发布记录。</p>
      <ul class="hist-diff">${diffs || '<li class="muted">与当前没有可恢复的字段差异</li>'}</ul>
      ${mediaBlock}
      ${can ? '<label class="hist-check"><input type="checkbox" id="histConfirm"><span>我已核对预览，确认把这一版作为一次新的修改提交。</span></label>' : '<p class="hist-note">这一版与当前资料相同，无需恢复。</p>'}
      <div class="hist-actions"><button type="button" class="btn primary" id="histRestore" ${can ? 'disabled' : 'disabled'}>确认恢复</button></div>
      <p class="hist-retry muted" id="histErr"></p>`;
    box().querySelector('.hist-close').onclick = close;
    box().querySelector('.hist-back').onclick = () => open(scope, lampId);
    const confirm = box().querySelector('#histConfirm');
    const btn = box().querySelector('#histRestore');
    const err = box().querySelector('#histErr');
    if (confirm) confirm.onchange = () => { btn.disabled = !confirm.checked; };
    btn.onclick = async () => {
      if (!confirm || !confirm.checked) return;
      btn.disabled = true;
      if (confirm) confirm.disabled = true;
      const media = box().querySelector('#histMedia');
      const key = 'restore:' + lampId + ':' + versionId;
      try {
        const body = window.__yycjEditUI
          ? window.__yycjEditUI.request({ expected_updated_at: cur.updated_at, confirm: true, include_media: !!(media && media.checked) }, key)
          : { operation_id: 'restore-' + Date.now(), expected_updated_at: cur.updated_at, confirm: true, include_media: !!(media && media.checked) };
        const result = await api(scope, encodeURIComponent(lampId) + '/edit-history/' + encodeURIComponent(versionId) + '/restore', { method:'POST', body });
        window.__yycjEditUI && window.__yycjEditUI.clear(key);
        box().innerHTML = `<h3>恢复已作为新修改提交</h3>${window.__yycjEditUI ? window.__yycjEditUI.html(result.operation, scope) : '<p>请到资料处理状态查看结果。</p>'}<div class="hist-actions"><button type="button" class="btn hist-close">关闭</button></div>`;
        box().querySelector('.hist-close').onclick = close;
      } catch (e) {
        err.textContent = e.message || '恢复失败';
        btn.disabled = false;
        if (confirm) confirm.disabled = false;
      }
    };
  }
  function toast(msg) {
    const el = document.getElementById('toast') || document.getElementById('status');
    if (!el) { alert(msg); return; }
    el.textContent = msg;
    el.classList.remove('hidden');
    setTimeout(() => el.classList.add('hidden'), 2800);
  }
  document.addEventListener('click', (ev) => {
    const b = ev.target.closest && ev.target.closest('[data-history]');
    if (!b) return;
    ev.preventDefault();
    ev.stopPropagation();
    const more = b.closest('details');
    if (more) more.removeAttribute('open');
    open(b.getAttribute('data-history-scope') || (b.closest('#adminListings, #workspaceLegacy') ? 'admin' : 'me'), b.getAttribute('data-history')).catch((e) => toast(e.message));
  });
  window.__yycjHistoryUI = { open, close };
})();
