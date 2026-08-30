"""SQLAlchemy engine and transaction boundary helpers."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import MetaData, create_engine, event, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.infrastructure.schema import SCHEMA_HEAD_REVISION

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _enable_sqlite_foreign_keys(
    dbapi_connection: Any,
    _connection_record: Any,
) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


class Database:
    """A lazy SQLAlchemy boundary.

    Constructing this object does not connect to SQLite and therefore does not
    create a database file. Connections are opened only by explicit operations.
    """

    def __init__(self, database_url: str) -> None:
        url = make_url(database_url)
        self._sqlite_file_path: Path | None = None
        connect_args: dict[str, object] = {}
        if url.get_backend_name() == "sqlite":
            connect_args["check_same_thread"] = False
            if url.database not in (None, "", ":memory:") and not url.database.startswith("file:"):
                candidate = Path(url.database)
                self._sqlite_file_path = (
                    candidate if candidate.is_absolute() else Path.cwd() / candidate
                )

        self.engine: Engine = create_engine(database_url, connect_args=connect_args)
        if url.get_backend_name() == "sqlite":
            event.listen(self.engine, "connect", _enable_sqlite_foreign_keys)

        self.session_factory = sessionmaker(
            bind=self.engine,
            class_=Session,
            expire_on_commit=False,
        )

    def check(self) -> None:
        """Raise unless connectivity and the complete schema are ready."""

        if self._sqlite_file_path is not None and not self._sqlite_file_path.is_file():
            raise RuntimeError("database file has not been initialized")

        with self.engine.connect() as connection:
            result = connection.scalar(text("SELECT 1"))
            if result != 1:
                raise RuntimeError("database readiness probe returned an invalid result")

            current_revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
            if current_revision != SCHEMA_HEAD_REVISION:
                raise RuntimeError("database schema is not at the expected revision")

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Yield a session while leaving commit ownership to the use case."""

        with self.session_factory() as session:
            yield session

    def dispose(self) -> None:
        self.engine.dispose()
