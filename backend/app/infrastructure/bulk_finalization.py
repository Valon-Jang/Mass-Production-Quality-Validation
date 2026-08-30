"""Persistent asynchronous Bulk finalization command state."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    select,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.infrastructure.audit import UTCDateTime
from app.infrastructure.database import Base


class BulkFinalizationCommandRow(Base):
    __tablename__ = "bulk_finalization_commands"
    __table_args__ = (
        UniqueConstraint("project_key", "id", name="uq_bulk_finalization_commands_project_id"),
        UniqueConstraint(
            "project_key", "batch_id", name="uq_bulk_finalization_commands_project_batch"
        ),
        UniqueConstraint(
            "project_key",
            "id",
            "batch_id",
            name="uq_bulk_finalization_commands_project_id_batch",
        ),
        ForeignKeyConstraint(
            ["project_key", "batch_id"],
            ["bulk_import_batches.project_key", "bulk_import_batches.id"],
            ondelete="RESTRICT",
            name="fk_bulk_finalization_commands_project_batch",
        ),
        CheckConstraint(
            "status IN ('QUEUED','PROCESSING','COMPLETED','BLOCKED')",
            name="bulk_finalization_command_status",
        ),
        CheckConstraint(
            "length(project_key) BETWEEN 1 AND 64",
            name="bulk_finalization_command_project_length",
        ),
        CheckConstraint(
            "length(supplier_scope) BETWEEN 1 AND 200",
            name="bulk_finalization_command_supplier_length",
        ),
        CheckConstraint(
            "length(finalization_digest) = 64",
            name="bulk_finalization_command_digest",
        ),
        CheckConstraint(
            "length(reason) BETWEEN 1 AND 1000",
            name="bulk_finalization_command_reason",
        ),
        CheckConstraint("entry_count >= 1", name="bulk_finalization_command_entry_count"),
        CheckConstraint("row_version >= 1", name="bulk_finalization_command_row_version"),
        CheckConstraint(
            "(status IN ('QUEUED','PROCESSING') AND finished_at IS NULL) OR "
            "(status IN ('COMPLETED','BLOCKED') AND finished_at IS NOT NULL)",
            name="bulk_finalization_command_terminal_shape",
        ),
        Index(
            "ix_bulk_finalization_commands_project_status",
            "project_key",
            "status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_key: Mapped[str] = mapped_column(String(64), nullable=False)
    batch_id: Mapped[str] = mapped_column(String(36), nullable=False)
    supplier_scope: Mapped[str] = mapped_column(String(200), nullable=False)
    finalization_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    requested_by: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    entry_count: Mapped[int] = mapped_column(Integer, nullable=False)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class BulkFinalizationEntryRow(Base):
    __tablename__ = "bulk_finalization_entries"
    __table_args__ = (
        UniqueConstraint("project_key", "id", name="uq_bulk_finalization_entries_project_id"),
        UniqueConstraint(
            "project_key",
            "command_id",
            "bulk_entry_id",
            name="uq_bulk_finalization_entries_command_bulk_entry",
        ),
        UniqueConstraint(
            "project_key",
            "command_id",
            "ordinal",
            name="uq_bulk_finalization_entries_command_ordinal",
        ),
        ForeignKeyConstraint(
            ["project_key", "command_id", "batch_id"],
            [
                "bulk_finalization_commands.project_key",
                "bulk_finalization_commands.id",
                "bulk_finalization_commands.batch_id",
            ],
            ondelete="RESTRICT",
            name="fk_bulk_finalization_entries_project_command",
        ),
        ForeignKeyConstraint(
            ["project_key", "bulk_entry_id", "batch_id"],
            [
                "bulk_import_entries.project_key",
                "bulk_import_entries.id",
                "bulk_import_entries.batch_id",
            ],
            ondelete="RESTRICT",
            name="fk_bulk_finalization_entries_project_bulk_entry",
        ),
        ForeignKeyConstraint(
            ["project_key", "long_ingestion_job_id", "long_source_file_id"],
            [
                "ingestion_jobs.project_key",
                "ingestion_jobs.id",
                "ingestion_jobs.source_file_id",
            ],
            ondelete="RESTRICT",
            name="fk_bulk_finalization_entries_project_job_source",
        ),
        CheckConstraint("ordinal >= 0", name="bulk_finalization_entry_ordinal"),
        CheckConstraint(
            "status IN ('PENDING','PROCESSING','COMPLETED','BLOCKED')",
            name="bulk_finalization_entry_status",
        ),
        CheckConstraint("attempt_count >= 0", name="bulk_finalization_entry_attempt_count"),
        CheckConstraint(
            "expected_bulk_row_version >= 1", name="bulk_finalization_entry_basis_version"
        ),
        CheckConstraint("row_version >= 1", name="bulk_finalization_entry_row_version"),
        CheckConstraint(
            "length(expected_content_sha256) = 64 "
            "AND length(expected_mapping_sha256) = 64 "
            "AND length(expected_candidate_payload_sha256) = 64 "
            "AND length(expected_long_candidate_digest) = 64 "
            "AND length(expected_checkpoint_sha256) = 64 "
            "AND expected_checkpoint_version = 'bulk-prepared-long-v1' "
            "AND expected_checkpoint_bytes BETWEEN 1 AND 16777216",
            name="bulk_finalization_entry_digest_lengths",
        ),
        CheckConstraint(
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
            name="bulk_finalization_entry_result_shape",
        ),
        Index(
            "ix_bulk_finalization_entries_project_command_status",
            "project_key",
            "command_id",
            "status",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_key: Mapped[str] = mapped_column(String(64), nullable=False)
    command_id: Mapped[str] = mapped_column(String(36), nullable=False)
    batch_id: Mapped[str] = mapped_column(String(36), nullable=False)
    bulk_entry_id: Mapped[str] = mapped_column(String(36), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_bulk_row_version: Mapped[int] = mapped_column(Integer, nullable=False)
    expected_receipt_id: Mapped[str] = mapped_column(String(200), nullable=False)
    expected_content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_mapping_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_candidate_payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_long_candidate_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_checkpoint_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_checkpoint_version: Mapped[str] = mapped_column(String(64), nullable=False)
    expected_checkpoint_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    long_source_file_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    long_ingestion_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    long_status: Mapped[str | None] = mapped_column(String(40), nullable=True)
    long_row_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    replayed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class BulkFinalizationRepository:
    @staticmethod
    def get_by_batch(
        session: Session, *, project_key: str, batch_id: str
    ) -> BulkFinalizationCommandRow | None:
        return session.scalar(
            select(BulkFinalizationCommandRow).where(
                BulkFinalizationCommandRow.project_key == project_key,
                BulkFinalizationCommandRow.batch_id == batch_id,
            )
        )

    @staticmethod
    def get_command(
        session: Session, *, project_key: str, command_id: str
    ) -> BulkFinalizationCommandRow | None:
        return session.scalar(
            select(BulkFinalizationCommandRow).where(
                BulkFinalizationCommandRow.project_key == project_key,
                BulkFinalizationCommandRow.id == command_id,
            )
        )

    @staticmethod
    def entries(
        session: Session, *, project_key: str, command_id: str
    ) -> tuple[BulkFinalizationEntryRow, ...]:
        return tuple(
            session.scalars(
                select(BulkFinalizationEntryRow)
                .where(
                    BulkFinalizationEntryRow.project_key == project_key,
                    BulkFinalizationEntryRow.command_id == command_id,
                )
                .order_by(BulkFinalizationEntryRow.ordinal, BulkFinalizationEntryRow.id)
            )
        )

    @staticmethod
    def recoverable_command_ids(session: Session) -> tuple[str, ...]:
        return tuple(
            session.scalars(
                select(BulkFinalizationCommandRow.id)
                .where(BulkFinalizationCommandRow.status.in_(("QUEUED", "PROCESSING")))
                .order_by(BulkFinalizationCommandRow.created_at, BulkFinalizationCommandRow.id)
            )
        )
