import os
from logging.config import fileConfig

from geoalchemy2 import alembic_helpers
from sqlalchemy import engine_from_config, pool

from alembic import context

# Importing the models registers every table on SQLModel.metadata, which is
# what autogenerate compares the live database against.
from tracker_mcp.models import SQLModel

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The connection URL comes from the environment, not alembic.ini, so the same
# DATABASE_URI drives the app and migrations.
database_uri = os.environ.get("DATABASE_URI")
if database_uri:
    config.set_main_option("sqlalchemy.url", database_uri)

target_metadata = SQLModel.metadata

# GeoAlchemy2's helpers teach autogenerate to render spatial column types and to
# ignore PostGIS-managed objects (e.g. the spatial_ref_sys table) so they don't
# show up as spurious drift.
_geo_kwargs = {
    "render_item": alembic_helpers.render_item,
    "include_object": alembic_helpers.include_object,
    "process_revision_directives": alembic_helpers.writer,
}


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode, emitting SQL without a DB connection."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        **_geo_kwargs,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode against a live database connection."""
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
            **_geo_kwargs,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
