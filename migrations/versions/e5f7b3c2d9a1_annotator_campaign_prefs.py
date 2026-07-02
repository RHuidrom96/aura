"""Server-side per-annotator, per-campaign preferences (segments-per-page)

Adds:
  - annotator_campaign_prefs table

Revision ID: e5f7b3c2d9a1
Revises: d4e6a2b8c1f0
Create Date: 2026-07-02 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e5f7b3c2d9a1'
down_revision = 'd4e6a2b8c1f0'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'annotator_campaign_prefs',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('annotator_id', sa.String(length=32), nullable=False),
        sa.Column('campaign_id', sa.String(length=32), nullable=False),
        sa.Column('segments_per_page', sa.Integer(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['annotator_id'], ['annotators.id']),
        sa.ForeignKeyConstraint(['campaign_id'], ['campaigns.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('annotator_id', 'campaign_id',
                            name='uq_annotator_campaign_pref'),
    )
    with op.batch_alter_table('annotator_campaign_prefs', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_annotator_campaign_prefs_annotator_id'),
                              ['annotator_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_annotator_campaign_prefs_campaign_id'),
                              ['campaign_id'], unique=False)


def downgrade():
    with op.batch_alter_table('annotator_campaign_prefs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_annotator_campaign_prefs_campaign_id'))
        batch_op.drop_index(batch_op.f('ix_annotator_campaign_prefs_annotator_id'))
    op.drop_table('annotator_campaign_prefs')
