import asyncio
import logging
import os
import smtplib
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


def _build_email(payment) -> tuple[str, str]:
    """
    Compose the clarification email subject and body.
    Pure function — no I/O, directly unit-testable.
    """
    subject = "Action needed: activate your Wire subscription"
    body = (
        f"Hi {payment.name},\n\n"
        f"We received your payment of INR {payment.amount_inr}. "
        f"To activate your subscription, please reply with the email address "
        f"you use on The India Cable's Substack newsletter.\n\n"
        f"If you used this address ({payment.email}), simply reply confirming that.\n\n"
        f"The India Cable team"
    )
    return subject, body


async def send_clarification_email(payment) -> bool:
    """
    Send a clarification email to the payer via SMTP.
    Returns True on success, False on any failure or when SMTP is not configured.
    Runs the blocking SMTP call in a thread-pool executor so the event loop
    is never blocked.
    """
    host = os.getenv("SMTP_HOST", "")
    from_email = os.getenv("CLARIFICATION_EMAIL_FROM", "")

    if not host or not from_email:
        logger.warning(
            "SMTP not configured (SMTP_HOST or CLARIFICATION_EMAIL_FROM is unset) — "
            "skipping clarification email for payment %s",
            payment.id,
        )
        return False

    subject, body = _build_email(payment)

    to_email = payment.email
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER", "")
    password = os.getenv("SMTP_PASSWORD", "")

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email

    def _send() -> None:
        with smtplib.SMTP(host, port) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(user, password)
            smtp.send_message(msg)

    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _send)
        return True
    except Exception as exc:
        logger.error(
            "SMTP send failed for payment %s to %s: %s",
            payment.id, to_email, exc,
        )
        return False
