"""Safe HTTP contract for explicit batch-wide Long materialization."""

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
from app.application.bulk_finalization import (
    BulkFinalizationConflictError,
    BulkFinalizationError,
    BulkFinalizationNotFoundError,
    BulkFinalizationUnavailableError,
    BulkFinalizationValidationError,
    SubmitBulkFinalizationRequest,
)
from app.domain.bulk_finalization import (
    BulkFinalizationCandidate,
    BulkFinalizationSnapshot,
)


class BulkFinalizationPort(Protocol):
    def candidate(self, *, project_key: str, batch_id: str) -> BulkFinalizationCandidate: ...

    def submit(self, request: SubmitBulkFinalizationRequest) -> BulkFinalizationSnapshot: ...

    def get(self, *, project_key: str, batch_id: str) -> BulkFinalizationSnapshot: ...


class BulkFinalizationCapabilitiesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_wide_only: bool = True
    async_processing: bool = True
    per_file_selection: bool = False
    auto_long: bool = False
    auto_valid: bool = False
    auto_replaced: bool = False
    calculations: bool = False
    ai_used: bool = False
    initial_database_gate_complete: bool = False


class BulkFinalizationEligibleEntryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: str
    ordinal: int
    filename: str
    bulk_row_version: int
    receipt_id: str
    content_sha256: str
    mapping_sha256: str
    long_candidate_digest: str
    prepared_checkpoint_sha256: str
    prepared_checkpoint_version: str
    prepared_checkpoint_bytes: int


class BulkFinalizationExcludedEntryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: str
    ordinal: int
    filename: str
    outcome: str
    status_code: str
    issues_sha256: str
    bulk_row_version: int
    size_bytes: int
    upload_sha256: str
    receipt_id: str | None
    content_sha256: str | None


class BulkFinalizationCandidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: str
    project_key: str
    supplier_scope: str
    batch_status: str
    batch_row_version: int
    finalization_digest: str
    can_finalize: bool
    eligible_count: int
    excluded_count: int
    eligible_entries: tuple[BulkFinalizationEligibleEntryResponse, ...]
    excluded_entries: tuple[BulkFinalizationExcludedEntryResponse, ...]
    capabilities: BulkFinalizationCapabilitiesResponse


class SubmitBulkFinalizationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_key: str = Field(min_length=1, max_length=64)
    finalization_digest: str = Field(min_length=64, max_length=64)
    confirmed: bool
    reason: str = Field(min_length=1, max_length=1000)


class BulkFinalizationEntryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: str
    bulk_entry_id: str
    ordinal: int
    status: str
    status_label: str
    attempt_count: int
    row_version: int
    long_source_file_id: str | None
    long_ingestion_job_id: str | None
    long_status: str | None
    long_row_version: int | None
    replayed: bool | None
    error_code: str | None


class BulkFinalizationSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int
    pending: int
    processing: int
    completed: int
    blocked: int


class BulkFinalizationSnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command_id: str
    batch_id: str
    project_key: str
    supplier_scope: str
    status: str
    status_label: str
    message: str
    finalization_digest: str
    reason: str
    row_version: int
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None
    terminal: bool
    poll_after_ms: int | None
    summary: BulkFinalizationSummaryResponse
    entries: tuple[BulkFinalizationEntryResponse, ...]
    capabilities: BulkFinalizationCapabilitiesResponse


_SAFE_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    status.HTTP_400_BAD_REQUEST: {"model": SafeErrorResponse},
    status.HTTP_404_NOT_FOUND: {"model": SafeErrorResponse},
    status.HTTP_409_CONFLICT: {"model": SafeErrorResponse},
    status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": SafeErrorResponse},
    status.HTTP_503_SERVICE_UNAVAILABLE: {"model": SafeErrorResponse},
}


class _SafeBulkFinalizationValidationRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def safe_handler(request: Request) -> Response:
            try:
                return await original(request)
            except RequestValidationError as error:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "INVALID_BULK_FINALIZATION_REQUEST",
                        "message": "일괄 반영 요청 형식과 필수 입력값을 확인해 주세요.",
                        "status_label": "일괄 반영 요청 오류",
                    },
                ) from error

        return safe_handler


def create_bulk_finalization_router(service: BulkFinalizationPort) -> APIRouter:
    """Create injected routes without opening a database at import time."""

    router = APIRouter(
        prefix="/api/v1/bulk/batches",
        tags=["bulk-finalization"],
        route_class=_SafeBulkFinalizationValidationRoute,
    )

    @router.get(
        "/{batch_id}/finalization-candidate",
        response_model=BulkFinalizationCandidateResponse,
        responses=_SAFE_ERROR_RESPONSES,
    )
    async def get_candidate(
        batch_id: str, project_key: str | None = None
    ) -> BulkFinalizationCandidateResponse:
        if project_key is None:
            _raise_safe(
                status.HTTP_400_BAD_REQUEST,
                "PROJECT_KEY_REQUIRED",
                "프로젝트 키를 입력해 주세요.",
                "프로젝트 키 필요",
            )
        try:
            candidate = await run_in_threadpool(
                service.candidate,
                project_key=project_key,
                batch_id=batch_id,
            )
        except BulkFinalizationError as error:
            _raise_application_error(error)
        except Exception as error:
            raise _unexpected_http_error() from error
        return _candidate_response(candidate)

    @router.post(
        "/{batch_id}/finalizations",
        response_model=BulkFinalizationSnapshotResponse,
        status_code=status.HTTP_202_ACCEPTED,
        responses=_SAFE_ERROR_RESPONSES,
    )
    async def submit_finalization(
        batch_id: str,
        body: SubmitBulkFinalizationBody,
    ) -> BulkFinalizationSnapshotResponse:
        try:
            snapshot = await run_in_threadpool(
                service.submit,
                SubmitBulkFinalizationRequest(
                    project_key=body.project_key,
                    batch_id=batch_id,
                    finalization_digest=body.finalization_digest,
                    confirmed=body.confirmed,
                    reason=body.reason,
                ),
            )
        except BulkFinalizationError as error:
            _raise_application_error(error)
        except Exception as error:
            raise _unexpected_http_error() from error
        return _snapshot_response(snapshot)

    @router.get(
        "/{batch_id}/finalizations",
        response_model=BulkFinalizationSnapshotResponse,
        responses=_SAFE_ERROR_RESPONSES,
    )
    async def get_finalization(
        batch_id: str, project_key: str | None = None
    ) -> BulkFinalizationSnapshotResponse:
        if project_key is None:
            _raise_safe(
                status.HTTP_400_BAD_REQUEST,
                "PROJECT_KEY_REQUIRED",
                "프로젝트 키를 입력해 주세요.",
                "프로젝트 키 필요",
            )
        try:
            snapshot = await run_in_threadpool(
                service.get,
                project_key=project_key,
                batch_id=batch_id,
            )
        except BulkFinalizationError as error:
            _raise_application_error(error)
        except Exception as error:
            raise _unexpected_http_error() from error
        return _snapshot_response(snapshot)

    return router


def _candidate_response(
    candidate: BulkFinalizationCandidate,
) -> BulkFinalizationCandidateResponse:
    return BulkFinalizationCandidateResponse(
        batch_id=candidate.batch_id,
        project_key=candidate.project_key,
        supplier_scope=candidate.supplier_scope,
        batch_status=candidate.batch_status,
        batch_row_version=candidate.batch_row_version,
        finalization_digest=candidate.finalization_digest,
        can_finalize=candidate.can_finalize,
        eligible_count=len(candidate.eligible_entries),
        excluded_count=len(candidate.excluded_entries),
        eligible_entries=tuple(
            BulkFinalizationEligibleEntryResponse.model_validate(item, from_attributes=True)
            for item in candidate.eligible_entries
        ),
        excluded_entries=tuple(
            BulkFinalizationExcludedEntryResponse.model_validate(item, from_attributes=True)
            for item in candidate.excluded_entries
        ),
        capabilities=BulkFinalizationCapabilitiesResponse(),
    )


def _snapshot_response(
    snapshot: BulkFinalizationSnapshot,
) -> BulkFinalizationSnapshotResponse:
    return BulkFinalizationSnapshotResponse(
        command_id=snapshot.command_id,
        batch_id=snapshot.batch_id,
        project_key=snapshot.project_key,
        supplier_scope=snapshot.supplier_scope,
        status=snapshot.status.value,
        status_label=snapshot.status_label,
        message=snapshot.message,
        finalization_digest=snapshot.finalization_digest,
        reason=snapshot.reason,
        row_version=snapshot.row_version,
        created_at=snapshot.created_at,
        updated_at=snapshot.updated_at,
        finished_at=snapshot.finished_at,
        terminal=snapshot.terminal,
        poll_after_ms=snapshot.poll_after_ms,
        summary=BulkFinalizationSummaryResponse.model_validate(
            snapshot.summary, from_attributes=True
        ),
        entries=tuple(
            BulkFinalizationEntryResponse.model_validate(item, from_attributes=True)
            for item in snapshot.entries
        ),
        capabilities=BulkFinalizationCapabilitiesResponse(),
    )


def _raise_application_error(error: BulkFinalizationError) -> Never:
    if isinstance(error, BulkFinalizationValidationError):
        status_code = status.HTTP_400_BAD_REQUEST
    elif isinstance(error, BulkFinalizationNotFoundError):
        status_code = status.HTTP_404_NOT_FOUND
    elif isinstance(error, BulkFinalizationConflictError):
        status_code = status.HTTP_409_CONFLICT
    elif isinstance(error, BulkFinalizationUnavailableError):
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    else:
        status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
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
            "code": "BULK_FINALIZATION_API_FAILURE",
            "message": "일괄 반영 요청을 안전하게 처리하지 못했습니다.",
            "status_label": "일괄 반영 오류",
        },
    )


__all__ = ["BulkFinalizationPort", "create_bulk_finalization_router"]
