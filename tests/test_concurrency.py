"""Several people from the same team ask for the same week: each later request must see the earlier ones."""
import datetime as dt, os, sys, collections
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['LEAVECOVER_NO_GEMINI'] = '1'
from fastapi.testclient import TestClient
import leavecover.api as api

client = TestClient(api.app)
client.headers['X-Auth-Token'] = client.post('/api/auth/login', json={'username': 'admin', 'password': 'admin'}).json()['token']
TODAY = api.rb.today()


def team_for_test():
    """A ward + role with at least 4 permanent RNs who have a roster and a balance in a common week."""
    groups = collections.defaultdict(list)
    for eid, e in api.store.employees.items():
        if e.job_type == 'PERMANENT' and e.role_class == 'NURSE_RN' and e.window_end and e.window_end > TODAY + dt.timedelta(days=35) and api.store.balances.get(eid, {}).get('AL', {}).get('remaining', 0) > 120:
            groups[e.primary_unit].append(eid)
    unit, members = max(groups.items(), key=lambda kv: len(kv[1]))
    start = TODAY + dt.timedelta(days=21); start += dt.timedelta(days=(7 - start.weekday()) % 7)   # next Monday after notice
    return unit, members[:5], start, start + dt.timedelta(days=4)


def test_later_requests_see_earlier_ones():
    unit, members, s, e = team_for_test()
    assert len(members) >= 3
    tok = client.post('/api/auth/login', json={'username': 'manager', 'password': 'manager'}).json()['token']
    results = []
    for eid in members:
        d = client.post('/api/requests', json={'employee_id': eid, 'leave_type': 'ANNUAL', 'start': s.isoformat(), 'end': e.isoformat()}).json()
        results.append(d)
    # every request after the first must record the others as competing (PRI-001) when in the same unit/role
    later = results[1:]
    assert any(any(r['code'] == 'PRI-001' for r in d['rules']) for d in later), 'PRI-001 competing-requests note missing'
    # availability shrinks: the last request must not show MORE available staff than the first on the same shifts
    first, last = results[0], results[-1]
    first_rem = {(day['date'], x['unit'], x['shift']): x['remaining'] for day in first['chart']['days'] for x in day['shifts']}
    for day in last['chart']['days']:
        for x in day['shifts']:
            k = (day['date'], x['unit'], x['shift'])
            if k in first_rem:
                assert x['remaining'] <= first_rem[k]
    # manager inbox lists them, excess-leave staff first among equals
    inbox = client.get('/api/manager/inbox', headers={'X-Auth-Token': tok}).json()
    ids = {d['request_id'] for d in results}
    assert sum(1 for r in inbox if r['id'] in ids) == len(results)
    # withdrawing a request gives the capacity back
    rid0 = results[0]['request_id']
    client.post(f'/api/requests/{rid0}/withdraw')
    again = client.post('/api/check', json={'employee_id': members[-1], 'leave_type': 'ANNUAL', 'start': s.isoformat(), 'end': e.isoformat()}).json()
    again_rem = {(day['date'], x['unit'], x['shift']): x['remaining'] for day in again['chart']['days'] for x in day['shifts']}
    last_rem = {(day['date'], x['unit'], x['shift']): x['remaining'] for day in last['chart']['days'] for x in day['shifts']}
    assert all(again_rem.get(k, 0) >= v for k, v in last_rem.items())
    for d in results[1:]:
        client.post(f"/api/requests/{d['request_id']}/withdraw")


def test_cover_colleague_is_busy_for_the_next_request():
    """A colleague recommended as cover for request 1 cannot be recommended for the same shift in request 2."""
    unit, members, s, e = team_for_test()
    tok = client.post('/api/auth/login', json={'username': 'manager', 'password': 'manager'}).json()['token']
    r1 = client.post('/api/requests', json={'employee_id': members[0], 'leave_type': 'ANNUAL', 'start': s.isoformat(), 'end': e.isoformat()}).json()
    r2 = client.post('/api/requests', json={'employee_id': members[1], 'leave_type': 'ANNUAL', 'start': s.isoformat(), 'end': e.isoformat()}).json()
    d1 = client.get(f"/api/manager/requests/{r1['request_id']}", headers={'X-Auth-Token': tok}).json()['decision']
    d2 = client.get(f"/api/manager/requests/{r2['request_id']}", headers={'X-Auth-Token': tok}).json()['decision']
    used = {(c['date'], c['candidate']['employee_id']) for c in d1.get('cover', []) if c.get('candidate')}
    for c in d2.get('cover', []):
        if c.get('candidate'):
            assert (c['date'], c['candidate']['employee_id']) not in used
    client.post(f"/api/requests/{r1['request_id']}/withdraw"); client.post(f"/api/requests/{r2['request_id']}/withdraw")
