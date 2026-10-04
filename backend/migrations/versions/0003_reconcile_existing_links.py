"""Bootstrap linking for populated databases, including already-applied revision 0002."""

from alembic import op

revision: str = "0003_reconcile_links"
down_revision: str = "0002_links_pending"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.execute(
        "UPDATE repositories SET links_pending = TRUE "
        "WHERE EXISTS (SELECT 1 FROM pull_requests WHERE pull_requests.repo_id = repositories.id)"
    )


def downgrade() -> None:
    # Retain pending work: the previous revision also understands this flag.
    pass
