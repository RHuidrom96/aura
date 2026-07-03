"""Add campaigns.shuffle_segments

Adds:
  - campaigns.shuffle_segments (bool) — present segments to annotators in a
    per-annotator randomised order (presentation only; results are unaffected).

Revision ID: f6a8c4d1b2e3
Revises: e5f7b3c2d9a1
Create Date: 2026-07-02 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f6a8c4d1b2e3'
down_revision = 'e5f7b3c2d9a1'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('campaigns', schema=None) as batch_op:
        batch_op.add_column(sa.Column('shuffle_segments', sa.Boolean(), nullable=True))
    # Default existing campaigns to "no shuffle" (original data order).
    op.execute(sa.text(
                    "UPDATE campaigns SET shuffle_segments = FALSE "
                    "WHERE shuffle_segments IS NULL"
                ))


def downgrade():
    with op.batch_alter_table('campaigns', schema=None) as batch_op:
        batch_op.drop_column('shuffle_segments')
