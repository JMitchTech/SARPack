"""Add patient_assessments table

Revision ID: 0004_add_patient_assessments
Revises: 0003_add_schedules
Create Date: 2025-01-01 00:00:03.000000
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0004_add_patient_assessments"
down_revision: Union[str, None] = "0003_add_schedules"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "patient_assessments",
        sa.Column("id",                 sa.Text, primary_key=True),
        sa.Column("incident_id",        sa.Text, sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("assessed_by",        sa.Text, sa.ForeignKey("personnel.id")),
        sa.Column("assessed_at",        sa.Text, nullable=False),
        sa.Column("patient_name",       sa.Text),
        sa.Column("patient_age",        sa.Integer),
        sa.Column("patient_sex",        sa.Text),
        sa.Column("chief_complaint",    sa.Text),
        sa.Column("complaint_category", sa.Text, server_default="Unknown"),
        sa.Column("mechanism_of_injury",sa.Text),
        sa.Column("scene_location",     sa.Text),
        sa.Column("scene_lat",          sa.Float),
        sa.Column("scene_lng",          sa.Float),
        sa.Column("loc",                sa.Text, server_default="Alert"),
        sa.Column("vitals",             sa.Text),   # JSON
        sa.Column("physical_exam",      sa.Text),   # JSON
        sa.Column("treatment_given",    sa.Text),
        sa.Column("notes",              sa.Text),
        sa.Column("disposition",        sa.Text),
        sa.Column("version",            sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at",         sa.Text, nullable=False),
        sa.Column("updated_at",         sa.Text, nullable=False),
    )
    op.create_index("idx_patient_incident", "patient_assessments", ["incident_id"])
    op.create_index("idx_patient_assessed", "patient_assessments", ["assessed_by"])


def downgrade() -> None:
    op.drop_table("patient_assessments")
