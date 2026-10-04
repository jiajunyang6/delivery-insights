"""Persist pending PR linking across interrupted sync jobs."""

import sqlalchemy as sa
from alembic import op

revision: str = "0002_links_pending"
down_revision: str = "0001_initial"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.add_column(
        "repositories",
        sa.Column("links_pending", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
    )


def downgrade() -> None:
    op.drop_column("repositories", "links_pending")
