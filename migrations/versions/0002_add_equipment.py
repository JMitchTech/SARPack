"""Add equipment table

Revision ID: 0002_add_equipment
Revises: 0001_baseline
Create Date: 2025-01-01 00:00:01.000000
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0002_add_equipment"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "equipment",
        sa.Column("id",            sa.Text, primary_key=True),
        sa.Column("personnel_id",  sa.Text, sa.ForeignKey("personnel.id"), nullable=False),
        sa.Column("item_name",     sa.Text, nullable=False),
        sa.Column("serial_number", sa.Text),
        sa.Column("condition",     sa.Text, nullable=False, server_default="serviceable"),
        sa.Column("assigned_date", sa.Text),
        sa.Column("expiry_date",   sa.Text),
        sa.Column("notes",         sa.Text),
        sa.Column("version",       sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at",    sa.Text, nullable=False),
        sa.Column("updated_at",    sa.Text, nullable=False),
    )
    op.create_index("idx_equipment_personnel", "equipment", ["personnel_id"])


def downgrade() -> None:
    op.drop_table("equipment")
