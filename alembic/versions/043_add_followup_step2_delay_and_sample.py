"""Add followup_step2_delay_hours and followup_sample_remaining to campaigns.

Revision ID: 043_followup_step2_sample
Revises: 042_add_campaign_resume_at
Create Date: 2026-11-13
"""
from alembic import op
import sqlalchemy as sa

revision = "043_followup_step2_sample"
down_revision = "042_add_campaign_resume_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("campaigns", sa.Column("followup_step2_delay_hours", sa.Float(), nullable=False, server_default="0"))
    op.add_column("campaigns", sa.Column("followup_sample_remaining", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("campaigns", "followup_sample_remaining")
    op.drop_column("campaigns", "followup_step2_delay_hours")
