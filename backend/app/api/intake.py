"""Safe asynchronous HTTP contract for local manual workbook intake."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Never, Protocol

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, ConfigDict
from starlette.concurrency import run_in_threadpool

from app.application.intake_jobs import (
    IntakeJobCapacityError,
    IntakeJobError,
    IntakeJobNotFoundError,
    IntakeJobSnapshot,
    IntakeJobStatus,
    IntakeJobUnavailableError,
    IntakeJobValidationError,
    IntakeUploadRequest,
)


class IntakeJobPort(Protocol):
    def submit(self, request: IntakeUploadRequest) -> IntakeJobSnapshot: ...

    def get(self, *, job_id: str, project_key: str) -> IntakeJobSnapshot: ...


class IntakeIssueResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    location: str | None


class IntakeReceiptResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    receipt_id: str
    content_sha256: str
    original_filename: str
    received_at: datetime
    size_bytes: int
    model_candidates: tuple[str, ...]
    lot_candidates: tuple[str, ...]


class IntakeSheetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    kind: str
    state: str
    used_range: str | None
    merged_ranges: tuple[str, ...]
    protected: bool
    issue_codes: tuple[str, ...]


class IntakeScanResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_size_bytes: int
    sha256_before: str
    sha256_after: str
    sheet_count: int
    sheets: tuple[IntakeSheetResponse, ...]


class IntakeJobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    project_key: str
    status: IntakeJobStatus
    status_label: str
    message: str
    created_at: datetime
    updated_at: datetime
    terminal: bool
    poll_after_ms: int | None
    receipt: IntakeReceiptResponse | None
    scan: IntakeScanResponse | None
    issues: tuple[IntakeIssueResponse, ...]


class SafeErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    status_label: str


class SafeErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detail: SafeErrorDetail


_SAFE_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    status.HTTP_400_BAD_REQUEST: {"model": SafeErrorResponse},
    status.HTTP_404_NOT_FOUND: {"model": SafeErrorResponse},
    status.HTTP_413_CONTENT_TOO_LARGE: {"model": SafeErrorResponse},
    status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: {"model": SafeErrorResponse},
    status.HTTP_429_TOO_MANY_REQUESTS: {"model": SafeErrorResponse},
    status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": SafeErrorResponse},
    status.HTTP_503_SERVICE_UNAVAILABLE: {"model": SafeErrorResponse},
}


def create_intake_router(manager: IntakeJobPort) -> APIRouter:
    """Create an explicitly injected router without starting workers or touching storage."""

    router = APIRouter(prefix="/api/v1/intake/jobs", tags=["intake"])

    @router.post(
        "",
        response_model=IntakeJobResponse,
        status_code=status.HTTP_202_ACCEPTED,
        responses=_SAFE_ERROR_RESPONSES,
    )
    async def create_job(
        project_key: Annotated[str | None, Form()] = None,
        workbook: Annotated[UploadFile | None, File()] = None,
        model_hint: Annotated[str | None, Form()] = None,
        lot_hint: Annotated[str | None, Form()] = None,
    ) -> IntakeJobResponse:
        if project_key is None:
            _raise_safe_error(
                status.HTTP_400_BAD_REQUEST,
                code="PROJECT_KEY_REQUIRED",
                message="프로젝트 키를 입력해 주세요.",
                status_label="프로젝트 키 필요",
            )
        if workbook is None:
            _raise_safe_error(
                status.HTTP_400_BAD_REQUEST,
                code="WORKBOOK_REQUIRED",
                message="접수할 Excel 파일을 선택해 주세요.",
                status_label="파일 선택 필요",
            )
        assert project_key is not None
        assert workbook is not None
        request = IntakeUploadRequest(
            project_key=project_key,
            original_filename=workbook.filename or "",
            declared_mime_type=workbook.content_type or "",
            source=workbook.file,
            model_hint=model_hint,
            lot_hint=lot_hint,
        )
        try:
            snapshot = await run_in_threadpool(manager.submit, request)
        except IntakeJobError as error:
            _raise_application_error(error)
        except Exception as error:
            raise _unexpected_http_error() from error
        finally:
            await workbook.close()
        return _response(snapshot)

    @router.get(
        "/{job_id}",
        response_model=IntakeJobResponse,
        responses=_SAFE_ERROR_RESPONSES,
    )
    def get_job(job_id: str, project_key: str | None = None) -> IntakeJobResponse:
        if project_key is None:
            _raise_safe_error(
                status.HTTP_400_BAD_REQUEST,
                code="PROJECT_KEY_REQUIRED",
                message="프로젝트 키를 입력해 주세요.",
                status_label="프로젝트 키 필요",
            )
        try:
            snapshot = manager.get(job_id=job_id, project_key=project_key)
        except IntakeJobError as error:
            _raise_application_error(error)
        except Exception as error:
            raise _unexpected_http_error() from error
        return _response(snapshot)

    return router


def _response(snapshot: IntakeJobSnapshot) -> IntakeJobResponse:
    receipt = snapshot.receipt
    scan = snapshot.scan
    return IntakeJobResponse(
        job_id=snapshot.job_id,
        project_key=snapshot.project_key,
        status=snapshot.status,
        status_label=snapshot.status_label,
        message=snapshot.message,
        created_at=snapshot.created_at,
        updated_at=snapshot.updated_at,
        terminal=snapshot.terminal,
        poll_after_ms=snapshot.poll_after_ms,
        receipt=(
            None
            if receipt is None
            else IntakeReceiptResponse(
                receipt_id=receipt.receipt_id,
                content_sha256=receipt.content_sha256,
                original_filename=receipt.original_filename,
                received_at=receipt.received_at,
                size_bytes=receipt.size_bytes,
                model_candidates=receipt.model_candidates,
                lot_candidates=receipt.lot_candidates,
            )
        ),
        scan=(
            None
            if scan is None
            else IntakeScanResponse(
                source_size_bytes=scan.source_size_bytes,
                sha256_before=scan.source_sha256_before,
                sha256_after=scan.source_sha256_after,
                sheet_count=scan.sheet_count,
                sheets=tuple(
                    IntakeSheetResponse(
                        name=sheet.name,
                        kind=sheet.kind,
                        state=sheet.state,
                        used_range=sheet.used_range,
                        merged_ranges=sheet.merged_ranges,
                        protected=sheet.protected,
                        issue_codes=sheet.issue_codes,
                    )
                    for sheet in scan.sheets
                ),
            )
        ),
        issues=tuple(
            IntakeIssueResponse(
                code=issue.code,
                message=issue.message,
                location=issue.location,
            )
            for issue in snapshot.issues
        ),
    )


def _raise_application_error(error: IntakeJobError) -> Never:
    if isinstance(error, IntakeJobCapacityError):
        status_code = status.HTTP_429_TOO_MANY_REQUESTS
    elif isinstance(error, IntakeJobNotFoundError):
        status_code = status.HTTP_404_NOT_FOUND
    elif isinstance(error, IntakeJobUnavailableError):
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    elif isinstance(error, IntakeJobValidationError):
        if error.code == "UPLOAD_TOO_LARGE":
            status_code = status.HTTP_413_CONTENT_TOO_LARGE
        elif error.code in {
            "DECLARED_MIME_MISMATCH",
            "DECLARED_MIME_REQUIRED",
            "UNSUPPORTED_EXTENSION",
        }:
            status_code = status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
        else:
            status_code = status.HTTP_400_BAD_REQUEST
    else:
        status_code = status.HTTP_400_BAD_REQUEST
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
            "code": "INTAKE_API_FAILURE",
            "message": "접수 요청을 처리하지 못했습니다.",
            "status_label": "접수 오류",
        },
    )
