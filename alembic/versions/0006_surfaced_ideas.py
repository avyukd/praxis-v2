"""surfaced_ideas table for D48 idea surfacing

Revision ID: 0006_surfaced_ideas
Revises: 0005_fundamentals_cache
Create Date: 2026-04-20 00:15:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision: str = "0006_surfaced_ideas"
down_revision: str | None = "0005_fundamentals_cache"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "surfaced_ideas",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("handle", sa.String(256), unique=True, nullable=False),
        sa.Column("dedup_handle", sa.String(128), nullable=False),
        sa.Column("idea_type", sa.String(64), nullable=False),
        sa.Column("tickers", ARRAY(sa.String(16)), nullable=False, server_default="{}"),
        sa.Column("themes", ARRAY(sa.String(128)), nullable=False, server_default="{}"),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("evidence", ARRAY(sa.String(512)), nullable=False, server_default="{}"),
        sa.Column("evidence_hash", sa.String(32), nullable=False),
        sa.Column("urgency", sa.String(16), nullable=False),
        sa.Column(
            "surfaced_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("batch_handle", sa.String(128), nullable=True),
        sa.Column("notified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("extra", JSONB(), nullable=True),
    )
    op.create_index(
        "ix_surfaced_dedup",
        "surfaced_ideas",
        ["dedup_handle", "surfaced_at"],
        postgresql_using="btree",
    )
    op.create_index(
        "ix_surfaced_recent", "surfaced_ideas", [sa.text("surfaced_at DESC")]
    )


def downgrade() -> None:
    op.drop_index("ix_surfaced_recent", table_name="surfaced_ideas")
    op.drop_index("ix_surfaced_dedup", table_name="surfaced_ideas")
    op.drop_table("surfaced_ideas")
