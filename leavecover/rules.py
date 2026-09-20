"""Rule implementations. Every function reads its parameters from the rulebook so
managers can change behaviour without touching code. Each returns RuleResult objects."""
from __future__ import annotations
import datetime as dt, math
from .config import RuleBook, fmt
from .models import Employee, Shift, RuleResult
from .store import DataStore, WORK_CATS

ONE_DAY = dt.timedelta(days=1)


def _res(rb: RuleBook, code: str, passed: bool, data: dict | None = None, **kw) -> RuleResult:
    r = rb.rule(code)
    data = data or {}
    return RuleResult(code=code, name=r['name'], severity=r.get('severity', 'info'), passed=passed,
                      public=fmt(r.get('public', ''), **kw), manager=fmt(r.get('manager', ''), **kw), data=data)


# ============================================================================
# Fatigue / safe-hours checks for a proposed extra shift
# ============================================================================
class FatigueChecker:
    def __init__(self, store: DataStore, rb: RuleBook):
        self.s, self.rb = store, rb

    def _fortnight_start(self, d: dt.date) -> dt.date:
        anchor = dt.date.fromisoformat(self.rb.settings.get('pay_period_anchor', '2026-05-18'))
        k = (d - anchor).days // 14
        return anchor + dt.timedelta(days=14 * k)

    def check(self, emp: Employee, new: Shift, extra: list[Shift] | None = None) -> list[RuleResult]:
        """Return the list of FAILED fatigue rules if `new` were added to `emp`'s schedule.
        `extra` = shifts already assigned to this person in the current cover plan."""
        rb, s = self.rb, self.s
        ag = rb.agreement(emp.agreement_family)
        fails: list[RuleResult] = []
        win_lo, win_hi = new.start_dt - dt.timedelta(days=16), new.end_dt + dt.timedelta(days=16)
        sched = [x for x in s.shifts_around(emp.employee_id, win_lo, win_hi)] + [x for x in (extra or []) if win_lo <= x.start_dt <= win_hi]
        sched.sort(key=lambda x: x.start_dt)

        # FAT-007 shift length
        if rb.enabled('FAT-007'):
            mx = float(ag.get('max_shift_hours', 10))
            if emp.agreement_family == 'AMA' and new.start_min >= 12 * 60:
                mx = min(mx, 12)
            paid = new.paid_hours if new.paid_hours else max(0.0, (new.span_min - 30) / 60)
            if paid > mx + 1e-9:
                fails.append(_res(rb, 'FAT-007', False, {'hours': paid}, max=mx))

        # overlap with anything -> hard, reported as FAT-001 with 0 h rest
        prev = [x for x in sched if x.end_dt <= new.start_dt]
        nxt = [x for x in sched if x.start_dt >= new.end_dt]
        overlap = [x for x in sched if x.start_dt < new.end_dt and x.end_dt > new.start_dt]
        if overlap:
            fails.append(_res(rb, 'FAT-001', False, {'gap_hours': 0, 'overlap': True}, hours=ag.get('min_break_hours')))
            return fails

        # FAT-001 rest, FAT-002 night/day changeover
        min_break = float(ag.get('min_break_hours', 10))
        chg = float(ag.get('night_day_changeover_hours', min_break))
        for neighbour, gap in ((prev[-1], (new.start_dt - prev[-1].end_dt).total_seconds() / 3600) if prev else (None, None),
                               (nxt[0], (nxt[0].start_dt - new.end_dt).total_seconds() / 3600) if nxt else (None, None)):
            if neighbour is None:
                continue
            if rb.enabled('FAT-001') and gap < min_break:
                fails.append(_res(rb, 'FAT-001', False, {'gap_hours': round(gap, 1)}, hours=min_break))
            elif rb.enabled('FAT-002') and ('NIGHT' in (neighbour.category, new.category)) and neighbour.category != new.category and gap < chg:
                fails.append(_res(rb, 'FAT-002', False, {'gap_hours': round(gap, 1)}, hours=chg))

        days = {x.date for x in sched if x.category in WORK_CATS or x.category == 'ONCALL_24H'}
        days.add(new.date)
        # FAT-003 consecutive duties
        if rb.enabled('FAT-003'):
            mx = int(ag.get('max_consecutive_duties', 7))
            run = 1
            d = new.date - ONE_DAY
            while d in days:
                run += 1; d -= ONE_DAY
            d = new.date + ONE_DAY
            while d in days:
                run += 1; d += ONE_DAY
            if run > mx:
                fails.append(_res(rb, 'FAT-003', False, {'consecutive': run}, max=mx))
        # FAT-004 duties per fortnight
        if rb.enabled('FAT-004'):
            mx = int(ag.get('max_duties_fortnight', 10))
            fs = self._fortnight_start(new.date); fe = fs + dt.timedelta(days=13)
            n = len({d for d in days if fs <= d <= fe})
            if n > mx:
                fails.append(_res(rb, 'FAT-004', False, {'duties': n}, max=mx))
        # FAT-005 nights
        if rb.enabled('FAT-005') and new.category == 'NIGHT':
            mxc = int(ag.get('max_consecutive_nights', 5)); mxf = int(ag.get('max_nights_fortnight', 8))
            nights = {x.date for x in sched if x.category == 'NIGHT'} | {new.date}
            run = 1
            d = new.date - ONE_DAY
            while d in nights:
                run += 1; d -= ONE_DAY
            d = new.date + ONE_DAY
            while d in nights:
                run += 1; d += ONE_DAY
            fs = self._fortnight_start(new.date); fe = fs + dt.timedelta(days=13)
            nf = len({d for d in nights if fs <= d <= fe})
            if run > mxc or nf > mxf:
                fails.append(_res(rb, 'FAT-005', False, {'consecutive_nights': run, 'nights_fortnight': nf}, max_consecutive=mxc, max_fortnight=mxf))
        # FAT-006 days off per week (Mon-Sun week containing the new shift)
        if rb.enabled('FAT-006'):
            mn = int(ag.get('min_days_off_week', 2))
            ws = new.date - dt.timedelta(days=new.date.isoweekday() - 1); we = ws + dt.timedelta(days=6)
            worked = len({d for d in days if ws <= d <= we})
            if 7 - worked < mn:
                fails.append(_res(rb, 'FAT-006', False, {'days_off': 7 - worked}, min=mn))
        # FAT-008 DIT hour caps
        if rb.enabled('FAT-008') and emp.agreement_family == 'AMA' and 'max_hours_7_days' in ag:
            h7 = sum(x.paid_hours for x in sched if new.date - dt.timedelta(days=6) <= x.date <= new.date) + new.paid_hours
            h14 = sum(x.paid_hours for x in sched if new.date - dt.timedelta(days=13) <= x.date <= new.date) + new.paid_hours
            if h7 > float(ag['max_hours_7_days']) or h14 > float(ag['max_hours_14_days']):
                fails.append(_res(rb, 'FAT-008', False, {'h7': h7, 'h14': h14}, max7=ag['max_hours_7_days'], max14=ag['max_hours_14_days']))
        return fails

    def existing_breaches(self, emp: Employee, start: dt.date, end: dt.date) -> int:
        """Count rest-rule breaches already present in the roster around the dates (FAT-010)."""
        ag = self.rb.agreement(emp.agreement_family)
        mb = float(ag.get('min_break_hours', 10))
        lst = self.s.shifts_around(emp.employee_id, dt.datetime.combine(start, dt.time()) - dt.timedelta(days=3), dt.datetime.combine(end, dt.time()) + dt.timedelta(days=3))
        n = 0
        for a, b in zip(lst, lst[1:]):
            if (b.start_dt - a.end_dt).total_seconds() / 3600 < mb:
                n += 1
        return n


# ============================================================================
# Request-level rules (validity, eligibility, balance, notice, calendar)
# ============================================================================
def entitlement_hours_per_year(rb: RuleBook, emp: Employee, leave_type: str) -> float:
    ag = rb.agreement(emp.agreement_family)
    if leave_type in ('ANNUAL',):
        base = float(ag.get('annual_leave_hours_year', 152))
        if emp.continuous_shift:
            base += float(ag.get('shift_worker_extra_hours_year', 0))
        return base * (emp.fte if emp.fte > 0 else 0)
    if leave_type in ('SICK', 'CARER'):
        return float(ag.get('personal_leave_hours_year', 114)) * (emp.fte if emp.fte > 0 else 0)
    return 0.0


def projected_balance(store: DataStore, rb: RuleBook, emp: Employee, balance_code: str | None, leave_type: str, start: dt.date, today: dt.date, already_booked_hours: float = 0.0):
    """Balance at the leave start: latest snapshot + accrual between snapshot date and start."""
    if not balance_code:
        return None, None
    codes = balance_code if isinstance(balance_code, list) else [balance_code]
    b = None
    for c in codes:
        b = store.balances.get(emp.employee_id, {}).get(c)
        if b:
            break
    if not b:
        return None, None
    per_year = entitlement_hours_per_year(rb, emp, leave_type)
    days = max(0, (start - b['effective']).days)
    accrual = per_year * days / 365.0
    return round(b['remaining'] + accrual + already_booked_hours, 1), b


def requested_hours(store: DataStore, rb: RuleBook, emp: Employee, start: dt.date, end: dt.date) -> tuple[float, bool]:
    """Hours the leave would consume: rostered paid hours inside the dates; usual pattern beyond the roster;
    contract_hours/10 per weekday when nothing is known. Public holidays are not deducted."""
    hrs = 0.0
    forecast = False
    d = start
    div = float(rb.settings.get('fallback_hours_per_weekday_divisor', 10))
    while d <= end:
        if d in store.holidays:
            d += ONE_DAY; continue
        if store.in_window(emp.employee_id, d):
            hrs += sum(s.paid_hours for s in store.shift_dates_by_emp.get(emp.employee_id, {}).get(d, ()) if s.category in WORK_CATS)
        else:
            forecast = True
            p = store.expected_shift(emp.employee_id, d)
            if p and p[2] >= store.pattern_min_p:
                hrs += p[3]
            elif not p and d.isoweekday() <= 5 and emp.contract_hours > 0 and not store.pattern.get(emp.employee_id):
                hrs += emp.contract_hours / div
        d += ONE_DAY
    return round(hrs, 1), forecast


def request_rules(store: DataStore, rb: RuleBook, emp: Employee, leave_type: str, start: dt.date, end: dt.date, note: str, today: dt.date, already_booked: bool = False) -> tuple[list[RuleResult], dict]:
    out: list[RuleResult] = []
    ctx: dict = {}
    lt = rb.leave_type(leave_type)
    ag = rb.agreement(emp.agreement_family)
    urgent = bool(lt and lt.get('urgent')) or any(w in (note or '').lower() for w in ('emergency', 'urgent', 'hospitalised'))
    ctx['urgent'] = urgent

    # REQ-001 / REQ-002
    if rb.enabled('REQ-001') and end < start:
        out.append(_res(rb, 'REQ-001', False)); return out, ctx
    if rb.enabled('REQ-002') and not lt:
        out.append(_res(rb, 'REQ-002', False)); return out, ctx
    cal_days = (end - start).days + 1
    lead = (start - today).days
    ctx.update(calendar_days=cal_days, lead_days=lead)

    # REQ-003 back-dating
    if rb.enabled('REQ-003') and start < today:
        allow = int(rb.params('REQ-003').get('backdate_days_allowed_for_urgent', 3))
        if not (urgent and (today - start).days <= allow):
            out.append(_res(rb, 'REQ-003', False, {'lead_days': lead}, backdate_days_allowed_for_urgent=allow))
    # REQ-004 block length
    if rb.enabled('REQ-004') and lt and lt.get('max_days') and cal_days > int(lt['max_days']):
        out.append(_res(rb, 'REQ-004', False, {'days': cal_days, 'max': lt['max_days']}))

    # ELG-001 contract
    if rb.enabled('ELG-001') and emp.contract_end and emp.contract_end < end:
        out.append(_res(rb, 'ELG-001', False, {'contract_end': emp.contract_end.isoformat()}, contract_end=emp.contract_end.isoformat()))
    # ELG-002 casual paid leave (a casual who nevertheless holds a balance for the type is dual-employed: allowed)
    if rb.enabled('ELG-002') and emp.job_type == 'CASUAL' and lt and lt.get('paid') and not lt.get('casual_allowed'):
        codes = lt.get('balance_code') if isinstance(lt.get('balance_code'), list) else [lt.get('balance_code')]
        has_bal = any(c and store.balances.get(emp.employee_id, {}).get(c, {}).get('remaining', 0) > 0 for c in codes)
        if not has_bal:
            out.append(_res(rb, 'ELG-002', False))
    # ELG-003 LSL eligibility: an LSL balance in the HR system proves entitlement; otherwise 7 years' service
    if rb.enabled('ELG-003') and leave_type == 'LONG_SERVICE':
        ls = store.balances.get(emp.employee_id, {}).get('LS', {}).get('remaining', 0)
        yrs = (start - emp.emp_start).days / 365.25 if emp.emp_start else 0
        mn = float(rb.params('ELG-003').get('min_years', 7))
        if ls <= 0 and yrs < mn:
            avail = (emp.emp_start + dt.timedelta(days=int(mn * 365.25))).isoformat() if emp.emp_start else 'unknown'
            r = _res(rb, 'ELG-003', False, {'years': round(yrs, 1), 'available_from': avail}, min_years=int(mn))
            r.public += f' Based on your start date it becomes available from {avail}.'
            out.append(r)

    # Balance
    req_hours, forecast = requested_hours(store, rb, emp, start, end)
    ctx['requested_hours'] = req_hours; ctx['forecast'] = forecast
    bcode = lt.get('balance_code') if lt else None
    booked_back = 0.0
    if already_booked:
        codes = bcode if isinstance(bcode, list) else [bcode]
        for c in codes:
            bb = store.balances.get(emp.employee_id, {}).get(c) if c else None
            if bb:
                booked_back = bb.get('booked', 0.0); break
    proj, brec = projected_balance(store, rb, emp, bcode, leave_type, start, today, booked_back)
    ctx['projected_balance'] = proj; ctx['balance_code'] = bcode
    if bcode and lt.get('paid'):
        if brec is None:
            if rb.enabled('BAL-003') and not urgent:
                out.append(_res(rb, 'BAL-003', False, {'balance_code': bcode}, balance_code=bcode if isinstance(bcode, str) else '/'.join(bcode)))
        elif rb.enabled('BAL-001'):
            allow_neg = float(rb.params('BAL-001').get('allow_negative_hours', 0))
            if proj + allow_neg < req_hours - 1e-6:
                out.append(_res(rb, 'BAL-001', False, {'projected_hours': proj, 'requested_hours': req_hours}, projected_hours=proj, requested_hours=req_hours))
            else:
                out.append(_res(rb, 'BAL-001', True, {'projected_hours': proj, 'requested_hours': req_hours}))
    # BAL-002 excess leave (annual)
    al = store.balances.get(emp.employee_id, {}).get('AL')
    if rb.enabled('BAL-002') and al:
        per_year = entitlement_hours_per_year(rb, emp, 'ANNUAL')
        thr = float(rb.params('BAL-002').get('entitlements_threshold', 2.0)) * per_year
        excess = per_year > 0 and al['remaining'] > thr
        ctx['excess_leave'] = bool(excess)
        if excess:
            out.append(_res(rb, 'BAL-002', True, {'balance_hours': round(al['remaining'], 1), 'threshold_hours': round(thr, 1), 'excess': True}, balance_hours=round(al['remaining'], 1), threshold_hours=round(thr, 1)))
        # BLK-001 nurses >10 weeks
        if rb.enabled('BLK-001') and emp.agreement_family in ('ANF',) and leave_type == 'ANNUAL':
            weeks = float(ag.get('excess_leave_direct_weeks', 10))
            if al['remaining'] > weeks * (emp.contract_hours / 2 if emp.contract_hours else 38):
                out.append(_res(rb, 'BLK-001', True, {'weeks': weeks}, weeks=weeks))

    # Notice
    if not urgent:
        if leave_type == 'ANNUAL' and rb.enabled('NOT-001'):
            nd = int(ag.get('annual_notice_days', 14))
            if lead < nd:
                out.append(_res(rb, 'NOT-001', False, {'lead_days': lead, 'notice_days': nd}, lead_days=lead, notice_days=nd))
        if leave_type == 'LONG_SERVICE' and rb.enabled('NOT-002'):
            nd = int(ag.get('lsl_notice_days_employee', 56))
            if lead < nd:
                out.append(_res(rb, 'NOT-002', False, {'lead_days': lead, 'notice_days': nd}, lead_days=lead, notice_days=nd))
        if lt and lt.get('notice_days') and leave_type not in ('ANNUAL', 'LONG_SERVICE') and rb.enabled('NOT-004'):
            nd = int(lt['notice_days'])
            if lead < nd:
                out.append(_res(rb, 'NOT-004', False, {'lead_days': lead, 'notice_days': nd}, lead_days=lead, notice_days=nd))
    pub_days = int(rb.settings.get('roster_published_days', 28))
    known_days = sum(1 for i in range(cal_days) if store.in_window(emp.employee_id, start + dt.timedelta(days=i)))
    ctx['known_days'] = known_days
    if rb.enabled('NOT-003') and 0 <= lead < pub_days and not urgent and known_days:
        out.append(_res(rb, 'NOT-003', True, {'lead_days': lead, 'known_days': known_days}, published_days=pub_days, known_days=known_days, days=cal_days))

    # Urgent leave
    if urgent and rb.enabled('SCK-001') and leave_type in ('SICK', 'CARER', 'COMPASSIONATE'):
        out.append(_res(rb, 'SCK-001', True))
    if urgent and rb.enabled('SCK-002') and leave_type in ('SICK', 'CARER'):
        ev = int(ag.get('evidence_after_days', 2))
        if cal_days > ev:
            out.append(_res(rb, 'SCK-002', True, {'days': ev}, days=ev))

    # Calendar
    phs = [d for d in (start + dt.timedelta(days=i) for i in range(cal_days)) if d in store.holidays]
    ctx['public_holidays'] = [d.isoformat() for d in phs]
    if rb.enabled('PH-001') and phs:
        out.append(_res(rb, 'PH-001', True, {'count': len(phs), 'dates': ctx['public_holidays']}, count=len(phs)))
    if rb.enabled('HOR-001') and forecast:
        fc = cal_days - ctx.get('known_days', 0)
        out.append(_res(rb, 'HOR-001', True, {'forecast': True, 'forecast_days': fc, 'days': cal_days}, forecast_days=fc, days=cal_days))

    # FAT-009 night before leave (requester)
    if rb.enabled('FAT-009'):
        before = store.shift_dates_by_emp.get(emp.employee_id, {}).get(start - ONE_DAY, ())
        if any(s.category in ('NIGHT', 'ONCALL_24H') for s in before):
            out.append(_res(rb, 'FAT-009', True, {'date': (start - ONE_DAY).isoformat()}))
    return out, ctx
