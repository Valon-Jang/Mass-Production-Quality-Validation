"""Bounded HTTP adapter for read-only historical OQC evidence comparison."""

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
from app.application.historical_comparison import (
    HISTORY_MAX_RESULTS_PER_SIDE,
    HistoricalCellProof,
    HistoricalComparison,
    HistoricalComparisonError,
    HistoricalComparisonRequest,
    HistoricalDateRange,
    HistoricalFilters,
    HistoricalReplacementChainProof,
    HistoricalReplacementLink,
    HistoricalResult,
    HistoricalSample,
    HistoricalSide,
)


class HistoricalComparisonPort(Protocol):
    def compare(self, request: HistoricalComparisonRequest) -> HistoricalComparison: ...


class HistoricalDateRangeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date_from: date
    date_to: date


class HistoricalFiltersBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canonical_model_key: str | None = Field(default=None, min_length=1, max_length=200)
    canonical_model_part_key: str | None = Field(default=None, min_length=1, max_length=200)
    canonical_item_key: str | None = Field(default=None, min_length=1, max_length=200)
    canonical_supplier_key: str | None = Field(default=None, min_length=1, max_length=200)
    mapping_revision_id: str | None = Field(default=None, min_length=1, max_length=200)


class HistoricalComparisonBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_key: str = Field(min_length=1, max_length=64)
    left: HistoricalDateRangeBody
    right: HistoricalDateRangeBody
    data_statuses: tuple[str, ...] = Field(min_length=1, max_length=6)
    filters: HistoricalFiltersBody = Field(default_factory=HistoricalFiltersBody)
    limit_per_side: int = Field(default=100, ge=1, le=HISTORY_MAX_RESULTS_PER_SIDE)


class HistoricalCellProofResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: str
    sheet_name: str
    coordinate: str
    raw_value: dict[str, Any]
    cached_value: dict[str, Any]
    formula_text: str | None
    number_format: str
    data_type: str
    display_value: str | None
    display_value_status: str
    value_kind: str
    evidence_sha256: str


class HistoricalSampleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    measurement_id: str
    ordinal: int
    row_version: int
    data_status: str
    source_sheet_name: str
    source_cell: str
    raw_value_tag: str
    raw_value_text: str | None
    raw_numeric_value: str | None
    raw_qualitative_value: str | None
    formula_flag: bool
    evidence_sha256: str


class HistoricalMappingProofResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision_id: str
    template_id: str
    revision: int
    payload_sha256: str
    schema_version: str
    applied_effective_from: date
    applied_effective_to: date | None
    current_declared_effective_from: date
    current_declared_effective_to: date | None
    current_resolved_effective_to: date | None


class HistoricalDecisionProofResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transition_id: str
    command_id: str
    evaluation_mode: str
    candidate_sha256: str
    decided_by: str
    decided_at: datetime
    reason: str
    from_status: str
    to_status: str
    before_result_row_version: int
    after_result_row_version: int
    intent_sha256: str
    decision_snapshot_sha256: str


class HistoricalMasterProofResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    history_id: str
    revision_id: str
    revision: int
    history_row_version: int
    revision_row_version: int
    payload_sha256: str
    declared_effective_from: date
    declared_effective_to: date | None
    resolved_effective_to: date | None


class HistoricalReplacementLinkResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    replacement_id: str
    predecessor_result_id: str
    successor_result_id: str
    predecessor_original_data_status_transition_id: str
    successor_data_status_transition_id: str
    predecessor_status_before: str
    predecessor_status_after: str
    successor_status_before: str
    successor_status_after: str
    predecessor_result_row_version_before: int
    predecessor_result_row_version_after: int
    successor_result_row_version_before: int
    successor_result_row_version_after: int
    predecessor_measurement_count: int
    predecessor_measurement_set_sha256: str
    successor_measurement_count: int
    successor_measurement_set_sha256: str
    candidate_sha256: str
    intent_sha256: str
    decided_by: str
    decided_at: datetime
    reason: str


class HistoricalReplacementChainProofResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    head_result_id: str
    tail_result_id: str | None
    current_result_id: str
    current_position: int | None
    returned_link_count: int
    has_more: bool
    links_sha256: str
    links: tuple[HistoricalReplacementLinkResponse, ...]


class HistoricalResultResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result_id: str
    lot_id: str
    source_file_id: str
    ingestion_job_id: str
    result_row_version: int
    inspection_date: date
    source_lot_text: str | None
    canonical_model_key: str | None
    canonical_model_part_key: str | None
    canonical_item_key: str | None
    canonical_supplier_key: str | None
    data_status: str
    receipt_id: str
    received_at: datetime
    original_filename: str
    content_sha256: str
    source_row_key: str
    source_sheet_name: str
    source_evidence_sha256: str
    source_fields: tuple[HistoricalCellProofResponse, ...]
    supplier_judgment: str | None
    system_judgment: str | None
    system_judgment_status: str
    spec_evaluation_status: str
    candidate_snapshot_sha256: str
    mapping: HistoricalMappingProofResponse
    binding_catalog_revision: str
    binding_fingerprint: str
    binding_revision: int | None
    binding_snapshot_sha256: str | None
    binding_proof: dict[str, Any] | None
    applied_master: HistoricalMasterProofResponse | None
    decision: HistoricalDecisionProofResponse | None
    replacement_chain: HistoricalReplacementChainProofResponse | None
    total_sample_count: int
    returned_sample_count: int
    samples_has_more: bool
    sample_set_sha256: str
    samples: tuple[HistoricalSampleResponse, ...]


class HistoricalComparisonSideResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date_from: date
    date_to: date
    total_matching: int
    returned_count: int
    has_more: bool
    total_sample_count: int
    returned_results_sample_count: int
    mapping_revision_ids: tuple[str, ...]
    results: tuple[HistoricalResultResponse, ...]


class HistoricalComparisonDeltaResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result_count_delta: int
    measurement_count_delta: int
    left_mapping_revision_ids: tuple[str, ...]
    right_mapping_revision_ids: tuple[str, ...]
    added_mapping_revision_ids: tuple[str, ...]
    removed_mapping_revision_ids: tuple[str, ...]


class HistoricalComparisonCapabilitiesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    official_values_created: bool = False
    calculations_performed: bool = False
    trend_analysis: bool = False
    thresholds_applied: bool = False
    current_master_rejudgment: bool = False
    ai_used: bool = False


class HistoricalComparisonResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_key: str
    data_statuses: tuple[str, ...]
    filters: HistoricalFiltersBody
    left: HistoricalComparisonSideResponse
    right: HistoricalComparisonSideResponse
    delta: HistoricalComparisonDeltaResponse
    capabilities: HistoricalComparisonCapabilitiesResponse


_SAFE_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    status.HTTP_400_BAD_REQUEST: {"model": SafeErrorResponse},
    status.HTTP_409_CONFLICT: {"model": SafeErrorResponse},
    status.HTTP_413_CONTENT_TOO_LARGE: {"model": SafeErrorResponse},
    status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": SafeErrorResponse},
    status.HTTP_503_SERVICE_UNAVAILABLE: {"model": SafeErrorResponse},
}


class _SafeHistoricalValidationRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def safe_handler(request: Request) -> Response:
            try:
                return await original(request)
            except RequestValidationError as error:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "INVALID_HISTORICAL_COMPARISON_REQUEST",
                        "message": "과거 근거 비교의 기간, 상태와 필터를 확인해 주세요.",
                        "status_label": "과거 비교 요청 오류",
                    },
                ) from error

        return safe_handler


def create_historical_comparison_router(service: HistoricalComparisonPort) -> APIRouter:
    """Create the on-demand, zero-write historical comparison route."""

    router = APIRouter(
        prefix="/api/v1/history",
        tags=["historical-evidence"],
        route_class=_SafeHistoricalValidationRoute,
    )

    @router.post(
        "/comparisons",
        response_model=HistoricalComparisonResponse,
        responses=_SAFE_ERROR_RESPONSES,
    )
    async def compare(body: HistoricalComparisonBody) -> HistoricalComparisonResponse:
        try:
            request = _request(body)
        except ValueError:
            _raise_safe(
                status.HTTP_400_BAD_REQUEST,
                "INVALID_HISTORICAL_COMPARISON_REQUEST",
                "과거 근거 비교의 기간, 상태와 필터를 확인해 주세요.",
                "과거 비교 요청 오류",
            )
        try:
            comparison = await run_in_threadpool(service.compare, request)
            return _response(comparison)
        except HistoricalComparisonError as error:
            _raise_application_error(error)
        except Exception as error:
            raise _unexpected_http_error() from error

    return router


def _request(body: HistoricalComparisonBody) -> HistoricalComparisonRequest:
    if len(set(body.data_statuses)) != len(body.data_statuses):
        raise ValueError("duplicate historical data status")
    return HistoricalComparisonRequest(
        project_key=body.project_key,
        left=HistoricalDateRange(body.left.date_from, body.left.date_to),
        right=HistoricalDateRange(body.right.date_from, body.right.date_to),
        data_statuses=tuple(sorted(body.data_statuses)),
        filters=HistoricalFilters(
            canonical_model_key=body.filters.canonical_model_key,
            canonical_model_part_key=body.filters.canonical_model_part_key,
            canonical_item_key=body.filters.canonical_item_key,
            canonical_supplier_key=body.filters.canonical_supplier_key,
            mapping_revision_id=body.filters.mapping_revision_id,
        ),
        limit_per_side=body.limit_per_side,
    )


def _response(comparison: HistoricalComparison) -> HistoricalComparisonResponse:
    if (
        comparison.official_values_created
        or comparison.calculations_performed
        or comparison.statistics_performed
        or comparison.ai_used
    ):
        raise ValueError("historical comparison crossed its read-only boundary")
    left_ids = comparison.left.mapping_revision_ids
    right_ids = comparison.right.mapping_revision_ids
    return HistoricalComparisonResponse(
        project_key=comparison.project_key,
        data_statuses=comparison.data_statuses,
        filters=HistoricalFiltersBody.model_validate(comparison.filters, from_attributes=True),
        left=_side_response(comparison.left),
        right=_side_response(comparison.right),
        delta=HistoricalComparisonDeltaResponse(
            result_count_delta=(
                comparison.right.total_result_count - comparison.left.total_result_count
            ),
            measurement_count_delta=(
                comparison.right.total_sample_count - comparison.left.total_sample_count
            ),
            left_mapping_revision_ids=left_ids,
            right_mapping_revision_ids=right_ids,
            added_mapping_revision_ids=tuple(sorted(set(right_ids) - set(left_ids))),
            removed_mapping_revision_ids=tuple(sorted(set(left_ids) - set(right_ids))),
        ),
        capabilities=HistoricalComparisonCapabilitiesResponse(),
    )


def _side_response(side: HistoricalSide) -> HistoricalComparisonSideResponse:
    return HistoricalComparisonSideResponse(
        date_from=side.date_range.date_from,
        date_to=side.date_range.date_to,
        total_matching=side.total_result_count,
        returned_count=side.returned_result_count,
        has_more=side.results_has_more,
        total_sample_count=side.total_sample_count,
        returned_results_sample_count=side.returned_results_sample_count,
        mapping_revision_ids=side.mapping_revision_ids,
        results=tuple(_result_response(item) for item in side.results),
    )


def _result_response(item: HistoricalResult) -> HistoricalResultResponse:
    return HistoricalResultResponse(
        result_id=item.result_id,
        lot_id=item.lot_id,
        source_file_id=item.source_file_id,
        ingestion_job_id=item.ingestion_job_id,
        result_row_version=item.result_row_version,
        inspection_date=item.inspection_date,
        source_lot_text=item.lot_text,
        canonical_model_key=item.canonical_model_key,
        canonical_model_part_key=item.canonical_model_part_key,
        canonical_item_key=item.canonical_item_key,
        canonical_supplier_key=item.canonical_supplier_key,
        data_status=item.data_status,
        receipt_id=item.receipt_id,
        received_at=item.received_at,
        original_filename=item.original_filename,
        content_sha256=item.content_sha256,
        source_row_key=item.source_row_key,
        source_sheet_name=item.source_sheet_name,
        source_evidence_sha256=item.source_evidence_sha256,
        source_fields=tuple(_cell_response(field) for field in item.source_fields),
        supplier_judgment=item.supplier_judgment_text,
        system_judgment=item.system_judgment,
        system_judgment_status=item.system_judgment_status,
        spec_evaluation_status=item.spec_evaluation_status,
        candidate_snapshot_sha256=item.candidate_snapshot_sha256,
        mapping=HistoricalMappingProofResponse.model_validate(item.mapping, from_attributes=True),
        binding_catalog_revision=item.binding_catalog_revision,
        binding_fingerprint=item.binding_fingerprint,
        binding_revision=item.binding_revision,
        binding_snapshot_sha256=item.binding_snapshot_sha256,
        binding_proof=item.binding_proof,
        applied_master=(
            HistoricalMasterProofResponse.model_validate(item.applied_master, from_attributes=True)
            if item.applied_master is not None
            else None
        ),
        decision=(
            HistoricalDecisionProofResponse.model_validate(item.decision, from_attributes=True)
            if item.decision is not None
            else None
        ),
        replacement_chain=(
            _replacement_chain_response(item.replacement_chain)
            if item.replacement_chain is not None
            else None
        ),
        total_sample_count=item.total_sample_count,
        returned_sample_count=item.returned_sample_count,
        samples_has_more=item.samples_has_more,
        sample_set_sha256=item.sample_set_sha256,
        samples=tuple(_sample_response(sample) for sample in item.samples),
    )


def _cell_response(item: HistoricalCellProof) -> HistoricalCellProofResponse:
    return HistoricalCellProofResponse.model_validate(item, from_attributes=True)


def _sample_response(item: HistoricalSample) -> HistoricalSampleResponse:
    return HistoricalSampleResponse(
        measurement_id=item.measurement_id,
        ordinal=item.sample_ordinal,
        row_version=item.row_version,
        data_status=item.data_status,
        source_sheet_name=item.source_sheet_name,
        source_cell=item.source_cell,
        raw_value_tag=item.raw_value_tag,
        raw_value_text=item.raw_value_text,
        raw_numeric_value=item.raw_numeric_value,
        raw_qualitative_value=item.raw_qualitative_value,
        formula_flag=item.formula_flag,
        evidence_sha256=item.evidence_sha256,
    )


def _replacement_chain_response(
    value: HistoricalReplacementChainProof,
) -> HistoricalReplacementChainProofResponse:
    return HistoricalReplacementChainProofResponse(
        head_result_id=value.head_result_id,
        tail_result_id=value.tail_result_id,
        current_result_id=value.current_result_id,
        current_position=value.current_position,
        returned_link_count=value.returned_link_count,
        has_more=value.has_more,
        links_sha256=value.links_sha256,
        links=tuple(_replacement_link_response(item) for item in value.links),
    )


def _replacement_link_response(
    value: HistoricalReplacementLink,
) -> HistoricalReplacementLinkResponse:
    return HistoricalReplacementLinkResponse(
        replacement_id=value.replacement_id,
        predecessor_result_id=value.predecessor_result_id,
        successor_result_id=value.successor_result_id,
        predecessor_original_data_status_transition_id=(
            value.predecessor_original_data_status_transition_id
        ),
        successor_data_status_transition_id=value.successor_data_status_transition_id,
        predecessor_status_before=value.predecessor_before_status,
        predecessor_status_after=value.predecessor_after_status,
        successor_status_before=value.successor_before_status,
        successor_status_after=value.successor_after_status,
        predecessor_result_row_version_before=(value.predecessor_before_result_row_version),
        predecessor_result_row_version_after=value.predecessor_after_result_row_version,
        successor_result_row_version_before=value.successor_before_result_row_version,
        successor_result_row_version_after=value.successor_after_result_row_version,
        predecessor_measurement_count=value.predecessor_measurement_count,
        predecessor_measurement_set_sha256=value.predecessor_measurement_set_sha256,
        successor_measurement_count=value.successor_measurement_count,
        successor_measurement_set_sha256=value.successor_measurement_set_sha256,
        candidate_sha256=value.candidate_sha256,
        intent_sha256=value.intent_sha256,
        decided_by=value.decided_by,
        decided_at=value.decided_at,
        reason=value.reason,
    )


def _raise_application_error(error: HistoricalComparisonError) -> Never:
    if "LIMIT_EXCEEDED" in error.code:
        status_code = status.HTTP_413_CONTENT_TOO_LARGE
    elif "DATABASE_UNAVAILABLE" in error.code or "SERVICE_UNAVAILABLE" in error.code:
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    else:
        status_code = status.HTTP_409_CONFLICT
    _raise_safe(status_code, error.code, error.safe_message, error.status_label)


def _raise_safe(status_code: int, code: str, message: str, status_label: str) -> Never:
    raise HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message, "status_label": status_label},
    )


def _unexpected_http_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={
            "code": "HISTORICAL_COMPARISON_API_FAILURE",
            "message": "과거 원본 근거 비교를 안전하게 처리하지 못했습니다.",
            "status_label": "과거 비교 오류",
        },
    )


__all__ = ["HistoricalComparisonPort", "create_historical_comparison_router"]
