"""Receipt-replay Mapping Preview workspace for the local manual UI.

This slice deliberately stops before Mapping Template commands and Long-format
persistence.  It rehydrates one immutable receipt, rescans it, reloads a fresh
persistent Mapping catalog, and returns either an approved Preview or bounded
exact source-cell evidence for human review.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from itertools import islice
from typing import BinaryIO, Protocol

from sqlalchemy import select

from app.application.mapping_preview import MappingTemplateCatalog, build_mapping_preview
from app.domain.mapping import (
    MappingIssue,
    MappingPreview,
    MappingPreviewRequest,
    MappingPreviewResult,
)
from app.domain.source_file import SourceFileReceipt
from app.domain.workbook_scan import (
    CellEvidence,
    ScanPolicy,
    WorkbookScan,
    WorkbookScanFailure,
)
from app.infrastructure.database import Database
from app.infrastructure.file_store import (
    OriginalFileStoreError,
    StoredSourceNotFoundError,
)
from app.infrastructure.mapping_templates import (
    MappingTemplatePersistenceError,
    MappingTemplateRepository,
    MappingTemplateRevisionRow,
)


class MappingWorkspaceState(StrEnum):
    PREVIEW_READY = "PREVIEW_READY"
    MAPPING_REQUIRED = "MAPPING_REQUIRED"


class MappingWorkspaceMode(StrEnum):
    APPROVED_TEMPLATE = "APPROVED_TEMPLATE"
    MANUAL_SOURCE_REVIEW = "MANUAL_SOURCE_REVIEW"


class MappingWorkspaceAIState(StrEnum):
    NOT_CALLED = "NOT_CALLED"


class SourceValueKind(StrEnum):
    NULL = "NULL"
    TEXT = "TEXT"
    INTEGER = "INTEGER"
    NUMBER = "NUMBER"
    DECIMAL = "DECIMAL"
    BOOLEAN = "BOOLEAN"
    DATE = "DATE"
    DATETIME = "DATETIME"
    UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True, slots=True)
class TaggedSourceValue:
    kind: SourceValueKind
    value: str | int | float | bool | None
    python_type: str


@dataclass(frozen=True, slots=True)
class SourceCellReviewEvidence:
    sheet_name: str
    sheet_position: int
    coordinate: str
    raw_value: TaggedSourceValue
    cached_value: TaggedSourceValue
    formula_text: str | None
    number_format: str
    data_type: str
    display_value: str | None
    display_value_status: str


@dataclass(frozen=True, slots=True)
class SourceCellPage:
    offset: int
    limit: int
    total: int
    truncated: bool
    cells: tuple[SourceCellReviewEvidence, ...]


@dataclass(frozen=True, slots=True)
class ApprovedTemplateProof:
    history_id: str
    revision_id: str
    template_id: str
    schema_version: str
    revision: int
    status: str
    payload_sha256: str
    effective_from: date
    effective_to: date | None
    approved_by: str
    approved_at: datetime
    history_row_version: int
    revision_row_version: int


@dataclass(frozen=True, slots=True)
class MappingWorkspaceSnapshot:
    state: MappingWorkspaceState
    mode: MappingWorkspaceMode
    status_label: str
    message: str
    receipt: SourceFileReceipt
    supplier_scope: str
    scan: WorkbookScan
    source_cells: SourceCellPage
    issues: tuple[MappingIssue, ...]
    preview: MappingPreview | None
    template: ApprovedTemplateProof | None
    ai_state: MappingWorkspaceAIState = MappingWorkspaceAIState.NOT_CALLED
    draft_command_available: bool = False
    long_confirmation_available: bool = False
    official_values_created: bool = False
    calculations_performed: bool = False

    def __post_init__(self) -> None:
        if self.state == MappingWorkspaceState.PREVIEW_READY:
            if self.preview is None or self.template is None or self.issues:
                raise ValueError("ready Mapping workspace requires exact approved proof")
        elif self.preview is not None or self.template is not None:
            raise ValueError("manual Mapping review cannot expose approved preview proof")
        if any(
            (
                self.draft_command_available,
                self.long_confirmation_available,
                self.official_values_created,
                self.calculations_performed,
            )
        ):
            raise ValueError("this review slice cannot approve, persist, or calculate")


@dataclass(frozen=True, slots=True)
class MappingWorkspaceRequest:
    project_key: str
    receipt_id: str
    content_sha256: str
    supplier_scope: str
    cell_offset: int = 0
    cell_limit: int = 120


class MappingWorkspaceError(RuntimeError):
    def __init__(self, code: str, message: str, status_label: str) -> None:
        self.code = code
        self.safe_message = message
        self.status_label = status_label
        super().__init__(code)


class MappingWorkspaceValidationError(MappingWorkspaceError):
    pass


class MappingWorkspaceNotFoundError(MappingWorkspaceError):
    def __init__(self) -> None:
        super().__init__(
            "MAPPING_RECEIPT_NOT_FOUND",
            "해당 프로젝트에서 보존된 원본을 찾을 수 없습니다.",
            "원본 없음",
        )


class MappingWorkspaceSourceError(MappingWorkspaceError):
    def __init__(self, code: str = "MAPPING_SOURCE_UNAVAILABLE") -> None:
        super().__init__(
            code,
            "보존된 원본을 다시 확인할 수 없습니다.",
            "원본 확인 실패",
        )


class ReceiptReplayStore(Protocol):
    def resolve_receipt(
        self,
        *,
        project_key: str,
        receipt_id: str,
        content_sha256: str,
    ) -> SourceFileReceipt: ...

    def open_source(self, receipt: SourceFileReceipt) -> AbstractContextManager[BinaryIO]: ...


class StreamWorkbookScanner(Protocol):
    def scan_stream(
        self,
        source: BinaryIO,
        *,
        source_name: str,
        policy: ScanPolicy | None = None,
    ) -> WorkbookScan: ...


MappingPreviewBuilder = Callable[
    [WorkbookScan, MappingPreviewRequest, MappingTemplateCatalog],
    MappingPreviewResult,
]


class MappingWorkspaceService:
    """Recompute a Mapping workspace from durable receipt evidence only."""

    def __init__(
        self,
        *,
        database: Database,
        file_store: ReceiptReplayStore,
        scanner: StreamWorkbookScanner,
        scan_policy: ScanPolicy,
        mapping_repository: MappingTemplateRepository | None = None,
        preview_builder: MappingPreviewBuilder = build_mapping_preview,
    ) -> None:
        self._database = database
        self._file_store = file_store
        self._scanner = scanner
        self._scan_policy = scan_policy
        self._mapping_repository = mapping_repository or MappingTemplateRepository()
        self._preview_builder = preview_builder

    def preview(self, request: MappingWorkspaceRequest) -> MappingWorkspaceSnapshot:
        _validate_request(request)
        try:
            receipt = self._file_store.resolve_receipt(
                project_key=request.project_key,
                receipt_id=request.receipt_id,
                content_sha256=request.content_sha256,
            )
        except StoredSourceNotFoundError as error:
            raise MappingWorkspaceNotFoundError from error
        except OriginalFileStoreError as error:
            raise MappingWorkspaceSourceError from error

        scan = self._rescan(receipt)
        return self.preview_scanned(request, receipt=receipt, scan=scan)

    def preview_scanned(
        self,
        request: MappingWorkspaceRequest,
        *,
        receipt: SourceFileReceipt,
        scan: WorkbookScan,
    ) -> MappingWorkspaceSnapshot:
        """Build the same fresh workspace from an already validated one-pass scan."""

        _validate_request(request)
        if (
            request.project_key != receipt.project_key
            or request.receipt_id != receipt.receipt_id
            or request.content_sha256 != receipt.content_sha256
            or scan.source_name != receipt.original_filename
            or scan.source_size_bytes != receipt.size_bytes
            or scan.source_sha256_before != receipt.content_sha256
            or scan.source_sha256_after != receipt.content_sha256
        ):
            raise MappingWorkspaceSourceError("MAPPING_SCANNED_EVIDENCE_MISMATCH")
        try:
            with self._database.session() as session:
                catalog = self._mapping_repository.load_catalog(
                    session,
                    project_key=receipt.project_key,
                )
        except MappingTemplatePersistenceError as error:
            raise MappingWorkspaceSourceError("MAPPING_CATALOG_UNAVAILABLE") from error

        mapping_result = self._preview_builder(
            scan,
            MappingPreviewRequest(
                project_key=receipt.project_key,
                supplier_scope=request.supplier_scope,
            ),
            catalog,
        )
        source_cells = _source_cell_page(
            scan,
            offset=request.cell_offset,
            limit=request.cell_limit,
        )
        preview = mapping_result.preview
        if preview is None:
            return MappingWorkspaceSnapshot(
                state=MappingWorkspaceState.MAPPING_REQUIRED,
                mode=MappingWorkspaceMode.MANUAL_SOURCE_REVIEW,
                status_label="수동 매핑 검토 필요",
                message="승인된 동일 양식을 찾지 못했습니다. 정확한 원본 셀을 검토해 주세요.",
                receipt=receipt,
                supplier_scope=request.supplier_scope,
                scan=scan,
                source_cells=source_cells,
                issues=mapping_result.issues,
                preview=None,
                template=None,
            )

        try:
            with self._database.session() as session:
                record = self._mapping_repository.get(
                    session,
                    project_key=receipt.project_key,
                    supplier_scope=request.supplier_scope,
                    template_id=preview.template_id,
                    revision=preview.template_revision,
                )
                payload_sha256 = session.scalar(
                    select(MappingTemplateRevisionRow.payload_sha256).where(
                        MappingTemplateRevisionRow.id == record.revision_id
                    )
                )
        except MappingTemplatePersistenceError as error:
            raise MappingWorkspaceSourceError("MAPPING_TEMPLATE_PROOF_MISSING") from error
        if payload_sha256 is None:
            raise MappingWorkspaceSourceError("MAPPING_TEMPLATE_PROOF_MISSING")
        return MappingWorkspaceSnapshot(
            state=MappingWorkspaceState.PREVIEW_READY,
            mode=MappingWorkspaceMode.APPROVED_TEMPLATE,
            status_label="승인된 매핑 미리보기 준비",
            message="승인된 동일 양식으로 원본 셀 미리보기를 만들었습니다.",
            receipt=receipt,
            supplier_scope=request.supplier_scope,
            scan=scan,
            source_cells=source_cells,
            issues=(),
            preview=preview,
            template=ApprovedTemplateProof(
                history_id=record.history_id,
                revision_id=record.revision_id,
                template_id=record.template.template_id,
                schema_version=record.template.schema_version,
                revision=record.template.revision,
                status=record.template.status.value,
                payload_sha256=payload_sha256,
                effective_from=record.template.effective_from,
                effective_to=record.resolved_effective_to,
                approved_by=preview.template_approved_by,
                approved_at=preview.template_approved_at,
                history_row_version=record.history_row_version,
                revision_row_version=record.revision_row_version,
            ),
        )

    def _rescan(self, receipt: SourceFileReceipt) -> WorkbookScan:
        try:
            with self._file_store.open_source(receipt) as source:
                scan = self._scanner.scan_stream(
                    source,
                    source_name=receipt.original_filename,
                    policy=self._scan_policy,
                )
        except WorkbookScanFailure as error:
            raise MappingWorkspaceSourceError("MAPPING_SOURCE_SCAN_FAILED") from error
        except OriginalFileStoreError as error:
            raise MappingWorkspaceSourceError from error
        except Exception as error:
            raise MappingWorkspaceSourceError from error
        if not (receipt.content_sha256 == scan.source_sha256_before == scan.source_sha256_after):
            raise MappingWorkspaceSourceError("MAPPING_SOURCE_HASH_MISMATCH")
        if receipt.size_bytes != scan.source_size_bytes:
            raise MappingWorkspaceSourceError("MAPPING_SOURCE_SIZE_MISMATCH")
        if receipt.original_filename != scan.source_name:
            raise MappingWorkspaceSourceError("MAPPING_SOURCE_NAME_MISMATCH")
        return scan


def _validate_request(request: MappingWorkspaceRequest) -> None:
    for field_name in ("project_key", "receipt_id", "content_sha256", "supplier_scope"):
        value = getattr(request, field_name)
        if not value or value != value.strip():
            raise MappingWorkspaceValidationError(
                "MAPPING_SCOPE_REQUIRED",
                "프로젝트, 원본 식별자와 업체 범위를 정확히 입력해 주세요.",
                "매핑 범위 필요",
            )
    if request.cell_offset < 0 or not 1 <= request.cell_limit <= 200:
        raise MappingWorkspaceValidationError(
            "INVALID_SOURCE_CELL_PAGE",
            "원본 셀 조회 범위가 올바르지 않습니다.",
            "셀 조회 범위 오류",
        )
    if len(request.supplier_scope) > 200:
        raise MappingWorkspaceValidationError(
            "INVALID_SUPPLIER_SCOPE",
            "업체 범위는 200자 이내로 입력해 주세요.",
            "업체 범위 오류",
        )


def _source_cell_page(scan: WorkbookScan, *, offset: int, limit: int) -> SourceCellPage:
    total = sum(len(sheet.cells) for sheet in scan.sheets)
    ordered_cells = (
        _review_cell(sheet.name, sheet.position, cell)
        for sheet in sorted(scan.sheets, key=lambda value: value.position)
        for cell in sheet.cells
    )
    selected = tuple(islice(ordered_cells, offset, offset + limit))
    return SourceCellPage(
        offset=offset,
        limit=limit,
        total=total,
        truncated=offset + len(selected) < total,
        cells=selected,
    )


def _review_cell(
    sheet_name: str,
    sheet_position: int,
    cell: CellEvidence,
) -> SourceCellReviewEvidence:
    return SourceCellReviewEvidence(
        sheet_name=sheet_name,
        sheet_position=sheet_position,
        coordinate=cell.coordinate,
        raw_value=tag_source_value(cell.stored_value),
        cached_value=tag_source_value(cell.cached_value),
        formula_text=cell.formula_text,
        number_format=cell.number_format,
        data_type=cell.data_type,
        display_value=cell.display_value,
        display_value_status=cell.display_value_status.value,
    )


def tag_source_value(value: object) -> TaggedSourceValue:
    python_type = type(value).__qualname__
    if value is None:
        return TaggedSourceValue(SourceValueKind.NULL, None, python_type)
    if isinstance(value, bool):
        return TaggedSourceValue(SourceValueKind.BOOLEAN, value, python_type)
    if isinstance(value, int):
        return TaggedSourceValue(SourceValueKind.INTEGER, value, python_type)
    if isinstance(value, float):
        if math.isfinite(value):
            return TaggedSourceValue(SourceValueKind.NUMBER, value, python_type)
        return TaggedSourceValue(SourceValueKind.UNSUPPORTED, None, python_type)
    if isinstance(value, Decimal):
        return TaggedSourceValue(SourceValueKind.DECIMAL, str(value), python_type)
    if isinstance(value, datetime):
        return TaggedSourceValue(SourceValueKind.DATETIME, value.isoformat(), python_type)
    if isinstance(value, date):
        return TaggedSourceValue(SourceValueKind.DATE, value.isoformat(), python_type)
    if isinstance(value, str):
        return TaggedSourceValue(SourceValueKind.TEXT, value, python_type)
    return TaggedSourceValue(SourceValueKind.UNSUPPORTED, None, python_type)
