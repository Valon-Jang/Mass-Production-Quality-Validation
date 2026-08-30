"""Immutable evidence contracts for one explicit result-replacement link.

The candidate is descriptive evidence only.  It never promotes a Long row,
matches samples, recalculates a value, or infers that one revision is better.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from app.domain.data_review import ReviewCandidateState, SystemJudgment
from app.domain.long_format import LongDataStatus

REPLACEMENT_CANDIDATE_CONTRACT_VERSION = "result-replacement-candidate-v1"
REPLACEMENT_CHAIN_LIMIT = 100
REPLACEMENT_MEASUREMENT_PROOF_LIMIT = 100


class ReplacementIssueCode(StrEnum):
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    PREDECESSOR_NOT_REPLACEABLE = "PREDECESSOR_NOT_REPLACEABLE"
    SUCCESSOR_NOT_PENDING = "SUCCESSOR_NOT_PENDING"
    SUCCESSOR_NOT_EVALUATED = "SUCCESSOR_NOT_EVALUATED"
    SUCCESSOR_VALID_NOT_ALLOWED = "SUCCESSOR_VALID_NOT_ALLOWED"
    EXISTING_OUTGOING_REPLACEMENT = "EXISTING_OUTGOING_REPLACEMENT"
    EXISTING_INCOMING_REPLACEMENT = "EXISTING_INCOMING_REPLACEMENT"
    CHAIN_LIMIT_REACHED = "CHAIN_LIMIT_REACHED"
    CHAIN_CYCLE = "CHAIN_CYCLE"
    EVIDENCE_INTEGRITY = "EVIDENCE_INTEGRITY"


class ReplacementDifferenceCode(StrEnum):
    JUDGMENT_CHANGED = "JUDGMENT_CHANGED"
    NG_TO_PASS = "NG_TO_PASS"
    SOURCE_FIELD_CHANGED = "SOURCE_FIELD_CHANGED"
    MEASUREMENT_SET_CHANGED = "MEASUREMENT_SET_CHANGED"
    SAMPLE_COUNT_CHANGED = "SAMPLE_COUNT_CHANGED"
    INSPECTION_DATE_CHANGED = "INSPECTION_DATE_CHANGED"
    NOT_EVALUABLE = "NOT_EVALUABLE"


@dataclass(frozen=True, slots=True, order=True)
class ReplacementIssue:
    code: ReplacementIssueCode
    message: str

    def __post_init__(self) -> None:
        _require_exact(self.message, "issue message")


@dataclass(frozen=True, slots=True, order=True)
class ReplacementDifference:
    code: ReplacementDifferenceCode
    field: str
    predecessor_value: str | None
    successor_value: str | None

    def __post_init__(self) -> None:
        _require_exact(self.field, "difference field")
        for value in (self.predecessor_value, self.successor_value):
            if value is not None and len(value) > 500:
                raise ValueError("difference values must be bounded")


@dataclass(frozen=True, slots=True, order=True)
class ReplacementMeasurementProof:
    measurement_id: str
    sample_ordinal: int
    source_cell: str
    data_status: LongDataStatus
    row_version: int
    evidence_sha256: str

    def __post_init__(self) -> None:
        _require_exact(self.measurement_id, "measurement_id")
        _require_exact(self.source_cell, "source_cell")
        _require_sha256(self.evidence_sha256, "evidence_sha256")
        if self.sample_ordinal < 1 or self.row_version < 1:
            raise ValueError("measurement ordinal and row_version must be positive")


@dataclass(frozen=True, slots=True)
class ReplacementResultProof:
    result_id: str
    source_file_id: str
    lot_id: str
    data_status: LongDataStatus
    row_version: int
    original_data_status_transition_id: str
    original_decision_candidate_sha256: str
    system_judgment: SystemJudgment | None
    measurement_set_sha256: str
    measurements: tuple[ReplacementMeasurementProof, ...]

    def __post_init__(self) -> None:
        for name in (
            "result_id",
            "source_file_id",
            "lot_id",
            "original_data_status_transition_id",
        ):
            _require_exact(getattr(self, name), name)
        _require_sha256(
            self.original_decision_candidate_sha256,
            "original_decision_candidate_sha256",
        )
        _require_sha256(self.measurement_set_sha256, "measurement_set_sha256")
        if self.data_status not in {LongDataStatus.VALID, LongDataStatus.SUSPECT}:
            raise ValueError("predecessor must be VALID or SUSPECT")
        if self.row_version < 2:
            raise ValueError("a decided predecessor row_version must be at least two")
        _validate_measurements(self.measurements, expected_status=self.data_status)
        if measurement_set_sha256(self.measurements) != self.measurement_set_sha256:
            raise ValueError("predecessor measurement-set digest does not match")

    @property
    def measurement_count(self) -> int:
        return len(self.measurements)


@dataclass(frozen=True, slots=True)
class ReplacementSuccessorProof:
    result_id: str
    source_file_id: str
    lot_id: str
    data_status: LongDataStatus
    row_version: int
    data_review_state: ReviewCandidateState
    data_review_candidate_sha256: str
    proposed_system_judgment: SystemJudgment | None
    selected_master_history_id: str | None
    selected_master_revision_id: str | None
    selected_master_payload_sha256: str | None
    item_row_version: int | None
    measurement_set_sha256: str
    measurements: tuple[ReplacementMeasurementProof, ...]

    def __post_init__(self) -> None:
        for name in ("result_id", "source_file_id", "lot_id"):
            _require_exact(getattr(self, name), name)
        _require_sha256(self.data_review_candidate_sha256, "data_review_candidate_sha256")
        _require_sha256(self.measurement_set_sha256, "measurement_set_sha256")
        if self.data_status != LongDataStatus.PENDING:
            raise ValueError("successor must remain PENDING before the paired command")
        if self.row_version < 1:
            raise ValueError("successor row_version must be positive")
        _validate_measurements(self.measurements, expected_status=LongDataStatus.PENDING)
        if measurement_set_sha256(self.measurements) != self.measurement_set_sha256:
            raise ValueError("successor measurement-set digest does not match")
        if self.data_review_state == ReviewCandidateState.EVALUATED:
            if (
                self.proposed_system_judgment is None
                or self.selected_master_history_id is None
                or self.selected_master_revision_id is None
                or self.selected_master_payload_sha256 is None
                or self.item_row_version is None
            ):
                raise ValueError("an evaluated successor requires exact Master/item evidence")
            _require_sha256(
                self.selected_master_payload_sha256,
                "selected_master_payload_sha256",
            )
        elif any(
            value is not None
            for value in (
                self.proposed_system_judgment,
                self.selected_master_history_id,
                self.selected_master_revision_id,
                self.selected_master_payload_sha256,
            )
        ):
            raise ValueError("a non-evaluated successor cannot claim Master judgment")
        for value in (self.selected_master_history_id, self.selected_master_revision_id):
            if value is not None:
                _require_exact(value, "selected Master identity")
        if self.item_row_version is not None and self.item_row_version < 1:
            raise ValueError("item_row_version must be positive")

    @property
    def measurement_count(self) -> int:
        return len(self.measurements)


@dataclass(frozen=True, slots=True)
class ReplacementIdentityProof:
    canonical_model_key: str
    canonical_model_part_key: str
    canonical_supplier_key: str
    canonical_item_key: str
    source_lot_text: str

    def __post_init__(self) -> None:
        for name in (
            "canonical_model_key",
            "canonical_model_part_key",
            "canonical_supplier_key",
            "canonical_item_key",
            "source_lot_text",
        ):
            _require_exact(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class ReplacementCapabilities:
    explicit_admin_only: bool = True
    atomic_successor_valid: bool = True
    automatic_replacement: bool = False
    automatic_valid: bool = False
    calculations: bool = False
    ai_used: bool = False
    measurement_pairing: bool = False

    def __post_init__(self) -> None:
        if not self.explicit_admin_only or not self.atomic_successor_valid:
            raise ValueError("replacement capabilities must preserve explicit atomic review")
        if any(
            (
                self.automatic_replacement,
                self.automatic_valid,
                self.calculations,
                self.ai_used,
                self.measurement_pairing,
            )
        ):
            raise ValueError("automatic replacement capabilities are forbidden")


@dataclass(frozen=True, slots=True)
class ResultReplacementCandidate:
    project_key: str
    predecessor: ReplacementResultProof
    successor: ReplacementSuccessorProof
    identity: ReplacementIdentityProof
    differences: tuple[ReplacementDifference, ...]
    issues: tuple[ReplacementIssue, ...]
    candidate_contract_version: str = REPLACEMENT_CANDIDATE_CONTRACT_VERSION
    capabilities: ReplacementCapabilities = ReplacementCapabilities()

    def __post_init__(self) -> None:
        _require_exact(self.project_key, "project_key")
        if self.predecessor.result_id == self.successor.result_id:
            raise ValueError("predecessor and successor must be distinct results")
        if tuple(sorted(set(self.differences))) != self.differences:
            raise ValueError("differences must be unique and deterministically sorted")
        if tuple(sorted(set(self.issues))) != self.issues:
            raise ValueError("issues must be unique and deterministically sorted")

    @property
    def can_replace(self) -> bool:
        return not self.issues

    @property
    def candidate_sha256(self) -> str:
        return canonical_json_sha256(serialize_result_replacement_candidate(self))


@dataclass(frozen=True, slots=True)
class PersistedReplacementDecision:
    replacement_id: str
    project_key: str
    predecessor_result_id: str
    successor_result_id: str
    predecessor_result_row_version: int
    successor_result_row_version: int
    successor_data_status_transition_id: str
    predecessor_measurement_count: int
    successor_measurement_count: int
    candidate_sha256: str
    intent_sha256: str
    decided_by: str
    decided_at: datetime
    reason: str
    replayed: bool
    capabilities: ReplacementCapabilities = ReplacementCapabilities()

    def __post_init__(self) -> None:
        for name in (
            "replacement_id",
            "project_key",
            "predecessor_result_id",
            "successor_result_id",
            "successor_data_status_transition_id",
            "decided_by",
            "reason",
        ):
            _require_exact(getattr(self, name), name)
        _require_sha256(self.candidate_sha256, "candidate_sha256")
        _require_sha256(self.intent_sha256, "intent_sha256")
        if self.decided_at.tzinfo is None or self.decided_at.utcoffset() is None:
            raise ValueError("decided_at must be timezone-aware")
        if (
            min(
                self.predecessor_result_row_version,
                self.successor_result_row_version,
                self.predecessor_measurement_count,
                self.successor_measurement_count,
            )
            < 1
        ):
            raise ValueError("replacement versions and measurement counts must be positive")


def measurement_set_sha256(values: tuple[ReplacementMeasurementProof, ...]) -> str:
    return canonical_json_sha256(
        [
            {
                "measurement_id": value.measurement_id,
                "sample_ordinal": value.sample_ordinal,
                "source_cell": value.source_cell,
                "data_status": value.data_status.value,
                "row_version": value.row_version,
                "evidence_sha256": value.evidence_sha256,
            }
            for value in values
        ]
    )


def serialize_result_replacement_candidate(
    candidate: ResultReplacementCandidate,
) -> dict[str, object]:
    return {
        "candidate_contract_version": candidate.candidate_contract_version,
        "project_key": candidate.project_key,
        "predecessor": _serialize_predecessor(candidate.predecessor),
        "successor": _serialize_successor(candidate.successor),
        "identity": {
            "canonical_model_key": candidate.identity.canonical_model_key,
            "canonical_model_part_key": candidate.identity.canonical_model_part_key,
            "canonical_supplier_key": candidate.identity.canonical_supplier_key,
            "canonical_item_key": candidate.identity.canonical_item_key,
            "source_lot_text": candidate.identity.source_lot_text,
        },
        "differences": [
            {
                "code": value.code.value,
                "field": value.field,
                "predecessor_value": value.predecessor_value,
                "successor_value": value.successor_value,
            }
            for value in candidate.differences
        ],
        "issues": [
            {"code": value.code.value, "message": value.message} for value in candidate.issues
        ],
        "capabilities": {
            "explicit_admin_only": True,
            "atomic_successor_valid": True,
            "automatic_replacement": False,
            "automatic_valid": False,
            "calculations": False,
            "ai_used": False,
            "measurement_pairing": False,
        },
    }


def canonical_json_sha256(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _serialize_predecessor(value: ReplacementResultProof) -> dict[str, object]:
    return {
        "result_id": value.result_id,
        "source_file_id": value.source_file_id,
        "lot_id": value.lot_id,
        "data_status": value.data_status.value,
        "row_version": value.row_version,
        "original_data_status_transition_id": value.original_data_status_transition_id,
        "original_decision_candidate_sha256": value.original_decision_candidate_sha256,
        "system_judgment": value.system_judgment.value if value.system_judgment else None,
        "measurement_count": value.measurement_count,
        "measurement_set_sha256": value.measurement_set_sha256,
    }


def _serialize_successor(value: ReplacementSuccessorProof) -> dict[str, object]:
    return {
        "result_id": value.result_id,
        "source_file_id": value.source_file_id,
        "lot_id": value.lot_id,
        "data_status": value.data_status.value,
        "row_version": value.row_version,
        "data_review_state": value.data_review_state.value,
        "data_review_candidate_sha256": value.data_review_candidate_sha256,
        "proposed_system_judgment": (
            value.proposed_system_judgment.value
            if value.proposed_system_judgment is not None
            else None
        ),
        "selected_master_history_id": value.selected_master_history_id,
        "selected_master_revision_id": value.selected_master_revision_id,
        "selected_master_payload_sha256": value.selected_master_payload_sha256,
        "item_row_version": value.item_row_version,
        "measurement_count": value.measurement_count,
        "measurement_set_sha256": value.measurement_set_sha256,
    }


def _validate_measurements(
    values: tuple[ReplacementMeasurementProof, ...],
    *,
    expected_status: LongDataStatus,
) -> None:
    if not values:
        raise ValueError("a replacement result requires at least one measurement")
    if tuple(sorted(values, key=lambda item: (item.sample_ordinal, item.measurement_id))) != values:
        raise ValueError("replacement measurements must be deterministically ordered")
    ids = tuple(value.measurement_id for value in values)
    ordinals = tuple(value.sample_ordinal for value in values)
    if len(set(ids)) != len(ids) or len(set(ordinals)) != len(ordinals):
        raise ValueError("replacement measurement IDs and ordinals must be unique")
    if any(value.data_status != expected_status for value in values):
        raise ValueError("every measurement must match its result status")


def _require_exact(value: str, name: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be an exact non-blank value")


def _require_sha256(value: str, name: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
