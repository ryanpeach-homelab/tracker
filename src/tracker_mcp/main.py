import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncIterator

from fastmcp import FastMCP
from pydantic import BaseModel
from sqlalchemy import text, update
from sqlmodel import Session, col, create_engine, select

from tracker_mcp.models import (
    Tracking,
    TrackingKey,
    TrackingUnit,
    make_point,
    parse_frequency,
)
from tracker_mcp.ntfy import NTFY_URL, notification_loop

DATABASE_URI = os.environ["DATABASE_URI"]
engine = create_engine(DATABASE_URI)


@asynccontextmanager
async def lifespan(_app: FastMCP) -> AsyncIterator[None]:  # type: ignore[type-arg]
    task: asyncio.Task[None] | None = None
    if NTFY_URL:
        task = asyncio.create_task(notification_loop(engine))
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


mcp = FastMCP("tracker", lifespan=lifespan)


class Measurement(BaseModel):
    """One key/value measurement within a batch insert."""

    key: str
    value: float


@mcp.tool()
def get_schema() -> str:
    """Return the column names and types for all tables in the tracking database."""
    with engine.connect() as conn:
        result = conn.execute(
            text("""
            SELECT table_name, column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = 'public'
            ORDER BY table_name, ordinal_position
        """)
        )
        rows = result.fetchall()
    if not rows:
        return "No tables found"
    out: list[str] = []
    current_table = None
    for table_name, column_name, data_type, is_nullable in rows:
        if table_name != current_table:
            out.append(f"\n{table_name}")
            current_table = table_name
        nullable = "" if is_nullable == "YES" else " NOT NULL"
        out.append(f"  {column_name}  {data_type}{nullable}")
    return "\n".join(out).strip()


@mcp.tool()
def new_key(name: str, unit: str, frequency: str | None = None) -> str:
    """Register a new measurement key. Keys must be registered before use in insert.

    Use dot-separated snake_case for hierarchical keys, e.g. 'workout.bicep_curl'.
    unit must already be registered via new_unit.

    Optionally set a tracking frequency — how often the measurement is meant to
    be recorded — as 'daily', 'weekly', 'monthly', or an 'n weekly' form like
    'every 2 weeks' / '3 weekly'.
    """
    unit_count = parse_frequency(frequency) if frequency is not None else None
    with Session(engine) as session:
        if session.get(TrackingKey, name):
            raise ValueError(f"Key '{name}' already exists")
        if not session.get(TrackingUnit, unit):
            raise ValueError(f"Unknown unit '{unit}' — register it first with new_unit")
        key = TrackingKey(name=name, unit=unit)
        if unit_count is not None:
            key.frequency_unit, key.frequency_count = unit_count
        session.add(key)
        session.commit()
        suffix = f" ({key.frequency})" if key.frequency else ""
        return f"Registered key: {name} [{unit}]{suffix}"


@mcp.tool()
def set_key_frequency(name: str, frequency: str | None) -> str:
    """Set, change, or clear the tracking frequency of an existing key.

    Pass a frequency string ('daily', 'weekly', 'monthly', or an 'n weekly' form
    like 'every 2 weeks' / '3 weekly') to set it, or null/empty to clear it.
    """
    unit_count = parse_frequency(frequency) if frequency else None
    with Session(engine) as session:
        key = session.get(TrackingKey, name)
        if key is None:
            raise ValueError(f"Unknown key '{name}' — register it first with new_key")
        if unit_count is None:
            key.frequency_unit, key.frequency_count = None, None
        else:
            key.frequency_unit, key.frequency_count = unit_count
        session.add(key)
        session.commit()
        if key.frequency:
            return f"Set frequency of '{name}' to {key.frequency}"
        return f"Cleared frequency of '{name}'"


@mcp.tool()
def new_unit(name: str) -> str:
    """Register a new measurement unit. Units must be registered before use in insert.

    Use snake_case. Prefer SI notation where applicable, e.g. 'sec', 'ms', 'kg', 'm', 'count'.
    """
    with Session(engine) as session:
        if session.get(TrackingUnit, name):
            raise ValueError(f"Unit '{name}' already exists")
        # TrackingUnit validates the name format at the ORM layer.
        session.add(TrackingUnit(name=name))
        session.commit()
        return f"Registered unit: {name}"


@mcp.tool()
def rename_key(old_name: str, new_name: str) -> str:
    """Rename a measurement key, repointing all existing measurements to the new name.

    new_name must be a valid, unregistered key (dot-separated snake_case,
    e.g. 'workout.bicep_curl'). All tracking rows referencing old_name are
    moved to new_name atomically.
    """
    if old_name == new_name:
        raise ValueError("old_name and new_name are the same")
    with Session(engine) as session:
        old_key = session.get(TrackingKey, old_name)
        if old_key is None:
            raise ValueError(f"Unknown key '{old_name}'")
        if session.get(TrackingKey, new_name):
            raise ValueError(f"Key '{new_name}' already exists")
        # Insert the new key, repoint measurements, then drop the old key.
        # The tracking.key FK has no ON UPDATE CASCADE, so this ordering
        # keeps every row referencing a live key throughout.
        # TrackingKey validates new_name's format at the ORM layer.
        session.add(
            TrackingKey(
                name=new_name,
                unit=old_key.unit,
                frequency_unit=old_key.frequency_unit,
                frequency_count=old_key.frequency_count,
            )
        )
        session.flush()
        rowcount = session.exec(
            update(Tracking).where(col(Tracking.key) == old_name).values(key=new_name)
        ).rowcount
        session.flush()
        session.delete(session.get(TrackingKey, old_name))
        session.commit()
    return (
        f"Renamed key '{old_name}' → '{new_name}' ({rowcount} measurement(s) updated)"
    )


@mcp.tool()
def rename_unit(old_name: str, new_name: str) -> str:
    """Rename a measurement unit, repointing all existing measurements to the new name.

    new_name must be a valid, unregistered unit (snake_case, e.g. 'kg'). All
    tracking rows referencing old_name are moved to new_name atomically.
    """
    if old_name == new_name:
        raise ValueError("old_name and new_name are the same")
    with Session(engine) as session:
        if not session.get(TrackingUnit, old_name):
            raise ValueError(f"Unknown unit '{old_name}'")
        if session.get(TrackingUnit, new_name):
            raise ValueError(f"Unit '{new_name}' already exists")
        # Insert the new unit, repoint keys, then drop the old unit.
        # The tracking_key.unit FK has no ON UPDATE CASCADE, so this ordering
        # keeps every row referencing a live unit throughout.
        # TrackingUnit validates new_name's format at the ORM layer.
        session.add(TrackingUnit(name=new_name))
        session.flush()
        rowcount = session.exec(
            update(TrackingKey)
            .where(col(TrackingKey.unit) == old_name)
            .values(unit=new_name)
        ).rowcount
        session.flush()
        session.delete(session.get(TrackingUnit, old_name))
        session.commit()
    return f"Renamed unit '{old_name}' → '{new_name}' ({rowcount} key(s) updated)"


@mcp.tool()
def list_keys(level: int = 0) -> str:
    """List registered measurement keys, optionally truncated to a hierarchy depth.

    level=0 returns all keys in full. level=1 returns unique top-level segments
    (e.g. 'workout'), level=2 returns unique two-segment prefixes, and so on.
    """
    with Session(engine) as session:
        keys = session.exec(select(TrackingKey)).all()
    if not keys:
        return "No keys registered"
    if level == 0:
        # At full depth, annotate each key with its tracking frequency if set.
        return "\n".join(
            f"{k.name} ({k.frequency})" if k.frequency else k.name
            for k in sorted(keys, key=lambda k: k.name)
        )
    prefixes = sorted({".".join(k.name.split(".")[:level]) for k in keys})
    return "\n".join(prefixes)


@mcp.tool()
def list_units() -> str:
    """List all registered measurement units."""
    with Session(engine) as session:
        units = session.exec(select(TrackingUnit)).all()
    if not units:
        return "No units registered"
    return "\n".join(u.name for u in units)


@mcp.tool()
def insert(
    key: str,
    value: float,
    latitude: float | None = None,
    longitude: float | None = None,
    meta: dict | None = None,
) -> str:
    """Insert a measurement. key must be registered first via new_key (unit is on the key).

    Keys use dot-separated snake_case hierarchy, e.g. 'workout.bicep_curl'.
    Optionally attach a geocoordinate for where the measurement was taken by
    passing both latitude and longitude (WGS 84 decimal degrees).
    """
    if (latitude is None) != (longitude is None):
        raise ValueError("latitude and longitude must be provided together")
    # make_point validates the coordinate ranges at the models layer.
    location = (
        make_point(latitude, longitude)
        if latitude is not None and longitude is not None
        else None
    )
    with Session(engine) as session:
        tk = session.get(TrackingKey, key)
        if tk is None:
            raise ValueError(f"Unknown key '{key}' — register it first with new_key")
        row = Tracking(key=key, value=value, location=location, meta=meta)
        session.add(row)
        session.commit()
        session.refresh(row)
        where = f" @ ({latitude}, {longitude})" if location else ""
        return (
            f"Inserted id={row.id}: {key}={value} {tk.unit}{where} at {row.created_at}"
        )


@mcp.tool()
def insert_batch(
    measurements: list[Measurement],
    latitude: float | None = None,
    longitude: float | None = None,
    meta: dict | None = None,
) -> str:
    """Insert many measurements that share one location, timestamp, and metadata.

    Each measurement carries its own key/value/unit (all keys and units must be
    registered first via new_key/new_unit). Every row is written with the same
    location (from latitude/longitude, WGS 84 decimal degrees), the same
    created_at timestamp, and the same metadata. The batch is inserted
    atomically — if any key or unit is unknown, nothing is written.
    """
    if not measurements:
        raise ValueError("measurements must not be empty")
    if (latitude is None) != (longitude is None):
        raise ValueError("latitude and longitude must be provided together")
    # make_point validates the coordinate ranges at the models layer.
    location = (
        make_point(latitude, longitude)
        if latitude is not None and longitude is not None
        else None
    )
    created_at = datetime.now(timezone.utc)
    with Session(engine) as session:
        # Validate every distinct key up front so the whole batch fails
        # fast and atomically rather than part-way through.
        for key in sorted({m.key for m in measurements}):
            if not session.get(TrackingKey, key):
                raise ValueError(
                    f"Unknown key '{key}' — register it first with new_key"
                )
        session.add_all(
            [
                Tracking(
                    key=m.key,
                    value=m.value,
                    location=location,
                    created_at=created_at,
                    meta=meta,
                )
                for m in measurements
            ]
        )
        session.commit()
    where = f" @ ({latitude}, {longitude})" if location else ""
    return f"Inserted {len(measurements)} measurement(s){where} at {created_at}"


@mcp.tool()
def update_item(
    id: int,
    key: str | None = None,
    value: float | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    meta: dict | None = None,
) -> str:
    """Update fields of an existing measurement identified by id.

    Only the provided fields are changed; any argument left as None is untouched
    (so this cannot clear location or metadata — omit them to keep them). key,
    if given, must already be registered via new_key. Pass both latitude and
    longitude together to move the measurement's geocoordinate (WGS 84 decimal degrees).
    """
    if (latitude is None) != (longitude is None):
        raise ValueError("latitude and longitude must be provided together")
    with Session(engine) as session:
        row = session.get(Tracking, id)
        if row is None:
            raise ValueError(f"Unknown measurement id={id}")
        if key is not None:
            if not session.get(TrackingKey, key):
                raise ValueError(
                    f"Unknown key '{key}' — register it first with new_key"
                )
            row.key = key
        if value is not None:
            row.value = value
        if latitude is not None and longitude is not None:
            # make_point validates the coordinate ranges at the models layer.
            row.location = make_point(latitude, longitude)
        if meta is not None:
            row.meta = meta
        session.add(row)
        session.commit()
        session.refresh(row)
        tk = session.get(TrackingKey, row.key)
        unit = tk.unit if tk is not None else "?"
        return f"Updated id={row.id}: {row.key}={row.value} {unit} at {row.created_at}"


@mcp.tool()
def delete_item(id: int) -> str:
    """Delete a measurement identified by id."""
    with Session(engine) as session:
        row = session.get(Tracking, id)
        if row is None:
            raise ValueError(f"Unknown measurement id={id}")
        key, value = row.key, row.value
        session.delete(row)
        session.commit()
    return f"Deleted id={id}: {key}={value}"


@mcp.tool()
def query(sql: str) -> str:
    """Execute a read-only SELECT query against the tracking database."""
    if not sql.strip().upper().startswith("SELECT"):
        raise ValueError("Only SELECT queries are permitted")
    with engine.begin() as conn:
        conn.execute(text("SET TRANSACTION READ ONLY"))
        result = conn.execute(text(sql))
        rows = result.fetchall()
    if not rows:
        return "No results"
    keys = list(result.keys())
    lines = ["\t".join(keys)] + ["\t".join(str(v) for v in row) for row in rows]
    return "\n".join(lines)


def main() -> None:
    mcp.run()
