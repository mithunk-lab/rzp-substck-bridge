"""
Unit tests for the Razorpay webhook listener.

The database is mocked via FastAPI dependency override so these tests
run without a live database. BackgroundTasks are verified by patching
services.identity.resolve_identity — Starlette's ASGITransport runs
background tasks within the same await, so the mock is called before
client.post() returns.
"""

import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

# Environment must be set before any app module is imported
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("RAZORPAY_WEBHOOK_SECRET", "test_webhook_secret")
os.environ.setdefault("ENVIRONMENT", "development")
os.environ.setdefault("FRONTEND_URL", "http://localhost:3000")

import pytest
from httpx import ASGITransport, AsyncClient

from main import app
from database import get_db
from models import Payment, PaymentStatus

TEST_SECRET = "test_webhook_secret"


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_signature(body: bytes, secret: str = TEST_SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def make_razorpay_payload(
    payment_id: str = "pay_test_001",
    email: str = "payment@example.com",
    notes_email: str = None,
    notes_name: str = None,
    amount_paise: int = 200_000,  # INR 2000
    event: str = "payment.captured",
) -> dict:
    notes = {}
    if notes_email:
        notes["email"] = notes_email
    if notes_name:
        notes["name"] = notes_name

    return {
        "event": event,
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "amount": amount_paise,
                    "email": email,
                    "contact": "+919876543210",
                    "description": "The Wire Subscription",
                    "notes": notes,
                    "created_at": int(datetime.now(timezone.utc).timestamp()),
                }
            }
        },
    }


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_db():
    """AsyncMock database session with sensible defaults for the happy path."""
    db = AsyncMock()

    # Default idempotency check: no existing payment
    no_match = MagicMock()
    no_match.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=no_match)

    db.add = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    # refresh: stamp a UUID and created_at on whatever object is passed
    async def _refresh(obj):
        if not getattr(obj, "id", None):
            obj.id = uuid4()
        if not getattr(obj, "created_at", None):
            obj.created_at = datetime.now(timezone.utc)

    db.refresh = _refresh
    return db


@pytest.fixture(autouse=True)
def override_db(mock_db):
    """Override the get_db dependency for every test in this module."""
    async def _get_db():
        yield mock_db

    app.dependency_overrides[get_db] = _get_db
    yield mock_db
    app.dependency_overrides.clear()


# ── Tests ─────────────────────────────────────────────────────────────────────

async def test_valid_signature_proceeds_to_db_write(override_db):
    """A correctly signed payload is processed and written to the database."""
    payload = make_razorpay_payload()
    body = json.dumps(payload).encode()
    sig = make_signature(body)

    with patch("routers.webhooks.resolve_identity"):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/webhooks/razorpay",
                content=body,
                headers={"X-Razorpay-Signature": sig, "Content-Type": "application/json"},
            )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    # Signature passed → DB was touched
    override_db.add.assert_called_once()
    override_db.commit.assert_awaited_once()


async def test_invalid_signature_returns_200_and_is_logged(override_db):
    """
    A bad signature must return 200 (not 401) to prevent Razorpay from
    retrying requests from bad actors. The failure must also be logged.
    """
    payload = make_razorpay_payload()
    body = json.dumps(payload).encode()

    with patch("routers.webhooks.logger") as mock_logger:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/webhooks/razorpay",
                content=body,
                headers={
                    "X-Razorpay-Signature": "this_is_not_a_valid_signature",
                    "Content-Type": "application/json",
                },
            )

    # Must return 200 — not 401
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    # Failure must be logged (warning level)
    mock_logger.warning.assert_called_once()
    logged = str(mock_logger.warning.call_args)
    assert "signature" in logged.lower() or "verification" in logged.lower()

    # Database must not be touched
    override_db.add.assert_not_called()


async def test_duplicate_payment_id_skipped_and_200(override_db):
    """
    A payment ID that already exists in the database must be skipped,
    logged as a duplicate, and still return 200.
    """
    existing = MagicMock()
    existing.scalar_one_or_none.return_value = MagicMock()  # simulates a found row
    override_db.execute = AsyncMock(return_value=existing)

    payload = make_razorpay_payload(payment_id="pay_already_exists")
    body = json.dumps(payload).encode()
    sig = make_signature(body)

    with patch("routers.webhooks.logger") as mock_logger:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/webhooks/razorpay",
                content=body,
                headers={"X-Razorpay-Signature": sig, "Content-Type": "application/json"},
            )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}

    # No second write
    override_db.add.assert_not_called()

    # "duplicate payment ID skipped" logged
    logged = str(mock_logger.info.call_args_list)
    assert "duplicate" in logged.lower()


async def test_email_extracted_from_notes_field(override_db):
    """When payment.notes.email is present it takes priority over payment.email."""
    payload = make_razorpay_payload(
        notes_email="from_notes@example.com",
        email="from_payment_field@example.com",
    )
    body = json.dumps(payload).encode()
    sig = make_signature(body)

    captured: list[Payment] = []
    override_db.add = lambda obj: captured.append(obj)

    with patch("routers.webhooks.resolve_identity"):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(
                "/webhooks/razorpay",
                content=body,
                headers={"X-Razorpay-Signature": sig, "Content-Type": "application/json"},
            )

    assert len(captured) == 1
    assert captured[0].email == "from_notes@example.com"


async def test_email_extracted_from_payment_field(override_db):
    """When notes has no email, payment.email is used as the fallback."""
    payload = make_razorpay_payload(
        notes_email=None,           # no email in notes
        email="from_payment@example.com",
    )
    body = json.dumps(payload).encode()
    sig = make_signature(body)

    captured: list[Payment] = []
    override_db.add = lambda obj: captured.append(obj)

    with patch("routers.webhooks.resolve_identity"):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(
                "/webhooks/razorpay",
                content=body,
                headers={"X-Razorpay-Signature": sig, "Content-Type": "application/json"},
            )

    assert len(captured) == 1
    assert captured[0].email == "from_payment@example.com"


async def test_database_write_success(override_db):
    """
    On the happy path, exactly one Payment row is written with:
    - status = pending
    - correct razorpay_payment_id
    - amount converted from paise to INR
    """
    payload = make_razorpay_payload(
        payment_id="pay_happy_path",
        amount_paise=200_000,  # INR 2000
    )
    body = json.dumps(payload).encode()
    sig = make_signature(body)

    with patch("routers.webhooks.resolve_identity"):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post(
                "/webhooks/razorpay",
                content=body,
                headers={"X-Razorpay-Signature": sig, "Content-Type": "application/json"},
            )

    assert response.status_code == 200
    override_db.add.assert_called_once()
    override_db.commit.assert_awaited_once()

    written: Payment = override_db.add.call_args[0][0]
    assert written.razorpay_payment_id == "pay_happy_path"
    assert written.status == PaymentStatus.pending
    assert written.amount_inr == 2000          # 200_000 paise ÷ 100
    assert written.phone == "+919876543210"


async def test_background_task_triggered_on_success(override_db):
    """
    After a successful DB write, resolve_identity must be enqueued
    as a BackgroundTask. Starlette's ASGITransport runs background tasks
    within the same await so the mock is called before client.post returns.
    """
    payload = make_razorpay_payload(payment_id="pay_bg_task_test")
    body = json.dumps(payload).encode()
    sig = make_signature(body)

    with patch("routers.webhooks.resolve_identity") as mock_resolve:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(
                "/webhooks/razorpay",
                content=body,
                headers={"X-Razorpay-Signature": sig, "Content-Type": "application/json"},
            )

    mock_resolve.assert_called_once()
    # First positional arg is payment_id (a UUID)
    called_with_payment_id = mock_resolve.call_args[0][0]
    assert called_with_payment_id is not None
