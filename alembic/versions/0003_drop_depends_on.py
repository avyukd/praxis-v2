"""drop Task.depends_on (D26 dead code)

Revision ID: 0003_drop_depends_on
Revises: 0002_market_cap_cache
Create Date: 2026-04-20 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0003_drop_depends_on"
down_revision: str | None = "0002_market_cap_cache"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Set everywhere, enforced nowhere. Drop.
    with op.batch_alter_table("tasks") as batch_op:
        batch_op.drop_column("depends_on")


def downgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("depends_on", sa.ARRAY(sa.dialects.postgresql.UUID()), nullable=True),
    )
