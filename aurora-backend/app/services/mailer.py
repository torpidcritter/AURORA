"""Sending e-mail. With MAIL_BACKEND=log (the default) nothing leaves the server: the attempt is logged without the
message body, so links and business data never land in logs. MAIL_BACKEND=smtp sends through the configured relay."""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

from app.core.config import get_settings

log = logging.getLogger("aurora.mail")


class MailNotConfigured(RuntimeError):
    pass


def send(to: list[str], subject: str, text: str, html: str | None = None) -> str:
    """Send one message addressed to every address in ``to``. Returns the backend used."""
    s = get_settings()
    if s.mail_backend == "log":
        log.info(
            "mail not sent (MAIL_BACKEND=log)", extra={"extra_fields": {"recipients": len(to), "subject": subject}}
        )
        return "log"
    if not s.smtp_host:
        raise MailNotConfigured("SMTP_HOST is not set")
    msg = EmailMessage()
    msg["From"] = s.mail_from
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
        if s.smtp_starttls:
            smtp.starttls(context=ssl.create_default_context())
        if s.smtp_username and s.smtp_password:
            smtp.login(s.smtp_username, s.smtp_password.get_secret_value())
        smtp.send_message(msg)
    return "smtp"
