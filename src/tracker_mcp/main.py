import os
import re
from datetime import datetime, timezone

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")

from fastmcp import FastMCP
from sqlalchemy import Column, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, Session, SQLModel, create_engine, select

DATABASE_URI = os.environ["DATABASE_URI"]
engine = create_engine(DATABASE_URI)


class TrackingKey(SQLModel, table=True):
    __tablename__ = "tracking_key"
    name: str = Field(primary_key=True)


class TrackingUnit(SQLModel, table=True):
    __tablename__ = "tracking_unit"
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
    meta: dict | None = Field(default=None, sa_column=Column("metadata", JSONB, nullable=True))


SQLModel.metadata.create_all(engine)

mcp = FastMCP("tracker")


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
    """Register a new measurement unit. Units must be registered before use in insert."""
    with Session(engine) as session:
        if session.get(TrackingUnit, name):
            raise ValueError(f"Unit '{name}' already exists")
        session.add(TrackingUnit(name=name))
        session.commit()
        return f"Registered unit: {name}"


@mcp.tool()
def list_keys() -> str:
    """List all registered measurement keys (dot-separated snake_case hierarchy, e.g. 'workout.bicep_curl')."""
    with Session(engine) as session:
        keys = session.exec(select(TrackingKey)).all()
    if not keys:
        return "No keys registered"
    return "\n".join(k.name for k in keys)


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
