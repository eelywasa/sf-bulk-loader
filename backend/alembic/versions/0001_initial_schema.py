"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-03-01 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- connection --------------------------------------------------------
    op.create_table(
        "connection",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("instance_url", sa.String(512), nullable=False),
        sa.Column("login_url", sa.String(512), nullable=False),
        sa.Column("client_id", sa.Text(), nullable=False),
        sa.Column("private_key", sa.Text(), nullable=False),
        sa.Column("username", sa.String(255), nullable=False),
        sa.Column("access_token", sa.Text(), nullable=True),
        sa.Column("token_expiry", sa.DateTime(), nullable=True),
        sa.Column("is_sandbox", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # --- load_plan ---------------------------------------------------------
    op.create_table(
        "load_plan",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("connection_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("abort_on_step_failure", sa.Boolean(), nullable=False),
        sa.Column("error_threshold_pct", sa.Float(), nullable=False),
        sa.Column("max_parallel_jobs", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["connection_id"], ["connection.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )

    # --- load_step ---------------------------------------------------------
    op.create_table(
        "load_step",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("load_plan_id", sa.String(36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("object_name", sa.String(255), nullable=False),
        sa.Column(
            "operation",
            sa.Enum("insert", "update", "upsert", "delete", name="operation_enum"),
            nullable=False,
        ),
        sa.Column("external_id_field", sa.String(255), nullable=True),
        sa.Column("csv_file_pattern", sa.String(512), nullable=False),
        sa.Column("partition_size", sa.Integer(), nullable=False),
        sa.Column("assignment_rule_id", sa.String(18), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["load_plan_id"], ["load_plan.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # --- load_run ----------------------------------------------------------
    op.create_table(
        "load_run",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("load_plan_id", sa.String(36), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "running",
                "completed",
                "completed_with_errors",
                "failed",
                "aborted",
                name="run_status_enum",
            ),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("total_records", sa.Integer(), nullable=True),
        sa.Column("total_success", sa.Integer(), nullable=True),
        sa.Column("total_errors", sa.Integer(), nullable=True),
        sa.Column("initiated_by", sa.String(255), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["load_plan_id"], ["load_plan.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )

    # --- job_record --------------------------------------------------------
    op.create_table(
        "job_record",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("load_run_id", sa.String(36), nullable=False),
        sa.Column("load_step_id", sa.String(36), nullable=False),
        sa.Column("sf_job_id", sa.String(18), nullable=True),
        sa.Column("partition_index", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "uploading",
                "upload_complete",
                "in_progress",
                "job_complete",
                "failed",
                "aborted",
                name="job_status_enum",
            ),
            nullable=False,
        ),
        sa.Column("records_processed", sa.Integer(), nullable=True),
        sa.Column("records_failed", sa.Integer(), nullable=True),
        sa.Column("success_file_path", sa.String(512), nullable=True),
        sa.Column("error_file_path", sa.String(512), nullable=True),
        sa.Column("unprocessed_file_path", sa.String(512), nullable=True),
        sa.Column("sf_api_response", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["load_run_id"], ["load_run.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["load_step_id"], ["load_step.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )

    # Indexes for common query patterns
    op.create_index("ix_load_plan_connection_id", "load_plan", ["connection_id"])
    op.create_index("ix_load_step_load_plan_id", "load_step", ["load_plan_id"])
    op.create_index("ix_load_step_sequence", "load_step", ["load_plan_id", "sequence"])
    op.create_index("ix_load_run_load_plan_id", "load_run", ["load_plan_id"])
    op.create_index("ix_load_run_status", "load_run", ["status"])
    op.create_index("ix_job_record_load_run_id", "job_record", ["load_run_id"])
    op.create_index("ix_job_record_load_step_id", "job_record", ["load_step_id"])
    op.create_index("ix_job_record_status", "job_record", ["status"])


def downgrade() -> None:
    op.drop_index("ix_job_record_status", table_name="job_record")
    op.drop_index("ix_job_record_load_step_id", table_name="job_record")
    op.drop_index("ix_job_record_load_run_id", table_name="job_record")
    op.drop_index("ix_load_run_status", table_name="load_run")
    op.drop_index("ix_load_run_load_plan_id", table_name="load_run")
    op.drop_index("ix_load_step_sequence", table_name="load_step")
    op.drop_index("ix_load_step_load_plan_id", table_name="load_step")
    op.drop_index("ix_load_plan_connection_id", table_name="load_plan")

    op.drop_table("job_record")
    op.drop_table("load_run")
    op.drop_table("load_step")
    op.drop_table("load_plan")
    op.drop_table("connection")
