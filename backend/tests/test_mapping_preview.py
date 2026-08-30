from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
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
    PreviewValueKind,
    RowStructureAssertion,
    SheetStructureAssertion,
    SystemJudgmentStatus,
    TemplateHistoryError,
    TemplateHistoryErrorCode,
    WorkbookFingerprint,
)
from app.domain.workbook_scan import (
    CellEvidence,
    DisplayValueStatus,
    IssueSeverity,
    MacroHandling,
    ScanIssue,
    SheetKind,
    SheetProtectionMetadata,
    SheetScan,
    SourceLocation,
    WorkbookScan,
    WorkbookScanState,
)

_SHEET = "Report"


def _address(coordinate: str) -> CellAddress:
    return CellAddress(sheet_name=_SHEET, coordinate=coordinate)


def _cell(
    coordinate: str,
    value: object,
    *,
    cached_value: object | None = None,
    formula_text: str | None = None,
    number_format: str = "General",
) -> CellEvidence:
    return CellEvidence(
        coordinate=coordinate,
        stored_value=value,
        cached_value=cached_value,
        formula_text=formula_text,
        number_format=number_format,
        data_type="f" if formula_text is not None else "n" if isinstance(value, float) else "s",
        display_value=None,
        display_value_status=DisplayValueStatus.NOT_RENDERED,
    )


def _scan(
    *,
    title: str = "Generic Quality Report",
    model: str = "MODEL-ALPHA",
    lot: str = "LOT-001",
    inspection_date: str = "2026-04-12",
    numeric_samples: tuple[float, float, float] = (10.0, 10.1, 9.9),
    qualitative_samples: tuple[str, str] = ("Clear", "Clear"),
    numeric_supplier_result: str = "OK",
    omit_model_cell: bool = False,
) -> WorkbookScan:
    cells = [
        _cell("A1", title),
        _cell("B2", inspection_date),
        _cell("C2", lot),
        _cell("D2", "SUPPLIER-SCOPE-VALUE"),
        _cell("A4", "Measured feature"),
        _cell("B4", "Comparator method"),
        _cell("C4", "Instrument type"),
        _cell("D4", "Nominal requirement"),
        _cell("E4", "Symmetric tolerance"),
        _cell("F4", 9.5),
        _cell(
            "G4",
            "=F4+1",
            cached_value=10.5,
            formula_text="=F4+1",
            number_format="0.00",
        ),
        _cell("H4", numeric_samples[0], number_format="0.00"),
        _cell("I4", numeric_samples[1], number_format="0.00"),
        _cell("J4", numeric_samples[2], number_format="0.00"),
        _cell("K4", numeric_supplier_result),
        _cell("A5", "Surface condition"),
        _cell("B5", "Visual method"),
        _cell("C5", "Inspection station"),
        _cell("D5", "Qualitative requirement"),
        _cell("E5", "Not numeric"),
        _cell("F5", "N/A"),
        _cell("G5", "N/A"),
        _cell("H5", qualitative_samples[0]),
        _cell("I5", qualitative_samples[1]),
        _cell("J5", "OK"),
    ]
    if not omit_model_cell:
        cells.append(_cell("A2", model))

    sheet = SheetScan(
        name=_SHEET,
        kind=SheetKind.WORKSHEET,
        position=0,
        visibility="visible",
        used_range="A1:K5",
        estimated_cells=55,
        merged_ranges=("A1:K1",),
        hidden_row_ranges=(),
        hidden_column_ranges=(),
        cells=tuple(cells),
        row_candidates=(),
        protection=SheetProtectionMetadata(enabled=False, protected_actions=()),
        images=(),
        issues=(),
    )
    return WorkbookScan(
        state=WorkbookScanState.SCANNED,
        source_name="synthetic.xlsx",
        source_size_bytes=1,
        source_sha256_before="a" * 64,
        source_sha256_after="a" * 64,
        sheets=(sheet,),
        issues=(),
        estimated_cells=55,
        external_link_count=0,
        macro_handling=MacroHandling.NOT_APPLICABLE,
    )


def _row_mapping(
    row_key: str,
    row_number: int,
    sample_columns: tuple[str, ...],
    supplier_result_column: str,
) -> InspectionRowMapping:
    return InspectionRowMapping(
        row_key=row_key,
        item=_address(f"A{row_number}"),
        method=_address(f"B{row_number}"),
        instrument=_address(f"C{row_number}"),
        specification=_address(f"D{row_number}"),
        tolerance=_address(f"E{row_number}"),
        minimum=_address(f"F{row_number}"),
        maximum=_address(f"G{row_number}"),
        sample_cells=tuple(_address(f"{column}{row_number}") for column in sample_columns),
        supplier_result=_address(f"{supplier_result_column}{row_number}"),
    )


def _template(
    *,
    template_id: str = "generic-oqc-template",
    schema_version: str = "1",
    revision: int = 1,
    status: MappingTemplateStatus = MappingTemplateStatus.APPROVED,
    project_key: str = "project-alpha",
    supplier_scope: str = "supplier-scope-alpha",
    supplier_source_aliases: tuple[str, ...] = ("SUPPLIER-SCOPE-VALUE",),
    effective_from: date = date(2026, 1, 1),
    effective_to: date | None = date(2026, 12, 31),
) -> MappingTemplate:
    numeric_row = _row_mapping("numeric-row", 4, ("H", "I", "J"), "K")
    qualitative_row = _row_mapping("qualitative-row", 5, ("H", "I"), "J")
    fingerprint = WorkbookFingerprint(
        header_tokens=(
            HeaderTokenAssertion(
                source=_address("A1"),
                expected_token="Generic Quality Report",
            ),
        ),
        sheet_structures=(
            SheetStructureAssertion(
                sheet_name=_SHEET,
                expected_position=0,
                expected_kind=SheetKind.WORKSHEET,
                expected_visibility="visible",
                expected_used_range="A1:K5",
            ),
        ),
        merge_signatures=(
            MergeSignatureAssertion(
                sheet_name=_SHEET,
                expected_merged_ranges=("A1:K1",),
            ),
        ),
        row_structures=(
            RowStructureAssertion(
                row_key=numeric_row.row_key,
                sheet_name=_SHEET,
                row_index=4,
                expected_non_empty_cells=numeric_row.all_addresses,
            ),
            RowStructureAssertion(
                row_key=qualitative_row.row_key,
                sheet_name=_SHEET,
                row_index=5,
                expected_non_empty_cells=qualitative_row.all_addresses,
            ),
        ),
    )
    approved = status == MappingTemplateStatus.APPROVED
    return MappingTemplate(
        template_id=template_id,
        schema_version=schema_version,
        revision=revision,
        status=status,
        project_key=project_key,
        supplier_scope=supplier_scope,
        supplier_source_aliases=supplier_source_aliases,
        approved_by="reviewer-001" if approved else None,
        approved_at=datetime(2026, 1, 1, tzinfo=UTC) if approved else None,
        effective_from=effective_from,
        effective_to=effective_to,
        fingerprint=fingerprint,
        identifiers=(
            IdentifierMapping(IdentifierKind.MODEL, _address("A2")),
            IdentifierMapping(IdentifierKind.INSPECTION_DATE, _address("B2")),
            IdentifierMapping(IdentifierKind.LOT_NUMBER, _address("C2")),
            IdentifierMapping(IdentifierKind.SUPPLIER, _address("D2")),
        ),
        inspection_rows=(numeric_row, qualitative_row),
    )


def _request(
    *,
    project_key: str = "project-alpha",
    supplier_scope: str = "supplier-scope-alpha",
) -> MappingPreviewRequest:
    return MappingPreviewRequest(
        project_key=project_key,
        supplier_scope=supplier_scope,
    )


def _registry(*templates: MappingTemplate) -> InMemoryMappingTemplateRegistry:
    registry = InMemoryMappingTemplateRegistry()
    for template in templates:
        registry.register(template)
    return registry


@pytest.mark.required_test_id("DQ-P1-MAP-001")
def test_approved_template_previews_exact_source_cell_evidence() -> None:
    warning = ScanIssue(
        code="CALCULATION_REFRESH_REQUIRED",
        severity=IssueSeverity.WARNING,
        message="Synthetic formula evidence requires refresh before official use.",
        location=SourceLocation.cell(_SHEET, "G4"),
    )
    scan = replace(
        _scan(),
        state=WorkbookScanState.SCANNED_WITH_WARNINGS,
        issues=(warning,),
    )
    result = build_mapping_preview(scan, _request(), _registry(_template()))

    assert result.state == MappingPreviewState.PREVIEW_READY
    assert result.preview is not None
    identifiers = {item.kind: item.evidence for item in result.preview.identifiers}
    assert identifiers[IdentifierKind.MODEL].source == _address("A2")
    assert identifiers[IdentifierKind.MODEL].raw_value == "MODEL-ALPHA"
    numeric_row = result.preview.inspection_rows[0]
    assert numeric_row.maximum.source == _address("G4")
    assert numeric_row.maximum.raw_value == "=F4+1"
    assert numeric_row.maximum.formula_text == "=F4+1"
    assert numeric_row.maximum.cached_value == 10.5
    assert numeric_row.maximum.display_value is None
    assert numeric_row.maximum.display_value_status == DisplayValueStatus.NOT_RENDERED
    assert result.preview.source_sha256_before == "a" * 64
    assert result.preview.source_sha256_after == "a" * 64
    assert result.preview.source_issues == (warning,)
    assert result.preview.is_golden_workbook_evidence is False
    assert result.preview.source_inspection_date == date(2026, 4, 12)
    assert result.official_values_created is False


@pytest.mark.required_test_id("DQ-P1-MAP-002")
def test_approval_effectivity_and_immutable_revision_history_fail_closed() -> None:
    revision_one = _template(effective_to=date(2026, 3, 31))
    registry = _registry(revision_one)
    with pytest.raises(FrozenInstanceError):
        revision_one.revision = 99  # type: ignore[misc]
    with pytest.raises(TemplateHistoryError) as overwrite:
        registry.register(revision_one)
    assert overwrite.value.code == TemplateHistoryErrorCode.REVISION_OVERWRITE

    revision_three = _template(
        revision=3,
        effective_from=date(2026, 4, 1),
        effective_to=date(2026, 6, 30),
    )
    registry.register(revision_three)
    with pytest.raises(TemplateHistoryError) as downgrade:
        registry.register(
            _template(
                revision=2,
                effective_from=date(2026, 7, 1),
                effective_to=date(2026, 8, 31),
            )
        )
    assert downgrade.value.code == TemplateHistoryErrorCode.REVISION_DOWNGRADE
    with pytest.raises(TemplateHistoryError) as overlap:
        registry.register(
            _template(
                revision=4,
                effective_from=date(2026, 6, 1),
                effective_to=date(2026, 7, 31),
            )
        )
    assert overlap.value.code == TemplateHistoryErrorCode.EFFECTIVE_PERIOD_OVERLAP

    open_predecessor = _template(template_id="open-history", effective_to=None)
    open_registry = _registry(open_predecessor)
    open_registry.register(
        _template(
            template_id="open-history",
            revision=2,
            status=MappingTemplateStatus.DRAFT,
            effective_from=date(2026, 6, 1),
            effective_to=None,
        )
    )
    successor = _template(
        template_id="open-history",
        revision=3,
        effective_from=date(2027, 1, 1),
        effective_to=None,
    )
    decision = open_registry.supersede(
        successor,
        decided_by="reviewer-002",
        decided_at=datetime(2026, 12, 15, tzinfo=UTC),
        reason="Approved structure revision becomes effective next year.",
    )
    assert decision.predecessor_revision == 1
    assert decision.successor_revision == 3
    assert decision.predecessor_effective_to == date(2026, 12, 31)
    assert open_registry.supersession_decisions == (decision,)
    before_result = build_mapping_preview(
        _scan(inspection_date="2025-12-31"),
        _request(),
        open_registry,
    )
    old_result = build_mapping_preview(
        _scan(inspection_date="2026-12-31"),
        _request(),
        open_registry,
    )
    new_result = build_mapping_preview(
        _scan(inspection_date="2027-01-01"),
        _request(),
        open_registry,
    )
    predecessor_not_effective = next(
        issue
        for issue in before_result.issues
        if issue.code == MappingIssueCode.TEMPLATE_NOT_EFFECTIVE and issue.template_revision == 1
    )
    assert predecessor_not_effective.expected == "2026-01-01..2026-12-31"
    assert old_result.preview is not None
    assert old_result.preview.template_revision == 1
    assert old_result.preview.template_effective_to == date(2026, 12, 31)
    assert new_result.preview is not None
    assert new_result.preview.template_revision == 3

    with pytest.raises(ValueError, match="effective_to"):
        _template(
            effective_from=date(2026, 5, 1),
            effective_to=date(2026, 4, 30),
        )
    with pytest.raises(ValueError, match="unsupported"):
        _template(schema_version="999")

    draft = build_mapping_preview(
        _scan(),
        _request(),
        _registry(_template(status=MappingTemplateStatus.DRAFT)),
    )
    future = build_mapping_preview(
        _scan(),
        _request(),
        _registry(
            _template(
                effective_from=date(2027, 1, 1),
                effective_to=date(2027, 12, 31),
            )
        ),
    )
    expired = build_mapping_preview(
        _scan(),
        _request(),
        _registry(
            _template(
                effective_from=date(2025, 1, 1),
                effective_to=date(2025, 12, 31),
            )
        ),
    )
    assert {issue.code for issue in draft.issues} == {MappingIssueCode.TEMPLATE_NOT_APPROVED}
    assert {issue.code for issue in future.issues} == {MappingIssueCode.TEMPLATE_NOT_EFFECTIVE}
    assert {issue.code for issue in expired.issues} == {MappingIssueCode.TEMPLATE_NOT_EFFECTIVE}

    invalid_source_date = build_mapping_preview(
        _scan(inspection_date="12/04/2026"),
        _request(),
        _registry(_template()),
    )
    assert {issue.code for issue in invalid_source_date.issues} == {
        MappingIssueCode.INSPECTION_DATE_INVALID
    }
    assert invalid_source_date.preview is None

    duplicate_identifiers = (
        IdentifierMapping(IdentifierKind.MODEL, _address("A2")),
        IdentifierMapping(IdentifierKind.INSPECTION_DATE, _address("A2")),
    )
    with pytest.raises(ValueError, match="source cells"):
        replace(_template(), identifiers=duplicate_identifiers)


@pytest.mark.required_test_id("DQ-P1-MAP-003")
def test_exact_fingerprint_reuses_template_when_volatile_values_change() -> None:
    registry = _registry(_template())
    first = build_mapping_preview(_scan(), _request(), registry)
    changed = build_mapping_preview(
        _scan(
            model="MODEL-BETA",
            lot="LOT-999",
            inspection_date="2026-04-13",
            numeric_samples=(8.2, 8.3, 8.4),
            qualitative_samples=("Uniform", "Uniform"),
            numeric_supplier_result="NG",
        ),
        _request(),
        registry,
    )

    assert first.state == changed.state == MappingPreviewState.PREVIEW_READY
    assert changed.preview is not None
    changed_identifiers = {item.kind: item.evidence for item in changed.preview.identifiers}
    assert changed_identifiers[IdentifierKind.LOT_NUMBER].raw_value == "LOT-999"
    assert [sample.raw_value for sample in changed.preview.inspection_rows[0].samples] == [
        8.2,
        8.3,
        8.4,
    ]


@pytest.mark.required_test_id("DQ-P1-MAP-004")
def test_fingerprint_and_missing_or_ambiguous_mapping_fail_closed_with_diffs() -> None:
    changed_scan = _scan(title="Changed Layout")
    changed_sheet = replace(
        changed_scan.sheets[0],
        visibility="hidden",
        merged_ranges=(),
        cells=tuple(cell for cell in changed_scan.sheets[0].cells if cell.coordinate != "J4"),
    )
    mismatch = build_mapping_preview(
        replace(changed_scan, sheets=(changed_sheet,)),
        _request(),
        _registry(_template()),
    )
    assert mismatch.state == MappingPreviewState.MAPPING_REQUIRED
    assert mismatch.preview is None
    mismatch_codes = {issue.code for issue in mismatch.issues}
    assert MappingIssueCode.FINGERPRINT_HEADER_MISMATCH in mismatch_codes
    assert MappingIssueCode.FINGERPRINT_MERGE_MISMATCH in mismatch_codes
    assert MappingIssueCode.FINGERPRINT_ROW_STRUCTURE_MISMATCH in mismatch_codes
    assert all(
        issue.expected is not None and issue.observed is not None for issue in mismatch.issues
    )

    missing = build_mapping_preview(
        _scan(omit_model_cell=True),
        _request(),
        _registry(_template()),
    )
    assert {issue.code for issue in missing.issues} == {MappingIssueCode.MAPPED_CELL_MISSING}
    assert missing.preview is None

    ambiguous_registry = _registry(
        _template(template_id="format-one"),
        _template(template_id="format-two"),
    )
    ambiguous = build_mapping_preview(_scan(), _request(), ambiguous_registry)
    assert {issue.code for issue in ambiguous.issues} == {MappingIssueCode.AMBIGUOUS_TEMPLATE_MATCH}
    assert ambiguous.official_values_created is False

    mutated_scan = replace(_scan(), source_sha256_after="b" * 64)
    mutated = build_mapping_preview(mutated_scan, _request(), _registry(_template()))
    assert {issue.code for issue in mutated.issues} == {MappingIssueCode.SOURCE_HASH_MISMATCH}
    assert mutated.preview is None


@pytest.mark.required_test_id("DQ-P1-MAP-005")
def test_supplier_result_and_spec_remain_separate_without_system_judgment() -> None:
    result = build_mapping_preview(
        _scan(numeric_supplier_result="NG"),
        _request(),
        _registry(_template()),
    )
    assert result.preview is not None
    numeric_row = result.preview.inspection_rows[0]
    assert numeric_row.specification.source == _address("D4")
    assert numeric_row.specification.raw_value == "Nominal requirement"
    assert numeric_row.supplier_result.source == _address("K4")
    assert numeric_row.supplier_result.raw_value == "NG"
    assert numeric_row.specification.source != numeric_row.supplier_result.source
    assert all(
        row.system_judgment_status == SystemJudgmentStatus.NOT_EVALUATED
        and row.system_judgment is None
        for row in result.preview.inspection_rows
    )
    assert result.calculations_performed is False

    wrong_supplier = build_mapping_preview(
        _scan(),
        _request(),
        _registry(_template(supplier_source_aliases=("ANOTHER-SUPPLIER",))),
    )
    assert {issue.code for issue in wrong_supplier.issues} == {
        MappingIssueCode.SUPPLIER_EVIDENCE_MISMATCH
    }
    assert wrong_supplier.preview is None


@pytest.mark.required_test_id("DQ-P1-MAP-006")
def test_generic_template_preserves_variable_numeric_and_qualitative_samples() -> None:
    supplier_scope = "arbitrary-scope-zeta"
    template = _template(supplier_scope=supplier_scope)
    result = build_mapping_preview(
        _scan(),
        _request(supplier_scope=supplier_scope),
        _registry(template),
    )

    assert result.preview is not None
    numeric_row, qualitative_row = result.preview.inspection_rows
    assert len(numeric_row.samples) == 3
    assert len(qualitative_row.samples) == 2
    assert {sample.value_kind for sample in numeric_row.samples} == {PreviewValueKind.NUMERIC}
    assert {sample.value_kind for sample in qualitative_row.samples} == {
        PreviewValueKind.QUALITATIVE
    }
    assert numeric_row.maximum.value_kind == PreviewValueKind.FORMULA
    assert qualitative_row.method.raw_value == "Visual method"
    assert result.preview.supplier_scope == supplier_scope

    optional_qualitative = replace(
        template.inspection_rows[1],
        method=None,
        instrument=None,
        specification=None,
        tolerance=None,
        minimum=None,
        maximum=None,
        sample_cells=(),
    )
    optional_template = replace(
        template,
        template_id="generic-optional-layout",
        inspection_rows=(template.inspection_rows[0], optional_qualitative),
    )
    optional_result = build_mapping_preview(
        _scan(),
        _request(supplier_scope=supplier_scope),
        _registry(optional_template),
    )
    assert optional_result.preview is not None
    optional_row = optional_result.preview.inspection_rows[1]
    assert optional_row.method is None
    assert optional_row.samples == ()
    assert optional_row.supplier_result is not None

    with pytest.raises(ValueError, match="valid canonical"):
        CellAddress(sheet_name=_SHEET, coordinate="XFE1")
    with pytest.raises(ValueError, match="valid canonical"):
        CellAddress(sheet_name=_SHEET, coordinate="A1048577")


@pytest.mark.required_test_id("DQ-P1-MAP-007")
def test_project_and_supplier_scopes_are_isolated() -> None:
    registry = _registry(_template())

    wrong_project = build_mapping_preview(
        _scan(),
        _request(project_key="project-beta"),
        registry,
    )
    wrong_supplier = build_mapping_preview(
        _scan(),
        _request(supplier_scope="supplier-scope-beta"),
        registry,
    )
    unrelated = build_mapping_preview(
        _scan(),
        _request(project_key="project-gamma", supplier_scope="supplier-scope-gamma"),
        registry,
    )

    assert {issue.code for issue in wrong_project.issues} == {
        MappingIssueCode.PROJECT_SCOPE_MISMATCH
    }
    assert {issue.code for issue in wrong_supplier.issues} == {
        MappingIssueCode.SUPPLIER_SCOPE_MISMATCH
    }
    assert {issue.code for issue in unrelated.issues} == {MappingIssueCode.TEMPLATE_MISSING}
    assert all(
        result.state == MappingPreviewState.MAPPING_REQUIRED and result.preview is None
        for result in (wrong_project, wrong_supplier, unrelated)
    )
