import csv
import io
import logging
import os
from datetime import date as date_type, datetime, timedelta, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth import require_api_key
from database import get_db
from models import (
    Action,
    ClarificationEmail,
    ExecutionStatus,
    Payment,
    PaymentStatus,
    Setting,
    Subscriber,
)
from schemas import ActionRead, PaymentRead, SubscriberRead
from services.subscription import calculate_subscription

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


# ── Request bodies ────────────────────────────────────────────────────────────

class ApproveRequest(BaseModel):
    subscriber_email: str


class RejectRequest(BaseModel):
    notes: str


# ── GET /dashboard/summary ────────────────────────────────────────────────────

@router.get("/summary")
async def summary(
    db: AsyncSession = Depends(get_db),
    api_key: str = Depends(require_api_key),
):
    today_start = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    payments_today_r = await db.execute(
        select(func.count()).select_from(Payment).where(
            Payment.payment_timestamp >= today_start
        )
    )
    payments_today = payments_today_r.scalar_one()

    auto_resolved_today_r = await db.execute(
        select(func.count()).select_from(Payment).where(
            Payment.payment_timestamp >= today_start,
            Payment.status == PaymentStatus.auto_resolved,
        )
    )
    auto_resolved_today = auto_resolved_today_r.scalar_one()

    pending_review_r = await db.execute(
        select(func.count()).select_from(Payment).where(
            Payment.status == PaymentStatus.needs_review
        )
    )
    pending_review = pending_review_r.scalar_one()

    unknown_r = await db.execute(
        select(func.count()).select_from(Payment).where(
            Payment.status == PaymentStatus.unknown
        )
    )
    unknown = unknown_r.scalar_one()

    failed_actions_r = await db.execute(
        select(func.count()).select_from(Action).where(
            Action.execution_status == ExecutionStatus.failed
        )
    )
    failed_actions = failed_actions_r.scalar_one()

    last_synced_r = await db.execute(select(func.max(Subscriber.last_synced_at)))
    last_synced_at = last_synced_r.scalar_one()

    sync_overdue = last_synced_at is None or (
        datetime.now(timezone.utc) - last_synced_at
    ) > timedelta(hours=24)

    cookie_r = await db.execute(
        select(Setting).where(Setting.key == "substack_cookie_expired")
    )
    cookie_setting = cookie_r.scalar_one_or_none()
    cookie_expired = cookie_setting is not None and cookie_setting.value == "true"

    return {
        "payments_today": payments_today,
        "auto_resolved_today": auto_resolved_today,
        "pending_review": pending_review,
        "unknown": unknown,
        "failed_actions": failed_actions,
        "last_subscriber_sync": last_synced_at.isoformat() if last_synced_at else None,
        "sync_overdue": sync_overdue,
        "cookie_expired": cookie_expired,
    }


# ── GET /dashboard/pending ────────────────────────────────────────────────────

@router.get("/pending")
async def pending(
    db: AsyncSession = Depends(get_db),
    api_key: str = Depends(require_api_key),
):
    payments_r = await db.execute(
        select(Payment).where(
            Payment.status.in_([PaymentStatus.needs_review, PaymentStatus.unknown])
        ).order_by(Payment.payment_timestamp.desc())
    )
    payments = payments_r.scalars().all()

    if not payments:
        return []

    payment_ids = [p.id for p in payments]

    # Batch-fetch clarification emails for all pending payments
    clar_r = await db.execute(
        select(ClarificationEmail).where(
            ClarificationEmail.payment_id.in_(payment_ids)
        )
    )
    clar_by_payment: dict = {c.payment_id: c for c in clar_r.scalars().all()}

    # Batch-fetch suggested match subscribers
    match_emails = [
        p.suggested_match_email
        for p in payments
        if p.suggested_match_email
    ]
    match_subscribers: dict = {}
    if match_emails:
        subs_r = await db.execute(
            select(Subscriber).where(Subscriber.email.in_(match_emails))
        )
        for sub in subs_r.scalars().all():
            match_subscribers[sub.email] = sub

    result = []
    for p in payments:
        clar = clar_by_payment.get(p.id)
        sub = match_subscribers.get(p.suggested_match_email) if p.suggested_match_email else None

        suggested_match = None
        if sub:
            suggested_match = {
                "email": sub.email,
                "name": sub.name,
                "substack_status": sub.substack_status,
                "expiry_date": sub.expiry_date.isoformat() if sub.expiry_date else None,
                "confidence_score": p.suggested_match_score,
            }

        result.append({
            "payment_id": str(p.id),
            "razorpay_payment_id": p.razorpay_payment_id,
            "email": p.email,
            "name": p.name,
            "amount_inr": p.amount_inr,
            "payment_timestamp": p.payment_timestamp.isoformat(),
            "status": p.status,
            "resolution_notes": p.resolution_notes,
            "clarification_sent": clar is not None,
            "clarification_resolved": clar.resolved if clar else False,
            "suggested_match": suggested_match,
        })

    return result


# ── POST /dashboard/approve/{payment_id} ──────────────────────────────────────

@router.post("/approve/{payment_id}", response_model=PaymentRead)
async def approve_payment(
    payment_id: UUID,
    body: ApproveRequest,
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


# ── POST /dashboard/reject/{payment_id} ───────────────────────────────────────

@router.post("/reject/{payment_id}", response_model=PaymentRead)
async def reject_payment(
    payment_id: UUID,
    body: RejectRequest,
    db: AsyncSession = Depends(get_db),
    api_key: str = Depends(require_api_key),
):
    payment_r = await db.execute(select(Payment).where(Payment.id == payment_id))
    payment = payment_r.scalar_one_or_none()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    payment.status = PaymentStatus.failed
    payment.resolution_notes = body.notes
    await db.commit()
    await db.refresh(payment)

    return PaymentRead.model_validate(payment)


# ── GET /dashboard/subscribers/search ────────────────────────────────────────

@router.get("/subscribers/search")
async def search_subscribers(
    q: str = Query(..., min_length=2),
    db: AsyncSession = Depends(get_db),
    api_key: str = Depends(require_api_key),
):
    pattern = f"%{q}%"
    result = await db.execute(
        select(Subscriber).where(
            or_(
                Subscriber.email.ilike(pattern),
                Subscriber.name.ilike(pattern),
            ),
            Subscriber.deleted_from_substack == False,  # noqa: E712
        ).limit(10)
    )
    subscribers = result.scalars().all()
    return [SubscriberRead.model_validate(s) for s in subscribers]


# ── GET /dashboard/log ────────────────────────────────────────────────────────

@router.get("/log")
async def action_log(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    status: Optional[ExecutionStatus] = None,
    email: Optional[str] = None,
    date_from: Optional[date_type] = None,
    date_to: Optional[date_type] = None,
    db: AsyncSession = Depends(get_db),
    api_key: str = Depends(require_api_key),
):
    def _apply_filters(q):
        if status:
            q = q.where(Action.execution_status == status)
        if email:
            q = q.where(Action.subscriber_email.ilike(f"%{email}%"))
        if date_from:
            dt_from = datetime(date_from.year, date_from.month, date_from.day, tzinfo=timezone.utc)
            q = q.where(Action.created_at >= dt_from)
        if date_to:
            dt_to = datetime(date_to.year, date_to.month, date_to.day, tzinfo=timezone.utc) + timedelta(days=1)
            q = q.where(Action.created_at < dt_to)
        return q

    count_q = _apply_filters(select(func.count(Action.id)))
    total_r = await db.execute(count_q)
    total = total_r.scalar_one()

    data_q = _apply_filters(
        select(Action, Payment).join(Payment, Action.payment_id == Payment.id)
    ).order_by(Action.created_at.desc()).offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(data_q)
    rows = result.all()

    items = []
    for action, payment in rows:
        item = ActionRead.model_validate(action).model_dump()
        item["payment"] = {
            "razorpay_payment_id": payment.razorpay_payment_id,
            "email": payment.email,
            "name": payment.name,
            "amount_inr": payment.amount_inr,
            "payment_timestamp": payment.payment_timestamp.isoformat(),
        }
        items.append(item)

    return {
        "items": items,
        "total": total,
        "page": page,
        "pages": max(1, (total + page_size - 1) // page_size),
    }


# ── GET /dashboard/log/export ─────────────────────────────────────────────────

@router.get("/log/export")
async def export_log(
    status: Optional[ExecutionStatus] = None,
    email: Optional[str] = None,
    date_from: Optional[date_type] = None,
    date_to: Optional[date_type] = None,
    db: AsyncSession = Depends(get_db),
    api_key: str = Depends(require_api_key),
):
    query = select(Action, Payment).join(Payment, Action.payment_id == Payment.id)

    if status:
        query = query.where(Action.execution_status == status)
    if email:
        query = query.where(Action.subscriber_email.ilike(f"%{email}%"))
    if date_from:
        query = query.where(Action.created_at >= datetime(date_from.year, date_from.month, date_from.day, tzinfo=timezone.utc))
    if date_to:
        query = query.where(Action.created_at < datetime(date_to.year, date_to.month, date_to.day, tzinfo=timezone.utc) + timedelta(days=1))

    query = query.order_by(Action.created_at.desc())
    result = await db.execute(query)
    rows = result.all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "timestamp",
        "payer_name",
        "payer_email",
        "subscriber_email",
        "amount_inr",
        "comp_days",
        "is_lifetime",
        "execution_status",
        "executed_at",
        "razorpay_payment_id",
    ])

    for action, payment in rows:
        writer.writerow([
            action.created_at.isoformat(),
            payment.name,
            payment.email,
            action.subscriber_email,
            payment.amount_inr,
            action.comp_days if action.comp_days is not None else "",
            action.is_lifetime,
            action.execution_status,
            action.executed_at.isoformat() if action.executed_at else "",
            payment.razorpay_payment_id,
        ])

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=action_log.csv"},
    )


# ── GET /dashboard/failed ─────────────────────────────────────────────────────

@router.get("/failed")
async def failed_actions(
    db: AsyncSession = Depends(get_db),
    api_key: str = Depends(require_api_key),
):
    result = await db.execute(
        select(Action).where(
            Action.execution_status == ExecutionStatus.failed
        ).order_by(Action.created_at.desc())
    )
    actions = result.scalars().all()
    return [ActionRead.model_validate(a) for a in actions]


# ── GET /dashboard/settings ───────────────────────────────────────────────────

@router.get("/settings")
async def settings(
    db: AsyncSession = Depends(get_db),
    api_key: str = Depends(require_api_key),
):
    last_synced_r = await db.execute(select(func.max(Subscriber.last_synced_at)))
    last_synced_at = last_synced_r.scalar_one()

    sync_overdue = last_synced_at is None or (
        datetime.now(timezone.utc) - last_synced_at
    ) > timedelta(hours=24)

    cookie_r = await db.execute(
        select(Setting).where(Setting.key == "substack_cookie_expired")
    )
    cookie_setting = cookie_r.scalar_one_or_none()
    cookie_expired = cookie_setting is not None and cookie_setting.value == "true"

    total_r = await db.execute(
        select(func.count()).select_from(Subscriber).where(
            Subscriber.deleted_from_substack == False  # noqa: E712
        )
    )
    total_subscribers = total_r.scalar_one()

    return {
        "last_sync_timestamp": last_synced_at.isoformat() if last_synced_at else None,
        "sync_overdue": sync_overdue,
        "cookie_expired": cookie_expired,
        "total_subscribers": total_subscribers,
        "environment": os.getenv("ENVIRONMENT", "production"),
    }
