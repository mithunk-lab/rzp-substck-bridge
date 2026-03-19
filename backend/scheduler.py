import logging
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import func, select, update

logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


async def check_sync_overdue() -> None:
    """
    Check whether the subscriber sync is overdue (> 24 hours since last sync).
    Logs a warning if so. Does not trigger any auto-fetch — the dashboard
    surfaces the overdue state via GET /admin/subscribers/stats.
    """
    from database import AsyncSessionLocal
    from models import Subscriber

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(func.max(Subscriber.last_synced_at)))
        last_synced_at = result.scalar_one()

    if last_synced_at is None:
        logger.warning("Subscriber sync overdue: no sync has ever been performed")
        return

    age = datetime.now(timezone.utc) - last_synced_at
    if age > timedelta(hours=24):
        hours = int(age.total_seconds() // 3600)
        logger.warning(
            "Subscriber sync overdue: last sync was %dh ago (threshold: 24h)", hours
        )


async def nullify_stale_phone_numbers() -> None:
    """
    Daily compliance job: nullify the phone field on payments that are
    resolved (completed or auto_resolved) and older than 30 days.
    Phone numbers are PII and are not needed once a payment is processed.
    """
    from database import AsyncSessionLocal
    from models import Payment, PaymentStatus

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            update(Payment)
            .where(
                Payment.status.in_([PaymentStatus.completed, PaymentStatus.auto_resolved]),
                Payment.phone.isnot(None),
                Payment.created_at < cutoff,
            )
            .values(phone=None)
        )
        await db.commit()

    count = result.rowcount
    if count:
        logger.info("Phone nullification: cleared phone from %d payment(s)", count)
    else:
        logger.debug("Phone nullification: no eligible payments")


def start_scheduler() -> None:
    scheduler.add_job(
        check_sync_overdue,
        trigger="interval",
        hours=24,
        id="check_sync_overdue",
        replace_existing=True,
    )
    scheduler.add_job(
        nullify_stale_phone_numbers,
        trigger="interval",
        hours=24,
        id="nullify_stale_phone_numbers",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "APScheduler started — check_sync_overdue and nullify_stale_phone_numbers "
        "run every 24h"
    )


def stop_scheduler() -> None:
    scheduler.shutdown(wait=False)
    logger.info("APScheduler stopped")
