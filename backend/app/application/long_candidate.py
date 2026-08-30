"""Pure conversion from an approved Mapping Preview to a pending Long candidate."""

from __future__ import annotations

import math
from dataclasses import replace
from datetime import date
from decimal import Decimal

from app.application.store_scan_mapping import (
    StoreScanMappingOutcome,
    StoreScanMappingStatus,
)
from app.domain.long_format import (
    CanonicalRowBinding,
    CanonicalRowBindingCatalog,
    CanonicalRowBindingKey,
    CanonicalRowBindingSelectionSignature,
    CanonicalRowBindingStatus,
    LongCandidateIssue,
    LongCandidateResult,
    LongCandidateState,
    LongInspectionCandidate,
    LongIssueCode,
    LongIssueScope,
    LongMeasurementCandidate,
    LongRowState,
    LongSourceProvenance,
    MeasurementMode,
    RawNumericValue,
    SamplePolicy,
)
from app.domain.mapping import (
    IdentifierKind,
    InspectionRowPreview,
    MappedCellEvidence,
    MappingPreview,
    MappingPreviewState,
    PreviewValueKind,
)
from app.domain.workbook_scan import SourceLocationKind


class LongCandidateInputError(ValueError):
    """The caller did not provide a completed PREVIEW_READY route outcome."""


def build_long_candidate(
    source: StoreScanMappingOutcome,
    catalog: CanonicalRowBindingCatalog,
) -> LongCandidateResult:
    """Build evidence-only PENDING rows without I/O, calculations, or official judgment."""

    preview = _require_ready_preview(source)
    catalog_revision = catalog.catalog_revision
    if not catalog_revision.strip() or catalog_revision != catalog_revision.strip():
        raise LongCandidateInputError("binding catalog revision must be an exact non-blank value")

    global_issues = _source_provenance_issues(source, preview)
    source_model = _identifier(preview, IdentifierKind.MODEL)
    source_lot = _identifier(preview, IdentifierKind.LOT_NUMBER)
    model_value = _exact_text_value(source_model)
    lot_value = _exact_text_value(source_lot)

    if model_value is None:
        global_issues.append(
            _source_issue(
                LongIssueCode.MODEL_IDENTIFIER_MISSING,
                "An exact text model identifier is required for a Long candidate.",
                source_model,
            )
        )
    elif source.receipt.model_candidates and model_value not in source.receipt.model_candidates:
        global_issues.append(
            _source_issue(
                LongIssueCode.MODEL_CANDIDATE_CONFLICT,
                "Workbook model evidence conflicts with the preserved intake candidates.",
                source_model,
                expected=repr(source.receipt.model_candidates),
                observed=model_value,
            )
        )

    if lot_value is None:
        global_issues.append(
            _source_issue(
                LongIssueCode.LOT_IDENTIFIER_MISSING,
                "An exact text LOT identifier is required for a Long candidate.",
                source_lot,
            )
        )
    elif source.receipt.lot_candidates and lot_value not in source.receipt.lot_candidates:
        global_issues.append(
            _source_issue(
                LongIssueCode.LOT_CANDIDATE_CONFLICT,
                "Workbook LOT evidence conflicts with the preserved intake candidates.",
                source_lot,
                expected=repr(source.receipt.lot_candidates),
                observed=lot_value,
            )
        )

    row_builds = tuple(
        _build_row(
            row,
            preview,
            model_value=model_value,
            catalog=catalog,
        )
        for row in preview.inspection_rows
    )
    rows = tuple(row for row, _selection in row_builds)
    binding_selections = tuple(selection for _row, selection in row_builds)
    global_issues.extend(_canonical_identity_issues(rows))
    issues = (*global_issues, *(issue for row in rows for issue in row.issues))
    if global_issues:
        rows = tuple(
            replace(
                row,
                state=LongRowState.ROW_HELD,
                issues=(*row.issues, *global_issues),
            )
            for row in rows
        )

    loadable_count = sum(row.state == LongRowState.LOADABLE_PENDING for row in rows)
    held_count = len(rows) - loadable_count
    if global_issues or loadable_count == 0:
        state = LongCandidateState.LOAD_HELD
    elif held_count:
        state = LongCandidateState.PARTIAL_HOLD
    else:
        state = LongCandidateState.LOAD_CANDIDATE_READY
    provenance = LongSourceProvenance(
        receipt=source.receipt,
        preview_source_name=preview.source_name,
        preview_source_size_bytes=preview.source_size_bytes,
        preview_sha256_before=preview.source_sha256_before,
        preview_sha256_after=preview.source_sha256_after,
        source_issues=preview.source_issues,
        is_golden_workbook_evidence=preview.is_golden_workbook_evidence,
        supplier_scope=preview.supplier_scope,
        template_id=preview.template_id,
        template_schema_version=preview.template_schema_version,
        template_revision=preview.template_revision,
        template_approved_by=preview.template_approved_by,
        template_approved_at=preview.template_approved_at,
        template_effective_from=preview.template_effective_from,
        template_effective_to=preview.template_effective_to,
        source_inspection_date=preview.source_inspection_date,
        binding_catalog_revision=catalog_revision,
        binding_selections=binding_selections,
    )
    return LongCandidateResult(
        state=state,
        provenance=provenance,
        source_identifiers=preview.identifiers,
        rows=rows,
        issues=issues,
    )


def _require_ready_preview(source: StoreScanMappingOutcome) -> MappingPreview:
    mapping_result = source.mapping_result
    if (
        source.status != StoreScanMappingStatus.PREVIEW_READY
        or mapping_result is None
        or mapping_result.state != MappingPreviewState.PREVIEW_READY
        or mapping_result.preview is None
    ):
        raise LongCandidateInputError("Long candidate conversion requires PREVIEW_READY")
    return mapping_result.preview


def _source_provenance_issues(
    source: StoreScanMappingOutcome,
    preview: MappingPreview,
) -> list[LongCandidateIssue]:
    receipt = source.receipt
    issues: list[LongCandidateIssue] = []
    if not (receipt.content_sha256 == preview.source_sha256_before == preview.source_sha256_after):
        issues.append(
            LongCandidateIssue(
                code=LongIssueCode.SOURCE_HASH_MISMATCH,
                scope=LongIssueScope.SOURCE,
                message="Receipt and Mapping Preview hashes do not identify one immutable source.",
                expected=receipt.content_sha256,
                observed=f"{preview.source_sha256_before}|{preview.source_sha256_after}",
            )
        )
    if receipt.original_filename != preview.source_name:
        issues.append(
            LongCandidateIssue(
                code=LongIssueCode.SOURCE_NAME_MISMATCH,
                scope=LongIssueScope.SOURCE,
                message="Receipt and Mapping Preview source names differ.",
                expected=receipt.original_filename,
                observed=preview.source_name,
            )
        )
    if receipt.size_bytes != preview.source_size_bytes:
        issues.append(
            LongCandidateIssue(
                code=LongIssueCode.SOURCE_SIZE_MISMATCH,
                scope=LongIssueScope.SOURCE,
                message="Receipt and Mapping Preview source sizes differ.",
                expected=str(receipt.size_bytes),
                observed=str(preview.source_size_bytes),
            )
        )
    if receipt.project_key != preview.project_key:
        issues.append(
            LongCandidateIssue(
                code=LongIssueCode.PROJECT_SCOPE_CONFLICT,
                scope=LongIssueScope.SOURCE,
                message="Receipt and Mapping Preview projects differ.",
                expected=receipt.project_key,
                observed=preview.project_key,
            )
        )
    if source.scope.supplier_scope != preview.supplier_scope:
        issues.append(
            LongCandidateIssue(
                code=LongIssueCode.SUPPLIER_SCOPE_CONFLICT,
                scope=LongIssueScope.SOURCE,
                message="Resolved and Mapping Preview supplier scopes differ.",
                expected=source.scope.supplier_scope,
                observed=preview.supplier_scope,
            )
        )
    return issues


def _identifier(preview: MappingPreview, kind: IdentifierKind) -> MappedCellEvidence | None:
    matches = tuple(item.evidence for item in preview.identifiers if item.kind == kind)
    return matches[0] if len(matches) == 1 else None


def _exact_text_value(evidence: MappedCellEvidence | None) -> str | None:
    if evidence is None or not isinstance(evidence.raw_value, str):
        return None
    value = evidence.raw_value
    return value if value.strip() and value == value.strip() else None


def _source_issue(
    code: LongIssueCode,
    message: str,
    evidence: MappedCellEvidence | None,
    *,
    expected: str | None = None,
    observed: str | None = None,
) -> LongCandidateIssue:
    return LongCandidateIssue(
        code=code,
        scope=LongIssueScope.SOURCE,
        message=message,
        sheet_name=evidence.source.sheet_name if evidence is not None else None,
        coordinate=evidence.source.coordinate if evidence is not None else None,
        expected=expected,
        observed=observed,
    )


def _build_row(
    row: InspectionRowPreview,
    preview: MappingPreview,
    *,
    model_value: str | None,
    catalog: CanonicalRowBindingCatalog,
) -> tuple[LongInspectionCandidate, CanonicalRowBindingSelectionSignature]:
    issues: list[LongCandidateIssue] = []
    binding: CanonicalRowBinding | None = None
    key = CanonicalRowBindingKey(
        project_key=preview.project_key,
        supplier_scope=preview.supplier_scope,
        template_id=preview.template_id,
        template_revision=preview.template_revision,
        row_key=row.row_key,
    )
    matches = tuple(
        sorted(
            catalog.find(key),
            key=lambda item: repr(item.signature.version_parts),
        )
    )
    selection = CanonicalRowBindingSelectionSignature(
        requested_key=key,
        matches=tuple(match.signature for match in matches),
    )
    if not matches:
        issues.append(
            _row_issue(
                LongIssueCode.CANONICAL_ROW_BINDING_MISSING,
                row,
                "No exact canonical row binding matched.",
                expected=repr(key),
                observed="MISSING",
            )
        )
    elif len(matches) > 1:
        issues.append(
            _row_issue(
                LongIssueCode.CANONICAL_ROW_BINDING_AMBIGUOUS,
                row,
                "More than one canonical row binding matched exactly.",
                expected="one binding",
                observed=str(len(matches)),
            )
        )
    else:
        candidate = matches[0]
        scope_conflict = False
        if candidate.key.project_key != key.project_key:
            scope_conflict = True
            issues.append(
                _row_issue(
                    LongIssueCode.PROJECT_SCOPE_CONFLICT,
                    row,
                    "The catalog returned a binding for a different project.",
                    expected=key.project_key,
                    observed=candidate.key.project_key,
                )
            )
        if candidate.key.supplier_scope != key.supplier_scope:
            scope_conflict = True
            issues.append(
                _row_issue(
                    LongIssueCode.SUPPLIER_SCOPE_CONFLICT,
                    row,
                    "The catalog returned a binding for a different supplier scope.",
                    expected=key.supplier_scope,
                    observed=candidate.key.supplier_scope,
                )
            )
        structural_key = (key.template_id, key.template_revision, key.row_key)
        candidate_structural_key = (
            candidate.key.template_id,
            candidate.key.template_revision,
            candidate.key.row_key,
        )
        if candidate_structural_key != structural_key:
            scope_conflict = True
            issues.append(
                _row_issue(
                    LongIssueCode.CANONICAL_ROW_BINDING_SCOPE_CONFLICT,
                    row,
                    "The catalog returned a binding for a different exact scope.",
                    expected=repr(key),
                    observed=repr(candidate.key),
                )
            )
        if not scope_conflict:
            binding = candidate
            if binding.status != CanonicalRowBindingStatus.APPROVED:
                issues.append(
                    _row_issue(
                        LongIssueCode.CANONICAL_ROW_BINDING_NOT_APPROVED,
                        row,
                        "Canonical row binding is not approved.",
                        expected=CanonicalRowBindingStatus.APPROVED.value,
                        observed=binding.status.value,
                    )
                )
            if not _binding_is_effective(binding, preview.source_inspection_date):
                issues.append(
                    _row_issue(
                        LongIssueCode.CANONICAL_ROW_BINDING_NOT_EFFECTIVE,
                        row,
                        "Canonical row binding is not effective on the source inspection date.",
                        expected=_binding_effective_period(binding),
                        observed=preview.source_inspection_date.isoformat(),
                    )
                )
            if model_value is not None and model_value not in binding.source_model_values:
                issues.append(
                    _row_issue(
                        LongIssueCode.SOURCE_MODEL_BINDING_CONFLICT,
                        row,
                        (
                            "Source model evidence is outside the exact aliases approved "
                            "by the binding."
                        ),
                        expected=repr(binding.source_model_values),
                        observed=model_value,
                    )
                )

    measurements: list[LongMeasurementCandidate] = []
    for ordinal, evidence in enumerate(row.samples, start=1):
        measurement, measurement_issues = _measurement_candidate(
            row.row_key,
            ordinal,
            evidence,
            preview,
            binding,
        )
        measurements.append(measurement)
        issues.extend(measurement_issues)

    if (
        not measurements
        and binding is not None
        and binding.sample_policy != SamplePolicy.ZERO_ALLOWED
    ):
        issues.append(
            _row_issue(
                LongIssueCode.ZERO_SAMPLE_POLICY_REQUIRED,
                row,
                "A zero-sample row requires an explicit ZERO_ALLOWED binding policy.",
                expected=SamplePolicy.ZERO_ALLOWED.value,
                observed=binding.sample_policy.value,
            )
        )

    state = LongRowState.ROW_HELD if issues else LongRowState.LOADABLE_PENDING
    return (
        LongInspectionCandidate(
            row_key=row.row_key,
            state=state,
            binding=binding,
            item=row.item,
            method=row.method,
            instrument=row.instrument,
            specification=row.specification,
            tolerance=row.tolerance,
            minimum=row.minimum,
            maximum=row.maximum,
            section=row.section,
            category=row.category,
            unit=row.unit,
            measurement_point=row.measurement_point,
            measurement_location=row.measurement_location,
            cavity=row.cavity,
            target=row.target,
            lsl=row.lsl,
            usl=row.usl,
            source_spec_revision=row.source_spec_revision,
            measurements=tuple(measurements),
            supplier_judgment=row.supplier_result,
            issues=tuple(issues),
        ),
        selection,
    )


def _measurement_candidate(
    row_key: str,
    ordinal: int,
    evidence: MappedCellEvidence,
    preview: MappingPreview,
    binding: CanonicalRowBinding | None,
) -> tuple[LongMeasurementCandidate, tuple[LongCandidateIssue, ...]]:
    issues: list[LongCandidateIssue] = []
    numeric: RawNumericValue | None = None
    qualitative: str | None = None

    if evidence.formula_text is not None or evidence.value_kind == PreviewValueKind.FORMULA:
        issues.append(
            _cell_issue(
                LongIssueCode.FORMULA_SAMPLE_NOT_RAW,
                row_key,
                evidence,
                "A formula or its cache cannot become an official raw measurement.",
            )
        )
    elif evidence.value_kind == PreviewValueKind.NUMERIC:
        numeric, numeric_issue = _direct_finite_numeric(row_key, evidence)
        if numeric_issue is not None:
            issues.append(numeric_issue)
    elif evidence.value_kind == PreviewValueKind.QUALITATIVE:
        if (
            isinstance(evidence.raw_value, str)
            and evidence.raw_value.strip()
            and evidence.raw_value == evidence.raw_value.strip()
        ):
            qualitative = evidence.raw_value
        else:
            issues.append(
                _cell_issue(
                    LongIssueCode.INVALID_QUALITATIVE_SAMPLE,
                    row_key,
                    evidence,
                    "Qualitative sample evidence must retain a direct text value.",
                )
            )
    else:
        issues.append(
            _cell_issue(
                LongIssueCode.UNSUPPORTED_SAMPLE_VALUE_KIND,
                row_key,
                evidence,
                "This sample value kind has no approved Long-format projection.",
                observed=evidence.value_kind.value,
            )
        )

    if binding is not None and not _measurement_mode_matches(binding.measurement_mode, evidence):
        issues.append(
            _cell_issue(
                LongIssueCode.MEASUREMENT_MODE_MISMATCH,
                row_key,
                evidence,
                "Mapped sample kind conflicts with the approved binding measurement mode.",
                observed=evidence.value_kind.value,
            )
        )
        numeric = None
        qualitative = None

    if _calculation_refresh_required(evidence, preview):
        issues.append(
            _cell_issue(
                LongIssueCode.CALCULATION_REFRESH_REQUIRED,
                row_key,
                evidence,
                "Scanner evidence requires calculation refresh at this mapped sample cell.",
            )
        )
        numeric = None
        qualitative = None

    return (
        LongMeasurementCandidate(
            sample_ordinal=ordinal,
            evidence=evidence,
            raw_numeric_value=numeric,
            raw_qualitative_value=qualitative,
        ),
        tuple(issues),
    )


def _direct_finite_numeric(
    row_key: str,
    evidence: MappedCellEvidence,
) -> tuple[RawNumericValue | None, LongCandidateIssue | None]:
    raw = evidence.raw_value
    if isinstance(raw, bool) or not isinstance(raw, (int, float, Decimal)):
        return None, _cell_issue(
            LongIssueCode.INVALID_NUMERIC_SAMPLE,
            row_key,
            evidence,
            "Numeric sample evidence must be a direct numeric stored value.",
        )
    if isinstance(raw, float) and not math.isfinite(raw):
        return None, _cell_issue(
            LongIssueCode.NONFINITE_NUMERIC_SAMPLE,
            row_key,
            evidence,
            "Non-finite numeric evidence cannot become a Long measurement.",
        )
    if isinstance(raw, Decimal) and not raw.is_finite():
        return None, _cell_issue(
            LongIssueCode.NONFINITE_NUMERIC_SAMPLE,
            row_key,
            evidence,
            "Non-finite numeric evidence cannot become a Long measurement.",
        )
    return raw, None


def _calculation_refresh_required(
    evidence: MappedCellEvidence,
    preview: MappingPreview,
) -> bool:
    return any(
        issue.code == "CALCULATION_REFRESH_REQUIRED"
        and issue.location.kind == SourceLocationKind.CELL
        and issue.location.sheet_name == evidence.source.sheet_name
        and issue.location.coordinate == evidence.source.coordinate
        for issue in preview.source_issues
    )


def _binding_is_effective(binding: CanonicalRowBinding, inspection_date: date) -> bool:
    if inspection_date < binding.effective_from:
        return False
    return binding.effective_to is None or inspection_date <= binding.effective_to


def _binding_effective_period(binding: CanonicalRowBinding) -> str:
    effective_to = binding.effective_to.isoformat() if binding.effective_to is not None else "OPEN"
    return f"{binding.effective_from.isoformat()}..{effective_to}"


def _measurement_mode_matches(
    mode: MeasurementMode,
    evidence: MappedCellEvidence,
) -> bool:
    if mode == MeasurementMode.NUMERIC:
        return evidence.value_kind == PreviewValueKind.NUMERIC
    if mode == MeasurementMode.QUALITATIVE:
        return evidence.value_kind == PreviewValueKind.QUALITATIVE
    return False


def _row_issue(
    code: LongIssueCode,
    row: InspectionRowPreview,
    message: str,
    *,
    expected: str | None = None,
    observed: str | None = None,
) -> LongCandidateIssue:
    return LongCandidateIssue(
        code=code,
        scope=LongIssueScope.ROW,
        message=message,
        row_key=row.row_key,
        sheet_name=row.item.source.sheet_name,
        coordinate=row.item.source.coordinate,
        expected=expected,
        observed=observed,
    )


def _cell_issue(
    code: LongIssueCode,
    row_key: str | None,
    evidence: MappedCellEvidence,
    message: str,
    *,
    observed: str | None = None,
) -> LongCandidateIssue:
    return LongCandidateIssue(
        code=code,
        scope=LongIssueScope.CELL,
        message=message,
        row_key=row_key,
        sheet_name=evidence.source.sheet_name,
        coordinate=evidence.source.coordinate,
        observed=observed,
    )


def _canonical_identity_issues(
    rows: tuple[LongInspectionCandidate, ...],
) -> tuple[LongCandidateIssue, ...]:
    bindings = tuple(row.binding for row in rows if row.binding is not None)
    model_keys = {binding.canonical_model_key for binding in bindings}
    supplier_keys = {binding.canonical_supplier_key for binding in bindings}
    issues: list[LongCandidateIssue] = []
    if len(model_keys) > 1:
        issues.append(
            LongCandidateIssue(
                code=LongIssueCode.CANONICAL_MODEL_CONFLICT,
                scope=LongIssueScope.SOURCE,
                message="Canonical row bindings disagree on the model.",
                expected="one canonical model",
                observed=repr(tuple(sorted(model_keys))),
            )
        )
    if len(supplier_keys) > 1:
        issues.append(
            LongCandidateIssue(
                code=LongIssueCode.CANONICAL_SUPPLIER_CONFLICT,
                scope=LongIssueScope.SOURCE,
                message="Canonical row bindings disagree on the supplier.",
                expected="one canonical supplier",
                observed=repr(tuple(sorted(supplier_keys))),
            )
        )
    return tuple(issues)
