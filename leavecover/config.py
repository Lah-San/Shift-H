"""Rulebook loader. The rulebook (config/rules.yaml) is the single source of policy.
Managers change it from the Rule Studio screen; every change is written back to disk
and recorded in the change log so the engine always runs the current version."""
from __future__ import annotations
import copy, datetime as dt, json, os, threading
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PATH = os.path.join(ROOT, 'config', 'rules.yaml')


class RuleBook:
    def __init__(self, path: str = DEFAULT_PATH):
        self.path = path
        self._lock = threading.RLock()
        self.reload()

    # ------------------------------------------------------------ load / save
    def reload(self):
        with self._lock:
            with open(self.path, encoding='utf-8') as f:
                self.data = yaml.safe_load(f)
            self._index = {r['id']: r for r in self.data['rules']}
            self.version = str(self.data.get('meta', {}).get('version', '0'))

    def save(self, actor: str = 'system', note: str = ''):
        with self._lock:
            self.data.setdefault('meta', {})['updated'] = dt.date.today().isoformat()
            with open(self.path, 'w', encoding='utf-8') as f:
                yaml.safe_dump(self.data, f, sort_keys=False, allow_unicode=True, width=120)
            self._index = {r['id']: r for r in self.data['rules']}
            self._log_change(actor, note)

    def _log_change(self, actor, note):
        log = os.path.join(os.path.dirname(self.path), 'rule_changes.log')
        with open(log, 'a', encoding='utf-8') as f:
            f.write(json.dumps({'ts': dt.datetime.now().isoformat(timespec='seconds'), 'actor': actor, 'note': note}) + '\n')

    # ------------------------------------------------------------ accessors
    @property
    def settings(self) -> dict:
        return self.data['settings']

    def today(self) -> dt.date:
        t = self.settings.get('today')
        return dt.date.fromisoformat(t) if t else dt.date.today()

    def rule(self, rid: str) -> dict:
        return self._index[rid]

    def enabled(self, rid: str) -> bool:
        r = self._index.get(rid)
        return bool(r and r.get('enabled', True))

    def params(self, rid: str) -> dict:
        return self._index.get(rid, {}).get('params', {}) or {}

    SYSTEM_SOURCES = ('system', 'local', 'shift-h', 'learned', 'calibrat')

    def is_policy_backed(self, target_type: str, target_id: str) -> bool:
        """True when a change needs an Australian policy/award/agreement document. Settings, cover options, ward
        requirements and rules whose source is the system itself are operational: a written reason is enough."""
        if target_type == 'agreement':
            return True
        if target_type != 'rule':
            return False
        r = self._index.get(target_id) or {}
        src = str(r.get('source') or '').strip().lower()
        return not (src == '' or src.startswith(self.SYSTEM_SOURCES))

    def rules(self) -> list[dict]:
        for r in self.data['rules']:
            r['policy_backed'] = self.is_policy_backed('rule', r['id'])
        return self.data['rules']

    def agreement(self, family: str) -> dict:
        ag = self.data['agreements']
        return ag.get(family) or ag['OTHER']

    def leave_type(self, code: str) -> dict | None:
        return self.data['leave_types'].get(code)

    def leave_types(self) -> dict:
        return self.data['leave_types']

    def cover_matrix(self) -> dict:
        return self.data['cover_matrix']

    def cover_tiers(self) -> list[dict]:
        return [t for t in self.data['cover_tiers'] if t.get('enabled', True)]

    def all_tiers(self) -> list[dict]:
        return self.data['cover_tiers']

    def ranking(self) -> dict:
        return self.data['ranking']

    # ------------------------------------------------------------ mutation (Rule Studio)
    def update_rule(self, rid: str, enabled: bool | None = None, params: dict | None = None, severity: str | None = None, actor='manager'):
        with self._lock:
            r = self._index[rid]
            if r.get('locked') and enabled is False:
                raise ValueError(f'{rid} is a locked governance rule and cannot be disabled')
            if enabled is not None:
                r['enabled'] = bool(enabled)
            if params:
                r.setdefault('params', {}).update(params)
            if severity in ('hard', 'soft', 'info'):
                r['severity'] = severity
            self.save(actor, f'update_rule {rid} enabled={enabled} params={params} severity={severity}')
            return r

    def update_settings(self, patch: dict, actor='manager'):
        with self._lock:
            for k, v in patch.items():
                if k in self.data['settings']:
                    self.data['settings'][k] = v
            self.save(actor, f'update_settings {patch}')
            return self.data['settings']

    def update_tier(self, tier_id: str, enabled: bool | None = None, cost_multiplier: float | None = None, actor='manager'):
        with self._lock:
            for t in self.data['cover_tiers']:
                if t['id'] == tier_id:
                    if enabled is not None:
                        t['enabled'] = bool(enabled)
                    if cost_multiplier is not None:
                        t['cost_multiplier'] = float(cost_multiplier)
            self.save(actor, f'update_tier {tier_id} enabled={enabled}')
            return self.data['cover_tiers']

    def set_staffing_override(self, unit: str, shift_category: str, role_class: str, required: int | None, actor='manager'):
        with self._lock:
            so = self.data.setdefault('staffing_overrides', {}) or {}
            self.data['staffing_overrides'] = so
            if required is None:
                so.get(unit, {}).get(shift_category, {}).pop(role_class, None)
            else:
                so.setdefault(unit, {}).setdefault(shift_category, {})[role_class] = int(required)
            self.save(actor, f'staffing_override {unit} {shift_category} {role_class}={required}')
            return so

    def set_ratio_ward(self, unit: str, classification: str | None, beds: int = 0, occupancy: float = 1.0, actor='manager'):
        with self._lock:
            rw = self.data.setdefault('ratio_wards', {}) or {}
            self.data['ratio_wards'] = rw
            if classification is None:
                rw.pop(unit, None)
            else:
                rw[unit] = {'classification': classification, 'beds': int(beds), 'occupancy': float(occupancy)}
            self.save(actor, f'ratio_ward {unit}={classification}')
            return rw

    def update_agreement(self, family: str, patch: dict, actor='manager'):
        with self._lock:
            self.data['agreements'][family].update(patch)
            self.save(actor, f'update_agreement {family} {patch}')
            return self.data['agreements'][family]

    def snapshot(self) -> dict:
        return copy.deepcopy(self.data)


def fmt(template: str, **kw) -> str:
    """Safe format: unknown placeholders stay as-is."""
    class _D(dict):
        def __missing__(self, k):
            return '{' + k + '}'
    try:
        return (template or '').format_map(_D(**kw))
    except Exception:
        return template or ''
