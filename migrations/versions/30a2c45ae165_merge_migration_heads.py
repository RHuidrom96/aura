"""Merge migration heads

Revision ID: 30a2c45ae165
Revises: 0b688ec4a822, f6a8c4d1b2e3
Create Date: 2026-07-04 13:23:52.974080

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '30a2c45ae165'
down_revision = ('0b688ec4a822', 'f6a8c4d1b2e3')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
