"""Bounded single-process jobs for local manual workbook intake.

This application service stages upload bytes first and runs the existing
``ManualWorkbookIngestionService`` on one dedicated worker thread.  Public
snapshots contain Korean display text and logical workbook locations only;
filesystem paths and exception text never cross this boundary.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Lock, RLock, Thread
from typing import BinaryIO, Protocol
from uuid import uuid4

from app.application.manual_ingestion import (
    ManualIngestionIntegrityError,
    ManualIngestionOutcome,
    ManualIngestionRequest,
    ManualIngestionStatus,
    ManualIngestionUnexpectedScanError,
)
from app.domain.source_file import SourceFileReceipt
from app.domain.workbook_scan import (
    ScanIssue,
    ScanPolicy,
    SourceLocation,
    SourceLocationKind,
    WorkbookScan,
)
from app.infrastructure.file_store import (
    XLSM_MIME,
    XLSX_MIME,
    OriginalFileStoreError,
    SourceFileValidationError,
)

_UPLOAD_CHUNK_BYTES = 1024 * 1024
_PROJECT_KEY_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_FORBIDDEN_WINDOWS_FILENAME_CHARS = frozenset('<>:"/\\|?*')
_RESERVED_WINDOWS_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{value}" for value in range(1, 10)),
        *(f"LPT{value}" for value in range(1, 10)),
    }
)
_MIME_BY_SUFFIX = {".xlsx": XLSX_MIME, ".xlsm": XLSM_MIME}
_TERMINAL_STATUSES = frozenset(
    {
        "MAPPING_REQUIRED",
        "RAW_PRESERVED_SCAN_FAILED",
        "ERROR",
    }
)


class IntakeJobStatus(StrEnum):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    MAPPING_REQUIRED = "MAPPING_REQUIRED"
    RAW_PRESERVED_SCAN_FAILED = "RAW_PRESERVED_SCAN_FAILED"
    ERROR = "ERROR"


_STATUS_LABELS = {
    IntakeJobStatus.QUEUED: "접수 대기",
    IntakeJobStatus.PROCESSING: "처리 중",
    IntakeJobStatus.MAPPING_REQUIRED: "매핑 등록 필요",
    IntakeJobStatus.RAW_PRESERVED_SCAN_FAILED: "원본 보존 · 스캔 실패",
    IntakeJobStatus.ERROR: "접수 오류",
}
_STATUS_MESSAGES = {
    IntakeJobStatus.QUEUED: "파일 접수를 기다리고 있습니다.",
    IntakeJobStatus.PROCESSING: "원본을 보존하고 통합 문서 구조를 확인하고 있습니다.",
    IntakeJobStatus.MAPPING_REQUIRED: (
        "원본과 스캔 근거가 보존되었습니다. 매핑 등록이 필요합니다."
    ),
    IntakeJobStatus.RAW_PRESERVED_SCAN_FAILED: ("원본은 보존되었지만 스캔을 완료하지 못했습니다."),
    IntakeJobStatus.ERROR: "파일 접수를 완료하지 못했습니다.",
}


@dataclass(frozen=True, slots=True)
class IntakeIssue:
    code: str
    message: str
    location: str | None = None

    def __post_init__(self) -> None:
        _require_exact(self.code, "issue code")
        _require_exact(self.message, "issue message")
        if self.location is not None:
            _require_exact(self.location, "issue location")


@dataclass(frozen=True, slots=True)
class IntakeReceiptSnapshot:
    receipt_id: str
    content_sha256: str
    original_filename: str
    received_at: datetime
    size_bytes: int
    model_candidates: tuple[str, ...]
    lot_candidates: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class IntakeSheetSnapshot:
    name: str
    kind: str
    state: str
    used_range: str | None
    merged_ranges: tuple[str, ...]
    protected: bool
    issue_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class IntakeScanSnapshot:
    source_size_bytes: int
    source_sha256_before: str
    source_sha256_after: str
    sheet_count: int
    sheets: tuple[IntakeSheetSnapshot, ...]


@dataclass(frozen=True, slots=True)
class IntakeJobSnapshot:
    job_id: str
    project_key: str
    status: IntakeJobStatus
    status_label: str
    message: str
    created_at: datetime
    updated_at: datetime
    terminal: bool
    poll_after_ms: int | None
    receipt: IntakeReceiptSnapshot | None
    scan: IntakeScanSnapshot | None
    issues: tuple[IntakeIssue, ...]


@dataclass(frozen=True, slots=True)
class IntakeUploadRequest:
    project_key: str
    original_filename: str
    declared_mime_type: str
    source: BinaryIO
    model_hint: str | None = None
    lot_hint: str | None = None


class ManualWorkbookIngestionPort(Protocol):
    def ingest(self, request: ManualIngestionRequest) -> ManualIngestionOutcome: ...


class IntakeJobError(RuntimeError):
    """Safe application error with no internal path or nested exception text."""

    def __init__(self, code: str, message: str, status_label: str) -> None:
        self.code = code
        self.safe_message = message
        self.status_label = status_label
        super().__init__(code)


class IntakeJobValidationError(IntakeJobError):
    pass


class IntakeJobCapacityError(IntakeJobError):
    def __init__(self) -> None:
        super().__init__(
            "INTAKE_CAPACITY_REACHED",
            "현재 처리 가능한 접수 건수를 초과했습니다. 잠시 후 다시 시도해 주세요.",
            "접수 용량 초과",
        )


class IntakeJobNotFoundError(IntakeJobError):
    def __init__(self) -> None:
        super().__init__(
            "INTAKE_JOB_NOT_FOUND",
            "해당 프로젝트에서 접수 작업을 찾을 수 없습니다.",
            "접수 작업 없음",
        )


class IntakeJobUnavailableError(IntakeJobError):
    def __init__(self) -> None:
        super().__init__(
            "INTAKE_SERVICE_UNAVAILABLE",
            "파일 접수 서비스가 실행 중이 아닙니다.",
            "접수 서비스 중지",
        )


class IntakeJobShutdownError(RuntimeError):
    pass


@dataclass(slots=True)
class _IntakeJobRecord:
    job_id: str
    project_key: str
    original_filename: str
    declared_mime_type: str
    model_hint: str | None
    lot_hint: str | None
    staged_path: Path | None
    status: IntakeJobStatus
    created_at: datetime
    updated_at: datetime
    receipt: IntakeReceiptSnapshot | None = None
    scan: IntakeScanSnapshot | None = None
    issues: tuple[IntakeIssue, ...] = ()


class IntakeJobManager:
    """One bounded queue, one worker, and one process-local job registry."""

    def __init__(
        self,
        *,
        ingestion_service: ManualWorkbookIngestionPort,
        staging_root: Path,
        max_upload_bytes: int,
        queue_capacity: int,
        registry_capacity: int,
        scan_policy: ScanPolicy,
        shutdown_timeout_seconds: float = 30.0,
        poll_after_ms: int = 500,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if max_upload_bytes < 1:
            raise ValueError("max_upload_bytes must be positive")
        if queue_capacity < 1:
            raise ValueError("queue_capacity must be positive")
        if registry_capacity < queue_capacity + 1:
            raise ValueError("registry_capacity must exceed queue_capacity")
        if shutdown_timeout_seconds <= 0:
            raise ValueError("shutdown_timeout_seconds must be positive")
        if poll_after_ms < 100:
            raise ValueError("poll_after_ms must be at least 100")
        self._ingestion = ingestion_service
        self._staging_root = staging_root.resolve()
        self._max_upload_bytes = max_upload_bytes
        self._registry_capacity = registry_capacity
        self._scan_policy = scan_policy
        self._shutdown_timeout_seconds = shutdown_timeout_seconds
        self._poll_after_ms = poll_after_ms
        self._clock = clock or (lambda: datetime.now(UTC))
        self._queue: Queue[str | None] = Queue(maxsize=queue_capacity)
        self._records: dict[str, _IntakeJobRecord] = {}
        self._lock = RLock()
        self._submission_lock = Lock()
        self._worker: Thread | None = None
        self._started = False
        self._accepting = False

    def start(self) -> None:
        """Start the worker without creating any file or directory."""

        with self._submission_lock, self._lock:
            if self._started:
                return
            self._started = True
            self._accepting = True
            self._worker = Thread(
                target=self._worker_loop,
                name="mass-production-quality-validation-intake-worker",
                daemon=False,
            )
            self._worker.start()

    def submit(self, request: IntakeUploadRequest) -> IntakeJobSnapshot:
        project_key = _validate_project_key(request.project_key)
        filename, suffix = _validate_filename(request.original_filename)
        declared_mime_type = _validate_declared_mime(request.declared_mime_type, suffix)
        model_hint = _normalize_hint(request.model_hint, "model_hint")
        lot_hint = _normalize_hint(request.lot_hint, "lot_hint")

        with self._submission_lock:
            with self._lock:
                if not self._started or not self._accepting:
                    raise IntakeJobUnavailableError
                self._evict_terminal_records_locked()
                if len(self._records) >= self._registry_capacity or self._queue.full():
                    raise IntakeJobCapacityError
            job_id = uuid4().hex
            staged_path = self._stage_upload(
                job_id=job_id,
                filename=filename,
                source=request.source,
            )
            occurred_at = self._now()
            record = _IntakeJobRecord(
                job_id=job_id,
                project_key=project_key,
                original_filename=filename,
                declared_mime_type=declared_mime_type,
                model_hint=model_hint,
                lot_hint=lot_hint,
                staged_path=staged_path,
                status=IntakeJobStatus.QUEUED,
                created_at=occurred_at,
                updated_at=occurred_at,
            )
            with self._lock:
                self._records[job_id] = record
            try:
                self._queue.put_nowait(job_id)
            except Full as error:
                with self._lock:
                    self._records.pop(job_id, None)
                self._cleanup_staged_path(staged_path)
                raise IntakeJobCapacityError from error
        return self.get(job_id=job_id, project_key=project_key)

    def get(self, *, job_id: str, project_key: str) -> IntakeJobSnapshot:
        try:
            normalized_project = _validate_project_key(project_key)
        except IntakeJobValidationError as error:
            raise IntakeJobNotFoundError from error
        with self._lock:
            record = self._records.get(job_id)
            if record is None or record.project_key != normalized_project:
                raise IntakeJobNotFoundError
            return self._snapshot(record)

    def shutdown(self) -> None:
        """Reject new work, cancel queued work, finish active work, and clean staging."""

        with self._submission_lock:
            with self._lock:
                if not self._started:
                    return
                self._accepting = False
            cancelled_ids: list[str] = []
            while True:
                try:
                    queued = self._queue.get_nowait()
                except Empty:
                    break
                try:
                    if queued is not None:
                        cancelled_ids.append(queued)
                finally:
                    self._queue.task_done()
            for job_id in cancelled_ids:
                with suppress(OSError):
                    self._cancel_queued_job(job_id)
            self._queue.put_nowait(None)

        worker = self._worker
        if worker is not None:
            worker.join(timeout=self._shutdown_timeout_seconds)
            if worker.is_alive():
                raise IntakeJobShutdownError(
                    "intake worker did not finish within the configured shutdown bound"
                )

        cleanup_failed = False
        with self._lock:
            staged_records = tuple(
                (record.job_id, record.staged_path)
                for record in self._records.values()
                if record.staged_path is not None
            )
        for job_id, staged_path in staged_records:
            if staged_path is None:  # narrowed by the snapshot above
                continue
            try:
                self._cleanup_staged_path(staged_path)
            except OSError:
                cleanup_failed = True
            else:
                with self._lock:
                    record = self._records.get(job_id)
                    if record is not None and record.staged_path == staged_path:
                        record.staged_path = None
        with self._lock:
            self._worker = None
            self._started = False
        if cleanup_failed:
            raise IntakeJobShutdownError("one or more intake staging files could not be removed")

    def _worker_loop(self) -> None:
        while True:
            job_id = self._queue.get()
            try:
                if job_id is None:
                    return
                self._process(job_id)
            finally:
                self._queue.task_done()

    def _process(self, job_id: str) -> None:
        with self._lock:
            record = self._records.get(job_id)
            if record is None or record.status != IntakeJobStatus.QUEUED:
                return
            record.status = IntakeJobStatus.PROCESSING
            record.updated_at = self._now()
            staged_path = record.staged_path
            project_key = record.project_key
            declared_mime_type = record.declared_mime_type
            model_candidates = (record.model_hint,) if record.model_hint is not None else ()
            lot_candidates = (record.lot_hint,) if record.lot_hint is not None else ()
        if staged_path is None:
            self._set_error(job_id, "STAGING_FILE_MISSING")
            return
        try:
            outcome = self._ingestion.ingest(
                ManualIngestionRequest(
                    project_key=project_key,
                    source=staged_path,
                    declared_mime_type=declared_mime_type,
                    scan_policy=self._scan_policy,
                    model_candidates=model_candidates,
                    lot_candidates=lot_candidates,
                )
            )
            self._apply_outcome(job_id, outcome)
        except ManualIngestionUnexpectedScanError as error:
            self._set_error(
                job_id,
                "UNEXPECTED_SCAN_FAILURE",
                receipt=error.receipt,
            )
        except ManualIngestionIntegrityError as error:
            self._set_error(
                job_id,
                "SOURCE_HASH_MISMATCH",
                receipt=error.receipt,
            )
        except SourceFileValidationError as error:
            self._set_error(job_id, error.code)
        except OriginalFileStoreError:
            self._set_error(job_id, "ORIGINAL_FILE_STORE_FAILURE")
        except Exception:
            self._set_error(job_id, "INTAKE_UNEXPECTED_FAILURE")
        finally:
            try:
                self._cleanup_staged_path(staged_path)
            except OSError:
                self._set_error(job_id, "STAGING_CLEANUP_FAILED")
            else:
                with self._lock:
                    current = self._records.get(job_id)
                    if current is not None:
                        current.staged_path = None

    def _apply_outcome(self, job_id: str, outcome: ManualIngestionOutcome) -> None:
        receipt = _receipt_snapshot(outcome.receipt)
        if outcome.status == ManualIngestionStatus.STORED_AND_SCANNED:
            if outcome.scan is None:
                self._set_error(job_id, "SCAN_EVIDENCE_MISSING", receipt=outcome.receipt)
                return
            scan = _scan_snapshot(outcome.scan)
            issues = _scan_issues(outcome.scan)
            with self._lock:
                record = self._records.get(job_id)
                if record is None:
                    return
                record.status = IntakeJobStatus.MAPPING_REQUIRED
                record.updated_at = self._now()
                record.receipt = receipt
                record.scan = scan
                record.issues = issues
            return
        failure = outcome.scan_failure
        if failure is None:
            self._set_error(job_id, "SCAN_FAILURE_EVIDENCE_MISSING", receipt=outcome.receipt)
            return
        with self._lock:
            record = self._records.get(job_id)
            if record is None:
                return
            record.status = IntakeJobStatus.RAW_PRESERVED_SCAN_FAILED
            record.updated_at = self._now()
            record.receipt = receipt
            record.scan = None
            record.issues = (_safe_scan_issue(failure.issue),)

    def _set_error(
        self,
        job_id: str,
        code: str,
        *,
        receipt: SourceFileReceipt | None = None,
    ) -> None:
        with self._lock:
            record = self._records.get(job_id)
            if record is None:
                return
            record.status = IntakeJobStatus.ERROR
            record.updated_at = self._now()
            if receipt is not None:
                record.receipt = _receipt_snapshot(receipt)
            record.scan = None
            record.issues = (IntakeIssue(code=code, message=_safe_issue_message(code)),)

    def _cancel_queued_job(self, job_id: str) -> None:
        with self._lock:
            record = self._records.get(job_id)
            if record is None or record.status != IntakeJobStatus.QUEUED:
                return
            staged_path = record.staged_path
            record.status = IntakeJobStatus.ERROR
            record.updated_at = self._now()
            record.issues = (
                IntakeIssue(
                    code="INTAKE_SHUTDOWN",
                    message="프로그램 종료로 대기 중인 접수가 중단되었습니다.",
                ),
            )
        if staged_path is not None:
            self._cleanup_staged_path(staged_path)
        with self._lock:
            record = self._records.get(job_id)
            if record is not None:
                record.staged_path = None

    def _stage_upload(self, *, job_id: str, filename: str, source: BinaryIO) -> Path:
        job_directory = self._resolve_job_directory(job_id)
        staged_path = job_directory / filename
        total_bytes = 0
        try:
            job_directory.mkdir(parents=True, exist_ok=False)
            with staged_path.open("xb") as destination:
                while True:
                    chunk = source.read(_UPLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    if not isinstance(chunk, bytes):
                        raise IntakeJobValidationError(
                            "INVALID_UPLOAD_STREAM",
                            "업로드 파일을 이진 데이터로 읽을 수 없습니다.",
                            "파일 형식 오류",
                        )
                    total_bytes += len(chunk)
                    if total_bytes > self._max_upload_bytes:
                        raise IntakeJobValidationError(
                            "UPLOAD_TOO_LARGE",
                            "업로드 파일이 허용된 크기를 초과했습니다.",
                            "파일 크기 초과",
                        )
                    destination.write(chunk)
                destination.flush()
                os.fsync(destination.fileno())
            if total_bytes == 0:
                raise IntakeJobValidationError(
                    "EMPTY_UPLOAD",
                    "빈 파일은 접수할 수 없습니다.",
                    "빈 파일",
                )
            return staged_path
        except IntakeJobValidationError:
            self._cleanup_staged_path(staged_path)
            raise
        except OSError as error:
            with suppress(OSError):
                self._cleanup_staged_path(staged_path)
            raise IntakeJobValidationError(
                "STAGING_WRITE_FAILED",
                "업로드 파일을 임시 보관할 수 없습니다.",
                "임시 보관 실패",
            ) from error
        except Exception as error:
            with suppress(OSError):
                self._cleanup_staged_path(staged_path)
            raise IntakeJobValidationError(
                "UPLOAD_STREAM_FAILED",
                "업로드 파일을 안전하게 읽을 수 없습니다.",
                "파일 읽기 실패",
            ) from error

    def _resolve_job_directory(self, job_id: str) -> Path:
        candidate = (self._staging_root / job_id).resolve()
        try:
            candidate.relative_to(self._staging_root)
        except ValueError as error:
            raise IntakeJobValidationError(
                "INVALID_JOB_ID",
                "안전한 접수 작업 경로를 만들 수 없습니다.",
                "접수 작업 오류",
            ) from error
        return candidate

    def _cleanup_staged_path(self, staged_path: Path) -> None:
        resolved = staged_path.resolve()
        try:
            resolved.relative_to(self._staging_root)
        except ValueError as error:
            raise IntakeJobShutdownError("staging cleanup target escaped its root") from error
        resolved.unlink(missing_ok=True)
        if resolved.parent.exists():
            resolved.parent.rmdir()

    def _evict_terminal_records_locked(self) -> None:
        while len(self._records) >= self._registry_capacity:
            candidates = sorted(
                (
                    record
                    for record in self._records.values()
                    if record.status.value in _TERMINAL_STATUSES and record.staged_path is None
                ),
                key=lambda value: (value.updated_at, value.created_at, value.job_id),
            )
            if not candidates:
                return
            self._records.pop(candidates[0].job_id, None)

    def _snapshot(self, record: _IntakeJobRecord) -> IntakeJobSnapshot:
        status = record.status
        if status.value in _TERMINAL_STATUSES and record.staged_path is not None:
            status = IntakeJobStatus.PROCESSING
        terminal = status.value in _TERMINAL_STATUSES
        return IntakeJobSnapshot(
            job_id=record.job_id,
            project_key=record.project_key,
            status=status,
            status_label=_STATUS_LABELS[status],
            message=_STATUS_MESSAGES[status],
            created_at=record.created_at,
            updated_at=record.updated_at,
            terminal=terminal,
            poll_after_ms=None if terminal else self._poll_after_ms,
            receipt=record.receipt,
            scan=record.scan,
            issues=record.issues,
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("intake clock must return a timezone-aware datetime")
        return value.astimezone(UTC)


def _receipt_snapshot(receipt: SourceFileReceipt) -> IntakeReceiptSnapshot:
    return IntakeReceiptSnapshot(
        receipt_id=receipt.receipt_id,
        content_sha256=receipt.content_sha256,
        original_filename=receipt.original_filename,
        received_at=receipt.received_at.astimezone(UTC),
        size_bytes=receipt.size_bytes,
        model_candidates=receipt.model_candidates,
        lot_candidates=receipt.lot_candidates,
    )


def _scan_snapshot(scan: WorkbookScan) -> IntakeScanSnapshot:
    sheets = tuple(
        IntakeSheetSnapshot(
            name=sheet.name,
            kind=sheet.kind.value,
            state=sheet.visibility,
            used_range=sheet.used_range,
            merged_ranges=sheet.merged_ranges,
            protected=sheet.protection.enabled,
            issue_codes=tuple(issue.code for issue in sheet.issues),
        )
        for sheet in scan.sheets
    )
    return IntakeScanSnapshot(
        source_size_bytes=scan.source_size_bytes,
        source_sha256_before=scan.source_sha256_before,
        source_sha256_after=scan.source_sha256_after,
        sheet_count=len(sheets),
        sheets=sheets,
    )


def _scan_issues(scan: WorkbookScan) -> tuple[IntakeIssue, ...]:
    issues = [*scan.issues]
    for sheet in scan.sheets:
        issues.extend(sheet.issues)
    unique: dict[tuple[str, str | None], IntakeIssue] = {}
    for issue in issues:
        safe = _safe_scan_issue(issue)
        unique[(safe.code, safe.location)] = safe
    return tuple(unique[key] for key in sorted(unique))


def _safe_scan_issue(issue: ScanIssue) -> IntakeIssue:
    return IntakeIssue(
        code=issue.code,
        message=_safe_issue_message(issue.code),
        location=_logical_location(issue.location),
    )


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


def _safe_issue_message(code: str) -> str:
    messages = {
        "UPLOAD_TOO_LARGE": "업로드 파일이 허용된 크기를 초과했습니다.",
        "INTAKE_SHUTDOWN": "프로그램 종료로 대기 중인 접수가 중단되었습니다.",
        "UNEXPECTED_SCAN_FAILURE": "원본은 보존되었지만 스캔 중 오류가 발생했습니다.",
        "SOURCE_HASH_MISMATCH": "원본 보존 근거와 스캔 해시가 일치하지 않습니다.",
        "STAGING_CLEANUP_FAILED": "임시 파일 정리가 완료되지 않았습니다.",
        "ORIGINAL_FILE_STORE_FAILURE": "원본 파일을 안전하게 보존하지 못했습니다.",
        "INTAKE_UNEXPECTED_FAILURE": "접수 처리 중 예상하지 못한 오류가 발생했습니다.",
    }
    return messages.get(code, "통합 문서 스캔 결과를 확인해 주세요.")


def _validate_project_key(value: str) -> str:
    if not isinstance(value, str) or _PROJECT_KEY_PATTERN.fullmatch(value) is None:
        raise IntakeJobValidationError(
            "INVALID_PROJECT_KEY",
            "프로젝트 키는 영문자, 숫자, 점, 밑줄, 하이픈으로 입력해 주세요.",
            "프로젝트 키 오류",
        )
    if value in {".", ".."}:
        raise IntakeJobValidationError(
            "INVALID_PROJECT_KEY",
            "상대 경로 형식의 프로젝트 키는 사용할 수 없습니다.",
            "프로젝트 키 오류",
        )
    return value


def _validate_filename(value: str) -> tuple[str, str]:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 255
        or value in {".", ".."}
        or value.endswith((".", " "))
        or any(character in _FORBIDDEN_WINDOWS_FILENAME_CHARS for character in value)
        or any(ord(character) < 32 for character in value)
    ):
        raise IntakeJobValidationError(
            "INVALID_FILENAME",
            "안전한 원본 파일 이름을 확인해 주세요.",
            "파일 이름 오류",
        )
    suffix = Path(value).suffix.lower()
    if suffix not in _MIME_BY_SUFFIX:
        raise IntakeJobValidationError(
            "UNSUPPORTED_EXTENSION",
            ".xlsx 또는 .xlsm 파일만 접수할 수 있습니다.",
            "지원하지 않는 파일",
        )
    stem_token = value[: -len(suffix)].split(".", maxsplit=1)[0].upper()
    if stem_token in _RESERVED_WINDOWS_NAMES:
        raise IntakeJobValidationError(
            "INVALID_FILENAME",
            "운영체제 예약 이름은 파일 이름으로 사용할 수 없습니다.",
            "파일 이름 오류",
        )
    return value, suffix


def _validate_declared_mime(value: str, suffix: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IntakeJobValidationError(
            "DECLARED_MIME_REQUIRED",
            "파일의 콘텐츠 형식 정보가 필요합니다.",
            "파일 형식 오류",
        )
    normalized = value.split(";", maxsplit=1)[0].strip().lower()
    if normalized != _MIME_BY_SUFFIX[suffix].lower():
        raise IntakeJobValidationError(
            "DECLARED_MIME_MISMATCH",
            "파일 확장자와 콘텐츠 형식이 일치하지 않습니다.",
            "파일 형식 오류",
        )
    return value.strip()


def _normalize_hint(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise IntakeJobValidationError(
            "INVALID_METADATA",
            "모델 및 LOT 힌트는 일반 텍스트로 입력해 주세요.",
            "힌트 입력 오류",
        )
    if not value.strip():
        return None
    normalized = value.strip()
    if len(normalized) > 200 or any(ord(character) < 32 for character in normalized):
        raise IntakeJobValidationError(
            "INVALID_METADATA",
            "모델 및 LOT 힌트는 200자 이내의 일반 텍스트로 입력해 주세요.",
            "힌트 입력 오류",
        )
    _require_exact(normalized, field_name)
    return normalized


def _require_exact(value: str, field_name: str) -> None:
    if not value or value != value.strip():
        raise ValueError(f"{field_name} must be an exact non-blank string")
