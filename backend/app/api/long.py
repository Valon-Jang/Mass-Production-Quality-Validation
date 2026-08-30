"""Safe HTTP contract for pending Long candidates and explicit confirmation."""

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
from app.application.long_workflow import (
    ConfirmLongCandidateRequest,
    LongCandidateRequest,
    LongWorkflowConflictError,
    LongWorkflowError,
    LongWorkflowNotFoundError,
    LongWorkflowResult,
    LongWorkflowUnavailableError,
    LongWorkflowValidationError,
)
from app.application.mapping_workspace import TaggedSourceValue, tag_source_value
from app.domain.long_format import (
    CanonicalRowBinding,
    LongCandidateIssue,
    LongCandidateState,
    LongInspectionCandidate,
    LongRowState,
)
from app.domain.mapping import MappedCellEvidence
from app.infrastructure.long_format import LongJobStatus


class LongWorkflowPort(Protocol):
    def candidate(self, request: LongCandidateRequest) -> LongWorkflowResult: ...

    def confirm(self, request: ConfirmLongCandidateRequest) -> LongWorkflowResult: ...


class LongCandidateRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_key: str = Field(min_length=1, max_length=64)
    receipt_id: str = Field(min_length=1, max_length=128)
    content_sha256: str = Field(min_length=64, max_length=64)
    supplier_scope: str = Field(min_length=1, max_length=200)


class ConfirmLongCandidateRequestBody(LongCandidateRequestBody):
    model_config = ConfigDict(extra="forbid")

    candidate_digest: str = Field(min_length=64, max_length=64)
    confirmed: bool


class LongTaggedValueResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    value: str | int | float | bool | None
    python_type: str


class LongCellReferenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sheet_name: str
    coordinate: str


class LongCellEvidenceSummaryResponse(LongCellReferenceResponse):
    model_config = ConfigDict(extra="forbid")

    raw_value: LongTaggedValueResponse


class LongIdentifierResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    source: LongCellReferenceResponse
    raw_value: LongTaggedValueResponse


class LongCandidateIssueResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    scope: str
    message: str
    row_key: str | None
    sheet_name: str | None
    coordinate: str | None
    expected: str | None
    observed: str | None


class LongBindingProofResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    binding_revision: int
    canonical_model_key: str
    canonical_supplier_key: str
    canonical_model_part_key: str
    canonical_item_key: str
    measurement_mode: str
    sample_policy: str
    approved_by: str
    approved_at: datetime
    effective_from: date
    effective_to: date | None


class LongCandidateRowResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_key: str
    state: str
    status_label: str
    pending_data_status: str
    source: LongCellEvidenceSummaryResponse
    measurement_count: int
    measurement_cells: tuple[LongCellReferenceResponse, ...]
    binding: LongBindingProofResponse | None
    issues: tuple[LongCandidateIssueResponse, ...]


class LongReceiptProofResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    receipt_id: str
    content_sha256: str
    original_filename: str
    size_bytes: int


class LongMappingProofResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    history_id: str
    revision_id: str
    payload_sha256: str
    template_id: str
    schema_version: str
    revision: int
    approved_by: str
    approved_at: datetime
    effective_from: date
    effective_to: date | None
    source_inspection_date: date


class LongCandidateCapabilitiesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    can_confirm: bool
    confirm_requires_digest: bool
    auto_binding: bool
    idempotency_managed_by_server: bool


class LongCandidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: str
    status_label: str
    message: str
    candidate_digest: str
    project_key: str
    supplier_scope: str
    receipt: LongReceiptProofResponse
    mapping: LongMappingProofResponse
    binding_catalog_revision: str
    row_count: int
    loadable_row_count: int
    held_row_count: int
    identifiers: tuple[LongIdentifierResponse, ...]
    rows: tuple[LongCandidateRowResponse, ...]
    issues: tuple[LongCandidateIssueResponse, ...]
    capabilities: LongCandidateCapabilitiesResponse
    official_values_created: bool
    calculations_performed: bool
    auto_valid: bool
    ai_called: bool


class LongMaterializationCountsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lot_count: int
    result_count: int
    measurement_count: int
    held_result_count: int


class LongPersistenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_file_id: str
    ingestion_job_id: str
    status: str
    status_label: str
    row_version: int
    replayed: bool
    reused_job_id: str | None
    blocking_job_id: str | None
    counts: LongMaterializationCountsResponse
    pending_only: bool
    official_values_created: bool
    calculations_performed: bool
    auto_valid: bool


class LongWorkflowResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate: LongCandidateResponse
    persistence: LongPersistenceResponse | None


_SAFE_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    status.HTTP_400_BAD_REQUEST: {"model": SafeErrorResponse},
    status.HTTP_404_NOT_FOUND: {"model": SafeErrorResponse},
    status.HTTP_409_CONFLICT: {"model": SafeErrorResponse},
    status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": SafeErrorResponse},
    status.HTTP_503_SERVICE_UNAVAILABLE: {"model": SafeErrorResponse},
}


class _SafeLongValidationRoute(APIRoute):
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
                        "code": "INVALID_LONG_REQUEST",
                        "message": "Long 후보 요청 형식과 필수 입력값을 확인해 주세요.",
                        "status_label": "Long 요청 오류",
                    },
                ) from error

        return safe_handler


def create_long_router(service: LongWorkflowPort) -> APIRouter:
    """Create injected Long routes without opening storage or a DB at import time."""

    router = APIRouter(
        prefix="/api/v1/long",
        tags=["long"],
        route_class=_SafeLongValidationRoute,
    )

    @router.post(
        "/candidates",
        response_model=LongWorkflowResponse,
        responses=_SAFE_ERROR_RESPONSES,
    )
    async def build_candidate(body: LongCandidateRequestBody) -> LongWorkflowResponse:
        try:
            result = await run_in_threadpool(service.candidate, _candidate_request(body))
        except LongWorkflowError as error:
            _raise_application_error(error)
        except Exception as error:
            raise _unexpected_http_error() from error
        return _response(result)

    @router.post(
        "/confirmations",
        response_model=LongWorkflowResponse,
        responses=_SAFE_ERROR_RESPONSES,
    )
    async def confirm_candidate(
        body: ConfirmLongCandidateRequestBody,
    ) -> LongWorkflowResponse:
        try:
            result = await run_in_threadpool(service.confirm, _confirmation_request(body))
        except LongWorkflowError as error:
            _raise_application_error(error)
        except Exception as error:
            raise _unexpected_http_error() from error
        return _response(result)

    return router


def _candidate_request(body: LongCandidateRequestBody) -> LongCandidateRequest:
    return LongCandidateRequest(
        project_key=body.project_key,
        receipt_id=body.receipt_id,
        content_sha256=body.content_sha256,
        supplier_scope=body.supplier_scope,
    )


def _confirmation_request(body: ConfirmLongCandidateRequestBody) -> ConfirmLongCandidateRequest:
    return ConfirmLongCandidateRequest(
        project_key=body.project_key,
        receipt_id=body.receipt_id,
        content_sha256=body.content_sha256,
        supplier_scope=body.supplier_scope,
        candidate_digest=body.candidate_digest,
        confirmed=body.confirmed,
    )


def _response(result: LongWorkflowResult) -> LongWorkflowResponse:
    workflow = result.candidate
    candidate = workflow.candidate
    provenance = candidate.provenance
    mapping = workflow.mapping_proof
    return LongWorkflowResponse(
        candidate=LongCandidateResponse(
            state=candidate.state.value,
            status_label=_candidate_status_label(candidate.state),
            message=_candidate_message(candidate.state),
            candidate_digest=workflow.candidate_digest,
            project_key=provenance.receipt.project_key,
            supplier_scope=provenance.supplier_scope,
            receipt=LongReceiptProofResponse(
                receipt_id=provenance.receipt.receipt_id,
                content_sha256=provenance.receipt.content_sha256,
                original_filename=provenance.receipt.original_filename,
                size_bytes=provenance.receipt.size_bytes,
            ),
            mapping=LongMappingProofResponse(
                history_id=mapping.history_id,
                revision_id=mapping.revision_id,
                payload_sha256=mapping.payload_sha256,
                template_id=mapping.template_id,
                schema_version=mapping.schema_version,
                revision=mapping.revision,
                approved_by=mapping.approved_by,
                approved_at=mapping.approved_at,
                effective_from=mapping.effective_from,
                effective_to=mapping.effective_to,
                source_inspection_date=provenance.source_inspection_date,
            ),
            binding_catalog_revision=provenance.binding_catalog_revision,
            row_count=len(candidate.rows),
            loadable_row_count=len(candidate.loadable_rows),
            held_row_count=len(candidate.held_rows),
            identifiers=tuple(
                LongIdentifierResponse(
                    kind=identifier.kind.value,
                    source=_cell_reference(identifier.evidence),
                    raw_value=_tagged_response(tag_source_value(identifier.evidence.raw_value)),
                )
                for identifier in candidate.source_identifiers
            ),
            rows=tuple(_row_response(row) for row in candidate.rows),
            issues=tuple(_issue_response(issue) for issue in candidate.issues),
            capabilities=LongCandidateCapabilitiesResponse(
                can_confirm=workflow.can_confirm,
                confirm_requires_digest=True,
                auto_binding=False,
                idempotency_managed_by_server=True,
            ),
            official_values_created=workflow.official_values_created,
            calculations_performed=workflow.calculations_performed,
            auto_valid=workflow.auto_valid,
            ai_called=workflow.ai_called,
        ),
        persistence=(
            None
            if result.persistence is None
            else LongPersistenceResponse(
                source_file_id=result.persistence.source_file_id,
                ingestion_job_id=result.persistence.ingestion_job_id,
                status=result.persistence.status.value,
                status_label=_job_status_label(result.persistence.status),
                row_version=result.persistence.row_version,
                replayed=result.persistence.replayed,
                reused_job_id=result.persistence.reused_job_id,
                blocking_job_id=result.persistence.blocking_job_id,
                counts=LongMaterializationCountsResponse(
                    lot_count=result.persistence.counts.lot_count,
                    result_count=result.persistence.counts.result_count,
                    measurement_count=result.persistence.counts.measurement_count,
                    held_result_count=result.persistence.counts.held_result_count,
                ),
                pending_only=True,
                official_values_created=False,
                calculations_performed=False,
                auto_valid=False,
            )
        ),
    )


def _row_response(row: LongInspectionCandidate) -> LongCandidateRowResponse:
    return LongCandidateRowResponse(
        row_key=row.row_key,
        state=row.state.value,
        status_label=(
            "대기 적재 가능" if row.state == LongRowState.LOADABLE_PENDING else "항목 보류"
        ),
        pending_data_status=("PENDING" if row.state == LongRowState.LOADABLE_PENDING else "HELD"),
        source=LongCellEvidenceSummaryResponse(
            sheet_name=row.item.source.sheet_name,
            coordinate=row.item.source.coordinate,
            raw_value=_tagged_response(tag_source_value(row.item.raw_value)),
        ),
        measurement_count=len(row.measurements),
        measurement_cells=tuple(
            _cell_reference(measurement.evidence) for measurement in row.measurements
        ),
        binding=_binding_response(row.binding),
        issues=tuple(_issue_response(issue) for issue in row.issues),
    )


def _binding_response(binding: CanonicalRowBinding | None) -> LongBindingProofResponse | None:
    if binding is None:
        return None
    if binding.approved_by is None or binding.approved_at is None:
        raise ValueError("candidate binding proof is not approved")
    return LongBindingProofResponse(
        binding_revision=binding.binding_revision,
        canonical_model_key=binding.canonical_model_key,
        canonical_supplier_key=binding.canonical_supplier_key,
        canonical_model_part_key=binding.canonical_model_part_key,
        canonical_item_key=binding.canonical_item_key,
        measurement_mode=binding.measurement_mode.value,
        sample_policy=binding.sample_policy.value,
        approved_by=binding.approved_by,
        approved_at=binding.approved_at,
        effective_from=binding.effective_from,
        effective_to=binding.effective_to,
    )


def _cell_reference(evidence: MappedCellEvidence) -> LongCellReferenceResponse:
    return LongCellReferenceResponse(
        sheet_name=evidence.source.sheet_name,
        coordinate=evidence.source.coordinate,
    )


def _tagged_response(value: TaggedSourceValue) -> LongTaggedValueResponse:
    return LongTaggedValueResponse(
        kind=value.kind.value,
        value=value.value,
        python_type=value.python_type,
    )


def _issue_response(issue: LongCandidateIssue) -> LongCandidateIssueResponse:
    return LongCandidateIssueResponse(
        code=issue.code.value,
        scope=issue.scope.value,
        message=_safe_issue_message(issue.code.value),
        row_key=issue.row_key,
        sheet_name=issue.sheet_name,
        coordinate=issue.coordinate,
        expected=issue.expected,
        observed=issue.observed,
    )


def _candidate_status_label(value: LongCandidateState) -> str:
    return {
        LongCandidateState.LOAD_CANDIDATE_READY: "대기 적재 준비",
        LongCandidateState.PARTIAL_HOLD: "일부 항목 보류",
        LongCandidateState.LOAD_HELD: "전체 보류",
    }[value]


def _candidate_message(value: LongCandidateState) -> str:
    return {
        LongCandidateState.LOAD_CANDIDATE_READY: (
            "모든 항목의 원본·매핑·행 연결 근거를 확인했습니다. "
            "명시적 확인 후 PENDING으로 저장합니다."
        ),
        LongCandidateState.PARTIAL_HOLD: (
            "확인 가능한 항목은 PENDING, 나머지는 HELD 근거로 함께 보존할 수 있습니다."
        ),
        LongCandidateState.LOAD_HELD: (
            "구조 또는 행 연결 근거가 부족하여 후보 전체가 HELD 상태입니다."
        ),
    }[value]


def _job_status_label(value: LongJobStatus) -> str:
    return {
        LongJobStatus.PROCESSING: "처리 중",
        LongJobStatus.COMPLETED_PENDING: "대기 적재 완료",
        LongJobStatus.PARTIAL_HELD: "일부 보류 적재 완료",
        LongJobStatus.HELD: "보류 근거 저장",
        LongJobStatus.REUSED: "기존 적재 재사용",
        LongJobStatus.RECOVERY_REQUIRED: "복구 확인 필요",
        LongJobStatus.FAILED: "적재 실패",
    }[value]


def _safe_issue_message(code: str) -> str:
    return {
        "MODEL_IDENTIFIER_MISSING": "모델 원본 식별자가 필요합니다.",
        "LOT_IDENTIFIER_MISSING": "LOT 원본 식별자가 필요합니다.",
        "MODEL_CANDIDATE_CONFLICT": "모델 원본값과 접수 후보가 일치하지 않습니다.",
        "LOT_CANDIDATE_CONFLICT": "LOT 원본값과 접수 후보가 일치하지 않습니다.",
        "CANONICAL_ROW_BINDING_MISSING": "승인된 정확한 행 연결이 없습니다.",
        "CANONICAL_ROW_BINDING_AMBIGUOUS": "승인된 행 연결을 하나로 확정할 수 없습니다.",
        "CANONICAL_ROW_BINDING_NOT_EFFECTIVE": "검사일에 유효한 행 연결이 아닙니다.",
        "CANONICAL_ROW_BINDING_NOT_APPROVED": "행 연결이 아직 승인되지 않았습니다.",
        "SOURCE_MODEL_BINDING_CONFLICT": "원본 모델값이 승인된 행 연결과 다릅니다.",
        "MEASUREMENT_MODE_MISMATCH": "측정값 유형이 승인된 행 연결 방식과 다릅니다.",
        "FORMULA_SAMPLE_NOT_RAW": "수식 셀은 원본 측정값으로 적재할 수 없습니다.",
        "CALCULATION_REFRESH_REQUIRED": "원본 프로그램에서 계산값 갱신이 필요합니다.",
        "NONFINITE_NUMERIC_SAMPLE": "유한한 수치가 아닌 측정값입니다.",
        "INVALID_NUMERIC_SAMPLE": "원본 수치 측정값을 확인해 주세요.",
        "INVALID_QUALITATIVE_SAMPLE": "원본 정성 측정값을 확인해 주세요.",
        "UNSUPPORTED_SAMPLE_VALUE_KIND": "지원하지 않는 원본 측정값 유형입니다.",
    }.get(code, "Long 후보의 원본 또는 행 연결 근거를 확인해 주세요.")


def _raise_application_error(error: LongWorkflowError) -> Never:
    if isinstance(error, LongWorkflowValidationError):
        status_code = status.HTTP_400_BAD_REQUEST
    elif isinstance(error, LongWorkflowNotFoundError):
        status_code = status.HTTP_404_NOT_FOUND
    elif isinstance(error, LongWorkflowConflictError):
        status_code = status.HTTP_409_CONFLICT
    elif isinstance(error, LongWorkflowUnavailableError):
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    else:
        status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    raise HTTPException(
        status_code=status_code,
        detail={
            "code": error.code,
            "message": error.safe_message,
            "status_label": error.status_label,
        },
    )


def _unexpected_http_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={
            "code": "LONG_API_FAILURE",
            "message": "Long 후보 요청을 안전하게 처리하지 못했습니다.",
            "status_label": "Long 처리 오류",
        },
    )
