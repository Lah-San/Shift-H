/* Rule Studio: policy discovery, rules table with a change drawer, agreements, cover options, history. */
/* Runs inside the console page (manager.html) after manager.js; the Policy and Governance screens are admin-only. */
const isAdmin = (session.get() || {}).role === 'admin';
let R = null, pending = null, disc = null;
const withR = fn => async () => { if (!R) await load(); fn(); };
Object.assign(SCREENS, { rules: withR(renderRules), agreements: withR(renderAgree), tiers: withR(renderTiers), update: () => {}, history: renderHist, quality: renderQuality });

async function load() {
  try { R = await api('/api/admin/rules'); } catch (e) { toast(e.message); return; }
  $('#n-rules').textContent = R.rules.length; renderRules();
  try { const c = await api('/api/admin/calibration'); if (c.report.chosen) $('#calib').textContent = `Calibration: known-answer tests 100%; agreement with booked leave ${(c.report.chosen.A_agreement * 100).toFixed(1)}%.`; } catch (e) { }
}

/* ---------- rules table + drawer ---------- */
function renderRules() {
  const f = ($('#filter').value || '').toLowerCase();
  const rows = R.rules.filter(r => !f || (r.id + ' ' + r.name + ' ' + r.category + ' ' + (r.description || '')).toLowerCase().includes(f));
  const cats = {}; rows.forEach(r => (cats[r.category] ||= []).push(r));
  $('#rules').innerHTML = `<table><tr><th style="width:44px">On</th><th style="width:90px">Code</th><th>Rule</th><th style="width:80px">Severity</th><th>Parameters</th><th style="width:110px"></th></tr>${Object.entries(cats).map(([c, rs]) => `<tr><td colspan="6" style="background:#FAFBFC;font-size:12px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);font-weight:600">${c}</td></tr>` + rs.map(r => `<tr><td><button class="switch ${r.enabled ? 'on' : ''} ${r.locked ? 'locked' : ''}" data-id="${r.id}" title="${r.locked ? 'Locked governance rule' : r.policy_backed ? 'Switch on or off (needs a policy document)' : 'Switch on or off (system rule: reason only)'}"></button></td><td><a class="code" href="/policy/${r.id}" target="_blank" title="Open the policy behind ${r.id}">${r.id}</a></td><td><b>${esc(r.name)}</b><div class="small muted">${esc(r.description || r.public || r.manager || '')}</div></td><td><span class="pill ${r.severity === 'hard' ? 'r' : r.severity === 'soft' ? 'a' : 'b'}">${r.severity}</span></td><td class="small">${Object.entries(r.params || {}).map(([k, v]) => `${k} = <b>${esc(String(v))}</b>`).join('<br>') || '<span class="muted">—</span>'}</td><td><button class="btn sm edit" data-id="${r.id}">Change…</button></td></tr>`).join('')).join('')}</table>`;
  $$('#rules .switch').forEach(b => b.onclick = () => { const r = R.rules.find(x => x.id === b.dataset.id); if (r.locked && r.enabled) return toast('Locked governance rule: it cannot be switched off.'); openDrawer('rule', r.id, { enabled: !r.enabled }, r); });
  $$('#rules .edit').forEach(b => b.onclick = () => { const r = R.rules.find(x => x.id === b.dataset.id); openEditor(r); });
}
$('#filter').oninput = renderRules;
function openEditor(r) {
  pending = null; $('#m-title').textContent = `${r.id} · ${r.name}`;
  $('#m-context').innerHTML = `<b>Source:</b> ${esc(r.source || '')}<br>${esc(r.description || '')}`;
  $('#m-diff').innerHTML = `<div class="field"><label>Severity</label><select id="e-sev"><option ${r.severity === 'hard' ? 'selected' : ''}>hard</option><option ${r.severity === 'soft' ? 'selected' : ''}>soft</option><option ${r.severity === 'info' ? 'selected' : ''}>info</option></select></div>${Object.entries(r.params || {}).map(([k, v]) => `<div class="field"><label>${k}</label><input data-p="${k}" value="${esc(String(v))}"></div>`).join('')}<button class="btn sm" id="e-stage">Stage this change</button>`;
  $('#m-why').value = ''; $('#m-file').value = ''; $('#m-review').classList.add('hidden'); $('#m-review').innerHTML = ''; setDrawerMode(!r.policy_backed); $('#m-status').textContent = r.policy_backed ? 'Edit the values, stage the change, then attach the policy and verify.' : 'System rule: edit the values, stage the change, give a reason and apply.';
  $('#drawer').classList.remove('hidden');
  $('#e-stage').onclick = () => { const change = {}, params = {}; $$('#m-diff [data-p]').forEach(i => { const n = Number(i.value); const v = (i.value !== '' && !isNaN(n)) ? n : i.value; if (String(v) !== String(r.params[i.dataset.p])) params[i.dataset.p] = v; }); if (Object.keys(params).length) change.params = params; const sev = $('#e-sev').value; if (sev !== r.severity) change.severity = sev; if (!Object.keys(change).length) return toast('Nothing changed.'); openDrawer('rule', r.id, change, r); };
}
function isSystemTarget(type, id) {
  if (type === 'agreement') return false;
  if (type !== 'rule') return true;
  const r = (R.rules || []).find(x => x.id === id);
  return r ? !r.policy_backed : false;
}
function setDrawerMode(system) {
  const fileField = $('#m-file').closest('.field');
  fileField.hidden = !!system;
  $('#m-verify').textContent = system ? 'Apply change' : 'Verify with AI';
  $('#m-why').placeholder = system ? 'e.g. ward now runs a 12-hour day model' : 'e.g. clause 28(12) of the 2027 agreement sets 10 hours';
  $('#m-status').textContent = system ? 'System rule: no policy document needed. Give a reason and apply; it is logged in the change history.' : 'Attach the policy document, then verify.';
}
function openDrawer(type, id, change, ctx) {
  pending = { type, id, change, system: isSystemTarget(type, id) }; $('#m-title').textContent = `Propose change · ${type} ${id}`;
  $('#m-context').innerHTML = (ctx ? `<b>Source:</b> ${esc(ctx.source || ctx.label || '')}` : '') + (pending.system ? ' <span class="pill n">system rule</span>' : ' <span class="pill b">policy-backed</span>');
  $('#m-diff').textContent = JSON.stringify(change, null, 2);
  $('#m-review').classList.add('hidden'); $('#m-review').innerHTML = ''; setDrawerMode(pending.system); $('#drawer').classList.remove('hidden');
}
$('#m-close').onclick = () => $('#drawer').classList.add('hidden');
$('#m-verify').onclick = async () => {
  if (!pending) return toast('Stage a change first.');
  const f = $('#m-file').files[0]; if (!f && !pending.system) return toast('Attach the policy document first.');
  if (pending.system && $('#m-why').value.trim().length < 5) return toast('Give a short reason for the change.');
  const fd = new FormData(); fd.append('target_type', pending.type); fd.append('target_id', pending.id); fd.append('change', JSON.stringify(pending.change)); fd.append('justification', $('#m-why').value); fd.append('file', f);
  $('#m-verify').disabled = true; const t0 = Date.now(); const tick = setInterval(() => { $('#m-status').textContent = `Reading the document and checking compliance… ${((Date.now() - t0) / 1000).toFixed(0)}s`; }, 500);
  try {
    const r = await api('/api/admin/proposals', { method: 'POST', body: fd }); const v = r.review; pending.pid = r.proposal_id; clearInterval(tick);
    if (r.applied) { $('#drawer').classList.add('hidden'); toast('Applied and logged. The engine now uses the new value.'); await load(); return; }
    $('#m-status').textContent = `Checked by ${v.engine === 'gemini' ? v.model : 'the built-in checker'} · ${v.characters} characters read · ${((Date.now() - t0) / 1000).toFixed(1)}s`;
    $('#m-review').classList.remove('hidden');
    $('#m-review').innerHTML = `<div class="verdict ${v.verdict}"><b>${v.verdict.replace('_', ' ')} · confidence ${(v.confidence * 100).toFixed(0)}%${v.australian_document ? '' : ' · does not look like an Australian policy'}</b><p style="margin-top:6px">${esc(v.summary || '')}</p>${(v.findings || []).length ? `<h4 style="margin-top:10px">Findings</h4><ul>${v.findings.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}${(v.evidence || []).length ? `<h4 style="margin-top:10px">Evidence</h4>${v.evidence.map(x => `<div class="quote">${esc(x)}</div>`).join('')}` : ''}${(v.recommendations || []).length ? `<h4 style="margin-top:10px">Recommendations</h4><ul>${v.recommendations.map(x => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}${v.suggested_change ? `<h4 style="margin-top:10px">Suggested instead</h4><div class="diff">${esc(JSON.stringify(v.suggested_change))}</div>` : ''}</div>
      <div class="actions">${r.can_apply ? `<button class="btn ok" id="m-apply">Apply change</button>` : `<button class="btn danger" id="m-override">Apply anyway (override, logged)</button>`}<button class="btn" id="m-reject">Discard</button></div>`;
    $('#m-apply')?.addEventListener('click', () => apply(''));
    $('#m-override')?.addEventListener('click', async () => { const why = await ui.prompt('Apply without AI confirmation', 'The AI did not confirm this change complies. Give a written reason; it is recorded in the audit log.', { placeholder: 'e.g. local agreement signed 1 July 2026 supersedes clause 28(12)', required: true, okLabel: 'Apply anyway', cls: 'danger' }); if (why) apply(why); });
    $('#m-reject').onclick = async () => { await api(`/api/admin/proposals/${r.proposal_id}/reject`, { method: 'POST', body: '{}' }); $('#drawer').classList.add('hidden'); toast('Discarded'); };
  } catch (e) { clearInterval(tick); $('#m-status').textContent = ''; toast(e.message); } finally { $('#m-verify').disabled = false; }
};
async function apply(reason) { try { await api(`/api/admin/proposals/${pending.pid}/apply`, { method: 'POST', body: JSON.stringify({ override_reason: reason }) }); $('#drawer').classList.add('hidden'); toast('Applied. The engine now uses the new rule.'); await load(); } catch (e) { toast(e.message); } }

/* ---------- agreements, tiers, history ---------- */
function renderAgree() {
  const ag = R.agreements; const sel = $('#fam'); if (!sel.options.length) { sel.innerHTML = Object.entries(ag).map(([k, v]) => `<option value="${k}">${esc(v.label)}</option>`).join(''); sel.onchange = renderAgree; }
  const fam = sel.value || Object.keys(ag)[0];
  $('#agree').innerHTML = `<table><tr><th>Condition</th><th style="width:160px">Value</th><th style="width:110px"></th></tr>${Object.entries(ag[fam]).filter(([k]) => k !== 'label').map(([k, v]) => `<tr><td>${k.replaceAll('_', ' ')}</td><td><input data-ag="${k}" value="${esc(String(v))}" style="padding:6px 8px"></td><td><button class="btn sm" data-agk="${k}">Change…</button></td></tr>`).join('')}</table>`;
  $$('#agree [data-agk]').forEach(b => b.onclick = () => { const k = b.dataset.agk; const v = $(`input[data-ag="${k}"]`).value; const n = Number(v); const nv = (v !== '' && !isNaN(n)) ? n : (v === 'true' ? true : v === 'false' ? false : v); if (String(nv) === String(ag[fam][k])) return toast('Value unchanged.'); openDrawer('agreement', fam, { [k]: nv }, ag[fam]); });
}
function renderTiers() {
  const t = R.tiers;
  $('#tiers').innerHTML = `<table><tr><th style="width:40px">#</th><th>Option</th><th>On</th><th class="num">Cost ×</th><th>Needs manager</th><th style="width:130px"></th></tr>${t.map((x, i) => `<tr><td>${i + 1}</td><td>${esc(x.label)}</td><td>${x.enabled ? '<span class="pill g">on</span>' : '<span class="pill n">off</span>'}</td><td class="num">${x.cost_multiplier}</td><td>${x.requires_manager ? 'yes' : 'no'}</td><td><button class="btn sm" data-toggle="${x.id}">${x.enabled ? 'Switch off…' : 'Switch on…'}</button></td></tr>`).join('')}</table>`;
  $$('#tiers [data-toggle]').forEach(b => b.onclick = () => { const x = t.find(y => y.id === b.dataset.toggle); openDrawer('tier', x.id, { enabled: !x.enabled }, { source: 'ANF cl.16; WACHS Roster Guideline s2.6' }); });
}
async function renderHist() {
  const ps = await api('/api/admin/proposals');
  $('#hist').innerHTML = ps.length ? `<table><tr><th>When</th><th>Target</th><th>Change</th><th>Document</th><th>AI verdict</th><th>Status</th></tr>${ps.map(p => `<tr><td class="small">${p.ts.replace('T', ' ')}</td><td>${p.target_type} <b>${p.target_id}</b></td><td class="small">${esc(JSON.stringify(p.change))}</td><td class="small">${p.verdict === 'SYSTEM' ? `<span class="muted">reason: ${esc(p.justification || '')}</span>` : esc(p.document)}</td><td><span class="pill ${p.verdict === 'COMPLIANT' ? 'g' : p.verdict === 'SYSTEM' ? 'n' : p.verdict === 'NON_COMPLIANT' ? 'r' : 'a'}">${p.verdict === 'SYSTEM' ? 'system rule' : p.verdict + ' ' + (p.confidence * 100).toFixed(0) + '%'}</span></td><td>${p.status}${p.override_reason ? `<div class="small muted">override: ${esc(p.override_reason)}</div>` : ''}</td></tr>`).join('')}</table>` : '<div class="empty">No changes proposed yet.</div>';
}

/* ---------- update from a document ---------- */
$('#d-go').onclick = async () => {
  const f = $('#d-file').files[0]; if (!f) return toast('Choose a document first.');
  const fd = new FormData(); fd.append('file', f);
  $('#d-go').disabled = true; const st = $('#d-status'); const t0 = Date.now();
  const steps = ['Reading the document', 'Finding the passages about hours, leave, notice and cover', 'Comparing with the current rulebook', 'Writing recommendations'];
  let i = 0; st.textContent = steps[0] + '…'; const tick = setInterval(() => { i = Math.min(i + 1, steps.length - 1); st.textContent = `${steps[i]}… ${((Date.now() - t0) / 1000).toFixed(0)}s`; }, 2500);
  try {
    disc = await api('/api/admin/discover', { method: 'POST', body: fd }); clearInterval(tick);
    st.textContent = `${disc.engine === 'gemini' ? disc.model : 'built-in scan'} · ${disc.characters} characters · ${((Date.now() - t0) / 1000).toFixed(1)}s`;
    const fs = disc.findings || []; const oc = disc.override_checks || [];
    const ovHtml = oc.length ? `<div class="section"><h4>Values previously set by manual override</h4><p class="small muted" style="margin:4px 0 8px">These were applied without AI confirmation. Each is checked against this document.</p><table><tr><th>Where</th><th class="num">Before</th><th class="num">Now (override)</th><th>Override reason</th><th class="num">Document says</th><th>Verdict</th></tr>${oc.map(o => `<tr><td><b>${esc(o.target_type)} ${esc(o.target_id)}</b><div class="small muted">${esc(o.field || '')}</div></td><td class="num">${esc(String(o.previous ?? '?'))}</td><td class="num"><b>${esc(String(o.current))}</b></td><td class="small">${esc(o.reason || '')}<div class="muted">${(o.when || '').replace('T', ' ')}</div></td><td class="num">${o.document_value === null || o.document_value === undefined ? '<span class="muted">not mentioned</span>' : `<b>${esc(String(o.document_value))}</b>`}</td><td>${o.status === 'differs' ? '<span class="pill r">differs, change listed below</span>' : o.status === 'matches' ? '<span class="pill g">document confirms the override</span>' : '<span class="pill n">not covered</span>'}</td></tr>`).join('')}</table></div>` : '';
    $('#d-out').innerHTML = `<div class="banner ${fs.length ? 'b' : 'n'}"><div class="ic">${fs.length}</div><div><h3>${fs.length ? `${fs.length} value${fs.length > 1 ? 's' : ''} in this document differ from the rulebook` : 'The rulebook already matches this document'}</h3><p>${esc(disc.document_summary || '')}${disc.confirmed ? ` · ${disc.confirmed} value${disc.confirmed > 1 ? 's' : ''} confirmed as already correct` : ''}${disc.australian_document === false ? ' · does not look like an Australian policy' : ''}${disc.warning ? ' · ' + esc(disc.warning) : ''}</p></div></div>${ovHtml}
      ${fs.length ? `<table style="margin-top:14px"><tr><th style="width:36px"></th><th>Where</th><th class="num">Now</th><th class="num">Document</th><th>Evidence</th><th class="num">Conf.</th></tr>${fs.map((x, k) => `<tr><td><input type="checkbox" class="d-pick" data-i="${k}" ${x.confidence >= 0.5 || x.override ? 'checked' : ''} style="width:16px"></td><td><b>${esc(x.target_type)} ${esc(x.target_id)}</b><div class="small muted">${esc(x.field || '')}</div>${x.override ? `<span class="pill a">overridden: ${esc((x.override.reason || '').slice(0, 60))}</span>` : ''}</td><td class="num">${esc(String(x.current))}</td><td class="num"><b>${esc(String(x.proposed))}</b></td><td class="small">${esc(x.evidence || '')}<div class="muted">${esc(x.rationale || '')}</div></td><td class="num">${Math.round((x.confidence || 0) * 100)}%</td></tr>`).join('')}</table>
      <div class="actions"><button class="btn ok" id="d-apply">Apply selected</button><span class="small muted">Each applied row is logged as a proposal with this document as evidence.</span></div>` : ''}`;
    $('#d-apply')?.addEventListener('click', async () => { const idx = $$('.d-pick:checked').map(c => +c.dataset.i); if (!idx.length) return toast('Tick at least one change.'); try { const r = await api('/api/admin/discover/apply', { method: 'POST', body: JSON.stringify({ token: disc.token, indices: idx }) }); await ui.info('Changes applied', `${r.applied.length ? `<p><b>${r.applied.length} change${r.applied.length > 1 ? 's' : ''} applied</b> and logged with this document as evidence.</p>${r.applied.map(a => `<div class="kv"><span>${esc(a.target)}</span><b>${esc(JSON.stringify(a.change))}</b></div>`).join('')}` : '<p>No changes were applied.</p>'}${(r.unchanged || []).length ? `<p class="small muted" style="margin-top:10px">Skipped, the rulebook already has that value: ${r.unchanged.map(esc).join(', ')}</p>` : ''}${r.errors.length ? `<p class="error" style="margin-top:10px">${r.errors.length} failed: ${r.errors.map(e => esc(e.error)).join('; ')}</p>` : ''}`); await load(); $('#d-go').click(); } catch (e) { toast(e.message); } });
  } catch (e) { clearInterval(tick); st.textContent = ''; toast(e.message); } finally { $('#d-go').disabled = false; }
};

async function renderQuality() {
  const q = await api('/api/admin/quality'); const c = q.calibration || {}; const b = q.benchmark || {}; const ch = c.chosen || {};
  const grid = (c.grid || []).find(g => g.baseline_percentile === ch.baseline_percentile && g.team_away_cap === ch.team_away_cap) || {};
  const tests = Object.entries(q.tests || {}).reduce((a, [, n]) => a + n, 0);
  $('#quality').innerHTML = `<div class="facts"><div class="fact"><b>${grid.B_accuracy !== undefined ? Math.round(grid.B_accuracy * 100) + '%' : '–'}</b><span>known-answer cases correct (${c.dataset_B_size || 0} cases)</span></div><div class="fact"><b>${ch.A_agreement !== undefined ? (ch.A_agreement * 100).toFixed(1) + '%' : '–'}</b><span>agreement with booked leave (${c.dataset_A_size || 0} cases)</span></div><div class="fact"><b>${b.per_scenario_ms ? b.per_scenario_ms.p50 + ' ms' : '–'}</b><span>median check time (${b.scenarios || 0} scenarios)</span></div><div class="fact"><b>${tests}</b><span>automated tests</span></div><div class="fact"><b>${q.rules}</b><span>rules in rulebook v${q.rules_version}</span></div></div>
    <div class="card" style="margin-top:16px"><div class="card-h"><h2>What the numbers mean</h2></div><div class="card-b"><div class="kv"><span>Known-answer cases</span><b>Constructed cases whose correct outcome is certain (invalid dates, casual paid leave, zero balance, sick leave, fatigue limits). Must be 100%.</b></div><div class="kv"><span>Agreement with booked leave</span><b>Leave the organisation actually booked in the data should not be declined on coverage grounds. The remainder are balance contradictions in the synthetic data or long blocks with no eligible cover.</b></div><div class="kv"><span>Calibrated parameters</span><b>Required staffing = ${ch.baseline_percentile !== undefined ? ch.baseline_percentile * 100 + 'th percentile' : '–'} of observed staffing; team-away cap ${ch.team_away_cap !== undefined ? ch.team_away_cap * 100 + '%' : '–'}. Chosen because they keep known-answer cases at 100% and maximise agreement.</b></div><div class="kv"><span>Benchmark outcomes</span><b>${b.outcomes ? Object.entries(b.outcomes).map(([k, v]) => `${k.replace('_', ' ').toLowerCase()} ${v}`).join(' · ') : '–'}</b></div><div class="kv"><span>Cover found by option</span><b>${b.cover_by_tier ? Object.entries(b.cover_by_tier).map(([k, v]) => `${k} ${v}`).join(' · ') : '–'}</b></div><div class="kv"><span>Data</span><b>${q.data.employees} employees · ${q.data.units} units · ${q.data.shifts} rostered shifts · historical sick-call rate ${(q.data.sick_rate_overall * 100).toFixed(1)}% of shifts</b></div><div class="kv"><span>Test files</span><b>${Object.entries(q.tests || {}).map(([f, n]) => `${f} (${n})`).join(' · ')}</b></div><div class="kv"><span>Last calibration</span><b>${c.run_at || '–'}</b></div></div></div>`;
}
