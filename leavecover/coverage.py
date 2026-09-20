"""Coverage: what each of the requester's shifts needs, and whether the ward still meets it."""
from __future__ import annotations
import datetime as dt, math
from .config import RuleBook
from .models import Employee, DemandShift
from .store import DataStore, WORK_CATS

ONE_DAY = dt.timedelta(days=1)
# typical start minute and span for forecast shifts (from the roster's most common templates)
TYPICAL_TIMES = {'AM': (420, 510), 'PM': (780, 510), 'NIGHT': (1260, 630), 'LONG_DAY': (420, 750), 'DAY': (480, 510)}


def requirement(store: DataStore, rb: RuleBook, unit: str, d: dt.date, cat: str, role: str) -> tuple[int, str]:
    """Required staff of `role` on (unit, date, shift). Priority: manual override > ratio ward > learned baseline."""
    so = (rb.data.get('staffing_overrides') or {}).get(unit, {}).get(cat, {})
    if role in so:
        return int(so[role]), 'manager-set requirement'
    rw = (rb.data.get('ratio_wards') or {}).get(unit)
    if rw and role in rb.data.get('ratio_direct_care_roles', []):
        defs = rb.data['ratio_definitions'].get(rw['classification'], {}).get(cat if cat in ('AM', 'PM', 'NIGHT', 'LONG_DAY') else 'AM')
        if defs:
            patients = rw.get('beds', 0) * rw.get('occupancy', 1.0)
            direct = math.ceil(patients / defs['ratio']) + int(defs.get('hfsc', 0))
            # share the direct-care requirement across nurse roles in proportion to usual mix
            roles = rb.data['ratio_direct_care_roles']
            bl = {r: store.baseline_for(unit, d, cat, r)[0] for r in roles}
            tot = sum(bl.values())
            share = (bl[role] / tot) if tot > 0 else 1.0 / len(roles)
            return int(math.ceil(direct * share)), f"ratio ward ({rw['classification']}: 1:{defs['ratio']} + {defs.get('hfsc',0)} coordinator)"
    b, src = store.baseline_for(unit, d, cat, role)
    frac = float(rb.settings.get('required_fraction', 1.0))
    return int(math.ceil(b * frac - 1e-9)), src


def demand_for(store: DataStore, rb: RuleBook, emp: Employee, start: dt.date, end: dt.date) -> tuple[list[DemandShift], bool]:
    """The requester's shifts inside the dates. Beyond the roster window, the usual pattern is used (HOR-001)."""
    out: list[DemandShift] = []
    forecast_any = False
    forecast_ok = bool(rb.settings.get('forecast_beyond_roster', True))
    d = start
    while d <= end:
        if store.in_window(emp.employee_id, d):
            for s in store.shift_dates_by_emp.get(emp.employee_id, {}).get(d, ()):
                if s.category in WORK_CATS:
                    out.append(DemandShift(d, s.unit, store.unit_type.get(s.unit, ''), s.category, emp.role_class, s.paid_hours, False, public_holiday=d in store.holidays, start_min=s.start_min, span_min=s.span_min))
        elif forecast_ok:
            p = store.expected_shift(emp.employee_id, d)
            if p and p[2] >= store.pattern_min_p:
                forecast_any = True
                sm, sp = TYPICAL_TIMES.get(p[1], (480, 510))
                out.append(DemandShift(d, p[0], store.unit_type.get(p[0], ''), p[1], emp.role_class, p[3], True, public_holiday=d in store.holidays, start_min=sm, span_min=sp))
        d += ONE_DAY
    return out, forecast_any


def evaluate_gaps(store: DataStore, rb: RuleBook, emp: Employee, demand: list[DemandShift]) -> None:
    """Fill required / available_after / gap on each demand shift (in place)."""
    for ds in demand:
        req, src = requirement(store, rb, ds.unit, ds.date, ds.category, ds.role_class)
        ds.required, ds.requirement_source = req, src
        eff, completeness = store.expected_staff(ds.unit, ds.date, ds.category, ds.role_class, exclude=emp.employee_id)
        ds.roster_completeness = completeness
        ds.available_after = max(0, eff)
        ds.gap = ds.available_after < ds.required and rb.enabled('COV-001')


def team_away_fraction(store: DataStore, emp: Employee, d: dt.date) -> tuple[float, int, int]:
    """Share of the requester's role in their primary unit that is on leave on date d (including this request)."""
    members = store.unit_role_members.get((emp.primary_unit, emp.role_class), set())
    if not members:
        return 0.0, 0, 0
    away = sum(1 for m in members if m != emp.employee_id and store.is_on_leave(m, d)) + 1
    return away / len(members), away, len(members)
