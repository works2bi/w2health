"""Ambiente do Alembic — metadatas do data plane + control plane; papel DONO do schema.

Migrations sempre rodam com `DATABASE_ADMIN_URL` (dono das tabelas, cria papéis/políticas
de RLS). A API roda com `DATABASE_URL` (papel `w2health_app`, sem privilégio de DDL).
Com `DB_ROLES_PROVISIONED=true` os papéis de runtime são criados pelo DBA e as migrations
só os verificam — o dono pode ser um papel comum, sem SUPERUSER/CREATEROLE (docs/DEPLOY_VPS.md).
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import get_settings
from app.db.base import ALL_METADATA
from app.models import *  # noqa: F401,F403  (registra todas as tabelas nas metadatas)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().admin_database_url.replace("%", "%%"))

target_metadata = list(ALL_METADATA)


def run_migrations_offline() -> None:
    context.configure(
        url=get_settings().admin_database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
