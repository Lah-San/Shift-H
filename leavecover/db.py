"""SQLite persistence for requests, cover assignments and the audit log (GOV-003)."""
from __future__ import annotations
import datetime as dt, json, os, sqlite3, threading, uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.environ.get('LEAVECOVER_DB') or os.path.join(ROOT, 'data', 'leavecover.db')   # tests point this at a scratch file


class RequestDB:
    def __init__(self, path: str = DEFAULT_DB):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.path = path
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init()

    def _init(self):
        c = self.conn
        c.executescript('''
        CREATE TABLE IF NOT EXISTS requests (
            id TEXT PRIMARY KEY, employee_id TEXT, leave_type TEXT, start TEXT, end TEXT, note TEXT,
            outcome TEXT, status TEXT, decision TEXT, created TEXT, updated TEXT, manager_note TEXT, channel TEXT);
        CREATE TABLE IF NOT EXISTS assignments (
            id TEXT PRIMARY KEY, request_id TEXT, employee_id TEXT, date TEXT, unit TEXT, category TEXT, hours REAL, tier TEXT, status TEXT, created TEXT);
        CREATE TABLE IF NOT EXISTS audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, actor TEXT, action TEXT, request_id TEXT, payload TEXT);
        CREATE TABLE IF NOT EXISTS proposals (
            id TEXT PRIMARY KEY, ts TEXT, actor TEXT, target_type TEXT, target_id TEXT, change TEXT, justification TEXT,
            document TEXT, document_sha TEXT, verdict TEXT, confidence REAL, review TEXT, status TEXT, applied_ts TEXT, override_reason TEXT);
        CREATE TABLE IF NOT EXISTS notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, audience TEXT, unit TEXT, title TEXT, body TEXT, request_id TEXT, read INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS contacts (
            employee_id TEXT PRIMARY KEY, email TEXT, updated TEXT);
        CREATE TABLE IF NOT EXISTS emails (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, to_addr TEXT, employee_id TEXT, subject TEXT, body TEXT, request_id TEXT, kind TEXT, status TEXT, error TEXT);
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, employee_id TEXT, conversation_id TEXT, ts TEXT, who TEXT, text TEXT, card TEXT);
        CREATE INDEX IF NOT EXISTS chat_conv ON chat_messages (employee_id, conversation_id);
        ''')
        c.commit()

    # ------------------------------------------------------------ requests
    def create_request(self, employee_id, leave_type, start, end, note, outcome, status, decision: dict, channel='web') -> str:
        rid = 'LR-' + uuid.uuid4().hex[:8].upper()
        now = dt.datetime.now().isoformat(timespec='seconds')
        with self._lock:
            self.conn.execute('INSERT INTO requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                              (rid, employee_id, leave_type, start, end, note or '', outcome, status, json.dumps(decision, default=str), now, now, '', channel))
            self.conn.commit()
        self.audit('system', 'evaluate', rid, {'outcome': outcome, 'status': status, 'rules_version': decision.get('rules_version')})
        return rid

    def update_request(self, rid, status=None, manager_note=None, decision=None, outcome=None):
        now = dt.datetime.now().isoformat(timespec='seconds')
        with self._lock:
            row = self.get_request(rid)
            if not row:
                return None
            dec_json = json.dumps(decision if decision is not None else row['decision'], default=str)
            self.conn.execute('UPDATE requests SET status=?, manager_note=?, decision=?, outcome=?, updated=? WHERE id=?',
                              (status or row['status'], manager_note if manager_note is not None else row['manager_note'],
                               dec_json, outcome or row['outcome'], now, rid))
            self.conn.commit()
        return self.get_request(rid)

    def get_request(self, rid):
        r = self.conn.execute('SELECT * FROM requests WHERE id=?', (rid,)).fetchone()
        return self._row(r)

    def list_requests(self, employee_id=None, status=None, limit=200):
        q, p = 'SELECT * FROM requests WHERE 1=1', []
        if employee_id:
            q += ' AND employee_id=?'; p.append(employee_id)
        if status:
            q += ' AND status IN (%s)' % ','.join('?' * len(status)); p.extend(status)
        q += ' ORDER BY created DESC LIMIT ?'; p.append(limit)
        return [self._row(r) for r in self.conn.execute(q, p).fetchall()]

    def _row(self, r):
        if r is None:
            return None
        d = dict(r)
        try:
            d['decision'] = json.loads(d['decision']) if d.get('decision') else None
        except Exception:
            pass
        return d

    # ------------------------------------------------------------ assignments
    def save_assignments(self, rid, lines: list[dict], status='proposed'):
        now = dt.datetime.now().isoformat(timespec='seconds')
        with self._lock:
            self.conn.execute('DELETE FROM assignments WHERE request_id=?', (rid,))
            for ln in lines:
                if ln.get('status') == 'covered' and ln.get('candidate'):
                    c = ln['candidate']
                    self.conn.execute('INSERT INTO assignments VALUES (?,?,?,?,?,?,?,?,?,?)',
                                      ('AS-' + uuid.uuid4().hex[:8].upper(), rid, c['employee_id'], ln['date'], ln['unit'], ln['category'], ln['hours'], c['tier'], status, now))
            self.conn.commit()

    def set_assignments_status(self, rid, status):
        with self._lock:
            self.conn.execute('UPDATE assignments SET status=? WHERE request_id=?', (status, rid))
            self.conn.commit()

    def extra_shift_counts(self, since: dt.date) -> dict[str, int]:
        rows = self.conn.execute("SELECT employee_id, COUNT(*) n FROM assignments WHERE status IN ('proposed','confirmed') AND date>=? GROUP BY employee_id", (since.isoformat(),)).fetchall()
        return {r['employee_id']: r['n'] for r in rows}

    def assignments_for(self, employee_id):
        return [dict(r) for r in self.conn.execute('SELECT a.*, r.employee_id AS for_employee, r.leave_type, r.status AS request_status FROM assignments a LEFT JOIN requests r ON r.id=a.request_id WHERE a.employee_id=? ORDER BY a.date', (employee_id,)).fetchall()]

    def get_assignment(self, aid):
        r = self.conn.execute('SELECT * FROM assignments WHERE id=?', (aid,)).fetchone()
        return dict(r) if r else None

    def respond_assignment(self, aid, status):
        with self._lock:
            self.conn.execute('UPDATE assignments SET status=? WHERE id=?', (status, aid)); self.conn.commit()

    def all_assignments(self):
        return [dict(r) for r in self.conn.execute('SELECT * FROM assignments ORDER BY date').fetchall()]

    # ------------------------------------------------------------ audit + notifications
    def audit(self, actor, action, request_id=None, payload=None):
        with self._lock:
            self.conn.execute('INSERT INTO audit (ts, actor, action, request_id, payload) VALUES (?,?,?,?,?)',
                              (dt.datetime.now().isoformat(timespec='seconds'), actor, action, request_id, json.dumps(payload or {}, default=str)))
            self.conn.commit()

    def audit_log(self, limit=200):
        return [dict(r) for r in self.conn.execute('SELECT * FROM audit ORDER BY id DESC LIMIT ?', (limit,)).fetchall()]

    # ------------------------------------------------------------ policy-verified change proposals
    def create_proposal(self, actor, target_type, target_id, change: dict, justification, document, document_sha, review: dict) -> str:
        pid = 'PC-' + uuid.uuid4().hex[:8].upper()
        with self._lock:
            self.conn.execute('INSERT INTO proposals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                              (pid, dt.datetime.now().isoformat(timespec='seconds'), actor, target_type, target_id, json.dumps(change, default=str), justification or '',
                               document, document_sha, review.get('verdict'), float(review.get('confidence') or 0), json.dumps(review, default=str), 'REVIEWED', None, None))
            self.conn.commit()
        return pid

    def get_proposal(self, pid):
        r = self.conn.execute('SELECT * FROM proposals WHERE id=?', (pid,)).fetchone()
        if not r:
            return None
        d = dict(r)
        d['change'] = json.loads(d['change']); d['review'] = json.loads(d['review'])
        return d

    def list_proposals(self, limit=100):
        out = []
        for r in self.conn.execute('SELECT * FROM proposals ORDER BY ts DESC LIMIT ?', (limit,)).fetchall():
            d = dict(r); d['change'] = json.loads(d['change']); d['review'] = json.loads(d['review']); out.append(d)
        return out

    def apply_proposal(self, pid, status='APPLIED', override_reason=None, previous=None):
        with self._lock:
            row = self.conn.execute('SELECT review FROM proposals WHERE id=?', (pid,)).fetchone()
            review = json.loads(row['review']) if row and row['review'] else {}
            review['previous'] = previous
            self.conn.execute('UPDATE proposals SET status=?, applied_ts=?, override_reason=?, review=? WHERE id=?', (status, dt.datetime.now().isoformat(timespec='seconds'), override_reason, json.dumps(review, default=str), pid))
            self.conn.commit()

    def active_overrides(self) -> list:
        """Latest applied proposal per target+field that was applied with an override (still worth re-checking against policy)."""
        out = {}
        for p in sorted(self.list_proposals(500), key=lambda x: x['applied_ts'] or ''):
            if p['status'] not in ('APPLIED', 'APPLIED_WITH_OVERRIDE'):
                continue
            ch = p['change']
            fields = list((ch.get('params') or {}).keys()) if p['target_type'] == 'rule' and ch.get('params') else [k for k in ch.keys() if k not in ('params', 'justification')]
            for f in fields or ['enabled']:
                key = (p['target_type'], p['target_id'], f)
                out[key] = {'target_type': p['target_type'], 'target_id': p['target_id'], 'field': f, 'value': (ch.get('params') or {}).get(f) if p['target_type'] == 'rule' and ch.get('params') else ch.get(f),
                            'previous': (p['review'] or {}).get('previous', {}).get(f) if isinstance((p['review'] or {}).get('previous'), dict) else (p['review'] or {}).get('previous'),
                            'reason': p['override_reason'], 'overridden': p['status'] == 'APPLIED_WITH_OVERRIDE', 'proposal_id': p['id'], 'when': p['applied_ts'], 'document': p['document']}
        return [v for v in out.values() if v['overridden']]

    def notify(self, audience, unit, title, body, request_id=None):
        with self._lock:
            self.conn.execute('INSERT INTO notifications (ts, audience, unit, title, body, request_id) VALUES (?,?,?,?,?,?)',
                              (dt.datetime.now().isoformat(timespec='seconds'), audience, unit, title, body, request_id))
            self.conn.commit()

    def unread_notifications(self, audience='manager') -> int:
        return int(self.conn.execute('SELECT COUNT(*) FROM notifications WHERE audience=? AND read=0', (audience,)).fetchone()[0])

    def mark_notifications_read(self, audience='manager'):
        with self._lock:
            self.conn.execute('UPDATE notifications SET read=1 WHERE audience=? AND read=0', (audience,)); self.conn.commit()

    def notifications(self, audience='manager', limit=50):
        return [dict(r) for r in self.conn.execute('SELECT * FROM notifications WHERE audience=? ORDER BY id DESC LIMIT ?', (audience, limit)).fetchall()]

    # ------------------------------------------------------------ chat history (per staff member; never shared)
    def chat_log(self, employee_id, conversation_id, who, text, card=None):
        with self._lock:
            self.conn.execute('INSERT INTO chat_messages (employee_id, conversation_id, ts, who, text, card) VALUES (?,?,?,?,?,?)',
                              (employee_id, conversation_id, dt.datetime.now().isoformat(timespec='seconds'), who, text or '', json.dumps(card, default=str) if card else None))
            self.conn.commit()

    def conversations(self, employee_id, limit=30):
        rows = self.conn.execute(
            "SELECT conversation_id, MIN(ts) first_ts, MAX(ts) last_ts, COUNT(*) n, "
            "(SELECT text FROM chat_messages m2 WHERE m2.employee_id=m.employee_id AND m2.conversation_id=m.conversation_id AND m2.who='user' ORDER BY id LIMIT 1) title "
            "FROM chat_messages m WHERE employee_id=? GROUP BY conversation_id ORDER BY last_ts DESC LIMIT ?", (employee_id, limit)).fetchall()
        return [dict(r) for r in rows]

    def conversation(self, employee_id, conversation_id):
        out = []
        for r in self.conn.execute('SELECT ts, who, text, card FROM chat_messages WHERE employee_id=? AND conversation_id=? ORDER BY id', (employee_id, conversation_id)):
            d = dict(r); d['card'] = json.loads(d['card']) if d['card'] else None; out.append(d)
        return out

    def delete_conversation(self, employee_id, conversation_id):
        with self._lock:
            self.conn.execute('DELETE FROM chat_messages WHERE employee_id=? AND conversation_id=?', (employee_id, conversation_id)); self.conn.commit()

    # ------------------------------------------------------------ contacts (email addresses entered in the app) and the email outbox
    def get_contact(self, employee_id):
        r = self.conn.execute('SELECT email FROM contacts WHERE employee_id=?', (employee_id,)).fetchone()
        return r['email'] if r else None

    def set_contact(self, employee_id, email):
        with self._lock:
            self.conn.execute('INSERT INTO contacts (employee_id, email, updated) VALUES (?,?,?) ON CONFLICT(employee_id) DO UPDATE SET email=excluded.email, updated=excluded.updated',
                              (employee_id, email, dt.datetime.now().isoformat(timespec='seconds')))
            self.conn.commit()

    def log_email(self, to_addr, employee_id, subject, body, request_id, kind, status, error=''):
        with self._lock:
            cur = self.conn.execute('INSERT INTO emails (ts, to_addr, employee_id, subject, body, request_id, kind, status, error) VALUES (?,?,?,?,?,?,?,?,?)',
                                    (dt.datetime.now().isoformat(timespec='seconds'), to_addr, employee_id, subject, body, request_id, kind, status, error or ''))
            self.conn.commit()
            return cur.lastrowid

    def emails(self, limit=50):
        return [dict(r) for r in self.conn.execute('SELECT * FROM emails ORDER BY id DESC LIMIT ?', (limit,)).fetchall()]
