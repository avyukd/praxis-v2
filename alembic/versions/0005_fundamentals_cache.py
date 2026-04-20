"""fundamentals_cache table for D25 fundamentals MCP

Revision ID: 0005_fundamentals_cache
Revises: 0004_investigation_priority
Create Date: 2026-04-20 00:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0005_fundamentals_cache"
down_revision: str | None = "0004_investigation_priority"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "fundamentals_cache",
        sa.Column("ticker", sa.String(16), nullable=False),
        sa.Column("method", sa.String(64), nullable=False),
        sa.Column("params_hash", sa.String(32), nullable=False),
        sa.Column("value", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.String(500), nullable=True),
        sa.PrimaryKeyConstraint("ticker", "method", "params_hash"),
    )
    op.create_index(
        "ix_fundamentals_cache_ticker_fetched",
        "fundamentals_cache",
        ["ticker", "fetched_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_fundamentals_cache_ticker_fetched", table_name="fundamentals_cache")
    op.drop_table("fundamentals_cache")
