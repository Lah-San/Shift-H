"""Invariants that must hold for every decision the engine makes. Run over a broad sample of real scenarios."""
import datetime as dt, os, sys, copy, random
import polars as pl
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['LEAVECOVER_NO_GEMINI'] = '1'
from leavecover import RuleBook, DataStore, Engine
from leavecover.models import Shift
from leavecover.rules import FatigueChecker
from leavecover import periods

rb = RuleBook(); store = DataStore(rulebook=rb); engine = Engine(store, rb)
TODAY = rb.today()
FAM = {'ANNUAL': 'ANNUAL', 'LONG_SERVICE': 'LONG_SERVICE', 'PERSONAL_SICK': 'SICK', 'PROF_DEV_STUDY': 'PROF_DEV', 'ADO_TOIL_ONCALL': 'ADO', 'PUBLIC_HOLIDAY_TOIL': 'TOIL', 'PARENTAL': 'PARENTAL', 'UNPAID': 'UNPAID', 'CULTURAL': 'CULTURAL', 'OTHER': 'UNPAID'}


def sample(n=150, seed=7):
    sc = pl.read_parquet(os.path.join(store.clean_dir, 'scenarios.parquet')).filter(pl.col('leave_start_date') > TODAY)
    rows = sc.to_dicts(); random.Random(seed).shuffle(rows)
    return [(r['employee_id'], FAM.get(r['leave_family'], 'ANNUAL'), r['leave_start_date'], r['leave_end_date']) for r in rows[:n]]


DECISIONS = [(eid, lt, s, e, engine.evaluate(eid, lt, s, e, '', with_alternatives=False)) for eid, lt, s, e in sample()]


def test_outcome_consistency():
    for eid, lt, s, e, d in DECISIONS:
        hard = [r for r in d.rules if r.severity == 'hard' and not r.passed]
        soft = [r for r in d.rules if r.severity == 'soft' and not r.passed]
        if d.outcome == 'DECLINED':
            assert hard, f'{eid} declined without a hard breach'
        if d.outcome == 'RECOMMEND_APPROVE':
            assert not hard and not soft
        if d.outcome == 'NEEDS_APPROVAL':
            assert not hard and soft
        assert d.requires_manager == (d.outcome in ('RECOMMEND_APPROVE', 'NEEDS_APPROVAL'))
        assert set(d.breached_codes) == {r.code for r in d.rules if not r.passed}


def test_cover_lines_match_demand_and_are_safe():
    fat = FatigueChecker(store, rb)
    for eid, lt, s, e, d in DECISIONS:
        assert len(d.cover) == len(d.demand)
        seen = set()
        for c in d.cover:
            assert c.status in ('covered', 'uncovered', 'not_needed')
            if c.status == 'covered':
                cand = c.candidate
                assert cand.employee_id != eid
                assert (cand.employee_id, c.date) not in seen; seen.add((cand.employee_id, c.date))
                assert not store.is_on_leave(cand.employee_id, c.date)
                assert cand.role_class in rb.cover_matrix()[c.role_class]
                emp = store.employees[cand.employee_id]
                if c.role_class in rb.data['same_position_required_for']:
                    assert emp.position_name == store.employees[eid].position_name
                assert not (emp.contract_end and emp.contract_end < c.date)
                if cand.tier != 'redeploy':
                    if store.in_window(cand.employee_id, c.date):
                        assert not any(x.category in ('AM', 'PM', 'NIGHT', 'LONG_DAY', 'DAY', 'ONCALL_24H') for x in store.shifts_on(cand.employee_id, c.date)), 'candidate already rostered that day'
                    def _ds(line):
                        return next(x for x in d.demand if x.date == line.date and x.unit == line.unit and x.category == line.category)
                    extras = [Shift(x.date, _ds(x).start_min, (_ds(x).start_min + _ds(x).span_min) % 1440, _ds(x).span_min, x.hours, x.unit, x.category, 'COVER') for x in d.cover if x.status == 'covered' and x.candidate and x.candidate.employee_id == cand.employee_id and x is not c]
                    ds = _ds(c)
                    new = Shift(c.date, ds.start_min, (ds.start_min + ds.span_min) % 1440, ds.span_min, c.hours, c.unit, c.category, 'COVER')
                    fails = [f.code for f in fat.check(emp, new, extras)]
                    assert not fails, f'{cand.employee_id} fails {fails} covering {c.date}'
                assert cand.cost_multiplier >= 1.0 and cand.hours == c.hours


def test_gap_logic():
    for eid, lt, s, e, d in DECISIONS:
        for ds in d.demand:
            assert ds.gap == (ds.available_after < ds.required)
            assert ds.available_after >= 0 and ds.required >= 0
            assert s <= ds.date <= e


def test_staff_view_is_anonymised_and_serialisable():
    import json
    for eid, lt, s, e, d in DECISIONS:
        v = Engine.to_dict(d, 'staff'); json.dumps(v, default=str)
        txt = json.dumps(v, default=str)
        for c in d.cover:
            if c.candidate:
                assert c.candidate.employee_id not in txt
        m = Engine.to_dict(d, 'manager'); json.dumps(m, default=str)
        assert m['summary']['shifts'] == len(d.demand)


def test_determinism():
    for eid, lt, s, e, d in DECISIONS[:40]:
        d2 = engine.evaluate(eid, lt, s, e, '', with_alternatives=False)
        assert d2.outcome == d.outcome and d2.breached_codes == d.breached_codes
        assert [(c.status, c.candidate.employee_id if c.candidate else None) for c in d2.cover] == [(c.status, c.candidate.employee_id if c.candidate else None) for c in d.cover]


def test_absence_monotonicity():
    """Adding another absence on the same ward never increases availability for the next requester."""
    picked = [x for x in DECISIONS if x[4].demand and x[4].outcome != 'DECLINED'][:15]
    for eid, lt, s, e, d in picked:
        emp = store.employees[eid]
        others = [m for m in store.unit_role_members.get((emp.primary_unit, emp.role_class), set()) if m != eid]
        if not others:
            continue
        other = others[0]
        base = {(x.date, x.unit, x.category): x.available_after for x in d.demand}
        store.add_absence(other, [s + dt.timedelta(days=i) for i in range((e - s).days + 1)], 'inv')
        try:
            d2 = engine.evaluate(eid, lt, s, e, '', with_alternatives=False)
            for x in d2.demand:
                assert x.available_after <= base[(x.date, x.unit, x.category)]
        finally:
            store.remove_absence(other, 'inv')
        d3 = engine.evaluate(eid, lt, s, e, '', with_alternatives=False)
        assert {(x.date, x.unit, x.category): x.available_after for x in d3.demand} == base, 'overlay not fully reverted'


def test_alternatives_and_split_plans_pass():
    from leavecover.suggestions import staff_suggestions
    checked = 0
    for eid, lt, s, e, d in DECISIONS:
        if d.outcome in ('DECLINED', 'NEEDS_APPROVAL') and checked < 8:
            for a in engine.alternatives(store.employees[eid], lt, s, e, TODAY):
                da = engine.evaluate(eid, lt, a.start, a.end, '', with_alternatives=False)
                assert da.outcome != 'DECLINED' and (a.end - a.start) == (e - s)
            checked += 1
    for eid in ['SYN000009']:
        out = staff_suggestions(store, rb, engine, eid, 12, 14, 'ANNUAL')
        for w in out['windows']:
            assert w['outcome'] != 'DECLINED'
        out90 = staff_suggestions(store, rb, engine, eid, 16, 90, 'ANNUAL')
        assert not out90['windows'] or all(w['outcome'] != 'DECLINED' for w in out90['windows'])
        if out90.get('split_plan'):
            blocks = out90['split_plan']['blocks']
            for b in blocks:
                assert b['outcome'] != 'DECLINED'
            for a, b in zip(blocks, blocks[1:]):
                assert dt.date.fromisoformat(b['start']) >= dt.date.fromisoformat(a['end']) + dt.timedelta(days=7)


def test_period_resolution():
    r = periods.resolve('two weeks for Christmas', TODAY, 14)
    assert r and r['anchor'] == dt.date(2026, 12, 25) and r['range_start'] <= dt.date(2026, 12, 12) <= r['range_end']
    assert periods.resolve('after christmas', TODAY)['range_start'] == dt.date(2026, 12, 27)
    assert periods.resolve('easter', TODAY)['anchor'] == dt.date(2027, 3, 28)
    assert periods.resolve('leave in December', TODAY)['range_start'] == dt.date(2026, 12, 1)
    assert periods.resolve('nothing here', TODAY) is None
    assert periods.length_from_text('a fortnight off') == 14 and periods.length_from_text('3 months') == 90 and periods.length_from_text('x', 0) == 0


def test_balance_maths():
    for eid, lt, s, e, d in DECISIONS:
        r = next((x for x in d.rules if x.code == 'BAL-001'), None)
        if r and not r.passed:
            assert d.outcome == 'DECLINED'
            o = d.chart.get('balance_options')
            assert o and o['short_hours'] > 0 and any(x['kind'] == 'manager' for x in o['options'])


def test_sick_leave_always_accepted():
    for eid, lt, s, e, d in DECISIONS:
        if lt == 'SICK' and store.employees[eid].job_type != 'CASUAL' and s <= TODAY + dt.timedelta(days=0):
            assert d.outcome == 'ACCEPTED'


def test_named_holidays_resolve_from_the_wa_calendar():
    r = periods.resolve("king's birthday", TODAY, 3, store.holidays)
    assert r and r['anchor'] == dt.date(2026, 9, 28) and 'Monday 28 September 2026' in r['note'] and r['range_start'] <= dt.date(2026, 9, 25) <= r['range_end']
    r = periods.resolve('anzac day', TODAY, 7, store.holidays)
    assert r['anchor'] == dt.date(2027, 4, 25) and r['substitute'] == '2027-04-26'
    r = periods.resolve('kings birthday 2027', TODAY, 3, store.holidays)
    assert r['anchor'] == dt.date(2027, 9, 27)
    assert periods.resolve('labour day', TODAY, 3, store.holidays)['anchor'] == dt.date(2027, 3, 1)
    assert periods.resolve('christmas', TODAY, 14, store.holidays)['anchor'] == dt.date(2026, 12, 25)
    assert periods.length_from_text('the long weekend') == 4
