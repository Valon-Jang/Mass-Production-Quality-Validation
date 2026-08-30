"""Persist source evidence and pending-only Long-format candidates.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_files",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_key", sa.String(length=200), nullable=False),
        sa.Column("receipt_id", sa.String(length=200), nullable=False),
        sa.Column("blob_id", sa.String(length=200), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("original_filename", sa.String(length=500), nullable=False),
        sa.Column("model_candidates", sa.JSON(), nullable=False),
        sa.Column("lot_candidates", sa.JSON(), nullable=False),
        sa.Column("declared_mime_type", sa.String(length=200), nullable=False),
        sa.Column("detected_mime_type", sa.String(length=200), nullable=False),
        sa.Column("canonical_extension", sa.String(length=16), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("parse_status", sa.String(length=40), nullable=False),
        sa.Column("scan_source_name", sa.String(length=500), nullable=False),
        sa.Column("scan_source_size_bytes", sa.Integer(), nullable=False),
        sa.Column("scan_sha256_before", sa.String(length=64), nullable=False),
        sa.Column("scan_sha256_after", sa.String(length=64), nullable=False),
        sa.Column("scan_contract_version", sa.String(length=64), nullable=False),
        sa.Column("estimated_cells", sa.Integer(), nullable=False),
        sa.Column("external_link_count", sa.Integer(), nullable=False),
        sa.Column("macro_handling", sa.String(length=64), nullable=False),
        sa.Column("display_value_contract", sa.String(length=64), nullable=False),
        sa.Column("is_golden_workbook_evidence", sa.Boolean(), nullable=False),
        sa.Column("scan_issues", sa.JSON(), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "size_bytes >= 0",
            name=op.f("ck_source_files_source_file_size_nonnegative"),
        ),
        sa.CheckConstraint(
            "length(content_sha256) = 64",
            name=op.f("ck_source_files_source_file_sha256_length"),
        ),
        sa.CheckConstraint(
            "scan_sha256_before = content_sha256 AND scan_sha256_after = content_sha256",
            name=op.f("ck_source_files_source_file_scan_hash_identity"),
        ),
        sa.CheckConstraint(
            "parse_status IN ('SCANNED', 'SCANNED_WITH_WARNINGS')",
            name=op.f("ck_source_files_source_file_parse_status"),
        ),
        sa.CheckConstraint(
            "scan_source_size_bytes >= 0",
            name=op.f("ck_source_files_source_file_scan_size_nonnegative"),
        ),
        sa.CheckConstraint(
            "estimated_cells >= 0",
            name=op.f("ck_source_files_source_file_scan_cells_nonnegative"),
        ),
        sa.CheckConstraint(
            "external_link_count >= 0",
            name=op.f("ck_source_files_source_file_external_links_nonnegative"),
        ),
        sa.CheckConstraint(
            "row_version >= 1",
            name=op.f("ck_source_files_source_file_row_version"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_files")),
        sa.UniqueConstraint(
            "project_key",
            "receipt_id",
            name="uq_source_files_project_receipt",
        ),
        sa.UniqueConstraint("project_key", "id", name="uq_source_files_project_id"),
    )
    op.create_index(
        "ix_source_files_project_sha256",
        "source_files",
        ["project_key", "content_sha256"],
        unique=False,
    )
    op.create_index(
        "ix_source_files_project_received",
        "source_files",
        ["project_key", "received_at"],
        unique=False,
    )

    op.create_table(
        "source_sheets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_key", sa.String(length=200), nullable=False),
        sa.Column("source_file_id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("sheet_name", sa.String(length=255), nullable=False),
        sa.Column("sheet_kind", sa.String(length=32), nullable=False),
        sa.Column("visibility", sa.String(length=32), nullable=False),
        sa.Column("used_range", sa.String(length=64), nullable=True),
        sa.Column("estimated_cells", sa.Integer(), nullable=False),
        sa.Column("merged_ranges", sa.JSON(), nullable=False),
        sa.Column("hidden_row_ranges", sa.JSON(), nullable=False),
        sa.Column("hidden_column_ranges", sa.JSON(), nullable=False),
        sa.Column("formula_count", sa.Integer(), nullable=False),
        sa.Column("protection_metadata", sa.JSON(), nullable=False),
        sa.Column("image_metadata", sa.JSON(), nullable=False),
        sa.Column("issues", sa.JSON(), nullable=False),
        sa.Column("scan_snapshot", sa.JSON(), nullable=False),
        sa.Column("snapshot_sha256", sa.String(length=64), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "position >= 0",
            name=op.f("ck_source_sheets_source_sheet_position_nonnegative"),
        ),
        sa.CheckConstraint(
            "estimated_cells >= 0",
            name=op.f("ck_source_sheets_source_sheet_cells_nonnegative"),
        ),
        sa.CheckConstraint(
            "formula_count >= 0",
            name=op.f("ck_source_sheets_source_sheet_formula_count_nonnegative"),
        ),
        sa.CheckConstraint(
            "row_version = 1",
            name=op.f("ck_source_sheets_source_sheet_immutable_version"),
        ),
        sa.ForeignKeyConstraint(
            ["project_key", "source_file_id"],
            ["source_files.project_key", "source_files.id"],
            name="fk_source_sheets_project_source_file",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_sheets")),
        sa.UniqueConstraint(
            "project_key",
            "id",
            "source_file_id",
            name="uq_source_sheets_project_id_source",
        ),
        sa.UniqueConstraint(
            "project_key",
            "source_file_id",
            "position",
            name="uq_source_sheets_source_position",
        ),
        sa.UniqueConstraint(
            "project_key",
            "source_file_id",
            "sheet_name",
            name="uq_source_sheets_source_name",
        ),
    )
    op.create_index(
        "ix_source_sheets_project_source",
        "source_sheets",
        ["project_key", "source_file_id"],
        unique=False,
    )

    op.create_table(
        "ingestion_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_key", sa.String(length=200), nullable=False),
        sa.Column("source_file_id", sa.String(length=36), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("mapping_template_revision_id", sa.String(length=36), nullable=False),
        sa.Column("mapping_payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("binding_catalog_revision", sa.String(length=200), nullable=False),
        sa.Column("binding_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("loader_version", sa.String(length=64), nullable=False),
        sa.Column("scan_contract_version", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("materialization_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("owns_materialization", sa.Boolean(), nullable=False),
        sa.Column("reused_job_id", sa.String(length=36), nullable=True),
        sa.Column("blocking_job_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=40), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lot_count", sa.Integer(), nullable=False),
        sa.Column("result_count", sa.Integer(), nullable=False),
        sa.Column("measurement_count", sa.Integer(), nullable=False),
        sa.Column("held_result_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("error_summary", sa.Text(), nullable=True),
        sa.Column("issues", sa.JSON(), nullable=False),
        sa.Column("candidate_snapshot", sa.JSON(), nullable=False),
        sa.Column("candidate_snapshot_sha256", sa.String(length=64), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "status IN ('PROCESSING', 'COMPLETED_PENDING', 'PARTIAL_HELD', "
            "'HELD', 'REUSED', 'RECOVERY_REQUIRED', 'FAILED')",
            name=op.f("ck_ingestion_jobs_ingestion_job_status"),
        ),
        sa.CheckConstraint(
            "row_version >= 1",
            name=op.f("ck_ingestion_jobs_ingestion_job_row_version"),
        ),
        sa.CheckConstraint(
            "lot_count >= 0 AND result_count >= 0 AND measurement_count >= 0 "
            "AND held_result_count >= 0",
            name=op.f("ck_ingestion_jobs_ingestion_job_counts_nonnegative"),
        ),
        sa.CheckConstraint(
            "(status = 'REUSED' AND reused_job_id IS NOT NULL "
            "AND blocking_job_id IS NULL AND owns_materialization = 0) OR "
            "(status <> 'REUSED' AND reused_job_id IS NULL)",
            name=op.f("ck_ingestion_jobs_ingestion_job_reuse_shape"),
        ),
        sa.CheckConstraint(
            "(status = 'RECOVERY_REQUIRED' AND blocking_job_id IS NOT NULL "
            "AND owns_materialization = 0) OR "
            "(status <> 'RECOVERY_REQUIRED' AND blocking_job_id IS NULL)",
            name=op.f("ck_ingestion_jobs_ingestion_job_recovery_shape"),
        ),
        sa.CheckConstraint(
            "(owns_materialization = 1 AND materialization_fingerprint IS NOT NULL "
            "AND status IN ('PROCESSING', 'COMPLETED_PENDING', 'PARTIAL_HELD', 'FAILED')) OR "
            "(owns_materialization = 0 AND materialization_fingerprint IS NULL "
            "AND status IN ('HELD', 'REUSED', 'RECOVERY_REQUIRED'))",
            name=op.f("ck_ingestion_jobs_ingestion_job_materialization_shape"),
        ),
        sa.CheckConstraint(
            "(status = 'PROCESSING' AND finished_at IS NULL) OR "
            "(status <> 'PROCESSING' AND finished_at IS NOT NULL)",
            name=op.f("ck_ingestion_jobs_ingestion_job_finished_shape"),
        ),
        sa.CheckConstraint(
            "(status = 'FAILED' AND error_code IS NOT NULL AND error_summary IS NOT NULL) OR "
            "(status <> 'FAILED' AND error_code IS NULL AND error_summary IS NULL)",
            name=op.f("ck_ingestion_jobs_ingestion_job_error_shape"),
        ),
        sa.ForeignKeyConstraint(
            ["mapping_template_revision_id"],
            ["mapping_template_revisions.id"],
            name="fk_ingestion_jobs_mapping_template_revision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_key", "source_file_id"],
            ["source_files.project_key", "source_files.id"],
            name="fk_ingestion_jobs_project_source_file",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_key", "reused_job_id"],
            ["ingestion_jobs.project_key", "ingestion_jobs.id"],
            name="fk_ingestion_jobs_project_reused_job",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_key", "blocking_job_id"],
            ["ingestion_jobs.project_key", "ingestion_jobs.id"],
            name="fk_ingestion_jobs_project_blocking_job",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ingestion_jobs")),
        sa.UniqueConstraint("project_key", "id", name="uq_ingestion_jobs_project_id"),
        sa.UniqueConstraint(
            "project_key",
            "id",
            "source_file_id",
            name="uq_ingestion_jobs_project_id_source",
        ),
        sa.UniqueConstraint(
            "project_key",
            "source_file_id",
            "mapping_template_revision_id",
            "binding_fingerprint",
            "loader_version",
            "scan_contract_version",
            name="uq_ingestion_jobs_exact_basis",
        ),
        sa.UniqueConstraint(
            "project_key",
            "idempotency_key",
            name="uq_ingestion_jobs_project_idempotency",
        ),
    )
    op.create_index(
        "ix_ingestion_jobs_project_status",
        "ingestion_jobs",
        ["project_key", "status"],
        unique=False,
    )
    op.create_index(
        "ix_ingestion_jobs_project_source",
        "ingestion_jobs",
        ["project_key", "source_file_id"],
        unique=False,
    )
    op.create_index(
        "uq_ingestion_jobs_materialization_owner",
        "ingestion_jobs",
        [
            "project_key",
            "content_sha256",
            "mapping_template_revision_id",
            "binding_fingerprint",
            "loader_version",
            "scan_contract_version",
        ],
        unique=True,
        sqlite_where=sa.text("owns_materialization = 1"),
    )

    op.create_table(
        "oqc_lots",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_key", sa.String(length=200), nullable=False),
        sa.Column("ingestion_job_id", sa.String(length=36), nullable=False),
        sa.Column("source_file_id", sa.String(length=36), nullable=False),
        sa.Column("lot_ordinal", sa.Integer(), nullable=False),
        sa.Column("canonical_model_key", sa.String(length=200), nullable=True),
        sa.Column("canonical_model_part_key", sa.String(length=200), nullable=True),
        sa.Column("canonical_supplier_key", sa.String(length=200), nullable=True),
        sa.Column("source_lot_text", sa.String(length=500), nullable=True),
        sa.Column("inspection_date", sa.Date(), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("identifier_evidence", sa.JSON(), nullable=False),
        sa.Column("identifier_evidence_sha256", sa.String(length=64), nullable=False),
        sa.Column("data_status", sa.String(length=16), nullable=False),
        sa.Column("hold_reasons", sa.JSON(), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "lot_ordinal >= 1",
            name=op.f("ck_oqc_lots_oqc_lot_ordinal_positive"),
        ),
        sa.CheckConstraint(
            "data_status IN ('PENDING', 'HELD')",
            name=op.f("ck_oqc_lots_oqc_lot_pending_status_only"),
        ),
        sa.CheckConstraint(
            "row_version >= 1",
            name=op.f("ck_oqc_lots_oqc_lot_row_version"),
        ),
        sa.ForeignKeyConstraint(
            ["project_key", "ingestion_job_id", "source_file_id"],
            ["ingestion_jobs.project_key", "ingestion_jobs.id", "ingestion_jobs.source_file_id"],
            name="fk_oqc_lots_project_job_source",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_oqc_lots")),
        sa.UniqueConstraint(
            "project_key",
            "id",
            "source_file_id",
            name="uq_oqc_lots_project_id_source",
        ),
        sa.UniqueConstraint(
            "project_key",
            "ingestion_job_id",
            "lot_ordinal",
            name="uq_oqc_lots_job_ordinal",
        ),
    )
    op.create_index(
        "ix_oqc_lots_project_job",
        "oqc_lots",
        ["project_key", "ingestion_job_id"],
        unique=False,
    )
    op.create_index(
        "ix_oqc_lots_natural_candidate",
        "oqc_lots",
        [
            "project_key",
            "canonical_model_key",
            "canonical_model_part_key",
            "canonical_supplier_key",
            "source_lot_text",
            "inspection_date",
        ],
        unique=False,
    )

    op.create_table(
        "inspection_results",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_key", sa.String(length=200), nullable=False),
        sa.Column("oqc_lot_id", sa.String(length=36), nullable=False),
        sa.Column("source_file_id", sa.String(length=36), nullable=False),
        sa.Column("source_sheet_id", sa.String(length=36), nullable=False),
        sa.Column("source_row_key", sa.String(length=200), nullable=False),
        sa.Column("binding_revision", sa.Integer(), nullable=True),
        sa.Column("canonical_model_part_key", sa.String(length=200), nullable=True),
        sa.Column("canonical_item_key", sa.String(length=200), nullable=True),
        sa.Column("supplier_judgment_text", sa.Text(), nullable=True),
        sa.Column("system_judgment", sa.Text(), nullable=True),
        sa.Column("system_judgment_status", sa.String(length=32), nullable=False),
        sa.Column("spec_evaluation_status", sa.String(length=32), nullable=False),
        sa.Column("source_evidence", sa.JSON(), nullable=False),
        sa.Column("source_evidence_sha256", sa.String(length=64), nullable=False),
        sa.Column("binding_snapshot", sa.JSON(), nullable=True),
        sa.Column("binding_snapshot_sha256", sa.String(length=64), nullable=True),
        sa.Column("candidate_snapshot_sha256", sa.String(length=64), nullable=False),
        sa.Column("data_status", sa.String(length=16), nullable=False),
        sa.Column("hold_reasons", sa.JSON(), nullable=False),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "data_status IN ('PENDING', 'HELD')",
            name=op.f("ck_inspection_results_inspection_result_pending_status_only"),
        ),
        sa.CheckConstraint(
            "system_judgment IS NULL AND system_judgment_status = 'NOT_EVALUATED'",
            name=op.f("ck_inspection_results_inspection_result_no_system_judgment"),
        ),
        sa.CheckConstraint(
            "spec_evaluation_status = 'NOT_EVALUATED'",
            name=op.f("ck_inspection_results_inspection_result_no_spec_evaluation"),
        ),
        sa.CheckConstraint(
            "(binding_snapshot IS NULL AND binding_snapshot_sha256 IS NULL) OR "
            "(binding_snapshot IS NOT NULL AND binding_snapshot_sha256 IS NOT NULL)",
            name=op.f("ck_inspection_results_inspection_result_binding_snapshot_shape"),
        ),
        sa.CheckConstraint(
            "row_version >= 1",
            name=op.f("ck_inspection_results_inspection_result_row_version"),
        ),
        sa.ForeignKeyConstraint(
            ["project_key", "oqc_lot_id", "source_file_id"],
            ["oqc_lots.project_key", "oqc_lots.id", "oqc_lots.source_file_id"],
            name="fk_inspection_results_project_lot_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["project_key", "source_sheet_id", "source_file_id"],
            ["source_sheets.project_key", "source_sheets.id", "source_sheets.source_file_id"],
            name="fk_inspection_results_project_sheet_source",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_inspection_results")),
        sa.UniqueConstraint(
            "project_key",
            "id",
            "source_file_id",
            name="uq_inspection_results_project_id_source",
        ),
        sa.UniqueConstraint(
            "project_key",
            "oqc_lot_id",
            "source_row_key",
            name="uq_inspection_results_lot_row_key",
        ),
    )
    op.create_index(
        "ix_inspection_results_item_status",
        "inspection_results",
        ["project_key", "canonical_item_key", "data_status"],
        unique=False,
    )
    op.create_index(
        "ix_inspection_results_project_lot",
        "inspection_results",
        ["project_key", "oqc_lot_id"],
        unique=False,
    )

    op.create_table(
        "measurements",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("project_key", sa.String(length=200), nullable=False),
        sa.Column("inspection_result_id", sa.String(length=36), nullable=False),
        sa.Column("source_file_id", sa.String(length=36), nullable=False),
        sa.Column("source_sheet_id", sa.String(length=36), nullable=False),
        sa.Column("sample_ordinal", sa.Integer(), nullable=False),
        sa.Column("source_cell", sa.String(length=32), nullable=False),
        sa.Column("raw_value_tag", sa.String(length=32), nullable=False),
        sa.Column("raw_value_text", sa.Text(), nullable=True),
        sa.Column("raw_numeric_value", sa.Text(), nullable=True),
        sa.Column("raw_qualitative_value", sa.Text(), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=False),
        sa.Column("formula_flag", sa.Boolean(), nullable=False),
        sa.Column("standardized_value", sa.Text(), nullable=True),
        sa.Column("unit_conversion_status", sa.String(length=32), nullable=False),
        sa.Column("data_status", sa.String(length=16), nullable=False),
        sa.Column("hold_reasons", sa.JSON(), nullable=False),
        sa.Column("superseded_measurement_id", sa.String(length=36), nullable=True),
        sa.Column("row_version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "sample_ordinal >= 1",
            name=op.f("ck_measurements_measurement_sample_positive"),
        ),
        sa.CheckConstraint(
            "data_status IN ('PENDING', 'HELD')",
            name=op.f("ck_measurements_measurement_pending_status_only"),
        ),
        sa.CheckConstraint(
            "standardized_value IS NULL AND unit_conversion_status = 'NOT_CONFIGURED'",
            name=op.f("ck_measurements_measurement_no_standardized_value"),
        ),
        sa.CheckConstraint(
            "row_version >= 1",
            name=op.f("ck_measurements_measurement_row_version"),
        ),
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


def downgrade() -> None:
    op.drop_index("ix_measurements_project_status", table_name="measurements")
    op.drop_index("ix_measurements_source_cell", table_name="measurements")
    op.drop_index("ix_measurements_project_result", table_name="measurements")
    op.drop_table("measurements")
    op.drop_index("ix_inspection_results_project_lot", table_name="inspection_results")
    op.drop_index("ix_inspection_results_item_status", table_name="inspection_results")
    op.drop_table("inspection_results")
    op.drop_index("ix_oqc_lots_natural_candidate", table_name="oqc_lots")
    op.drop_index("ix_oqc_lots_project_job", table_name="oqc_lots")
    op.drop_table("oqc_lots")
    op.drop_index("uq_ingestion_jobs_materialization_owner", table_name="ingestion_jobs")
    op.drop_index("ix_ingestion_jobs_project_source", table_name="ingestion_jobs")
    op.drop_index("ix_ingestion_jobs_project_status", table_name="ingestion_jobs")
    op.drop_table("ingestion_jobs")
    op.drop_index("ix_source_sheets_project_source", table_name="source_sheets")
    op.drop_table("source_sheets")
    op.drop_index("ix_source_files_project_received", table_name="source_files")
    op.drop_index("ix_source_files_project_sha256", table_name="source_files")
    op.drop_table("source_files")
