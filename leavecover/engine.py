"""The decision engine: one call evaluates a leave request end to end.

    check   -> rules -> demand -> gaps -> cover plan -> outcome -> alternatives -> explanation

Outcomes
  ACCEPTED           urgent leave (sick/carer's) recorded immediately; manager notified with cover plan
  RECOMMEND_APPROVE  every rule passes and every shift is covered (or needs no cover); manager confirms with one click
  NEEDS_APPROVAL     something needs a manager's judgement (short notice, overtime cover, team-away cap, unknown balance)
  DECLINED           a hard rule is breached; the staff member sees rule codes, a shift graphic and alternative dates
"""
from __future__ import annotations
import collections, datetime as dt, time
from dataclasses import asdict
from .config import RuleBook, fmt
from .models import Employee, Decision, RuleResult, DemandShift, CoverLine, Alternative, Candidate
from .rules import FatigueChecker, request_rules
from .coverage import demand_for, evaluate_gaps, team_away_fraction
from .recommend import Recommender
from .store import DataStore

ONE_DAY = dt.timedelta(days=1)
HARD_STOP_CATEGORIES = ('request', 'eligibility', 'balance')


def _d(x):
    return x.isoformat() if isinstance(x, (dt.date, dt.datetime)) else x


class Engine:
    def __init__(self, store: DataStore, rb: RuleBook, db=None):
        self.s, self.rb, self.db = store, rb, db
        self.fat = FatigueChecker(store, rb)
        self.rec = Recommender(store, rb, self.fat)

    # ------------------------------------------------------------------ public API
    def evaluate(self, employee_id: str, leave_type: str, start: dt.date, end: dt.date, note: str = '', with_alternatives: bool = True, already_booked: bool = False) -> Decision:
        t0 = time.perf_counter()
        rb, s = self.rb, self.s
        today = rb.today()
        emp = s.employees.get(employee_id)
        if emp is None:
            raise KeyError(f'unknown employee {employee_id}')
        results, ctx = request_rules(s, rb, emp, leave_type, start, end, note, today, already_booked)
        urgent = ctx.get('urgent', False)

        # ---- coverage
        demand, forecast = demand_for(s, rb, emp, start, end) if not any(r.code in ('REQ-001', 'REQ-002') and not r.passed for r in results) else ([], False)
        evaluate_gaps(s, rb, emp, demand)
        recent_extra = self.db.extra_shift_counts(today - dt.timedelta(days=int(rb.settings.get('extra_shift_lookback_days', 28)))) if self.db else {}
        cover = self.rec.plan(emp, demand, recent_extra) if (demand and (rb.enabled('COV-001') or urgent)) else []
        if urgent and not rb.settings.get('cover_recommendations_for_sick_leave', True):
            cover = []
        gap_shifts = [d for d in demand if d.gap]
        uncovered = [c for c in cover if c.status == 'uncovered']
        overtime_lines = [c for c in cover if c.status == 'covered' and c.candidate and c.candidate.requires_manager]
        if demand and rb.enabled('COV-001'):
            # MP 0100/18 s3.4 / AMA cl.34(14): absences of two weeks or less are not backfilled by default, so an
            # unfilled gap on a non-ratio ward goes to the manager (soft) instead of blocking. Ratio wards stay hard.
            sev = rb.rule('COV-001')['severity']
            short_days = int(rb.params('COV-004').get('days', 14)) if rb.enabled('COV-004') else 0
            ratio_units = set((rb.data.get('ratio_wards') or {}).keys())
            if uncovered and sev == 'hard' and ctx.get('calendar_days', 0) <= short_days and not any(c.unit in ratio_units for c in uncovered) and rb.params('COV-004').get('short_absence_to_manager', True):
                sev = 'soft'
            results.append(RuleResult('COV-001', rb.rule('COV-001')['name'], sev, passed=not uncovered,
                                      public=(fmt(rb.rule('COV-001')['public'], gap_shifts=len(uncovered), total_shifts=len(demand)) if sev == 'hard' else f'{len(uncovered)} of your {len(demand)} rostered shifts have no cover found yet; for a short absence your manager decides whether cover is needed.') if uncovered else '',
                                      manager=fmt(rb.rule('COV-001')['manager'], gap_shifts=len(uncovered), total_shifts=len(demand)),
                                      data={'gap_shifts': len(gap_shifts), 'uncovered': len(uncovered), 'total_shifts': len(demand)}))
        if overtime_lines and rb.enabled('COV-003'):
            results.append(RuleResult('COV-003', rb.rule('COV-003')['name'], rb.rule('COV-003')['severity'], passed=False,
                                      public=rb.rule('COV-003')['public'], manager=fmt(rb.rule('COV-003')['manager'], overtime_shifts=len(overtime_lines)), data={'overtime_shifts': len(overtime_lines)}))
        if rb.enabled('COV-002') and not urgent and demand:
            p = rb.params('COV-002'); mx = float(p.get('max_fraction_on_leave', 0.25)); mn = int(p.get('min_team_size', 6))
            bad = 0
            for d in sorted({x.date for x in demand}):
                frac, away, n = team_away_fraction(s, emp, d)
                if n >= mn and frac > mx:
                    bad += 1
            if bad:
                results.append(RuleResult('COV-002', rb.rule('COV-002')['name'], rb.rule('COV-002')['severity'], passed=False,
                                          public=fmt(rb.rule('COV-002')['public'], pct=int(mx * 100), days=bad), manager=fmt(rb.rule('COV-002')['manager'], pct=int(mx * 100), days=bad), data={'days': bad}))
        if rb.enabled('COV-005') and leave_type in ('PROF_DEV', 'STUDY') and emp.role_class == 'MED_SENIOR' and demand:
            mx = float(rb.params('COV-005').get('max_fraction', 0.5)); bad = 0
            for d in sorted({x.date for x in demand}):
                members = s.unit_role_members.get((emp.primary_unit, emp.role_class), set())
                if members:
                    away = sum(1 for m in members if m != emp.employee_id and s.leave_family_by_day.get(m, {}).get(d) == 'PROF_DEV_STUDY') + 1
                    if away / len(members) > mx:
                        bad += 1
            if bad:
                results.append(RuleResult('COV-005', rb.rule('COV-005')['name'], rb.rule('COV-005')['severity'], passed=False, public=rb.rule('COV-005')['public'], manager=fmt(rb.rule('COV-005')['manager'], pct=int(mx * 100)), data={'days': bad}))
        if rb.enabled('COV-007') and demand and not urgent:
            thin = 0; exp_list = []
            for ds in demand:
                exp = s.expected_sick_calls(ds.unit, ds.role_class, ds.available_after + 1)
                exp_list.append(exp)
                if exp >= float(rb.params('COV-007').get('min_expected_absences', 0.5)) and (ds.available_after - ds.required) < exp:
                    thin += 1
            if thin:
                avg = round(sum(exp_list) / len(exp_list), 1)
                results.append(RuleResult('COV-007', rb.rule('COV-007')['name'], rb.rule('COV-007')['severity'], passed=False,
                                          public=fmt(rb.rule('COV-007')['public'], shifts=thin), manager=fmt(rb.rule('COV-007')['manager'], shifts=thin, expected=avg), data={'shifts': thin, 'expected_per_shift': avg}))
        if rb.enabled('COV-004') and demand and ctx.get('calendar_days', 0) <= int(rb.params('COV-004').get('days', 14)):
            results.append(RuleResult('COV-004', rb.rule('COV-004')['name'], 'info', passed=True, public='', manager=fmt(rb.rule('COV-004')['manager'], days=rb.params('COV-004').get('days', 14))))
        if rb.enabled('FAT-010'):
            nb = self.fat.existing_breaches(emp, start, end)
            if nb:
                results.append(RuleResult('FAT-010', rb.rule('FAT-010')['name'], 'info', passed=True, manager=rb.rule('FAT-010')['manager'], data={'breaches': nb}))

        # ---- outcome
        hard = [r for r in results if r.severity == 'hard' and not r.passed]
        soft = [r for r in results if r.severity == 'soft' and not r.passed]
        if urgent and rb.settings.get('auto_accept_sick_leave', True) and rb.enabled('SCK-001') and leave_type in ('SICK', 'CARER', 'COMPASSIONATE') and not any(r.code in ('REQ-001', 'REQ-002', 'ELG-002') for r in hard):
            outcome, requires_manager = 'ACCEPTED', False
            headline = 'Recorded. Your manager has been notified.'
        elif hard:
            outcome, requires_manager = 'DECLINED', False
            headline = 'These dates cannot be approved as requested.'
        elif soft:
            outcome, requires_manager = 'NEEDS_APPROVAL', True
            headline = 'Possible, but a manager needs to confirm.'
        else:
            auto = bool(rb.settings.get('auto_approve_when_fully_covered', False))
            outcome = 'APPROVED' if auto else 'RECOMMEND_APPROVE'
            requires_manager = not auto
            headline = 'Approved.' if auto else 'Looks good. Sent to your manager to confirm.'
        # competing requests from the same team for the same dates (PRI-001)
        competing = []
        if self.db and demand and rb.enabled('PRI-001'):
            for r0 in self.db.list_requests(status=['PENDING_MANAGER', 'ESCALATED'], limit=500):
                e0 = s.employees.get(r0['employee_id'])
                if not e0 or r0['employee_id'] == emp.employee_id or e0.primary_unit != emp.primary_unit or e0.role_class != emp.role_class:
                    continue
                if not (dt.date.fromisoformat(r0['end']) < start or dt.date.fromisoformat(r0['start']) > end):
                    competing.append(r0)
            if competing:
                n = len(competing)
                results.append(RuleResult('PRI-001', rb.rule('PRI-001')['name'], 'info', True,
                                          public=f'{n} other request{"s" if n > 1 else ""} from your team overlap{"" if n > 1 else "s"} these dates and {"are" if n > 1 else "is"} still waiting for a decision. They were counted as absences in this check; ties go to staff with excess leave first, then whoever asked first.',
                                          manager=f'{n} overlapping pending request(s) from the same team/role: ' + ', '.join(f"{x['id']} ({x['employee_id']} {x['start']}..{x['end']})" for x in competing[:5]) + '. Priority: excess leave first (MP 0100/18 s3.2), then earliest submission.', data={'competing': [x['id'] for x in competing]}))
        # balance options when the balance is the blocker (BAL-001)
        bal_fail = next((r for r in results if r.code == 'BAL-001' and not r.passed), None)
        bal_options = self._balance_options(emp, leave_type, ctx, bal_fail, demand, start, end, today) if bal_fail else None
        if bal_fail and demand:
            proj = float(bal_fail.data.get('projected_hours') or 0)
            acc, n_days = 0.0, 0
            for dsx in sorted(demand, key=lambda x: x.date):
                if acc + dsx.hours <= proj + 1e-6:
                    acc += dsx.hours; n_days = (dsx.date - start).days + 1
                else:
                    break
            hint = f'Your balance covers about {proj:.0f} h, roughly the first {n_days} day(s) of this request.' if n_days else 'Your balance does not cover any full shift in this request.'
            results.append(RuleResult('BAL-001', 'Balance hint', 'info', True, public=hint + ' You could shorten the dates or ask for the rest as leave without pay.', manager=hint))
        breached = [r.code for r in results if not r.passed]
        public_reasons = [f'{r.code} - {r.public}' for r in results if not r.passed and r.public]
        public_reasons += [r.public for r in results if r.passed and r.public and r.severity == 'info' and r.code not in ('COV-004',)]
        manager_reasons = [f'{r.code}: {r.manager}' for r in results if r.manager and (not r.passed or r.severity == 'info')]

        # ---- alternatives (only when the dates themselves are the problem)
        alts: list[Alternative] = []
        date_related = any(r.code.startswith(('COV', 'NOT', 'ELG-001', 'BAL-001', 'REQ-003')) for r in hard + soft)
        if with_alternatives and outcome in ('DECLINED', 'NEEDS_APPROVAL') and date_related and not urgent:
            alts = self.alternatives(emp, leave_type, start, end, today)
        chart = self._chart(demand, cover, results)
        if bal_options:
            chart['balance_options'] = bal_options
        dec = Decision(employee_id, leave_type, start, end, outcome, headline, requires_manager, ctx.get('requested_hours', 0.0), ctx.get('projected_balance'),
                       results, demand, cover, alts, breached, public_reasons, manager_reasons, forecast, self.rb.version,
                       dt.datetime.now().isoformat(timespec='seconds'), round((time.perf_counter() - t0) * 1000, 1), outcome in ('DECLINED', 'NEEDS_APPROVAL'), chart)
        return dec

    # ------------------------------------------------------------------ options when the balance is not enough
    def _balance_options(self, emp, leave_type, ctx, bal_fail, demand, start, end, today) -> dict:
        from .rules import entitlement_hours_per_year
        rb, s = self.rb, self.s
        proj = float(bal_fail.data.get('projected_hours') or 0); need = float(bal_fail.data.get('requested_hours') or ctx.get('requested_hours') or 0)
        short = max(0.0, need - proj)
        per_year = entitlement_hours_per_year(rb, emp, leave_type)
        out = {'short_hours': round(short, 1), 'projected_hours': round(proj, 1), 'requested_hours': round(need, 1), 'accrual_hours_per_year': round(per_year, 1), 'options': []}
        # when will the balance be enough (accrual only)?
        if per_year > 0:
            days = int(short / per_year * 365.25) + 1
            avail = start + dt.timedelta(days=days)
            out['enough_from'] = avail.isoformat()
            out['options'].append({'kind': 'wait', 'title': f'Wait until about {avail:%d %b %Y}', 'detail': f'You accrue about {per_year/26:.1f} h a fortnight, so the balance covers these {need:.0f} h from around then.' if days < 3 * 365 else 'At your accrual rate this would take more than three years.'})
        else:
            out['enough_from'] = None
        # shorter block affordable now
        acc, n_days = 0.0, 0
        for dsx in sorted(demand, key=lambda x: x.date):
            if acc + dsx.hours <= proj + 1e-6:
                acc += dsx.hours; n_days = (dsx.date - start).days + 1
            else:
                break
        if n_days:
            out['options'].append({'kind': 'shorter', 'title': f'Take the first {n_days} day(s) now', 'detail': f'{start} to {start + dt.timedelta(days=n_days-1)} fits your current balance ({proj:.0f} h).', 'start': start.isoformat(), 'end': (start + dt.timedelta(days=n_days-1)).isoformat()})
        # other balances that could pay for it
        NAMES = {'LS': ('Long service leave', 'LONG_SERVICE'), 'AD': ('Accrued days off', 'ADO'), 'TP': ('Time in lieu (public holidays)', 'TOIL'), 'TO': ('Time in lieu (overtime)', 'TOIL'), 'PU': ('Purchased leave', 'PURCHASED'), 'PC': ('Professional development', 'PROF_DEV')}
        for code, (name, lt) in NAMES.items():
            b = s.balances.get(emp.employee_id, {}).get(code)
            if b and b['remaining'] >= 4 and lt != leave_type:
                out['options'].append({'kind': 'other_balance', 'leave_type': lt, 'title': f'Use {name.lower()} ({b["remaining"]:.0f} h available)', 'detail': 'Covers the whole request.' if b['remaining'] >= need else f'Covers {b["remaining"]:.0f} of the {need:.0f} h; the rest could be leave without pay.'})
        # half pay: same dates, half the hours deducted (ANF cl.33(19), HSUWA 39.20, AMA 34(24))
        if leave_type == 'ANNUAL' and proj * 2 >= need and proj > 0:
            out['options'].append({'kind': 'half_pay', 'title': 'Take it at half pay', 'detail': f'All agreements allow annual leave at half pay: the same {need:.0f} h of absence deducts only {need/2:.0f} h, which your balance ({proj:.0f} h) covers. Needs manager agreement.'})
        # unpaid for the shortfall
        if emp.job_type != 'CASUAL' or True:
            out['options'].append({'kind': 'unpaid', 'leave_type': 'UNPAID', 'title': 'Leave without pay for the shortfall', 'detail': f'{short:.0f} h as leave without pay (needs manager approval); the rest from your balance.'})
        # purchased leave scheme
        out['options'].append({'kind': 'purchase', 'title': 'Purchased leave (42/52 scheme) for next year', 'detail': 'Buy up to 10 extra weeks a year by salary sacrifice; applications each 12 months, subject to operational requirements.'})
        out['options'].append({'kind': 'manager', 'title': 'Send to your manager for a decision', 'detail': 'Submit anyway: the manager sees the shortfall and these options and can approve a combination.'})
        return out

    # ------------------------------------------------------------------ alternative dates
    def alternatives(self, emp: Employee, leave_type: str, start: dt.date, end: dt.date, today: dt.date) -> list[Alternative]:
        rb = self.rb
        length = (end - start).days
        before = int(rb.settings.get('alternatives_search_before_days', 28)); after = int(rb.settings.get('alternatives_search_after_days', 56))
        nmax = int(rb.settings.get('alternatives_max', 3))
        ag = rb.agreement(emp.agreement_family)
        notice = int(ag.get('annual_notice_days', 14)) if leave_type == 'ANNUAL' else int(ag.get('lsl_notice_days_employee', 56)) if leave_type == 'LONG_SERVICE' else 0
        found: list[Alternative] = []
        # same weekday alignment first (shift patterns repeat weekly), then any day
        offsets = sorted(range(-before, after + 1), key=lambda o: (o % 7 != 0, abs(o)))
        for off in offsets:
            if off == 0:
                continue
            s2 = start + dt.timedelta(days=off); e2 = s2 + dt.timedelta(days=length)
            if (s2 - today).days < max(notice, 1):
                continue
            if emp.contract_end and emp.contract_end < e2:
                continue
            d = self.evaluate(emp.employee_id, leave_type, s2, e2, '', with_alternatives=False)
            if d.outcome in ('RECOMMEND_APPROVE', 'APPROVED') or (d.outcome == 'NEEDS_APPROVAL' and not any(r.code.startswith('COV') for r in d.rules if not r.passed)):
                gaps = sum(1 for c in d.cover if c.status == 'uncovered')
                covered = sum(1 for c in d.cover if c.status == 'covered'); nn = sum(1 for c in d.cover if c.status == 'not_needed')
                ordinary = not any(c.candidate and c.candidate.tier in ('overtime', 'agency') for c in d.cover)
                fit = max(0, 100 - abs(off) - 10 * gaps - (15 if d.outcome == 'NEEDS_APPROVAL' else 0) - (0 if ordinary else 10))
                label = 'fully covered' if d.outcome in ('RECOMMEND_APPROVE', 'APPROVED') else 'needs manager sign-off'
                why = []
                if len(d.demand) == 0:
                    why.append('you have no rostered shifts in this period')
                else:
                    if nn == len(d.demand):
                        why.append(f'the ward stays at requirement for all {len(d.demand)} of your shifts')
                    else:
                        why.append(f'{nn} of {len(d.demand)} shifts need no cover; the other {covered} are covered' + (' at ordinary hours' if ordinary else ' (some overtime)'))
                    soft = [r for r in d.rules if r.severity == 'soft' and not r.passed]
                    if soft:
                        why.append('manager to confirm: ' + ', '.join(r.code for r in soft))
                why.append(f'same length, {"starts" if off > 0 else "starts"} {abs(off)} day{"s" if abs(off) != 1 else ""} {"later" if off > 0 else "earlier"}' + (' (same weekdays)' if off % 7 == 0 else ''))
                found.append(Alternative(s2, e2, d.outcome, gaps, len(d.demand), off, label, fit, why, covered, nn, ordinary))
                if len(found) >= nmax * 3:
                    break
        found.sort(key=lambda a: (-a.fit, abs(a.distance_days)))
        return found[:nmax]

    # ------------------------------------------------------------------ chart data for the anonymised graphic
    def _chart(self, demand: list[DemandShift], cover: list[CoverLine], results: list[RuleResult]) -> dict:
        bydate = collections.OrderedDict()
        cov_by = {(c.date, c.unit, c.category): c for c in cover}
        for d in demand:
            c = cov_by.get((d.date, d.unit, d.category))
            status = 'not_needed' if not d.gap else ('covered' if c and c.status == 'covered' else 'uncovered')
            tier = c.candidate.tier if (c and c.candidate) else None
            bydate.setdefault(d.date.isoformat(), []).append({'unit': d.unit, 'unit_type': d.unit_type, 'shift': d.category, 'hours': d.hours, 'required': d.required,
                                                              'remaining': d.available_after, 'status': status, 'tier': tier, 'forecast': d.forecast, 'public_holiday': d.public_holiday,
                                                              'why': (c.why if (c and c.status == 'uncovered') else None), 'note': (c.note if c else ''), 'completeness': round(getattr(d, 'roster_completeness', 1.0), 2)})
        return {'days': [{'date': k, 'shifts': v} for k, v in bydate.items()],
                'summary': {'shifts': len(demand), 'not_needed': sum(1 for d in demand if not d.gap),
                            'covered': sum(1 for c in cover if c.status == 'covered'), 'uncovered': sum(1 for c in cover if c.status == 'uncovered')},
                'codes': [r.code for r in results if not r.passed]}

    # ------------------------------------------------------------------ serialisation
    @staticmethod
    def primary_reason(dec: Decision) -> dict | None:
        """The one rule the employee should read first: a hard breach, else a soft one (rule order = importance)."""
        for sev in ('hard', 'soft'):
            for r in dec.rules:
                if r.severity == sev and not r.passed and r.public:
                    return {'code': r.code, 'name': r.name, 'severity': sev, 'text': r.public}
        return None

    @staticmethod
    def to_dict(dec: Decision, audience: str = 'staff') -> dict:
        """audience='staff' strips colleague identities (anonymised); 'manager' includes the cover plan."""
        d = {
            'employee_id': dec.employee_id, 'leave_type': dec.leave_type, 'start': _d(dec.start), 'end': _d(dec.end),
            'outcome': dec.outcome, 'headline': dec.headline, 'requires_manager': dec.requires_manager,
            'requested_hours': dec.requested_hours, 'projected_balance': dec.projected_balance,
            'breached_codes': dec.breached_codes, 'public_reasons': dec.public_reasons, 'forecast_mode': dec.forecast_mode,
            'rules_version': dec.rules_version, 'evaluated_at': dec.evaluated_at, 'elapsed_ms': dec.elapsed_ms, 'can_escalate': dec.can_escalate,
            'alternatives': [{'start': _d(a.start), 'end': _d(a.end), 'outcome': a.outcome, 'label': a.label, 'fit': a.fit, 'distance_days': a.distance_days, 'gap_shifts': a.gap_shifts, 'total_shifts': a.total_shifts, 'why': a.why, 'covered': a.covered, 'not_needed': a.not_needed, 'ordinary_only': a.ordinary_only} for a in dec.alternatives],
            'primary_reason': Engine.primary_reason(dec),
            'balance_after': (round(dec.projected_balance - dec.requested_hours, 1) if dec.projected_balance is not None else None),
            'chart': dec.chart,
            'balance_options': dec.chart.get('balance_options'),
            'rules': [{'code': r.code, 'name': r.name, 'severity': r.severity, 'passed': r.passed, 'public': r.public} for r in dec.rules],
            'summary': dec.chart['summary'],
        }
        if audience == 'manager':
            d['manager_reasons'] = dec.manager_reasons
            d['rules'] = [asdict(r) for r in dec.rules]
            d['demand'] = [{**asdict(x), 'date': _d(x.date)} for x in dec.demand]
            d['cover'] = [{'date': _d(c.date), 'unit': c.unit, 'category': c.category, 'role_class': c.role_class, 'hours': c.hours, 'status': c.status, 'note': c.note,
                           'candidate': asdict(c.candidate) if c.candidate else None, 'alternates': [asdict(a) for a in c.alternates]} for c in dec.cover]
            d['cost_estimate'] = round(sum(c.hours * c.candidate.cost_multiplier for c in dec.cover if c.candidate), 1)
        return d
