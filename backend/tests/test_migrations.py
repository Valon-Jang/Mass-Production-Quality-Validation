from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from app.infrastructure.schema import SCHEMA_HEAD_REVISION

ROOT = Path(__file__).resolve().parents[2]


def _config(database_url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "migrations"))
    config.set_main_option("prepend_sys_path", str(ROOT / "backend"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


@pytest.mark.required_test_id("DQ-P0-MIG-001")
def test_fresh_upgrade_downgrade_and_reupgrade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MASS_PRODUCTION_QUALITY_VALIDATION_DATABASE_URL", raising=False)
    database_path = tmp_path / "migration.sqlite3"
    database_url = f"sqlite+pysqlite:///{database_path.as_posix()}"
    config = _config(database_url)
    assert ScriptDirectory.from_config(config).get_current_head() == SCHEMA_HEAD_REVISION

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    try:
        assert set(inspect(engine).get_table_names()) == {
            "alembic_version",
            "audit_log",
            "bulk_import_batches",
            "bulk_import_entries",
            "bulk_finalization_commands",
            "bulk_finalization_entries",
            "canonical_inspection_items",
            "canonical_model_parts",
            "canonical_models",
            "canonical_row_binding_histories",
            "canonical_row_binding_revisions",
            "canonical_row_binding_supersessions",
            "canonical_suppliers",
            "data_status_transitions",
            "ingestion_jobs",
            "inspection_results",
            "mapping_template_histories",
            "mapping_template_revisions",
            "mapping_template_supersessions",
            "master_spec_histories",
            "master_spec_revisions",
            "master_spec_supersessions",
            "measurements",
            "oqc_lots",
            "result_replacement_measurements",
            "result_replacement_transitions",
            "source_files",
            "source_sheets",
        }
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == SCHEMA_HEAD_REVISION
            )
        columns = {column["name"] for column in inspect(engine).get_columns("audit_log")}
        assert columns == {
            "id",
            "occurred_at",
            "actor_id",
            "actor_kind",
            "actor_roles",
            "action",
            "target_type",
            "target_id",
            "before_state",
            "after_state",
            "reason",
            "requirement_id",
            "source_reference",
        }
    finally:
        engine.dispose()

    command.downgrade(config, "base")
    engine = create_engine(database_url)
    try:
        assert "audit_log" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    try:
        assert "audit_log" in inspect(engine).get_table_names()
    finally:
        engine.dispose()


@pytest.mark.required_test_id("DQ-P0-MIG-002")
def test_alembic_cli_refuses_an_implicit_workspace_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MASS_PRODUCTION_QUALITY_VALIDATION_DATABASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "migrations"))
    config.set_main_option("prepend_sys_path", str(ROOT / "backend"))

    with pytest.raises(
        RuntimeError, match="requires an explicit MASS_PRODUCTION_QUALITY_VALIDATION_DATABASE_URL"
    ):
        command.current(config)

    assert tuple(tmp_path.iterdir()) == ()


@pytest.mark.required_test_id("DQ-P1-MAP-015")
def test_0001_audit_data_survives_mapping_upgrade_downgrade_and_reupgrade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MASS_PRODUCTION_QUALITY_VALIDATION_DATABASE_URL", raising=False)
    database_path = tmp_path / "mapping-upgrade.sqlite3"
    database_url = f"sqlite+pysqlite:///{database_path.as_posix()}"
    config = _config(database_url)

    command.upgrade(config, "0001")
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO audit_log "
                    "(id, occurred_at, actor_id, actor_kind, actor_roles, action, "
                    "target_type, target_id, before_state, after_state, reason, "
                    "requirement_id, source_reference) VALUES "
                    "(:id, :occurred_at, :actor_id, :actor_kind, :actor_roles, :action, "
                    ":target_type, NULL, NULL, NULL, :reason, NULL, NULL)"
                ),
                {
                    "id": "preserved-audit-row",
                    "occurred_at": "2026-08-15 06:30:00",
                    "actor_id": "local-owner",
                    "actor_kind": "LOCAL_OWNER",
                    "actor_roles": '["ADMIN"]',
                    "action": "PREEXISTING_ACTION",
                    "target_type": "migration_evidence",
                    "reason": "Verify 0001 audit preservation.",
                },
            )
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == SCHEMA_HEAD_REVISION
            )
            assert (
                connection.scalar(
                    text("SELECT action FROM audit_log WHERE id = 'preserved-audit-row'")
                )
                == "PREEXISTING_ACTION"
            )
        assert "mapping_template_revisions" in inspect(engine).get_table_names()
    finally:
        engine.dispose()

    command.downgrade(config, "0001")
    engine = create_engine(database_url)
    try:
        assert set(inspect(engine).get_table_names()) == {"alembic_version", "audit_log"}
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0001"
            assert (
                connection.scalar(
                    text("SELECT COUNT(*) FROM audit_log WHERE id = 'preserved-audit-row'")
                )
                == 1
            )
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    try:
        assert "mapping_template_supersessions" in inspect(engine).get_table_names()
        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT COUNT(*) FROM audit_log WHERE id = 'preserved-audit-row'")
                )
                == 1
            )
    finally:
        engine.dispose()


@pytest.mark.required_test_id("DQ-P1-LDB-008")
def test_0002_audit_and_mapping_survive_long_upgrade_downgrade_and_reupgrade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MASS_PRODUCTION_QUALITY_VALIDATION_DATABASE_URL", raising=False)
    database_path = tmp_path / "long-upgrade.sqlite3"
    database_url = f"sqlite+pysqlite:///{database_path.as_posix()}"
    config = _config(database_url)

    fresh_path = tmp_path / "long-fresh.sqlite3"
    fresh_url = f"sqlite+pysqlite:///{fresh_path.as_posix()}"
    fresh_config = _config(fresh_url)
    command.upgrade(fresh_config, "head")
    fresh_engine = create_engine(fresh_url)
    try:
        fresh_tables = set(inspect(fresh_engine).get_table_names())
        assert {
            "source_files",
            "source_sheets",
            "ingestion_jobs",
            "oqc_lots",
            "inspection_results",
            "measurements",
        }.issubset(fresh_tables)
        with fresh_engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == SCHEMA_HEAD_REVISION
            )
    finally:
        fresh_engine.dispose()

    command.upgrade(config, "0002")
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO audit_log "
                    "(id, occurred_at, actor_id, actor_kind, actor_roles, action, "
                    "target_type, target_id, before_state, after_state, reason, "
                    "requirement_id, source_reference) VALUES "
                    "('long-preserved-audit', '2026-08-15 08:30:00', 'local-owner', "
                    "'LOCAL_OWNER', '[\"ADMIN\"]', 'PREEXISTING_MAPPING_APPROVAL', "
                    "'mapping_template_revision', 'history-1:1', NULL, NULL, "
                    "'Preserve prior evidence.', 'GOV-008', NULL)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO mapping_template_histories "
                    "(id, project_key, supplier_scope, template_id, row_version, created_at) "
                    "VALUES ('history-1', 'project-alpha', 'supplier-alpha', "
                    "'oqc-layout', 3, '2026-08-15 08:00:00')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO mapping_template_revisions "
                    "(id, history_id, revision, schema_version, status, template_payload, "
                    "payload_sha256, declared_effective_from, declared_effective_to, "
                    "resolved_effective_to, reviewed_by, reviewed_at, approved_by, "
                    "approved_at, row_version, created_at) VALUES "
                    "('revision-1', 'history-1', 1, '1', 'APPROVED', '{}', "
                    "'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', "
                    "'2026-01-01', '2026-12-31', NULL, 'reviewer', "
                    "'2026-08-15 08:10:00', 'admin', '2026-08-15 08:20:00', 3, "
                    "'2026-08-15 08:00:00')"
                )
            )
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == SCHEMA_HEAD_REVISION
            )
            assert connection.scalar(text("SELECT COUNT(*) FROM audit_log")) == 1
            assert connection.scalar(text("SELECT COUNT(*) FROM mapping_template_revisions")) == 1
        assert "measurements" in inspect(engine).get_table_names()
    finally:
        engine.dispose()

    command.downgrade(config, "0002")
    engine = create_engine(database_url)
    try:
        tables = set(inspect(engine).get_table_names())
        assert "source_files" not in tables
        assert "measurements" not in tables
        assert "mapping_template_revisions" in tables
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0002"
            assert connection.scalar(text("SELECT COUNT(*) FROM audit_log")) == 1
            assert connection.scalar(text("SELECT COUNT(*) FROM mapping_template_revisions")) == 1
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT COUNT(*) FROM audit_log")) == 1
            assert connection.scalar(text("SELECT COUNT(*) FROM mapping_template_revisions")) == 1
        assert "source_files" in inspect(engine).get_table_names()
    finally:
        engine.dispose()
