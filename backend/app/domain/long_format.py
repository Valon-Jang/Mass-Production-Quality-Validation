"""Evidence-preserving contracts for a pending Long-format load candidate.

These values are not database rows and never imply VALID data, unit
standardization, specification evaluation, or an official system judgment.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from app.domain.mapping import (
    MAPPING_V2_IDENTIFIER_KINDS,
    SUPPORTED_MAPPING_TEMPLATE_SCHEMA_VERSIONS,
    IdentifierKind,
    IdentifierPreview,
    MappedCellEvidence,
    SystemJudgmentStatus,
)
from app.domain.source_file import SourceFileReceipt
from app.domain.workbook_scan import ScanIssue

type RawNumericValue = int | float | Decimal


class LongCandidateState(StrEnum):
    LOAD_CANDIDATE_READY = "LOAD_CANDIDATE_READY"
    PARTIAL_HOLD = "PARTIAL_HOLD"
    LOAD_HELD = "LOAD_HELD"


class LongRowState(StrEnum):
    LOADABLE_PENDING = "LOADABLE_PENDING"
    ROW_HELD = "ROW_HELD"


class LongDataStatus(StrEnum):
    PENDING = "PENDING"
    HELD = "HELD"
    VALID = "VALID"
    SUSPECT = "SUSPECT"
    EXCLUDED = "EXCLUDED"
    REPLACED = "REPLACED"


class SamplePolicy(StrEnum):
    AT_LEAST_ONE = "AT_LEAST_ONE"
    ZERO_ALLOWED = "ZERO_ALLOWED"


class CanonicalRowBindingStatus(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"


class MeasurementMode(StrEnum):
    NUMERIC = "NUMERIC"
    QUALITATIVE = "QUALITATIVE"
    JUDGMENT_ONLY = "JUDGMENT_ONLY"


class UnitConversionStatus(StrEnum):
    NOT_CONFIGURED = "NOT_CONFIGURED"


class SpecEvaluationStatus(StrEnum):
    NOT_EVALUATED = "NOT_EVALUATED"
    EVALUATED_APPROVED_MASTER = "EVALUATED_APPROVED_MASTER"


class LongIssueScope(StrEnum):
    SOURCE = "SOURCE"
    ROW = "ROW"
    CELL = "CELL"


class LongIssueCode(StrEnum):
    SOURCE_HASH_MISMATCH = "SOURCE_HASH_MISMATCH"
    SOURCE_NAME_MISMATCH = "SOURCE_NAME_MISMATCH"
    SOURCE_SIZE_MISMATCH = "SOURCE_SIZE_MISMATCH"
    PROJECT_SCOPE_CONFLICT = "PROJECT_SCOPE_CONFLICT"
    SUPPLIER_SCOPE_CONFLICT = "SUPPLIER_SCOPE_CONFLICT"
    MODEL_IDENTIFIER_MISSING = "MODEL_IDENTIFIER_MISSING"
    LOT_IDENTIFIER_MISSING = "LOT_IDENTIFIER_MISSING"
    MODEL_CANDIDATE_CONFLICT = "MODEL_CANDIDATE_CONFLICT"
    LOT_CANDIDATE_CONFLICT = "LOT_CANDIDATE_CONFLICT"
    CANONICAL_ROW_BINDING_MISSING = "CANONICAL_ROW_BINDING_MISSING"
    CANONICAL_ROW_BINDING_AMBIGUOUS = "CANONICAL_ROW_BINDING_AMBIGUOUS"
    CANONICAL_ROW_BINDING_SCOPE_CONFLICT = "CANONICAL_ROW_BINDING_SCOPE_CONFLICT"
    CANONICAL_ROW_BINDING_NOT_APPROVED = "CANONICAL_ROW_BINDING_NOT_APPROVED"
    CANONICAL_ROW_BINDING_NOT_EFFECTIVE = "CANONICAL_ROW_BINDING_NOT_EFFECTIVE"
    SOURCE_MODEL_BINDING_CONFLICT = "SOURCE_MODEL_BINDING_CONFLICT"
    CANONICAL_MODEL_CONFLICT = "CANONICAL_MODEL_CONFLICT"
    CANONICAL_SUPPLIER_CONFLICT = "CANONICAL_SUPPLIER_CONFLICT"
    ZERO_SAMPLE_POLICY_REQUIRED = "ZERO_SAMPLE_POLICY_REQUIRED"
    MEASUREMENT_MODE_MISMATCH = "MEASUREMENT_MODE_MISMATCH"
    FORMULA_SAMPLE_NOT_RAW = "FORMULA_SAMPLE_NOT_RAW"
    CALCULATION_REFRESH_REQUIRED = "CALCULATION_REFRESH_REQUIRED"
    NONFINITE_NUMERIC_SAMPLE = "NONFINITE_NUMERIC_SAMPLE"
    INVALID_NUMERIC_SAMPLE = "INVALID_NUMERIC_SAMPLE"
    INVALID_QUALITATIVE_SAMPLE = "INVALID_QUALITATIVE_SAMPLE"
    UNSUPPORTED_SAMPLE_VALUE_KIND = "UNSUPPORTED_SAMPLE_VALUE_KIND"


@dataclass(frozen=True, slots=True, order=True)
class CanonicalRowBindingKey:
    project_key: str
    supplier_scope: str
    template_id: str
    template_revision: int
    row_key: str

    def __post_init__(self) -> None:
        for field_name in (
            "project_key",
            "supplier_scope",
            "template_id",
            "row_key",
        ):
            value = getattr(self, field_name)
            if not value.strip() or value != value.strip():
                raise ValueError(f"{field_name} must be an exact non-blank value")
        if self.template_revision < 1:
            raise ValueError("template_revision must be positive")


@dataclass(frozen=True, slots=True)
class CanonicalRowBinding:
    key: CanonicalRowBindingKey
    binding_revision: int
    status: CanonicalRowBindingStatus
    approved_by: str | None
    approved_at: datetime | None
    effective_from: date
    effective_to: date | None
    source_model_values: tuple[str, ...]
    canonical_model_key: str
    canonical_supplier_key: str
    canonical_model_part_key: str
    canonical_item_key: str
    sample_policy: SamplePolicy
    measurement_mode: MeasurementMode

    def __post_init__(self) -> None:
        if self.binding_revision < 1:
            raise ValueError("binding_revision must be positive")
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("binding effective_to must not precede effective_from")
        if self.status == CanonicalRowBindingStatus.APPROVED:
            if self.approved_by is None or not self.approved_by.strip():
                raise ValueError("approved binding requires an approver")
            if self.approved_by != self.approved_by.strip():
                raise ValueError("binding approver must be an exact value")
            if self.approved_at is None or self.approved_at.utcoffset() is None:
                raise ValueError("approved binding requires a timezone-aware approval time")
        elif self.approved_by is not None or self.approved_at is not None:
            raise ValueError("draft binding must not contain approval metadata")
        judgment_only = self.measurement_mode == MeasurementMode.JUDGMENT_ONLY
        zero_allowed = self.sample_policy == SamplePolicy.ZERO_ALLOWED
        if judgment_only != zero_allowed:
            raise ValueError("JUDGMENT_ONLY and ZERO_ALLOWED must be configured together")
        if not self.source_model_values:
            raise ValueError("source_model_values must contain at least one exact model value")
        if len(set(self.source_model_values)) != len(self.source_model_values):
            raise ValueError("source_model_values must not contain duplicates")
        if any(not value.strip() or value != value.strip() for value in self.source_model_values):
            raise ValueError("source_model_values must contain exact non-blank values")
        for field_name in (
            "canonical_model_key",
            "canonical_supplier_key",
            "canonical_model_part_key",
            "canonical_item_key",
        ):
            value = getattr(self, field_name)
            if not value.strip() or value != value.strip():
                raise ValueError(f"{field_name} must be an exact non-blank value")

    @property
    def signature(self) -> CanonicalRowBindingSignature:
        """Return every value that can affect exact row binding semantics."""

        return CanonicalRowBindingSignature(
            key=self.key,
            binding_revision=self.binding_revision,
            status=self.status,
            approved_by=self.approved_by,
            approved_at=self.approved_at,
            effective_from=self.effective_from,
            effective_to=self.effective_to,
            source_model_values=self.source_model_values,
            canonical_model_key=self.canonical_model_key,
            canonical_supplier_key=self.canonical_supplier_key,
            canonical_model_part_key=self.canonical_model_part_key,
            canonical_item_key=self.canonical_item_key,
            sample_policy=self.sample_policy,
            measurement_mode=self.measurement_mode,
        )


@dataclass(frozen=True, slots=True)
class CanonicalRowBindingSignature:
    """Immutable, complete signature for one versioned row binding."""

    key: CanonicalRowBindingKey
    binding_revision: int
    status: CanonicalRowBindingStatus
    approved_by: str | None
    approved_at: datetime | None
    effective_from: date
    effective_to: date | None
    source_model_values: tuple[str, ...]
    canonical_model_key: str
    canonical_supplier_key: str
    canonical_model_part_key: str
    canonical_item_key: str
    sample_policy: SamplePolicy
    measurement_mode: MeasurementMode

    @property
    def version_parts(self) -> tuple[object, ...]:
        return (
            self.key.project_key,
            self.key.supplier_scope,
            self.key.template_id,
            self.key.template_revision,
            self.key.row_key,
            self.binding_revision,
            self.status.value,
            self.approved_by,
            self.approved_at.isoformat() if self.approved_at is not None else None,
            self.effective_from.isoformat(),
            self.effective_to.isoformat() if self.effective_to is not None else None,
            self.source_model_values,
            self.canonical_model_key,
            self.canonical_supplier_key,
            self.canonical_model_part_key,
            self.canonical_item_key,
            self.sample_policy.value,
            self.measurement_mode.value,
        )


@dataclass(frozen=True, slots=True)
class CanonicalRowBindingSelectionSignature:
    """Exact catalog response for one requested Mapping Preview row."""

    requested_key: CanonicalRowBindingKey
    matches: tuple[CanonicalRowBindingSignature, ...]


class CanonicalRowBindingCatalog(Protocol):
    """Materialized exact-match view; implementations must perform no fuzzy matching."""

    @property
    def catalog_revision(self) -> str: ...

    def find(self, key: CanonicalRowBindingKey) -> Sequence[CanonicalRowBinding]: ...


@dataclass(frozen=True, slots=True)
class MaterializedCanonicalRowBindingCatalog:
    """Immutable snapshot whose revision fingerprints every binding field.

    The computed revision includes every row binding revision and semantic
    value.  Consequently, changing any binding creates a different catalog
    revision instead of silently reusing an opaque caller-supplied label. This
    snapshot validates supplied approval evidence but does not implement a
    persistent binding approval workflow.
    """

    bindings: tuple[CanonicalRowBinding, ...]
    catalog_revision: str = field(init=False)

    def __post_init__(self) -> None:
        identities = tuple((binding.key, binding.binding_revision) for binding in self.bindings)
        if len(set(identities)) != len(identities):
            raise ValueError("one binding revision identity must not appear more than once")
        payload = [
            binding.signature.version_parts
            for binding in sorted(
                self.bindings, key=lambda item: repr(item.signature.version_parts)
            )
        ]
        serialized = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
        digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        object.__setattr__(self, "catalog_revision", f"sha256:{digest}")

    def find(self, key: CanonicalRowBindingKey) -> tuple[CanonicalRowBinding, ...]:
        return tuple(binding for binding in self.bindings if binding.key == key)


@dataclass(frozen=True, slots=True)
class LongCandidateIssue:
    code: LongIssueCode
    scope: LongIssueScope
    message: str
    row_key: str | None = None
    sheet_name: str | None = None
    coordinate: str | None = None
    expected: str | None = None
    observed: str | None = None


@dataclass(frozen=True, slots=True)
class LongSourceProvenance:
    receipt: SourceFileReceipt
    preview_source_name: str
    preview_source_size_bytes: int
    preview_sha256_before: str
    preview_sha256_after: str
    source_issues: tuple[ScanIssue, ...]
    is_golden_workbook_evidence: bool
    supplier_scope: str
    template_id: str
    template_schema_version: str
    template_revision: int
    template_approved_by: str
    template_approved_at: datetime
    template_effective_from: date
    template_effective_to: date | None
    source_inspection_date: date
    binding_catalog_revision: str
    binding_selections: tuple[CanonicalRowBindingSelectionSignature, ...]

    def __post_init__(self) -> None:
        if not self.binding_catalog_revision.strip():
            raise ValueError("binding_catalog_revision must not be blank")
        if not self.template_approved_by.strip():
            raise ValueError("template_approved_by must not be blank")
        if self.template_approved_at.utcoffset() is None:
            raise ValueError("template_approved_at must be timezone-aware")
        if (
            self.template_effective_to is not None
            and self.template_effective_to < self.template_effective_from
        ):
            raise ValueError("template effective interval is invalid")
        requested_keys = tuple(selection.requested_key for selection in self.binding_selections)
        if len(set(requested_keys)) != len(requested_keys):
            raise ValueError("binding selections must have unique requested row keys")

    @property
    def deterministic_key(self) -> tuple[object, ...]:
        return (
            self.receipt.receipt_id,
            self.receipt.content_sha256,
            self.template_id,
            self.template_revision,
            self.binding_catalog_revision,
            self.binding_selections,
        )


@dataclass(frozen=True, slots=True)
class LongMeasurementCandidate:
    sample_ordinal: int
    evidence: MappedCellEvidence
    raw_numeric_value: RawNumericValue | None
    raw_qualitative_value: str | None
    standardized_value: None = field(default=None, init=False)
    unit_conversion_status: UnitConversionStatus = field(
        default=UnitConversionStatus.NOT_CONFIGURED,
        init=False,
    )

    def __post_init__(self) -> None:
        if self.sample_ordinal < 1:
            raise ValueError("sample_ordinal must be positive")
        if self.raw_numeric_value is not None and self.raw_qualitative_value is not None:
            raise ValueError("one sample cannot have numeric and qualitative projections")


@dataclass(frozen=True, slots=True)
class LongInspectionCandidate:
    row_key: str
    state: LongRowState
    binding: CanonicalRowBinding | None
    item: MappedCellEvidence
    method: MappedCellEvidence | None
    instrument: MappedCellEvidence | None
    specification: MappedCellEvidence | None
    tolerance: MappedCellEvidence | None
    minimum: MappedCellEvidence | None
    maximum: MappedCellEvidence | None
    section: MappedCellEvidence | None
    category: MappedCellEvidence | None
    unit: MappedCellEvidence | None
    measurement_point: MappedCellEvidence | None
    measurement_location: MappedCellEvidence | None
    cavity: MappedCellEvidence | None
    target: MappedCellEvidence | None
    lsl: MappedCellEvidence | None
    usl: MappedCellEvidence | None
    source_spec_revision: MappedCellEvidence | None
    measurements: tuple[LongMeasurementCandidate, ...]
    supplier_judgment: MappedCellEvidence | None
    issues: tuple[LongCandidateIssue, ...]
    data_status: LongDataStatus = field(default=LongDataStatus.PENDING, init=False)
    system_judgment_status: SystemJudgmentStatus = field(
        default=SystemJudgmentStatus.NOT_EVALUATED,
        init=False,
    )
    system_judgment: None = field(default=None, init=False)
    spec_evaluation_status: SpecEvaluationStatus = field(
        default=SpecEvaluationStatus.NOT_EVALUATED,
        init=False,
    )

    def __post_init__(self) -> None:
        if not self.row_key.strip():
            raise ValueError("row_key must not be blank")
        if self.state == LongRowState.LOADABLE_PENDING and self.binding is None:
            raise ValueError("a loadable row requires a canonical binding")
        if self.state == LongRowState.LOADABLE_PENDING and self.issues:
            raise ValueError("a loadable row must not contain hold issues")
        if self.state == LongRowState.ROW_HELD and not self.issues:
            raise ValueError("a held row requires explicit hold evidence")

    @property
    def has_v2_evidence(self) -> bool:
        return any(
            evidence is not None
            for evidence in (
                self.section,
                self.category,
                self.unit,
                self.measurement_point,
                self.measurement_location,
                self.cavity,
                self.target,
                self.lsl,
                self.usl,
                self.source_spec_revision,
            )
        )


@dataclass(frozen=True, slots=True)
class LongCandidateResult:
    state: LongCandidateState
    provenance: LongSourceProvenance
    source_identifiers: tuple[IdentifierPreview, ...]
    rows: tuple[LongInspectionCandidate, ...]
    issues: tuple[LongCandidateIssue, ...]
    official_values_created: bool = field(default=False, init=False)
    calculations_performed: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if (
            self.provenance.template_schema_version
            not in SUPPORTED_MAPPING_TEMPLATE_SCHEMA_VERSIONS
        ):
            raise ValueError("unsupported Long candidate Mapping schema version")
        if self.provenance.template_schema_version == "1":
            if any(item.kind in MAPPING_V2_IDENTIFIER_KINDS for item in self.source_identifiers):
                raise ValueError("schema-v1 Long candidate cannot carry v2 identifiers")
            if any(row.has_v2_evidence for row in self.rows):
                raise ValueError("schema-v1 Long candidate cannot carry v2 row evidence")
        loadable_count = sum(row.state == LongRowState.LOADABLE_PENDING for row in self.rows)
        held_count = sum(row.state == LongRowState.ROW_HELD for row in self.rows)
        if self.state == LongCandidateState.LOAD_CANDIDATE_READY:
            if not self.rows or held_count or loadable_count != len(self.rows):
                raise ValueError("ready candidate requires only loadable rows")
            if self.issues:
                raise ValueError("ready candidate must not contain hold issues")
        elif self.state == LongCandidateState.PARTIAL_HOLD:
            if not loadable_count or not held_count:
                raise ValueError("partial hold requires loadable and held rows")
        elif self.state == LongCandidateState.LOAD_HELD:
            if loadable_count:
                raise ValueError("globally held candidate cannot expose loadable rows")
            if not self.issues and not any(row.issues for row in self.rows):
                raise ValueError("globally held candidate requires explicit hold evidence")

    @property
    def loadable_rows(self) -> tuple[LongInspectionCandidate, ...]:
        return tuple(row for row in self.rows if row.state == LongRowState.LOADABLE_PENDING)

    @property
    def held_rows(self) -> tuple[LongInspectionCandidate, ...]:
        return tuple(row for row in self.rows if row.state == LongRowState.ROW_HELD)

    def identifier(self, kind: IdentifierKind) -> MappedCellEvidence | None:
        matches = tuple(item.evidence for item in self.source_identifiers if item.kind == kind)
        return matches[0] if len(matches) == 1 else None

    @property
    def source_model(self) -> MappedCellEvidence | None:
        return self.identifier(IdentifierKind.MODEL)

    @property
    def source_lot(self) -> MappedCellEvidence | None:
        return self.identifier(IdentifierKind.LOT_NUMBER)
