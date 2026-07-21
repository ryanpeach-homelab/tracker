import re
from datetime import datetime, timezone

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import validates
from sqlmodel import Field, SQLModel

# Validation lives on the ORM models (via SQLAlchemy @validates) so that every
# write path — MCP tools, migrations, or direct ORM use — is checked, not just
# the MCP tool entrypoints.
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")
_UNIT_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class TrackingKey(SQLModel, table=True):
    __tablename__ = "tracking_key"  # pyright: ignore[reportAssignmentType]
    name: str = Field(primary_key=True)

    @validates("name")
    def _validate_name(self, _key: str, value: str) -> str:
        if not _KEY_RE.match(value):
            raise ValueError(
                f"Invalid key '{value}' — keys must be dot-separated snake_case, e.g. 'workout.bicep_curl'"
            )
        return value


class TrackingUnit(SQLModel, table=True):
    __tablename__ = "tracking_unit"  # pyright: ignore[reportAssignmentType]
    name: str = Field(primary_key=True)

    @validates("name")
    def _validate_name(self, _key: str, value: str) -> str:
        if not _UNIT_RE.match(value):
            raise ValueError(
                f"Invalid unit '{value}' — units must be snake_case, e.g. 'sec', 'ms', 'count'"
            )
        return value


class Tracking(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    key: str = Field(foreign_key="tracking_key.name", index=True)
    value: float
    unit: str = Field(foreign_key="tracking_unit.name")
    location: str | None = Field(default=None)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    # "metadata" is reserved by SQLAlchemy declarative; column is named metadata in DB
    meta: dict | None = Field(
        default=None, sa_column=Column("metadata", JSONB, nullable=True)
    )

    @validates("location")
    def _validate_location(self, _key: str, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("location must be a non-empty string or omitted")
        return value
