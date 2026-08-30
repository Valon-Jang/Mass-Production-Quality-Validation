"""HTTP contract for explicit atomic result replacement."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Any, Never, Protocol

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from app.api.intake import SafeErrorResponse
from app.application.result_replacement import (
    DecideResultReplacementCommand,
    ReplacementCandidateRequest,
    ResultReplacementAuthorizationError,
    ResultReplacementError,
    ResultReplacementIneligibleError,
    ResultReplacementMissingError,
    ResultReplacementService,
    ResultReplacementStaleError,
    ResultReplacementUnavailableError,
    ResultReplacementValidationError,
)
from app.domain.identity import LOCAL_OWNER, Actor
from app.domain.result_replacement import (
    REPLACEMENT_MEASUREMENT_PROOF_LIMIT,
    PersistedReplacementDecision,
    ReplacementCapabilities,
    ReplacementMeasurementProof,
    ResultReplacementCandidate,
)
from app.infrastructure.result_replacement import (
    ResultReplacementConflictError,
    ResultReplacementNotFoundError,
)


class ResultReplacementPort(Protocol):
    def candidate(self, request: ReplacementCandidateRequest) -> ResultReplacementCandidate: ...

    def decide(self, command: DecideResultReplacementCommand) -> PersistedReplacementDecision: ...

    def get(self, *, project_key: str, replacement_id: str) -> PersistedReplacementDecision: ...


class ReplacementCandidateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_key: str = Field(min_length=1, max_length=64)
    predecessor_result_id: str = Field(min_length=1, max_length=36)
    successor_result_id: str = Field(min_length=1, max_length=36)


class ReplacementDecisionBody(ReplacementCandidateBody):
    candidate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_predecessor_result_row_version: int = Field(ge=1)
    expected_successor_result_row_version: int = Field(ge=1)
    expected_predecessor_measurement_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_successor_measurement_set_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_predecessor_decision_transition_id: str = Field(min_length=1, max_length=36)
    expected_successor_data_review_candidate_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmed: bool
    reason: str = Field(min_length=1, max_length=2_000)


class ReplacementMeasurementProofResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    measurement_id: str
    sample_ordinal: int
    source_cell: str
    data_status: str
    row_version: int
    evidence_sha256: str


class ReplacementResultProofResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result_id: str
    source_file_id: str
    lot_id: str
    data_status: str
    row_version: int
    original_data_status_transition_id: str
    original_decision_candidate_sha256: str
    system_judgment: str | None
    measurement_count: int
    measurement_set_sha256: str
    returned_measurement_count: int
    measurements_has_more: bool
    measurements: tuple[ReplacementMeasurementProofResponse, ...]


class ReplacementSuccessorProofResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    result_id: str
    source_file_id: str
    lot_id: str
    data_status: str
    row_version: int
    data_review_state: str
    data_review_candidate_sha256: str
    proposed_system_judgment: str | None
    selected_master_history_id: str | None
    selected_master_revision_id: str | None
    selected_master_payload_sha256: str | None
    item_row_version: int | None
    measurement_count: int
    measurement_set_sha256: str
    returned_measurement_count: int
    measurements_has_more: bool
    measurements: tuple[ReplacementMeasurementProofResponse, ...]


class ReplacementIdentityProofResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    canonical_model_key: str
    canonical_model_part_key: str
    canonical_supplier_key: str
    canonical_item_key: str
    source_lot_text: str


class ReplacementDifferenceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    field: str
    predecessor_value: str | None
    successor_value: str | None


class ReplacementIssueResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


class ReplacementCapabilitiesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    explicit_admin_only: bool = True
    atomic_successor_valid: bool = True
    automatic_replacement: bool = False
    automatic_valid: bool = False
    calculations: bool = False
    ai_used: bool = False
    measurement_pairing: bool = False


class ReplacementCandidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_contract_version: str
    project_key: str
    predecessor: ReplacementResultProofResponse
    successor: ReplacementSuccessorProofResponse
    identity: ReplacementIdentityProofResponse
    differences: tuple[ReplacementDifferenceResponse, ...]
    issues: tuple[ReplacementIssueResponse, ...]
    can_replace: bool
    candidate_sha256: str
    capabilities: ReplacementCapabilitiesResponse


class ReplacementDecisionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    replacement_id: str
    project_key: str
    predecessor_result_id: str
    successor_result_id: str
    predecessor_status: str
    successor_status: str
    predecessor_result_row_version: int
    successor_result_row_version: int
    successor_data_status_transition_id: str
    predecessor_measurement_count: int
    successor_measurement_count: int
    candidate_sha256: str
    intent_sha256: str
    decided_by: str
    decided_at: datetime
    reason: str
    replayed: bool
    official_predecessor: bool
    official_successor: bool
    capabilities: ReplacementCapabilitiesResponse


_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    status.HTTP_400_BAD_REQUEST: {"model": SafeErrorResponse},
    status.HTTP_403_FORBIDDEN: {"model": SafeErrorResponse},
    status.HTTP_404_NOT_FOUND: {"model": SafeErrorResponse},
    status.HTTP_409_CONFLICT: {"model": SafeErrorResponse},
    status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": SafeErrorResponse},
    status.HTTP_503_SERVICE_UNAVAILABLE: {"model": SafeErrorResponse},
}


class _SafeReplacementValidationRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def safe_handler(request: Request) -> Response:
            try:
                return await original(request)
            except RequestValidationError as error:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "RESULT_REPLACEMENT_INVALID_REQUEST",
                        "message": "수정본 연결 요청의 대상, 확인값, 사유를 확인해 주세요.",
                        "status_label": "수정본 연결 요청 오류",
                    },
                ) from error

        return safe_handler


def create_result_replacement_router(
    service: ResultReplacementPort,
    *,
    trusted_actor: Actor = LOCAL_OWNER,
) -> APIRouter:
    """Create injected routes without opening a database at import time."""

    router = APIRouter(
        prefix="/api/v1/result-replacements",
        tags=["result-replacements"],
        route_class=_SafeReplacementValidationRoute,
    )

    @router.post(
        "/candidates",
        response_model=ReplacementCandidateResponse,
        responses=_ERROR_RESPONSES,
    )
    async def candidate(body: ReplacementCandidateBody) -> ReplacementCandidateResponse:
        try:
            value = await run_in_threadpool(
                service.candidate,
                ReplacementCandidateRequest(
                    body.project_key,
                    body.predecessor_result_id,
                    body.successor_result_id,
                ),
            )
        except (ResultReplacementError, ResultReplacementConflictError) as error:
            _raise_error(error)
        except ValueError as error:
            _raise_invalid(error)
        except Exception as error:
            raise _unexpected() from error
        return _candidate_response(value)

    @router.post(
        "/decisions",
        response_model=ReplacementDecisionResponse,
        responses=_ERROR_RESPONSES,
    )
    async def decide(body: ReplacementDecisionBody) -> ReplacementDecisionResponse:
        try:
            value = await run_in_threadpool(
                service.decide,
                DecideResultReplacementCommand(
                    project_key=body.project_key,
                    predecessor_result_id=body.predecessor_result_id,
                    successor_result_id=body.successor_result_id,
                    candidate_sha256=body.candidate_sha256,
                    expected_predecessor_result_row_version=(
                        body.expected_predecessor_result_row_version
                    ),
                    expected_successor_result_row_version=(
                        body.expected_successor_result_row_version
                    ),
                    expected_predecessor_measurement_set_sha256=(
                        body.expected_predecessor_measurement_set_sha256
                    ),
                    expected_successor_measurement_set_sha256=(
                        body.expected_successor_measurement_set_sha256
                    ),
                    expected_predecessor_decision_transition_id=(
                        body.expected_predecessor_decision_transition_id
                    ),
                    expected_successor_data_review_candidate_sha256=(
                        body.expected_successor_data_review_candidate_sha256
                    ),
                    confirmed=body.confirmed,
                    reason=body.reason,
                    actor=trusted_actor,
                ),
            )
        except (ResultReplacementError, ResultReplacementConflictError) as error:
            _raise_error(error)
        except ValueError as error:
            _raise_invalid(error)
        except Exception as error:
            raise _unexpected() from error
        return _decision_response(value)

    @router.get(
        "/{replacement_id}",
        response_model=ReplacementDecisionResponse,
        responses=_ERROR_RESPONSES,
    )
    async def get(replacement_id: str, project_key: str) -> ReplacementDecisionResponse:
        try:
            value = await run_in_threadpool(
                service.get,
                project_key=project_key,
                replacement_id=replacement_id,
            )
        except (ResultReplacementError, ResultReplacementConflictError) as error:
            _raise_error(error)
        except ValueError as error:
            _raise_invalid(error)
        except Exception as error:
            raise _unexpected() from error
        return _decision_response(value)

    return router


def _candidate_response(value: ResultReplacementCandidate) -> ReplacementCandidateResponse:
    return ReplacementCandidateResponse(
        candidate_contract_version=value.candidate_contract_version,
        project_key=value.project_key,
        predecessor=ReplacementResultProofResponse(
            result_id=value.predecessor.result_id,
            source_file_id=value.predecessor.source_file_id,
            lot_id=value.predecessor.lot_id,
            data_status=value.predecessor.data_status.value,
            row_version=value.predecessor.row_version,
            original_data_status_transition_id=(
                value.predecessor.original_data_status_transition_id
            ),
            original_decision_candidate_sha256=(
                value.predecessor.original_decision_candidate_sha256
            ),
            system_judgment=(
                value.predecessor.system_judgment.value
                if value.predecessor.system_judgment is not None
                else None
            ),
            measurement_count=value.predecessor.measurement_count,
            measurement_set_sha256=value.predecessor.measurement_set_sha256,
            returned_measurement_count=min(
                value.predecessor.measurement_count,
                REPLACEMENT_MEASUREMENT_PROOF_LIMIT,
            ),
            measurements_has_more=(
                value.predecessor.measurement_count > REPLACEMENT_MEASUREMENT_PROOF_LIMIT
            ),
            measurements=_measurement_responses(
                value.predecessor.measurements[:REPLACEMENT_MEASUREMENT_PROOF_LIMIT]
            ),
        ),
        successor=ReplacementSuccessorProofResponse(
            result_id=value.successor.result_id,
            source_file_id=value.successor.source_file_id,
            lot_id=value.successor.lot_id,
            data_status=value.successor.data_status.value,
            row_version=value.successor.row_version,
            data_review_state=value.successor.data_review_state.value,
            data_review_candidate_sha256=value.successor.data_review_candidate_sha256,
            proposed_system_judgment=(
                value.successor.proposed_system_judgment.value
                if value.successor.proposed_system_judgment is not None
                else None
            ),
            selected_master_history_id=value.successor.selected_master_history_id,
            selected_master_revision_id=value.successor.selected_master_revision_id,
            selected_master_payload_sha256=value.successor.selected_master_payload_sha256,
            item_row_version=value.successor.item_row_version,
            measurement_count=value.successor.measurement_count,
            measurement_set_sha256=value.successor.measurement_set_sha256,
            returned_measurement_count=min(
                value.successor.measurement_count,
                REPLACEMENT_MEASUREMENT_PROOF_LIMIT,
            ),
            measurements_has_more=(
                value.successor.measurement_count > REPLACEMENT_MEASUREMENT_PROOF_LIMIT
            ),
            measurements=_measurement_responses(
                value.successor.measurements[:REPLACEMENT_MEASUREMENT_PROOF_LIMIT]
            ),
        ),
        identity=ReplacementIdentityProofResponse(
            canonical_model_key=value.identity.canonical_model_key,
            canonical_model_part_key=value.identity.canonical_model_part_key,
            canonical_supplier_key=value.identity.canonical_supplier_key,
            canonical_item_key=value.identity.canonical_item_key,
            source_lot_text=value.identity.source_lot_text,
        ),
        differences=tuple(
            ReplacementDifferenceResponse(
                code=item.code.value,
                field=item.field,
                predecessor_value=item.predecessor_value,
                successor_value=item.successor_value,
            )
            for item in value.differences
        ),
        issues=tuple(
            ReplacementIssueResponse(code=item.code.value, message=item.message)
            for item in value.issues
        ),
        can_replace=value.can_replace,
        candidate_sha256=value.candidate_sha256,
        capabilities=_capabilities(value.capabilities),
    )


def _decision_response(value: PersistedReplacementDecision) -> ReplacementDecisionResponse:
    return ReplacementDecisionResponse(
        replacement_id=value.replacement_id,
        project_key=value.project_key,
        predecessor_result_id=value.predecessor_result_id,
        successor_result_id=value.successor_result_id,
        predecessor_status="REPLACED",
        successor_status="VALID",
        predecessor_result_row_version=value.predecessor_result_row_version,
        successor_result_row_version=value.successor_result_row_version,
        successor_data_status_transition_id=value.successor_data_status_transition_id,
        predecessor_measurement_count=value.predecessor_measurement_count,
        successor_measurement_count=value.successor_measurement_count,
        candidate_sha256=value.candidate_sha256,
        intent_sha256=value.intent_sha256,
        decided_by=value.decided_by,
        decided_at=value.decided_at,
        reason=value.reason,
        replayed=value.replayed,
        official_predecessor=False,
        official_successor=True,
        capabilities=_capabilities(value.capabilities),
    )


def _measurement_responses(
    values: tuple[ReplacementMeasurementProof, ...],
) -> tuple[ReplacementMeasurementProofResponse, ...]:
    return tuple(
        ReplacementMeasurementProofResponse(
            measurement_id=value.measurement_id,
            sample_ordinal=value.sample_ordinal,
            source_cell=value.source_cell,
            data_status=value.data_status.value,
            row_version=value.row_version,
            evidence_sha256=value.evidence_sha256,
        )
        for value in values
    )


def _capabilities(value: ReplacementCapabilities) -> ReplacementCapabilitiesResponse:
    return ReplacementCapabilitiesResponse(
        explicit_admin_only=value.explicit_admin_only,
        atomic_successor_valid=value.atomic_successor_valid,
        automatic_replacement=value.automatic_replacement,
        automatic_valid=value.automatic_valid,
        calculations=value.calculations,
        ai_used=value.ai_used,
        measurement_pairing=value.measurement_pairing,
    )


def _raise_error(error: Exception) -> Never:
    if isinstance(error, ResultReplacementValidationError):
        status_code = status.HTTP_400_BAD_REQUEST
        detail = (error.code, error.safe_message, error.status_label)
    elif isinstance(error, ResultReplacementAuthorizationError):
        status_code = status.HTTP_403_FORBIDDEN
        detail = (error.code, error.safe_message, error.status_label)
    elif isinstance(error, (ResultReplacementNotFoundError, ResultReplacementMissingError)):
        status_code = status.HTTP_404_NOT_FOUND
        detail = (
            "RESULT_REPLACEMENT_NOT_FOUND",
            "요청한 수정본 연결 대상을 찾을 수 없습니다.",
            "대상 없음",
        )
    elif isinstance(error, (ResultReplacementIneligibleError, ResultReplacementStaleError)):
        status_code = status.HTTP_409_CONFLICT
        detail = (error.code, error.safe_message, error.status_label)
    elif isinstance(error, ResultReplacementConflictError):
        status_code = status.HTTP_409_CONFLICT
        detail = (
            "RESULT_REPLACEMENT_CONFLICT",
            "이미 다른 수정본 연결 의도가 저장되어 있습니다.",
            "수정본 연결 충돌",
        )
    elif isinstance(error, ResultReplacementUnavailableError):
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        detail = (error.code, error.safe_message, error.status_label)
    elif isinstance(error, ResultReplacementError):
        status_code = status.HTTP_409_CONFLICT
        detail = (error.code, error.safe_message, error.status_label)
    else:
        raise _unexpected() from error
    raise HTTPException(
        status_code=status_code,
        detail={"code": detail[0], "message": detail[1], "status_label": detail[2]},
    )


def _raise_invalid(error: ValueError) -> Never:
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={
            "code": "RESULT_REPLACEMENT_INVALID_REQUEST",
            "message": "수정본 연결 요청의 대상, 확인값, 사유를 확인해 주세요.",
            "status_label": "수정본 연결 요청 오류",
        },
    ) from error


def _unexpected() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={
            "code": "RESULT_REPLACEMENT_API_FAILURE",
            "message": "수정본 연결 요청을 안전하게 처리하지 못했습니다.",
            "status_label": "수정본 연결 오류",
        },
    )


def default_result_replacement_service(database: Any) -> ResultReplacementService:
    """Narrow factory retained for explicit application wiring and tests."""

    return ResultReplacementService(database)
