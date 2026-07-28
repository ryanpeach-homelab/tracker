"""add tracking frequency to tracking_key

Revision ID: 0003_add_key_frequency
Revises: 0002_add_location
Create Date: 2026-07-28 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003_add_key_frequency"
down_revision: str | None = "0002_add_location"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tracking_key",
        sa.Column("frequency_unit", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    )
    op.add_column(
        "tracking_key",
        sa.Column("frequency_count", sa.Integer(), nullable=True),
    )
    # A frequency is either fully set (both columns) or fully absent (both NULL).
    op.create_check_constraint(
        "ck_tracking_key_frequency_both_or_neither",
        "tracking_key",
        "(frequency_unit IS NULL) = (frequency_count IS NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_tracking_key_frequency_both_or_neither",
        "tracking_key",
        type_="check",
    )
    op.drop_column("tracking_key", "frequency_count")
    op.drop_column("tracking_key", "frequency_unit")
