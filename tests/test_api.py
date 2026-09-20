"""End-to-end API tests: staff flow, manager flow, super-user policy-verified change, chat fallback."""
import datetime as dt, io, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['LEAVECOVER_NO_GEMINI'] = '1'   # tests use the deterministic fallback
from fastapi.testclient import TestClient
import leavecover.api as api

client = TestClient(api.app)
TODAY = api.rb.today()
# the super user may act for any staff member; every call below carries that token unless a test overrides it
client.headers['X-Auth-Token'] = client.post('/api/auth/login', json={'username': 'admin', 'password': 'admin'}).json()['token']


def _staff_with_balance():
    for eid, e in api.store.employees.items():
        if e.job_type == 'PERMANENT' and e.role_class == 'NURSE_RN' and e.window_end and e.window_end > TODAY + dt.timedelta(days=35) and api.store.balances.get(eid, {}).get('AL', {}).get('remaining', 0) > 200:
            return eid
    raise RuntimeError('no suitable employee')


def _hdr(tok):
    return {'X-Auth-Token': tok}


def test_health_and_personas():
    assert client.get('/api/health').json()['ok']
    assert len(client.get('/api/demo-personas').json()) >= 5


def test_staff_flow_check_submit_escalate():
    eid = _staff_with_balance()
    me = client.get(f'/api/me/{eid}').json()
    assert 'balances' in me and me['leave_types']
    s = max(TODAY + dt.timedelta(days=21), api.store.employees[eid].window_start)
    body = {'employee_id': eid, 'leave_type': 'ANNUAL', 'start': s.isoformat(), 'end': (s + dt.timedelta(days=4)).isoformat(), 'note': 'family'}
    chk = client.post('/api/check', json=body).json()
    assert chk['outcome'] in ('RECOMMEND_APPROVE', 'NEEDS_APPROVAL', 'DECLINED')
    assert 'chart' in chk and 'rules' in chk
    # anonymised: no other employee ids in staff view
    txt = str(chk)
    assert all(not (k.startswith('SYN') and k != eid) for k in set(w for w in txt.replace("'", ' ').split() if w.startswith('SYN')))
    sub = client.post('/api/requests', json=body).json()
    rid = sub['request_id']
    assert rid.startswith('LR-')
    lst = client.get(f'/api/requests?employee_id={eid}').json()
    assert any(r['id'] == rid for r in lst)
    if sub['status'] in ('DECLINED', 'PENDING_MANAGER'):
        esc = client.post(f'/api/requests/{rid}/escalate', json={'message': 'wedding'}).json()
        assert esc['status'] == 'ESCALATED'


def test_sick_leave_is_accepted_and_manager_notified():
    eid = _staff_with_balance()
    r = client.post('/api/requests', json={'employee_id': eid, 'leave_type': 'SICK', 'start': TODAY.isoformat(), 'end': TODAY.isoformat(), 'note': 'flu'}).json()
    assert r['outcome'] == 'ACCEPTED' and r['status'] == 'ACCEPTED'
    tok = client.post('/api/auth/login', json={'username': 'manager', 'password': 'manager'}).json()['token']
    notes = client.get('/api/manager/notifications', headers=_hdr(tok)).json()
    assert any(n['request_id'] == r['request_id'] for n in notes)
    client.post(f"/api/requests/{r['request_id']}/withdraw")


def test_manager_requires_login_and_can_decide():
    assert client.get('/api/manager/inbox', headers={'X-Auth-Token': ''}).status_code == 403
    tok = client.post('/api/auth/login', json={'username': 'manager', 'password': 'manager'}).json()['token']
    eid = _staff_with_balance()
    s = max(TODAY + dt.timedelta(days=21), api.store.employees[eid].window_start)
    sub = client.post('/api/requests', json={'employee_id': eid, 'leave_type': 'ANNUAL', 'start': s.isoformat(), 'end': (s + dt.timedelta(days=2)).isoformat()}).json()
    inbox = client.get('/api/manager/inbox', headers=_hdr(tok)).json()
    detail = client.get(f"/api/manager/requests/{sub['request_id']}", headers=_hdr(tok)).json()
    assert 'cover' in (detail['decision'] or {})
    # decline without reason is refused
    assert client.post(f"/api/manager/requests/{sub['request_id']}/decide", json={'action': 'decline', 'note': ''}, headers=_hdr(tok)).status_code == 400
    if sub['status'] in ('PENDING_MANAGER', 'ESCALATED', 'ACCEPTED'):
        ok = client.post(f"/api/manager/requests/{sub['request_id']}/decide", json={'action': 'approve', 'note': 'fine'}, headers=_hdr(tok)).json()
        assert ok['status'] == 'APPROVED'
    cov = client.get(f"/api/manager/coverage?unit={api.store.employees[eid].primary_unit}&start={TODAY}&end={TODAY + dt.timedelta(days=13)}", headers=_hdr(tok)).json()
    assert cov['days']
    assert 'insights' in client.get(f"/api/manager/suggestions?unit={api.store.employees[eid].primary_unit}", headers=_hdr(tok)).json()


def test_admin_policy_verified_change():
    bad = client.post('/api/auth/login', json={'username': 'admin', 'password': 'wrong'})
    assert bad.status_code == 401
    tok = client.post('/api/auth/login', json={'username': 'admin', 'password': 'admin'}).json()['token']
    # direct edits are disabled
    assert client.patch('/api/admin/rules/NOT-001', json={'enabled': False}, headers=_hdr(tok)).status_code == 400
    # locked rule cannot be disabled even via proposal
    doc = b'WA Health Industrial Agreement (Western Australia). Clause 28(12): a break of 9.5 hours between shifts. Annual leave applications: respond within 14 days.'
    r = client.post('/api/admin/proposals', data={'target_type': 'rule', 'target_id': 'GOV-001', 'change': '{"enabled": false}', 'justification': 'x'}, files={'file': ('policy.txt', io.BytesIO(doc), 'text/plain')}, headers=_hdr(tok))
    assert r.status_code == 400
    # a real proposal with a document: change NOT-001 severity to soft (already soft) and params -> review runs
    before = api.rb.rule('COV-002')['params']['max_fraction_on_leave']
    r = client.post('/api/admin/proposals', data={'target_type': 'rule', 'target_id': 'COV-002', 'change': '{"params": {"max_fraction_on_leave": 0.4}}', 'justification': 'local staffing profile'}, files={'file': ('policy.txt', io.BytesIO(doc), 'text/plain')}, headers=_hdr(tok)).json()
    assert r['review']['verdict'] in ('COMPLIANT', 'NON_COMPLIANT', 'UNCERTAIN') and r['review']['australian_document']
    pid = r['proposal_id']
    if r['can_apply']:
        a = client.post(f'/api/admin/proposals/{pid}/apply', json={'override_reason': ''}, headers=_hdr(tok)).json()
    else:
        assert client.post(f'/api/admin/proposals/{pid}/apply', json={'override_reason': ''}, headers=_hdr(tok)).status_code == 400
        a = client.post(f'/api/admin/proposals/{pid}/apply', json={'override_reason': 'Local nursing director approved 40% during winter surge'}, headers=_hdr(tok)).json()
    assert a['ok'] and api.rb.rule('COV-002')['params']['max_fraction_on_leave'] == 0.4
    hist = client.get('/api/admin/proposals', headers=_hdr(tok)).json()
    assert any(p['id'] == pid for p in hist)
    # restore directly and remove the test's proposals so they do not pollute the change history
    api.rb.update_rule('COV-002', params={'max_fraction_on_leave': before}, actor='test-cleanup')
    api.db.conn.execute("DELETE FROM proposals WHERE document='policy.txt'"); api.db.conn.commit()
    assert api.rb.rule('COV-002')['params']['max_fraction_on_leave'] == before
    sim = client.post('/api/admin/simulate', json={'employee_id': _staff_with_balance(), 'leave_type': 'ANNUAL', 'start': (TODAY + dt.timedelta(days=3)).isoformat(), 'end': (TODAY + dt.timedelta(days=4)).isoformat(), 'rules': {'NOT-001': {'enabled': False}}}, headers=_hdr(tok)).json()
    assert 'NOT-001' in sim['before']['breached_codes'] and 'NOT-001' not in sim['after']['breached_codes']


def test_chat_fallback_flow():
    eid = _staff_with_balance()
    r = client.post('/api/chat', json={'employee_id': eid, 'session_id': 't', 'message': "What's my leave balance?"}).json()
    assert 'balance' in r['reply'].lower() and r['engine'] == 'fallback'
    s = max(TODAY + dt.timedelta(days=21), api.store.employees[eid].window_start)
    r = client.post('/api/chat', json={'employee_id': eid, 'session_id': 't', 'message': f'annual leave {s.isoformat()} to {(s + dt.timedelta(days=3)).isoformat()}'}).json()
    assert 'check_leave' in r['tools_used']
    r = client.post('/api/chat', json={'employee_id': eid, 'session_id': 't', 'message': 'submit'}).json()
    assert 'submit_leave' in r['tools_used'] and 'LR-' in r['reply']
    r = client.post('/api/chat', json={'employee_id': eid, 'session_id': 't2', 'message': "I'm sick today"}).json()
    assert 'check_leave' in r['tools_used'] and not (r.get('decision') or {}).get('request_id'), 'sick leave is recorded from the card, not automatically'


def _request_with_two_covered_lines():
    for eid, e in api.store.employees.items():
        if e.job_type != 'PERMANENT' or e.role_class not in ('NURSE_RN', 'NURSE_EN') or not e.window_end or e.window_end < TODAY + dt.timedelta(days=40):
            continue
        if api.store.balances.get(eid, {}).get('AL', {}).get('remaining', 0) < 150:
            continue
        s = TODAY + dt.timedelta(days=28)
        dec = api.engine.evaluate(eid, 'ANNUAL', s, s + dt.timedelta(days=4), '', with_alternatives=False)
        if dec.outcome != 'DECLINED' and sum(1 for c in dec.cover if c.status == 'covered') >= 2:
            return eid, s
    raise RuntimeError('no request with two covered lines found')


def test_cover_handshake_decline_replans_and_accept_is_kept():
    eid, s = _request_with_two_covered_lines()
    body = {'employee_id': eid, 'leave_type': 'ANNUAL', 'start': s.isoformat(), 'end': (s + dt.timedelta(days=4)).isoformat(), 'note': 'handshake'}
    r = client.post('/api/requests', json=body).json()
    rid = r['request_id']
    try:
        tok = client.post('/api/auth/login', json={'username': 'manager', 'password': 'manager'}).json()['token']
        plan = client.get(f'/api/manager/requests/{rid}', headers=_hdr(tok)).json()['decision']['cover']
        covered = {(c['date'], c['candidate']['employee_id']) for c in plan if c['status'] == 'covered'}
        rows = [a for a in api.db.all_assignments() if a['request_id'] == rid and a['status'] == 'proposed']
        assert {(a['date'], a['employee_id']) for a in rows} == covered
        keep, drop = rows[0], rows[1]
        # the proposed colleague sees the shift in their own cover list
        mine = client.get(f"/api/me/{drop['employee_id']}/cover").json()
        assert any(a['id'] == drop['id'] and a['status'] == 'proposed' for a in mine)
        assert client.post(f"/api/cover/{keep['id']}/respond", json={'accept': True}).json()['status'] == 'accepted'
        assert client.post(f"/api/cover/{drop['id']}/respond", json={'accept': False, 'note': 'not available'}).json()['status'] == 'declined_by_staff'
        new = client.get(f'/api/manager/requests/{rid}', headers=_hdr(tok)).json()['decision']['cover']
        by_date = {c['date']: c for c in new}
        # the declined colleague is not proposed again on that date, and the gap did not vanish
        assert (by_date[drop['date']].get('candidate') or {}).get('employee_id') != drop['employee_id']
        assert by_date[drop['date']]['status'] in ('covered', 'uncovered')
        # the colleague who accepted keeps the shift and is shown as accepted
        assert by_date[keep['date']]['candidate']['employee_id'] == keep['employee_id']
        assert by_date[keep['date']].get('response') == 'accepted'
        # a manager notification was raised
        notes = client.get('/api/manager/notifications', headers=_hdr(tok)).json()
        assert any(n.get('request_id') == rid and 'declined' in (n.get('title') or '').lower() for n in notes)
    finally:
        client.post(f'/api/requests/{rid}/withdraw')
    # withdrawing releases the colleagues
    assert not any(k[0] in (keep['employee_id'], drop['employee_id']) for k in api.store.cover_confirmed)
    assert (drop['employee_id'], dt.date.fromisoformat(drop['date'])) not in api.store.cover_refused


def test_system_rule_changes_with_reason_only_but_policy_rules_need_a_document():
    tok = client.post('/api/auth/login', json={'username': 'admin', 'password': 'admin'}).json()['token']
    before = api.rb.settings['alternatives_max']
    fat_before = dict(api.rb.rule('FAT-001')['params'])
    try:
        # a system setting: no document, a reason is enough, applied at once and logged
        r = client.post('/api/admin/proposals', data={'target_type': 'setting', 'target_id': 'alternatives_max', 'change': '{"value": %d}' % (before + 1), 'justification': 'test: show one more alternative'}, headers=_hdr(tok))
        assert r.status_code == 200, r.text
        assert r.json()['applied'] and r.json()['review']['verdict'] == 'SYSTEM'
        assert api.rb.settings['alternatives_max'] == before + 1
        # without a reason it is refused
        r = client.post('/api/admin/proposals', data={'target_type': 'setting', 'target_id': 'alternatives_max', 'change': '{"value": %d}' % before, 'justification': ''}, headers=_hdr(tok))
        assert r.status_code == 400
        # a policy-backed rule still needs the document
        r = client.post('/api/admin/proposals', data={'target_type': 'rule', 'target_id': 'FAT-001', 'change': '{"params": {"min_break_hours": 8}}', 'justification': 'test'}, headers=_hdr(tok))
        assert r.status_code == 400 and 'Attach' in r.json()['detail']
        assert api.rb.rule('FAT-001')['params'] == fat_before
        rules = client.get('/api/admin/rules', headers=_hdr(tok)).json()['rules']
        flags = {x['id']: x['policy_backed'] for x in rules}
        assert flags['FAT-001'] and flags['BAL-001'] and not flags['REQ-001'] and not flags['BAL-003']
    finally:
        api.rb.update_settings({'alternatives_max': before}, actor='test-cleanup')
        api.db.conn.execute("DELETE FROM proposals WHERE justification LIKE 'test:%'"); api.db.conn.commit()
    assert api.rb.settings['alternatives_max'] == before


def test_chat_more_recommendations_pages_without_repeats():
    eid = _staff_with_balance()
    seen = []
    for i, msg in enumerate(['When is the best time for 2 weeks of annual leave?', 'show more recommendations', 'more', 'any other options?']):
        r = client.post('/api/chat', json={'employee_id': eid, 'session_id': 'paging', 'message': msg}).json()
        d = r.get('decision') or {}
        assert d.get('kind') == 'windows', r.get('reply')
        starts = [w['start'] for w in d.get('windows', [])]
        assert not (set(starts) & set(seen)), f'page {i} repeated a window: {starts}'
        if i == 0:
            assert starts and d['page']['from'] == 1
        if not starts:
            assert d['page']['from'] > 1 and d['more'] is False
            break
        seen += starts
    assert len(seen) >= 4
    api.db.delete_conversation(eid, 'paging')


def test_sick_leave_is_confirmed_from_a_card_and_never_duplicated():
    eid = _staff_with_balance()
    for x in api.db.list_requests(employee_id=eid, status=['ACCEPTED'], limit=20):
        if x['leave_type'] == 'SICK':
            client.post(f"/api/requests/{x['id']}/withdraw")
    r = client.post('/api/chat', json={'employee_id': eid, 'session_id': 'sick-card', 'message': "I'm sick today"}).json()
    d = r.get('decision') or {}
    assert d.get('outcome') == 'ACCEPTED' and not d.get('request_id'), 'sick leave must be checked, not recorded, until the card is tapped'
    assert d['leave_type'] == 'SICK' and d['start'] == d['end'] == TODAY.isoformat()
    body = {'employee_id': eid, 'leave_type': 'SICK', 'start': d['start'], 'end': d['end'], 'note': '', 'channel': 'chat'}
    first = client.post('/api/requests', json=body).json()
    try:
        assert first['outcome'] == 'ACCEPTED' and first['request_id'] and not first.get('duplicate')
        again = client.post('/api/requests', json=body).json()
        assert again.get('duplicate') and again['request_id'] == first['request_id']
        # saying "submit" afterwards in chat cannot create a second request either
        r2 = client.post('/api/chat', json={'employee_id': eid, 'session_id': 'sick-card', 'message': 'submit'}).json()
        assert (r2.get('decision') or {}).get('request_id') == first['request_id']
        assert client.post('/api/chat/log', json={'employee_id': eid, 'session_id': 'sick-card', 'text': 'Recorded', 'card': first}).json()['ok']
    finally:
        client.post(f"/api/requests/{first['request_id']}/withdraw")
        api.db.delete_conversation(eid, 'sick-card')


def test_agent_can_withdraw_only_while_pending():
    eid = _staff_with_balance()
    s = max(TODAY + dt.timedelta(days=30), api.store.employees[eid].window_start)
    body = {'employee_id': eid, 'leave_type': 'ANNUAL', 'start': s.isoformat(), 'end': (s + dt.timedelta(days=2)).isoformat(), 'note': 'wd'}
    sub = client.post('/api/requests', json=body).json()
    rid = sub['request_id']
    assert sub['status'] in ('PENDING_MANAGER', 'ESCALATED')
    r = client.post('/api/chat', json={'employee_id': eid, 'session_id': 'wd', 'message': f'please withdraw {rid}'}).json()
    assert 'withdraw_request' in r['tools_used'] and 'Withdrawn' in r['reply'] and (r.get('decision') or {}).get('kind') == 'withdrawn'
    assert api.db.get_request(rid)['status'] == 'WITHDRAWN'
    # approved leave cannot be withdrawn by staff or by the agent
    sub2 = client.post('/api/requests', json={**body, 'note': 'wd2'}).json(); rid2 = sub2['request_id']
    tok = client.post('/api/auth/login', json={'username': 'manager', 'password': 'manager'}).json()['token']
    client.post(f'/api/manager/requests/{rid2}/decide', json={'action': 'approve', 'note': 'ok'}, headers=_hdr(tok))
    assert client.post(f'/api/requests/{rid2}/withdraw').status_code == 400
    r = client.post('/api/chat', json={'employee_id': eid, 'session_id': 'wd', 'message': f'cancel {rid2}'}).json()
    assert 'approved' in r['reply'].lower() and api.db.get_request(rid2)['status'] == 'APPROVED'
    api.db.update_request(rid2, status='WITHDRAWN'); api._remove_overlay(rid2, eid, (api.db.get_request(rid2).get('decision') or {}).get('cover', []))
    api.db.delete_conversation(eid, 'wd')


def test_form_check_hands_over_to_the_assistant_with_memory():
    eid = _staff_with_balance()
    s = max(TODAY + dt.timedelta(days=21), api.store.employees[eid].window_start)
    chk = client.post('/api/check', json={'employee_id': eid, 'leave_type': 'ANNUAL', 'start': s.isoformat(), 'end': (s + dt.timedelta(days=4)).isoformat()}).json()
    r = client.post('/api/chat/context', json={'employee_id': eid, 'session_id': 'handoff', 'decision': chk}).json()
    assert r['ok'] and chk['start'] in r['context'] and 'Outcome' in r['context']
    msgs = client.get(f'/api/me/{eid}/conversations/handoff').json()
    assert msgs[0]['who'] == 'user' and 'From the request form' in msgs[0]['text'] and msgs[1]['card']['start'] == chk['start']
    # the built-in assistant answers follow-ups from that memory and can submit the same dates
    r = client.post('/api/chat', json={'employee_id': eid, 'session_id': 'handoff', 'message': 'why?'}).json()
    assert r['reply'].startswith('- ')
    r = client.post('/api/chat', json={'employee_id': eid, 'session_id': 'handoff', 'message': 'submit'}).json()
    assert 'submit_leave' in r['tools_used'] and r['decision']['start'] == chk['start']
    rid = r['decision']['request_id']
    client.post(f'/api/requests/{rid}/withdraw'); api.db.delete_conversation(eid, 'handoff')
    # a check belonging to someone else is refused
    other = next(x for x in api.store.employees if x != eid)
    assert client.post('/api/chat/context', json={'employee_id': other, 'session_id': 'x', 'decision': chk}).status_code == 400


def test_auth_is_enforced_and_staff_only_see_their_own_data():
    from starlette.testclient import TestClient as TC
    anon = TC(api.app)
    eid = _staff_with_balance()
    assert anon.get(f'/api/me/{eid}').status_code == 401
    assert anon.post('/api/check', json={'employee_id': eid, 'leave_type': 'ANNUAL', 'start': TODAY.isoformat(), 'end': TODAY.isoformat()}).status_code == 401
    assert anon.get('/api/manager/inbox').status_code == 403 and anon.get('/api/admin/rules').status_code == 403
    assert anon.get(f'/api/me/{eid}/conversations').status_code == 401
    # empty password is refused; the staff password works; another staff member's data is refused
    assert anon.post('/api/auth/login', json={'username': eid, 'password': ''}).status_code == 401
    tok = anon.post('/api/auth/login', json={'username': eid, 'password': 'password'}).json()['token']
    other = next(x for x in api.store.employees if x != eid)
    assert anon.get(f'/api/me/{eid}', headers={'X-Auth-Token': tok}).status_code == 200
    assert anon.get(f'/api/me/{other}', headers={'X-Auth-Token': tok}).status_code == 403
    assert anon.post('/api/requests', json={'employee_id': other, 'leave_type': 'ANNUAL', 'start': TODAY.isoformat(), 'end': TODAY.isoformat()}, headers={'X-Auth-Token': tok}).status_code == 403
    assert anon.get('/api/manager/inbox', headers={'X-Auth-Token': tok}).status_code == 403
    # lockout after repeated failures
    for _ in range(5):
        anon.post('/api/auth/login', json={'username': 'manager', 'password': 'nope'})
    assert anon.post('/api/auth/login', json={'username': 'manager', 'password': 'manager'}).status_code == 429
    api._login_fails.pop('MANAGER', None)
    # oversized or wrong-type uploads are refused
    atok = client.headers['X-Auth-Token']
    r = anon.post('/api/admin/proposals', data={'target_type': 'rule', 'target_id': 'FAT-001', 'change': '{"params": {"x": 1}}', 'justification': 'test'}, files={'file': ('evil.exe', b'MZ' * 10, 'application/octet-stream')}, headers={'X-Auth-Token': atok})
    assert r.status_code == 400
    r = anon.get('/api/health')
    assert r.headers.get('x-content-type-options') == 'nosniff' and r.headers.get('cache-control') == 'no-store'


def test_chat_answers_holiday_dates_from_the_calendar():
    eid = _staff_with_balance()
    r = client.post('/api/chat', json={'employee_id': eid, 'session_id': 'ph', 'message': "When is the king's birthday?"}).json()
    assert 'Monday 28 September 2026' in r['reply'] and 'get_public_holidays' in r['tools_used'] and not r.get('decision')
    r = client.post('/api/chat', json={'employee_id': eid, 'session_id': 'ph', 'message': 'when is anzac day'}).json()
    assert 'Sunday 25 April 2027' in r['reply'] and 'Monday 26 April' in r['reply']
    api.db.delete_conversation(eid, 'ph')


def test_chat_prompt_limits_per_account_and_ip():
    eid = _staff_with_balance()
    saved = {k: api.rb.settings.get(k) for k in ('chat_prompts_per_user', 'chat_prompts_per_ip', 'chat_limit_window_minutes')}
    api.rb.settings.update({'chat_prompts_per_user': 3, 'chat_prompts_per_ip': 100, 'chat_limit_window_minutes': 30})
    api._prompt_log.clear()
    try:
        codes = [client.post('/api/chat', json={'employee_id': eid, 'session_id': 'rl', 'message': "What's my leave balance?"}).status_code for _ in range(4)]
        assert codes == [200, 200, 200, 429]
        r = client.post('/api/chat', json={'employee_id': eid, 'session_id': 'rl', 'message': 'hi'})
        assert r.status_code == 429 and 'minute' in r.json()['detail']
        # the form still works
        assert client.get(f'/api/me/{eid}').status_code == 200
        # per-IP limit counts across accounts
        api._prompt_log.clear(); api.rb.settings.update({'chat_prompts_per_user': 100, 'chat_prompts_per_ip': 2})
        codes = [client.post('/api/chat', json={'employee_id': eid, 'session_id': 'rl', 'message': 'hi'}, headers={'X-Forwarded-For': '203.0.113.9'}).status_code for _ in range(3)]
        assert codes == [200, 200, 429]
        assert client.post('/api/chat', json={'employee_id': eid, 'session_id': 'rl', 'message': 'hi'}, headers={'X-Forwarded-For': '203.0.113.10'}).status_code == 200
    finally:
        api.rb.settings.update({k: v for k, v in saved.items() if v is not None}); api._prompt_log.clear()
        api.db.delete_conversation(eid, 'rl')
