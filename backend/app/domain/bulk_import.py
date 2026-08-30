"""Durable Bulk staging states and safe, read-only review evidence."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class BulkBatchStatus(StrEnum):
    STAGED = "STAGED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    COMPLETED_WITH_EXCEPTIONS = "COMPLETED_WITH_EXCEPTIONS"
    FAILED = "FAILED"


class BulkEntryStatus(StrEnum):
    STAGED = "STAGED"
    PROCESSING = "PROCESSING"
    TERMINAL = "TERMINAL"


class BulkEntryOutcome(StrEnum):
    CANDIDATE_READY = "CANDIDATE_READY"
    DUPLICATE_CANDIDATE = "DUPLICATE_CANDIDATE"
    MAPPING_REQUIRED = "MAPPING_REQUIRED"
    SCAN_FAILED = "SCAN_FAILED"
    IDENTIFIER_HOLD = "IDENTIFIER_HOLD"
    BINDING_HOLD = "BINDING_HOLD"
    VARIATION_REVIEW_REQUIRED = "VARIATION_REVIEW_REQUIRED"
    REVISION_REVIEW_REQUIRED = "REVISION_REVIEW_REQUIRED"
    ERROR = "ERROR"


class BulkIssueCategory(StrEnum):
    SCAN = "SCAN"
    MAPPING = "MAPPING"
    IDENTIFIER = "IDENTIFIER"
    BINDING = "BINDING"
    VARIATION = "VARIATION"
    REVISION = "REVISION"
    DUPLICATE = "DUPLICATE"
    SYSTEM = "SYSTEM"


class BulkIssueSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    BLOCKING = "BLOCKING"


@dataclass(frozen=True, slots=True)
class BulkIssue:
    code: str
    category: BulkIssueCategory
    severity: BulkIssueSeverity
    message: str
    location: str | None = None
    evidence_path: str | None = None
    baseline_entry_id: str | None = None
    expected_json: Any | None = None
    observed_json: Any | None = None

    def __post_init__(self) -> None:
        _exact(self.code, "code", 100)
        _exact(self.message, "message", 1000)
        for name in ("location", "evidence_path", "baseline_entry_id"):
            value = getattr(self, name)
            if value is not None:
                _exact(value, name, 500)
        if self.location is not None and ("\\" in self.location or ":/" in self.location):
            raise ValueError("issue location must be workbook-logical, not a filesystem path")
        if self.evidence_path is not None and (
            "\\" in self.evidence_path or ":/" in self.evidence_path
        ):
            raise ValueError("evidence_path must be logical, not a filesystem path")


@dataclass(frozen=True, slots=True)
class BulkReceiptProof:
    receipt_id: str
    content_sha256: str
    original_filename: str
    received_at: datetime
    size_bytes: int

    def __post_init__(self) -> None:
        _exact(self.receipt_id, "receipt_id", 128)
        _sha(self.content_sha256, "content_sha256")
        _exact(self.original_filename, "original_filename", 500)
        _aware(self.received_at, "received_at")
        if self.size_bytes < 0:
            raise ValueError("size_bytes cannot be negative")


@dataclass(frozen=True, slots=True)
class BulkMappingProof:
    template_id: str
    revision: int
    template_sha256: str
    effective_from: str
    effective_to: str | None
    history_row_version: int
    revision_row_version: int

    def __post_init__(self) -> None:
        _exact(self.template_id, "template_id", 200)
        _sha(self.template_sha256, "template_sha256")
        _exact(self.effective_from, "effective_from", 10)
        if self.effective_to is not None:
            _exact(self.effective_to, "effective_to", 10)
        if min(self.revision, self.history_row_version, self.revision_row_version) < 1:
            raise ValueError("mapping revision and row versions must be positive")


@dataclass(frozen=True, slots=True)
class BulkCandidateProof:
    state: str
    candidate_digest: str
    loadable_row_count: int
    held_row_count: int
    revision_identity_sha256: str
    revision_evidence_sha256: str

    def __post_init__(self) -> None:
        _exact(self.state, "state", 64)
        for name in (
            "candidate_digest",
            "revision_identity_sha256",
            "revision_evidence_sha256",
        ):
            _sha(getattr(self, name), name)
        if self.loadable_row_count < 0 or self.held_row_count < 0:
            raise ValueError("candidate row counts cannot be negative")


@dataclass(frozen=True, slots=True)
class BulkEntrySnapshot:
    entry_id: str
    ordinal: int
    filename: str
    mime_type: str
    size_bytes: int
    upload_sha256: str
    status: BulkEntryStatus
    outcome: BulkEntryOutcome | None
    status_label: str
    message: str
    attempt_count: int
    row_version: int
    receipt: BulkReceiptProof | None
    mapping: BulkMappingProof | None
    candidate: BulkCandidateProof | None
    duplicate_of_entry_id: str | None
    revision_baseline_entry_id: str | None
    issues: tuple[BulkIssue, ...]

    def __post_init__(self) -> None:
        _exact(self.entry_id, "entry_id", 128)
        _exact(self.filename, "filename", 500)
        _exact(self.mime_type, "mime_type", 200)
        _sha(self.upload_sha256, "upload_sha256")
        _exact(self.status_label, "status_label", 100)
        _exact(self.message, "message", 1000)
        if min(self.ordinal, self.size_bytes, self.attempt_count) < 0 or self.row_version < 1:
            raise ValueError("entry counters and row_version are invalid")
        if (self.status == BulkEntryStatus.TERMINAL) != (self.outcome is not None):
            raise ValueError("only terminal entries carry an outcome")
        if self.receipt is not None:
            if self.receipt.content_sha256 != self.upload_sha256:
                raise ValueError("receipt digest must match uploaded bytes")
            if self.receipt.size_bytes != self.size_bytes:
                raise ValueError("receipt size must match uploaded bytes")
            if self.receipt.original_filename != self.filename:
                raise ValueError("receipt filename must match uploaded filename")


@dataclass(frozen=True, slots=True)
class BulkSummary:
    total: int
    staged: int
    processing: int
    candidate_ready: int
    duplicate: int
    variation: int
    mapping_required: int
    scan_failed: int
    identifier_hold: int
    binding_hold: int
    revision_review_required: int
    error: int

    def __post_init__(self) -> None:
        values = (
            self.total,
            self.staged,
            self.processing,
            self.candidate_ready,
            self.duplicate,
            self.variation,
            self.mapping_required,
            self.scan_failed,
            self.identifier_hold,
            self.binding_hold,
            self.revision_review_required,
            self.error,
        )
        if any(value < 0 for value in values):
            raise ValueError("Bulk summary counts cannot be negative")
        terminal = (
            self.candidate_ready
            + self.duplicate
            + self.variation
            + self.mapping_required
            + self.scan_failed
            + self.identifier_hold
            + self.binding_hold
            + self.revision_review_required
            + self.error
        )
        if self.staged + self.processing + terminal != self.total:
            raise ValueError("Bulk summary partitions must equal total")


@dataclass(frozen=True, slots=True)
class BulkLimits:
    max_files: int
    max_file_bytes: int
    max_batch_bytes: int

    def __post_init__(self) -> None:
        if min(self.max_files, self.max_file_bytes, self.max_batch_bytes) < 1:
            raise ValueError("Bulk limits must be positive")
        if self.max_batch_bytes < self.max_file_bytes:
            raise ValueError("batch byte limit cannot be smaller than one file limit")


@dataclass(frozen=True, slots=True)
class BulkCapabilities:
    durable_staging: bool = True
    approved_template_reuse: bool = True
    per_file_approval: bool = False
    finalize_available: bool = False
    auto_long: bool = False
    auto_valid: bool = False
    auto_replaced: bool = False
    auto_revision: bool = False
    ai_used: bool = False

    def __post_init__(self) -> None:
        if not self.durable_staging or not self.approved_template_reuse:
            raise ValueError("Bulk staging capabilities cannot be disabled")
        if any(
            (
                self.per_file_approval,
                self.finalize_available,
                self.auto_long,
                self.auto_valid,
                self.auto_replaced,
                self.auto_revision,
                self.ai_used,
            )
        ):
            raise ValueError("Bulk staging cannot expose downstream automatic actions")


@dataclass(frozen=True, slots=True)
class BulkBatchSnapshot:
    batch_id: str
    project_key: str
    supplier_scope: str
    idempotency_key: str
    status: BulkBatchStatus
    status_label: str
    message: str
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None
    terminal: bool
    poll_after_ms: int | None
    replayed: bool
    limits: BulkLimits
    summary: BulkSummary
    entries: tuple[BulkEntrySnapshot, ...]
    capabilities: BulkCapabilities = BulkCapabilities()

    def __post_init__(self) -> None:
        for name in ("batch_id", "project_key", "supplier_scope", "idempotency_key"):
            _exact(getattr(self, name), name, 200)
        _exact(self.status_label, "status_label", 100)
        _exact(self.message, "message", 1000)
        _aware(self.created_at, "created_at")
        _aware(self.updated_at, "updated_at")
        if self.finished_at is not None:
            _aware(self.finished_at, "finished_at")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if self.finished_at is not None and self.finished_at < self.created_at:
            raise ValueError("finished_at cannot precede created_at")
        terminal_status = self.status in {
            BulkBatchStatus.COMPLETED,
            BulkBatchStatus.COMPLETED_WITH_EXCEPTIONS,
            BulkBatchStatus.FAILED,
        }
        if self.terminal != terminal_status:
            raise ValueError("terminal flag disagrees with batch status")
        if self.terminal != (self.poll_after_ms is None):
            raise ValueError("only nonterminal batches expose a poll delay")
        if self.poll_after_ms is not None and self.poll_after_ms < 1:
            raise ValueError("poll_after_ms must be positive")
        if len(self.entries) != self.summary.total:
            raise ValueError("summary total must equal entry count")
        if tuple(entry.ordinal for entry in self.entries) != tuple(range(len(self.entries))):
            raise ValueError("entries must use contiguous deterministic ordinals")


def _exact(value: str, name: str, limit: int) -> None:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > limit:
        raise ValueError(f"{name} must be an exact nonblank string")


def _sha(value: str, name: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256")


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
