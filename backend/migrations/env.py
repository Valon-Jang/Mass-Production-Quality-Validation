"""Alembic environment for the Mass Production Quality Validation-owned database."""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.infrastructure.bulk_finalization import BulkFinalizationCommandRow
from app.infrastructure.bulk_import import BulkBatchRow
from app.infrastructure.data_review import DataStatusTransitionRow
from app.infrastructure.long_format import LongSourceFileRow
from app.infrastructure.mapping_templates import MappingTemplateHistoryRow
from app.infrastructure.master_config import CanonicalModelRow
from app.infrastructure.result_replacement import ResultReplacementTransitionRow

config = context.config
_EXPLICIT_DATABASE_URL_SENTINEL = (
    "MASS_PRODUCTION_QUALITY_VALIDATION_EXPLICIT_DATABASE_URL_REQUIRED"
)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

environment_url = os.environ.get("MASS_PRODUCTION_QUALITY_VALIDATION_DATABASE_URL")
if environment_url:
    config.set_main_option("sqlalchemy.url", environment_url.replace("%", "%%"))
elif config.get_main_option("sqlalchemy.url") == _EXPLICIT_DATABASE_URL_SENTINEL:
    raise RuntimeError(
        "Alembic requires an explicit MASS_PRODUCTION_QUALITY_VALIDATION_DATABASE_URL "
        "or a programmatic test URL"
    )

target_metadata = LongSourceFileRow.metadata

# These references make the complete shared Base registry explicit for Alembic.
_REGISTERED_MODELS = (
    MappingTemplateHistoryRow,
    LongSourceFileRow,
    CanonicalModelRow,
    DataStatusTransitionRow,
    BulkBatchRow,
    BulkFinalizationCommandRow,
    ResultReplacementTransitionRow,
)


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
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
        if connection.dialect.name == "sqlite":
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            # SQLAlchemy 2 starts an implicit transaction for the PRAGMA. End it
            # before Alembic opens its migration transaction, otherwise SQLite
            # DDL may persist while the alembic_version row is rolled back.
            connection.commit()
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
