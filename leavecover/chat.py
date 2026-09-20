"""Chat agent. Uses Google Gemini with function calling when GEMINI_API_KEY is set;
otherwise a deterministic fallback that understands dates and leave types.

The model never decides anything: every check, submission or escalation goes through
the same rule engine as the linear form, and colleague identities are never exposed."""
import datetime as dt, os, re, json, traceback

import queue, threading, time

_ctx: dict = {}
_sessions: dict = {}
_client = None   # one Gemini client for the process (closing it mid-session breaks chats)
_progress: dict = {}   # (employee_id, session_id) -> queue.Queue of status strings shown to the user while working
_current: dict = {}    # thread id -> progress key (so tools know which session they run for)


def _say(text: str):
    """Report progress for the session whose request is running on this thread (or its worker threads)."""
    k = _current.get(threading.get_ident()) or _current.get('*')
    q = _progress.get(k) if k else None
    if q is not None:
        q.put(text)

SYSTEM = """You are Shift-H, the leave and cover assistant for WA Health staff. One staff member is signed in (fixed employee id). Today is {today}.
Always use the tools for balances, checks, submissions, requests, escalations and best-time questions. Never guess.
STYLE, strictly: answer first, in one line. Then at most 3 short bullets with the facts that matter (dates, hours, rule code and what it means). Under 60 words unless listing options. No greetings, no repetition, no "I can help with". Use **bold** for dates and hours. Never reveal or guess colleagues' details.
- Sick or carer's leave: call check_leave (assume today if no dates are given; use the number of days if stated). One line: what will be recorded, that the manager is notified with cover recommendations, and "Tap Record on the card". Do not submit it yourself. Mention a certificate only if the tool says so.
- Missing leave type or dates: ask for it in one short sentence.
- Declined: give the rule code and meaning, the alternative dates from the tool, and offer escalation in one line.
- Balance too low (balance_options): list up to 4 options in bullets: when it will be enough, shorter block, other leave type, half pay / leave without pay; end with "Say send to manager to submit anyway."
- Public holidays: the calendar is Western Australia's only; never ask which state. "When is X" or "is X a holiday": call get_public_holidays and answer with the exact date and weekday (and the substitute day if any) in one line; do not search leave windows unless they ask for time off.
- Leave around a holiday ("King's Birthday long weekend", "Anzac Day week"): call resolve_period (it returns holiday_date), then check_leave for the exact days asked or find_best_leave_windows with anchor_date = holiday_date. If those exact days cannot be approved, say so plainly with the rule, then give the closest windows that pass. If the length or which side of the holiday is unclear, ask_followup with options like "Friday before", "Tuesday after", "the whole week".
- Named periods ("Christmas", "Easter", "school holidays", "December", "next month"): call resolve_period first, then find_best_leave_windows with from_date/to_date/anchor_date from it. Present the top 2-3 windows inside that period with their dates and status. Never answer a Christmas question with October dates.
- If the length, leave type or which side of a holiday is unclear, call ask_followup with 2-4 short options (e.g. "2 weeks including Christmas Day", "2 weeks starting after Boxing Day", "3 weeks") and keep the reply to one sentence; the options are shown as cards.
- "More", "other", "different" or "next" options after a best-time answer: call find_best_leave_windows again with the SAME length and period and skip = shown_so_far from the previous result; present only the new windows. If more_available is false or the result has a note, say there are no other windows that pass and offer a different length or period.
- Withdraw or cancel: call withdraw_request (with the request id if the staff member named one, else empty). If it returns choose_one_of, the options are shown as cards; ask in one line which one. Approved leave cannot be withdrawn: say the manager has to cancel it.
- Best-time questions: call find_best_leave_windows with the length asked. Only windows_that_pass are recommendations. If it is empty, say plainly that no single block of that length can be approved, name the blocking_rules with their meaning, and present the split_plan blocks (dates, block length) as the recommendation. Say if the balance is short.
- After a check, end with one line: "Tap Submit on the card, or say submit." Never call submit_leave unless the latest message explicitly confirms it (submit, yes, book it, send it)."""

LEAVE_WORDS = {
    'annual': 'ANNUAL', 'holiday': 'ANNUAL', 'vacation': 'ANNUAL', 'al ': 'ANNUAL', 'recreation': 'ANNUAL',
    'long service': 'LONG_SERVICE', 'lsl': 'LONG_SERVICE',
    'sick': 'SICK', 'unwell': 'SICK', 'ill': 'SICK', 'flu': 'SICK', 'personal leave': 'SICK',
    'carer': 'CARER', 'child is sick': 'CARER', 'kid is sick': 'CARER',
    'compassionate': 'COMPASSIONATE', 'bereavement': 'COMPASSIONATE', 'funeral': 'COMPASSIONATE',
    'professional development': 'PROF_DEV', 'pdl': 'PROF_DEV', 'conference': 'STUDY', 'study': 'STUDY', 'exam': 'STUDY',
    'ado': 'ADO', 'accrued day': 'ADO', 'toil': 'TOIL', 'time in lieu': 'TOIL', 'purchased': 'PURCHASED',
    'parental': 'PARENTAL', 'maternity': 'PARENTAL', 'paternity': 'PARENTAL', 'cultural': 'CULTURAL', 'ceremonial': 'CULTURAL',
    'unpaid': 'UNPAID', 'without pay': 'UNPAID', 'lwop': 'UNPAID',
}
MONTHS = {m: i for i, m in enumerate(['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'], 1)}


def bind(**kw):
    _ctx.update(kw)


_seed: dict = {}   # (employee_id, session_id) -> context text handed over from the request form


def context_summary(d: dict) -> str:
    """Plain-text memory of a form check, small enough to seed a model session."""
    rb = _ctx['rb']
    lt = (rb.leave_type(d.get('leave_type', '')) or {}).get('label', d.get('leave_type', ''))
    lines = [f"Context from the request form: {lt} from {d.get('start')} to {d.get('end')} ({d.get('requested_hours')} h). Outcome: {d.get('outcome')} - {d.get('headline')}"]
    if d.get('request_id'):
        lines.append(f"Submitted as {d['request_id']} (status {d.get('status')}).")
    else:
        lines.append('Not submitted yet.')
    if d.get('balance_after') is not None:
        lines.append(f"Balance after this request: {d['balance_after']} h.")
    for r in (d.get('rules') or []):
        if not r.get('passed') and r.get('public'):
            lines.append(f"Rule {r['code']} ({r.get('severity')}): {r['public']}")
    sm = d.get('summary') or {}
    if sm:
        lines.append(f"Shifts: {sm.get('shifts', 0)} rostered, {sm.get('not_needed', 0)} need no cover, {sm.get('covered', 0)} covered, {sm.get('uncovered', 0)} without cover.")
    for day in (d.get('chart') or {}).get('days', []):
        for sh in day.get('shifts', []):
            if sh.get('status') == 'uncovered':
                why = sh.get('why') or {}
                parts = [f"{v} {k}" for k, v in why.items() if k != 'considered']
                lines.append(f"No cover on {day['date']} {sh.get('shift')} {sh.get('unit')}: ward at {sh.get('remaining')} of {sh.get('required')} required; {why.get('considered', 0)} colleagues checked: {'; '.join(parts) or 'nobody suitable'}.")
    for a in (d.get('alternatives') or [])[:3]:
        lines.append(f"Alternative {a['start']} to {a['end']}: {a.get('label')}; {'; '.join(a.get('why') or [])}")
    bo = d.get('balance_options')
    if bo:
        lines.append('Balance options: ' + '; '.join(o.get('title', '') for o in bo.get('options', [])[:5]))
    return '\n'.join(lines)


def seed_context(employee_id: str, session_id: str, decision: dict) -> str:
    """Remember a form check for this conversation: the model session starts with it, and the built-in assistant treats
    the dates as pending (say submit) or as the last request (escalate / withdraw)."""
    text = context_summary(decision)
    _seed[(employee_id, session_id)] = text
    _sessions.pop((employee_id, session_id), None)
    st = _sessions.setdefault(('fb', employee_id, session_id), {'type': None, 'dates': [], 'last_request': None, 'pending': None, 'period': None})
    st['type'] = decision.get('leave_type')
    if decision.get('request_id'):
        st['last_request'] = decision['request_id']; st['pending'] = None
    else:
        st['pending'] = {'type': decision.get('leave_type'), 'start': decision.get('start'), 'end': decision.get('end'), 'note': ''}
    _last[employee_id] = decision
    return text


def gemini_available() -> bool:
    return bool(os.environ.get('GEMINI_API_KEY')) and not os.environ.get('LEAVECOVER_NO_GEMINI')


# ----------------------------------------------------------------------------- tools (shared by Gemini and fallback)
_last: dict = {}   # (employee_id) -> last decision dict produced by a tool (for the UI card)


def _tools_for(employee_id: str):
    engine, store, rb, db, submit_fn, CheckIn = _ctx['engine'], _ctx['store'], _ctx['rb'], _ctx['db'], _ctx['submit_fn'], _ctx['CheckIn']

    def get_my_leave_summary() -> dict:
        """Get the staff member's balances (hours), upcoming leave, next shifts and available leave types."""
        _say('Reading your leave balances and roster')
        s = store.employee_summary(employee_id, rb.today())
        s['requests'] = [{k: r[k] for k in ('id', 'leave_type', 'start', 'end', 'outcome', 'status')} for r in db.list_requests(employee_id=employee_id, limit=10)]
        s.pop('next_shifts', None)
        s['leave_types'] = [k for k, v in rb.leave_types().items() if (store.employees[employee_id].job_type != 'CASUAL' or v.get('casual_allowed'))]
        return s

    def check_leave(leave_type: str, start_date: str, end_date: str, note: str = '') -> dict:
        """Check (without submitting) whether leave of type leave_type (e.g. ANNUAL, SICK, CARER, LONG_SERVICE, PROF_DEV, ADO, TOIL, UNPAID) from start_date to end_date (YYYY-MM-DD) can be approved. Returns outcome, headline, anonymised reasons with rule codes, and alternative dates."""
        _say(f'Checking {leave_type.replace("_", " ").lower()} {start_date} to {end_date} against the rules')
        dec = engine.evaluate(employee_id, leave_type, dt.date.fromisoformat(start_date), dt.date.fromisoformat(end_date), note)
        d = engine.to_dict(dec, 'staff')
        _say(f"Checked {d['summary']['shifts']} rostered shifts; searching for cover and alternative dates done")
        _last[employee_id] = d
        return {k: d.get(k) for k in ('outcome', 'headline', 'requested_hours', 'projected_balance', 'breached_codes', 'public_reasons', 'alternatives', 'summary', 'can_escalate', 'balance_options')}

    def submit_leave(leave_type: str, start_date: str, end_date: str, note: str = '') -> dict:
        """Submit the leave request for real. Call ONLY after check_leave has been shown for the same dates and the staff member has confirmed in their latest message (e.g. "submit", "yes", "book it", "send it"). This includes sick and carer's leave. Returns request_id, outcome and status."""
        _say('Submitting your request and notifying your manager')
        d = submit_fn(CheckIn(employee_id=employee_id, leave_type=leave_type, start=dt.date.fromisoformat(start_date), end=dt.date.fromisoformat(end_date), note=note, channel='chat'))
        _last[employee_id] = d
        return {k: d[k] for k in ('request_id', 'status', 'outcome', 'headline', 'requested_hours', 'projected_balance', 'breached_codes', 'public_reasons', 'alternatives', 'summary', 'can_escalate')}

    def list_my_requests() -> list:
        """List the staff member's existing leave requests with their status."""
        return [{k: r[k] for k in ('id', 'leave_type', 'start', 'end', 'outcome', 'status', 'manager_note')} for r in db.list_requests(employee_id=employee_id, limit=10)]

    def escalate_request(request_id: str, message: str) -> dict:
        """Ask a manager to review a declined or pending request (human review, GOV-001)."""
        r = db.get_request(request_id)
        if not r or r['employee_id'] != employee_id:
            return {'error': 'request not found'}
        if r['status'] not in ('DECLINED', 'PENDING_MANAGER'):
            return {'error': f'request is {r["status"]} and cannot be escalated'}
        db.update_request(request_id, status='ESCALATED', manager_note=f'Escalated by staff member: {message}')
        db.audit(employee_id, 'escalate', request_id, {'message': message, 'channel': 'chat'})
        emp = store.employees[employee_id]
        db.notify('manager', emp.primary_unit or '', f'Escalation: {employee_id}', message, request_id)
        return {'request_id': request_id, 'status': 'ESCALATED'}

    def resolve_period(phrase: str, length_days: int = 14) -> dict:
        """Turn a vague period the person mentioned ("Christmas", "before Christmas", "New Year", "Easter", "school holidays", "December", "next month", "summer") into a concrete date range to search, with the anchor date (e.g. 25 December) and public-holiday notes. Returns null if the phrase is not a recognised period."""
        from . import periods
        r = periods.resolve(phrase, rb.today(), int(length_days), store.holidays)
        if not r:
            return {'recognised': False, 'phrase': phrase}
        return {'recognised': True, 'label': r['label'], 'range_start': r['range_start'].isoformat(), 'range_end': r['range_end'].isoformat(), 'anchor': r['anchor'].isoformat() if r['anchor'] else None, 'note': r['note'], 'holiday_date': r.get('holiday'), 'substitute_date': r.get('substitute')}

    def get_public_holidays(year: int = 0, name: str = '') -> dict:
        """The official Western Australia public-holiday calendar held by the system (source: wa.gov.au). Use it for any question like "when is the King's Birthday", "is Anzac Day a holiday", "what holidays are in December". Returns exact dates with weekdays; filter by year and/or a name fragment. Never guess holiday dates."""
        today = rb.today()
        rows = [{'date': d.isoformat(), 'weekday': d.strftime('%A'), 'name': n, 'passed': d < today} for d, n in sorted(store.holidays.items())
                if (not year or d.year == int(year)) and (not name or name.lower().replace("'", '') in n.lower().replace("'", ''))]
        return {'state': 'Western Australia', 'today': today.isoformat(), 'holidays': rows}

    def ask_followup(question: str, options: list[str]) -> dict:
        """Ask the person a short follow-up question with 2-4 clickable options (each option is a short phrase they could type, e.g. "Include Christmas Day", "Start after Boxing Day", "2 weeks", "3 weeks"). Use it when dates, length or leave type are missing or ambiguous instead of guessing. Returns the options as shown."""
        opts = [str(o) for o in (options or [])][:4]
        _last[employee_id] = {'kind': 'choices', 'question': question, 'options': opts}
        return {'shown': True, 'question': question, 'options': opts}

    def find_best_leave_windows(length_days: int = 7, leave_type: str = 'ANNUAL', weeks_ahead: int = 16, from_date: str = '', to_date: str = '', anchor_date: str = '', skip: int = 0) -> dict:
        """Recommend the best windows to take leave of length_days for this staff member. Without from_date/to_date it scans the coming weeks from today; with from_date and to_date (YYYY-MM-DD, e.g. from resolve_period) it scans every possible block inside that period and prefers blocks containing anchor_date. Every window is checked by the full rule engine. Returns the next 3 windows_that_pass, shown_so_far, more_available, blocking_rules and a split_plan when no single block passes. skip = how many windows were already shown for this same question; when the staff member asks for more, other or different options, pass skip = shown_so_far from the previous result."""
        from .suggestions import staff_suggestions
        length_days = max(1, min(int(length_days), 182))
        rs = dt.date.fromisoformat(from_date) if from_date else None; re_ = dt.date.fromisoformat(to_date) if to_date else None
        an = dt.date.fromisoformat(anchor_date) if anchor_date else None
        lt = leave_type if rb.leave_type(leave_type) else 'ANNUAL'
        # paging memory: a repeat of the same question with "more/other/next" moves to the next page even if skip was not passed
        hist = _sessions.setdefault(('bw', employee_id), {})
        sig = (length_days, lt, from_date, to_date)
        skip = max(0, int(skip or 0))
        msg = _sessions.get(('msg', employee_id), '') or ''
        if skip == 0 and hist.get('sig') == sig and re.search(r'\b(more|other|another|else|different|next|further|additional|alternatives?)\b', msg.lower()):
            skip = int(hist.get('shown', 0))
        if skip > 0 and not rs:
            weeks_ahead = max(int(weeks_ahead), 16 + 8 * (skip // 3))   # look further ahead for later pages
        _say(f'Checking every {length_days}-day block between {from_date} and {to_date}' if rs else f'Scoring the next {int(weeks_ahead)} weeks for a {length_days}-day break (ward capacity, cover, notice)')
        out = staff_suggestions(store, rb, engine, employee_id, int(weeks_ahead), length_days, lt, rs, re_, an)
        bc = rb.leave_type(lt).get('balance_code')
        bc = bc if isinstance(bc, str) else (bc or ['AL'])[0]
        al = store.balances.get(employee_id, {}).get(bc or 'AL', {})
        allw = out['windows']
        seen = list(hist.get('seen', [])) if (hist.get('sig') == sig and skip) else []
        fresh = [w for w in allw if w['start'] not in seen]
        page = fresh[:3] if not seen else sorted(fresh[:3], key=lambda w: w['start'])   # first page best-first, later pages in date order
        shown = len(seen) + len(page); total = len(seen) + len(fresh); more = len(fresh) > len(page)
        hist.update({'sig': sig, 'shown': shown, 'total': total, 'seen': seen + [w['start'] for w in page]})
        note = ''
        if not page and skip:
            note = f'No further {length_days}-day windows pass the rules in this period beyond the {skip} already shown. Offer a different length or period.'
        _last[employee_id] = {'kind': 'windows', 'windows': page, 'split_plan': out.get('split_plan') if not skip else None, 'blocking_rules': out.get('blocking_rules'), 'length_days': length_days, 'leave_type': lt,
                              'page': {'from': len(seen) + 1, 'to': shown, 'total': total}, 'more': more}
        return {'length_days': length_days, 'leave_type': lt, 'balance_hours': round(al.get('remaining', 0), 1), 'windows_that_pass': page, 'shown_so_far': shown, 'total_passing': total, 'more_available': more,
                'declined_windows': out.get('declined_windows', 0), 'blocking_rules': out.get('blocking_rules', []), 'split_plan': out.get('split_plan') if not skip else None, 'nudges': out['nudges'], 'note': note}

    def withdraw_request(request_id: str = '') -> dict:
        """Withdraw one of the staff member's own leave requests while it is still waiting for the manager (status PENDING_MANAGER, ESCALATED or CHANGES_REQUESTED) or recorded but not yet approved. Approved leave cannot be withdrawn here. With an empty request_id, withdraws the most recent request that is still waiting; if several are waiting, returns them so the staff member can choose. Any cover proposed for it is released."""
        _say('Checking which of your requests can still be withdrawn')
        mine = [r for r in db.list_requests(employee_id=employee_id, limit=20) if r['status'] in ('PENDING_MANAGER', 'ESCALATED', 'CHANGES_REQUESTED', 'DECLINED', 'ACCEPTED')]
        if request_id:
            r = db.get_request(request_id)
            if not r or r['employee_id'] != employee_id:
                return {'error': 'request not found'}
            if r['status'] == 'APPROVED':
                return {'error': 'already approved: only the manager can cancel approved leave', 'status': r['status']}
            if r['status'] not in ('PENDING_MANAGER', 'ESCALATED', 'CHANGES_REQUESTED', 'DECLINED', 'ACCEPTED'):
                return {'error': f"request is {r['status']} and cannot be withdrawn", 'status': r['status']}
        else:
            if not mine:
                return {'error': 'no request is waiting for a decision'}
            if len(mine) > 1:
                opts = [f"Withdraw {r['id']} ({r['leave_type'].lower().replace('_', ' ')} {r['start']} to {r['end']})" for r in mine[:4]]
                _last[employee_id] = {'kind': 'choices', 'question': 'Which request do you want to withdraw?', 'options': opts}
                return {'choose_one_of': [{'request_id': r['id'], 'leave_type': r['leave_type'], 'start': r['start'], 'end': r['end'], 'status': r['status']} for r in mine[:4]]}
            r = mine[0]
        _say(f"Withdrawing {r['id']} and releasing its cover")
        _ctx['withdraw_fn'](r['id'])
        out = {'request_id': r['id'], 'leave_type': r['leave_type'], 'start': r['start'], 'end': r['end'], 'previous_status': r['status'], 'status': 'WITHDRAWN'}
        _last[employee_id] = {'kind': 'withdrawn', **out}
        return out

    return [get_my_leave_summary, check_leave, submit_leave, list_my_requests, escalate_request, withdraw_request, find_best_leave_windows, resolve_period, get_public_holidays, ask_followup]


# ----------------------------------------------------------------------------- Gemini path
def _models() -> list:
    """Models to try in order. The free tier has a small daily quota per model, so we fall through on 429."""
    env = os.environ.get('GEMINI_MODELS') or os.environ.get('GEMINI_MODEL') or ''
    lst = [m.strip() for m in env.split(',') if m.strip()]
    for m in ('gemini-3.8-flash', 'gemini-3.5-flash-lite', 'gemini-3.5-flash', 'gemini-2.5-flash', 'gemini-flash-lite-latest'):
        if m not in lst:
            lst.append(m)
    return lst


_model_state: dict = {'index': 0}


def _gemini_reply(employee_id: str, session_id: str, message: str) -> dict:
    from google import genai
    from google.genai import types
    from google.genai import errors as gerrors
    key = (employee_id, session_id)
    _sessions[('msg', employee_id)] = message
    global _client
    if _client is None:
        _client = genai.Client(api_key=os.environ['GEMINI_API_KEY'])
    models = _models()
    tools = _tools_for(employee_id)
    sysi = SYSTEM.format(today=_ctx['rb'].today().isoformat())

    def cfg_for(m):
        # thinking adds ~10 s per reply on the flash models; lite models do not accept the flag
        think = None if ('lite' in m or os.environ.get('GEMINI_THINKING', '0') != '0') else types.ThinkingConfig(thinking_budget=0)
        return types.GenerateContentConfig(system_instruction=sysi, tools=tools, temperature=0.2, thinking_config=think, http_options=types.HttpOptions(timeout=45000))
    resp = None; model = None
    for attempt in range(len(models)):
        model = models[(_model_state['index'] + attempt) % len(models)]
        sess = _sessions.get(key)
        if sess is None or sess._model != model:
            history = sess.get_history() if sess is not None else None
            if history is None and _seed.get(key):
                history = [types.Content(role='user', parts=[types.Part(text=_seed[key] + '\n(The staff member opened this conversation from the request form; answer follow-up questions about it.)')]),
                           types.Content(role='model', parts=[types.Part(text='Understood. I have the details of that check and will answer questions about it.')])]
            sess = _client.chats.create(model=model, config=cfg_for(model), history=history)
            _sessions[key] = sess
        try:
            _say(f'Asking {model}')
            resp = sess.send_message(message)
            _model_state['index'] = (_model_state['index'] + attempt) % len(models)
            break
        except gerrors.APIError as ex:
            _say(f'{model} is busy, trying the next model')
            if getattr(ex, 'code', None) in (429, 404, 503, 400) or any(k in str(ex) for k in ('RESOURCE_EXHAUSTED', 'NOT_FOUND', 'UNAVAILABLE', 'INVALID_ARGUMENT')):
                continue   # quota used up, model retired, overloaded or rejects the config: try the next one
            raise
    if resp is None:
        raise RuntimeError('all Gemini models are over quota')
    chat = _sessions[key]
    calls = []
    try:
        hist = chat.get_history()
        # function calls made since the user's latest message
        idx = max(i for i, c in enumerate(hist) if c.role == 'user' and any(getattr(p, 'text', None) == message for p in (c.parts or [])))
        for c in hist[idx:]:
            for p in c.parts or []:
                if getattr(p, 'function_call', None):
                    calls.append(p.function_call.name)
    except Exception:
        pass
    out = {'reply': resp.text or '(no reply)', 'engine': 'gemini', 'model': model, 'tools_used': calls}
    dec = _last.pop(employee_id, None)
    if dec is not None:
        out['decision'] = dec
        if 'check_leave' in calls and dec.get('start'):
            # keep the built-in assistant in step so "submit" still works if Gemini runs out of quota
            fb = _sessions.setdefault(('fb', employee_id, session_id), {'type': None, 'dates': [], 'last_request': None, 'pending': None})
            fb['pending'] = {'type': dec.get('leave_type'), 'start': dec['start'], 'end': dec['end'], 'note': ''}
        if dec.get('request_id'):
            fb = _sessions.setdefault(('fb', employee_id, session_id), {'type': None, 'dates': [], 'last_request': None, 'pending': None})
            fb['last_request'] = dec['request_id']; fb['pending'] = None
    return out


# ----------------------------------------------------------------------------- fallback path (no API key)
def _parse_dates(text: str, today: dt.date):
    t = text.lower()
    iso = re.findall(r'(\d{4}-\d{2}-\d{2})', t)
    dates = [dt.date.fromisoformat(x) for x in iso]
    m = re.search(r'(\d{1,2})(?:st|nd|rd|th)?\s*(?:to|-|–|until|till|and)\s*(\d{1,2})(?:st|nd|rd|th)?\s*(?:of\s*)?([a-z]{3,9})\.?(?:\s*(\d{4}))?', t)
    if m and MONTHS.get(m.group(3)[:3]):
        mon = MONTHS[m.group(3)[:3]]; yr = int(m.group(4)) if m.group(4) else (today.year if mon >= today.month else today.year + 1)
        try:
            return sorted({dt.date(yr, mon, int(m.group(1))), dt.date(yr, mon, int(m.group(2)))})
        except ValueError:
            pass
    for m in re.finditer(r'(\d{1,2})(?:st|nd|rd|th)?\s*(?:of\s*)?([a-z]{3,9})\.?(?:\s*(\d{4}))?', t):
        mon = MONTHS.get(m.group(2)[:3])
        if mon:
            yr = int(m.group(3)) if m.group(3) else (today.year if mon >= today.month else today.year + 1)
            try:
                dates.append(dt.date(yr, mon, int(m.group(1))))
            except ValueError:
                pass
    for m in re.finditer(r'(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?', t):
        try:
            yr = int(m.group(3)) if m.group(3) else today.year
            yr = yr + 2000 if yr < 100 else yr
            dates.append(dt.date(yr, int(m.group(2)), int(m.group(1))))
        except ValueError:
            pass
    if 'today' in t:
        dates.append(today)
    if 'tomorrow' in t:
        dates.append(today + dt.timedelta(days=1))
    m = re.search(r'next (\w+)', t)
    if m and m.group(1)[:3] in ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'):
        wd = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'].index(m.group(1)[:3]) + 1
        d = today + dt.timedelta(days=1)
        while d.isoweekday() != wd:
            d += dt.timedelta(days=1)
        dates.append(d)
    dates = sorted(set(dates))
    m = re.search(r'(\d+)\s*(day|week)s?', t)
    if len(dates) == 1 and m:
        n = int(m.group(1)) * (7 if m.group(2) == 'week' else 1)
        dates.append(dates[0] + dt.timedelta(days=n - 1))
    if len(dates) == 1 and ('for a day' in t or 'one day' in t):
        dates.append(dates[0])
    return dates


def _parse_type(text: str):
    t = ' ' + text.lower() + ' '
    for k, v in LEAVE_WORDS.items():
        if k in t:
            return v
    return None


def _fmt_reasons(d: dict) -> str:
    lines = []
    for r in d.get('public_reasons', [])[:5]:
        lines.append('- ' + r)
    if d.get('alternatives'):
        lines.append('Dates that would work:')
        for a in d['alternatives']:
            lines.append(f"- {a['start']} to {a['end']} ({a['label']})")
    return '\n'.join(lines)


def _fallback_reply(employee_id: str, session_id: str, message: str) -> dict:
    tools = {f.__name__: f for f in _tools_for(employee_id)}
    rb = _ctx['rb']; store = _ctx['store']; today = rb.today()
    st = _sessions.setdefault(('fb', employee_id, session_id), {'type': None, 'dates': [], 'last_request': None, 'pending': None, 'period': None})
    t = message.lower().strip()
    _sessions[('msg', employee_id)] = message
    used = []
    # follow-ups about a check handed over from the form
    seed = _seed.get((employee_id, session_id))
    if seed and re.search(r'\b(why|reason|explain|what does|cover|alternative|other dates|options?)\b', t) and not _parse_dates(t, today):
        lines = [l for l in seed.split('\n')[1:] if l]
        if re.search(r'alternative|other dates|options?', t):
            lines = [l for l in lines if l.startswith(('Alternative', 'Balance options'))] or ['No alternative dates were found for that check.']
        elif re.search(r'cover', t):
            lines = [l for l in lines if l.startswith(('No cover', 'Shifts'))] or ['Every shift in that check either needs no cover or has cover found.']
        else:
            lines = [l for l in lines if l.startswith(('Rule', 'No cover', 'Balance'))] or ['Every rule passed for that check.']
        return {'reply': '\n'.join('- ' + l for l in lines[:8]), 'engine': 'fallback', 'tools_used': []}
    # confirmations
    if st.get('pending') and re.search(r'\b(yes|yep|submit|confirm|go ahead|please do|book it)\b', t):
        p = st['pending']; st['pending'] = None
        d = tools['submit_leave'](p['type'], p['start'], p['end'], p.get('note', '')); used.append('submit_leave')
        st['last_request'] = d['request_id']
        return {'reply': f"Submitted ({d['request_id']}). {d['headline']}\n{_fmt_reasons(d)}", 'engine': 'fallback', 'tools_used': used, 'decision': _last.pop(employee_id, d)}
    if re.search(r'\b(withdraw|cancel|take back|retract)\b', t):
        m = re.search(r'\bLR-[0-9A-F]{8}\b', message.upper())
        r = tools['withdraw_request'](m.group(0) if m else ''); used.append('withdraw_request')
        if r.get('error'):
            return {'reply': r['error'][0].upper() + r['error'][1:] + '.', 'engine': 'fallback', 'tools_used': used}
        if r.get('choose_one_of'):
            return {'reply': 'Which one do you want to withdraw?', 'engine': 'fallback', 'tools_used': used, 'decision': _last.pop(employee_id, None)}
        st['last_request'] = None
        return {'reply': f"Withdrawn: {r['request_id']} ({r['leave_type'].lower().replace('_', ' ')} {r['start']} to {r['end']}). Any cover proposed for it has been released and your manager's inbox is updated.", 'engine': 'fallback', 'tools_used': used, 'decision': _last.pop(employee_id, None)}
    if st.get('last_request') and re.search(r'\b(escalat|human review|speak to (a|my) manager|ask (a|my) manager)', t):
        d = tools['escalate_request'](st['last_request'], message); used.append('escalate_request')
        return {'reply': f"Done. Request {st['last_request']} has been sent to your manager for a human review. They will respond within 14 days.", 'engine': 'fallback', 'tools_used': used}
    from . import periods
    nh = periods.named_holiday(t, today, store.holidays)
    if nh and (re.search(r'\b(when|what (day|date)|date of|is .* (a )?(public )?holiday)\b', t) or not re.search(r'\b(leave|off|holiday(s)? (from|for)|week|weekend|days?)\b', t)):
        name, d0, sub = nh
        used.append('get_public_holidays')
        line = f"{name} is **{d0:%A %d %B %Y}** in Western Australia" + (f"; the substitute public holiday is **{sub:%A %d %B}**" if sub else '') + '.'
        if d0.weekday() == 0:
            line += ' It makes a long weekend (Saturday to Monday). Public holidays are not deducted from your leave.'
        others = sorted(d for d, n in store.holidays.items() if n == name and d > d0)
        if others:
            line += f" The following year it is {others[0]:%A %d %B %Y}."
        line += ' Want leave around it? Tell me the dates, e.g. "annual leave Friday before".'
        return {'reply': line, 'engine': 'fallback', 'tools_used': used}
    per = periods.resolve(t, today, periods.length_from_text(t, 0) or 14, store.holidays)
    if per and not _parse_dates(t, today):
        n = periods.length_from_text(t, 0)
        if not n:
            _last_q = {'kind': 'choices', 'question': f"How long a break around {per['label']}?", 'options': ['1 week', '2 weeks', '3 weeks', '4 weeks']}
            st['type'] = _parse_type(t) or st['type'] or 'ANNUAL'; st['period'] = t
            return {'reply': f"Happy to check {per['label']}. How long a break?", 'engine': 'fallback', 'tools_used': ['resolve_period', 'ask_followup'], 'decision': _last_q}
        lt = _parse_type(t) or st.get('type') or 'ANNUAL'
        r = tools['find_best_leave_windows'](n, lt, 16, per['range_start'].isoformat(), per['range_end'].isoformat(), per['anchor'].isoformat() if per['anchor'] else ''); used += ['resolve_period', 'find_best_leave_windows']
        if r['windows_that_pass']:
            lines = [f"Best {n}-day blocks for {per['label']} (balance {r['balance_hours']} h):"]
            for w in r['windows_that_pass'][:3]:
                lines.append(f"- **{w['start']} to {w['end']}**: {w['label']}, about {w['hours']} h{' (includes ' + per['label'].split()[0] + ' Day)' if w.get('includes_anchor') else ''}")
        else:
            lines = [f"No {n}-day block inside {per['label']} can be approved."] + [f"- {b['code']}: {b['meaning']}" for b in r['blocking_rules'][:3]]
            if r.get('split_plan'):
                lines.append(f"Instead: {len(r['split_plan']['blocks'])} blocks of {r['split_plan']['block_days']} days, starting {r['split_plan']['blocks'][0]['start']}.")
        if per['note']:
            lines.append(f"Note: {per['note']}.")
        lines.append('Tell me which dates you want and I will check them.')
        return {'reply': '\n'.join(lines), 'engine': 'fallback', 'tools_used': used, 'decision': _last.pop(employee_id, None)}
    # a bare length after a period question ("2 weeks")
    if st.get('period') and re.fullmatch(r'\s*(\d+|a|one|two|three|four)\s*(day|week|month|fortnight)s?\s*', t):
        return _fallback_reply(employee_id, session_id, f"{st['period']} {t}")
    hist = _sessions.get(('bw', employee_id)) or {}
    if hist.get('sig') and re.search(r'\b(more|other|another|else|different|next|further|additional|alternatives?)\b', t) and not _parse_dates(t, today) and len(t) < 80:
        n, lt, fd, td = hist['sig']
        r = tools['find_best_leave_windows'](n, lt, 16, fd, td, '', hist.get('shown', 0)); used.append('find_best_leave_windows')
        if r['windows_that_pass']:
            lines = [f"More {n}-day windows that pass (windows {r['shown_so_far'] - len(r['windows_that_pass']) + 1}-{r['shown_so_far']} of {r['total_passing']}):"]
            for w in r['windows_that_pass']:
                lines.append(f"- **{w['start']} to {w['end']}**: {w['label']}, about {w['hours']} h")
            lines.append('Say "more" again for the next ones, or tell me which dates you want.' if r['more_available'] else 'Those are all the windows that pass in this period. Tell me which dates you want, or ask for a different length.')
        else:
            lines = [f"No other {n}-day windows pass the rules in this period beyond the {hist.get('shown', 0)} already shown. Try a different length (e.g. a shorter block) or a different period."]
        return {'reply': '\n'.join(lines), 'engine': 'fallback', 'tools_used': used, 'decision': _last.pop(employee_id, None)}
    if re.search(r'\b(best time|when (is|would be|should)|good time|plan(ning)? to take|fit in)\b', t):
        m = re.search(r'(\d+)\s*(day|week|month)s?', t)
        n = int(m.group(1)) * {'day': 1, 'week': 7, 'month': 30}[m.group(2)] if m else 7
        lt = _parse_type(t) or 'ANNUAL'
        r = tools['find_best_leave_windows'](n, lt, 16); used.append('find_best_leave_windows')
        if r['windows_that_pass']:
            lines = [f"Best windows for {n} days of {rb.leave_type(lt)['label'].lower()} (balance {r['balance_hours']} h):"]
            for w in r['windows_that_pass'][:3]:
                lines.append(f"- **{w['start']} to {w['end']}**: {w['label']}, about {w['hours']} h")
        else:
            lines = [f"No single {n}-day block can be approved in the next few months."]
            for b in r['blocking_rules'][:3]:
                lines.append(f"- {b['code']}: {b['meaning']}")
            sp = r.get('split_plan')
            if sp:
                lines.append(f"Recommended instead, {len(sp['blocks'])} blocks of {sp['block_days']} days:")
                for b in sp['blocks']:
                    lines.append(f"- **{b['start']} to {b['end']}**: {b['label']}")
        lines.append(('Say "more" for other windows, or tell' if r.get('more_available') else 'Tell') + ' me which dates you want and I will check them.')
        return {'reply': '\n'.join(lines), 'engine': 'fallback', 'tools_used': used, 'decision': _last.pop(employee_id, None)}
    if re.search(r'\b(balance|how much leave|how many (hours|days)|entitle)', t):
        s = tools['get_my_leave_summary'](); used.append('get_my_leave_summary')
        b = s['balances']
        parts = [f"{k}: {v['remaining_hours']} h" for k, v in b.items()]
        return {'reply': 'Your balances: ' + ', '.join(parts) + f". You are {s['position']} on {s['primary_unit']} ({s['contract_hours']} h a fortnight). What dates and leave type would you like to check?", 'engine': 'fallback', 'tools_used': used}
    if re.search(r'\b(my requests|status of|what did i (submit|request)|show (my )?requests)', t):
        rs = tools['list_my_requests'](); used.append('list_my_requests')
        if not rs:
            return {'reply': 'You have no requests yet. Tell me the dates and the leave type and I will check them.', 'engine': 'fallback', 'tools_used': used}
        return {'reply': 'Your requests:\n' + '\n'.join(f"- {r['id']}: {r['leave_type']} {r['start']} to {r['end']} - {r['status']}" for r in rs), 'engine': 'fallback', 'tools_used': used}
    lt = _parse_type(t) or st['type']
    dates = _parse_dates(t, today) or st['dates']
    st['type'] = lt; st['dates'] = dates
    if not lt and not dates:
        return {'reply': "Hi! I can check and book leave for you. Tell me the leave type (annual, sick, carer's, long service, professional development...) and the dates, e.g. 'annual leave 12 to 18 October'.", 'engine': 'fallback', 'tools_used': used}
    if not lt:
        return {'reply': f"Got the dates ({dates[0]}{' to ' + str(dates[-1]) if len(dates) > 1 else ''}). Which leave type is it - annual, sick, carer's, long service, professional development, ADO or unpaid?", 'engine': 'fallback', 'tools_used': used}
    if not dates:
        return {'reply': f"{rb.leave_type(lt)['label']} - which dates? For example '12 to 18 October' or '2026-10-12 to 2026-10-18'.", 'engine': 'fallback', 'tools_used': used}
    start, end = dates[0], dates[-1]
    urgent = rb.leave_type(lt).get('urgent')
    if urgent:
        d = tools['check_leave'](lt, start.isoformat(), end.isoformat(), message); used.append('check_leave')
        st['pending'] = {'type': lt, 'start': start.isoformat(), 'end': end.isoformat(), 'note': message}
        days = (end - start).days + 1
        return {'reply': f"Sorry to hear it. Ready to record {rb.leave_type(lt)['label'].lower()} for {start}{' to ' + str(end) if days > 1 else ''} ({d['requested_hours']} h). Your manager is notified with cover recommendations. Tap Record on the card, or say submit.\n{_fmt_reasons(d)}", 'engine': 'fallback', 'tools_used': used, 'decision': _last.pop(employee_id, d)}
    d = tools['check_leave'](lt, start.isoformat(), end.isoformat(), message); used.append('check_leave')
    st['pending'] = {'type': lt, 'start': start.isoformat(), 'end': end.isoformat(), 'note': message}
    tail = "\nSay 'submit' to send this request, or give me other dates." if d['outcome'] != 'DECLINED' else "\nYou can pick one of the alternative dates, or say 'escalate' after submitting to ask a manager to review."
    return {'reply': f"{rb.leave_type(lt)['label']} {start} to {end}: {d['headline']} (uses about {d['requested_hours']} h; balance at start {d['projected_balance']} h)\n{_fmt_reasons(d)}{tail}", 'engine': 'fallback', 'tools_used': used, 'decision': _last.pop(employee_id, d)}


def handle_stream(employee_id: str, session_id: str, message: str):
    """Generator of server-sent events: progress lines while working, then the final result."""
    key = (employee_id, session_id)
    q = queue.Queue(); _progress[key] = q
    result = {}

    def work():
        _current[threading.get_ident()] = key; _current['*'] = key
        try:
            result.update(handle(employee_id, session_id, message))
        except Exception as ex:
            traceback.print_exc()
            result.update({'reply': 'Sorry, something went wrong on our side. Please try again.', 'engine': 'error'})
        finally:
            q.put(None)
    t0 = time.time()
    threading.Thread(target=work, daemon=True).start()
    yield 'event: status\ndata: ' + json.dumps({'text': 'Understanding your message'}) + '\n\n'
    while True:
        try:
            item = q.get(timeout=1.0)
        except queue.Empty:
            yield ': keep-alive\n\n'
            continue
        if item is None:
            break
        yield 'event: status\ndata: ' + json.dumps({'text': item}) + '\n\n'
    result['seconds'] = round(time.time() - t0, 1)
    _progress.pop(key, None); _current.pop('*', None)
    yield 'event: result\ndata: ' + json.dumps(result, default=str) + '\n\n'


def handle(employee_id: str, session_id: str, message: str) -> dict:
    if gemini_available():
        try:
            return _gemini_reply(employee_id, session_id, message)
        except Exception as ex:  # fall back gracefully (quota, network, model name)
            traceback.print_exc()
            out = _fallback_reply(employee_id, session_id, message)
            out['warning'] = 'Gemini quota used up for today; the built-in assistant answered.' if '429' in str(ex) or 'quota' in str(ex).lower() else f'Gemini unavailable ({type(ex).__name__}); the built-in assistant answered.'
            return out
    return _fallback_reply(employee_id, session_id, message)
