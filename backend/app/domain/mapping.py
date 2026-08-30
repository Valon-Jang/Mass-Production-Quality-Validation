"""Fail-closed mapping template and preview domain contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum

from app.domain.workbook_scan import DisplayValueStatus, ScanIssue, SheetKind

_CELL_COORDINATE_PATTERN = re.compile(r"^([A-Z]{1,3})([1-9][0-9]*)$")
_MAX_EXCEL_COLUMN = 16_384
_MAX_EXCEL_ROW = 1_048_576
SUPPORTED_MAPPING_TEMPLATE_SCHEMA_VERSIONS = frozenset({"1", "2"})


def _coordinate_is_valid(coordinate: str) -> bool:
    match = _CELL_COORDINATE_PATTERN.fullmatch(coordinate)
    if match is None:
        return False
    column_number = 0
    for character in match.group(1):
        column_number = column_number * 26 + (ord(character) - ord("A") + 1)
    return column_number <= _MAX_EXCEL_COLUMN and int(match.group(2)) <= _MAX_EXCEL_ROW


def _reference_is_valid(reference: str, *, require_range: bool = False) -> bool:
    coordinates = reference.split(":")
    if require_range and len(coordinates) != 2:
        return False
    if len(coordinates) not in {1, 2}:
        return False
    return all(_coordinate_is_valid(coordinate) for coordinate in coordinates)


class MappingTemplateStatus(StrEnum):
    DRAFT = "DRAFT"
    REVIEWED = "REVIEWED"
    APPROVED = "APPROVED"
    RETIRED = "RETIRED"


class MappingPreviewState(StrEnum):
    PREVIEW_READY = "PREVIEW_READY"
    MAPPING_REQUIRED = "MAPPING_REQUIRED"


class MappingIssueCode(StrEnum):
    TEMPLATE_MISSING = "TEMPLATE_MISSING"
    TEMPLATE_NOT_APPROVED = "TEMPLATE_NOT_APPROVED"
    TEMPLATE_NOT_EFFECTIVE = "TEMPLATE_NOT_EFFECTIVE"
    PROJECT_SCOPE_MISMATCH = "PROJECT_SCOPE_MISMATCH"
    SUPPLIER_SCOPE_MISMATCH = "SUPPLIER_SCOPE_MISMATCH"
    FINGERPRINT_HEADER_MISMATCH = "FINGERPRINT_HEADER_MISMATCH"
    FINGERPRINT_SHEET_MISMATCH = "FINGERPRINT_SHEET_MISMATCH"
    FINGERPRINT_MERGE_MISMATCH = "FINGERPRINT_MERGE_MISMATCH"
    FINGERPRINT_ROW_STRUCTURE_MISMATCH = "FINGERPRINT_ROW_STRUCTURE_MISMATCH"
    AMBIGUOUS_TEMPLATE_MATCH = "AMBIGUOUS_TEMPLATE_MATCH"
    MAPPED_CELL_MISSING = "MAPPED_CELL_MISSING"
    MAPPED_CELL_AMBIGUOUS = "MAPPED_CELL_AMBIGUOUS"
    SOURCE_HASH_MISMATCH = "SOURCE_HASH_MISMATCH"
    SUPPLIER_EVIDENCE_MISMATCH = "SUPPLIER_EVIDENCE_MISMATCH"
    INSPECTION_DATE_INVALID = "INSPECTION_DATE_INVALID"


class TemplateHistoryErrorCode(StrEnum):
    REVISION_OVERWRITE = "REVISION_OVERWRITE"
    REVISION_DOWNGRADE = "REVISION_DOWNGRADE"
    EFFECTIVE_PERIOD_OVERLAP = "EFFECTIVE_PERIOD_OVERLAP"
    SUPERSESSION_PREDECESSOR_MISSING = "SUPERSESSION_PREDECESSOR_MISSING"
    SUPERSESSION_REQUIRES_APPROVED = "SUPERSESSION_REQUIRES_APPROVED"
    SUPERSESSION_DUPLICATE = "SUPERSESSION_DUPLICATE"
    INVALID_STATUS_TRANSITION = "INVALID_STATUS_TRANSITION"


class TemplateHistoryError(ValueError):
    def __init__(self, code: TemplateHistoryErrorCode, message: str) -> None:
        self.code = code
        super().__init__(f"{code.value}: {message}")


@dataclass(frozen=True, slots=True)
class TemplateSupersessionDecision:
    project_key: str
    supplier_scope: str
    template_id: str
    predecessor_revision: int
    successor_revision: int
    predecessor_effective_to: date
    decided_by: str
    decided_at: datetime
    reason: str

    def __post_init__(self) -> None:
        for field_name in (
            "project_key",
            "supplier_scope",
            "template_id",
            "decided_by",
            "reason",
        ):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} must not be blank")
        if self.predecessor_revision < 1 or self.successor_revision <= self.predecessor_revision:
            raise ValueError("supersession revisions must be positive and increasing")
        if self.decided_at.utcoffset() is None:
            raise ValueError("decided_at must be timezone-aware")


class IdentifierKind(StrEnum):
    MODEL = "MODEL"
    PART_NUMBER = "PART_NUMBER"
    LOT_NUMBER = "LOT_NUMBER"
    SUPPLIER = "SUPPLIER"
    INSPECTION_DATE = "INSPECTION_DATE"
    REPORT_NUMBER = "REPORT_NUMBER"
    REVISION = "REVISION"
    PART_NAME = "PART_NAME"
    PRODUCTION_DATE = "PRODUCTION_DATE"
    CURRENT_SHIPMENT_QUANTITY = "CURRENT_SHIPMENT_QUANTITY"
    SUPPLIER_CUMULATIVE_SHIPMENT_QUANTITY = "SUPPLIER_CUMULATIVE_SHIPMENT_QUANTITY"


MAPPING_V2_IDENTIFIER_KINDS = frozenset(
    {
        IdentifierKind.PART_NAME,
        IdentifierKind.PRODUCTION_DATE,
        IdentifierKind.CURRENT_SHIPMENT_QUANTITY,
        IdentifierKind.SUPPLIER_CUMULATIVE_SHIPMENT_QUANTITY,
    }
)


class PreviewValueKind(StrEnum):
    NUMERIC = "NUMERIC"
    QUALITATIVE = "QUALITATIVE"
    FORMULA = "FORMULA"
    BOOLEAN = "BOOLEAN"
    TEMPORAL = "TEMPORAL"
    OTHER = "OTHER"


class SystemJudgmentStatus(StrEnum):
    NOT_EVALUATED = "NOT_EVALUATED"
    EVALUATED = "EVALUATED"


@dataclass(frozen=True, slots=True, order=True)
class CellAddress:
    sheet_name: str
    coordinate: str

    def __post_init__(self) -> None:
        if not self.sheet_name.strip():
            raise ValueError("sheet_name must not be blank")
        if not _coordinate_is_valid(self.coordinate):
            raise ValueError("coordinate must be a valid canonical uppercase Excel A1 address")

    @property
    def row_index(self) -> int:
        match = _CELL_COORDINATE_PATTERN.fullmatch(self.coordinate)
        if match is None:  # pragma: no cover - guarded by construction
            raise AssertionError("validated coordinate no longer matches")
        return int(match.group(2))


@dataclass(frozen=True, slots=True)
class HeaderTokenAssertion:
    source: CellAddress
    expected_token: str

    def __post_init__(self) -> None:
        if not self.expected_token.strip():
            raise ValueError("expected_token must not be blank")


@dataclass(frozen=True, slots=True)
class SheetStructureAssertion:
    sheet_name: str
    expected_position: int
    expected_kind: SheetKind
    expected_visibility: str
    expected_used_range: str | None

    def __post_init__(self) -> None:
        for field_name in ("sheet_name", "expected_visibility"):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} must not be blank")
        if self.expected_position < 0:
            raise ValueError("expected_position must not be negative")
        if self.expected_visibility not in {"visible", "hidden", "veryHidden"}:
            raise ValueError("expected_visibility is not a supported worksheet state")
        if self.expected_kind == SheetKind.WORKSHEET:
            if self.expected_used_range is None or not _reference_is_valid(
                self.expected_used_range
            ):
                raise ValueError("worksheet expected_used_range must be a canonical A1 range")
        elif self.expected_used_range is not None:
            raise ValueError("chartsheet expected_used_range must be None")


@dataclass(frozen=True, slots=True)
class MergeSignatureAssertion:
    sheet_name: str
    expected_merged_ranges: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.sheet_name.strip():
            raise ValueError("sheet_name must not be blank")
        if len(set(self.expected_merged_ranges)) != len(self.expected_merged_ranges):
            raise ValueError("expected_merged_ranges must not contain duplicates")
        if any(not cell_range.strip() for cell_range in self.expected_merged_ranges):
            raise ValueError("expected_merged_ranges must not contain blank ranges")
        if any(
            not _reference_is_valid(cell_range, require_range=True)
            for cell_range in self.expected_merged_ranges
        ):
            raise ValueError("merge signatures must use canonical A1 ranges")


@dataclass(frozen=True, slots=True)
class RowStructureAssertion:
    row_key: str
    sheet_name: str
    row_index: int
    expected_non_empty_cells: tuple[CellAddress, ...]

    def __post_init__(self) -> None:
        if not self.row_key.strip() or not self.sheet_name.strip():
            raise ValueError("row_key and sheet_name must not be blank")
        if self.row_index < 1 or not self.expected_non_empty_cells:
            raise ValueError("row structure requires a positive row and expected cells")
        if len(set(self.expected_non_empty_cells)) != len(self.expected_non_empty_cells):
            raise ValueError("row structure cells must not contain duplicates")
        if any(
            source.sheet_name != self.sheet_name or source.row_index != self.row_index
            for source in self.expected_non_empty_cells
        ):
            raise ValueError("row structure cells must belong to the asserted sheet and row")


@dataclass(frozen=True, slots=True)
class WorkbookFingerprint:
    header_tokens: tuple[HeaderTokenAssertion, ...]
    sheet_structures: tuple[SheetStructureAssertion, ...]
    merge_signatures: tuple[MergeSignatureAssertion, ...]
    row_structures: tuple[RowStructureAssertion, ...]

    def __post_init__(self) -> None:
        if not self.header_tokens:
            raise ValueError("fingerprint requires header token assertions")
        if not self.sheet_structures:
            raise ValueError("fingerprint requires sheet structure assertions")
        if not self.merge_signatures:
            raise ValueError("fingerprint requires merge signature assertions")
        if not self.row_structures:
            raise ValueError("fingerprint requires row/sample structure assertions")

        header_sources = [assertion.source for assertion in self.header_tokens]
        if len(set(header_sources)) != len(header_sources):
            raise ValueError("fingerprint header token cells must be unique")
        sheet_names = [assertion.sheet_name for assertion in self.sheet_structures]
        sheet_positions = [assertion.expected_position for assertion in self.sheet_structures]
        merge_sheet_names = [assertion.sheet_name for assertion in self.merge_signatures]
        row_keys = [assertion.row_key for assertion in self.row_structures]
        if len(set(sheet_names)) != len(sheet_names):
            raise ValueError("fingerprint sheet structure assertions must be unique")
        if sorted(sheet_positions) != list(range(len(sheet_positions))):
            raise ValueError("fingerprint sheet positions must be unique and contiguous")
        if len(set(merge_sheet_names)) != len(merge_sheet_names):
            raise ValueError("fingerprint merge assertions must be unique by sheet")
        if len(set(row_keys)) != len(row_keys):
            raise ValueError("fingerprint row structure assertions must have unique keys")

        declared_sheets = set(sheet_names)
        if set(merge_sheet_names) != declared_sheets:
            raise ValueError("each declared sheet requires one merge signature assertion")
        referenced_sheets = (
            {assertion.source.sheet_name for assertion in self.header_tokens}
            | set(merge_sheet_names)
            | {assertion.sheet_name for assertion in self.row_structures}
        )
        if not referenced_sheets.issubset(declared_sheets):
            raise ValueError("all fingerprint assertions must reference declared sheets")


@dataclass(frozen=True, slots=True)
class IdentifierMapping:
    kind: IdentifierKind
    source: CellAddress


@dataclass(frozen=True, slots=True)
class InspectionRowMapping:
    row_key: str
    item: CellAddress
    method: CellAddress | None = None
    instrument: CellAddress | None = None
    specification: CellAddress | None = None
    tolerance: CellAddress | None = None
    minimum: CellAddress | None = None
    maximum: CellAddress | None = None
    sample_cells: tuple[CellAddress, ...] = ()
    supplier_result: CellAddress | None = None
    section: CellAddress | None = None
    category: CellAddress | None = None
    unit: CellAddress | None = None
    measurement_point: CellAddress | None = None
    measurement_location: CellAddress | None = None
    cavity: CellAddress | None = None
    target: CellAddress | None = None
    lsl: CellAddress | None = None
    usl: CellAddress | None = None
    source_spec_revision: CellAddress | None = None

    def __post_init__(self) -> None:
        if not self.row_key.strip():
            raise ValueError("row_key must not be blank")
        addresses = self.all_addresses
        if len(set(addresses)) != len(addresses):
            raise ValueError("inspection row maps one source cell more than once")
        if len({address.sheet_name for address in addresses}) != 1:
            raise ValueError("one inspection row mapping must use one source sheet")

    @property
    def all_addresses(self) -> tuple[CellAddress, ...]:
        optional_addresses = (
            self.method,
            self.instrument,
            self.specification,
            self.tolerance,
            self.minimum,
            self.maximum,
            *self.sample_cells,
            self.supplier_result,
            self.section,
            self.category,
            self.unit,
            self.measurement_point,
            self.measurement_location,
            self.cavity,
            self.target,
            self.lsl,
            self.usl,
            self.source_spec_revision,
        )
        return (self.item, *(address for address in optional_addresses if address is not None))

    @property
    def has_v2_roles(self) -> bool:
        return any(
            address is not None
            for address in (
                self.section,
                self.category,
                self.unit,
                self.measurement_point,
                self.measurement_location,
                self.cavity,
                self.target,
                self.lsl,
                self.usl,
                self.source_spec_revision,
            )
        )


@dataclass(frozen=True, slots=True)
class MappingTemplate:
    template_id: str
    schema_version: str
    revision: int
    status: MappingTemplateStatus
    project_key: str
    supplier_scope: str
    supplier_source_aliases: tuple[str, ...]
    approved_by: str | None
    approved_at: datetime | None
    effective_from: date
    effective_to: date | None
    fingerprint: WorkbookFingerprint
    identifiers: tuple[IdentifierMapping, ...]
    inspection_rows: tuple[InspectionRowMapping, ...]
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None

    def __post_init__(self) -> None:
        for field_name in ("template_id", "schema_version", "project_key", "supplier_scope"):
            if not getattr(self, field_name).strip():
                raise ValueError(f"{field_name} must not be blank")
        if self.schema_version not in SUPPORTED_MAPPING_TEMPLATE_SCHEMA_VERSIONS:
            raise ValueError("unsupported Mapping Template schema_version")
        if self.revision < 1:
            raise ValueError("revision must be positive")
        normalized_aliases = [
            " ".join(alias.strip().casefold().split()) for alias in self.supplier_source_aliases
        ]
        if not normalized_aliases or any(not alias for alias in normalized_aliases):
            raise ValueError("supplier_source_aliases must contain non-blank source values")
        if len(set(normalized_aliases)) != len(normalized_aliases):
            raise ValueError("supplier_source_aliases must be unique after normalization")
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to must not precede effective_from")
        if self.status == MappingTemplateStatus.APPROVED:
            if self.approved_by is None or not self.approved_by.strip():
                raise ValueError("approved template requires an approver")
            if self.approved_at is None or self.approved_at.utcoffset() is None:
                raise ValueError("approved template requires a timezone-aware approved_at")
        if self.status == MappingTemplateStatus.REVIEWED:
            if self.reviewed_by is None or not self.reviewed_by.strip():
                raise ValueError("reviewed template requires a reviewer")
            if self.reviewed_at is None or self.reviewed_at.utcoffset() is None:
                raise ValueError("reviewed template requires a timezone-aware reviewed_at")
            if self.approved_by is not None or self.approved_at is not None:
                raise ValueError("reviewed template must not contain approval metadata")
        if self.status == MappingTemplateStatus.DRAFT:
            if self.approved_by is not None or self.approved_at is not None:
                raise ValueError("draft template must not contain approval metadata")
            if self.reviewed_by is not None or self.reviewed_at is not None:
                raise ValueError("draft template must not contain review metadata")
        for actor_field, time_field in (
            (self.reviewed_by, self.reviewed_at),
            (self.approved_by, self.approved_at),
        ):
            if (actor_field is None) != (time_field is None):
                raise ValueError("workflow actor and time must be provided together")
            if time_field is not None and time_field.utcoffset() is None:
                raise ValueError("workflow timestamps must be timezone-aware")
        if not self.identifiers or not self.inspection_rows:
            raise ValueError("template requires identifier and inspection mappings")

        identifier_kinds = [mapping.kind for mapping in self.identifiers]
        identifier_sources = [mapping.source for mapping in self.identifiers]
        if len(set(identifier_kinds)) != len(identifier_kinds):
            raise ValueError("identifier kinds must not be mapped more than once")
        if len(set(identifier_sources)) != len(identifier_sources):
            raise ValueError("identifier source cells must not be mapped more than once")
        if IdentifierKind.INSPECTION_DATE not in identifier_kinds:
            raise ValueError("template requires an inspection date mapping")
        if IdentifierKind.SUPPLIER not in identifier_kinds:
            raise ValueError("template requires a supplier evidence mapping")
        if self.schema_version == "1":
            if any(kind in MAPPING_V2_IDENTIFIER_KINDS for kind in identifier_kinds):
                raise ValueError("Mapping Template schema v1 cannot carry v2 identifier roles")
            if any(mapping.has_v2_roles for mapping in self.inspection_rows):
                raise ValueError("Mapping Template schema v1 cannot carry v2 inspection roles")

        row_keys = [mapping.row_key for mapping in self.inspection_rows]
        if len(set(row_keys)) != len(row_keys):
            raise ValueError("inspection row keys must be unique")
        fingerprint_rows = {
            assertion.row_key: assertion for assertion in self.fingerprint.row_structures
        }
        if set(row_keys) != set(fingerprint_rows):
            raise ValueError("each inspection row requires one fingerprint row assertion")
        for mapping in self.inspection_rows:
            asserted_cells = set(fingerprint_rows[mapping.row_key].expected_non_empty_cells)
            if not set(mapping.all_addresses).issubset(asserted_cells):
                raise ValueError("row fingerprint must include every mapped inspection cell")

        declared_sheets = {assertion.sheet_name for assertion in self.fingerprint.sheet_structures}
        mapped_sheets = {mapping.source.sheet_name for mapping in self.identifiers} | {
            source.sheet_name for row in self.inspection_rows for source in row.all_addresses
        }
        if not mapped_sheets.issubset(declared_sheets):
            raise ValueError("all mapped cells must belong to fingerprinted sheets")
        all_mapped_sources = identifier_sources + [
            source for row in self.inspection_rows for source in row.all_addresses
        ]
        if len(set(all_mapped_sources)) != len(all_mapped_sources):
            raise ValueError("template source cells must not map to multiple semantic targets")


@dataclass(frozen=True, slots=True)
class MappingPreviewRequest:
    project_key: str
    supplier_scope: str

    def __post_init__(self) -> None:
        if not self.project_key.strip() or not self.supplier_scope.strip():
            raise ValueError("project_key and supplier_scope must not be blank")


@dataclass(frozen=True, slots=True)
class MappedCellEvidence:
    source: CellAddress
    raw_value: object
    cached_value: object | None
    formula_text: str | None
    number_format: str
    data_type: str
    display_value: str | None
    display_value_status: DisplayValueStatus
    value_kind: PreviewValueKind


@dataclass(frozen=True, slots=True)
class IdentifierPreview:
    kind: IdentifierKind
    evidence: MappedCellEvidence


@dataclass(frozen=True, slots=True)
class InspectionRowPreview:
    row_key: str
    item: MappedCellEvidence
    method: MappedCellEvidence | None
    instrument: MappedCellEvidence | None
    specification: MappedCellEvidence | None
    tolerance: MappedCellEvidence | None
    minimum: MappedCellEvidence | None
    maximum: MappedCellEvidence | None
    section: MappedCellEvidence | None
    category: MappedCellEvidence | None
    unit: MappedCellEvidence | None
    measurement_point: MappedCellEvidence | None
    measurement_location: MappedCellEvidence | None
    cavity: MappedCellEvidence | None
    target: MappedCellEvidence | None
    lsl: MappedCellEvidence | None
    usl: MappedCellEvidence | None
    source_spec_revision: MappedCellEvidence | None
    samples: tuple[MappedCellEvidence, ...]
    supplier_result: MappedCellEvidence | None
    system_judgment_status: SystemJudgmentStatus = field(
        default=SystemJudgmentStatus.NOT_EVALUATED,
        init=False,
    )
    system_judgment: None = field(default=None, init=False)

    @property
    def has_v2_evidence(self) -> bool:
        return any(
            evidence is not None
            for evidence in (
                self.section,
                self.category,
                self.unit,
                self.measurement_point,
                self.measurement_location,
                self.cavity,
                self.target,
                self.lsl,
                self.usl,
                self.source_spec_revision,
            )
        )


@dataclass(frozen=True, slots=True)
class MappingPreview:
    source_name: str
    source_size_bytes: int
    source_sha256_before: str
    source_sha256_after: str
    source_issues: tuple[ScanIssue, ...]
    is_golden_workbook_evidence: bool
    template_id: str
    template_schema_version: str
    template_revision: int
    template_approved_by: str
    template_approved_at: datetime
    template_effective_from: date
    template_effective_to: date | None
    source_inspection_date: date
    project_key: str
    supplier_scope: str
    identifiers: tuple[IdentifierPreview, ...]
    inspection_rows: tuple[InspectionRowPreview, ...]

    def __post_init__(self) -> None:
        if self.template_schema_version not in SUPPORTED_MAPPING_TEMPLATE_SCHEMA_VERSIONS:
            raise ValueError("unsupported Mapping Preview template_schema_version")
        if self.template_schema_version == "1":
            if any(item.kind in MAPPING_V2_IDENTIFIER_KINDS for item in self.identifiers):
                raise ValueError("schema-v1 Mapping Preview cannot carry v2 identifiers")
            if any(row.has_v2_evidence for row in self.inspection_rows):
                raise ValueError("schema-v1 Mapping Preview cannot carry v2 row evidence")


@dataclass(frozen=True, slots=True)
class MappingIssue:
    code: MappingIssueCode
    message: str
    template_id: str | None = None
    template_revision: int | None = None
    sheet_name: str | None = None
    coordinate: str | None = None
    expected: str | None = None
    observed: str | None = None


@dataclass(frozen=True, slots=True)
class MappingPreviewResult:
    state: MappingPreviewState
    preview: MappingPreview | None
    issues: tuple[MappingIssue, ...]
    official_values_created: bool = field(default=False, init=False)
    calculations_performed: bool = field(default=False, init=False)
