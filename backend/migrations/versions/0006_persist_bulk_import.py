"""Persist durable Bulk staging and exception evidence.

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "sqlite":
        raise RuntimeError("0006 is a bounded SQLite migration")
    op.create_table(
        "bulk_import_batches",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_key", sa.String(length=64), nullable=False),
        sa.Column("supplier_scope", sa.String(length=200), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("entry_count", sa.Integer(), nullable=False),
        sa.Column("terminal_summary", sa.JSON(), nullable=True),
        sa.Column("terminal_summary_sha256", sa.String(length=64), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('STAGED','PROCESSING','COMPLETED','COMPLETED_WITH_EXCEPTIONS','FAILED')",
            name=op.f("ck_bulk_import_batches_bulk_batch_status"),
        ),
        sa.CheckConstraint(
            "length(manifest_sha256) = 64",
            name=op.f("ck_bulk_import_batches_bulk_batch_manifest_sha"),
        ),
        sa.CheckConstraint(
            "length(project_key) BETWEEN 1 AND 64",
            name=op.f("ck_bulk_import_batches_bulk_batch_project_length"),
        ),
        sa.CheckConstraint(
            "length(supplier_scope) BETWEEN 1 AND 200",
            name=op.f("ck_bulk_import_batches_bulk_batch_supplier_length"),
        ),
        sa.CheckConstraint(
            "length(idempotency_key) BETWEEN 8 AND 128",
            name=op.f("ck_bulk_import_batches_bulk_batch_idempotency_length"),
        ),
        sa.CheckConstraint(
            "entry_count >= 1", name=op.f("ck_bulk_import_batches_bulk_batch_entry_count")
        ),
        sa.CheckConstraint(
            "row_version >= 1", name=op.f("ck_bulk_import_batches_bulk_batch_row_version")
        ),
        sa.CheckConstraint(
            "(terminal_summary IS NULL AND terminal_summary_sha256 IS NULL) OR "
            "(terminal_summary IS NOT NULL AND terminal_summary_sha256 IS NOT NULL "
            "AND length(terminal_summary_sha256) = 64)",
            name=op.f("ck_bulk_import_batches_bulk_batch_summary_shape"),
        ),
        sa.CheckConstraint(
            "(status IN ('STAGED','PROCESSING') AND finished_at IS NULL "
            "AND terminal_summary IS NULL) OR "
            "(status IN ('COMPLETED','COMPLETED_WITH_EXCEPTIONS','FAILED') "
            "AND finished_at IS NOT NULL AND terminal_summary IS NOT NULL)",
            name=op.f("ck_bulk_import_batches_bulk_batch_terminal_shape"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bulk_import_batches")),
        sa.UniqueConstraint("project_key", "id", name="uq_bulk_batches_project_id"),
        sa.UniqueConstraint(
            "project_key", "idempotency_key", name="uq_bulk_batches_project_idempotency"
        ),
    )
    op.create_index(
        "ix_bulk_batches_project_status",
        "bulk_import_batches",
        ["project_key", "status"],
        unique=False,
    )
    op.create_table(
        "bulk_import_entries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_key", sa.String(length=64), nullable=False),
        sa.Column("batch_id", sa.String(length=36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("reserved_receipt_id", sa.String(length=32), nullable=False),
        sa.Column("reserved_received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("filename", sa.String(length=500), nullable=False),
        sa.Column("mime_type", sa.String(length=200), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("upload_sha256", sa.String(length=64), nullable=False),
        sa.Column("staged_relative_path", sa.String(length=1000), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("outcome", sa.String(length=64), nullable=True),
        sa.Column("status_code", sa.String(length=100), nullable=False),
        sa.Column("message", sa.String(length=1000), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("receipt_payload", sa.JSON(), nullable=True),
        sa.Column("receipt_sha256", sa.String(length=64), nullable=True),
        sa.Column("mapping_payload", sa.JSON(), nullable=True),
        sa.Column("mapping_sha256", sa.String(length=64), nullable=True),
        sa.Column("candidate_payload", sa.JSON(), nullable=True),
        sa.Column("candidate_sha256", sa.String(length=64), nullable=True),
        sa.Column("revision_identity", sa.String(length=64), nullable=True),
        sa.Column("revision_evidence", sa.JSON(), nullable=True),
        sa.Column("revision_evidence_sha256", sa.String(length=64), nullable=True),
        sa.Column("issues", sa.JSON(), nullable=False),
        sa.Column("issues_sha256", sa.String(length=64), nullable=False),
        sa.Column("duplicate_of_entry_id", sa.String(length=36), nullable=True),
        sa.Column("revision_baseline_entry_id", sa.String(length=36), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("ordinal >= 0", name=op.f("ck_bulk_import_entries_bulk_entry_ordinal")),
        sa.CheckConstraint("size_bytes >= 0", name=op.f("ck_bulk_import_entries_bulk_entry_size")),
        sa.CheckConstraint(
            "length(project_key) BETWEEN 1 AND 64",
            name=op.f("ck_bulk_import_entries_bulk_entry_project_length"),
        ),
        sa.CheckConstraint(
            "length(filename) BETWEEN 1 AND 500",
            name=op.f("ck_bulk_import_entries_bulk_entry_filename_length"),
        ),
        sa.CheckConstraint(
            "length(mime_type) BETWEEN 1 AND 200",
            name=op.f("ck_bulk_import_entries_bulk_entry_mime_length"),
        ),
        sa.CheckConstraint(
            "length(upload_sha256) = 64",
            name=op.f("ck_bulk_import_entries_bulk_entry_upload_sha"),
        ),
        sa.CheckConstraint(
            "length(reserved_receipt_id) = 32",
            name=op.f("ck_bulk_import_entries_bulk_entry_receipt_id"),
        ),
        sa.CheckConstraint(
            "status IN ('STAGED','PROCESSING','TERMINAL')",
            name=op.f("ck_bulk_import_entries_bulk_entry_status"),
        ),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN ('CANDIDATE_READY','DUPLICATE_CANDIDATE',"
            "'MAPPING_REQUIRED','SCAN_FAILED','IDENTIFIER_HOLD','BINDING_HOLD',"
            "'VARIATION_REVIEW_REQUIRED','REVISION_REVIEW_REQUIRED','ERROR')",
            name=op.f("ck_bulk_import_entries_bulk_entry_outcome"),
        ),
        sa.CheckConstraint(
            "(status IN ('STAGED','PROCESSING') AND outcome IS NULL AND finished_at IS NULL) OR "
            "(status = 'TERMINAL' AND outcome IS NOT NULL AND finished_at IS NOT NULL)",
            name=op.f("ck_bulk_import_entries_bulk_entry_terminal_shape"),
        ),
        sa.CheckConstraint(
            "attempt_count >= 0", name=op.f("ck_bulk_import_entries_bulk_entry_attempt_count")
        ),
        sa.CheckConstraint(
            "row_version >= 1", name=op.f("ck_bulk_import_entries_bulk_entry_row_version")
        ),
        sa.CheckConstraint(
            "(receipt_payload IS NULL AND receipt_sha256 IS NULL) OR "
            "(receipt_payload IS NOT NULL AND receipt_sha256 IS NOT NULL "
            "AND length(receipt_sha256) = 64)",
            name=op.f("ck_bulk_import_entries_bulk_entry_receipt_shape"),
        ),
        sa.CheckConstraint(
            "(mapping_payload IS NULL AND mapping_sha256 IS NULL) OR "
            "(mapping_payload IS NOT NULL AND mapping_sha256 IS NOT NULL "
            "AND length(mapping_sha256) = 64)",
            name=op.f("ck_bulk_import_entries_bulk_entry_mapping_shape"),
        ),
        sa.CheckConstraint(
            "(candidate_payload IS NULL AND candidate_sha256 IS NULL) OR "
            "(candidate_payload IS NOT NULL AND candidate_sha256 IS NOT NULL "
            "AND length(candidate_sha256) = 64)",
            name=op.f("ck_bulk_import_entries_bulk_entry_candidate_shape"),
        ),
        sa.CheckConstraint(
            "(revision_evidence IS NULL AND revision_evidence_sha256 IS NULL) OR "
            "(revision_evidence IS NOT NULL AND revision_evidence_sha256 IS NOT NULL "
            "AND length(revision_evidence_sha256) = 64)",
            name=op.f("ck_bulk_import_entries_bulk_entry_revision_evidence_shape"),
        ),
        sa.CheckConstraint(
            "revision_identity IS NULL OR length(revision_identity) = 64",
            name=op.f("ck_bulk_import_entries_bulk_entry_revision_identity_sha"),
        ),
        sa.CheckConstraint(
            "length(issues_sha256) = 64",
            name=op.f("ck_bulk_import_entries_bulk_entry_issues_sha"),
        ),
        sa.ForeignKeyConstraint(
            ["project_key", "batch_id"],
            ["bulk_import_batches.project_key", "bulk_import_batches.id"],
            name="fk_bulk_entries_project_batch",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_key", "duplicate_of_entry_id"],
            ["bulk_import_entries.project_key", "bulk_import_entries.id"],
            name="fk_bulk_entries_project_duplicate",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_key", "revision_baseline_entry_id"],
            ["bulk_import_entries.project_key", "bulk_import_entries.id"],
            name="fk_bulk_entries_project_revision_baseline",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bulk_import_entries")),
        sa.UniqueConstraint("project_key", "id", name="uq_bulk_entries_project_id"),
        sa.UniqueConstraint(
            "project_key", "batch_id", "ordinal", name="uq_bulk_entries_batch_ordinal"
        ),
        sa.UniqueConstraint(
            "project_key", "reserved_receipt_id", name="uq_bulk_entries_project_receipt"
        ),
    )
    op.create_index(
        "ix_bulk_entries_project_batch_status",
        "bulk_import_entries",
        ["project_key", "batch_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_bulk_entries_project_upload",
        "bulk_import_entries",
        ["project_key", "upload_sha256"],
        unique=False,
    )
    op.create_index(
        "ix_bulk_entries_project_revision_identity",
        "bulk_import_entries",
        ["project_key", "revision_identity"],
        unique=False,
    )


def downgrade() -> None:
    if op.get_bind().dialect.name != "sqlite":
        raise RuntimeError("0006 is a bounded SQLite migration")
    connection = op.get_bind()
    entry_count = connection.scalar(sa.text("SELECT COUNT(*) FROM bulk_import_entries"))
    batch_count = connection.scalar(sa.text("SELECT COUNT(*) FROM bulk_import_batches"))
    if entry_count or batch_count:
        raise RuntimeError("0006 downgrade is blocked while durable Bulk history exists")
    op.drop_index("ix_bulk_entries_project_revision_identity", table_name="bulk_import_entries")
    op.drop_index("ix_bulk_entries_project_upload", table_name="bulk_import_entries")
    op.drop_index("ix_bulk_entries_project_batch_status", table_name="bulk_import_entries")
    op.drop_table("bulk_import_entries")
    op.drop_index("ix_bulk_batches_project_status", table_name="bulk_import_batches")
    op.drop_table("bulk_import_batches")
