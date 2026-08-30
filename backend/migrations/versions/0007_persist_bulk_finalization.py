"""Persist prepared Bulk checkpoints and explicit asynchronous finalization.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-15
"""

import hashlib
import json
from collections.abc import Sequence
from datetime import date

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_APPLIED_MAPPING_PROOF_VERSION = "long-applied-mapping-proof-v1"


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _required_text(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise RuntimeError(f"0007 cannot verify candidate field {key}")
    return value


def _candidate_payload(value: object) -> dict[str, object]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise RuntimeError("0007 cannot verify a Long candidate snapshot")
    return value


def _backfill_applied_mapping_proof(connection: sa.Connection) -> None:
    rows = connection.execute(
        sa.text(
            "SELECT j.id, j.project_key, j.source_file_id, j.content_sha256, "
            "j.mapping_template_revision_id, j.mapping_payload_sha256, "
            "j.candidate_snapshot, j.candidate_snapshot_sha256, s.receipt_id "
            "FROM ingestion_jobs AS j "
            "JOIN source_files AS s "
            "ON s.project_key = j.project_key AND s.id = j.source_file_id "
            "ORDER BY j.id"
        )
    ).mappings()
    for row in rows:
        snapshot = _candidate_payload(row["candidate_snapshot"])
        snapshot_sha = hashlib.sha256(_canonical_json(snapshot).encode("utf-8")).hexdigest()
        if snapshot_sha != row["candidate_snapshot_sha256"]:
            raise RuntimeError("0007 refused a Long candidate with a mismatched digest")
        provenance_value = snapshot.get("provenance")
        if not isinstance(provenance_value, dict):
            raise RuntimeError("0007 cannot verify Long candidate provenance")
        provenance = provenance_value
        receipt_value = provenance.get("receipt")
        if not isinstance(receipt_value, dict):
            raise RuntimeError("0007 cannot verify Long candidate receipt provenance")
        receipt = receipt_value
        if (
            _required_text(receipt, "project_key") != row["project_key"]
            or _required_text(receipt, "receipt_id") != row["receipt_id"]
            or _required_text(receipt, "content_sha256") != row["content_sha256"]
        ):
            raise RuntimeError("0007 refused inconsistent Long receipt provenance")
        template_revision = provenance.get("template_revision")
        if (
            isinstance(template_revision, bool)
            or not isinstance(template_revision, int)
            or template_revision < 1
        ):
            raise RuntimeError("0007 cannot verify a Long Mapping revision")
        effective_from = _required_text(provenance, "template_effective_from")
        effective_to = provenance.get("template_effective_to")
        if effective_to is not None and not isinstance(effective_to, str):
            raise RuntimeError("0007 cannot verify Long Mapping effectivity")
        try:
            start = date.fromisoformat(effective_from)
            end = date.fromisoformat(effective_to) if isinstance(effective_to, str) else None
        except ValueError as error:
            raise RuntimeError("0007 cannot verify Long Mapping effectivity") from error
        if end is not None and end < start:
            raise RuntimeError("0007 refused reversed Long Mapping effectivity")
        proof = {
            "version": _APPLIED_MAPPING_PROOF_VERSION,
            "project_key": row["project_key"],
            "source_file_id": row["source_file_id"],
            "receipt_id": row["receipt_id"],
            "content_sha256": row["content_sha256"],
            "candidate_snapshot_sha256": row["candidate_snapshot_sha256"],
            "mapping_template_revision_id": row["mapping_template_revision_id"],
            "mapping_payload_sha256": row["mapping_payload_sha256"],
            "supplier_scope": _required_text(provenance, "supplier_scope"),
            "template_id": _required_text(provenance, "template_id"),
            "template_schema_version": _required_text(provenance, "template_schema_version"),
            "template_revision": template_revision,
            "template_effective_from": effective_from,
            "template_effective_to": effective_to,
        }
        proof_json = _canonical_json(proof)
        proof_sha = hashlib.sha256(proof_json.encode("utf-8")).hexdigest()
        connection.execute(
            sa.text(
                "UPDATE ingestion_jobs "
                "SET applied_mapping_proof = :proof, "
                "applied_mapping_proof_sha256 = :proof_sha "
                "WHERE id = :job_id"
            ),
            {"proof": proof_json, "proof_sha": proof_sha, "job_id": row["id"]},
        )


def upgrade() -> None:
    _begin_sqlite_ddl_transaction()

    op.add_column(
        "ingestion_jobs",
        sa.Column("applied_mapping_proof", sa.JSON(), nullable=True),
    )
    op.add_column(
        "ingestion_jobs",
        sa.Column("applied_mapping_proof_sha256", sa.String(length=64), nullable=True),
    )
    _backfill_applied_mapping_proof(op.get_bind())

    op.add_column(
        "bulk_import_entries",
        sa.Column("prepared_checkpoint", sa.JSON(), nullable=True),
    )
    op.add_column(
        "bulk_import_entries",
        sa.Column("prepared_checkpoint_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "bulk_import_entries",
        sa.Column("prepared_checkpoint_version", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "bulk_import_entries",
        sa.Column("prepared_checkpoint_bytes", sa.Integer(), nullable=True),
    )
    with op.batch_alter_table("bulk_import_entries") as batch_op:
        batch_op.create_check_constraint(
            "bulk_entry_prepared_checkpoint_shape",
            "(prepared_checkpoint IS NULL AND prepared_checkpoint_sha256 IS NULL "
            "AND prepared_checkpoint_version IS NULL AND prepared_checkpoint_bytes IS NULL) OR "
            "(prepared_checkpoint IS NOT NULL AND prepared_checkpoint_sha256 IS NOT NULL "
            "AND length(prepared_checkpoint_sha256) = 64 "
            "AND prepared_checkpoint_version = 'bulk-prepared-long-v1' "
            "AND prepared_checkpoint_bytes BETWEEN 1 AND 16777216)",
        )
    op.create_index(
        "uq_bulk_entries_project_id_batch",
        "bulk_import_entries",
        ["project_key", "id", "batch_id"],
        unique=True,
    )

    op.create_table(
        "bulk_finalization_commands",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_key", sa.String(length=64), nullable=False),
        sa.Column("batch_id", sa.String(length=36), nullable=False),
        sa.Column("supplier_scope", sa.String(length=200), nullable=False),
        sa.Column("finalization_digest", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("requested_by", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("entry_count", sa.Integer(), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('QUEUED','PROCESSING','COMPLETED','BLOCKED')",
            name=op.f("ck_bulk_finalization_commands_bulk_finalization_command_status"),
        ),
        sa.CheckConstraint(
            "length(project_key) BETWEEN 1 AND 64",
            name=op.f("ck_bulk_finalization_commands_bulk_finalization_command_project_length"),
        ),
        sa.CheckConstraint(
            "length(supplier_scope) BETWEEN 1 AND 200",
            name=op.f("ck_bulk_finalization_commands_bulk_finalization_command_supplier_length"),
        ),
        sa.CheckConstraint(
            "length(finalization_digest) = 64",
            name=op.f("ck_bulk_finalization_commands_bulk_finalization_command_digest"),
        ),
        sa.CheckConstraint(
            "length(reason) BETWEEN 1 AND 1000",
            name=op.f("ck_bulk_finalization_commands_bulk_finalization_command_reason"),
        ),
        sa.CheckConstraint(
            "entry_count >= 1",
            name=op.f("ck_bulk_finalization_commands_bulk_finalization_command_entry_count"),
        ),
        sa.CheckConstraint(
            "row_version >= 1",
            name=op.f("ck_bulk_finalization_commands_bulk_finalization_command_row_version"),
        ),
        sa.CheckConstraint(
            "(status IN ('QUEUED','PROCESSING') AND finished_at IS NULL) OR "
            "(status IN ('COMPLETED','BLOCKED') AND finished_at IS NOT NULL)",
            name=op.f("ck_bulk_finalization_commands_bulk_finalization_command_terminal_shape"),
        ),
        sa.ForeignKeyConstraint(
            ["project_key", "batch_id"],
            ["bulk_import_batches.project_key", "bulk_import_batches.id"],
            name="fk_bulk_finalization_commands_project_batch",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bulk_finalization_commands")),
        sa.UniqueConstraint("project_key", "id", name="uq_bulk_finalization_commands_project_id"),
        sa.UniqueConstraint(
            "project_key", "batch_id", name="uq_bulk_finalization_commands_project_batch"
        ),
        sa.UniqueConstraint(
            "project_key",
            "id",
            "batch_id",
            name="uq_bulk_finalization_commands_project_id_batch",
        ),
    )
    op.create_index(
        "ix_bulk_finalization_commands_project_status",
        "bulk_finalization_commands",
        ["project_key", "status"],
        unique=False,
    )

    op.create_table(
        "bulk_finalization_entries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_key", sa.String(length=64), nullable=False),
        sa.Column("command_id", sa.String(length=36), nullable=False),
        sa.Column("batch_id", sa.String(length=36), nullable=False),
        sa.Column("bulk_entry_id", sa.String(length=36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("expected_bulk_row_version", sa.Integer(), nullable=False),
        sa.Column("expected_receipt_id", sa.String(length=200), nullable=False),
        sa.Column("expected_content_sha256", sa.String(length=64), nullable=False),
        sa.Column("expected_mapping_sha256", sa.String(length=64), nullable=False),
        sa.Column("expected_candidate_payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("expected_long_candidate_digest", sa.String(length=64), nullable=False),
        sa.Column("expected_checkpoint_sha256", sa.String(length=64), nullable=False),
        sa.Column("expected_checkpoint_version", sa.String(length=64), nullable=False),
        sa.Column("expected_checkpoint_bytes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("long_source_file_id", sa.String(length=36), nullable=True),
        sa.Column("long_ingestion_job_id", sa.String(length=36), nullable=True),
        sa.Column("long_status", sa.String(length=40), nullable=True),
        sa.Column("long_row_version", sa.Integer(), nullable=True),
        sa.Column("replayed", sa.Boolean(), nullable=True),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "ordinal >= 0",
            name=op.f("ck_bulk_finalization_entries_bulk_finalization_entry_ordinal"),
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','PROCESSING','COMPLETED','BLOCKED')",
            name=op.f("ck_bulk_finalization_entries_bulk_finalization_entry_status"),
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name=op.f("ck_bulk_finalization_entries_bulk_finalization_entry_attempt_count"),
        ),
        sa.CheckConstraint(
            "expected_bulk_row_version >= 1",
            name=op.f("ck_bulk_finalization_entries_bulk_finalization_entry_basis_version"),
        ),
        sa.CheckConstraint(
            "row_version >= 1",
            name=op.f("ck_bulk_finalization_entries_bulk_finalization_entry_row_version"),
        ),
        sa.CheckConstraint(
            "length(expected_content_sha256) = 64 "
            "AND length(expected_mapping_sha256) = 64 "
            "AND length(expected_candidate_payload_sha256) = 64 "
            "AND length(expected_long_candidate_digest) = 64 "
            "AND length(expected_checkpoint_sha256) = 64 "
            "AND expected_checkpoint_version = 'bulk-prepared-long-v1' "
            "AND expected_checkpoint_bytes BETWEEN 1 AND 16777216",
            name=op.f("ck_bulk_finalization_entries_bulk_finalization_entry_digest_lengths"),
        ),
        sa.CheckConstraint(
            "(status = 'COMPLETED' AND long_source_file_id IS NOT NULL "
            "AND long_ingestion_job_id IS NOT NULL "
            "AND long_status IN ('COMPLETED_PENDING','REUSED') "
            "AND long_row_version IS NOT NULL AND long_row_version >= 1 "
            "AND replayed IS NOT NULL AND error_code IS NULL AND finished_at IS NOT NULL) OR "
            "(status = 'BLOCKED' AND long_source_file_id IS NULL "
            "AND long_ingestion_job_id IS NULL AND long_status IS NULL "
            "AND long_row_version IS NULL AND replayed IS NULL "
            "AND error_code IS NOT NULL AND finished_at IS NOT NULL) OR "
            "(status IN ('PENDING','PROCESSING') AND long_source_file_id IS NULL "
            "AND long_ingestion_job_id IS NULL AND long_status IS NULL "
            "AND long_row_version IS NULL AND replayed IS NULL "
            "AND error_code IS NULL AND finished_at IS NULL)",
            name=op.f("ck_bulk_finalization_entries_bulk_finalization_entry_result_shape"),
        ),
        sa.ForeignKeyConstraint(
            ["project_key", "command_id", "batch_id"],
            [
                "bulk_finalization_commands.project_key",
                "bulk_finalization_commands.id",
                "bulk_finalization_commands.batch_id",
            ],
            name="fk_bulk_finalization_entries_project_command",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_key", "bulk_entry_id", "batch_id"],
            [
                "bulk_import_entries.project_key",
                "bulk_import_entries.id",
                "bulk_import_entries.batch_id",
            ],
            name="fk_bulk_finalization_entries_project_bulk_entry",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_key", "long_ingestion_job_id", "long_source_file_id"],
            [
                "ingestion_jobs.project_key",
                "ingestion_jobs.id",
                "ingestion_jobs.source_file_id",
            ],
            name="fk_bulk_finalization_entries_project_job_source",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bulk_finalization_entries")),
        sa.UniqueConstraint("project_key", "id", name="uq_bulk_finalization_entries_project_id"),
        sa.UniqueConstraint(
            "project_key",
            "command_id",
            "bulk_entry_id",
            name="uq_bulk_finalization_entries_command_bulk_entry",
        ),
        sa.UniqueConstraint(
            "project_key",
            "command_id",
            "ordinal",
            name="uq_bulk_finalization_entries_command_ordinal",
        ),
    )
    op.create_index(
        "ix_bulk_finalization_entries_project_command_status",
        "bulk_finalization_entries",
        ["project_key", "command_id", "status"],
        unique=False,
    )
    _assert_foreign_keys_clean()


def downgrade() -> None:
    _begin_sqlite_ddl_transaction()
    connection = op.get_bind()
    command_count = connection.scalar(sa.text("SELECT COUNT(*) FROM bulk_finalization_commands"))
    entry_count = connection.scalar(sa.text("SELECT COUNT(*) FROM bulk_finalization_entries"))
    checkpoint_count = connection.scalar(
        sa.text("SELECT COUNT(*) FROM bulk_import_entries WHERE prepared_checkpoint IS NOT NULL")
    )
    if command_count or entry_count or checkpoint_count:
        raise RuntimeError("0007 downgrade is blocked while prepared/finalization history exists")

    op.drop_index(
        "ix_bulk_finalization_entries_project_command_status",
        table_name="bulk_finalization_entries",
    )
    op.drop_table("bulk_finalization_entries")
    op.drop_index(
        "ix_bulk_finalization_commands_project_status",
        table_name="bulk_finalization_commands",
    )
    op.drop_table("bulk_finalization_commands")
    op.drop_index("uq_bulk_entries_project_id_batch", table_name="bulk_import_entries")
    with op.batch_alter_table("bulk_import_entries") as batch_op:
        batch_op.drop_constraint(
            "bulk_entry_prepared_checkpoint_shape",
            type_="check",
        )
        batch_op.drop_column("prepared_checkpoint_bytes")
        batch_op.drop_column("prepared_checkpoint_version")
        batch_op.drop_column("prepared_checkpoint_sha256")
        batch_op.drop_column("prepared_checkpoint")
    # SQLite 3.35+ supports direct DROP COLUMN.  Avoid a batch-recreated parent
    # table because existing OQC lots and ingestion self-links use RESTRICT FKs.
    op.drop_column("ingestion_jobs", "applied_mapping_proof_sha256")
    op.drop_column("ingestion_jobs", "applied_mapping_proof")
    _assert_foreign_keys_clean()


def _begin_sqlite_ddl_transaction() -> None:
    connection = op.get_bind()
    if connection.dialect.name != "sqlite":
        raise RuntimeError("0007 is a bounded SQLite migration")
    driver_connection = connection.connection.driver_connection
    if driver_connection is None:
        raise RuntimeError("0007 requires an active SQLite driver connection")
    if not driver_connection.in_transaction:
        connection.exec_driver_sql("BEGIN IMMEDIATE")


def _assert_foreign_keys_clean() -> None:
    violations = op.get_bind().exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError("0007 migration produced a foreign-key violation")
