"""
Unit tests for the identity resolution engine.

Pure-tier tests call the internal functions (_tier1_exact_email,
_tier2_fuzzy_name, _tier3_no_match) directly with mock payments and DB.
Orchestration tests call _run_resolution with a DB configured to return
a specific side-effect chain.
"""

import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

# Must be set before any app module is imported
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("RAZORPAY_WEBHOOK_SECRET", "test_webhook_secret")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")
os.environ.setdefault("DASHBOARD_API_KEY", "test_api_key")

from main import app
from database import get_db
from models import Payment, PaymentStatus, Subscriber
from services.email import _build_email
from services.identity import (
    _run_resolution,
    _tier1_exact_email,
    _tier2_fuzzy_name,
    _tier3_no_match,
)

AUTH_HEADERS = {"Authorization": "Bearer test_api_key"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_payment(**kwargs) -> MagicMock:
    p = MagicMock(spec=Payment)
    p.id = kwargs.get("id", uuid4())
    p.email = kwargs.get("email", "payer@example.com")
    p.name = kwargs.get("name", "Test Payer")
    p.amount_inr = kwargs.get("amount_inr", 2000)
    p.status = kwargs.get("status", PaymentStatus.pending)
    p.resolution_notes = kwargs.get("resolution_notes", None)
    return p


def make_subscriber(**kwargs) -> MagicMock:
    s = MagicMock(spec=Subscriber)
    s.email = kwargs.get("email", "subscriber@example.com")
    s.name = kwargs.get("name", "Test Subscriber")
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


def _scalars_list(items: list):
    r = MagicMock()
    r.scalars.return_value.all.return_value = items
    return r


def make_db(*side_effects) -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.execute = AsyncMock(side_effect=list(side_effects))
    return db


# ── Test 1: Tier 1 exact match ────────────────────────────────────────────────

async def test_tier1_exact_match():
    """Matching subscriber sets status=auto_resolved and fires the subscription calculator."""
    payment = make_payment(email="match@example.com")
    subscriber = make_subscriber(email="match@example.com")
    db = make_db(_found(subscriber))

    with patch("services.identity.asyncio.create_task") as mock_task:
        result = await _tier1_exact_email(payment, db)

    assert result is True
    assert payment.status == PaymentStatus.auto_resolved
    db.commit.assert_awaited_once()
    mock_task.assert_called_once()


# ── Test 2: Tier 1 case and whitespace normalisation ─────────────────────────

async def test_tier1_case_and_whitespace():
    """
    Payment email with leading/trailing whitespace and mixed case still
    matches — normalisation happens before the query.
    """
    payment = make_payment(email="  USER@EXAMPLE.COM  ")
    subscriber = make_subscriber(email="user@example.com")
    db = make_db(_found(subscriber))

    with patch("services.identity.asyncio.create_task"):
        result = await _tier1_exact_email(payment, db)

    assert result is True
    assert payment.status == PaymentStatus.auto_resolved


# ── Test 3: Tier 1 excludes deleted subscribers ───────────────────────────────

async def test_tier1_excludes_deleted():
    """
    When the only subscriber matching the email is deleted, the DB query
    (which filters deleted_from_substack=False) returns None — Tier 1 fails.
    """
    payment = make_payment(email="deleted@example.com", status=PaymentStatus.pending)
    # DB returns None, simulating that the matching subscriber is deleted
    db = make_db(_not_found())

    result = await _tier1_exact_email(payment, db)

    assert result is False
    assert payment.status == PaymentStatus.pending  # unchanged
    db.commit.assert_not_awaited()


# ── Test 4: Tier 2 fires only when Tier 1 fails ───────────────────────────────

async def test_tier2_fires_only_when_tier1_fails():
    """
    When Tier 1 finds no email match, Tier 2 runs and sets status=needs_review
    if a fuzzy match above threshold is found.
    """
    payment_obj = make_payment(email="nobody@example.com", name="Priya Sharma")
    subscriber = make_subscriber(name="Priya Sharma")  # token_sort_ratio = 100

    db = make_db(
        _found(payment_obj),         # _run_resolution: fetch payment
        _not_found(),                # _tier1: no email match
        _scalars_list([subscriber]), # _tier2: all non-deleted subscribers
    )

    with patch("services.identity.asyncio.create_task"):
        await _run_resolution(payment_obj.id, db)

    # Tier 2 matched — not Tier 1
    assert payment_obj.status == PaymentStatus.needs_review
    assert "Priya Sharma" in payment_obj.resolution_notes


# ── Test 5: Tier 2 selects highest scoring match ──────────────────────────────

async def test_tier2_selects_highest_scoring_match():
    """
    When multiple subscribers score above the threshold, the one with the
    highest score is recorded in resolution_notes.
    """
    payment = make_payment(name="John Smith", email="noone@example.com")
    high_match = make_subscriber(name="John Smith")    # score = 100
    low_match = make_subscriber(name="Jane Doe")        # score << 85

    db = make_db(_scalars_list([high_match, low_match]))

    result = await _tier2_fuzzy_name(payment, db)

    assert result is True
    assert payment.status == PaymentStatus.needs_review
    assert "John Smith" in payment.resolution_notes
    assert "Jane Doe" not in payment.resolution_notes


# ── Test 6: Tier 2 falls through when no score meets threshold ────────────────

async def test_tier2_falls_through_below_threshold():
    """
    When the best fuzzy score is below 85, Tier 2 returns False and
    _run_resolution falls through to Tier 3 (status=unknown).
    """
    payment_obj = make_payment(email="noone@example.com", name="Xyz Qrst Uvwxyz")
    low_match = make_subscriber(name="Jane Doe")  # very low score

    db = make_db(
        _found(payment_obj),          # fetch payment
        _not_found(),                 # Tier 1: no email match
        _scalars_list([low_match]),   # Tier 2: score < 85
    )

    with patch(
        "services.identity.send_clarification_email",
        new_callable=AsyncMock,
        return_value=True,
    ):
        await _run_resolution(payment_obj.id, db)

    assert payment_obj.status == PaymentStatus.unknown


# ── Test 7: Tier 3 fires when both previous tiers fail ────────────────────────

async def test_tier3_fires_when_both_tiers_fail():
    """
    _tier3_no_match sets status=unknown, inserts a ClarificationEmail row,
    and commits.
    """
    payment = make_payment(email="ghost@example.com", name="Xyz Qrst")
    db = make_db()  # no execute calls needed — _tier3 only uses add/commit

    with patch(
        "services.identity.send_clarification_email",
        new_callable=AsyncMock,
        return_value=True,
    ):
        await _tier3_no_match(payment, db)

    assert payment.status == PaymentStatus.unknown
    db.add.assert_called_once()   # ClarificationEmail row
    db.commit.assert_awaited_once()


# ── Test 8: Clarification email content ──────────────────────────────────────

async def test_clarification_email_content():
    """
    _build_email composes correct subject and body with all required fields.
    """
    payment = make_payment(
        name="Priya Sharma",
        email="priya@example.com",
        amount_inr=2000,
    )

    subject, body = _build_email(payment)

    assert subject == "Action needed: activate your Wire subscription"
    assert "Priya Sharma" in body
    assert "INR 2000" in body
    assert "priya@example.com" in body
    assert "The Wire team" in body


# ── Test 9: Manual resolution validates subscriber ───────────────────────────

async def test_manual_resolution_validates_subscriber():
    """
    POST /admin/resolve-payment/{id} returns 404 when the provided
    subscriber_email does not exist in the subscribers table.
    """
    payment_obj = make_payment()

    # Payment found, subscriber not found
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _found(payment_obj),  # payment lookup
        _not_found(),         # subscriber lookup
    ])

    async def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/admin/resolve-payment/{payment_obj.id}",
                json={"subscriber_email": "nothere@example.com", "override": True},
                headers=AUTH_HEADERS,
            )
        assert response.status_code == 404
        assert "Subscriber" in response.json()["detail"]
    finally:
        app.dependency_overrides.pop(get_db, None)
