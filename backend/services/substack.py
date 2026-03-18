import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlparse
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

    # Step 4: Navigate directly to subscriber detail page
    detail_url = f"{publication_url}/publish/subscribers/details?email={quote(action.subscriber_email)}"
    await page.goto(detail_url, wait_until="networkidle")
    await page.wait_for_timeout(4000)

    # Re-check for login redirect after detail navigation
    if "login" in page.url:
        await _fail_action(action, page, db, "Substack session cookie expired — refresh required")
        await _upsert_setting(db, "substack_cookie_expired", "true")
        return

    # Step 5: Confirm subscriber detail page loaded (Back button is present when on detail page)
    if not await page.locator('button[aria-label="Back"]').is_visible(timeout=5000):
        await _fail_action(action, page, db, f"Subscriber detail page not found for {action.subscriber_email}")
        return

    # Step 6: Open subscriber management menu via Ellipsis button
    await page.locator('button[aria-label="Ellipsis"]').first.click(timeout=10000)
    await page.wait_for_timeout(1000)

    # Step 7: Click the appropriate option in the dropdown menu
    # Free subscriber → "Comp"  |  Already-comped subscriber → "Extend subscription"
    comp_menu_item = page.locator(
        '[role="menuitem"]:has-text("Comp"), [role="menuitem"]:has-text("Extend subscription")'
    ).first
    if not await comp_menu_item.is_visible(timeout=5000):
        await _fail_action(
            action, page, db,
            "Neither Comp nor Extend subscription option found in subscriber menu",
        )
        return
    await comp_menu_item.click()
    await page.wait_for_timeout(1500)

    # Step 8: DRY_RUN gate — screenshot the comp dialog before filling
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

    # Step 9: Select duration in the dialog dropdown
    # The dialog has preset options; fall back to "Other" + date input for custom durations.
    _DURATION_PRESETS = {7: '7 days', 30: '30 days', 90: '90 days', 180: '6 months', 365: '1 year'}
    if action.is_lifetime:
        target_label = 'Forever'
    else:
        target_label = _DURATION_PRESETS.get(action.comp_days, 'Other')

    # Try native <select> first; fall back to custom dropdown (click trigger → click option)
    select_el = page.locator('select')
    if await select_el.count() > 0:
        await select_el.first.select_option(label=target_label)
    else:
        await page.locator('[role="combobox"], [aria-haspopup="listbox"]').first.click()
        await page.wait_for_timeout(500)
        await page.locator(
            f'[role="option"]:has-text("{target_label}"), li:has-text("{target_label}")'
        ).first.click()

    # For "Other": a date input appears — fill with target date (today + comp_days)
    if target_label == 'Other':
        await page.wait_for_timeout(500)
        target_date = date.today() + timedelta(days=action.comp_days)
        await page.locator('input[type="date"]').first.fill(target_date.isoformat())
        await page.wait_for_timeout(300)

    # Step 10: Confirm — button text is dynamic across flows:
    # "Comp for 30 days" / "Comp indefinitely" / "Comp until …" / "Extend until …"
    await page.locator(
        'button:has-text("Comp for"), '
        'button:has-text("Comp indefinitely"), '
        'button:has-text("Comp until"), '
        'button:has-text("Extend until"), '
        'button:has-text("Extend for"), '
        'button:has-text("Extend indefinitely")'
    ).last.click(timeout=5000)
    await page.wait_for_timeout(2000)

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


# ── Helpers ───────────────────────────────────────────────────────────────────


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
