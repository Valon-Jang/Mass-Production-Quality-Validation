"""Domain contract for deterministic, read-only workbook structure scans."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import BinaryIO, Protocol

PROVISIONAL_DEFAULT_MAX_CELLS = 1_000_000
PROVISIONAL_DEFAULT_MAX_PACKAGE_PARTS = 4_096
PROVISIONAL_DEFAULT_MAX_PART_UNCOMPRESSED_BYTES = 128 * 1024 * 1024
PROVISIONAL_DEFAULT_MAX_TOTAL_UNCOMPRESSED_BYTES = 1024 * 1024 * 1024
PROVISIONAL_DEFAULT_MAX_TOTAL_XML_UNCOMPRESSED_BYTES = 512 * 1024 * 1024


class WorkbookScanState(StrEnum):
    SCANNED = "SCANNED"
    SCANNED_WITH_WARNINGS = "SCANNED_WITH_WARNINGS"


class WorkbookScanFailureStatus(StrEnum):
    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"
    WORKBOOK_UNREADABLE = "WORKBOOK_UNREADABLE"
    ENCRYPTED_OR_BINARY_WORKBOOK = "ENCRYPTED_OR_BINARY_WORKBOOK"
    CORRUPT_OOXML = "CORRUPT_OOXML"
    PACKAGE_PART_COUNT_LIMIT_EXCEEDED = "PACKAGE_PART_COUNT_LIMIT_EXCEEDED"
    PACKAGE_PART_SIZE_LIMIT_EXCEEDED = "PACKAGE_PART_SIZE_LIMIT_EXCEEDED"
    PACKAGE_TOTAL_SIZE_LIMIT_EXCEEDED = "PACKAGE_TOTAL_SIZE_LIMIT_EXCEEDED"
    PACKAGE_XML_SIZE_LIMIT_EXCEEDED = "PACKAGE_XML_SIZE_LIMIT_EXCEEDED"
    SCAN_LIMIT_EXCEEDED = "SCAN_LIMIT_EXCEEDED"
    SOURCE_MUTATED_DURING_SCAN = "SOURCE_MUTATED_DURING_SCAN"


class IssueSeverity(StrEnum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class SourceLocationKind(StrEnum):
    WORKBOOK = "WORKBOOK"
    PACKAGE_PART = "PACKAGE_PART"
    SHEET = "SHEET"
    CELL = "CELL"
    RANGE = "RANGE"


class SheetKind(StrEnum):
    WORKSHEET = "WORKSHEET"
    CHARTSHEET = "CHARTSHEET"


class RowCandidateKind(StrEnum):
    BLANK = "BLANK"
    STRUCTURAL = "STRUCTURAL"
    REPEATED_HEADER = "REPEATED_HEADER"


class DisplayValueStatus(StrEnum):
    NOT_RENDERED = "NOT_RENDERED"


class MacroHandling(StrEnum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    NOT_LOADED_OR_EXECUTED = "NOT_LOADED_OR_EXECUTED"


@dataclass(frozen=True, slots=True)
class SourceLocation:
    kind: SourceLocationKind
    sheet_name: str | None = None
    coordinate: str | None = None
    package_part: str | None = None

    @classmethod
    def workbook(cls) -> SourceLocation:
        return cls(kind=SourceLocationKind.WORKBOOK)

    @classmethod
    def part(cls, package_part: str) -> SourceLocation:
        return cls(kind=SourceLocationKind.PACKAGE_PART, package_part=package_part)

    @classmethod
    def sheet(cls, sheet_name: str) -> SourceLocation:
        return cls(kind=SourceLocationKind.SHEET, sheet_name=sheet_name)

    @classmethod
    def cell(cls, sheet_name: str, coordinate: str) -> SourceLocation:
        return cls(
            kind=SourceLocationKind.CELL,
            sheet_name=sheet_name,
            coordinate=coordinate,
        )

    @classmethod
    def range(cls, sheet_name: str, coordinate: str) -> SourceLocation:
        return cls(
            kind=SourceLocationKind.RANGE,
            sheet_name=sheet_name,
            coordinate=coordinate,
        )


@dataclass(frozen=True, slots=True)
class ScanIssue:
    code: str
    severity: IssueSeverity
    message: str
    location: SourceLocation


class WorkbookScanFailure(RuntimeError):
    """Expected scan rejection with a machine-readable status and exact location."""

    def __init__(
        self,
        status: WorkbookScanFailureStatus,
        issue: ScanIssue,
        *,
        source_sha256_before: str | None = None,
        source_sha256_after: str | None = None,
    ) -> None:
        self.status = status
        self.issue = issue
        self.source_sha256_before = source_sha256_before
        self.source_sha256_after = source_sha256_after
        super().__init__(f"{status.value}: {issue.message}")


@dataclass(frozen=True, slots=True)
class ScanPolicy:
    """Bounded scan policy with provisional defaults pending real-volume evidence."""

    max_cells: int = PROVISIONAL_DEFAULT_MAX_CELLS
    max_package_parts: int = PROVISIONAL_DEFAULT_MAX_PACKAGE_PARTS
    max_part_uncompressed_bytes: int = PROVISIONAL_DEFAULT_MAX_PART_UNCOMPRESSED_BYTES
    max_total_uncompressed_bytes: int = PROVISIONAL_DEFAULT_MAX_TOTAL_UNCOMPRESSED_BYTES
    max_total_xml_uncompressed_bytes: int = PROVISIONAL_DEFAULT_MAX_TOTAL_XML_UNCOMPRESSED_BYTES

    def __post_init__(self) -> None:
        for field_name in (
            "max_cells",
            "max_package_parts",
            "max_part_uncompressed_bytes",
            "max_total_uncompressed_bytes",
            "max_total_xml_uncompressed_bytes",
        ):
            if getattr(self, field_name) < 1:
                raise ValueError(f"{field_name} must be positive")


@dataclass(frozen=True, slots=True)
class IndexRange:
    start: int
    end: int

    def __post_init__(self) -> None:
        if self.start < 1 or self.end < self.start:
            raise ValueError("index range must be one-based and ordered")


@dataclass(frozen=True, slots=True)
class CellEvidence:
    """Stored OOXML evidence; display_value is deliberately not fabricated."""

    coordinate: str
    stored_value: object
    cached_value: object | None
    formula_text: str | None
    number_format: str
    data_type: str
    display_value: str | None = None
    display_value_status: DisplayValueStatus = DisplayValueStatus.NOT_RENDERED


@dataclass(frozen=True, slots=True)
class RowCandidate:
    row_index: int
    kind: RowCandidateKind
    reason: str
    signature: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SheetProtectionMetadata:
    enabled: bool
    protected_actions: tuple[str, ...]
    password_material_collected: bool = False
    bypass_attempted: bool = False


@dataclass(frozen=True, slots=True)
class ImageMetadata:
    anchor_from: str | None
    anchor_to: str | None
    width_px: float | None
    height_px: float | None
    image_format: str | None
    content_collected: bool = False
    analysis_performed: bool = False


@dataclass(frozen=True, slots=True)
class SheetScan:
    name: str
    kind: SheetKind
    position: int
    visibility: str
    used_range: str | None
    estimated_cells: int
    merged_ranges: tuple[str, ...]
    hidden_row_ranges: tuple[IndexRange, ...]
    hidden_column_ranges: tuple[IndexRange, ...]
    cells: tuple[CellEvidence, ...]
    row_candidates: tuple[RowCandidate, ...]
    protection: SheetProtectionMetadata
    images: tuple[ImageMetadata, ...]
    issues: tuple[ScanIssue, ...]

    @property
    def formula_cells(self) -> tuple[CellEvidence, ...]:
        return tuple(cell for cell in self.cells if cell.formula_text is not None)


@dataclass(frozen=True, slots=True)
class WorkbookScan:
    state: WorkbookScanState
    source_name: str
    source_size_bytes: int
    source_sha256_before: str
    source_sha256_after: str
    sheets: tuple[SheetScan, ...]
    issues: tuple[ScanIssue, ...]
    estimated_cells: int
    external_link_count: int
    macro_handling: MacroHandling
    display_value_contract: DisplayValueStatus = DisplayValueStatus.NOT_RENDERED
    is_golden_workbook_evidence: bool = False


class WorkbookScannerPort(Protocol):
    """Provider-neutral scanner boundary for a Path or File Store stream."""

    def scan(self, source: Path, policy: ScanPolicy | None = None) -> WorkbookScan: ...

    def scan_stream(
        self,
        source: BinaryIO,
        *,
        source_name: str,
        policy: ScanPolicy | None = None,
    ) -> WorkbookScan: ...
