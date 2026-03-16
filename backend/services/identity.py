import asyncio
import logging
from datetime import datetime, timezone
from uuid import UUID

from rapidfuzz import fuzz
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import AsyncSessionLocal
from models import ClarificationEmail, Payment, PaymentStatus, Subscriber
from services.email import send_clarification_email
from services.subscription import calculate_subscription

logger = logging.getLogger(__name__)

_FUZZY_THRESHOLD = 85


async def resolve_identity(payment_id: UUID) -> None:
    """
    Entry point — BackgroundTask-safe.
    Creates its own DB session so it is not bound to the request lifecycle.
    """
    async with AsyncSessionLocal() as db:
        await _run_resolution(payment_id, db)


async def _run_resolution(payment_id: UUID, db: AsyncSession) -> None:
    """Orchestrates the three-tier matching in strict order."""
    result = await db.execute(select(Payment).where(Payment.id == payment_id))
    payment = result.scalar_one_or_none()
    if not payment:
        logger.error("resolve_identity: payment %s not found", payment_id)
        return

    if await _tier1_exact_email(payment, db):
        return

    if await _tier2_fuzzy_name(payment, db):
        return

    await _tier3_no_match(payment, db)


async def _tier1_exact_email(payment: Payment, db: AsyncSession) -> bool:
    """
    Exact email match — case-insensitive, whitespace-stripped.
    Excludes deleted subscribers.
    On match: sets status=auto_resolved and fires the subscription calculator.
    """
    email = (payment.email or "").strip().lower()
    if not email:
        return False

    result = await db.execute(
        select(Subscriber).where(
            func.lower(func.trim(Subscriber.email)) == email,
            Subscriber.deleted_from_substack == False,  # noqa: E712
        )
    )
    subscriber = result.scalar_one_or_none()
    if not subscriber:
        return False

    payment.status = PaymentStatus.auto_resolved
    await db.commit()

    logger.info("Tier 1 match: %s → %s", payment.id, subscriber.email)

    # We are already inside a BackgroundTask so there is no FastAPI
    # BackgroundTasks object available — use asyncio.create_task instead.
    asyncio.create_task(calculate_subscription(payment.id))
    return True


async def _tier2_fuzzy_name(payment: Payment, db: AsyncSession) -> bool:
    """
    Fuzzy name match via rapidfuzz token_sort_ratio.
    Threshold: 85. Takes the highest-scoring match above the threshold.
    On match: sets status=needs_review and records the match in resolution_notes.
    Does NOT trigger the subscription calculator — waits for editor approval.
    """
    if not payment.name:
        return False

    result = await db.execute(
        select(Subscriber).where(
            Subscriber.deleted_from_substack == False,  # noqa: E712
        )
    )
    subscribers = result.scalars().all()
    if not subscribers:
        return False

    best_score = 0
    best_subscriber = None
    for sub in subscribers:
        score = fuzz.token_sort_ratio(payment.name, sub.name)
        if score > best_score:
            best_score = score
            best_subscriber = sub

    if best_score < _FUZZY_THRESHOLD:
        return False

    payment.status = PaymentStatus.needs_review
    payment.resolution_notes = (
        f"Fuzzy match: {best_subscriber.name} (score: {best_score})"
    )
    await db.commit()

    logger.info(
        "Tier 2 match: %s → %s score=%d",
        payment.id, best_subscriber.name, best_score,
    )
    return True


async def _tier3_no_match(payment: Payment, db: AsyncSession) -> None:
    """
    No match found. Sets status=unknown, sends a clarification email to the
    payer, and writes a record to clarification_emails for dashboard tracking.
    """
    payment.status = PaymentStatus.unknown

    email_sent = await send_clarification_email(payment)
    if not email_sent:
        payment.resolution_notes = "clarification email failed"

    db.add(ClarificationEmail(
        payment_id=payment.id,
        sent_to_email=payment.email,
        sent_at=datetime.now(timezone.utc),
    ))
    await db.commit()

    logger.info("Tier 3: no match for %s, clarification sent", payment.id)
