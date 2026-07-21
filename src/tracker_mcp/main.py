import os
from datetime import datetime, timezone

from fastmcp import FastMCP
from sqlalchemy import text
from sqlmodel import Field, Session, SQLModel, create_engine

DATABASE_URI = os.environ["DATABASE_URI"]
engine = create_engine(DATABASE_URI)


class Tracking(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    key: str = Field(index=True)
    value: float
    unit: str
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


SQLModel.metadata.create_all(engine)

mcp = FastMCP("tracker")


@mcp.tool()
def insert(key: str, value: float, unit: str) -> str:
    """Insert a measurement into the tracking table."""
    with Session(engine) as session:
        row = Tracking(key=key, value=value, unit=unit)
        session.add(row)
        session.commit()
        session.refresh(row)
        return f"Inserted id={row.id}: {key}={value} {unit} at {row.created_at}"


@mcp.tool()
def query(sql: str) -> str:
    """Execute a read-only SELECT query against the tracking table."""
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
