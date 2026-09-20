/* Staff app: sign in, then four screens: Request leave, Ask the assistant, My requests, Cover requests. */
let me = null;
const NAMES = { AL: 'Annual leave', LS: 'Long service leave', PE: 'Personal / sick leave', SF: 'Personal / sick leave', PN: 'Personal leave (non-cumulative)', TP: 'Time in lieu (public holidays)', TO: 'Time in lieu (overtime)', AD: 'Accrued days off', PC: 'Professional development', PD: 'Professional development', OS: 'Overseas professional development', PU: 'Purchased leave', OL: 'On-call leave' };
const shiftHours = () => (me && me.shift_hours) || 8;
const asShifts = h => `${Math.round(h / shiftHours())} shift${Math.round(h / shiftHours()) === 1 ? '' : 's'}`;

/* ---------- sign in ---------- */
$('#login-form').addEventListener('submit', async e => {
  e.preventDefault(); const btn = e.target.querySelector('button'); btn.disabled = true; $('#l-error').classList.add('hidden');
  try {
    const r = await api('/api/auth/login', { method: 'POST', body: JSON.stringify({ username: $('#l-user').value.trim(), password: $('#l-pass').value }) });
    session.set(r);
    if (r.role === 'manager') return location.href = '/manager';
    if (r.role === 'admin') return location.href = '/manager';
    await enterApp();
  } catch (err) { $('#l-error').textContent = err.message === 'unknown user' ? 'No staff member with that ID.' : err.message; $('#l-error').classList.remove('hidden'); }
  btn.disabled = false;
});
$('#signout').onclick = () => { session.clear(); location.reload(); };
$$('.sidenav [data-screen]').forEach(b => b.onclick = () => show(b.dataset.screen));
function show(name) { $$('.sidenav [data-screen]').forEach(b => b.classList.toggle('active', b.dataset.screen === name)); $$('.screen').forEach(s => s.classList.toggle('active', s.id === 's-' + name)); if (name === 'mine') renderMine(); if (name === 'cover') renderCover(); if (name === 'assistant') loadConvs(); }

async function renderCover() {
  const rows = await api(`/api/me/${session.get().user}/cover`);
  $('#n-cover').textContent = rows.filter(r => r.status === 'proposed').length;
  const tierName = t => ({ ordinary: 'ordinary hours', redeploy: 'from another ward', casual: 'casual pool', overtime: 'overtime', agency: 'agency' }[t] || t);
  $('#cover').innerHTML = rows.length ? `<table><tr><th>Date</th><th>Shift</th><th>Basis</th><th>Covering for</th><th>Status</th><th></th></tr>${rows.map(a => `<tr><td>${fmtD(a.date)}</td><td>${SHIFT[a.category] || a.category} · ${a.unit} · ${a.hours} h</td><td><span class="tier">${tierName(a.tier)}</span></td><td class="small muted">a colleague's ${esc((a.leave_type || '').toLowerCase().replace('_', ' '))} (${a.request_id})</td><td>${a.status === 'accepted' ? '<span class="pill g">accepted</span>' : a.status === 'declined_by_staff' ? '<span class="pill r">declined</span>' : a.status === 'confirmed' ? '<span class="pill b">confirmed by manager</span>' : '<span class="pill a">awaiting your answer</span>'}</td><td>${a.status === 'proposed' ? `<button class="btn sm ok cv-yes" data-id="${a.id}">Accept</button> <button class="btn sm cv-no" data-id="${a.id}">Decline</button>` : ''}</td></tr>`).join('')}</table>` : '<div class="empty">No cover requests for you.</div>';
  $$('#cover .cv-yes').forEach(b => b.onclick = async () => { await api(`/api/cover/${b.dataset.id}/respond`, { method: 'POST', body: JSON.stringify({ accept: true }) }); toast('Accepted. Thank you.'); renderCover(); });
  $$('#cover .cv-no').forEach(b => b.onclick = async () => { const n = await ui.prompt('Decline this cover shift', 'Optional: tell your manager why (e.g. childcare, second job).', { placeholder: 'reason', okLabel: 'Decline', cls: 'danger' }); if (n === null) return; await api(`/api/cover/${b.dataset.id}/respond`, { method: 'POST', body: JSON.stringify({ accept: false, note: n }) }); toast('Declined. The engine is finding someone else.'); renderCover(); });
}

async function enterApp() {
  const s = session.get();
  try { me = await api('/api/me/' + s.user); } catch (e) { session.clear(); return; }
  $('#login').classList.add('hidden'); $('#app').classList.remove('hidden');
  $('#who').textContent = `${s.user} · ${me.position}`;
  renderSide();
  $('#f-type').innerHTML = me.leave_types.map(t => `<option value="${t.code}">${esc(t.label)}</option>`).join('');
  const t = new Date(me.today + 'T00:00:00'); const a = new Date(t); a.setDate(a.getDate() + 28); const b = new Date(a); b.setDate(b.getDate() + 4);
  $('#f-start').value = a.toISOString().slice(0, 10); $('#f-end').value = b.toISOString().slice(0, 10); summary();
  loadBestWindows();
  if (!$('#msgs').children.length) addMsg('ai', `Hello. You have ${me.balances.AL ? me.balances.AL.remaining_hours : 0} hours of annual leave (about ${asShifts(me.balances.AL ? me.balances.AL.remaining_hours : 0)}). Ask me to check dates, book leave or find a good time for a break.`);
}
function renderSide() {
  const order = ['AL', 'PE', 'SF', 'LS', 'AD', 'TP', 'TO', 'PC', 'PD', 'PU'];
  const keys = Object.keys(me.balances).sort((a, b) => (order.indexOf(a) + 1 || 99) - (order.indexOf(b) + 1 || 99));
  $('#balances').innerHTML = keys.slice(0, 6).map(k => { const v = me.balances[k]; return `<div class="bal ${v.excess ? 'excess' : ''}"><div class="row"><span>${NAMES[k] || k}</span><b>${asShifts(v.remaining_hours)} <span class="muted small">(${v.remaining_hours} h)</span></b></div><div class="bar"><i style="width:${Math.min(100, v.remaining_hours / 400 * 100)}%"></i></div>${k === 'AL' && me.accrual_hours_per_year ? `<div class="small muted" style="margin-top:4px">Accrues about ${(me.accrual_hours_per_year / 26).toFixed(1)} h a fortnight${v.anniversary ? ` · anniversary ${fmtD(v.anniversary)}` : ''}</div>` : ''}${v.excess ? '<div class="note">Above two years of leave: your requests are given priority.</div>' : ''}</div>`; }).join('') || '<p class="muted small">No balances on file.</p>';
  $('#bal-asat').textContent = me.balances.AL ? `as at ${fmtD(me.balances.AL.as_at)} · ${shiftHours()} h per shift` : '';
  $('#details').innerHTML = [['Position', me.position], ['Ward', `${(me.unit_type || '').replace('Synthetic ', '')} ${me.primary_unit || ''}`], ['Employment', me.job_type.toLowerCase()], ['Contract', `${me.contract_hours} h per fortnight`], ['Agreement', me.agreement], ['Contract ends', me.contract_end ? fmtD(me.contract_end) : 'ongoing']].map(([k, v]) => `<div class="kv"><span>${k}</span><b>${esc(v)}</b></div>`).join('');
  $('#shifts').innerHTML = me.next_shifts.slice(0, 6).map(x => `<div class="kv"><span>${fmtD(x.date)}</span><b>${SHIFT[x.category] || x.category} ${x.start}–${x.end} · ${x.unit}</b></div>`).join('') || '<p class="muted small">No roster published for the coming days.</p>';
  $('#n-mine').textContent = (me.requests || []).length;
  api(`/api/me/${session.get().user}/cover`).then(rows => { $('#n-cover').textContent = rows.filter(r => r.status === 'proposed').length; }).catch(() => {});
}
async function refreshMe() { try { me = await api('/api/me/' + session.get().user); renderSide(); } catch (e) { } }
(function boot() { const s = session.get(); if (s && s.role === 'staff') enterApp(); })();

/* ---------- best windows (suggestions engine) ---------- */
async function loadBestWindows() {
  const box = $('#bestwin'); const days = Number($('#bw-len').value);
  box.innerHTML = '<p class="muted small">Checking every week in the next 12 weeks against the rules…</p>';
  try {
    const r = await api(`/api/suggestions/${session.get().user}?weeks=12&days=${days}&leave_type=ANNUAL`);
    const ws = (r.windows || []).slice(0, 3);
    if (!ws.length) {
      const br = r.blocking_rules || [];
      box.innerHTML = `<p class="small">No ${days}-day block in the next 12 weeks can be approved${br.length ? ': ' + br.map(b => `<a class="code warn" href="/policy/${b.code}" target="_blank">${b.code}</a> ${esc(b.meaning)}`).join('; ') : ''}.</p>${r.split_plan ? `<p class="small" style="margin-top:8px"><b>Try ${r.split_plan.blocks.length} shorter blocks instead:</b></p>${r.split_plan.blocks.map(w => `<div class="win click" data-s="${w.start}" data-e="${w.end}"><b>${fmtD(w.start)} – ${fmtD(w.end)}</b><span>${esc(w.label)}</span></div>`).join('')}` : ''}`;
    } else {
      box.innerHTML = ws.map((w, i) => `<div class="win click" data-s="${w.start}" data-e="${w.end}" title="Use these dates"><b>${fmtD(w.start)} – ${fmtD(w.end)}</b><span>${esc(w.label)} · ${w.hours} h${w.public_holidays ? ` · ${w.public_holidays} public holiday${w.public_holidays > 1 ? 's' : ''} free` : ''}</span>${i === 0 ? '<em>best</em>' : ''}</div>`).join('') + `<p class="tiny muted" style="margin-top:8px">Every window was checked by the full rulebook. Click one to load it into the form.</p>`;
    }
    (r.nudges || []).slice(0, 1).forEach(n => { box.insertAdjacentHTML('beforeend', `<p class="small" style="margin-top:8px;color:var(--warn)">${esc(typeof n === 'string' ? n : (n.text || n.title || ''))}</p>`); });
    $$('#bestwin .win').forEach(el => el.onclick = () => { $('#f-type').value = 'ANNUAL'; $('#f-start').value = el.dataset.s; $('#f-end').value = el.dataset.e; summary(); $('#f-go').click(); });
  } catch (e) { box.innerHTML = `<p class="small muted">Could not load suggestions: ${esc(e.message)}</p>`; }
}
$('#bw-len').onchange = loadBestWindows;

/* ---------- request leave ---------- */
function summary() { const s = $('#f-start').value, e = $('#f-end').value; if (!s || !e) return; const days = Math.round((new Date(e) - new Date(s)) / 864e5) + 1; $('#f-summary').textContent = days > 0 ? `${days} calendar day${days > 1 ? 's' : ''}` : 'The last day must be on or after the first day.'; }
$('#f-start').onchange = $('#f-end').onchange = summary;
$('#leave-form').addEventListener('submit', async e => {
  e.preventDefault(); const btn = $('#f-go'); btn.disabled = true; btn.textContent = 'Checking rules and cover…';
  try { const d = await api('/api/check', { method: 'POST', body: JSON.stringify({ employee_id: session.get().user, leave_type: $('#f-type').value, start: $('#f-start').value, end: $('#f-end').value, note: $('#f-note').value }) }); showResult(d, false); }
  catch (err) { toast(err.message); } finally { btn.disabled = false; btn.textContent = 'Check these dates'; }
});
function bannerFor(d, submitted) {
  const s = d.summary || {};
  if (d.outcome === 'DECLINED') return ['r', '✕', 'These dates cannot be approved as requested', 'Here is exactly why, and what you can do instead.'];
  if (d.outcome === 'NEEDS_APPROVAL') return ['a', '!', 'Possible, your manager needs to confirm', 'The rules allow it with one manager decision, shown below.'];
  if (d.outcome === 'ACCEPTED') return ['g', '✓', 'Recorded. Your manager has been notified', 'Cover recommendations were sent with it. Get well soon.'];
  return ['g', '✓', submitted ? 'Sent to your manager to confirm' : 'These dates work', s.shifts ? `${s.not_needed || 0} of ${s.shifts} shifts need no cover and ${s.covered || 0} already have cover found.` : 'You have no rostered shifts in this period.'];
}
function showResult(d, submitted) {
  const box = $('#result'); box.classList.remove('hidden'); const s = d.summary || {};
  const [cls, ic, title, sub] = bannerFor(d, submitted);
  $('#r-banner').innerHTML = `<div class="banner ${cls}"><div class="ic">${ic}</div><div><h3>${title}</h3><p>${sub}</p></div></div>`;
  // the one reason to read first, then any other rule that needs a decision
  const p = d.primary_reason; const others = (d.rules || []).filter(r => !r.passed && r.public && (!p || r.code !== p.code));
  $('#r-primary').innerHTML = p ? `<div class="primary ${p.severity === 'hard' ? 'bad' : 'warn'}"><a class="code ${p.severity === 'hard' ? 'bad' : 'warn'}" href="/policy/${p.code}" target="_blank" title="${esc(p.name)} – open the policy">${p.code}</a><div><b>${esc(p.name)}</b><p>${esc(p.text)}</p></div></div>${others.length ? `<div class="reasons" style="margin-top:8px">${others.map(r => `<div class="reason"><a class="code ${r.severity === 'hard' ? 'bad' : 'warn'}" href="/policy/${r.code}" target="_blank">${r.code}</a><span>${esc(r.public)}</span></div>`).join('')}</div>` : ''}` : '';
  const after = d.balance_after;
  $('#r-facts').innerHTML = [[`${d.requested_hours} h`, `of leave (${asShifts(d.requested_hours)})`], [after == null ? '–' : `${after} h`, after == null ? 'balance after' : `left after this (${asShifts(Math.max(0, after))})`], [s.shifts || 0, 'rostered shifts'], [`${s.not_needed || 0} / ${s.covered || 0} / ${s.uncovered || 0}`, 'no cover needed / cover found / no cover']].map(([v, l], i) => `<div class="fact ${(i === 3 && s.uncovered) || (i === 1 && after != null && after < 0) ? 'bad' : ''}"><b>${v}</b><span>${l}</span></div>`).join('');
  $('#r-strip').innerHTML = strip(d, d.start, d.end, me.public_holidays);
  $('#r-shifts').innerHTML = gapDetails(d);
  // good to know: informational notes only
  const info = (d.rules || []).filter(r => r.passed && r.severity === 'info' && r.public);
  $('#r-reasons').innerHTML = info.length ? info.map(r => `<div class="reason"><a class="code info" href="/policy/${r.code}" target="_blank">${r.code}</a><span>${esc(r.public)}</span></div>`).join('') : '<div class="reason"><span class="code info">OK</span><span>Nothing else to note.</span></div>';
  $('#r-more').hidden = !info.length; $('#r-more').open = false;
  const ow = $('#r-options-wrap'), oc = $('#r-options'), ot = $('#r-options-title'); ow.classList.add('hidden'); oc.innerHTML = '';
  if (d.balance_options) {
    const o = d.balance_options; ot.textContent = `Your balance covers ${o.projected_hours} of the ${o.requested_hours} hours. Ways to still take this time off`;
    oc.innerHTML = o.options.map(x => `<div class="opt"><b>${esc(x.title)}</b><p>${esc(x.detail)}</p>${x.kind === 'shorter' ? `<div class="act"><button class="btn sm opt-short" data-s="${x.start}" data-e="${x.end}">Use these dates</button></div>` : ''}${x.leave_type ? `<div class="act"><button class="btn sm opt-type" data-t="${x.leave_type}">Check as this type</button></div>` : ''}</div>`).join('');
    ow.classList.remove('hidden');
  } else if ((d.alternatives || []).length) {
    ot.textContent = d.outcome === 'DECLINED' ? 'Nearby dates that would work' : 'Nearby dates that need no manager decision';
    oc.innerHTML = d.alternatives.map(a => altCard(a)).join('');
    ow.classList.remove('hidden');
  }
  const act = $('#r-actions');
  const toChat = `<button class="btn" id="to-chat" title="Open the assistant with this check as its memory">Continue with the assistant</button>`;
  if (submitted) act.innerHTML = `<span class="small muted">Request <b>${d.request_id}</b> · ${LABEL[d.status] || d.status}</span>${d.can_escalate ? `<button class="btn" id="escalate">Ask a manager to review</button>` : ''}${toChat}<button class="btn" id="go-mine">View my requests</button>`;
  else act.innerHTML = d.outcome === 'DECLINED' ? `<button class="btn primary" id="submit">Send to manager anyway</button>${toChat}<span class="small muted">Your manager sees the reasons and the options above.</span>` : `<button class="btn primary" id="submit">${d.outcome === 'ACCEPTED' ? 'Record this leave' : 'Submit request'}</button>${toChat}<span class="small muted">Checked in ${d.elapsed_ms} ms against rulebook v${d.rules_version}.</span>`;
  $('#to-chat').onclick = () => handOff(d);
  $$('#r-options .alt').forEach(el => el.onclick = async () => { $('#f-start').value = el.dataset.s; $('#f-end').value = el.dataset.e; summary(); if (submitted && d.request_id) { try { const r = await api(`/api/requests/${d.request_id}/accept-alternative`, { method: 'POST', body: JSON.stringify({ start: el.dataset.s, end: el.dataset.e }) }); toast('Submitted with the new dates'); showResult(r, true); refreshMe(); } catch (e) { toast(e.message); } } else $('#f-go').click(); });
  $$('#r-options .opt-short').forEach(b => b.onclick = () => { $('#f-start').value = b.dataset.s; $('#f-end').value = b.dataset.e; summary(); $('#f-go').click(); });
  $$('#r-options .opt-type').forEach(b => b.onclick = () => { if ([...$('#f-type').options].some(o => o.value === b.dataset.t)) { $('#f-type').value = b.dataset.t; $('#f-go').click(); } else toast('That leave type is not available for you.'); });
  $('#submit')?.addEventListener('click', async () => { try { const r = await api('/api/requests', { method: 'POST', body: JSON.stringify({ employee_id: session.get().user, leave_type: $('#f-type').value, start: d.start, end: d.end, note: $('#f-note').value }) }); if (r.outcome === 'DECLINED' && r.request_id) { const why = await ui.prompt('Send to your manager', 'Tell your manager why you need these dates (optional).', { placeholder: 'e.g. family wedding, flights already booked', okLabel: 'Send' }); if (why === null) { showResult(r, true); refreshMe(); return; } await api(`/api/requests/${r.request_id}/escalate`, { method: 'POST', body: JSON.stringify({ message: why || 'Please review; I am aware of the reasons shown.' }) }); r.status = 'ESCALATED'; r.can_escalate = false; toast('Sent to your manager for approval'); } else toast(r.outcome === 'ACCEPTED' ? 'Recorded. Your manager has been notified.' : 'Submitted'); showResult(r, true); refreshMe(); } catch (e) { toast(e.message); } });
  $('#escalate')?.addEventListener('click', async () => { const m = await ui.prompt('Ask a manager to review', 'Tell your manager why these dates matter.', { placeholder: 'e.g. school holidays, no other option', okLabel: 'Send for review' }); if (m === null) return; try { await api(`/api/requests/${d.request_id}/escalate`, { method: 'POST', body: JSON.stringify({ message: m }) }); toast('Sent for a human review'); $('#escalate').disabled = true; refreshMe(); } catch (e) { toast(e.message); } });
  $('#go-mine')?.addEventListener('click', () => show('mine'));
  box.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

/* ---------- my requests ---------- */
async function renderMine() {
  await refreshMe();
  const rs = me.requests || [];
  $('#mine').innerHTML = rs.length ? `<table><tr><th>Request</th><th>Type</th><th>Dates</th><th>Status</th><th>Manager note</th><th></th></tr>${rs.map(r => `<tr><td class="small">${r.id}</td><td>${esc(r.leave_type.replace('_', ' ').toLowerCase())}</td><td>${fmtD(r.start)} – ${fmtD(r.end)}</td><td>${pill(r.status)}</td><td class="small muted">${esc(r.manager_note || '')}</td><td>${['PENDING_MANAGER', 'DECLINED', 'ESCALATED'].includes(r.status) ? `<button class="btn sm wd" data-id="${r.id}">Withdraw</button>` : ''}</td></tr>`).join('')}</table>` : '<div class="empty">No requests yet.</div>';
  $$('#mine .wd').forEach(b => b.onclick = async () => { if (!(await ui.confirm('Withdraw request', `Withdraw request <b>${b.dataset.id}</b>? Any cover already proposed for it is released.`, 'Withdraw', 'danger'))) return; await api(`/api/requests/${b.dataset.id}/withdraw`, { method: 'POST', body: '{}' }); toast('Withdrawn'); renderMine(); });
}

/* ---------- assistant: conversations are kept per staff member ---------- */
let conv = null;   // current conversation id
const newConvId = () => 'c' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
function addMsg(who, text) { const el = document.createElement('div'); el.className = 'msg ' + who; el.innerHTML = '<div class="bubble"></div>'; el.firstChild.textContent = text; $('#msgs').appendChild(el); $('#msgs').scrollTop = 1e9; return el; }
function addCard(html) { const el = document.createElement('div'); el.className = 'msg ai'; el.innerHTML = `<div class="card-inline">${html}</div>`; $('#msgs').appendChild(el); $('#msgs').scrollTop = 1e9; return el; }
function wireCard(c) {
  $$('.use', c).forEach(el => el.onclick = () => send(`check annual leave ${el.dataset.s} to ${el.dataset.e}`));
  $$('.say', c).forEach(el => el.onclick = () => send(el.dataset.m));
  $$('.card-submit', c).forEach(b => b.onclick = async () => {
    b.disabled = true; b.textContent = 'Submitting…';
    try {
      let r = await api('/api/requests', { method: 'POST', body: JSON.stringify({ employee_id: session.get().user, leave_type: b.dataset.t, start: b.dataset.s, end: b.dataset.e, note: b.dataset.n || '', channel: 'chat' }) });
      if (r.outcome === 'DECLINED' && r.request_id && !r.duplicate) {
        const why = await ui.prompt('Send to your manager', 'Tell your manager why you need these dates (optional).', { placeholder: 'e.g. family wedding, flights already booked', okLabel: 'Send' });
        if (why !== null) { await api(`/api/requests/${r.request_id}/escalate`, { method: 'POST', body: JSON.stringify({ message: why || 'Please review; I am aware of the reasons shown.' }) }); r.status = 'ESCALATED'; r.can_escalate = false; }
      }
      const line = r.duplicate ? r.headline : (r.outcome === 'ACCEPTED' ? `Recorded as ${r.request_id}. Your manager has been notified with cover recommendations.` : r.status === 'ESCALATED' ? `Sent to your manager for approval as ${r.request_id}.` : `Submitted as ${r.request_id}. ${r.headline}`);
      const box = c.querySelector('.card-inline'); box.innerHTML = card(r); wireCard(c);
      addMsg('ai', line);
      if (conv) api('/api/chat/log', { method: 'POST', body: JSON.stringify({ employee_id: session.get().user, session_id: conv, who: 'ai', text: line, card: r }) }).catch(() => {});
      toast(r.outcome === 'ACCEPTED' ? 'Recorded' : 'Submitted'); refreshMe(); loadConvs();
    } catch (e) { toast(e.message); b.disabled = false; b.textContent = 'Try again'; }
  });
}
function card(d) {
  if (d.kind === 'withdrawn') return `<div style="display:flex;gap:10px;align-items:center">${pill('WITHDRAWN')}<span><b>${esc(d.request_id)}</b> · ${esc((d.leave_type || '').toLowerCase().replace('_', ' '))} ${fmtD(d.start)} – ${fmtD(d.end)}</span></div><p class="small muted" style="margin-top:6px">Cover proposed for it has been released.</p>`;
  if (d.kind === 'choices') return `<b>${esc(d.question)}</b><div class="options" style="margin-top:8px">${(d.options || []).map(o => `<div class="opt click say" data-m="${esc(o)}"><b>${esc(o)}</b></div>`).join('')}</div>`;
  if (d.kind === 'windows') {
    const ws = d.windows || [], sp = d.split_plan, br = d.blocking_rules || [];
    const pg = d.page; const cap = pg && pg.total > 3 ? ` <span class="small muted" style="font-weight:400">(windows ${pg.from}–${pg.to} of ${pg.total})</span>` : '';
    if (ws.length) return `<b>Windows that pass the rules${cap}</b><div class="options" style="margin-top:8px">${ws.slice(0, 3).map(w => `<div class="opt click use" data-s="${w.start}" data-e="${w.end}"><b>${fmtD(w.start)} – ${fmtD(w.end)}</b><p>${esc(w.label)} · ${w.hours} h</p></div>`).join('')}</div>${d.more ? '<p class="small muted" style="margin-top:6px">Ask for "more" to see the next ones.</p>' : ''}`;
    if (pg && pg.from > 1) return `<b>No other ${d.length_days}-day windows pass in this period</b><p class="small muted" style="margin-top:6px">All ${pg.total} that pass have been shown. Try a different length or period.</p>`;
    return `<b>No single ${d.length_days}-day block can be approved</b>${br.length ? `<div class="reasons" style="margin-top:8px">${br.map(b => `<div class="reason"><a class="code warn" href="/policy/${b.code}" target="_blank">${b.code}</a><span>${esc(b.meaning)}</span></div>`).join('')}</div>` : ''}${sp ? `<p style="margin-top:10px"><b>Recommended instead: ${sp.blocks.length} block${sp.blocks.length > 1 ? 's' : ''} of ${sp.block_days} days</b>${sp.complete ? '' : ' (covers ' + sp.total_days + ' days)'}</p><div class="options" style="margin-top:8px">${sp.blocks.map(w => `<div class="opt click use" data-s="${w.start}" data-e="${w.end}"><b>${fmtD(w.start)} – ${fmtD(w.end)}</b><p>${esc(w.label)} · ${w.hours} h</p></div>`).join('')}</div>` : '<p class="small muted" style="margin-top:8px">No shorter blocks pass either in this period.</p>'}`;
  }
  const s = d.summary || {}; const p = d.primary_reason;
  return `<div style="display:flex;gap:10px;align-items:center;margin-bottom:8px;flex-wrap:wrap">${pill(d.outcome)}<span class="small muted">${d.requested_hours ?? ''} h · ${d.balance_after != null ? `${d.balance_after} h left after` : `balance ${d.projected_balance ?? '–'} h`} · ${s.not_needed || 0} no cover needed / ${s.covered || 0} covered / ${s.uncovered || 0} no cover</span></div>${p ? `<div class="reason" style="margin-bottom:8px"><a class="code ${p.severity === 'hard' ? 'bad' : 'warn'}" href="/policy/${p.code}" target="_blank">${p.code}</a><span>${esc(p.text)}</span></div>` : ''}${d.start ? strip(d, d.start, d.end, me ? me.public_holidays : {}) : ''}${gapDetails(d)}${(d.alternatives || []).length ? `<div class="options" style="margin-top:10px">${d.alternatives.map(a => altCard(a, 'use')).join('')}</div>` : ''}${d.request_id ? `<p class="small muted" style="margin-top:6px">Request <b>${d.request_id}</b> · ${LABEL[d.status] || d.status || ''}</p>` : (d.start && d.end && d.leave_type && d.outcome ? `<div class="actions" style="margin-top:10px"><button class="btn primary sm card-submit" data-t="${d.leave_type}" data-s="${d.start}" data-e="${d.end}">${d.outcome === 'ACCEPTED' ? 'Record this leave' : d.outcome === 'DECLINED' ? 'Send to manager anyway' : 'Submit request'}</button><span class="small muted">${d.outcome === 'ACCEPTED' ? 'Your manager is notified with cover recommendations.' : 'Nothing is sent until you tap.'}</span></div>` : '')}`;
}
async function send(text) {
  if (!text.trim()) return;
  if (!conv) { conv = newConvId(); $('#conv-title').textContent = text.slice(0, 60); }
  addMsg('user', text);
  const w = addMsg('ai', ''); const b = w.firstChild; b.classList.add('working'); b.innerHTML = 'Working…<div class="steps"></div>'; const steps = b.querySelector('.steps'); const t0 = Date.now();
  const tick = setInterval(() => { b.firstChild.textContent = `Working… ${((Date.now() - t0) / 1000).toFixed(0)}s`; }, 1000);
  try {
    const r = await chatStream(session.get().user, conv, text, s => { $$('div', steps).forEach(x => x.classList.remove('now')); const d = document.createElement('div'); d.className = 'now'; d.textContent = s; steps.appendChild(d); $('#msgs').scrollTop = 1e9; });
    clearInterval(tick); b.classList.remove('working'); b.innerHTML = linkCodes(md(r.reply || '(no reply)'));
    $('#engine').textContent = r.engine === 'gemini' ? `${r.model} · ${r.seconds}s` : `built-in assistant · ${r.seconds}s`;
    if (r.warning) addMsg('ai', r.warning).firstChild.classList.add('working');
    if (r.decision) wireCard(addCard(card(r.decision)));
    refreshMe(); loadConvs();
  } catch (err) { clearInterval(tick); b.classList.remove('working'); b.textContent = 'The server could not be reached: ' + err.message; }
}
async function loadConvs() {
  let rows = []; try { rows = await api(`/api/me/${session.get().user}/conversations`); } catch (e) { }
  $('#convs').innerHTML = rows.length ? rows.map(c => `<div class="item ${c.conversation_id === conv ? 'sel' : ''}" data-id="${c.conversation_id}"><div class="t"><span>${esc((c.title || 'Conversation').slice(0, 48))}</span></div><div class="s">${fmtD(c.last_ts.slice(0, 10))} ${c.last_ts.slice(11, 16)} · ${Math.ceil(c.n / 2)} exchange${c.n > 2 ? 's' : ''}</div></div>`).join('') : '<div class="empty small">No conversations yet. Ask something to start one.</div>';
  $$('#convs .item').forEach(el => el.onclick = () => openConv(el.dataset.id));
  $('#chat-delete').hidden = !conv || !rows.some(c => c.conversation_id === conv);
}
async function openConv(id) {
  conv = id; const msgs = await api(`/api/me/${session.get().user}/conversations/${id}`);
  $('#msgs').innerHTML = '';
  for (const m of msgs) {
    if (m.who === 'user') addMsg('user', m.text);
    else { const el = addMsg('ai', ''); el.firstChild.innerHTML = linkCodes(md(m.text || '')); if (m.card) wireCard(addCard(card(m.card))); }
  }
  const first = msgs.find(m => m.who === 'user'); $('#conv-title').textContent = first ? first.text.slice(0, 60) : 'Conversation';
  $$('#convs .item').forEach(el => el.classList.toggle('sel', el.dataset.id === id)); $('#chat-delete').hidden = false;
  $('#msgs').scrollTop = 1e9;
}
async function handOff(d) {
  conv = newConvId();
  try { const r = await api('/api/chat/context', { method: 'POST', body: JSON.stringify({ employee_id: session.get().user, session_id: conv, decision: d }) });
    show('assistant'); $('#msgs').innerHTML = '';
    const lt = (me.leave_types.find(t => t.code === d.leave_type) || {}).label || d.leave_type;
    addMsg('user', `From the request form: ${lt} ${fmtD(d.start)} – ${fmtD(d.end)}.`);
    addMsg('ai', r.intro); wireCard(addCard(card(d)));
    $('#conv-title').textContent = `${lt} ${fmtD(d.start)} – ${fmtD(d.end)}`; $('#chat-delete').hidden = false;
    loadConvs(); $('#chat-in').placeholder = 'e.g. why is there no cover on the Thursday?'; $('#chat-in').focus();
  } catch (e) { toast(e.message); }
}
function newConv() { conv = null; $('#msgs').innerHTML = ''; $('#conv-title').textContent = 'New conversation'; $('#chat-delete').hidden = true; $$('#convs .item').forEach(el => el.classList.remove('sel')); addMsg('ai', 'What would you like to check?'); $('#chat-in').focus(); }
$('#chat-new').onclick = newConv;
$('#chat-delete').onclick = async () => { if (!conv) return; if (!(await ui.confirm('Delete conversation', 'Delete this conversation from your history? Requests you submitted are kept.', 'Delete', 'danger'))) return; await api(`/api/me/${session.get().user}/conversations/${conv}`, { method: 'DELETE' }); newConv(); loadConvs(); };
$('#chat-form').onsubmit = e => { e.preventDefault(); const v = $('#chat-in').value; $('#chat-in').value = ''; send(v); };
$$('#chat-chips .chip').forEach(c => c.onclick = () => send(c.textContent));
