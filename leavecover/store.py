"""In-memory data store built from the cleaned Parquet tables.

Everything the engine needs is turned into dictionaries and sorted lists once at
start-up so that a single leave check is a handful of dictionary lookups
(hundreds of microseconds) instead of DataFrame scans.
"""
from __future__ import annotations
import bisect, collections, datetime as dt, json, os, statistics, time
import polars as pl
from .models import Employee, Shift

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CLEAN = os.path.join(ROOT, 'data', 'clean')   # cleaned parquet files shipped with the app

WORK_CATS = ('AM', 'PM', 'NIGHT', 'LONG_DAY', 'DAY')


def _iso(d):
    return d.isoformat() if d else None


class DataStore:
    def __init__(self, clean_dir: str = DEFAULT_CLEAN, rulebook=None):
        t0 = time.time()
        self.clean_dir = clean_dir
        self.rb = rulebook
        s = rulebook.settings if rulebook else {}
        self.peak_start = dt.date.fromisoformat(s.get('peak_window_start', '2026-07-13'))
        self.peak_end = dt.date.fromisoformat(s.get('peak_window_end', '2026-09-13'))
        self.pattern_min_p = float(s.get('pattern_min_probability', 0.5))
        self.min_obs = int(s.get('min_observations_for_baseline', 4))
        self._load_employees()
        self._load_rosters()
        self._load_leave()
        self._load_balances()
        self._load_holidays()
        self._build_patterns()
        self._build_coverage()
        self.load_seconds = round(time.time() - t0, 2)

    # ------------------------------------------------------------------ employees
    def _load_employees(self):
        cur = pl.read_parquet(os.path.join(self.clean_dir, 'employee_current.parquet'))
        prof = pl.read_parquet(os.path.join(self.clean_dir, 'employee_roster_profile.parquet'))
        cur = cur.join(prof, on='employee_id', how='left')
        self.employees: dict[str, Employee] = {}
        for r in cur.iter_rows(named=True):
            self.employees[r['employee_id']] = Employee(
                employee_id=r['employee_id'], position_name=r['position_name'], rate_grouping=r['rate_grouping'],
                role_class=r['role_class'], occupational_group=r['occupational_group'],
                job_type=r['employee_overall_job_type'] or 'PERMANENT', agreement_family=r['agreement_family'],
                contract_hours=float(r['contract_hours'] or 0), fte=float(r['fte'] or 0),
                occupancy_status=r['occupancy_status'], emp_start=r['emp_start_date'], contract_end=r['position_entry_end_date'],
                is_graduate=bool(r['is_graduate']), primary_unit=r['primary_roster_unit'], unit_type=None,
                window_start=r['roster_window_start'], window_end=r['roster_window_end'])
        # all contracts (for contract-end checks with multiple contracts, take the latest end)
        emp_all = pl.read_parquet(os.path.join(self.clean_dir, 'employees.parquet'))
        ends = emp_all.group_by('employee_id').agg(pl.col('position_entry_end_date').max().alias('mx'), pl.col('position_entry_end_date').is_null().any().alias('open'))
        for r in ends.iter_rows(named=True):
            e = self.employees.get(r['employee_id'])
            if e:
                e.contract_end = None if r['open'] else r['mx']

    # ------------------------------------------------------------------ rosters
    def _load_rosters(self):
        ros = pl.read_parquet(os.path.join(self.clean_dir, 'rosters.parquet')).select(
            'employee_id', 'shift_date', 'start_min', 'end_min', 'span_min', 'paid_hours', 'roster_unit', 'unit_type', 'shift_category', 'work_code')
        self.shifts_by_emp: dict[str, list[Shift]] = collections.defaultdict(list)
        self.shift_dates_by_emp: dict[str, dict[dt.date, list[Shift]]] = collections.defaultdict(dict)
        self.unit_type: dict[str, str] = {}
        self.unit_staff: dict[str, dict[str, set]] = collections.defaultdict(lambda: collections.defaultdict(set))
        self.emp_units_recent: dict[str, dict[str, dt.date]] = collections.defaultdict(dict)
        rostered = collections.defaultdict(lambda: collections.Counter())   # (unit,date,cat) -> Counter(role)
        self.min_date, self.max_date = None, None
        for r in ros.iter_rows(named=True):
            e = self.employees.get(r['employee_id'])
            if not e:
                continue
            sh = Shift(r['shift_date'], int(r['start_min'] or 0), int(r['end_min'] or 0), int(r['span_min'] or 0), float(r['paid_hours'] or 0), r['roster_unit'], r['shift_category'], r['work_code'])
            self.shifts_by_emp[e.employee_id].append(sh)
            self.shift_dates_by_emp[e.employee_id].setdefault(sh.date, []).append(sh)
            self.unit_type[sh.unit] = r['unit_type']
            self.unit_staff[sh.unit][e.role_class].add(e.employee_id)
            prev = self.emp_units_recent[e.employee_id].get(sh.unit)
            if prev is None or sh.date > prev:
                self.emp_units_recent[e.employee_id][sh.unit] = sh.date
            if sh.category in WORK_CATS:
                rostered[(sh.unit, sh.date, sh.category)][e.role_class] += 1
            if self.min_date is None or sh.date < self.min_date:
                self.min_date = sh.date
            if self.max_date is None or sh.date > self.max_date:
                self.max_date = sh.date
        for eid, lst in self.shifts_by_emp.items():
            lst.sort(key=lambda s: (s.start_dt, s.end_dt))
            self.employees[eid].unit_type = self.unit_type.get(self.employees[eid].primary_unit)
            # continuous-shift status: any night or PM shifts in the roster (ANF cl.33(8))
            self.employees[eid].continuous_shift = any(s.category in ('NIGHT', 'PM') for s in lst)
        self._rostered = rostered
        self.shift_start_index: dict[str, list[dt.datetime]] = {eid: [s.start_dt for s in lst] for eid, lst in self.shifts_by_emp.items()}

    # ------------------------------------------------------------------ leave
    def _load_leave(self):
        days = pl.read_parquet(os.path.join(self.clean_dir, 'leave_days.parquet'))
        self.leave_days: dict[str, set] = collections.defaultdict(set)
        self.leave_family_by_day: dict[str, dict[dt.date, str]] = collections.defaultdict(dict)
        for r in days.select('employee_id', 'leave_day', 'leave_family').iter_rows(named=True):
            self.leave_days[r['employee_id']].add(r['leave_day'])
            self.leave_family_by_day[r['employee_id']][r['leave_day']] = r['leave_family']
        lv = pl.read_parquet(os.path.join(self.clean_dir, 'leave.parquet')).filter(pl.col('quality_flag') == 'OK')
        self.leave_records: dict[str, list[dict]] = collections.defaultdict(list)
        for r in lv.select('employee_id', 'leave_record_type', 'leave_status_name', 'leave_type_code', 'leave_type_name', 'leave_family', 'leave_start_date', 'leave_end_date', 'leave_hours', 'calendar_days').iter_rows(named=True):
            self.leave_records[r['employee_id']].append(r)
        for lst in self.leave_records.values():
            lst.sort(key=lambda x: x['leave_start_date'])
        # refused-in-last-12-months flag is unknown in data; kept as hook
        self.refused_recent: dict[str, bool] = {}

    # ------------------------------------------------------------------ balances
    def _load_balances(self):
        bal = pl.read_parquet(os.path.join(self.clean_dir, 'balances.parquet')).filter(pl.col('is_latest_snapshot'))
        self.balances: dict[str, dict[str, dict]] = collections.defaultdict(dict)
        for r in bal.iter_rows(named=True):
            b = self.balances[r['employee_id']].setdefault(r['leave_code'], {'remaining': 0.0, 'untaken': 0.0, 'booked': 0.0, 'excess': 0, 'effective': r['date_effective'], 'groups': []})
            b['remaining'] += float(r['total_leave_remaining_hours'])
            b['untaken'] += float(r['total_leave_untaken_hours'])
            b['booked'] += float(r['booked_leave_hours'])
            b['excess'] = max(b['excess'], int(r['has_excess_leave']))
            b['groups'].append(r['leave_type_group_name'])
            b['anniversary'] = r['leave_anniversary_date']

    # ------------------------------------------------------------------ holidays
    def _load_holidays(self):
        p = os.path.join(self.clean_dir, 'wa_public_holidays.json')
        self.holidays: dict[dt.date, str] = {}
        if os.path.exists(p):
            for h in json.load(open(p, encoding='utf-8'))['holidays']:
                self.holidays[dt.date.fromisoformat(h['date'])] = h['name']

    # ------------------------------------------------------------------ usual pattern per employee
    def _build_patterns(self):
        """pattern[eid][dow] = (unit, category, probability, typical_hours). Used beyond the roster window."""
        self.pattern: dict[str, dict[int, tuple]] = {}
        self.pattern_dist: dict[str, dict[int, dict]] = {}   # eid -> dow -> {(unit, cat): probability}
        for eid, lst in self.shifts_by_emp.items():
            e = self.employees[eid]
            if not lst:
                continue
            weeks = max(1, ((e.window_end - e.window_start).days + 1) / 7)
            bydow = collections.defaultdict(list)
            for s in lst:
                if s.category in WORK_CATS:
                    bydow[s.date.isoweekday()].append(s)
            pat = {}
            dist = {}
            for dow, shs in bydow.items():
                ndays = len({s.date for s in shs})
                # availability forecast: how often this person actually worked (unit, shift) on that weekday, net of leave days
                ld = self.leave_days.get(eid, ())
                dist[dow] = {k: min(1.0, len({x.date for x in shs if (x.unit, x.category) == k and x.date not in ld}) / weeks) for k in {(x.unit, x.category) for x in shs}}
                p = min(1.0, ndays / weeks)
                mode = collections.Counter((s.unit, s.category) for s in shs).most_common(1)[0][0]
                hrs = statistics.median(s.paid_hours for s in shs)
                pat[dow] = (mode[0], mode[1], p, hrs)
            self.pattern[eid] = pat
            self.pattern_dist[eid] = dist

    # ------------------------------------------------------------------ coverage cubes
    def _build_coverage(self):
        """effective[(unit,date,cat)][role] = rostered - on leave that day.
        baseline[(unit,dow,cat,role)] = percentile of effective over the peak window."""
        self.on_leave_count = collections.defaultdict(lambda: collections.Counter())
        for (unit, date, cat), roles in self._rostered.items():
            pass
        # who is rostered AND on leave -> subtract
        for eid, bydate in self.shift_dates_by_emp.items():
            ld = self.leave_days.get(eid)
            if not ld:
                continue
            e = self.employees[eid]
            for d in ld:
                for sh in bydate.get(d, ()):  # rostered while on leave
                    if sh.category in WORK_CATS:
                        self.on_leave_count[(sh.unit, d, sh.category)][e.role_class] += 1
        self.effective: dict[tuple, collections.Counter] = {}
        samples = collections.defaultdict(list)
        pct = float(self.rb.settings.get('baseline_percentile', 0.5)) if self.rb else 0.5
        for key, roles in self._rostered.items():
            unit, date, cat = key
            eff = collections.Counter()
            for role, n in roles.items():
                eff[role] = n - self.on_leave_count.get(key, {}).get(role, 0)
            self.effective[key] = eff
        # sample every peak-window day for every shift type and role the unit uses, INCLUDING days with nobody in that
        # role on that shift (a zero). Without the zeros a role that is only staffed some days gets an inflated requirement.
        unit_cats, unit_roles = collections.defaultdict(set), collections.defaultdict(set)
        for (unit, date, cat), roles in self._rostered.items():
            if self.peak_start <= date <= self.peak_end:
                unit_cats[unit].add(cat); unit_roles[unit] |= set(roles)
        for unit, cats in unit_cats.items():
            d = self.peak_start
            while d <= self.peak_end:
                for cat in cats:
                    eff = self.effective.get((unit, d, cat), {})
                    for role in unit_roles[unit]:
                        samples[(unit, d.isoweekday(), cat, role)].append(eff.get(role, 0))
                d += dt.timedelta(days=1)
        self.baseline: dict[tuple, float] = {}       # requirement floor (configured percentile)
        self.baseline_mid: dict[tuple, float] = {}   # usual staffing (median) used to forecast beyond the roster
        unit_cat_samples = collections.defaultdict(list)
        for k, v in samples.items():
            unit_cat_samples[(k[0], k[2], k[3])].extend(v)
            if len(v) >= self.min_obs:
                self.baseline[k] = self._quantile(v, pct)
                self.baseline_mid[k] = self._quantile(v, 0.5)
        self.baseline_fallback: dict[tuple, float] = {k: self._quantile(v, pct) for k, v in unit_cat_samples.items() if len(v) >= self.min_obs}
        self.baseline_mid_fallback: dict[tuple, float] = {k: self._quantile(v, 0.5) for k, v in unit_cat_samples.items() if len(v) >= self.min_obs}
        # forecast: employees per unit/role (primary unit) for beyond-roster estimates
        self.unit_role_members: dict[tuple, set] = collections.defaultdict(set)
        for eid, e in self.employees.items():
            if e.primary_unit:
                self.unit_role_members[(e.primary_unit, e.role_class)].add(eid)
        self.units = sorted(self.unit_type.keys())
        # historical unplanned-absence rate per (unit, role): rostered shifts that fell on a personal/sick leave day / all rostered shifts (peak window)
        sick = collections.Counter(); tot = collections.Counter()
        for eid, bydate in self.shift_dates_by_emp.items():
            e = self.employees[eid]; fam = self.leave_family_by_day.get(eid, {})
            for d, shs in bydate.items():
                if not (self.peak_start <= d <= self.peak_end):
                    continue
                for sh in shs:
                    if sh.category in WORK_CATS:
                        tot[(sh.unit, e.role_class)] += 1
                        if fam.get(d) == 'PERSONAL_SICK':
                            sick[(sh.unit, e.role_class)] += 1
        self.sick_rate: dict[tuple, float] = {k: sick[k] / v for k, v in tot.items() if v >= 20}
        self.sick_rate_overall = (sum(sick.values()) / sum(tot.values())) if tot else 0.0
        self.cover_refused: set = set()   # (employee_id, date) declined by the colleague
        self.cover_confirmed: set = set()   # (employee_id, date) accepted by the colleague or confirmed by the manager
        self._overlay_version = 0
        self._oow_cache: dict = {}
        # everyone attached to a unit: rostered there or whose primary unit it is
        self._unit_members: dict[str, set] = collections.defaultdict(set)
        for unit, roles in self.unit_staff.items():
            for members in roles.values():
                self._unit_members[unit] |= set(members)
        for (unit, role), members in self.unit_role_members.items():
            self._unit_members[unit] |= set(members)

    @staticmethod
    def _quantile(v, p):
        v = sorted(v)
        if not v:
            return 0.0
        idx = (len(v) - 1) * p
        lo, hi = int(idx), min(int(idx) + 1, len(v) - 1)
        return v[lo] + (v[hi] - v[lo]) * (idx - lo)

    # ------------------------------------------------------------------ live overlay: requests and cover made through the app
    # Every submitted request (pending, escalated, approved, accepted) counts as an absence for everyone evaluated
    # after it, and every proposed cover shift makes that colleague busy. Withdrawn/declined requests are removed.
    def add_absence(self, eid: str, dates, key: str):
        """key = request id, so it can be reversed later."""
        if not hasattr(self, 'dyn_absent'):
            self.dyn_absent = collections.defaultdict(dict)   # eid -> {date: set(keys)}
            self.dyn_cover = collections.defaultdict(dict)    # eid -> {key: [Shift]}
        e = self.employees.get(eid)
        self._overlay_version = getattr(self, '_overlay_version', 0) + 1
        for d in dates:
            already = d in self.leave_days.get(eid, ()) or bool(self.dyn_absent[eid].get(d))
            self.dyn_absent[eid].setdefault(d, set()).add(key)
            if not already and e:
                for sh in self.shift_dates_by_emp.get(eid, {}).get(d, ()):
                    if sh.category in WORK_CATS and (sh.unit, d, sh.category) in self.effective:
                        self.effective[(sh.unit, d, sh.category)][e.role_class] -= 1

    def remove_absence(self, eid: str, key: str):
        if not hasattr(self, 'dyn_absent'):
            return
        e = self.employees.get(eid)
        self._overlay_version = getattr(self, '_overlay_version', 0) + 1
        for d, keys in list(self.dyn_absent.get(eid, {}).items()):
            if key in keys:
                keys.discard(key)
                if not keys:
                    del self.dyn_absent[eid][d]
                    if e and d not in self.leave_days.get(eid, ()):
                        for sh in self.shift_dates_by_emp.get(eid, {}).get(d, ()):
                            if sh.category in WORK_CATS and (sh.unit, d, sh.category) in self.effective:
                                self.effective[(sh.unit, d, sh.category)][e.role_class] += 1

    def add_cover(self, eid: str, shifts: list, key: str):
        if not hasattr(self, 'dyn_absent'):
            self.dyn_absent = collections.defaultdict(dict); self.dyn_cover = collections.defaultdict(dict)
        self.dyn_cover[eid].setdefault(key, []).extend(shifts)
        e = self.employees.get(eid)
        for sh in shifts:
            if e and (sh.unit, sh.date, sh.category) in self.effective:
                self.effective[(sh.unit, sh.date, sh.category)][e.role_class] += 1

    def remove_cover(self, eid: str, key: str):
        if not hasattr(self, 'dyn_cover'):
            return
        shifts = self.dyn_cover.get(eid, {}).pop(key, [])
        e = self.employees.get(eid)
        for sh in shifts:
            if e and (sh.unit, sh.date, sh.category) in self.effective:
                self.effective[(sh.unit, sh.date, sh.category)][e.role_class] -= 1

    def extra_shifts(self, eid: str) -> list:
        return [x for lst in getattr(self, 'dyn_cover', {}).get(eid, {}).values() for x in lst]

    def absences_for(self, eid: str) -> set:
        return set(self.leave_days.get(eid, ())) | set(getattr(self, 'dyn_absent', {}).get(eid, {}).keys())

    # ------------------------------------------------------------------ helpers used by the engine
    def shifts_on(self, eid: str, d: dt.date) -> list[Shift]:
        """Rostered shifts plus cover shifts assigned through the app on that date."""
        base = list(self.shift_dates_by_emp.get(eid, {}).get(d, ()))
        return base + [x for x in self.extra_shifts(eid) if x.date == d]

    def shifts_between(self, eid: str, start: dt.date, end: dt.date) -> list[Shift]:
        bydate = self.shift_dates_by_emp.get(eid, {})
        out = []
        d = start
        while d <= end:
            out.extend(bydate.get(d, ()))
            d += dt.timedelta(days=1)
        out += [x for x in self.extra_shifts(eid) if start <= x.date <= end]
        return out

    def shifts_around(self, eid: str, start: dt.datetime, end: dt.datetime) -> list[Shift]:
        lst = self.shifts_by_emp.get(eid, [])
        idx = self.shift_start_index.get(eid, [])
        lo = bisect.bisect_left(idx, start - dt.timedelta(days=2))
        hi = bisect.bisect_right(idx, end + dt.timedelta(days=2))
        out = lst[lo:hi] + [x for x in self.extra_shifts(eid) if start - dt.timedelta(days=2) <= x.start_dt <= end + dt.timedelta(days=2)]
        out.sort(key=lambda x: x.start_dt)
        return out

    def in_window(self, eid: str, d: dt.date) -> bool:
        e = self.employees.get(eid)
        return bool(e and e.window_start and e.window_start <= d <= e.window_end)

    def is_on_leave(self, eid: str, d: dt.date) -> bool:
        if d in self.leave_days.get(eid, ()):
            return True
        return bool(getattr(self, 'dyn_absent', {}).get(eid, {}).get(d))

    def expected_shift(self, eid: str, d: dt.date):
        """Usual (unit, category, probability, hours) for that weekday, or None."""
        return self.pattern.get(eid, {}).get(d.isoweekday())

    def works_usually(self, eid: str, d: dt.date) -> bool:
        p = self.expected_shift(eid, d)
        return bool(p and p[2] >= self.pattern_min_p)

    def baseline_for(self, unit: str, d: dt.date, cat: str, role: str) -> tuple[float, str]:
        k = (unit, d.isoweekday(), cat, role)
        if k in self.baseline:
            return self.baseline[k], 'usual staffing (same weekday)'
        k2 = (unit, cat, role)
        if k2 in self.baseline_fallback:
            return self.baseline_fallback[k2], 'usual staffing (any weekday)'
        return 0.0, 'no history'

    def usual_for(self, unit: str, d: dt.date, cat: str, role: str) -> float:
        k = (unit, d.isoweekday(), cat, role)
        if k in self.baseline_mid:
            return self.baseline_mid[k]
        return self.baseline_mid_fallback.get((unit, cat, role), 0.0)

    def forecast_effective(self, unit: str, d: dt.date, cat: str, role: str) -> int:
        """Expected staff in role beyond the published roster: usual staffing minus colleagues already booked on leave."""
        return int(round(self.usual_for(unit, d, cat, role))) - self.forecast_on_leave(unit, d, role)

    def _oow_sum(self, unit: str, d: dt.date, cat: str, role: str) -> float:
        """Expected number of `role` staff attached to `unit` whose published roster does not reach d, on that shift:
        sum over those people of how often they work (unit, shift) on that weekday, skipping anyone on leave.
        Rosters are published per person in staggered 4-month windows, so this fills the unpublished part of a ward's roster."""
        key = (unit, d, cat, role, self._overlay_version)
        v = self._oow_cache.get(key)
        if v is not None:
            return v
        total = 0.0
        for eid in self._unit_members.get(unit, ()):
            total += self._oow_p(eid, unit, d, cat, role)
        if len(self._oow_cache) > 200000:
            self._oow_cache.clear()
        self._oow_cache[key] = total
        return total

    def _oow_p(self, eid: str, unit: str, d: dt.date, cat: str, role: str) -> float:
        e = self.employees.get(eid)
        if not e or e.role_class != role or self.in_window(eid, d):
            return 0.0
        # a contract that ends before d is not treated as a vacancy: the funded position is assumed to be filled
        # (the named person is still ineligible to be *assigned* cover, see Recommender)
        if self.is_on_leave(eid, d):
            return 0.0
        return self.pattern_dist.get(eid, {}).get(d.isoweekday(), {}).get((unit, cat), 0.0)

    def out_of_window_forecast(self, unit: str, d: dt.date, cat: str, role: str, exclude: str | None = None) -> int:
        total = self._oow_sum(unit, d, cat, role)
        if exclude:
            total -= self._oow_p(exclude, unit, d, cat, role)
        return int(round(max(0.0, total) + 1e-9))

    def roster_completeness(self, unit: str, d: dt.date) -> float:
        """Share of the unit's staff whose published roster covers date d (1.0 = fully published)."""
        members = self._unit_members.get(unit, ())
        if not members:
            return 0.0
        return sum(1 for eid in members if self.in_window(eid, d)) / len(members)

    def effective_count(self, unit: str, d: dt.date, cat: str, role: str) -> int | None:
        """Staff of `role` expected on (unit, date, shift): published roster minus leave, plus the usual-pattern forecast for
        colleagues whose roster is not published for that date. None only when the date is outside all roster data."""
        c = self.effective.get((unit, d, cat))
        if c is None and not (self.min_date and self.max_date and self.min_date <= d <= self.max_date):
            return None
        real = int(c.get(role, 0)) if c is not None else 0
        return real + self.out_of_window_forecast(unit, d, cat, role)

    def expected_staff(self, unit: str, d: dt.date, cat: str, role: str, exclude: str | None = None) -> tuple[int, float]:
        """(expected staff on shift without `exclude`, roster completeness 0..1).
        Uses the hybrid count while any roster is published for the date and the usual-staffing forecast beyond the last one."""
        comp = self.roster_completeness(unit, d)
        if self.max_date and d <= self.max_date and comp > 0:
            c = self.effective.get((unit, d, cat))
            real = int(c.get(role, 0)) if c is not None else 0
            if exclude and self.in_window(exclude, d) and not self.is_on_leave(exclude, d):
                if any(x.unit == unit and x.category == cat for x in self.shifts_on(exclude, d)):
                    real -= 1
            return max(0, real + self.out_of_window_forecast(unit, d, cat, role, exclude)), comp
        n = self.forecast_effective(unit, d, cat, role)
        if exclude and not self.is_on_leave(exclude, d) and self.works_usually(exclude, d):
            n -= 1
        return max(0, n), 0.0

    def forecast_on_leave(self, unit: str, d: dt.date, role: str) -> int:
        n = 0
        for eid in self.unit_role_members.get((unit, role), ()):
            if d in self.leave_days.get(eid, ()) and self.works_usually(eid, d):
                n += 1
        return n

    def expected_sick_calls(self, unit: str, role: str, staff_on_shift: int) -> float:
        return self.sick_rate.get((unit, role), self.sick_rate_overall) * max(0, staff_on_shift)

    def employee_summary(self, eid: str, today: dt.date) -> dict:
        from .rules import entitlement_hours_per_year
        _accrual = lambda st, emp: entitlement_hours_per_year(st.rb, emp, 'ANNUAL') if st.rb else 0.0
        e = self.employees[eid]
        bal = self.balances.get(eid, {})
        upcoming = [r for r in self.leave_records.get(eid, []) if r['leave_end_date'] >= today][:10]
        nxt = [s for s in self.shifts_by_emp.get(eid, []) if s.date >= today][:14]
        return {
            'employee_id': eid, 'position': e.position_name, 'role_class': e.role_class, 'agreement': e.agreement_family,
            'job_type': e.job_type, 'contract_hours': e.contract_hours, 'fte': e.fte, 'primary_unit': e.primary_unit,
            'unit_type': e.unit_type, 'contract_end': _iso(e.contract_end), 'roster_window': [_iso(e.window_start), _iso(e.window_end)],
            'continuous_shift': e.continuous_shift, 'is_graduate': e.is_graduate,
            'balances': {k: {'remaining_hours': round(v['remaining'], 1), 'booked_hours': round(v['booked'], 1), 'excess': v['excess'], 'as_at': _iso(v['effective']), 'anniversary': _iso(v.get('anniversary'))} for k, v in bal.items()},
            'shift_hours': round((self.pattern.get(eid) and max((p[3] for p in self.pattern[eid].values()), default=0)) or (e.contract_hours / 10 if e.contract_hours else 8), 2),
            'accrual_hours_per_year': round(_accrual(self, e), 1),
            'upcoming_leave': [{'type': r['leave_type_name'], 'family': r['leave_family'], 'start': _iso(r['leave_start_date']), 'end': _iso(r['leave_end_date']), 'hours': r['leave_hours'], 'status': r['leave_status_name']} for r in upcoming],
            'next_shifts': [{'date': _iso(s.date), 'unit': s.unit, 'category': s.category, 'start': f'{s.start_min//60:02d}:{s.start_min%60:02d}', 'end': f'{s.end_min//60:02d}:{s.end_min%60:02d}', 'hours': s.paid_hours} for s in nxt],
        }
