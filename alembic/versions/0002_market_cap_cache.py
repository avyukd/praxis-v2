"""market_cap_cache table

Revision ID: 0002_market_cap_cache
Revises: 0001_initial
Create Date: 2026-04-18 22:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_market_cap_cache"
down_revision: str | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_cap_cache",
        sa.Column("ticker", sa.String(16), primary_key=True),
        sa.Column("market_cap_usd", sa.BigInteger),
        sa.Column("source", sa.String(32), nullable=False, server_default="yfinance"),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_error", sa.Text),
    )
    op.create_index("idx_market_cap_fetched_at", "market_cap_cache", ["fetched_at"])


def downgrade() -> None:
    op.drop_table("market_cap_cache")
