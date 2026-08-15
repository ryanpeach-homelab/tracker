import asyncio
import json
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
    to_utc,
    validate_json_schema,
    validate_metadata,
)
from tracker_mcp.ntfy import NTFY_URL, notification_loop
from tracker_mcp.version import (
    GITHUB_REPO,
    VersionStatus,
    compare,
    current_version,
    latest_release,
)

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
    # Optional free-form per-reading note for this row.
    description: str | None = None


def _unit_schema_for_key(session: Session, key: str) -> dict | None:
    """Return the JSON Schema of the unit backing ``key``, or None if unset.

    Assumes the key exists (callers validate that first). The unit is looked up
    through the key so that metadata validation follows the same key → unit
    path the rest of the schema uses.
    """
    tk = session.get(TrackingKey, key)
    if tk is None:
        return None
    unit = session.get(TrackingUnit, tk.unit)
    return unit.json_schema if unit is not None else None


@mcp.tool()
def get_version() -> str:
    """Report the running server version and whether it is up to date.

    Always returns the installed tracker version. When the GitHub Releases API
    is reachable, it also reports the latest published version and compares the
    two, so you can tell at a glance whether a newer release is available. If the
    API can't be reached (offline homelab, rate limit), the current version is
    still returned with a note that the check was skipped.
    """
    current = current_version()
    lines = [f"tracker {current}"]
    try:
        latest = latest_release()
    except Exception as exc:
        lines.append(f"latest release: check failed ({type(exc).__name__})")
        return "\n".join(lines)
    if latest is None:
        lines.append(f"latest release: none published yet ({GITHUB_REPO})")
        return "\n".join(lines)
    latest_norm = latest.lstrip("v")
    match compare(current, latest):
        case VersionStatus.UP_TO_DATE:
            lines.append(f"latest release: {latest_norm} — up to date")
        case VersionStatus.UPDATE_AVAILABLE:
            lines.append(
                f"latest release: {latest_norm} — update available ({current} → {latest_norm})"
            )
        case VersionStatus.AHEAD:
            lines.append(
                f"latest release: {latest_norm} — this server is ahead of the latest release"
            )
    return "\n".join(lines)


@mcp.tool()
def get_schema() -> str:
    """Return the table columns and each unit's metadata JSON Schema.

    The first section lists the column names and types for all tables. The
    second section lists any unit that defines a metadata ``json_schema`` — the
    JSON Schema its measurements' metadata must satisfy.
    """
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

    with Session(engine) as session:
        units = session.exec(
            select(TrackingUnit).where(col(TrackingUnit.json_schema).is_not(None))
        ).all()
    if units:
        out.append("\nunit metadata schemas")
        for unit in sorted(units, key=lambda u: u.name):
            out.append(f"  {unit.name}: {json.dumps(unit.json_schema)}")
    return "\n".join(out).strip()


@mcp.tool()
def new_key(
    name: str,
    unit: str,
    frequency: str | None = None,
    description: str | None = None,
) -> str:
    """Register a new measurement key. Keys must be registered before use in insert.

    Use dot-separated snake_case for hierarchical keys, e.g. 'workout.bicep_curl'.
    unit must already be registered via new_unit.

    Optionally set a tracking frequency — how often the measurement is meant to
    be recorded — as 'daily', 'weekly', 'monthly', or an 'n weekly' form like
    'every 2 weeks' / '3 weekly'. Optionally attach a free-form description of
    what the key measures.
    """
    unit_count = parse_frequency(frequency) if frequency is not None else None
    with Session(engine) as session:
        if session.get(TrackingKey, name):
            raise ValueError(f"Key '{name}' already exists")
        if not session.get(TrackingUnit, unit):
            raise ValueError(f"Unknown unit '{unit}' — register it first with new_unit")
        key = TrackingKey(name=name, unit=unit, description=description)
        if unit_count is not None:
            key.frequency_unit, key.frequency_count = unit_count
        session.add(key)
        session.commit()
        suffix = f" ({key.frequency})" if key.frequency else ""
        return f"Registered key: {name} [{unit}]{suffix}"


@mcp.tool()
def update_key(
    name: str,
    unit: str | None = None,
    frequency: str | None = None,
    description: str | None = None,
) -> str:
    """Update fields of an existing key. Only the provided fields change.

    Any argument left as null/omitted is untouched. To clear an optional field,
    pass an empty value: '' clears the frequency or description. unit, if given,
    must already be registered via new_unit and cannot be cleared.

    frequency accepts 'daily'/'weekly'/'monthly' or an 'n weekly' form like
    'every 2 weeks' / '3 weekly'.
    """
    with Session(engine) as session:
        key = session.get(TrackingKey, name)
        if key is None:
            raise ValueError(f"Unknown key '{name}' — register it first with new_key")
        changed: list[str] = []
        if unit is not None:
            if not session.get(TrackingUnit, unit):
                raise ValueError(
                    f"Unknown unit '{unit}' — register it first with new_unit"
                )
            key.unit = unit
            changed.append(f"unit={unit}")
        if frequency is not None:
            unit_count = parse_frequency(frequency) if frequency else None
            key.frequency_unit, key.frequency_count = unit_count or (None, None)
            changed.append(f"frequency={key.frequency or 'cleared'}")
        if description is not None:
            key.description = description or None
            changed.append(f"description={'cleared' if not key.description else 'set'}")
        if not changed:
            return f"No changes for key '{name}'"
        session.add(key)
        session.commit()
        return f"Updated key '{name}': {', '.join(changed)}"


@mcp.tool()
def new_unit(
    name: str,
    description: str | None = None,
    json_schema: dict | None = None,
) -> str:
    """Register a new measurement unit. Units must be registered before use in insert.

    Use snake_case. Prefer SI notation where applicable, e.g. 'sec', 'ms', 'kg', 'm', 'count'.
    Optionally attach a free-form description of what the unit measures, and a
    JSON Schema (Draft 2020-12) that validates the metadata of every measurement
    recorded against this unit.
    """
    with Session(engine) as session:
        if session.get(TrackingUnit, name):
            raise ValueError(f"Unit '{name}' already exists")
        # TrackingUnit validates the name format and JSON Schema at the ORM layer.
        session.add(
            TrackingUnit(name=name, description=description, json_schema=json_schema)
        )
        session.commit()
        return f"Registered unit: {name}"


@mcp.tool()
def update_unit(
    name: str,
    description: str | None = None,
    json_schema: dict | None = None,
) -> str:
    """Update fields of an existing unit. Only the provided fields change.

    Any argument left as null/omitted is untouched. To clear an optional field,
    pass an empty value: '' clears the description, {} clears the metadata JSON
    Schema. A provided json_schema (Draft 2020-12) is validated for
    well-formedness; existing measurements are not re-validated.
    """
    if json_schema:
        # Fail fast with a clear error before touching the row.
        validate_json_schema(json_schema)
    with Session(engine) as session:
        unit = session.get(TrackingUnit, name)
        if unit is None:
            raise ValueError(f"Unknown unit '{name}' — register it first with new_unit")
        changed: list[str] = []
        if description is not None:
            unit.description = description or None
            changed.append(
                f"description={'cleared' if not unit.description else 'set'}"
            )
        if json_schema is not None:
            unit.json_schema = json_schema or None
            changed.append(f"schema={'cleared' if not unit.json_schema else 'set'}")
        if not changed:
            return f"No changes for unit '{name}'"
        session.add(unit)
        session.commit()
        return f"Updated unit '{name}': {', '.join(changed)}"


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
                description=old_key.description,
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
        old_unit = session.get(TrackingUnit, old_name)
        if old_unit is None:
            raise ValueError(f"Unknown unit '{old_name}'")
        if session.get(TrackingUnit, new_name):
            raise ValueError(f"Unit '{new_name}' already exists")
        # Insert the new unit, repoint keys, then drop the old unit.
        # The tracking_key.unit FK has no ON UPDATE CASCADE, so this ordering
        # keeps every row referencing a live unit throughout.
        # TrackingUnit validates new_name's format at the ORM layer.
        session.add(
            TrackingUnit(
                name=new_name,
                description=old_unit.description,
                json_schema=old_unit.json_schema,
            )
        )
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
        # At full depth, annotate each key with its tracking frequency and
        # description if set.
        def _fmt(k: TrackingKey) -> str:
            line = f"{k.name} ({k.frequency})" if k.frequency else k.name
            if k.description:
                line += f" — {k.description}"
            return line

        return "\n".join(_fmt(k) for k in sorted(keys, key=lambda k: k.name))
    prefixes = sorted({".".join(k.name.split(".")[:level]) for k in keys})
    return "\n".join(prefixes)


@mcp.tool()
def list_units() -> str:
    """List all registered measurement units."""
    with Session(engine) as session:
        units = session.exec(select(TrackingUnit)).all()
    if not units:
        return "No units registered"
    return "\n".join(
        f"{u.name} — {u.description}" if u.description else u.name for u in units
    )


@mcp.tool()
def insert(
    key: str,
    value: float,
    latitude: float | None = None,
    longitude: float | None = None,
    meta: dict | None = None,
    description: str | None = None,
    created_at: datetime | None = None,
) -> str:
    """Insert a measurement. key must be registered first via new_key (unit is on the key).

    Keys use dot-separated snake_case hierarchy, e.g. 'workout.bicep_curl'.
    Optionally attach a geocoordinate for where the measurement was taken by
    passing both latitude and longitude (WGS 84 decimal degrees). Optionally
    attach a free-form description — a per-reading note distinct from the
    structured meta.

    Pass created_at (ISO 8601) to backdate the measurement; omitted, it defaults
    to now. A naive timestamp is treated as UTC, an aware one is converted to UTC.
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
        # Validate metadata against the unit's JSON Schema, if it has one.
        validate_metadata(_unit_schema_for_key(session, key), meta)
        row = Tracking(
            key=key,
            value=value,
            location=location,
            meta=meta,
            description=description,
        )
        if created_at is not None:
            row.created_at = to_utc(created_at)
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
    created_at: datetime | None = None,
) -> str:
    """Insert many measurements that share one location, timestamp, and metadata.

    Each measurement carries its own key/value/unit and an optional per-reading
    description (all keys and units must be registered first via
    new_key/new_unit). Every row is written with the same location (from
    latitude/longitude, WGS 84 decimal degrees), the same created_at timestamp,
    and the same metadata. The batch is inserted atomically — if any key or unit
    is unknown, nothing is written.

    Pass created_at (ISO 8601) to backdate the whole batch; omitted, it defaults
    to now. A naive timestamp is treated as UTC, an aware one is converted to UTC.
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
    created_at = (
        to_utc(created_at) if created_at is not None else datetime.now(timezone.utc)
    )
    with Session(engine) as session:
        # Validate every distinct key up front so the whole batch fails
        # fast and atomically rather than part-way through. The shared metadata
        # must satisfy each key's unit schema.
        for key in sorted({m.key for m in measurements}):
            if not session.get(TrackingKey, key):
                raise ValueError(
                    f"Unknown key '{key}' — register it first with new_key"
                )
            validate_metadata(_unit_schema_for_key(session, key), meta)
        session.add_all(
            [
                Tracking(
                    key=m.key,
                    value=m.value,
                    location=location,
                    created_at=created_at,
                    meta=meta,
                    description=m.description,
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
    description: str | None = None,
    created_at: datetime | None = None,
) -> str:
    """Update fields of an existing measurement identified by id.

    Only the provided fields are changed; any argument left as None is untouched
    (so this cannot clear location or metadata — omit them to keep them). key,
    if given, must already be registered via new_key. Pass both latitude and
    longitude together to move the measurement's geocoordinate (WGS 84 decimal
    degrees). Pass description to set the per-reading note, or '' to clear it.
    Pass created_at (ISO 8601) to backdate the measurement; a naive timestamp is
    treated as UTC, an aware one is converted to UTC.
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
        if description is not None:
            # '' clears the note; any other string sets it.
            row.description = description or None
        if created_at is not None:
            row.created_at = to_utc(created_at)
        # If the key or metadata changed, re-validate the effective metadata
        # against the (possibly new) unit's JSON Schema.
        if key is not None or meta is not None:
            validate_metadata(_unit_schema_for_key(session, row.key), row.meta)
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
