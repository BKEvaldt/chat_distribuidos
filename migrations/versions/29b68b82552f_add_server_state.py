"""add server state

Revision ID: 29b68b82552f
Revises: a821fe0ae708
Create Date: 2026-05-16 17:56:55.625882

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '29b68b82552f'
down_revision = 'a821fe0ae708'
branch_labels = None
depends_on = None


def upgrade():
    # Tabela pequena para guardar estado global, como qual servidor esta ativo.
    op.create_table('server_state',
    sa.Column('key', sa.String(length=64), nullable=False),
    sa.Column('value', sa.String(length=255), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )


def downgrade():
    # Remover esta tabela apaga o controle de papel primario/backup.
    op.drop_table('server_state')
