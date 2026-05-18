"""Configuracao do Alembic usada pelo Flask-Migrate."""

import logging
from logging.config import fileConfig

from flask import current_app

from alembic import context

# O objeto config representa o alembic.ini carregado pelo Flask-Migrate.
config = context.config

# Mantemos a configuracao de logs do Alembic para enxergar migracoes no terminal.
fileConfig(config.config_file_name)
logger = logging.getLogger('alembic.env')


def get_engine():
    """Busca a engine SQLAlchemy considerando versoes antigas e novas."""
    try:
        # Compatibilidade com Flask-SQLAlchemy<3 e Alchemical.
        return current_app.extensions['migrate'].db.get_engine()
    except (TypeError, AttributeError):
        # Caminho usado pelo Flask-SQLAlchemy>=3.
        return current_app.extensions['migrate'].db.engine


def get_engine_url():
    """Entrega a URL do banco para o Alembic sem esconder a senha."""
    try:
        return get_engine().url.render_as_string(hide_password=False).replace(
            '%', '%%')
    except AttributeError:
        return str(get_engine().url).replace('%', '%%')


# O Flask-Migrate usa a metadata dos models para comparar o banco atual com o
# codigo quando geramos uma nova migracao.
config.set_main_option('sqlalchemy.url', get_engine_url())
target_db = current_app.extensions['migrate'].db


def get_metadata():
    """Retorna a metadata correta mesmo quando ha suporte a binds multiplos."""
    if hasattr(target_db, 'metadatas'):
        return target_db.metadatas[None]
    return target_db.metadata


def run_migrations_offline():
    """Executa migracoes sem abrir uma conexao real com o banco.

    Esse modo e mais comum para gerar SQL, mas deixamos pronto porque faz parte
    do fluxo padrao do Alembic.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url, target_metadata=get_metadata(), literal_binds=True
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    """Executa migracoes conectando de fato no banco configurado."""

    # Evita criar uma revisao vazia quando o autogenerate nao encontra mudancas.
    def process_revision_directives(context, revision, directives):
        if getattr(config.cmd_opts, 'autogenerate', False):
            script = directives[0]
            if script.upgrade_ops.is_empty():
                directives[:] = []
                logger.info('No changes in schema detected.')

    conf_args = current_app.extensions['migrate'].configure_args
    if conf_args.get("process_revision_directives") is None:
        conf_args["process_revision_directives"] = process_revision_directives

    connectable = get_engine()

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=get_metadata(),
            **conf_args
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
