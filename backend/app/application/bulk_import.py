"""Durable multi-file Bulk staging with read-only Mapping and Long review."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections.abc import Callable, Iterable
from contextlib import suppress
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from itertools import chain
from pathlib import Path
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
from time import monotonic
from typing import Any, Protocol
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError

from app.application.long_workflow import (
    LONG_UI_LOADER_VERSION,
    LONG_UI_SCAN_CONTRACT_VERSION,
    LongCandidateRequest,
    LongWorkflowError,
    LongWorkflowResult,
)
from app.application.manual_ingestion import (
    ManualIngestionIntegrityError,
    ManualIngestionRequest,
    ManualIngestionStatus,
    ManualIngestionUnexpectedScanError,
    ManualWorkbookIngestionService,
)
from app.application.mapping_workspace import (
    MappingWorkspaceError,
    MappingWorkspaceRequest,
    MappingWorkspaceSnapshot,
    MappingWorkspaceState,
)
from app.domain.bulk_import import (
    BulkBatchSnapshot,
    BulkBatchStatus,
    BulkCandidateProof,
    BulkCapabilities,
    BulkEntryOutcome,
    BulkEntrySnapshot,
    BulkEntryStatus,
    BulkIssue,
    BulkIssueCategory,
    BulkIssueSeverity,
    BulkLimits,
    BulkMappingProof,
    BulkReceiptProof,
    BulkSummary,
)
from app.domain.long_format import LongCandidateState
from app.domain.source_file import SourceFileReceipt
from app.domain.workbook_scan import ScanPolicy, WorkbookScan
from app.infrastructure.bulk_import import (
    BULK_PREPARED_CHECKPOINT_MAX_BYTES,
    BULK_PREPARED_CHECKPOINT_VERSION,
    BulkBatchRow,
    BulkEntryRow,
    BulkImportRepository,
)
from app.infrastructure.database import Database
from app.infrastructure.long_format import (
    canonical_json_sha256,
    serialize_long_candidate,
    serialize_workbook_scan,
)

_PROJECT_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_IDEMPOTENCY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{7,127}\Z")
_FINGERPRINT_CODES = {
    "FINGERPRINT_HEADER_MISMATCH",
    "FINGERPRINT_SHEET_MISMATCH",
    "FINGERPRINT_MERGE_MISMATCH",
    "FINGERPRINT_ROW_STRUCTURE_MISMATCH",
}
_MAX_VARIATION_ISSUES = 200
_MAX_TERMINAL_ISSUES = 200
_MAX_ISSUE_JSON_BYTES = 4096
_MAX_REVISION_EVIDENCE_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class BulkStagedFile:
    ordinal: int
    filename: str
    mime_type: str
    size_bytes: int
    upload_sha256: str
    staged_relative_path: str


@dataclass(frozen=True, slots=True)
class BulkSubmitRequest:
    project_key: str
    supplier_scope: str
    idempotency_key: str
    files: tuple[BulkStagedFile, ...]


class BulkMappingWorkspacePort(Protocol):
    def preview(self, request: MappingWorkspaceRequest) -> MappingWorkspaceSnapshot: ...

    def preview_scanned(
        self,
        request: MappingWorkspaceRequest,
        *,
        receipt: SourceFileReceipt,
        scan: WorkbookScan,
    ) -> MappingWorkspaceSnapshot: ...


class BulkLongWorkflowPort(Protocol):
    def candidate(self, request: LongCandidateRequest) -> LongWorkflowResult: ...

    def candidate_from_workspace(
        self,
        request: LongCandidateRequest,
        workspace: MappingWorkspaceSnapshot,
    ) -> LongWorkflowResult: ...


class BulkImportError(RuntimeError):
    def __init__(self, code: str, message: str, status_label: str) -> None:
        self.code = code
        self.safe_message = message
        self.status_label = status_label
        super().__init__(code)


class BulkImportValidationError(BulkImportError):
    pass


class BulkImportNotFoundError(BulkImportError):
    pass


class BulkImportConflictError(BulkImportError):
    pass


class BulkImportCapacityError(BulkImportError):
    pass


class BulkImportUnavailableError(BulkImportError):
    pass


class _RawReceiptLinkRetry(RuntimeError):
    pass


class BulkImportManager:
    """Single-process worker backed by durable SQLite queue rows.

    Only the queue/session registry is process local. Source receipts, states,
    evidence, and idempotency are persistent and rebuilt after restart.
    """

    def __init__(
        self,
        *,
        database: Database,
        ingestion_service: ManualWorkbookIngestionService,
        mapping_workspace: BulkMappingWorkspacePort,
        long_workflow: BulkLongWorkflowPort,
        staging_root: Path,
        max_files: int,
        max_file_bytes: int,
        max_batch_bytes: int,
        queue_capacity: int,
        scan_policy: ScanPolicy,
        repository: BulkImportRepository | None = None,
        clock: Callable[[], datetime] | None = None,
        receipt_link_retry_delay_seconds: float = 1.0,
    ) -> None:
        self._database = database
        self._ingestion = ingestion_service
        self._mapping = mapping_workspace
        self._long = long_workflow
        self._staging_root = staging_root.resolve()
        self._limits = BulkLimits(max_files, max_file_bytes, max_batch_bytes)
        if queue_capacity < 1:
            raise ValueError("queue_capacity must be positive")
        self._queue_capacity = queue_capacity
        self._scan_policy = scan_policy
        self._repository = repository or BulkImportRepository()
        self._clock = clock or (lambda: datetime.now(UTC))
        if receipt_link_retry_delay_seconds <= 0:
            raise ValueError("receipt_link_retry_delay_seconds must be positive")
        self._receipt_link_retry_delay_seconds = receipt_link_retry_delay_seconds
        self._queue: Queue[str | None] = Queue(maxsize=queue_capacity)
        self._stop = Event()
        self._thread: Thread | None = None
        self._lifecycle_lock = Lock()
        self._schedule_lock = Lock()
        self._scheduled_batch_ids: set[str] = set()
        self._deferred_batch_ids: dict[str, float] = {}

    @property
    def limits(self) -> BulkLimits:
        return self._limits

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._queue = Queue(maxsize=self._queue_capacity)
            with self._schedule_lock:
                self._scheduled_batch_ids.clear()
                self._deferred_batch_ids.clear()
            with self._database.session() as session, session.begin():
                now = self._now()
                session.execute(
                    update(BulkEntryRow)
                    .where(BulkEntryRow.status == BulkEntryStatus.PROCESSING.value)
                    .values(
                        status=BulkEntryStatus.STAGED.value,
                        status_code="BULK_RECOVERED",
                        message="중단된 처리를 안전하게 다시 시작합니다.",
                        updated_at=now,
                        row_version=BulkEntryRow.row_version + 1,
                    )
                )
                session.execute(
                    update(BulkBatchRow)
                    .where(BulkBatchRow.status == BulkBatchStatus.PROCESSING.value)
                    .values(
                        status=BulkBatchStatus.STAGED.value,
                        updated_at=now,
                        row_version=BulkBatchRow.row_version + 1,
                    )
                )
                recoverable = self._repository.recoverable_batch_ids(session)
                referenced_staging = tuple(
                    session.scalars(
                        select(BulkEntryRow.staged_relative_path).where(
                            BulkEntryRow.staged_relative_path.is_not(None)
                        )
                    )
                )
            self._cleanup_orphan_submissions(referenced_staging)
            self._thread = Thread(target=self._worker, name="dq-bulk-worker", daemon=True)
            self._thread.start()
            for batch_id in recoverable[: self._queue_capacity]:
                self._enqueue(batch_id)

    def shutdown(self) -> None:
        with self._lifecycle_lock:
            thread = self._thread
            if thread is None:
                return
            self._stop.set()
            with suppress(Full):
                self._queue.put_nowait(None)
        thread.join(timeout=30)
        with self._lifecycle_lock:
            if thread.is_alive():
                raise BulkImportUnavailableError(
                    "BULK_WORKER_SHUTDOWN_TIMEOUT",
                    "진행 중인 원본 검사를 안전하게 종료하지 못했습니다.",
                    "일괄 작업 종료 대기",
                )
            self._thread = None

    def submit(self, request: BulkSubmitRequest) -> BulkBatchSnapshot:
        try:
            _validate_submit(request, self._limits, self._staging_root)
            manifest = _manifest(request)
            manifest_sha256 = canonical_json_sha256(manifest)
            now = self._now()
        except Exception:
            self._cleanup_request_files(request.files)
            raise
        replayed = False
        batch_id: str
        try:
            with self._database.session() as session, session.begin():
                existing = self._repository.find_by_idempotency(
                    session,
                    project_key=request.project_key,
                    idempotency_key=request.idempotency_key,
                )
                if existing is not None:
                    if (
                        existing.manifest_sha256 != manifest_sha256
                        or existing.supplier_scope != request.supplier_scope
                    ):
                        raise BulkImportConflictError(
                            "BULK_IDEMPOTENCY_CONFLICT",
                            "같은 재시도 키에 다른 파일 묶음을 사용할 수 없습니다.",
                            "재시도 충돌",
                        )
                    batch_id = existing.id
                    replayed = True
                else:
                    active_count = session.scalar(
                        select(func.count())
                        .select_from(BulkBatchRow)
                        .where(BulkBatchRow.status.in_(("STAGED", "PROCESSING")))
                    )
                    if int(active_count or 0) >= self._queue_capacity:
                        raise BulkImportCapacityError(
                            "BULK_QUEUE_FULL",
                            "처리 대기 공간이 가득 찼습니다. 잠시 후 다시 시도해 주세요.",
                            "일괄 등록 대기",
                        )
                    batch = self._repository.create_batch(
                        session,
                        project_key=request.project_key,
                        supplier_scope=request.supplier_scope,
                        idempotency_key=request.idempotency_key,
                        manifest_sha256=manifest_sha256,
                        entries=tuple(
                            {
                                **asdict(item),
                                "reserved_receipt_id": uuid4().hex,
                                "reserved_received_at": now,
                            }
                            for item in request.files
                        ),
                        now=now,
                    )
                    batch_id = batch.id
        except BulkImportError:
            self._cleanup_request_files(request.files)
            raise
        except (IntegrityError, OperationalError):
            try:
                with self._database.session() as replay_session:
                    winner = self._repository.find_by_idempotency(
                        replay_session,
                        project_key=request.project_key,
                        idempotency_key=request.idempotency_key,
                    )
                    if winner is None:
                        raise BulkImportConflictError(
                            "BULK_SUBMISSION_CONFLICT",
                            "일괄 등록 요청이 다른 요청과 충돌했습니다.",
                            "등록 충돌",
                        )
                    if (
                        winner.manifest_sha256 != manifest_sha256
                        or winner.supplier_scope != request.supplier_scope
                    ):
                        raise BulkImportConflictError(
                            "BULK_IDEMPOTENCY_CONFLICT",
                            "같은 재시도 키에 다른 파일 묶음을 사용할 수 없습니다.",
                            "재시도 충돌",
                        )
                    batch_id = winner.id
                    replayed = True
            except BulkImportError:
                self._cleanup_request_files(request.files)
                raise
            except SQLAlchemyError as replay_error:
                self._cleanup_request_files(request.files)
                raise _unavailable("BULK_IDEMPOTENCY_REPLAY_UNAVAILABLE") from replay_error
        except SQLAlchemyError as error:
            self._cleanup_request_files(request.files)
            raise _unavailable("BULK_DATABASE_UNAVAILABLE") from error
        except Exception as error:
            self._cleanup_request_files(request.files)
            raise _unavailable("BULK_SUBMISSION_UNAVAILABLE") from error

        if replayed:
            for item in request.files:
                self._cleanup_relative(item.staged_relative_path)
        else:
            self._enqueue(batch_id)
        return self.get(project_key=request.project_key, batch_id=batch_id, replayed=replayed)

    def get(self, *, project_key: str, batch_id: str, replayed: bool = False) -> BulkBatchSnapshot:
        _validate_lookup(project_key, batch_id)
        try:
            with self._database.session() as session:
                batch = self._repository.get_batch(
                    session, project_key=project_key, batch_id=batch_id
                )
                if batch is None:
                    raise BulkImportNotFoundError(
                        "BULK_BATCH_NOT_FOUND",
                        "해당 프로젝트에서 일괄 등록 작업을 찾을 수 없습니다.",
                        "작업 없음",
                    )
                entries = self._repository.entries(
                    session, project_key=project_key, batch_id=batch_id
                )
                return self._snapshot(batch, entries, replayed=replayed)
        except BulkImportError:
            raise
        except (SQLAlchemyError, ValueError, TypeError) as error:
            raise _unavailable("BULK_SNAPSHOT_UNAVAILABLE") from error

    def _enqueue(self, batch_id: str) -> None:
        with self._schedule_lock:
            retry_after = self._deferred_batch_ids.get(batch_id)
            if retry_after is not None and retry_after > monotonic():
                return
            self._deferred_batch_ids.pop(batch_id, None)
            if batch_id in self._scheduled_batch_ids:
                return
            self._scheduled_batch_ids.add(batch_id)
        try:
            self._queue.put_nowait(batch_id)
        except Full:
            with self._schedule_lock:
                self._scheduled_batch_ids.discard(batch_id)
            # The row remains durable STAGED. A worker completion wakes the next row.
            return

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                batch_id = self._queue.get(timeout=0.25)
            except Empty:
                self._enqueue_next()
                continue
            if batch_id is None:
                self._queue.task_done()
                return
            try:
                self._process_batch(batch_id)
            except Exception:
                self._recover_batch_after_failure(batch_id)
            finally:
                self._queue.task_done()
                with self._schedule_lock:
                    self._scheduled_batch_ids.discard(batch_id)
                self._enqueue_next()

    def _enqueue_next(self) -> None:
        if self._stop.is_set() or self._queue.full():
            return
        try:
            with self._database.session() as session:
                ids = self._repository.recoverable_batch_ids(session)
            with self._schedule_lock:
                now = monotonic()
                next_id = next(
                    (item for item in ids if self._deferred_batch_ids.get(item, 0.0) <= now),
                    None,
                )
            if next_id is not None:
                self._enqueue(next_id)
        except SQLAlchemyError:
            return

    def _process_batch(self, batch_id: str) -> None:
        with self._database.session() as session, session.begin():
            batch = session.get(BulkBatchRow, batch_id)
            if batch is None or batch.status not in {"STAGED", "PROCESSING"}:
                return
            batch.status = BulkBatchStatus.PROCESSING.value
            batch.updated_at = self._now()
            batch.row_version += 1
            project_key = batch.project_key
            supplier_scope = batch.supplier_scope
            entry_ids = tuple(
                session.scalars(
                    select(BulkEntryRow.id)
                    .where(
                        BulkEntryRow.project_key == project_key,
                        BulkEntryRow.batch_id == batch_id,
                        BulkEntryRow.status != BulkEntryStatus.TERMINAL.value,
                    )
                    .order_by(BulkEntryRow.ordinal)
                )
            )
        for entry_id in entry_ids:
            if self._stop.is_set():
                return
            try:
                self._process_entry(project_key, supplier_scope, entry_id)
            except _RawReceiptLinkRetry:
                self._stage_receipt_link_retry(project_key, entry_id)
            except Exception as error:
                self._terminal_error(
                    project_key,
                    entry_id,
                    error=error,
                    stage="ENTRY_PROCESSING",
                )
        self._finish_batch(project_key, batch_id)

    def _stage_receipt_link_retry(self, project_key: str, entry_id: str) -> None:
        with self._database.session() as session, session.begin():
            entry = session.scalar(
                select(BulkEntryRow).where(
                    BulkEntryRow.project_key == project_key,
                    BulkEntryRow.id == entry_id,
                )
            )
            if entry is None:
                return
            entry.status = BulkEntryStatus.STAGED.value
            entry.status_code = "BULK_RECEIPT_LINK_RETRY_REQUIRED"
            entry.message = "보존된 원본 Receipt 연결을 다시 확인해야 합니다."
            entry.updated_at = self._now()
            entry.row_version += 1
            if entry.attempt_count >= 3:
                with self._schedule_lock:
                    self._deferred_batch_ids[entry.batch_id] = (
                        monotonic() + self._receipt_link_retry_delay_seconds
                    )

    def _recover_batch_after_failure(self, batch_id: str) -> None:
        """Keep one batch failure from terminating the durable worker thread."""

        cleanup_paths: list[str] = []
        try:
            with self._database.session() as session, session.begin():
                batch = session.get(BulkBatchRow, batch_id)
                if batch is None or batch.status not in {"STAGED", "PROCESSING"}:
                    return
                now = self._now()
                entries = self._repository.entries(
                    session, project_key=batch.project_key, batch_id=batch.id
                )
                for entry in entries:
                    if entry.status != BulkEntryStatus.PROCESSING.value:
                        continue
                    if entry.attempt_count >= 3:
                        issue_payload = [_issue_payload(_system_issue("BULK_RETRY_EXHAUSTED"))]
                        entry.status = BulkEntryStatus.TERMINAL.value
                        entry.outcome = BulkEntryOutcome.ERROR.value
                        entry.status_code = "BULK_RETRY_EXHAUSTED"
                        entry.message = "반복 실패로 원본 처리를 보류했습니다."
                        entry.issues = issue_payload
                        entry.issues_sha256 = canonical_json_sha256(issue_payload)
                        entry.finished_at = now
                        if entry.staged_relative_path is not None:
                            cleanup_paths.append(entry.staged_relative_path)
                            entry.staged_relative_path = None
                    else:
                        entry.status = BulkEntryStatus.STAGED.value
                        entry.status_code = "BULK_RETRY_STAGED"
                        entry.message = "중단된 원본 처리를 다시 시도합니다."
                    entry.updated_at = now
                    entry.row_version += 1
                batch.status = BulkBatchStatus.STAGED.value
                batch.updated_at = now
                batch.row_version += 1
            for relative_path in cleanup_paths:
                self._cleanup_relative(relative_path)
        except Exception:
            # A later process restart still resets durable PROCESSING rows.
            return

    def _process_entry(self, project_key: str, supplier_scope: str, entry_id: str) -> None:
        with self._database.session() as session, session.begin():
            entry = session.scalar(
                select(BulkEntryRow).where(
                    BulkEntryRow.project_key == project_key, BulkEntryRow.id == entry_id
                )
            )
            if entry is None or entry.status == BulkEntryStatus.TERMINAL.value:
                return
            entry.status = BulkEntryStatus.PROCESSING.value
            entry.status_code = "BULK_PROCESSING"
            entry.message = "원본을 보존하고 승인 매핑 재사용 가능성을 확인하고 있습니다."
            entry.attempt_count += 1
            entry.row_version += 1
            entry.updated_at = self._now()
            receipt_payload = entry.receipt_payload
            staged_relative_path = entry.staged_relative_path
            request_values = (
                entry.filename,
                entry.mime_type,
                entry.reserved_receipt_id,
                entry.reserved_received_at,
            )

        scanned_receipt: SourceFileReceipt | None = None
        scanned_workbook: Any | None = None
        if receipt_payload is None:
            staged_path = self._resolve_staged(staged_relative_path)
            staged_sha256, staged_size = _hash_staged(staged_path, self._limits.max_file_bytes)
            with self._database.session() as session:
                expected = session.scalar(
                    select(BulkEntryRow).where(
                        BulkEntryRow.project_key == project_key,
                        BulkEntryRow.id == entry_id,
                    )
                )
                if (
                    expected is None
                    or staged_sha256 != expected.upload_sha256
                    or staged_size != expected.size_bytes
                ):
                    raise ValueError("staged bytes changed after durable reservation")
            try:
                ingestion = self._ingestion.ingest(
                    ManualIngestionRequest(
                        project_key=project_key,
                        source=staged_path,
                        declared_mime_type=request_values[1],
                        scan_policy=self._scan_policy,
                        reserved_receipt_id=request_values[2],
                        reserved_received_at=request_values[3],
                        on_preserved=lambda receipt: self._persist_receipt(
                            project_key, entry_id, receipt
                        ),
                    )
                )
            except ManualIngestionUnexpectedScanError as error:
                self._terminal_error(
                    project_key,
                    entry_id,
                    error=error.__cause__ or error,
                    stage="WORKBOOK_SCAN",
                    status_code="BULK_SCAN_UNEXPECTED_FAILURE",
                    message="원본은 보존했지만 통합 문서 검사를 완료하지 못했습니다.",
                )
                return
            except ManualIngestionIntegrityError:
                self._cleanup_staged_entry(project_key, entry_id, staged_relative_path)
                self._complete(
                    project_key,
                    entry_id,
                    BulkEntryOutcome.ERROR,
                    "BULK_SOURCE_INTEGRITY_ERROR",
                    "원본과 검사 근거가 일치하지 않습니다.",
                    (_system_issue("BULK_SOURCE_INTEGRITY_ERROR"),),
                )
                return
            self._cleanup_staged_entry(project_key, entry_id, staged_relative_path)
            receipt_payload = _receipt_payload(ingestion.receipt)
            if ingestion.status == ManualIngestionStatus.RAW_PRESERVED_SCAN_FAILED:
                failure = ingestion.scan_failure
                code = failure.status.value if failure is not None else "WORKBOOK_SCAN_FAILED"
                self._complete(
                    project_key,
                    entry_id,
                    BulkEntryOutcome.SCAN_FAILED,
                    code,
                    "원본은 보존했지만 통합 문서 검사가 보류되었습니다.",
                    (
                        _issue(
                            code,
                            BulkIssueCategory.SCAN,
                            BulkIssueSeverity.BLOCKING,
                            "원본은 보존되었습니다. 통합 문서 구조를 확인해 주세요.",
                        ),
                    ),
                )
                return
            scanned_receipt = ingestion.receipt
            scanned_workbook = ingestion.scan
        elif staged_relative_path is not None:
            # Covers a crash after receipt commit but before staging cleanup.
            self._cleanup_staged_entry(project_key, entry_id, staged_relative_path)

        receipt_id = str(receipt_payload["receipt_id"])
        content_sha256 = str(receipt_payload["content_sha256"])
        workspace_request = MappingWorkspaceRequest(
            project_key=project_key,
            receipt_id=receipt_id,
            content_sha256=content_sha256,
            supplier_scope=supplier_scope,
            cell_offset=0,
            cell_limit=1,
        )
        try:
            if scanned_receipt is not None and scanned_workbook is not None:
                workspace = self._mapping.preview_scanned(
                    workspace_request,
                    receipt=scanned_receipt,
                    scan=scanned_workbook,
                )
            else:
                workspace = self._mapping.preview(workspace_request)
        except MappingWorkspaceError as error:
            self._complete(
                project_key,
                entry_id,
                BulkEntryOutcome.MAPPING_REQUIRED,
                error.code,
                "승인 매핑을 안전하게 재사용할 수 없습니다.",
                (
                    _issue(
                        error.code,
                        BulkIssueCategory.MAPPING,
                        BulkIssueSeverity.BLOCKING,
                        error.safe_message,
                    ),
                ),
            )
            return
        if workspace.state != MappingWorkspaceState.PREVIEW_READY:
            variation = any(item.code.value in _FINGERPRINT_CODES for item in workspace.issues)
            self._complete(
                project_key,
                entry_id,
                (
                    BulkEntryOutcome.VARIATION_REVIEW_REQUIRED
                    if variation
                    else BulkEntryOutcome.MAPPING_REQUIRED
                ),
                "BULK_TEMPLATE_VARIATION" if variation else "BULK_MAPPING_REQUIRED",
                (
                    "승인 양식과 다른 구조를 검토해야 합니다."
                    if variation
                    else "일치하는 승인 매핑이 필요합니다."
                ),
                chain(
                    (_mapping_issue(item) for item in workspace.issues),
                    (_system_issue("BULK_MAPPING_REQUIRED"),),
                ),
            )
            return
        try:
            long_request = LongCandidateRequest(
                project_key=project_key,
                receipt_id=receipt_id,
                content_sha256=content_sha256,
                supplier_scope=supplier_scope,
            )
            long_result = self._long.candidate_from_workspace(long_request, workspace)
        except LongWorkflowError as error:
            category = (
                BulkIssueCategory.IDENTIFIER
                if "IDENTIFIER" in error.code or "LOT" in error.code or "MODEL" in error.code
                else BulkIssueCategory.BINDING
            )
            outcome = (
                BulkEntryOutcome.IDENTIFIER_HOLD
                if category == BulkIssueCategory.IDENTIFIER
                else BulkEntryOutcome.BINDING_HOLD
            )
            self._complete(
                project_key,
                entry_id,
                outcome,
                error.code,
                "Long 후보의 연결 근거를 검토해야 합니다.",
                (_issue(error.code, category, BulkIssueSeverity.BLOCKING, error.safe_message),),
                mapping_payload=_mapping_payload(workspace),
            )
            return
        self._complete_candidate(
            project_key,
            supplier_scope,
            entry_id,
            long_result,
            workspace.scan,
        )

    def _complete_candidate(
        self,
        project_key: str,
        supplier_scope: str,
        entry_id: str,
        result: LongWorkflowResult,
        scan: Any,
    ) -> None:
        candidate = result.candidate.candidate
        serialized = serialize_long_candidate(candidate)
        mapping_payload = _mapping_proof_payload(result)
        candidate_payload = {
            "state": candidate.state.value,
            "candidate_digest": result.candidate.candidate_digest,
            "loadable_row_count": len(candidate.loadable_rows),
            "held_row_count": len(candidate.held_rows),
        }
        revision_identity = _revision_identity(serialized, supplier_scope)
        revision_evidence = _revision_evidence(serialized)
        revision_evidence_bytes = len(
            json.dumps(
                revision_evidence,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        if revision_evidence_bytes > _MAX_REVISION_EVIDENCE_BYTES:
            self._complete(
                project_key,
                entry_id,
                BulkEntryOutcome.BINDING_HOLD,
                "BULK_EVIDENCE_LIMIT_EXCEEDED",
                "후보 비교 근거가 서버 제한을 초과해 원본만 보존했습니다.",
                (
                    _issue(
                        "BULK_EVIDENCE_LIMIT_EXCEEDED",
                        BulkIssueCategory.BINDING,
                        BulkIssueSeverity.BLOCKING,
                        "자동 적재 없이 원본 Receipt에서 다시 검토해야 합니다.",
                        expected_json={"max_bytes": _MAX_REVISION_EVIDENCE_BYTES},
                        observed_json={"serialized_bytes": revision_evidence_bytes},
                    ),
                ),
                mapping_payload=mapping_payload,
            )
            return
        evidence_sha = canonical_json_sha256(revision_evidence)
        series_identity = _series_identity(serialized, supplier_scope)
        if candidate.state != LongCandidateState.LOAD_CANDIDATE_READY:
            identifier = any(
                any(token in issue.code.value for token in ("IDENTIFIER", "MODEL", "LOT"))
                for issue in chain(
                    candidate.issues,
                    chain.from_iterable(row.issues for row in candidate.rows),
                )
            )
            self._complete(
                project_key,
                entry_id,
                (BulkEntryOutcome.IDENTIFIER_HOLD if identifier else BulkEntryOutcome.BINDING_HOLD),
                "BULK_IDENTIFIER_HOLD" if identifier else "BULK_BINDING_HOLD",
                "식별자 또는 행 연결 근거를 검토해야 합니다.",
                chain(
                    (_long_issue(item) for item in candidate.issues),
                    (
                        _long_issue(item)
                        for item in chain.from_iterable(row.issues for row in candidate.rows)
                    ),
                    (_system_issue("BULK_CANDIDATE_HELD"),),
                ),
                mapping_payload=mapping_payload,
                candidate_payload=candidate_payload,
                revision_identity=revision_identity,
                revision_evidence=revision_evidence,
            )
            return

        prepared_checkpoint = _prepared_checkpoint(result, scan)
        prepared_checkpoint_bytes = len(_canonical_json_bytes(prepared_checkpoint))
        if prepared_checkpoint_bytes > BULK_PREPARED_CHECKPOINT_MAX_BYTES:
            self._complete(
                project_key,
                entry_id,
                BulkEntryOutcome.BINDING_HOLD,
                "BULK_FINALIZATION_CHECKPOINT_LIMIT_EXCEEDED",
                "최종화 준비 근거가 서버의 안전한 저장 한도를 초과했습니다.",
                (
                    _issue(
                        "BULK_FINALIZATION_CHECKPOINT_LIMIT_EXCEEDED",
                        BulkIssueCategory.BINDING,
                        BulkIssueSeverity.BLOCKING,
                        "원본 Receipt는 보존되며 자동 Long 적재는 수행하지 않습니다.",
                        expected_json={"max_bytes": BULK_PREPARED_CHECKPOINT_MAX_BYTES},
                        observed_json={"serialized_bytes": prepared_checkpoint_bytes},
                    ),
                ),
                mapping_payload=mapping_payload,
                candidate_payload=candidate_payload,
                revision_identity=revision_identity,
                revision_evidence=revision_evidence,
            )
            return

        with self._database.session() as session:
            current = session.scalar(
                select(BulkEntryRow).where(
                    BulkEntryRow.project_key == project_key, BulkEntryRow.id == entry_id
                )
            )
            if current is None:
                return
            priors = self._repository.prior_terminal_entries(
                session,
                project_key=project_key,
                supplier_scope=supplier_scope,
                exclude_entry_id=entry_id,
            )
            revision = next(
                (
                    item
                    for item in priors
                    if item.outcome == BulkEntryOutcome.CANDIDATE_READY.value
                    and item.revision_identity == revision_identity
                    and item.revision_evidence_sha256 != evidence_sha
                ),
                None,
            )
            same_lot_retest = next(
                (
                    item
                    for item in priors
                    if item.outcome == BulkEntryOutcome.CANDIDATE_READY.value
                    and item.revision_identity == revision_identity
                    and item.revision_evidence_sha256 == evidence_sha
                    and item.upload_sha256 != current.upload_sha256
                ),
                None,
            )
            variation = next(
                (
                    item
                    for item in priors
                    if item.outcome == BulkEntryOutcome.CANDIDATE_READY.value
                    and item.revision_identity != revision_identity
                    and _series_identity_from_evidence(item.revision_evidence or {}, supplier_scope)
                    == series_identity
                    and item.mapping_payload is not None
                    and item.mapping_payload.get("template_id") == mapping_payload["template_id"]
                    and item.mapping_payload.get("revision") == mapping_payload["revision"]
                    and _variation_projection(item.revision_evidence or {})
                    != _variation_projection(revision_evidence)
                ),
                None,
            )
            revision_baseline_evidence = (
                dict(revision.revision_evidence or {}) if revision is not None else None
            )
            variation_baseline_evidence = (
                dict(variation.revision_evidence or {}) if variation is not None else None
            )
        if same_lot_retest is not None:
            self._complete(
                project_key,
                entry_id,
                BulkEntryOutcome.DUPLICATE_CANDIDATE,
                "BULK_SAME_LOT_RETEST_CANDIDATE",
                "같은 LOT의 동일 업무 근거를 가진 별도 원본을 재검 후보로 보존했습니다.",
                (
                    _issue(
                        "BULK_SAME_LOT_RETEST_CANDIDATE",
                        BulkIssueCategory.DUPLICATE,
                        BulkIssueSeverity.WARNING,
                        "자동 폐기하거나 기존 결과를 대체하지 않았습니다.",
                        baseline_entry_id=same_lot_retest.id,
                    ),
                ),
                mapping_payload=mapping_payload,
                candidate_payload=candidate_payload,
                revision_identity=revision_identity,
                revision_evidence=revision_evidence,
                duplicate_of_entry_id=same_lot_retest.id,
            )
            return
        if revision is not None:
            if revision_baseline_evidence is None:
                raise ValueError("revision baseline evidence is unavailable")
            revision_issues = _revision_issues(
                baseline_entry_id=revision.id,
                expected=revision_baseline_evidence,
                observed=revision_evidence,
            )
            self._complete(
                project_key,
                entry_id,
                BulkEntryOutcome.REVISION_REVIEW_REQUIRED,
                "BULK_REVISION_REVIEW_REQUIRED",
                "같은 LOT의 다른 근거가 있어 수정본 또는 재검 여부를 검토해야 합니다.",
                revision_issues,
                mapping_payload=mapping_payload,
                candidate_payload=candidate_payload,
                revision_identity=revision_identity,
                revision_evidence=revision_evidence,
                revision_baseline_entry_id=revision.id,
            )
            return
        if variation is not None:
            if variation_baseline_evidence is None:
                raise ValueError("variation baseline evidence is unavailable")
            variation_issues = tuple(
                BulkIssue(
                    code=issue.code.replace("BULK_REVISION_", "BULK_VARIATION_"),
                    category=BulkIssueCategory.VARIATION,
                    severity=BulkIssueSeverity.WARNING,
                    message=(
                        "새 LOT에서 기존 승인 양식의 구조 또는 항목 근거 차이가 확인되었습니다."
                    ),
                    location=issue.location,
                    evidence_path=issue.evidence_path,
                    baseline_entry_id=issue.baseline_entry_id,
                    expected_json=issue.expected_json,
                    observed_json=issue.observed_json,
                )
                for issue in _revision_issues(
                    baseline_entry_id=variation.id,
                    expected=_variation_projection(variation_baseline_evidence),
                    observed=_variation_projection(revision_evidence),
                )
            )
            self._complete(
                project_key,
                entry_id,
                BulkEntryOutcome.VARIATION_REVIEW_REQUIRED,
                "BULK_VARIATION_REVIEW_REQUIRED",
                "새 LOT의 항목 또는 양식 근거 차이를 검토해야 합니다.",
                variation_issues,
                mapping_payload=mapping_payload,
                candidate_payload=candidate_payload,
                revision_identity=revision_identity,
                revision_evidence=revision_evidence,
            )
            return
        self._complete(
            project_key,
            entry_id,
            BulkEntryOutcome.CANDIDATE_READY,
            "BULK_CANDIDATE_READY",
            "승인 매핑과 행 연결로 읽기 전용 후보가 준비되었습니다.",
            (),
            mapping_payload=mapping_payload,
            candidate_payload=candidate_payload,
            revision_identity=revision_identity,
            revision_evidence=revision_evidence,
            prepared_checkpoint=prepared_checkpoint,
            prepared_checkpoint_bytes=prepared_checkpoint_bytes,
        )

    def _persist_receipt(self, project_key: str, entry_id: str, receipt: SourceFileReceipt) -> None:
        payload = _receipt_payload(receipt)
        try:
            with self._database.session() as session, session.begin():
                entry = session.scalar(
                    select(BulkEntryRow).where(
                        BulkEntryRow.project_key == project_key, BulkEntryRow.id == entry_id
                    )
                )
                if entry is None:
                    return
                if (
                    receipt.receipt_id != entry.reserved_receipt_id
                    or receipt.content_sha256 != entry.upload_sha256
                    or receipt.original_filename != entry.filename
                    or receipt.size_bytes != entry.size_bytes
                ):
                    raise ValueError("reserved raw receipt does not match staged evidence")
                digest = canonical_json_sha256(payload)
                if entry.receipt_payload is not None and (
                    entry.receipt_payload != payload or entry.receipt_sha256 != digest
                ):
                    raise ValueError("persisted raw receipt evidence changed")
                entry.receipt_payload = payload
                entry.receipt_sha256 = digest
                entry.updated_at = self._now()
                entry.row_version += 1
        except SQLAlchemyError as error:
            raise _RawReceiptLinkRetry from error

    def _cleanup_staged_entry(
        self, project_key: str, entry_id: str, relative_path: str | None
    ) -> None:
        if relative_path is None:
            return
        self._cleanup_relative(relative_path)
        with self._database.session() as session, session.begin():
            entry = session.scalar(
                select(BulkEntryRow).where(
                    BulkEntryRow.project_key == project_key, BulkEntryRow.id == entry_id
                )
            )
            if entry is not None and entry.receipt_payload is not None:
                entry.staged_relative_path = None
                entry.updated_at = self._now()
                entry.row_version += 1

    def _discard_staged_entry(
        self, project_key: str, entry_id: str, relative_path: str | None
    ) -> None:
        if relative_path is not None:
            self._cleanup_relative(relative_path)
        with self._database.session() as session, session.begin():
            entry = session.scalar(
                select(BulkEntryRow).where(
                    BulkEntryRow.project_key == project_key,
                    BulkEntryRow.id == entry_id,
                )
            )
            if entry is not None and entry.status == BulkEntryStatus.TERMINAL.value:
                entry.staged_relative_path = None
                entry.updated_at = self._now()
                entry.row_version += 1

    def _complete(
        self,
        project_key: str,
        entry_id: str,
        outcome: BulkEntryOutcome,
        status_code: str,
        message: str,
        issues: Iterable[BulkIssue],
        *,
        mapping_payload: dict[str, Any] | None = None,
        candidate_payload: dict[str, Any] | None = None,
        revision_identity: str | None = None,
        revision_evidence: dict[str, Any] | None = None,
        prepared_checkpoint: dict[str, Any] | None = None,
        prepared_checkpoint_bytes: int | None = None,
        duplicate_of_entry_id: str | None = None,
        revision_baseline_entry_id: str | None = None,
    ) -> None:
        issues = _bounded_issues(issues)
        issue_payload = [_issue_payload(item) for item in issues]
        now = self._now()
        with self._database.session() as session, session.begin():
            entry = session.scalar(
                select(BulkEntryRow).where(
                    BulkEntryRow.project_key == project_key, BulkEntryRow.id == entry_id
                )
            )
            if entry is None or entry.status == BulkEntryStatus.TERMINAL.value:
                return
            batch = session.scalar(
                select(BulkBatchRow).where(
                    BulkBatchRow.project_key == project_key,
                    BulkBatchRow.id == entry.batch_id,
                )
            )
            if batch is None:
                raise ValueError("Bulk entry lost its project batch")
            prior_same_bytes = next(
                (
                    prior
                    for prior in self._repository.prior_terminal_entries(
                        session,
                        project_key=project_key,
                        supplier_scope=batch.supplier_scope,
                        exclude_entry_id=entry_id,
                    )
                    if prior.upload_sha256 == entry.upload_sha256
                ),
                None,
            )
            if (
                prior_same_bytes is not None
                and entry.receipt_payload is not None
                and prior_same_bytes.receipt_payload is not None
                and outcome != BulkEntryOutcome.DUPLICATE_CANDIDATE
            ):
                original_outcome = outcome
                outcome = BulkEntryOutcome.DUPLICATE_CANDIDATE
                status_code = f"BULK_EXACT_DUPLICATE_{original_outcome.value}"
                message = "동일한 원본 바이트의 별도 Receipt와 기존 예외 근거를 보존했습니다."
                duplicate_of_entry_id = prior_same_bytes.id
                issues = _bounded_issues(
                    chain(
                        (
                            _issue(
                                "BULK_EXACT_DUPLICATE_CANDIDATE",
                                BulkIssueCategory.DUPLICATE,
                                BulkIssueSeverity.INFO,
                                "자동 폐기하거나 기존 이력을 합치지 않았습니다.",
                                baseline_entry_id=prior_same_bytes.id,
                                expected_json={"original_outcome": original_outcome.value},
                            ),
                        ),
                        issues,
                    )
                )
                issue_payload = [_issue_payload(item) for item in issues]
            _validate_terminal_payload(
                outcome,
                receipt=entry.receipt_payload,
                mapping=mapping_payload,
                candidate=candidate_payload,
                revision_identity=revision_identity,
                revision_evidence=revision_evidence,
            )
            entry.status = BulkEntryStatus.TERMINAL.value
            entry.outcome = outcome.value
            entry.status_code = status_code
            entry.message = message
            entry.mapping_payload = mapping_payload
            entry.mapping_sha256 = (
                canonical_json_sha256(mapping_payload) if mapping_payload is not None else None
            )
            entry.candidate_payload = candidate_payload
            entry.candidate_sha256 = (
                canonical_json_sha256(candidate_payload) if candidate_payload is not None else None
            )
            entry.revision_identity = revision_identity
            entry.revision_evidence = revision_evidence
            entry.revision_evidence_sha256 = (
                canonical_json_sha256(revision_evidence) if revision_evidence is not None else None
            )
            if (prepared_checkpoint is None) != (prepared_checkpoint_bytes is None):
                raise ValueError("prepared checkpoint payload shape is invalid")
            entry.prepared_checkpoint = prepared_checkpoint
            entry.prepared_checkpoint_sha256 = (
                canonical_json_sha256(prepared_checkpoint)
                if prepared_checkpoint is not None
                else None
            )
            entry.prepared_checkpoint_version = (
                BULK_PREPARED_CHECKPOINT_VERSION if prepared_checkpoint is not None else None
            )
            entry.prepared_checkpoint_bytes = prepared_checkpoint_bytes
            entry.issues = issue_payload
            entry.issues_sha256 = canonical_json_sha256(issue_payload)
            entry.duplicate_of_entry_id = duplicate_of_entry_id
            entry.revision_baseline_entry_id = revision_baseline_entry_id
            entry.finished_at = now
            entry.updated_at = now
            entry.row_version += 1

    def _terminal_error(
        self,
        project_key: str,
        entry_id: str,
        *,
        error: BaseException,
        stage: str,
        status_code: str = "BULK_UNEXPECTED_FAILURE",
        message: str = "원본 처리 중 안전하게 공개할 수 없는 오류가 발생했습니다.",
    ) -> None:
        with self._database.session() as session:
            entry = session.scalar(
                select(BulkEntryRow).where(
                    BulkEntryRow.project_key == project_key,
                    BulkEntryRow.id == entry_id,
                )
            )
            relative_path = entry.staged_relative_path if entry is not None else None
        self._complete(
            project_key,
            entry_id,
            BulkEntryOutcome.ERROR,
            status_code,
            message,
            (
                _issue(
                    status_code,
                    BulkIssueCategory.SYSTEM,
                    BulkIssueSeverity.BLOCKING,
                    "내부 오류 원문 없이 안전한 원인 유형만 보존했습니다.",
                    expected_json=_safe_cause_provenance(error, stage=stage),
                ),
            ),
        )
        self._discard_staged_entry(project_key, entry_id, relative_path)

    def _finish_batch(self, project_key: str, batch_id: str) -> None:
        with self._database.session() as session, session.begin():
            batch = self._repository.get_batch(session, project_key=project_key, batch_id=batch_id)
            if batch is None:
                return
            entries = self._repository.entries(session, project_key=project_key, batch_id=batch_id)
            if any(item.status != BulkEntryStatus.TERMINAL.value for item in entries):
                batch.status = BulkBatchStatus.STAGED.value
                batch.updated_at = self._now()
                batch.row_version += 1
                return
            summary = _summary(entries)
            if summary.error == summary.total:
                status = BulkBatchStatus.FAILED
            elif any(
                (
                    summary.variation,
                    summary.duplicate,
                    summary.mapping_required,
                    summary.scan_failed,
                    summary.identifier_hold,
                    summary.binding_hold,
                    summary.revision_review_required,
                    summary.error,
                )
            ):
                status = BulkBatchStatus.COMPLETED_WITH_EXCEPTIONS
            else:
                status = BulkBatchStatus.COMPLETED
            payload = asdict(summary)
            now = self._now()
            batch.status = status.value
            batch.terminal_summary = payload
            batch.terminal_summary_sha256 = canonical_json_sha256(payload)
            batch.finished_at = now
            batch.updated_at = now
            batch.row_version += 1

    def _snapshot(
        self,
        batch: BulkBatchRow,
        rows: tuple[BulkEntryRow, ...],
        *,
        replayed: bool,
    ) -> BulkBatchSnapshot:
        _verify_batch_payload(batch, rows)
        entries = tuple(_entry_snapshot(row) for row in rows)
        summary = _summary(rows)
        terminal = batch.status in {
            BulkBatchStatus.COMPLETED.value,
            BulkBatchStatus.COMPLETED_WITH_EXCEPTIONS.value,
            BulkBatchStatus.FAILED.value,
        }
        if terminal and batch.terminal_summary != asdict(summary):
            raise ValueError("persisted Bulk summary does not match entry evidence")
        labels = {
            "STAGED": ("대기", "원본 보존 작업이 대기 중입니다."),
            "PROCESSING": ("처리 중", "원본과 승인 매핑을 확인하고 있습니다."),
            "COMPLETED": ("후보 준비", "일괄 후보 준비를 완료했습니다."),
            "COMPLETED_WITH_EXCEPTIONS": (
                "검토 필요",
                "일괄 처리는 완료했으며 일부 항목은 검토가 필요합니다.",
            ),
            "FAILED": ("처리 실패", "모든 파일 처리가 보류되었습니다."),
        }
        status_label, message = labels[batch.status]
        return BulkBatchSnapshot(
            batch_id=batch.id,
            project_key=batch.project_key,
            supplier_scope=batch.supplier_scope,
            idempotency_key=batch.idempotency_key,
            status=BulkBatchStatus(batch.status),
            status_label=status_label,
            message=message,
            created_at=_utc(batch.created_at),
            updated_at=_utc(batch.updated_at),
            finished_at=_utc(batch.finished_at) if batch.finished_at is not None else None,
            terminal=terminal,
            poll_after_ms=None if terminal else 500,
            replayed=replayed,
            limits=self._limits,
            summary=summary,
            entries=entries,
            capabilities=BulkCapabilities(),
        )

    def _resolve_staged(self, relative_path: str | None) -> Path:
        if relative_path is None:
            raise ValueError("staged source is unavailable before raw preservation")
        candidate = (self._staging_root / relative_path).resolve()
        try:
            candidate.relative_to(self._staging_root)
        except ValueError as error:
            raise ValueError("staged source escaped the configured root") from error
        if not candidate.is_file():
            raise ValueError("staged source is unavailable")
        return candidate

    def _cleanup_relative(self, relative_path: str) -> None:
        try:
            path = (self._staging_root / relative_path).resolve()
            path.relative_to(self._staging_root)
        except ValueError:
            return
        path.unlink(missing_ok=True)
        parent = path.parent
        while parent != self._staging_root:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent

    def _cleanup_request_files(self, files: tuple[BulkStagedFile, ...]) -> None:
        for item in files:
            self._cleanup_relative(item.staged_relative_path)

    def _cleanup_orphan_submissions(self, referenced_paths: tuple[str | None, ...]) -> None:
        if not self._staging_root.is_dir():
            return
        referenced = {
            Path(path).parts[0]
            for path in referenced_paths
            if path is not None and Path(path).parts
        }
        for child in self._staging_root.iterdir():
            if (
                not child.is_dir()
                or re.fullmatch(r"[0-9a-f]{32}", child.name) is None
                or child.name in referenced
            ):
                continue
            resolved = child.resolve()
            try:
                resolved.relative_to(self._staging_root)
            except ValueError:
                continue
            shutil.rmtree(resolved)

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("clock must return timezone-aware datetime")
        return value.astimezone(UTC)


def _validate_submit(request: BulkSubmitRequest, limits: BulkLimits, root: Path) -> None:
    if _PROJECT_KEY.fullmatch(request.project_key) is None or request.project_key in {".", ".."}:
        raise _validation("INVALID_BULK_PROJECT")
    if (
        not request.supplier_scope.strip()
        or request.supplier_scope != request.supplier_scope.strip()
        or len(request.supplier_scope) > 200
    ):
        raise _validation("INVALID_BULK_SUPPLIER_SCOPE")
    if _IDEMPOTENCY.fullmatch(request.idempotency_key) is None:
        raise _validation("INVALID_BULK_IDEMPOTENCY_KEY")
    if not request.files or len(request.files) > limits.max_files:
        raise _validation("INVALID_BULK_FILE_COUNT")
    if tuple(item.ordinal for item in request.files) != tuple(range(len(request.files))):
        raise _validation("INVALID_BULK_FILE_ORDER")
    total = 0
    for item in request.files:
        if (
            not item.filename
            or item.filename != Path(item.filename).name
            or item.filename in {".", ".."}
            or "\x00" in item.filename
        ):
            raise _validation("INVALID_BULK_FILENAME")
        if item.size_bytes < 1 or item.size_bytes > limits.max_file_bytes:
            raise _validation("BULK_FILE_TOO_LARGE")
        if _SHA256.fullmatch(item.upload_sha256) is None:
            raise _validation("INVALID_BULK_FILE_SHA256")
        candidate = (root / item.staged_relative_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as error:
            raise _validation("INVALID_BULK_STAGING_PATH") from error
        if not candidate.is_file():
            raise _validation("BULK_STAGED_FILE_MISSING")
        total += item.size_bytes
    if total > limits.max_batch_bytes:
        raise _validation("BULK_BATCH_TOO_LARGE")


def _validate_lookup(project_key: str, batch_id: str) -> None:
    if _PROJECT_KEY.fullmatch(project_key) is None or not batch_id.strip() or len(batch_id) > 64:
        raise _validation("INVALID_BULK_LOOKUP")


def _manifest(request: BulkSubmitRequest) -> dict[str, Any]:
    return {
        "project_key": request.project_key,
        "supplier_scope": request.supplier_scope,
        "files": [
            {
                "ordinal": item.ordinal,
                "filename": item.filename,
                "mime_type": item.mime_type,
                "size_bytes": item.size_bytes,
                "upload_sha256": item.upload_sha256,
            }
            for item in request.files
        ],
    }


def _receipt_payload(receipt: SourceFileReceipt) -> dict[str, Any]:
    return {
        "receipt_id": receipt.receipt_id,
        "content_sha256": receipt.content_sha256,
        "original_filename": receipt.original_filename,
        "received_at": receipt.received_at.isoformat(),
        "size_bytes": receipt.size_bytes,
    }


def _mapping_payload(workspace: MappingWorkspaceSnapshot) -> dict[str, Any] | None:
    proof = workspace.template
    if proof is None:
        return None
    return {
        "template_id": proof.template_id,
        "revision": proof.revision,
        "template_sha256": proof.payload_sha256,
        "effective_from": proof.effective_from.isoformat(),
        "effective_to": proof.effective_to.isoformat() if proof.effective_to else None,
        "history_row_version": proof.history_row_version,
        "revision_row_version": proof.revision_row_version,
    }


def _mapping_proof_payload(result: LongWorkflowResult) -> dict[str, Any]:
    proof = result.candidate.mapping_proof
    return {
        "template_id": proof.template_id,
        "revision": proof.revision,
        "template_sha256": proof.payload_sha256,
        "effective_from": proof.effective_from.isoformat(),
        "effective_to": proof.effective_to.isoformat() if proof.effective_to else None,
        "history_row_version": proof.history_row_version,
        "revision_row_version": proof.revision_row_version,
    }


def _prepared_checkpoint(
    result: LongWorkflowResult,
    scan: WorkbookScan,
) -> dict[str, Any]:
    candidate_payload = serialize_long_candidate(result.candidate.candidate)
    provenance = candidate_payload.get("provenance")
    if not isinstance(provenance, dict):
        raise ValueError("Long candidate provenance is missing")
    receipt = provenance.get("receipt")
    selections = provenance.get("binding_selections")
    if not isinstance(receipt, dict) or not isinstance(selections, list):
        raise ValueError("Long candidate checkpoint evidence is malformed")
    mapping = _mapping_proof_payload(result)
    return {
        "version": BULK_PREPARED_CHECKPOINT_VERSION,
        "loader_version": LONG_UI_LOADER_VERSION,
        "scan_contract_version": LONG_UI_SCAN_CONTRACT_VERSION,
        "receipt": receipt,
        "scan": serialize_workbook_scan(scan),
        "mapping": mapping,
        "mapping_sha256": canonical_json_sha256(mapping),
        "binding_selections_sha256": canonical_json_sha256(selections),
        "long_candidate": candidate_payload,
        "long_candidate_digest": result.candidate.candidate_digest,
        "long_candidate_sha256": canonical_json_sha256(candidate_payload),
    }


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _series_identity(candidate: dict[str, Any], supplier_scope: str) -> str:
    identifiers = candidate.get("source_identifiers", candidate.get("identifiers", []))
    series = {
        item.get("kind"): item.get("evidence", {}).get("raw_value")
        for item in identifiers
        if item.get("kind") in {"MODEL", "PART_NUMBER", "PART_NAME"}
    }
    return canonical_json_sha256({"supplier_scope": supplier_scope, "series": series})


def _series_identity_from_evidence(evidence: dict[str, Any], supplier_scope: str) -> str:
    return _series_identity(evidence, supplier_scope)


def _revision_identity(candidate: dict[str, Any], supplier_scope: str) -> str:
    identifiers = candidate.get("source_identifiers", candidate.get("identifiers", []))
    lot = {
        item.get("kind"): item.get("evidence", {}).get("raw_value")
        for item in identifiers
        if item.get("kind") == "LOT_NUMBER"
    }
    return canonical_json_sha256(
        {"series_identity": _series_identity(candidate, supplier_scope), "lot": lot}
    )


def _revision_evidence(candidate: dict[str, Any]) -> dict[str, Any]:
    provenance = dict(candidate.get("provenance", {}))
    provenance.pop("receipt", None)
    provenance.pop("preview_source_name", None)
    provenance.pop("preview_sha256_before", None)
    provenance.pop("preview_sha256_after", None)
    provenance.pop("preview_source_size_bytes", None)
    provenance.pop("binding_catalog_revision", None)
    return {
        "provenance": provenance,
        "identifiers": candidate.get("source_identifiers", []),
        "rows": candidate.get("rows", []),
    }


def _variation_projection(evidence: dict[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for source_row in evidence.get("rows", []):
        row = dict(source_row)
        for volatile in (
            "measurements",
            "supplier_judgment",
            "issues",
            "state",
            "data_status",
            "system_judgment_status",
            "system_judgment",
            "spec_evaluation_status",
        ):
            row.pop(volatile, None)
        rows.append(row)
    return {"rows": rows}


def _revision_issues(
    *, baseline_entry_id: str, expected: dict[str, Any], observed: dict[str, Any]
) -> tuple[BulkIssue, ...]:
    issues: list[BulkIssue] = []
    expected_flat = _flatten(expected)
    observed_flat = _flatten(observed)
    changed_paths = [
        path
        for path in sorted(set(expected_flat) | set(observed_flat))
        if expected_flat.get(path) != observed_flat.get(path)
    ]
    path_limit = _MAX_VARIATION_ISSUES
    if len(changed_paths) > _MAX_VARIATION_ISSUES:
        path_limit -= 1
        issues.append(
            _issue(
                "BULK_REVISION_DIFF_TRUNCATED",
                BulkIssueCategory.REVISION,
                BulkIssueSeverity.WARNING,
                "차이 목록은 서버 제한에 따라 일부만 표시합니다. 전체 근거 해시는 보존됩니다.",
                baseline_entry_id=baseline_entry_id,
                expected_json={"total_differences": len(changed_paths)},
                observed_json={"reported_differences": path_limit},
            )
        )
    for path in changed_paths[:path_limit]:
        before = expected_flat.get(path)
        after = observed_flat.get(path)
        if before == after:
            continue
        category = _variation_category(path)
        issues.append(
            _issue(
                f"BULK_REVISION_{category}",
                BulkIssueCategory.REVISION,
                BulkIssueSeverity.BLOCKING,
                "같은 LOT에서 원본 근거 차이가 확인되었습니다.",
                evidence_path=path,
                baseline_entry_id=baseline_entry_id,
                expected_json=before,
                observed_json=after,
            )
        )
    return tuple(issues) or (
        _issue(
            "BULK_REVISION_EVIDENCE_CHANGED",
            BulkIssueCategory.REVISION,
            BulkIssueSeverity.BLOCKING,
            "같은 LOT의 후보 근거 해시가 변경되었습니다.",
            baseline_entry_id=baseline_entry_id,
        ),
    )


def _flatten(value: Any, prefix: str = "candidate") -> dict[str, Any]:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key in sorted(value):
            result.update(_flatten(value[key], f"{prefix}.{key}"))
        return result
    if isinstance(value, list):
        result = {}
        for index, item in enumerate(value):
            result.update(_flatten(item, f"{prefix}[{index}]"))
        return result
    return {prefix: value}


def _variation_category(path: str) -> str:
    lowered = path.casefold()
    for token, label in (
        ("spec", "SPEC_TOLERANCE"),
        ("lsl", "SPEC_TOLERANCE"),
        ("usl", "SPEC_TOLERANCE"),
        ("tolerance", "SPEC_TOLERANCE"),
        ("method", "METHOD"),
        ("sample", "SAMPLE"),
        ("measurement", "SAMPLE"),
        ("judgment", "JUDGMENT"),
        ("shipment", "SHIPMENT"),
        ("inspection_date", "DATE_LOT_REV"),
        ("revision", "DATE_LOT_REV"),
        ("section", "PART_SECTION"),
        ("part", "PART_SECTION"),
        ("row_key", "STRUCTURE"),
        ("item", "ITEM"),
    ):
        if token in lowered:
            return label
    return "STRUCTURE"


def _mapping_issue(issue: Any) -> BulkIssue:
    location = _logical_location(issue.sheet_name, issue.coordinate)
    return _issue(
        issue.code.value,
        BulkIssueCategory.MAPPING,
        BulkIssueSeverity.BLOCKING,
        issue.message,
        location=location,
        evidence_path=(
            f"workbook.{issue.sheet_name}.{issue.coordinate}"
            if issue.sheet_name and issue.coordinate
            else None
        ),
        expected_json=issue.expected,
        observed_json=issue.observed,
    )


def _long_issue(issue: Any) -> BulkIssue:
    category = (
        BulkIssueCategory.IDENTIFIER
        if any(token in issue.code.value for token in ("IDENTIFIER", "MODEL", "LOT"))
        else BulkIssueCategory.BINDING
    )
    return _issue(
        issue.code.value,
        category,
        BulkIssueSeverity.BLOCKING,
        issue.message,
        location=_logical_location(issue.sheet_name, issue.coordinate),
        evidence_path=f"rows.{issue.row_key}" if issue.row_key else None,
        expected_json=issue.expected,
        observed_json=issue.observed,
    )


def _issue(
    code: str,
    category: BulkIssueCategory,
    severity: BulkIssueSeverity,
    message: str,
    *,
    location: str | None = None,
    evidence_path: str | None = None,
    baseline_entry_id: str | None = None,
    expected_json: Any | None = None,
    observed_json: Any | None = None,
) -> BulkIssue:
    return BulkIssue(
        code=code,
        category=category,
        severity=severity,
        message=message,
        location=location,
        evidence_path=evidence_path,
        baseline_entry_id=baseline_entry_id,
        expected_json=expected_json,
        observed_json=observed_json,
    )


def _system_issue(code: str) -> BulkIssue:
    return _issue(
        code,
        BulkIssueCategory.SYSTEM,
        BulkIssueSeverity.BLOCKING,
        "안전하게 공개할 수 있는 근거만 표시합니다.",
    )


def _safe_cause_provenance(error: BaseException, *, stage: str) -> dict[str, str]:
    cause_type = f"{type(error).__module__}.{type(error).__qualname__}"
    return {
        "stage": stage,
        "cause_type": cause_type,
        "cause_type_sha256": hashlib.sha256(cause_type.encode("utf-8")).hexdigest(),
    }


def _logical_location(sheet: str | None, coordinate: str | None) -> str | None:
    if not sheet:
        return coordinate
    return f"{sheet}!{coordinate}" if coordinate else sheet


def _issue_payload(issue: BulkIssue) -> dict[str, Any]:
    return {
        "code": issue.code,
        "category": issue.category.value,
        "severity": issue.severity.value,
        "message": issue.message,
        "location": issue.location,
        "evidence_path": issue.evidence_path,
        "baseline_entry_id": issue.baseline_entry_id,
        "expected_json": issue.expected_json,
        "observed_json": issue.observed_json,
    }


def _bounded_issues(issues: Iterable[BulkIssue]) -> tuple[BulkIssue, ...]:
    digest = hashlib.sha256()
    digest.update(b"[")
    total_count = 0
    full_serialized_bytes = 2
    bounded: list[BulkIssue] = []
    value_truncations = 0
    for issue in issues:
        encoded = json.dumps(
            _issue_payload(issue),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        if total_count:
            digest.update(b",")
            full_serialized_bytes += 1
        digest.update(encoded)
        full_serialized_bytes += len(encoded)
        total_count += 1
        if len(bounded) < _MAX_TERMINAL_ISSUES:
            expected, expected_truncated = _bounded_json(issue.expected_json)
            observed, observed_truncated = _bounded_json(issue.observed_json)
            value_truncations += int(expected_truncated) + int(observed_truncated)
            bounded.append(
                BulkIssue(
                    code=issue.code,
                    category=issue.category,
                    severity=issue.severity,
                    message=issue.message,
                    location=issue.location[:300] if issue.location is not None else None,
                    evidence_path=(
                        issue.evidence_path[:500] if issue.evidence_path is not None else None
                    ),
                    baseline_entry_id=issue.baseline_entry_id,
                    expected_json=expected,
                    observed_json=observed,
                )
            )
    digest.update(b"]")
    full_digest = digest.hexdigest()
    truncated_count = max(0, total_count - len(bounded))
    if truncated_count or value_truncations:
        if len(bounded) == _MAX_TERMINAL_ISSUES:
            bounded.pop()
        reported_count = len(bounded) + 1
        bounded.append(
            _issue(
                "BULK_ISSUES_TRUNCATED",
                BulkIssueCategory.SYSTEM,
                BulkIssueSeverity.WARNING,
                "표시 근거는 서버 제한에 따라 축약했으며 전체 목록 해시는 보존됩니다.",
                expected_json={
                    "total_count": total_count,
                    "reported_count": reported_count,
                    "truncated_count": truncated_count,
                    "value_truncations": value_truncations,
                    "full_issues_sha256": full_digest,
                    "full_serialized_bytes": full_serialized_bytes,
                },
            )
        )
    return tuple(bounded)


def _bounded_json(value: Any | None) -> tuple[Any | None, bool]:
    if value is None:
        return None, False
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    if len(encoded) <= _MAX_ISSUE_JSON_BYTES:
        return value, False
    return (
        {
            "truncated": True,
            "sha256": hashlib.sha256(encoded).hexdigest(),
            "serialized_bytes": len(encoded),
        },
        True,
    )


def _entry_snapshot(row: BulkEntryRow) -> BulkEntrySnapshot:
    _verify_json(row.receipt_payload, row.receipt_sha256, "receipt")
    _verify_json(row.mapping_payload, row.mapping_sha256, "mapping")
    _verify_json(row.candidate_payload, row.candidate_sha256, "candidate")
    _verify_json(row.issues, row.issues_sha256, "issues")
    receipt = None
    if row.receipt_payload:
        receipt_data = dict(row.receipt_payload)
        receipt_data["received_at"] = datetime.fromisoformat(
            str(receipt_data["received_at"]).replace("Z", "+00:00")
        )
        receipt = BulkReceiptProof(**receipt_data)
    mapping = BulkMappingProof(**row.mapping_payload) if row.mapping_payload else None
    candidate = None
    if row.candidate_payload is not None:
        if row.revision_identity is None or row.revision_evidence_sha256 is None:
            raise ValueError("candidate payload is missing revision evidence digests")
        proof = row.candidate_payload
        candidate = BulkCandidateProof(
            **proof,
            revision_identity_sha256=row.revision_identity,
            revision_evidence_sha256=row.revision_evidence_sha256,
        )
    issues = tuple(
        BulkIssue(
            code=item["code"],
            category=BulkIssueCategory(item["category"]),
            severity=BulkIssueSeverity(item["severity"]),
            message=item["message"],
            location=item.get("location"),
            evidence_path=item.get("evidence_path"),
            baseline_entry_id=item.get("baseline_entry_id"),
            expected_json=item.get("expected_json"),
            observed_json=item.get("observed_json"),
        )
        for item in row.issues
    )
    labels = {
        "STAGED": "대기",
        "PROCESSING": "처리 중",
        "CANDIDATE_READY": "후보 준비",
        "DUPLICATE_CANDIDATE": "중복 후보",
        "MAPPING_REQUIRED": "매핑 필요",
        "SCAN_FAILED": "검사 보류",
        "IDENTIFIER_HOLD": "식별자 보류",
        "BINDING_HOLD": "행 연결 보류",
        "VARIATION_REVIEW_REQUIRED": "양식 변화 검토",
        "REVISION_REVIEW_REQUIRED": "수정본 검토",
        "ERROR": "처리 실패",
    }
    return BulkEntrySnapshot(
        entry_id=row.id,
        ordinal=row.ordinal,
        filename=row.filename,
        mime_type=row.mime_type,
        size_bytes=row.size_bytes,
        upload_sha256=row.upload_sha256,
        status=BulkEntryStatus(row.status),
        outcome=BulkEntryOutcome(row.outcome) if row.outcome else None,
        status_label=labels[row.outcome or row.status],
        message=row.message,
        attempt_count=row.attempt_count,
        row_version=row.row_version,
        receipt=receipt,
        mapping=mapping,
        candidate=candidate,
        duplicate_of_entry_id=row.duplicate_of_entry_id,
        revision_baseline_entry_id=row.revision_baseline_entry_id,
        issues=issues,
    )


def _summary(rows: tuple[BulkEntryRow, ...]) -> BulkSummary:
    outcomes = [row.outcome for row in rows]
    return BulkSummary(
        total=len(rows),
        staged=sum(row.status == "STAGED" for row in rows),
        processing=sum(row.status == "PROCESSING" for row in rows),
        candidate_ready=outcomes.count("CANDIDATE_READY"),
        duplicate=outcomes.count("DUPLICATE_CANDIDATE"),
        variation=outcomes.count("VARIATION_REVIEW_REQUIRED"),
        mapping_required=outcomes.count("MAPPING_REQUIRED"),
        scan_failed=outcomes.count("SCAN_FAILED"),
        identifier_hold=outcomes.count("IDENTIFIER_HOLD"),
        binding_hold=outcomes.count("BINDING_HOLD"),
        revision_review_required=outcomes.count("REVISION_REVIEW_REQUIRED"),
        error=outcomes.count("ERROR"),
    )


def _verify_batch_payload(batch: BulkBatchRow, rows: tuple[BulkEntryRow, ...]) -> None:
    if batch.entry_count != len(rows):
        raise ValueError("Bulk batch entry count changed")
    if batch.terminal_summary is not None:
        _verify_json(
            batch.terminal_summary,
            batch.terminal_summary_sha256,
            "terminal summary",
        )


def _verify_json(payload: Any, digest: str | None, name: str) -> None:
    if (payload is None) != (digest is None):
        raise ValueError(f"{name} payload shape is invalid")
    if payload is not None and canonical_json_sha256(payload) != digest:
        raise ValueError(f"{name} payload digest is invalid")


def _validate_terminal_payload(
    outcome: BulkEntryOutcome,
    *,
    receipt: dict[str, Any] | None,
    mapping: dict[str, Any] | None,
    candidate: dict[str, Any] | None,
    revision_identity: str | None,
    revision_evidence: dict[str, Any] | None,
) -> None:
    if outcome != BulkEntryOutcome.ERROR and receipt is None:
        raise ValueError("non-error terminal outcome requires a preserved receipt")
    proof_required_outcomes = {
        BulkEntryOutcome.CANDIDATE_READY,
        BulkEntryOutcome.REVISION_REVIEW_REQUIRED,
    }
    if outcome in proof_required_outcomes and (mapping is None or candidate is None):
        raise ValueError("candidate terminal outcome requires Mapping and candidate proof")
    if outcome in {BulkEntryOutcome.IDENTIFIER_HOLD, BulkEntryOutcome.BINDING_HOLD} and (
        mapping is None
    ):
        raise ValueError("identifier/binding hold requires approved Mapping proof")
    if candidate is not None and (
        revision_identity is None
        or revision_evidence is None
        or _SHA256.fullmatch(revision_identity) is None
    ):
        raise ValueError("candidate proof requires exact revision evidence")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _hash_staged(path: Path, max_bytes: int) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if size > max_bytes:
                raise ValueError("staged source exceeds the configured file limit")
            digest.update(chunk)
    return digest.hexdigest(), size


def _validation(code: str) -> BulkImportValidationError:
    return BulkImportValidationError(
        code,
        "일괄 등록 요청 값을 확인해 주세요.",
        "요청 오류",
    )


def _unavailable(code: str) -> BulkImportUnavailableError:
    return BulkImportUnavailableError(
        code,
        "일괄 등록 상태를 안전하게 처리할 수 없습니다.",
        "일괄 등록 일시 중단",
    )
