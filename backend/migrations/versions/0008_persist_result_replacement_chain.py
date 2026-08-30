"""Persist explicit atomic result-replacement chains.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-16
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

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
    "AND data_status IN ('VALID', 'SUSPECT', 'EXCLUDED', 'REPLACED') "
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
    "AND data_status IN ('SUSPECT', 'EXCLUDED', 'REPLACED') "
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


def upgrade() -> None:
    _begin_sqlite_ddl_transaction()
    _ensure_artifacts_absent()
    _create_replacement_tables()
    transition_backup = _backup_table(
        "data_status_transitions",
        "_data_status_transitions_0008_backup",
    )
    measurement_backup = _backup_table(
        "measurements",
        "_measurements_0008_backup",
    )
    with op.batch_alter_table(
        "inspection_results",
        recreate="always",
        reflect_kwargs={"resolve_fks": False},
    ) as batch_op:
        batch_op.drop_constraint(
            op.f("ck_inspection_results_inspection_result_decision_projection_shape"),
            type_="check",
        )
        batch_op.add_column(
            sa.Column("current_replacement_transition_id", sa.String(36), nullable=True)
        )
        batch_op.create_unique_constraint(
            "uq_inspection_results_project_id_source_lot",
            ["project_key", "id", "source_file_id", "oqc_lot_id"],
        )
        batch_op.create_check_constraint(
            op.f("ck_inspection_results_inspection_result_decision_projection_shape"),
            _RESULT_DECISION_SHAPE,
        )
        batch_op.create_check_constraint(
            op.f("ck_inspection_results_inspection_result_replacement_pointer_shape"),
            "(data_status = 'REPLACED' AND current_replacement_transition_id IS NOT NULL) OR "
            "(data_status != 'REPLACED' AND current_replacement_transition_id IS NULL)",
        )
        batch_op.create_foreign_key(
            "fk_inspection_result_current_replacement",
            "result_replacement_transitions",
            ["project_key", "current_replacement_transition_id", "id", "source_file_id"],
            ["project_key", "id", "predecessor_result_id", "predecessor_source_file_id"],
            ondelete="RESTRICT",
        )
    with op.batch_alter_table(
        "measurements",
        recreate="always",
        reflect_kwargs={"resolve_fks": False},
    ) as batch_op:
        batch_op.add_column(sa.Column("replacement_transition_id", sa.String(36), nullable=True))
        batch_op.create_unique_constraint(
            "uq_measurements_project_id_result_source",
            ["project_key", "id", "inspection_result_id", "source_file_id"],
        )
        batch_op.create_check_constraint(
            op.f("ck_measurements_measurement_replacement_pointer_shape"),
            "(data_status = 'REPLACED' AND replacement_transition_id IS NOT NULL) OR "
            "(data_status != 'REPLACED' AND replacement_transition_id IS NULL)",
        )
        batch_op.create_foreign_key(
            "fk_measurement_replacement_transition",
            "result_replacement_transitions",
            [
                "project_key",
                "replacement_transition_id",
                "inspection_result_id",
                "source_file_id",
            ],
            ["project_key", "id", "predecessor_result_id", "predecessor_source_file_id"],
            ondelete="RESTRICT",
        )
    _assert_backup_equal(transition_backup, "data_status_transitions")
    _assert_backup_equal(measurement_backup, "measurements")
    transition_backup.drop(op.get_bind())
    measurement_backup.drop(op.get_bind())
    _assert_foreign_keys_clean()


def downgrade() -> None:
    _begin_sqlite_ddl_transaction()
    _ensure_artifacts_absent()
    connection = op.get_bind()
    unsafe = any(
        connection.scalar(sa.text(statement))
        for statement in (
            "SELECT EXISTS(SELECT 1 FROM result_replacement_measurements LIMIT 1)",
            "SELECT EXISTS(SELECT 1 FROM result_replacement_transitions LIMIT 1)",
            "SELECT EXISTS(SELECT 1 FROM inspection_results "
            "WHERE data_status = 'REPLACED' "
            "OR current_replacement_transition_id IS NOT NULL LIMIT 1)",
            "SELECT EXISTS(SELECT 1 FROM measurements "
            "WHERE data_status = 'REPLACED' OR replacement_transition_id IS NOT NULL LIMIT 1)",
        )
    )
    if unsafe:
        raise RuntimeError("0008 downgrade refused: result-replacement history would be lost")
    transition_backup = _backup_table(
        "data_status_transitions",
        "_data_status_transitions_0008_backup",
    )
    measurement_backup = _backup_table(
        "measurements",
        "_measurements_0008_backup",
    )
    with op.batch_alter_table(
        "inspection_results",
        recreate="always",
        reflect_kwargs={"resolve_fks": False},
    ) as batch_op:
        batch_op.drop_constraint("fk_inspection_result_current_replacement", type_="foreignkey")
        batch_op.drop_constraint(
            "uq_inspection_results_project_id_source_lot",
            type_="unique",
        )
        batch_op.drop_constraint(
            op.f("ck_inspection_results_inspection_result_replacement_pointer_shape"),
            type_="check",
        )
        batch_op.drop_constraint(
            op.f("ck_inspection_results_inspection_result_decision_projection_shape"),
            type_="check",
        )
        batch_op.drop_column("current_replacement_transition_id")
        batch_op.create_check_constraint(
            op.f("ck_inspection_results_inspection_result_decision_projection_shape"),
            _RESULT_DECISION_SHAPE.replace(
                "'VALID', 'SUSPECT', 'EXCLUDED', 'REPLACED'",
                "'VALID', 'SUSPECT', 'EXCLUDED'",
            ).replace(
                "'SUSPECT', 'EXCLUDED', 'REPLACED'",
                "'SUSPECT', 'EXCLUDED'",
            ),
        )
    with op.batch_alter_table(
        "measurements",
        recreate="always",
        reflect_kwargs={"resolve_fks": False},
    ) as batch_op:
        batch_op.drop_constraint("fk_measurement_replacement_transition", type_="foreignkey")
        batch_op.drop_constraint(
            "uq_measurements_project_id_result_source",
            type_="unique",
        )
        batch_op.drop_constraint(
            op.f("ck_measurements_measurement_replacement_pointer_shape"),
            type_="check",
        )
        batch_op.drop_column("replacement_transition_id")
    _assert_backup_equal(transition_backup, "data_status_transitions")
    _assert_backup_equal(
        measurement_backup,
        "measurements",
        excluded_columns=("replacement_transition_id",),
    )
    op.drop_table("result_replacement_measurements")
    op.drop_index("ix_result_replacement_successor", table_name="result_replacement_transitions")
    op.drop_index(
        "ix_result_replacement_predecessor",
        table_name="result_replacement_transitions",
    )
    op.drop_table("result_replacement_transitions")
    transition_backup.drop(op.get_bind())
    measurement_backup.drop(op.get_bind())
    _assert_foreign_keys_clean()


def _begin_sqlite_ddl_transaction() -> None:
    connection = op.get_bind()
    if connection.dialect.name != "sqlite":
        raise RuntimeError("0008 is a bounded SQLite migration slice")
    driver_connection = connection.connection.driver_connection
    if driver_connection is None:
        raise RuntimeError("0008 requires an active SQLite driver connection")
    if driver_connection.in_transaction:
        raise RuntimeError("0008 requires a clean SQLite migration connection")
    # SQLite cannot safely rebuild both sides of the cyclic
    # result<->data-status-transition relationship while FK enforcement is
    # active.  Alembic owns a NullPool connection for this bounded migration;
    # disable enforcement before BEGIN, rebuild atomically, prove the final
    # graph with foreign_key_check, and let the migration connection close.
    connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
    if connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() != 0:
        raise RuntimeError("0008 requires migration-local foreign-key suspension")
    connection.exec_driver_sql("PRAGMA legacy_alter_table=ON")
    if connection.exec_driver_sql("PRAGMA legacy_alter_table").scalar_one() != 1:
        raise RuntimeError("0008 requires legacy SQLite rename semantics")
    connection.exec_driver_sql("BEGIN IMMEDIATE")


def _ensure_artifacts_absent() -> None:
    names = {
        row[0]
        for row in op.get_bind().exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    artifacts = {
        "_data_status_transitions_0008_backup",
        "_measurements_0008_backup",
    } & names
    if artifacts:
        raise RuntimeError("0008 temporary backup artifact already exists")


def _backup_table(name: str, backup_name: str) -> sa.Table:
    connection = op.get_bind()
    original = sa.Table(name, sa.MetaData(), autoload_with=connection, resolve_fks=False)
    backup = sa.Table(
        backup_name,
        sa.MetaData(),
        *(sa.Column(column.name, column.type, nullable=True) for column in original.columns),
    )
    backup.create(connection)
    column_names = tuple(column.name for column in original.columns)
    connection.execute(
        backup.insert().from_select(
            column_names,
            sa.select(*(original.c[name] for name in column_names)),
        )
    )
    return backup


def _assert_backup_equal(
    backup: sa.Table,
    target_name: str,
    *,
    excluded_columns: tuple[str, ...] = (),
) -> None:
    connection = op.get_bind()
    target = sa.Table(
        target_name,
        sa.MetaData(),
        autoload_with=connection,
        resolve_fks=False,
    )
    columns = tuple(column.name for column in backup.columns if column.name not in excluded_columns)
    before = connection.execute(
        sa.select(*(backup.c[name] for name in columns)).order_by(backup.c.id)
    ).all()
    after = connection.execute(
        sa.select(*(target.c[name] for name in columns)).order_by(target.c.id)
    ).all()
    if before != after:
        raise RuntimeError(f"0008 failed to preserve {target_name} rows exactly")


def _assert_foreign_keys_clean() -> None:
    connection = op.get_bind()
    violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError("0008 migration produced a foreign-key violation")


def _create_replacement_tables() -> None:
    op.create_table(
        "result_replacement_transitions",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("project_key", sa.String(200), nullable=False),
        sa.Column("command_id", sa.String(120), nullable=False),
        sa.Column("intent_sha256", sa.String(64), nullable=False),
        sa.Column("candidate_snapshot", sa.JSON(), nullable=False),
        sa.Column("candidate_sha256", sa.String(64), nullable=False),
        sa.Column("predecessor_result_id", sa.String(36), nullable=False),
        sa.Column("predecessor_source_file_id", sa.String(36), nullable=False),
        sa.Column("predecessor_lot_id", sa.String(36), nullable=False),
        sa.Column(
            "predecessor_original_data_status_transition_id",
            sa.String(36),
            nullable=False,
        ),
        sa.Column("predecessor_before_status", sa.String(16), nullable=False),
        sa.Column("predecessor_after_status", sa.String(16), nullable=False),
        sa.Column("predecessor_before_result_row_version", sa.Integer(), nullable=False),
        sa.Column("predecessor_after_result_row_version", sa.Integer(), nullable=False),
        sa.Column("predecessor_measurement_count", sa.Integer(), nullable=False),
        sa.Column("predecessor_measurement_set_sha256", sa.String(64), nullable=False),
        sa.Column("successor_result_id", sa.String(36), nullable=False),
        sa.Column("successor_source_file_id", sa.String(36), nullable=False),
        sa.Column("successor_lot_id", sa.String(36), nullable=False),
        sa.Column("successor_data_status_transition_id", sa.String(36), nullable=False),
        sa.Column("successor_before_status", sa.String(16), nullable=False),
        sa.Column("successor_after_status", sa.String(16), nullable=False),
        sa.Column("successor_before_result_row_version", sa.Integer(), nullable=False),
        sa.Column("successor_after_result_row_version", sa.Integer(), nullable=False),
        sa.Column("successor_measurement_count", sa.Integer(), nullable=False),
        sa.Column("successor_measurement_set_sha256", sa.String(64), nullable=False),
        sa.Column("decided_by", sa.String(120), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_result_replacement_transitions")),
        sa.UniqueConstraint("project_key", "id", name="uq_result_replacement_project_id"),
        sa.UniqueConstraint(
            "project_key",
            "id",
            "predecessor_result_id",
            "predecessor_source_file_id",
            name="uq_result_replacement_predecessor_projection",
        ),
        sa.UniqueConstraint(
            "project_key",
            "id",
            "successor_result_id",
            "successor_source_file_id",
            name="uq_result_replacement_successor_projection",
        ),
        sa.UniqueConstraint(
            "project_key", "command_id", name="uq_result_replacement_project_command"
        ),
        sa.UniqueConstraint(
            "project_key", "predecessor_result_id", name="uq_result_replacement_outgoing"
        ),
        sa.UniqueConstraint(
            "project_key", "successor_result_id", name="uq_result_replacement_incoming"
        ),
        sa.UniqueConstraint(
            "project_key",
            "predecessor_result_id",
            "successor_result_id",
            name="uq_result_replacement_pair",
        ),
        sa.ForeignKeyConstraint(
            [
                "project_key",
                "predecessor_result_id",
                "predecessor_source_file_id",
                "predecessor_lot_id",
            ],
            [
                "inspection_results.project_key",
                "inspection_results.id",
                "inspection_results.source_file_id",
                "inspection_results.oqc_lot_id",
            ],
            name="fk_result_replacement_predecessor_result",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "project_key",
                "successor_result_id",
                "successor_source_file_id",
                "successor_lot_id",
            ],
            [
                "inspection_results.project_key",
                "inspection_results.id",
                "inspection_results.source_file_id",
                "inspection_results.oqc_lot_id",
            ],
            name="fk_result_replacement_successor_result",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "project_key",
                "predecessor_original_data_status_transition_id",
                "predecessor_result_id",
                "predecessor_source_file_id",
            ],
            [
                "data_status_transitions.project_key",
                "data_status_transitions.id",
                "data_status_transitions.inspection_result_id",
                "data_status_transitions.source_file_id",
            ],
            name="fk_result_replacement_predecessor_decision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "project_key",
                "successor_data_status_transition_id",
                "successor_result_id",
                "successor_source_file_id",
            ],
            [
                "data_status_transitions.project_key",
                "data_status_transitions.id",
                "data_status_transitions.inspection_result_id",
                "data_status_transitions.source_file_id",
            ],
            name="fk_result_replacement_successor_decision",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "predecessor_result_id != successor_result_id",
            name=op.f("ck_result_replacement_transitions_result_replacement_distinct_results"),
        ),
        sa.CheckConstraint(
            "predecessor_before_status IN ('VALID','SUSPECT') "
            "AND predecessor_after_status = 'REPLACED' "
            "AND successor_before_status = 'PENDING' "
            "AND successor_after_status = 'VALID'",
            name=op.f("ck_result_replacement_transitions_result_replacement_status_steps"),
        ),
        sa.CheckConstraint(
            "predecessor_after_result_row_version = "
            "predecessor_before_result_row_version + 1 "
            "AND successor_after_result_row_version = successor_before_result_row_version + 1",
            name=op.f("ck_result_replacement_transitions_result_replacement_result_version_steps"),
        ),
        sa.CheckConstraint(
            "predecessor_measurement_count >= 1 AND successor_measurement_count >= 1",
            name=op.f("ck_result_replacement_transitions_result_replacement_measurement_counts"),
        ),
        sa.CheckConstraint(
            "length(intent_sha256) = 64 AND length(candidate_sha256) = 64 "
            "AND length(predecessor_measurement_set_sha256) = 64 "
            "AND length(successor_measurement_set_sha256) = 64",
            name=op.f("ck_result_replacement_transitions_result_replacement_digest_lengths"),
        ),
    )
    op.create_index(
        "ix_result_replacement_predecessor",
        "result_replacement_transitions",
        ["project_key", "predecessor_result_id"],
        unique=False,
    )
    op.create_index(
        "ix_result_replacement_successor",
        "result_replacement_transitions",
        ["project_key", "successor_result_id"],
        unique=False,
    )
    _create_replacement_measurements()


def _create_replacement_measurements() -> None:
    op.create_table(
        "result_replacement_measurements",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("project_key", sa.String(200), nullable=False),
        sa.Column("transition_id", sa.String(36), nullable=False),
        sa.Column("side", sa.String(16), nullable=False),
        sa.Column("measurement_id", sa.String(36), nullable=False),
        sa.Column("inspection_result_id", sa.String(36), nullable=False),
        sa.Column("source_file_id", sa.String(36), nullable=False),
        sa.Column("predecessor_result_id", sa.String(36), nullable=True),
        sa.Column("predecessor_source_file_id", sa.String(36), nullable=True),
        sa.Column("successor_result_id", sa.String(36), nullable=True),
        sa.Column("successor_source_file_id", sa.String(36), nullable=True),
        sa.Column("sample_ordinal", sa.Integer(), nullable=False),
        sa.Column("before_status", sa.String(16), nullable=False),
        sa.Column("after_status", sa.String(16), nullable=False),
        sa.Column("before_row_version", sa.Integer(), nullable=False),
        sa.Column("after_row_version", sa.Integer(), nullable=False),
        sa.Column("evidence_sha256", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_result_replacement_measurements")),
        sa.UniqueConstraint(
            "project_key",
            "transition_id",
            "side",
            "sample_ordinal",
            name="uq_result_replacement_measurement_ordinal",
        ),
        sa.UniqueConstraint(
            "project_key",
            "transition_id",
            "measurement_id",
            name="uq_result_replacement_measurement_identity",
        ),
        sa.ForeignKeyConstraint(
            ["project_key", "transition_id"],
            ["result_replacement_transitions.project_key", "result_replacement_transitions.id"],
            name="fk_result_replacement_measurement_transition",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_key", "measurement_id", "inspection_result_id", "source_file_id"],
            [
                "measurements.project_key",
                "measurements.id",
                "measurements.inspection_result_id",
                "measurements.source_file_id",
            ],
            name="fk_result_replacement_measurement_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "project_key",
                "transition_id",
                "predecessor_result_id",
                "predecessor_source_file_id",
            ],
            [
                "result_replacement_transitions.project_key",
                "result_replacement_transitions.id",
                "result_replacement_transitions.predecessor_result_id",
                "result_replacement_transitions.predecessor_source_file_id",
            ],
            name="fk_result_replacement_measurement_predecessor",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "project_key",
                "transition_id",
                "successor_result_id",
                "successor_source_file_id",
            ],
            [
                "result_replacement_transitions.project_key",
                "result_replacement_transitions.id",
                "result_replacement_transitions.successor_result_id",
                "result_replacement_transitions.successor_source_file_id",
            ],
            name="fk_result_replacement_measurement_successor",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "side IN ('PREDECESSOR','SUCCESSOR')",
            name=op.f("ck_result_replacement_measurements_replacement_side"),
        ),
        sa.CheckConstraint(
            "(side = 'PREDECESSOR' "
            "AND predecessor_result_id = inspection_result_id "
            "AND predecessor_source_file_id = source_file_id "
            "AND successor_result_id IS NULL AND successor_source_file_id IS NULL) OR "
            "(side = 'SUCCESSOR' "
            "AND successor_result_id = inspection_result_id "
            "AND successor_source_file_id = source_file_id "
            "AND predecessor_result_id IS NULL AND predecessor_source_file_id IS NULL)",
            name=op.f("ck_result_replacement_measurements_replacement_measurement_side_scope"),
        ),
        sa.CheckConstraint(
            "(side = 'PREDECESSOR' AND before_status IN ('VALID','SUSPECT') "
            "AND after_status = 'REPLACED') OR "
            "(side = 'SUCCESSOR' AND before_status = 'PENDING' "
            "AND after_status = 'VALID')",
            name=op.f("ck_result_replacement_measurements_replacement_measurement_status_step"),
        ),
        sa.CheckConstraint(
            "after_row_version = before_row_version + 1 AND sample_ordinal >= 1",
            name=op.f("ck_result_replacement_measurements_replacement_measurement_version_step"),
        ),
        sa.CheckConstraint(
            "length(evidence_sha256) = 64",
            name=op.f("ck_result_replacement_measurements_replacement_measurement_evidence_digest"),
        ),
    )
