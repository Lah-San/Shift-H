"""Policy-verified changes.

When the super user changes a rule, tier, agreement value or setting, they must attach an
Australian policy / award / agreement document. This module extracts the document text,
asks the AI (Gemini) to check whether the proposed change complies with it, and returns a
verdict, evidence quotes and recommendations. Without a Gemini key a built-in checker
does a keyword-and-number comparison and still produces evidence and recommendations.
"""
from __future__ import annotations
import hashlib, io, json, os, re, zipfile, traceback

AUS_MARKERS = ['western australia', 'wa health', 'australia', 'australian', 'fair work', 'wairc', 'industrial agreement', 'award', 'anf', 'hsuwa', 'ama ', 'uwu',
               'health services act', 'public sector', 'mp 0', 'policy framework', 'nurses', 'midwives', 'department of health']

# words that tell us which part of a document is relevant to each rule category
CATEGORY_KEYWORDS = {
    'fatigue': ['rest', 'break between', 'consecutive', 'hours of work', 'roster', 'night', 'fortnight', 'days off', 'shift', 'fatigue', 'maximum'],
    'notice': ['notice', 'application', 'respond', 'within', 'days', 'posted', 'published', 'roster'],
    'balance': ['accru', 'entitlement', 'balance', 'excess', 'two years', 'anniversary', 'cash out'],
    'coverage': ['staff', 'ratio', 'cover', 'backfill', 'relief', 'casual', 'agency', 'overtime', 'nurse', 'patient'],
    'eligibility': ['casual', 'fixed term', 'contract', 'service', 'eligib', 'long service'],
    'request': ['leave', 'application', 'block', 'period', 'weeks'],
    'urgent': ['personal leave', 'sick', 'carer', 'evidence', 'certificate', 'medical'],
    'calendar': ['public holiday', 'roster', 'published'],
    'equity': ['equit', 'fair', 'offer', 'vacant shift'],
    'governance': ['ai', 'artificial intelligence', 'decision', 'human', 'record', 'privacy'],
    'tier': ['casual', 'agency', 'overtime', 'relief', 'part-time', 'part time', 'additional hours', 'cover'],
    'agreement': ['hours', 'leave', 'rest', 'consecutive', 'notice', 'loading', 'overtime', 'casual'],
    'setting': ['roster', 'published', 'approve', 'leave'],
}


# ----------------------------------------------------------------------------- extraction
def extract_text(filename: str, data: bytes) -> str:
    name = (filename or '').lower()
    try:
        if name.endswith('.pdf'):
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            pages = []
            for i, p in enumerate(reader.pages[:200]):
                try:
                    pages.append(f'[page {i+1}]\n' + (p.extract_text() or ''))
                except Exception:
                    pass
            return '\n'.join(pages)
        if name.endswith('.docx'):
            z = zipfile.ZipFile(io.BytesIO(data))
            xml = z.read('word/document.xml').decode('utf8', 'ignore')
            xml = re.sub(r'</w:p>', '\n', xml)
            return re.sub(r'<[^>]+>', ' ', xml)
        return data.decode('utf-8', 'ignore')
    except Exception as ex:
        return f'[could not extract text: {ex}]'


def looks_australian(text: str) -> tuple[bool, list[str]]:
    t = text.lower()
    hits = [m for m in AUS_MARKERS if m in t]
    return len(hits) >= 2, hits


def relevant_passages(text: str, keywords: list[str], max_chars: int = 14000) -> list[str]:
    paras = [p.strip() for p in re.split(r'\n\s*\n|\n(?=\s*\d+[\.\)]\s)|\n(?=\(\w\))', text) if p.strip()]
    scored = []
    for p in paras:
        low = p.lower()
        sc = sum(low.count(k) for k in keywords)
        if sc:
            scored.append((sc, p))
    scored.sort(key=lambda x: -x[0])
    out, n = [], 0
    for sc, p in scored:
        p = p[:1500]
        if n + len(p) > max_chars:
            break
        out.append(p); n += len(p)
    return out


def _numbers_near(text: str, words: list[str]):
    found = []
    for w in words:
        for m in re.finditer(r'([^\n;]{0,90}\b' + re.escape(w) + r'\b[^\n;]{0,90})', text, re.I):
            seg = m.group(1)
            nums = re.findall(r'(\d+(?:\.\d+)?)\s*(hours?|hrs?|days?|weeks?|consecutive|%|per cent|shifts?|duties)', seg, re.I)
            for n, unit in nums:
                found.append((float(n), unit.lower(), seg.strip()))
    return found


# ----------------------------------------------------------------------------- built-in checker (no API key)
def builtin_check(target: dict, change: dict, text: str) -> dict:
    cat = target.get('category', 'setting')
    kws = CATEGORY_KEYWORDS.get(cat, []) + [w.lower() for w in re.findall(r'[A-Za-z]{4,}', target.get('name', ''))]
    passages = relevant_passages(text, kws, 6000)
    is_aus, markers = looks_australian(text)
    findings, recs, evidence = [], [], []
    verdict, confidence = 'UNCERTAIN', 0.4
    new_params = change.get('params') or {}
    new_vals = {k: v for k, v in new_params.items() if isinstance(v, (int, float))}
    new_vals.update({k: v for k, v in change.items() if k not in ('params', 'enabled', 'severity', 'justification') and isinstance(v, (int, float))})
    matches, conflicts = 0, 0
    for k, v in new_vals.items():
        words = [w for w in re.split(r'[_\s]+', k) if len(w) > 2]
        near = _numbers_near('\n'.join(passages) or text[:20000], words)
        for n, unit, seg in near[:6]:
            evidence.append(seg[:220])
            if abs(n - float(v)) < 1e-9:
                matches += 1; findings.append(f'"{k}" = {v} matches a value stated in the document ("...{seg[:120]}...").')
            elif unit.startswith(('hour', 'hr', 'day', 'week', 'shift', 'dut', 'consecutive')):
                conflicts += 1; findings.append(f'"{k}" proposed as {v} but the document mentions {n:g} {unit} nearby ("...{seg[:120]}...").')
    if change.get('enabled') is False:
        findings.append('The change switches the rule OFF. Check the document explicitly removes or relaxes this requirement; most WA Health instruments keep it.')
        recs.append('Prefer relaxing the parameter or lowering severity to "soft" instead of disabling the rule, so the manager still sees it.')
    if matches and not conflicts:
        verdict, confidence = 'COMPLIANT', 0.7
    elif conflicts and not matches:
        verdict, confidence = 'NON_COMPLIANT', 0.65
    elif matches and conflicts:
        verdict, confidence = 'UNCERTAIN', 0.5
        recs.append('The document contains both matching and conflicting figures; check whether they apply to different employee groups (agreement families).')
    if not is_aus:
        recs.append('The document does not read like an Australian policy, award or agreement (no WA Health / Fair Work / WAIRC / Australian markers). Upload the governing WA Health instrument.')
        confidence = min(confidence, 0.35)
    if not passages:
        recs.append('No passage in the document relates to this rule. Upload the section of the agreement or policy that covers it.')
        confidence = min(confidence, 0.3)
    recs.append(f"Record the clause reference in the rule's 'source' field so the audit trail points to the document ({target.get('source', 'current source unknown')}).")
    return {'verdict': verdict, 'confidence': round(confidence, 2), 'australian_document': is_aus, 'markers': markers[:8], 'findings': findings[:8],
            'recommendations': recs[:6], 'evidence': evidence[:6], 'engine': 'builtin', 'summary': f'Built-in check: {matches} matching and {conflicts} conflicting figures found in {len(passages)} relevant passage(s).'}


# ----------------------------------------------------------------------------- Gemini checker
PROMPT = """You are a compliance reviewer for a hospital leave-and-rostering rule engine in Western Australia.
A super user wants to change one rule. Decide whether the change complies with the uploaded Australian policy / award / agreement text.

RULE BEING CHANGED (JSON):
{target}

PROPOSED CHANGE (JSON):
{change}

ADMIN'S JUSTIFICATION: {justification}

RELEVANT PASSAGES FROM THE UPLOADED DOCUMENT ({doc_name}):
{passages}

Respond ONLY with JSON of this shape:
{{
 "verdict": "COMPLIANT" | "NON_COMPLIANT" | "UNCERTAIN",
 "confidence": 0.0-1.0,
 "australian_document": true|false,
 "summary": "two sentences",
 "findings": ["specific comparison between the proposed value and what the document says, quoting the clause"],
 "evidence": ["short verbatim quotes from the document that support the verdict, with clause or page if visible"],
 "recommendations": ["intelligent, specific advice: e.g. the exact value the document supports, other rules or agreement families that should change together, severity suggestions, what to record as the source"],
 "suggested_change": {{"params": {{}}, "enabled": true, "severity": "hard|soft|info"}}  // the change you would apply instead, or the same change if compliant
}}
Be strict: if the document sets a stricter limit than the proposed value, the verdict is NON_COMPLIANT. If the document does not cover the topic, say UNCERTAIN and explain what document would be needed."""


def gemini_check(target: dict, change: dict, text: str, doc_name: str, justification: str) -> dict:
    from google import genai
    from google.genai import types
    cat = target.get('category', 'setting')
    kws = CATEGORY_KEYWORDS.get(cat, []) + [w.lower() for w in re.findall(r'[A-Za-z]{4,}', target.get('name', ''))]
    passages = relevant_passages(text, kws, 16000) or [text[:8000]]
    client = genai.Client(api_key=os.environ['GEMINI_API_KEY'])
    from .chat import _models
    prompt = PROMPT.format(target=json.dumps(target, default=str)[:4000], change=json.dumps(change, default=str), justification=justification or '(none)', doc_name=doc_name, passages='\n---\n'.join(passages))
    resp = None; model = None
    for model in _models():
        try:
            resp = client.models.generate_content(model=model, contents=prompt, config=types.GenerateContentConfig(temperature=0.1, response_mime_type='application/json'))
            break
        except Exception as ex:
            if any(k in str(ex) for k in ('429', '404', '503', 'RESOURCE_EXHAUSTED', 'NOT_FOUND', 'UNAVAILABLE')):
                continue
            raise
    if resp is None:
        raise RuntimeError('all Gemini models over quota')
    raw = resp.text or '{}'
    try:
        out = json.loads(raw)
    except Exception:
        m = re.search(r'\{.*\}', raw, re.S)
        out = json.loads(m.group(0)) if m else {'verdict': 'UNCERTAIN', 'summary': raw[:500]}
    out.setdefault('verdict', 'UNCERTAIN'); out.setdefault('confidence', 0.5); out.setdefault('findings', []); out.setdefault('recommendations', []); out.setdefault('evidence', [])
    out['engine'] = 'gemini'; out['model'] = model
    is_aus, markers = looks_australian(text)
    out.setdefault('australian_document', is_aus); out['markers'] = markers[:8]
    return out


def check(target: dict, change: dict, filename: str, data: bytes, justification: str = '') -> dict:
    text = extract_text(filename, data)
    digest = hashlib.sha256(data).hexdigest()[:16]
    base = {'document': filename, 'document_sha256': digest, 'characters': len(text)}
    if os.environ.get('GEMINI_API_KEY') and not os.environ.get('LEAVECOVER_NO_GEMINI'):
        try:
            return {**base, **gemini_check(target, change, text, filename, justification)}
        except Exception as ex:
            traceback.print_exc()
            out = builtin_check(target, change, text)
            out['warning'] = f'Gemini unavailable ({type(ex).__name__}); built-in checker used.'
            return {**base, **out}
    return {**base, **builtin_check(target, change, text)}


# ----------------------------------------------------------------------------- discovery: read a document, propose rule updates
DISCOVER_PROMPT = """You are updating a hospital leave-and-rostering rulebook from a newly uploaded Australian policy, award or agreement.
Compare the CURRENT RULEBOOK VALUES with what the DOCUMENT says. Report only concrete, numeric or on/off differences the document supports.

CURRENT RULEBOOK VALUES (JSON):
{current}

DOCUMENT PASSAGES ({doc_name}):
{passages}

Respond ONLY with JSON: {{"document_summary": "one sentence: what the document is and who it covers",
 "findings": [ {{"target_type": "rule"|"agreement"|"tier"|"setting", "target_id": "e.g. FAT-001 or ANF or overtime or roster_published_days",
   "field": "the param or agreement key, e.g. min_break_hours (omit for rule on/off)", "current": <current value>, "proposed": <new value>,
   "confidence": 0.0-1.0, "evidence": "verbatim quote with clause/page", "rationale": "one sentence" }} ] }}
VALUES SET BY MANUAL OVERRIDE (check each of these against the document and ALWAYS include a finding for it: if the document states a different value, propose the document's value; if the document confirms the current value, still include it with proposed equal to current and rationale "document confirms"; if the document does not mention it, include it with proposed null and rationale "not covered by this document"):
{overrides}

Rules: apart from the override list, only include findings where the document clearly states a value that is DIFFERENT from the current rulebook value (never report confirmations of the same value); map to the agreement family the document covers (ANF nurses, UWU_EN_AIN, HSUWA, AMA doctors); keep at most 14 findings; if nothing differs, return an empty list."""


def _same(a, b) -> bool:
    try:
        return abs(float(a) - float(b)) < 1e-9
    except (TypeError, ValueError):
        return str(a).strip().lower() == str(b).strip().lower()


def current_value(rb, tt: str, tid: str, field):
    if tt == 'rule':
        r = rb._index.get(tid) or {}
        if field in (None, '', 'enabled'):
            return r.get('enabled', True)
        if field == 'severity':
            return r.get('severity')
        return (r.get('params') or {}).get(field)
    if tt == 'agreement':
        return rb.data['agreements'].get(tid, {}).get(field)
    if tt == 'tier':
        t = next((x for x in rb.all_tiers() if x['id'] == tid), {})
        return t.get(field or 'enabled')
    if tt == 'setting':
        return rb.settings.get(tid)
    return None


def _current_values(rb) -> dict:
    cur = {'rules': {r['id']: {'name': r['name'], 'enabled': r.get('enabled', True), 'severity': r.get('severity'), 'params': r.get('params') or {}} for r in rb.rules()},
           'agreements': {k: {kk: vv for kk, vv in v.items() if kk != 'label'} for k, v in rb.data['agreements'].items()},
           'cover_tiers': {t['id']: {'enabled': t.get('enabled', True), 'cost_multiplier': t.get('cost_multiplier')} for t in rb.all_tiers()},
           'settings': {k: rb.settings[k] for k in ('roster_published_days', 'auto_accept_sick_leave', 'auto_approve_when_fully_covered')}}
    return cur


AGREEMENT_FIELDS = {  # (agreement key, keywords, unit words)
    'min_break_hours': (['break between shifts', 'between shifts', 'rest between', 'minimum break', 'break of'], ('hour', 'hr')),
    'max_consecutive_duties': (['consecutive', 'in a row'], ('day', 'shift', 'dut', 'consecutive')),
    'max_duties_fortnight': (['per fortnight', 'fortnightly', 'in a fortnight'], ('shift', 'dut', 'day')),
    'annual_notice_days': (['notice', 'application for annual leave', 'apply for annual leave'], ('day', 'week')),
    'response_days': (['respond', 'response', 'within'], ('day',)),
    'evidence_after_days': (['evidence', 'certificate'], ('day',)),
    'casual_loading': (['casual loading', 'loading of'], ('%', 'per cent')),
    'max_shift_hours': (['ordinary hours in any one day', 'maximum shift', 'shift length', 'hours in any one day'], ('hour', 'hr')),
    'night_day_changeover_hours': (['night and day', 'changing from night', 'night duty to day'], ('hour', 'hr')),
}


def builtin_discover(rb, text: str) -> dict:
    """No-AI fallback: find numbers next to well-known phrases and compare with every agreement family."""
    is_aus, markers = looks_australian(text)
    fam = 'ANF' if 'anf' in text.lower() or 'nurses' in text.lower() else 'AMA' if 'ama' in text.lower() or 'medical practitioner' in text.lower() else 'HSUWA' if 'hsu' in text.lower() else 'UWU_EN_AIN' if 'uwu' in text.lower() or 'enrolled' in text.lower() else 'ANF'
    findings = []
    for field, (kws, units) in AGREEMENT_FIELDS.items():
        cur = rb.data['agreements'][fam].get(field)
        if cur is None:
            continue
        for n, unit, seg in _numbers_near(text[:60000], kws)[:8]:
            if not unit.startswith(units):
                continue
            val = n / 100 if field == 'casual_loading' and n > 1 else n
            if unit.startswith('week') and field.endswith('days'):
                val = n * 7
            if abs(float(val) - float(cur)) > 1e-9:
                findings.append({'target_type': 'agreement', 'target_id': fam, 'field': field, 'current': cur, 'proposed': val, 'confidence': 0.45, 'evidence': seg[:240], 'rationale': f'Document states {n:g} {unit} near "{[k for k in kws if k in seg.lower()][:1] or kws[0]}"; rulebook has {cur}.'})
                break
    return {'document_summary': f'Built-in scan (no AI). Looks like an Australian instrument for {fam}.' if is_aus else 'Built-in scan (no AI). Document does not look like an Australian policy.', 'australian_document': is_aus, 'findings': findings[:12], 'engine': 'builtin'}


def _override_key(o):
    return (o['target_type'], o['target_id'], o.get('field') or 'enabled')


def _annotate_overrides(rb, findings: list, overrides: list, text: str) -> tuple:
    """Attach override info to findings and build override_checks (one row per overridden value)."""
    by_key = {(f.get('target_type'), f.get('target_id'), f.get('field') or 'enabled'): f for f in findings}
    checks = []
    for o in overrides or []:
        k = _override_key(o); cur = current_value(rb, o['target_type'], o['target_id'], o.get('field') if o['target_type'] != 'setting' else None)
        f = by_key.get(k)
        doc_val = f.get('proposed') if f else None
        if f is None:
            # built-in scan for this specific field
            kws = [w for w in re.split(r'[_\s]+', o.get('field') or '') if len(w) > 2]
            near = _numbers_near(text[:80000], kws) if kws else []
            if near:
                doc_val = near[0][0]
        if doc_val is None:
            status = 'not_found'
        elif _same(doc_val, cur):
            status = 'matches'
        else:
            status = 'differs'
        row = {**o, 'current': cur, 'document_value': doc_val, 'status': status}
        checks.append(row)
        if f is not None:
            f['override'] = {'reason': o.get('reason'), 'previous': o.get('previous'), 'when': o.get('when'), 'proposal_id': o.get('proposal_id')}
            f['current'] = cur
        elif status == 'differs':
            findings.append({'target_type': o['target_type'], 'target_id': o['target_id'], 'field': o.get('field'), 'current': cur, 'proposed': doc_val, 'confidence': 0.55,
                             'evidence': next((seg for n, unit, seg in _numbers_near(text[:80000], [w for w in re.split(r'[_\s]+', o.get('field') or '') if len(w) > 2]) if abs(n - doc_val) < 1e-9), ''),
                             'rationale': 'Built-in scan: the document states this value; the rulebook holds a manually overridden value.',
                             'override': {'reason': o.get('reason'), 'previous': o.get('previous'), 'when': o.get('when'), 'proposal_id': o.get('proposal_id')}})
    # findings that merely confirm (proposed == current) are not changes: drop them from the change list but keep the check row
    findings[:] = [f for f in findings if f.get('proposed') is not None and not _same(f.get('current'), f.get('proposed'))]
    return findings, checks


def discover(rb, filename: str, data: bytes, overrides: list | None = None) -> dict:
    text = extract_text(filename, data)
    digest = hashlib.sha256(data).hexdigest()[:16]
    base = {'document': filename, 'document_sha256': digest, 'characters': len(text)}
    overrides = overrides or []
    ov_text = '\n'.join(f"- {o['target_type']} {o['target_id']} field={o.get('field')} current={current_value(rb, o['target_type'], o['target_id'], o.get('field') if o['target_type'] != 'setting' else None)} previous={o.get('previous')} override_reason={o.get('reason')!r}" for o in overrides) or '(none)'
    if os.environ.get('GEMINI_API_KEY') and not os.environ.get('LEAVECOVER_NO_GEMINI'):
        try:
            from google import genai
            from google.genai import types
            from .chat import _models
            kws = sorted({k for v in CATEGORY_KEYWORDS.values() for k in v})
            passages = relevant_passages(text, kws, 22000) or [text[:12000]]
            client = genai.Client(api_key=os.environ['GEMINI_API_KEY'])
            prompt = DISCOVER_PROMPT.format(current=json.dumps(_current_values(rb), default=str), doc_name=filename, passages='\n---\n'.join(passages), overrides=ov_text)
            resp = None
            for model in _models():
                try:
                    resp = client.models.generate_content(model=model, contents=prompt, config=types.GenerateContentConfig(temperature=0.1, response_mime_type='application/json')); break
                except Exception as ex:
                    if any(k in str(ex) for k in ('429', '404', '503', '400', 'RESOURCE_EXHAUSTED', 'NOT_FOUND', 'UNAVAILABLE')):
                        continue
                    raise
            if resp is None:
                raise RuntimeError('all models over quota')
            raw = resp.text or '{}'
            try:
                out = json.loads(raw)
            except Exception:
                m = re.search(r'\{.*\}', raw, re.S); out = json.loads(m.group(0)) if m else {'findings': []}
            out.setdefault('findings', []); out['engine'] = 'gemini'; out['model'] = model
            out['australian_document'] = looks_australian(text)[0]
            # validate targets against the rulebook and drop anything that does not actually change a value
            valid, confirmed = [], []
            ov_keys = {_override_key(o) for o in overrides}
            for f in out['findings']:
                tt, tid = f.get('target_type'), f.get('target_id')
                if not (tt == 'rule' and tid in rb._index or tt == 'agreement' and tid in rb.data['agreements'] or tt == 'tier' and any(t['id'] == tid for t in rb.all_tiers()) or tt == 'setting' and tid in rb.settings):
                    continue
                cur = current_value(rb, tt, tid, f.get('field') if tt != 'setting' else None)
                f['current'] = cur
                if (tt, tid, f.get('field') or 'enabled') in ov_keys:
                    valid.append(f); continue
                if f.get('proposed') is None or _same(cur, f.get('proposed')):
                    confirmed.append(f); continue
                valid.append(f)
            findings, checks = _annotate_overrides(rb, valid, overrides, text)
            out['findings'] = findings[:14]
            out['override_checks'] = checks
            out['confirmed'] = len(confirmed)
            return {**base, **out}
        except Exception as ex:
            traceback.print_exc()
            out = builtin_discover(rb, text); out['warning'] = f'Gemini unavailable ({type(ex).__name__}); built-in scan used.'
            out['findings'], out['override_checks'] = _annotate_overrides(rb, out['findings'], overrides, text)
            return {**base, **out}
    out = builtin_discover(rb, text)
    out['findings'], out['override_checks'] = _annotate_overrides(rb, out['findings'], overrides, text)
    return {**base, **out}
