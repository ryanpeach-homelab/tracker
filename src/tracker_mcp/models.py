import re
from datetime import datetime, timezone
from typing import Any

from geoalchemy2 import Geography
from geoalchemy2.elements import WKTElement
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from sqlalchemy import CheckConstraint, Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import validates
from sqlmodel import Field, SQLModel

# Validation lives on the ORM models (via SQLAlchemy @validates) so that every
# write path — MCP tools, migrations, or direct ORM use — is checked, not just
# the MCP tool entrypoints.
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")
_UNIT_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# A tracking key can carry an optional tracking *frequency* — how often the
# measurement is meant to be recorded. It's stored as a (unit, count) pair so
# that the parametric "every n weeks" case is captured exactly, without the
# month-vs-30-days lossiness of a raw INTERVAL/timedelta. daily/weekly/monthly
# are just count=1; "n weekly" is unit='week', count=n.
FREQUENCY_UNITS = ("day", "week", "month")
# Single-word adverbs map to a period unit with count 1.
_FREQUENCY_ADVERBS = {"daily": "day", "weekly": "week", "monthly": "month"}
# Plural/singular nouns map to their period unit ("2 weeks" -> week).
_FREQUENCY_NOUNS = {
    "day": "day",
    "days": "day",
    "week": "week",
    "weeks": "week",
    "month": "month",
    "months": "month",
}


def parse_frequency(value: str) -> tuple[str, int]:
    """Parse a friendly frequency string into a ``(unit, count)`` pair.

    Accepts, case-insensitively:

    * ``"daily"`` / ``"weekly"`` / ``"monthly"`` → count 1
    * ``"n weekly"`` (and ``"n daily"`` / ``"n monthly"``) → count n
    * ``"n days"`` / ``"n weeks"`` / ``"n months"`` (singular or plural)
    * an optional leading ``"every"`` (e.g. ``"every 2 weeks"``)

    ``unit`` is one of :data:`FREQUENCY_UNITS`; ``count`` is a positive int.
    """
    tokens = value.strip().lower().split()
    if tokens and tokens[0] == "every":
        tokens = tokens[1:]

    if len(tokens) == 1:
        (word,) = tokens
        if word in _FREQUENCY_ADVERBS:
            return _FREQUENCY_ADVERBS[word], 1
        if word in _FREQUENCY_NOUNS:  # "every week"
            return _FREQUENCY_NOUNS[word], 1
    elif len(tokens) == 2:
        count_str, word = tokens
        if count_str.isdigit():
            count = int(count_str)
            if count < 1:
                raise ValueError("frequency count must be a positive integer")
            if word in _FREQUENCY_ADVERBS:  # "3 weekly"
                return _FREQUENCY_ADVERBS[word], count
            if word in _FREQUENCY_NOUNS:  # "3 weeks"
                return _FREQUENCY_NOUNS[word], count

    raise ValueError(
        f"Invalid frequency '{value}' — use 'daily', 'weekly', 'monthly', "
        "or 'n weekly' / 'every n weeks' (n a positive integer)"
    )


def format_frequency(unit: str, count: int) -> str:
    """Render a ``(unit, count)`` frequency back into a friendly string.

    The inverse of :func:`parse_frequency` for the canonical forms: count 1
    becomes the adverb (``"weekly"``), higher counts become ``"every n weeks"``.
    """
    if count == 1:
        return {"day": "daily", "week": "weekly", "month": "monthly"}[unit]
    return f"every {count} {unit}s"


def validate_json_schema(schema: dict) -> dict:
    """Validate that ``schema`` is itself a well-formed JSON Schema.

    A unit's ``json_schema`` is used to validate the metadata of every
    measurement recorded against that unit, so it must be a valid schema to
    begin with. Draft 2020-12 is used. Returns the schema unchanged on success.
    """
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as e:
        raise ValueError(f"Invalid JSON Schema: {e.message}") from e
    return schema


def validate_metadata(schema: dict | None, meta: dict | None) -> None:
    """Validate a measurement's metadata against a unit's JSON Schema.

    A ``None`` schema means the unit imposes no constraints (anything goes).
    A ``None`` metadata is treated as an empty object so that ``required``
    fields are still enforced. Raises ``ValueError`` on a mismatch.
    """
    if schema is None:
        return
    try:
        Draft202012Validator(schema).validate(meta if meta is not None else {})
    except ValidationError as e:
        raise ValueError(f"metadata does not match unit schema: {e.message}") from e


def to_utc(dt: datetime) -> datetime:
    """Normalize a datetime to an aware UTC datetime.

    A naive datetime is assumed to already be in UTC; an aware one is converted.
    Lives here in the models module so every write path shares one timestamp
    normalization, matching where ``created_at`` is defaulted.
    """
    return (
        dt.replace(tzinfo=timezone.utc)
        if dt.tzinfo is None
        else dt.astimezone(timezone.utc)
    )


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
    # A frequency is either fully set (both columns) or fully absent (both NULL).
    __table_args__ = (
        CheckConstraint(
            "(frequency_unit IS NULL) = (frequency_count IS NULL)",
            name="ck_tracking_key_frequency_both_or_neither",
        ),
    )
    name: str = Field(primary_key=True)
    unit: str = Field(foreign_key="tracking_unit.name")
    # Free-form human description of what this key measures.
    description: str | None = Field(default=None)
    # Optional tracking frequency, stored as a period unit + positive count.
    # See parse_frequency/format_frequency for the friendly-string mapping.
    frequency_unit: str | None = Field(default=None)
    frequency_count: int | None = Field(default=None)

    @validates("name")
    def _validate_name(self, _key: str, value: str) -> str:
        if not _KEY_RE.match(value):
            raise ValueError(
                f"Invalid key '{value}' — keys must be dot-separated snake_case, e.g. 'workout.bicep_curl'"
            )
        return value

    @validates("frequency_unit")
    def _validate_frequency_unit(self, _key: str, value: str | None) -> str | None:
        if value is not None and value not in FREQUENCY_UNITS:
            raise ValueError(
                f"Invalid frequency unit '{value}' — must be one of {FREQUENCY_UNITS}"
            )
        return value

    @validates("frequency_count")
    def _validate_frequency_count(self, _key: str, value: int | None) -> int | None:
        if value is not None and value < 1:
            raise ValueError("frequency_count must be a positive integer")
        return value

    @property
    def frequency(self) -> str | None:
        """The friendly frequency label (e.g. 'weekly'), or None if unset."""
        if self.frequency_unit is None or self.frequency_count is None:
            return None
        return format_frequency(self.frequency_unit, self.frequency_count)


class TrackingUnit(SQLModel, table=True):
    __tablename__ = "tracking_unit"  # pyright: ignore[reportAssignmentType]
    name: str = Field(primary_key=True)
    # Free-form human description of what this unit measures.
    description: str | None = Field(default=None)
    # Optional JSON Schema (Draft 2020-12) validating the metadata of every
    # measurement recorded against this unit. NULL means no constraint.
    json_schema: dict | None = Field(
        default=None,
        sa_column=Column("json_schema", JSONB, nullable=True),
    )

    @validates("name")
    def _validate_name(self, _key: str, value: str) -> str:
        if not _UNIT_RE.match(value):
            raise ValueError(
                f"Invalid unit '{value}' — units must be snake_case, e.g. 'sec', 'ms', 'count'"
            )
        return value

    @validates("json_schema")
    def _validate_json_schema(self, _key: str, value: dict | None) -> dict | None:
        if value is not None:
            validate_json_schema(value)
        return value


class Tracking(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    key: str = Field(foreign_key="tracking_key.name", index=True)
    value: float
    # Free-form human description of this individual measurement (a per-reading
    # note). Distinct from the structured ``meta`` JSONB; NULL means unset.
    description: str | None = Field(default=None)
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
