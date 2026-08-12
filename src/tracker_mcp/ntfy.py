"""ntfy notification loop for overdue tracking keys.

Reads all TrackingKey rows that have both a unit and a frequency set, checks
which ones haven't been measured within the expected period, and fires an ntfy
push notification for each one.

Required env vars
-----------------
NTFY_URL   ntfy endpoint including the topic, e.g. ``https://ntfy.sh/mytracker``.

Optional env vars
-----------------
CHECK_INTERVAL   Polling interval in seconds (default: 3600).
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

import httpx
from sqlmodel import Session, col, select

from tracker_mcp.models import Tracking, TrackingKey

NTFY_URL = os.getenv("NTFY_URL")
if NTFY_URL is None:
    raise KeyError("Please set NTFY_URL environment variable")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "3600"))

log = logging.getLogger(__name__)


def _period(unit: str, count: int) -> timedelta:
    if unit == "day":
        return timedelta(days=count)
    if unit == "week":
        return timedelta(weeks=count)
    return timedelta(days=30 * count)  # month: approximate


def _as_utc(dt: datetime) -> datetime:
    return (
        dt.replace(tzinfo=timezone.utc)
        if dt.tzinfo is None
        else dt.astimezone(timezone.utc)
    )


def find_overdue(engine: object) -> list[tuple[TrackingKey, datetime | None]]:
    """Return (key, last_tracked_at) for every key with a frequency that is overdue."""
    now = datetime.now(timezone.utc)
    overdue: list[tuple[TrackingKey, datetime | None]] = []
    with Session(engine) as session:  # type: ignore[arg-type]
        keys = session.exec(
            select(TrackingKey).where(col(TrackingKey.frequency_unit).is_not(None))
        ).all()
        for key in keys:
            assert key.frequency_unit is not None and key.frequency_count is not None
            deadline = now - _period(key.frequency_unit, key.frequency_count)
            last = session.exec(
                select(Tracking)
                .where(col(Tracking.key) == key.name)
                .order_by(col(Tracking.created_at).desc())
                .limit(1)
            ).first()
            last_at = _as_utc(last.created_at) if last is not None else None
            if last_at is None or last_at < deadline:
                overdue.append((key, last_at))
    return overdue


async def notify(
    client: httpx.AsyncClient, key: TrackingKey, last_at: datetime | None
) -> None:
    """Send a single ntfy reminder for one overdue key."""
    if last_at is None:
        body = f"Never recorded. Log {key.name} ({key.unit})."
    else:
        body = f"Last logged {last_at.strftime('%Y-%m-%d %H:%M UTC')}. Log {key.name} ({key.unit})."
    resp = await client.post(
        NTFY_URL,  # type: ignore[arg-type]
        content=body,
        headers={
            "Title": f"Reminder: track {key.name}",
            "Tags": "reminder,white_check_mark",
        },
    )
    resp.raise_for_status()
    log.info("Notified: %s (last=%s)", key.name, last_at)


async def notification_loop(engine: object) -> None:
    """Background task: poll for overdue keys and fire ntfy reminders."""
    log.info("Notifier started (interval=%ds, ntfy=%s)", CHECK_INTERVAL, NTFY_URL)
    while True:
        try:
            overdue = await asyncio.to_thread(find_overdue, engine)
            if overdue:
                async with httpx.AsyncClient(timeout=10) as client:
                    for key, last_at in overdue:
                        try:
                            await notify(client, key, last_at)
                        except Exception:
                            log.exception("Failed to notify for key %r", key.name)
        except Exception:
            log.exception("Error during overdue check")
        await asyncio.sleep(CHECK_INTERVAL)
