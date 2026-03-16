import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_api_key
from database import get_db
from models import Action, ExecutionStatus, Payment, PaymentStatus, Subscriber, SubstackStatus
from schemas import ActionRead, PaymentRead
from services.subscriber_sync import process_csv
from services.subscription import calculate_subscription
from services.substack import execute_substack_action

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


class ResolvePaymentRequest(BaseModel):
    subscriber_email: str
    override: bool = True


@router.post("/sync-subscribers")
async def sync_subscribers(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    api_key: str = Depends(require_api_key),
):
    content = (await file.read()).decode("utf-8")
    return await process_csv(content, db)


@router.get("/subscribers/stats")
async def subscribers_stats(
    db: AsyncSession = Depends(get_db),
    api_key: str = Depends(require_api_key),
):
    total_r = await db.execute(select(func.count()).select_from(Subscriber))
    total = total_r.scalar_one()

    active_r = await db.execute(
        select(func.count()).select_from(Subscriber).where(
            Subscriber.substack_status == SubstackStatus.active,
            Subscriber.deleted_from_substack == False,  # noqa: E712
        )
    )
    active = active_r.scalar_one()

    lapsed_r = await db.execute(
        select(func.count()).select_from(Subscriber).where(
            Subscriber.substack_status == SubstackStatus.lapsed,
            Subscriber.deleted_from_substack == False,  # noqa: E712
        )
    )
    lapsed = lapsed_r.scalar_one()

    lifetime_r = await db.execute(
        select(func.count()).select_from(Subscriber).where(
            Subscriber.substack_status == SubstackStatus.lifetime,
            Subscriber.deleted_from_substack == False,  # noqa: E712
        )
    )
    lifetime = lifetime_r.scalar_one()

    deleted_r = await db.execute(
        select(func.count()).select_from(Subscriber).where(
            Subscriber.deleted_from_substack == True,  # noqa: E712
        )
    )
    deleted = deleted_r.scalar_one()

    last_synced_r = await db.execute(select(func.max(Subscriber.last_synced_at)))
    last_synced_at = last_synced_r.scalar_one()

    sync_overdue = (
        last_synced_at is None
        or (datetime.now(timezone.utc) - last_synced_at) > timedelta(hours=24)
    )

    return {
        "total": total,
        "active": active,
        "lapsed": lapsed,
        "lifetime": lifetime,
        "deleted": deleted,
        "last_synced_at": last_synced_at.isoformat() if last_synced_at else None,
        "sync_overdue": sync_overdue,
    }


@router.post("/resolve-payment/{payment_id}", response_model=PaymentRead)
async def resolve_payment(
    payment_id: UUID,
    body: ResolvePaymentRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    api_key: str = Depends(require_api_key),
):
    payment_r = await db.execute(select(Payment).where(Payment.id == payment_id))
    payment = payment_r.scalar_one_or_none()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    sub_r = await db.execute(
        select(Subscriber).where(
            func.lower(Subscriber.email) == body.subscriber_email.strip().lower(),
            Subscriber.deleted_from_substack == False,  # noqa: E712
        )
    )
    subscriber = sub_r.scalar_one_or_none()
    if not subscriber:
        raise HTTPException(status_code=404, detail="Subscriber not found or deleted")

    payment.status = PaymentStatus.auto_resolved
    await db.commit()
    await db.refresh(payment)

    background_tasks.add_task(calculate_subscription, payment.id)

    return PaymentRead.model_validate(payment)


@router.post("/retry-action/{action_id}", response_model=ActionRead)
async def retry_action(
    action_id: UUID,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    api_key: str = Depends(require_api_key),
):
    result = await db.execute(select(Action).where(Action.id == action_id))
    action = result.scalar_one_or_none()
    if not action:
        raise HTTPException(status_code=404, detail="Action not found")

    action.execution_status = ExecutionStatus.pending
    action.failure_reason = None
    await db.commit()
    await db.refresh(action)

    background_tasks.add_task(execute_substack_action, action.id)

    return ActionRead.model_validate(action)
