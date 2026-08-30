"""Safe HTTP contract for read-only review candidates and explicit decisions."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import date
from typing import Any, Literal, Never, Protocol

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from app.api.intake import SafeErrorResponse
from app.application.data_review_workflow import (
    DataReviewCandidateCas,
    DataReviewCandidateRequest,
    DataReviewExpectedMaster,
    DataReviewExpectedMeasurement,
    DataReviewTargetList,
    DataReviewTargetsRequest,
    DataReviewWorkflowConflictError,
    DataReviewWorkflowError,
    DataReviewWorkflowNotFoundError,
    DataReviewWorkflowUnavailableError,
    DataReviewWorkflowValidationError,
    DecideDataReviewRequest,
    candidate_cas,
)
from app.domain.data_review import (
    DataReviewCandidate,
    HistoricalMasterEvidence,
    ReviewCandidateState,
    ReviewedSample,
    ReviewIssueCode,
    SourceUnitEvidence,
)
from app.domain.long_format import LongDataStatus
from app.infrastructure.data_review import PersistedDataStatusDecision


class DataReviewWorkflowPort(Protocol):
    def targets(self, request: DataReviewTargetsRequest) -> DataReviewTargetList: ...

    def candidate(self, request: DataReviewCandidateRequest) -> DataReviewCandidate: ...

    def decide(self, request: DecideDataReviewRequest) -> PersistedDataStatusDecision: ...


class DataReviewTargetsRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_key: str = Field(min_length=1, max_length=200)
    ingestion_job_id: str = Field(min_length=1, max_length=128)


class DataReviewCandidateRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_key: str = Field(min_length=1, max_length=200)
    result_id: str = Field(min_length=1, max_length=128)


class DataReviewExpectedMeasurementModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_ordinal: int = Field(ge=1)
    measurement_id: str = Field(min_length=1, max_length=128)
    row_version: int = Field(ge=1)


class DataReviewExpectedMasterModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    history_id: str = Field(min_length=1, max_length=128)
    revision_id: str = Field(min_length=1, max_length=128)
    history_row_version: int = Field(ge=1)
    revision_row_version: int = Field(ge=1)
    payload_sha256: str = Field(min_length=64, max_length=64)


class DataReviewCasModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_result_row_version: int = Field(ge=1)
    expected_item_row_version: int | None = Field(default=None, ge=1)
    expected_measurement_versions: tuple[DataReviewExpectedMeasurementModel, ...]
    expected_master: DataReviewExpectedMasterModel | None


class DecideDataReviewRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_key: str = Field(min_length=1, max_length=200)
    result_id: str = Field(min_length=1, max_length=128)
    target_status: Literal["VALID", "SUSPECT", "EXCLUDED"]
    candidate_sha256: str = Field(min_length=64, max_length=64)
    cas: DataReviewCasModel
    reason: str = Field(min_length=1, max_length=2000)
    confirmed: bool


class DataReviewTargetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result_id: str
    source_row_key: str
    data_status: str
    row_version: int
    canonical_item_key: str | None
    lot_id: str
    lot_ordinal: int
    source_lot_text: str | None
    inspection_date: date | None
    reviewable: bool
    status_label: str


class DataReviewTargetListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_key: str
    ingestion_job_id: str
    job_status: str
    targets: tuple[DataReviewTargetResponse, ...]
    official_values_created: bool


class DataReviewResultEvidenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    source_file_id: str
    lot_id: str
    source_content_sha256: str
    inspection_date: date | None
    data_status: str
    current_system_judgment: str | None
    current_system_judgment_status: str
    current_spec_evaluation_status: str
    source_evidence_sha256: str
    binding_snapshot_sha256: str | None
    candidate_snapshot_sha256: str


class DataReviewItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canonical_item_key: str | None
    disposition: str | None
    measurement_mode: str | None


class DataReviewUnitResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sheet_name: str
    coordinate: str
    raw_value: str
    cell_evidence_sha256: str


class DataReviewMasterResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_key: str
    canonical_item_key: str
    history_id: str
    revision_id: str
    revision_number: int
    history_row_version: int
    revision_row_version: int
    payload_sha256: str
    declared_effective_from: date
    declared_effective_to: date | None
    resolved_effective_to: date | None
    target: str | None
    lsl: str | None
    usl: str | None
    unit: str
    external_spec_revision: str


class DataReviewSampleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    measurement_id: str
    sample_ordinal: int
    source_cell: str
    row_version: int
    evidence_sha256: str
    raw_value_json: str
    raw_numeric_value_json: str | None
    raw_qualitative_value: str | None
    formula_flag: bool
    numeric_value: str | None
    comparison: str


class DataReviewIssueResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


class DataReviewCapabilitiesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    can_decide: bool
    explicit_confirmation_required: bool
    trusted_local_admin: bool


class DataReviewCandidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: str
    status_label: str
    message: str
    candidate_sha256: str
    project_key: str
    result: DataReviewResultEvidenceResponse
    item: DataReviewItemResponse
    source_unit: DataReviewUnitResponse | None
    master_candidates: tuple[DataReviewMasterResponse, ...]
    selected_master: DataReviewMasterResponse | None
    samples: tuple[DataReviewSampleResponse, ...]
    issues: tuple[DataReviewIssueResponse, ...]
    proposed_system_judgment: str | None
    proposed_system_judgment_status: str
    proposed_spec_evaluation_status: str
    allowed_target_statuses: tuple[str, ...]
    cas: DataReviewCasModel
    capabilities: DataReviewCapabilitiesResponse
    official_values_created: bool
    unit_conversion_performed: bool
    ai_used: bool
    statistics_calculated: bool


class DataReviewCandidateEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate: DataReviewCandidateResponse


class PersistedDataReviewDecisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transition_id: str
    project_key: str
    result_id: str
    candidate_sha256: str
    intent_sha256: str
    target_status: str
    result_row_version: int
    measurement_count: int
    evaluation_mode: str
    system_judgment: str | None
    master: DataReviewMasterResponse | None
    replayed: bool
    auto_decision: bool
    ai_used: bool
    additional_calculation: bool


class DataReviewDecisionEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: PersistedDataReviewDecisionResponse


_SAFE_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    status.HTTP_400_BAD_REQUEST: {"model": SafeErrorResponse},
    status.HTTP_404_NOT_FOUND: {"model": SafeErrorResponse},
    status.HTTP_409_CONFLICT: {"model": SafeErrorResponse},
    status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": SafeErrorResponse},
    status.HTTP_503_SERVICE_UNAVAILABLE: {"model": SafeErrorResponse},
}


class _SafeDataReviewValidationRoute(APIRoute):
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
                        "code": "INVALID_DATA_REVIEW_REQUEST",
                        "message": "데이터상태 검토 요청 형식과 필수 입력값을 확인해 주세요.",
                        "status_label": "검토 요청 오류",
                    },
                ) from error

        return safe_handler


def create_data_review_router(service: DataReviewWorkflowPort) -> APIRouter:
    """Create injected routes without opening a default database at import time."""

    router = APIRouter(
        prefix="/api/v1/data-reviews",
        tags=["data-reviews"],
        route_class=_SafeDataReviewValidationRoute,
    )

    @router.post(
        "/targets",
        response_model=DataReviewTargetListResponse,
        responses=_SAFE_ERROR_RESPONSES,
    )
    async def list_targets(body: DataReviewTargetsRequestBody) -> DataReviewTargetListResponse:
        try:
            result = await run_in_threadpool(
                service.targets,
                DataReviewTargetsRequest(
                    project_key=body.project_key,
                    ingestion_job_id=body.ingestion_job_id,
                ),
            )
        except DataReviewWorkflowError as error:
            _raise_application_error(error)
        except Exception as error:
            raise _unexpected_http_error() from error
        return _targets_response(result)

    @router.post(
        "/candidates",
        response_model=DataReviewCandidateEnvelope,
        responses=_SAFE_ERROR_RESPONSES,
    )
    async def build_candidate(
        body: DataReviewCandidateRequestBody,
    ) -> DataReviewCandidateEnvelope:
        try:
            candidate = await run_in_threadpool(
                service.candidate,
                DataReviewCandidateRequest(
                    project_key=body.project_key,
                    result_id=body.result_id,
                ),
            )
        except DataReviewWorkflowError as error:
            _raise_application_error(error)
        except Exception as error:
            raise _unexpected_http_error() from error
        return DataReviewCandidateEnvelope(candidate=_candidate_response(candidate))

    @router.post(
        "/decisions",
        response_model=DataReviewDecisionEnvelope,
        responses=_SAFE_ERROR_RESPONSES,
    )
    async def decide(body: DecideDataReviewRequestBody) -> DataReviewDecisionEnvelope:
        try:
            decision = await run_in_threadpool(service.decide, _decision_request(body))
        except DataReviewWorkflowError as error:
            _raise_application_error(error)
        except Exception as error:
            raise _unexpected_http_error() from error
        return DataReviewDecisionEnvelope(decision=_decision_response(decision))

    return router


def _targets_response(value: DataReviewTargetList) -> DataReviewTargetListResponse:
    return DataReviewTargetListResponse(
        project_key=value.project_key,
        ingestion_job_id=value.ingestion_job_id,
        job_status=value.job_status,
        targets=tuple(
            DataReviewTargetResponse(
                result_id=target.result_id,
                source_row_key=target.source_row_key,
                data_status=target.data_status.value,
                row_version=target.row_version,
                canonical_item_key=target.canonical_item_key,
                lot_id=target.lot_id,
                lot_ordinal=target.lot_ordinal,
                source_lot_text=target.source_lot_text,
                inspection_date=target.inspection_date,
                reviewable=target.reviewable,
                status_label=_data_status_label(target.data_status),
            )
            for target in value.targets
        ),
        official_values_created=value.official_values_created,
    )


def _candidate_response(value: DataReviewCandidate) -> DataReviewCandidateResponse:
    basis = value.basis
    cas = candidate_cas(value)
    return DataReviewCandidateResponse(
        state=value.state.value,
        status_label=_candidate_status_label(value.state),
        message=_candidate_message(value.state),
        candidate_sha256=value.candidate_sha256,
        project_key=basis.project_key,
        result=DataReviewResultEvidenceResponse(
            id=basis.result_id,
            source_file_id=basis.source_file_id,
            lot_id=basis.lot_id,
            source_content_sha256=basis.source_content_sha256,
            inspection_date=basis.inspection_date,
            data_status=basis.data_status.value,
            current_system_judgment=basis.current_system_judgment,
            current_system_judgment_status=basis.current_system_judgment_status.value,
            current_spec_evaluation_status=basis.current_spec_evaluation_status.value,
            source_evidence_sha256=basis.source_evidence_sha256,
            binding_snapshot_sha256=basis.binding_snapshot_sha256,
            candidate_snapshot_sha256=basis.candidate_snapshot_sha256,
        ),
        item=DataReviewItemResponse(
            canonical_item_key=basis.canonical_item_key,
            disposition=(basis.item_disposition.value if basis.item_disposition else None),
            measurement_mode=(basis.measurement_mode.value if basis.measurement_mode else None),
        ),
        source_unit=_unit_response(basis.source_unit),
        master_candidates=tuple(_master_response(master) for master in basis.masters),
        selected_master=(
            _master_response(value.selected_master) if value.selected_master is not None else None
        ),
        samples=tuple(_sample_response(sample) for sample in value.samples),
        issues=tuple(
            DataReviewIssueResponse(code=issue.code.value, message=_issue_message(issue.code))
            for issue in value.issues
        ),
        proposed_system_judgment=(
            value.proposed_system_judgment.value
            if value.proposed_system_judgment is not None
            else None
        ),
        proposed_system_judgment_status=value.proposed_system_judgment_status.value,
        proposed_spec_evaluation_status=value.proposed_spec_evaluation_status.value,
        allowed_target_statuses=tuple(
            status_value.value for status_value in value.allowed_target_statuses
        ),
        cas=DataReviewCasModel(
            expected_result_row_version=cas.expected_result_row_version,
            expected_item_row_version=cas.expected_item_row_version,
            expected_measurement_versions=tuple(
                DataReviewExpectedMeasurementModel(
                    sample_ordinal=item.sample_ordinal,
                    measurement_id=item.measurement_id,
                    row_version=item.row_version,
                )
                for item in cas.expected_measurement_versions
            ),
            expected_master=(
                DataReviewExpectedMasterModel(
                    history_id=cas.expected_master.history_id,
                    revision_id=cas.expected_master.revision_id,
                    history_row_version=cas.expected_master.history_row_version,
                    revision_row_version=cas.expected_master.revision_row_version,
                    payload_sha256=cas.expected_master.payload_sha256,
                )
                if cas.expected_master is not None
                else None
            ),
        ),
        capabilities=DataReviewCapabilitiesResponse(
            can_decide=bool(value.allowed_target_statuses),
            explicit_confirmation_required=True,
            trusted_local_admin=True,
        ),
        official_values_created=value.official_values_created,
        unit_conversion_performed=value.unit_conversion_performed,
        ai_used=value.ai_used,
        statistics_calculated=value.statistics_calculated,
    )


def _decision_request(body: DecideDataReviewRequestBody) -> DecideDataReviewRequest:
    master = body.cas.expected_master
    return DecideDataReviewRequest(
        project_key=body.project_key,
        result_id=body.result_id,
        target_status=LongDataStatus(body.target_status),
        candidate_sha256=body.candidate_sha256,
        cas=DataReviewCandidateCas(
            expected_result_row_version=body.cas.expected_result_row_version,
            expected_item_row_version=body.cas.expected_item_row_version,
            expected_measurement_versions=tuple(
                DataReviewExpectedMeasurement(
                    sample_ordinal=value.sample_ordinal,
                    measurement_id=value.measurement_id,
                    row_version=value.row_version,
                )
                for value in body.cas.expected_measurement_versions
            ),
            expected_master=(
                DataReviewExpectedMaster(
                    history_id=master.history_id,
                    revision_id=master.revision_id,
                    history_row_version=master.history_row_version,
                    revision_row_version=master.revision_row_version,
                    payload_sha256=master.payload_sha256,
                )
                if master is not None
                else None
            ),
        ),
        reason=body.reason,
        confirmed=body.confirmed,
    )


def _decision_response(value: PersistedDataStatusDecision) -> PersistedDataReviewDecisionResponse:
    return PersistedDataReviewDecisionResponse(
        transition_id=value.transition_id,
        project_key=value.project_key,
        result_id=value.result_id,
        candidate_sha256=value.candidate_sha256,
        intent_sha256=value.intent_sha256,
        target_status=value.target_status.value,
        result_row_version=value.result_row_version,
        measurement_count=value.measurement_count,
        evaluation_mode=value.evaluation_mode.value,
        system_judgment=(value.system_judgment.value if value.system_judgment else None),
        master=_master_response(value.master) if value.master is not None else None,
        replayed=value.replayed,
        auto_decision=False,
        ai_used=False,
        additional_calculation=False,
    )


def _master_response(value: HistoricalMasterEvidence) -> DataReviewMasterResponse:
    return DataReviewMasterResponse(
        project_key=value.project_key,
        canonical_item_key=value.canonical_item_key,
        history_id=value.history_id,
        revision_id=value.revision_id,
        revision_number=value.revision_number,
        history_row_version=value.history_row_version,
        revision_row_version=value.revision_row_version,
        payload_sha256=value.payload_sha256,
        declared_effective_from=value.declared_effective_from,
        declared_effective_to=value.declared_effective_to,
        resolved_effective_to=value.resolved_effective_to,
        target=str(value.target) if value.target is not None else None,
        lsl=str(value.lsl) if value.lsl is not None else None,
        usl=str(value.usl) if value.usl is not None else None,
        unit=value.unit,
        external_spec_revision=value.external_spec_revision,
    )


def _unit_response(value: SourceUnitEvidence | None) -> DataReviewUnitResponse | None:
    if value is None:
        return None
    return DataReviewUnitResponse(
        sheet_name=value.sheet_name,
        coordinate=value.coordinate,
        raw_value=value.raw_value,
        cell_evidence_sha256=value.cell_evidence_sha256,
    )


def _sample_response(value: ReviewedSample) -> DataReviewSampleResponse:
    evidence = value.evidence
    return DataReviewSampleResponse(
        measurement_id=evidence.measurement_id,
        sample_ordinal=evidence.sample_ordinal,
        source_cell=evidence.source_cell,
        row_version=evidence.row_version,
        evidence_sha256=evidence.evidence_sha256,
        raw_value_json=evidence.raw_value_json,
        raw_numeric_value_json=evidence.raw_numeric_value_json,
        raw_qualitative_value=evidence.raw_qualitative_value,
        formula_flag=evidence.formula_flag,
        numeric_value=str(evidence.numeric_value) if evidence.numeric_value is not None else None,
        comparison=value.comparison.value,
    )


def _candidate_status_label(value: ReviewCandidateState) -> str:
    return {
        ReviewCandidateState.EVALUATED: "승인 Master 비교 완료",
        ReviewCandidateState.REVIEW_ONLY: "수동 검토 전용",
        ReviewCandidateState.INELIGIBLE: "결정 불가",
    }[value]


def _candidate_message(value: ReviewCandidateState) -> str:
    return {
        ReviewCandidateState.EVALUATED: (
            "검사일 기준 승인 Master와 원본 단위·수치 시료를 정확히 비교했습니다."
        ),
        ReviewCandidateState.REVIEW_ONLY: (
            "공식 비교 근거가 부족하여 SUSPECT 또는 EXCLUDED만 명시적으로 선택할 수 있습니다."
        ),
        ReviewCandidateState.INELIGIBLE: (
            "구조 또는 무결성 보류로 이 검사 결과의 데이터상태를 변경할 수 없습니다."
        ),
    }[value]


def _data_status_label(value: LongDataStatus) -> str:
    return {
        LongDataStatus.PENDING: "검토 대기",
        LongDataStatus.HELD: "구조 보류",
        LongDataStatus.VALID: "공식 사용 가능",
        LongDataStatus.SUSPECT: "의심 데이터",
        LongDataStatus.EXCLUDED: "명시적 제외",
        LongDataStatus.REPLACED: "대체됨",
    }[value]


def _issue_message(value: ReviewIssueCode) -> str:
    return {
        ReviewIssueCode.RESULT_NOT_PENDING: "이미 결정된 결과는 다시 결정할 수 없습니다.",
        ReviewIssueCode.RESULT_HELD: "HELD 결과는 데이터상태 결정 대상이 아닙니다.",
        ReviewIssueCode.RESULT_PROJECTION_NOT_EMPTY: "기존 결정 투영이 남아 있습니다.",
        ReviewIssueCode.MEASUREMENT_STATUS_MISMATCH: "결과와 측정값 상태가 일치하지 않습니다.",
        ReviewIssueCode.ITEM_NOT_MAPPED: "연결된 검사항목을 확인할 수 없습니다.",
        ReviewIssueCode.ITEM_CANDIDATE: "검사항목의 관리 여부 결정이 필요합니다.",
        ReviewIssueCode.SOURCE_EVIDENCE_INTEGRITY: "원본 파일 근거의 무결성을 확인할 수 없습니다.",
        ReviewIssueCode.BINDING_EVIDENCE_INTEGRITY: "행 연결 근거의 무결성을 확인할 수 없습니다.",
        ReviewIssueCode.CANDIDATE_EVIDENCE_INTEGRITY: (
            "Long 후보 근거의 무결성을 확인할 수 없습니다."
        ),
        ReviewIssueCode.MEASUREMENT_EVIDENCE_INTEGRITY: (
            "측정값 근거의 무결성을 확인할 수 없습니다."
        ),
        ReviewIssueCode.MASTER_NOT_FOUND: "검사일에 유효한 승인 Master가 없습니다.",
        ReviewIssueCode.MASTER_AMBIGUOUS: "검사일에 유효한 승인 Master가 여러 개입니다.",
        ReviewIssueCode.MASTER_EVIDENCE_INTEGRITY: "Master 근거의 무결성을 확인할 수 없습니다.",
        ReviewIssueCode.INSPECTION_DATE_MISSING: "Master 선택에 필요한 검사일이 없습니다.",
        ReviewIssueCode.UNIT_EVIDENCE_MISSING: "원본 단위 근거가 없습니다.",
        ReviewIssueCode.UNIT_EVIDENCE_NOT_EXACT_TEXT: "원본 단위가 정확한 문자열이 아닙니다.",
        ReviewIssueCode.UNIT_MISMATCH: "원본 단위와 Master 단위가 정확히 일치하지 않습니다.",
        ReviewIssueCode.NON_NUMERIC_MEASUREMENT: "수치 원본으로 평가할 수 없는 시료가 있습니다.",
        ReviewIssueCode.NONFINITE_MEASUREMENT: "유한 수치가 아닌 시료가 있습니다.",
        ReviewIssueCode.FORMULA_MEASUREMENT: "수식 시료는 공식 비교에 사용할 수 없습니다.",
        ReviewIssueCode.QUALITATIVE_REVIEW_REQUIRED: "정성 시료는 수동 검토가 필요합니다.",
        ReviewIssueCode.ZERO_MEASUREMENTS: "검토할 원본 시료가 없습니다.",
    }[value]


def _raise_application_error(error: DataReviewWorkflowError) -> Never:
    if isinstance(error, DataReviewWorkflowValidationError):
        status_code = status.HTTP_400_BAD_REQUEST
    elif isinstance(error, DataReviewWorkflowNotFoundError):
        status_code = status.HTTP_404_NOT_FOUND
    elif isinstance(error, DataReviewWorkflowConflictError):
        status_code = status.HTTP_409_CONFLICT
    elif isinstance(error, DataReviewWorkflowUnavailableError):
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
            "code": "DATA_REVIEW_API_FAILURE",
            "message": "데이터상태 검토 요청을 안전하게 처리하지 못했습니다.",
            "status_label": "데이터상태 검토 오류",
        },
    )
