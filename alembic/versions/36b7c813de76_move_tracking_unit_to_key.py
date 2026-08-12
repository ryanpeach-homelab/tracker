"""Move tracking unit to key

Revision ID: 36b7c813de76
Revises: 0003_add_key_frequency
Create Date: 2026-08-12 01:03:49.896505

The unit moves from the per-measurement ``tracking`` table onto ``tracking_key``
(each key now has a single unit). The Alembic autogenerate output dropped
``tracking.unit`` and then added ``tracking_key.unit`` as ``NOT NULL`` with no
backfill, which fails on any non-empty database:

    (psycopg2.errors.NotNullViolation) column "unit" of relation
    "tracking_key" contains null values

This revision instead adds the column nullable, backfills it from existing
data, and only then enforces ``NOT NULL`` + the foreign key — mirrored in
``downgrade``.
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "36b7c813de76"
down_revision: str | None = "0003_add_key_frequency"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Add the new column as nullable so existing rows survive; the FK and
    #    NOT NULL are applied below once every row has a value.
    op.add_column(
        "tracking_key",
        sa.Column("unit", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )

    # 2. Backfill each key's unit from its measurements. Historically a key's
    #    measurements all shared one unit; take the most recent if they didn't.
    op.execute(
        """
        UPDATE tracking_key AS tk
        SET unit = sub.unit
        FROM (
            SELECT DISTINCT ON (key) key, unit
            FROM tracking
            ORDER BY key, created_at DESC
        ) AS sub
        WHERE tk.name = sub.key
        """
    )

    # 3. Keys with no measurements have no unit to inherit. When the database
    #    defines exactly one unit, adopt it unambiguously; otherwise leave NULL
    #    so the NOT NULL below fails loudly rather than inventing wrong data.
    op.execute(
        """
        UPDATE tracking_key
        SET unit = (SELECT name FROM tracking_unit)
        WHERE unit IS NULL
          AND (SELECT count(*) FROM tracking_unit) = 1
        """
    )

    # 4. Enforce the model invariants now that every row is populated.
    op.alter_column("tracking_key", "unit", nullable=False)
    op.create_foreign_key(
        op.f("tracking_key_unit_fkey"),
        "tracking_key",
        "tracking_unit",
        ["unit"],
        ["name"],
    )

    # 5. Drop the now-redundant per-measurement unit.
    op.drop_constraint(op.f("tracking_unit_fkey"), "tracking", type_="foreignkey")
    op.drop_column("tracking", "unit")


def downgrade() -> None:
    # Reverse: restore tracking.unit, backfill from the key, then drop it off
    # tracking_key.
    op.add_column(
        "tracking",
        sa.Column("unit", sa.VARCHAR(), autoincrement=False, nullable=True),
    )
    op.execute(
        """
        UPDATE tracking AS t
        SET unit = tk.unit
        FROM tracking_key AS tk
        WHERE t.key = tk.name
        """
    )
    op.alter_column("tracking", "unit", nullable=False)
    op.create_foreign_key(
        op.f("tracking_unit_fkey"), "tracking", "tracking_unit", ["unit"], ["name"]
    )

    op.drop_constraint(
        op.f("tracking_key_unit_fkey"), "tracking_key", type_="foreignkey"
    )
    op.drop_column("tracking_key", "unit")
