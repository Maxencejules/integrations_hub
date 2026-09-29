import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, make_url, pool

from integrations_hub.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Allow env var override for the sync database URL
sync_url = os.environ.get("IH_DATABASE_URL_SYNC")
if sync_url:
    config.set_main_option("sqlalchemy.url", sync_url)

# Use the driver installed by the project across SQLAlchemy 2.0/2.1 defaults.
migration_url = make_url(config.get_main_option("sqlalchemy.url"))
if migration_url.drivername == "postgresql":
    migration_url = migration_url.set(drivername="postgresql+psycopg2")
    config.set_main_option("sqlalchemy.url", migration_url.render_as_string(hide_password=False))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
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
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
