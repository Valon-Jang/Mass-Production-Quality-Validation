"""Safe receipt-replay Mapping Preview HTTP contract."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import date, datetime
from typing import Any, Never, Protocol

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from app.api.intake import SafeErrorResponse
from app.application.mapping_registration import (
    CellSelection,
    CreateMappingDraftRequest,
    IdentifierSelection,
    InspectionRowSelection,
    MappingRegistrationConflictError,
    MappingRegistrationError,
    MappingRegistrationNotFoundError,
    MappingRegistrationResult,
    MappingRegistrationUnavailableError,
    MappingRegistrationValidationError,
    MappingWorkflowRequest,
)
from app.application.mapping_workspace import (
    MappingWorkspaceError,
    MappingWorkspaceNotFoundError,
    MappingWorkspaceRequest,
    MappingWorkspaceSnapshot,
    MappingWorkspaceSourceError,
    MappingWorkspaceValidationError,
    SourceCellReviewEvidence,
    TaggedSourceValue,
    tag_source_value,
)
from app.domain.mapping import IdentifierKind, MappedCellEvidence
from app.domain.workbook_scan import ScanIssue, SheetScan, SourceLocation, SourceLocationKind


class MappingWorkspacePort(Protocol):
    def preview(self, request: MappingWorkspaceRequest) -> MappingWorkspaceSnapshot: ...


class MappingRegistrationPort(Protocol):
    def create_draft(self, request: CreateMappingDraftRequest) -> MappingRegistrationResult: ...

    def review(
        self,
        *,
        template_id: str,
        revision: int,
        request: MappingWorkflowRequest,
    ) -> MappingRegistrationResult: ...

    def approve(
        self,
        *,
        template_id: str,
        revision: int,
        request: MappingWorkflowRequest,
    ) -> MappingRegistrationResult: ...


class TaggedSourceValueResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    value: str | int | float | bool | None
    python_type: str


class MappingReceiptResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    receipt_id: str
    content_sha256: str
    original_filename: str
    received_at: datetime
    size_bytes: int
    model_candidates: tuple[str, ...]
    lot_candidates: tuple[str, ...]


class MappingScanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_size_bytes: int
    sha256_before: str
    sha256_after: str
    sheet_count: int
    estimated_cells: int
    external_link_count: int
    macro_handling: str
    sheets: tuple[MappingSheetResponse, ...]
    issues: tuple[ScanIssueResponse, ...]


class IndexRangeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: int
    end: int


class MappingSheetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    kind: str
    position: int
    visibility: str
    used_range: str | None
    estimated_cells: int
    merged_ranges: tuple[str, ...]
    hidden_row_ranges: tuple[IndexRangeResponse, ...]
    hidden_column_ranges: tuple[IndexRangeResponse, ...]
    protected: bool
    protected_actions: tuple[str, ...]
    formula_count: int
    issue_codes: tuple[str, ...]


class ScanIssueResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    severity: str
    message: str
    location: str | None


class SourceCellResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sheet_name: str
    sheet_position: int
    coordinate: str
    raw_value: TaggedSourceValueResponse
    cached_value: TaggedSourceValueResponse
    formula_text: str | None
    number_format: str
    data_type: str
    display_value: str | None
    display_value_status: str


class SourceCellPageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    offset: int
    limit: int
    total: int
    truncated: bool
    cells: tuple[SourceCellResponse, ...]


class MappingIssueResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    template_id: str | None
    template_revision: int | None
    sheet_name: str | None
    coordinate: str | None
    expected: str | None
    observed: str | None


class ApprovedTemplateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

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


class MappedCellResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sheet_name: str
    coordinate: str
    raw_value: TaggedSourceValueResponse
    cached_value: TaggedSourceValueResponse
    formula_text: str | None
    number_format: str
    data_type: str
    display_value: str | None
    display_value_status: str
    value_kind: str


class IdentifierPreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    evidence: MappedCellResponse


class InspectionRowPreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_key: str
    item: MappedCellResponse
    method: MappedCellResponse | None
    instrument: MappedCellResponse | None
    specification: MappedCellResponse | None
    tolerance: MappedCellResponse | None
    minimum: MappedCellResponse | None
    maximum: MappedCellResponse | None
    section: MappedCellResponse | None
    category: MappedCellResponse | None
    unit: MappedCellResponse | None
    measurement_point: MappedCellResponse | None
    measurement_location: MappedCellResponse | None
    cavity: MappedCellResponse | None
    target: MappedCellResponse | None
    lsl: MappedCellResponse | None
    usl: MappedCellResponse | None
    source_spec_revision: MappedCellResponse | None
    samples: tuple[MappedCellResponse, ...]
    supplier_result: MappedCellResponse | None


class ApprovedPreviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_inspection_date: date
    identifiers: tuple[IdentifierPreviewResponse, ...]
    inspection_rows: tuple[InspectionRowPreviewResponse, ...]


class MappingWorkspaceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: str
    mode: str
    status_label: str
    message: str
    supplier_scope: str
    ai_state: str
    draft_command_available: bool
    long_confirmation_available: bool
    official_values_created: bool
    calculations_performed: bool
    receipt: MappingReceiptResponse
    scan: MappingScanResponse
    source_cells: SourceCellPageResponse
    issues: tuple[MappingIssueResponse, ...]
    template: ApprovedTemplateResponse | None
    preview: ApprovedPreviewResponse | None


class MappingCellSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sheet_name: str = Field(min_length=1, max_length=120)
    coordinate: str = Field(min_length=2, max_length=10)


class MappingIdentifierSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: IdentifierKind
    source: MappingCellSelectionRequest


class MappingInspectionRowSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_key: str = Field(min_length=1, max_length=200)
    item: MappingCellSelectionRequest
    method: MappingCellSelectionRequest | None = None
    instrument: MappingCellSelectionRequest | None = None
    specification: MappingCellSelectionRequest | None = None
    tolerance: MappingCellSelectionRequest | None = None
    minimum: MappingCellSelectionRequest | None = None
    maximum: MappingCellSelectionRequest | None = None
    sample_cells: tuple[MappingCellSelectionRequest, ...] = ()
    supplier_result: MappingCellSelectionRequest | None = None
    section: MappingCellSelectionRequest | None = None
    category: MappingCellSelectionRequest | None = None
    unit: MappingCellSelectionRequest | None = None
    measurement_point: MappingCellSelectionRequest | None = None
    measurement_location: MappingCellSelectionRequest | None = None
    cavity: MappingCellSelectionRequest | None = None
    target: MappingCellSelectionRequest | None = None
    lsl: MappingCellSelectionRequest | None = None
    usl: MappingCellSelectionRequest | None = None
    source_spec_revision: MappingCellSelectionRequest | None = None


class CreateMappingDraftRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_key: str = Field(min_length=1, max_length=64)
    receipt_id: str = Field(min_length=1, max_length=128)
    content_sha256: str = Field(min_length=64, max_length=64)
    supplier_scope: str = Field(min_length=1, max_length=200)
    effective_from: date
    effective_to: date | None = None
    expected_history_row_version: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=2000)
    header_assertion_cells: tuple[MappingCellSelectionRequest, ...] = Field(min_length=1)
    identifiers: tuple[MappingIdentifierSelectionRequest, ...] = Field(min_length=1)
    inspection_rows: tuple[MappingInspectionRowSelectionRequest, ...] = Field(min_length=1)


class MappingWorkflowRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_key: str = Field(min_length=1, max_length=64)
    receipt_id: str = Field(min_length=1, max_length=128)
    content_sha256: str = Field(min_length=64, max_length=64)
    supplier_scope: str = Field(min_length=1, max_length=200)
    expected_history_row_version: int = Field(ge=1)
    expected_revision_row_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)


class MappingCapabilitiesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    can_review: bool
    can_approve: bool
    additional_revisions_supported: bool


class MappingWorkflowResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_id: str
    schema_version: str
    revision: int
    status: str
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
    capabilities: MappingCapabilitiesResponse


class MappingSourceProofResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    receipt_id: str
    content_sha256: str
    original_filename: str
    size_bytes: int
    fingerprint_sha256: str
    header_assertion_count: int
    identifier_count: int
    inspection_row_count: int
    mapped_cell_count: int
    official_values_created: bool
    calculations_performed: bool


class ApprovedMappingPreviewSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: str
    source_inspection_date: date
    identifier_count: int
    inspection_row_count: int
    system_judgment_status: str
    official_values_created: bool
    calculations_performed: bool


class MappingRegistrationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow: MappingWorkflowResponse
    proof: MappingSourceProofResponse
    preview: ApprovedMappingPreviewSummaryResponse | None


_SAFE_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    status.HTTP_400_BAD_REQUEST: {"model": SafeErrorResponse},
    status.HTTP_404_NOT_FOUND: {"model": SafeErrorResponse},
    status.HTTP_409_CONFLICT: {"model": SafeErrorResponse},
    status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": SafeErrorResponse},
    status.HTTP_503_SERVICE_UNAVAILABLE: {"model": SafeErrorResponse},
}


class _SafeMappingValidationRoute(APIRoute):
    """Redact Pydantic internals while retaining the OpenAPI request schemas."""

    def get_route_handler(
        self,
    ) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def safe_handler(request: Request) -> Response:
            try:
                return await original(request)
            except RequestValidationError as error:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "INVALID_MAPPING_REQUEST",
                        "message": "매핑 요청 형식과 필수 입력값을 확인해 주세요.",
                        "status_label": "매핑 요청 오류",
                    },
                ) from error

        return safe_handler


def create_mapping_router(service: MappingWorkspacePort) -> APIRouter:
    """Create an injected Mapping router without opening storage or a DB."""

    router = APIRouter(prefix="/api/v1/mapping/receipts", tags=["mapping"])

    @router.get(
        "/{receipt_id}/preview",
        response_model=MappingWorkspaceResponse,
        responses=_SAFE_ERROR_RESPONSES,
    )
    async def get_mapping_preview(
        receipt_id: str,
        project_key: str | None = None,
        content_sha256: str | None = None,
        supplier_scope: str | None = None,
        cell_offset: int = 0,
        cell_limit: int = 120,
    ) -> MappingWorkspaceResponse:
        if project_key is None or content_sha256 is None or supplier_scope is None:
            _raise_safe_error(
                status.HTTP_400_BAD_REQUEST,
                code="MAPPING_SCOPE_REQUIRED",
                message="프로젝트, 원본 식별자와 업체 범위를 모두 입력해 주세요.",
                status_label="매핑 범위 필요",
            )
        assert project_key is not None
        assert content_sha256 is not None
        assert supplier_scope is not None
        try:
            snapshot = await run_in_threadpool(
                service.preview,
                MappingWorkspaceRequest(
                    project_key=project_key,
                    receipt_id=receipt_id,
                    content_sha256=content_sha256,
                    supplier_scope=supplier_scope,
                    cell_offset=cell_offset,
                    cell_limit=cell_limit,
                ),
            )
        except MappingWorkspaceError as error:
            _raise_application_error(error)
        except Exception as error:
            raise _unexpected_http_error() from error
        return _response(snapshot)

    return router


def create_mapping_registration_router(service: MappingRegistrationPort) -> APIRouter:
    """Create trusted local Mapping workflow routes with no identity in request bodies."""

    router = APIRouter(
        prefix="/api/v1/mapping/templates",
        tags=["mapping"],
        route_class=_SafeMappingValidationRoute,
    )

    @router.post(
        "/drafts",
        response_model=MappingRegistrationResponse,
        status_code=status.HTTP_201_CREATED,
        responses=_SAFE_ERROR_RESPONSES,
    )
    async def create_mapping_draft(
        body: CreateMappingDraftRequestBody,
    ) -> MappingRegistrationResponse:
        try:
            result = await run_in_threadpool(
                service.create_draft,
                _draft_request(body),
            )
        except MappingRegistrationError as error:
            _raise_registration_error(error)
        except Exception as error:
            raise _unexpected_registration_http_error() from error
        return _registration_response(result)

    @router.post(
        "/{template_id}/revisions/{revision}/review",
        response_model=MappingRegistrationResponse,
        responses=_SAFE_ERROR_RESPONSES,
    )
    async def review_mapping_draft(
        template_id: str,
        revision: int,
        body: MappingWorkflowRequestBody,
    ) -> MappingRegistrationResponse:
        try:
            result = await run_in_threadpool(
                service.review,
                template_id=template_id,
                revision=revision,
                request=_workflow_request(body),
            )
        except MappingRegistrationError as error:
            _raise_registration_error(error)
        except Exception as error:
            raise _unexpected_registration_http_error() from error
        return _registration_response(result)

    @router.post(
        "/{template_id}/revisions/{revision}/approve",
        response_model=MappingRegistrationResponse,
        responses=_SAFE_ERROR_RESPONSES,
    )
    async def approve_mapping_draft(
        template_id: str,
        revision: int,
        body: MappingWorkflowRequestBody,
    ) -> MappingRegistrationResponse:
        try:
            result = await run_in_threadpool(
                service.approve,
                template_id=template_id,
                revision=revision,
                request=_workflow_request(body),
            )
        except MappingRegistrationError as error:
            _raise_registration_error(error)
        except Exception as error:
            raise _unexpected_registration_http_error() from error
        return _registration_response(result)

    return router


def _draft_request(body: CreateMappingDraftRequestBody) -> CreateMappingDraftRequest:
    return CreateMappingDraftRequest(
        project_key=body.project_key,
        receipt_id=body.receipt_id,
        content_sha256=body.content_sha256,
        supplier_scope=body.supplier_scope,
        effective_from=body.effective_from,
        effective_to=body.effective_to,
        expected_history_row_version=body.expected_history_row_version,
        reason=body.reason,
        header_assertion_cells=tuple(_cell_selection(cell) for cell in body.header_assertion_cells),
        identifiers=tuple(
            IdentifierSelection(
                kind=identifier.kind,
                source=_cell_selection(identifier.source),
            )
            for identifier in body.identifiers
        ),
        inspection_rows=tuple(_row_selection(row) for row in body.inspection_rows),
    )


def _workflow_request(body: MappingWorkflowRequestBody) -> MappingWorkflowRequest:
    return MappingWorkflowRequest(
        project_key=body.project_key,
        receipt_id=body.receipt_id,
        content_sha256=body.content_sha256,
        supplier_scope=body.supplier_scope,
        expected_history_row_version=body.expected_history_row_version,
        expected_revision_row_version=body.expected_revision_row_version,
        reason=body.reason,
    )


def _cell_selection(cell: MappingCellSelectionRequest) -> CellSelection:
    return CellSelection(sheet_name=cell.sheet_name, coordinate=cell.coordinate)


def _optional_cell_selection(
    cell: MappingCellSelectionRequest | None,
) -> CellSelection | None:
    return None if cell is None else _cell_selection(cell)


def _row_selection(row: MappingInspectionRowSelectionRequest) -> InspectionRowSelection:
    return InspectionRowSelection(
        row_key=row.row_key,
        item=_cell_selection(row.item),
        method=_optional_cell_selection(row.method),
        instrument=_optional_cell_selection(row.instrument),
        specification=_optional_cell_selection(row.specification),
        tolerance=_optional_cell_selection(row.tolerance),
        minimum=_optional_cell_selection(row.minimum),
        maximum=_optional_cell_selection(row.maximum),
        sample_cells=tuple(_cell_selection(cell) for cell in row.sample_cells),
        supplier_result=_optional_cell_selection(row.supplier_result),
        section=_optional_cell_selection(row.section),
        category=_optional_cell_selection(row.category),
        unit=_optional_cell_selection(row.unit),
        measurement_point=_optional_cell_selection(row.measurement_point),
        measurement_location=_optional_cell_selection(row.measurement_location),
        cavity=_optional_cell_selection(row.cavity),
        target=_optional_cell_selection(row.target),
        lsl=_optional_cell_selection(row.lsl),
        usl=_optional_cell_selection(row.usl),
        source_spec_revision=_optional_cell_selection(row.source_spec_revision),
    )


def _registration_response(result: MappingRegistrationResult) -> MappingRegistrationResponse:
    workflow = result.workflow
    proof = result.proof
    preview = result.preview
    return MappingRegistrationResponse(
        workflow=MappingWorkflowResponse(
            template_id=workflow.template_id,
            schema_version=workflow.schema_version,
            revision=workflow.revision,
            status=workflow.status.value,
            project_key=workflow.project_key,
            supplier_scope=workflow.supplier_scope,
            effective_from=workflow.effective_from,
            effective_to=workflow.effective_to,
            history_id=workflow.history_id,
            revision_id=workflow.revision_id,
            history_row_version=workflow.history_row_version,
            revision_row_version=workflow.revision_row_version,
            reviewed_by=workflow.reviewed_by,
            reviewed_at=workflow.reviewed_at,
            approved_by=workflow.approved_by,
            approved_at=workflow.approved_at,
            capabilities=MappingCapabilitiesResponse(
                can_review=workflow.capabilities.can_review,
                can_approve=workflow.capabilities.can_approve,
                additional_revisions_supported=(
                    workflow.capabilities.additional_revisions_supported
                ),
            ),
        ),
        proof=MappingSourceProofResponse(
            receipt_id=proof.receipt_id,
            content_sha256=proof.content_sha256,
            original_filename=proof.original_filename,
            size_bytes=proof.size_bytes,
            fingerprint_sha256=proof.fingerprint_sha256,
            header_assertion_count=proof.header_assertion_count,
            identifier_count=proof.identifier_count,
            inspection_row_count=proof.inspection_row_count,
            mapped_cell_count=proof.mapped_cell_count,
            official_values_created=proof.official_values_created,
            calculations_performed=proof.calculations_performed,
        ),
        preview=(
            None
            if preview is None
            else ApprovedMappingPreviewSummaryResponse(
                state=preview.state.value,
                source_inspection_date=preview.source_inspection_date,
                identifier_count=preview.identifier_count,
                inspection_row_count=preview.inspection_row_count,
                system_judgment_status=preview.system_judgment_status.value,
                official_values_created=preview.official_values_created,
                calculations_performed=preview.calculations_performed,
            )
        ),
    )


def _response(snapshot: MappingWorkspaceSnapshot) -> MappingWorkspaceResponse:
    receipt = snapshot.receipt
    scan = snapshot.scan
    template = snapshot.template
    preview = snapshot.preview
    return MappingWorkspaceResponse(
        state=snapshot.state.value,
        mode=snapshot.mode.value,
        status_label=snapshot.status_label,
        message=snapshot.message,
        supplier_scope=snapshot.supplier_scope,
        ai_state=snapshot.ai_state.value,
        draft_command_available=snapshot.draft_command_available,
        long_confirmation_available=snapshot.long_confirmation_available,
        official_values_created=snapshot.official_values_created,
        calculations_performed=snapshot.calculations_performed,
        receipt=MappingReceiptResponse(
            receipt_id=receipt.receipt_id,
            content_sha256=receipt.content_sha256,
            original_filename=receipt.original_filename,
            received_at=receipt.received_at,
            size_bytes=receipt.size_bytes,
            model_candidates=receipt.model_candidates,
            lot_candidates=receipt.lot_candidates,
        ),
        scan=MappingScanResponse(
            source_size_bytes=scan.source_size_bytes,
            sha256_before=scan.source_sha256_before,
            sha256_after=scan.source_sha256_after,
            sheet_count=len(scan.sheets),
            estimated_cells=scan.estimated_cells,
            external_link_count=scan.external_link_count,
            macro_handling=scan.macro_handling.value,
            sheets=tuple(
                MappingSheetResponse(
                    name=sheet.name,
                    kind=sheet.kind.value,
                    position=sheet.position,
                    visibility=sheet.visibility,
                    used_range=sheet.used_range,
                    estimated_cells=sheet.estimated_cells,
                    merged_ranges=sheet.merged_ranges,
                    hidden_row_ranges=tuple(
                        IndexRangeResponse(start=value.start, end=value.end)
                        for value in sheet.hidden_row_ranges
                    ),
                    hidden_column_ranges=tuple(
                        IndexRangeResponse(start=value.start, end=value.end)
                        for value in sheet.hidden_column_ranges
                    ),
                    protected=sheet.protection.enabled,
                    protected_actions=sheet.protection.protected_actions,
                    formula_count=len(sheet.formula_cells),
                    issue_codes=tuple(issue.code for issue in sheet.issues),
                )
                for sheet in scan.sheets
            ),
            issues=_scan_issue_responses(scan.issues, scan.sheets),
        ),
        source_cells=SourceCellPageResponse(
            offset=snapshot.source_cells.offset,
            limit=snapshot.source_cells.limit,
            total=snapshot.source_cells.total,
            truncated=snapshot.source_cells.truncated,
            cells=tuple(_source_cell_response(cell) for cell in snapshot.source_cells.cells),
        ),
        issues=tuple(
            MappingIssueResponse(
                code=issue.code.value,
                message=issue.message,
                template_id=issue.template_id,
                template_revision=issue.template_revision,
                sheet_name=issue.sheet_name,
                coordinate=issue.coordinate,
                expected=issue.expected,
                observed=issue.observed,
            )
            for issue in snapshot.issues
        ),
        template=(
            None
            if template is None
            else ApprovedTemplateResponse(
                history_id=template.history_id,
                revision_id=template.revision_id,
                template_id=template.template_id,
                schema_version=template.schema_version,
                revision=template.revision,
                status=template.status,
                payload_sha256=template.payload_sha256,
                effective_from=template.effective_from,
                effective_to=template.effective_to,
                approved_by=template.approved_by,
                approved_at=template.approved_at,
                history_row_version=template.history_row_version,
                revision_row_version=template.revision_row_version,
            )
        ),
        preview=(
            None
            if preview is None
            else ApprovedPreviewResponse(
                source_inspection_date=preview.source_inspection_date,
                identifiers=tuple(
                    IdentifierPreviewResponse(
                        kind=identifier.kind.value,
                        evidence=_mapped_cell_response(identifier.evidence),
                    )
                    for identifier in preview.identifiers
                ),
                inspection_rows=tuple(
                    InspectionRowPreviewResponse(
                        row_key=row.row_key,
                        item=_mapped_cell_response(row.item),
                        method=_optional_mapped_cell_response(row.method),
                        instrument=_optional_mapped_cell_response(row.instrument),
                        specification=_optional_mapped_cell_response(row.specification),
                        tolerance=_optional_mapped_cell_response(row.tolerance),
                        minimum=_optional_mapped_cell_response(row.minimum),
                        maximum=_optional_mapped_cell_response(row.maximum),
                        section=_optional_mapped_cell_response(row.section),
                        category=_optional_mapped_cell_response(row.category),
                        unit=_optional_mapped_cell_response(row.unit),
                        measurement_point=_optional_mapped_cell_response(row.measurement_point),
                        measurement_location=_optional_mapped_cell_response(
                            row.measurement_location
                        ),
                        cavity=_optional_mapped_cell_response(row.cavity),
                        target=_optional_mapped_cell_response(row.target),
                        lsl=_optional_mapped_cell_response(row.lsl),
                        usl=_optional_mapped_cell_response(row.usl),
                        source_spec_revision=_optional_mapped_cell_response(
                            row.source_spec_revision
                        ),
                        samples=tuple(_mapped_cell_response(sample) for sample in row.samples),
                        supplier_result=_optional_mapped_cell_response(row.supplier_result),
                    )
                    for row in preview.inspection_rows
                ),
            )
        ),
    )


def _source_cell_response(cell: SourceCellReviewEvidence) -> SourceCellResponse:
    return SourceCellResponse(
        sheet_name=cell.sheet_name,
        sheet_position=cell.sheet_position,
        coordinate=cell.coordinate,
        raw_value=_tagged_response(cell.raw_value),
        cached_value=_tagged_response(cell.cached_value),
        formula_text=cell.formula_text,
        number_format=cell.number_format,
        data_type=cell.data_type,
        display_value=cell.display_value,
        display_value_status=cell.display_value_status,
    )


def _mapped_cell_response(cell: MappedCellEvidence) -> MappedCellResponse:
    return MappedCellResponse(
        sheet_name=cell.source.sheet_name,
        coordinate=cell.source.coordinate,
        raw_value=_tagged_response(tag_source_value(cell.raw_value)),
        cached_value=_tagged_response(tag_source_value(cell.cached_value)),
        formula_text=cell.formula_text,
        number_format=cell.number_format,
        data_type=cell.data_type,
        display_value=cell.display_value,
        display_value_status=cell.display_value_status.value,
        value_kind=cell.value_kind.value,
    )


def _optional_mapped_cell_response(
    cell: MappedCellEvidence | None,
) -> MappedCellResponse | None:
    return None if cell is None else _mapped_cell_response(cell)


def _tagged_response(value: TaggedSourceValue) -> TaggedSourceValueResponse:
    return TaggedSourceValueResponse(
        kind=value.kind.value,
        value=value.value,
        python_type=value.python_type,
    )


def _scan_issue_responses(
    workbook_issues: tuple[ScanIssue, ...],
    sheets: tuple[SheetScan, ...],
) -> tuple[ScanIssueResponse, ...]:
    all_issues = [*workbook_issues]
    for sheet in sheets:
        all_issues.extend(sheet.issues)
    unique: dict[tuple[str, str | None], ScanIssueResponse] = {}
    for issue in all_issues:
        location = _logical_location(issue.location)
        unique[(issue.code, location)] = ScanIssueResponse(
            code=issue.code,
            severity=issue.severity.value,
            message=_safe_scan_issue_message(issue.code),
            location=location,
        )
    return tuple(unique[key] for key in sorted(unique))


def _logical_location(location: SourceLocation) -> str | None:
    if location.kind == SourceLocationKind.WORKBOOK:
        return "workbook"
    if location.kind == SourceLocationKind.PACKAGE_PART:
        return f"part:{location.package_part}" if location.package_part else "part"
    if location.kind == SourceLocationKind.SHEET:
        return f"sheet:{location.sheet_name}" if location.sheet_name else "sheet"
    if location.kind in {SourceLocationKind.CELL, SourceLocationKind.RANGE}:
        if location.sheet_name and location.coordinate:
            return f"{location.sheet_name}!{location.coordinate}"
        return location.kind.value.lower()
    return None


def _safe_scan_issue_message(code: str) -> str:
    messages = {
        "DISPLAY_VALUE_NOT_RENDERED": "표시값은 원본 계산 결과로 확인해야 합니다.",
        "EXTERNAL_LINKS_PRESENT": "외부 연결이 있어 원본 계산 상태를 확인해야 합니다.",
        "VBA_NOT_LOADED_OR_EXECUTED": "매크로는 불러오거나 실행하지 않았습니다.",
        "CALCULATION_REFRESH_REQUIRED": "원본 프로그램에서 계산 새로 고침이 필요합니다.",
        "WORKBOOK_STRUCTURE_PROTECTED": "통합 문서 구조 보호가 확인되었습니다.",
        "SHEET_PROTECTION_OBSERVED": "시트 보호가 확인되었습니다.",
        "IMAGE_METADATA_ONLY": "이미지는 위치 정보만 보존했습니다.",
        "CHARTSHEET_STRUCTURE_ONLY": "차트 시트는 구조 정보만 확인했습니다.",
        "EXTERNAL_REFERENCE_FORMULA": "외부 참조 수식이 확인되었습니다.",
        "BROKEN_CELL_REFERENCE": "깨진 셀 참조가 확인되었습니다.",
        "FORMULA_CACHE_MISSING": "수식의 저장된 계산값이 없습니다.",
        "SOURCE_MUTATED_DURING_SCAN": "스캔 중 원본 식별자가 달라졌습니다.",
    }
    return messages.get(code, "통합 문서 스캔 근거를 확인해 주세요.")


def _raise_application_error(error: MappingWorkspaceError) -> Never:
    if isinstance(error, MappingWorkspaceValidationError):
        status_code = status.HTTP_400_BAD_REQUEST
    elif isinstance(error, MappingWorkspaceNotFoundError):
        status_code = status.HTTP_404_NOT_FOUND
    elif isinstance(error, MappingWorkspaceSourceError) and error.code in {
        "MAPPING_CATALOG_UNAVAILABLE",
        "MAPPING_SERVICE_UNAVAILABLE",
        "MAPPING_TEMPLATE_PROOF_MISSING",
    }:
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    else:
        status_code = status.HTTP_409_CONFLICT
    _raise_safe_error(
        status_code,
        code=error.code,
        message=error.safe_message,
        status_label=error.status_label,
    )


def _raise_registration_error(error: MappingRegistrationError) -> Never:
    if isinstance(error, MappingRegistrationValidationError):
        status_code = status.HTTP_400_BAD_REQUEST
    elif isinstance(error, MappingRegistrationNotFoundError):
        status_code = status.HTTP_404_NOT_FOUND
    elif isinstance(error, MappingRegistrationConflictError):
        status_code = status.HTTP_409_CONFLICT
    elif isinstance(error, MappingRegistrationUnavailableError):
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    else:
        status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    _raise_safe_error(
        status_code,
        code=error.code,
        message=error.safe_message,
        status_label=error.status_label,
    )


def _raise_safe_error(
    status_code: int,
    *,
    code: str,
    message: str,
    status_label: str,
) -> Never:
    raise HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message, "status_label": status_label},
    )


def _unexpected_http_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={
            "code": "MAPPING_API_FAILURE",
            "message": "매핑 검토 요청을 처리하지 못했습니다.",
            "status_label": "매핑 검토 오류",
        },
    )


def _unexpected_registration_http_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={
            "code": "MAPPING_REGISTRATION_API_FAILURE",
            "message": "매핑 등록 요청을 안전하게 처리하지 못했습니다.",
            "status_label": "매핑 등록 오류",
        },
    )
