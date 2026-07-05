"""Merge preference selection and email verification migrations

Revision ID: a7b31fd81eb8
Revises: 316f2948b73d, b7d9e1f3a2c4
Create Date: 2026-07-06 00:55:29.789250

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a7b31fd81eb8'
down_revision = ('316f2948b73d', 'b7d9e1f3a2c4')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
