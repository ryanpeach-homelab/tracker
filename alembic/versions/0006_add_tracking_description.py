"""add description to tracking

Revision ID: 0006_add_tracking_description
Revises: 0005_add_unit_json_schema
Create Date: 2026-08-12 00:00:00.000000

Adds an optional free-form ``description`` column to ``tracking`` — a
per-reading note distinct from the structured ``metadata`` JSONB. Nullable, so
existing rows are unaffected.

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006_add_tracking_description"
down_revision: str | None = "0005_add_unit_json_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tracking",
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tracking", "description")
