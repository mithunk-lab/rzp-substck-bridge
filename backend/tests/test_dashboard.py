"""
Unit tests for the dashboard API endpoints.
All DB operations are mocked — no live database required.
"""
import uuid
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from main import app
from models import (
    Action,
    ClarificationEmail,
    ExecutionStatus,
    Payment,
    PaymentStatus,
    Setting,
    Subscriber,
    SubstackStatus,
)
from database import get_db


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("DASHBOARD_API_KEY", "test-key")
    monkeypatch.setenv("ENVIRONMENT", "development")


AUTH = {"Authorization": "Bearer test-key"}
BASE = "http://test"


def _client(mock_db):
    async def override():
        yield mock_db

    app.dependency_overrides[get_db] = override
    return AsyncClient(transport=ASGITransport(app=app), base_url=BASE)


def _scalar(value):
    m = MagicMock()
    m.scalar_one.return_value = value
    return m


def _scalar_opt(value):
    m = MagicMock()
    m.scalar_one_or_none.return_value = value
    return m


def _scalars(values):
    m = MagicMock()
    inner = MagicMock()
    inner.all.return_value = values
    m.scalars.return_value = inner
    return m


def make_payment(**kwargs) -> MagicMock:
    p = MagicMock(spec=Payment)
    p.id = kwargs.get("id", uuid.uuid4())
    p.razorpay_payment_id = kwargs.get("razorpay_payment_id", "pay_test")
    p.email = kwargs.get("email", "payer@example.com")
    p.name = kwargs.get("name", "Test Payer")
    p.amount_inr = kwargs.get("amount_inr", 200)
    p.payment_timestamp = kwargs.get(
        "payment_timestamp", datetime.now(timezone.utc)
    )
    p.status = kwargs.get("status", PaymentStatus.needs_review)
    p.resolution_notes = kwargs.get("resolution_notes", None)
    p.suggested_match_email = kwargs.get("suggested_match_email", None)
    p.suggested_match_score = kwargs.get("suggested_match_score", None)
    p.created_at = kwargs.get("created_at", datetime.now(timezone.utc))
    return p


def make_action(**kwargs) -> MagicMock:
    a = MagicMock(spec=Action)
    a.id = kwargs.get("id", uuid.uuid4())
    a.payment_id = kwargs.get("payment_id", uuid.uuid4())
    a.subscriber_email = kwargs.get("subscriber_email", "sub@example.com")
    a.comp_days = kwargs.get("comp_days", 30)
    a.is_lifetime = kwargs.get("is_lifetime", False)
    a.execution_status = kwargs.get("execution_status", ExecutionStatus.failed)
    a.executed_at = kwargs.get("executed_at", None)
    a.failure_reason = kwargs.get("failure_reason", "some error")
    a.screenshot_path = kwargs.get("screenshot_path", None)
    a.created_at = kwargs.get("created_at", datetime.now(timezone.utc))
    return a


def make_subscriber(**kwargs) -> MagicMock:
    s = MagicMock(spec=Subscriber)
    s.id = kwargs.get("id", uuid.uuid4())
    s.email = kwargs.get("email", "sub@example.com")
    s.name = kwargs.get("name", "Subscriber One")
    s.substack_status = kwargs.get("substack_status", SubstackStatus.active)
    s.expiry_date = kwargs.get("expiry_date", date(2026, 12, 31))
    s.last_synced_at = kwargs.get("last_synced_at", datetime.now(timezone.utc))
    s.deleted_from_substack = kwargs.get("deleted_from_substack", False)
    return s


# ── GET /dashboard/summary ────────────────────────────────────────────────────

class TestSummary:
    async def test_summary_returns_all_keys(self):
        db = AsyncMock()
        db.execute.side_effect = [
            _scalar(3),   # payments_today
            _scalar(2),   # auto_resolved_today
            _scalar(1),   # pending_review
            _scalar(0),   # unknown
            _scalar(0),   # failed_actions
            _scalar(datetime.now(timezone.utc) - timedelta(hours=1)),  # last_synced_at
            _scalar_opt(None),  # cookie setting
        ]

        async with _client(db) as client:
            resp = await client.get("/dashboard/summary", headers=AUTH)

        app.dependency_overrides.clear()
        assert resp.status_code == 200
        body = resp.json()
        for key in (
            "payments_today", "auto_resolved_today", "pending_review",
            "unknown", "failed_actions", "last_subscriber_sync",
            "sync_overdue", "cookie_expired",
        ):
            assert key in body

    async def test_summary_cookie_expired_true(self):
        setting = MagicMock(spec=Setting)
        setting.value = "true"

        db = AsyncMock()
        db.execute.side_effect = [
            _scalar(0), _scalar(0), _scalar(0), _scalar(0), _scalar(0),
            _scalar(datetime.now(timezone.utc)),
            _scalar_opt(setting),
        ]

        async with _client(db) as client:
            resp = await client.get("/dashboard/summary", headers=AUTH)

        app.dependency_overrides.clear()
        assert resp.json()["cookie_expired"] is True

    async def test_summary_sync_overdue_when_never_synced(self):
        db = AsyncMock()
        db.execute.side_effect = [
            _scalar(0), _scalar(0), _scalar(0), _scalar(0), _scalar(0),
            _scalar(None),      # last_synced_at is None → overdue
            _scalar_opt(None),
        ]

        async with _client(db) as client:
            resp = await client.get("/dashboard/summary", headers=AUTH)

        app.dependency_overrides.clear()
        assert resp.json()["sync_overdue"] is True


# ── GET /dashboard/pending ────────────────────────────────────────────────────

class TestPending:
    async def test_returns_only_needs_review_and_unknown(self):
        p1 = make_payment(status=PaymentStatus.needs_review)
        p2 = make_payment(status=PaymentStatus.unknown)

        db = AsyncMock()
        db.execute.side_effect = [
            _scalars([p1, p2]),   # payments query
            _scalars([]),          # clarification emails
            _scalars([]),          # suggested match subscribers (none set)
        ]

        async with _client(db) as client:
            resp = await client.get("/dashboard/pending", headers=AUTH)

        app.dependency_overrides.clear()
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    async def test_suggested_match_populated(self):
        sub_email = "match@example.com"
        p = make_payment(
            status=PaymentStatus.needs_review,
            suggested_match_email=sub_email,
            suggested_match_score=92,
        )
        sub = make_subscriber(email=sub_email, name="Matched Sub")

        subs_result = MagicMock()
        subs_inner = MagicMock()
        subs_inner.all.return_value = [sub]
        subs_result.scalars.return_value = subs_inner

        db = AsyncMock()
        db.execute.side_effect = [
            _scalars([p]),
            _scalars([]),    # no clarification emails
            subs_result,     # suggested match subscriber found
        ]

        async with _client(db) as client:
            resp = await client.get("/dashboard/pending", headers=AUTH)

        app.dependency_overrides.clear()
        record = resp.json()[0]
        assert record["suggested_match"] is not None
        assert record["suggested_match"]["email"] == sub_email
        assert record["suggested_match"]["confidence_score"] == 92

    async def test_suggested_match_null_when_none_stored(self):
        p = make_payment(
            status=PaymentStatus.needs_review,
            suggested_match_email=None,
        )

        db = AsyncMock()
        db.execute.side_effect = [
            _scalars([p]),
            _scalars([]),
        ]

        async with _client(db) as client:
            resp = await client.get("/dashboard/pending", headers=AUTH)

        app.dependency_overrides.clear()
        assert resp.json()[0]["suggested_match"] is None


# ── POST /dashboard/approve ───────────────────────────────────────────────────

class TestApprove:
    async def test_approve_sets_auto_resolved(self):
        p = make_payment(status=PaymentStatus.needs_review)
        sub = make_subscriber()

        db = AsyncMock()
        db.execute.side_effect = [_scalar_opt(p), _scalar_opt(sub)]

        with patch("routers.dashboard.calculate_subscription"):
            async with _client(db) as client:
                resp = await client.post(
                    f"/dashboard/approve/{p.id}",
                    json={"subscriber_email": sub.email},
                    headers=AUTH,
                )

        app.dependency_overrides.clear()
        assert resp.status_code == 200
        assert p.status == PaymentStatus.auto_resolved

    async def test_approve_returns_404_for_missing_payment(self):
        db = AsyncMock()
        db.execute.return_value = _scalar_opt(None)

        async with _client(db) as client:
            resp = await client.post(
                f"/dashboard/approve/{uuid.uuid4()}",
                json={"subscriber_email": "x@example.com"},
                headers=AUTH,
            )

        app.dependency_overrides.clear()
        assert resp.status_code == 404


# ── POST /dashboard/reject ────────────────────────────────────────────────────

class TestReject:
    async def test_reject_sets_failed_and_writes_notes(self):
        p = make_payment(status=PaymentStatus.needs_review)

        db = AsyncMock()
        db.execute.return_value = _scalar_opt(p)

        async with _client(db) as client:
            resp = await client.post(
                f"/dashboard/reject/{p.id}",
                json={"notes": "Confirmed fraud"},
                headers=AUTH,
            )

        app.dependency_overrides.clear()
        assert resp.status_code == 200
        assert p.status == PaymentStatus.failed
        assert p.resolution_notes == "Confirmed fraud"


# ── GET /dashboard/subscribers/search ────────────────────────────────────────

class TestSubscriberSearch:
    async def test_returns_up_to_10_results(self):
        subs = [make_subscriber(email=f"user{i}@example.com") for i in range(5)]

        db = AsyncMock()
        db.execute.return_value = _scalars(subs)

        async with _client(db) as client:
            resp = await client.get(
                "/dashboard/subscribers/search?q=user", headers=AUTH
            )

        app.dependency_overrides.clear()
        assert resp.status_code == 200
        assert len(resp.json()) == 5

    async def test_rejects_query_shorter_than_2_chars(self):
        db = AsyncMock()

        async with _client(db) as client:
            resp = await client.get(
                "/dashboard/subscribers/search?q=x", headers=AUTH
            )

        app.dependency_overrides.clear()
        assert resp.status_code == 422


# ── GET /dashboard/log ────────────────────────────────────────────────────────

class TestActionLog:
    async def test_log_returns_paginated_shape(self):
        action = make_action(execution_status=ExecutionStatus.success)
        payment = make_payment()

        db = AsyncMock()

        count_result = _scalar(1)
        data_result = MagicMock()
        data_result.all.return_value = [(action, payment)]

        db.execute.side_effect = [count_result, data_result]

        async with _client(db) as client:
            resp = await client.get("/dashboard/log", headers=AUTH)

        app.dependency_overrides.clear()
        assert resp.status_code == 200
        body = resp.json()
        assert "items" in body
        assert "total" in body
        assert "page" in body
        assert "pages" in body
        assert body["total"] == 1
        assert len(body["items"]) == 1
        assert "payment" in body["items"][0]


# ── GET /dashboard/log/export ─────────────────────────────────────────────────

class TestLogExport:
    async def test_export_returns_csv_content_type(self):
        action = make_action()
        payment = make_payment()

        db = AsyncMock()
        data_result = MagicMock()
        data_result.all.return_value = [(action, payment)]
        db.execute.return_value = data_result

        async with _client(db) as client:
            resp = await client.get("/dashboard/log/export", headers=AUTH)

        app.dependency_overrides.clear()
        assert resp.status_code == 200
        assert "text/csv" in resp.headers["content-type"]

    async def test_export_csv_has_correct_headers(self):
        db = AsyncMock()
        data_result = MagicMock()
        data_result.all.return_value = []
        db.execute.return_value = data_result

        async with _client(db) as client:
            resp = await client.get("/dashboard/log/export", headers=AUTH)

        app.dependency_overrides.clear()
        first_line = resp.text.splitlines()[0]
        for col in ("payer_name", "payer_email", "subscriber_email", "comp_days",
                    "execution_status", "razorpay_payment_id"):
            assert col in first_line


# ── GET /dashboard/failed ─────────────────────────────────────────────────────

class TestFailedActions:
    async def test_failed_returns_only_failed_actions(self):
        a1 = make_action(execution_status=ExecutionStatus.failed)
        a2 = make_action(execution_status=ExecutionStatus.failed)

        db = AsyncMock()
        db.execute.return_value = _scalars([a1, a2])

        async with _client(db) as client:
            resp = await client.get("/dashboard/failed", headers=AUTH)

        app.dependency_overrides.clear()
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 2
        assert all(i["execution_status"] == "failed" for i in items)


# ── GET /dashboard/settings ───────────────────────────────────────────────────

class TestSettings:
    async def test_settings_returns_all_keys(self):
        db = AsyncMock()
        db.execute.side_effect = [
            _scalar(datetime.now(timezone.utc)),  # last_synced_at
            _scalar_opt(None),                    # cookie setting
            _scalar(150),                         # total_subscribers
        ]

        async with _client(db) as client:
            resp = await client.get("/dashboard/settings", headers=AUTH)

        app.dependency_overrides.clear()
        assert resp.status_code == 200
        body = resp.json()
        for key in (
            "last_sync_timestamp", "sync_overdue", "cookie_expired",
            "total_subscribers", "environment",
        ):
            assert key in body


# ── Identity engine Tier 2 — stores suggested match fields ───────────────────

class TestTier2StoresSuggestedMatch:
    async def test_tier2_writes_suggested_match_email_and_score(self):
        """
        After a Tier 2 fuzzy match, the payment record should have
        suggested_match_email and suggested_match_score populated.
        """
        from services.identity import _tier2_fuzzy_name

        payment = MagicMock(spec=Payment)
        payment.name = "Rahul Sharma"
        payment.status = PaymentStatus.pending
        payment.resolution_notes = None
        payment.suggested_match_email = None
        payment.suggested_match_score = None

        sub = make_subscriber(name="Rahul Sharma", email="rahul@example.com")

        db = AsyncMock()
        subs_result = MagicMock()
        inner = MagicMock()
        inner.all.return_value = [sub]
        subs_result.scalars.return_value = inner
        db.execute.return_value = subs_result

        result = await _tier2_fuzzy_name(payment, db)

        assert result is True
        assert payment.suggested_match_email == "rahul@example.com"
        assert payment.suggested_match_score == 100
