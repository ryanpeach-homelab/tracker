"""add description to tracking_key and tracking_unit

Revision ID: 0004_add_description
Revises: 36b7c813de76
Create Date: 2026-08-12 00:00:00.000000

Adds an optional free-form ``description`` column to both ``tracking_key`` and
``tracking_unit``. Nullable, so existing rows are unaffected.

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004_add_description"
down_revision: str | None = "36b7c813de76"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tracking_key",
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )
    op.add_column(
        "tracking_unit",
        sa.Column("description", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tracking_unit", "description")
    op.drop_column("tracking_key", "description")
