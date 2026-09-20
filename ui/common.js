/* Shared helpers: session (sessionStorage, cleared when the tab closes), API calls, formatting. */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const session = {
  get() { try { return JSON.parse(sessionStorage.getItem('lc_session') || 'null'); } catch (e) { return null; } },
  set(v) { sessionStorage.setItem('lc_session', JSON.stringify(v)); },
  clear() { sessionStorage.removeItem('lc_session'); },
};
async function api(path, opts = {}) {
  const s = session.get();
  const headers = { 'X-Auth-Token': s ? s.token : '' };
  if (!(opts.body instanceof FormData)) headers['Content-Type'] = 'application/json';
  const r = await fetch(path, { ...opts, headers: { ...headers, ...(opts.headers || {}) } });
  if (!r.ok) { let m = r.statusText; try { m = (await r.json()).detail || m; } catch (e) { } throw new Error(m); }
  return r.json();
}
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const fmtD = s => { const d = new Date(s + 'T00:00:00'); return d.toLocaleDateString('en-AU', { weekday: 'short', day: 'numeric', month: 'short' }); };
const SHIFT = { AM: 'AM', PM: 'PM', NIGHT: 'Night', LONG_DAY: '12h', DAY: 'Day', ONCALL_24H: 'On call' };
const LABEL = { ACCEPTED: 'Recorded', APPROVED: 'Approved', RECOMMEND_APPROVE: 'Ready to approve', NEEDS_APPROVAL: 'Manager to confirm', DECLINED: 'Cannot approve as requested', PENDING_MANAGER: 'With manager', ESCALATED: 'Escalated', DECLINED_BY_MANAGER: 'Declined by manager', CHANGES_REQUESTED: 'Changes requested', WITHDRAWN: 'Withdrawn' };
const PILL = { ACCEPTED: 'g', APPROVED: 'g', RECOMMEND_APPROVE: 'g', NEEDS_APPROVAL: 'a', PENDING_MANAGER: 'a', ESCALATED: 'a', CHANGES_REQUESTED: 'a', DECLINED: 'r', DECLINED_BY_MANAGER: 'r', WITHDRAWN: 'n' };
const pill = s => `<span class="pill ${PILL[s] || 'n'}">${esc(LABEL[s] || s)}</span>`;
function toast(m, ms = 2600) { const t = $('#toast'); t.textContent = m; t.classList.remove('hidden'); clearTimeout(t._h); t._h = setTimeout(() => t.classList.add('hidden'), ms); }
function strip(d, start, end, holidays = {}) {
  const byDate = Object.fromEntries((d.chart?.days || []).map(x => [x.date, x.shifts]));
  const out = []; let cur = new Date(start + 'T00:00:00'); const last = new Date(end + 'T00:00:00'); let n = 0;
  while (cur <= last && n < 42) {
    const k = cur.toISOString().slice(0, 10); const shifts = byDate[k] || [];
    const cells = shifts.length ? shifts.map(s => `<div class="c ${s.status} ${s.forecast ? 'forecast' : ''}" title="${s.unit} ${s.shift}: ${s.status === 'not_needed' ? 'ward stays at requirement' : s.status === 'covered' ? 'cover found' : 'no cover found'}">${SHIFT[s.shift] || s.shift}</div>`).join('') : '<div class="c off">off</div>';
    out.push(`<div class="day"><div class="d ${holidays[k] ? 'ph' : ''}" title="${holidays[k] || ''}">${fmtD(k)}</div>${cells}</div>`);
    cur.setDate(cur.getDate() + 1); n++;
  }
  return `<div class="timeline">${out.join('')}</div><div class="legend"><span><i style="background:#d1fae5"></i>no cover needed</span><span><i style="background:#eff6ff"></i>cover found</span><span><i style="background:#fee2e2"></i>no cover</span><span><i></i>not rostered</span><span>hatched = beyond published roster</span></div>`;
}
function codes(d) {
  const sev = Object.fromEntries((d.rules || []).map(r => [r.code, r.severity]));
  return (d.breached_codes || []).map(c => `<a class="code ${sev[c] === 'hard' ? 'bad' : sev[c] === 'soft' ? 'warn' : 'info'}" href="/policy/${c}" target="_blank" title="Open the policy behind ${c}">${c}</a>`).join('');
}
function explain(d) {
  const lines = (d.public_reasons || []).map(t => t.replace(/^([A-Z]{2,3}-\d{3}) - /, '$1: '));
  return lines.length ? lines.join('\n') : 'All rules passed.';
}
/* streaming chat: shows what the assistant is doing while it works */
async function chatStream(employeeId, sessionId, message, onStatus) {
  const s = session.get();
  const r = await fetch('/api/chat/stream', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Auth-Token': s ? s.token : '' }, body: JSON.stringify({ employee_id: employeeId, session_id: sessionId, message }) });
  if (!r.ok) throw new Error(r.statusText);
  const reader = r.body.getReader(); const dec = new TextDecoder(); let buf = ''; let result = null;
  while (true) {
    const { value, done } = await reader.read(); if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf('\n\n')) >= 0) {
      const chunk = buf.slice(0, i); buf = buf.slice(i + 2);
      const ev = (chunk.match(/^event: (.*)$/m) || [])[1]; const data = (chunk.match(/^data: (.*)$/m) || [])[1];
      if (!ev || !data) continue;
      const obj = JSON.parse(data);
      if (ev === 'status') onStatus(obj.text); else if (ev === 'result') result = obj;
    }
  }
  return result;
}

/* tiny safe markdown: **bold**, *italic*, `code`, "- " bullets, "1. " lists, line breaks */
function md(text) {
  let t = esc(text || '');
  t = t.replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').replace(/(^|[^*])\*(?!\*)([^*\n]+)\*/g, '$1<i>$2</i>').replace(/`([^`]+)`/g, '<code>$1</code>');
  const lines = t.split('\n'); let out = '', list = null;
  const close = () => { if (list) { out += `</${list}>`; list = null; } };
  for (const ln of lines) {
    const b = ln.match(/^\s*[-*•]\s+(.*)$/), n = ln.match(/^\s*\d+[.)]\s+(.*)$/);
    if (b) { if (list !== 'ul') { close(); out += '<ul>'; list = 'ul'; } out += `<li>${b[1]}</li>`; }
    else if (n) { if (list !== 'ol') { close(); out += '<ol>'; list = 'ol'; } out += `<li>${n[1]}</li>`; }
    else { close(); out += ln.trim() ? `<p>${ln}</p>` : ''; }
  }
  close(); return out;
}

/* reasons list with rule codes (staff view uses public text, manager view manager text) */
function reasons(d, manager = false) {
  const rows = (d.rules || []).filter(r => (!r.passed || r.severity === 'info') && (manager ? (r.manager || r.public) : r.public));
  if (!rows.length) return '<div class="reason"><span class="code info">OK</span><span>All rules passed.</span></div>';
  return rows.map(r => `<div class="reason"><a class="code ${r.severity === 'hard' && !r.passed ? 'bad' : r.severity === 'soft' && !r.passed ? 'warn' : 'info'}" href="/policy/${r.code}" target="_blank" title="${esc(r.name)} – open the policy">${r.code}</a><span>${esc(manager ? (r.manager || r.public) : r.public)}</span></div>`).join('');
}


/* ---------- themed dialogs (replace alert / prompt / confirm) ---------- */
const ui = (() => {
  function mount() {
    let m = document.getElementById('ui-modal');
    if (m) return m;
    m = document.createElement('div'); m.id = 'ui-modal'; m.className = 'modal hidden';
    m.innerHTML = '<div class="modal-card"><div class="modal-h"><h2 id="um-title"></h2><button class="link" id="um-x">Close</button></div><div class="modal-b" id="um-body"></div><div class="modal-f" id="um-foot"></div></div>';
    document.body.appendChild(m); return m;
  }
  function open({ title, body, buttons }) {
    const m = mount(); $('#um-title', m).textContent = title; $('#um-body', m).innerHTML = body;
    return new Promise(resolve => {
      const foot = $('#um-foot', m); foot.innerHTML = '';
      const close = v => { m.classList.add('hidden'); document.removeEventListener('keydown', onKey); resolve(v); };
      const onKey = e => { if (e.key === 'Escape') close(null); };
      buttons.forEach(b => { const el = document.createElement('button'); el.className = 'btn ' + (b.cls || ''); el.textContent = b.label; el.onclick = () => { const v = b.value ? b.value() : b.result; if (v === undefined) return; close(v); }; foot.appendChild(el); });
      $('#um-x', m).onclick = () => close(null); document.addEventListener('keydown', onKey);
      m.classList.remove('hidden'); const first = m.querySelector('textarea,input'); if (first) first.focus(); else foot.querySelector('button.primary,button.ok,button.danger,button')?.focus();
    });
  }
  return {
    info: (title, html) => open({ title, body: html, buttons: [{ label: 'Close', cls: 'primary', result: true }] }),
    confirm: (title, html, okLabel = 'Confirm', cls = 'primary') => open({ title, body: html, buttons: [{ label: 'Cancel', result: false }, { label: okLabel, cls, result: true }] }),
    prompt: (title, html, { placeholder = '', required = false, okLabel = 'Continue', cls = 'primary', rows = 3 } = {}) => open({ title, body: `${html ? `<p class="small" style="margin-bottom:10px">${html}</p>` : ''}<textarea id="um-input" rows="${rows}" placeholder="${esc(placeholder)}"></textarea>${required ? '<p class="small muted" style="margin-top:6px">Required.</p>' : ''}`, buttons: [{ label: 'Cancel', result: null }, { label: okLabel, cls, value: () => { const v = $('#um-input').value.trim(); if (required && !v) { $('#um-input').focus(); $('#um-input').style.borderColor = 'var(--bad)'; return undefined; } return v; } }] }),
  };
})();

/* turn rule codes inside free text (e.g. "COV-001: ...") into policy links */
function linkCodes(html) { return String(html).replace(/\b([A-Z]{2,3}-\d{3})\b(?![^<]*<\/a>)/g, '<a class="code-inline" href="/policy/$1" target="_blank">$1</a>'); }

/* ---------- shared renderers for a decision (form result and chat card) ---------- */
const TIER_WORDS = { ordinary: 'a colleague at ordinary hours', redeploy: 'a colleague moved from another ward', casual: 'the casual pool', overtime: 'a colleague on overtime (manager to authorise)', agency: 'agency staff' };
function gapDetails(d) {
  const days = d.chart?.days || []; const rows = [];
  for (const day of days) for (const s of day.shifts) {
    if (s.status === 'not_needed') continue;
    const where = `${fmtD(day.date)} · ${SHIFT[s.shift] || s.shift} · ${s.unit}`;
    if (s.status === 'covered') rows.push(`<div class="shift ok"><span class="dot"></span><div><b>${where}</b><p>Ward would be at ${s.remaining} of ${s.required} required; cover found from ${TIER_WORDS[s.tier] || 'a colleague'}.</p></div></div>`);
    else {
      const w = s.why || {}; const n = w.considered || 0; const parts = Object.entries(w).filter(([k]) => k !== 'considered').map(([k, v]) => `${v} ${k}`);
      rows.push(`<div class="shift bad"><span class="dot"></span><div><b>${where}</b><p>Ward would be at ${s.remaining} of ${s.required} required and no cover was found. ${n ? `${n} colleague${n > 1 ? 's' : ''} checked: ${parts.join('; ')}.` : 'Nobody with a suitable role is attached to this ward.'}${s.forecast ? ' Roster not published yet for this day.' : ''}</p></div></div>`);
    }
  }
  return rows.length ? `<div class="shifts">${rows.join('')}</div>` : '';
}
function altCard(a, cls = 'alt') {
  return `<div class="opt click ${cls}" data-s="${a.start}" data-e="${a.end}"><b>${fmtD(a.start)} – ${fmtD(a.end)}</b><p>${esc(a.label)}${a.fit != null ? ` · fit ${a.fit}%` : ''}</p>${(a.why || []).length ? `<ul class="why">${a.why.map(w => `<li>${esc(w)}</li>`).join('')}</ul>` : ''}</div>`;
}
