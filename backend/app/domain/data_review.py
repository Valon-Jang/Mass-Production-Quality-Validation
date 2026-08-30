"""Explicit review contracts for deciding trust status on one persisted result.

The candidate is evidence only.  Building it never promotes data, converts a
unit, standardizes a value, calls AI, or changes a supplier judgment.  A later
ADMIN command may apply one explicitly selected trust status atomically.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum

from app.domain.long_format import LongDataStatus, MeasurementMode, SpecEvaluationStatus
from app.domain.mapping import SystemJudgmentStatus
from app.domain.master_config import InspectionItemDisposition


class ReviewCandidateState(StrEnum):
    EVALUATED = "EVALUATED"
    REVIEW_ONLY = "REVIEW_ONLY"
    INELIGIBLE = "INELIGIBLE"


class SystemJudgment(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


class SampleComparison(StrEnum):
    WITHIN_LIMITS = "WITHIN_LIMITS"
    BELOW_LSL = "BELOW_LSL"
    ABOVE_USL = "ABOVE_USL"
    NOT_EVALUATED = "NOT_EVALUATED"


class ReviewIssueCode(StrEnum):
    RESULT_NOT_PENDING = "RESULT_NOT_PENDING"
    RESULT_HELD = "RESULT_HELD"
    RESULT_PROJECTION_NOT_EMPTY = "RESULT_PROJECTION_NOT_EMPTY"
    MEASUREMENT_STATUS_MISMATCH = "MEASUREMENT_STATUS_MISMATCH"
    ITEM_NOT_MAPPED = "ITEM_NOT_MAPPED"
    ITEM_CANDIDATE = "ITEM_CANDIDATE"
    SOURCE_EVIDENCE_INTEGRITY = "SOURCE_EVIDENCE_INTEGRITY"
    BINDING_EVIDENCE_INTEGRITY = "BINDING_EVIDENCE_INTEGRITY"
    CANDIDATE_EVIDENCE_INTEGRITY = "CANDIDATE_EVIDENCE_INTEGRITY"
    MEASUREMENT_EVIDENCE_INTEGRITY = "MEASUREMENT_EVIDENCE_INTEGRITY"
    MASTER_NOT_FOUND = "MASTER_NOT_FOUND"
    MASTER_AMBIGUOUS = "MASTER_AMBIGUOUS"
    MASTER_EVIDENCE_INTEGRITY = "MASTER_EVIDENCE_INTEGRITY"
    INSPECTION_DATE_MISSING = "INSPECTION_DATE_MISSING"
    UNIT_EVIDENCE_MISSING = "UNIT_EVIDENCE_MISSING"
    UNIT_EVIDENCE_NOT_EXACT_TEXT = "UNIT_EVIDENCE_NOT_EXACT_TEXT"
    UNIT_MISMATCH = "UNIT_MISMATCH"
    NON_NUMERIC_MEASUREMENT = "NON_NUMERIC_MEASUREMENT"
    NONFINITE_MEASUREMENT = "NONFINITE_MEASUREMENT"
    FORMULA_MEASUREMENT = "FORMULA_MEASUREMENT"
    QUALITATIVE_REVIEW_REQUIRED = "QUALITATIVE_REVIEW_REQUIRED"
    ZERO_MEASUREMENTS = "ZERO_MEASUREMENTS"


@dataclass(frozen=True, slots=True, order=True)
class ReviewIssue:
    code: ReviewIssueCode
    detail: str

    def __post_init__(self) -> None:
        _require_exact(self.detail, "issue detail")


@dataclass(frozen=True, slots=True)
class SourceUnitEvidence:
    sheet_name: str
    coordinate: str
    raw_value: str
    cell_evidence_json: str
    cell_evidence_sha256: str

    def __post_init__(self) -> None:
        for name in ("sheet_name", "coordinate", "cell_evidence_json"):
            _require_exact(getattr(self, name), name)
        if not isinstance(self.raw_value, str):
            raise ValueError("raw_value must preserve one exact source string")
        _require_sha256(self.cell_evidence_sha256, "cell_evidence_sha256")
        if _text_sha256(self.cell_evidence_json) != self.cell_evidence_sha256:
            raise ValueError("unit cell evidence digest does not match")


@dataclass(frozen=True, slots=True)
class ReviewMeasurementEvidence:
    measurement_id: str
    sample_ordinal: int
    source_cell: str
    row_version: int
    evidence_sha256: str
    raw_value_json: str
    raw_numeric_value_json: str | None
    raw_qualitative_value: str | None
    formula_flag: bool
    numeric_value: Decimal | None

    def __post_init__(self) -> None:
        for name in ("measurement_id", "source_cell", "raw_value_json"):
            _require_exact(getattr(self, name), name)
        if self.sample_ordinal < 1 or self.row_version < 1:
            raise ValueError("measurement ordinal and row_version must be positive")
        _require_sha256(self.evidence_sha256, "evidence_sha256")
        if self.numeric_value is not None and not self.numeric_value.is_finite():
            raise ValueError("numeric_value must be finite")


@dataclass(frozen=True, slots=True)
class HistoricalMasterEvidence:
    project_key: str
    canonical_item_key: str
    history_id: str
    revision_id: str
    revision_number: int
    history_row_version: int
    revision_row_version: int
    payload_sha256: str
    declared_effective_from: date
    declared_effective_to: date | None
    resolved_effective_to: date | None
    target: Decimal | None
    lsl: Decimal | None
    usl: Decimal | None
    unit: str
    external_spec_revision: str

    def __post_init__(self) -> None:
        for name in (
            "project_key",
            "canonical_item_key",
            "history_id",
            "revision_id",
            "unit",
            "external_spec_revision",
        ):
            _require_exact(getattr(self, name), name)
        if min(self.revision_number, self.history_row_version, self.revision_row_version) < 1:
            raise ValueError("Master revision and row versions must be positive")
        _require_sha256(self.payload_sha256, "payload_sha256")
        if self.declared_effective_to is not None and (
            self.declared_effective_to < self.declared_effective_from
        ):
            raise ValueError("declared Master effectivity is invalid")
        if self.resolved_effective_to is not None and (
            self.resolved_effective_to < self.declared_effective_from
            or (
                self.declared_effective_to is not None
                and self.resolved_effective_to > self.declared_effective_to
            )
        ):
            raise ValueError("resolved Master effectivity is invalid")
        for value in (self.target, self.lsl, self.usl):
            if value is not None and not value.is_finite():
                raise ValueError("Master numeric evidence must be finite")
        if self.lsl is None and self.usl is None:
            raise ValueError("Master evidence requires at least one numeric limit")


@dataclass(frozen=True, slots=True)
class DataReviewBasis:
    project_key: str
    result_id: str
    source_file_id: str
    lot_id: str
    source_content_sha256: str
    inspection_date: date | None
    data_status: LongDataStatus
    result_row_version: int
    current_system_judgment: str | None
    current_system_judgment_status: SystemJudgmentStatus
    current_spec_evaluation_status: SpecEvaluationStatus
    source_evidence_sha256: str
    binding_snapshot_sha256: str | None
    candidate_snapshot_sha256: str
    canonical_item_key: str | None
    item_disposition: InspectionItemDisposition | None
    item_row_version: int | None
    measurement_mode: MeasurementMode | None
    source_unit: SourceUnitEvidence | None
    measurements: tuple[ReviewMeasurementEvidence, ...]
    masters: tuple[HistoricalMasterEvidence, ...]
    blocking_issues: tuple[ReviewIssue, ...]
    review_only_issues: tuple[ReviewIssue, ...]

    def __post_init__(self) -> None:
        for name in ("project_key", "result_id", "source_file_id", "lot_id"):
            _require_exact(getattr(self, name), name)
        for name in (
            "source_content_sha256",
            "source_evidence_sha256",
            "candidate_snapshot_sha256",
        ):
            _require_sha256(getattr(self, name), name)
        if self.binding_snapshot_sha256 is not None:
            _require_sha256(self.binding_snapshot_sha256, "binding_snapshot_sha256")
        if self.result_row_version < 1:
            raise ValueError("result_row_version must be positive")
        if self.item_disposition is not None and self.canonical_item_key is None:
            raise ValueError("resolved item disposition requires canonical item identity")
        if (self.item_disposition is None) != (self.item_row_version is None):
            raise ValueError("item disposition and row_version must be present together")
        if self.item_row_version is not None and self.item_row_version < 1:
            raise ValueError("item_row_version must be positive")
        measurement_ids = tuple(item.measurement_id for item in self.measurements)
        ordinals = tuple(item.sample_ordinal for item in self.measurements)
        if len(set(measurement_ids)) != len(measurement_ids):
            raise ValueError("measurement IDs must be unique")
        if len(set(ordinals)) != len(ordinals):
            raise ValueError("measurement ordinals must be unique")
        if (
            tuple(
                sorted(
                    self.measurements,
                    key=lambda item: (item.sample_ordinal, item.measurement_id),
                )
            )
            != self.measurements
        ):
            raise ValueError("measurements must be deterministically ordered")
        if (
            tuple(
                sorted(
                    self.masters,
                    key=lambda item: (item.revision_number, item.revision_id),
                )
            )
            != self.masters
        ):
            raise ValueError("Master candidates must be deterministically ordered")
        if any(
            master.project_key != self.project_key
            or master.canonical_item_key != self.canonical_item_key
            for master in self.masters
        ):
            raise ValueError("Master candidates must match the candidate project and item")
        if tuple(sorted(self.blocking_issues)) != self.blocking_issues:
            raise ValueError("blocking issues must be deterministically sorted")
        if tuple(sorted(self.review_only_issues)) != self.review_only_issues:
            raise ValueError("review-only issues must be deterministically sorted")


@dataclass(frozen=True, slots=True)
class ReviewedSample:
    evidence: ReviewMeasurementEvidence
    comparison: SampleComparison


@dataclass(frozen=True, slots=True)
class DataReviewCandidate:
    basis: DataReviewBasis
    state: ReviewCandidateState
    issues: tuple[ReviewIssue, ...]
    selected_master: HistoricalMasterEvidence | None
    samples: tuple[ReviewedSample, ...]
    proposed_system_judgment: SystemJudgment | None
    proposed_system_judgment_status: SystemJudgmentStatus
    proposed_spec_evaluation_status: SpecEvaluationStatus
    allowed_target_statuses: tuple[LongDataStatus, ...]
    candidate_contract_version: str = "data-review-candidate-v1"

    def __post_init__(self) -> None:
        _require_exact(self.candidate_contract_version, "candidate_contract_version")
        if tuple(sorted(self.issues)) != self.issues:
            raise ValueError("candidate issues must be deterministically sorted")
        if self.state == ReviewCandidateState.EVALUATED:
            if (
                self.selected_master is None
                or self.proposed_system_judgment is None
                or self.proposed_system_judgment_status != SystemJudgmentStatus.EVALUATED
                or self.proposed_spec_evaluation_status
                != SpecEvaluationStatus.EVALUATED_APPROVED_MASTER
            ):
                raise ValueError("an EVALUATED candidate requires exact Master judgment evidence")
            if not self.samples or any(
                sample.comparison == SampleComparison.NOT_EVALUATED for sample in self.samples
            ):
                raise ValueError("an EVALUATED candidate requires every sample comparison")
        else:
            if (
                self.selected_master is not None
                or self.proposed_system_judgment is not None
                or self.proposed_system_judgment_status != SystemJudgmentStatus.NOT_EVALUATED
                or self.proposed_spec_evaluation_status != SpecEvaluationStatus.NOT_EVALUATED
            ):
                raise ValueError("a non-evaluated candidate cannot claim Master judgment")
            if any(sample.comparison != SampleComparison.NOT_EVALUATED for sample in self.samples):
                raise ValueError("a non-evaluated candidate cannot contain comparisons")
        if self.state == ReviewCandidateState.INELIGIBLE and self.allowed_target_statuses:
            raise ValueError("an INELIGIBLE candidate cannot allow a transition")
        if (
            self.state != ReviewCandidateState.EVALUATED
            and LongDataStatus.VALID in self.allowed_target_statuses
        ):
            raise ValueError("only an EVALUATED candidate may allow VALID")
        allowed_decisions = {
            LongDataStatus.VALID,
            LongDataStatus.SUSPECT,
            LongDataStatus.EXCLUDED,
        }
        if not set(self.allowed_target_statuses).issubset(allowed_decisions):
            raise ValueError("candidate exposes an unsupported status decision")
        if len(set(self.allowed_target_statuses)) != len(self.allowed_target_statuses):
            raise ValueError("allowed target statuses must not contain duplicates")
        if tuple(sorted(self.allowed_target_statuses, key=lambda item: item.value)) != (
            self.allowed_target_statuses
        ):
            raise ValueError("allowed target statuses must be deterministically ordered")
        if self.selected_master is not None and (
            self.selected_master.project_key != self.basis.project_key
            or self.selected_master.canonical_item_key != self.basis.canonical_item_key
            or self.selected_master not in self.basis.masters
        ):
            raise ValueError("selected Master must be one exact in-scope basis candidate")

    @property
    def candidate_sha256(self) -> str:
        return canonical_json_sha256(serialize_data_review_candidate(self))

    @property
    def official_values_created(self) -> bool:
        return False

    @property
    def unit_conversion_performed(self) -> bool:
        return False

    @property
    def ai_used(self) -> bool:
        return False

    @property
    def statistics_calculated(self) -> bool:
        return False


def serialize_data_review_candidate(candidate: DataReviewCandidate) -> dict[str, object]:
    basis = candidate.basis
    return {
        "candidate_contract_version": candidate.candidate_contract_version,
        "project_key": basis.project_key,
        "result": {
            "id": basis.result_id,
            "source_file_id": basis.source_file_id,
            "lot_id": basis.lot_id,
            "source_content_sha256": basis.source_content_sha256,
            "inspection_date": (
                basis.inspection_date.isoformat() if basis.inspection_date is not None else None
            ),
            "data_status": basis.data_status.value,
            "row_version": basis.result_row_version,
            "current_system_judgment": basis.current_system_judgment,
            "current_system_judgment_status": basis.current_system_judgment_status.value,
            "current_spec_evaluation_status": basis.current_spec_evaluation_status.value,
            "source_evidence_sha256": basis.source_evidence_sha256,
            "binding_snapshot_sha256": basis.binding_snapshot_sha256,
            "candidate_snapshot_sha256": basis.candidate_snapshot_sha256,
        },
        "item": {
            "canonical_item_key": basis.canonical_item_key,
            "disposition": (
                basis.item_disposition.value if basis.item_disposition is not None else None
            ),
            "row_version": basis.item_row_version,
            "measurement_mode": (
                basis.measurement_mode.value if basis.measurement_mode is not None else None
            ),
        },
        "unit": _serialize_unit(basis.source_unit),
        "master_candidates": [_serialize_master(master) for master in basis.masters],
        "state": candidate.state.value,
        "issues": [
            {"code": issue.code.value, "detail": issue.detail} for issue in candidate.issues
        ],
        "selected_master": _serialize_master(candidate.selected_master),
        "samples": [
            {
                "measurement_id": sample.evidence.measurement_id,
                "sample_ordinal": sample.evidence.sample_ordinal,
                "source_cell": sample.evidence.source_cell,
                "row_version": sample.evidence.row_version,
                "evidence_sha256": sample.evidence.evidence_sha256,
                "raw_value_json": sample.evidence.raw_value_json,
                "raw_numeric_value_json": sample.evidence.raw_numeric_value_json,
                "raw_qualitative_value": sample.evidence.raw_qualitative_value,
                "formula_flag": sample.evidence.formula_flag,
                "numeric_value": _decimal_text(sample.evidence.numeric_value),
                "comparison": sample.comparison.value,
            }
            for sample in candidate.samples
        ],
        "proposed_system_judgment": (
            candidate.proposed_system_judgment.value
            if candidate.proposed_system_judgment is not None
            else None
        ),
        "proposed_system_judgment_status": candidate.proposed_system_judgment_status.value,
        "proposed_spec_evaluation_status": candidate.proposed_spec_evaluation_status.value,
        "allowed_target_statuses": [value.value for value in candidate.allowed_target_statuses],
        "official_values_created": False,
        "unit_conversion_performed": False,
        "ai_used": False,
        "statistics_calculated": False,
    }


def canonical_json_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _serialize_unit(value: SourceUnitEvidence | None) -> dict[str, object] | None:
    if value is None:
        return None
    return {
        "sheet_name": value.sheet_name,
        "coordinate": value.coordinate,
        "raw_value": value.raw_value,
        "cell_evidence_json": value.cell_evidence_json,
        "cell_evidence_sha256": value.cell_evidence_sha256,
    }


def _serialize_master(value: HistoricalMasterEvidence | None) -> dict[str, object] | None:
    if value is None:
        return None
    return {
        "project_key": value.project_key,
        "canonical_item_key": value.canonical_item_key,
        "history_id": value.history_id,
        "revision_id": value.revision_id,
        "revision_number": value.revision_number,
        "history_row_version": value.history_row_version,
        "revision_row_version": value.revision_row_version,
        "payload_sha256": value.payload_sha256,
        "declared_effective_from": value.declared_effective_from.isoformat(),
        "declared_effective_to": (
            value.declared_effective_to.isoformat()
            if value.declared_effective_to is not None
            else None
        ),
        "resolved_effective_to": (
            value.resolved_effective_to.isoformat()
            if value.resolved_effective_to is not None
            else None
        ),
        "target": _decimal_text(value.target),
        "lsl": _decimal_text(value.lsl),
        "usl": _decimal_text(value.usl),
        "unit": value.unit,
        "external_spec_revision": value.external_spec_revision,
    }


def _decimal_text(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _require_sha256(value: str, name: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")


def _require_exact(value: str, name: str) -> None:
    if not value or value != value.strip():
        raise ValueError(f"{name} must be an exact non-blank value")
