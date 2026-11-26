"""Per-user tutor correction style.

Revision ID: 0063_correction_style
Revises: 0062_mistakes
"""

import sqlalchemy as sa

from alembic import op

revision = "0063_correction_style"
down_revision = "0062_mistakes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("correction_style", sa.String(10), nullable=False, server_default="recast"),
    )


def downgrade() -> None:
    op.drop_column("users", "correction_style")
