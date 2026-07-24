"""initial schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-07-21 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tracking_key",
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )
    op.create_table(
        "tracking_unit",
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )
    op.create_table(
        "tracking",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("unit", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["key"], ["tracking_key.name"]),
        sa.ForeignKeyConstraint(["unit"], ["tracking_unit.name"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tracking_key"), "tracking", ["key"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_tracking_key"), table_name="tracking")
    op.drop_table("tracking")
    op.drop_table("tracking_unit")
    op.drop_table("tracking_key")
