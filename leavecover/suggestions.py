"""Suggestions engine.

Staff:   the best upcoming windows to take leave (ward has slack, notice is fine, balance covers it),
         excess-leave nudges, and what to do when a request is declined.
Manager: ward insights - crunch days ahead, staff with excess leave (ELMP due), unfilled gaps,
         and rule-calibration suggestions learned from the data.
"""
from __future__ import annotations
import collections, datetime as dt
from .config import RuleBook
from .store import DataStore, WORK_CATS
from .coverage import requirement
from .rules import entitlement_hours_per_year

ONE_DAY = dt.timedelta(days=1)


RULE_MEANING = {
    'REQ-004': 'a single block longer than the leave type allows (annual leave: 84 days) needs manager approval',
    'COV-001': 'the ward would fall below its required staffing with no cover found',
    'COV-002': 'too large a share of the team would be away at once',
    'COV-003': 'cover would need overtime',
    'NOT-001': 'inside the notice period',
    'BAL-001': 'not enough leave balance',
    'ELG-001': 'the contract ends before the leave ends',
}


def _score_windows(store, rb, engine, eid, leave_type, length_days, first, weeks_ahead, step_days=7):
    """Evaluate every window of length_days starting each step from `first`; return (passing, declined_codes, all)."""
    emp = store.employees[eid]
    windows, declined = [], collections.Counter()
    for w in range(weeks_ahead):
        s = first + dt.timedelta(days=step_days * w); e = s + dt.timedelta(days=length_days - 1)
        if emp.contract_end and emp.contract_end < e:
            break
        d = engine.evaluate(eid, leave_type, s, e, '', with_alternatives=False)
        gaps = d.chart['summary']['uncovered']; covered = d.chart['summary']['covered']
        slack = sum(max(0, x.available_after - x.required) for x in d.demand)
        soft = [r.code for r in d.rules if r.severity == 'soft' and not r.passed]
        score = 100 - 25 * gaps - 3 * covered + min(20, slack * 2) - 12 * len(soft)
        item = {'start': s.isoformat(), 'end': e.isoformat(), 'outcome': d.outcome, 'score': max(0, int(score)), 'shifts': d.chart['summary']['shifts'],
                'no_cover_needed': d.chart['summary']['not_needed'], 'cover_found': covered, 'uncovered': gaps, 'hours': d.requested_hours,
                'public_holidays': sum(1 for x in d.demand if x.public_holiday), 'codes': d.breached_codes,
                'label': ('ward has spare capacity' if slack >= 2 and gaps == 0 else 'every shift covered') if d.outcome in ('RECOMMEND_APPROVE', 'APPROVED') else ('needs manager sign-off: ' + ', '.join(soft) if d.outcome == 'NEEDS_APPROVAL' else 'cannot be approved: ' + ', '.join(d.breached_codes))}
        windows.append(item)
        if d.outcome == 'DECLINED':
            for c in d.breached_codes:
                declined[c] += 1
    passing = sorted([w for w in windows if w['outcome'] != 'DECLINED'], key=lambda x: (0 if x['outcome'] in ('RECOMMEND_APPROVE', 'APPROVED') else 1, -x['score']))
    return passing, declined, windows


def staff_suggestions(store: DataStore, rb: RuleBook, engine, eid: str, weeks_ahead: int = 12, length_days: int = 7, leave_type: str = 'ANNUAL',
                      range_start: dt.date | None = None, range_end: dt.date | None = None, anchor: dt.date | None = None) -> dict:
    """Best windows to take leave. Every window is checked by the full rule engine; only windows that could be
    approved are recommended. If no single block of the requested length can pass, a split plan of shorter blocks
    that do pass is returned together with the rules that block the single block."""
    emp = store.employees[eid]
    today = rb.today()
    ag = rb.agreement(emp.agreement_family)
    notice = int(ag.get('annual_notice_days', 14))
    out = {'employee_id': eid, 'length_days': length_days, 'windows': [], 'nudges': [], 'checked_by_rules': True}
    al = store.balances.get(eid, {}).get('AL')
    per_year = entitlement_hours_per_year(rb, emp, 'ANNUAL')
    if al and per_year and al['remaining'] > 2 * per_year:
        out['nudges'].append({'code': 'BAL-002', 'text': f"You hold {al['remaining']:.0f} h of annual leave, more than two years' worth ({2*per_year:.0f} h). Requests from you get priority and your manager will discuss a leave plan."})
    elif al and per_year and al['remaining'] > 1.5 * per_year:
        out['nudges'].append({'code': 'BAL-002', 'text': f"Your annual leave balance ({al['remaining']:.0f} h) is getting high. Booking a break in the next few months keeps it under the two-year limit."})
    if emp.contract_end and (emp.contract_end - today).days < 90:
        out['nudges'].append({'code': 'ELG-001', 'text': f"Your current contract ends on {emp.contract_end}. Leave after that date needs your manager to confirm the contract continues."})
    first = today + dt.timedelta(days=max(notice, 1)); first += dt.timedelta(days=(7 - first.weekday()) % 7)
    lt = rb.leave_type(leave_type) or {}
    max_block = int(lt.get('max_days') or 0)
    step = 7
    if range_start and range_end:
        # a named period: scan every start day inside the range (bounded), windows must end inside it
        first = max(range_start, today + dt.timedelta(days=1))
        last_start = range_end - dt.timedelta(days=length_days - 1)
        n = max(0, (last_start - first).days + 1)
        step = 1 if n <= 45 else 2 if n <= 90 else 7
        weeks_ahead = max(1, n // step + 1)
        out['period'] = {'start': range_start.isoformat(), 'end': range_end.isoformat(), 'anchor': anchor.isoformat() if anchor else None}
    passing, declined, windows = _score_windows(store, rb, engine, eid, leave_type, length_days, first, weeks_ahead, step)
    if anchor:
        for w in windows:
            if dt.date.fromisoformat(w['start']) <= anchor <= dt.date.fromisoformat(w['end']):
                w['score'] += 15; w['includes_anchor'] = True
        passing.sort(key=lambda x: (0 if x['outcome'] in ('RECOMMEND_APPROVE', 'APPROVED') else 1, -x['score']))
    # keep the list varied: no two recommended windows start within 4 days of each other
    varied = []
    for w in passing:
        if all(abs((dt.date.fromisoformat(w['start']) - dt.date.fromisoformat(v['start'])).days) >= 4 for v in varied):
            varied.append(w)
        if len(varied) >= 12:
            break
    out['windows'] = varied
    out['declined_windows'] = len(windows) - len(passing)
    out['all_windows'] = sorted(windows, key=lambda x: x['start'])
    if declined:
        out['blocking_rules'] = [{'code': c, 'windows': n, 'meaning': RULE_MEANING.get(c, rb.rule(c)['name'] if c in rb._index else c)} for c, n in declined.most_common(4)]
    # split plan when the single block cannot pass anywhere
    if not passing and length_days >= 14:
        candidates = []
        for block in sorted({max(7, (length_days + 1) // 2), max(7, (length_days + 2) // 3), 28, 14}, reverse=True):
            if block >= length_days:
                continue
            if max_block and block > max_block:
                continue
            p2, _, _ = _score_windows(store, rb, engine, eid, leave_type, block, first, weeks_ahead + 12)
            if not p2:
                continue
            # greedy non-overlapping plan, at least a week back at work between blocks, until the requested days are reached
            plan, used_until, total = [], None, 0
            for w in sorted(p2, key=lambda x: x['start']):
                ws = dt.date.fromisoformat(w['start'])
                if used_until and ws < used_until + dt.timedelta(days=7):
                    continue
                if w['outcome'] not in ('RECOMMEND_APPROVE', 'APPROVED', 'NEEDS_APPROVAL'):
                    continue
                plan.append(w); used_until = dt.date.fromisoformat(w['end']); total += block
                if total >= length_days:
                    break
            if plan:
                candidates.append({'block_days': block, 'blocks': plan, 'total_days': total, 'complete': total >= length_days, 'hours': round(sum(b['hours'] for b in plan), 1)})
        candidates.sort(key=lambda c: (not c['complete'], -c['block_days'], len(c['blocks'])))
        if candidates:
            best = candidates[0]
            reasons = '; '.join(f"{b['code']} ({b['meaning']})" for b in out.get('blocking_rules', [])[:3])
            best['text'] = (f"No single {length_days}-day block can be approved in the next {weeks_ahead} weeks: {reasons}. "
                            f"Split it into {len(best['blocks'])} block{'s' if len(best['blocks']) > 1 else ''} of {best['block_days']} days; each block below passes the rules.")
            out['split_plan'] = best
            out['nudges'].append({'code': 'COV-001', 'text': best['text']})
    return out


def manager_suggestions(store: DataStore, rb: RuleBook, db, unit: str, days_ahead: int = 28) -> dict:
    today = rb.today()
    out = {'unit': unit, 'unit_type': store.unit_type.get(unit), 'crunch_days': [], 'excess_leave_staff': [], 'unfilled_gaps': [], 'insights': []}
    roles = sorted(store.unit_staff.get(unit, {}).keys())
    # crunch days: dates where effective < required for any role
    d = today
    while d <= today + dt.timedelta(days=days_ahead):
        short = []
        for cat in ('AM', 'PM', 'NIGHT', 'LONG_DAY'):
            for role in roles:
                req, _ = requirement(store, rb, unit, d, cat, role)
                if req == 0:
                    continue
                eff, comp = store.expected_staff(unit, d, cat, role)
                known = comp >= 0.95
                if eff < req:
                    short.append({'shift': cat, 'role': role, 'short_by': req - eff, 'forecast': not known})
        if short:
            out['crunch_days'].append({'date': d.isoformat(), 'public_holiday': store.holidays.get(d), 'shortfalls': short})
        d += ONE_DAY
    # excess leave staff on this unit
    for role in roles:
        for eid in store.unit_role_members.get((unit, role), ()):
            e = store.employees[eid]
            al = store.balances.get(eid, {}).get('AL')
            per_year = entitlement_hours_per_year(rb, e, 'ANNUAL')
            if al and per_year and al['remaining'] > 2 * per_year:
                out['excess_leave_staff'].append({'employee_id': eid, 'position': e.position_name, 'balance_hours': round(al['remaining'], 1), 'two_entitlements_hours': round(2 * per_year, 1), 'excess_hours': round(al['remaining'] - 2 * per_year, 1), 'action': 'Employee Leave Management Plan due (MP 0100/18 s3.2)'})
    out['excess_leave_staff'].sort(key=lambda x: -x['excess_hours'])
    # unfilled gaps from pending/accepted requests
    if db:
        for r in db.list_requests(status=['PENDING_MANAGER', 'ACCEPTED', 'ESCALATED'], limit=300):
            e = store.employees.get(r['employee_id'])
            if not e or e.primary_unit != unit:
                continue
            dec = r['decision'] or {}
            for c in dec.get('cover', []):
                if c.get('status') == 'uncovered':
                    out['unfilled_gaps'].append({'request_id': r['id'], 'employee_id': r['employee_id'], 'date': c['date'], 'shift': c['category'], 'role': c['role_class'], 'hours': c['hours']})
    # insights
    n_crunch = len(out['crunch_days'])
    if n_crunch:
        worst = max(out['crunch_days'], key=lambda x: sum(s['short_by'] for s in x['shortfalls']))
        out['insights'].append(f"{n_crunch} day(s) in the next {days_ahead} are below requirement on at least one shift; the worst is {worst['date']}. Consider offering extra ordinary hours to part-time staff early.")
    if out['excess_leave_staff']:
        out['insights'].append(f"{len(out['excess_leave_staff'])} staff on this ward hold more than two years of annual leave. Approving their requests first reduces liability (MP 0100/18) and they win ties for the same dates.")
    quiet = []
    d = today + dt.timedelta(days=int(rb.settings.get('roster_published_days', 28)))
    for w in range(8):
        s = d + dt.timedelta(days=7 * w)
        slack = 0
        for k in range(7):
            dd = s + dt.timedelta(days=k)
            for role in roles:
                slack += max(0, store.forecast_effective(unit, dd, 'AM', role) - requirement(store, rb, unit, dd, 'AM', role)[0])
        quiet.append((slack, s))
    if quiet:
        best = max(quiet)
        out['insights'].append(f"Week starting {best[1]} has the most spare capacity in the next two months; a good time to encourage leave for staff with high balances.")
    return out


def calibration_suggestions(store: DataStore, rb: RuleBook) -> list[dict]:
    """Data-driven suggestions for rule parameters (shown in Rule Studio)."""
    out = []
    # COV-002: what share of a team is typically away on the busiest leave days?
    fr = []
    for (unit, role), members in store.unit_role_members.items():
        if len(members) < 6:
            continue
        cnt = collections.Counter()
        for m in members:
            for d in store.leave_days.get(m, ()):
                if store.peak_start <= d <= store.peak_end:
                    cnt[d] += 1
        if cnt:
            fr.append(max(cnt.values()) / len(members))
    if fr:
        fr.sort()
        p50, p90 = fr[len(fr) // 2], fr[int(len(fr) * 0.9)]
        out.append({'rule': 'COV-002', 'param': 'max_fraction_on_leave', 'current': rb.params('COV-002').get('max_fraction_on_leave'),
                    'suggested': round(p90, 2), 'why': f'On the busiest leave day of the peak window the median team had {p50:.0%} away and 9 in 10 teams stayed under {p90:.0%}.'})
    # baseline percentile sanity
    out.append({'rule': 'settings', 'param': 'baseline_percentile', 'current': rb.settings.get('baseline_percentile'), 'suggested': 0.25,
                'why': 'Using the lower quartile of observed staffing as the requirement reproduces historical leave approvals best (see data/calibration_report.json).'})
    # notice: share of booked leave with < 14 days notice
    short = tot = 0
    for eid, recs in store.leave_records.items():
        for r in recs:
            if r['leave_record_type'] == 'Booked Leave' and r['leave_family'] == 'ANNUAL' and r['leave_start_date'] > rb.today():
                tot += 1
                if (r['leave_start_date'] - rb.today()).days < 14:
                    short += 1
    if tot:
        out.append({'rule': 'NOT-001', 'param': 'severity', 'current': rb.rule('NOT-001').get('severity'), 'suggested': 'soft',
                    'why': f'{short/tot:.0%} of booked annual leave starts within 14 days of today; keeping this rule soft routes those to a manager instead of blocking them.'})
    return out
