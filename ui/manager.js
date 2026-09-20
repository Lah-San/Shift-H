/* Manager: inbox with one-click recommendation approval, ward coverage, fairness, notifications. */
const s = session.get();
if (!s || !['manager', 'admin'].includes(s.role)) location.href = '/';
$('#who').textContent = `${s.user} · ${s.role === 'admin' ? 'super user' : s.role}`; $('#brand-sub').textContent = s.role === 'admin' ? 'WA Health · Console' : 'WA Health · Manager';
$('#signout').onclick = () => { session.clear(); location.href = '/'; };
let selected = null;
const SCREENS = { inbox: loadInbox, coverage: initCov, simulate: initSim, fairness: loadEquity, notes: () => { loadNotes(); loadOutbox(); } };
function go(name) {
  if (!$(`.sidenav [data-screen="${name}"]`)) name = 'inbox';
  $$('.sidenav [data-screen]').forEach(x => x.classList.toggle('active', x.dataset.screen === name));
  $$('.screen').forEach(sc => sc.classList.toggle('active', sc.id === 's-' + name));
  if (location.hash !== '#' + name) history.replaceState(null, '', '#' + name);
  (SCREENS[name] || (() => {}))();
}
$$('.sidenav [data-screen]').forEach(b => b.onclick = () => go(b.dataset.screen));
window.addEventListener('hashchange', () => { const n = location.hash.slice(1); if (n && !$(`.sidenav [data-screen="${n}"]`).classList.contains('active')) go(n); });
$('#refresh').onclick = loadInbox;
const tierName = t => ({ ordinary: 'ordinary hours', redeploy: 'from another ward', casual: 'casual pool', overtime: 'overtime', agency: 'agency' }[t] || t);

let INBOX = [];
async function loadInbox() {
  try { INBOX = await api('/api/manager/inbox'); } catch (e) { toast(e.message); return; }
  $('#n-inbox').textContent = INBOX.length; $('.sidenav [data-screen="inbox"]').classList.toggle('urgent', INBOX.length > 0);
  try { const u = await api('/api/manager/notifications/unread'); $('#n-notes').textContent = u.unread; $('#n-notes').hidden = !u.unread; } catch (e) { }
  renderInbox();
}
function renderInbox() {
  const q = ($('#inbox-q').value || '').trim().toLowerCase();
  let rows = INBOX.filter(r => !q || r.id.toLowerCase().includes(q) || r.employee_id.toLowerCase().includes(q) || (r.unit || '').toLowerCase().includes(q) || (r.position || '').toLowerCase().includes(q));
  if ($('#inbox-sort').value === 'newest') rows = [...rows].sort((a, b) => b.created.localeCompare(a.created));
  const today = new Date().toISOString().slice(0, 10);
  $('#inbox').innerHTML = rows.map(r => `<div class="item ${selected === r.id ? 'sel' : ''}" data-id="${r.id}"><div class="t"><span>${r.employee_id} · ${esc(r.position)}</span>${pill(r.status)}</div><div class="s"><span class="code" style="cursor:default">${r.id}</span> · ${esc(r.leave_type.toLowerCase().replace('_', ' '))} · ${fmtD(r.start)} – ${fmtD(r.end)} · ${esc(r.unit || '')}${r.excess ? ' · <b style="color:var(--warn)">excess leave</b>' : ''}${r.created.slice(0, 10) === today ? ' · <b style="color:var(--primary)">new today</b>' : ''}</div><div class="s">${r.summary ? `${r.summary.covered} covered · ${r.summary.uncovered} uncovered` : ''}</div></div>`).join('') || `<div class="empty">${q ? 'No request matches that search.' : 'Nothing waiting.'}</div>`;
  $$('#inbox .item').forEach(el => el.onclick = () => open(el.dataset.id));
}
$('#inbox-q').oninput = renderInbox; $('#inbox-sort').onchange = renderInbox;
async function open(id) {
  selected = id; $$('#inbox .item').forEach(el => el.classList.toggle('sel', el.dataset.id === id));
  const r = await api('/api/manager/requests/' + id); const d = r.decision || {}; const sm = d.summary || {}; const cover = d.cover || [];
  const hard = (d.rules || []).filter(x => x.severity === 'hard' && !x.passed), soft = (d.rules || []).filter(x => x.severity === 'soft' && !x.passed);
  const actionable = ['PENDING_MANAGER', 'ESCALATED', 'ACCEPTED'].includes(r.status);
  let cls, ic, title, why;
  if (d.outcome === 'ACCEPTED') { cls = 'a'; ic = '!'; title = 'Urgent leave already recorded. Confirm the cover plan.'; why = `${sm.covered || 0} shift(s) have a recommended colleague${sm.uncovered ? `, ${sm.uncovered} still need someone` : ''}.`; }
  else if (hard.length) { cls = 'r'; ic = '✕'; title = 'Recommendation: decline as requested and offer alternatives'; why = hard.map(x => `${x.code}: ${x.manager || x.name}`).join(' '); }
  else if (!sm.uncovered && !soft.length) { cls = 'g'; ic = '✓'; title = 'Recommendation: approve'; why = `${sm.not_needed || 0} of ${sm.shifts || 0} shifts need no cover; ${sm.covered || 0} covered. Estimated cover cost ${d.cost_estimate || 0} paid hours.`; }
  else if (!sm.uncovered) { cls = 'g'; ic = '✓'; title = 'Recommendation: approve, with your sign-off on the flagged points'; why = soft.map(x => `${x.code}: ${x.manager || x.name}`).join(' '); }
  else { cls = 'a'; ic = '!'; title = `Your call: ${sm.uncovered} shift(s) could not be covered automatically`; why = 'For a short absence the policy does not require backfill (MP 0100/18 s3.4). Approve and absorb, adjust the plan, or decline with written reasons.'; }
  $('#detail').innerHTML = `
    <div class="card-h"><div><h2>${r.employee_id} <span class="muted" style="font-weight:400">· ${esc(r.position)} · ${esc(r.unit || '')}</span></h2><div class="small muted">${esc(r.leave_type.replace('_', ' '))} · ${fmtD(r.start)} – ${fmtD(r.end)} · ${d.requested_hours ?? '–'} h · balance at start ${d.projected_balance ?? '–'} h${r.note ? ` · “${esc(r.note)}”` : ''}</div></div>${pill(r.status)}</div>
    <div class="card-b">
      <div class="banner ${cls}"><div class="ic">${ic}</div><div><h3>${esc(title)}</h3><p>${linkCodes(esc(why))}</p></div></div>
      ${!actionable ? `<div class="actions" style="margin-top:14px;padding-top:0;border:0"><button class="btn" id="email-decision">Email decision</button><span class="small muted">Sends the current status (${esc(LABEL[r.status] || r.status)}) to the staff member.</span></div>` : ''}
      ${actionable ? `<div class="actions" style="margin-top:14px;padding-top:0;border:0">${!hard.length ? `<button class="btn ok" id="approve">${sm.covered ? 'Approve and confirm cover' : 'Approve'}</button>` : ''}<button class="btn" id="changes">Ask for changes</button><button class="btn danger" id="decline">Decline with reasons</button><button class="btn" id="recompute">Re-run check</button><button class="btn" id="email-decision" title="Email the current status of this request to the staff member">Email decision</button></div>` : `<p class="small muted" style="margin-top:10px">Decision recorded: ${esc(r.manager_note || '—')}</p>`}
      <div class="section"><h4>Shifts in the period</h4>${strip(d, r.start, r.end)}</div>
      ${coverPanel(r, cover, actionable)}
      <div class="section"><h4>Cover plan</h4>${cover.length ? `<table><tr><th>Date</th><th>Shift</th><th>Status</th><th>Recommended</th><th>Basis</th><th>Why</th>${actionable ? '<th></th>' : ''}</tr>${cover.map((c, i) => `<tr><td>${fmtD(c.date)}</td><td>${SHIFT[c.category] || c.category} · ${c.unit} · ${c.hours} h</td><td>${c.status === 'covered' ? '<span class="pill b">cover found</span>' : c.status === 'uncovered' ? '<span class="pill r">no cover</span>' : '<span class="pill g">not needed</span>'}</td><td>${c.candidate ? `<b>${c.candidate.employee_id}</b><br><span class="small muted">${esc(c.candidate.position_name)}</span>${c.response === 'accepted' ? '<br><span class="pill g">accepted</span>' : c.response === 'declined_by_staff' ? '<br><span class="pill r">declined</span>' : c.response === 'confirmed' ? '<br><span class="pill b">confirmed</span>' : '<br><span class="pill n">awaiting reply</span>'}` : ''}</td><td>${c.candidate ? `<span class="tier">${tierName(c.candidate.tier)}${c.candidate.cost_multiplier > 1 ? ` × ${c.candidate.cost_multiplier}` : ''}</span>` : ''}</td><td class="small muted">${c.candidate ? esc(c.candidate.reasons.slice(0, 2).join('; ')) : esc(c.note || '')}</td>${actionable ? `<td>${(c.alternates || []).length ? `<select data-swap="${i}" style="padding:5px 8px;font-size:13px"><option value="">Swap…</option>${c.alternates.map(a => `<option value="${a.employee_id}">${a.employee_id} · ${tierName(a.tier)}</option>`).join('')}</select>` : ''}</td>` : ''}</tr>`).join('')}</table>` : '<p class="small muted">No rostered shifts inside the dates.</p>'}</div>
      <div class="section"><h4>Rule notes</h4><div class="reasons">${reasons(d, true)}</div></div>
      ${(d.rules || []).some(x => x.code === 'BAL-002') ? `<div class="section"><div class="reason"><span class="code info">ELMP</span><span>This person holds excess leave. <a href="#" id="elmp">Open a pre-filled Employee Leave Management Plan</a></span></div></div>` : ''}
    </div>`;
  const decide = async (action, note) => { try { await api(`/api/manager/requests/${id}/decide`, { method: 'POST', body: JSON.stringify({ action, note }) }); toast(action === 'approve' ? 'Approved. Staff member and cover colleagues notified.' : action === 'decline' ? 'Declined with written reasons.' : 'Changes requested.'); await loadInbox(); await open(id); toast('Decision recorded. Use "Email decision" to notify the staff member.', 3500); } catch (e) { toast(e.message); } };
  $('#approve')?.addEventListener('click', () => decide('approve', 'Approved as recommended.'));
  $('#changes')?.addEventListener('click', async () => { const n = await ui.prompt('Ask for changes', 'What should the staff member change? They will see this note.', { placeholder: 'e.g. could you start a day later so the Monday night is covered?', required: true, okLabel: 'Send' }); if (n) decide('request_changes', n); });
  $('#decline')?.addEventListener('click', async () => { const n = await ui.prompt('Decline with written reasons', 'ANF cl.33 requires written reasons covering the operational requirements: cover availability, cost, patient service impact, impact on other staff, leave liability.', { placeholder: 'e.g. Three of five night shifts would fall below the ward requirement (COV-001) and no cover is available within the enabled options; alternative dates offered were 3 to 7 November.', required: true, okLabel: 'Decline', cls: 'danger', rows: 5 }); if (n) decide('decline', n); });
  $('#email-decision')?.addEventListener('click', () => emailFlow(id, { kind: 'decision' }, r.employee_id));
  $('#recompute')?.addEventListener('click', async () => { await api(`/api/manager/requests/${id}/plan/recompute`, { method: 'POST', body: '{}' }); toast('Re-checked with the current rules'); open(id); });
  $$('#detail .cv-email').forEach(b => b.onclick = () => emailFlow(id, { kind: 'cover', assignment_id: b.dataset.aid }, b.dataset.eid));
  $('#cv-email-all')?.addEventListener('click', async () => { const rows = (r.assignments || []).filter(a => a.status === 'proposed'); if (!rows.length) return toast('Nobody is waiting to be asked.'); for (const a of rows) { await emailFlow(id, { kind: 'cover', assignment_id: a.id }, a.employee_id); } open(id); });
  $$('#detail [data-swap]').forEach(sel => { const c = cover[+sel.dataset.swap]; if (c && c.status === 'covered' && c.candidate) { const a = (r.assignments || []).find(x => x.date === c.date && x.employee_id === c.candidate.employee_id); if (a) { const b = document.createElement('button'); b.className = 'btn sm'; b.textContent = 'Email'; b.title = 'Email this cover request to the colleague'; b.style.marginTop = '6px'; b.onclick = () => emailFlow(id, { kind: 'cover', assignment_id: a.id }, c.candidate.employee_id); sel.parentElement.appendChild(b); } } });
  $$('#detail [data-swap]').forEach(sel => sel.onchange = async () => { if (!sel.value) return; try { await api(`/api/manager/requests/${id}/plan/swap`, { method: 'POST', body: JSON.stringify({ line: +sel.dataset.swap, employee_id: sel.value }) }); toast('Plan updated'); open(id); } catch (e) { toast(e.message); } });
  $('#elmp')?.addEventListener('click', async ev => { ev.preventDefault(); const p = await api('/api/manager/elmp/' + r.employee_id); ui.info('Employee Leave Management Plan (MP 0100/18)', `<p class="small muted" style="margin-bottom:10px">${p.employee_id} · ${esc(p.position)} · ${esc(p.agreement)}</p>${[['Current balance', p.current_balance_hours + ' h'], ['Two entitlements', p.two_entitlements_hours + ' h'], ['Excess to clear', p.excess_hours + ' h'], ['Accrual next 12 months', p.accrual_next_12_months_hours + ' h'], ['Total to clear', p.total_to_clear_hours + ' h'], ['Suggested blocks', p.suggested_blocks.map(b => b.weeks + ' weeks').join(', ')], ['Cash-out option', p.cash_out_option ? 'yes' : 'no']].map(([k, v]) => `<div class="kv"><span>${k}</span><b>${v}</b></div>`).join('')}`); });
}
async function initCov() {
  if (!$('#unit').options.length) { const us = await api('/api/manager/units'); $('#unit').innerHTML = us.slice(0, 150).map(u => `<option value="${u.unit}">${u.unit} · ${esc((u.type || '').replace('Synthetic ', ''))} · ${u.staff} staff</option>`).join(''); $('#cov-go').onclick = loadCov; }
  loadCov();
}
async function loadCov() {
  const h = await api('/api/health'); const t = new Date(h.today + 'T00:00:00'); const e = new Date(t); e.setDate(e.getDate() + 27);
  const c = await api(`/api/manager/coverage?unit=${$('#unit').value}&start=${h.today}&end=${e.toISOString().slice(0, 10)}`);
  const keys = [...new Set(c.days.flatMap(d => d.cells.map(x => x.shift + ' · ' + x.role)))].sort();
  let html = '<div class="hd"></div>' + c.days.map(d => `<div class="hd">${d.date.slice(8)}</div>`).join('');
  for (const k of keys) { html += `<div class="rl">${k.replace('NURSE_', '').replace('MED_', 'Dr ').replace('ALLIED_PROF', 'Allied').replace('SUPPORT_OTHER', 'Support')}</div>`; for (const d of c.days) { const cell = d.cells.find(x => x.shift + ' · ' + x.role === k); if (!cell) { html += '<div class="cell"></div>'; continue; } const diff = cell.effective - cell.required; html += `<div class="cell ${diff >= 0 ? 'g' : diff === -1 ? 'a' : 'r'} ${cell.known ? '' : 'fc'}" title="${d.date} ${k}: ${cell.effective} on shift, ${cell.required} required (${cell.source})${cell.known ? '' : ' · roster ' + Math.round(cell.completeness * 100) + '% published, rest from usual patterns'}">${cell.effective}/${cell.required}</div>`; } }
  $('#heat').style.gridTemplateColumns = `130px repeat(${c.days.length}, minmax(26px,1fr))`; $('#heat').innerHTML = html;
  try { const sg = await api('/api/manager/suggestions?unit=' + $('#unit').value); $('#tips').innerHTML = `<h4>Suggestions for this ward</h4><div class="reasons">${sg.insights.map(i => `<div class="reason"><span class="code info">tip</span><span>${esc(i)}</span></div>`).join('')}</div>${sg.excess_leave_staff.length ? `<div class="section"><h4>Excess leave, plan due</h4><table><tr><th>Staff</th><th>Position</th><th class="num">Balance</th><th class="num">Excess</th></tr>${sg.excess_leave_staff.slice(0, 8).map(x => `<tr><td>${x.employee_id}</td><td>${esc(x.position)}</td><td class="num">${x.balance_hours} h</td><td class="num">${x.excess_hours} h</td></tr>`).join('')}</table></div>` : ''}`; } catch (e) { }
}
async function loadEquity() { const e = await api('/api/manager/equity'); $('#equity').innerHTML = `<div class="facts"><div class="fact"><b>${e.total_extra_shifts}</b><span>extra shifts proposed</span></div><div class="fact"><b>${e.people_with_extra_shifts}</b><span>people asked</span></div>${Object.entries(e.by_tier).map(([k, v]) => `<div class="fact"><b>${v}</b><span>${tierName(k)}</span></div>`).join('')}</div>${e.top.length ? `<table><tr><th>Staff</th><th>Position</th><th class="num">Extra shifts</th></tr>${e.top.map(t => `<tr><td>${t.employee_id}</td><td>${esc(t.position)}</td><td class="num">${t.extra_shifts}</td></tr>`).join('')}</table>` : '<div class="empty">No cover assignments yet.</div>'}`; }
async function loadNotes() { const n = await api('/api/manager/notifications'); try { await api('/api/manager/notifications/read', { method: 'POST', body: '{}' }); $('#n-notes').hidden = true; } catch (e) { } $('#notes').innerHTML = n.map(x => `<div class="item" ${x.request_id ? `data-rid="${x.request_id}" style="cursor:pointer" title="Open in the inbox"` : ''}><div class="t"><span>${x.read ? '' : '<span class="pill b">new</span> '}${esc(x.title)}</span><span class="small muted" style="font-weight:400">${x.ts.replace('T', ' ')}</span></div><div class="s">${esc(x.body)}</div></div>`).join('') || '<div class="empty">No notifications.</div>'; $$('#notes .item[data-rid]').forEach(el => el.onclick = () => { go('inbox'); $('#inbox-q').value = el.dataset.rid; renderInbox(); open(el.dataset.rid); }); }

/* ---------- ward simulator ---------- */
let simStaff = [];
async function initSim() {
  if (!$('#sim-unit').options.length) {
    const us = await api('/api/manager/units'); $('#sim-unit').innerHTML = us.slice(0, 150).map(u => `<option value="${u.unit}">${u.unit} · ${esc((u.type || '').replace('Synthetic ', ''))} · ${u.staff} staff</option>`).join('');
    const h = await api('/api/health'); const t = new Date(h.today + 'T00:00:00'); const a = new Date(t); a.setDate(a.getDate() + 7); const b = new Date(a); b.setDate(b.getDate() + 13);
    $('#sim-start').value = a.toISOString().slice(0, 10); $('#sim-end').value = b.toISOString().slice(0, 10);
    $('#sim-unit').onchange = loadSimStaff; $('#sim-add').onclick = addSimRow; $('#sim-go').onclick = runSim;
    await loadSimStaff(); addSimRow();
  }
}
async function loadSimStaff() { simStaff = await api(`/api/manager/units/${$('#sim-unit').value}/staff`); $('#sim-rows').innerHTML = ''; addSimRow(); }
function addSimRow() {
  const row = document.createElement('div'); row.className = 'form-row'; row.style.marginBottom = '8px';
  row.innerHTML = `<div class="field" style="margin:0"><select class="sim-who">${simStaff.map(x => `<option value="${x.employee_id}">${x.employee_id} · ${esc(x.position)} · ${x.shifts_next_28_days} shifts</option>`).join('')}</select></div><div class="field" style="margin:0"><input type="date" class="sim-s" value="${$('#sim-start').value}"></div><div class="field" style="margin:0"><input type="date" class="sim-e" value="${$('#sim-start').value}"></div>`;
  $('#sim-rows').appendChild(row);
}
async function runSim() {
  const absences = $$('#sim-rows .form-row').map(r => ({ employee_id: $('.sim-who', r).value, start: $('.sim-s', r).value, end: $('.sim-e', r).value })).filter(a => a.start && a.end && a.end >= a.start);
  if (!absences.length) return toast('Add at least one absence with dates.');
  $('#sim-go').disabled = true; $('#sim-status').textContent = 'Re-running the rules with these absences…';
  try {
    const r = await api('/api/manager/simulate-ward', { method: 'POST', body: JSON.stringify({ unit: $('#sim-unit').value, start: $('#sim-start').value, end: $('#sim-end').value, absences }) });
    $('#sim-status').textContent = r.note;
    const c = r.coverage; const keys = [...new Set(c.days.flatMap(d => d.cells.map(x => x.shift + ' · ' + x.role)))].sort();
    let heat = '<div class="hd"></div>' + c.days.map(d => `<div class="hd">${d.date.slice(8)}</div>`).join('');
    for (const k of keys) { heat += `<div class="rl">${k.replace('NURSE_', '').replace('MED_', 'Dr ').replace('ALLIED_PROF', 'Allied').replace('SUPPORT_OTHER', 'Support')}</div>`; for (const d of c.days) { const cell = d.cells.find(x => x.shift + ' · ' + x.role === k); if (!cell) { heat += '<div class="cell"></div>'; continue; } const diff = cell.effective - cell.required; heat += `<div class="cell ${diff >= 0 ? 'g' : diff === -1 ? 'a' : 'r'} ${cell.known ? '' : 'fc'} ${cell.newly_short ? 'new' : ''}" title="${d.date} ${k}: ${cell.effective} on shift (${cell.before} before), ${cell.required} required">${cell.effective}/${cell.required}</div>`; } }
    $('#sim-out').innerHTML = `<div class="card-h"><h2>Result</h2><span class="small muted">${r.decisions.length} absence(s) simulated</span></div><div class="card-b">
      <div class="facts"><div class="fact ${r.cells_newly_short ? 'bad' : ''}"><b>${r.cells_newly_short}</b><span>shift-role cells pushed below requirement</span></div><div class="fact"><b>${r.cells_below_before}</b><span>already below requirement before</span></div><div class="fact"><b>${r.total_cover_cost_hours}</b><span>estimated cover cost (paid hours)</span></div><div class="fact"><b>${r.decisions.filter(d => d.outcome === 'DECLINED').length}</b><span>would be declined</span></div><div class="fact"><b>${r.decisions.filter(d => d.outcome === 'NEEDS_APPROVAL').length}</b><span>need your decision</span></div></div>
      <h4>Each absence, in the order entered</h4><table><tr><th>Who</th><th>Dates</th><th>Outcome</th><th>Shifts</th><th>Rules</th></tr>${r.decisions.map(d => `<tr><td><b>${d.employee_id}</b><div class="small muted">${esc(d.position)}</div></td><td>${fmtD(d.start)} – ${fmtD(d.end)}</td><td>${pill(d.outcome)}</td><td class="small">${d.summary.not_needed} no cover needed · ${d.summary.covered} covered · ${d.summary.uncovered} uncovered</td><td>${d.breached_codes.map(x => `<a class="code" href="/policy/${x}" target="_blank">${x}</a>`).join(' ') || '<span class="muted small">none</span>'}</td></tr>`).join('')}</table>
      <h4 style="margin-top:16px">Ward coverage with these absences</h4><div class="heat" style="grid-template-columns:130px repeat(${c.days.length}, minmax(26px,1fr))">${heat}</div></div>`;
  } catch (e) { toast(e.message); $('#sim-status').textContent = ''; } finally { $('#sim-go').disabled = false; }
}


/* ---------- email a decision or a cover request; ask for the address when none is on file ---------- */
async function emailFlow(rid, opts, recipient) {
  try {
    let res = await api(`/api/manager/requests/${rid}/email`, { method: 'POST', body: JSON.stringify(opts) });
    if (res.needs_address) {
      const what = opts.kind === 'cover' ? 'the cover request' : 'the decision';
      const a = await ui.email(`Email ${what}`, `No email address is on file for <b>${esc(recipient)}</b>. Enter it to send ${what}; the message is also kept in the Emails list.`);
      if (!a) return;
      res = await api(`/api/manager/requests/${rid}/email`, { method: 'POST', body: JSON.stringify({ ...opts, to: a.email, remember: a.remember }) });
    }
    if (res.status === 'sent') toast(`Email sent to ${res.to}`, 3500);
    else await ui.info(res.status === 'failed' ? 'Email could not be sent' : 'Email saved to the outbox', `<p>${esc(res.status === 'failed' ? 'The mail server refused the message: ' + res.error : res.hint)}</p><p style="margin-top:10px"><b>To:</b> ${esc(res.to)}<br><b>Subject:</b> ${esc(res.subject)}</p><p style="margin-top:12px"><a class="btn primary" href="${res.mailto}">Open in your mail app</a></p>`);
  } catch (e) { toast(e.message); }
}
async function loadOutbox() {
  try {
    const o = await api('/api/manager/emails');
    $('#mail-status').textContent = o.configured ? 'Delivery: SMTP configured' : 'Delivery not configured: messages are kept here and can be opened in your mail app';
    $('#outbox').innerHTML = o.emails.length ? o.emails.map(m => `<div class="item"><div class="t"><span>${esc(m.subject)}</span><span class="pill ${m.status === 'sent' ? 'g' : m.status === 'failed' ? 'r' : 'a'}">${m.status}</span></div><div class="s">to ${esc(m.to_addr)} (${esc(m.employee_id)}) · ${m.ts.replace('T', ' ')} · ${esc(m.kind)}${m.error ? ` · ${esc(m.error)}` : ''} · <a href="mailto:${encodeURIComponent(m.to_addr)}?subject=${encodeURIComponent(m.subject)}&body=${encodeURIComponent(m.body)}">open in mail app</a></div></div>`).join('') : '<div class="empty small">No emails yet. They are created from a decision or from the Email button on a cover plan line.</div>';
  } catch (e) { }
}


/* ---------- the cover requests Shift-H has sent to colleagues, in one glance ---------- */
function coverPanel(r, cover, actionable) {
  const rows = (r.assignments || []).filter(a => !['cancelled'].includes(a.status));
  if (!rows.length) return '';
  const who = r.colleagues || {};
  const mail = {}; (r.emails || []).forEach(m => { if (m.kind === 'cover') mail[m.employee_id] = m; });
  const n = rows.length, acc = rows.filter(a => a.status === 'accepted' || a.status === 'confirmed').length, dec = rows.filter(a => a.status === 'declined_by_staff').length, wait = rows.filter(a => a.status === 'proposed').length;
  const tierName = t => ({ ordinary: 'ordinary hours', redeploy: 'moved from another ward', casual: 'casual pool', overtime: 'overtime', agency: 'agency' }[t] || t);
  const st = a => a.status === 'accepted' ? '<span class="pill g">accepted</span>' : a.status === 'confirmed' ? '<span class="pill g">confirmed</span>' : a.status === 'declined_by_staff' ? '<span class="pill r">declined</span>' : a.status === 'not_needed' ? '<span class="pill n">not needed</span>' : '<span class="pill a">awaiting reply</span>';
  const em = a => { const m = mail[a.employee_id]; if (!m) return '<span class="tiny muted">app notice only</span>'; return m.status === 'sent' ? `<span class="tiny" style="color:var(--ok);font-weight:700">✓ emailed ${m.to_addr}</span>` : m.status === 'outbox' ? `<span class="tiny" style="color:var(--warn);font-weight:700">in outbox · ${esc(m.to_addr)}</span>` : `<span class="tiny" style="color:var(--bad);font-weight:700">email failed</span>`; };
  return `<div class="section cover-panel">
    <div class="cp-head"><div><h4>Cover requests sent by Shift-H</h4><p class="small muted">Each colleague below was checked for rest, hours and fatigue rules, then asked in the app. They answer under <b>Cover requests</b> in their own Shift-H; you can also email them.</p></div>
      <div class="cp-stats"><span class="pill a">${wait} awaiting</span><span class="pill g">${acc} accepted</span><span class="pill r">${dec} declined</span></div></div>
    <div class="cp-list">${rows.map(a => `<div class="cp-row ${a.status === 'proposed' ? 'wait' : a.status === 'declined_by_staff' ? 'no' : 'yes'}">
      <div class="cp-who"><b>${esc(a.employee_id)}</b><span>${esc((who[a.employee_id] || {}).position || '')}${(who[a.employee_id] || {}).unit ? ' · ' + esc(who[a.employee_id].unit) : ''}</span></div>
      <div class="cp-shift"><b>${fmtD(a.date)} · ${SHIFT[a.category] || a.category}</b><span>${esc(a.unit)} · ${a.hours} h · ${tierName(a.tier)}</span></div>
      <div class="cp-state">${st(a)}<div>${em(a)}</div><div class="tiny muted">asked ${(a.created || '').replace('T', ' ').slice(0, 16)}</div></div>
      <div class="cp-act">${actionable && a.status !== 'not_needed' ? `<button class="btn sm cv-email" data-aid="${a.id}" data-eid="${a.employee_id}">${mail[a.employee_id] ? 'Email again' : 'Email'}</button>` : ''}</div>
    </div>`).join('')}</div>
    ${actionable && wait ? `<div class="actions" style="margin-top:12px;padding-top:12px"><button class="btn primary" id="cv-email-all">Email all ${wait} waiting colleague${wait > 1 ? 's' : ''}</button><span class="small muted">Asks for any address not on file, then sends (or saves to the outbox when delivery is not configured).</span></div>` : ''}
  </div>`;
}
