"""Per-page bounds + annotator social variables

Adds:
  - campaigns.min_segments_per_page, campaigns.max_segments_per_page
  - annotators.location, annotators.dialect, annotators.age_group

Revision ID: d4e6a2b8c1f0
Revises: c3d5f1a9e6b2
Create Date: 2026-07-02 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd4e6a2b8c1f0'
down_revision = 'c3d5f1a9e6b2'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('campaigns', schema=None) as batch_op:
        batch_op.add_column(sa.Column('min_segments_per_page', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('max_segments_per_page', sa.Integer(), nullable=True))

    with op.batch_alter_table('annotators', schema=None) as batch_op:
        batch_op.add_column(sa.Column('location', sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column('dialect', sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column('age_group', sa.String(length=20), nullable=True))

    # Backfill sensible bounds for existing campaigns: min = 1, max = at least the current
    # default (and at least 10) so nothing shrinks below what admins already use.
    campaigns = sa.table(
        'campaigns',
        sa.column('segments_per_page', sa.Integer),
        sa.column('min_segments_per_page', sa.Integer),
        sa.column('max_segments_per_page', sa.Integer),
    )
    op.execute(campaigns.update().values(min_segments_per_page=1))
    # max = at least the current default, and at least 10 (done in two portable steps
    # rather than a two-arg max()/greatest, which differ between SQLite and Postgres).
    op.execute(
        campaigns.update().values(
            max_segments_per_page=sa.func.coalesce(campaigns.c.segments_per_page, 3)
        )
    )
    op.execute(
        campaigns.update()
        .where(campaigns.c.max_segments_per_page < 10)
        .values(max_segments_per_page=10)
    )


def downgrade():
    with op.batch_alter_table('annotators', schema=None) as batch_op:
        batch_op.drop_column('age_group')
        batch_op.drop_column('dialect')
        batch_op.drop_column('location')

    with op.batch_alter_table('campaigns', schema=None) as batch_op:
        batch_op.drop_column('max_segments_per_page')
        batch_op.drop_column('min_segments_per_page')
