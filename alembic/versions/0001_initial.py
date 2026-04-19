"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-04-18 12:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision: str = "0001_initial"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')

    op.create_table(
        "investigations",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("handle", sa.String(128), unique=True, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("scope", sa.String(32), nullable=False),
        sa.Column("initiated_by", sa.String(64), nullable=False),
        sa.Column("hypothesis", sa.Text),
        sa.Column("entry_nodes", ARRAY(sa.String)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(timezone=True)),
        sa.Column("last_progress_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("artifacts", ARRAY(sa.String)),
        sa.Column("vault_path", sa.String(512)),
    )

    op.create_table(
        "tasks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("type", sa.String(64), nullable=False),
        sa.Column("priority", sa.Integer, nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("model", sa.String(16), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("dedup_key", sa.String(256), unique=True),
        sa.Column("resource_key", sa.String(128)),
        sa.Column(
            "investigation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("investigations.id", ondelete="SET NULL"),
        ),
        sa.Column("parent_task_id", UUID(as_uuid=True), sa.ForeignKey("tasks.id", ondelete="SET NULL")),
        sa.Column("depends_on", ARRAY(UUID(as_uuid=True))),
        sa.Column("lease_holder", sa.String(128)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("attempts", sa.Integer, nullable=False, server_default="0"),
        sa.Column("rate_limit_bounces", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default="3"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text),
        sa.Column("validation_result", JSONB),
        sa.Column("telemetry", JSONB),
    )
    op.create_index(
        "idx_tasks_dispatch",
        "tasks",
        ["status", "priority", "created_at"],
        postgresql_where=sa.text("status IN ('queued', 'partial')"),
    )
    op.create_index(
        "idx_tasks_resource",
        "tasks",
        ["resource_key", "status"],
        postgresql_where=sa.text("status = 'running'"),
    )
    op.create_index("idx_tasks_investigation", "tasks", ["investigation_id"])
    op.create_index("idx_tasks_type_status", "tasks", ["type", "status"])

    op.create_table(
        "rate_limit_state",
        sa.Column("id", sa.Integer, primary_key=True, server_default="1"),
        sa.Column("status", sa.String(16), nullable=False, server_default="clear"),
        sa.Column("limited_until_ts", sa.DateTime(timezone=True)),
        sa.Column("consecutive_hits", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_hit_ts", sa.DateTime(timezone=True)),
        sa.Column("probe_task_id", UUID(as_uuid=True)),
        sa.CheckConstraint("id = 1", name="rate_limit_singleton"),
    )
    op.execute("INSERT INTO rate_limit_state (id, status) VALUES (1, 'clear')")

    op.create_table(
        "heartbeats",
        sa.Column("component", sa.String(128), primary_key=True),
        sa.Column("last_heartbeat", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("status", JSONB),
    )

    op.create_table(
        "events",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("component", sa.String(128), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("payload", JSONB),
    )
    op.create_index("idx_events_recent", "events", ["ts"])
    op.create_index("idx_events_component_type", "events", ["component", "event_type"])

    op.create_table(
        "sources",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("dedup_key", sa.String(256), unique=True, nullable=False),
        sa.Column("source_type", sa.String(64), nullable=False),
        sa.Column("vault_path", sa.String(512), nullable=False),
        sa.Column("ticker", sa.String(16)),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("metadata", JSONB),
    )

    op.create_table(
        "system_state",
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("value", JSONB, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "dead_letter_tasks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("original_task", JSONB, nullable=False),
        sa.Column("final_error", sa.Text),
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "signals_fired",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("task_id", UUID(as_uuid=True), sa.ForeignKey("tasks.id", ondelete="SET NULL")),
        sa.Column("ticker", sa.String(16)),
        sa.Column("signal_type", sa.String(64), nullable=False),
        sa.Column("urgency", sa.String(32), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_signals_fired_at", "signals_fired", ["fired_at"])


def downgrade() -> None:
    op.drop_table("signals_fired")
    op.drop_table("dead_letter_tasks")
    op.drop_table("system_state")
    op.drop_table("sources")
    op.drop_table("events")
    op.drop_table("heartbeats")
    op.drop_table("rate_limit_state")
    op.drop_table("tasks")
    op.drop_table("investigations")
