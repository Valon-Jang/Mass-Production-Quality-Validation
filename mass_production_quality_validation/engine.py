from __future__ import annotations

from datetime import date
from typing import Iterable

from .models import (
    ApprovalStatus,
    EvidenceStatus,
    QualityStatus,
    RiskLevel,
    ValidationItem,
    ValidationStatus,
    ensure_unique_ids,
)


def _progress(item: ValidationItem) -> int:
    checks: list[bool] = []
    if item.validation_required:
        checks.append(item.validation_status == ValidationStatus.PASS)
    else:
        checks.append(True)
    checks.append(item.quality_status == QualityStatus.APPROVED)
    if item.approval_required:
        checks.append(item.approval_status == ApprovalStatus.APPROVED)
    else:
        checks.append(True)
    checks.append(item.evidence_status == EvidenceStatus.COMPLETE)
    checks.append(item.risk_level != RiskLevel.UNASSESSED)
    return round(sum(checks) / len(checks) * 100)


def evaluate_item(item: ValidationItem, *, as_of: date | None = None) -> dict:
    as_of = as_of or date.today()
    blockers: list[str] = []
    warnings: list[str] = []

    if item.validation_required and item.validation_status != ValidationStatus.PASS:
        blockers.append("VALIDATION_NOT_PASSED")
    if item.quality_status != QualityStatus.APPROVED:
        blockers.append("QUALITY_REVIEW_NOT_APPROVED")
    if item.approval_required and item.approval_status != ApprovalStatus.APPROVED:
        blockers.append("REQUIRED_APPROVAL_NOT_APPROVED")
    if item.evidence_status != EvidenceStatus.COMPLETE:
        blockers.append("EVIDENCE_INCOMPLETE")
    if item.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL} and item.risk_open:
        blockers.append("HIGH_OR_CRITICAL_RISK_OPEN")
    elif item.risk_level == RiskLevel.UNASSESSED:
        blockers.append("RISK_NOT_ASSESSED")

    if item.validation_status == ValidationStatus.FAIL:
        blockers.append("VALIDATION_FAILED")
    if item.quality_status == QualityStatus.REJECTED:
        blockers.append("QUALITY_REJECTED")
    if item.approval_status == ApprovalStatus.REJECTED:
        blockers.append("APPROVAL_REJECTED")

    overdue = bool(item.target_date and not item.completed_date and item.target_date < as_of)
    if overdue:
        warnings.append("TARGET_DATE_OVERDUE")
    if item.target_date is None:
        warnings.append("TARGET_DATE_MISSING")
    if item.owner is None:
        warnings.append("OWNER_MISSING")
    if item.next_action is None and blockers:
        warnings.append("NEXT_ACTION_MISSING")

    blockers = list(dict.fromkeys(blockers))
    return {
        "item_id": item.item_id,
        "name": item.name,
        "ready": not blockers,
        "progress_percent": _progress(item),
        "blockers": blockers,
        "warnings": warnings,
        "risk_level": item.risk_level.value,
        "risk_open": item.risk_open,
        "target_date": item.target_date.isoformat() if item.target_date else None,
        "completed_date": item.completed_date.isoformat() if item.completed_date else None,
        "next_action": item.next_action,
    }


def evaluate_release_gate(items: Iterable[ValidationItem], *, gate_name: str = "MASS_PRODUCTION_RELEASE", as_of: date | None = None) -> dict:
    items = ensure_unique_ids(items)
    evaluations = [evaluate_item(item, as_of=as_of) for item in items]
    blockers = [
        {"item_id": row["item_id"], "blockers": row["blockers"]}
        for row in evaluations
        if row["blockers"]
    ]
    return {
        "gate": gate_name,
        "verdict": "READY" if not blockers else "NOT_READY",
        "total_items": len(items),
        "ready_items": sum(1 for row in evaluations if row["ready"]),
        "blocking_items": len(blockers),
        "blockers": blockers,
        "items": evaluations,
    }


def summarize_portfolio(items: Iterable[ValidationItem], *, as_of: date | None = None) -> dict:
    items = ensure_unique_ids(items)
    evaluations = [evaluate_item(item, as_of=as_of) for item in items]

    def count_status(predicate) -> int:
        return sum(1 for item in items if predicate(item))

    progress_values = [row["progress_percent"] for row in evaluations]
    return {
        "total_items": len(items),
        "validation_incomplete": count_status(lambda item: item.validation_required and item.validation_status != ValidationStatus.PASS),
        "quality_not_approved": count_status(lambda item: item.quality_status != QualityStatus.APPROVED),
        "approval_pending": count_status(lambda item: item.approval_required and item.approval_status != ApprovalStatus.APPROVED),
        "evidence_incomplete": count_status(lambda item: item.evidence_status != EvidenceStatus.COMPLETE),
        "high_risk_open": count_status(lambda item: item.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL} and item.risk_open),
        "overdue": sum(1 for row in evaluations if "TARGET_DATE_OVERDUE" in row["warnings"]),
        "average_progress_percent": round(sum(progress_values) / len(progress_values), 1) if progress_values else 0.0,
        "release_ready": all(row["ready"] for row in evaluations),
    }
