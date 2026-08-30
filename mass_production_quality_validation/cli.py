from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from .engine import evaluate_release_gate, summarize_portfolio
from .models import ApprovalStatus, EvidenceStatus, QualityStatus, RiskLevel, ValidationItem, ValidationStatus


def _parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value else None


def _load_item(row: dict) -> ValidationItem:
    return ValidationItem(
        item_id=str(row["item_id"]),
        name=str(row["name"]),
        validation_required=bool(row.get("validation_required", True)),
        validation_status=ValidationStatus(row.get("validation_status", "NOT_STARTED")),
        quality_status=QualityStatus(row.get("quality_status", "NOT_REVIEWED")),
        approval_required=bool(row.get("approval_required", False)),
        approval_status=ApprovalStatus(row.get("approval_status", "NOT_REQUIRED")),
        evidence_status=EvidenceStatus(row.get("evidence_status", "MISSING")),
        risk_level=RiskLevel(row.get("risk_level", "UNASSESSED")),
        risk_open=bool(row.get("risk_open", False)),
        owner=row.get("owner"),
        target_date=_parse_date(row.get("target_date")),
        completed_date=_parse_date(row.get("completed_date")),
        next_action=row.get("next_action"),
        notes=row.get("notes"),
        evidence_refs=tuple(str(v) for v in row.get("evidence_refs", [])),
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="mass-production-quality-validation",
        description="Evaluate evidence-driven mass production quality readiness.",
    )
    parser.add_argument("input", type=Path, help="JSON file containing an items array")
    parser.add_argument("--as-of", help="Evaluation date in YYYY-MM-DD format")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    items = [_load_item(row) for row in payload["items"]]
    as_of = _parse_date(args.as_of)
    result = {
        "summary": summarize_portfolio(items, as_of=as_of),
        "release_gate": evaluate_release_gate(items, as_of=as_of),
    }
    print(json.dumps(result, indent=2 if args.pretty else None, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
