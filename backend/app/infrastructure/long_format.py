"""SQLite-backed pending Long-format persistence and exact source evidence."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    select,
    text,
    update,
)
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.domain.long_format import (
    CanonicalRowBinding,
    CanonicalRowBindingKey,
    CanonicalRowBindingSelectionSignature,
    CanonicalRowBindingSignature,
    CanonicalRowBindingStatus,
    LongCandidateIssue,
    LongCandidateResult,
    LongCandidateState,
    LongInspectionCandidate,
    LongMeasurementCandidate,
    LongRowState,
)
from app.domain.mapping import (
    CellAddress,
    IdentifierPreview,
    MappedCellEvidence,
    PreviewValueKind,
)
from app.domain.source_file import SourceFileReceipt
from app.domain.workbook_scan import (
    CellEvidence,
    DisplayValueStatus,
    ImageMetadata,
    IndexRange,
    IssueSeverity,
    MacroHandling,
    RowCandidate,
    RowCandidateKind,
    ScanIssue,
    SheetKind,
    SheetProtectionMetadata,
    SheetScan,
    SourceLocation,
    SourceLocationKind,
    WorkbookScan,
    WorkbookScanState,
)
from app.infrastructure.audit import UTCDateTime
from app.infrastructure.database import Base

APPLIED_MAPPING_PROOF_VERSION = "long-applied-mapping-proof-v1"


def _new_id() -> str:
    return str(uuid4())


class SourceParseStatus(StrEnum):
    SCANNED = "SCANNED"
    SCANNED_WITH_WARNINGS = "SCANNED_WITH_WARNINGS"


class LongJobStatus(StrEnum):
    PROCESSING = "PROCESSING"
    COMPLETED_PENDING = "COMPLETED_PENDING"
    PARTIAL_HELD = "PARTIAL_HELD"
    HELD = "HELD"
    REUSED = "REUSED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    FAILED = "FAILED"


class PendingDataStatus(StrEnum):
    PENDING = "PENDING"
    HELD = "HELD"


class LongSourceFileRow(Base):
    __tablename__ = "source_files"
    __table_args__ = (
        UniqueConstraint(
            "project_key",
            "receipt_id",
            name="uq_source_files_project_receipt",
        ),
        UniqueConstraint(
            "project_key",
            "id",
            name="uq_source_files_project_id",
        ),
        CheckConstraint("size_bytes >= 0", name="source_file_size_nonnegative"),
        CheckConstraint("length(content_sha256) = 64", name="source_file_sha256_length"),
        CheckConstraint(
            "scan_sha256_before = content_sha256 AND scan_sha256_after = content_sha256",
            name="source_file_scan_hash_identity",
        ),
        CheckConstraint(
            "parse_status IN ('SCANNED', 'SCANNED_WITH_WARNINGS')",
            name="source_file_parse_status",
        ),
        CheckConstraint("scan_source_size_bytes >= 0", name="source_file_scan_size_nonnegative"),
        CheckConstraint("estimated_cells >= 0", name="source_file_scan_cells_nonnegative"),
        CheckConstraint("external_link_count >= 0", name="source_file_external_links_nonnegative"),
        CheckConstraint("row_version >= 1", name="source_file_row_version"),
        Index("ix_source_files_project_sha256", "project_key", "content_sha256"),
        Index("ix_source_files_project_received", "project_key", "received_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    project_key: Mapped[str] = mapped_column(String(200), nullable=False)
    receipt_id: Mapped[str] = mapped_column(String(200), nullable=False)
    blob_id: Mapped[str] = mapped_column(String(200), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    model_candidates: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    lot_candidates: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    declared_mime_type: Mapped[str] = mapped_column(String(200), nullable=False)
    detected_mime_type: Mapped[str] = mapped_column(String(200), nullable=False)
    canonical_extension: Mapped[str] = mapped_column(String(16), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    parse_status: Mapped[str] = mapped_column(String(40), nullable=False)
    scan_source_name: Mapped[str] = mapped_column(String(500), nullable=False)
    scan_source_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    scan_sha256_before: Mapped[str] = mapped_column(String(64), nullable=False)
    scan_sha256_after: Mapped[str] = mapped_column(String(64), nullable=False)
    scan_contract_version: Mapped[str] = mapped_column(String(64), nullable=False)
    estimated_cells: Mapped[int] = mapped_column(Integer, nullable=False)
    external_link_count: Mapped[int] = mapped_column(Integer, nullable=False)
    macro_handling: Mapped[str] = mapped_column(String(64), nullable=False)
    display_value_contract: Mapped[str] = mapped_column(String(64), nullable=False)
    is_golden_workbook_evidence: Mapped[bool] = mapped_column(Boolean, nullable=False)
    scan_issues: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class LongSourceSheetRow(Base):
    __tablename__ = "source_sheets"
    __table_args__ = (
        UniqueConstraint(
            "project_key",
            "id",
            "source_file_id",
            name="uq_source_sheets_project_id_source",
        ),
        UniqueConstraint(
            "project_key",
            "source_file_id",
            "position",
            name="uq_source_sheets_source_position",
        ),
        UniqueConstraint(
            "project_key",
            "source_file_id",
            "sheet_name",
            name="uq_source_sheets_source_name",
        ),
        ForeignKeyConstraint(
            ["project_key", "source_file_id"],
            ["source_files.project_key", "source_files.id"],
            ondelete="RESTRICT",
            name="fk_source_sheets_project_source_file",
        ),
        CheckConstraint("position >= 0", name="source_sheet_position_nonnegative"),
        CheckConstraint("estimated_cells >= 0", name="source_sheet_cells_nonnegative"),
        CheckConstraint("formula_count >= 0", name="source_sheet_formula_count_nonnegative"),
        CheckConstraint("row_version = 1", name="source_sheet_immutable_version"),
        Index("ix_source_sheets_project_source", "project_key", "source_file_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    project_key: Mapped[str] = mapped_column(String(200), nullable=False)
    source_file_id: Mapped[str] = mapped_column(String(36), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    sheet_name: Mapped[str] = mapped_column(String(255), nullable=False)
    sheet_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    visibility: Mapped[str] = mapped_column(String(32), nullable=False)
    used_range: Mapped[str | None] = mapped_column(String(64), nullable=True)
    estimated_cells: Mapped[int] = mapped_column(Integer, nullable=False)
    merged_ranges: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    hidden_row_ranges: Mapped[list[dict[str, int]]] = mapped_column(JSON, nullable=False)
    hidden_column_ranges: Mapped[list[dict[str, int]]] = mapped_column(JSON, nullable=False)
    formula_count: Mapped[int] = mapped_column(Integer, nullable=False)
    protection_metadata: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    image_metadata: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    issues: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    scan_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class LongIngestionJobRow(Base):
    __tablename__ = "ingestion_jobs"
    __table_args__ = (
        UniqueConstraint(
            "project_key",
            "id",
            name="uq_ingestion_jobs_project_id",
        ),
        UniqueConstraint(
            "project_key",
            "id",
            "source_file_id",
            name="uq_ingestion_jobs_project_id_source",
        ),
        UniqueConstraint(
            "project_key",
            "source_file_id",
            "mapping_template_revision_id",
            "binding_fingerprint",
            "loader_version",
            "scan_contract_version",
            name="uq_ingestion_jobs_exact_basis",
        ),
        UniqueConstraint(
            "project_key",
            "idempotency_key",
            name="uq_ingestion_jobs_project_idempotency",
        ),
        ForeignKeyConstraint(
            ["project_key", "source_file_id"],
            ["source_files.project_key", "source_files.id"],
            ondelete="RESTRICT",
            name="fk_ingestion_jobs_project_source_file",
        ),
        ForeignKeyConstraint(
            ["project_key", "reused_job_id"],
            ["ingestion_jobs.project_key", "ingestion_jobs.id"],
            ondelete="RESTRICT",
            name="fk_ingestion_jobs_project_reused_job",
        ),
        ForeignKeyConstraint(
            ["project_key", "blocking_job_id"],
            ["ingestion_jobs.project_key", "ingestion_jobs.id"],
            ondelete="RESTRICT",
            name="fk_ingestion_jobs_project_blocking_job",
        ),
        CheckConstraint(
            "status IN ('PROCESSING', 'COMPLETED_PENDING', 'PARTIAL_HELD', "
            "'HELD', 'REUSED', 'RECOVERY_REQUIRED', 'FAILED')",
            name="ingestion_job_status",
        ),
        CheckConstraint("row_version >= 1", name="ingestion_job_row_version"),
        CheckConstraint(
            "lot_count >= 0 AND result_count >= 0 AND measurement_count >= 0 "
            "AND held_result_count >= 0",
            name="ingestion_job_counts_nonnegative",
        ),
        CheckConstraint(
            "(status = 'REUSED' AND reused_job_id IS NOT NULL "
            "AND blocking_job_id IS NULL AND owns_materialization = 0) OR "
            "(status <> 'REUSED' AND reused_job_id IS NULL)",
            name="ingestion_job_reuse_shape",
        ),
        CheckConstraint(
            "(status = 'RECOVERY_REQUIRED' AND blocking_job_id IS NOT NULL "
            "AND owns_materialization = 0) OR "
            "(status <> 'RECOVERY_REQUIRED' AND blocking_job_id IS NULL)",
            name="ingestion_job_recovery_shape",
        ),
        CheckConstraint(
            "(owns_materialization = 1 AND materialization_fingerprint IS NOT NULL "
            "AND status IN ('PROCESSING', 'COMPLETED_PENDING', 'PARTIAL_HELD', 'FAILED')) OR "
            "(owns_materialization = 0 AND materialization_fingerprint IS NULL "
            "AND status IN ('HELD', 'REUSED', 'RECOVERY_REQUIRED'))",
            name="ingestion_job_materialization_shape",
        ),
        CheckConstraint(
            "(status = 'PROCESSING' AND finished_at IS NULL) OR "
            "(status <> 'PROCESSING' AND finished_at IS NOT NULL)",
            name="ingestion_job_finished_shape",
        ),
        CheckConstraint(
            "(status = 'FAILED' AND error_code IS NOT NULL AND error_summary IS NOT NULL) OR "
            "(status <> 'FAILED' AND error_code IS NULL AND error_summary IS NULL)",
            name="ingestion_job_error_shape",
        ),
        ForeignKeyConstraint(
            ["mapping_template_revision_id"],
            ["mapping_template_revisions.id"],
            ondelete="RESTRICT",
            name="fk_ingestion_jobs_mapping_template_revision",
        ),
        Index("ix_ingestion_jobs_project_status", "project_key", "status"),
        Index("ix_ingestion_jobs_project_source", "project_key", "source_file_id"),
        Index(
            "uq_ingestion_jobs_materialization_owner",
            "project_key",
            "content_sha256",
            "mapping_template_revision_id",
            "binding_fingerprint",
            "loader_version",
            "scan_contract_version",
            unique=True,
            sqlite_where=text("owns_materialization = 1"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    project_key: Mapped[str] = mapped_column(String(200), nullable=False)
    source_file_id: Mapped[str] = mapped_column(String(36), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    mapping_template_revision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    mapping_payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    binding_catalog_revision: Mapped[str] = mapped_column(String(200), nullable=False)
    binding_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    loader_version: Mapped[str] = mapped_column(String(64), nullable=False)
    scan_contract_version: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False)
    materialization_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    owns_materialization: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reused_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    blocking_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    lot_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    result_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    measurement_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    held_result_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    issues: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    candidate_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    candidate_snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # Physically nullable so SQLite can add/drop the bounded projection without
    # rebuilding this parent table while historical Long child rows reference
    # it.  Migration backfill and every application write populate both; all
    # readers fail closed on a partial or null pair.
    applied_mapping_proof: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    applied_mapping_proof_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class OqcLotRow(Base):
    __tablename__ = "oqc_lots"
    __table_args__ = (
        UniqueConstraint(
            "project_key",
            "id",
            "source_file_id",
            name="uq_oqc_lots_project_id_source",
        ),
        UniqueConstraint(
            "project_key",
            "ingestion_job_id",
            "lot_ordinal",
            name="uq_oqc_lots_job_ordinal",
        ),
        ForeignKeyConstraint(
            ["project_key", "ingestion_job_id", "source_file_id"],
            ["ingestion_jobs.project_key", "ingestion_jobs.id", "ingestion_jobs.source_file_id"],
            ondelete="RESTRICT",
            name="fk_oqc_lots_project_job_source",
        ),
        CheckConstraint("lot_ordinal >= 1", name="oqc_lot_ordinal_positive"),
        CheckConstraint(
            "data_status IN ('PENDING', 'HELD')",
            name="oqc_lot_pending_status_only",
        ),
        CheckConstraint("row_version >= 1", name="oqc_lot_row_version"),
        Index("ix_oqc_lots_project_job", "project_key", "ingestion_job_id"),
        Index(
            "ix_oqc_lots_natural_candidate",
            "project_key",
            "canonical_model_key",
            "canonical_model_part_key",
            "canonical_supplier_key",
            "source_lot_text",
            "inspection_date",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    project_key: Mapped[str] = mapped_column(String(200), nullable=False)
    ingestion_job_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_file_id: Mapped[str] = mapped_column(String(36), nullable=False)
    lot_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    canonical_model_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    canonical_model_part_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    canonical_supplier_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    source_lot_text: Mapped[str | None] = mapped_column(String(500), nullable=True)
    inspection_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    received_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    identifier_evidence: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    identifier_evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    data_status: Mapped[str] = mapped_column(String(16), nullable=False)
    hold_reasons: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class LongInspectionResultRow(Base):
    __tablename__ = "inspection_results"
    __table_args__ = (
        UniqueConstraint(
            "project_key",
            "id",
            "source_file_id",
            name="uq_inspection_results_project_id_source",
        ),
        UniqueConstraint(
            "project_key",
            "id",
            "source_file_id",
            "oqc_lot_id",
            name="uq_inspection_results_project_id_source_lot",
        ),
        UniqueConstraint(
            "project_key",
            "oqc_lot_id",
            "source_row_key",
            name="uq_inspection_results_lot_row_key",
        ),
        ForeignKeyConstraint(
            ["project_key", "oqc_lot_id", "source_file_id"],
            ["oqc_lots.project_key", "oqc_lots.id", "oqc_lots.source_file_id"],
            ondelete="RESTRICT",
            name="fk_inspection_results_project_lot_source",
        ),
        ForeignKeyConstraint(
            ["project_key", "source_sheet_id", "source_file_id"],
            ["source_sheets.project_key", "source_sheets.id", "source_sheets.source_file_id"],
            ondelete="RESTRICT",
            name="fk_inspection_results_project_sheet_source",
        ),
        CheckConstraint(
            "data_status IN ('PENDING', 'HELD', 'VALID', 'SUSPECT', 'EXCLUDED', 'REPLACED')",
            name="inspection_result_data_status",
        ),
        CheckConstraint(
            "system_judgment_status IN ('NOT_EVALUATED', 'EVALUATED')",
            name="inspection_result_judgment_status",
        ),
        CheckConstraint(
            "spec_evaluation_status IN ('NOT_EVALUATED', 'EVALUATED_APPROVED_MASTER')",
            name="inspection_result_spec_evaluation_status",
        ),
        CheckConstraint(
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
            "AND applied_master_resolved_effective_to IS NULL)))",
            name="inspection_result_decision_projection_shape",
        ),
        CheckConstraint(
            "current_decision_candidate_sha256 IS NULL "
            "OR length(current_decision_candidate_sha256) = 64",
            name="inspection_result_decision_digest_length",
        ),
        CheckConstraint(
            "current_decision_command_id IS NULL OR length(current_decision_command_id) > 0",
            name="inspection_result_decision_command_nonblank",
        ),
        CheckConstraint(
            "(data_status = 'REPLACED' AND current_replacement_transition_id IS NOT NULL) OR "
            "(data_status != 'REPLACED' AND current_replacement_transition_id IS NULL)",
            name="inspection_result_replacement_pointer_shape",
        ),
        CheckConstraint(
            "length(source_evidence_sha256) = 64 "
            "AND length(candidate_snapshot_sha256) = 64 "
            "AND (binding_snapshot_sha256 IS NULL OR length(binding_snapshot_sha256) = 64)",
            name="inspection_result_evidence_digest_lengths",
        ),
        CheckConstraint(
            "(binding_snapshot IS NULL AND binding_snapshot_sha256 IS NULL) OR "
            "(binding_snapshot IS NOT NULL AND binding_snapshot_sha256 IS NOT NULL)",
            name="inspection_result_binding_snapshot_shape",
        ),
        CheckConstraint("row_version >= 1", name="inspection_result_row_version"),
        Index(
            "ix_inspection_results_item_status",
            "project_key",
            "canonical_item_key",
            "data_status",
        ),
        Index("ix_inspection_results_project_lot", "project_key", "oqc_lot_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    project_key: Mapped[str] = mapped_column(String(200), nullable=False)
    oqc_lot_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_file_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_sheet_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_row_key: Mapped[str] = mapped_column(String(200), nullable=False)
    binding_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    canonical_model_part_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    canonical_item_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    supplier_judgment_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    system_judgment: Mapped[str | None] = mapped_column(Text, nullable=True)
    system_judgment_status: Mapped[str] = mapped_column(String(32), nullable=False)
    spec_evaluation_status: Mapped[str] = mapped_column(String(32), nullable=False)
    source_evidence: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    source_evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    binding_snapshot: Mapped[dict[str, object] | None] = mapped_column(
        JSON(none_as_null=True),
        nullable=True,
    )
    binding_snapshot_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    candidate_snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    data_status: Mapped[str] = mapped_column(String(16), nullable=False)
    hold_reasons: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    current_data_status_transition_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    current_decision_command_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    current_decision_candidate_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    current_decision_mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    applied_master_history_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    applied_master_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    applied_master_revision_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    applied_master_history_row_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    applied_master_revision_row_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    applied_master_payload_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    applied_master_declared_effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    applied_master_declared_effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    applied_master_resolved_effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    current_decided_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    current_decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    current_decision_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_replacement_transition_id: Mapped[str | None] = mapped_column(
        String(36),
        nullable=True,
    )
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class LongMeasurementRow(Base):
    __tablename__ = "measurements"
    __table_args__ = (
        UniqueConstraint(
            "project_key",
            "inspection_result_id",
            "sample_ordinal",
            name="uq_measurements_result_sample",
        ),
        ForeignKeyConstraint(
            ["project_key", "inspection_result_id", "source_file_id"],
            [
                "inspection_results.project_key",
                "inspection_results.id",
                "inspection_results.source_file_id",
            ],
            ondelete="RESTRICT",
            name="fk_measurements_project_result_source",
        ),
        ForeignKeyConstraint(
            ["project_key", "source_sheet_id", "source_file_id"],
            ["source_sheets.project_key", "source_sheets.id", "source_sheets.source_file_id"],
            ondelete="RESTRICT",
            name="fk_measurements_project_sheet_source",
        ),
        ForeignKeyConstraint(
            ["project_key", "superseded_measurement_id"],
            ["measurements.project_key", "measurements.id"],
            ondelete="RESTRICT",
            name="fk_measurements_project_superseded_measurement",
        ),
        UniqueConstraint(
            "project_key",
            "id",
            name="uq_measurements_project_id",
        ),
        UniqueConstraint(
            "project_key",
            "id",
            "inspection_result_id",
            "source_file_id",
            name="uq_measurements_project_id_result_source",
        ),
        CheckConstraint("sample_ordinal >= 1", name="measurement_sample_positive"),
        CheckConstraint(
            "data_status IN ('PENDING', 'HELD', 'VALID', 'SUSPECT', 'EXCLUDED', 'REPLACED')",
            name="measurement_data_status",
        ),
        CheckConstraint(
            "standardized_value IS NULL AND unit_conversion_status = 'NOT_CONFIGURED'",
            name="measurement_no_standardized_value",
        ),
        CheckConstraint(
            "length(evidence_sha256) = 64",
            name="measurement_evidence_digest_length",
        ),
        CheckConstraint("row_version >= 1", name="measurement_row_version"),
        CheckConstraint(
            "(data_status = 'REPLACED' AND replacement_transition_id IS NOT NULL) OR "
            "(data_status != 'REPLACED' AND replacement_transition_id IS NULL)",
            name="measurement_replacement_pointer_shape",
        ),
        Index(
            "ix_measurements_project_result",
            "project_key",
            "inspection_result_id",
        ),
        Index(
            "ix_measurements_source_cell",
            "project_key",
            "source_sheet_id",
            "source_cell",
        ),
        Index("ix_measurements_project_status", "project_key", "data_status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    project_key: Mapped[str] = mapped_column(String(200), nullable=False)
    inspection_result_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_file_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_sheet_id: Mapped[str] = mapped_column(String(36), nullable=False)
    sample_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    source_cell: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_value_tag: Mapped[str] = mapped_column(String(32), nullable=False)
    raw_value_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_numeric_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_qualitative_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    formula_flag: Mapped[bool] = mapped_column(Boolean, nullable=False)
    standardized_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit_conversion_status: Mapped[str] = mapped_column(String(32), nullable=False)
    data_status: Mapped[str] = mapped_column(String(16), nullable=False)
    hold_reasons: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    superseded_measurement_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    replacement_transition_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


@dataclass(frozen=True, slots=True)
class LongClaimBasis:
    receipt: SourceFileReceipt
    scan: WorkbookScan
    scan_contract_version: str
    mapping_template_revision_id: str
    mapping_payload_sha256: str
    binding_catalog_revision: str
    binding_fingerprint: str
    loader_version: str
    idempotency_key: str
    materialization_fingerprint: str
    issues: tuple[LongCandidateIssue, ...]
    candidate_snapshot: dict[str, object]
    candidate_snapshot_sha256: str
    held_without_materialization: bool
    claimed_at: datetime


@dataclass(frozen=True, slots=True)
class LongClaimResult:
    project_key: str
    source_file_id: str
    ingestion_job_id: str
    status: LongJobStatus
    row_version: int
    reused_job_id: str | None
    blocking_job_id: str | None
    replayed: bool


@dataclass(frozen=True, slots=True)
class LongMaterializationCounts:
    lot_count: int
    result_count: int
    measurement_count: int
    held_result_count: int


class LongPersistenceError(RuntimeError):
    """Base class for fail-closed pending Long persistence errors."""


class LongPersistenceIntegrityError(LongPersistenceError):
    """Immutable receipt, scan, evidence, or persisted metadata disagrees."""


class StaleLongJobWriteError(LongPersistenceError):
    """The ingestion job row_version changed before a requested transition."""


def canonical_json_sha256(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


_APPLIED_MAPPING_PROOF_KEYS = {
    "version",
    "project_key",
    "source_file_id",
    "receipt_id",
    "content_sha256",
    "candidate_snapshot_sha256",
    "mapping_template_revision_id",
    "mapping_payload_sha256",
    "supplier_scope",
    "template_id",
    "template_schema_version",
    "template_revision",
    "template_effective_from",
    "template_effective_to",
}


def build_applied_mapping_proof(
    *,
    project_key: str,
    source_file_id: str,
    receipt_id: str,
    content_sha256: str,
    mapping_template_revision_id: str,
    mapping_payload_sha256: str,
    candidate_snapshot: dict[str, object],
    candidate_snapshot_sha256: str,
) -> dict[str, object]:
    """Build the bounded immutable Mapping projection for historical reads.

    The full candidate is authenticated once at ingestion (and once while an
    older database is upgraded).  Later historical queries can validate this
    small projection without hydrating workbook-scale candidate JSON.
    """

    if canonical_json_sha256(candidate_snapshot) != candidate_snapshot_sha256:
        raise LongPersistenceIntegrityError("candidate snapshot digest does not match")
    provenance_value = candidate_snapshot.get("provenance")
    if not isinstance(provenance_value, dict):
        raise LongPersistenceIntegrityError("candidate provenance must be an object")
    provenance = cast(dict[str, object], provenance_value)
    receipt_value = provenance.get("receipt")
    if not isinstance(receipt_value, dict):
        raise LongPersistenceIntegrityError("candidate receipt must be an object")
    receipt = cast(dict[str, object], receipt_value)

    def required_text(payload: dict[str, object], key: str) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value:
            raise LongPersistenceIntegrityError(f"candidate field {key} must be text")
        return value

    template_revision = provenance.get("template_revision")
    if isinstance(template_revision, bool) or not isinstance(template_revision, int):
        raise LongPersistenceIntegrityError("candidate template revision must be an integer")
    if template_revision < 1:
        raise LongPersistenceIntegrityError("candidate template revision must be positive")
    template_effective_from = required_text(provenance, "template_effective_from")
    template_effective_to_value = provenance.get("template_effective_to")
    if template_effective_to_value is not None and not isinstance(template_effective_to_value, str):
        raise LongPersistenceIntegrityError("candidate template effective-to must be text or null")
    try:
        effective_from = date.fromisoformat(template_effective_from)
        effective_to = (
            date.fromisoformat(template_effective_to_value)
            if isinstance(template_effective_to_value, str)
            else None
        )
    except ValueError as error:
        raise LongPersistenceIntegrityError("candidate Mapping effectivity is invalid") from error
    if effective_to is not None and effective_to < effective_from:
        raise LongPersistenceIntegrityError("candidate Mapping effectivity is reversed")

    if (
        required_text(receipt, "project_key") != project_key
        or required_text(receipt, "receipt_id") != receipt_id
        or required_text(receipt, "content_sha256") != content_sha256
    ):
        raise LongPersistenceIntegrityError("candidate receipt provenance changed")

    proof: dict[str, object] = {
        "version": APPLIED_MAPPING_PROOF_VERSION,
        "project_key": project_key,
        "source_file_id": source_file_id,
        "receipt_id": receipt_id,
        "content_sha256": content_sha256,
        "candidate_snapshot_sha256": candidate_snapshot_sha256,
        "mapping_template_revision_id": mapping_template_revision_id,
        "mapping_payload_sha256": mapping_payload_sha256,
        "supplier_scope": required_text(provenance, "supplier_scope"),
        "template_id": required_text(provenance, "template_id"),
        "template_schema_version": required_text(provenance, "template_schema_version"),
        "template_revision": template_revision,
        "template_effective_from": template_effective_from,
        "template_effective_to": template_effective_to_value,
    }
    verify_applied_mapping_proof(
        proof,
        canonical_json_sha256(proof),
        project_key=project_key,
        source_file_id=source_file_id,
        receipt_id=receipt_id,
        content_sha256=content_sha256,
        candidate_snapshot_sha256=candidate_snapshot_sha256,
        mapping_template_revision_id=mapping_template_revision_id,
        mapping_payload_sha256=mapping_payload_sha256,
    )
    return proof


def verify_applied_mapping_proof(
    proof: object,
    proof_sha256: str,
    *,
    project_key: str,
    source_file_id: str,
    receipt_id: str,
    content_sha256: str,
    candidate_snapshot_sha256: str,
    mapping_template_revision_id: str,
    mapping_payload_sha256: str,
) -> dict[str, object]:
    """Validate a bounded applied-Mapping proof against its immutable job keys."""

    if not isinstance(proof, dict) or set(proof) != _APPLIED_MAPPING_PROOF_KEYS:
        raise LongPersistenceIntegrityError("applied Mapping proof shape changed")
    typed = cast(dict[str, object], proof)
    if len(proof_sha256) != 64 or canonical_json_sha256(typed) != proof_sha256:
        raise LongPersistenceIntegrityError("applied Mapping proof digest changed")
    expected = {
        "version": APPLIED_MAPPING_PROOF_VERSION,
        "project_key": project_key,
        "source_file_id": source_file_id,
        "receipt_id": receipt_id,
        "content_sha256": content_sha256,
        "candidate_snapshot_sha256": candidate_snapshot_sha256,
        "mapping_template_revision_id": mapping_template_revision_id,
        "mapping_payload_sha256": mapping_payload_sha256,
    }
    if any(typed.get(key) != value for key, value in expected.items()):
        raise LongPersistenceIntegrityError("applied Mapping proof identity changed")
    for key in (
        "supplier_scope",
        "template_id",
        "template_schema_version",
        "template_effective_from",
    ):
        value = typed.get(key)
        if not isinstance(value, str) or not value:
            raise LongPersistenceIntegrityError(f"applied Mapping proof {key} changed")
    revision = typed.get("template_revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
        raise LongPersistenceIntegrityError("applied Mapping proof revision changed")
    effective_to = typed.get("template_effective_to")
    if effective_to is not None and not isinstance(effective_to, str):
        raise LongPersistenceIntegrityError("applied Mapping proof effective-to changed")
    try:
        effective_from_date = date.fromisoformat(cast(str, typed["template_effective_from"]))
        effective_to_date = (
            date.fromisoformat(effective_to) if isinstance(effective_to, str) else None
        )
    except ValueError as error:
        raise LongPersistenceIntegrityError("applied Mapping proof effectivity changed") from error
    if effective_to_date is not None and effective_to_date < effective_from_date:
        raise LongPersistenceIntegrityError("applied Mapping proof period changed")
    return typed


def tagged_value(value: object) -> dict[str, object]:
    if value is None:
        return {"kind": "none", "value": None}
    if isinstance(value, bool):
        return {"kind": "bool", "value": value}
    if isinstance(value, int):
        return {"kind": "int", "value": str(value)}
    if isinstance(value, float):
        return {"kind": "float", "value": value.hex()}
    if isinstance(value, Decimal):
        return {"kind": "decimal", "value": str(value)}
    if isinstance(value, datetime):
        return {"kind": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"kind": "date", "value": value.isoformat()}
    if isinstance(value, time):
        return {"kind": "time", "value": value.isoformat()}
    if isinstance(value, timedelta):
        return {
            "kind": "timedelta",
            "value": {
                "days": value.days,
                "seconds": value.seconds,
                "microseconds": value.microseconds,
            },
        }
    if isinstance(value, str):
        return {"kind": "str", "value": value}
    if isinstance(value, bytes):
        return {"kind": "bytes", "value": base64.b64encode(value).decode("ascii")}
    raise LongPersistenceIntegrityError(
        f"unsupported exact source evidence type: {type(value).__qualname__}"
    )


def untagged_value(tagged: dict[str, object]) -> object:
    """Decode one exact JSON-safe value, rejecting malformed or unknown tags."""

    kind = tagged.get("kind")
    value = tagged.get("value")
    if kind == "none" and value is None:
        return None
    if kind == "bool" and isinstance(value, bool):
        return value
    if kind == "int" and isinstance(value, str):
        return int(value)
    if kind == "float" and isinstance(value, str):
        return float.fromhex(value)
    if kind == "decimal" and isinstance(value, str):
        return Decimal(value)
    if kind == "datetime" and isinstance(value, str):
        return datetime.fromisoformat(value)
    if kind == "date" and isinstance(value, str):
        return date.fromisoformat(value)
    if kind == "time" and isinstance(value, str):
        return time.fromisoformat(value)
    if kind == "timedelta" and isinstance(value, dict):
        days = value.get("days")
        seconds = value.get("seconds")
        microseconds = value.get("microseconds")
        parts = (days, seconds, microseconds)
        if all(isinstance(part, int) and not isinstance(part, bool) for part in parts):
            return timedelta(
                days=cast(int, days),
                seconds=cast(int, seconds),
                microseconds=cast(int, microseconds),
            )
    if kind == "str" and isinstance(value, str):
        return value
    if kind == "bytes" and isinstance(value, str):
        try:
            return base64.b64decode(value, validate=True)
        except ValueError as error:
            raise LongPersistenceIntegrityError("invalid base64 source evidence") from error
    raise LongPersistenceIntegrityError("malformed or unsupported tagged source evidence")


def serialize_cell_evidence(evidence: MappedCellEvidence) -> dict[str, object]:
    return {
        "sheet_name": evidence.source.sheet_name,
        "coordinate": evidence.source.coordinate,
        "raw_value": tagged_value(evidence.raw_value),
        "cached_value": tagged_value(evidence.cached_value),
        "formula_text": evidence.formula_text,
        "number_format": evidence.number_format,
        "data_type": evidence.data_type,
        "display_value": evidence.display_value,
        "display_value_status": evidence.display_value_status.value,
        "value_kind": evidence.value_kind.value,
    }


def deserialize_cell_evidence(payload: dict[str, object]) -> MappedCellEvidence:
    """Rebuild mapped cell evidence without coercing its source value."""

    raw_value = _tagged_payload(payload, "raw_value")
    cached_value = _tagged_payload(payload, "cached_value")
    return MappedCellEvidence(
        source=CellAddress(
            sheet_name=_string(payload, "sheet_name"),
            coordinate=_string(payload, "coordinate"),
        ),
        raw_value=untagged_value(raw_value),
        cached_value=untagged_value(cached_value),
        formula_text=_optional_string(payload, "formula_text"),
        number_format=_string(payload, "number_format"),
        data_type=_string(payload, "data_type"),
        display_value=_optional_string(payload, "display_value"),
        display_value_status=DisplayValueStatus(_string(payload, "display_value_status")),
        value_kind=PreviewValueKind(_string(payload, "value_kind")),
    )


def serialize_candidate_issue(issue: LongCandidateIssue) -> dict[str, object]:
    return {
        "code": issue.code.value,
        "scope": issue.scope.value,
        "message": issue.message,
        "row_key": issue.row_key,
        "sheet_name": issue.sheet_name,
        "coordinate": issue.coordinate,
        "expected": issue.expected,
        "observed": issue.observed,
    }


def serialize_long_candidate(candidate: LongCandidateResult) -> dict[str, object]:
    """Serialize every candidate and provenance field with exact value tags."""

    provenance = candidate.provenance
    return {
        "state": candidate.state.value,
        "provenance": {
            "receipt": _serialize_receipt(provenance.receipt),
            "preview_source_name": provenance.preview_source_name,
            "preview_source_size_bytes": provenance.preview_source_size_bytes,
            "preview_sha256_before": provenance.preview_sha256_before,
            "preview_sha256_after": provenance.preview_sha256_after,
            "source_issues": [_serialize_scan_issue(issue) for issue in provenance.source_issues],
            "is_golden_workbook_evidence": provenance.is_golden_workbook_evidence,
            "supplier_scope": provenance.supplier_scope,
            "template_id": provenance.template_id,
            "template_schema_version": provenance.template_schema_version,
            "template_revision": provenance.template_revision,
            "template_approved_by": provenance.template_approved_by,
            "template_approved_at": provenance.template_approved_at.isoformat(),
            "template_effective_from": provenance.template_effective_from.isoformat(),
            "template_effective_to": (
                provenance.template_effective_to.isoformat()
                if provenance.template_effective_to is not None
                else None
            ),
            "source_inspection_date": provenance.source_inspection_date.isoformat(),
            "binding_catalog_revision": provenance.binding_catalog_revision,
            "binding_selections": [
                _serialize_binding_selection(selection)
                for selection in provenance.binding_selections
            ],
        },
        "source_identifiers": [
            _serialize_identifier(identifier) for identifier in candidate.source_identifiers
        ],
        "rows": [
            _serialize_inspection_candidate(
                row,
                schema_version=provenance.template_schema_version,
            )
            for row in candidate.rows
        ],
        "issues": [serialize_candidate_issue(issue) for issue in candidate.issues],
        "official_values_created": candidate.official_values_created,
        "calculations_performed": candidate.calculations_performed,
    }


def serialize_binding(binding: CanonicalRowBinding) -> dict[str, object]:
    return _serialize_binding_signature(binding.signature)


def _serialize_receipt(receipt: SourceFileReceipt) -> dict[str, object]:
    return {
        "receipt_id": receipt.receipt_id,
        "project_key": receipt.project_key,
        "blob_id": receipt.blob_id,
        "content_sha256": receipt.content_sha256,
        "received_at": receipt.received_at.isoformat(),
        "original_filename": receipt.original_filename,
        "model_candidates": list(receipt.model_candidates),
        "lot_candidates": list(receipt.lot_candidates),
        "declared_mime_type": receipt.declared_mime_type,
        "detected_mime_type": receipt.detected_mime_type,
        "canonical_extension": receipt.canonical_extension,
        "size_bytes": receipt.size_bytes,
    }


def _serialize_binding_key(key: CanonicalRowBindingKey) -> dict[str, object]:
    return {
        "project_key": key.project_key,
        "supplier_scope": key.supplier_scope,
        "template_id": key.template_id,
        "template_revision": key.template_revision,
        "row_key": key.row_key,
    }


def _serialize_binding_signature(
    signature: CanonicalRowBindingSignature,
) -> dict[str, object]:
    return {
        "key": _serialize_binding_key(signature.key),
        "binding_revision": signature.binding_revision,
        "status": signature.status.value,
        "approved_by": signature.approved_by,
        "approved_at": (
            signature.approved_at.isoformat() if signature.approved_at is not None else None
        ),
        "effective_from": signature.effective_from.isoformat(),
        "effective_to": (
            signature.effective_to.isoformat() if signature.effective_to is not None else None
        ),
        "source_model_values": list(signature.source_model_values),
        "canonical_model_key": signature.canonical_model_key,
        "canonical_supplier_key": signature.canonical_supplier_key,
        "canonical_model_part_key": signature.canonical_model_part_key,
        "canonical_item_key": signature.canonical_item_key,
        "sample_policy": signature.sample_policy.value,
        "measurement_mode": signature.measurement_mode.value,
    }


def _serialize_binding_selection(
    selection: CanonicalRowBindingSelectionSignature,
) -> dict[str, object]:
    return {
        "requested_key": _serialize_binding_key(selection.requested_key),
        "matches": [_serialize_binding_signature(match) for match in selection.matches],
    }


def _serialize_identifier(identifier: IdentifierPreview) -> dict[str, object]:
    return {
        "kind": identifier.kind.value,
        "evidence": serialize_cell_evidence(identifier.evidence),
    }


def _serialize_optional_evidence(
    evidence: MappedCellEvidence | None,
) -> dict[str, object] | None:
    return None if evidence is None else serialize_cell_evidence(evidence)


def _serialize_measurement_candidate(
    measurement: LongMeasurementCandidate,
) -> dict[str, object]:
    return {
        "sample_ordinal": measurement.sample_ordinal,
        "evidence": serialize_cell_evidence(measurement.evidence),
        "raw_numeric_value": tagged_value(measurement.raw_numeric_value),
        "raw_qualitative_value": measurement.raw_qualitative_value,
        "standardized_value": measurement.standardized_value,
        "unit_conversion_status": measurement.unit_conversion_status.value,
    }


def _serialize_inspection_candidate(
    row: LongInspectionCandidate,
    *,
    schema_version: str,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "row_key": row.row_key,
        "state": row.state.value,
        "binding": serialize_binding(row.binding) if row.binding is not None else None,
        "item": serialize_cell_evidence(row.item),
        "method": _serialize_optional_evidence(row.method),
        "instrument": _serialize_optional_evidence(row.instrument),
        "specification": _serialize_optional_evidence(row.specification),
        "tolerance": _serialize_optional_evidence(row.tolerance),
        "minimum": _serialize_optional_evidence(row.minimum),
        "maximum": _serialize_optional_evidence(row.maximum),
        "measurements": [
            _serialize_measurement_candidate(measurement) for measurement in row.measurements
        ],
        "supplier_judgment": _serialize_optional_evidence(row.supplier_judgment),
        "issues": [serialize_candidate_issue(issue) for issue in row.issues],
        "data_status": row.data_status.value,
        "system_judgment_status": row.system_judgment_status.value,
        "system_judgment": row.system_judgment,
        "spec_evaluation_status": row.spec_evaluation_status.value,
    }
    if schema_version == "2":
        payload.update(_v2_row_evidence(row))
    return payload


def _v2_row_evidence(row: LongInspectionCandidate) -> dict[str, object]:
    return {
        "section": _serialize_optional_evidence(row.section),
        "category": _serialize_optional_evidence(row.category),
        "unit": _serialize_optional_evidence(row.unit),
        "measurement_point": _serialize_optional_evidence(row.measurement_point),
        "measurement_location": _serialize_optional_evidence(row.measurement_location),
        "cavity": _serialize_optional_evidence(row.cavity),
        "target": _serialize_optional_evidence(row.target),
        "lsl": _serialize_optional_evidence(row.lsl),
        "usl": _serialize_optional_evidence(row.usl),
        "source_spec_revision": _serialize_optional_evidence(row.source_spec_revision),
    }


def _serialize_scan_issue(issue: ScanIssue) -> dict[str, object]:
    return {
        "code": issue.code,
        "severity": issue.severity.value,
        "message": issue.message,
        "location": {
            "kind": issue.location.kind.value,
            "sheet_name": issue.location.sheet_name,
            "coordinate": issue.location.coordinate,
            "package_part": issue.location.package_part,
        },
    }


def _serialize_scan_cell(cell: CellEvidence) -> dict[str, object]:
    return {
        "coordinate": cell.coordinate,
        "stored_value": tagged_value(cell.stored_value),
        "cached_value": tagged_value(cell.cached_value),
        "formula_text": cell.formula_text,
        "number_format": cell.number_format,
        "data_type": cell.data_type,
        "display_value": cell.display_value,
        "display_value_status": cell.display_value_status.value,
    }


def serialize_sheet(sheet: SheetScan) -> dict[str, object]:
    return {
        "position": sheet.position,
        "sheet_name": sheet.name,
        "sheet_kind": sheet.kind.value,
        "visibility": sheet.visibility,
        "used_range": sheet.used_range,
        "estimated_cells": sheet.estimated_cells,
        "merged_ranges": list(sheet.merged_ranges),
        "hidden_row_ranges": [
            {"start": value.start, "end": value.end} for value in sheet.hidden_row_ranges
        ],
        "hidden_column_ranges": [
            {"start": value.start, "end": value.end} for value in sheet.hidden_column_ranges
        ],
        "cells": [_serialize_scan_cell(cell) for cell in sheet.cells],
        "row_candidates": [
            {
                "row_index": candidate.row_index,
                "kind": candidate.kind.value,
                "reason": candidate.reason,
                "signature": list(candidate.signature),
            }
            for candidate in sheet.row_candidates
        ],
        "formula_count": len(sheet.formula_cells),
        "protection_metadata": {
            "enabled": sheet.protection.enabled,
            "protected_actions": list(sheet.protection.protected_actions),
            "password_material_collected": sheet.protection.password_material_collected,
            "bypass_attempted": sheet.protection.bypass_attempted,
        },
        "image_metadata": [
            {
                "anchor_from": image.anchor_from,
                "anchor_to": image.anchor_to,
                "width_px": image.width_px,
                "height_px": image.height_px,
                "image_format": image.image_format,
                "content_collected": image.content_collected,
                "analysis_performed": image.analysis_performed,
            }
            for image in sheet.images
        ],
        "issues": [_serialize_scan_issue(issue) for issue in sheet.issues],
    }


def serialize_workbook_scan(scan: WorkbookScan) -> dict[str, object]:
    """Serialize the complete one-time scan for a durable prepared checkpoint."""

    return {
        "state": scan.state.value,
        "source_name": scan.source_name,
        "source_size_bytes": scan.source_size_bytes,
        "source_sha256_before": scan.source_sha256_before,
        "source_sha256_after": scan.source_sha256_after,
        "sheets": [serialize_sheet(sheet) for sheet in scan.sheets],
        "issues": [_serialize_scan_issue(issue) for issue in scan.issues],
        "estimated_cells": scan.estimated_cells,
        "external_link_count": scan.external_link_count,
        "macro_handling": scan.macro_handling.value,
        "display_value_contract": scan.display_value_contract.value,
        "is_golden_workbook_evidence": scan.is_golden_workbook_evidence,
    }


def deserialize_workbook_scan(payload: dict[str, object]) -> WorkbookScan:
    """Strictly rebuild a scan checkpoint without reopening the workbook."""

    _checkpoint_keys(
        payload,
        {
            "state",
            "source_name",
            "source_size_bytes",
            "source_sha256_before",
            "source_sha256_after",
            "sheets",
            "issues",
            "estimated_cells",
            "external_link_count",
            "macro_handling",
            "display_value_contract",
            "is_golden_workbook_evidence",
        },
        "workbook scan",
    )
    sheets = tuple(_deserialize_sheet(item) for item in _checkpoint_object_list(payload, "sheets"))
    scan = WorkbookScan(
        state=WorkbookScanState(_string(payload, "state")),
        source_name=_string(payload, "source_name"),
        source_size_bytes=_checkpoint_int(payload, "source_size_bytes", minimum=0),
        source_sha256_before=_string(payload, "source_sha256_before"),
        source_sha256_after=_string(payload, "source_sha256_after"),
        sheets=sheets,
        issues=tuple(
            _deserialize_scan_issue(item) for item in _checkpoint_object_list(payload, "issues")
        ),
        estimated_cells=_checkpoint_int(payload, "estimated_cells", minimum=0),
        external_link_count=_checkpoint_int(payload, "external_link_count", minimum=0),
        macro_handling=MacroHandling(_string(payload, "macro_handling")),
        display_value_contract=DisplayValueStatus(_string(payload, "display_value_contract")),
        is_golden_workbook_evidence=_checkpoint_bool(payload, "is_golden_workbook_evidence"),
    )
    if scan.estimated_cells != sum(sheet.estimated_cells for sheet in scan.sheets):
        raise LongPersistenceIntegrityError("scan estimated cell total changed")
    if tuple(sheet.position for sheet in scan.sheets) != tuple(range(len(scan.sheets))):
        raise LongPersistenceIntegrityError("scan sheet positions are not deterministic")
    return scan


def _deserialize_sheet(payload: dict[str, object]) -> SheetScan:
    _checkpoint_keys(
        payload,
        {
            "position",
            "sheet_name",
            "sheet_kind",
            "visibility",
            "used_range",
            "estimated_cells",
            "merged_ranges",
            "hidden_row_ranges",
            "hidden_column_ranges",
            "cells",
            "row_candidates",
            "formula_count",
            "protection_metadata",
            "image_metadata",
            "issues",
        },
        "sheet scan",
    )
    protection = _checkpoint_object(payload, "protection_metadata")
    _checkpoint_keys(
        protection,
        {"enabled", "protected_actions", "password_material_collected", "bypass_attempted"},
        "sheet protection",
    )
    sheet = SheetScan(
        name=_string(payload, "sheet_name"),
        kind=SheetKind(_string(payload, "sheet_kind")),
        position=_checkpoint_int(payload, "position", minimum=0),
        visibility=_string(payload, "visibility"),
        used_range=_optional_string(payload, "used_range"),
        estimated_cells=_checkpoint_int(payload, "estimated_cells", minimum=0),
        merged_ranges=_checkpoint_string_tuple(payload, "merged_ranges"),
        hidden_row_ranges=tuple(
            _deserialize_index_range(item)
            for item in _checkpoint_object_list(payload, "hidden_row_ranges")
        ),
        hidden_column_ranges=tuple(
            _deserialize_index_range(item)
            for item in _checkpoint_object_list(payload, "hidden_column_ranges")
        ),
        cells=tuple(
            _deserialize_scan_cell(item) for item in _checkpoint_object_list(payload, "cells")
        ),
        row_candidates=tuple(
            _deserialize_row_candidate(item)
            for item in _checkpoint_object_list(payload, "row_candidates")
        ),
        protection=SheetProtectionMetadata(
            enabled=_checkpoint_bool(protection, "enabled"),
            protected_actions=_checkpoint_string_tuple(protection, "protected_actions"),
            password_material_collected=_checkpoint_bool(protection, "password_material_collected"),
            bypass_attempted=_checkpoint_bool(protection, "bypass_attempted"),
        ),
        images=tuple(
            _deserialize_image(item) for item in _checkpoint_object_list(payload, "image_metadata")
        ),
        issues=tuple(
            _deserialize_scan_issue(item) for item in _checkpoint_object_list(payload, "issues")
        ),
    )
    if _checkpoint_int(payload, "formula_count", minimum=0) != len(sheet.formula_cells):
        raise LongPersistenceIntegrityError("sheet formula count changed")
    return sheet


def _deserialize_scan_cell(payload: dict[str, object]) -> CellEvidence:
    _checkpoint_keys(
        payload,
        {
            "coordinate",
            "stored_value",
            "cached_value",
            "formula_text",
            "number_format",
            "data_type",
            "display_value",
            "display_value_status",
        },
        "scan cell",
    )
    stored = _tagged_payload(payload, "stored_value")
    cached = _tagged_payload(payload, "cached_value")
    _checkpoint_keys(stored, {"kind", "value"}, "stored value tag")
    _checkpoint_keys(cached, {"kind", "value"}, "cached value tag")
    return CellEvidence(
        coordinate=_string(payload, "coordinate"),
        stored_value=untagged_value(stored),
        cached_value=untagged_value(cached),
        formula_text=_optional_string(payload, "formula_text"),
        number_format=_string(payload, "number_format"),
        data_type=_string(payload, "data_type"),
        display_value=_optional_string(payload, "display_value"),
        display_value_status=DisplayValueStatus(_string(payload, "display_value_status")),
    )


def _deserialize_scan_issue(payload: dict[str, object]) -> ScanIssue:
    _checkpoint_keys(payload, {"code", "severity", "message", "location"}, "scan issue")
    location = _checkpoint_object(payload, "location")
    _checkpoint_keys(
        location,
        {"kind", "sheet_name", "coordinate", "package_part"},
        "scan issue location",
    )
    return ScanIssue(
        code=_string(payload, "code"),
        severity=IssueSeverity(_string(payload, "severity")),
        message=_string(payload, "message"),
        location=SourceLocation(
            kind=SourceLocationKind(_string(location, "kind")),
            sheet_name=_optional_string(location, "sheet_name"),
            coordinate=_optional_string(location, "coordinate"),
            package_part=_optional_string(location, "package_part"),
        ),
    )


def _deserialize_index_range(payload: dict[str, object]) -> IndexRange:
    _checkpoint_keys(payload, {"start", "end"}, "index range")
    return IndexRange(
        start=_checkpoint_int(payload, "start", minimum=1),
        end=_checkpoint_int(payload, "end", minimum=1),
    )


def _deserialize_row_candidate(payload: dict[str, object]) -> RowCandidate:
    _checkpoint_keys(payload, {"row_index", "kind", "reason", "signature"}, "row candidate")
    return RowCandidate(
        row_index=_checkpoint_int(payload, "row_index", minimum=1),
        kind=RowCandidateKind(_string(payload, "kind")),
        reason=_string(payload, "reason"),
        signature=_checkpoint_string_tuple(payload, "signature"),
    )


def _deserialize_image(payload: dict[str, object]) -> ImageMetadata:
    _checkpoint_keys(
        payload,
        {
            "anchor_from",
            "anchor_to",
            "width_px",
            "height_px",
            "image_format",
            "content_collected",
            "analysis_performed",
        },
        "image metadata",
    )
    return ImageMetadata(
        anchor_from=_optional_string(payload, "anchor_from"),
        anchor_to=_optional_string(payload, "anchor_to"),
        width_px=_checkpoint_optional_number(payload, "width_px"),
        height_px=_checkpoint_optional_number(payload, "height_px"),
        image_format=_optional_string(payload, "image_format"),
        content_collected=_checkpoint_bool(payload, "content_collected"),
        analysis_performed=_checkpoint_bool(payload, "analysis_performed"),
    )


class LongFormatRepository:
    """Repository whose methods never commit the caller-owned transaction."""

    def claim(self, session: Session, basis: LongClaimBasis) -> LongClaimResult:
        if canonical_json_sha256(basis.candidate_snapshot) != basis.candidate_snapshot_sha256:
            raise LongPersistenceIntegrityError("candidate snapshot digest does not match")
        source = self._get_or_create_source(session, basis)
        applied_mapping_proof = build_applied_mapping_proof(
            project_key=basis.receipt.project_key,
            source_file_id=source.id,
            receipt_id=basis.receipt.receipt_id,
            content_sha256=basis.receipt.content_sha256,
            mapping_template_revision_id=basis.mapping_template_revision_id,
            mapping_payload_sha256=basis.mapping_payload_sha256,
            candidate_snapshot=basis.candidate_snapshot,
            candidate_snapshot_sha256=basis.candidate_snapshot_sha256,
        )
        applied_mapping_proof_sha256 = canonical_json_sha256(applied_mapping_proof)
        exact = session.scalar(
            select(LongIngestionJobRow).where(
                LongIngestionJobRow.project_key == basis.receipt.project_key,
                LongIngestionJobRow.source_file_id == source.id,
                LongIngestionJobRow.mapping_template_revision_id
                == basis.mapping_template_revision_id,
                LongIngestionJobRow.binding_fingerprint == basis.binding_fingerprint,
                LongIngestionJobRow.loader_version == basis.loader_version,
                LongIngestionJobRow.scan_contract_version == basis.scan_contract_version,
            )
        )
        if exact is not None:
            if _stored_job_basis(exact) != _requested_job_basis(basis):
                raise LongPersistenceIntegrityError(
                    "an exact replay disagrees with the immutable ingestion basis"
                )
            return _claim_result(exact, replayed=True)

        owner = None
        if not basis.held_without_materialization:
            owner = session.scalar(
                select(LongIngestionJobRow).where(
                    LongIngestionJobRow.project_key == basis.receipt.project_key,
                    LongIngestionJobRow.content_sha256 == basis.receipt.content_sha256,
                    LongIngestionJobRow.mapping_template_revision_id
                    == basis.mapping_template_revision_id,
                    LongIngestionJobRow.binding_fingerprint == basis.binding_fingerprint,
                    LongIngestionJobRow.loader_version == basis.loader_version,
                    LongIngestionJobRow.scan_contract_version == basis.scan_contract_version,
                    LongIngestionJobRow.owns_materialization.is_(True),
                )
            )

        blocking_job_id = None
        if owner is not None and owner.status in {
            LongJobStatus.COMPLETED_PENDING.value,
            LongJobStatus.PARTIAL_HELD.value,
        }:
            status = LongJobStatus.REUSED
            reused_job_id = owner.id
            owns_materialization = False
            fingerprint = None
            finished_at = basis.claimed_at
            counts = LongMaterializationCounts(
                lot_count=owner.lot_count,
                result_count=owner.result_count,
                measurement_count=owner.measurement_count,
                held_result_count=owner.held_result_count,
            )
        elif owner is not None and owner.status in {
            LongJobStatus.PROCESSING.value,
            LongJobStatus.FAILED.value,
        }:
            status = LongJobStatus.RECOVERY_REQUIRED
            reused_job_id = None
            blocking_job_id = owner.id
            owns_materialization = False
            fingerprint = None
            finished_at = basis.claimed_at
            counts = LongMaterializationCounts(0, 0, 0, 0)
        elif owner is not None:
            raise LongPersistenceIntegrityError(
                "materialization owner has an impossible non-reusable status"
            )
        elif basis.held_without_materialization:
            status = LongJobStatus.HELD
            reused_job_id = None
            owns_materialization = False
            fingerprint = None
            finished_at = basis.claimed_at
            counts = LongMaterializationCounts(0, 0, 0, 0)
        else:
            status = LongJobStatus.PROCESSING
            reused_job_id = None
            owns_materialization = True
            fingerprint = basis.materialization_fingerprint
            finished_at = None
            counts = LongMaterializationCounts(0, 0, 0, 0)

        job = LongIngestionJobRow(
            project_key=basis.receipt.project_key,
            source_file_id=source.id,
            content_sha256=basis.receipt.content_sha256,
            mapping_template_revision_id=basis.mapping_template_revision_id,
            mapping_payload_sha256=basis.mapping_payload_sha256,
            binding_catalog_revision=basis.binding_catalog_revision,
            binding_fingerprint=basis.binding_fingerprint,
            loader_version=basis.loader_version,
            scan_contract_version=basis.scan_contract_version,
            idempotency_key=basis.idempotency_key,
            materialization_fingerprint=fingerprint,
            owns_materialization=owns_materialization,
            reused_job_id=reused_job_id,
            blocking_job_id=blocking_job_id,
            status=status.value,
            started_at=basis.claimed_at,
            finished_at=finished_at,
            lot_count=counts.lot_count,
            result_count=counts.result_count,
            measurement_count=counts.measurement_count,
            held_result_count=counts.held_result_count,
            error_code=None,
            error_summary=None,
            issues=[serialize_candidate_issue(issue) for issue in basis.issues],
            candidate_snapshot=basis.candidate_snapshot,
            candidate_snapshot_sha256=basis.candidate_snapshot_sha256,
            applied_mapping_proof=applied_mapping_proof,
            applied_mapping_proof_sha256=applied_mapping_proof_sha256,
            row_version=1,
        )
        session.add(job)
        session.flush()
        return _claim_result(job, replayed=False)

    def materialize(
        self,
        session: Session,
        *,
        claim: LongClaimResult,
        candidate: LongCandidateResult,
    ) -> LongMaterializationCounts:
        """Insert one complete pending/held materialization without committing it."""

        if candidate.state == LongCandidateState.LOAD_HELD:
            raise LongPersistenceIntegrityError(
                "a globally held candidate must remain in its immutable job snapshot"
            )
        job = self.get_job(
            session,
            project_key=candidate.provenance.receipt.project_key,
            job_id=claim.ingestion_job_id,
        )
        if (
            job.status != LongJobStatus.PROCESSING.value
            or not job.owns_materialization
            or job.source_file_id != claim.source_file_id
        ):
            raise LongPersistenceIntegrityError("only a PROCESSING owner job can materialize")
        snapshot = serialize_long_candidate(candidate)
        snapshot_sha256 = canonical_json_sha256(snapshot)
        if snapshot != job.candidate_snapshot or snapshot_sha256 != job.candidate_snapshot_sha256:
            raise LongPersistenceIntegrityError(
                "materialization candidate differs from the claimed immutable snapshot"
            )
        existing_lot = session.scalar(
            select(OqcLotRow.id).where(
                OqcLotRow.project_key == job.project_key,
                OqcLotRow.ingestion_job_id == job.id,
            )
        )
        if existing_lot is not None:
            raise LongPersistenceIntegrityError(
                "a PROCESSING job unexpectedly already owns Long-format rows"
            )

        sheets = {
            row.sheet_name: row
            for row in session.scalars(
                select(LongSourceSheetRow).where(
                    LongSourceSheetRow.project_key == job.project_key,
                    LongSourceSheetRow.source_file_id == job.source_file_id,
                )
            ).all()
        }
        identifier_evidence = [
            _serialize_identifier(identifier) for identifier in candidate.source_identifiers
        ]
        source_lot = candidate.source_lot
        source_lot_text = (
            source_lot.raw_value
            if source_lot is not None and isinstance(source_lot.raw_value, str)
            else None
        )
        result_count = 0
        measurement_count = 0
        held_result_count = 0
        groups = _candidate_row_groups(candidate)
        for lot_ordinal, rows in enumerate(groups, start=1):
            first_binding = next(
                (
                    binding
                    for row in rows
                    if (binding := _trusted_binding(row, candidate)) is not None
                ),
                None,
            )
            lot_status = (
                PendingDataStatus.HELD
                if any(row.state == LongRowState.ROW_HELD for row in rows)
                else PendingDataStatus.PENDING
            )
            lot_holds = [
                serialize_candidate_issue(issue)
                for row in rows
                if row.state == LongRowState.ROW_HELD
                for issue in row.issues
            ]
            lot = OqcLotRow(
                project_key=job.project_key,
                ingestion_job_id=job.id,
                source_file_id=job.source_file_id,
                lot_ordinal=lot_ordinal,
                canonical_model_key=(
                    first_binding.canonical_model_key if first_binding is not None else None
                ),
                canonical_model_part_key=(
                    first_binding.canonical_model_part_key if first_binding is not None else None
                ),
                canonical_supplier_key=(
                    first_binding.canonical_supplier_key if first_binding is not None else None
                ),
                source_lot_text=source_lot_text,
                inspection_date=candidate.provenance.source_inspection_date,
                received_at=candidate.provenance.receipt.received_at,
                identifier_evidence=identifier_evidence,
                identifier_evidence_sha256=canonical_json_sha256(identifier_evidence),
                data_status=lot_status.value,
                hold_reasons=lot_holds,
                row_version=1,
            )
            session.add(lot)
            session.flush()
            for row in rows:
                trusted_binding = _trusted_binding(row, candidate)
                source_sheet = _source_sheet(sheets, row.item.source.sheet_name)
                row_status = (
                    PendingDataStatus.HELD
                    if row.state == LongRowState.ROW_HELD
                    else PendingDataStatus.PENDING
                )
                source_evidence = _row_source_evidence(
                    row,
                    schema_version=candidate.provenance.template_schema_version,
                )
                binding_snapshot = (
                    serialize_binding(row.binding) if row.binding is not None else None
                )
                holds = [serialize_candidate_issue(issue) for issue in row.issues]
                result = LongInspectionResultRow(
                    project_key=job.project_key,
                    oqc_lot_id=lot.id,
                    source_file_id=job.source_file_id,
                    source_sheet_id=source_sheet.id,
                    source_row_key=row.row_key,
                    binding_revision=(
                        trusted_binding.binding_revision if trusted_binding is not None else None
                    ),
                    canonical_model_part_key=(
                        trusted_binding.canonical_model_part_key
                        if trusted_binding is not None
                        else None
                    ),
                    canonical_item_key=(
                        trusted_binding.canonical_item_key if trusted_binding is not None else None
                    ),
                    supplier_judgment_text=_source_text(row.supplier_judgment),
                    system_judgment=None,
                    system_judgment_status=row.system_judgment_status.value,
                    spec_evaluation_status=row.spec_evaluation_status.value,
                    source_evidence=source_evidence,
                    source_evidence_sha256=canonical_json_sha256(source_evidence),
                    binding_snapshot=binding_snapshot,
                    binding_snapshot_sha256=(
                        canonical_json_sha256(binding_snapshot)
                        if binding_snapshot is not None
                        else None
                    ),
                    candidate_snapshot_sha256=snapshot_sha256,
                    data_status=row_status.value,
                    hold_reasons=holds,
                    row_version=1,
                )
                session.add(result)
                session.flush()
                result_count += 1
                if row_status == PendingDataStatus.HELD:
                    held_result_count += 1
                for measurement in row.measurements:
                    measurement_sheet = _source_sheet(
                        sheets, measurement.evidence.source.sheet_name
                    )
                    evidence = serialize_cell_evidence(measurement.evidence)
                    raw_tag = tagged_value(measurement.evidence.raw_value)
                    session.add(
                        LongMeasurementRow(
                            project_key=job.project_key,
                            inspection_result_id=result.id,
                            source_file_id=job.source_file_id,
                            source_sheet_id=measurement_sheet.id,
                            sample_ordinal=measurement.sample_ordinal,
                            source_cell=measurement.evidence.source.coordinate,
                            raw_value_tag=cast(str, raw_tag["kind"]),
                            raw_value_text=_canonical_json(raw_tag),
                            raw_numeric_value=(
                                _canonical_json(tagged_value(measurement.raw_numeric_value))
                                if measurement.raw_numeric_value is not None
                                else None
                            ),
                            raw_qualitative_value=measurement.raw_qualitative_value,
                            evidence=evidence,
                            evidence_sha256=canonical_json_sha256(evidence),
                            formula_flag=measurement.evidence.formula_text is not None,
                            standardized_value=None,
                            unit_conversion_status=measurement.unit_conversion_status.value,
                            data_status=row_status.value,
                            hold_reasons=holds,
                            superseded_measurement_id=None,
                            row_version=1,
                        )
                    )
                    measurement_count += 1
        session.flush()
        return LongMaterializationCounts(
            lot_count=len(groups),
            result_count=result_count,
            measurement_count=measurement_count,
            held_result_count=held_result_count,
        )

    def mark_materialized(
        self,
        session: Session,
        *,
        project_key: str,
        job_id: str,
        expected_row_version: int,
        status: LongJobStatus,
        counts: LongMaterializationCounts,
        finished_at: datetime,
    ) -> LongClaimResult:
        if status not in {LongJobStatus.COMPLETED_PENDING, LongJobStatus.PARTIAL_HELD}:
            raise ValueError("materialized status must be pending or partial-held")
        result = cast(
            CursorResult[Any],
            session.execute(
                update(LongIngestionJobRow)
                .where(
                    LongIngestionJobRow.project_key == project_key,
                    LongIngestionJobRow.id == job_id,
                    LongIngestionJobRow.status == LongJobStatus.PROCESSING.value,
                    LongIngestionJobRow.row_version == expected_row_version,
                )
                .values(
                    status=status.value,
                    finished_at=finished_at,
                    lot_count=counts.lot_count,
                    result_count=counts.result_count,
                    measurement_count=counts.measurement_count,
                    held_result_count=counts.held_result_count,
                    row_version=expected_row_version + 1,
                )
                .execution_options(synchronize_session=False)
            ),
        )
        if result.rowcount != 1:
            raise StaleLongJobWriteError("ingestion job status or row_version is stale")
        job = self.get_job(session, project_key=project_key, job_id=job_id)
        session.refresh(job)
        return _claim_result(job, replayed=False)

    def mark_failed(
        self,
        session: Session,
        *,
        project_key: str,
        job_id: str,
        expected_row_version: int,
        finished_at: datetime,
        error_code: str,
        error_summary: str,
    ) -> LongClaimResult:
        result = cast(
            CursorResult[Any],
            session.execute(
                update(LongIngestionJobRow)
                .where(
                    LongIngestionJobRow.project_key == project_key,
                    LongIngestionJobRow.id == job_id,
                    LongIngestionJobRow.status == LongJobStatus.PROCESSING.value,
                    LongIngestionJobRow.row_version == expected_row_version,
                )
                .values(
                    status=LongJobStatus.FAILED.value,
                    finished_at=finished_at,
                    error_code=error_code,
                    error_summary=error_summary,
                    row_version=expected_row_version + 1,
                )
                .execution_options(synchronize_session=False)
            ),
        )
        if result.rowcount != 1:
            raise StaleLongJobWriteError("ingestion job status or row_version is stale")
        job = self.get_job(session, project_key=project_key, job_id=job_id)
        session.refresh(job)
        return _claim_result(job, replayed=False)

    @staticmethod
    def get_job(session: Session, *, project_key: str, job_id: str) -> LongIngestionJobRow:
        job = session.scalar(
            select(LongIngestionJobRow).where(
                LongIngestionJobRow.project_key == project_key,
                LongIngestionJobRow.id == job_id,
            )
        )
        if job is None:
            raise LongPersistenceIntegrityError("ingestion job was not found in its project")
        return job

    def load_candidate_snapshot(
        self,
        session: Session,
        *,
        project_key: str,
        job_id: str,
    ) -> dict[str, object]:
        job = self.get_job(session, project_key=project_key, job_id=job_id)
        if canonical_json_sha256(job.candidate_snapshot) != job.candidate_snapshot_sha256:
            raise LongPersistenceIntegrityError("stored candidate snapshot digest does not match")
        return job.candidate_snapshot

    @staticmethod
    def load_measurement_evidence(
        session: Session,
        *,
        project_key: str,
        job_id: str,
    ) -> tuple[MappedCellEvidence, ...]:
        rows = session.scalars(
            select(LongMeasurementRow)
            .join(
                LongInspectionResultRow,
                (LongInspectionResultRow.project_key == LongMeasurementRow.project_key)
                & (LongInspectionResultRow.id == LongMeasurementRow.inspection_result_id)
                & (LongInspectionResultRow.source_file_id == LongMeasurementRow.source_file_id),
            )
            .join(
                OqcLotRow,
                (OqcLotRow.project_key == LongInspectionResultRow.project_key)
                & (OqcLotRow.id == LongInspectionResultRow.oqc_lot_id)
                & (OqcLotRow.source_file_id == LongInspectionResultRow.source_file_id),
            )
            .where(
                LongMeasurementRow.project_key == project_key,
                OqcLotRow.ingestion_job_id == job_id,
            )
            .order_by(
                OqcLotRow.lot_ordinal,
                LongInspectionResultRow.source_row_key,
                LongMeasurementRow.sample_ordinal,
            )
        ).all()
        evidence: list[MappedCellEvidence] = []
        for row in rows:
            if canonical_json_sha256(row.evidence) != row.evidence_sha256:
                raise LongPersistenceIntegrityError(
                    "stored measurement evidence digest does not match"
                )
            raw_tag = _tagged_payload(row.evidence, "raw_value")
            if row.raw_value_tag != raw_tag["kind"] or row.raw_value_text != _canonical_json(
                raw_tag
            ):
                raise LongPersistenceIntegrityError(
                    "stored measurement projection disagrees with exact evidence"
                )
            evidence.append(deserialize_cell_evidence(row.evidence))
        return tuple(evidence)

    def _get_or_create_source(
        self,
        session: Session,
        basis: LongClaimBasis,
    ) -> LongSourceFileRow:
        receipt = basis.receipt
        existing = session.scalar(
            select(LongSourceFileRow).where(
                LongSourceFileRow.project_key == receipt.project_key,
                LongSourceFileRow.receipt_id == receipt.receipt_id,
            )
        )
        snapshot = _source_snapshot(basis)
        if existing is not None:
            if _stored_source_snapshot(session, existing) != snapshot:
                raise LongPersistenceIntegrityError(
                    "an existing receipt has different immutable source or scan metadata"
                )
            return existing

        source = LongSourceFileRow(
            project_key=receipt.project_key,
            receipt_id=receipt.receipt_id,
            blob_id=receipt.blob_id,
            content_sha256=receipt.content_sha256,
            received_at=receipt.received_at,
            original_filename=receipt.original_filename,
            model_candidates=list(receipt.model_candidates),
            lot_candidates=list(receipt.lot_candidates),
            declared_mime_type=receipt.declared_mime_type,
            detected_mime_type=receipt.detected_mime_type,
            canonical_extension=receipt.canonical_extension,
            size_bytes=receipt.size_bytes,
            parse_status=_parse_status(basis.scan).value,
            scan_source_name=basis.scan.source_name,
            scan_source_size_bytes=basis.scan.source_size_bytes,
            scan_sha256_before=basis.scan.source_sha256_before,
            scan_sha256_after=basis.scan.source_sha256_after,
            scan_contract_version=basis.scan_contract_version,
            estimated_cells=basis.scan.estimated_cells,
            external_link_count=basis.scan.external_link_count,
            macro_handling=basis.scan.macro_handling.value,
            display_value_contract=basis.scan.display_value_contract.value,
            is_golden_workbook_evidence=basis.scan.is_golden_workbook_evidence,
            scan_issues=[_serialize_scan_issue(issue) for issue in basis.scan.issues],
            row_version=1,
            created_at=basis.claimed_at,
        )
        session.add(source)
        session.flush()
        for sheet in basis.scan.sheets:
            payload = serialize_sheet(sheet)
            session.add(
                LongSourceSheetRow(
                    project_key=receipt.project_key,
                    source_file_id=source.id,
                    position=sheet.position,
                    sheet_name=sheet.name,
                    sheet_kind=sheet.kind.value,
                    visibility=sheet.visibility,
                    used_range=sheet.used_range,
                    estimated_cells=sheet.estimated_cells,
                    merged_ranges=list(sheet.merged_ranges),
                    hidden_row_ranges=cast(list[dict[str, int]], payload["hidden_row_ranges"]),
                    hidden_column_ranges=cast(
                        list[dict[str, int]], payload["hidden_column_ranges"]
                    ),
                    formula_count=len(sheet.formula_cells),
                    protection_metadata=cast(dict[str, object], payload["protection_metadata"]),
                    image_metadata=cast(list[dict[str, object]], payload["image_metadata"]),
                    issues=cast(list[dict[str, object]], payload["issues"]),
                    scan_snapshot=payload,
                    snapshot_sha256=canonical_json_sha256(payload),
                    row_version=1,
                )
            )
        session.flush()
        return source


def _parse_status(scan: WorkbookScan) -> SourceParseStatus:
    return SourceParseStatus(scan.state.value)


def _source_snapshot(basis: LongClaimBasis) -> dict[str, object]:
    receipt = basis.receipt
    return {
        "blob_id": receipt.blob_id,
        "content_sha256": receipt.content_sha256,
        "received_at": receipt.received_at.isoformat(),
        "original_filename": receipt.original_filename,
        "model_candidates": list(receipt.model_candidates),
        "lot_candidates": list(receipt.lot_candidates),
        "declared_mime_type": receipt.declared_mime_type,
        "detected_mime_type": receipt.detected_mime_type,
        "canonical_extension": receipt.canonical_extension,
        "size_bytes": receipt.size_bytes,
        "parse_status": _parse_status(basis.scan).value,
        "scan_source_name": basis.scan.source_name,
        "scan_source_size_bytes": basis.scan.source_size_bytes,
        "scan_sha256_before": basis.scan.source_sha256_before,
        "scan_sha256_after": basis.scan.source_sha256_after,
        "scan_contract_version": basis.scan_contract_version,
        "estimated_cells": basis.scan.estimated_cells,
        "external_link_count": basis.scan.external_link_count,
        "macro_handling": basis.scan.macro_handling.value,
        "display_value_contract": basis.scan.display_value_contract.value,
        "is_golden_workbook_evidence": basis.scan.is_golden_workbook_evidence,
        "scan_issues": [_serialize_scan_issue(issue) for issue in basis.scan.issues],
        "sheets": [serialize_sheet(sheet) for sheet in basis.scan.sheets],
    }


def _candidate_row_groups(
    candidate: LongCandidateResult,
) -> tuple[tuple[LongInspectionCandidate, ...], ...]:
    groups: dict[tuple[str | None, str | None, str | None], list[LongInspectionCandidate]] = {}
    for row in candidate.rows:
        binding = _trusted_binding(row, candidate)
        key = (
            binding.canonical_model_key if binding is not None else None,
            binding.canonical_supplier_key if binding is not None else None,
            binding.canonical_model_part_key if binding is not None else None,
        )
        groups.setdefault(key, []).append(row)
    return tuple(tuple(group) for group in groups.values())


def _trusted_binding(
    row: LongInspectionCandidate,
    candidate: LongCandidateResult,
) -> CanonicalRowBinding | None:
    binding = row.binding
    provenance = candidate.provenance
    source_model = candidate.source_model
    if binding is None or binding.status != CanonicalRowBindingStatus.APPROVED:
        return None
    if not (
        binding.effective_from
        <= provenance.source_inspection_date
        <= (binding.effective_to or date.max)
    ):
        return None
    if (
        source_model is None
        or not isinstance(source_model.raw_value, str)
        or source_model.raw_value not in binding.source_model_values
    ):
        return None
    expected_key = CanonicalRowBindingKey(
        project_key=provenance.receipt.project_key,
        supplier_scope=provenance.supplier_scope,
        template_id=provenance.template_id,
        template_revision=provenance.template_revision,
        row_key=row.row_key,
    )
    return binding if binding.key == expected_key else None


def _row_source_evidence(
    row: LongInspectionCandidate,
    *,
    schema_version: str,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "item": serialize_cell_evidence(row.item),
        "method": _serialize_optional_evidence(row.method),
        "instrument": _serialize_optional_evidence(row.instrument),
        "specification": _serialize_optional_evidence(row.specification),
        "tolerance": _serialize_optional_evidence(row.tolerance),
        "minimum": _serialize_optional_evidence(row.minimum),
        "maximum": _serialize_optional_evidence(row.maximum),
        "supplier_judgment": _serialize_optional_evidence(row.supplier_judgment),
    }
    if schema_version == "2":
        payload.update(_v2_row_evidence(row))
    return payload


def _source_text(evidence: MappedCellEvidence | None) -> str | None:
    if evidence is None or not isinstance(evidence.raw_value, str):
        return None
    return evidence.raw_value


def _source_sheet(
    sheets: dict[str, LongSourceSheetRow],
    sheet_name: str,
) -> LongSourceSheetRow:
    try:
        return sheets[sheet_name]
    except KeyError as error:
        raise LongPersistenceIntegrityError(
            "candidate evidence refers to a sheet outside the preserved source scan"
        ) from error


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _stored_source_snapshot(
    session: Session,
    source: LongSourceFileRow,
) -> dict[str, object]:
    sheet_rows = session.scalars(
        select(LongSourceSheetRow)
        .where(
            LongSourceSheetRow.project_key == source.project_key,
            LongSourceSheetRow.source_file_id == source.id,
        )
        .order_by(LongSourceSheetRow.position)
    ).all()
    sheets: list[dict[str, object]] = []
    for row in sheet_rows:
        snapshot = row.scan_snapshot
        if canonical_json_sha256(snapshot) != row.snapshot_sha256:
            raise LongPersistenceIntegrityError(
                "stored source sheet snapshot digest does not match"
            )
        if _sheet_index_snapshot(row) != _sheet_index_snapshot_from_payload(snapshot):
            raise LongPersistenceIntegrityError(
                "stored source sheet indexes disagree with the immutable snapshot"
            )
        sheets.append(snapshot)
    return {
        "blob_id": source.blob_id,
        "content_sha256": source.content_sha256,
        "received_at": source.received_at.isoformat(),
        "original_filename": source.original_filename,
        "model_candidates": source.model_candidates,
        "lot_candidates": source.lot_candidates,
        "declared_mime_type": source.declared_mime_type,
        "detected_mime_type": source.detected_mime_type,
        "canonical_extension": source.canonical_extension,
        "size_bytes": source.size_bytes,
        "parse_status": source.parse_status,
        "scan_source_name": source.scan_source_name,
        "scan_source_size_bytes": source.scan_source_size_bytes,
        "scan_sha256_before": source.scan_sha256_before,
        "scan_sha256_after": source.scan_sha256_after,
        "scan_contract_version": source.scan_contract_version,
        "estimated_cells": source.estimated_cells,
        "external_link_count": source.external_link_count,
        "macro_handling": source.macro_handling,
        "display_value_contract": source.display_value_contract,
        "is_golden_workbook_evidence": source.is_golden_workbook_evidence,
        "scan_issues": source.scan_issues,
        "sheets": sheets,
    }


def _sheet_index_snapshot(row: LongSourceSheetRow) -> dict[str, object]:
    return {
        "position": row.position,
        "sheet_name": row.sheet_name,
        "sheet_kind": row.sheet_kind,
        "visibility": row.visibility,
        "used_range": row.used_range,
        "estimated_cells": row.estimated_cells,
        "merged_ranges": row.merged_ranges,
        "hidden_row_ranges": row.hidden_row_ranges,
        "hidden_column_ranges": row.hidden_column_ranges,
        "formula_count": row.formula_count,
        "protection_metadata": row.protection_metadata,
        "image_metadata": row.image_metadata,
        "issues": row.issues,
    }


def _sheet_index_snapshot_from_payload(payload: dict[str, object]) -> dict[str, object]:
    return {
        key: payload[key]
        for key in (
            "position",
            "sheet_name",
            "sheet_kind",
            "visibility",
            "used_range",
            "estimated_cells",
            "merged_ranges",
            "hidden_row_ranges",
            "hidden_column_ranges",
            "formula_count",
            "protection_metadata",
            "image_metadata",
            "issues",
        )
    }


def _requested_job_basis(basis: LongClaimBasis) -> dict[str, object]:
    return {
        "mapping_payload_sha256": basis.mapping_payload_sha256,
        "binding_catalog_revision": basis.binding_catalog_revision,
        "scan_contract_version": basis.scan_contract_version,
        "idempotency_key": basis.idempotency_key,
        "candidate_snapshot": basis.candidate_snapshot,
        "candidate_snapshot_sha256": basis.candidate_snapshot_sha256,
        "issues": [serialize_candidate_issue(issue) for issue in basis.issues],
    }


def _stored_job_basis(job: LongIngestionJobRow) -> dict[str, object]:
    if canonical_json_sha256(job.candidate_snapshot) != job.candidate_snapshot_sha256:
        raise LongPersistenceIntegrityError("stored candidate snapshot digest does not match")
    provenance = job.candidate_snapshot.get("provenance")
    if not isinstance(provenance, dict):
        raise LongPersistenceIntegrityError("stored candidate provenance changed")
    receipt = provenance.get("receipt")
    if not isinstance(receipt, dict):
        raise LongPersistenceIntegrityError("stored candidate receipt changed")
    expected_proof = build_applied_mapping_proof(
        project_key=job.project_key,
        source_file_id=job.source_file_id,
        receipt_id=_string(cast(dict[str, object], receipt), "receipt_id"),
        content_sha256=job.content_sha256,
        mapping_template_revision_id=job.mapping_template_revision_id,
        mapping_payload_sha256=job.mapping_payload_sha256,
        candidate_snapshot=job.candidate_snapshot,
        candidate_snapshot_sha256=job.candidate_snapshot_sha256,
    )
    if (
        job.applied_mapping_proof != expected_proof
        or canonical_json_sha256(job.applied_mapping_proof) != job.applied_mapping_proof_sha256
    ):
        raise LongPersistenceIntegrityError("stored applied Mapping proof changed")
    return {
        "mapping_payload_sha256": job.mapping_payload_sha256,
        "binding_catalog_revision": job.binding_catalog_revision,
        "scan_contract_version": job.scan_contract_version,
        "idempotency_key": job.idempotency_key,
        "candidate_snapshot": job.candidate_snapshot,
        "candidate_snapshot_sha256": job.candidate_snapshot_sha256,
        "issues": job.issues,
    }


def _string(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str):
        raise LongPersistenceIntegrityError(f"snapshot field {key} must be a string")
    return value


def _optional_string(payload: dict[str, object], key: str) -> str | None:
    value = payload.get(key)
    if value is not None and not isinstance(value, str):
        raise LongPersistenceIntegrityError(f"snapshot field {key} must be a string or null")
    return value


def _tagged_payload(payload: dict[str, object], key: str) -> dict[str, object]:
    value = payload.get(key)
    if not isinstance(value, dict) or any(not isinstance(child, str) for child in value):
        raise LongPersistenceIntegrityError(f"snapshot field {key} must be a tagged object")
    return cast(dict[str, object], value)


def _checkpoint_keys(payload: dict[str, object], expected: set[str], name: str) -> None:
    if set(payload) != expected:
        raise LongPersistenceIntegrityError(f"{name} key set is invalid")


def _checkpoint_object(payload: dict[str, object], key: str) -> dict[str, object]:
    value = payload.get(key)
    if not isinstance(value, dict) or any(not isinstance(child, str) for child in value):
        raise LongPersistenceIntegrityError(f"checkpoint field {key} must be an object")
    return cast(dict[str, object], value)


def _checkpoint_object_list(payload: dict[str, object], key: str) -> tuple[dict[str, object], ...]:
    value = payload.get(key)
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        raise LongPersistenceIntegrityError(f"checkpoint field {key} must be a list of objects")
    return tuple(cast(dict[str, object], item) for item in value)


def _checkpoint_string_tuple(payload: dict[str, object], key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise LongPersistenceIntegrityError(f"checkpoint field {key} must be a list of strings")
    return tuple(cast(str, item) for item in value)


def _checkpoint_int(payload: dict[str, object], key: str, *, minimum: int) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise LongPersistenceIntegrityError(f"checkpoint field {key} is not a valid integer")
    return value


def _checkpoint_bool(payload: dict[str, object], key: str) -> bool:
    value = payload.get(key)
    if not isinstance(value, bool):
        raise LongPersistenceIntegrityError(f"checkpoint field {key} must be a boolean")
    return value


def _checkpoint_optional_number(payload: dict[str, object], key: str) -> float | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise LongPersistenceIntegrityError(f"checkpoint field {key} must be a number or null")
    return float(value)


def _claim_result(job: LongIngestionJobRow, *, replayed: bool) -> LongClaimResult:
    return LongClaimResult(
        project_key=job.project_key,
        source_file_id=job.source_file_id,
        ingestion_job_id=job.id,
        status=LongJobStatus(job.status),
        row_version=job.row_version,
        reused_job_id=job.reused_job_id,
        blocking_job_id=job.blocking_job_id,
        replayed=replayed,
    )
