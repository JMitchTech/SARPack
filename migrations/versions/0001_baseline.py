"""Initial schema — SARPack v1.0

Baseline migration. Creates all tables from scratch.
Running `alembic upgrade head` on a fresh database applies this first,
then any subsequent migrations in order.

Revision ID: 0001_baseline
Revises: None
Create Date: 2025-01-01 00:00:00.000000
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -----------------------------------------------------------------------
    # Core operational tables
    # -----------------------------------------------------------------------

    op.create_table(
        "personnel",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("first_name", sa.Text, nullable=False),
        sa.Column("last_name", sa.Text, nullable=False),
        sa.Column("call_sign", sa.Text, unique=True),
        sa.Column("phone", sa.Text),
        sa.Column("email", sa.Text, unique=True),
        sa.Column("blood_type", sa.Text),
        sa.Column("allergies", sa.Text),
        sa.Column("medical_notes", sa.Text),
        sa.Column("emergency_contact_name", sa.Text),
        sa.Column("emergency_contact_phone", sa.Text),
        sa.Column("is_active", sa.Integer, nullable=False, server_default="1"),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
    )

    op.create_table(
        "incidents",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("incident_number", sa.Text, nullable=False, unique=True),
        sa.Column("incident_name", sa.Text, nullable=False),
        sa.Column("incident_type", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        sa.Column("lat", sa.Float),
        sa.Column("lng", sa.Float),
        sa.Column("county", sa.Text),
        sa.Column("state", sa.Text),
        sa.Column("started_at", sa.Text, nullable=False),
        sa.Column("closed_at", sa.Text),
        sa.Column("incident_commander_id", sa.Text, sa.ForeignKey("personnel.id")),
        sa.Column("notes", sa.Text),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
    )

    op.create_table(
        "deployments",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("incident_id", sa.Text, sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("personnel_id", sa.Text, sa.ForeignKey("personnel.id"), nullable=False),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("division", sa.Text),
        sa.Column("team", sa.Text),
        sa.Column("checked_in_at", sa.Text),
        sa.Column("checked_out_at", sa.Text),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
        sa.UniqueConstraint("incident_id", "personnel_id"),
    )

    op.create_table(
        "certifications",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("personnel_id", sa.Text, sa.ForeignKey("personnel.id"), nullable=False),
        sa.Column("cert_type", sa.Text, nullable=False),
        sa.Column("cert_number", sa.Text),
        sa.Column("issuing_body", sa.Text),
        sa.Column("issued_date", sa.Text),
        sa.Column("expiry_date", sa.Text),
        sa.Column("is_verified", sa.Integer, nullable=False, server_default="0"),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
    )

    op.create_table(
        "gps_tracks",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("incident_id", sa.Text, sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("personnel_id", sa.Text, sa.ForeignKey("personnel.id"), nullable=False),
        sa.Column("lat", sa.Float, nullable=False),
        sa.Column("lng", sa.Float, nullable=False),
        sa.Column("elevation", sa.Float),
        sa.Column("accuracy", sa.Float),
        sa.Column("recorded_at", sa.Text, nullable=False),
        sa.Column("source", sa.Text, nullable=False, server_default="trailhead"),
        sa.Column("created_at", sa.Text, nullable=False),
    )

    op.create_table(
        "search_segments",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("incident_id", sa.Text, sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("segment_id", sa.Text, nullable=False),
        sa.Column("assigned_team", sa.Text),
        sa.Column("status", sa.Text, nullable=False, server_default="unassigned"),
        sa.Column("boundary_coords", sa.Text),
        sa.Column("probability_of_detection", sa.Float, server_default="0.0"),
        sa.Column("assigned_at", sa.Text),
        sa.Column("cleared_at", sa.Text),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
        sa.UniqueConstraint("incident_id", "segment_id"),
    )

    op.create_table(
        "radio_log",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("incident_id", sa.Text, sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("personnel_id", sa.Text, sa.ForeignKey("personnel.id")),
        sa.Column("channel", sa.Text),
        sa.Column("message", sa.Text, nullable=False),
        sa.Column("logged_at", sa.Text, nullable=False),
        sa.Column("is_missed_checkin", sa.Integer, nullable=False, server_default="0"),
        sa.Column("source", sa.Text, nullable=False, server_default="manual"),
        sa.Column("created_at", sa.Text, nullable=False),
    )

    # -----------------------------------------------------------------------
    # ICS form tables
    # -----------------------------------------------------------------------

    op.create_table(
        "ics_201",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("incident_id", sa.Text, sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("situation_summary", sa.Text),
        sa.Column("initial_objectives", sa.Text),
        sa.Column("current_actions", sa.Text),
        sa.Column("resource_summary", sa.Text),
        sa.Column("prepared_by", sa.Text, sa.ForeignKey("personnel.id")),
        sa.Column("prepared_at", sa.Text),
        sa.Column("signed_by", sa.Text, sa.ForeignKey("personnel.id")),
        sa.Column("signed_at", sa.Text),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
    )

    op.create_table(
        "ics_204",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("incident_id", sa.Text, sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("operational_period", sa.Text),
        sa.Column("branch", sa.Text),
        sa.Column("division", sa.Text),
        sa.Column("group_name", sa.Text),
        sa.Column("supervisor_id", sa.Text, sa.ForeignKey("personnel.id")),
        sa.Column("assigned_resources", sa.Text),
        sa.Column("special_instructions", sa.Text),
        sa.Column("signed_by", sa.Text, sa.ForeignKey("personnel.id")),
        sa.Column("signed_at", sa.Text),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
    )

    op.create_table(
        "ics_205",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("incident_id", sa.Text, sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("operational_period", sa.Text),
        sa.Column("channel_assignments", sa.Text),
        sa.Column("special_instructions", sa.Text),
        sa.Column("prepared_by", sa.Text, sa.ForeignKey("personnel.id")),
        sa.Column("prepared_at", sa.Text),
        sa.Column("signed_by", sa.Text, sa.ForeignKey("personnel.id")),
        sa.Column("signed_at", sa.Text),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
    )

    op.create_table(
        "ics_206",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("incident_id", sa.Text, sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("operational_period", sa.Text),
        sa.Column("medical_aid_stations", sa.Text),
        sa.Column("medical_personnel", sa.Text),
        sa.Column("hospitals", sa.Text),
        sa.Column("medical_officer_id", sa.Text, sa.ForeignKey("personnel.id")),
        sa.Column("signed_by", sa.Text, sa.ForeignKey("personnel.id")),
        sa.Column("signed_at", sa.Text),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
    )

    op.create_table(
        "ics_209",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("incident_id", sa.Text, sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("operational_period", sa.Text),
        sa.Column("incident_phase", sa.Text),
        sa.Column("total_personnel", sa.Integer, server_default="0"),
        sa.Column("current_situation", sa.Text),
        sa.Column("primary_mission", sa.Text),
        sa.Column("planned_actions", sa.Text),
        sa.Column("resource_totals", sa.Text),
        sa.Column("prepared_by", sa.Text, sa.ForeignKey("personnel.id")),
        sa.Column("prepared_at", sa.Text),
        sa.Column("signed_by", sa.Text, sa.ForeignKey("personnel.id")),
        sa.Column("signed_at", sa.Text),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
    )

    op.create_table(
        "ics_211",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("incident_id", sa.Text, sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("personnel_id", sa.Text, sa.ForeignKey("personnel.id"), nullable=False),
        sa.Column("assignment", sa.Text),
        sa.Column("check_in_time", sa.Text),
        sa.Column("check_out_time", sa.Text),
        sa.Column("home_agency", sa.Text),
        sa.Column("resource_type", sa.Text),
        sa.Column("created_at", sa.Text, nullable=False),
    )

    op.create_table(
        "ics_214",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("incident_id", sa.Text, sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("personnel_id", sa.Text, sa.ForeignKey("personnel.id")),
        sa.Column("operational_period", sa.Text),
        sa.Column("unit_name", sa.Text),
        sa.Column("activity_entries", sa.Text),
        sa.Column("prepared_by", sa.Text, sa.ForeignKey("personnel.id")),
        sa.Column("prepared_at", sa.Text),
        sa.Column("signed_by", sa.Text, sa.ForeignKey("personnel.id")),
        sa.Column("signed_at", sa.Text),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
    )

    op.create_table(
        "ics_215",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("incident_id", sa.Text, sa.ForeignKey("incidents.id"), nullable=False),
        sa.Column("operational_period", sa.Text),
        sa.Column("branches", sa.Text),
        sa.Column("divisions", sa.Text),
        sa.Column("tactical_objectives", sa.Text),
        sa.Column("support_requirements", sa.Text),
        sa.Column("prepared_by", sa.Text, sa.ForeignKey("personnel.id")),
        sa.Column("prepared_at", sa.Text),
        sa.Column("signed_by", sa.Text, sa.ForeignKey("personnel.id")),
        sa.Column("signed_at", sa.Text),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
    )

    # -----------------------------------------------------------------------
    # Auth tables
    # -----------------------------------------------------------------------

    op.create_table(
        "users",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("personnel_id", sa.Text, sa.ForeignKey("personnel.id")),
        sa.Column("username", sa.Text, nullable=False, unique=True),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("is_active", sa.Integer, nullable=False, server_default="1"),
        sa.Column("last_login_at", sa.Text),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("updated_at", sa.Text, nullable=False),
    )

    op.create_table(
        "sessions",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("user_id", sa.Text, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("token", sa.Text, nullable=False, unique=True),
        sa.Column("expires_at", sa.Text, nullable=False),
        sa.Column("created_at", sa.Text, nullable=False),
    )

    # -----------------------------------------------------------------------
    # Sync outbox
    # -----------------------------------------------------------------------

    op.create_table(
        "outbox",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("table_name", sa.Text, nullable=False),
        sa.Column("record_id", sa.Text, nullable=False),
        sa.Column("operation", sa.Text, nullable=False),
        sa.Column("payload", sa.Text, nullable=False),
        sa.Column("created_at", sa.Text, nullable=False),
        sa.Column("synced_at", sa.Text),
        sa.Column("sync_attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text),
    )

    # -----------------------------------------------------------------------
    # Indexes
    # -----------------------------------------------------------------------

    op.create_index("idx_deployments_incident",    "deployments",    ["incident_id"])
    op.create_index("idx_deployments_personnel",   "deployments",    ["personnel_id"])
    op.create_index("idx_gps_tracks_incident",     "gps_tracks",     ["incident_id"])
    op.create_index("idx_gps_tracks_personnel",    "gps_tracks",     ["personnel_id"])
    op.create_index("idx_gps_tracks_recorded",     "gps_tracks",     ["recorded_at"])
    op.create_index("idx_radio_log_incident",      "radio_log",      ["incident_id"])
    op.create_index("idx_search_segments_incident","search_segments", ["incident_id"])
    op.create_index("idx_certifications_personnel","certifications",  ["personnel_id"])
    op.create_index("idx_outbox_synced",           "outbox",         ["synced_at"])
    op.create_index("idx_sessions_token",          "sessions",       ["token"])
    op.create_index("idx_sessions_user",           "sessions",       ["user_id"])


def downgrade() -> None:
    # Drop in reverse order of creation to respect foreign key constraints
    op.drop_table("outbox")
    op.drop_table("sessions")
    op.drop_table("users")
    op.drop_table("ics_215")
    op.drop_table("ics_214")
    op.drop_table("ics_211")
    op.drop_table("ics_209")
    op.drop_table("ics_206")
    op.drop_table("ics_205")
    op.drop_table("ics_204")
    op.drop_table("ics_201")
    op.drop_table("radio_log")
    op.drop_table("search_segments")
    op.drop_table("gps_tracks")
    op.drop_table("certifications")
    op.drop_table("deployments")
    op.drop_table("incidents")
    op.drop_table("personnel")
