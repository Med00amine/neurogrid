"""campaign backoff state

Revision ID: 0003
Revises: 0002
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("campaigns", sa.Column("backoff_until", sa.DateTime(timezone=True), nullable=True))
    op.add_column("campaigns", sa.Column("consecutive_failures", sa.Integer(), server_default="0", nullable=False))


def downgrade() -> None:
    op.drop_column("campaigns", "consecutive_failures")
    op.drop_column("campaigns", "backoff_until")
