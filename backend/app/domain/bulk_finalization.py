"""Durable, explicit Bulk finalization contracts.

The candidate is a small proof over already-persisted Bulk staging rows.  It is
not a Long candidate snapshot and never authorizes a data-status decision.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class BulkFinalizationStatus(StrEnum):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"


class BulkFinalizationEntryStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True, slots=True)
class BulkFinalizationEligibleEntry:
    entry_id: str
    ordinal: int
    filename: str
    bulk_row_version: int
    receipt_id: str
    content_sha256: str
    mapping_sha256: str
    long_candidate_digest: str
    prepared_checkpoint_sha256: str
    prepared_checkpoint_version: str
    prepared_checkpoint_bytes: int

    def __post_init__(self) -> None:
        for name in ("entry_id", "filename", "receipt_id", "prepared_checkpoint_version"):
            _exact(getattr(self, name), name, 500)
        for name in (
            "content_sha256",
            "mapping_sha256",
            "long_candidate_digest",
            "prepared_checkpoint_sha256",
        ):
            _sha(getattr(self, name), name)
        if self.ordinal < 0 or self.bulk_row_version < 1 or self.prepared_checkpoint_bytes < 1:
            raise ValueError("eligible entry ordinal/version is invalid")


@dataclass(frozen=True, slots=True)
class BulkFinalizationExcludedEntry:
    entry_id: str
    ordinal: int
    filename: str
    outcome: str
    status_code: str
    issues_sha256: str
    bulk_row_version: int
    size_bytes: int
    upload_sha256: str
    receipt_id: str | None
    content_sha256: str | None

    def __post_init__(self) -> None:
        for name in ("entry_id", "filename", "outcome", "status_code"):
            _exact(getattr(self, name), name, 500)
        _sha(self.issues_sha256, "issues_sha256")
        _sha(self.upload_sha256, "upload_sha256")
        if self.ordinal < 0 or self.bulk_row_version < 1 or self.size_bytes < 1:
            raise ValueError("excluded entry ordinal is invalid")
        if (self.receipt_id is None) != (self.content_sha256 is None):
            raise ValueError("excluded receipt proof must be all-null or all-set")
        if self.receipt_id is not None:
            _exact(self.receipt_id, "receipt_id", 500)
            content_sha256 = self.content_sha256
            if content_sha256 is None:
                raise ValueError("excluded receipt content digest is missing")
            _sha(content_sha256, "content_sha256")


@dataclass(frozen=True, slots=True)
class BulkFinalizationCandidate:
    batch_id: str
    project_key: str
    supplier_scope: str
    batch_status: str
    batch_row_version: int
    finalization_digest: str
    eligible_entries: tuple[BulkFinalizationEligibleEntry, ...]
    excluded_entries: tuple[BulkFinalizationExcludedEntry, ...]

    def __post_init__(self) -> None:
        for name in ("batch_id", "project_key", "supplier_scope", "batch_status"):
            _exact(getattr(self, name), name, 200)
        _sha(self.finalization_digest, "finalization_digest")
        if self.batch_row_version < 1:
            raise ValueError("batch_row_version must be positive")
        entry_ids = tuple(item.entry_id for item in self.eligible_entries) + tuple(
            item.entry_id for item in self.excluded_entries
        )
        ordinals = tuple(item.ordinal for item in self.eligible_entries) + tuple(
            item.ordinal for item in self.excluded_entries
        )
        if not entry_ids:
            raise ValueError("a finalization candidate requires Bulk entries")
        if len(set(entry_ids)) != len(entry_ids):
            raise ValueError("finalization entries must be unique")
        if tuple(item.ordinal for item in self.eligible_entries) != tuple(
            sorted(item.ordinal for item in self.eligible_entries)
        ) or tuple(item.ordinal for item in self.excluded_entries) != tuple(
            sorted(item.ordinal for item in self.excluded_entries)
        ):
            raise ValueError("each finalization partition must use deterministic order")
        if tuple(sorted(ordinals)) != tuple(range(len(ordinals))):
            raise ValueError("finalization entries must cover the complete Bulk ordinal set")

    @property
    def can_finalize(self) -> bool:
        return bool(self.eligible_entries)


@dataclass(frozen=True, slots=True)
class BulkFinalizationEntrySnapshot:
    entry_id: str
    bulk_entry_id: str
    ordinal: int
    status: BulkFinalizationEntryStatus
    status_label: str
    attempt_count: int
    row_version: int
    long_source_file_id: str | None
    long_ingestion_job_id: str | None
    long_status: str | None
    long_row_version: int | None
    replayed: bool | None
    error_code: str | None

    def __post_init__(self) -> None:
        for name in ("entry_id", "bulk_entry_id", "status_label"):
            _exact(getattr(self, name), name, 200)
        if min(self.ordinal, self.attempt_count) < 0 or self.row_version < 1:
            raise ValueError("finalization entry counters are invalid")
        proof = (
            self.long_source_file_id,
            self.long_ingestion_job_id,
            self.long_status,
            self.long_row_version,
            self.replayed,
        )
        if self.status == BulkFinalizationEntryStatus.COMPLETED:
            if any(value is None for value in proof) or self.error_code is not None:
                raise ValueError("completed finalization entry requires exact Long proof")
            if self.long_status not in {"COMPLETED_PENDING", "REUSED"}:
                raise ValueError("completed finalization entry requires successful Long status")
        elif any(value is not None for value in proof):
            raise ValueError("non-completed finalization entry cannot carry Long proof")
        if self.status == BulkFinalizationEntryStatus.BLOCKED:
            if self.error_code is None:
                raise ValueError("blocked finalization entry requires a safe error code")
        elif self.error_code is not None:
            raise ValueError("only blocked finalization entries carry an error code")


@dataclass(frozen=True, slots=True)
class BulkFinalizationSummary:
    total: int
    pending: int
    processing: int
    completed: int
    blocked: int

    def __post_init__(self) -> None:
        values = (self.total, self.pending, self.processing, self.completed, self.blocked)
        if any(value < 0 for value in values):
            raise ValueError("finalization summary counts cannot be negative")
        if self.pending + self.processing + self.completed + self.blocked != self.total:
            raise ValueError("finalization summary partitions must equal total")


@dataclass(frozen=True, slots=True)
class BulkFinalizationSnapshot:
    command_id: str
    batch_id: str
    project_key: str
    supplier_scope: str
    status: BulkFinalizationStatus
    status_label: str
    message: str
    finalization_digest: str
    reason: str
    row_version: int
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None
    entries: tuple[BulkFinalizationEntrySnapshot, ...]
    summary: BulkFinalizationSummary

    def __post_init__(self) -> None:
        for name in (
            "command_id",
            "batch_id",
            "project_key",
            "supplier_scope",
            "status_label",
            "message",
            "reason",
        ):
            _exact(getattr(self, name), name, 1000)
        _sha(self.finalization_digest, "finalization_digest")
        if self.row_version < 1:
            raise ValueError("finalization row_version must be positive")
        for name in ("created_at", "updated_at"):
            _aware(getattr(self, name), name)
        if self.finished_at is not None:
            _aware(self.finished_at, "finished_at")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if self.finished_at is not None and self.finished_at < self.created_at:
            raise ValueError("finished_at cannot precede created_at")
        if len(self.entries) != self.summary.total:
            raise ValueError("summary total must equal entry count")
        if self.status == BulkFinalizationStatus.COMPLETED:
            if self.finished_at is None or self.summary.completed != self.summary.total:
                raise ValueError("completed finalization shape is invalid")
        elif self.status == BulkFinalizationStatus.BLOCKED:
            if self.finished_at is None or self.summary.blocked < 1:
                raise ValueError("blocked finalization shape is invalid")
        elif self.finished_at is not None:
            raise ValueError("nonterminal finalization cannot have finished_at")

    @property
    def terminal(self) -> bool:
        return self.status in {
            BulkFinalizationStatus.COMPLETED,
            BulkFinalizationStatus.BLOCKED,
        }

    @property
    def poll_after_ms(self) -> int | None:
        return None if self.terminal else 500


def _exact(value: str, name: str, limit: int) -> None:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > limit:
        raise ValueError(f"{name} must be an exact nonblank value")


def _sha(value: str, name: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256")


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
