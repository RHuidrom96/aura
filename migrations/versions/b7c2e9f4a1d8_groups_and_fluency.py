"""Campaign groups + annotator source/target fluency

Adds:
  - annotators.source_fluency, annotators.target_fluency
  - campaign_groups table
  - campaigns.group_id (FK -> campaign_groups.id) + index

Revision ID: b7c2e9f4a1d8
Revises: a4a84a67630b
Create Date: 2026-07-02 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b7c2e9f4a1d8'
down_revision = 'a4a84a67630b'
branch_labels = None
depends_on = None


def upgrade():
    # New group table first, so the campaigns FK can reference it.
    op.create_table(
        'campaign_groups',
        sa.Column('id', sa.String(length=32), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('owner_email', sa.String(length=200), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )

    # Annotator self-rated fluency in the source / target language.
    with op.batch_alter_table('annotators', schema=None) as batch_op:
        batch_op.add_column(sa.Column('source_fluency', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('target_fluency', sa.String(length=20), nullable=True))

    # Optional campaign -> group link.
    with op.batch_alter_table('campaigns', schema=None) as batch_op:
        batch_op.add_column(sa.Column('group_id', sa.String(length=32), nullable=True))
        batch_op.create_index(batch_op.f('ix_campaigns_group_id'), ['group_id'], unique=False)
        batch_op.create_foreign_key(
            'fk_campaigns_group_id_campaign_groups',
            'campaign_groups', ['group_id'], ['id'],
        )


def downgrade():
    with op.batch_alter_table('campaigns', schema=None) as batch_op:
        batch_op.drop_constraint('fk_campaigns_group_id_campaign_groups', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_campaigns_group_id'))
        batch_op.drop_column('group_id')

    with op.batch_alter_table('annotators', schema=None) as batch_op:
        batch_op.drop_column('target_fluency')
        batch_op.drop_column('source_fluency')

    op.drop_table('campaign_groups')
