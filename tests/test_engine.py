import unittest
from datetime import date

from mass_production_quality_validation import (
    ApprovalStatus,
    EvidenceStatus,
    QualityStatus,
    RiskLevel,
    ValidationItem,
    ValidationStatus,
    evaluate_item,
    evaluate_release_gate,
    summarize_portfolio,
)


class MassProductionQualityValidationTests(unittest.TestCase):
    def test_ready_item_passes_release_gate(self):
        item = ValidationItem(
            item_id="A",
            name="Synthetic component",
            validation_status=ValidationStatus.PASS,
            quality_status=QualityStatus.APPROVED,
            approval_required=True,
            approval_status=ApprovalStatus.APPROVED,
            evidence_status=EvidenceStatus.COMPLETE,
            risk_level=RiskLevel.LOW,
            owner="owner",
            target_date=date(2026, 9, 1),
            completed_date=date(2026, 8, 30),
        )
        self.assertTrue(evaluate_item(item, as_of=date(2026, 8, 31))["ready"])
        self.assertEqual(evaluate_release_gate([item])["verdict"], "READY")

    def test_missing_evidence_blocks_release(self):
        item = ValidationItem(
            item_id="A",
            name="Synthetic component",
            validation_status=ValidationStatus.PASS,
            quality_status=QualityStatus.APPROVED,
            evidence_status=EvidenceStatus.PARTIAL,
            risk_level=RiskLevel.LOW,
        )
        self.assertIn("EVIDENCE_INCOMPLETE", evaluate_item(item)["blockers"])

    def test_open_high_risk_blocks_release(self):
        item = ValidationItem(
            item_id="A",
            name="Synthetic component",
            validation_status=ValidationStatus.PASS,
            quality_status=QualityStatus.APPROVED,
            evidence_status=EvidenceStatus.COMPLETE,
            risk_level=RiskLevel.HIGH,
            risk_open=True,
        )
        self.assertIn("HIGH_OR_CRITICAL_RISK_OPEN", evaluate_item(item)["blockers"])

    def test_required_approval_must_be_approved(self):
        item = ValidationItem(
            item_id="A",
            name="Synthetic component",
            validation_status=ValidationStatus.PASS,
            quality_status=QualityStatus.APPROVED,
            approval_required=True,
            approval_status=ApprovalStatus.PENDING,
            evidence_status=EvidenceStatus.COMPLETE,
            risk_level=RiskLevel.LOW,
        )
        self.assertIn("REQUIRED_APPROVAL_NOT_APPROVED", evaluate_item(item)["blockers"])

    def test_summary_counts_overdue_and_incomplete(self):
        item = ValidationItem(
            item_id="A",
            name="Synthetic component",
            validation_status=ValidationStatus.IN_PROGRESS,
            quality_status=QualityStatus.REVIEWING,
            approval_required=True,
            approval_status=ApprovalStatus.PENDING,
            evidence_status=EvidenceStatus.PARTIAL,
            risk_level=RiskLevel.MEDIUM,
            owner="owner",
            target_date=date(2026, 8, 1),
            next_action="finish validation",
        )
        summary = summarize_portfolio([item], as_of=date(2026, 8, 31))
        self.assertEqual(summary["validation_incomplete"], 1)
        self.assertEqual(summary["approval_pending"], 1)
        self.assertEqual(summary["overdue"], 1)
        self.assertFalse(summary["release_ready"])

    def test_duplicate_ids_rejected(self):
        item = ValidationItem(item_id="A", name="One")
        with self.assertRaises(ValueError):
            evaluate_release_gate([item, item])


if __name__ == "__main__":
    unittest.main()
