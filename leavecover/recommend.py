"""Cover recommendation: who can safely and cheaply take each uncovered shift, and an
auto-assignment that never double-books anyone or breaks a fatigue rule."""
from __future__ import annotations
import collections, datetime as dt
from .config import RuleBook
from .models import Employee, Shift, DemandShift, Candidate, CoverLine
from .rules import FatigueChecker
from .store import DataStore, WORK_CATS
from .coverage import requirement

ONE_DAY = dt.timedelta(days=1)


def _rostered_index(store: DataStore):
    """(unit, date, cat) -> {role: [employee ids]} built once and cached on the store."""
    if getattr(store, '_rostered_people', None) is None:
        idx = collections.defaultdict(lambda: collections.defaultdict(list))
        for eid, lst in store.shifts_by_emp.items():
            role = store.employees[eid].role_class
            for s in lst:
                if s.category in WORK_CATS:
                    idx[(s.unit, s.date, s.category)][role].append(eid)
        store._rostered_people = idx
        bydc = collections.defaultdict(list)
        for (unit, d, cat) in idx.keys():
            bydc[(d, cat)].append(unit)
        store._units_by_datecat = bydc
    return store._rostered_people


def _fortnight_bounds(rb: RuleBook, d: dt.date):
    anchor = dt.date.fromisoformat(rb.settings.get('pay_period_anchor', '2026-05-18'))
    k = (d - anchor).days // 14
    fs = anchor + dt.timedelta(days=14 * k)
    return fs, fs + dt.timedelta(days=13)


def _hours_in_fortnight(store: DataStore, eid: str, fs: dt.date, fe: dt.date, extra: list[Shift]) -> float:
    h = sum(s.paid_hours for s in store.shifts_between(eid, fs, fe) if s.category in WORK_CATS)
    h += sum(s.paid_hours for s in extra if fs <= s.date <= fe)
    return h


class Recommender:
    def __init__(self, store: DataStore, rb: RuleBook, fatigue: FatigueChecker):
        self.s, self.rb, self.fat = store, rb, fatigue

    # ------------------------------------------------------------------ candidates for one gap
    def candidates(self, requester: Employee, ds: DemandShift, plan_extra: dict[str, list[Shift]], recent_extra: dict[str, int], limit: int = 6, why: collections.Counter | None = None) -> list[Candidate]:
        s, rb = self.s, self.rb
        why = why if why is not None else collections.Counter()
        tiers = {t['id']: (i, t) for i, t in enumerate(rb.cover_tiers())}
        if not tiers:
            return []
        allowed_roles = rb.cover_matrix().get(ds.role_class, [ds.role_class])
        same_pos_required = ds.role_class in rb.data.get('same_position_required_for', [])
        rank = rb.ranking()
        fs, fe = _fortnight_bounds(rb, ds.date)
        new_shift = Shift(ds.date, ds.start_min, (ds.start_min + ds.span_min) % 1440, ds.span_min, ds.hours, ds.unit, ds.category, 'COVER')
        pool: dict[str, str | None] = {}   # eid -> donor unit (None = not rostered that day)

        # (a) people attached to the unit who are free that day
        for role in allowed_roles:
            for eid in s.unit_staff.get(ds.unit, {}).get(role, ()):
                pool.setdefault(eid, None)
            for eid in s.unit_role_members.get((ds.unit, role), ()):
                pool.setdefault(eid, None)
        # (b) redeploy: people rostered the same shift elsewhere whose ward stays at requirement (COV-006)
        if 'redeploy' in tiers and not ds.forecast:
            idx = _rostered_index(s)
            for unit in s._units_by_datecat.get((ds.date, ds.category), ()):
                if unit == ds.unit:
                    continue
                roles = idx[(unit, ds.date, ds.category)]
                for role in allowed_roles:
                    for eid in roles.get(role, ()):
                        if eid not in pool:
                            pool[eid] = unit

        out: list[Candidate] = []
        for eid, donor in pool.items():
            if eid == requester.employee_id:
                continue
            e = s.employees.get(eid)
            if not e or e.role_class not in allowed_roles:
                continue
            why['considered'] += 1
            if same_pos_required and e.position_name != requester.position_name:
                why['different position (same position required for this role)'] += 1; continue
            if e.contract_end and e.contract_end < ds.date:
                why['contract ends before that date'] += 1; continue
            if s.is_on_leave(eid, ds.date):
                why['on leave that day'] += 1; continue
            if (eid, ds.date) in getattr(s, 'cover_refused', ()):
                why['declined that shift earlier'] += 1; continue
            extra = plan_extra.get(eid, [])
            if any(x.date == ds.date for x in extra):
                why['already covering another of your shifts that day'] += 1; continue
            reasons: list[str] = []
            if donor is None:
                # must not already be rostered that day (known roster) or usually work it (forecast)
                if s.in_window(eid, ds.date):
                    if any(x.category in WORK_CATS or x.category == 'ONCALL_24H' for x in s.shifts_on(eid, ds.date)):
                        why['already rostered that day'] += 1; continue
                elif s.works_usually(eid, ds.date):
                    why['usually works that day (roster not published yet)'] += 1; continue
                # fatigue on the combined schedule
                fails = self.fat.check(e, new_shift, extra)
                if fails:
                    why['would breach a rest or hours rule (' + ', '.join(sorted({f.code for f in fails})) + ')'] += 1; continue
                hrs_fn = _hours_in_fortnight(s, eid, fs, fe, extra)
                ag = rb.agreement(e.agreement_family)
                if e.job_type == 'CASUAL':
                    if ds.hours < float(ag.get('casual_min_hours', 2)):
                        why['casual: shift shorter than the minimum engagement'] += 1; continue
                    if hrs_fn + ds.hours > float(ag.get('fte_hours_fortnight', 76)):
                        why['casual: would exceed full-time hours this fortnight'] += 1; continue
                    tier = 'casual'; spare = float(ag.get('fte_hours_fortnight', 76)) - hrs_fn
                    reasons.append('casual pool member')
                else:
                    spare = e.contract_hours - hrs_fn
                    if spare >= ds.hours - 1e-6:
                        tier = 'ordinary'; reasons.append(f'{spare:.0f} h spare at ordinary pay this fortnight')
                    else:
                        tier = 'overtime'; reasons.append('already at contracted hours -> overtime')
            else:
                # redeploy from donor unit: donor must stay at its requirement after losing this person
                if not rb.enabled('COV-006'):
                    donor_ok = True
                else:
                    req, _ = requirement(s, rb, donor, ds.date, ds.category, e.role_class)
                    eff = s.effective_count(donor, ds.date, ds.category, e.role_class) or 0
                    donor_ok = eff - 1 >= req
                if not donor_ok:
                    why['their own ward would fall below requirement'] += 1; continue
                tier = 'redeploy'; spare = 0.0
                reasons.append(f'rostered on {donor} that shift; that ward stays at requirement')
            if tier not in tiers:
                why[f'{tier} cover is switched off'] += 1; continue
            ti, tdef = tiers[tier]
            score = rank['tier_weight'] * (len(tiers) - ti)
            if e.primary_unit == ds.unit:
                score += rank['primary_unit_bonus']; reasons.append('home ward')
            elif ds.unit in s.emp_units_recent.get(eid, {}):
                last = s.emp_units_recent[eid][ds.unit]
                if (ds.date - last).days <= 56:
                    score += rank['worked_in_unit_bonus']; reasons.append('worked on this ward recently')
            if e.position_name == requester.position_name:
                score += rank['same_position_bonus']
            if e.role_class == ds.role_class:
                score += rank['exact_role_bonus']
            if e.is_graduate and ds.role_class == 'NURSE_SENIOR':
                score -= rank['graduate_penalty']
            if e.job_type == 'FIXED TERM':
                score -= rank['fixed_term_penalty']
            if rb.enabled('EQ-001'):
                n = recent_extra.get(eid, 0) + len(extra)
                if n:
                    score -= rank['equity_penalty_per_extra_shift'] * n; reasons.append(f'{n} extra shift(s) recently')
            score += rank['hours_headroom_weight'] * max(0.0, spare)
            if (eid, ds.date) in getattr(s, 'cover_confirmed', ()):
                score += 1000; reasons.insert(0, 'already accepted this shift')
            out.append(Candidate(eid, e.role_class, e.position_name, e.job_type, e.agreement_family, tier, round(score, 1), reasons,
                                 float(tdef['cost_multiplier']) * (2.5 if ds.public_holiday else 1.0), ds.hours, round(spare, 1), donor, bool(tdef.get('requires_manager'))))
        out.sort(key=lambda c: -c.score)
        return out[:limit]

    # ------------------------------------------------------------------ auto-assign a whole plan
    def plan(self, requester: Employee, demand: list[DemandShift], recent_extra: dict[str, int]) -> list[CoverLine]:
        lines: list[CoverLine] = []
        plan_extra: dict[str, list[Shift]] = collections.defaultdict(list)
        for ds in sorted(demand, key=lambda x: (x.date, x.start_min)):
            if not ds.gap:
                lines.append(CoverLine(ds.date, ds.unit, ds.category, ds.role_class, ds.hours, 'not_needed', note=f'{ds.available_after} of {ds.required} required still on shift'))
                continue
            why = collections.Counter()
            cands = self.candidates(requester, ds, plan_extra, recent_extra, why=why)
            if not cands:
                n = why.pop('considered', 0)
                tally = dict(why.most_common())
                parts = [f'{v} {k}' for k, v in tally.items()]
                note = (f'{n} colleague(s) checked: ' + '; '.join(parts)) if n else 'nobody with a suitable role is attached to this ward'
                lines.append(CoverLine(ds.date, ds.unit, ds.category, ds.role_class, ds.hours, 'uncovered', note=note, why={'considered': n, **tally}))
                continue
            best = cands[0]
            plan_extra[best.employee_id].append(Shift(ds.date, ds.start_min, (ds.start_min + ds.span_min) % 1440, ds.span_min, ds.hours, ds.unit, ds.category, 'COVER'))
            lines.append(CoverLine(ds.date, ds.unit, ds.category, ds.role_class, ds.hours, 'covered', candidate=best, alternates=cands[1:4]))
        return lines
