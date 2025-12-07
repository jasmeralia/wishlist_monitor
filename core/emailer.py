# core/emailer.py
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from .logger import get_logger

logger = get_logger(__name__)

EMAIL_FROM = os.getenv("EMAIL_FROM", "").strip()
SMTP_HOST = os.getenv("SMTP_HOST", "").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASS = os.getenv("SMTP_PASS", "").strip()
SMTP_USE_SSL = os.getenv("SMTP_USE_SSL", "false").lower() == "true"

# Global default recipients (comma or semicolon separated)
_EMAIL_TO_RAW = os.getenv("EMAIL_TO", "").strip()


def get_global_recipients() -> list[str]:
    if not _EMAIL_TO_RAW:
        return []
    parts = [p.strip() for p in _EMAIL_TO_RAW.replace(";", ",").split(",")]
    return [p for p in parts if p]


def send_email(
    subject: str,
    html_body: str,
    text_body: str | None,
    recipients: list[str],
):
    if not recipients:
        logger.warning(
            "No recipients provided for email '%s'; skipping send.", subject
        )
        return

    if not (EMAIL_FROM and SMTP_HOST):
        logger.warning(
            "Email not fully configured (EMAIL_FROM/SMTP_HOST); skipping email: %s",
            subject,
        )
        return

    msg = MIMEMultipart("alternative")
    msg["From"] = EMAIL_FROM
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject

    if not text_body:
        text_body = "HTML capable email client required to view this report."

    part_text = MIMEText(text_body, "plain", "utf-8")
    part_html = MIMEText(html_body, "html", "utf-8")

    msg.attach(part_text)
    msg.attach(part_html)

    if SMTP_USE_SSL:
        server = smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30)
    else:
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30)

    try:
        if not SMTP_USE_SSL:
            server.starttls()
        if SMTP_USER:
            server.login(SMTP_USER, SMTP_PASS)
        server.sendmail(EMAIL_FROM, recipients, msg.as_string())
        logger.info("Email sent to %s: %s", recipients, subject)
    finally:
        try:
            server.quit()
        except Exception:
            pass
