"""
Unit tests for the Subscription State Calculator.

All date-sensitive tests patch services.subscription.date so that
date.today() returns the fixed anchor date 2025-06-15, making
remaining_days arithmetic deterministic.
"""

import os
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("RAZORPAY_WEBHOOK_SECRET", "test_webhook_secret")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")
os.environ.setdefault("DASHBOARD_API_KEY", "test_api_key")

from models import Action, ExecutionStatus, Payment, PaymentStatus, Subscriber, SubstackStatus
from services.subscription import _run_calculation

# Fixed anchor date used in all date-sensitive tests
TODAY = date(2025, 6, 15)


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_payment(**kwargs) -> MagicMock:
    p = MagicMock(spec=Payment)
    p.id = kwargs.get("id", uuid4())
    p.email = kwargs.get("email", "payer@example.com")
    p.name = kwargs.get("name", "Test Payer")
    p.amount_inr = kwargs.get("amount_inr", 2000)
    p.status = kwargs.get("status", PaymentStatus.auto_resolved)
    p.resolution_notes = kwargs.get("resolution_notes", None)
    return p


def make_subscriber(**kwargs) -> MagicMock:
    s = MagicMock(spec=Subscriber)
    s.email = kwargs.get("email", "payer@example.com")
    s.name = kwargs.get("name", "Test Payer")
    s.substack_status = kwargs.get("substack_status", SubstackStatus.active)
    s.expiry_date = kwargs.get("expiry_date", None)
    s.deleted_from_substack = kwargs.get("deleted_from_substack", False)
    return s


def _found(obj):
    r = MagicMock()
    r.scalar_one_or_none.return_value = obj
    return r


def _not_found():
    r = MagicMock()
    r.scalar_one_or_none.return_value = None
    return r


def make_db(*side_effects) -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.execute = AsyncMock(side_effect=list(side_effects))
    return db


# ── Test 1: Unrecognised amount ───────────────────────────────────────────────

async def test_unrecognised_amount_sets_needs_review():
    """
    An amount not in the map sets needs_review with the exact note and
    does NOT create an action record.
    """
    payment = make_payment(amount_inr=500)
    db = make_db(_found(payment))  # only the payment fetch runs

    await _run_calculation(payment.id, db)

    assert payment.status == PaymentStatus.needs_review
    assert payment.resolution_notes == "Unrecognised payment amount: INR 500"
    db.add.assert_not_called()
    db.commit.assert_awaited_once()


# ── Test 2: INR 200, subscriber not found (new subscriber) ───────────────────

async def test_200_new_subscriber():
    """INR 200 with no matching subscriber → comp_days=30, is_lifetime=False."""
    payment = make_payment(amount_inr=200)
    db = make_db(_found(payment), _not_found())

    with patch("services.subscription.asyncio.create_task"):
        await _run_calculation(payment.id, db)

    assert payment.status == PaymentStatus.completed
    added_action = db.add.call_args[0][0]
    assert added_action.comp_days == 30
    assert added_action.is_lifetime is False
    assert added_action.execution_status == ExecutionStatus.pending


# ── Test 3: INR 2000, lapsed subscriber ───────────────────────────────────────

async def test_2000_lapsed_subscriber():
    """INR 2000 with a lapsed subscriber → comp_days=365."""
    payment = make_payment(amount_inr=2000)
    subscriber = make_subscriber(substack_status=SubstackStatus.lapsed)
    db = make_db(_found(payment), _found(subscriber))

    with patch("services.subscription.asyncio.create_task"):
        await _run_calculation(payment.id, db)

    added_action = db.add.call_args[0][0]
    assert added_action.comp_days == 365
    assert added_action.is_lifetime is False


# ── Test 4: INR 10000, new subscriber → lifetime ─────────────────────────────

async def test_10000_new_subscriber_lifetime():
    """INR 10000 with no matching subscriber → is_lifetime=True, comp_days=None."""
    payment = make_payment(amount_inr=10000)
    db = make_db(_found(payment), _not_found())

    with patch("services.subscription.asyncio.create_task"):
        await _run_calculation(payment.id, db)

    added_action = db.add.call_args[0][0]
    assert added_action.is_lifetime is True
    assert added_action.comp_days is None


# ── Test 5: Lifetime subscriber pays INR 10000 ────────────────────────────────

async def test_lifetime_subscriber_pays_10000():
    """Lifetime subscriber paying INR 10000 → needs_review, specific note."""
    payment = make_payment(amount_inr=10000)
    subscriber = make_subscriber(substack_status=SubstackStatus.lifetime)
    db = make_db(_found(payment), _found(subscriber))

    await _run_calculation(payment.id, db)

    assert payment.status == PaymentStatus.needs_review
    assert payment.resolution_notes == (
        "Existing lifetime subscriber made a payment — verify intent"
    )
    db.add.assert_not_called()


# ── Test 6: Lifetime subscriber pays INR 200 ─────────────────────────────────

async def test_lifetime_subscriber_pays_200():
    """Lifetime subscriber paying INR 200 → needs_review with renewal note."""
    payment = make_payment(amount_inr=200)
    subscriber = make_subscriber(substack_status=SubstackStatus.lifetime)
    db = make_db(_found(payment), _found(subscriber))

    await _run_calculation(payment.id, db)

    assert payment.status == PaymentStatus.needs_review
    assert payment.resolution_notes == (
        "Lifetime subscriber made renewal payment — verify intent"
    )
    db.add.assert_not_called()


# ── Test 7: Lifetime subscriber pays INR 2000 ────────────────────────────────

async def test_lifetime_subscriber_pays_2000():
    """Lifetime subscriber paying INR 2000 → same renewal note as INR 200."""
    payment = make_payment(amount_inr=2000)
    subscriber = make_subscriber(substack_status=SubstackStatus.lifetime)
    db = make_db(_found(payment), _found(subscriber))

    await _run_calculation(payment.id, db)

    assert payment.status == PaymentStatus.needs_review
    assert payment.resolution_notes == (
        "Lifetime subscriber made renewal payment — verify intent"
    )
    db.add.assert_not_called()


# ── Test 8: Active subscriber, future expiry, INR 200 ────────────────────────

async def test_active_future_expiry_200():
    """
    Active subscriber with expiry 2025-07-15 (30 days from anchor) pays INR 200
    → remaining=30, comp_days=30+30=60.
    """
    payment = make_payment(amount_inr=200)
    subscriber = make_subscriber(
        substack_status=SubstackStatus.active,
        expiry_date=date(2025, 7, 15),
    )
    db = make_db(_found(payment), _found(subscriber))

    with patch("services.subscription.date") as mock_date:
        mock_date.today.return_value = TODAY
        with patch("services.subscription.asyncio.create_task"):
            await _run_calculation(payment.id, db)

    added_action = db.add.call_args[0][0]
    assert added_action.comp_days == 60  # 30 remaining + 30 mapped
    assert added_action.is_lifetime is False


# ── Test 9: Active subscriber, expiry = today → lapsed ───────────────────────

async def test_active_expiry_today_treated_as_lapsed():
    """
    Active subscriber whose expiry_date equals today (remaining=0)
    is treated as lapsed → comp_days=30.
    """
    payment = make_payment(amount_inr=200)
    subscriber = make_subscriber(
        substack_status=SubstackStatus.active,
        expiry_date=TODAY,
    )
    db = make_db(_found(payment), _found(subscriber))

    with patch("services.subscription.date") as mock_date:
        mock_date.today.return_value = TODAY
        with patch("services.subscription.asyncio.create_task"):
            await _run_calculation(payment.id, db)

    added_action = db.add.call_args[0][0]
    assert added_action.comp_days == 30


# ── Test 10: Active subscriber, expiry in past → lapsed ──────────────────────

async def test_active_expiry_in_past_treated_as_lapsed():
    """
    Active subscriber whose expiry_date is 2025-06-01 (14 days before anchor)
    is treated as lapsed → comp_days=30.
    """
    payment = make_payment(amount_inr=200)
    subscriber = make_subscriber(
        substack_status=SubstackStatus.active,
        expiry_date=date(2025, 6, 1),
    )
    db = make_db(_found(payment), _found(subscriber))

    with patch("services.subscription.date") as mock_date:
        mock_date.today.return_value = TODAY
        with patch("services.subscription.asyncio.create_task"):
            await _run_calculation(payment.id, db)

    added_action = db.add.call_args[0][0]
    assert added_action.comp_days == 30


# ── Test 11: Active subscriber, future expiry, INR 10000 → lifetime ──────────

async def test_active_future_expiry_10000_lifetime():
    """
    Active subscriber with future expiry paying INR 10000
    → is_lifetime=True, comp_days=None (remaining days ignored).
    """
    payment = make_payment(amount_inr=10000)
    subscriber = make_subscriber(
        substack_status=SubstackStatus.active,
        expiry_date=date(2025, 7, 15),
    )
    db = make_db(_found(payment), _found(subscriber))

    with patch("services.subscription.date") as mock_date:
        mock_date.today.return_value = TODAY
        with patch("services.subscription.asyncio.create_task"):
            await _run_calculation(payment.id, db)

    added_action = db.add.call_args[0][0]
    assert added_action.is_lifetime is True
    assert added_action.comp_days is None


# ── Test 12: Deleted subscriber ───────────────────────────────────────────────

async def test_deleted_subscriber_routes_to_needs_review():
    """Deleted subscriber → needs_review with exact note, no action record."""
    payment = make_payment(amount_inr=2000)
    subscriber = make_subscriber(deleted_from_substack=True)
    db = make_db(_found(payment), _found(subscriber))

    await _run_calculation(payment.id, db)

    assert payment.status == PaymentStatus.needs_review
    assert payment.resolution_notes == (
        "Subscriber marked as deleted from Substack — verify"
    )
    db.add.assert_not_called()


# ── Test 13: Payment not found ────────────────────────────────────────────────

async def test_payment_not_found_returns_cleanly():
    """Missing payment ID logs an error and returns without touching anything."""
    db = make_db(_not_found())

    await _run_calculation(uuid4(), db)  # random ID, not in DB

    db.add.assert_not_called()
    db.commit.assert_not_awaited()


# ── Test 14: Payment status set to completed ─────────────────────────────────

async def test_payment_status_completed_on_success():
    """A successful calculation sets payment.status = completed."""
    payment = make_payment(amount_inr=2000)
    subscriber = make_subscriber(substack_status=SubstackStatus.lapsed)
    db = make_db(_found(payment), _found(subscriber))

    with patch("services.subscription.asyncio.create_task"):
        await _run_calculation(payment.id, db)

    assert payment.status == PaymentStatus.completed


# ── Test 15: Substack Executor is triggered ───────────────────────────────────

async def test_substack_executor_triggered_on_success():
    """asyncio.create_task is called once after a successful action creation."""
    payment = make_payment(amount_inr=200)
    db = make_db(_found(payment), _not_found())

    with patch("services.subscription.asyncio.create_task") as mock_task:
        await _run_calculation(payment.id, db)

    mock_task.assert_called_once()
