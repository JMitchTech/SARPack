"""Add schedules table

Revision ID: 0003_add_schedules
Revises: 0002_add_equipment
Create Date: 2025-01-01 00:00:02.000000
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0003_add_schedules"
down_revision: Union[str, None] = "0002_add_equipment"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "schedules",
        sa.Column("id",           sa.Text, primary_key=True),
        sa.Column("personnel_id", sa.Text, sa.ForeignKey("personnel.id"), nullable=False),
        sa.Column("shift_name",   sa.Text, nullable=False),
        sa.Column("starts_at",    sa.Text, nullable=False),  # ISO 8601 datetime
        sa.Column("ends_at",      sa.Text, nullable=False),  # ISO 8601 datetime
        sa.Column("is_oncall",    sa.Integer, nullable=False, server_default="1"),
        sa.Column("notes",        sa.Text),
        sa.Column("version",      sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at",   sa.Text, nullable=False),
        sa.Column("updated_at",   sa.Text, nullable=False),
    )
    op.create_index("idx_schedules_personnel", "schedules", ["personnel_id"])
    op.create_index("idx_schedules_starts",    "schedules", ["starts_at"])
    op.create_index("idx_schedules_ends",      "schedules", ["ends_at"])


def downgrade() -> None:
    op.drop_table("schedules")
