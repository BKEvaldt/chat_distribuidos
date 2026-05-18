"""initial schema

Revision ID: a821fe0ae708
Revises: 
Create Date: 2026-05-16 15:13:01.649322

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a821fe0ae708'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # Primeira migracao do projeto: cria usuarios e mensagens do chat.
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('username', sa.String(length=32), nullable=False),
    sa.Column('username_key', sa.String(length=32), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        # Indices unicos garantem que nao existam dois usuarios com o mesmo nome.
        batch_op.create_index(batch_op.f('ix_users_username'), ['username'], unique=True)
        batch_op.create_index(batch_op.f('ix_users_username_key'), ['username_key'], unique=True)

    op.create_table('messages',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('type', sa.String(length=16), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('username', sa.String(length=32), nullable=False),
    sa.Column('text', sa.String(length=1000), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('messages', schema=None) as batch_op:
        # O historico sempre busca as mensagens mais recentes primeiro.
        batch_op.create_index(batch_op.f('ix_messages_created_at'), ['created_at'], unique=False)


def downgrade():
    # Desfaz na ordem inversa para respeitar a chave estrangeira messages -> users.
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_messages_created_at'))

    op.drop_table('messages')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_username_key'))
        batch_op.drop_index(batch_op.f('ix_users_username'))

    op.drop_table('users')
