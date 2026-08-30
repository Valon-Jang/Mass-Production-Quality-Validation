"""Project-local canonical hierarchy, Master Spec, and row-binding revisions.

This module deliberately models configuration evidence only.  It contains no
supplier-to-Master copying, unit conversion, Spec evaluation, or data-status
promotion rule.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from app.domain.long_format import (
    CanonicalRowBinding,
    CanonicalRowBindingKey,
    CanonicalRowBindingStatus,
    MeasurementMode,
    SamplePolicy,
)


class InspectionItemDisposition(StrEnum):
    CANDIDATE = "CANDIDATE"
    MANAGED = "MANAGED"
    EXCLUDED = "EXCLUDED"


class ConfigurationRevisionStatus(StrEnum):
    DRAFT = "DRAFT"
    REVIEWED = "REVIEWED"
    APPROVED = "APPROVED"


@dataclass(frozen=True, slots=True)
class CanonicalModel:
    project_key: str
    model_key: str
    display_name: str

    def __post_init__(self) -> None:
        _require_exact(self.project_key, "project_key")
        _require_exact(self.model_key, "model_key")
        _require_exact(self.display_name, "display_name")


@dataclass(frozen=True, slots=True)
class CanonicalSupplier:
    project_key: str
    supplier_key: str
    display_name: str

    def __post_init__(self) -> None:
        _require_exact(self.project_key, "project_key")
        _require_exact(self.supplier_key, "supplier_key")
        _require_exact(self.display_name, "display_name")


@dataclass(frozen=True, slots=True)
class CanonicalModelPart:
    project_key: str
    model_key: str
    model_part_key: str
    display_name: str

    def __post_init__(self) -> None:
        _require_exact(self.project_key, "project_key")
        _require_exact(self.model_key, "model_key")
        _require_exact(self.model_part_key, "model_part_key")
        _require_exact(self.display_name, "display_name")


@dataclass(frozen=True, slots=True)
class CanonicalInspectionItem:
    project_key: str
    model_part_key: str
    item_key: str
    display_name: str
    disposition: InspectionItemDisposition = InspectionItemDisposition.CANDIDATE

    def __post_init__(self) -> None:
        _require_exact(self.project_key, "project_key")
        _require_exact(self.model_part_key, "model_part_key")
        _require_exact(self.item_key, "item_key")
        _require_exact(self.display_name, "display_name")


@dataclass(frozen=True, slots=True)
class MasterSpecRevision:
    """Immutable numeric-limit payload plus explicit workflow evidence."""

    project_key: str
    canonical_item_key: str
    revision: int
    status: ConfigurationRevisionStatus
    target: Decimal | None
    lsl: Decimal | None
    usl: Decimal | None
    unit: str
    external_spec_revision: str
    effective_from: date
    effective_to: date | None
    change_reason: str
    source_reference: str
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_exact(self.project_key, "project_key")
        _require_exact(self.canonical_item_key, "canonical_item_key")
        _require_positive_revision(self.revision)
        _require_effective_period(self.effective_from, self.effective_to, "Master Spec")
        _require_exact(self.unit, "unit")
        _require_exact(self.external_spec_revision, "external_spec_revision")
        _require_exact(self.change_reason, "change_reason")
        _require_exact(self.source_reference, "source_reference")
        values = (self.target, self.lsl, self.usl)
        if self.lsl is None and self.usl is None:
            raise ValueError("a numeric Master Spec requires at least one limit")
        for name, value in zip(("target", "lsl", "usl"), values, strict=True):
            _require_decimal(value, name)
        if self.lsl is not None and self.usl is not None and self.lsl > self.usl:
            raise ValueError("Master Spec lsl must not exceed usl")
        if self.target is not None:
            if self.lsl is not None and self.target < self.lsl:
                raise ValueError("Master Spec target must not be below lsl")
            if self.usl is not None and self.target > self.usl:
                raise ValueError("Master Spec target must not exceed usl")
        _validate_workflow_metadata(
            self.status,
            reviewed_by=self.reviewed_by,
            reviewed_at=self.reviewed_at,
            approved_by=self.approved_by,
            approved_at=self.approved_at,
            subject="Master Spec",
        )

    def reviewed(self, *, actor_id: str, occurred_at: datetime) -> MasterSpecRevision:
        if self.status != ConfigurationRevisionStatus.DRAFT:
            raise ValueError("only a DRAFT Master Spec revision can be reviewed")
        _require_exact(actor_id, "reviewed_by")
        _require_aware(occurred_at, "reviewed_at")
        return replace(
            self,
            status=ConfigurationRevisionStatus.REVIEWED,
            reviewed_by=actor_id,
            reviewed_at=occurred_at,
        )

    def approved(self, *, actor_id: str, occurred_at: datetime) -> MasterSpecRevision:
        if self.status != ConfigurationRevisionStatus.REVIEWED:
            raise ValueError("only a REVIEWED Master Spec revision can be approved")
        _require_exact(actor_id, "approved_by")
        _require_aware(occurred_at, "approved_at")
        return replace(
            self,
            status=ConfigurationRevisionStatus.APPROVED,
            approved_by=actor_id,
            approved_at=occurred_at,
        )


NumericMasterSpecRevision = MasterSpecRevision


@dataclass(frozen=True, slots=True)
class MasterSpecSupersessionDecision:
    project_key: str
    canonical_item_key: str
    predecessor_revision: int
    successor_revision: int
    predecessor_effective_to: date
    decided_by: str
    decided_at: datetime
    reason: str

    def __post_init__(self) -> None:
        _require_exact(self.project_key, "project_key")
        _require_exact(self.canonical_item_key, "canonical_item_key")
        _require_positive_revision(self.predecessor_revision)
        _require_positive_revision(self.successor_revision)
        if self.successor_revision <= self.predecessor_revision:
            raise ValueError("Master Spec successor revision must be greater than predecessor")
        _require_exact(self.decided_by, "decided_by")
        _require_aware(self.decided_at, "decided_at")
        _require_exact(self.reason, "reason")


@dataclass(frozen=True, slots=True)
class CanonicalRowBindingRevision:
    """Persistent workflow form of the existing immutable Long binding."""

    key: CanonicalRowBindingKey
    binding_revision: int
    status: ConfigurationRevisionStatus
    effective_from: date
    effective_to: date | None
    source_model_values: tuple[str, ...]
    canonical_model_key: str
    canonical_supplier_key: str
    canonical_model_part_key: str
    canonical_item_key: str
    sample_policy: SamplePolicy
    measurement_mode: MeasurementMode
    change_reason: str
    source_reference: str
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    approved_by: str | None = None
    approved_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_positive_revision(self.binding_revision)
        _require_effective_period(self.effective_from, self.effective_to, "row binding")
        if not self.source_model_values:
            raise ValueError("source_model_values must contain at least one exact model value")
        if len(set(self.source_model_values)) != len(self.source_model_values):
            raise ValueError("source_model_values must not contain duplicates")
        for value in self.source_model_values:
            _require_exact(value, "source_model_values")
        for name in (
            "canonical_model_key",
            "canonical_supplier_key",
            "canonical_model_part_key",
            "canonical_item_key",
            "change_reason",
            "source_reference",
        ):
            _require_exact(getattr(self, name), name)
        judgment_only = self.measurement_mode == MeasurementMode.JUDGMENT_ONLY
        zero_allowed = self.sample_policy == SamplePolicy.ZERO_ALLOWED
        if judgment_only != zero_allowed:
            raise ValueError("JUDGMENT_ONLY and ZERO_ALLOWED must be configured together")
        _validate_workflow_metadata(
            self.status,
            reviewed_by=self.reviewed_by,
            reviewed_at=self.reviewed_at,
            approved_by=self.approved_by,
            approved_at=self.approved_at,
            subject="row binding",
        )

    def reviewed(self, *, actor_id: str, occurred_at: datetime) -> CanonicalRowBindingRevision:
        if self.status != ConfigurationRevisionStatus.DRAFT:
            raise ValueError("only a DRAFT row binding revision can be reviewed")
        _require_exact(actor_id, "reviewed_by")
        _require_aware(occurred_at, "reviewed_at")
        return replace(
            self,
            status=ConfigurationRevisionStatus.REVIEWED,
            reviewed_by=actor_id,
            reviewed_at=occurred_at,
        )

    def approved(self, *, actor_id: str, occurred_at: datetime) -> CanonicalRowBindingRevision:
        if self.status != ConfigurationRevisionStatus.REVIEWED:
            raise ValueError("only a REVIEWED row binding revision can be approved")
        _require_exact(actor_id, "approved_by")
        _require_aware(occurred_at, "approved_at")
        return replace(
            self,
            status=ConfigurationRevisionStatus.APPROVED,
            approved_by=actor_id,
            approved_at=occurred_at,
        )

    def materialize(self) -> CanonicalRowBinding:
        if self.status != ConfigurationRevisionStatus.APPROVED:
            raise ValueError("only an APPROVED row binding revision can be materialized")
        return CanonicalRowBinding(
            key=self.key,
            binding_revision=self.binding_revision,
            status=CanonicalRowBindingStatus.APPROVED,
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
class CanonicalRowBindingSupersessionDecision:
    key: CanonicalRowBindingKey
    predecessor_revision: int
    successor_revision: int
    predecessor_effective_to: date
    decided_by: str
    decided_at: datetime
    reason: str

    def __post_init__(self) -> None:
        _require_positive_revision(self.predecessor_revision)
        _require_positive_revision(self.successor_revision)
        if self.successor_revision <= self.predecessor_revision:
            raise ValueError("row binding successor revision must be greater than predecessor")
        _require_exact(self.decided_by, "decided_by")
        _require_aware(self.decided_at, "decided_at")
        _require_exact(self.reason, "reason")


@dataclass(frozen=True, slots=True)
class EffectiveMasterSpecRevision:
    """Approved payload plus separately resolved supersession effectivity."""

    spec: MasterSpecRevision
    resolved_effective_to: date | None

    def __post_init__(self) -> None:
        if self.spec.status != ConfigurationRevisionStatus.APPROVED:
            raise ValueError("effective Master Spec record requires an APPROVED revision")
        if (
            self.resolved_effective_to is not None
            and self.resolved_effective_to < self.spec.effective_from
        ):
            raise ValueError("resolved_effective_to must not precede effective_from")
        if (
            self.spec.effective_to is not None
            and self.resolved_effective_to is not None
            and self.resolved_effective_to > self.spec.effective_to
        ):
            raise ValueError("resolved_effective_to must not extend declared effectivity")

    @property
    def effective_end(self) -> date | None:
        return self.resolved_effective_to or self.spec.effective_to


@dataclass(frozen=True, slots=True)
class MaterializedMasterSpecCatalog:
    """One deterministic approved/effective numeric Spec per managed item."""

    project_key: str
    as_of: date
    revisions: tuple[EffectiveMasterSpecRevision, ...]

    def __post_init__(self) -> None:
        _require_exact(self.project_key, "project_key")
        keys = tuple(item.spec.canonical_item_key for item in self.revisions)
        if len(set(keys)) != len(keys):
            raise ValueError("Master Spec catalog contains multiple active revisions for one item")
        for item in self.revisions:
            if item.spec.project_key != self.project_key:
                raise ValueError("Master Spec catalog cannot mix projects")
            if not _is_effective(item.spec.effective_from, item.effective_end, self.as_of):
                raise ValueError("Master Spec catalog accepts only effective revisions")

    def find(self, canonical_item_key: str) -> EffectiveMasterSpecRevision | None:
        _require_exact(canonical_item_key, "canonical_item_key")
        return next(
            (item for item in self.revisions if item.spec.canonical_item_key == canonical_item_key),
            None,
        )


def _require_exact(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{field_name} must be an exact non-blank value")


def _require_positive_revision(value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError("revision must be a positive integer")


def _require_decimal(value: Decimal | None, field_name: str) -> None:
    if value is None:
        return
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError(f"{field_name} must be a finite Decimal or None")


def _require_effective_period(start: date, end: date | None, subject: str) -> None:
    if isinstance(start, datetime) or not isinstance(start, date):
        raise ValueError(f"{subject} effective_from must be a date")
    if end is not None and (isinstance(end, datetime) or not isinstance(end, date)):
        raise ValueError(f"{subject} effective_to must be a date or None")
    if end is not None and end < start:
        raise ValueError(f"{subject} effective_to must not precede effective_from")


def _require_aware(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _validate_workflow_metadata(
    status: ConfigurationRevisionStatus,
    *,
    reviewed_by: str | None,
    reviewed_at: datetime | None,
    approved_by: str | None,
    approved_at: datetime | None,
    subject: str,
) -> None:
    if status == ConfigurationRevisionStatus.DRAFT:
        if any(value is not None for value in (reviewed_by, reviewed_at, approved_by, approved_at)):
            raise ValueError(f"DRAFT {subject} must not contain review or approval metadata")
        return
    if reviewed_by is None or reviewed_at is None:
        raise ValueError(f"{status.value} {subject} requires review metadata")
    _require_exact(reviewed_by, "reviewed_by")
    _require_aware(reviewed_at, "reviewed_at")
    if status == ConfigurationRevisionStatus.REVIEWED:
        if approved_by is not None or approved_at is not None:
            raise ValueError(f"REVIEWED {subject} must not contain approval metadata")
        return
    if approved_by is None or approved_at is None:
        raise ValueError(f"APPROVED {subject} requires approval metadata")
    _require_exact(approved_by, "approved_by")
    _require_aware(approved_at, "approved_at")


def _is_effective(start: date, end: date | None, value: date) -> bool:
    return start <= value and (end is None or value <= end)
