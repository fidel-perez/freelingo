"""Browser image generation: prompt caches and per-user opt-in.

Revision ID: 0064_image_prompts
Revises: 0063_correction_style
"""

import sqlalchemy as sa

from alembic import op

revision = "0064_image_prompts"
down_revision = "0063_correction_style"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("lessons", sa.Column("image_prompt", sa.Text(), nullable=True))
    op.create_table(
        "situation_image_prompts",
        sa.Column("situation_id", sa.String(100), primary_key=True),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.add_column(
        "users",
        sa.Column("images_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("users", "images_enabled")
    op.drop_table("situation_image_prompts")
    op.drop_column("lessons", "image_prompt")
