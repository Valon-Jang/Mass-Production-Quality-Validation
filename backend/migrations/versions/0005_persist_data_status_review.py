"""Persist explicit data-status decisions and approved-Master evidence.

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from alembic.operations import BatchOperations

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MEASUREMENT_COLUMNS = (
    "id",
    "project_key",
    "inspection_result_id",
    "source_file_id",
    "source_sheet_id",
    "sample_ordinal",
    "source_cell",
    "raw_value_tag",
    "raw_value_text",
    "raw_numeric_value",
    "raw_qualitative_value",
    "evidence",
    "evidence_sha256",
    "formula_flag",
    "standardized_value",
    "unit_conversion_status",
    "data_status",
    "hold_reasons",
    "superseded_measurement_id",
    "row_version",
)

_RESULT_DECISION_SHAPE = (
    "(current_data_status_transition_id IS NULL "
    "AND current_decision_command_id IS NULL "
    "AND current_decision_candidate_sha256 IS NULL "
    "AND current_decision_mode IS NULL "
    "AND applied_master_history_id IS NULL "
    "AND applied_master_revision_id IS NULL "
    "AND applied_master_revision_number IS NULL "
    "AND applied_master_history_row_version IS NULL "
    "AND applied_master_revision_row_version IS NULL "
    "AND applied_master_payload_sha256 IS NULL "
    "AND applied_master_declared_effective_from IS NULL "
    "AND applied_master_declared_effective_to IS NULL "
    "AND applied_master_resolved_effective_to IS NULL "
    "AND current_decided_by IS NULL AND current_decided_at IS NULL "
    "AND current_decision_reason IS NULL "
    "AND system_judgment IS NULL "
    "AND system_judgment_status = 'NOT_EVALUATED' "
    "AND spec_evaluation_status = 'NOT_EVALUATED' "
    "AND data_status IN ('PENDING', 'HELD')) OR "
    "(current_data_status_transition_id IS NOT NULL "
    "AND current_decision_command_id IS NOT NULL "
    "AND current_decision_candidate_sha256 IS NOT NULL "
    "AND current_decision_mode IN ('EVALUATED', 'REVIEW_ONLY') "
    "AND current_decided_by IS NOT NULL AND current_decided_at IS NOT NULL "
    "AND current_decision_reason IS NOT NULL "
    "AND ((current_decision_mode = 'EVALUATED' "
    "AND data_status IN ('VALID', 'SUSPECT', 'EXCLUDED') "
    "AND system_judgment IN ('PASS', 'FAIL') "
    "AND system_judgment_status = 'EVALUATED' "
    "AND spec_evaluation_status = 'EVALUATED_APPROVED_MASTER' "
    "AND applied_master_history_id IS NOT NULL "
    "AND applied_master_revision_id IS NOT NULL "
    "AND applied_master_revision_number IS NOT NULL "
    "AND applied_master_history_row_version IS NOT NULL "
    "AND applied_master_revision_row_version IS NOT NULL "
    "AND applied_master_payload_sha256 IS NOT NULL "
    "AND applied_master_revision_number >= 1 "
    "AND applied_master_history_row_version >= 1 "
    "AND applied_master_revision_row_version >= 1 "
    "AND length(applied_master_payload_sha256) = 64 "
    "AND applied_master_declared_effective_from IS NOT NULL) OR "
    "(current_decision_mode = 'REVIEW_ONLY' "
    "AND data_status IN ('SUSPECT', 'EXCLUDED') "
    "AND system_judgment IS NULL "
    "AND system_judgment_status = 'NOT_EVALUATED' "
    "AND spec_evaluation_status = 'NOT_EVALUATED' "
    "AND applied_master_history_id IS NULL "
    "AND applied_master_revision_id IS NULL "
    "AND applied_master_revision_number IS NULL "
    "AND applied_master_history_row_version IS NULL "
    "AND applied_master_revision_row_version IS NULL "
    "AND applied_master_payload_sha256 IS NULL "
    "AND applied_master_declared_effective_from IS NULL "
    "AND applied_master_declared_effective_to IS NULL "
    "AND applied_master_resolved_effective_to IS NULL)))"
)

_TRANSITION_EVALUATION_SHAPE = (
    "(evaluation_mode = 'EVALUATED' "
    "AND to_status IN ('VALID', 'SUSPECT', 'EXCLUDED') "
    "AND system_judgment IN ('PASS', 'FAIL') "
    "AND system_judgment_status = 'EVALUATED' "
    "AND spec_evaluation_status = 'EVALUATED_APPROVED_MASTER' "
    "AND applied_master_history_id IS NOT NULL "
    "AND applied_master_revision_id IS NOT NULL "
    "AND applied_master_revision_number IS NOT NULL "
    "AND applied_master_history_row_version IS NOT NULL "
    "AND applied_master_revision_row_version IS NOT NULL "
    "AND applied_master_payload_sha256 IS NOT NULL "
    "AND applied_master_revision_number >= 1 "
    "AND applied_master_history_row_version >= 1 "
    "AND applied_master_revision_row_version >= 1 "
    "AND length(applied_master_payload_sha256) = 64 "
    "AND applied_master_declared_effective_from IS NOT NULL) OR "
    "(evaluation_mode = 'REVIEW_ONLY' "
    "AND to_status IN ('SUSPECT', 'EXCLUDED') "
    "AND system_judgment IS NULL "
    "AND system_judgment_status = 'NOT_EVALUATED' "
    "AND spec_evaluation_status = 'NOT_EVALUATED' "
    "AND applied_master_history_id IS NULL "
    "AND applied_master_revision_id IS NULL "
    "AND applied_master_revision_number IS NULL "
    "AND applied_master_history_row_version IS NULL "
    "AND applied_master_revision_row_version IS NULL "
    "AND applied_master_payload_sha256 IS NULL "
    "AND applied_master_declared_effective_from IS NULL "
    "AND applied_master_declared_effective_to IS NULL "
    "AND applied_master_resolved_effective_to IS NULL)"
)


def upgrade() -> None:
    _begin_sqlite_ddl_transaction()
    _create_transition_table()
    _backup_and_drop_measurements()
    with op.batch_alter_table("inspection_results", recreate="always") as batch_op:
        batch_op.drop_constraint(
            op.f("ck_inspection_results_inspection_result_pending_status_only"),
            type_="check",
        )
        batch_op.drop_constraint(
            op.f("ck_inspection_results_inspection_result_no_system_judgment"),
            type_="check",
        )
        batch_op.drop_constraint(
            op.f("ck_inspection_results_inspection_result_no_spec_evaluation"),
            type_="check",
        )
        _add_result_projection_columns(batch_op)
        batch_op.create_check_constraint(
            op.f("ck_inspection_results_inspection_result_data_status"),
            "data_status IN ('PENDING', 'HELD', 'VALID', 'SUSPECT', 'EXCLUDED', 'REPLACED')",
        )
        batch_op.create_check_constraint(
            op.f("ck_inspection_results_inspection_result_judgment_status"),
            "system_judgment_status IN ('NOT_EVALUATED', 'EVALUATED')",
        )
        batch_op.create_check_constraint(
            op.f("ck_inspection_results_inspection_result_spec_evaluation_status"),
            "spec_evaluation_status IN ('NOT_EVALUATED', 'EVALUATED_APPROVED_MASTER')",
        )
        batch_op.create_check_constraint(
            op.f("ck_inspection_results_inspection_result_decision_projection_shape"),
            _RESULT_DECISION_SHAPE,
        )
        batch_op.create_check_constraint(
            op.f("ck_inspection_results_inspection_result_decision_digest_length"),
            "current_decision_candidate_sha256 IS NULL "
            "OR length(current_decision_candidate_sha256) = 64",
        )
        batch_op.create_check_constraint(
            op.f("ck_inspection_results_inspection_result_decision_command_nonblank"),
            "current_decision_command_id IS NULL OR length(current_decision_command_id) > 0",
        )
        batch_op.create_check_constraint(
            op.f("ck_inspection_results_inspection_result_evidence_digest_lengths"),
            "length(source_evidence_sha256) = 64 "
            "AND length(candidate_snapshot_sha256) = 64 "
            "AND (binding_snapshot_sha256 IS NULL "
            "OR length(binding_snapshot_sha256) = 64)",
        )
        batch_op.create_foreign_key(
            "fk_inspection_result_applied_master_revision",
            "master_spec_revisions",
            ["project_key", "applied_master_revision_id", "applied_master_history_id"],
            ["project_key", "id", "history_id"],
            ondelete="RESTRICT",
        )
        batch_op.create_foreign_key(
            "fk_inspection_result_applied_master_history",
            "master_spec_histories",
            ["project_key", "applied_master_history_id"],
            ["project_key", "id"],
            ondelete="RESTRICT",
        )
        batch_op.create_foreign_key(
            "fk_inspection_result_current_data_status_transition",
            "data_status_transitions",
            [
                "project_key",
                "current_data_status_transition_id",
                "id",
                "source_file_id",
            ],
            ["project_key", "id", "inspection_result_id", "source_file_id"],
            ondelete="RESTRICT",
        )
    _create_measurements(
        status_check=(
            "data_status IN ('PENDING', 'HELD', 'VALID', 'SUSPECT', 'EXCLUDED', 'REPLACED')"
        ),
        status_constraint="measurement_data_status",
        include_digest_check=True,
    )
    _restore_and_drop_measurement_backup()
    _assert_foreign_keys_clean()


def downgrade() -> None:
    _begin_sqlite_ddl_transaction()
    connection = op.get_bind()
    unsafe = any(
        connection.scalar(sa.text(statement))
        for statement in (
            "SELECT EXISTS(SELECT 1 FROM data_status_transitions LIMIT 1)",
            "SELECT EXISTS(SELECT 1 FROM inspection_results "
            "WHERE data_status NOT IN ('PENDING','HELD') "
            "OR current_data_status_transition_id IS NOT NULL "
            "OR current_decision_command_id IS NOT NULL "
            "OR current_decision_candidate_sha256 IS NOT NULL "
            "OR current_decision_mode IS NOT NULL "
            "OR applied_master_history_id IS NOT NULL "
            "OR applied_master_revision_id IS NOT NULL "
            "OR applied_master_revision_number IS NOT NULL "
            "OR applied_master_history_row_version IS NOT NULL "
            "OR applied_master_revision_row_version IS NOT NULL "
            "OR applied_master_payload_sha256 IS NOT NULL "
            "OR applied_master_declared_effective_from IS NOT NULL "
            "OR applied_master_declared_effective_to IS NOT NULL "
            "OR applied_master_resolved_effective_to IS NOT NULL "
            "OR current_decided_by IS NOT NULL "
            "OR current_decided_at IS NOT NULL "
            "OR current_decision_reason IS NOT NULL "
            "OR system_judgment IS NOT NULL "
            "OR system_judgment_status <> 'NOT_EVALUATED' "
            "OR spec_evaluation_status <> 'NOT_EVALUATED' LIMIT 1)",
            "SELECT EXISTS(SELECT 1 FROM measurements "
            "WHERE data_status NOT IN ('PENDING','HELD') LIMIT 1)",
        )
    )
    if unsafe:
        raise RuntimeError(
            "0005 downgrade refused: explicit terminal data-status decisions would be lost"
        )

    _backup_and_drop_measurements()
    with op.batch_alter_table("inspection_results", recreate="always") as batch_op:
        batch_op.drop_constraint(
            "fk_inspection_result_current_data_status_transition",
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            "fk_inspection_result_applied_master_revision",
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            "fk_inspection_result_applied_master_history",
            type_="foreignkey",
        )
        for name in (
            "ck_inspection_results_inspection_result_data_status",
            "ck_inspection_results_inspection_result_judgment_status",
            "ck_inspection_results_inspection_result_spec_evaluation_status",
            "ck_inspection_results_inspection_result_decision_projection_shape",
            "ck_inspection_results_inspection_result_decision_digest_length",
            "ck_inspection_results_inspection_result_decision_command_nonblank",
            "ck_inspection_results_inspection_result_evidence_digest_lengths",
        ):
            batch_op.drop_constraint(op.f(name), type_="check")
        _drop_result_projection_columns(batch_op)
        batch_op.create_check_constraint(
            op.f("ck_inspection_results_inspection_result_pending_status_only"),
            "data_status IN ('PENDING', 'HELD')",
        )
        batch_op.create_check_constraint(
            op.f("ck_inspection_results_inspection_result_no_system_judgment"),
            "system_judgment IS NULL AND system_judgment_status = 'NOT_EVALUATED'",
        )
        batch_op.create_check_constraint(
            op.f("ck_inspection_results_inspection_result_no_spec_evaluation"),
            "spec_evaluation_status = 'NOT_EVALUATED'",
        )
    op.drop_index("ix_data_status_transitions_master", table_name="data_status_transitions")
    op.drop_index("ix_data_status_transitions_result", table_name="data_status_transitions")
    op.drop_table("data_status_transitions")
    _create_measurements(
        status_check="data_status IN ('PENDING', 'HELD')",
        status_constraint="measurement_pending_status_only",
        include_digest_check=False,
    )
    _restore_and_drop_measurement_backup()
    _assert_foreign_keys_clean()


def _begin_sqlite_ddl_transaction() -> None:
    connection = op.get_bind()
    if connection.dialect.name != "sqlite":
        raise RuntimeError("0005 is a bounded SQLite migration slice")
    driver_connection = connection.connection.driver_connection
    if driver_connection is None:
        raise RuntimeError("0005 requires an active SQLite driver connection")
    if not driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")


def _assert_foreign_keys_clean() -> None:
    violations = op.get_bind().exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError("0005 migration produced a foreign-key violation")


def _add_result_projection_columns(batch_op: BatchOperations) -> None:
    add_column = batch_op.add_column
    add_column(sa.Column("current_data_status_transition_id", sa.String(36), nullable=True))
    add_column(sa.Column("current_decision_command_id", sa.String(120), nullable=True))
    add_column(sa.Column("current_decision_candidate_sha256", sa.String(64), nullable=True))
    add_column(sa.Column("current_decision_mode", sa.String(32), nullable=True))
    add_column(sa.Column("applied_master_history_id", sa.String(36), nullable=True))
    add_column(sa.Column("applied_master_revision_id", sa.String(36), nullable=True))
    add_column(sa.Column("applied_master_revision_number", sa.Integer(), nullable=True))
    add_column(sa.Column("applied_master_history_row_version", sa.Integer(), nullable=True))
    add_column(sa.Column("applied_master_revision_row_version", sa.Integer(), nullable=True))
    add_column(sa.Column("applied_master_payload_sha256", sa.String(64), nullable=True))
    add_column(sa.Column("applied_master_declared_effective_from", sa.Date(), nullable=True))
    add_column(sa.Column("applied_master_declared_effective_to", sa.Date(), nullable=True))
    add_column(sa.Column("applied_master_resolved_effective_to", sa.Date(), nullable=True))
    add_column(sa.Column("current_decided_by", sa.String(120), nullable=True))
    add_column(sa.Column("current_decided_at", sa.DateTime(timezone=True), nullable=True))
    add_column(sa.Column("current_decision_reason", sa.Text(), nullable=True))


def _drop_result_projection_columns(batch_op: BatchOperations) -> None:
    drop_column = batch_op.drop_column
    for name in (
        "current_decision_reason",
        "current_decided_at",
        "current_decided_by",
        "applied_master_resolved_effective_to",
        "applied_master_declared_effective_to",
        "applied_master_declared_effective_from",
        "applied_master_payload_sha256",
        "applied_master_revision_row_version",
        "applied_master_history_row_version",
        "applied_master_revision_number",
        "applied_master_revision_id",
        "applied_master_history_id",
        "current_decision_mode",
        "current_decision_candidate_sha256",
        "current_decision_command_id",
        "current_data_status_transition_id",
    ):
        drop_column(name)


def _backup_and_drop_measurements() -> None:
    op.create_table(
        "_measurements_0005_backup",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("project_key", sa.String(200), nullable=False),
        sa.Column("inspection_result_id", sa.String(36), nullable=False),
        sa.Column("source_file_id", sa.String(36), nullable=False),
        sa.Column("source_sheet_id", sa.String(36), nullable=False),
        sa.Column("sample_ordinal", sa.Integer(), nullable=False),
        sa.Column("source_cell", sa.String(32), nullable=False),
        sa.Column("raw_value_tag", sa.String(32), nullable=False),
        sa.Column("raw_value_text", sa.Text(), nullable=True),
        sa.Column("raw_numeric_value", sa.Text(), nullable=True),
        sa.Column("raw_qualitative_value", sa.Text(), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("evidence_sha256", sa.String(64), nullable=False),
        sa.Column("formula_flag", sa.Boolean(), nullable=False),
        sa.Column("standardized_value", sa.Text(), nullable=True),
        sa.Column("unit_conversion_status", sa.String(32), nullable=False),
        sa.Column("data_status", sa.String(16), nullable=False),
        sa.Column("hold_reasons", sa.JSON(), nullable=False),
        sa.Column("superseded_measurement_id", sa.String(36), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False),
    )
    columns = ", ".join(_MEASUREMENT_COLUMNS)
    op.execute(
        sa.text(
            f"INSERT INTO _measurements_0005_backup ({columns}) SELECT {columns} FROM measurements"
        )
    )
    op.drop_index("ix_measurements_project_status", table_name="measurements")
    op.drop_index("ix_measurements_source_cell", table_name="measurements")
    op.drop_index("ix_measurements_project_result", table_name="measurements")
    op.drop_table("measurements")


def _restore_and_drop_measurement_backup() -> None:
    columns = ", ".join(_MEASUREMENT_COLUMNS)
    op.execute(
        sa.text(
            f"INSERT INTO measurements ({columns}) SELECT {columns} FROM _measurements_0005_backup"
        )
    )
    op.drop_table("_measurements_0005_backup")


def _create_measurements(
    *,
    status_check: str,
    status_constraint: str,
    include_digest_check: bool,
) -> None:
    checks = [
        sa.CheckConstraint(
            "sample_ordinal >= 1",
            name=op.f("ck_measurements_measurement_sample_positive"),
        ),
        sa.CheckConstraint(
            status_check,
            name=op.f(f"ck_measurements_{status_constraint}"),
        ),
        sa.CheckConstraint(
            "standardized_value IS NULL AND unit_conversion_status = 'NOT_CONFIGURED'",
            name=op.f("ck_measurements_measurement_no_standardized_value"),
        ),
        sa.CheckConstraint(
            "row_version >= 1",
            name=op.f("ck_measurements_measurement_row_version"),
        ),
    ]
    if include_digest_check:
        checks.append(
            sa.CheckConstraint(
                "length(evidence_sha256) = 64",
                name=op.f("ck_measurements_measurement_evidence_digest_length"),
            )
        )
    op.create_table(
        "measurements",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("project_key", sa.String(200), nullable=False),
        sa.Column("inspection_result_id", sa.String(36), nullable=False),
        sa.Column("source_file_id", sa.String(36), nullable=False),
        sa.Column("source_sheet_id", sa.String(36), nullable=False),
        sa.Column("sample_ordinal", sa.Integer(), nullable=False),
        sa.Column("source_cell", sa.String(32), nullable=False),
        sa.Column("raw_value_tag", sa.String(32), nullable=False),
        sa.Column("raw_value_text", sa.Text(), nullable=True),
        sa.Column("raw_numeric_value", sa.Text(), nullable=True),
        sa.Column("raw_qualitative_value", sa.Text(), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("evidence_sha256", sa.String(64), nullable=False),
        sa.Column("formula_flag", sa.Boolean(), nullable=False),
        sa.Column("standardized_value", sa.Text(), nullable=True),
        sa.Column("unit_conversion_status", sa.String(32), nullable=False),
        sa.Column("data_status", sa.String(16), nullable=False),
        sa.Column("hold_reasons", sa.JSON(), nullable=False),
        sa.Column("superseded_measurement_id", sa.String(36), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False),
        *checks,
        sa.ForeignKeyConstraint(
            ["project_key", "inspection_result_id", "source_file_id"],
            [
                "inspection_results.project_key",
                "inspection_results.id",
                "inspection_results.source_file_id",
            ],
            name="fk_measurements_project_result_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_key", "source_sheet_id", "source_file_id"],
            ["source_sheets.project_key", "source_sheets.id", "source_sheets.source_file_id"],
            name="fk_measurements_project_sheet_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_key", "superseded_measurement_id"],
            ["measurements.project_key", "measurements.id"],
            name="fk_measurements_project_superseded_measurement",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_measurements")),
        sa.UniqueConstraint("project_key", "id", name="uq_measurements_project_id"),
        sa.UniqueConstraint(
            "project_key",
            "inspection_result_id",
            "sample_ordinal",
            name="uq_measurements_result_sample",
        ),
    )
    op.create_index(
        "ix_measurements_project_result",
        "measurements",
        ["project_key", "inspection_result_id"],
        unique=False,
    )
    op.create_index(
        "ix_measurements_source_cell",
        "measurements",
        ["project_key", "source_sheet_id", "source_cell"],
        unique=False,
    )
    op.create_index(
        "ix_measurements_project_status",
        "measurements",
        ["project_key", "data_status"],
        unique=False,
    )


def _create_transition_table() -> None:
    op.create_table(
        "data_status_transitions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("project_key", sa.String(200), nullable=False),
        sa.Column("source_file_id", sa.String(36), nullable=False),
        sa.Column("inspection_result_id", sa.String(36), nullable=False),
        sa.Column("command_id", sa.String(120), nullable=False),
        sa.Column("intent_sha256", sa.String(64), nullable=False),
        sa.Column("from_status", sa.String(16), nullable=False),
        sa.Column("to_status", sa.String(16), nullable=False),
        sa.Column("before_result_row_version", sa.Integer(), nullable=False),
        sa.Column("after_result_row_version", sa.Integer(), nullable=False),
        sa.Column("measurement_count", sa.Integer(), nullable=False),
        sa.Column("candidate_snapshot", sa.JSON(), nullable=False),
        sa.Column("candidate_sha256", sa.String(64), nullable=False),
        sa.Column("decision_snapshot", sa.JSON(), nullable=False),
        sa.Column("decision_snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("evaluation_mode", sa.String(32), nullable=False),
        sa.Column("system_judgment", sa.String(16), nullable=True),
        sa.Column("system_judgment_status", sa.String(32), nullable=False),
        sa.Column("spec_evaluation_status", sa.String(40), nullable=False),
        sa.Column("applied_master_history_id", sa.String(36), nullable=True),
        sa.Column("applied_master_revision_id", sa.String(36), nullable=True),
        sa.Column("applied_master_revision_number", sa.Integer(), nullable=True),
        sa.Column("applied_master_history_row_version", sa.Integer(), nullable=True),
        sa.Column("applied_master_revision_row_version", sa.Integer(), nullable=True),
        sa.Column("applied_master_payload_sha256", sa.String(64), nullable=True),
        sa.Column("applied_master_declared_effective_from", sa.Date(), nullable=True),
        sa.Column("applied_master_declared_effective_to", sa.Date(), nullable=True),
        sa.Column("applied_master_resolved_effective_to", sa.Date(), nullable=True),
        sa.Column("decided_by", sa.String(120), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "from_status = 'PENDING'",
            name=op.f("ck_data_status_transitions_data_status_transition_from_pending"),
        ),
        sa.CheckConstraint(
            "to_status IN ('VALID', 'SUSPECT', 'EXCLUDED')",
            name=op.f("ck_data_status_transitions_data_status_transition_target"),
        ),
        sa.CheckConstraint(
            "after_result_row_version = before_result_row_version + 1",
            name=op.f("ck_data_status_transitions_data_status_transition_result_version_step"),
        ),
        sa.CheckConstraint(
            "measurement_count >= 0",
            name=op.f("ck_data_status_transitions_data_status_transition_measurement_count"),
        ),
        sa.CheckConstraint(
            "length(candidate_sha256) = 64 AND length(intent_sha256) = 64 "
            "AND length(decision_snapshot_sha256) = 64",
            name=op.f("ck_data_status_transitions_data_status_transition_digest_lengths"),
        ),
        sa.CheckConstraint(
            _TRANSITION_EVALUATION_SHAPE,
            name=op.f("ck_data_status_transitions_data_status_transition_evaluation_shape"),
        ),
        sa.CheckConstraint(
            "applied_master_declared_effective_to IS NULL "
            "OR applied_master_declared_effective_to >= "
            "applied_master_declared_effective_from",
            name=op.f("ck_data_status_transitions_data_status_transition_master_declared_period"),
        ),
        sa.CheckConstraint(
            "applied_master_resolved_effective_to IS NULL "
            "OR (applied_master_resolved_effective_to >= "
            "applied_master_declared_effective_from "
            "AND (applied_master_declared_effective_to IS NULL "
            "OR applied_master_resolved_effective_to <= "
            "applied_master_declared_effective_to))",
            name=op.f("ck_data_status_transitions_data_status_transition_master_resolved_period"),
        ),
        sa.ForeignKeyConstraint(
            ["project_key", "inspection_result_id", "source_file_id"],
            [
                "inspection_results.project_key",
                "inspection_results.id",
                "inspection_results.source_file_id",
            ],
            name="fk_data_status_transition_result_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_key", "source_file_id"],
            ["source_files.project_key", "source_files.id"],
            name="fk_data_status_transition_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_key", "applied_master_history_id"],
            ["master_spec_histories.project_key", "master_spec_histories.id"],
            name="fk_data_status_transition_master_history",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_key", "applied_master_revision_id", "applied_master_history_id"],
            [
                "master_spec_revisions.project_key",
                "master_spec_revisions.id",
                "master_spec_revisions.history_id",
            ],
            name="fk_data_status_transition_master_revision",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_data_status_transitions")),
        sa.UniqueConstraint(
            "project_key",
            "id",
            name="uq_data_status_transition_project_id",
        ),
        sa.UniqueConstraint(
            "project_key",
            "id",
            "inspection_result_id",
            "source_file_id",
            name="uq_data_status_transition_projection_scope",
        ),
        sa.UniqueConstraint(
            "project_key",
            "command_id",
            name="uq_data_status_transition_project_command",
        ),
        sa.UniqueConstraint(
            "project_key",
            "inspection_result_id",
            "before_result_row_version",
            name="uq_data_status_transition_result_before_version",
        ),
    )
    op.create_index(
        "ix_data_status_transitions_result",
        "data_status_transitions",
        ["project_key", "inspection_result_id", "before_result_row_version"],
        unique=False,
    )
    op.create_index(
        "ix_data_status_transitions_master",
        "data_status_transitions",
        ["project_key", "applied_master_history_id", "applied_master_revision_id"],
        unique=False,
    )
