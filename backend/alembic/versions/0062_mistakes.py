"""Per-plan mistake log that feeds flashcards.

Revision ID: 0062_mistakes
Revises: 0061_grammar_practice
"""

import sqlalchemy as sa

from alembic import op

revision = "0062_mistakes"
down_revision = "0061_grammar_practice"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mistakes",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "study_plan_id",
            sa.Integer(),
            sa.ForeignKey("study_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("original", sa.Text(), nullable=False),
        sa.Column("original_key", sa.String(300), nullable=False),
        sa.Column("corrected", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("occurrences", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "flashcard_id",
            sa.Integer(),
            sa.ForeignKey("flashcards.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("study_plan_id", "original_key", name="uq_mistake_plan_key"),
    )
    op.create_index("ix_mistakes_study_plan_id", "mistakes", ["study_plan_id"])


def downgrade() -> None:
    op.drop_index("ix_mistakes_study_plan_id", table_name="mistakes")
    op.drop_table("mistakes")
