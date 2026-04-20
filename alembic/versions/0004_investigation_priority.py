"""add Investigation.research_priority (D21)

Revision ID: 0004_investigation_priority
Revises: 0003_drop_depends_on
Create Date: 2026-04-20 00:05:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0004_investigation_priority"
down_revision: str | None = "0003_drop_depends_on"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "investigations",
        sa.Column("research_priority", sa.Integer(), nullable=False, server_default="5"),
    )


def downgrade() -> None:
    op.drop_column("investigations", "research_priority")
