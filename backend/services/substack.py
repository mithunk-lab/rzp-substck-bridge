import asyncio
import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

from playwright.async_api import async_playwright
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import AsyncSessionLocal
from models import Action, ExecutionStatus, Setting

logger = logging.getLogger(__name__)

# Screenshots are stored alongside the backend package
_SCREENSHOTS_DIR = Path(__file__).parent.parent / "screenshots"


# ── Entry point ───────────────────────────────────────────────────────────────

async def execute_substack_action(action_id: UUID) -> None:
    """
    Substack Action Executor — BackgroundTask-safe entry point.
    Creates its own DB session so it is not bound to the request lifecycle.
    """
    async with AsyncSessionLocal() as db:
        await _run_executor(action_id, db)


# ── Orchestrator ──────────────────────────────────────────────────────────────

async def _run_executor(action_id: UUID, db: AsyncSession) -> None:
    """Fetches the action record, launches the browser, and drives the comp flow."""

    # 1. Fetch action record
    result = await db.execute(select(Action).where(Action.id == action_id))
    action = result.scalar_one_or_none()
    if not action:
        logger.error("execute_substack_action: action %s not found", action_id)
        return

    _SCREENSHOTS_DIR.mkdir(exist_ok=True)
    await _upsert_setting(db, "last_executor_run", datetime.now(timezone.utc).isoformat())

    headless = os.getenv("PLAYWRIGHT_HEADLESS", "true").lower() == "true"
    publication_url = os.getenv("SUBSTACK_PUBLICATION_URL", "").rstrip("/")
    session_cookie = os.getenv("SUBSTACK_SESSION_COOKIE", "")

    if not publication_url or not session_cookie:
        action.execution_status = ExecutionStatus.failed
        action.failure_reason = "SUBSTACK_PUBLICATION_URL or SUBSTACK_SESSION_COOKIE not configured"
        await db.commit()
        await _upsert_setting(db, "last_executor_status", "failed")
        logger.error("Executor misconfigured for action %s: missing env vars", action_id)
        return

    parsed = urlparse(publication_url)
    domain = parsed.hostname or "substack.com"

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=headless)
            context = await browser.new_context()

            # Inject session cookie — no login flow
            # Cookie name varies by setup: custom-domain publications use connect.sid
            cookie_name = os.getenv("SUBSTACK_SESSION_COOKIE_NAME", "connect.sid")
            await context.add_cookies([
                {
                    "name": cookie_name,
                    "value": session_cookie,
                    "domain": domain,
                    "path": "/",
                    "httpOnly": True,
                    "secure": True,
                }
            ])

            page = await context.new_page()

            try:
                await _execute_comp(action, page, db, publication_url)
            except Exception as exc:
                logger.error(
                    "Substack executor unexpected error for action %s",
                    action_id,
                    exc_info=True,
                )
                await _fail_action(action, page, db, f"Unexpected error: {exc}")
            finally:
                await browser.close()
    except Exception as exc:
        action.execution_status = ExecutionStatus.failed
        action.failure_reason = f"Browser launch failed: {exc}"
        await db.commit()
        await _upsert_setting(db, "last_executor_status", "failed")
        logger.error("Browser launch failed for action %s: %s", action_id, exc, exc_info=True)


# ── Core comp flow ────────────────────────────────────────────────────────────

async def _execute_comp(
    action: Action,
    page,
    db: AsyncSession,
    publication_url: str,
) -> None:
    """Drives the Substack UI to execute a comp grant for the subscriber."""

    # Step 2: Navigate to subscriber management page
    await page.goto(f"{publication_url}/publish/subscribers", wait_until="networkidle")

    # Step 3: Detect login redirect — cookie may have expired
    if "login" in page.url:
        logger.warning(
            "Substack session cookie expired — action %s cannot be executed", action.id
        )
        await _fail_action(
            action,
            page,
            db,
            "Substack session cookie expired — refresh required",
        )
        # Persist cookie-expiry flag so the dashboard can surface a top-level warning
        await _upsert_setting(db, "substack_cookie_expired", "true")
        return

    # Clear any previously-set cookie-expiry flag on successful navigation
    await _upsert_setting(db, "substack_cookie_expired", "false")

    # Step 4: Search for subscriber by email
    # The search input on the Substack subscribers page
    search_input = page.locator('input[placeholder*="Search"]').first
    await search_input.fill(action.subscriber_email)
    await page.wait_for_timeout(1500)  # allow results to load

    # Step 5: Locate subscriber row in results
    # Substack renders subscribers in a table; each row contains the email address
    subscriber_row = page.locator(
        f'tr:has-text("{action.subscriber_email}"), '
        f'[data-email="{action.subscriber_email}"]'
    ).first

    if not await subscriber_row.is_visible(timeout=3000):
        await _fail_action(
            action,
            page,
            db,
            "Subscriber email not found on Substack dashboard",
        )
        return

    # Step 6: Open subscriber management panel
    await subscriber_row.click()
    await page.wait_for_timeout(500)

    # Step 7: Select "Comp subscription" option in the management panel
    await page.locator('button:has-text("Comp")').first.click()
    await page.wait_for_timeout(500)

    # Step 8: Fill in comp details
    if action.is_lifetime:
        # Select the lifetime / forever option in the comp dialog
        await page.locator(
            'input[value="forever"], '
            'label:has-text("Forever"), '
            'label:has-text("Lifetime")'
        ).first.click()
    else:
        # Enter the integer number of days into the days field
        await page.locator('input[type="number"]').first.fill(str(action.comp_days))

    # Step 9: DRY_RUN gate — stop here if dry run, do NOT click confirm
    dry_run = os.getenv("DRY_RUN", "false").lower() == "true"
    if dry_run:
        logger.info(
            "DRY RUN: would have comped %s for %s days / lifetime=%s",
            action.subscriber_email,
            action.comp_days,
            action.is_lifetime,
        )
        screenshot_path = await _take_screenshot(page, action.id, "dryrun")
        action.execution_status = ExecutionStatus.manual
        action.failure_reason = "Dry run — not executed"
        action.screenshot_path = screenshot_path
        await db.commit()
        await _upsert_setting(db, "last_executor_status", "manual")
        return

    # Step 10 (execute): Click the confirm button
    # The confirm button is typically the primary submit button inside the comp dialog
    await page.locator(
        'button[type="submit"]:visible, '
        'button:has-text("Confirm"):visible'
    ).last.click()
    await page.wait_for_timeout(2000)

    # Step 10 (verify): Re-fetch subscriber to confirm expiry was updated
    await search_input.fill(action.subscriber_email)
    await page.wait_for_timeout(1500)

    expiry_text = await page.locator(
        f'tr:has-text("{action.subscriber_email}") .expiry-date, '
        f'tr:has-text("{action.subscriber_email}") [data-expiry]'
    ).first.inner_text(timeout=3000)

    if await _verify_expiry(action, expiry_text):
        screenshot_path = await _take_screenshot(page, action.id, "success")
        action.execution_status = ExecutionStatus.success
        action.executed_at = datetime.now(timezone.utc)
        action.screenshot_path = screenshot_path
        await db.commit()
        await _upsert_setting(db, "last_executor_status", "success")
        logger.info(
            "Executed: %s comped for %s days / lifetime=%s",
            action.subscriber_email,
            action.comp_days,
            action.is_lifetime,
        )
    else:
        if action.is_lifetime:
            expected = "lifetime"
        else:
            expected = str(date.today() + timedelta(days=action.comp_days))
        await _fail_action(
            action,
            page,
            db,
            f"Verification failed: expected expiry {expected}, got {expiry_text!r}",
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _verify_expiry(action: Action, expiry_text: str) -> bool:
    """
    Returns True if the expiry text from the dashboard matches what was expected.
    Allows a 1-day tolerance for timezone edge cases.
    """
    if action.is_lifetime:
        return "lifetime" in expiry_text.lower() or "forever" in expiry_text.lower()

    expected_date = date.today() + timedelta(days=action.comp_days)
    # Check ISO format and common display formats within ±1 day tolerance
    for delta in range(-1, 2):
        candidate = expected_date + timedelta(days=delta)
        if (
            candidate.isoformat() in expiry_text
            or candidate.strftime("%b %-d, %Y") in expiry_text
            or candidate.strftime("%B %-d, %Y") in expiry_text
        ):
            return True
    return False


async def _take_screenshot(page, action_id: UUID, suffix: str) -> str:
    """Takes a screenshot and returns the absolute path string."""
    path = str(_SCREENSHOTS_DIR / f"{action_id}_{suffix}.png")
    await page.screenshot(path=path)
    return path


async def _fail_action(
    action: Action,
    page,
    db: AsyncSession,
    reason: str,
) -> None:
    """
    Records a failure on the action record, takes a failure screenshot,
    and writes last_executor_status to the settings table.
    Failed actions with execution_status=failed serve as the failures queue
    that the dashboard polls.
    """
    screenshot_path = await _take_screenshot(page, action.id, "failure")
    action.execution_status = ExecutionStatus.failed
    action.failure_reason = reason
    action.screenshot_path = screenshot_path
    await db.commit()
    await _upsert_setting(db, "last_executor_status", "failed")
    logger.error("Executor failed for action %s: %s", action.id, reason)


async def _upsert_setting(db: AsyncSession, key: str, value: str) -> None:
    """Insert or update a row in the settings table."""
    result = await db.execute(select(Setting).where(Setting.key == key))
    setting = result.scalar_one_or_none()
    if setting:
        setting.value = value
        setting.updated_at = datetime.now(timezone.utc)
    else:
        setting = Setting(key=key, value=value)
        db.add(setting)
    await db.commit()
