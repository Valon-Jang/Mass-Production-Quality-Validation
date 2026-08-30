"""Read-only Phase 2 initial database data-quality report contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class EvaluationState(StrEnum):
    EVALUATED = "EVALUATED"
    NOT_EVALUATED_BY_PHASE = "NOT_EVALUATED_BY_PHASE"
    BLOCKED_BY_INPUT = "BLOCKED_BY_INPUT"


class FinalizationProofState(StrEnum):
    NOT_REQUESTED = "NOT_REQUESTED"
    PRESENT = "PRESENT"


@dataclass(frozen=True, slots=True)
class DataQualityInventory:
    submitted_file_count: int
    receipt_count: int
    materialized_source_file_count: int
    lot_count: int
    result_count: int
    measurement_count: int

    def __post_init__(self) -> None:
        values = (
            self.submitted_file_count,
            self.receipt_count,
            self.materialized_source_file_count,
            self.lot_count,
            self.result_count,
            self.measurement_count,
        )
        if any(value < 0 for value in values):
            raise ValueError("inventory counts cannot be negative")
        if self.receipt_count > self.submitted_file_count:
            raise ValueError("receipt count cannot exceed submitted files")
        if self.materialized_source_file_count > self.receipt_count:
            raise ValueError("materialized files cannot exceed receipts")


@dataclass(frozen=True, slots=True)
class DataStatusCounts:
    pending: int
    held: int
    valid: int
    suspect: int
    excluded: int
    replaced: int

    def __post_init__(self) -> None:
        if any(value < 0 for value in self.values()):
            raise ValueError("data-status counts cannot be negative")

    def values(self) -> tuple[int, ...]:
        return (
            self.pending,
            self.held,
            self.valid,
            self.suspect,
            self.excluded,
            self.replaced,
        )


@dataclass(frozen=True, slots=True)
class BulkOutcomeCounts:
    candidate_ready: int
    duplicate_candidate: int
    mapping_required: int
    scan_failed: int
    identifier_hold: int
    binding_hold: int
    variation_review_required: int
    revision_review_required: int
    error: int

    def __post_init__(self) -> None:
        if any(value < 0 for value in self.values()):
            raise ValueError("Bulk outcome counts cannot be negative")

    def values(self) -> tuple[int, ...]:
        return (
            self.candidate_ready,
            self.duplicate_candidate,
            self.mapping_required,
            self.scan_failed,
            self.identifier_hold,
            self.binding_hold,
            self.variation_review_required,
            self.revision_review_required,
            self.error,
        )


@dataclass(frozen=True, slots=True)
class BulkDataQualityProof:
    batch_row_version: int
    terminal: bool
    manifest_sha256: str
    terminal_summary_sha256: str | None
    outcome_counts: BulkOutcomeCounts
    unresolved_count: int
    unresolved_entries_sha256: str

    def __post_init__(self) -> None:
        if self.batch_row_version < 1 or self.unresolved_count < 0:
            raise ValueError("Bulk proof counters are invalid")
        _sha(self.manifest_sha256, "manifest_sha256")
        if self.terminal_summary_sha256 is not None:
            _sha(self.terminal_summary_sha256, "terminal_summary_sha256")
        _sha(self.unresolved_entries_sha256, "unresolved_entries_sha256")


@dataclass(frozen=True, slots=True)
class FinalizationDataQualityProof:
    state: FinalizationProofState
    command_id: str | None
    status: str | None
    finalization_digest: str | None
    row_version: int | None
    total: int
    pending: int
    processing: int
    completed: int
    blocked: int
    materialized_job_count: int

    def __post_init__(self) -> None:
        counts = (
            self.total,
            self.pending,
            self.processing,
            self.completed,
            self.blocked,
            self.materialized_job_count,
        )
        if any(value < 0 for value in counts):
            raise ValueError("finalization proof counts cannot be negative")
        if self.pending + self.processing + self.completed + self.blocked != self.total:
            raise ValueError("finalization partitions must equal total")
        if self.materialized_job_count > self.completed:
            raise ValueError("materialized jobs cannot exceed completed entries")
        nullable = (self.command_id, self.status, self.finalization_digest, self.row_version)
        if self.state == FinalizationProofState.NOT_REQUESTED:
            if any(value is not None for value in nullable) or any(counts):
                raise ValueError("not-requested finalization proof must be empty")
        else:
            if any(value is None for value in nullable):
                raise ValueError("present finalization proof is incomplete")
            _exact(self.command_id, "command_id", 120)
            if self.status not in {"QUEUED", "PROCESSING", "COMPLETED", "BLOCKED"}:
                raise ValueError("finalization status is invalid")
            if self.finalization_digest is None:
                raise ValueError("finalization digest is missing")
            _sha(self.finalization_digest, "finalization_digest")
            if self.row_version is None or self.row_version < 1:
                raise ValueError("finalization row version is invalid")


@dataclass(frozen=True, slots=True)
class ReplacementDataQualityProof:
    transition_count: int
    predecessor_result_count: int
    successor_result_count: int
    transitions_sha256: str

    def __post_init__(self) -> None:
        if (
            min(
                self.transition_count,
                self.predecessor_result_count,
                self.successor_result_count,
            )
            < 0
        ):
            raise ValueError("replacement proof counts cannot be negative")
        if (
            self.predecessor_result_count != self.transition_count
            or self.successor_result_count != self.transition_count
        ):
            raise ValueError("replacement endpoints must equal transition count")
        _sha(self.transitions_sha256, "transitions_sha256")


@dataclass(frozen=True, slots=True)
class BulkEntryDataQualityDetail:
    entry_id: str
    ordinal: int
    status: str
    outcome: str | None
    status_code: str
    receipt_id: str | None
    content_sha256: str | None
    issue_count: int
    issues_sha256: str
    duplicate_of_entry_id: str | None
    revision_baseline_entry_id: str | None


@dataclass(frozen=True, slots=True)
class FinalizationEntryDataQualityDetail:
    bulk_entry_id: str
    ordinal: int
    status: str
    error_code: str | None
    long_source_file_id: str | None
    long_ingestion_job_id: str | None
    long_status: str | None
    row_version: int


@dataclass(frozen=True, slots=True)
class ResultDataQualityDetail:
    result_id: str
    lot_id: str
    source_file_id: str
    data_status: str
    measurement_count: int
    row_version: int
    current_data_status_transition_id: str | None
    current_replacement_transition_id: str | None


@dataclass(frozen=True, slots=True)
class ReplacementLinkDataQualityDetail:
    replacement_id: str
    predecessor_result_id: str
    successor_result_id: str
    predecessor_before_status: str
    predecessor_after_status: str
    successor_before_status: str
    successor_after_status: str
    decided_at: str
    candidate_sha256: str


@dataclass(frozen=True, slots=True)
class BoundedDataQualityDetails[DETAIL]:
    total: int
    returned: int
    has_more: bool
    full_set_sha256: str
    items: tuple[DETAIL, ...]

    def __post_init__(self) -> None:
        if min(self.total, self.returned) < 0 or self.returned > self.total:
            raise ValueError("bounded detail counts are invalid")
        if self.returned != len(self.items):
            raise ValueError("returned detail count disagrees with items")
        if self.has_more != (self.returned < self.total):
            raise ValueError("bounded detail has_more flag is invalid")
        _sha(self.full_set_sha256, "full_set_sha256")


@dataclass(frozen=True, slots=True)
class DataQualityDetails:
    bulk_entries: BoundedDataQualityDetails[BulkEntryDataQualityDetail]
    finalization_entries: BoundedDataQualityDetails[FinalizationEntryDataQualityDetail]
    results: BoundedDataQualityDetails[ResultDataQualityDetail]
    replacement_links: BoundedDataQualityDetails[ReplacementLinkDataQualityDetail]


@dataclass(frozen=True, slots=True)
class DataQualityEvaluationScope:
    scope: str
    state: EvaluationState
    finding_count: int | None
    evidence_sha256: str | None
    message: str

    def __post_init__(self) -> None:
        _exact(self.scope, "scope", 120)
        _exact(self.message, "message", 1000)
        if self.state == EvaluationState.EVALUATED:
            if self.finding_count is None or self.finding_count < 0:
                raise ValueError("evaluated scope requires a nonnegative finding count")
            if self.evidence_sha256 is None:
                raise ValueError("evaluated scope requires evidence")
            _sha(self.evidence_sha256, "evidence_sha256")
        elif self.finding_count is not None or self.evidence_sha256 is not None:
            raise ValueError("unevaluated scope cannot imply zero findings or evidence")


@dataclass(frozen=True, slots=True)
class DataQualityCapabilities:
    read_only: bool = True
    bounded_details: bool = True
    official_baseline: bool = False
    initial_database_gate_complete: bool = False
    pass_score: bool = False
    calculations: bool = False
    thresholds: bool = False
    ai_used: bool = False
    scheduler_used: bool = False
    automatic_state_change: bool = False

    def __post_init__(self) -> None:
        if not self.read_only or not self.bounded_details:
            raise ValueError("data-quality report read boundaries cannot be disabled")
        if any(
            (
                self.official_baseline,
                self.initial_database_gate_complete,
                self.pass_score,
                self.calculations,
                self.thresholds,
                self.ai_used,
                self.scheduler_used,
                self.automatic_state_change,
            )
        ):
            raise ValueError("data-quality report cannot expose unimplemented capabilities")


@dataclass(frozen=True, slots=True)
class InitialDataQualityReport:
    report_version: str
    report_sha256: str
    project_key: str
    batch_id: str
    supplier_scope: str
    bulk_status: str
    inventory: DataQualityInventory
    result_status_counts: DataStatusCounts
    measurement_status_counts: DataStatusCounts
    bulk_proof: BulkDataQualityProof
    finalization_proof: FinalizationDataQualityProof
    replacement_proof: ReplacementDataQualityProof
    details: DataQualityDetails
    evaluation_scopes: tuple[DataQualityEvaluationScope, ...]
    capabilities: DataQualityCapabilities = DataQualityCapabilities()

    def __post_init__(self) -> None:
        if self.report_version != "initial-data-quality-report-v1":
            raise ValueError("report version is unsupported")
        _sha(self.report_sha256, "report_sha256")
        _exact(self.project_key, "project_key", 64)
        _exact(self.batch_id, "batch_id", 36)
        _exact(self.supplier_scope, "supplier_scope", 200)
        if self.bulk_status not in {
            "STAGED",
            "PROCESSING",
            "COMPLETED",
            "COMPLETED_WITH_EXCEPTIONS",
            "FAILED",
        }:
            raise ValueError("Bulk status is invalid")
        if sum(self.result_status_counts.values()) != self.inventory.result_count:
            raise ValueError("result status counts must equal inventory")
        if sum(self.measurement_status_counts.values()) != self.inventory.measurement_count:
            raise ValueError("measurement status counts must equal inventory")
        if self.details.bulk_entries.total != self.inventory.submitted_file_count:
            raise ValueError("Bulk detail total must equal submitted files")
        if self.details.results.total != self.inventory.result_count:
            raise ValueError("result detail total must equal inventory")


def _sha(value: str, name: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256")


def _exact(value: str | None, name: str, limit: int) -> None:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > limit:
        raise ValueError(f"{name} must be an exact nonblank value")
