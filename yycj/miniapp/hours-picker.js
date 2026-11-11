/* 营业时间选择器：开始 / 结束（结束早于开始自动算跨夜「次日」），最多 3 段。
   window.yycjHours.mount(el, {max}) / .get(el) / .set(el, hours) / .parse(raw) */
(() => {
  if (window.yycjHours) return;
  const pad = (n) => String(n).padStart(2, "0");
  const FW = { "０":"0","１":"1","２":"2","３":"3","４":"4","５":"5","６":"6","７":"7","８":"8","９":"9","：":":" };
  function toMin(s) {
    s = String(s || "").replace(/[０-９：]/g, (c) => FW[c]).replace(/\s+/g, "").replace(/^(次日|翌日|凌晨)/, "");
    const m = s.match(/^(\d{1,2})(?:[:.点时](\d{1,2})?)?(半)?$/);
    if (!m) return null;
    const h = +m[1], mi = m[3] ? 30 : +(m[2] || 0);
    if (h === 24 && mi === 0) return 1440;
    if (h > 23 || mi > 59) return null;
    return h * 60 + mi;
  }
  const fmt = (m) => pad(Math.floor(m / 60)) + ":" + pad(m % 60);
  function parse(raw) {
    let items = [];
    if (Array.isArray(raw)) items = raw;
    else if (raw && typeof raw === "object") items = [raw];
    else {
      const t = String(raw || "").replace(/[０-９：～－]/g, (c) => FW[c] || (c === "～" ? "~" : "-"))
        .replace(/\s*(?:-|–|—|―|~|〜|至|到)\s*/g, "-");
      items = t.split(/[,，、;；/\n]+|\s+/).filter(Boolean);
    }
    const out = [];
    items.forEach((it) => {
      let a = null, b = null;
      if (it && typeof it === "object" && !Array.isArray(it)) { a = toMin(it.start); b = toMin(it.end); }
      else if (Array.isArray(it)) { a = toMin(it[0]); b = toMin(it[1]); }
      else if (typeof it === "string" && it.split("-").length === 2) { const p = it.split("-"); a = toMin(p[0]); b = toMin(p[1]); }
      if (a == null || b == null || a === b || a === 1440) return;
      const v = { start: fmt(a), end: fmt(b) };
      if (out.length < 3 && !out.some((x) => x.start === v.start && x.end === v.end)) out.push(v);
    });
    return out;
  }
  function opts(max, sel) {
    let h = "<option value=\"\">--</option>";
    for (let i = 0; i <= max; i += 1) h += `<option value="${pad(i)}"${sel === pad(i) ? " selected" : ""}>${pad(i)}</option>`;
    return h;
  }
  function mopts(sel) {
    return ["00", "15", "30", "45"].map((m) => `<option value="${m}"${sel === m ? " selected" : ""}>${m}</option>`).join("");
  }
  function rowHtml(v, i) {
    const [sh, sm] = (v && v.start ? v.start : ":").split(":");
    const [eh, em] = (v && v.end ? v.end : ":").split(":");
    return `<div class="hp-row" data-i="${i}">
      <select class="hp-sh" aria-label="开始 时">${opts(23, sh)}</select><b>:</b><select class="hp-sm" aria-label="开始 分">${mopts(sm || "00")}</select>
      <span class="hp-to">至</span>
      <span class="hp-next" hidden>次日</span><select class="hp-eh" aria-label="结束 时">${opts(24, eh)}</select><b>:</b><select class="hp-em" aria-label="结束 分">${mopts(em || "00")}</select>
      ${i > 0 ? '<button type="button" class="hp-del" aria-label="删除这段">×</button>' : ""}
    </div>`;
  }
  function readRows(el) {
    return [...el.querySelectorAll(".hp-row")].map((r) => {
      const sh = r.querySelector(".hp-sh").value, eh = r.querySelector(".hp-eh").value;
      if (!sh || !eh) return null;
      return { start: sh + ":" + r.querySelector(".hp-sm").value, end: eh + ":" + r.querySelector(".hp-em").value };
    });
  }
  function refresh(el) {
    const rows = [...el.querySelectorAll(".hp-row")];
    const vals = readRows(el);
    let txt = [];
    rows.forEach((r, i) => {
      const v = vals[i];
      const over = !!(v && toMin(v.end) <= toMin(v.start));
      r.querySelector(".hp-next").hidden = !over;
      if (v && v.start !== v.end) txt.push(over ? `${v.start}–次日${v.end}` : `${v.start}–${v.end}`);
    });
    const add = el.querySelector(".hp-add");
    if (add) add.hidden = rows.length >= (+el.dataset.max || 3);
    const hint = el.querySelector(".hp-hint");
    if (hint) hint.textContent = txt.length ? "首页显示：营业 " + txt.join("、") + "。默认到点自动营业，可在「我的」改成每天确认" : "不填则首页不显示营业状态。结束早于开始 = 跨夜到次日";
  }
  function render(el, list) {
    const rows = (list && list.length ? list : [null]).slice(0, +el.dataset.max || 3);
    el.querySelector(".hp-rows").innerHTML = rows.map(rowHtml).join("");
    refresh(el);
  }
  function mount(el, o) {
    if (!el || el.dataset.hp) return el;
    el.dataset.hp = "1";
    el.dataset.max = String((o && o.max) || 3);
    el.classList.add("hours-picker");
    el.innerHTML = `<label class="hp-label">营业时间 <span class="muted">选填</span></label>
      <div class="hp-rows"></div>
      <div class="hp-foot"><button type="button" class="hp-add">+ 再加一段</button><button type="button" class="hp-clear">清空</button></div>
      <p class="hp-hint muted"></p>`;
    el.addEventListener("change", () => refresh(el));
    el.addEventListener("click", (ev) => {
      if (ev.target.closest(".hp-add")) {
        const n = el.querySelectorAll(".hp-row").length;
        el.querySelector(".hp-rows").insertAdjacentHTML("beforeend", rowHtml(null, n));
        refresh(el);
      } else if (ev.target.closest(".hp-del")) {
        ev.target.closest(".hp-row").remove();
        refresh(el);
      } else if (ev.target.closest(".hp-clear")) render(el, []);
    });
    render(el, (o && o.value) || []);
    return el;
  }
  function get(el) { return el ? parse(readRows(el).filter(Boolean)) : []; }
  function set(el, raw) { if (el) render(el, parse(raw)); }
  window.yycjHours = { mount, get, set, parse };
})();
