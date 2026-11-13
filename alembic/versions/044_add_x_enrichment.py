"""Add X (Twitter) handle enrichment fields.

leads.x_alternatives  JSON  — scored candidate handles from the X resolver
leads.x_contacted_at  DATETIME — when the lead was messaged on X
lead_lists.x_enrich_enabled BOOLEAN — opt-in per list for the X sweeper

Revision ID: 044_add_x_enrichment
Revises: 043_followup_step2_sample
Create Date: 2026-11-13
"""
from alembic import op
import sqlalchemy as sa

revision = "044_add_x_enrichment"
down_revision = "043_followup_step2_sample"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("leads", sa.Column("x_alternatives", sa.JSON(), nullable=True))
    op.add_column("leads", sa.Column("x_contacted_at", sa.DateTime(), nullable=True))
    op.add_column(
        "lead_lists",
        sa.Column("x_enrich_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("lead_lists", "x_enrich_enabled")
    op.drop_column("leads", "x_contacted_at")
    op.drop_column("leads", "x_alternatives")
