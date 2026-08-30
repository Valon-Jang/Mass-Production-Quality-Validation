"""Asynchronous HTTP adapter for durable Bulk workbook staging."""

from __future__ import annotations

import hashlib
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Never, Protocol
from uuid import uuid4

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, ConfigDict
from starlette.concurrency import run_in_threadpool

from app.application.bulk_import import (
    BulkImportCapacityError,
    BulkImportConflictError,
    BulkImportError,
    BulkImportNotFoundError,
    BulkImportUnavailableError,
    BulkImportValidationError,
    BulkStagedFile,
    BulkSubmitRequest,
)
from app.domain.bulk_import import BulkBatchSnapshot, BulkBatchStatus, BulkEntryOutcome
from app.infrastructure.file_store.original import XLSM_MIME, XLSX_MIME

_CHUNK_BYTES = 1024 * 1024
_MIME_BY_EXTENSION = {".xlsx": XLSX_MIME, ".xlsm": XLSM_MIME}
_MAX_FILENAME_CHARS = 180
_MAX_STAGED_PATH_CHARS = 240


class BulkImportPort(Protocol):
    @property
    def limits(self) -> Any: ...

    def submit(self, request: BulkSubmitRequest) -> BulkBatchSnapshot: ...

    def get(self, *, project_key: str, batch_id: str) -> BulkBatchSnapshot: ...


class BulkLimitsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_files: int
    max_file_bytes: int
    max_batch_bytes: int


class BulkSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    total: int
    staged: int
    processing: int
    candidate_ready: int
    duplicate: int
    variation: int
    mapping_required: int
    scan_failed: int
    identifier_hold: int
    binding_hold: int
    revision_review_required: int
    error: int


class BulkCapabilitiesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    durable_staging: bool
    approved_template_reuse: bool
    per_file_approval: bool
    finalize_available: bool
    auto_long: bool
    auto_valid: bool
    auto_replaced: bool
    auto_revision: bool
    ai_used: bool


class BulkIssueResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    category: str
    severity: str
    message: str
    location: str | None
    evidence_path: str | None
    baseline_entry_id: str | None
    expected_json: Any | None
    observed_json: Any | None


class BulkReceiptResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    receipt_id: str
    content_sha256: str
    original_filename: str
    received_at: datetime
    size_bytes: int


class BulkMappingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    template_id: str
    revision: int
    template_sha256: str
    effective_from: str
    effective_to: str | None
    history_row_version: int
    revision_row_version: int


class BulkCandidateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: str
    candidate_digest: str
    loadable_row_count: int
    held_row_count: int
    revision_identity_sha256: str
    revision_evidence_sha256: str


class BulkEntryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: str
    ordinal: int
    filename: str
    mime_type: str
    size_bytes: int
    upload_sha256: str
    status: str
    outcome: BulkEntryOutcome | None
    status_label: str
    message: str
    attempt_count: int
    row_version: int
    receipt: BulkReceiptResponse | None
    mapping: BulkMappingResponse | None
    candidate: BulkCandidateResponse | None
    duplicate_of_entry_id: str | None
    revision_baseline_entry_id: str | None
    issues: tuple[BulkIssueResponse, ...]


class BulkBatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    batch_id: str
    project_key: str
    supplier_scope: str
    idempotency_key: str
    status: BulkBatchStatus
    status_label: str
    message: str
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None
    terminal: bool
    poll_after_ms: int | None
    replayed: bool
    limits: BulkLimitsResponse
    summary: BulkSummaryResponse
    entries: tuple[BulkEntryResponse, ...]
    capabilities: BulkCapabilitiesResponse


class BulkSafeErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    status_label: str


class BulkSafeErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detail: BulkSafeErrorDetail


_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    status.HTTP_400_BAD_REQUEST: {"model": BulkSafeErrorResponse},
    status.HTTP_404_NOT_FOUND: {"model": BulkSafeErrorResponse},
    status.HTTP_409_CONFLICT: {"model": BulkSafeErrorResponse},
    status.HTTP_413_CONTENT_TOO_LARGE: {"model": BulkSafeErrorResponse},
    status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: {"model": BulkSafeErrorResponse},
    status.HTTP_429_TOO_MANY_REQUESTS: {"model": BulkSafeErrorResponse},
    status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": BulkSafeErrorResponse},
    status.HTTP_503_SERVICE_UNAVAILABLE: {"model": BulkSafeErrorResponse},
}


def create_bulk_router(manager: BulkImportPort, *, staging_root: Path) -> APIRouter:
    """Create the router without touching a filesystem or starting a worker."""

    router = APIRouter(prefix="/api/v1/bulk/batches", tags=["bulk"])
    resolved_staging_root = staging_root.resolve()

    @router.post(
        "",
        response_model=BulkBatchResponse,
        status_code=status.HTTP_202_ACCEPTED,
        responses=_ERROR_RESPONSES,
    )
    async def create_batch(
        project_key: Annotated[str | None, Form()] = None,
        supplier_scope: Annotated[str | None, Form()] = None,
        idempotency_key: Annotated[str | None, Form()] = None,
        workbooks: Annotated[list[UploadFile] | None, File()] = None,
    ) -> BulkBatchResponse:
        if project_key is None or supplier_scope is None or idempotency_key is None:
            _raise_safe(
                status.HTTP_400_BAD_REQUEST,
                "BULK_SCOPE_REQUIRED",
                "프로젝트, 공급사 범위, 재시도 키를 모두 입력해 주세요.",
                "범위 입력 필요",
            )
        if not workbooks:
            _raise_safe(
                status.HTTP_400_BAD_REQUEST,
                "BULK_WORKBOOKS_REQUIRED",
                "등록할 Excel 파일을 한 개 이상 선택해 주세요.",
                "파일 선택 필요",
            )
        assert project_key is not None
        assert supplier_scope is not None
        assert idempotency_key is not None
        assert workbooks is not None
        if len(workbooks) > manager.limits.max_files:
            _raise_safe(
                status.HTTP_413_CONTENT_TOO_LARGE,
                "BULK_FILE_COUNT_EXCEEDED",
                "한 번에 등록할 수 있는 파일 수를 초과했습니다.",
                "파일 수 초과",
            )
        submission = uuid4().hex
        staged: list[BulkStagedFile] = []
        total_bytes = 0
        ownership_transferred = False
        try:
            for ordinal, upload in enumerate(workbooks):
                filename, mime_type = _validate_upload(upload)
                relative = Path(submission) / str(ordinal) / filename
                destination = (resolved_staging_root / relative).resolve()
                _require_within(destination, resolved_staging_root)
                if len(str(destination)) > _MAX_STAGED_PATH_CHARS:
                    _raise_safe(
                        status.HTTP_400_BAD_REQUEST,
                        "BULK_STAGING_PATH_TOO_LONG",
                        "파일 이름이 설치 경로에 비해 너무 깁니다.",
                        "파일 경로 길이 초과",
                    )
                destination.parent.mkdir(parents=True, exist_ok=False)
                digest = hashlib.sha256()
                size_bytes = 0
                with destination.open("xb") as stream:
                    while chunk := await upload.read(_CHUNK_BYTES):
                        size_bytes += len(chunk)
                        total_bytes += len(chunk)
                        if size_bytes > manager.limits.max_file_bytes:
                            _raise_safe(
                                status.HTTP_413_CONTENT_TOO_LARGE,
                                "BULK_FILE_TOO_LARGE",
                                "파일 하나의 허용 크기를 초과했습니다.",
                                "파일 크기 초과",
                            )
                        if total_bytes > manager.limits.max_batch_bytes:
                            _raise_safe(
                                status.HTTP_413_CONTENT_TOO_LARGE,
                                "BULK_BATCH_TOO_LARGE",
                                "파일 묶음의 허용 크기를 초과했습니다.",
                                "묶음 크기 초과",
                            )
                        stream.write(chunk)
                        digest.update(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())
                if size_bytes == 0:
                    _raise_safe(
                        status.HTTP_400_BAD_REQUEST,
                        "BULK_EMPTY_FILE",
                        "빈 파일은 등록할 수 없습니다.",
                        "빈 파일",
                    )
                staged.append(
                    BulkStagedFile(
                        ordinal=ordinal,
                        filename=filename,
                        mime_type=mime_type,
                        size_bytes=size_bytes,
                        upload_sha256=digest.hexdigest(),
                        staged_relative_path=relative.as_posix(),
                    )
                )
            ownership_transferred = True
            snapshot = await run_in_threadpool(
                manager.submit,
                BulkSubmitRequest(
                    project_key=project_key,
                    supplier_scope=supplier_scope,
                    idempotency_key=idempotency_key,
                    files=tuple(staged),
                ),
            )
            return _response(snapshot)
        except BulkImportError as error:
            _raise_application_error(error)
        except HTTPException:
            raise
        except Exception as error:
            raise _unexpected() from error
        finally:
            for upload in workbooks:
                await upload.close()
            if not ownership_transferred:
                _cleanup_submission(resolved_staging_root, submission)

    @router.get("/{batch_id}", response_model=BulkBatchResponse, responses=_ERROR_RESPONSES)
    def get_batch(batch_id: str, project_key: str | None = None) -> BulkBatchResponse:
        if project_key is None:
            _raise_safe(
                status.HTTP_400_BAD_REQUEST,
                "PROJECT_KEY_REQUIRED",
                "프로젝트 키를 입력해 주세요.",
                "프로젝트 키 필요",
            )
        try:
            return _response(manager.get(project_key=project_key, batch_id=batch_id))
        except BulkImportError as error:
            _raise_application_error(error)
        except Exception as error:
            raise _unexpected() from error

    return router


def _response(snapshot: BulkBatchSnapshot) -> BulkBatchResponse:
    return BulkBatchResponse.model_validate(snapshot, from_attributes=True)


def _validate_upload(upload: UploadFile) -> tuple[str, str]:
    filename = upload.filename or ""
    if (
        not filename
        or filename in {".", ".."}
        or "/" in filename
        or "\\" in filename
        or "\x00" in filename
        or filename != filename.strip()
        or len(filename) > _MAX_FILENAME_CHARS
    ):
        _raise_safe(
            status.HTTP_400_BAD_REQUEST,
            "INVALID_BULK_FILENAME",
            "파일 이름을 확인해 주세요.",
            "파일 이름 오류",
        )
    extension = Path(filename).suffix.lower()
    expected_mime = _MIME_BY_EXTENSION.get(extension)
    if expected_mime is None:
        _raise_safe(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "UNSUPPORTED_BULK_EXTENSION",
            ".xlsx 또는 .xlsm 파일만 등록할 수 있습니다.",
            "지원하지 않는 파일",
        )
    mime_type = (upload.content_type or "").split(";", maxsplit=1)[0].strip().lower()
    if mime_type != expected_mime.lower():
        _raise_safe(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "BULK_MIME_MISMATCH",
            "파일 확장자와 형식 정보가 일치하지 않습니다.",
            "파일 형식 오류",
        )
    return filename, upload.content_type or expected_mime


def _require_within(candidate: Path, root: Path) -> None:
    try:
        candidate.relative_to(root)
    except ValueError:
        _raise_safe(
            status.HTTP_400_BAD_REQUEST,
            "INVALID_BULK_FILENAME",
            "파일 이름을 확인해 주세요.",
            "파일 이름 오류",
        )


def _cleanup_submission(root: Path, submission: str) -> None:
    target = (root / submission).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return
    if target.is_dir():
        shutil.rmtree(target)


def _raise_application_error(error: BulkImportError) -> Never:
    if isinstance(error, BulkImportCapacityError):
        status_code = status.HTTP_429_TOO_MANY_REQUESTS
    elif isinstance(error, BulkImportNotFoundError):
        status_code = status.HTTP_404_NOT_FOUND
    elif isinstance(error, BulkImportConflictError):
        status_code = status.HTTP_409_CONFLICT
    elif isinstance(error, BulkImportUnavailableError):
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    elif isinstance(error, BulkImportValidationError):
        if "TOO_LARGE" in error.code or "FILE_COUNT" in error.code:
            status_code = status.HTTP_413_CONTENT_TOO_LARGE
        elif "MIME" in error.code or "EXTENSION" in error.code:
            status_code = status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
        else:
            status_code = status.HTTP_400_BAD_REQUEST
    else:
        status_code = status.HTTP_400_BAD_REQUEST
    _raise_safe(status_code, error.code, error.safe_message, error.status_label)


def _raise_safe(status_code: int, code: str, message: str, status_label: str) -> Never:
    raise HTTPException(
        status_code=status_code,
        detail={"code": code, "message": message, "status_label": status_label},
    )


def _unexpected() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={
            "code": "BULK_API_FAILURE",
            "message": "일괄 등록 요청을 처리하지 못했습니다.",
            "status_label": "일괄 등록 오류",
        },
    )


__all__ = ["BulkImportPort", "create_bulk_router"]
