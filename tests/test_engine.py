"""Engine tests: rule behaviour, perturbation checks and hard guarantees.
Run:  python -m pytest tests -q"""
import datetime as dt, os, sys, copy
import pytest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from leavecover import RuleBook, DataStore, Engine
from leavecover.models import Shift
from leavecover.rules import FatigueChecker

rb = RuleBook()
store = DataStore(rulebook=rb)
engine = Engine(store, rb)
TODAY = rb.today()


def find_employee(pred, n=1):
    out = []
    for eid, e in store.employees.items():
        if pred(e):
            out.append(eid)
            if len(out) >= n:
                break
    return out


def in_window_dates(eid, days=5):
    e = store.employees[eid]
    s = max(TODAY + dt.timedelta(days=21), e.window_start)
    return s, s + dt.timedelta(days=days - 1)


# ----------------------------------------------------------------------------- basics
def test_invalid_dates_declined():
    eid = next(iter(store.employees))
    d = engine.evaluate(eid, 'ANNUAL', TODAY + dt.timedelta(days=30), TODAY + dt.timedelta(days=20))
    assert d.outcome == 'DECLINED' and 'REQ-001' in d.breached_codes


def test_unknown_leave_type():
    eid = next(iter(store.employees))
    d = engine.evaluate(eid, 'BOGUS', TODAY + dt.timedelta(days=30), TODAY + dt.timedelta(days=31))
    assert 'REQ-002' in d.breached_codes


def test_backdated_planned_leave_declined():
    eid = next(iter(store.employees))
    d = engine.evaluate(eid, 'ANNUAL', TODAY - dt.timedelta(days=10), TODAY - dt.timedelta(days=5))
    assert 'REQ-003' in d.breached_codes and d.outcome == 'DECLINED'


def test_sick_leave_accepted_and_backdate_allowed():
    eid = find_employee(lambda e: e.job_type == 'PERMANENT' and e.role_class == 'NURSE_RN')[0]
    d = engine.evaluate(eid, 'SICK', TODAY - dt.timedelta(days=1), TODAY, 'flu')
    assert d.outcome == 'ACCEPTED' and not d.requires_manager
    assert 'SCK-001' in [r.code for r in d.rules]


def test_casual_cannot_take_paid_annual_leave():
    eid = find_employee(lambda e: e.job_type == 'CASUAL' and e.window_end and e.window_end > TODAY + dt.timedelta(days=30) and store.balances.get(e.employee_id, {}).get('AL', {}).get('remaining', 0) <= 0)[0]
    s, e = in_window_dates(eid)
    d = engine.evaluate(eid, 'ANNUAL', s, e)
    assert 'ELG-002' in d.breached_codes and d.outcome == 'DECLINED'
    d2 = engine.evaluate(eid, 'UNPAID', s, e)
    assert 'ELG-002' not in d2.breached_codes


def test_contract_end_blocks_leave():
    eid = find_employee(lambda e: e.contract_end and TODAY + dt.timedelta(days=20) < e.contract_end < TODAY + dt.timedelta(days=60) and e.job_type != 'CASUAL')[0]
    ce = store.employees[eid].contract_end
    d = engine.evaluate(eid, 'ANNUAL', ce + dt.timedelta(days=5), ce + dt.timedelta(days=8))
    assert 'ELG-001' in d.breached_codes


def test_balance_rule_blocks_when_insufficient():
    eid = find_employee(lambda e: e.job_type == 'PERMANENT' and 'AL' in store.balances.get(e.employee_id, {}) and store.balances[e.employee_id]['AL']['remaining'] < 8 and e.window_end and e.window_end > TODAY + dt.timedelta(days=30) and store.shifts_by_emp.get(e.employee_id))[0]
    s, e = in_window_dates(eid, 10)
    d = engine.evaluate(eid, 'ANNUAL', s, e)
    assert 'BAL-001' in d.breached_codes


def test_short_notice_needs_manager():
    eid = find_employee(lambda e: e.job_type == 'PERMANENT' and 'AL' in store.balances.get(e.employee_id, {}) and store.balances[e.employee_id]['AL']['remaining'] > 200 and e.window_end and e.window_end > TODAY + dt.timedelta(days=10))[0]
    d = engine.evaluate(eid, 'ANNUAL', TODAY + dt.timedelta(days=3), TODAY + dt.timedelta(days=4))
    assert 'NOT-001' in d.breached_codes and d.outcome in ('NEEDS_APPROVAL', 'DECLINED')


# ----------------------------------------------------------------------------- toggles change behaviour
def test_toggle_rule_off_changes_outcome():
    eid = find_employee(lambda e: e.job_type == 'CASUAL' and e.window_end and e.window_end > TODAY + dt.timedelta(days=30) and store.balances.get(e.employee_id, {}).get('AL', {}).get('remaining', 0) <= 0)[0]
    s, e = in_window_dates(eid)
    rb2 = copy.copy(rb); rb2.data = copy.deepcopy(rb.data); rb2._index = {r['id']: r for r in rb2.data['rules']}
    rb2._index['ELG-002']['enabled'] = False
    d = Engine(store, rb2).evaluate(eid, 'ANNUAL', s, e)
    assert 'ELG-002' not in d.breached_codes


def test_locked_rule_cannot_be_disabled():
    with pytest.raises(ValueError):
        rb.update_rule('GOV-001', enabled=False)
    rb.reload()


# ----------------------------------------------------------------------------- cover plan guarantees
def _plans(n=60):
    out = []
    for eid, e in store.employees.items():
        if e.job_type == 'PERMANENT' and e.window_end and e.window_end > TODAY + dt.timedelta(days=28) and store.balances.get(eid, {}).get('AL', {}).get('remaining', 0) > 100:
            s, en = in_window_dates(eid, 5)
            out.append(engine.evaluate(eid, 'ANNUAL', s, en, with_alternatives=False))
            if len(out) >= n:
                break
    return out


def test_cover_plan_never_double_books_or_breaks_fatigue():
    fat = FatigueChecker(store, rb)
    for d in _plans():
        seen = set()
        for c in d.cover:
            if c.status != 'covered':
                continue
            cand = c.candidate
            assert cand.employee_id != d.employee_id
            key = (cand.employee_id, c.date)
            assert key not in seen, 'same person assigned twice on one day'
            seen.add(key)
            assert not store.is_on_leave(cand.employee_id, c.date)
            # role compatibility
            assert cand.role_class in rb.cover_matrix()[c.role_class]
            if cand.tier != 'redeploy':
                # combined-schedule fatigue check must pass (with the other plan shifts for that person)
                extras = [Shift(x.date, 0, 0, 0, x.hours, x.unit, x.category, 'COVER') for x in d.cover if x.status == 'covered' and x.candidate and x.candidate.employee_id == cand.employee_id and x is not c]
                new = Shift(c.date, 420, 930, 510, c.hours, c.unit, c.category, 'COVER')
                # only assert rest/consecutive style rules; exact times of forecast shifts are approximations
                fails = [f.code for f in fat.check(store.employees[cand.employee_id], new, extras)]
                assert 'FAT-004' not in fails and 'FAT-003' not in fails


def test_removing_candidate_pool_flips_outcome():
    """Perturbation: disable every cover tier -> shifts that needed cover become uncovered."""
    plans = [d for d in _plans(80) if any(c.status == 'covered' for c in d.cover)]
    assert plans, 'need at least one plan with cover'
    d0 = plans[0]
    rb2 = copy.copy(rb); rb2.data = copy.deepcopy(rb.data); rb2._index = {r['id']: r for r in rb2.data['rules']}
    for t in rb2.data['cover_tiers']:
        t['enabled'] = False
    d1 = Engine(store, rb2).evaluate(d0.employee_id, 'ANNUAL', d0.start, d0.end, with_alternatives=False)
    assert all(c.status != 'covered' for c in d1.cover)
    assert 'COV-001' in d1.breached_codes


def test_alternatives_are_valid_and_different():
    plans = [d for d in _plans(120) if d.outcome in ('DECLINED', 'NEEDS_APPROVAL')]
    for d in plans[:5]:
        alts = engine.alternatives(store.employees[d.employee_id], 'ANNUAL', d.start, d.end, TODAY)
        for a in alts:
            assert (a.start, a.end) != (d.start, d.end)
            assert (a.end - a.start) == (d.end - d.start)
            assert a.start > TODAY


def test_staff_view_is_anonymised():
    d = _plans(5)[0]
    view = Engine.to_dict(d, 'staff')
    txt = str(view)
    for c in d.cover:
        if c.candidate:
            assert c.candidate.employee_id not in txt


def test_speed():
    import time
    ds = _plans(30)
    t = time.perf_counter()
    for d in ds:
        engine.evaluate(d.employee_id, 'ANNUAL', d.start, d.end, with_alternatives=False)
    per = (time.perf_counter() - t) / len(ds) * 1000
    assert per < 100, f'{per:.1f} ms per evaluation'
