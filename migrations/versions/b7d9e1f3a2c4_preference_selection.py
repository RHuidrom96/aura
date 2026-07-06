"""Preference Selection mode: ratings.ranking_json

Adds:
  - ratings.ranking_json (JSON {candidate_key: rank}) for the Preference Selection mode.

Revision ID: b7d9e1f3a2c4
Revises: e1d1c9eb6ce9
Create Date: 2026-07-05 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b7d9e1f3a2c4'
down_revision = 'e1d1c9eb6ce9'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('ratings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('ranking_json', sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('ratings', schema=None) as batch_op:
        batch_op.drop_column('ranking_json')
