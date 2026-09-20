"""Print a ready-to-run demo use case for concurrent leave requests: a ward and week where
submitting one request after another changes the outcome for the later ones."""
import collections, datetime as dt, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ['LEAVECOVER_NO_GEMINI'] = '1'
from leavecover import RuleBook, DataStore, Engine
rb = RuleBook(); s = DataStore(rulebook=rb); eng = Engine(s, rb)
TODAY = rb.today()
start = TODAY + dt.timedelta(days=21); start += dt.timedelta(days=(7 - start.weekday()) % 7); end = start + dt.timedelta(days=4)
days = [start + dt.timedelta(days=k) for k in range(5)]
groups = collections.defaultdict(list)
for eid, e in s.employees.items():
    if e.job_type == 'PERMANENT' and e.role_class in ('NURSE_RN', 'NURSE_SENIOR') and e.window_end and e.window_end >= end and s.balances.get(eid, {}).get('AL', {}).get('remaining', 0) > 120 and len([x for x in s.shifts_between(eid, start, end) if x.category in ('AM', 'PM', 'NIGHT', 'LONG_DAY')]) >= 3:
        groups[(e.primary_unit, e.role_class)].append(eid)


def simulate(members):
    out = []
    for i, eid in enumerate(members):
        d = eng.evaluate(eid, 'ANNUAL', start, end, '', with_alternatives=False)
        out.append((eid, d.outcome, d.chart['summary'], d.breached_codes))
        s.add_absence(eid, days, f'sim{i}')
    for i, eid in enumerate(members):
        s.remove_absence(eid, f'sim{i}')
    return out


best = None
for (unit, role), members in sorted(groups.items(), key=lambda kv: -len(kv[1])):
    if len(members) < 3:
        continue
    res = simulate(members[:6])
    firsts = [r for r in res if r[1] in ('RECOMMEND_APPROVE',)]
    flips = [r for r in res if r[1] in ('NEEDS_APPROVAL', 'DECLINED') and any(c.startswith('COV') for c in r[3])]
    if res[0][1] == 'RECOMMEND_APPROVE' and flips:
        score = len(firsts) * 10 - res.index(flips[0])
        if best is None or score > best[0]:
            best = (score, unit, role, members[:6], res)
if best:
    _, unit, role, members, res = best
    print(f'Ward {unit} ({s.unit_type.get(unit)}), role {role}. Week {start} to {end}.')
    print('Submit annual leave for that week in this order (staff login, password "password"):')
    for i, (eid, outcome, sm, codes) in enumerate(res, 1):
        print(f'  {i}. {eid}: {outcome:18s} {sm["shifts"]} shifts, {sm["not_needed"]} need no cover, {sm["covered"]} covered, {sm["uncovered"]} no cover   {codes}')
else:
    print('No flipping team found in the window.')
zero = [eid for eid, e in s.employees.items() if e.job_type == 'PERMANENT' and e.role_class.startswith('NURSE') and e.window_end and e.window_end >= end and 0 <= s.balances.get(eid, {}).get('AL', {}).get('remaining', 99) < 2 and s.shifts_between(eid, start, end)]
print('\nNurses with (almost) no annual leave left, for the zero-balance case:', zero[:4])
