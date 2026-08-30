from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest

from app.application.mapping_preview import (
    InMemoryMappingTemplateRegistry,
    build_mapping_preview,
)
from app.domain.mapping import (
    CellAddress,
    HeaderTokenAssertion,
    IdentifierKind,
    IdentifierMapping,
    InspectionRowMapping,
    MappingIssueCode,
    MappingPreviewRequest,
    MappingPreviewState,
    MappingTemplate,
    MappingTemplateStatus,
    MergeSignatureAssertion,
    RowStructureAssertion,
    SheetStructureAssertion,
    WorkbookFingerprint,
)
from app.domain.workbook_scan import (
    CellEvidence,
    MacroHandling,
    SheetKind,
    SheetProtectionMetadata,
    SheetScan,
    WorkbookScan,
    WorkbookScanState,
)

_REPORT = "Report"
_CHART = "Summary Chart"


def _address(sheet_name: str, coordinate: str) -> CellAddress:
    return CellAddress(sheet_name=sheet_name, coordinate=coordinate)


def _cell(coordinate: str, value: object) -> CellEvidence:
    return CellEvidence(
        coordinate=coordinate,
        stored_value=value,
        cached_value=None,
        formula_text=None,
        number_format="General",
        data_type="s",
    )


def _sheet(
    *,
    name: str,
    kind: SheetKind,
    position: int,
    used_range: str | None,
    cells: tuple[CellEvidence, ...] = (),
) -> SheetScan:
    return SheetScan(
        name=name,
        kind=kind,
        position=position,
        visibility="visible",
        used_range=used_range,
        estimated_cells=0 if kind == SheetKind.CHARTSHEET else 8,
        merged_ranges=(),
        hidden_row_ranges=(),
        hidden_column_ranges=(),
        cells=cells,
        row_candidates=(),
        protection=SheetProtectionMetadata(enabled=False, protected_actions=()),
        images=(),
        issues=(),
    )


def _scan() -> WorkbookScan:
    report = _sheet(
        name=_REPORT,
        kind=SheetKind.WORKSHEET,
        position=0,
        used_range="A1:B4",
        cells=(
            _cell("A1", "Generic Report"),
            _cell("A2", "SUPPLIER-GENERIC"),
            _cell("B2", "2026-06-01"),
            _cell("A4", "Inspection item"),
        ),
    )
    chart = _sheet(
        name=_CHART,
        kind=SheetKind.CHARTSHEET,
        position=1,
        used_range=None,
    )
    return WorkbookScan(
        state=WorkbookScanState.SCANNED,
        source_name="synthetic-chart.xlsx",
        source_size_bytes=1,
        source_sha256_before="c" * 64,
        source_sha256_after="c" * 64,
        sheets=(report, chart),
        issues=(),
        estimated_cells=8,
        external_link_count=0,
        macro_handling=MacroHandling.NOT_APPLICABLE,
    )


def _template() -> MappingTemplate:
    item = _address(_REPORT, "A4")
    return MappingTemplate(
        template_id="worksheet-and-chart-template",
        schema_version="1",
        revision=1,
        status=MappingTemplateStatus.APPROVED,
        project_key="chart-project",
        supplier_scope="chart-supplier-scope",
        supplier_source_aliases=("SUPPLIER-GENERIC",),
        approved_by="chart-reviewer",
        approved_at=datetime(2026, 1, 1, tzinfo=UTC),
        effective_from=date(2026, 1, 1),
        effective_to=date(2026, 12, 31),
        fingerprint=WorkbookFingerprint(
            header_tokens=(HeaderTokenAssertion(_address(_REPORT, "A1"), "Generic Report"),),
            sheet_structures=(
                SheetStructureAssertion(
                    sheet_name=_REPORT,
                    expected_position=0,
                    expected_kind=SheetKind.WORKSHEET,
                    expected_visibility="visible",
                    expected_used_range="A1:B4",
                ),
                SheetStructureAssertion(
                    sheet_name=_CHART,
                    expected_position=1,
                    expected_kind=SheetKind.CHARTSHEET,
                    expected_visibility="visible",
                    expected_used_range=None,
                ),
            ),
            merge_signatures=(
                MergeSignatureAssertion(_REPORT, ()),
                MergeSignatureAssertion(_CHART, ()),
            ),
            row_structures=(
                RowStructureAssertion(
                    row_key="item-row",
                    sheet_name=_REPORT,
                    row_index=4,
                    expected_non_empty_cells=(item,),
                ),
            ),
        ),
        identifiers=(
            IdentifierMapping(IdentifierKind.SUPPLIER, _address(_REPORT, "A2")),
            IdentifierMapping(IdentifierKind.INSPECTION_DATE, _address(_REPORT, "B2")),
        ),
        inspection_rows=(InspectionRowMapping(row_key="item-row", item=item),),
    )


def _preview(scan: WorkbookScan) -> MappingPreviewState:
    registry = InMemoryMappingTemplateRegistry()
    registry.register(_template())
    return build_mapping_preview(
        scan,
        MappingPreviewRequest(
            project_key="chart-project",
            supplier_scope="chart-supplier-scope",
        ),
        registry,
    ).state


@pytest.mark.required_test_id("DQ-P1-MAP-009")
def test_chartsheet_has_no_used_range_and_kind_or_range_changes_fail_closed() -> None:
    with pytest.raises(ValueError, match="worksheet expected_used_range"):
        SheetStructureAssertion(
            sheet_name=_REPORT,
            expected_position=0,
            expected_kind=SheetKind.WORKSHEET,
            expected_visibility="visible",
            expected_used_range=None,
        )
    with pytest.raises(ValueError, match="chartsheet expected_used_range"):
        SheetStructureAssertion(
            sheet_name=_CHART,
            expected_position=1,
            expected_kind=SheetKind.CHARTSHEET,
            expected_visibility="visible",
            expected_used_range="A1",
        )

    scan = _scan()
    assert _preview(scan) == MappingPreviewState.PREVIEW_READY

    wrong_kind_chart = replace(
        scan.sheets[1],
        kind=SheetKind.WORKSHEET,
        used_range="A1",
        estimated_cells=1,
    )
    wrong_kind = build_mapping_preview(
        replace(scan, sheets=(scan.sheets[0], wrong_kind_chart)),
        MappingPreviewRequest("chart-project", "chart-supplier-scope"),
        _registry_with_template(),
    )
    assert wrong_kind.state == MappingPreviewState.MAPPING_REQUIRED
    assert wrong_kind.preview is None
    assert MappingIssueCode.FINGERPRINT_SHEET_MISMATCH in {
        issue.code for issue in wrong_kind.issues
    }

    wrong_range_chart = replace(scan.sheets[1], used_range="A1")
    wrong_range = build_mapping_preview(
        replace(scan, sheets=(scan.sheets[0], wrong_range_chart)),
        MappingPreviewRequest("chart-project", "chart-supplier-scope"),
        _registry_with_template(),
    )
    assert wrong_range.state == MappingPreviewState.MAPPING_REQUIRED
    assert wrong_range.preview is None
    assert MappingIssueCode.FINGERPRINT_SHEET_MISMATCH in {
        issue.code for issue in wrong_range.issues
    }


def _registry_with_template() -> InMemoryMappingTemplateRegistry:
    registry = InMemoryMappingTemplateRegistry()
    registry.register(_template())
    return registry
