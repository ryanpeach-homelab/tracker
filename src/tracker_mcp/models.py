import re
from datetime import datetime, timezone
from typing import Any

from geoalchemy2 import Geography
from geoalchemy2.elements import WKTElement
from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import validates
from sqlmodel import Field, SQLModel

# Validation lives on the ORM models (via SQLAlchemy @validates) so that every
# write path — MCP tools, migrations, or direct ORM use — is checked, not just
# the MCP tool entrypoints.
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")
_UNIT_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def make_point(latitude: float, longitude: float) -> WKTElement:
    """Build a validated WGS 84 (SRID 4326) point from decimal degrees.

    This is the canonical way to construct a ``Tracking.location`` value.
    Range validation lives here in the models module so every write path shares
    it, rather than only the MCP tool layer.
    """
    if not -90 <= latitude <= 90:
        raise ValueError(f"latitude {latitude} out of range [-90, 90]")
    if not -180 <= longitude <= 180:
        raise ValueError(f"longitude {longitude} out of range [-180, 180]")
    return WKTElement(f"POINT({longitude} {latitude})", srid=4326)


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
    # WGS 84 (SRID 4326) geographic point — (longitude, latitude) on the earth.
    # spatial_index is left off to keep the schema minimal; add one via a
    # migration if location queries need it.
    location: Any | None = Field(
        default=None,
        sa_column=Column(
            Geography(geometry_type="POINT", srid=4326, spatial_index=False),
            nullable=True,
        ),
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    # "metadata" is reserved by SQLAlchemy declarative; column is named metadata in DB
    meta: dict | None = Field(
        default=None, sa_column=Column("metadata", JSONB, nullable=True)
    )

    @validates("location")
    def _validate_location(self, _key: str, value: Any) -> Any:
        """Guard against storing raw coordinates on the ORM.

        A ``location`` must be a geometry element (or ``None``); build one with
        ``make_point(latitude, longitude)``, which validates the coordinate
        ranges. Rejecting a bare pair here turns an otherwise cryptic database
        error into a clear one.
        """
        if isinstance(value, (tuple, list)):
            raise ValueError("build location with make_point(latitude, longitude)")
        return value
