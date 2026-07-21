import os
import re
from datetime import datetime, timezone

from fastmcp import FastMCP
from sqlalchemy import Column, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Session, SQLModel, create_engine, select

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")
_UNIT_RE = re.compile(r"^[a-z][a-z0-9_]*$")

DATABASE_URI = os.environ["DATABASE_URI"]
engine = create_engine(DATABASE_URI)


class TrackingKey(SQLModel, table=True):
    __tablename__ = "tracking_key"  # pyright: ignore[reportAssignmentType]
    name: str = Field(primary_key=True)


class TrackingUnit(SQLModel, table=True):
    __tablename__ = "tracking_unit"  # pyright: ignore[reportAssignmentType]
    name: str = Field(primary_key=True)


class Tracking(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    key: str = Field(foreign_key="tracking_key.name", index=True)
    value: float
    unit: str = Field(foreign_key="tracking_unit.name")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    # "metadata" is reserved by SQLAlchemy declarative; column is named metadata in DB
    meta: dict | None = Field(
        default=None, sa_column=Column("metadata", JSONB, nullable=True)
    )


SQLModel.metadata.create_all(engine)

mcp = FastMCP("tracker")


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
def new_key(name: str) -> str:
    """Register a new measurement key. Keys must be registered before use in insert.

    Use dot-separated snake_case for hierarchical keys, e.g. 'workout.bicep_curl'.
    """
    if not _KEY_RE.match(name):
        raise ValueError(
            f"Invalid key '{name}' — keys must be dot-separated snake_case, e.g. 'workout.bicep_curl'"
        )
    with Session(engine) as session:
        if session.get(TrackingKey, name):
            raise ValueError(f"Key '{name}' already exists")
        session.add(TrackingKey(name=name))
        session.commit()
        return f"Registered key: {name}"


@mcp.tool()
def new_unit(name: str) -> str:
    """Register a new measurement unit. Units must be registered before use in insert.

    Use snake_case. Prefer SI notation where applicable, e.g. 'sec', 'ms', 'kg', 'm', 'count'.
    """
    if not _UNIT_RE.match(name):
        raise ValueError(
            f"Invalid unit '{name}' — units must be snake_case, e.g. 'sec', 'ms', 'count'"
        )
    with Session(engine) as session:
        if session.get(TrackingUnit, name):
            raise ValueError(f"Unit '{name}' already exists")
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
    if not _KEY_RE.match(new_name):
        raise ValueError(
            f"Invalid key '{new_name}' — keys must be dot-separated snake_case, e.g. 'workout.bicep_curl'"
        )
    if old_name == new_name:
        raise ValueError("old_name and new_name are the same")
    with Session(engine) as session:
        if not session.get(TrackingKey, old_name):
            raise ValueError(f"Unknown key '{old_name}'")
        if session.get(TrackingKey, new_name):
            raise ValueError(f"Key '{new_name}' already exists")
        # Insert the new key, repoint measurements, then drop the old key.
        # The tracking.key FK has no ON UPDATE CASCADE, so this ordering
        # keeps every row referencing a live key throughout.
        session.add(TrackingKey(name=new_name))
        session.flush()
        rows = session.exec(select(Tracking).where(Tracking.key == old_name)).all()
        for row in rows:
            row.key = new_name
            session.add(row)
        session.flush()
        session.delete(session.get(TrackingKey, old_name))
        session.commit()
    return (
        f"Renamed key '{old_name}' → '{new_name}' ({len(rows)} measurement(s) updated)"
    )


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
    names = [k.name for k in keys]
    if level == 0:
        return "\n".join(sorted(names))
    prefixes = sorted({".".join(name.split(".")[:level]) for name in names})
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
def insert(key: str, value: float, unit: str, meta: dict | None = None) -> str:
    """Insert a measurement. key and unit must be registered first via new_key/new_unit.

    Keys use dot-separated snake_case hierarchy, e.g. 'workout.bicep_curl'.
    """
    with Session(engine) as session:
        if not session.get(TrackingKey, key):
            raise ValueError(f"Unknown key '{key}' — register it first with new_key")
        if not session.get(TrackingUnit, unit):
            raise ValueError(f"Unknown unit '{unit}' — register it first with new_unit")
        row = Tracking(key=key, value=value, unit=unit, meta=meta)
        session.add(row)
        session.commit()
        session.refresh(row)
        return f"Inserted id={row.id}: {key}={value} {unit} at {row.created_at}"


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
