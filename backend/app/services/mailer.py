"""
Sending email (password reset codes).

Set these in backend/.env to send real email (any SMTP provider: Google Workspace,
Microsoft 365, Zoho, Amazon SES, ...):

  FMCG_SMTP_HOST=smtp.gmail.com
  FMCG_SMTP_PORT=587                 (587 = STARTTLS, 465 = SSL)
  FMCG_SMTP_USER=no-reply@yourcompany.in
  FMCG_SMTP_PASSWORD=...             (an app password; never commit it)
  FMCG_SMTP_FROM=FMCG Insights <no-reply@yourcompany.in>

Without FMCG_SMTP_HOST (development), the message is written to the backend's log
instead, so a reset can still be tested on your own computer.
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

log = logging.getLogger("fmcg.mail")


def configured() -> bool:
    return bool(os.environ.get("FMCG_SMTP_HOST"))


def send_mail(to: str, subject: str, text: str) -> bool:
    """True when handed to the mail server; False when only logged (no SMTP set up)."""
    if not configured():
        log.warning("Email not sent (FMCG_SMTP_HOST is not set). To: %s | %s\n%s", to, subject, text)
        print(f"\n[email not sent: SMTP not configured]\nTo: {to}\nSubject: {subject}\n{text}\n", flush=True)
        return False
    host = os.environ["FMCG_SMTP_HOST"]
    port = int(os.environ.get("FMCG_SMTP_PORT", "587"))
    user = os.environ.get("FMCG_SMTP_USER", "")
    password = os.environ.get("FMCG_SMTP_PASSWORD", "")
    msg = EmailMessage()
    msg["From"] = os.environ.get("FMCG_SMTP_FROM") or user
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=20) as s:
            if user:
                s.login(user, password)
            s.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=20) as s:
            s.starttls(context=context)
            if user:
                s.login(user, password)
            s.send_message(msg)
    return True
