"""Add resume_at column to campaigns for timed pause.

Revision ID: 042_add_campaign_resume_at
Revises: 041_add_actionlog_lead_index
Create Date: 2026-11-01
"""
from alembic import op
import sqlalchemy as sa

revision = "042_add_campaign_resume_at"
down_revision = "041_add_actionlog_lead_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("campaigns", sa.Column("resume_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("campaigns", "resume_at")
