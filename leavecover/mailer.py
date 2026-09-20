"""Outbound email for decisions and cover requests.

Delivery uses plain SMTP with STARTTLS (port 587) or SSL (port 465) when these environment variables are set:
    SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, EMAIL_FROM
Any provider works: Brevo (300 free emails a day, no card), Gmail with an app password, Outlook, SendGrid.
Without them nothing is sent; every message is still recorded in the outbox so the manager can open it in their own
mail client (a mailto link) or copy it. Nothing here ever raises: the caller gets {'sent': bool, 'error': str}.
"""
from __future__ import annotations
import os, smtplib, ssl, urllib.parse
from email.message import EmailMessage


def configured() -> bool:
    return bool(os.environ.get('SMTP_HOST') and os.environ.get('EMAIL_FROM'))


def provider_hint() -> str:
    return 'Email delivery is not configured on this server (set SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, EMAIL_FROM). The message was saved to the outbox.'


def mailto(to: str, subject: str, body: str) -> str:
    q = urllib.parse.urlencode({'subject': subject, 'body': body}, quote_via=urllib.parse.quote)
    return f'mailto:{to}?{q}'


def send(to: str, subject: str, body: str) -> dict:
    if not configured():
        return {'sent': False, 'error': 'not configured'}
    host = os.environ['SMTP_HOST']; port = int(os.environ.get('SMTP_PORT', '587'))
    user = os.environ.get('SMTP_USER', ''); pw = os.environ.get('SMTP_PASSWORD', ''); sender = os.environ['EMAIL_FROM']
    msg = EmailMessage()
    msg['From'] = sender; msg['To'] = to; msg['Subject'] = subject
    msg.set_content(body)
    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=20, context=ssl.create_default_context()) as s:
                if user:
                    s.login(user, pw)
                s.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=20) as s:
                s.ehlo(); s.starttls(context=ssl.create_default_context()); s.ehlo()
                if user:
                    s.login(user, pw)
                s.send_message(msg)
        return {'sent': True, 'error': ''}
    except Exception as ex:   # never break the app because a mail server is down
        return {'sent': False, 'error': f'{type(ex).__name__}: {ex}'[:300]}
