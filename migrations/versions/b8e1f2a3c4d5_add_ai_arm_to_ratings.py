"""Add ai_arm and ai_eligible to ratings

Revision ID: b8e1f2a3c4d5
Revises: f2345a55758b
Create Date: 2026-09-21 18:55:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b8e1f2a3c4d5'
down_revision = 'f2345a55758b'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('ratings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('ai_arm', sa.String(length=32), nullable=True))
        batch_op.add_column(sa.Column('ai_eligible', sa.Boolean(), nullable=True))


def downgrade():
    with op.batch_alter_table('ratings', schema=None) as batch_op:
        batch_op.drop_column('ai_eligible')
        batch_op.drop_column('ai_arm')

