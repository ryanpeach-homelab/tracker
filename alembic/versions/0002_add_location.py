"""add location geography column to tracking

Revision ID: 0002_add_location
Revises: 0001_initial_schema
Create Date: 2026-07-21 00:00:01.000000

"""

from collections.abc import Sequence

import geoalchemy2
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0002_add_location"
down_revision: str | None = "0001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    op.add_column(
        "tracking",
        sa.Column(
            "location",
            geoalchemy2.Geography(
                geometry_type="POINT", srid=4326, spatial_index=False
            ),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("tracking", "location")
    # The postgis extension is left in place; dropping it could affect other
    # schemas that depend on it.
