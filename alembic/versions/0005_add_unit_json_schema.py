"""add json_schema to tracking_unit

Revision ID: 0005_add_unit_json_schema
Revises: 0004_add_description
Create Date: 2026-08-12 00:00:00.000000

Adds an optional JSON Schema (stored as JSONB) to ``tracking_unit``. When set,
it validates the metadata of every measurement recorded against that unit.
Nullable, so existing rows are unaffected.

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005_add_unit_json_schema"
down_revision: str | None = "0004_add_description"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tracking_unit",
        sa.Column(
            "json_schema", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
    )


def downgrade() -> None:
    op.drop_column("tracking_unit", "json_schema")
