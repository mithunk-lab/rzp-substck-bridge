import csv
import io
import logging
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Subscriber, SubstackStatus

logger = logging.getLogger(__name__)

# Canonical field name → accepted CSV header names, checked in priority order
_COLUMN_ALIASES: dict[str, list[str]] = {
    "email": ["email"],
    "name": ["name", "full_name"],
    "subscription_status": ["subscription_status", "type"],
    "expiry_date": ["expiry_date", "end_date"],
}

_STATUS_MAP: dict[str, SubstackStatus] = {
    "active": SubstackStatus.active,
    "comped": SubstackStatus.active,    # comped counts as active, keep expiry_date
    "lifetime": SubstackStatus.lifetime,
    # anything else → lapsed (handled as default)
}


def _build_column_map(fieldnames: list[str] | None) -> dict[str, str]:
    """
    Returns a mapping from canonical field name → actual CSV column name.
    E.g. {"name": "full_name", "subscription_status": "type", ...}
    Only canonical names whose alias was found in the CSV are included.
    """
    if not fieldnames:
        return {}
    normalised = {h.strip().lower(): h for h in fieldnames}
    col_map: dict[str, str] = {}
    for canonical, aliases in _COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in normalised:
                col_map[canonical] = normalised[alias]
                break
    return col_map


def _map_status(raw: str) -> SubstackStatus:
    """Map a raw CSV status value to SubstackStatus. Unknown values → lapsed."""
    return _STATUS_MAP.get(raw.strip().lower(), SubstackStatus.lapsed)


def _extract_row(row: dict[str, Any], col_map: dict[str, str]) -> dict:
    """
    Extract and validate a single CSV row dict into clean typed fields.
    Raises ValueError if email is missing.
    """
    email = (row.get(col_map.get("email", "email")) or "").strip().lower()
    if not email:
        raise ValueError("Missing or empty email")

    name = (row.get(col_map.get("name", "name")) or "").strip()
    if not name:
        name = email  # subscribers.name is NOT NULL — fall back to email

    status_raw = (row.get(col_map.get("subscription_status", "subscription_status")) or "").strip()
    substack_status = _map_status(status_raw)

    expiry_raw = (row.get(col_map.get("expiry_date", "expiry_date")) or "").strip()
    expiry_date: date | None = None
    if substack_status == SubstackStatus.lifetime:
        expiry_date = None  # lifetime → always null, ignore whatever the CSV says
    elif expiry_raw:
        try:
            expiry_date = date.fromisoformat(expiry_raw)
        except ValueError:
            logger.debug("Could not parse expiry_date %r — storing null", expiry_raw)

    return {
        "email": email,
        "name": name,
        "substack_status": substack_status,
        "expiry_date": expiry_date,
    }


async def process_csv(content: str, db: AsyncSession) -> dict:
    """
    Parse a Substack subscriber CSV export and upsert all rows.

    Processing order:
      1. Record sync_start timestamp.
      2. For each row: upsert (INSERT if new email, UPDATE if existing).
         Row-level errors are collected and processing continues.
      3. Commit all upserts.
      4. Deletion reconciliation: subscribers whose last_synced_at is
         older than sync_start were absent from this export — mark them
         deleted_from_substack = True (retain rows for audit).
      5. Commit reconciliation.

    Returns a summary dict matching the API response shape.
    """
    sync_start = datetime.now(timezone.utc)
    processed = 0
    inserted = 0
    updated = 0
    errors: list[dict] = []

    reader = csv.DictReader(io.StringIO(content))
    col_map = _build_column_map(reader.fieldnames)

    for row_num, row in enumerate(reader, start=2):
        try:
            data = _extract_row(row, col_map)

            result = await db.execute(
                select(Subscriber).where(Subscriber.email == data["email"])
            )
            existing = result.scalar_one_or_none()

            if existing:
                existing.name = data["name"]
                existing.substack_status = data["substack_status"]
                existing.expiry_date = data["expiry_date"]
                existing.last_synced_at = sync_start
                updated += 1
            else:
                db.add(Subscriber(
                    email=data["email"],
                    name=data["name"],
                    substack_status=data["substack_status"],
                    expiry_date=data["expiry_date"],
                    last_synced_at=sync_start,
                ))
                inserted += 1

            processed += 1

        except Exception as exc:
            logger.warning("Error processing CSV row %d: %s", row_num, exc)
            errors.append({"row": row_num, "error": str(exc)})

    await db.commit()

    # Deletion reconciliation — records untouched by this sync are gone from Substack
    stale_result = await db.execute(
        select(Subscriber).where(
            Subscriber.last_synced_at < sync_start,
            Subscriber.deleted_from_substack == False,  # noqa: E712
        )
    )
    stale = stale_result.scalars().all()
    marked_deleted = len(stale)

    for sub in stale:
        sub.deleted_from_substack = True

    await db.commit()

    if marked_deleted:
        logger.info("Marked %d subscriber(s) as deleted_from_substack", marked_deleted)

    return {
        "processed": processed,
        "inserted": inserted,
        "updated": updated,
        "marked_deleted": marked_deleted,
        "errors": errors,
        "sync_timestamp": sync_start.isoformat(),
    }
