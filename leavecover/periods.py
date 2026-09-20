"""Turn vague periods ("Christmas", "Easter", "school holidays", "December", "next month") into concrete date ranges.
WA school-holiday dates are the published 2026 and 2027 calendars (approximate to the day)."""
from __future__ import annotations
import datetime as dt, re

MONTHS = {m: i for i, m in enumerate(['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december'], 1)}
WA_SCHOOL_HOLIDAYS = [  # (label, start, end)
    ('Spring school holidays 2026', dt.date(2026, 9, 26), dt.date(2026, 10, 11)),
    ('Summer school holidays 2026-27', dt.date(2026, 12, 18), dt.date(2027, 1, 31)),
    ('Autumn school holidays 2027', dt.date(2027, 4, 2), dt.date(2027, 4, 18)),
    ('Winter school holidays 2027', dt.date(2027, 7, 3), dt.date(2027, 7, 18)),
    ('Spring school holidays 2027', dt.date(2027, 9, 25), dt.date(2027, 10, 10)),
]


HOLIDAY_ALIASES = [   # (regex, canonical name in the WA calendar)
    (r"king'?s? ?birthday|queen'?s? ?birthday|monarch'?s birthday", "King's Birthday"),
    (r"anzac", 'Anzac Day'),
    (r"australia day", 'Australia Day'),
    (r"labou?r day", 'Labour Day'),
    (r"wa day|western australia day|foundation day", 'Western Australia Day'),
    (r"good friday", 'Good Friday'),
    (r"easter monday", 'Easter Monday'),
    (r"boxing day", 'Boxing Day'),
    (r"new year'?s? day", "New Year's Day"),
]


def named_holiday(phrase: str, today: dt.date, holidays: dict | None):
    """(canonical name, next date on/after today, substitute date or None) for a holiday named in the phrase, else None.
    `holidays` is the WA calendar: {date: name}."""
    if not holidays:
        return None
    t = (phrase or '').lower()
    for rx, name in HOLIDAY_ALIASES:
        if re.search(rx, t):
            m = re.search(r'\b(20\d{2})\b', t)
            year = int(m.group(1)) if m else None
            dates = sorted(d for d, n in holidays.items() if n == name and (d.year == year if year else d >= today))
            if not dates:
                dates = sorted(d for d, n in holidays.items() if n == name)
                if not dates:
                    return None
                dates = dates[-1:]
            d0 = dates[0]
            sub = next((d for d, n in holidays.items() if n == f'{name} (substitute)' and 0 <= (d - d0).days <= 3), None)
            return name, d0, sub
    return None


def resolve(phrase: str, today: dt.date, length_days: int = 14, holidays: dict | None = None) -> dict | None:
    """Return {'label', 'range_start', 'range_end', 'anchor', 'note'} for a recognised period, else None."""
    t = (phrase or '').lower()
    yr = today.year
    nh = named_holiday(phrase, today, holidays)
    if nh and not re.search(r'christmas|xmas|easter\b(?! monday)', t):
        name, d0, sub = nh
        note = f"{name} is {d0:%A %d %B %Y} in Western Australia (a public holiday, not deducted from leave)"
        if sub:
            note += f"; the substitute public holiday is {sub:%A %d %B}"
        if d0.weekday() == 0:
            note += '; it makes a long weekend Saturday to Monday'
        if d0 < today:
            note += ' (already passed)'
        pad = max(3, length_days)
        return {'label': f'{name} {d0.year}', 'range_start': d0 - dt.timedelta(days=pad), 'range_end': (sub or d0) + dt.timedelta(days=pad), 'anchor': d0, 'note': note, 'holiday': d0.isoformat(), 'substitute': sub.isoformat() if sub else None, 'name': name}
    def xmas_year():
        return yr if today <= dt.date(yr, 12, 26) else yr + 1
    if re.search(r'christmas|xmas|festive', t):
        y = xmas_year(); anchor = dt.date(y, 12, 25)
        if 'before' in t:
            return {'label': f'before Christmas {y}', 'range_start': anchor - dt.timedelta(days=length_days + 14), 'range_end': dt.date(y, 12, 24), 'anchor': None, 'note': 'ending on or before Christmas Eve'}
        if 'after' in t:
            return {'label': f'after Christmas {y}', 'range_start': dt.date(y, 12, 27), 'range_end': dt.date(y + 1, 1, 20), 'anchor': None, 'note': 'starting after Boxing Day'}
        return {'label': f'Christmas {y}', 'range_start': anchor - dt.timedelta(days=length_days + 3), 'range_end': dt.date(y + 1, 1, 12), 'anchor': anchor, 'note': 'blocks that include Christmas Day are listed first; 25, 26 and 28 December are public holidays'}
    if re.search(r'new year', t):
        y = xmas_year(); anchor = dt.date(y + 1, 1, 1)
        return {'label': f'New Year {y + 1}', 'range_start': dt.date(y, 12, 20), 'range_end': dt.date(y + 1, 1, 20), 'anchor': anchor, 'note': '1 January is a public holiday'}
    if re.search(r'easter', t):
        # Easter Sunday 2027 = 28 March; 2028 = 16 April
        anchor = dt.date(2027, 3, 28) if today < dt.date(2027, 3, 30) else dt.date(2028, 4, 16)
        return {'label': f'Easter {anchor.year}', 'range_start': anchor - dt.timedelta(days=length_days), 'range_end': anchor + dt.timedelta(days=16), 'anchor': anchor, 'note': 'Good Friday, Easter Sunday and Easter Monday are public holidays'}
    if re.search(r'school holiday|school break|term break', t):
        for label, s, e in WA_SCHOOL_HOLIDAYS:
            if e >= today + dt.timedelta(days=7):
                return {'label': label, 'range_start': s - dt.timedelta(days=2), 'range_end': e, 'anchor': s, 'note': 'WA public school holiday dates'}
    m = re.search(r'\b(' + '|'.join(MONTHS) + r')\b(?:\s+(\d{4}))?', t)
    if m:
        mon = MONTHS[m.group(1)]; y = int(m.group(2)) if m.group(2) else (yr if mon >= today.month else yr + 1)
        s = dt.date(y, mon, 1); e = (dt.date(y + (mon == 12), (mon % 12) + 1, 1) - dt.timedelta(days=1))
        return {'label': s.strftime('%B %Y'), 'range_start': s, 'range_end': e, 'anchor': None, 'note': ''}
    if re.search(r'next month', t):
        s = (today.replace(day=1) + dt.timedelta(days=32)).replace(day=1); e = (s + dt.timedelta(days=32)).replace(day=1) - dt.timedelta(days=1)
        return {'label': s.strftime('%B %Y'), 'range_start': s, 'range_end': e, 'anchor': None, 'note': ''}
    if re.search(r'summer', t):
        y = xmas_year(); return {'label': f'summer {y}-{y + 1}', 'range_start': dt.date(y, 12, 1), 'range_end': dt.date(y + 1, 2, 28), 'anchor': dt.date(y, 12, 25), 'note': ''}
    if re.search(r'winter', t):
        y = yr if today < dt.date(yr, 8, 15) else yr + 1; return {'label': f'winter {y}', 'range_start': dt.date(y, 6, 1), 'range_end': dt.date(y, 8, 31), 'anchor': None, 'note': ''}
    return None


def length_from_text(text: str, default: int = 14) -> int:
    t = text.lower()
    m = re.search(r'(\d+)\s*(day|week|month|fortnight)s?', t)
    if m:
        n = int(m.group(1)); u = m.group(2)
        return n * {'day': 1, 'week': 7, 'month': 30, 'fortnight': 14}[u]
    for w, n in (('a fortnight', 14), ('two weeks', 14), ('a week', 7), ('one week', 7), ('three weeks', 21), ('a month', 30), ('two months', 60), ('three months', 90), ('long weekend', 4)):
        if w in t:
            return n
    return default
