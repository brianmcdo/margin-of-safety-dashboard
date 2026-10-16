"""Console and optional email alerts for newly qualified candidates."""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

from src.config import EmailSettings, Settings
from src.data.models import SignalRecord

logger = logging.getLogger(__name__)

ALERT_SIGNALS = {"Strong Value", "Moderate Value", "Deep Value / Net-Net"}


def new_candidates(
    current: list[SignalRecord],
    previous_tickers: set[str],
) -> list[SignalRecord]:
    """High-scoring names that were not in the prior run's alert set."""
    fresh = [
        record
        for record in current
        if record.signal in ALERT_SIGNALS and record.ticker not in previous_tickers
    ]
    fresh.sort(key=lambda record: (-record.composite_score, record.ticker))
    return fresh


def format_alert_lines(records: list[SignalRecord]) -> list[str]:
    lines: list[str] = []
    for record in records:
        mos = "n/a" if record.margin_of_safety is None else f"{record.margin_of_safety:.1%}"
        lines.append(
            f"{record.ticker}: {record.signal}  score {record.composite_score:.1f}  "
            f"margin of safety {mos}  price {record.price}"
        )
    return lines


def emit_alerts(
    records: list[SignalRecord],
    previous_tickers: set[str],
    settings: Settings,
) -> list[str]:
    """Print new signals and optionally email them. Returns the alert lines."""
    fresh = new_candidates(records, previous_tickers)
    lines = format_alert_lines(fresh)
    if settings.alerts.console:
        if not lines:
            logger.info("No new high-scoring candidates since the previous run")
        for line in lines:
            logger.info("NEW SIGNAL %s", line)
    email_settings = settings.alerts.email
    if email_settings.enabled and lines:
        _send_email(lines, email_settings)
    return lines


def _send_email(lines: list[str], email_settings: EmailSettings) -> None:
    sender = os.environ.get(email_settings.from_env, "")
    password = os.environ.get(email_settings.password_env, "")
    username = os.environ.get(email_settings.username_env, sender)
    if not email_settings.smtp_host or not sender or not email_settings.to:
        logger.warning("Email alerts are enabled but SMTP host, sender, or recipients are missing")
        return
    message = EmailMessage()
    message["Subject"] = f"Value signals: {len(lines)} new candidate(s)"
    message["From"] = sender
    message["To"] = ", ".join(email_settings.to)
    message.set_content(
        "New margin-of-safety candidates. These are research signals, not advice.\n\n"
        + "\n".join(lines)
    )
    try:
        with smtplib.SMTP(email_settings.smtp_host, email_settings.smtp_port, timeout=30) as smtp:
            if email_settings.use_tls:
                smtp.starttls()
            if password:
                smtp.login(username, password)
            smtp.send_message(message)
    except Exception:
        logger.exception("Email alert failed")
