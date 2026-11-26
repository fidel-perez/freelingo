"""Picture talk scenes and the native speech rate.

Revision ID: 0065_picture_talk
Revises: 0064_image_prompts
"""

import sqlalchemy as sa

from alembic import op

revision = "0065_picture_talk"
down_revision = "0064_image_prompts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "picture_talk_scenes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "study_plan_id",
            sa.Integer(),
            sa.ForeignKey("study_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("image_prompt", sa.Text(), nullable=False),
        sa.Column("words", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index(
        "ix_picture_talk_scenes_study_plan_id", "picture_talk_scenes", ["study_plan_id"]
    )
    op.add_column(
        "users",
        sa.Column("native_speech_rate", sa.Float(), nullable=False, server_default="1.8"),
    )


def downgrade() -> None:
    op.drop_column("users", "native_speech_rate")
    op.drop_index("ix_picture_talk_scenes_study_plan_id", table_name="picture_talk_scenes")
    op.drop_table("picture_talk_scenes")
