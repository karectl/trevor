"""align user and project with spec

Revision ID: 555a167047a2
Revises: 7d8d7e1827c4
Create Date: 2026-04-25 00:07:35.054031

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


# revision identifiers, used by Alembic.
revision: str = "555a167047a2"
down_revision: Union[str, Sequence[str], None] = "7d8d7e1827c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # projects.status: no DDL change — StrEnum stored as VARCHAR, SUSPENDED removed at Python level.

    # Add new user columns (server_default avoids NOT NULL errors on existing rows).
    op.add_column(
        "users",
        sa.Column(
            "username", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=""
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "given_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=""
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "family_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=""
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "affiliation", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=""
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "crd_name", sqlmodel.sql.sqltypes.AutoString(), nullable=False, server_default=""
        ),
    )
    op.add_column(
        "users", sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true())
    )
    op.add_column(
        "users",
        sa.Column(
            "crd_synced_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("(datetime('now'))"),
        ),
    )

    # Make keycloak_sub nullable and drop display_name via batch (SQLite requires batch for these).
    with op.batch_alter_table("users") as batch_op:
        batch_op.alter_column("keycloak_sub", existing_type=sa.VARCHAR(), nullable=True)
        batch_op.drop_column("display_name")


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(
            sa.Column("display_name", sa.VARCHAR(), nullable=False, server_default="")
        )
        batch_op.alter_column("keycloak_sub", existing_type=sa.VARCHAR(), nullable=False)

    op.drop_column("users", "crd_synced_at")
    op.drop_column("users", "active")
    op.drop_column("users", "crd_name")
    op.drop_column("users", "affiliation")
    op.drop_column("users", "family_name")
    op.drop_column("users", "given_name")
    op.drop_column("users", "username")
