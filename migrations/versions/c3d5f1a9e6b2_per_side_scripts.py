"""Per-side scripts (source_script / target_script) on campaigns

Adds:
  - campaigns.source_script
  - campaigns.target_script

The legacy campaigns.script column is left in place (backward compatibility / fallback).

Revision ID: c3d5f1a9e6b2
Revises: b7c2e9f4a1d8
Create Date: 2026-07-02 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c3d5f1a9e6b2'
down_revision = 'b7c2e9f4a1d8'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('campaigns', schema=None) as batch_op:
        batch_op.add_column(sa.Column('source_script', sa.String(length=50), nullable=True))
        batch_op.add_column(sa.Column('target_script', sa.String(length=50), nullable=True))

    # Seed the new per-side scripts from the legacy single `script` value so existing
    # campaigns keep displaying/exporting their script. Applied to the target side, which
    # is what the single field historically represented (the language annotators read).
    campaigns = sa.table(
        'campaigns',
        sa.column('script', sa.String),
        sa.column('source_script', sa.String),
        sa.column('target_script', sa.String),
    )
    op.execute(
        campaigns.update()
        .where(campaigns.c.script.isnot(None))
        .values(target_script=campaigns.c.script)
    )


def downgrade():
    with op.batch_alter_table('campaigns', schema=None) as batch_op:
        batch_op.drop_column('target_script')
        batch_op.drop_column('source_script')
