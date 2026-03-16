"""
Unit tests for the subscriber sync module.

Pure-function tests (column mapping, status mapping, row extraction) need
no DB. Async tests (process_csv, stats endpoint) mock the DB session the
same way test_webhooks.py does.
"""

import os
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

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
from models import SubstackStatus
from services.subscriber_sync import (
    _build_column_map,
    _extract_row,
    _map_status,
    process_csv,
)

AUTH_HEADERS = {"Authorization": "Bearer test_api_key"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _scalar(value):
    """Wrap a value so mock_db.execute(...).scalar_one() returns it."""
    r = MagicMock()
    r.scalar_one.return_value = value
    return r


def _no_match():
    """DB execute result where scalar_one_or_none() returns None."""
    r = MagicMock()
    r.scalar_one_or_none.return_value = None
    return r


def _match(obj):
    """DB execute result where scalar_one_or_none() returns obj."""
    r = MagicMock()
    r.scalar_one_or_none.return_value = obj
    return r


def _stale_list(subscribers: list):
    """DB execute result where scalars().all() returns subscribers."""
    r = MagicMock()
    r.scalars.return_value.all.return_value = subscribers
    return r


def make_db(*side_effects) -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.execute = AsyncMock(side_effect=list(side_effects))
    return db


# ── Test 1: CSV column name variations ───────────────────────────────────────

def test_column_map_canonical_names():
    """Standard Substack column names map to themselves."""
    col_map = _build_column_map(["email", "name", "subscription_status", "expiry_date"])
    assert col_map["email"] == "email"
    assert col_map["name"] == "name"
    assert col_map["subscription_status"] == "subscription_status"
    assert col_map["expiry_date"] == "expiry_date"


def test_column_map_alias_names():
    """Alias column names are recognised and mapped to canonical keys."""
    col_map = _build_column_map(["email", "full_name", "type", "end_date"])
    assert col_map["name"] == "full_name"
    assert col_map["subscription_status"] == "type"
    assert col_map["expiry_date"] == "end_date"


def test_extract_row_with_aliases_end_to_end():
    """A row using all alias column names is parsed to the correct typed values."""
    col_map = _build_column_map(["email", "full_name", "type", "end_date"])
    row = {
        "email": "alias@example.com",
        "full_name": "Alias User",
        "type": "active",
        "end_date": "2025-06-30",
    }
    data = _extract_row(row, col_map)
    assert data["email"] == "alias@example.com"
    assert data["name"] == "Alias User"
    assert data["substack_status"] == SubstackStatus.active
    assert data["expiry_date"] == date(2025, 6, 30)


# ── Test 2: Status mapping ────────────────────────────────────────────────────

def test_status_mapping_all_values():
    """Every documented input value maps to the correct SubstackStatus."""
    assert _map_status("active") == SubstackStatus.active
    assert _map_status("comped") == SubstackStatus.active    # comped → active
    assert _map_status("lifetime") == SubstackStatus.lifetime
    assert _map_status("inactive") == SubstackStatus.lapsed
    assert _map_status("expired") == SubstackStatus.lapsed
    assert _map_status("cancelled") == SubstackStatus.lapsed  # unknown → lapsed
    assert _map_status("") == SubstackStatus.lapsed


def test_status_mapping_is_case_insensitive():
    """Status matching ignores case."""
    assert _map_status("ACTIVE") == SubstackStatus.active
    assert _map_status("Comped") == SubstackStatus.active
    assert _map_status("LIFETIME") == SubstackStatus.lifetime


def test_lifetime_status_forces_expiry_date_null():
    """Lifetime subscribers always get expiry_date = None even if CSV has a value."""
    col_map = _build_column_map(["email", "name", "subscription_status", "expiry_date"])
    row = {
        "email": "lifetime@example.com",
        "name": "Lifetime User",
        "subscription_status": "lifetime",
        "expiry_date": "2099-01-01",   # must be ignored
    }
    data = _extract_row(row, col_map)
    assert data["substack_status"] == SubstackStatus.lifetime
    assert data["expiry_date"] is None


# ── Test 3: Upsert — insert new ───────────────────────────────────────────────

async def test_upsert_inserts_new_subscriber():
    """An email not in the DB results in db.add() being called once."""
    db = make_db(
        _no_match(),        # email lookup → not found
        _stale_list([]),    # deletion reconciliation → no stale rows
    )
    csv = "email,name,subscription_status,expiry_date\nnew@example.com,New User,active,2025-12-31\n"

    result = await process_csv(csv, db)

    assert result["processed"] == 1
    assert result["inserted"] == 1
    assert result["updated"] == 0
    assert result["errors"] == []
    db.add.assert_called_once()
    added: object = db.add.call_args[0][0]
    assert added.email == "new@example.com"
    assert added.substack_status == SubstackStatus.active


# ── Test 4: Upsert — update existing ─────────────────────────────────────────

async def test_upsert_updates_existing_subscriber():
    """An email already in the DB updates that record in place; no insert."""
    existing = MagicMock()
    existing.email = "existing@example.com"

    db = make_db(
        _match(existing),   # email lookup → found
        _stale_list([]),    # deletion reconciliation → no stale rows
    )
    csv = "email,name,subscription_status,expiry_date\nexisting@example.com,Updated Name,lapsed,\n"

    result = await process_csv(csv, db)

    assert result["processed"] == 1
    assert result["inserted"] == 0
    assert result["updated"] == 1
    assert result["errors"] == []
    db.add.assert_not_called()
    assert existing.name == "Updated Name"
    assert existing.substack_status == SubstackStatus.lapsed


# ── Test 5: Deletion reconciliation ──────────────────────────────────────────

async def test_deletion_reconciliation_marks_stale_subscribers():
    """
    Subscribers whose last_synced_at predates the current sync_start are
    not present in the export and must be marked deleted_from_substack=True.
    """
    stale1 = MagicMock()
    stale1.deleted_from_substack = False
    stale2 = MagicMock()
    stale2.deleted_from_substack = False

    db = make_db(
        _no_match(),                    # email lookup for the one CSV row
        _stale_list([stale1, stale2]),  # reconciliation finds 2 stale records
    )
    csv = "email,name,subscription_status,expiry_date\nnew@example.com,New User,active,\n"

    result = await process_csv(csv, db)

    assert result["marked_deleted"] == 2
    assert stale1.deleted_from_substack is True
    assert stale2.deleted_from_substack is True
    # Two commits: one after upserts, one after reconciliation
    assert db.commit.await_count == 2


# ── Tests 6 & 7: sync_overdue ────────────────────────────────────────────────

async def test_sync_overdue_true_when_last_sync_over_24h():
    """
    GET /admin/subscribers/stats returns sync_overdue=true when the
    most recent last_synced_at is more than 24 hours ago.
    """
    old_ts = datetime.now(timezone.utc) - timedelta(hours=25)

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar(50),    # total
        _scalar(30),    # active
        _scalar(15),    # lapsed
        _scalar(5),     # lifetime
        _scalar(2),     # deleted
        _scalar(old_ts),  # last_synced_at
    ])

    async def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/admin/subscribers/stats", headers=AUTH_HEADERS)
        assert response.status_code == 200
        data = response.json()
        assert data["sync_overdue"] is True
        assert data["total"] == 50
        assert data["active"] == 30
    finally:
        app.dependency_overrides.pop(get_db, None)


async def test_sync_overdue_false_when_last_sync_recent():
    """
    GET /admin/subscribers/stats returns sync_overdue=false when the
    most recent last_synced_at is within the last 24 hours.
    """
    recent_ts = datetime.now(timezone.utc) - timedelta(hours=2)

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[
        _scalar(50),
        _scalar(30),
        _scalar(15),
        _scalar(5),
        _scalar(2),
        _scalar(recent_ts),
    ])

    async def _override():
        yield db

    app.dependency_overrides[get_db] = _override
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/admin/subscribers/stats", headers=AUTH_HEADERS)
        assert response.status_code == 200
        assert response.json()["sync_overdue"] is False
    finally:
        app.dependency_overrides.pop(get_db, None)
