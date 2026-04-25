"""add reviews table

Revision ID: 3a979d6afefc
Revises: 080e732ad01d
Create Date: 2026-04-25 23:21:51.824590

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = "3a979d6afefc"
down_revision: Union[str, Sequence[str], None] = "080e732ad01d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "reviews",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("reviewer_id", sa.Uuid(), nullable=True),
        sa.Column("reviewer_type", sa.Enum("AGENT", "HUMAN", name="reviewertype"), nullable=False),
        sa.Column(
            "decision",
            sa.Enum("APPROVED", "REJECTED", "CHANGES_REQUESTED", name="reviewdecision"),
            nullable=False,
        ),
        sa.Column("summary", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("findings", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["airlock_requests.id"],
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_id"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_reviews_request_id"), "reviews", ["request_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_reviews_request_id"), table_name="reviews")
    op.drop_table("reviews")
