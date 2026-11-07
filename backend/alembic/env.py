"""Alembic environment — online + offline, driven by app config + models."""
from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from alembic.ddl.postgresql import PostgresqlImpl
from sqlalchemy import Column, MetaData, PrimaryKeyConstraint, String, Table, engine_from_config, pool

from app.config import settings
from app.db.base import Base
import app.models  # noqa: F401  (register all tables on Base.metadata)


class _LongVersionPostgresqlImpl(PostgresqlImpl):
    """Alembic 1.14 hardcodes alembic_version.version_num to VARCHAR(32) via
    this hook (the old version_table_column_length config option no longer
    has any effect). Several of this project's revision ids run past 32
    chars, so override the hook to widen the column instead of renaming
    already-shipped revisions."""

    __dialect__ = "postgresql"

    def version_table_impl(
        self, *, version_table: str, version_table_schema: str | None, version_table_pk: bool, **kw
    ) -> Table:
        vt = Table(
            version_table,
            MetaData(),
            Column("version_num", String(64), nullable=False),
            schema=version_table_schema,
        )
        if version_table_pk:
            vt.append_constraint(PrimaryKeyConstraint("version_num", name=f"{version_table}_pkc"))
        return vt


config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
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
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
