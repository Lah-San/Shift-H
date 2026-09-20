"""Typed records used across the engine."""
from __future__ import annotations
import datetime as dt
from dataclasses import dataclass, field, asdict


@dataclass(slots=True)
class Employee:
    employee_id: str
    position_name: str
    rate_grouping: str
    role_class: str
    occupational_group: str
    job_type: str            # PERMANENT | FIXED TERM | CASUAL
    agreement_family: str
    contract_hours: float    # per fortnight
    fte: float
    occupancy_status: str
    emp_start: dt.date | None
    contract_end: dt.date | None
    is_graduate: bool
    primary_unit: str | None
    unit_type: str | None
    window_start: dt.date | None
    window_end: dt.date | None
    continuous_shift: bool = False   # works nights/afternoons -> extra leave week


@dataclass(slots=True)
class Shift:
    date: dt.date
    start_min: int
    end_min: int
    span_min: int
    paid_hours: float
    unit: str
    category: str            # AM PM NIGHT LONG_DAY DAY ONCALL_24H
    work_code: str
    start_dt: dt.datetime = field(default=None)
    end_dt: dt.datetime = field(default=None)

    def __post_init__(self):
        base = dt.datetime.combine(self.date, dt.time())
        self.start_dt = base + dt.timedelta(minutes=self.start_min)
        self.end_dt = self.start_dt + dt.timedelta(minutes=self.span_min)


@dataclass(slots=True)
class RuleResult:
    code: str
    name: str
    severity: str            # hard | soft | info
    passed: bool
    public: str = ''         # anonymised text for the staff member
    manager: str = ''        # detail for the manager
    data: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


@dataclass(slots=True)
class DemandShift:
    date: dt.date
    unit: str
    unit_type: str
    category: str
    role_class: str
    hours: float
    forecast: bool           # True when derived from usual pattern (beyond roster)
    required: int = 0
    available_after: int = 0 # effective staff in role after requester leaves
    gap: bool = False
    requirement_source: str = ''
    public_holiday: bool = False
    start_min: int = 0
    span_min: int = 0
    roster_completeness: float = 1.0


@dataclass(slots=True)
class Candidate:
    employee_id: str
    role_class: str
    position_name: str
    job_type: str
    agreement_family: str
    tier: str                # redeploy | ordinary | casual | overtime | agency
    score: float
    reasons: list[str]
    cost_multiplier: float
    hours: float
    spare_ordinary_hours: float
    from_unit: str | None = None   # for redeploy
    requires_manager: bool = False


@dataclass(slots=True)
class CoverLine:
    date: dt.date
    unit: str
    category: str
    role_class: str
    hours: float
    status: str              # covered | uncovered | not_needed
    candidate: Candidate | None = None
    alternates: list[Candidate] = field(default_factory=list)
    note: str = ''
    why: dict = field(default_factory=dict)   # anonymised tally of why colleagues could not cover (uncovered lines)


@dataclass(slots=True)
class Alternative:
    start: dt.date
    end: dt.date
    outcome: str
    gap_shifts: int
    total_shifts: int
    distance_days: int
    label: str
    fit: int                 # 0-100
    why: list = field(default_factory=list)   # short arguments for this window
    covered: int = 0
    not_needed: int = 0
    ordinary_only: bool = True


@dataclass(slots=True)
class Decision:
    employee_id: str
    leave_type: str
    start: dt.date
    end: dt.date
    outcome: str             # ACCEPTED | RECOMMEND_APPROVE | NEEDS_APPROVAL | DECLINED
    headline: str
    requires_manager: bool
    requested_hours: float
    projected_balance: float | None
    rules: list[RuleResult]
    demand: list[DemandShift]
    cover: list[CoverLine]
    alternatives: list[Alternative]
    breached_codes: list[str]
    public_reasons: list[str]
    manager_reasons: list[str]
    forecast_mode: bool
    rules_version: str
    evaluated_at: str
    elapsed_ms: float
    can_escalate: bool
    chart: dict
