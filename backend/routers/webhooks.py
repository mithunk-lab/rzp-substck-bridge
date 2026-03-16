import hashlib
import hmac
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models import Payment, PaymentStatus
from schemas import PaymentRead
from services.identity import resolve_identity

logger = logging.getLogger(__name__)

router = APIRouter()

# Ensure logs directory exists at module load time
LOGS_DIR = Path(__file__).parent.parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)
FAILED_WEBHOOKS_LOG = LOGS_DIR / "failed_webhooks.log"


def _verify_signature(body: bytes, signature: str, secret: str) -> bool:
    expected = hmac.new(
        secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def _extract_payment_data(payload: dict) -> dict:
    """
    Extract payment fields from a Razorpay payload with the specified fallback priority.
    email:  payment.notes.email → payment.email → contact.email
    name:   payment.notes.name  → payment.description
    phone:  payment.contact     (string on Razorpay)
    """
    entity = payload.get("payload", {}).get("payment", {}).get("entity", {})
    notes = entity.get("notes") or {}

    # email: notes first, then payment field, then contact object if it ever appears as a dict
    contact_field = entity.get("contact")
    contact_email = (
        contact_field.get("email")
        if isinstance(contact_field, dict)
        else None
    )
    email = notes.get("email") or entity.get("email") or contact_email

    # name: notes first, then description
    name = notes.get("name") or entity.get("description") or ""

    # phone is always a string on Razorpay
    phone = contact_field if isinstance(contact_field, str) else None

    # amount: paise → INR
    amount_inr = int(entity.get("amount", 0)) // 100

    # payment_timestamp: Unix int → aware datetime
    created_at_unix = entity.get("created_at")
    payment_timestamp = (
        datetime.fromtimestamp(created_at_unix, tz=timezone.utc)
        if created_at_unix
        else datetime.now(timezone.utc)
    )

    return {
        "razorpay_payment_id": entity.get("id"),
        "email": email or "",
        "name": name,
        "phone": phone,
        "amount_inr": amount_inr,
        "payment_timestamp": payment_timestamp,
    }


def _write_failed_webhook_log(exc: Exception, payload: dict) -> None:
    timestamp = datetime.now(timezone.utc).isoformat()
    try:
        with FAILED_WEBHOOKS_LOG.open("a") as f:
            f.write(f"[{timestamp}] ERROR: {exc}\n")
            f.write(f"PAYLOAD: {json.dumps(payload, default=str)}\n")
            f.write("---\n")
    except Exception as log_exc:
        logger.error("Could not write to failed_webhooks.log: %s", log_exc)
    logger.error("DB write failed | error=%s", exc)


@router.post("/webhooks/razorpay")
async def razorpay_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    # Belt-and-suspenders: never let any exception produce a 500 on this endpoint
    try:
        body = await request.body()
        signature = request.headers.get("X-Razorpay-Signature", "")
        secret = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
        source_ip = request.client.host if request.client else "unknown"

        if not _verify_signature(body, signature, secret):
            logger.warning(
                "Webhook signature verification failed | timestamp=%s | source_ip=%s",
                datetime.now(timezone.utc).isoformat(),
                source_ip,
            )
            return {"status": "ok"}

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            logger.error("Webhook body is not valid JSON | source_ip=%s", source_ip)
            return {"status": "ok"}

        if payload.get("event") != "payment.captured":
            return {"status": "ok"}

        data = _extract_payment_data(payload)
        razorpay_payment_id = data["razorpay_payment_id"]

        # Idempotency: skip if already recorded
        result = await db.execute(
            select(Payment).where(Payment.razorpay_payment_id == razorpay_payment_id)
        )
        if result.scalar_one_or_none():
            logger.info("duplicate payment ID skipped: %s", razorpay_payment_id)
            return {"status": "ok"}

        # Persist — if this fails we still return 200 to Razorpay
        try:
            payment = Payment(
                razorpay_payment_id=razorpay_payment_id,
                email=data["email"],
                name=data["name"],
                phone=data["phone"],
                amount_inr=data["amount_inr"],
                payment_timestamp=data["payment_timestamp"],
                status=PaymentStatus.pending,
            )
            db.add(payment)
            await db.commit()
            await db.refresh(payment)
        except Exception as exc:
            await db.rollback()
            _write_failed_webhook_log(exc, payload)
            return {"status": "ok"}

        background_tasks.add_task(resolve_identity, payment.id)

        return {"status": "ok"}

    except Exception as exc:
        logger.error("Unhandled exception in webhook handler: %s", exc)
        return {"status": "ok"}


@router.post("/webhooks/test-payment", response_model=PaymentRead)
async def test_payment(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    if os.getenv("ENVIRONMENT", "production") == "production":
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Not found")

    payload = await request.json()
    data = _extract_payment_data(payload)
    razorpay_payment_id = data["razorpay_payment_id"]

    result = await db.execute(
        select(Payment).where(Payment.razorpay_payment_id == razorpay_payment_id)
    )
    if existing := result.scalar_one_or_none():
        return PaymentRead.model_validate(existing)

    payment = Payment(
        razorpay_payment_id=razorpay_payment_id,
        email=data["email"],
        name=data["name"],
        phone=data["phone"],
        amount_inr=data["amount_inr"],
        payment_timestamp=data["payment_timestamp"],
        status=PaymentStatus.pending,
    )
    db.add(payment)
    await db.commit()
    await db.refresh(payment)

    background_tasks.add_task(resolve_identity, payment.id)

    return PaymentRead.model_validate(payment)
