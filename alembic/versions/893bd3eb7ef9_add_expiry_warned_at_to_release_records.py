"""add expiry_warned_at to release_records

Revision ID: 893bd3eb7ef9
Revises: 9c520f360f06
Create Date: 2026-04-26 20:19:02.410508

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '893bd3eb7ef9'
down_revision: Union[str, Sequence[str], None] = '9c520f360f06'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('release_records', sa.Column('expiry_warned_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('release_records', 'expiry_warned_at')
