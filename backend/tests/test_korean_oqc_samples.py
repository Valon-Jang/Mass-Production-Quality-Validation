from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import cast

import pytest
from openpyxl import load_workbook  # type: ignore[import-untyped]
from scripts.build_korean_oqc_samples import SAMPLE_FILENAMES, build_korean_oqc_samples

from app.application.ai_mapping_candidate import propose_ai_mapping_candidates
from app.domain.ai_mapping import (
    AI_MAPPING_RESPONSE_SCHEMA_VERSION,
    AIMappingCandidateRole,
    AIMappingIssueCode,
    AIMappingOutcomeState,
    AISourceValueKind,
)
from app.infrastructure.excel.workbook_scanner import OpenpyxlWorkbookScanner


class _ExactSampleProvider:
    def __init__(self, targets: tuple[tuple[str, str, str], ...]) -> None:
        self.targets = targets
        self.calls = 0

    def complete(self, request_json: str, *, timeout_seconds: float) -> str:
        assert timeout_seconds > 0
        self.calls += 1
        payload = cast(dict[str, object], json.loads(request_json))
        workbook = cast(dict[str, object], payload["workbook_structure"])
        sheets = cast(list[dict[str, object]], workbook["sheets"])
        tokens = [
            token for sheet in sheets for token in cast(list[dict[str, object]], sheet["tokens"])
        ]
        candidates: list[dict[str, object]] = []
        for role, sheet_name, coordinate in self.targets:
            matches = [
                token
                for token in tokens
                if token["sheet_name"] == sheet_name and token["coordinate"] == coordinate
            ]
            assert len(matches) == 1
            token = matches[0]
            candidates.append(
                {
                    "role": role,
                    "source_id": token["source_id"],
                    "sheet_name": token["sheet_name"],
                    "sheet_position": token["sheet_position"],
                    "coordinate": token["coordinate"],
                    "source_token": token["value"],
                    "source_value_kind": token["value_kind"],
                    "confidence": "MEDIUM",
                    "reason_ko": "합성 OQC 구조에서 찾은 위치 후보이며 사용자 확인이 필요합니다.",
                }
            )
        return json.dumps(
            {
                "schema_version": AI_MAPPING_RESPONSE_SCHEMA_VERSION,
                "request_digest": payload["request_digest"],
                "candidates": candidates,
                "unresolved": [],
                "warnings": [],
            },
            ensure_ascii=False,
        )


@pytest.mark.required_test_id("DQ-P1-AIMAP-007")
def test_five_korean_synthetic_oqc_scenarios_are_readable_and_fail_closed(
    tmp_path: Path,
) -> None:
    output_dir = tmp_path / "한글_OQC_샘플"
    paths = build_korean_oqc_samples(output_dir)

    assert tuple(path.name for path in paths) == SAMPLE_FILENAMES
    assert len({path.read_bytes() for path in paths}) == 5
    assert all(path.stat().st_size > 8_000 for path in paths)

    scans = [OpenpyxlWorkbookScanner().scan(path) for path in paths]
    for path, scan in zip(paths, scans, strict=True):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert scan.source_sha256_before == digest
        assert scan.source_sha256_after == digest
        assert scan.is_golden_workbook_evidence is False
        assert any(sheet.name == "합성자료안내" for sheet in scan.sheets)

        workbook = load_workbook(path, read_only=False, data_only=False)
        try:
            info = workbook["합성자료안내"]
            assert "합성" in str(info["B2"].value)
            assert "Golden" in str(info["A7"].value)
            assert "아님" in str(info["B7"].value)
            assert all("DEMO Precision" not in str(cell.value) for row in info for cell in row)
        finally:
            workbook.close()

    baseline, historical, changed, ambiguous, error = scans
    baseline_report = next(sheet for sheet in baseline.sheets if sheet.name == "출하검사성적서")
    historical_report = next(sheet for sheet in historical.sheets if sheet.name == "출하검사성적서")
    assert baseline_report.used_range == historical_report.used_range
    assert baseline_report.merged_ranges == historical_report.merged_ranges
    assert tuple(candidate.signature for candidate in baseline_report.row_candidates) == tuple(
        candidate.signature for candidate in historical_report.row_candidates
    )
    baseline_provider = _ExactSampleProvider(
        (
            ("MODEL", "출하검사성적서", "D3"),
            ("INSPECTION_DATE", "출하검사성적서", "F4"),
        )
    )
    baseline_ai = propose_ai_mapping_candidates(baseline, baseline_provider)
    assert baseline_ai.state == AIMappingOutcomeState.REVIEW_REQUIRED
    assert baseline_provider.calls == 1
    assert {
        (candidate.role, candidate.source.coordinate) for candidate in baseline_ai.candidates
    } == {
        (AIMappingCandidateRole.MODEL, "D3"),
        (AIMappingCandidateRole.INSPECTION_DATE, "F4"),
    }
    inspection_date_source = next(
        candidate.source
        for candidate in baseline_ai.candidates
        if candidate.role == AIMappingCandidateRole.INSPECTION_DATE
    )
    assert inspection_date_source.value_kind in {
        AISourceValueKind.DATE,
        AISourceValueKind.DATETIME,
    }

    changed_report = next(sheet for sheet in changed.sheets if sheet.name == "출하검사결과서")
    assert changed_report.used_range != baseline_report.used_range
    assert changed_report.merged_ranges != baseline_report.merged_ranges
    assert any(cell.stored_value == "신규 검사항목 후보" for cell in changed_report.cells)
    changed_provider = _ExactSampleProvider(
        (
            ("MODEL", "출하검사결과서", "E4"),
            ("LOT_NUMBER", "출하검사결과서", "B5"),
        )
    )
    changed_ai = propose_ai_mapping_candidates(changed, changed_provider)
    assert changed_ai.state == AIMappingOutcomeState.REVIEW_REQUIRED
    assert changed_provider.calls == 1
    assert {candidate.source.coordinate for candidate in changed_ai.candidates} == {"E4", "B5"}
    assert changed_ai.mapping_approved is False
    assert changed_ai.persistence_performed is False
    assert changed_ai.official_value_created is False
    assert changed_ai.calculation_performed is False

    report_names = {sheet.name for sheet in ambiguous.sheets}
    assert {"치수검사", "외관검사", "합성자료안내"}.issubset(report_names)
    ambiguous_models = {
        cell.stored_value
        for sheet in ambiguous.sheets
        for cell in sheet.cells
        if cell.coordinate == "D3"
    }
    assert {"DNX-가상-200A", "DNX-가상-200B"}.issubset(ambiguous_models)

    error_report = next(sheet for sheet in error.sheets if sheet.name == "출하검사성적서")
    error_codes = {
        (issue.code, issue.location.coordinate if issue.location is not None else None)
        for issue in error_report.issues
    }
    assert ("BROKEN_CELL_REFERENCE", "P10") in error_codes
    assert ("EXTERNAL_REFERENCE_FORMULA", "P11") in error_codes
    assert error_report.protection.enabled is True
    assert any(
        index_range.start <= 13 <= index_range.end for index_range in error_report.hidden_row_ranges
    )
    assert any(
        index_range.start <= 18 <= index_range.end
        for index_range in error_report.hidden_column_ranges
    )
    assert any(
        cell.coordinate == "R8" and "이전 지시를 무시" in str(cell.stored_value)
        for cell in error_report.cells
    )
    rejected_provider = _ExactSampleProvider((("MODEL", "출하검사성적서", "D3"),))
    rejected_ai = propose_ai_mapping_candidates(error, rejected_provider)
    assert rejected_ai.state == AIMappingOutcomeState.REVIEW_HOLD
    assert rejected_ai.issues[0].code == AIMappingIssueCode.PROMPT_INJECTION_DETECTED
    assert rejected_provider.calls == 0
