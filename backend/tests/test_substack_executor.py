"""
Integration tests for the Substack Action Executor.

These tests launch a real Playwright Chromium browser and navigate to a real
Substack publication. They run in DRY_RUN mode so no comps are ever executed.

Required environment variables (tests are skipped if absent):
    SUBSTACK_SESSION_COOKIE   — valid session cookie for the publication account
    SUBSTACK_PUBLICATION_URL  — e.g. https://thewire.substack.com
    SUBSTACK_TEST_EMAIL       — an email address that EXISTS as a subscriber
                                in the publication (used for positive-path tests)

Optional:
    SUBSTACK_INVALID_COOKIE   — a deliberately invalid cookie value used by the
                                cookie-expiry detection test (defaults to "invalid")
"""

import os
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from main import app
from models import Action, ExecutionStatus, Setting
from services.substack import _run_executor, _upsert_setting, _verify_expiry

# ── Skip markers ──────────────────────────────────────────────────────────────

_HAS_CREDENTIALS = bool(
    os.getenv("SUBSTACK_SESSION_COOKIE") and os.getenv("SUBSTACK_PUBLICATION_URL")
)
_HAS_TEST_EMAIL = bool(os.getenv("SUBSTACK_TEST_EMAIL"))

requires_credentials = pytest.mark.skipif(
    not _HAS_CREDENTIALS,
    reason="SUBSTACK_SESSION_COOKIE and SUBSTACK_PUBLICATION_URL required",
)
requires_test_email = pytest.mark.skipif(
    not (_HAS_CREDENTIALS and _HAS_TEST_EMAIL),
    reason="SUBSTACK_SESSION_COOKIE, SUBSTACK_PUBLICATION_URL, and SUBSTACK_TEST_EMAIL required",
)

pytestmark = pytest.mark.integration


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_action(
    comp_days: int | None = 30,
    is_lifetime: bool = False,
    subscriber_email: str | None = None,
) -> MagicMock:
    action = MagicMock(spec=Action)
    action.id = uuid.uuid4()
    action.subscriber_email = subscriber_email or os.getenv(
        "SUBSTACK_TEST_EMAIL", "test@example.com"
    )
    action.comp_days = comp_days
    action.is_lifetime = is_lifetime
    action.execution_status = ExecutionStatus.pending
    action.failure_reason = None
    action.screenshot_path = None
    return action


def make_db(action: MagicMock) -> AsyncMock:
    """
    Returns an AsyncMock DB whose first execute() yields the given action
    and whose subsequent execute() calls (settings upserts) return empty results
    so new Setting rows are inserted.
    """
    db = AsyncMock()

    action_result = MagicMock()
    action_result.scalar_one_or_none.return_value = action

    # Every setting SELECT returns None → triggers an INSERT path in _upsert_setting
    empty_result = MagicMock()
    empty_result.scalar_one_or_none.return_value = None

    # Provide enough side-effects for all execute() calls in the happy path
    db.execute.side_effect = [action_result] + [empty_result] * 20
    return db


# ── _verify_expiry unit tests (no browser needed) ─────────────────────────────

class TestVerifyExpiry:
    """Pure-logic tests — no credentials required."""

    async def test_lifetime_match_lowercase(self):
        action = make_action(is_lifetime=True)
        assert await _verify_expiry(action, "lifetime subscription") is True

    async def test_lifetime_match_forever(self):
        action = make_action(is_lifetime=True)
        assert await _verify_expiry(action, "forever") is True

    async def test_lifetime_mismatch(self):
        action = make_action(is_lifetime=True)
        assert await _verify_expiry(action, "Jun 15, 2025") is False

    async def test_date_exact_iso(self):
        action = make_action(comp_days=30)
        from datetime import date, timedelta
        expected = (date.today() + timedelta(days=30)).isoformat()
        assert await _verify_expiry(action, expected) is True

    async def test_date_within_tolerance(self):
        """One day off is still accepted."""
        action = make_action(comp_days=30)
        from datetime import date, timedelta
        one_day_off = (date.today() + timedelta(days=31)).isoformat()
        assert await _verify_expiry(action, one_day_off) is True

    async def test_date_outside_tolerance(self):
        action = make_action(comp_days=30)
        assert await _verify_expiry(action, "2020-01-01") is False


# ── Retry endpoint test (no browser needed) ───────────────────────────────────

class TestRetryActionEndpoint:
    """Tests the POST /admin/retry-action/{id} endpoint with a mocked DB."""

    @pytest.fixture(autouse=True)
    def _patch_env(self, monkeypatch):
        monkeypatch.setenv("DASHBOARD_API_KEY", "test-key")
        monkeypatch.setenv("ENVIRONMENT", "development")

    async def test_retry_resets_status_to_pending(self):
        action = MagicMock(spec=Action)
        action.id = uuid.uuid4()
        action.payment_id = uuid.uuid4()
        action.subscriber_email = "user@example.com"
        action.comp_days = 30
        action.is_lifetime = False
        action.execution_status = ExecutionStatus.failed
        action.failure_reason = "some error"
        action.screenshot_path = None
        action.created_at = datetime.now(timezone.utc)
        action.executed_at = None

        action_result = MagicMock()
        action_result.scalar_one_or_none.return_value = action

        mock_db = AsyncMock()
        mock_db.execute.return_value = action_result

        async def override_db():
            yield mock_db

        from database import get_db
        from services.substack import execute_substack_action

        app.dependency_overrides[get_db] = override_db

        # Patch the background executor so it doesn't actually run
        with patch("routers.admin.execute_substack_action"):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.post(
                    f"/admin/retry-action/{action.id}",
                    headers={"Authorization": "Bearer test-key"},
                )

        app.dependency_overrides.clear()

        assert response.status_code == 200
        assert action.execution_status == ExecutionStatus.pending
        assert action.failure_reason is None

    async def test_retry_returns_404_for_missing_action(self):
        missing_result = MagicMock()
        missing_result.scalar_one_or_none.return_value = None

        mock_db = AsyncMock()
        mock_db.execute.return_value = missing_result

        async def override_db():
            yield mock_db

        from database import get_db

        app.dependency_overrides[get_db] = override_db

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                f"/admin/retry-action/{uuid.uuid4()}",
                headers={"Authorization": "Bearer test-key"},
            )

        app.dependency_overrides.clear()
        assert response.status_code == 404


# ── Playwright integration tests ──────────────────────────────────────────────

class TestSubstackExecutorDryRun:
    """
    Live browser tests against the real Substack publication.
    All tests run with DRY_RUN=true — no comps are ever executed.
    """

    @pytest.fixture(autouse=True)
    def _dry_run(self, monkeypatch):
        monkeypatch.setenv("DRY_RUN", "true")

    @requires_test_email
    async def test_dry_run_comp_days(self):
        """
        Positive path: executor navigates to a known subscriber, fills in 30 days,
        and stops at the DRY_RUN gate. Action is set to manual.
        """
        action = make_action(comp_days=30, is_lifetime=False)
        db = make_db(action)

        await _run_executor(action.id, db)

        assert action.execution_status == ExecutionStatus.manual
        assert action.failure_reason == "Dry run — not executed"

    @requires_test_email
    async def test_dry_run_lifetime(self):
        """Lifetime variant: executor selects the lifetime option and stops at gate."""
        action = make_action(comp_days=None, is_lifetime=True)
        db = make_db(action)

        await _run_executor(action.id, db)

        assert action.execution_status == ExecutionStatus.manual
        assert action.failure_reason == "Dry run — not executed"

    @requires_test_email
    async def test_dry_run_screenshot_saved(self, tmp_path, monkeypatch):
        """A screenshot file is created at the expected path after a dry run."""
        monkeypatch.setattr(
            "services.substack._SCREENSHOTS_DIR", tmp_path
        )
        action = make_action(comp_days=30)
        db = make_db(action)

        await _run_executor(action.id, db)

        assert action.screenshot_path is not None
        assert action.screenshot_path.endswith("_dryrun.png")
        from pathlib import Path
        assert Path(action.screenshot_path).exists()

    @requires_credentials
    async def test_subscriber_not_found(self, monkeypatch):
        """
        Searching for an email that definitely does not exist on Substack
        routes the action to failed with the expected reason.
        """
        action = make_action(
            comp_days=30,
            subscriber_email="nonexistent_bridge_test_99999@example.com",
        )
        db = make_db(action)

        await _run_executor(action.id, db)

        assert action.execution_status == ExecutionStatus.failed
        assert "not found" in (action.failure_reason or "").lower()

    @requires_credentials
    async def test_cookie_expired_detection(self, monkeypatch):
        """
        An invalid session cookie causes a login redirect.
        The action is failed and substack_cookie_expired is written to settings.
        """
        invalid_cookie = os.getenv("SUBSTACK_INVALID_COOKIE", "invalid-session-token")
        monkeypatch.setenv("SUBSTACK_SESSION_COOKIE", invalid_cookie)

        action = make_action(comp_days=30)
        db = make_db(action)

        # Capture the setting key written during the run
        written_settings: dict[str, str] = {}

        async def fake_upsert(db_, key, value):
            written_settings[key] = value

        with patch("services.substack._upsert_setting", side_effect=fake_upsert):
            await _run_executor(action.id, db)

        assert action.execution_status == ExecutionStatus.failed
        assert "cookie" in (action.failure_reason or "").lower()
        assert written_settings.get("substack_cookie_expired") == "true"
