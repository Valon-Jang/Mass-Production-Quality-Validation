"""Pure mapping-template selection and source-evidence preview application service."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Protocol

from app.domain.mapping import (
    CellAddress,
    IdentifierKind,
    IdentifierPreview,
    InspectionRowPreview,
    MappedCellEvidence,
    MappingIssue,
    MappingIssueCode,
    MappingPreview,
    MappingPreviewRequest,
    MappingPreviewResult,
    MappingPreviewState,
    MappingTemplate,
    MappingTemplateStatus,
    PreviewValueKind,
    TemplateHistoryError,
    TemplateHistoryErrorCode,
    TemplateSupersessionDecision,
)
from app.domain.workbook_scan import CellEvidence, SheetScan, WorkbookScan

_COORDINATE_ROW_PATTERN = re.compile(r"^[A-Z]{1,3}([1-9][0-9]*)$")


class MappingTemplateCatalog(Protocol):
    """Read-only template view used by Preview regardless of storage adapter."""

    @property
    def templates(self) -> Sequence[MappingTemplate]: ...

    def is_effective_on(self, template: MappingTemplate, value: date) -> bool: ...

    def resolved_effective_to(self, template: MappingTemplate) -> date | None: ...


class InMemoryMappingTemplateRegistry:
    """Append-only in-memory revision history for a bounded Phase 1 slice."""

    def __init__(self) -> None:
        self._templates: list[MappingTemplate] = []
        self._superseded_effective_to: dict[tuple[str, str, str, int], date] = {}
        self._supersession_decisions: list[TemplateSupersessionDecision] = []

    @property
    def templates(self) -> tuple[MappingTemplate, ...]:
        return tuple(self._templates)

    @property
    def supersession_decisions(self) -> tuple[TemplateSupersessionDecision, ...]:
        return tuple(self._supersession_decisions)

    def register(self, template: MappingTemplate) -> None:
        history = self._history_for(template)
        self._validate_revision_append(template, history)
        if template.status == MappingTemplateStatus.APPROVED and any(
            existing.status == MappingTemplateStatus.APPROVED
            and self._periods_overlap(existing, template)
            for existing in history
        ):
            raise TemplateHistoryError(
                TemplateHistoryErrorCode.EFFECTIVE_PERIOD_OVERLAP,
                "approved template revisions in one scoped history cannot overlap",
            )
        self._templates.append(template)

    def supersede(
        self,
        successor: MappingTemplate,
        *,
        decided_by: str,
        decided_at: datetime,
        reason: str,
    ) -> TemplateSupersessionDecision:
        """Atomically close one open approved predecessor and append its successor."""

        if successor.status != MappingTemplateStatus.APPROVED:
            raise TemplateHistoryError(
                TemplateHistoryErrorCode.SUPERSESSION_REQUIRES_APPROVED,
                "a superseding template must already be approved by the caller's workflow",
            )
        history = self._history_for(successor)
        self._validate_revision_append(successor, history)
        candidates = [
            existing
            for existing in history
            if existing.status == MappingTemplateStatus.APPROVED
            and existing.effective_from < successor.effective_from
            and self._effective_end(existing) >= successor.effective_from
        ]
        if len(candidates) != 1:
            raise TemplateHistoryError(
                TemplateHistoryErrorCode.SUPERSESSION_PREDECESSOR_MISSING,
                "supersession requires exactly one approved predecessor spanning the start date",
            )
        predecessor = candidates[0]
        predecessor_end = successor.effective_from - timedelta(days=1)
        decision = TemplateSupersessionDecision(
            project_key=successor.project_key,
            supplier_scope=successor.supplier_scope,
            template_id=successor.template_id,
            predecessor_revision=predecessor.revision,
            successor_revision=successor.revision,
            predecessor_effective_to=predecessor_end,
            decided_by=decided_by,
            decided_at=decided_at,
            reason=reason,
        )
        predecessor_key = self._template_key(predecessor)
        self._superseded_effective_to[predecessor_key] = predecessor_end
        conflicts = [
            existing
            for existing in history
            if existing is not predecessor
            and existing.status == MappingTemplateStatus.APPROVED
            and self._periods_overlap(existing, successor)
        ]
        if conflicts:
            del self._superseded_effective_to[predecessor_key]
            raise TemplateHistoryError(
                TemplateHistoryErrorCode.EFFECTIVE_PERIOD_OVERLAP,
                "successor still overlaps another approved revision",
            )
        self._templates.append(successor)
        self._supersession_decisions.append(decision)
        return decision

    def is_effective_on(self, template: MappingTemplate, value: date) -> bool:
        return template.effective_from <= value <= self._effective_end(template)

    def resolved_effective_to(self, template: MappingTemplate) -> date | None:
        """Return the immutable declaration or an audited supersession boundary."""

        return self._superseded_effective_to.get(
            self._template_key(template),
            template.effective_to,
        )

    def _history_for(self, template: MappingTemplate) -> list[MappingTemplate]:
        return [
            existing
            for existing in self._templates
            if existing.project_key == template.project_key
            and existing.supplier_scope == template.supplier_scope
            and existing.template_id == template.template_id
        ]

    @staticmethod
    def _validate_revision_append(
        template: MappingTemplate,
        history: list[MappingTemplate],
    ) -> None:
        if any(existing.revision == template.revision for existing in history):
            raise TemplateHistoryError(
                TemplateHistoryErrorCode.REVISION_OVERWRITE,
                "a registered template revision is immutable",
            )
        if history and template.revision < max(existing.revision for existing in history):
            raise TemplateHistoryError(
                TemplateHistoryErrorCode.REVISION_DOWNGRADE,
                "a lower revision cannot be appended after a higher revision",
            )

    def _periods_overlap(self, left: MappingTemplate, right: MappingTemplate) -> bool:
        left_end = self._effective_end(left)
        right_end = self._effective_end(right)
        return left.effective_from <= right_end and right.effective_from <= left_end

    def _effective_end(self, template: MappingTemplate) -> date:
        return self.resolved_effective_to(template) or date.max

    @staticmethod
    def _template_key(template: MappingTemplate) -> tuple[str, str, str, int]:
        return (
            template.project_key,
            template.supplier_scope,
            template.template_id,
            template.revision,
        )


def build_mapping_preview(
    scan: WorkbookScan,
    request: MappingPreviewRequest,
    registry: MappingTemplateCatalog,
) -> MappingPreviewResult:
    """Select one approved exact match and expose evidence without official judgment."""

    if scan.source_sha256_before != scan.source_sha256_after:
        return _mapping_required(
            MappingIssue(
                code=MappingIssueCode.SOURCE_HASH_MISMATCH,
                message="Workbook identity changed before Mapping Preview.",
                expected=scan.source_sha256_before,
                observed=scan.source_sha256_after,
            )
        )

    scoped_templates = tuple(
        template
        for template in registry.templates
        if template.project_key == request.project_key
        and template.supplier_scope == request.supplier_scope
    )
    if not scoped_templates:
        return _scope_or_missing_result(request, registry.templates)

    evaluations = tuple(
        (template, _fingerprint_issues(scan, template)) for template in scoped_templates
    )
    fingerprint_matches = tuple(template for template, issues in evaluations if not issues)
    eligible: list[tuple[MappingTemplate, date]] = []
    state_issues: list[MappingIssue] = []
    for template in fingerprint_matches:
        if template.status != MappingTemplateStatus.APPROVED:
            state_issues.append(
                _template_issue(
                    MappingIssueCode.TEMPLATE_NOT_APPROVED,
                    template,
                    "Only an approved template may be applied automatically.",
                    expected=MappingTemplateStatus.APPROVED.value,
                    observed=template.status.value,
                )
            )
            continue

        inspection_date, inspection_date_issue = _source_inspection_date(scan, template)
        if inspection_date_issue is not None:
            state_issues.append(inspection_date_issue)
        supplier_issue = _supplier_evidence_issue(scan, template)
        if supplier_issue is not None:
            state_issues.append(supplier_issue)
        if inspection_date is not None and not registry.is_effective_on(template, inspection_date):
            resolved_effective_to = registry.resolved_effective_to(template)
            period = (
                f"{template.effective_from.isoformat()}.."
                f"{resolved_effective_to.isoformat() if resolved_effective_to else 'OPEN'}"
            )
            state_issues.append(
                _template_issue(
                    MappingIssueCode.TEMPLATE_NOT_EFFECTIVE,
                    template,
                    "Approved template is not effective on the source inspection date.",
                    expected=period,
                    observed=inspection_date.isoformat(),
                )
            )
        if (
            inspection_date is not None
            and inspection_date_issue is None
            and supplier_issue is None
            and registry.is_effective_on(template, inspection_date)
        ):
            eligible.append((template, inspection_date))

    if len(eligible) > 1:
        return _mapping_required(
            MappingIssue(
                code=MappingIssueCode.AMBIGUOUS_TEMPLATE_MATCH,
                message="More than one approved, effective template exactly matched.",
                expected="one matching template",
                observed=", ".join(
                    f"{template.template_id}@{template.revision}" for template, _ in eligible
                ),
            )
        )
    if len(eligible) == 1:
        template, inspection_date = eligible[0]
        preview, extraction_issues = _extract_preview(
            scan,
            template,
            inspection_date,
            registry.resolved_effective_to(template),
        )
        if extraction_issues:
            return _mapping_required(*extraction_issues)
        if preview is None:  # pragma: no cover - protected by extraction contract
            raise AssertionError("preview extraction returned neither data nor issues")
        return MappingPreviewResult(
            state=MappingPreviewState.PREVIEW_READY,
            preview=preview,
            issues=(),
        )

    if fingerprint_matches:
        return _mapping_required(*state_issues)

    mismatch_issues = tuple(issue for _, issues in evaluations for issue in issues)
    return _mapping_required(*mismatch_issues)


def _scope_or_missing_result(
    request: MappingPreviewRequest,
    templates: Sequence[MappingTemplate],
) -> MappingPreviewResult:
    if not templates:
        return _mapping_required(
            MappingIssue(
                code=MappingIssueCode.TEMPLATE_MISSING,
                message="No mapping template is registered.",
            )
        )

    issues: list[MappingIssue] = []
    if any(template.supplier_scope == request.supplier_scope for template in templates):
        issues.append(
            MappingIssue(
                code=MappingIssueCode.PROJECT_SCOPE_MISMATCH,
                message="Templates for this supplier scope belong to another project.",
                expected=request.project_key,
                observed=", ".join(
                    sorted(
                        {
                            template.project_key
                            for template in templates
                            if template.supplier_scope == request.supplier_scope
                        }
                    )
                ),
            )
        )
    if any(template.project_key == request.project_key for template in templates):
        issues.append(
            MappingIssue(
                code=MappingIssueCode.SUPPLIER_SCOPE_MISMATCH,
                message="Templates for this project belong to another supplier scope.",
                expected=request.supplier_scope,
                observed=", ".join(
                    sorted(
                        {
                            template.supplier_scope
                            for template in templates
                            if template.project_key == request.project_key
                        }
                    )
                ),
            )
        )
    if not issues:
        issues.append(
            MappingIssue(
                code=MappingIssueCode.TEMPLATE_MISSING,
                message="No template exists for the requested project and supplier scope.",
            )
        )
    return _mapping_required(*issues)


def _fingerprint_issues(
    scan: WorkbookScan,
    template: MappingTemplate,
) -> tuple[MappingIssue, ...]:
    issues: list[MappingIssue] = []
    fingerprint = template.fingerprint

    ordered_sheet_assertions = tuple(
        sorted(
            fingerprint.sheet_structures,
            key=lambda assertion: assertion.expected_position,
        )
    )
    expected_sheet_order = tuple(assertion.sheet_name for assertion in ordered_sheet_assertions)
    observed_sheet_order = tuple(
        sheet.name for sheet in sorted(scan.sheets, key=lambda sheet: sheet.position)
    )
    if observed_sheet_order != expected_sheet_order:
        issues.append(
            _fingerprint_issue(
                MappingIssueCode.FINGERPRINT_SHEET_MISMATCH,
                template,
                None,
                None,
                "Workbook sheet names, count, or order changed.",
                repr(expected_sheet_order),
                repr(observed_sheet_order),
            )
        )

    for sheet_assertion in fingerprint.sheet_structures:
        sheets = _matching_sheets(scan, sheet_assertion.sheet_name)
        expected = (
            f"{sheet_assertion.expected_position}|{sheet_assertion.expected_kind.value}|"
            f"{sheet_assertion.expected_visibility}|{sheet_assertion.expected_used_range}"
        )
        if len(sheets) != 1:
            issues.append(
                _fingerprint_issue(
                    MappingIssueCode.FINGERPRINT_SHEET_MISMATCH,
                    template,
                    sheet_assertion.sheet_name,
                    None,
                    "Sheet structure assertion did not resolve exactly one sheet.",
                    expected,
                    "MISSING" if not sheets else "AMBIGUOUS",
                )
            )
            continue
        sheet = sheets[0]
        observed = f"{sheet.position}|{sheet.kind.value}|{sheet.visibility}|{sheet.used_range}"
        if observed != expected:
            issues.append(
                _fingerprint_issue(
                    MappingIssueCode.FINGERPRINT_SHEET_MISMATCH,
                    template,
                    sheet_assertion.sheet_name,
                    None,
                    "Sheet kind, visibility, or used range changed.",
                    expected,
                    observed,
                )
            )

    for header_assertion in fingerprint.header_tokens:
        cells = _matching_cells(scan, header_assertion.source)
        expected = _normalize_token(header_assertion.expected_token)
        if len(cells) != 1:
            issues.append(
                _fingerprint_issue(
                    MappingIssueCode.FINGERPRINT_HEADER_MISMATCH,
                    template,
                    header_assertion.source.sheet_name,
                    header_assertion.source.coordinate,
                    "Header token cell did not resolve exactly once.",
                    expected,
                    "MISSING" if not cells else "AMBIGUOUS",
                )
            )
            continue
        observed = _normalize_token(cells[0].stored_value)
        if observed != expected:
            issues.append(
                _fingerprint_issue(
                    MappingIssueCode.FINGERPRINT_HEADER_MISMATCH,
                    template,
                    header_assertion.source.sheet_name,
                    header_assertion.source.coordinate,
                    "Header token changed.",
                    expected,
                    observed,
                )
            )

    for merge_assertion in fingerprint.merge_signatures:
        sheets = _matching_sheets(scan, merge_assertion.sheet_name)
        expected_ranges = tuple(sorted(merge_assertion.expected_merged_ranges))
        if len(sheets) != 1:
            observed = "MISSING" if not sheets else "AMBIGUOUS"
        else:
            observed = repr(tuple(sorted(sheets[0].merged_ranges)))
        expected = repr(expected_ranges)
        if observed != expected:
            issues.append(
                _fingerprint_issue(
                    MappingIssueCode.FINGERPRINT_MERGE_MISMATCH,
                    template,
                    merge_assertion.sheet_name,
                    None,
                    "Merged-range signature changed.",
                    expected,
                    observed,
                )
            )

    for row_assertion in fingerprint.row_structures:
        sheets = _matching_sheets(scan, row_assertion.sheet_name)
        expected_coordinates = tuple(
            sorted(source.coordinate for source in row_assertion.expected_non_empty_cells)
        )
        if len(sheets) != 1:
            observed = "MISSING" if not sheets else "AMBIGUOUS"
        else:
            observed = repr(
                tuple(
                    sorted(
                        cell.coordinate
                        for cell in sheets[0].cells
                        if _coordinate_row(cell.coordinate) == row_assertion.row_index
                    )
                )
            )
        expected = repr(expected_coordinates)
        if observed != expected:
            issues.append(
                _fingerprint_issue(
                    MappingIssueCode.FINGERPRINT_ROW_STRUCTURE_MISMATCH,
                    template,
                    row_assertion.sheet_name,
                    None,
                    f"Cell/sample structure changed for row key {row_assertion.row_key}.",
                    expected,
                    observed,
                )
            )
    return tuple(issues)


def _source_inspection_date(
    scan: WorkbookScan,
    template: MappingTemplate,
) -> tuple[date | None, MappingIssue | None]:
    mapping = next(
        mapping
        for mapping in template.identifiers
        if mapping.kind == IdentifierKind.INSPECTION_DATE
    )
    evidence, source_issue = _map_cell(scan, template, mapping.source)
    if source_issue is not None:
        return None, source_issue
    if evidence is None:  # pragma: no cover - protected by _map_cell
        raise AssertionError("inspection date mapping returned no evidence")

    value = evidence.raw_value
    parsed: date | None = None
    if evidence.formula_text is None:
        if isinstance(value, datetime):
            parsed = value.date()
        elif isinstance(value, date):
            parsed = value
        elif isinstance(value, str):
            try:
                parsed = date.fromisoformat(value.strip())
            except ValueError:
                parsed = None
    if parsed is not None:
        return parsed, None
    return None, _template_issue(
        MappingIssueCode.INSPECTION_DATE_INVALID,
        template,
        "Source inspection date is missing, formula-dependent, or not ISO/date typed.",
        sheet_name=mapping.source.sheet_name,
        coordinate=mapping.source.coordinate,
        expected="typed date, datetime, or ISO YYYY-MM-DD",
        observed=repr(value),
    )


def _supplier_evidence_issue(
    scan: WorkbookScan,
    template: MappingTemplate,
) -> MappingIssue | None:
    mapping = next(
        mapping for mapping in template.identifiers if mapping.kind == IdentifierKind.SUPPLIER
    )
    evidence, source_issue = _map_cell(scan, template, mapping.source)
    if source_issue is not None:
        return source_issue
    if evidence is None:  # pragma: no cover - protected by _map_cell
        raise AssertionError("supplier mapping returned no evidence")
    observed = _normalize_token(evidence.raw_value)
    allowed = {_normalize_token(alias) for alias in template.supplier_source_aliases}
    if observed in allowed:
        return None
    return _template_issue(
        MappingIssueCode.SUPPLIER_EVIDENCE_MISMATCH,
        template,
        "Workbook supplier evidence is outside the approved template aliases.",
        sheet_name=mapping.source.sheet_name,
        coordinate=mapping.source.coordinate,
        expected=repr(tuple(sorted(allowed))),
        observed=observed,
    )


def _extract_preview(
    scan: WorkbookScan,
    template: MappingTemplate,
    inspection_date: date,
    resolved_effective_to: date | None,
) -> tuple[MappingPreview | None, tuple[MappingIssue, ...]]:
    issues: list[MappingIssue] = []
    identifiers: list[IdentifierPreview] = []
    rows: list[InspectionRowPreview] = []

    for identifier_mapping in template.identifiers:
        evidence, issue = _map_cell(scan, template, identifier_mapping.source)
        if issue is not None:
            issues.append(issue)
        elif evidence is not None:
            identifiers.append(IdentifierPreview(kind=identifier_mapping.kind, evidence=evidence))

    for row_mapping in template.inspection_rows:
        row_values: dict[CellAddress, MappedCellEvidence] = {}
        row_has_issue = False
        for source in row_mapping.all_addresses:
            evidence, issue = _map_cell(scan, template, source)
            if issue is not None:
                issues.append(issue)
                row_has_issue = True
            elif evidence is not None:
                row_values[source] = evidence
        if row_has_issue:
            continue
        if len(row_values) != len(row_mapping.all_addresses):  # pragma: no cover
            raise AssertionError("mapped row evidence count is inconsistent")
        rows.append(
            InspectionRowPreview(
                row_key=row_mapping.row_key,
                item=row_values[row_mapping.item],
                method=_optional_evidence(row_values, row_mapping.method),
                instrument=_optional_evidence(row_values, row_mapping.instrument),
                specification=_optional_evidence(row_values, row_mapping.specification),
                tolerance=_optional_evidence(row_values, row_mapping.tolerance),
                minimum=_optional_evidence(row_values, row_mapping.minimum),
                maximum=_optional_evidence(row_values, row_mapping.maximum),
                section=_optional_evidence(row_values, row_mapping.section),
                category=_optional_evidence(row_values, row_mapping.category),
                unit=_optional_evidence(row_values, row_mapping.unit),
                measurement_point=_optional_evidence(row_values, row_mapping.measurement_point),
                measurement_location=_optional_evidence(
                    row_values, row_mapping.measurement_location
                ),
                cavity=_optional_evidence(row_values, row_mapping.cavity),
                target=_optional_evidence(row_values, row_mapping.target),
                lsl=_optional_evidence(row_values, row_mapping.lsl),
                usl=_optional_evidence(row_values, row_mapping.usl),
                source_spec_revision=_optional_evidence(
                    row_values, row_mapping.source_spec_revision
                ),
                samples=tuple(row_values[source] for source in row_mapping.sample_cells),
                supplier_result=_optional_evidence(row_values, row_mapping.supplier_result),
            )
        )

    if issues:
        return None, tuple(issues)
    return (
        MappingPreview(
            source_name=scan.source_name,
            source_size_bytes=scan.source_size_bytes,
            source_sha256_before=scan.source_sha256_before,
            source_sha256_after=scan.source_sha256_after,
            source_issues=scan.issues,
            is_golden_workbook_evidence=scan.is_golden_workbook_evidence,
            template_id=template.template_id,
            template_schema_version=template.schema_version,
            template_revision=template.revision,
            template_approved_by=_approved_by(template),
            template_approved_at=_approved_at(template),
            template_effective_from=template.effective_from,
            template_effective_to=resolved_effective_to,
            source_inspection_date=inspection_date,
            project_key=template.project_key,
            supplier_scope=template.supplier_scope,
            identifiers=tuple(identifiers),
            inspection_rows=tuple(rows),
        ),
        (),
    )


def _optional_evidence(
    values: dict[CellAddress, MappedCellEvidence],
    source: CellAddress | None,
) -> MappedCellEvidence | None:
    return None if source is None else values[source]


def _approved_by(template: MappingTemplate) -> str:
    if template.approved_by is None:  # pragma: no cover - approved template invariant
        raise AssertionError("approved template has no approver")
    return template.approved_by


def _approved_at(template: MappingTemplate) -> datetime:
    if template.approved_at is None:  # pragma: no cover - approved template invariant
        raise AssertionError("approved template has no approval time")
    return template.approved_at


def _map_cell(
    scan: WorkbookScan,
    template: MappingTemplate,
    source: CellAddress,
) -> tuple[MappedCellEvidence | None, MappingIssue | None]:
    cells = _matching_cells(scan, source)
    if len(cells) != 1:
        code = (
            MappingIssueCode.MAPPED_CELL_MISSING
            if not cells
            else MappingIssueCode.MAPPED_CELL_AMBIGUOUS
        )
        observed = "MISSING" if not cells else f"{len(cells)} MATCHES"
        return None, _template_issue(
            code,
            template,
            "Mapped source cell did not resolve exactly once.",
            sheet_name=source.sheet_name,
            coordinate=source.coordinate,
            expected="one source cell",
            observed=observed,
        )
    cell = cells[0]
    return (
        MappedCellEvidence(
            source=source,
            raw_value=cell.stored_value,
            cached_value=cell.cached_value,
            formula_text=cell.formula_text,
            number_format=cell.number_format,
            data_type=cell.data_type,
            display_value=cell.display_value,
            display_value_status=cell.display_value_status,
            value_kind=_value_kind(cell),
        ),
        None,
    )


def _value_kind(cell: CellEvidence) -> PreviewValueKind:
    value = cell.stored_value
    if cell.formula_text is not None:
        return PreviewValueKind.FORMULA
    if isinstance(value, bool):
        return PreviewValueKind.BOOLEAN
    if isinstance(value, (int, float, Decimal)):
        return PreviewValueKind.NUMERIC
    if isinstance(value, str):
        return PreviewValueKind.QUALITATIVE
    if isinstance(value, date):
        return PreviewValueKind.TEMPORAL
    return PreviewValueKind.OTHER


def _matching_sheets(scan: WorkbookScan, sheet_name: str) -> tuple[SheetScan, ...]:
    return tuple(sheet for sheet in scan.sheets if sheet.name == sheet_name)


def _matching_cells(scan: WorkbookScan, source: CellAddress) -> tuple[CellEvidence, ...]:
    return tuple(
        cell
        for sheet in _matching_sheets(scan, source.sheet_name)
        for cell in sheet.cells
        if cell.coordinate == source.coordinate
    )


def _coordinate_row(coordinate: str) -> int | None:
    match = _COORDINATE_ROW_PATTERN.fullmatch(coordinate)
    return None if match is None else int(match.group(1))


def _normalize_token(value: object) -> str:
    return " ".join(str(value).strip().casefold().split())


def _fingerprint_issue(
    code: MappingIssueCode,
    template: MappingTemplate,
    sheet_name: str | None,
    coordinate: str | None,
    message: str,
    expected: str,
    observed: str,
) -> MappingIssue:
    return _template_issue(
        code,
        template,
        message,
        sheet_name=sheet_name,
        coordinate=coordinate,
        expected=expected,
        observed=observed,
    )


def _template_issue(
    code: MappingIssueCode,
    template: MappingTemplate,
    message: str,
    *,
    sheet_name: str | None = None,
    coordinate: str | None = None,
    expected: str | None = None,
    observed: str | None = None,
) -> MappingIssue:
    return MappingIssue(
        code=code,
        message=message,
        template_id=template.template_id,
        template_revision=template.revision,
        sheet_name=sheet_name,
        coordinate=coordinate,
        expected=expected,
        observed=observed,
    )


def _mapping_required(*issues: MappingIssue) -> MappingPreviewResult:
    return MappingPreviewResult(
        state=MappingPreviewState.MAPPING_REQUIRED,
        preview=None,
        issues=tuple(issues),
    )
