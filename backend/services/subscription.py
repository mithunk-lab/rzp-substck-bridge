import asyncio
import logging
from datetime import date
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import AsyncSessionLocal
from models import Action, ExecutionStatus, Payment, PaymentStatus, Subscriber, SubstackStatus

logger = logging.getLogger(__name__)

_AMOUNT_MAP: dict[int, int | None] = {
    200: 30,
    2000: 365,
    10000: None,  # lifetime — comp_days is irrelevant
}


async def calculate_subscription(payment_id: UUID) -> None:
    """
    Entry point — BackgroundTask-safe.
    Creates its own DB session so it is not bound to the request lifecycle.
    """
    async with AsyncSessionLocal() as db:
        await _run_calculation(payment_id, db)


async def _run_calculation(payment_id: UUID, db: AsyncSession) -> None:
    """Computes comp duration and writes an Action record."""

    # ── 1. Fetch payment ─────────────────────────────────────────────────────
    result = await db.execute(select(Payment).where(Payment.id == payment_id))
    payment = result.scalar_one_or_none()
    if not payment:
        logger.error("calculate_subscription: payment %s not found", payment_id)
        return

    # ── 2. Validate amount ───────────────────────────────────────────────────
    amount = payment.amount_inr
    if amount not in _AMOUNT_MAP:
        payment.status = PaymentStatus.needs_review
        payment.resolution_notes = f"Unrecognised payment amount: INR {amount}"
        await db.commit()
        logger.warning(
            "Unrecognised payment amount: INR %d for payment %s", amount, payment_id
        )
        return

    mapped_days = _AMOUNT_MAP[amount]  # None for lifetime
    is_lifetime = amount == 10000

    # ── 3. Fetch subscriber by normalised email ───────────────────────────────
    email = (payment.email or "").strip().lower()
    result = await db.execute(
        select(Subscriber).where(
            func.lower(func.trim(Subscriber.email)) == email,
        )
    )
    subscriber = result.scalar_one_or_none()

    # ── 4. Deleted subscriber ─────────────────────────────────────────────────
    if subscriber and subscriber.deleted_from_substack:
        payment.status = PaymentStatus.needs_review
        payment.resolution_notes = (
            "Subscriber marked as deleted from Substack — verify"
        )
        await db.commit()
        logger.warning(
            "Deleted subscriber made payment: %s (payment %s)", email, payment_id
        )
        return

    # ── 5. Lifetime subscriber ────────────────────────────────────────────────
    if subscriber and subscriber.substack_status == SubstackStatus.lifetime:
        if amount in (200, 2000):
            note = "Lifetime subscriber made renewal payment — verify intent"
        else:
            note = "Existing lifetime subscriber made a payment — verify intent"
        payment.status = PaymentStatus.needs_review
        payment.resolution_notes = note
        await db.commit()
        logger.warning(
            "Lifetime subscriber made payment of INR %d: %s (payment %s)",
            amount, email, payment_id,
        )
        return

    # ── 6. Compute comp_days ──────────────────────────────────────────────────
    if is_lifetime:
        comp_days = None
    elif subscriber is None or subscriber.substack_status == SubstackStatus.lapsed:
        # Case 2: new or lapsed
        comp_days = mapped_days
    else:
        # Case 3: active subscriber — extend from current expiry
        if subscriber.expiry_date is None:
            comp_days = mapped_days
        else:
            remaining_days = (subscriber.expiry_date - date.today()).days
            if remaining_days <= 0:
                # Expiry is today or in the past — treat as lapsed
                comp_days = mapped_days
            else:
                comp_days = remaining_days + mapped_days

    # ── 7. Resolve subscriber_email for Action record ─────────────────────────
    subscriber_email = subscriber.email if subscriber else payment.email

    # ── 8. Write Action and complete payment ──────────────────────────────────
    action = Action(
        payment_id=payment.id,
        subscriber_email=subscriber_email,
        comp_days=comp_days,
        is_lifetime=is_lifetime,
        execution_status=ExecutionStatus.pending,
    )
    db.add(action)
    payment.status = PaymentStatus.completed
    await db.commit()
    await db.refresh(action)

    logger.info(
        "Action created: %s comp=%s days / lifetime=%s",
        subscriber_email, comp_days, is_lifetime,
    )

    # ── 9. Trigger Substack Executor ──────────────────────────────────────────
    from services.substack import execute_substack_action
    asyncio.create_task(execute_substack_action(action.id))
