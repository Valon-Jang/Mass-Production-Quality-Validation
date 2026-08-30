"""Receipt-bound manual Mapping draft, review, and approval orchestration.

The browser supplies only exact source-cell selections and optimistic-lock
versions.  This service reloads and rescans the immutable receipt for every
command, derives the schema-v2 fingerprint on the server, injects the trusted
local owner, and delegates transactional workflow writes to the existing
Mapping Template command service.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from typing import BinaryIO, Protocol
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.application.mapping_preview import (
    MappingTemplateCatalog,
    build_mapping_preview,
)
from app.application.mapping_template_commands import (
    ApproveMappingTemplateRevisionCommand,
    CreateMappingTemplateRevisionCommand,
    MappingTemplateAuthorizationError,
    MappingTemplateCommandService,
    ReviewMappingTemplateRevisionCommand,
)
from app.domain.identity import LOCAL_OWNER
from app.domain.mapping import (
    CellAddress,
    HeaderTokenAssertion,
    IdentifierKind,
    IdentifierMapping,
    InspectionRowMapping,
    MappingPreview,
    MappingPreviewRequest,
    MappingPreviewState,
    MappingTemplate,
    MappingTemplateStatus,
    MergeSignatureAssertion,
    RowStructureAssertion,
    SheetStructureAssertion,
    SystemJudgmentStatus,
    TemplateHistoryError,
    TemplateHistoryErrorCode,
    WorkbookFingerprint,
)
from app.domain.source_file import SourceFileReceipt
from app.domain.workbook_scan import (
    CellEvidence,
    ScanPolicy,
    WorkbookScan,
    WorkbookScanFailure,
)
from app.infrastructure.audit import AuditLog
from app.infrastructure.database import Database
from app.infrastructure.file_store import OriginalFileStoreError, StoredSourceNotFoundError
from app.infrastructure.mapping_templates import (
    MappingTemplateNotFoundError,
    MappingTemplatePayloadIntegrityError,
    MappingTemplatePersistenceError,
    MappingTemplateRepository,
    PersistedMappingTemplate,
    StaleMappingTemplateWriteError,
)

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_PROSPECTIVE_APPROVAL_TIME = datetime(2000, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True, order=True)
class CellSelection:
    sheet_name: str
    coordinate: str

    def address(self) -> CellAddress:
        return CellAddress(sheet_name=self.sheet_name, coordinate=self.coordinate)


@dataclass(frozen=True, slots=True)
class IdentifierSelection:
    kind: IdentifierKind
    source: CellSelection


@dataclass(frozen=True, slots=True)
class InspectionRowSelection:
    row_key: str
    item: CellSelection
    method: CellSelection | None = None
    instrument: CellSelection | None = None
    specification: CellSelection | None = None
    tolerance: CellSelection | None = None
    minimum: CellSelection | None = None
    maximum: CellSelection | None = None
    sample_cells: tuple[CellSelection, ...] = ()
    supplier_result: CellSelection | None = None
    section: CellSelection | None = None
    category: CellSelection | None = None
    unit: CellSelection | None = None
    measurement_point: CellSelection | None = None
    measurement_location: CellSelection | None = None
    cavity: CellSelection | None = None
    target: CellSelection | None = None
    lsl: CellSelection | None = None
    usl: CellSelection | None = None
    source_spec_revision: CellSelection | None = None

    def mapping(self) -> InspectionRowMapping:
        return InspectionRowMapping(
            row_key=self.row_key,
            item=self.item.address(),
            method=_address(self.method),
            instrument=_address(self.instrument),
            specification=_address(self.specification),
            tolerance=_address(self.tolerance),
            minimum=_address(self.minimum),
            maximum=_address(self.maximum),
            sample_cells=tuple(cell.address() for cell in self.sample_cells),
            supplier_result=_address(self.supplier_result),
            section=_address(self.section),
            category=_address(self.category),
            unit=_address(self.unit),
            measurement_point=_address(self.measurement_point),
            measurement_location=_address(self.measurement_location),
            cavity=_address(self.cavity),
            target=_address(self.target),
            lsl=_address(self.lsl),
            usl=_address(self.usl),
            source_spec_revision=_address(self.source_spec_revision),
        )


@dataclass(frozen=True, slots=True)
class CreateMappingDraftRequest:
    project_key: str
    receipt_id: str
    content_sha256: str
    supplier_scope: str
    effective_from: date
    effective_to: date | None
    expected_history_row_version: int
    reason: str
    header_assertion_cells: tuple[CellSelection, ...]
    identifiers: tuple[IdentifierSelection, ...]
    inspection_rows: tuple[InspectionRowSelection, ...]


@dataclass(frozen=True, slots=True)
class MappingWorkflowRequest:
    project_key: str
    receipt_id: str
    content_sha256: str
    supplier_scope: str
    expected_history_row_version: int
    expected_revision_row_version: int
    reason: str


@dataclass(frozen=True, slots=True)
class MappingWorkflowCapabilities:
    can_review: bool
    can_approve: bool
    additional_revisions_supported: bool = False


@dataclass(frozen=True, slots=True)
class MappingWorkflowSnapshot:
    template_id: str
    schema_version: str
    revision: int
    status: MappingTemplateStatus
    project_key: str
    supplier_scope: str
    effective_from: date
    effective_to: date | None
    history_id: str
    revision_id: str
    history_row_version: int
    revision_row_version: int
    reviewed_by: str | None
    reviewed_at: datetime | None
    approved_by: str | None
    approved_at: datetime | None
    capabilities: MappingWorkflowCapabilities


@dataclass(frozen=True, slots=True)
class MappingSourceProof:
    receipt_id: str
    content_sha256: str
    original_filename: str
    size_bytes: int
    fingerprint_sha256: str
    header_assertion_count: int
    identifier_count: int
    inspection_row_count: int
    mapped_cell_count: int
    official_values_created: bool = False
    calculations_performed: bool = False


@dataclass(frozen=True, slots=True)
class ApprovedMappingPreviewSummary:
    state: MappingPreviewState
    source_inspection_date: date
    identifier_count: int
    inspection_row_count: int
    system_judgment_status: SystemJudgmentStatus
    official_values_created: bool = False
    calculations_performed: bool = False


@dataclass(frozen=True, slots=True)
class MappingRegistrationResult:
    workflow: MappingWorkflowSnapshot
    proof: MappingSourceProof
    preview: ApprovedMappingPreviewSummary | None


class MappingRegistrationError(RuntimeError):
    """Safe application error that never exposes storage paths or raw exceptions."""

    def __init__(self, code: str, message: str, status_label: str) -> None:
        self.code = code
        self.safe_message = message
        self.status_label = status_label
        super().__init__(code)


class MappingRegistrationValidationError(MappingRegistrationError):
    pass


class MappingRegistrationNotFoundError(MappingRegistrationError):
    pass


class MappingRegistrationConflictError(MappingRegistrationError):
    pass


class MappingRegistrationUnavailableError(MappingRegistrationError):
    pass


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


class MappingRegistrationService:
    """Create/review/approve one new schema-v2 history from immutable source evidence."""

    def __init__(
        self,
        *,
        database: Database,
        file_store: ReceiptReplayStore,
        scanner: StreamWorkbookScanner,
        scan_policy: ScanPolicy,
        mapping_repository: MappingTemplateRepository | None = None,
        command_service: MappingTemplateCommandService | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._database = database
        self._file_store = file_store
        self._scanner = scanner
        self._scan_policy = scan_policy
        self._repository = mapping_repository or MappingTemplateRepository()
        self._commands = command_service or MappingTemplateCommandService(
            database,
            repository=self._repository,
        )
        self._id_factory = id_factory or (lambda: uuid4().hex)

    def create_draft(self, request: CreateMappingDraftRequest) -> MappingRegistrationResult:
        _validate_source_scope(request)
        if request.expected_history_row_version != 0:
            raise _validation(
                "NEW_HISTORY_VERSION_REQUIRED",
                "새 매핑 이력은 예상 이력 버전 0으로 시작해야 합니다.",
            )
        if not request.header_assertion_cells:
            raise _validation(
                "HEADER_ASSERTION_REQUIRED",
                "머리글 기준 셀을 하나 이상 선택해 주세요.",
            )
        receipt, scan = self._receipt_scan(request)
        try:
            identifiers = tuple(
                IdentifierMapping(selection.kind, selection.source.address())
                for selection in request.identifiers
            )
            rows = tuple(selection.mapping() for selection in request.inspection_rows)
            supplier_alias = _supplier_alias(scan, identifiers)
            fingerprint = _derive_fingerprint(
                scan,
                header_sources=tuple(cell.address() for cell in request.header_assertion_cells),
                rows=rows,
            )
            template = MappingTemplate(
                template_id=_new_template_id(self._id_factory()),
                schema_version="2",
                revision=1,
                status=MappingTemplateStatus.DRAFT,
                project_key=request.project_key,
                supplier_scope=request.supplier_scope,
                supplier_source_aliases=(supplier_alias,),
                approved_by=None,
                approved_at=None,
                effective_from=request.effective_from,
                effective_to=request.effective_to,
                fingerprint=fingerprint,
                identifiers=identifiers,
                inspection_rows=rows,
            )
            _require_preview_ready(
                scan,
                template,
                existing_catalog=self._catalog(request.project_key),
            )
        except MappingRegistrationError:
            raise
        except (TypeError, ValueError) as error:
            raise _validation(
                "INVALID_MAPPING_SELECTION",
                "선택한 셀 역할과 매핑 구조를 확인해 주세요.",
            ) from error

        source_reference = _source_reference(receipt)
        try:
            record = self._commands.create_revision(
                CreateMappingTemplateRevisionCommand(
                    template=template,
                    expected_history_row_version=request.expected_history_row_version,
                    actor=LOCAL_OWNER,
                    reason=request.reason,
                    source_reference=source_reference,
                )
            )
        except Exception as error:
            raise _translate_command_error(error) from error
        return _result(record, receipt, preview=None)

    def review(
        self,
        *,
        template_id: str,
        revision: int,
        request: MappingWorkflowRequest,
    ) -> MappingRegistrationResult:
        _validate_source_scope(request)
        _validate_supported_revision(template_id, revision)
        receipt, scan = self._receipt_scan(request)
        record = self._record(request, template_id, revision)
        self._assert_same_receipt(record, receipt)
        _validate_persisted_source(
            scan,
            record.template,
            existing_catalog=self._catalog(request.project_key),
        )
        try:
            reviewed = self._commands.review(
                ReviewMappingTemplateRevisionCommand(
                    project_key=request.project_key,
                    supplier_scope=request.supplier_scope,
                    template_id=template_id,
                    revision=revision,
                    expected_history_row_version=request.expected_history_row_version,
                    expected_revision_row_version=request.expected_revision_row_version,
                    actor=LOCAL_OWNER,
                    reason=request.reason,
                    source_reference=_source_reference(receipt),
                )
            )
        except Exception as error:
            raise _translate_command_error(error) from error
        return _result(reviewed, receipt, preview=None)

    def approve(
        self,
        *,
        template_id: str,
        revision: int,
        request: MappingWorkflowRequest,
    ) -> MappingRegistrationResult:
        _validate_source_scope(request)
        _validate_supported_revision(template_id, revision)
        receipt, scan = self._receipt_scan(request)
        record = self._record(request, template_id, revision)
        self._assert_same_receipt(record, receipt)
        _validate_persisted_source(
            scan,
            record.template,
            existing_catalog=self._catalog(request.project_key),
        )
        try:
            approved = self._commands.approve(
                ApproveMappingTemplateRevisionCommand(
                    project_key=request.project_key,
                    supplier_scope=request.supplier_scope,
                    template_id=template_id,
                    revision=revision,
                    expected_history_row_version=request.expected_history_row_version,
                    expected_revision_row_version=request.expected_revision_row_version,
                    actor=LOCAL_OWNER,
                    reason=request.reason,
                    source_reference=_source_reference(receipt),
                )
            )
        except Exception as error:
            raise _translate_command_error(error) from error

        try:
            with self._database.session() as session:
                catalog = self._repository.load_catalog(
                    session,
                    project_key=request.project_key,
                )
        except (MappingTemplatePersistenceError, SQLAlchemyError) as error:
            raise _unavailable("MAPPING_CATALOG_UNAVAILABLE") from error
        preview_result = build_mapping_preview(
            scan,
            MappingPreviewRequest(
                project_key=request.project_key,
                supplier_scope=request.supplier_scope,
            ),
            catalog,
        )
        if preview_result.state != MappingPreviewState.PREVIEW_READY:
            raise _unavailable("APPROVED_PREVIEW_RELOAD_FAILED")
        preview = preview_result.preview
        if preview is None or preview.template_id != approved.template.template_id:
            raise _unavailable("APPROVED_PREVIEW_RELOAD_FAILED")
        return _result(approved, receipt, preview=preview)

    def _receipt_scan(
        self,
        request: CreateMappingDraftRequest | MappingWorkflowRequest,
    ) -> tuple[SourceFileReceipt, WorkbookScan]:
        try:
            receipt = self._file_store.resolve_receipt(
                project_key=request.project_key,
                receipt_id=request.receipt_id,
                content_sha256=request.content_sha256,
            )
        except StoredSourceNotFoundError as error:
            raise MappingRegistrationNotFoundError(
                "MAPPING_RECEIPT_NOT_FOUND",
                "해당 프로젝트에서 보존된 원본을 찾을 수 없습니다.",
                "원본 없음",
            ) from error
        except OriginalFileStoreError as error:
            raise _unavailable("MAPPING_SOURCE_UNAVAILABLE") from error
        try:
            with self._file_store.open_source(receipt) as source:
                scan = self._scanner.scan_stream(
                    source,
                    source_name=receipt.original_filename,
                    policy=self._scan_policy,
                )
        except WorkbookScanFailure as error:
            raise MappingRegistrationConflictError(
                "MAPPING_SOURCE_SCAN_FAILED",
                "보존된 원본의 구조를 다시 확인할 수 없습니다.",
                "원본 스캔 실패",
            ) from error
        except OriginalFileStoreError as error:
            raise _unavailable("MAPPING_SOURCE_UNAVAILABLE") from error
        except Exception as error:
            raise _unavailable("MAPPING_SOURCE_SCAN_FAILED") from error
        if not (receipt.content_sha256 == scan.source_sha256_before == scan.source_sha256_after):
            raise MappingRegistrationConflictError(
                "MAPPING_SOURCE_HASH_MISMATCH",
                "보존된 원본의 식별자가 일치하지 않습니다.",
                "원본 무결성 오류",
            )
        if (
            receipt.size_bytes != scan.source_size_bytes
            or receipt.original_filename != scan.source_name
        ):
            raise MappingRegistrationConflictError(
                "MAPPING_SOURCE_IDENTITY_MISMATCH",
                "보존된 원본의 이름 또는 크기가 일치하지 않습니다.",
                "원본 무결성 오류",
            )
        return receipt, scan

    def _record(
        self,
        request: MappingWorkflowRequest,
        template_id: str,
        revision: int,
    ) -> PersistedMappingTemplate:
        try:
            with self._database.session() as session:
                return self._repository.get(
                    session,
                    project_key=request.project_key,
                    supplier_scope=request.supplier_scope,
                    template_id=template_id,
                    revision=revision,
                )
        except MappingTemplateNotFoundError as error:
            raise MappingRegistrationNotFoundError(
                "MAPPING_TEMPLATE_NOT_FOUND",
                "요청한 매핑 초안을 찾을 수 없습니다.",
                "매핑 없음",
            ) from error
        except MappingTemplatePersistenceError as error:
            raise _unavailable("MAPPING_TEMPLATE_UNAVAILABLE") from error

    def _catalog(self, project_key: str) -> MappingTemplateCatalog:
        try:
            with self._database.session() as session:
                return self._repository.load_catalog(session, project_key=project_key)
        except (MappingTemplatePersistenceError, SQLAlchemyError) as error:
            raise _unavailable("MAPPING_CATALOG_UNAVAILABLE") from error

    def _assert_same_receipt(
        self,
        record: PersistedMappingTemplate,
        receipt: SourceFileReceipt,
    ) -> None:
        target_id = _target_id(record.template)
        try:
            with self._database.session() as session:
                creation_audits = tuple(
                    session.scalars(
                        select(AuditLog).where(
                            AuditLog.action == "MAPPING_TEMPLATE_REVISION_CREATED",
                            AuditLog.target_type == "mapping_template_revision",
                            AuditLog.target_id == target_id,
                        )
                    ).all()
                )
        except SQLAlchemyError as error:
            raise _unavailable("MAPPING_TEMPLATE_PROOF_UNAVAILABLE") from error
        if len(creation_audits) != 1:
            raise MappingRegistrationConflictError(
                "MAPPING_TEMPLATE_RECEIPT_PROOF_MISSING",
                "초안과 원본 Receipt의 연결 근거를 확인할 수 없습니다.",
                "원본 연결 확인 실패",
            )
        audit = creation_audits[0]
        state = audit.after_state
        expected_state = {
            "project_key": record.template.project_key,
            "supplier_scope": record.template.supplier_scope,
            "template_id": record.template.template_id,
            "revision": record.template.revision,
            "status": MappingTemplateStatus.DRAFT.value,
            "effective_from": record.template.effective_from.isoformat(),
            "declared_effective_to": (
                record.template.effective_to.isoformat() if record.template.effective_to else None
            ),
            "resolved_effective_to": None,
            "reviewed_by": None,
            "reviewed_at": None,
            "approved_by": None,
            "approved_at": None,
            "history_row_version": 1,
            "revision_row_version": 1,
        }
        if (
            audit.actor_id != LOCAL_OWNER.actor_id
            or audit.actor_kind != LOCAL_OWNER.kind.value
            or set(audit.actor_roles) != {role.value for role in LOCAL_OWNER.roles}
            or audit.source_reference != _source_reference(receipt)
            or state != expected_state
        ):
            raise MappingRegistrationConflictError(
                "MAPPING_TEMPLATE_RECEIPT_MISMATCH",
                "초안을 만들 때 사용한 원본 Receipt와 일치하지 않습니다.",
                "원본 연결 불일치",
            )


def _address(selection: CellSelection | None) -> CellAddress | None:
    return None if selection is None else selection.address()


def _validate_source_scope(request: CreateMappingDraftRequest | MappingWorkflowRequest) -> None:
    for field_name in ("project_key", "receipt_id", "supplier_scope", "reason"):
        value = getattr(request, field_name)
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise _validation(
                "MAPPING_SCOPE_REQUIRED",
                "프로젝트, 원본, 업체 범위와 사유를 정확히 입력해 주세요.",
            )
    if not _SHA256_PATTERN.fullmatch(request.content_sha256):
        raise _validation(
            "INVALID_CONTENT_SHA256",
            "원본 SHA-256 식별자가 올바르지 않습니다.",
        )


def _validate_supported_revision(template_id: str, revision: int) -> None:
    if not template_id.strip():
        raise _validation("MAPPING_TEMPLATE_ID_REQUIRED", "매핑 Template ID가 필요합니다.")
    if revision != 1:
        raise MappingRegistrationValidationError(
            "ADDITIONAL_REVISION_NOT_SUPPORTED",
            "이번 단계에서는 새 이력의 첫 번째 Revision만 지원합니다.",
            "추가 Revision 미지원",
        )


def _new_template_id(value: str) -> str:
    if not re.fullmatch(r"[0-9a-f]{32}", value):
        raise ValueError("id_factory must return 32 lowercase hexadecimal characters")
    return f"map-{value}"


def _supplier_alias(scan: WorkbookScan, identifiers: tuple[IdentifierMapping, ...]) -> str:
    supplier_mappings = tuple(
        mapping for mapping in identifiers if mapping.kind == IdentifierKind.SUPPLIER
    )
    if len(supplier_mappings) != 1:
        raise _validation(
            "SUPPLIER_IDENTIFIER_REQUIRED",
            "업체 원본 셀을 정확히 하나 선택해 주세요.",
        )
    evidence = _exact_cell(scan, supplier_mappings[0].source)
    if (
        evidence.formula_text is not None
        or not isinstance(evidence.stored_value, str)
        or not evidence.stored_value.strip()
    ):
        raise _validation(
            "SUPPLIER_SOURCE_TEXT_REQUIRED",
            "업체 셀은 수식이 아닌 비어 있지 않은 원본 문자열이어야 합니다.",
        )
    return evidence.stored_value


def _derive_fingerprint(
    scan: WorkbookScan,
    *,
    header_sources: tuple[CellAddress, ...],
    rows: tuple[InspectionRowMapping, ...],
) -> WorkbookFingerprint:
    if not header_sources:
        raise _validation("HEADER_ASSERTION_REQUIRED", "머리글 기준 셀이 필요합니다.")
    headers: list[HeaderTokenAssertion] = []
    for source in header_sources:
        evidence = _exact_cell(scan, source)
        if (
            evidence.formula_text is not None
            or not isinstance(evidence.stored_value, str)
            or not evidence.stored_value.strip()
        ):
            raise _validation(
                "HEADER_SOURCE_TEXT_REQUIRED",
                "머리글 기준 셀은 수식이 아닌 비어 있지 않은 문자열이어야 합니다.",
            )
        headers.append(HeaderTokenAssertion(source=source, expected_token=evidence.stored_value))

    sheet_structures = tuple(
        SheetStructureAssertion(
            sheet_name=sheet.name,
            expected_position=sheet.position,
            expected_kind=sheet.kind,
            expected_visibility=sheet.visibility,
            expected_used_range=sheet.used_range,
        )
        for sheet in sorted(scan.sheets, key=lambda item: item.position)
    )
    merge_signatures = tuple(
        MergeSignatureAssertion(
            sheet_name=sheet.name,
            expected_merged_ranges=tuple(sorted(sheet.merged_ranges)),
        )
        for sheet in sorted(scan.sheets, key=lambda item: item.position)
    )
    row_structures: list[RowStructureAssertion] = []
    for row in rows:
        item_row = row.item.row_index
        if any(source.row_index != item_row for source in row.all_addresses):
            raise _validation(
                "ROW_ORIENTED_MAPPING_REQUIRED",
                "한 검사항목의 모든 역할 셀은 같은 행에 있어야 합니다.",
            )
        matching_sheets = tuple(sheet for sheet in scan.sheets if sheet.name == row.item.sheet_name)
        if len(matching_sheets) != 1:
            raise _validation(
                "MAPPED_SHEET_NOT_FOUND",
                "선택한 검사항목 Sheet를 정확히 찾을 수 없습니다.",
            )
        expected_cells = tuple(
            CellAddress(sheet_name=row.item.sheet_name, coordinate=cell.coordinate)
            for cell in matching_sheets[0].cells
            if CellAddress(row.item.sheet_name, cell.coordinate).row_index == item_row
        )
        if not expected_cells or not set(row.all_addresses).issubset(expected_cells):
            raise _validation(
                "MAPPED_ROW_EVIDENCE_MISSING",
                "선택한 행의 전체 원본 셀 근거를 확인할 수 없습니다.",
            )
        row_structures.append(
            RowStructureAssertion(
                row_key=row.row_key,
                sheet_name=row.item.sheet_name,
                row_index=item_row,
                expected_non_empty_cells=tuple(sorted(expected_cells)),
            )
        )
    return WorkbookFingerprint(
        header_tokens=tuple(headers),
        sheet_structures=sheet_structures,
        merge_signatures=merge_signatures,
        row_structures=tuple(row_structures),
    )


def _exact_cell(scan: WorkbookScan, source: CellAddress) -> CellEvidence:
    matches = tuple(
        cell
        for sheet in scan.sheets
        if sheet.name == source.sheet_name
        for cell in sheet.cells
        if cell.coordinate == source.coordinate
    )
    if len(matches) != 1:
        raise _validation(
            "SOURCE_CELL_NOT_FOUND",
            "선택한 원본 셀을 정확히 찾을 수 없습니다.",
        )
    return matches[0]


def _validate_persisted_source(
    scan: WorkbookScan,
    template: MappingTemplate,
    *,
    existing_catalog: MappingTemplateCatalog,
) -> None:
    if template.schema_version != "2" or template.revision != 1:
        raise MappingRegistrationConflictError(
            "MAPPING_TEMPLATE_SCOPE_MISMATCH",
            "이 수동 등록 단계에서 만든 schema-v2 첫 Revision이 아닙니다.",
            "매핑 범위 불일치",
        )
    rebuilt = _derive_fingerprint(
        scan,
        header_sources=tuple(assertion.source for assertion in template.fingerprint.header_tokens),
        rows=template.inspection_rows,
    )
    if rebuilt != template.fingerprint:
        raise MappingRegistrationConflictError(
            "MAPPING_FINGERPRINT_MISMATCH",
            "현재 원본 스캔과 저장된 매핑 지문이 일치하지 않습니다.",
            "매핑 지문 불일치",
        )
    alias = _supplier_alias(scan, template.identifiers)
    if template.supplier_source_aliases != (alias,):
        raise MappingRegistrationConflictError(
            "MAPPING_SUPPLIER_EVIDENCE_MISMATCH",
            "저장된 업체 근거가 선택한 원본 셀과 일치하지 않습니다.",
            "업체 근거 불일치",
        )
    _require_preview_ready(scan, template, existing_catalog=existing_catalog)


class _ProspectiveCatalog:
    def __init__(
        self,
        existing: MappingTemplateCatalog,
        prospective: MappingTemplate,
    ) -> None:
        self._existing = existing
        self._prospective = prospective
        prospective_key = _template_key(prospective)
        self._templates = (
            *(
                template
                for template in existing.templates
                if _template_key(template) != prospective_key
            ),
            prospective,
        )

    @property
    def templates(self) -> tuple[MappingTemplate, ...]:
        return self._templates

    def is_effective_on(self, template: MappingTemplate, value: date) -> bool:
        if template is self._prospective:
            return template.effective_from <= value <= (template.effective_to or date.max)
        return self._existing.is_effective_on(template, value)

    def resolved_effective_to(self, template: MappingTemplate) -> date | None:
        if template is self._prospective:
            return template.effective_to
        return self._existing.resolved_effective_to(template)


def _require_preview_ready(
    scan: WorkbookScan,
    template: MappingTemplate,
    *,
    existing_catalog: MappingTemplateCatalog,
) -> MappingPreview:
    prospective = replace(
        template,
        status=MappingTemplateStatus.APPROVED,
        reviewed_by=template.reviewed_by or LOCAL_OWNER.actor_id,
        reviewed_at=template.reviewed_at or _PROSPECTIVE_APPROVAL_TIME,
        approved_by=LOCAL_OWNER.actor_id,
        approved_at=_PROSPECTIVE_APPROVAL_TIME,
    )
    result = build_mapping_preview(
        scan,
        MappingPreviewRequest(
            project_key=template.project_key,
            supplier_scope=template.supplier_scope,
        ),
        _ProspectiveCatalog(existing_catalog, prospective),
    )
    if result.state != MappingPreviewState.PREVIEW_READY or result.preview is None:
        codes = ",".join(sorted({issue.code.value for issue in result.issues}))
        raise MappingRegistrationValidationError(
            "MAPPING_SELECTION_NOT_PREVIEW_READY",
            f"선택한 원본 셀로 미리보기를 만들 수 없습니다. ({codes or 'UNKNOWN'})",
            "매핑 선택 확인 필요",
        )
    return result.preview


def _result(
    record: PersistedMappingTemplate,
    receipt: SourceFileReceipt,
    *,
    preview: MappingPreview | None,
) -> MappingRegistrationResult:
    template = record.template
    status = template.status
    return MappingRegistrationResult(
        workflow=MappingWorkflowSnapshot(
            template_id=template.template_id,
            schema_version=template.schema_version,
            revision=template.revision,
            status=status,
            project_key=template.project_key,
            supplier_scope=template.supplier_scope,
            effective_from=template.effective_from,
            effective_to=template.effective_to,
            history_id=record.history_id,
            revision_id=record.revision_id,
            history_row_version=record.history_row_version,
            revision_row_version=record.revision_row_version,
            reviewed_by=template.reviewed_by,
            reviewed_at=template.reviewed_at,
            approved_by=template.approved_by,
            approved_at=template.approved_at,
            capabilities=MappingWorkflowCapabilities(
                can_review=status == MappingTemplateStatus.DRAFT,
                can_approve=status == MappingTemplateStatus.REVIEWED,
            ),
        ),
        proof=MappingSourceProof(
            receipt_id=receipt.receipt_id,
            content_sha256=receipt.content_sha256,
            original_filename=receipt.original_filename,
            size_bytes=receipt.size_bytes,
            fingerprint_sha256=_fingerprint_digest(template.fingerprint),
            header_assertion_count=len(template.fingerprint.header_tokens),
            identifier_count=len(template.identifiers),
            inspection_row_count=len(template.inspection_rows),
            mapped_cell_count=(
                len(template.identifiers)
                + sum(len(row.all_addresses) for row in template.inspection_rows)
            ),
        ),
        preview=(
            None
            if preview is None
            else ApprovedMappingPreviewSummary(
                state=MappingPreviewState.PREVIEW_READY,
                source_inspection_date=preview.source_inspection_date,
                identifier_count=len(preview.identifiers),
                inspection_row_count=len(preview.inspection_rows),
                system_judgment_status=SystemJudgmentStatus.NOT_EVALUATED,
            )
        ),
    )


def _fingerprint_digest(fingerprint: WorkbookFingerprint) -> str:
    payload = {
        "header_tokens": [
            {
                "sheet_name": assertion.source.sheet_name,
                "coordinate": assertion.source.coordinate,
                "expected_token": assertion.expected_token,
            }
            for assertion in fingerprint.header_tokens
        ],
        "sheet_structures": [
            {
                "sheet_name": assertion.sheet_name,
                "expected_position": assertion.expected_position,
                "expected_kind": assertion.expected_kind.value,
                "expected_visibility": assertion.expected_visibility,
                "expected_used_range": assertion.expected_used_range,
            }
            for assertion in fingerprint.sheet_structures
        ],
        "merge_signatures": [
            {
                "sheet_name": assertion.sheet_name,
                "expected_merged_ranges": list(assertion.expected_merged_ranges),
            }
            for assertion in fingerprint.merge_signatures
        ],
        "row_structures": [
            {
                "row_key": assertion.row_key,
                "sheet_name": assertion.sheet_name,
                "row_index": assertion.row_index,
                "expected_non_empty_cells": [
                    {
                        "sheet_name": source.sheet_name,
                        "coordinate": source.coordinate,
                    }
                    for source in assertion.expected_non_empty_cells
                ],
            }
            for assertion in fingerprint.row_structures
        ],
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _source_reference(receipt: SourceFileReceipt) -> str:
    return (
        f"mapping-receipt:{receipt.project_key}:{receipt.receipt_id}:"
        f"sha256:{receipt.content_sha256}"
    )


def _target_id(template: MappingTemplate) -> str:
    return (
        f"{template.project_key}:{template.supplier_scope}:"
        f"{template.template_id}:{template.revision}"
    )


def _template_key(template: MappingTemplate) -> tuple[str, str, str, int]:
    return (
        template.project_key,
        template.supplier_scope,
        template.template_id,
        template.revision,
    )


def _translate_command_error(error: Exception) -> MappingRegistrationError:
    if isinstance(error, StaleMappingTemplateWriteError):
        return MappingRegistrationConflictError(
            "MAPPING_VERSION_CONFLICT",
            "다른 변경이 먼저 저장되었습니다. 최신 버전을 다시 확인해 주세요.",
            "버전 충돌",
        )
    if isinstance(error, MappingTemplateNotFoundError):
        return MappingRegistrationNotFoundError(
            "MAPPING_TEMPLATE_NOT_FOUND",
            "요청한 매핑 초안을 찾을 수 없습니다.",
            "매핑 없음",
        )
    if isinstance(error, MappingTemplatePayloadIntegrityError):
        return MappingRegistrationConflictError(
            "MAPPING_TEMPLATE_INTEGRITY_ERROR",
            "저장된 매핑 근거의 무결성을 확인할 수 없습니다.",
            "매핑 무결성 오류",
        )
    if isinstance(error, TemplateHistoryError):
        code = (
            "MAPPING_STATUS_CONFLICT"
            if error.code == TemplateHistoryErrorCode.INVALID_STATUS_TRANSITION
            else f"MAPPING_{error.code.value}"
        )
        return MappingRegistrationConflictError(
            code,
            "현재 상태에서는 요청한 매핑 변경을 적용할 수 없습니다.",
            "매핑 상태 충돌",
        )
    if isinstance(error, MappingTemplateAuthorizationError):
        return MappingRegistrationUnavailableError(
            "MAPPING_TRUST_BOUNDARY_FAILURE",
            "신뢰된 로컬 승인 경계를 확인할 수 없습니다.",
            "승인 경계 오류",
        )
    if isinstance(error, MappingTemplatePersistenceError):
        return _unavailable("MAPPING_TEMPLATE_UNAVAILABLE")
    if isinstance(error, (TypeError, ValueError)):
        return _validation(
            "INVALID_MAPPING_COMMAND",
            "매핑 요청 값과 사유를 확인해 주세요.",
        )
    return _unavailable("MAPPING_COMMAND_FAILED")


def _validation(code: str, message: str) -> MappingRegistrationValidationError:
    return MappingRegistrationValidationError(code, message, "매핑 요청 확인 필요")


def _unavailable(code: str) -> MappingRegistrationUnavailableError:
    return MappingRegistrationUnavailableError(
        code,
        "매핑 처리 근거를 안전하게 확인할 수 없습니다.",
        "매핑 처리 불가",
    )
