"""Persistent project-local Bulk staging queue and immutable review evidence."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    select,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.domain.bulk_import import BulkBatchStatus, BulkEntryStatus
from app.infrastructure.audit import UTCDateTime
from app.infrastructure.database import Base
from app.infrastructure.long_format import canonical_json_sha256

BULK_PREPARED_CHECKPOINT_VERSION = "bulk-prepared-long-v1"
BULK_PREPARED_CHECKPOINT_MAX_BYTES = 16 * 1024 * 1024


class BulkBatchRow(Base):
    __tablename__ = "bulk_import_batches"
    __table_args__ = (
        UniqueConstraint("project_key", "id", name="uq_bulk_batches_project_id"),
        UniqueConstraint(
            "project_key",
            "idempotency_key",
            name="uq_bulk_batches_project_idempotency",
        ),
        CheckConstraint(
            "status IN ('STAGED','PROCESSING','COMPLETED','COMPLETED_WITH_EXCEPTIONS','FAILED')",
            name="bulk_batch_status",
        ),
        CheckConstraint("length(manifest_sha256) = 64", name="bulk_batch_manifest_sha"),
        CheckConstraint("length(project_key) BETWEEN 1 AND 64", name="bulk_batch_project_length"),
        CheckConstraint(
            "length(supplier_scope) BETWEEN 1 AND 200", name="bulk_batch_supplier_length"
        ),
        CheckConstraint(
            "length(idempotency_key) BETWEEN 8 AND 128", name="bulk_batch_idempotency_length"
        ),
        CheckConstraint("entry_count >= 1", name="bulk_batch_entry_count"),
        CheckConstraint("row_version >= 1", name="bulk_batch_row_version"),
        CheckConstraint(
            "(terminal_summary IS NULL AND terminal_summary_sha256 IS NULL) OR "
            "(terminal_summary IS NOT NULL AND terminal_summary_sha256 IS NOT NULL "
            "AND length(terminal_summary_sha256) = 64)",
            name="bulk_batch_summary_shape",
        ),
        CheckConstraint(
            "(status IN ('STAGED','PROCESSING') AND finished_at IS NULL "
            "AND terminal_summary IS NULL) OR "
            "(status IN ('COMPLETED','COMPLETED_WITH_EXCEPTIONS','FAILED') "
            "AND finished_at IS NOT NULL AND terminal_summary IS NOT NULL)",
            name="bulk_batch_terminal_shape",
        ),
        Index("ix_bulk_batches_project_status", "project_key", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_key: Mapped[str] = mapped_column(String(64), nullable=False)
    supplier_scope: Mapped[str] = mapped_column(String(200), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    entry_count: Mapped[int] = mapped_column(Integer, nullable=False)
    terminal_summary: Mapped[dict[str, int] | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )
    terminal_summary_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class BulkEntryRow(Base):
    __tablename__ = "bulk_import_entries"
    __table_args__ = (
        UniqueConstraint("project_key", "id", name="uq_bulk_entries_project_id"),
        UniqueConstraint(
            "project_key", "batch_id", "ordinal", name="uq_bulk_entries_batch_ordinal"
        ),
        UniqueConstraint(
            "project_key",
            "reserved_receipt_id",
            name="uq_bulk_entries_project_receipt",
        ),
        ForeignKeyConstraint(
            ["project_key", "batch_id"],
            ["bulk_import_batches.project_key", "bulk_import_batches.id"],
            ondelete="RESTRICT",
            name="fk_bulk_entries_project_batch",
        ),
        ForeignKeyConstraint(
            ["project_key", "duplicate_of_entry_id"],
            ["bulk_import_entries.project_key", "bulk_import_entries.id"],
            ondelete="RESTRICT",
            name="fk_bulk_entries_project_duplicate",
        ),
        ForeignKeyConstraint(
            ["project_key", "revision_baseline_entry_id"],
            ["bulk_import_entries.project_key", "bulk_import_entries.id"],
            ondelete="RESTRICT",
            name="fk_bulk_entries_project_revision_baseline",
        ),
        CheckConstraint("ordinal >= 0", name="bulk_entry_ordinal"),
        CheckConstraint("size_bytes >= 0", name="bulk_entry_size"),
        CheckConstraint("length(project_key) BETWEEN 1 AND 64", name="bulk_entry_project_length"),
        CheckConstraint("length(filename) BETWEEN 1 AND 500", name="bulk_entry_filename_length"),
        CheckConstraint("length(mime_type) BETWEEN 1 AND 200", name="bulk_entry_mime_length"),
        CheckConstraint("length(upload_sha256) = 64", name="bulk_entry_upload_sha"),
        CheckConstraint("length(reserved_receipt_id) = 32", name="bulk_entry_receipt_id"),
        CheckConstraint("status IN ('STAGED','PROCESSING','TERMINAL')", name="bulk_entry_status"),
        CheckConstraint(
            "outcome IS NULL OR outcome IN ("
            "'CANDIDATE_READY','DUPLICATE_CANDIDATE','MAPPING_REQUIRED','SCAN_FAILED',"
            "'IDENTIFIER_HOLD','BINDING_HOLD','VARIATION_REVIEW_REQUIRED',"
            "'REVISION_REVIEW_REQUIRED','ERROR')",
            name="bulk_entry_outcome",
        ),
        CheckConstraint(
            "(status IN ('STAGED','PROCESSING') AND outcome IS NULL AND finished_at IS NULL) OR "
            "(status = 'TERMINAL' AND outcome IS NOT NULL AND finished_at IS NOT NULL)",
            name="bulk_entry_terminal_shape",
        ),
        CheckConstraint("attempt_count >= 0", name="bulk_entry_attempt_count"),
        CheckConstraint("row_version >= 1", name="bulk_entry_row_version"),
        CheckConstraint(
            "(receipt_payload IS NULL AND receipt_sha256 IS NULL) OR "
            "(receipt_payload IS NOT NULL AND receipt_sha256 IS NOT NULL "
            "AND length(receipt_sha256) = 64)",
            name="bulk_entry_receipt_shape",
        ),
        CheckConstraint(
            "(mapping_payload IS NULL AND mapping_sha256 IS NULL) OR "
            "(mapping_payload IS NOT NULL AND mapping_sha256 IS NOT NULL "
            "AND length(mapping_sha256) = 64)",
            name="bulk_entry_mapping_shape",
        ),
        CheckConstraint(
            "(candidate_payload IS NULL AND candidate_sha256 IS NULL) OR "
            "(candidate_payload IS NOT NULL AND candidate_sha256 IS NOT NULL "
            "AND length(candidate_sha256) = 64)",
            name="bulk_entry_candidate_shape",
        ),
        CheckConstraint("length(issues_sha256) = 64", name="bulk_entry_issues_sha"),
        CheckConstraint(
            "(revision_evidence IS NULL AND revision_evidence_sha256 IS NULL) OR "
            "(revision_evidence IS NOT NULL AND revision_evidence_sha256 IS NOT NULL "
            "AND length(revision_evidence_sha256) = 64)",
            name="bulk_entry_revision_evidence_shape",
        ),
        CheckConstraint(
            "revision_identity IS NULL OR length(revision_identity) = 64",
            name="bulk_entry_revision_identity_sha",
        ),
        CheckConstraint(
            "(prepared_checkpoint IS NULL AND prepared_checkpoint_sha256 IS NULL "
            "AND prepared_checkpoint_version IS NULL AND prepared_checkpoint_bytes IS NULL) OR "
            "(prepared_checkpoint IS NOT NULL AND prepared_checkpoint_sha256 IS NOT NULL "
            "AND length(prepared_checkpoint_sha256) = 64 "
            "AND prepared_checkpoint_version = 'bulk-prepared-long-v1' "
            "AND prepared_checkpoint_bytes BETWEEN 1 AND 16777216)",
            name="bulk_entry_prepared_checkpoint_shape",
        ),
        Index("ix_bulk_entries_project_batch_status", "project_key", "batch_id", "status"),
        Index(
            "uq_bulk_entries_project_id_batch",
            "project_key",
            "id",
            "batch_id",
            unique=True,
        ),
        Index("ix_bulk_entries_project_upload", "project_key", "upload_sha256"),
        Index("ix_bulk_entries_project_revision_identity", "project_key", "revision_identity"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_key: Mapped[str] = mapped_column(String(64), nullable=False)
    batch_id: Mapped[str] = mapped_column(String(36), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved_receipt_id: Mapped[str] = mapped_column(String(32), nullable=False)
    reserved_received_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(200), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    upload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    staged_relative_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status_code: Mapped[str] = mapped_column(String(100), nullable=False)
    message: Mapped[str] = mapped_column(String(1000), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    receipt_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )
    receipt_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    mapping_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )
    mapping_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    candidate_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True), nullable=True
    )
    candidate_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    revision_identity: Mapped[str | None] = mapped_column(String(64), nullable=True)
    revision_evidence: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True), nullable=True, deferred=True
    )
    revision_evidence_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prepared_checkpoint: Mapped[dict[str, Any] | None] = mapped_column(
        JSON(none_as_null=True), nullable=True, deferred=True
    )
    prepared_checkpoint_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prepared_checkpoint_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prepared_checkpoint_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    issues: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    issues_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    duplicate_of_entry_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    revision_baseline_entry_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class BulkImportPersistenceError(RuntimeError):
    pass


class BulkImportRepository:
    """Small repository; callers own transaction boundaries and retry policy."""

    def create_batch(
        self,
        session: Session,
        *,
        project_key: str,
        supplier_scope: str,
        idempotency_key: str,
        manifest_sha256: str,
        entries: tuple[dict[str, Any], ...],
        now: datetime,
    ) -> BulkBatchRow:
        batch = BulkBatchRow(
            id=str(uuid4()),
            project_key=project_key,
            supplier_scope=supplier_scope,
            idempotency_key=idempotency_key,
            manifest_sha256=manifest_sha256,
            status=BulkBatchStatus.STAGED.value,
            entry_count=len(entries),
            row_version=1,
            created_at=now,
            updated_at=now,
        )
        session.add(batch)
        session.flush()
        for entry in entries:
            session.add(
                BulkEntryRow(
                    id=str(uuid4()),
                    project_key=project_key,
                    batch_id=batch.id,
                    ordinal=int(entry["ordinal"]),
                    reserved_receipt_id=str(entry["reserved_receipt_id"]),
                    reserved_received_at=entry["reserved_received_at"],
                    filename=str(entry["filename"]),
                    mime_type=str(entry["mime_type"]),
                    size_bytes=int(entry["size_bytes"]),
                    upload_sha256=str(entry["upload_sha256"]),
                    staged_relative_path=str(entry["staged_relative_path"]),
                    status=BulkEntryStatus.STAGED.value,
                    status_code="BULK_STAGED",
                    message="원본 보존 및 검사를 기다리고 있습니다.",
                    attempt_count=0,
                    issues=[],
                    issues_sha256=canonical_json_sha256([]),
                    row_version=1,
                    created_at=now,
                    updated_at=now,
                )
            )
        session.flush()
        return batch

    @staticmethod
    def find_by_idempotency(
        session: Session, *, project_key: str, idempotency_key: str
    ) -> BulkBatchRow | None:
        return session.scalar(
            select(BulkBatchRow).where(
                BulkBatchRow.project_key == project_key,
                BulkBatchRow.idempotency_key == idempotency_key,
            )
        )

    @staticmethod
    def get_batch(session: Session, *, project_key: str, batch_id: str) -> BulkBatchRow | None:
        return session.scalar(
            select(BulkBatchRow).where(
                BulkBatchRow.project_key == project_key,
                BulkBatchRow.id == batch_id,
            )
        )

    @staticmethod
    def entries(session: Session, *, project_key: str, batch_id: str) -> tuple[BulkEntryRow, ...]:
        return tuple(
            session.scalars(
                select(BulkEntryRow)
                .where(
                    BulkEntryRow.project_key == project_key,
                    BulkEntryRow.batch_id == batch_id,
                )
                .order_by(BulkEntryRow.ordinal)
            )
        )

    @staticmethod
    def recoverable_batch_ids(session: Session) -> tuple[str, ...]:
        return tuple(
            session.scalars(
                select(BulkBatchRow.id)
                .where(BulkBatchRow.status.in_(("STAGED", "PROCESSING")))
                .order_by(BulkBatchRow.created_at, BulkBatchRow.id)
            )
        )

    @staticmethod
    def prior_terminal_entries(
        session: Session, *, project_key: str, supplier_scope: str, exclude_entry_id: str
    ) -> tuple[BulkEntryRow, ...]:
        return tuple(
            session.scalars(
                select(BulkEntryRow)
                .join(
                    BulkBatchRow,
                    (BulkBatchRow.project_key == BulkEntryRow.project_key)
                    & (BulkBatchRow.id == BulkEntryRow.batch_id),
                )
                .where(
                    BulkEntryRow.project_key == project_key,
                    BulkBatchRow.supplier_scope == supplier_scope,
                    BulkEntryRow.id != exclude_entry_id,
                    BulkEntryRow.status == BulkEntryStatus.TERMINAL.value,
                )
                .order_by(BulkEntryRow.created_at, BulkEntryRow.id)
            )
        )


def utc_now() -> datetime:
    return datetime.now(UTC)
