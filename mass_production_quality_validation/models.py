from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Iterable


class ValidationStatus(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    PASS = "PASS"
    FAIL = "FAIL"
    NOT_REQUIRED = "NOT_REQUIRED"


class QualityStatus(str, Enum):
    NOT_REVIEWED = "NOT_REVIEWED"
    REVIEWING = "REVIEWING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class ApprovalStatus(str, Enum):
    NOT_REQUIRED = "NOT_REQUIRED"
    NOT_REQUESTED = "NOT_REQUESTED"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class EvidenceStatus(str, Enum):
    MISSING = "MISSING"
    PARTIAL = "PARTIAL"
    COMPLETE = "COMPLETE"


class RiskLevel(str, Enum):
    UNASSESSED = "UNASSESSED"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass(frozen=True)
class ValidationItem:
    item_id: str
    name: str
    validation_required: bool = True
    validation_status: ValidationStatus = ValidationStatus.NOT_STARTED
    quality_status: QualityStatus = QualityStatus.NOT_REVIEWED
    approval_required: bool = False
    approval_status: ApprovalStatus = ApprovalStatus.NOT_REQUIRED
    evidence_status: EvidenceStatus = EvidenceStatus.MISSING
    risk_level: RiskLevel = RiskLevel.UNASSESSED
    risk_open: bool = False
    owner: str | None = None
    target_date: date | None = None
    completed_date: date | None = None
    next_action: str | None = None
    notes: str | None = None
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.item_id.strip():
            raise ValueError("item_id must not be empty")
        if not self.name.strip():
            raise ValueError("name must not be empty")
        if self.approval_required and self.approval_status == ApprovalStatus.NOT_REQUIRED:
            raise ValueError("approval_required items cannot use NOT_REQUIRED approval_status")
        if not self.approval_required and self.approval_status != ApprovalStatus.NOT_REQUIRED:
            raise ValueError("approval_status must be NOT_REQUIRED when approval_required is false")


def ensure_unique_ids(items: Iterable[ValidationItem]) -> tuple[ValidationItem, ...]:
    result = tuple(items)
    ids = [item.item_id for item in result]
    if len(ids) != len(set(ids)):
        raise ValueError("item_id values must be unique")
    return result
