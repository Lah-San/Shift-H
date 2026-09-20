"""FastAPI service: staff flow (linear + chat), manager inbox, Rule Studio, audit.
Run:  python run.py   (serves the UI at http://127.0.0.1:8000)"""
from __future__ import annotations
import copy, datetime as dt, os, collections, re, time
from typing import Any, Optional
import json, secrets, hashlib
from fastapi import FastAPI, HTTPException, Query, Header, UploadFile, File, Form, Depends, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from . import env  # noqa: F401  (loads .env)
from .config import RuleBook
from .store import DataStore
from .engine import Engine
from .db import RequestDB
from .coverage import requirement
from . import chat as chatmod
from . import mailer
from . import policy_check
from . import suggestions as sugg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI_DIR = os.path.join(ROOT, 'ui')

rb = RuleBook()
store = DataStore(rulebook=rb)
db = RequestDB()
engine = Engine(store, rb, db)
app = FastAPI(title='Shift-H', version='1.0')


# ----------------------------------------------------------------------------- models
class CheckIn(BaseModel):
    employee_id: str
    leave_type: str
    start: dt.date
    end: dt.date
    note: str = ''
    channel: str = 'web'


class EscalateIn(BaseModel):
    message: str = ''


class AltIn(BaseModel):
    start: dt.date
    end: dt.date


class DecideIn(BaseModel):
    action: str                     # approve | decline | request_changes
    note: str = ''
    actor: str = 'manager'


class RulePatch(BaseModel):
    enabled: Optional[bool] = None
    params: Optional[dict] = None
    severity: Optional[str] = None


class TierPatch(BaseModel):
    enabled: Optional[bool] = None
    cost_multiplier: Optional[float] = None


class StaffingIn(BaseModel):
    unit: str
    shift_category: str
    role_class: str
    required: Optional[int] = None


class RatioIn(BaseModel):
    unit: str
    classification: Optional[str] = None
    beds: int = 0
    occupancy: float = 1.0


class SimulateIn(BaseModel):
    employee_id: str
    leave_type: str
    start: dt.date
    end: dt.date
    note: str = ''
    rules: dict = {}
    settings: dict = {}
    tiers: dict = {}


class ChatIn(BaseModel):
    employee_id: str = Field(max_length=32)
    session_id: str = Field(default='default', max_length=64, pattern=r'^[A-Za-z0-9_.:-]+$')
    message: str = Field(max_length=4000)


# ----------------------------------------------------------------------------- helpers
STATUS_FROM_OUTCOME = {'ACCEPTED': 'ACCEPTED', 'APPROVED': 'APPROVED', 'RECOMMEND_APPROVE': 'PENDING_MANAGER', 'NEEDS_APPROVAL': 'PENDING_MANAGER', 'DECLINED': 'DECLINED'}


def _emp_or_404(eid: str):
    if eid not in store.employees:
        raise HTTPException(404, f'unknown employee {eid}')
    return store.employees[eid]


def _notify_cover(rid: str, requester: str, cover: list):
    """Tell each proposed colleague, in the app, that Shift-H has asked them to cover a shift."""
    lt = None
    for c in cover or []:
        if c.get('status') == 'covered' and c.get('candidate'):
            cand = c['candidate']['employee_id']
            d = dt.date.fromisoformat(str(c['date']))
            db.notify('staff', cand, f"Cover request: {SHIFT_WORDS.get(c['category'], c['category'])} on {d:%a %d %b} ({c['unit']})",
                      f"Shift-H proposes you cover this {c['hours']} h shift for a colleague on leave ({rid}). Accept or decline under Cover requests.", rid)


LABEL_OUT = {'ACCEPTED': 'recorded', 'RECOMMEND_APPROVE': 'ready to approve', 'NEEDS_APPROVAL': 'manager to confirm', 'DECLINED': 'cannot be approved as requested', 'APPROVED': 'approved'}
LABEL_STATUS = {'ACCEPTED': 'recorded', 'PENDING_MANAGER': 'with your manager', 'ESCALATED': 'escalated to your manager', 'APPROVED': 'approved', 'CHANGES_REQUESTED': 'changes requested'}


def submit(body: CheckIn) -> dict:
    _emp_or_404(body.employee_id)
    if body.leave_type not in rb.leave_types():
        raise HTTPException(400, 'unknown leave type')
    for r0 in db.list_requests(employee_id=body.employee_id, status=list(ACTIVE), limit=50):
        if r0['leave_type'] == body.leave_type and r0['start'] == body.start.isoformat() and r0['end'] == body.end.isoformat():
            out = Engine.to_dict(engine.evaluate(body.employee_id, body.leave_type, body.start, body.end, body.note, with_alternatives=False), 'staff')
            out.update({'request_id': r0['id'], 'status': r0['status'], 'duplicate': True, 'headline': f"Already submitted as {r0['id']} ({LABEL_STATUS.get(r0['status'], r0['status'])})."})
            return out
    dec = engine.evaluate(body.employee_id, body.leave_type, body.start, body.end, body.note)
    mview = Engine.to_dict(dec, 'manager')
    status = STATUS_FROM_OUTCOME[dec.outcome]
    rid = db.create_request(body.employee_id, body.leave_type, body.start.isoformat(), body.end.isoformat(), body.note, dec.outcome, status, mview, body.channel)
    if mview.get('cover'):
        db.save_assignments(rid, mview['cover'], status='proposed')
        _notify_cover(rid, body.employee_id, mview['cover'])
    if status in ACTIVE:
        _apply_overlay(rid, body.employee_id, body.start, body.end, mview.get('cover', []))
    emp = store.employees[body.employee_id]
    if dec.outcome == 'ACCEPTED':
        db.notify('manager', emp.primary_unit or '', f'Urgent leave recorded: {body.employee_id}',
                  f'{rb.leave_type(body.leave_type)["label"]} {body.start} to {body.end}. {mview["summary"]["covered"]} shift(s) have a recommended cover; {mview["summary"]["uncovered"]} need your attention.', rid)
    elif status == 'PENDING_MANAGER':
        db.notify('manager', emp.primary_unit or '', f'Leave request to confirm: {body.employee_id}', f'{rb.leave_type(body.leave_type)["label"]} {body.start} to {body.end} - {dec.headline}', rid)
    out = Engine.to_dict(dec, 'staff')
    out['request_id'] = rid; out['status'] = status
    return out


def _dates(a, b):
    a, b = dt.date.fromisoformat(str(a)), dt.date.fromisoformat(str(b))
    return [a + dt.timedelta(days=i) for i in range((b - a).days + 1)]


def _apply_overlay(rid: str, employee_id: str, start, end, cover: list):
    """Count this request as an absence and its recommended cover as busy colleagues."""
    from .models import Shift
    store.add_absence(employee_id, _dates(start, end), rid)
    for c in cover or []:
        if c.get('status') == 'covered' and c.get('candidate'):
            cand = c['candidate']; d0 = dt.date.fromisoformat(str(c['date']))
            sm, sp = {'AM': (420, 510), 'PM': (780, 510), 'NIGHT': (1260, 630), 'LONG_DAY': (420, 750), 'DAY': (480, 510)}.get(c['category'], (480, 510))
            store.add_cover(cand['employee_id'], [Shift(d0, sm, (sm + sp) % 1440, sp, float(c['hours']), c['unit'], c['category'], 'COVER')], rid)


def _remove_overlay(rid: str, employee_id: str, cover: list):
    store.remove_absence(employee_id, rid)
    for c in cover or []:
        if c.get('candidate'):
            store.remove_cover(c['candidate']['employee_id'], rid)


_refused_by_request: dict[str, set] = {}


def _forget_handshake(rid: str):
    """A withdrawn or declined request releases its colleagues: their acceptances and refusals no longer apply."""
    for x in db.all_assignments():
        if x['request_id'] == rid:
            store.cover_confirmed.discard((x['employee_id'], dt.date.fromisoformat(x['date'])))
    for k in _refused_by_request.pop(rid, set()):
        store.cover_refused.discard(k)


ACTIVE = ('PENDING_MANAGER', 'ESCALATED', 'ACCEPTED', 'APPROVED', 'CHANGES_REQUESTED')
for _r in db.list_requests(status=list(ACTIVE), limit=5000):   # replay on start-up
    _apply_overlay(_r['id'], _r['employee_id'], _r['start'], _r['end'], (_r.get('decision') or {}).get('cover', []))


for _a in db.all_assignments():
    if _a['status'] in ('accepted', 'confirmed'):
        store.cover_confirmed.add((_a['employee_id'], dt.date.fromisoformat(_a['date'])))

chatmod.bind(engine=engine, store=store, rb=rb, db=db, submit_fn=submit, CheckIn=CheckIn, withdraw_fn=lambda rid: withdraw(rid))


CLOSED_PAGE = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Shift-H</title>
<style>body{margin:0;min-height:100vh;display:grid;place-items:center;font-family:'Segoe UI',Arial,sans-serif;background:linear-gradient(160deg,#E3F3F4,#F2F5F8 45%,#ECE9FA);color:#1C2733}
.c{background:#fff;border:1px solid #D9E1EA;border-top:5px solid #0E7C86;border-radius:14px;padding:32px;max-width:440px;margin:24px;box-shadow:0 12px 32px rgba(23,50,77,.18)}
.m{display:inline-grid;place-items:center;width:40px;height:40px;border-radius:10px;background:#0E7C86;color:#fff;font-weight:800;margin-right:10px;vertical-align:middle}
h1{font-size:22px;color:#17324D;margin:14px 0 8px}p{color:#46586A;line-height:1.5;margin:0 0 10px}
a.b{display:inline-block;margin-top:8px;background:#0E7C86;color:#fff;text-decoration:none;padding:10px 16px;border-radius:8px;font-weight:600}</style></head>
<body><div class="c"><span class="m">SH</span><b style="color:#17324D;font-size:18px">Shift-H</b><h1>The demo has ended</h1>
<p>Shift-H, the clinician leave and cover assistant built for the WA Health hackathon, is no longer open to the public.</p>
<p>Access is available on request.</p>{contact}</div></body></html>"""


@app.middleware('http')
async def _demo_closed(request, call_next):
    """DEMO_CLOSED=1 puts a closed page in front of everything (pages and API); DEMO_CONTACT adds a request-access link."""
    if os.environ.get('DEMO_CLOSED', '') not in ('', '0', 'false'):
        from fastapi.responses import HTMLResponse, JSONResponse
        if request.url.path.startswith('/api/'):
            return JSONResponse({'detail': 'The demo has ended. Access is available on request.'}, status_code=403)
        contact = os.environ.get('DEMO_CONTACT', '')
        link = f'<a class="b" href="mailto:{contact}?subject=Shift-H%20access%20request">Request access</a>' if contact else ''
        return HTMLResponse(CLOSED_PAGE.replace('{contact}', link), status_code=503, headers={'Cache-Control': 'no-store'})
    return await call_next(request)


@app.middleware('http')
async def _security_headers(request, call_next):
    resp = await call_next(request)
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    resp.headers['X-Frame-Options'] = 'DENY'
    resp.headers['Referrer-Policy'] = 'same-origin'
    resp.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=()'
    if request.url.path.startswith('/api/'):
        resp.headers['Cache-Control'] = 'no-store'
    return resp


# ----------------------------------------------------------------------------- auth (demo accounts)
ACCOUNTS = {'admin': {'password': os.environ.get('ADMIN_PASSWORD', 'admin'), 'role': 'admin'},
            'manager': {'password': os.environ.get('MANAGER_PASSWORD', 'manager'), 'role': 'manager'}}
_tokens: dict[str, dict] = {}


class LoginIn(BaseModel):
    username: str
    password: str = ''


_login_fails: dict[str, list] = {}
STAFF_PASSWORD = os.environ.get('STAFF_PASSWORD', 'password')


@app.post('/api/auth/login')
def login(body: LoginIn):
    u = body.username.strip()
    now = time.time()
    fails = [t for t in _login_fails.get(u.upper(), []) if now - t < 300]
    if len(fails) >= 5:
        raise HTTPException(429, 'Too many failed sign-ins. Try again in five minutes.')
    ok, role = False, None
    if u in ACCOUNTS:
        ok = secrets.compare_digest(ACCOUNTS[u]['password'], body.password or '\0'); role = ACCOUNTS[u]['role']
    elif u.upper() in store.employees:
        ok = secrets.compare_digest(STAFF_PASSWORD, body.password or '\0'); u = u.upper(); role = 'staff'
    else:
        _login_fails[u.upper()] = fails + [now]
        raise HTTPException(401, 'unknown user')
    if not ok:
        _login_fails[u.upper()] = fails + [now]
        raise HTTPException(401, 'wrong password')
    _login_fails.pop(u.upper(), None)
    if len(_tokens) > 5000:   # bound the session table
        for k in list(_tokens)[:1000]:
            _tokens.pop(k, None)
    tok = secrets.token_urlsafe(24)
    _tokens[tok] = {'user': u, 'role': role, 'ts': now}
    db.audit(u, 'login', None, {'role': role})
    return {'token': tok, 'user': u, 'role': role}


_prompt_log: dict[str, list] = {}   # 'u:<account>' / 'ip:<address>' -> timestamps of chat prompts


def _client_ip(request) -> str:
    xff = request.headers.get('x-forwarded-for', '') if request is not None else ''
    if xff:
        return xff.split(',')[0].strip()
    return (request.client.host if request is not None and request.client else '') or 'unknown'


def _chat_limit(who: dict, request):
    """30 prompts per 30 minutes per signed-in account and 15 per IP address by default (settings chat_prompts_per_user,
    chat_prompts_per_ip, chat_limit_window_minutes). Raises 429 with the wait time."""
    st = rb.settings
    window = float(st.get('chat_limit_window_minutes', 30)) * 60
    now = time.time()
    checks = [('u:' + who['user'], int(st.get('chat_prompts_per_user', 30)), 'this account'),
              ('ip:' + _client_ip(request), int(st.get('chat_prompts_per_ip', 15)), 'this network address')]
    for key, limit, label in checks:
        if limit <= 0:
            continue
        hits = [t for t in _prompt_log.get(key, []) if now - t < window]
        _prompt_log[key] = hits
        if len(hits) >= limit:
            wait = int((window - (now - hits[0])) / 60) + 1
            raise HTTPException(429, f'Assistant paused for {label}: this demo allows {limit} Gemini prompts every {int(window // 60)} minutes so the shared free quota lasts the whole event, and that limit has been reached. It resets in about {wait} minute{"s" if wait != 1 else ""}. The Request leave form, best windows and cover requests keep working without the assistant.')
    for key, limit, _ in checks:
        if limit > 0:
            _prompt_log.setdefault(key, []).append(now)
    if len(_prompt_log) > 20000:
        for k in [k for k, v in _prompt_log.items() if not v or now - v[-1] > window]:
            _prompt_log.pop(k, None)


def require_user(x_auth_token: str = Header(default='')):
    """Any signed-in user (staff, manager or super user)."""
    who = _tokens.get(x_auth_token)
    if not who:
        raise HTTPException(401, 'Sign in first.')
    return who


def _own(who, employee_id: str):
    """Staff may only act for themselves; managers and the super user may act for anyone (e.g. record a phoned-in sick day).
    Internal calls (no request context) pass through."""
    if not isinstance(who, dict):
        return
    if who['role'] in ('manager', 'admin'):
        return
    if who['user'] != (employee_id or '').upper():
        raise HTTPException(403, 'You can only access your own leave.')


def require_role(*roles):
    def dep(x_auth_token: str = Header(default='')):
        who = _tokens.get(x_auth_token)
        if not who or who['role'] not in roles:
            raise HTTPException(403, f'requires one of: {", ".join(roles)}')
        return who
    return dep


# ----------------------------------------------------------------------------- staff
@app.get('/api/health')
def health():
    return {'ok': True, 'employees': len(store.employees), 'units': len(store.units), 'rules_version': rb.version, 'today': rb.today().isoformat(), 'load_seconds': store.load_seconds, 'gemini': chatmod.gemini_available()}


@app.get('/api/employees')
def employees(q: str = '', limit: int = 20, who=Depends(require_role('manager', 'admin'))):
    limit = max(1, min(int(limit), 100))
    q = q.strip().upper()
    out = []
    for eid, e in store.employees.items():
        if not q or q in eid or q in e.position_name.upper() or (e.primary_unit and q in e.primary_unit):
            out.append({'employee_id': eid, 'position': e.position_name, 'unit': e.primary_unit, 'job_type': e.job_type, 'agreement': e.agreement_family})
            if len(out) >= limit:
                break
    return out


@app.get('/api/demo-personas')
def demo_personas(who=Depends(require_user)):
    """A handful of employees that show off different paths (chosen for the demo)."""
    picks = [('SYN000009', 'Nurse with excess leave (priority + ELMP prompt)'), ('SYN002452', 'Ward RN, short notice request'),
             ('SYN000024', 'Low balance (declined with hint)'), ('SYN004753', 'Casual RN (paid leave not available)'),
             ('SYN003379', 'Clinical nurse, very high balance, fixed-term end'), ('SYN007785', 'Dietitian, contract ended')]
    out = []
    for eid, why in picks:
        e = store.employees.get(eid)
        if e:
            out.append({'employee_id': eid, 'position': e.position_name, 'unit': e.primary_unit, 'why': why})
    # add a doctor and an allied health professional with roster in window
    for eid, e in store.employees.items():
        if len(out) >= 9:
            break
        if e.role_class == 'MED_REGISTRAR' and e.window_end and e.window_end > rb.today() + dt.timedelta(days=20) and all(o['employee_id'] != eid for o in out):
            out.append({'employee_id': eid, 'position': e.position_name, 'unit': e.primary_unit, 'why': 'Doctor in training (AMA hour caps)'})
    return out


@app.get('/api/me/{eid}')
def me(eid: str, who=Depends(require_user)):
    _own(who, eid)
    _emp_or_404(eid)
    s = store.employee_summary(eid, rb.today())
    s['requests'] = [{k: r[k] for k in ('id', 'leave_type', 'start', 'end', 'outcome', 'status', 'created', 'manager_note')} for r in db.list_requests(employee_id=eid, limit=20)]
    s['assigned_cover'] = db.assignments_for(eid)
    s['leave_types'] = [{'code': k, **v} for k, v in rb.leave_types().items() if (store.employees[eid].job_type != 'CASUAL' or v.get('casual_allowed'))]
    s['today'] = rb.today().isoformat()
    s['public_holidays'] = {d.isoformat(): n for d, n in store.holidays.items()}
    s['contact_email'] = db.get_contact(eid)
    return s


@app.get('/api/leave-types')
def leave_types():
    return [{'code': k, **v} for k, v in rb.leave_types().items()]


@app.post('/api/check')
def check(body: CheckIn, who=Depends(require_user)):
    _own(who, body.employee_id)
    _emp_or_404(body.employee_id)
    if body.leave_type not in rb.leave_types():
        raise HTTPException(400, 'unknown leave type')
    dec = engine.evaluate(body.employee_id, body.leave_type, body.start, body.end, body.note)
    db.audit('staff', 'check', None, {'employee_id': body.employee_id, 'leave_type': body.leave_type, 'start': str(body.start), 'end': str(body.end), 'outcome': dec.outcome})
    return Engine.to_dict(dec, 'staff')


@app.post('/api/requests')
def create_request(body: CheckIn, who=Depends(require_user)):
    _own(who, body.employee_id)
    return submit(body)


@app.get('/api/requests')
def list_requests(employee_id: str, who=Depends(require_user)):
    _own(who, employee_id)
    return [{k: r[k] for k in ('id', 'leave_type', 'start', 'end', 'outcome', 'status', 'created', 'manager_note', 'note')} for r in db.list_requests(employee_id=employee_id)]


@app.get('/api/requests/{rid}')
def get_request(rid: str, who=Depends(require_user)):
    r = db.get_request(rid)
    if r:
        _own(who, r['employee_id'])
    if not r:
        raise HTTPException(404)
    d = r['decision'] or {}
    staff_view = {k: d.get(k) for k in ('outcome', 'headline', 'requires_manager', 'requested_hours', 'projected_balance', 'breached_codes', 'public_reasons', 'alternatives', 'chart', 'summary', 'forecast_mode', 'rules_version', 'evaluated_at', 'can_escalate')}
    staff_view['rules'] = [{k: x.get(k) for k in ('code', 'name', 'severity', 'passed', 'public')} for x in d.get('rules', [])]
    return {**{k: r[k] for k in ('id', 'employee_id', 'leave_type', 'start', 'end', 'note', 'outcome', 'status', 'created', 'updated', 'manager_note')}, 'decision': staff_view}


@app.post('/api/requests/{rid}/escalate')
def escalate(rid: str, body: EscalateIn, who=Depends(require_user)):
    r = db.get_request(rid)
    if r:
        _own(who, r['employee_id'])
    if not r:
        raise HTTPException(404)
    if r['status'] not in ('DECLINED', 'PENDING_MANAGER'):
        raise HTTPException(400, 'this request cannot be escalated')
    db.update_request(rid, status='ESCALATED', manager_note=f'Escalated by staff member: {body.message}')
    if r['status'] == 'DECLINED':
        _apply_overlay(rid, r['employee_id'], r['start'], r['end'], (r.get('decision') or {}).get('cover', []))
    db.audit(r['employee_id'], 'escalate', rid, {'message': body.message})
    emp = store.employees[r['employee_id']]
    db.notify('manager', emp.primary_unit or '', f'Escalation: {r["employee_id"]}', f'{r["leave_type"]} {r["start"]} to {r["end"]}. "{body.message}"', rid)
    return db.get_request(rid) | {'decision': None}


@app.post('/api/requests/{rid}/accept-alternative')
def accept_alternative(rid: str, body: AltIn, who=Depends(require_user)):
    r = db.get_request(rid)
    if r:
        _own(who, r['employee_id'])
    if not r:
        raise HTTPException(404)
    db.update_request(rid, status='WITHDRAWN', manager_note='Replaced by alternative dates')
    _remove_overlay(rid, r['employee_id'], (r.get('decision') or {}).get('cover', []))
    db.audit(r['employee_id'], 'accept_alternative', rid, {'start': str(body.start), 'end': str(body.end)})
    return submit(CheckIn(employee_id=r['employee_id'], leave_type=r['leave_type'], start=body.start, end=body.end, note=(r['note'] or '') + ' (alternative dates)', channel='web'))


WITHDRAWABLE = ('PENDING_MANAGER', 'ESCALATED', 'CHANGES_REQUESTED', 'DECLINED', 'ACCEPTED')


@app.post('/api/requests/{rid}/withdraw')
def withdraw(rid: str, who=Depends(require_user)):
    r = db.get_request(rid)
    if not r:
        raise HTTPException(404)
    _own(who, r['employee_id'])
    if r['status'] == 'APPROVED':
        raise HTTPException(400, 'This leave is already approved. Ask your manager to cancel it.')
    if r['status'] not in WITHDRAWABLE:
        raise HTTPException(400, f'This request is {r["status"].lower().replace("_", " ")} and cannot be withdrawn.')
    db.update_request(rid, status='WITHDRAWN')
    db.set_assignments_status(rid, 'cancelled')
    _remove_overlay(rid, r['employee_id'], (r.get('decision') or {}).get('cover', []))
    _forget_handshake(rid)
    db.audit(r['employee_id'], 'withdraw', rid, {})
    return {'ok': True}


# ----------------------------------------------------------------------------- suggestions engine
@app.get('/api/suggestions/{eid}')
def staff_suggestions(eid: str, weeks: int = 12, days: int = 7, leave_type: str = 'ANNUAL', who=Depends(require_user)):
    _own(who, eid)
    _emp_or_404(eid)
    weeks, days = max(1, min(int(weeks), 52)), max(1, min(int(days), 182))
    return sugg.staff_suggestions(store, rb, engine, eid, weeks, days, leave_type)


@app.get('/api/manager/suggestions')
def manager_suggestions(unit: str, days: int = 28, who=Depends(require_role('manager', 'admin'))):
    return sugg.manager_suggestions(store, rb, db, unit, days)


@app.get('/api/admin/calibration')
def calibration(who=Depends(require_role('manager', 'admin'))):
    rep = {}
    pth = os.path.join(ROOT, 'data', 'calibration_report.json')
    if os.path.exists(pth):
        rep = json.load(open(pth))
    return {'suggestions': sugg.calibration_suggestions(store, rb), 'report': rep}


# ----------------------------------------------------------------------------- manager
@app.get('/api/manager/inbox')
def inbox(status: str = 'PENDING_MANAGER,ESCALATED,ACCEPTED', who=Depends(require_role('manager', 'admin'))):
    rows = db.list_requests(status=status.split(','))
    out = []
    for r in rows:
        d = r['decision'] or {}
        e = store.employees.get(r['employee_id'])
        out.append({**{k: r[k] for k in ('id', 'employee_id', 'leave_type', 'start', 'end', 'note', 'outcome', 'status', 'created', 'manager_note')},
                    'position': e.position_name if e else '', 'unit': e.primary_unit if e else '', 'headline': d.get('headline'), 'summary': d.get('summary'),
                    'breached_codes': d.get('breached_codes'), 'cost_estimate': d.get('cost_estimate'), 'excess': any(x.get('code') == 'BAL-002' for x in d.get('rules', []))})
    # PRI-001: excess leave first, then oldest
    out.sort(key=lambda x: (0 if x['status'] == 'ESCALATED' else 1, 0 if x['excess'] else 1, x['created']))
    return out


@app.get('/api/manager/requests/{rid}')
def manager_request(rid: str, who=Depends(require_role('manager', 'admin'))):
    r = db.get_request(rid)
    if not r:
        raise HTTPException(404)
    e = store.employees.get(r['employee_id'])
    asg = [a for a in db.all_assignments() if a['request_id'] == rid]
    st = {(a['date'], a['employee_id']): a['status'] for a in asg}
    for c in (r.get('decision') or {}).get('cover', []):
        if c.get('candidate'):
            c['response'] = st.get((c['date'], c['candidate']['employee_id']), 'proposed')
    return {**r, 'position': e.position_name if e else '', 'unit': e.primary_unit if e else '', 'assignments': asg,
            'emails': [{k: m[k] for k in ('ts', 'to_addr', 'employee_id', 'kind', 'status')} for m in db.emails(300) if m['request_id'] == rid],
            'colleagues': {a['employee_id']: {'position': store.employees[a['employee_id']].position_name, 'unit': store.employees[a['employee_id']].primary_unit} for a in asg if a['employee_id'] in store.employees}}


@app.post('/api/manager/requests/{rid}/decide')
def decide(rid: str, body: DecideIn, who=Depends(require_role('manager', 'admin'))):
    body.actor = who['user']
    r = db.get_request(rid)
    if not r:
        raise HTTPException(404)
    if body.action == 'approve':
        db.update_request(rid, status='APPROVED', manager_note=body.note, outcome='APPROVED')
        db.set_assignments_status(rid, 'confirmed')
        for x in db.all_assignments():
            if x['request_id'] == rid and x['status'] == 'confirmed':
                store.cover_confirmed.add((x['employee_id'], dt.date.fromisoformat(x['date'])))
        db.notify('staff', r['employee_id'], 'Leave approved', f'{r["leave_type"]} {r["start"]} to {r["end"]} approved. {body.note}', rid)
    elif body.action == 'decline':
        if not body.note.strip():
            raise HTTPException(400, 'A written reason is required when declining (ANF cl.33(1)(d)).')
        db.update_request(rid, status='DECLINED_BY_MANAGER', manager_note=body.note, outcome='DECLINED')
        db.set_assignments_status(rid, 'cancelled')
        _remove_overlay(rid, r['employee_id'], (r.get('decision') or {}).get('cover', []))
        _forget_handshake(rid)
        db.notify('staff', r['employee_id'], 'Leave not approved', body.note, rid)
    elif body.action == 'request_changes':
        db.update_request(rid, status='CHANGES_REQUESTED', manager_note=body.note)
        db.notify('staff', r['employee_id'], 'Manager asked for changes', body.note, rid)
    else:
        raise HTTPException(400, 'unknown action')
    db.audit(body.actor, f'decide:{body.action}', rid, {'note': body.note})
    return db.get_request(rid)


class PlanSwapIn(BaseModel):
    line: int
    employee_id: str


@app.post('/api/manager/requests/{rid}/plan/swap')
def plan_swap(rid: str, body: PlanSwapIn, who=Depends(require_role('manager', 'admin'))):
    """Manager replaces the recommended colleague on one shift with one of the listed alternates."""
    r = db.get_request(rid)
    if not r or not r.get('decision'):
        raise HTTPException(404)
    dec = r['decision']; cover = dec.get('cover', [])
    if body.line < 0 or body.line >= len(cover):
        raise HTTPException(400, 'bad line')
    ln = cover[body.line]
    alt = next((a for a in ln.get('alternates', []) if a['employee_id'] == body.employee_id), None)
    if not alt:
        raise HTTPException(400, 'that colleague is not one of the listed alternates')
    old = ln.get('candidate')
    ln['candidate'] = alt; ln['status'] = 'covered'
    ln['alternates'] = [a for a in ln['alternates'] if a['employee_id'] != alt['employee_id']] + ([old] if old else [])
    dec['cost_estimate'] = round(sum(c['hours'] * c['candidate']['cost_multiplier'] for c in cover if c.get('candidate')), 1)
    dec['summary'] = {**dec.get('summary', {}), 'covered': sum(1 for c in cover if c['status'] == 'covered'), 'uncovered': sum(1 for c in cover if c['status'] == 'uncovered')}
    db.update_request(rid, decision=dec)
    db.save_assignments(rid, cover, status='proposed')
    _remove_overlay(rid, r['employee_id'], (r.get('decision') or {}).get('cover', [])); _apply_overlay(rid, r['employee_id'], r['start'], r['end'], cover)
    db.audit(who['user'], 'plan_swap', rid, {'line': body.line, 'from': old and old['employee_id'], 'to': alt['employee_id']})
    return db.get_request(rid)


@app.post('/api/manager/requests/{rid}/plan/recompute')
def plan_recompute(rid: str, who=Depends(require_role('manager', 'admin'))):
    """Re-run the engine (e.g. after a rule change) and refresh the plan."""
    r = db.get_request(rid)
    if not r:
        raise HTTPException(404)
    _remove_overlay(rid, r['employee_id'], (r.get('decision') or {}).get('cover', []))
    dec = engine.evaluate(r['employee_id'], r['leave_type'], dt.date.fromisoformat(r['start']), dt.date.fromisoformat(r['end']), r['note'] or '')
    mview = Engine.to_dict(dec, 'manager')
    db.update_request(rid, decision=mview, outcome=dec.outcome)
    db.save_assignments(rid, mview.get('cover', []), status='proposed')
    _apply_overlay(rid, r['employee_id'], r['start'], r['end'], mview.get('cover', []))
    db.audit(who['user'], 'plan_recompute', rid, {'outcome': dec.outcome, 'rules_version': rb.version})
    return db.get_request(rid)


@app.get('/api/manager/notifications')
def notifications(audience: str = 'manager', who=Depends(require_role('manager', 'admin'))):
    return db.notifications(audience)


@app.get('/api/manager/notifications/unread')
def notifications_unread(audience: str = 'manager', who=Depends(require_role('manager', 'admin'))):
    return {'unread': db.unread_notifications(audience)}


@app.post('/api/manager/notifications/read')
def notifications_read(audience: str = 'manager', who=Depends(require_role('manager', 'admin'))):
    db.mark_notifications_read(audience)
    return {'ok': True}


@app.get('/api/manager/units')
def units(who=Depends(require_role('manager', 'admin'))):
    out = []
    for u in store.units:
        roles = {r: len(m) for r, m in store.unit_staff.get(u, {}).items()}
        out.append({'unit': u, 'type': store.unit_type.get(u), 'staff': sum(roles.values()), 'roles': roles})
    out.sort(key=lambda x: -x['staff'])
    return out


@app.get('/api/manager/coverage')
def coverage(unit: str, start: dt.date, end: dt.date, role_class: str = '', who=Depends(require_role('manager', 'admin'))):
    """Heat-map data: per date x shift, effective staff vs requirement (known roster) or forecast."""
    days = []
    d = start
    roles = [role_class] if role_class else sorted(store.unit_staff.get(unit, {}).keys())
    while d <= end and (d - start).days < 62:
        cells = []
        comp = store.roster_completeness(unit, d)
        for cat in ('AM', 'PM', 'NIGHT', 'LONG_DAY'):
            for role in roles:
                req, src = requirement(store, rb, unit, d, cat, role)
                eff, comp = store.expected_staff(unit, d, cat, role)
                if req == 0 and eff == 0:
                    continue
                cells.append({'shift': cat, 'role': role, 'effective': eff, 'required': req, 'known': comp >= 0.95, 'completeness': round(comp, 2), 'source': src})
        days.append({'date': d.isoformat(), 'public_holiday': store.holidays.get(d), 'cells': cells})
        d += dt.timedelta(days=1)
    return {'unit': unit, 'type': store.unit_type.get(unit), 'days': days}


@app.get('/api/manager/equity')
def equity(who=Depends(require_role('manager', 'admin'))):
    counts = collections.Counter()
    tiers = collections.Counter()
    for a in db.all_assignments():
        if a['status'] in ('proposed', 'confirmed'):
            counts[a['employee_id']] += 1
            tiers[a['tier']] += 1
    top = [{'employee_id': k, 'extra_shifts': v, 'position': store.employees[k].position_name if k in store.employees else ''} for k, v in counts.most_common(15)]
    return {'top': top, 'by_tier': dict(tiers), 'people_with_extra_shifts': len(counts), 'total_extra_shifts': sum(counts.values())}


@app.get('/api/manager/elmp/{eid}')
def elmp(eid: str, who=Depends(require_role('manager', 'admin'))):
    """Pre-filled Employee Leave Management Plan (MP 0100/18 template) for staff with excess leave."""
    e = _emp_or_404(eid)
    from .rules import entitlement_hours_per_year
    al = store.balances.get(eid, {}).get('AL', {})
    per_year = entitlement_hours_per_year(rb, e, 'ANNUAL')
    thr = 2 * per_year
    excess = max(0.0, al.get('remaining', 0) - thr)
    return {'employee_id': eid, 'position': e.position_name, 'unit': e.primary_unit, 'agreement': rb.agreement(e.agreement_family)['label'],
            'current_balance_hours': round(al.get('remaining', 0), 1), 'two_entitlements_hours': round(thr, 1), 'excess_hours': round(excess, 1),
            'accrual_next_12_months_hours': round(per_year, 1), 'total_to_clear_hours': round(excess + per_year, 1),
            'suggested_blocks': [{'weeks': 2, 'hours': round((e.contract_hours or 76), 1)} for _ in range(min(4, int((excess + per_year) // max(1, (e.contract_hours or 76))) + 1))],
            'cash_out_option': e.agreement_family in ('ANF', 'AMA'), 'template': 'Employee Leave Management Plan (MP 0100/18)'}


# ----------------------------------------------------------------------------- admin / rule studio
@app.get('/api/admin/rules')
def rules(who=Depends(require_role('manager', 'admin'))):
    return {'version': rb.version, 'rules': rb.rules(), 'settings': rb.settings, 'tiers': rb.all_tiers(), 'ranking': rb.ranking(),
            'agreements': rb.data['agreements'], 'cover_matrix': rb.cover_matrix(), 'staffing_overrides': rb.data.get('staffing_overrides') or {}, 'ratio_wards': rb.data.get('ratio_wards') or {}}


@app.patch('/api/admin/rules/{rid}')
def patch_rule(rid: str, body: RulePatch, who=Depends(require_role('admin'))):
    raise HTTPException(400, 'Direct edits are disabled. Submit a policy-verified proposal via /api/admin/proposals.')
    try:
        r = rb.update_rule(rid, body.enabled, body.params, body.severity)
    except KeyError:
        raise HTTPException(404, 'unknown rule')
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    db.audit('manager', 'rule_update', None, {'rule': rid, 'enabled': body.enabled, 'params': body.params, 'severity': body.severity})
    return r


@app.patch('/api/admin/settings')
def patch_settings(body: dict, who=Depends(require_role('admin'))):
    raise HTTPException(400, 'Direct edits are disabled. Submit a policy-verified proposal via /api/admin/proposals.')
    s = rb.update_settings(body)
    db.audit('manager', 'settings_update', None, body)
    return s


@app.patch('/api/admin/tiers/{tid}')
def patch_tier(tid: str, body: TierPatch, who=Depends(require_role('admin'))):
    raise HTTPException(400, 'Direct edits are disabled. Submit a policy-verified proposal via /api/admin/proposals.')
    t = rb.update_tier(tid, body.enabled, body.cost_multiplier)
    db.audit('manager', 'tier_update', None, {'tier': tid, **body.model_dump()})
    return t


@app.patch('/api/admin/agreements/{family}')
def patch_agreement(family: str, body: dict, who=Depends(require_role('admin'))):
    raise HTTPException(400, 'Direct edits are disabled. Submit a policy-verified proposal via /api/admin/proposals.')
    if family not in rb.data['agreements']:
        raise HTTPException(404)
    out = rb.update_agreement(family, body)
    db.audit('manager', 'agreement_update', None, {'family': family, **body})
    return out


@app.post('/api/admin/staffing')
def staffing(body: StaffingIn, who=Depends(require_role('manager', 'admin'))):
    out = rb.set_staffing_override(body.unit, body.shift_category, body.role_class, body.required)
    db.audit('manager', 'staffing_override', None, body.model_dump())
    return out


@app.post('/api/admin/ratio-ward')
def ratio_ward(body: RatioIn, who=Depends(require_role('manager', 'admin'))):
    out = rb.set_ratio_ward(body.unit, body.classification, body.beds, body.occupancy)
    db.audit('manager', 'ratio_ward', None, body.model_dump())
    return out


@app.post('/api/admin/simulate')
def simulate(body: SimulateIn, who=Depends(require_role('manager', 'admin'))):
    """What-if: evaluate a request under temporary rule/setting changes without saving them."""
    _emp_or_404(body.employee_id)
    rb2 = copy.copy(rb)
    rb2.data = copy.deepcopy(rb.data)
    rb2._index = {r['id']: r for r in rb2.data['rules']}
    for rid, patch in body.rules.items():
        if rid in rb2._index:
            if 'enabled' in patch:
                rb2._index[rid]['enabled'] = bool(patch['enabled'])
            if patch.get('params'):
                rb2._index[rid].setdefault('params', {}).update(patch['params'])
            if patch.get('severity'):
                rb2._index[rid]['severity'] = patch['severity']
    rb2.data['settings'].update(body.settings)
    for tid, patch in body.tiers.items():
        for t in rb2.data['cover_tiers']:
            if t['id'] == tid:
                t.update(patch)
    eng2 = Engine(store, rb2, db)
    base = engine.evaluate(body.employee_id, body.leave_type, body.start, body.end, body.note)
    sim = eng2.evaluate(body.employee_id, body.leave_type, body.start, body.end, body.note)
    return {'before': Engine.to_dict(base, 'manager'), 'after': Engine.to_dict(sim, 'manager')}


@app.get('/api/audit')
def audit(limit: int = 100, who=Depends(require_role('manager', 'admin'))):
    return db.audit_log(limit)


MAX_UPLOAD = 10 * 1024 * 1024
ALLOWED_UPLOADS = ('.pdf', '.docx', '.txt', '.md')


def _check_upload(filename: str, data: bytes):
    if not data:
        return
    if len(data) > MAX_UPLOAD:
        raise HTTPException(413, 'Document too large (10 MB limit).')
    if not (filename or '').lower().endswith(ALLOWED_UPLOADS):
        raise HTTPException(400, 'Only PDF, DOCX, TXT or MD documents are accepted.')


# ----------------------------------------------------------------------------- policy-verified change proposals (super user)
def _target_snapshot(target_type: str, target_id: str) -> dict:
    if target_type == 'rule':
        r = rb._index.get(target_id)
        if not r:
            raise HTTPException(404, 'unknown rule')
        return {**r, 'kind': 'rule', 'policy_backed': rb.is_policy_backed('rule', target_id)}
    if target_type == 'tier':
        for t in rb.all_tiers():
            if t['id'] == target_id:
                return {**t, 'kind': 'tier', 'category': 'tier', 'name': t['label'], 'source': 'ANF cl.16; WACHS Roster Guideline s2.6', 'policy_backed': False}
        raise HTTPException(404, 'unknown tier')
    if target_type == 'agreement':
        if target_id not in rb.data['agreements']:
            raise HTTPException(404, 'unknown agreement')
        return {**rb.data['agreements'][target_id], 'kind': 'agreement', 'category': 'agreement', 'name': rb.data['agreements'][target_id]['label'], 'source': 'Registered industrial agreement', 'policy_backed': True}
    if target_type == 'setting':
        if target_id not in rb.settings:
            raise HTTPException(404, 'unknown setting')
        return {'kind': 'setting', 'category': 'setting', 'name': target_id, 'current': rb.settings[target_id], 'source': 'Local configuration', 'policy_backed': False}
    raise HTTPException(400, 'target_type must be rule | tier | agreement | setting')


@app.post('/api/admin/proposals')
async def create_proposal(target_type: str = Form(...), target_id: str = Form(...), change: str = Form(...), justification: str = Form(''),
                          file: UploadFile | None = File(None), who=Depends(require_role('admin'))):
    """Step 1: propose a change. Policy-backed targets need an attached Australian policy document, which the AI reviews.
    System targets (settings, cover options, ward requirements, rules sourced by the system) apply at once with a written reason."""
    try:
        change_d = json.loads(change)
    except Exception:
        raise HTTPException(400, 'change must be JSON')
    target = _target_snapshot(target_type, target_id)
    if target.get('locked') and change_d.get('enabled') is False:
        raise HTTPException(400, f'{target_id} is a locked governance rule and cannot be disabled')
    data = await file.read() if file is not None and file.filename else b''
    _check_upload(file.filename if file is not None else '', data)
    if not target.get('policy_backed') and not data:
        if len(justification.strip()) < 5:
            raise HTTPException(400, 'Give a short reason for this change; it is recorded in the change history.')
        review = {'verdict': 'SYSTEM', 'confidence': 1.0, 'engine': 'rulebook', 'model': '', 'characters': 0, 'australian_document': None,
                  'summary': 'System rule: no policy document is needed. Applied with the reason given and logged.', 'findings': [], 'evidence': [], 'recommendations': []}
        pid = db.create_proposal(who['user'], target_type, target_id, change_d, justification, '(system rule, no document)', None, review)
        db.audit(who['user'], 'proposal_create', None, {'proposal': pid, 'target': f'{target_type}:{target_id}', 'verdict': 'SYSTEM'})
        out = _apply_proposal_record(db.get_proposal(pid), None, who)
        return {'proposal_id': pid, 'review': review, 'target': target, 'change': change_d, 'can_apply': True, 'applied': True, 'proposal': out['proposal']}
    if not data:
        raise HTTPException(400, f'{target_id} comes from a policy or agreement ({target.get("source")}). Attach the Australian policy, award or agreement that supports the change.')
    review = policy_check.check(target, change_d, file.filename, data, justification)
    pid = db.create_proposal(who['user'], target_type, target_id, change_d, justification, file.filename, review.get('document_sha256'), review)
    db.audit(who['user'], 'proposal_create', None, {'proposal': pid, 'target': f'{target_type}:{target_id}', 'verdict': review.get('verdict')})
    return {'proposal_id': pid, 'review': review, 'target': target, 'change': change_d, 'can_apply': review.get('verdict') == 'COMPLIANT'}


class ApplyIn(BaseModel):
    override_reason: str = ''


def _apply_proposal_record(p: dict, override: str | None, who: dict) -> dict:
    pid = p['id']
    ch, tt, tid = p['change'], p['target_type'], p['target_id']
    fields = list((ch.get('params') or {}).keys()) if tt == 'rule' and ch.get('params') else [k for k in ch.keys() if k not in ('params', 'justification')]
    previous = {f: policy_check.current_value(rb, tt, tid, f if tt != 'setting' else None) for f in (fields or ['enabled'])}
    note = f'proposal {pid} ({p["document"]}) verdict={p["verdict"]}' + (f' OVERRIDE: {override}' if override else '') + (f' REASON: {p.get("justification")}' if p['verdict'] == 'SYSTEM' else '')
    if tt == 'rule':
        rb.update_rule(tid, ch.get('enabled'), ch.get('params'), ch.get('severity'), actor=who['user'])
        if ch.get('source'):
            rb._index[tid]['source'] = ch['source']; rb.save(who['user'], note)
    elif tt == 'tier':
        rb.update_tier(tid, ch.get('enabled'), ch.get('cost_multiplier'), actor=who['user'])
    elif tt == 'agreement':
        rb.update_agreement(tid, {k: v for k, v in ch.items() if k not in ('justification',)}, actor=who['user'])
    elif tt == 'setting':
        rb.update_settings({tid: ch.get('value')}, actor=who['user'])
    db.apply_proposal(pid, 'APPLIED_WITH_OVERRIDE' if override else 'APPLIED', override, previous)
    db.audit(who['user'], 'proposal_apply', None, {'proposal': pid, 'target': f'{tt}:{tid}', 'change': ch, 'previous': previous, 'override': override, 'rules_version': rb.version})
    return {'ok': True, 'proposal': db.get_proposal(pid)}


@app.post('/api/admin/proposals/{pid}/apply')
def apply_proposal(pid: str, body: ApplyIn, who=Depends(require_role('admin'))):
    """Step 2: apply. Compliant (or system) proposals apply directly; others need a written override reason (logged)."""
    p = db.get_proposal(pid)
    if not p:
        raise HTTPException(404)
    if p['status'] != 'REVIEWED':
        raise HTTPException(400, f'proposal is {p["status"]}')
    override = None
    if p['verdict'] not in ('COMPLIANT', 'SYSTEM'):
        if len(body.override_reason.strip()) < 10:
            raise HTTPException(400, 'The AI review did not find this change compliant. To apply it anyway, give a written reason (at least 10 characters). This is recorded in the audit log.')
        override = body.override_reason.strip()
    return _apply_proposal_record(p, override, who)


_discoveries: dict = {}


@app.post('/api/admin/discover')
async def discover(file: UploadFile = File(...), who=Depends(require_role('admin'))):
    """Upload a policy; the AI reads it and lists the rulebook values it would change, with evidence."""
    data = await file.read()
    if not data:
        raise HTTPException(400, 'empty document')
    _check_upload(file.filename, data)
    out = policy_check.discover(rb, file.filename, data, overrides=db.active_overrides())
    tok = secrets.token_urlsafe(12)
    _discoveries[tok] = {'filename': file.filename, 'data': data, 'findings': out.get('findings', []), 'summary': out.get('document_summary'), 'engine': out.get('engine')}
    db.audit(who['user'], 'discover', None, {'document': file.filename, 'findings': len(out.get('findings', []))})
    return {'token': tok, **out}


class ApplyFindingsIn(BaseModel):
    token: str
    indices: list[int]


@app.post('/api/admin/discover/apply')
def discover_apply(body: ApplyFindingsIn, who=Depends(require_role('admin'))):
    """Turn the selected findings into policy-backed proposals (the uploaded document is the evidence) and apply them."""
    d = _discoveries.get(body.token)
    if not d:
        raise HTTPException(404, 'upload the document again')
    applied, errors, unchanged = [], [], []
    for i in body.indices:
        if i < 0 or i >= len(d['findings']):
            continue
        f = d['findings'][i]; tt, tid = f['target_type'], f['target_id']
        if policy_check._same(policy_check.current_value(rb, tt, tid, f.get('field')), f.get('proposed')):
            unchanged.append(f'{tt}:{tid} {f.get("field") or ""}'.strip()); continue
        try:
            if tt == 'rule':
                change = {'params': {f['field']: f['proposed']}} if f.get('field') and f['field'] not in ('enabled', 'severity') else ({'enabled': bool(f['proposed'])} if f.get('field') == 'enabled' else {'severity': str(f['proposed'])})
            elif tt == 'agreement':
                change = {f['field']: f['proposed']}
            elif tt == 'tier':
                change = {f.get('field', 'enabled'): f['proposed']}
            else:
                change = {'value': f['proposed']}
            review = {'verdict': 'COMPLIANT', 'confidence': float(f.get('confidence', 0.6)), 'engine': d['engine'], 'summary': f.get('rationale', ''), 'findings': [f.get('rationale', '')], 'evidence': [f.get('evidence', '')], 'recommendations': [], 'australian_document': True, 'document': d['filename'], 'source': 'discovery'}
            just = f.get('rationale', 'found in uploaded document')
            if f.get('override'):
                just = f"Reverses manual override ({f['override'].get('reason')}) using the uploaded document. " + just
            prev = {f.get('field') or 'enabled': policy_check.current_value(rb, tt, tid, f.get('field') if tt != 'setting' else None)}
            pid = db.create_proposal(who['user'], tt, tid, change, just, d['filename'], hashlib.sha256(d['data']).hexdigest()[:16], review)
            if tt == 'rule':
                rb.update_rule(tid, change.get('enabled'), change.get('params'), change.get('severity'), actor=who['user'])
            elif tt == 'tier':
                rb.update_tier(tid, change.get('enabled'), change.get('cost_multiplier'), actor=who['user'])
            elif tt == 'agreement':
                rb.update_agreement(tid, change, actor=who['user'])
            else:
                rb.update_settings({tid: change['value']}, actor=who['user'])
            db.apply_proposal(pid, 'APPLIED', None, prev)
            db.audit(who['user'], 'proposal_apply', None, {'proposal': pid, 'target': f'{tt}:{tid}', 'change': change, 'previous': prev, 'via': 'discovery', 'document': d['filename']})
            applied.append({'proposal_id': pid, 'target': f'{tt}:{tid}', 'change': change})
        except Exception as ex:
            errors.append({'index': i, 'error': str(ex)})
    return {'applied': applied, 'errors': errors, 'unchanged': unchanged, 'rules_version': rb.version}


@app.post('/api/admin/proposals/{pid}/reject')
def reject_proposal(pid: str, who=Depends(require_role('admin'))):
    db.apply_proposal(pid, 'REJECTED')
    db.audit(who['user'], 'proposal_reject', None, {'proposal': pid})
    return {'ok': True}


@app.get('/api/admin/proposals')
def list_proposals(who=Depends(require_role('admin', 'manager'))):
    return db.list_proposals()


# ----------------------------------------------------------------------------- ward simulator (existing people, existing shifts, hypothetical dates)
class SimAbsence(BaseModel):
    employee_id: str
    start: dt.date
    end: dt.date
    leave_type: str = 'ANNUAL'


class WardSimIn(BaseModel):
    unit: str
    start: dt.date
    end: dt.date
    absences: list[SimAbsence] = []


@app.get('/api/manager/units/{unit}/staff')
def unit_staff(unit: str, who=Depends(require_role('manager', 'admin'))):
    out = []
    for role, members in store.unit_staff.get(unit, {}).items():
        for eid in members:
            e = store.employees[eid]
            out.append({'employee_id': eid, 'position': e.position_name, 'role_class': e.role_class, 'job_type': e.job_type, 'contract_hours': e.contract_hours,
                        'shifts_next_28_days': len([x for x in store.shifts_between(eid, rb.today(), rb.today() + dt.timedelta(days=28)) if x.unit == unit])})
    out.sort(key=lambda x: (-x['shifts_next_28_days'], x['role_class'], x['employee_id']))
    return out


@app.post('/api/manager/simulate-ward')
def simulate_ward(body: WardSimIn, who=Depends(require_role('manager', 'admin'))):
    """What-if for a ward: add hypothetical absences for real staff, see the coverage grid and each absence's decision. Nothing is saved."""
    key = 'sim:' + secrets.token_hex(4)
    decisions, applied = [], []
    before = coverage(body.unit, body.start, body.end)
    before_short = {(d['date'], c['shift'], c['role']) for d in before['days'] for c in d['cells'] if c['effective'] < c['required']}
    before_eff = {(d['date'], c['shift'], c['role']): c['effective'] for d in before['days'] for c in d['cells']}
    try:
        for a in body.absences:
            if a.employee_id not in store.employees:
                continue
            dec = engine.evaluate(a.employee_id, a.leave_type, a.start, a.end, 'simulation', with_alternatives=False)
            mv = Engine.to_dict(dec, 'manager')
            decisions.append({'employee_id': a.employee_id, 'position': store.employees[a.employee_id].position_name, 'start': a.start.isoformat(), 'end': a.end.isoformat(),
                              'outcome': dec.outcome, 'headline': dec.headline, 'breached_codes': dec.breached_codes, 'summary': mv['summary'], 'cost_estimate': mv.get('cost_estimate', 0),
                              'cover': [{'date': c['date'], 'category': c['category'], 'status': c['status'], 'candidate': (c['candidate'] or {}).get('employee_id'), 'tier': (c['candidate'] or {}).get('tier')} for c in mv.get('cover', [])]})
            _apply_overlay(key + a.employee_id, a.employee_id, a.start, a.end, mv.get('cover', []))
            applied.append((a.employee_id, mv.get('cover', [])))
        grid = coverage(body.unit, body.start, body.end)
        newly, short = [], 0
        for d in grid['days']:
            for c in d['cells']:
                k = (d['date'], c['shift'], c['role'])
                c['before'] = before_eff.get(k, c['effective'])
                c['newly_short'] = c['effective'] < c['required'] and k not in before_short
                short += c['effective'] < c['required']
                if c['newly_short']:
                    newly.append({'date': d['date'], 'shift': c['shift'], 'role': c['role'], 'before': c['before'], 'after': c['effective'], 'required': c['required']})
        return {'unit': body.unit, 'decisions': decisions, 'coverage': grid, 'cells_below_requirement': short, 'cells_below_before': len(before_short),
                'cells_newly_short': len(newly), 'newly_short': newly, 'roster_completeness': round(sum(store.roster_completeness(body.unit, dt.date.fromisoformat(d['date'])) for d in grid['days']) / max(1, len(grid['days'])), 2),
                'total_cover_cost_hours': round(sum(d['cost_estimate'] or 0 for d in decisions), 1), 'note': 'Simulation only: nothing was saved.'}
    finally:
        for eid, cov in applied:
            _remove_overlay(key + eid, eid, cov)


# ----------------------------------------------------------------------------- cover handshake: the proposed colleague accepts or declines
@app.get('/api/me/{eid}/cover')
def my_cover(eid: str, who=Depends(require_user)):
    _own(who, eid)
    _emp_or_404(eid)
    return [a for a in db.assignments_for(eid) if a['status'] in ('proposed', 'confirmed', 'accepted', 'declined_by_staff') and a.get('request_status') not in ('WITHDRAWN', 'DECLINED_BY_MANAGER')]


class CoverRespondIn(BaseModel):
    accept: bool
    note: str = ''


@app.post('/api/cover/{aid}/respond')
def cover_respond(aid: str, body: CoverRespondIn, who=Depends(require_user)):
    a = db.get_assignment(aid)
    if not a:
        raise HTTPException(404)
    _own(who, a['employee_id'])
    if body.accept:
        db.respond_assignment(aid, 'accepted')
        store.cover_confirmed.add((a['employee_id'], dt.date.fromisoformat(a['date'])))
        db.audit(a['employee_id'], 'cover_accept', a['request_id'], {'assignment': aid})
        return {'ok': True, 'status': 'accepted'}
    # declined: mark, block this colleague for that date, and re-plan the request with the live engine
    db.respond_assignment(aid, 'declined_by_staff')
    store.cover_refused.add((a['employee_id'], dt.date.fromisoformat(a['date'])))
    _refused_by_request.setdefault(a['request_id'], set()).add((a['employee_id'], dt.date.fromisoformat(a['date'])))
    db.audit(a['employee_id'], 'cover_decline', a['request_id'], {'assignment': aid, 'note': body.note})
    r = db.get_request(a['request_id'])
    if r and r['status'] in ACTIVE:
        _remove_overlay(r['id'], r['employee_id'], (r.get('decision') or {}).get('cover', []))
        dec = engine.evaluate(r['employee_id'], r['leave_type'], dt.date.fromisoformat(r['start']), dt.date.fromisoformat(r['end']), r['note'] or '')
        mview = Engine.to_dict(dec, 'manager')
        # keep accepted colleagues on their shifts
        kept = {(x['date'], x['employee_id']) for x in db.all_assignments() if x['request_id'] == r['id'] and x['status'] == 'accepted'}
        db.update_request(r['id'], decision=mview, outcome=dec.outcome)
        db.save_assignments(r['id'], mview.get('cover', []), status='proposed')
        for x in db.all_assignments():
            if x['request_id'] == r['id'] and (x['date'], x['employee_id']) in kept:
                db.respond_assignment(x['id'], 'accepted')
        _apply_overlay(r['id'], r['employee_id'], r['start'], r['end'], mview.get('cover', []))
        db.notify('manager', store.employees[r['employee_id']].primary_unit or '', f'Cover declined: {a["employee_id"]} on {a["date"]}', f'Plan for {r["id"]} was re-run. {mview["summary"]["uncovered"]} shift(s) now without cover.', r['id'])
    return {'ok': True, 'status': 'declined_by_staff'}


@app.get('/api/admin/quality')
def quality(who=Depends(require_role('manager', 'admin'))):
    out = {}
    for name in ('calibration_report.json', 'benchmark_results.json'):
        pth = os.path.join(ROOT, 'data', name)
        if os.path.exists(pth):
            out[name.split('_')[0]] = json.load(open(pth))
    tests = os.path.join(ROOT, 'tests')
    out['tests'] = {f: sum(1 for l in open(os.path.join(tests, f), encoding='utf-8') if l.startswith('def test_')) for f in os.listdir(tests) if f.startswith('test_')}
    out['rules'] = len(rb.rules()); out['rules_version'] = rb.version
    out['data'] = {'employees': len(store.employees), 'units': len(store.units), 'shifts': sum(len(v) for v in store.shifts_by_emp.values()), 'sick_rate_overall': round(store.sick_rate_overall, 4)}
    return out


# ----------------------------------------------------------------------------- email: decisions and cover requests
def _app_url(request) -> str:
    env = os.environ.get('APP_URL')
    if env:
        return env.rstrip('/')
    host = request.headers.get('x-forwarded-host') or request.headers.get('host') or '127.0.0.1:8000'
    proto = request.headers.get('x-forwarded-proto') or ('https' if not host.startswith(('127.', 'localhost')) else 'http')
    return f'{proto}://{host}'


def _email_text(kind: str, r: dict, a: dict | None, url: str) -> tuple[str, str]:
    lt = (rb.leave_type(r['leave_type']) or {}).get('label', r['leave_type'])
    if kind == 'cover':
        d = dt.date.fromisoformat(a['date'])
        subject = f"Shift-H: can you cover {SHIFT_WORDS.get(a['category'], a['category'])} on {d:%a %d %b} ({a['unit']})?"
        body = (f"Hello,\n\nYou have been proposed to cover a shift for a colleague's {lt.lower()}:\n\n"
                f"  Date:   {d:%A %d %B %Y}\n  Shift:  {SHIFT_WORDS.get(a['category'], a['category'])} ({a['hours']} h)\n  Ward:   {a['unit']}\n  Basis:  {TIER_WORDS.get(a['tier'], a['tier'])}\n\n"
                f"Please accept or decline in Shift-H under Cover requests: {url}/\n\n"
                f"Reference {r['id']}. This shift was checked against your rest, hours and fatigue rules before it was proposed.\n")
        return subject, body
    status_words = {'APPROVED': 'approved', 'DECLINED_BY_MANAGER': 'not approved', 'CHANGES_REQUESTED': 'returned with a request for changes', 'ACCEPTED': 'recorded', 'PENDING_MANAGER': 'received and waiting for a decision', 'ESCALATED': 'escalated for review'}
    word = status_words.get(r['status'], r['status'].lower().replace('_', ' '))
    subject = f"Shift-H: your {lt.lower()} {r['start']} to {r['end']} is {word}"
    body = (f"Hello,\n\nYour leave request {r['id']} ({lt.lower()}, {dt.date.fromisoformat(r['start']):%A %d %B %Y} to {dt.date.fromisoformat(r['end']):%A %d %B %Y}) is {word}.\n")
    if r.get('manager_note'):
        body += f"\nManager's note: {r['manager_note']}\n"
    body += f"\nSee the details and your requests in Shift-H: {url}/\n"
    return subject, body


SHIFT_WORDS = {'AM': 'morning shift', 'PM': 'afternoon shift', 'NIGHT': 'night shift', 'LONG_DAY': '12-hour day', 'DAY': 'day shift', 'ONCALL_24H': 'on call'}
TIER_WORDS = {'ordinary': 'ordinary hours', 'redeploy': 'moved from another ward', 'casual': 'casual pool', 'overtime': 'overtime (manager authorised)', 'agency': 'agency'}


class EmailIn(BaseModel):
    kind: str = 'decision'          # decision | cover
    to: str = ''                    # optional: address entered in the modal
    assignment_id: str = ''         # for kind=cover
    remember: bool = True           # store the entered address for next time


@app.post('/api/manager/requests/{rid}/email')
def email_request(rid: str, body: EmailIn, request: Request, who=Depends(require_role('manager', 'admin'))):
    """Email the decision to the staff member, or a cover request to the proposed colleague. If no address is known and none
    is given, returns needs_address so the UI can ask for one. Always records the message in the outbox."""
    r = db.get_request(rid)
    if not r:
        raise HTTPException(404)
    a = None
    if body.kind == 'cover':
        a = db.get_assignment(body.assignment_id)
        if not a or a['request_id'] != rid:
            raise HTTPException(404, 'cover line not found')
        recipient = a['employee_id']
    else:
        recipient = r['employee_id']
    to = (body.to or '').strip() or db.get_contact(recipient) or ''
    if not to:
        return {'needs_address': True, 'recipient': recipient, 'has_contact': False}
    if '@' not in to or ' ' in to:
        raise HTTPException(400, 'That does not look like an email address.')
    if body.to and body.remember:
        db.set_contact(recipient, to)
    subject, text = _email_text(body.kind, r, a, _app_url(request))
    res = mailer.send(to, subject, text)
    status = 'sent' if res['sent'] else ('outbox' if res['error'] == 'not configured' else 'failed')
    db.log_email(to, recipient, subject, text, rid, body.kind, status, res['error'])
    db.audit(who['user'], 'email', rid, {'kind': body.kind, 'to': to, 'status': status})
    return {'needs_address': False, 'to': to, 'status': status, 'configured': mailer.configured(), 'error': res['error'] if status == 'failed' else '',
            'hint': '' if res['sent'] else mailer.provider_hint(), 'mailto': mailer.mailto(to, subject, text), 'subject': subject}


@app.get('/api/manager/emails')
def emails_outbox(who=Depends(require_role('manager', 'admin'))):
    return {'configured': mailer.configured(), 'emails': db.emails()}


class ContactIn(BaseModel):
    email: str = Field(max_length=120)


@app.get('/api/me/{eid}/contact')
def get_contact(eid: str, who=Depends(require_user)):
    _own(who, eid); _emp_or_404(eid)
    return {'employee_id': eid, 'email': db.get_contact(eid)}


@app.put('/api/me/{eid}/contact')
def put_contact(eid: str, body: ContactIn, who=Depends(require_user)):
    _own(who, eid); _emp_or_404(eid)
    e = body.email.strip()
    if e and ('@' not in e or ' ' in e):
        raise HTTPException(400, 'That does not look like an email address.')
    db.set_contact(eid, e)
    db.audit(who['user'], 'contact', None, {'employee_id': eid, 'set': bool(e)})
    return {'employee_id': eid, 'email': e}


# ----------------------------------------------------------------------------- staff roster page
@app.get('/api/me/{eid}/roster')
def my_roster(eid: str, start: dt.date, end: dt.date, who=Depends(require_user)):
    """Day-by-day roster for the staff member: rostered shifts, cover shifts accepted through the app, leave, public
    holidays, and whether the roster is published for that day."""
    _own(who, eid); e = _emp_or_404(eid)
    if (end - start).days > 92:
        raise HTTPException(400, 'at most 93 days at a time')
    mine = {a['date']: a for a in db.assignments_for(eid) if a['status'] in ('accepted', 'confirmed', 'proposed') and a.get('request_status') not in ('WITHDRAWN', 'DECLINED_BY_MANAGER')}
    days = []
    d = start
    while d <= end:
        shifts = [{'unit': s.unit, 'category': s.category, 'start': f'{s.start_min//60:02d}:{s.start_min%60:02d}', 'end': f'{s.end_min//60:02d}:{s.end_min%60:02d}', 'hours': s.paid_hours, 'kind': 'rostered'}
                  for s in store.shift_dates_by_emp.get(eid, {}).get(d, ())]
        cov = mine.get(d.isoformat())
        if cov:
            shifts.append({'unit': cov['unit'], 'category': cov['category'], 'start': '', 'end': '', 'hours': cov['hours'], 'kind': 'cover', 'status': cov['status'], 'request_id': cov['request_id']})
        fam = store.leave_family_by_day.get(eid, {}).get(d)
        dyn = bool(getattr(store, 'dyn_absent', {}).get(eid, {}).get(d))
        days.append({'date': d.isoformat(), 'weekday': d.strftime('%a'), 'published': store.in_window(eid, d), 'public_holiday': store.holidays.get(d),
                     'shifts': shifts, 'leave': (fam.replace('_', ' ').title() if fam else ('Requested leave' if dyn else None)), 'today': d == rb.today()})
        d += dt.timedelta(days=1)
    return {'employee_id': eid, 'unit': e.primary_unit, 'window': [str(e.window_start), str(e.window_end)] if e.window_start else None, 'days': days,
            'hours': round(sum(s['hours'] for x in days for s in x['shifts']), 1)}


# ----------------------------------------------------------------------------- policy reference for a rule code
OFFICIAL = [
    (['MP 0100/18', 'Accrued Leave', 'ELMP'], 'Management of Accrued Leave Policy MP 0100/18 (WA Health)', 'https://www.health.wa.gov.au/About-us/Policy-frameworks/Workforce-and-Employment/Mandatory-requirements/Industrial-Relations/Management-of-Accrued-Leave-Policy'),
    (['MP 0187/24', 'Ratio', 'Non-Compliance Form', 'NHpPD'], 'Nurse/Midwife to Patient Ratios Policy MP 0187/24 (WA Health)', 'https://www.health.wa.gov.au/About-us/Policy-frameworks/Clinical-Services-and-Planning/Mandatory-requirements/Nurse-Midwife-to-Patient-Ratios-Policy'),
    (['MP 0193/25', 'AI Standard', 'State Records'], 'Artificial Intelligence Policy MP 0193/25 and AI Standard (WA Health)', 'https://www.health.wa.gov.au/About-us/Policy-frameworks/Digital-Health/Mandatory-requirements/Artificial-Intelligence-Policy'),
    (['ANF'], 'WA Health System – ANF – Registered Nurses, Midwives, Enrolled (Mental Health) Nurses Industrial Agreement 2024', 'https://www.health.wa.gov.au/~/media/Corp/Documents/Health-for/Industrial-relations/Awards-and-agreements/Nurses-Registered-and-Enrolled-Mental-Health/WA-Health-ANF-Agreement-2024.pdf'),
    (['HSUWA', 'HSU'], 'WA Health System – HSUWA – PACTS Industrial Agreement 2024', 'https://www.health.wa.gov.au/~/media/Corp/Documents/Health-for/Industrial-relations/Awards-and-agreements/Salaried-officers/WA-HEALTH-SYSTEM---HSUWA---PACTS-INDUSTRIAL-AGREEMENT-2024.pdf'),
    (['AMA'], 'WA Health System – Medical Practitioners – AMA Industrial Agreement 2024', 'https://www.health.wa.gov.au/~/media/Corp/Documents/Health-for/Industrial-relations/Awards-and-agreements/Doctors/Medical-practitioners-AMA-industrial-agreement-2024.pdf'),
    (['UWU'], 'WA Health System – United Workers Union – Enrolled Nurses and AINs Industrial Agreement 2024 (Awards and Agreements library)', 'https://www.health.wa.gov.au/articles/a_e/awards-and-agreements'),
    (['WACHS Roster', 'WACHS Fatigue', 'Roster Guideline'], 'WACHS Nursing and Midwifery Roster Guideline', 'https://www.wacountry.health.wa.gov.au/~/media/WACHS/Documents/About-us/Policies/Nursing-and-Midwifery-Roster-Guideline.pdf'),
    (['Equal Opportunity'], 'Equal Opportunity Act 1984 (WA) and MP 0118/19', 'https://www.health.wa.gov.au/About-us/Policy-frameworks/Workforce-and-Employment'),
    (['public holiday'], 'WA public holidays', 'https://www.wa.gov.au/service/employment/workplace-arrangements/public-holidays-western-australia'),
    (['Purchased Leave'], 'Purchased Leave Guidelines (WA Health)', 'https://www.health.wa.gov.au/~/media/Corp/Documents/Health-for/Industrial-relations/Fact-sheets/Purchased-Leave-Guidelines.pdf'),
]
RESEARCH_DIR = os.path.join(ROOT, 'data', 'research')
_research_cache: dict = {}


def _research_paragraphs():
    if _research_cache:
        return _research_cache['p']
    out = []
    if os.path.isdir(RESEARCH_DIR):
        for fn in sorted(os.listdir(RESEARCH_DIR)):
            if not fn.endswith('.md'):
                continue
            label = fn.replace('research_', '').replace('.md', '').replace('_', ' ')
            txt = open(os.path.join(RESEARCH_DIR, fn), encoding='utf-8', errors='ignore').read()
            for para in re.split(r'\n\s*\n', txt):
                para = para.strip()
                if 60 < len(para) < 1800 and not para.startswith('|--'):
                    out.append((label, para))
    _research_cache['p'] = out
    return out


@app.get('/api/policy/{code}')
def policy_reference(code: str):
    code = code.upper()
    r = rb._index.get(code)
    if not r:
        raise HTTPException(404, 'unknown rule code')
    source = r.get('source', '') or ''
    # tokens worth searching for: clause numbers, policy ids, document names
    tokens = set(re.findall(r'\b(?:cl\.?\s?\d+(?:\(\d+\))?(?:\([a-z]\))?|s\.?\d+(?:\.\d+)*|MP \d{4}/\d{2}|\d{2}\(\d{1,2}\))', source, re.I))
    words = [w for w in re.findall(r'[A-Za-z][A-Za-z/\-]{3,}', source) if w.lower() not in ('policy', 'agreement', 'guideline', 'health', 'system', 'clause')]
    tokens |= set(words[:6])
    # clause tokens like "28(12)" also match "cl 28(12)" / "cl.28(12)"; policy ids like "MP 0100/18"
    clause_tokens = {re.sub(r'^(cl\.?\s?|s\.?)', '', t, flags=re.I).lower() for t in tokens if any(ch.isdigit() for ch in t)}
    name_words = {w.lower() for w in re.findall(r'[A-Za-z]{5,}', r['name'])} - {'shift', 'shifts', 'leave', 'must', 'every', 'their'}
    scored = []
    for label, para in _research_paragraphs():
        if para.lstrip().startswith('#') or para.count('http') > 2 or para.lstrip().startswith('|--'):
            continue
        low = para.lower()
        sc = sum(4 for t in clause_tokens if len(t) >= 3 and t in low)
        sc += sum(1 for w in words if w.lower() in low)
        sc += sum(1 for w in name_words if w in low)
        if code in para:
            sc += 3
        if sc >= 2:
            scored.append((sc, label, para))
    scored.sort(key=lambda x: -x[0])
    strong = [x for x in scored if x[0] >= 3]
    chosen = strong[:6] if len(strong) >= 3 else scored[:6]
    passages = [{'source': l, 'text': t.replace('|', ' · ').strip(' ·')} for _, l, t in chosen]
    official = [{'title': t, 'url': u} for keys, t, u in OFFICIAL if any(k.lower() in source.lower() for k in keys)]
    ag = None
    if r.get('category') == 'fatigue' or code.startswith(('NOT', 'BAL-002')):
        ag = {k: {kk: vv for kk, vv in v.items() if kk in ('label', 'min_break_hours', 'night_day_changeover_hours', 'max_consecutive_duties', 'max_duties_fortnight', 'max_consecutive_nights', 'max_nights_fortnight', 'min_days_off_week', 'max_shift_hours', 'annual_notice_days', 'lsl_notice_days_employee', 'response_days', 'annual_leave_hours_year')} for k, v in rb.data['agreements'].items()}
    return {'code': code, 'name': r['name'], 'category': r.get('category'), 'severity': r.get('severity'), 'enabled': r.get('enabled', True), 'locked': r.get('locked', False),
            'description': r.get('description', ''), 'public': r.get('public', ''), 'manager': r.get('manager', ''), 'params': r.get('params') or {}, 'source': source,
            'official': official, 'passages': passages, 'agreements': ag, 'rules_version': rb.version}


# ----------------------------------------------------------------------------- chat
@app.post('/api/chat')
def chat(body: ChatIn, request: Request, who=Depends(require_user)):
    _own(who, body.employee_id)
    _chat_limit(who, request)
    _emp_or_404(body.employee_id)
    db.chat_log(body.employee_id, body.session_id, 'user', body.message)
    out = chatmod.handle(body.employee_id, body.session_id, body.message)
    db.chat_log(body.employee_id, body.session_id, 'ai', out.get('reply', ''), out.get('decision'))
    return out


@app.post('/api/chat/stream')
def chat_stream(body: ChatIn, request: Request, who=Depends(require_user)):
    _own(who, body.employee_id)
    _chat_limit(who, request)
    """Same as /api/chat but streams progress lines (server-sent events) while the assistant works. Every exchange is kept
    in the staff member's own conversation history."""
    _emp_or_404(body.employee_id)
    db.chat_log(body.employee_id, body.session_id, 'user', body.message)

    def gen():
        for chunk in chatmod.handle_stream(body.employee_id, body.session_id, body.message):
            if chunk.startswith('event: result'):
                try:
                    data = json.loads(chunk.split('data: ', 1)[1].strip())
                    db.chat_log(body.employee_id, body.session_id, 'ai', data.get('reply', ''), data.get('decision'))
                except Exception:
                    pass
            yield chunk
    return StreamingResponse(gen(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


class ChatContextIn(BaseModel):
    employee_id: str = Field(max_length=32)
    session_id: str = Field(max_length=64, pattern=r'^[A-Za-z0-9_.:-]+$')
    decision: dict


@app.post('/api/chat/context')
def chat_context(body: ChatContextIn, who=Depends(require_user)):
    _own(who, body.employee_id)
    """Hand a form check to the assistant: the conversation starts with that check as memory."""
    _emp_or_404(body.employee_id)
    d = body.decision
    if d.get('employee_id') != body.employee_id:
        raise HTTPException(400, 'context belongs to another staff member')
    text = chatmod.seed_context(body.employee_id, body.session_id, d)
    lt = (rb.leave_type(d.get('leave_type', '')) or {}).get('label', d.get('leave_type', ''))
    db.chat_log(body.employee_id, body.session_id, 'user', f"From the request form: {lt} {d.get('start')} to {d.get('end')} ({LABEL_OUT.get(d.get('outcome'), d.get('outcome'))}).")
    intro = 'I have the details of that check. Ask me why, ask for other dates or cover options' + (', or tap Submit / say submit to send it.' if not d.get('request_id') else f", or say escalate or withdraw for {d['request_id']}.")
    db.chat_log(body.employee_id, body.session_id, 'ai', intro, d)
    return {'ok': True, 'intro': intro, 'context': text}


class ChatLogIn(BaseModel):
    employee_id: str = Field(max_length=32)
    session_id: str = Field(max_length=64, pattern=r'^[A-Za-z0-9_.:-]+$')
    who: str = 'ai'
    text: str = Field(default='', max_length=8000)
    card: dict | None = None


@app.post('/api/chat/log')
def chat_log(body: ChatLogIn, who=Depends(require_user)):
    _own(who, body.employee_id)
    """Record something that happened on a card (e.g. a submission) in the conversation history."""
    _emp_or_404(body.employee_id)
    db.chat_log(body.employee_id, body.session_id, body.who if body.who in ('ai', 'user') else 'ai', body.text, body.card)
    return {'ok': True}


@app.get('/api/me/{eid}/conversations')
def my_conversations(eid: str, who=Depends(require_user)):
    _own(who, eid)
    _emp_or_404(eid)
    return db.conversations(eid)


@app.get('/api/me/{eid}/conversations/{cid}')
def my_conversation(eid: str, cid: str, who=Depends(require_user)):
    _own(who, eid)
    _emp_or_404(eid)
    return db.conversation(eid, cid)


@app.delete('/api/me/{eid}/conversations/{cid}')
def delete_conversation(eid: str, cid: str, who=Depends(require_user)):
    _own(who, eid)
    _emp_or_404(eid)
    db.delete_conversation(eid, cid)
    return {'ok': True}


# ----------------------------------------------------------------------------- UI
if os.path.isdir(UI_DIR):
    app.mount('/static', StaticFiles(directory=UI_DIR), name='static')

    @app.get('/')
    def index():
        return FileResponse(os.path.join(UI_DIR, 'index.html'))

    @app.get('/manager')
    def manager_page():
        return FileResponse(os.path.join(UI_DIR, 'manager.html'))

    @app.get('/rules')
    def rules_page():
        from fastapi.responses import RedirectResponse
        return RedirectResponse('/manager#rules')

    @app.get('/policy/{code}')
    def policy_page(code: str):
        return FileResponse(os.path.join(UI_DIR, 'policy.html'))
