from datetime import UTC, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.domain.audit import AuditChange
from app.domain.identity import LOCAL_OWNER, SYSTEM_ACTOR
from app.infrastructure.audit import AuditLog, AuditRepository
from app.infrastructure.database import Base, Database


def _database(tmp_path: Path) -> Database:
    url = f"sqlite+pysqlite:///{(tmp_path / 'audit.sqlite3').as_posix()}"
    database = Database(url)
    Base.metadata.create_all(database.engine)
    return database


@pytest.mark.required_test_id("DQ-P0-AUDIT-001")
def test_audit_repository_records_actor_changes_and_traceability(tmp_path: Path) -> None:
    database = _database(tmp_path)
    repository = AuditRepository()
    try:
        with database.session() as session:
            record = repository.append(
                session,
                AuditChange(
                    actor=LOCAL_OWNER,
                    action="MAPPING_APPROVED",
                    target_type="mapping_revision",
                    target_id="mapping-1",
                    before_state={"status": "PENDING"},
                    after_state={"status": "APPROVED"},
                    reason="Reviewed against the source workbook",
                    requirement_id="ING-017",
                    source_reference="source-file:sha256-example",
                ),
            )
            assert record.id
            session.commit()

        with database.session() as session:
            stored = repository.list_recent(session, limit=1)[0]
            assert stored.actor_id == "local-owner"
            assert stored.actor_kind == "LOCAL_OWNER"
            assert stored.actor_roles == ["ADMIN", "REVIEWER", "VIEWER"]
            assert stored.before_state == {"status": "PENDING"}
            assert stored.after_state == {"status": "APPROVED"}
            assert stored.requirement_id == "ING-017"
            assert stored.source_reference == "source-file:sha256-example"
            assert stored.occurred_at.tzinfo is UTC
            assert stored.occurred_at.utcoffset() == timedelta(0)
    finally:
        database.dispose()


def test_audit_repository_does_not_commit_for_the_caller(tmp_path: Path) -> None:
    database = _database(tmp_path)
    repository = AuditRepository()
    try:
        with database.session() as session:
            repository.append(
                session,
                AuditChange(
                    actor=SYSTEM_ACTOR,
                    action="CACHE_REBUILD_REQUESTED",
                    target_type="cache_segment",
                    reason="Derived data is rebuildable",
                ),
            )
            session.rollback()

        with database.session() as session:
            count = session.scalar(select(func.count()).select_from(AuditLog))
            assert count == 0
    finally:
        database.dispose()


def test_sqlite_connections_enable_foreign_keys(tmp_path: Path) -> None:
    database = _database(tmp_path)
    try:
        with database.engine.connect() as connection:
            enabled = connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one()
        assert enabled == 1
    finally:
        database.dispose()
