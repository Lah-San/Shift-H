"""Calibration harness.

1. Builds labelled sample datasets from the real synthetic data:
   A. observational: leave the organisation actually booked (expected: not declined on coverage/eligibility grounds)
   B. constructed: cases whose correct answer is known by construction (expected rule codes / outcomes)
2. Runs the engine on both, reports accuracy per rule.
3. Searches the key parameters (baseline percentile, team-away cap, required fraction) for the setting
   that keeps dataset B at 100% and maximises agreement on dataset A, and writes it to the rulebook.

Run:  python calibrate.py [--apply]
Outputs: data/calibration_report.json, data/sample_dataset_A.csv, data/sample_dataset_B.csv
"""
import argparse, collections, copy, datetime as dt, json, os, sys, time
import polars as pl
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from leavecover import RuleBook, DataStore, Engine
from leavecover.models import Shift
from leavecover.rules import FatigueChecker

FAMILY_TO_TYPE = {'ANNUAL': 'ANNUAL', 'LONG_SERVICE': 'LONG_SERVICE', 'PERSONAL_SICK': 'SICK', 'PROF_DEV_STUDY': 'PROF_DEV', 'ADO_TOIL_ONCALL': 'ADO',
                  'PUBLIC_HOLIDAY_TOIL': 'TOIL', 'PARENTAL': 'PARENTAL', 'UNPAID': 'UNPAID', 'CULTURAL': 'CULTURAL', 'OTHER': 'UNPAID'}
OK_OUTCOMES = {'RECOMMEND_APPROVE', 'NEEDS_APPROVAL', 'ACCEPTED', 'APPROVED'}


def clone_rb(rb, settings=None, params=None):
    rb2 = copy.copy(rb); rb2.data = copy.deepcopy(rb.data); rb2._index = {r['id']: r for r in rb2.data['rules']}
    if settings:
        rb2.data['settings'].update(settings)
    for rid, p in (params or {}).items():
        rb2._index[rid].setdefault('params', {}).update(p)
    return rb2


# ----------------------------------------------------------------------------- dataset A
def dataset_a(store, rb, limit=4000):
    sc = pl.read_parquet(os.path.join(store.clean_dir, 'scenarios.parquet')).filter((pl.col('leave_start_date') > rb.today()) & (pl.col('leave_family').is_in(['ANNUAL', 'LONG_SERVICE', 'PROF_DEV_STUDY', 'ADO_TOIL_ONCALL', 'PUBLIC_HOLIDAY_TOIL'])))
    rows = []
    for r in sc.head(limit).iter_rows(named=True):
        rows.append({'employee_id': r['employee_id'], 'leave_type': FAMILY_TO_TYPE[r['leave_family']], 'start': r['leave_start_date'], 'end': r['leave_end_date'],
                     'expected': 'NOT_DECLINED_ON_COVERAGE', 'source': 'booked leave in HR system'})
    return rows


def run_a(engine, rows):
    agree = 0; codes = collections.Counter(); details = []
    for r in rows:
        d = engine.evaluate(r['employee_id'], r['leave_type'], r['start'], r['end'], '', with_alternatives=False, already_booked=True)
        cov_decline = d.outcome == 'DECLINED' and any(c.startswith('COV') for c in d.breached_codes)
        ok = not cov_decline
        agree += ok
        for c in d.breached_codes:
            codes[c] += 1
        details.append({**r, 'start': str(r['start']), 'end': str(r['end']), 'outcome': d.outcome, 'breached': '|'.join(d.breached_codes), 'pass': ok})
    return agree / max(1, len(rows)), codes, details


# ----------------------------------------------------------------------------- dataset B (known answers)
def dataset_b(store, rb, engine):
    today = rb.today(); cases = []
    emps = store.employees

    def pick(pred, n=1):
        out = []
        for eid, e in emps.items():
            if pred(e):
                out.append(eid)
                if len(out) >= n:
                    break
        return out

    def in_win(eid, days=5, ahead=21):
        e = emps[eid]; s = max(today + dt.timedelta(days=ahead), e.window_start); return s, s + dt.timedelta(days=days - 1)

    perm_rn = pick(lambda e: e.job_type == 'PERMANENT' and e.role_class == 'NURSE_RN' and e.window_end and e.window_end > today + dt.timedelta(days=30) and store.balances.get(e.employee_id, {}).get('AL', {}).get('remaining', 0) > 150, 5)
    for eid in perm_rn:
        s, e = in_win(eid)
        cases.append({'name': 'end before start', 'employee_id': eid, 'leave_type': 'ANNUAL', 'start': e, 'end': s, 'expect_codes': ['REQ-001'], 'expect_outcome': 'DECLINED'})
        cases.append({'name': 'unknown leave type', 'employee_id': eid, 'leave_type': 'BOGUS', 'start': s, 'end': e, 'expect_codes': ['REQ-002'], 'expect_outcome': 'DECLINED'})
        cases.append({'name': 'planned leave in the past', 'employee_id': eid, 'leave_type': 'ANNUAL', 'start': today - dt.timedelta(days=10), 'end': today - dt.timedelta(days=8), 'expect_codes': ['REQ-003'], 'expect_outcome': 'DECLINED'})
        cases.append({'name': 'sick leave today accepted', 'employee_id': eid, 'leave_type': 'SICK', 'start': today, 'end': today, 'expect_codes': [], 'expect_outcome': 'ACCEPTED', 'note': 'flu'})
        cases.append({'name': 'sick leave backdated 2 days accepted', 'employee_id': eid, 'leave_type': 'SICK', 'start': today - dt.timedelta(days=2), 'end': today, 'expect_codes': [], 'expect_outcome': 'ACCEPTED'})
        cases.append({'name': 'carer leave 4 days needs evidence note', 'employee_id': eid, 'leave_type': 'CARER', 'start': today, 'end': today + dt.timedelta(days=3), 'expect_info': ['SCK-002'], 'expect_outcome': 'ACCEPTED'})
        cases.append({'name': 'short notice annual -> manager', 'employee_id': eid, 'leave_type': 'ANNUAL', 'start': today + dt.timedelta(days=3), 'end': today + dt.timedelta(days=4), 'expect_codes': ['NOT-001'], 'expect_not_outcome': 'DECLINED_BY_NOTICE'})
        cases.append({'name': 'block over max length -> soft', 'employee_id': eid, 'leave_type': 'ANNUAL', 'start': today + dt.timedelta(days=30), 'end': today + dt.timedelta(days=30 + 100), 'expect_codes': ['REQ-004']})
        cases.append({'name': 'public holiday flagged', 'employee_id': eid, 'leave_type': 'ANNUAL', 'start': dt.date(2026, 9, 26), 'end': dt.date(2026, 9, 30), 'expect_info': ['PH-001']})
        cases.append({'name': 'beyond roster -> forecast', 'employee_id': eid, 'leave_type': 'ANNUAL', 'start': dt.date(2027, 2, 1), 'end': dt.date(2027, 2, 5), 'expect_info': ['HOR-001'], 'expect_forecast': True})
    for eid in pick(lambda e: e.job_type == 'CASUAL' and e.window_end and e.window_end > today + dt.timedelta(days=30) and store.balances.get(e.employee_id, {}).get('AL', {}).get('remaining', 0) <= 0, 3):
        s, e = in_win(eid)
        cases.append({'name': 'casual without balance: annual not available', 'employee_id': eid, 'leave_type': 'ANNUAL', 'start': s, 'end': e, 'expect_codes': ['ELG-002'], 'expect_outcome': 'DECLINED'})
        cases.append({'name': 'casual unpaid leave allowed', 'employee_id': eid, 'leave_type': 'UNPAID', 'start': s, 'end': e, 'expect_not_codes': ['ELG-002']})
    for eid in pick(lambda e: e.contract_end and today + dt.timedelta(days=20) < e.contract_end < today + dt.timedelta(days=60) and e.job_type != 'CASUAL' and store.balances.get(e.employee_id, {}).get('AL', {}).get('remaining', 0) > 50, 3):
        ce = emps[eid].contract_end
        cases.append({'name': 'leave after contract end -> manager confirms', 'employee_id': eid, 'leave_type': 'ANNUAL', 'start': ce + dt.timedelta(days=5), 'end': ce + dt.timedelta(days=6), 'expect_codes': ['ELG-001']})
    for eid in pick(lambda e: e.job_type == 'PERMANENT' and 0 < store.balances.get(e.employee_id, {}).get('AL', {}).get('remaining', 99) < 8 and e.window_end and e.window_end > today + dt.timedelta(days=35) and len(store.shifts_between(e.employee_id, today + dt.timedelta(days=21), today + dt.timedelta(days=30))) >= 3, 3):
        s, e = in_win(eid, 10)
        cases.append({'name': 'balance too low', 'employee_id': eid, 'leave_type': 'ANNUAL', 'start': s, 'end': e, 'expect_codes': ['BAL-001'], 'expect_outcome': 'DECLINED'})
    for eid in pick(lambda e: e.job_type == 'PERMANENT' and store.balances.get(e.employee_id, {}).get('LS', {}).get('remaining', 0) <= 0 and e.emp_start and (today - e.emp_start).days < 5 * 365 and store.balances.get(e.employee_id, {}), 3):
        s, e = in_win(eid)
        cases.append({'name': 'LSL without entitlement', 'employee_id': eid, 'leave_type': 'LONG_SERVICE', 'start': s, 'end': e, 'expect_codes': ['ELG-003'], 'expect_outcome': 'DECLINED'})
    for eid in pick(lambda e: e.job_type == 'PERMANENT' and store.balances.get(e.employee_id, {}).get('LS', {}).get('remaining', 0) > 200 and e.window_end and e.window_end > today + dt.timedelta(days=70), 3):
        e0 = emps[eid]; s = max(today + dt.timedelta(days=int(rb.agreement(e0.agreement_family).get('lsl_notice_days_employee', 56)) + 1), e0.window_start); en = s + dt.timedelta(days=6)
        cases.append({'name': 'LSL with entitlement and notice: eligible', 'employee_id': eid, 'leave_type': 'LONG_SERVICE', 'start': s, 'end': en, 'expect_not_codes': ['ELG-003', 'NOT-002']})
    # excess leave priority
    for eid in pick(lambda e: e.job_type == 'PERMANENT' and store.balances.get(e.employee_id, {}).get('AL', {}).get('remaining', 0) > 500 and e.window_end and e.window_end > today + dt.timedelta(days=30), 3):
        s, e = in_win(eid)
        cases.append({'name': 'excess leave flagged', 'employee_id': eid, 'leave_type': 'ANNUAL', 'start': s, 'end': e, 'expect_info': ['BAL-002']})
    return cases


def run_b(store, rb, engine, cases):
    results = []; passed = 0
    for c in cases:
        d = engine.evaluate(c['employee_id'], c['leave_type'], c['start'], c['end'], c.get('note', ''), with_alternatives=False)
        codes = set(d.breached_codes); infos = {r.code for r in d.rules}
        ok = True; why = []
        for x in c.get('expect_codes', []):
            if x not in codes:
                ok = False; why.append(f'missing {x}')
        for x in c.get('expect_not_codes', []):
            if x in codes:
                ok = False; why.append(f'unexpected {x}')
        for x in c.get('expect_info', []):
            if x not in infos:
                ok = False; why.append(f'missing info {x}')
        if c.get('expect_outcome') and d.outcome != c['expect_outcome']:
            ok = False; why.append(f'outcome {d.outcome} != {c["expect_outcome"]}')
        if c.get('expect_forecast') is not None and d.forecast_mode != c['expect_forecast']:
            ok = False; why.append('forecast flag')
        passed += ok
        results.append({'name': c['name'], 'employee_id': c['employee_id'], 'leave_type': c['leave_type'], 'start': str(c['start']), 'end': str(c['end']), 'outcome': d.outcome, 'breached': '|'.join(sorted(codes)), 'pass': ok, 'why': '; '.join(why)})
    # structural guarantees on cover plans
    fat = FatigueChecker(store, rb); n_plans = 0; viol = 0
    for eid, e in store.employees.items():
        if n_plans >= 60:
            break
        if e.job_type == 'PERMANENT' and e.role_class.startswith('NURSE') and e.window_end and e.window_end > rb.today() + dt.timedelta(days=28) and store.balances.get(eid, {}).get('AL', {}).get('remaining', 0) > 100:
            s = max(rb.today() + dt.timedelta(days=21), e.window_start); d = engine.evaluate(eid, 'ANNUAL', s, s + dt.timedelta(days=4), '', with_alternatives=False)
            n_plans += 1
            seen = set()
            for c in d.cover:
                if c.status == 'covered':
                    cand = c.candidate
                    if cand.employee_id == eid or (cand.employee_id, c.date) in seen or store.is_on_leave(cand.employee_id, c.date) or cand.role_class not in rb.cover_matrix()[c.role_class]:
                        viol += 1
                    seen.add((cand.employee_id, c.date))
    results.append({'name': f'cover plans: no double booking, no leave clash, role matrix respected ({n_plans} plans)', 'pass': viol == 0, 'why': f'{viol} violations', 'outcome': '', 'breached': '', 'employee_id': '', 'leave_type': '', 'start': '', 'end': ''})
    passed += (viol == 0)
    # fatigue engine unit checks with synthetic schedules
    e = next(x for x in store.employees.values() if x.agreement_family == 'ANF')
    base = dt.date(2027, 6, 7)
    prev = Shift(base, 1260, 450, 630, 10, 'X', 'NIGHT', 'T')          # night ending 07:30 next day
    new = Shift(base + dt.timedelta(days=1), 780, 1290, 510, 8, 'X', 'PM', 'T')   # PM 13:00 same day as night ends -> 5.5 h rest
    fails = {f.code for f in fat.check(e, new, [prev])}
    results.append({'name': 'fatigue: 5.5 h after a night is rejected (FAT-001/FAT-002)', 'pass': bool(fails & {'FAT-001', 'FAT-002'}), 'why': str(fails), 'outcome': '', 'breached': '', 'employee_id': e.employee_id, 'leave_type': '', 'start': '', 'end': ''}); passed += bool(fails & {'FAT-001', 'FAT-002'})
    run = [Shift(base + dt.timedelta(days=i), 420, 930, 510, 8, 'X', 'AM', 'T') for i in range(7)]
    new8 = Shift(base + dt.timedelta(days=7), 420, 930, 510, 8, 'X', 'AM', 'T')
    fails = {f.code for f in fat.check(e, new8, run)}
    results.append({'name': 'fatigue: 8th consecutive duty rejected (FAT-003)', 'pass': 'FAT-003' in fails, 'why': str(fails), 'outcome': '', 'breached': '', 'employee_id': e.employee_id, 'leave_type': '', 'start': '', 'end': ''}); passed += 'FAT-003' in fails
    ok6 = [Shift(base + dt.timedelta(days=i), 420, 930, 510, 8, 'X', 'AM', 'T') for i in range(4)]
    fails = {f.code for f in fat.check(e, Shift(base + dt.timedelta(days=4), 420, 930, 510, 8, 'X', 'AM', 'T'), ok6)}
    results.append({'name': 'fatigue: 5th consecutive day with 2 days off is fine', 'pass': not fails, 'why': str(fails), 'outcome': '', 'breached': '', 'employee_id': e.employee_id, 'leave_type': '', 'start': '', 'end': ''}); passed += (not fails)
    return passed / len(results), results


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(); ap.add_argument('--apply', action='store_true'); ap.add_argument('--limit', type=int, default=3000)
    a = ap.parse_args()
    os.chdir(os.path.dirname(os.path.abspath(__file__))); os.makedirs('data', exist_ok=True)
    rb = RuleBook(); store = DataStore(rulebook=rb); engine = Engine(store, rb)
    A = dataset_a(store, rb, a.limit); B = dataset_b(store, rb, engine)
    report = {'run_at': dt.datetime.now().isoformat(timespec='seconds'), 'dataset_A_size': len(A), 'dataset_B_size': len(B), 'grid': []}
    best = None
    grid = [(0.25, 0.25), (0.25, 0.34), (0.25, 0.5), (0.1, 0.34), (0.35, 0.34), (0.5, 0.34)]
    for pct, cap in grid:
        rb2 = clone_rb(rb, {'baseline_percentile': pct}, {'COV-002': {'max_fraction_on_leave': cap}})
        st2 = DataStore(rulebook=rb2) if pct != store.rb.settings.get('baseline_percentile') else store
        eng2 = Engine(st2, rb2)
        t = time.time()
        accA, codesA, detA = run_a(eng2, A)
        accB, detB = run_b(st2, rb2, eng2, B)
        row = {'baseline_percentile': pct, 'team_away_cap': cap, 'A_agreement': round(accA, 4), 'B_accuracy': round(accB, 4), 'A_rule_hits': dict(codesA.most_common(8)), 'seconds': round(time.time() - t, 1)}
        report['grid'].append(row); print(row)
        if accB >= 0.999 and (best is None or accA > best[0]):
            best = (accA, pct, cap, detA, detB)
    if best:
        accA, pct, cap, detA, detB = best
        report['chosen'] = {'baseline_percentile': pct, 'team_away_cap': cap, 'A_agreement': round(accA, 4)}
        pl.DataFrame(detA).write_csv('data/sample_dataset_A.csv'); pl.DataFrame(detB).write_csv('data/sample_dataset_B.csv')
        report['A_disagreements'] = collections.Counter(d['breached'] for d in detA if not d['pass']).most_common(10)
        report['B_failures'] = [d for d in detB if not d['pass']]
        if a.apply:
            rb.update_settings({'baseline_percentile': pct}, actor='calibration')
            rb.update_rule('COV-002', params={'max_fraction_on_leave': cap}, actor='calibration')
            report['applied'] = True
    json.dump(report, open('data/calibration_report.json', 'w'), indent=2, default=str)
    print(json.dumps({k: v for k, v in report.items() if k != 'grid'}, indent=2, default=str))


if __name__ == '__main__':
    main()
