"""The staffing model must be right by construction: rosters are published per person in staggered windows, so a ward's
roster for a date is often only partly published. These tests pin the hybrid count, the learned requirement and the
live overlay so recursive changes cannot silently break them."""
import datetime as dt, os, sys, collections, copy
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['LEAVECOVER_NO_GEMINI'] = '1'
from leavecover import RuleBook, DataStore, Engine
from leavecover.models import Shift
from leavecover.coverage import requirement

rb = RuleBook(); store = DataStore(rulebook=rb); engine = Engine(store, rb)
TODAY = rb.today()
ROLES = ('NURSE_RN', 'NURSE_EN', 'ALLIED_PROF', 'MED_SENIOR', 'MED_JUNIOR', 'SUPPORT_OTHER')


def _big_units(n=40):
    sized = sorted(store.units, key=lambda u: -sum(len(v) for v in store.unit_staff[u].values()))
    return sized[:n]


def test_requirement_never_exceeds_what_the_ward_usually_ran():
    """The learned requirement is a floor the ward met on at least 90% of peak days, zeros included."""
    for u in _big_units():
        for role in ROLES:
            for cat in ('AM', 'PM', 'NIGHT'):
                for dow in range(1, 8):
                    k = (u, dow, cat, role)
                    if k not in store.baseline:
                        continue
                    req = store.baseline[k]
                    days = [d for d in (store.peak_start + dt.timedelta(days=i) for i in range((store.peak_end - store.peak_start).days + 1)) if d.isoweekday() == dow]
                    counts = [int(store.effective.get((u, d, cat), {}).get(role, 0)) for d in days]
                    below = sum(1 for c in counts if c < req)
                    assert below <= len(counts) * 0.2 + 1, f'{k}: requirement {req} but {below}/{len(counts)} days were below it'
                    assert req <= store.baseline_mid[k]


def test_roles_staffed_on_few_days_have_no_requirement():
    found = 0
    for u in _big_units():
        for role in ROLES:
            for cat in ('AM', 'PM'):
                for dow in range(1, 8):
                    k = (u, dow, cat, role)
                    if k not in store.baseline:
                        continue
                    days = [store.peak_start + dt.timedelta(days=i) for i in range((store.peak_end - store.peak_start).days + 1)]
                    present = sum(1 for d in days if d.isoweekday() == dow and store.effective.get((u, d, cat), {}).get(role, 0) > 0)
                    total = sum(1 for d in days if d.isoweekday() == dow)
                    if present < total / 2:
                        found += 1
                        assert store.baseline[k] == 0, f'{k}: staffed on {present}/{total} days but requirement {store.baseline[k]}'
    assert found > 0


def test_completeness_full_in_peak_and_falls_afterwards():
    for u in _big_units(15):
        assert store.roster_completeness(u, store.peak_start + dt.timedelta(days=21)) >= 0.9
        assert store.roster_completeness(u, store.max_date - dt.timedelta(days=3)) < 0.5


def test_hybrid_count_matches_usual_staffing_where_nothing_is_published():
    """Far in the tail almost every roster is unpublished: the hybrid count must land near the usual (median) staffing,
    not the raw handful of published shifts."""
    d = store.max_date - dt.timedelta(days=6)
    checked = 0
    for u in _big_units(30):
        for role in ('NURSE_RN', 'ALLIED_PROF', 'NURSE_EN'):
            med = store.usual_for(u, d, 'AM', role)
            if med < 4:
                continue
            n, comp = store.expected_staff(u, d, 'AM', role)
            real = int(store.effective.get((u, d, 'AM'), {}).get(role, 0))
            assert n >= real
            assert abs(n - med) <= max(2, 0.35 * med), f'{u} {role} {d}: hybrid {n} vs usual {med} (published {real})'
            checked += 1
    assert checked >= 10


def test_hybrid_count_equals_published_roster_in_peak():
    d = store.peak_start + dt.timedelta(days=22)
    for u in _big_units(30):
        for role in ROLES:
            n, comp = store.expected_staff(u, d, 'AM', role)
            real = int(store.effective.get((u, d, 'AM'), {}).get(role, 0))
            assert comp >= 0.85
            # the unpublished share can add at most that many people
            assert n - real <= max(1, int((1 - comp) * len(store._unit_members[u]) + 1))


def test_excluding_the_requester_removes_exactly_them():
    d = TODAY + dt.timedelta(days=24)
    n_checked = 0
    for eid, e in list(store.employees.items())[:3000]:
        if not e.primary_unit or e.role_class not in ROLES:
            continue
        shs = [s for s in store.shifts_on(eid, d) if s.category == 'AM' and s.unit == e.primary_unit]
        if store.in_window(eid, d) and not shs:
            continue
        base, _ = store.expected_staff(e.primary_unit, d, 'AM', e.role_class)
        excl, _ = store.expected_staff(e.primary_unit, d, 'AM', e.role_class, exclude=eid)
        assert 0 <= base - excl <= 1
        n_checked += 1
        if n_checked > 200:
            break
    assert n_checked > 50


def test_cover_overlay_is_exactly_reversible():
    """One request can give the same colleague several shifts under one key; apply then remove must leave the cube untouched."""
    u = _big_units(1)[0]
    role = 'NURSE_RN'
    cand = next(iter(store.unit_role_members.get((u, role), ())) or iter(store.unit_staff[u][role]))
    days = [TODAY + dt.timedelta(days=i) for i in (10, 11, 12)]
    keys = [(u, d, 'AM') for d in days]
    before = {k: dict(store.effective.get(k, {})) for k in keys}
    for d in days:   # same key each time, like _apply_overlay does per cover line
        store.add_cover(cand, [Shift(d, 420, 930, 510, 8.5, u, 'AM', 'COVER')], 'req-x')
    assert len(store.extra_shifts(cand)) == 3
    assert all(store.effective[k][role] == before[k].get(role, 0) + 1 for k in keys if k in before and before[k])
    store.remove_cover(cand, 'req-x')
    store.remove_cover(cand, 'req-x')   # second removal is a no-op
    after = {k: dict(store.effective.get(k, {})) for k in keys}
    assert after == before and not store.extra_shifts(cand)


def test_absence_overlay_is_exactly_reversible_and_visible_to_the_engine():
    u = _big_units(1)[0]
    role = 'NURSE_RN'
    members = sorted(m for m in store.unit_role_members.get((u, role), ()) if store.in_window(m, TODAY + dt.timedelta(days=5)))
    assert len(members) >= 2
    a, d = next((m, x) for m in members for x in (TODAY + dt.timedelta(days=i) for i in range(5, 30))
                if any(s.unit == u and s.category == 'AM' for s in store.shifts_on(m, x)) and store.in_window(m, x) and not store.is_on_leave(m, x))
    n0, _ = store.expected_staff(u, d, 'AM', role)
    store.add_absence(a, [d], 'inv-a')
    n1, _ = store.expected_staff(u, d, 'AM', role)
    assert n1 == n0 - 1 and store.is_on_leave(a, d)
    store.add_absence(a, [d], 'inv-b')          # second request on the same day does not double count
    assert store.expected_staff(u, d, 'AM', role)[0] == n1
    store.remove_absence(a, 'inv-a')
    assert store.expected_staff(u, d, 'AM', role)[0] == n1 and store.is_on_leave(a, d)
    store.remove_absence(a, 'inv-b')
    assert store.expected_staff(u, d, 'AM', role)[0] == n0 and not store.is_on_leave(a, d)


def test_gap_uses_hybrid_count_and_requirement():
    """For every demand shift the engine's numbers must be reproducible from the store."""
    sample = [e for e in store.employees.values() if e.primary_unit and e.role_class == 'NURSE_RN' and e.window_end and e.window_end > TODAY + dt.timedelta(days=20)][:40]
    for e in sample:
        s = TODAY + dt.timedelta(days=15)
        dec = engine.evaluate(e.employee_id, 'ANNUAL', s, s + dt.timedelta(days=6), '', with_alternatives=False)
        for ds in dec.demand:
            n, comp = store.expected_staff(ds.unit, ds.date, ds.category, ds.role_class, exclude=e.employee_id)
            assert ds.available_after == n
            assert ds.required == requirement(store, rb, ds.unit, ds.date, ds.category, ds.role_class)[0]
            assert ds.gap == (n < ds.required)
