"""Explicit asynchronous materialization of durable Bulk candidate checkpoints."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
from typing import Any, Protocol, cast
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.application.long_workflow import (
    LONG_UI_LOADER_VERSION,
    LONG_UI_SCAN_CONTRACT_VERSION,
    ConfirmLongCandidateRequest,
    LongCandidateRequest,
    LongWorkflowError,
    LongWorkflowResult,
)
from app.domain.audit import AuditChange
from app.domain.bulk_finalization import (
    BulkFinalizationCandidate,
    BulkFinalizationEligibleEntry,
    BulkFinalizationEntrySnapshot,
    BulkFinalizationEntryStatus,
    BulkFinalizationExcludedEntry,
    BulkFinalizationSnapshot,
    BulkFinalizationStatus,
    BulkFinalizationSummary,
)
from app.domain.bulk_import import BulkBatchStatus, BulkEntryOutcome
from app.domain.identity import LOCAL_OWNER, SYSTEM_ACTOR
from app.domain.source_file import SourceFileReceipt
from app.domain.workbook_scan import WorkbookScan
from app.infrastructure.audit import AuditRepository
from app.infrastructure.bulk_finalization import (
    BulkFinalizationCommandRow,
    BulkFinalizationEntryRow,
    BulkFinalizationRepository,
)
from app.infrastructure.bulk_import import (
    BULK_PREPARED_CHECKPOINT_MAX_BYTES,
    BULK_PREPARED_CHECKPOINT_VERSION,
    BulkBatchRow,
    BulkEntryRow,
)
from app.infrastructure.database import Database
from app.infrastructure.long_format import (
    LongJobStatus,
    LongPersistenceIntegrityError,
    canonical_json_sha256,
    deserialize_workbook_scan,
    serialize_long_candidate,
)

_PROJECT_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_TERMINAL_BULK = {
    BulkBatchStatus.COMPLETED.value,
    BulkBatchStatus.COMPLETED_WITH_EXCEPTIONS.value,
}
_CHECKPOINT_KEYS = {
    "version",
    "loader_version",
    "scan_contract_version",
    "receipt",
    "scan",
    "mapping",
    "mapping_sha256",
    "binding_selections_sha256",
    "long_candidate",
    "long_candidate_digest",
    "long_candidate_sha256",
}
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SubmitBulkFinalizationRequest:
    project_key: str
    batch_id: str
    finalization_digest: str
    confirmed: bool
    reason: str


class PreparedLongWorkflowPort(Protocol):
    def candidate_prepared(
        self,
        request: LongCandidateRequest,
        *,
        receipt: SourceFileReceipt,
        scan: WorkbookScan,
    ) -> LongWorkflowResult: ...

    def confirm_prepared(
        self,
        request: ConfirmLongCandidateRequest,
        *,
        receipt: SourceFileReceipt,
        scan: WorkbookScan,
    ) -> LongWorkflowResult: ...


class BulkFinalizationError(RuntimeError):
    def __init__(self, code: str, message: str, status_label: str) -> None:
        self.code = code
        self.safe_message = message
        self.status_label = status_label
        super().__init__(code)


class BulkFinalizationValidationError(BulkFinalizationError):
    pass


class BulkFinalizationNotFoundError(BulkFinalizationError):
    pass


class BulkFinalizationConflictError(BulkFinalizationError):
    pass


class BulkFinalizationUnavailableError(BulkFinalizationError):
    pass


class BulkFinalizationManager:
    """One bounded worker; all progress and intent remain durable in SQLite."""

    def __init__(
        self,
        *,
        database: Database,
        long_workflow: PreparedLongWorkflowPort,
        queue_capacity: int = 32,
        repository: BulkFinalizationRepository | None = None,
        audit_repository: AuditRepository | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if queue_capacity < 1:
            raise ValueError("queue_capacity must be positive")
        self._database = database
        self._long = long_workflow
        self._repository = repository or BulkFinalizationRepository()
        self._audit = audit_repository or AuditRepository()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._queue_capacity = queue_capacity
        self._queue: Queue[str | None] = Queue(maxsize=queue_capacity)
        self._stop = Event()
        self._thread: Thread | None = None
        self._lifecycle_lock = Lock()
        self._schedule_lock = Lock()
        self._scheduled: set[str] = set()

    def start(self) -> None:
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._queue = Queue(maxsize=self._queue_capacity)
            with self._schedule_lock:
                self._scheduled.clear()
            with self._database.session() as session, session.begin():
                now = self._now()
                session.execute(
                    update(BulkFinalizationEntryRow)
                    .where(BulkFinalizationEntryRow.status == "PROCESSING")
                    .values(
                        status="PENDING",
                        updated_at=now,
                        row_version=BulkFinalizationEntryRow.row_version + 1,
                    )
                )
                session.execute(
                    update(BulkFinalizationCommandRow)
                    .where(BulkFinalizationCommandRow.status == "PROCESSING")
                    .values(
                        status="QUEUED",
                        updated_at=now,
                        row_version=BulkFinalizationCommandRow.row_version + 1,
                    )
                )
            self._thread = Thread(
                target=self._worker,
                name="dq-bulk-finalization-worker",
                daemon=True,
            )
            self._thread.start()
            self._sweep_recoverable()

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
                raise BulkFinalizationUnavailableError(
                    "BULK_FINALIZATION_SHUTDOWN_TIMEOUT",
                    "진행 중인 정상 후보 반영 작업을 안전하게 종료하지 못했습니다.",
                    "종료 대기 필요",
                )
            self._thread = None

    def candidate(self, *, project_key: str, batch_id: str) -> BulkFinalizationCandidate:
        _validate_scope(project_key, batch_id)
        try:
            with self._database.session() as session:
                return self._candidate(session, project_key=project_key, batch_id=batch_id)
        except BulkFinalizationError:
            raise
        except (SQLAlchemyError, ValueError, TypeError) as error:
            raise _unavailable("BULK_FINALIZATION_CANDIDATE_UNAVAILABLE") from error

    def submit(self, request: SubmitBulkFinalizationRequest) -> BulkFinalizationSnapshot:
        _validate_submit(request)
        command_id: str
        enqueue = False
        try:
            with self._database.session() as session, session.begin():
                candidate = self._candidate(
                    session,
                    project_key=request.project_key,
                    batch_id=request.batch_id,
                    lock=True,
                )
                if request.finalization_digest != candidate.finalization_digest:
                    raise BulkFinalizationConflictError(
                        "BULK_FINALIZATION_STALE",
                        "일괄 후보 근거가 변경되었습니다. 후보를 다시 확인해 주세요.",
                        "후보 변경됨",
                    )
                if not candidate.can_finalize:
                    raise BulkFinalizationConflictError(
                        "BULK_FINALIZATION_NO_ELIGIBLE_ENTRIES",
                        "Long에 반영할 정상 후보가 없습니다.",
                        "정상 후보 없음",
                    )
                existing = self._repository.get_by_batch(
                    session,
                    project_key=request.project_key,
                    batch_id=request.batch_id,
                )
                if existing is not None:
                    if (
                        existing.finalization_digest != request.finalization_digest
                        or existing.reason != request.reason
                        or existing.supplier_scope != candidate.supplier_scope
                    ):
                        raise BulkFinalizationConflictError(
                            "BULK_FINALIZATION_INTENT_CONFLICT",
                            "이미 기록된 명시적 반영 의도와 요청이 일치하지 않습니다.",
                            "반영 요청 충돌",
                        )
                    command_id = existing.id
                    if existing.status == BulkFinalizationStatus.BLOCKED.value:
                        now = self._now()
                        before_version = existing.row_version
                        for entry in self._repository.entries(
                            session,
                            project_key=request.project_key,
                            command_id=existing.id,
                        ):
                            if entry.status == BulkFinalizationEntryStatus.BLOCKED.value:
                                entry.status = BulkFinalizationEntryStatus.PENDING.value
                                entry.error_code = None
                                entry.finished_at = None
                                entry.updated_at = now
                                entry.row_version += 1
                        existing.status = BulkFinalizationStatus.QUEUED.value
                        existing.finished_at = None
                        existing.updated_at = now
                        existing.row_version += 1
                        self._audit.append(
                            session,
                            AuditChange(
                                actor=LOCAL_OWNER,
                                action="bulk_finalization_resumed",
                                target_type="bulk_finalization_command",
                                target_id=existing.id,
                                before_state={
                                    "status": BulkFinalizationStatus.BLOCKED.value,
                                    "row_version": before_version,
                                },
                                after_state={
                                    "status": BulkFinalizationStatus.QUEUED.value,
                                    "row_version": existing.row_version,
                                    "finalization_digest": existing.finalization_digest,
                                },
                                reason=request.reason,
                                requirement_id="DQ-P2-BULKFINAL-005",
                                source_reference=f"bulk:{request.batch_id}",
                            ),
                        )
                        enqueue = True
                    elif existing.status in {
                        BulkFinalizationStatus.QUEUED.value,
                        BulkFinalizationStatus.PROCESSING.value,
                    }:
                        enqueue = True
                else:
                    command_id = str(uuid4())
                    now = self._now()
                    command = BulkFinalizationCommandRow(
                        id=command_id,
                        project_key=request.project_key,
                        batch_id=request.batch_id,
                        supplier_scope=candidate.supplier_scope,
                        finalization_digest=request.finalization_digest,
                        reason=request.reason,
                        requested_by=LOCAL_OWNER.actor_id,
                        status=BulkFinalizationStatus.QUEUED.value,
                        entry_count=len(candidate.eligible_entries),
                        row_version=1,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(command)
                    for item in candidate.eligible_entries:
                        session.add(
                            BulkFinalizationEntryRow(
                                id=str(uuid4()),
                                project_key=request.project_key,
                                command_id=command_id,
                                batch_id=request.batch_id,
                                bulk_entry_id=item.entry_id,
                                ordinal=item.ordinal,
                                expected_bulk_row_version=item.bulk_row_version,
                                expected_receipt_id=item.receipt_id,
                                expected_content_sha256=item.content_sha256,
                                expected_mapping_sha256=item.mapping_sha256,
                                expected_candidate_payload_sha256=_candidate_payload_sha(
                                    session, request.project_key, item.entry_id
                                ),
                                expected_long_candidate_digest=item.long_candidate_digest,
                                expected_checkpoint_sha256=item.prepared_checkpoint_sha256,
                                expected_checkpoint_version=item.prepared_checkpoint_version,
                                expected_checkpoint_bytes=item.prepared_checkpoint_bytes,
                                status=BulkFinalizationEntryStatus.PENDING.value,
                                attempt_count=0,
                                row_version=1,
                                created_at=now,
                                updated_at=now,
                            )
                        )
                    self._audit.append(
                        session,
                        AuditChange(
                            actor=LOCAL_OWNER,
                            action="bulk_finalization_requested",
                            target_type="bulk_finalization_command",
                            target_id=command_id,
                            before_state=None,
                            after_state={
                                "project_key": request.project_key,
                                "batch_id": request.batch_id,
                                "finalization_digest": request.finalization_digest,
                                "eligible_count": len(candidate.eligible_entries),
                                "excluded_count": len(candidate.excluded_entries),
                                "auto_valid": False,
                                "auto_replaced": False,
                            },
                            reason=request.reason,
                            requirement_id="DQ-P2-BULKFINAL-002",
                            source_reference=f"bulk:{request.batch_id}",
                        ),
                    )
                    enqueue = True
        except BulkFinalizationError:
            raise
        except IntegrityError as error:
            return self._concurrent_replay(request, error)
        except SQLAlchemyError as error:
            raise _unavailable("BULK_FINALIZATION_DATABASE_UNAVAILABLE") from error
        if enqueue:
            self._enqueue(command_id)
        self._sweep_recoverable()
        return self.get(project_key=request.project_key, batch_id=request.batch_id)

    def get(self, *, project_key: str, batch_id: str) -> BulkFinalizationSnapshot:
        _validate_scope(project_key, batch_id)
        try:
            with self._database.session() as session:
                command = self._repository.get_by_batch(
                    session, project_key=project_key, batch_id=batch_id
                )
                if command is None:
                    raise BulkFinalizationNotFoundError(
                        "BULK_FINALIZATION_NOT_FOUND",
                        "해당 프로젝트에서 일괄 반영 요청을 찾을 수 없습니다.",
                        "반영 요청 없음",
                    )
                entries = self._repository.entries(
                    session, project_key=project_key, command_id=command.id
                )
                return _snapshot(command, entries)
        except BulkFinalizationError:
            raise
        except (SQLAlchemyError, ValueError) as error:
            raise _unavailable("BULK_FINALIZATION_READ_UNAVAILABLE") from error

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                command_id = self._queue.get(timeout=0.25)
            except Empty:
                self._sweep_recoverable()
                continue
            if command_id is None:
                self._queue.task_done()
                break
            try:
                self._process_command(command_id)
            except Exception as error:
                try:
                    self._block_unexpected(command_id, error, "process_command")
                except SQLAlchemyError as persistence_error:
                    proof = _safe_cause(persistence_error, "block_unexpected")
                    _LOGGER.error(
                        "Bulk finalization recovery persistence failed: %s %s",
                        proof["cause_type"],
                        proof["cause_type_sha256"],
                    )
            finally:
                with self._schedule_lock:
                    self._scheduled.discard(command_id)
                self._queue.task_done()
                self._sweep_recoverable()

    def _process_command(self, command_id: str) -> None:
        with self._database.session() as session, session.begin():
            command = session.scalar(
                select(BulkFinalizationCommandRow)
                .where(BulkFinalizationCommandRow.id == command_id)
                .with_for_update()
            )
            if command is None or command.status in {"COMPLETED", "BLOCKED"}:
                return
            command.status = BulkFinalizationStatus.PROCESSING.value
            command.updated_at = self._now()
            command.row_version += 1
            project_key = command.project_key

        while not self._stop.is_set():
            claimed = self._claim_next(project_key, command_id)
            if claimed is None:
                break
            try:
                result = self._materialize_prepared(claimed)
            except (
                BulkFinalizationError,
                LongWorkflowError,
                LongPersistenceIntegrityError,
            ) as error:
                code = getattr(error, "code", "BULK_FINALIZATION_CHECKPOINT_INTEGRITY_ERROR")
                self._mark_blocked(claimed, str(code)[:120])
            except (SQLAlchemyError, TypeError, ValueError) as error:
                self._mark_blocked(
                    claimed,
                    "BULK_FINALIZATION_INTEGRITY_ERROR",
                    cause=_safe_cause(error, "materialize_prepared"),
                )
            else:
                self._mark_completed(claimed, result)
        self._finish_command(project_key, command_id)

    def _claim_next(self, project_key: str, command_id: str) -> BulkFinalizationEntryRow | None:
        with self._database.session() as session, session.begin():
            row = session.scalar(
                select(BulkFinalizationEntryRow)
                .where(
                    BulkFinalizationEntryRow.project_key == project_key,
                    BulkFinalizationEntryRow.command_id == command_id,
                    BulkFinalizationEntryRow.status == BulkFinalizationEntryStatus.PENDING.value,
                )
                .order_by(BulkFinalizationEntryRow.ordinal)
                .with_for_update()
            )
            if row is None:
                return None
            row.status = BulkFinalizationEntryStatus.PROCESSING.value
            row.attempt_count += 1
            row.updated_at = self._now()
            row.row_version += 1
            session.flush()
            session.expunge(row)
            return row

    def _materialize_prepared(self, plan: BulkFinalizationEntryRow) -> LongWorkflowResult:
        with self._database.session() as session:
            bulk = session.scalar(
                select(BulkEntryRow).where(
                    BulkEntryRow.project_key == plan.project_key,
                    BulkEntryRow.id == plan.bulk_entry_id,
                    BulkEntryRow.batch_id == plan.batch_id,
                )
            )
            command = self._repository.get_command(
                session, project_key=plan.project_key, command_id=plan.command_id
            )
            if bulk is None or command is None:
                raise _conflict("BULK_FINALIZATION_BASIS_MISSING")
            checkpoint = _validated_checkpoint(plan, bulk)
            receipt = _receipt(checkpoint)
            if (
                receipt.project_key != plan.project_key
                or receipt.receipt_id != plan.expected_receipt_id
                or receipt.content_sha256 != plan.expected_content_sha256
                or receipt.original_filename != bulk.filename
                or receipt.size_bytes != bulk.size_bytes
            ):
                raise _conflict("BULK_FINALIZATION_RECEIPT_SCOPE_MISMATCH")
            scan = _scan(checkpoint)
            long_request = LongCandidateRequest(
                project_key=plan.project_key,
                receipt_id=plan.expected_receipt_id,
                content_sha256=plan.expected_content_sha256,
                supplier_scope=command.supplier_scope,
            )
            rebuilt = self._long.candidate_prepared(
                long_request,
                receipt=receipt,
                scan=scan,
            )
            _verify_rebuilt_checkpoint(plan, checkpoint, rebuilt)
        persisted = self._long.confirm_prepared(
            ConfirmLongCandidateRequest(
                project_key=plan.project_key,
                receipt_id=plan.expected_receipt_id,
                content_sha256=plan.expected_content_sha256,
                supplier_scope=command.supplier_scope,
                candidate_digest=plan.expected_long_candidate_digest,
                confirmed=True,
            ),
            receipt=receipt,
            scan=scan,
        )
        if persisted.persistence is None or persisted.persistence.status not in {
            LongJobStatus.COMPLETED_PENDING,
            LongJobStatus.REUSED,
        }:
            raise _conflict("BULK_FINALIZATION_LONG_NOT_PENDING")
        return persisted

    def _mark_completed(self, plan: BulkFinalizationEntryRow, result: LongWorkflowResult) -> None:
        persistence = result.persistence
        if persistence is None:
            raise AssertionError("completed prepared Long result lost persistence")
        with self._database.session() as session, session.begin():
            row = _locked_plan(session, plan)
            row.status = BulkFinalizationEntryStatus.COMPLETED.value
            row.long_source_file_id = persistence.source_file_id
            row.long_ingestion_job_id = persistence.ingestion_job_id
            row.long_status = persistence.status.value
            row.long_row_version = persistence.row_version
            row.replayed = persistence.replayed
            row.finished_at = self._now()
            row.updated_at = row.finished_at
            row.row_version += 1
            command = self._repository.get_command(
                session, project_key=row.project_key, command_id=row.command_id
            )
            if command is None:
                raise _conflict("BULK_FINALIZATION_COMMAND_MISSING")
            self._audit.append(
                session,
                AuditChange(
                    actor=SYSTEM_ACTOR,
                    action="bulk_finalization_entry_materialized",
                    target_type="bulk_finalization_entry",
                    target_id=row.id,
                    before_state={"status": "PROCESSING", "row_version": plan.row_version},
                    after_state={
                        "status": row.status,
                        "row_version": row.row_version,
                        "long_source_file_id": row.long_source_file_id,
                        "long_ingestion_job_id": row.long_ingestion_job_id,
                        "long_status": row.long_status,
                        "long_row_version": row.long_row_version,
                        "replayed": row.replayed,
                        "auto_valid": False,
                        "auto_replaced": False,
                    },
                    reason=command.reason,
                    requirement_id="DQ-P2-BULKFINAL-004",
                    source_reference=f"bulk:{row.batch_id}:entry:{row.bulk_entry_id}",
                ),
            )

    def _mark_blocked(
        self,
        plan: BulkFinalizationEntryRow,
        code: str,
        *,
        cause: dict[str, str] | None = None,
    ) -> None:
        with self._database.session() as session, session.begin():
            row = _locked_plan(session, plan)
            row.status = BulkFinalizationEntryStatus.BLOCKED.value
            row.error_code = code
            row.finished_at = self._now()
            row.updated_at = row.finished_at
            row.row_version += 1
            command = self._repository.get_command(
                session, project_key=row.project_key, command_id=row.command_id
            )
            if command is None:
                raise _conflict("BULK_FINALIZATION_COMMAND_MISSING")
            self._audit.append(
                session,
                AuditChange(
                    actor=SYSTEM_ACTOR,
                    action="bulk_finalization_entry_blocked",
                    target_type="bulk_finalization_entry",
                    target_id=row.id,
                    before_state={"status": "PROCESSING", "row_version": plan.row_version},
                    after_state={
                        "status": row.status,
                        "row_version": row.row_version,
                        "error_code": row.error_code,
                        **(cause or {}),
                    },
                    reason=command.reason,
                    requirement_id="DQ-P2-BULKFINAL-004",
                    source_reference=f"bulk:{row.batch_id}:entry:{row.bulk_entry_id}",
                ),
            )

    def _finish_command(self, project_key: str, command_id: str) -> None:
        with self._database.session() as session, session.begin():
            command = session.scalar(
                select(BulkFinalizationCommandRow)
                .where(
                    BulkFinalizationCommandRow.project_key == project_key,
                    BulkFinalizationCommandRow.id == command_id,
                )
                .with_for_update()
            )
            if command is None:
                return
            entries = self._repository.entries(
                session, project_key=project_key, command_id=command_id
            )
            if len(entries) != command.entry_count:
                raise _conflict("BULK_FINALIZATION_PLAN_INTEGRITY_ERROR")
            if any(
                item.status
                in {
                    BulkFinalizationEntryStatus.PENDING.value,
                    BulkFinalizationEntryStatus.PROCESSING.value,
                }
                for item in entries
            ):
                command.status = BulkFinalizationStatus.QUEUED.value
                command.finished_at = None
                command.updated_at = self._now()
                command.row_version += 1
                return
            before_version = command.row_version
            command.status = (
                BulkFinalizationStatus.BLOCKED.value
                if any(item.status == BulkFinalizationEntryStatus.BLOCKED.value for item in entries)
                else BulkFinalizationStatus.COMPLETED.value
            )
            command.finished_at = self._now()
            command.updated_at = command.finished_at
            command.row_version += 1
            self._audit.append(
                session,
                AuditChange(
                    actor=SYSTEM_ACTOR,
                    action="bulk_finalization_finished",
                    target_type="bulk_finalization_command",
                    target_id=command.id,
                    before_state={"status": "PROCESSING", "row_version": before_version},
                    after_state={
                        "status": command.status,
                        "row_version": command.row_version,
                        "completed_entry_ids": [
                            item.id for item in entries if item.status == "COMPLETED"
                        ],
                        "blocked_entry_ids": [
                            item.id for item in entries if item.status == "BLOCKED"
                        ],
                        "long_job_ids": [
                            item.long_ingestion_job_id
                            for item in entries
                            if item.long_ingestion_job_id is not None
                        ],
                        "initial_database_gate_complete": False,
                    },
                    reason=command.reason,
                    requirement_id="DQ-P2-BULKFINAL-004",
                    source_reference=f"bulk:{command.batch_id}",
                ),
            )

    def _block_unexpected(self, command_id: str, error: Exception, stage: str) -> None:
        proof = _safe_cause(error, stage)
        with self._database.session() as session, session.begin():
            command = session.scalar(
                select(BulkFinalizationCommandRow)
                .where(BulkFinalizationCommandRow.id == command_id)
                .with_for_update()
            )
            if command is None or command.status in {"COMPLETED", "BLOCKED"}:
                return
            now = self._now()
            entries = self._repository.entries(
                session, project_key=command.project_key, command_id=command.id
            )
            for entry in entries:
                if entry.status in {"PENDING", "PROCESSING"}:
                    entry.status = "BLOCKED"
                    entry.error_code = "BULK_FINALIZATION_UNEXPECTED_FAILURE"
                    entry.finished_at = now
                    entry.updated_at = now
                    entry.row_version += 1
            before_version = command.row_version
            command.status = "BLOCKED"
            command.finished_at = now
            command.updated_at = now
            command.row_version += 1
            self._audit.append(
                session,
                AuditChange(
                    actor=SYSTEM_ACTOR,
                    action="bulk_finalization_worker_failed",
                    target_type="bulk_finalization_command",
                    target_id=command.id,
                    before_state={"status": "PROCESSING", "row_version": before_version},
                    after_state={
                        "status": "BLOCKED",
                        "row_version": command.row_version,
                        **proof,
                    },
                    reason=command.reason,
                    requirement_id="DQ-P2-BULKFINAL-005",
                    source_reference=f"bulk:{command.batch_id}",
                ),
            )

    def _enqueue(self, command_id: str) -> bool:
        with self._schedule_lock:
            if command_id in self._scheduled:
                return True
            self._scheduled.add(command_id)
        try:
            self._queue.put_nowait(command_id)
        except Full:
            with self._schedule_lock:
                self._scheduled.discard(command_id)
            return False
        return True

    def _sweep_recoverable(self) -> None:
        if self._stop.is_set():
            return
        try:
            with self._database.session() as session:
                recoverable = self._repository.recoverable_command_ids(session)
        except SQLAlchemyError as error:
            proof = _safe_cause(error, "recoverable_sweep")
            _LOGGER.error(
                "Bulk finalization durable sweep failed: %s %s",
                proof["cause_type"],
                proof["cause_type_sha256"],
            )
            return
        for command_id in recoverable:
            if not self._enqueue(command_id):
                break

    def _concurrent_replay(
        self, request: SubmitBulkFinalizationRequest, error: IntegrityError
    ) -> BulkFinalizationSnapshot:
        try:
            with self._database.session() as session:
                existing = self._repository.get_by_batch(
                    session,
                    project_key=request.project_key,
                    batch_id=request.batch_id,
                )
                if existing is None or (
                    existing.finalization_digest != request.finalization_digest
                    or existing.reason != request.reason
                ):
                    raise BulkFinalizationConflictError(
                        "BULK_FINALIZATION_CONCURRENT_CONFLICT",
                        "동시에 생성된 반영 요청과 입력이 일치하지 않습니다.",
                        "동시 요청 충돌",
                    ) from error
                command_id = existing.id
        except BulkFinalizationError:
            raise
        except SQLAlchemyError as read_error:
            raise _unavailable("BULK_FINALIZATION_DATABASE_UNAVAILABLE") from read_error
        self._enqueue(command_id)
        self._sweep_recoverable()
        return self.get(project_key=request.project_key, batch_id=request.batch_id)

    def _candidate(
        self,
        session: Session,
        *,
        project_key: str,
        batch_id: str,
        lock: bool = False,
    ) -> BulkFinalizationCandidate:
        statement = select(BulkBatchRow).where(
            BulkBatchRow.project_key == project_key,
            BulkBatchRow.id == batch_id,
        )
        if lock:
            statement = statement.with_for_update()
        batch = session.scalar(statement)
        if batch is None:
            raise BulkFinalizationNotFoundError(
                "BULK_BATCH_NOT_FOUND",
                "해당 프로젝트에서 일괄 등록 묶음을 찾을 수 없습니다.",
                "일괄 묶음 없음",
            )
        if batch.status not in _TERMINAL_BULK:
            raise BulkFinalizationConflictError(
                "BULK_BATCH_NOT_TERMINAL",
                "일괄 원본 검사와 예외 분류가 끝난 뒤 후보를 확인해 주세요.",
                "일괄 검사 진행 중",
            )
        entry_statement = (
            select(BulkEntryRow)
            .where(BulkEntryRow.project_key == project_key, BulkEntryRow.batch_id == batch_id)
            .order_by(BulkEntryRow.ordinal)
        )
        if lock:
            entry_statement = entry_statement.with_for_update()
        rows = tuple(session.scalars(entry_statement))
        if len(rows) != batch.entry_count or any(row.status != "TERMINAL" for row in rows):
            raise BulkFinalizationConflictError(
                "BULK_BATCH_EVIDENCE_INCOMPLETE",
                "일괄 등록의 영속 근거가 완전하지 않습니다.",
                "일괄 근거 오류",
            )
        eligible: list[BulkFinalizationEligibleEntry] = []
        excluded: list[BulkFinalizationExcludedEntry] = []
        digest_entries: list[dict[str, object]] = []
        for row in rows:
            _verify_small_bulk_evidence(row)
            prepared = _prepared_metadata_valid(row)
            if row.outcome == BulkEntryOutcome.CANDIDATE_READY.value and prepared:
                receipt = cast(dict[str, Any], row.receipt_payload)
                candidate = cast(dict[str, Any], row.candidate_payload)
                eligible_item = BulkFinalizationEligibleEntry(
                    entry_id=row.id,
                    ordinal=row.ordinal,
                    filename=row.filename,
                    bulk_row_version=row.row_version,
                    receipt_id=str(receipt["receipt_id"]),
                    content_sha256=str(receipt["content_sha256"]),
                    mapping_sha256=cast(str, row.mapping_sha256),
                    long_candidate_digest=str(candidate["candidate_digest"]),
                    prepared_checkpoint_sha256=cast(str, row.prepared_checkpoint_sha256),
                    prepared_checkpoint_version=cast(str, row.prepared_checkpoint_version),
                    prepared_checkpoint_bytes=cast(int, row.prepared_checkpoint_bytes),
                )
                eligible.append(eligible_item)
                digest_entries.append(
                    {"eligibility": "ELIGIBLE", **_eligible_payload(eligible_item)}
                )
            else:
                excluded_receipt = (
                    row.receipt_payload if isinstance(row.receipt_payload, dict) else None
                )
                status_code = (
                    "BULK_FINALIZATION_PREPARATION_REQUIRED"
                    if row.outcome == BulkEntryOutcome.CANDIDATE_READY.value
                    else row.status_code
                )
                excluded_item = BulkFinalizationExcludedEntry(
                    entry_id=row.id,
                    ordinal=row.ordinal,
                    filename=row.filename,
                    outcome=cast(str, row.outcome),
                    status_code=status_code,
                    issues_sha256=row.issues_sha256,
                    bulk_row_version=row.row_version,
                    size_bytes=row.size_bytes,
                    upload_sha256=row.upload_sha256,
                    receipt_id=(
                        cast(str, excluded_receipt["receipt_id"])
                        if excluded_receipt is not None
                        and isinstance(excluded_receipt.get("receipt_id"), str)
                        else None
                    ),
                    content_sha256=(
                        cast(str, excluded_receipt["content_sha256"])
                        if excluded_receipt is not None
                        and isinstance(excluded_receipt.get("content_sha256"), str)
                        else None
                    ),
                )
                excluded.append(excluded_item)
                digest_entries.append(
                    {"eligibility": "EXCLUDED", **_excluded_payload(excluded_item)}
                )
        digest = canonical_json_sha256(
            {
                "contract_version": "bulk-finalization-candidate-v1",
                "batch_id": batch.id,
                "project_key": batch.project_key,
                "supplier_scope": batch.supplier_scope,
                "batch_status": batch.status,
                "batch_manifest_sha256": batch.manifest_sha256,
                "batch_row_version": batch.row_version,
                "entries": digest_entries,
            }
        )
        return BulkFinalizationCandidate(
            batch_id=batch.id,
            project_key=batch.project_key,
            supplier_scope=batch.supplier_scope,
            batch_status=batch.status,
            batch_row_version=batch.row_version,
            finalization_digest=digest,
            eligible_entries=tuple(eligible),
            excluded_entries=tuple(excluded),
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Bulk finalization clock must be timezone-aware")
        return value.astimezone(UTC)


def _validate_scope(project_key: str, batch_id: str) -> None:
    if _PROJECT_KEY.fullmatch(project_key) is None:
        raise BulkFinalizationValidationError(
            "INVALID_PROJECT_KEY", "프로젝트 키 형식이 올바르지 않습니다.", "요청 확인 필요"
        )
    if not batch_id or batch_id != batch_id.strip() or len(batch_id) > 36:
        raise BulkFinalizationValidationError(
            "INVALID_BATCH_ID", "일괄 묶음 ID가 올바르지 않습니다.", "요청 확인 필요"
        )


def _validate_submit(request: SubmitBulkFinalizationRequest) -> None:
    _validate_scope(request.project_key, request.batch_id)
    if request.confirmed is not True:
        raise BulkFinalizationValidationError(
            "EXPLICIT_BULK_FINALIZATION_CONFIRMATION_REQUIRED",
            "정상 후보의 Long 반영을 명시적으로 확인해 주세요.",
            "명시적 확인 필요",
        )
    if _SHA256.fullmatch(request.finalization_digest) is None:
        raise BulkFinalizationValidationError(
            "INVALID_FINALIZATION_DIGEST", "후보 근거 해시가 올바르지 않습니다.", "요청 확인 필요"
        )
    if (
        not isinstance(request.reason, str)
        or not request.reason.strip()
        or request.reason != request.reason.strip()
        or len(request.reason) > 1000
    ):
        raise BulkFinalizationValidationError(
            "INVALID_FINALIZATION_REASON", "반영 사유를 정확히 입력해 주세요.", "요청 확인 필요"
        )


def _prepared_metadata_valid(row: BulkEntryRow) -> bool:
    values = (
        row.prepared_checkpoint_sha256,
        row.prepared_checkpoint_version,
        row.prepared_checkpoint_bytes,
    )
    if any(value is None for value in values):
        return False
    return bool(
        _SHA256.fullmatch(cast(str, row.prepared_checkpoint_sha256))
        and row.prepared_checkpoint_version == BULK_PREPARED_CHECKPOINT_VERSION
        and 1 <= cast(int, row.prepared_checkpoint_bytes) <= BULK_PREPARED_CHECKPOINT_MAX_BYTES
    )


def _verify_small_bulk_evidence(row: BulkEntryRow) -> None:
    if row.issues is None or canonical_json_sha256(row.issues) != row.issues_sha256:
        raise ValueError("Bulk issue proof changed")
    for payload, digest, name in (
        (row.receipt_payload, row.receipt_sha256, "receipt"),
        (row.mapping_payload, row.mapping_sha256, "mapping"),
        (row.candidate_payload, row.candidate_sha256, "candidate"),
    ):
        if (payload is None) != (digest is None):
            raise ValueError(f"Bulk {name} proof shape changed")
        if payload is not None and canonical_json_sha256(payload) != digest:
            raise ValueError(f"Bulk {name} proof changed")
    if row.outcome == BulkEntryOutcome.CANDIDATE_READY.value:
        if not all((row.receipt_payload, row.mapping_payload, row.candidate_payload)):
            raise ValueError("candidate-ready Bulk entry lost proof")
        receipt = cast(dict[str, Any], row.receipt_payload)
        candidate = cast(dict[str, Any], row.candidate_payload)
        if (
            receipt.get("receipt_id") != row.reserved_receipt_id
            or receipt.get("content_sha256") != row.upload_sha256
            or receipt.get("size_bytes") != row.size_bytes
            or receipt.get("original_filename") != row.filename
            or candidate.get("state") != "LOAD_CANDIDATE_READY"
            or not isinstance(candidate.get("candidate_digest"), str)
            or _SHA256.fullmatch(cast(str, candidate.get("candidate_digest"))) is None
        ):
            raise ValueError("candidate-ready Bulk entry proof is inconsistent")


def _eligible_payload(item: BulkFinalizationEligibleEntry) -> dict[str, object]:
    return {
        "entry_id": item.entry_id,
        "ordinal": item.ordinal,
        "filename": item.filename,
        "bulk_row_version": item.bulk_row_version,
        "receipt_id": item.receipt_id,
        "content_sha256": item.content_sha256,
        "mapping_sha256": item.mapping_sha256,
        "long_candidate_digest": item.long_candidate_digest,
        "prepared_checkpoint_sha256": item.prepared_checkpoint_sha256,
        "prepared_checkpoint_version": item.prepared_checkpoint_version,
        "prepared_checkpoint_bytes": item.prepared_checkpoint_bytes,
    }


def _excluded_payload(item: BulkFinalizationExcludedEntry) -> dict[str, object]:
    return {
        "entry_id": item.entry_id,
        "ordinal": item.ordinal,
        "filename": item.filename,
        "outcome": item.outcome,
        "status_code": item.status_code,
        "issues_sha256": item.issues_sha256,
        "bulk_row_version": item.bulk_row_version,
        "size_bytes": item.size_bytes,
        "upload_sha256": item.upload_sha256,
        "receipt_id": item.receipt_id,
        "content_sha256": item.content_sha256,
    }


def _candidate_payload_sha(session: Session, project_key: str, entry_id: str) -> str:
    value = session.scalar(
        select(BulkEntryRow.candidate_sha256).where(
            BulkEntryRow.project_key == project_key, BulkEntryRow.id == entry_id
        )
    )
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError("Bulk candidate payload digest is unavailable")
    return value


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _validated_checkpoint(
    plan: BulkFinalizationEntryRow,
    bulk: BulkEntryRow,
) -> dict[str, Any]:
    _verify_small_bulk_evidence(bulk)
    if (
        bulk.outcome != BulkEntryOutcome.CANDIDATE_READY.value
        or bulk.row_version != plan.expected_bulk_row_version
        or bulk.receipt_sha256 is None
        or bulk.mapping_sha256 != plan.expected_mapping_sha256
        or bulk.candidate_sha256 != plan.expected_candidate_payload_sha256
        or bulk.prepared_checkpoint_sha256 != plan.expected_checkpoint_sha256
        or bulk.prepared_checkpoint_version != plan.expected_checkpoint_version
        or bulk.prepared_checkpoint_bytes != plan.expected_checkpoint_bytes
        or bulk.prepared_checkpoint is None
    ):
        raise _conflict("BULK_FINALIZATION_BASIS_STALE")
    checkpoint = bulk.prepared_checkpoint
    if set(checkpoint) != _CHECKPOINT_KEYS:
        raise _conflict("BULK_FINALIZATION_CHECKPOINT_SCHEMA_INVALID")
    if (
        not isinstance(bulk.receipt_payload, dict)
        or checkpoint.get("receipt") != bulk.receipt_payload
        or checkpoint.get("mapping") != bulk.mapping_payload
    ):
        raise _conflict("BULK_FINALIZATION_CHECKPOINT_SOURCE_PROOF_MISMATCH")
    actual_bytes = len(_canonical_bytes(checkpoint))
    if (
        actual_bytes != plan.expected_checkpoint_bytes
        or actual_bytes > BULK_PREPARED_CHECKPOINT_MAX_BYTES
        or canonical_json_sha256(checkpoint) != plan.expected_checkpoint_sha256
        or checkpoint.get("version") != BULK_PREPARED_CHECKPOINT_VERSION
        or checkpoint.get("loader_version") != LONG_UI_LOADER_VERSION
        or checkpoint.get("scan_contract_version") != LONG_UI_SCAN_CONTRACT_VERSION
    ):
        raise _conflict("BULK_FINALIZATION_CHECKPOINT_INTEGRITY_ERROR")
    mapping = checkpoint.get("mapping")
    candidate = checkpoint.get("long_candidate")
    if not isinstance(mapping, dict) or not isinstance(candidate, dict):
        raise _conflict("BULK_FINALIZATION_CHECKPOINT_SCHEMA_INVALID")
    if (
        canonical_json_sha256(mapping) != checkpoint.get("mapping_sha256")
        or checkpoint.get("mapping_sha256") != plan.expected_mapping_sha256
        or canonical_json_sha256(candidate) != checkpoint.get("long_candidate_sha256")
        or checkpoint.get("long_candidate_digest") != plan.expected_long_candidate_digest
    ):
        raise _conflict("BULK_FINALIZATION_CHECKPOINT_NESTED_DIGEST_MISMATCH")
    provenance = candidate.get("provenance")
    if not isinstance(provenance, dict) or not isinstance(
        provenance.get("binding_selections"), list
    ):
        raise _conflict("BULK_FINALIZATION_CHECKPOINT_SCHEMA_INVALID")
    if canonical_json_sha256(provenance["binding_selections"]) != checkpoint.get(
        "binding_selections_sha256"
    ):
        raise _conflict("BULK_FINALIZATION_BINDING_PROOF_MISMATCH")
    return checkpoint


def _receipt(checkpoint: dict[str, Any]) -> SourceFileReceipt:
    payload = checkpoint.get("receipt")
    expected = {
        "receipt_id",
        "project_key",
        "blob_id",
        "content_sha256",
        "received_at",
        "original_filename",
        "model_candidates",
        "lot_candidates",
        "declared_mime_type",
        "detected_mime_type",
        "canonical_extension",
        "size_bytes",
    }
    if not isinstance(payload, dict) or set(payload) != expected:
        raise _conflict("BULK_FINALIZATION_RECEIPT_SCHEMA_INVALID")
    try:
        received_at_text = _receipt_text(payload, "received_at", 80)
        received_at = datetime.fromisoformat(received_at_text)
        model_candidates = _receipt_text_list(payload, "model_candidates")
        lot_candidates = _receipt_text_list(payload, "lot_candidates")
        size_bytes = payload["size_bytes"]
        if isinstance(size_bytes, bool) or not isinstance(size_bytes, int) or size_bytes < 1:
            raise ValueError("invalid receipt size")
        receipt = SourceFileReceipt(
            receipt_id=_receipt_text(payload, "receipt_id", 200),
            project_key=_receipt_text(payload, "project_key", 64),
            blob_id=_receipt_text(payload, "blob_id", 500),
            content_sha256=_receipt_sha(payload, "content_sha256"),
            received_at=received_at,
            original_filename=_receipt_text(payload, "original_filename", 500),
            model_candidates=model_candidates,
            lot_candidates=lot_candidates,
            declared_mime_type=_receipt_text(payload, "declared_mime_type", 200),
            detected_mime_type=_receipt_text(payload, "detected_mime_type", 200),
            canonical_extension=_receipt_text(payload, "canonical_extension", 16),
            size_bytes=size_bytes,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise _conflict("BULK_FINALIZATION_RECEIPT_SCHEMA_INVALID") from error
    if receipt.received_at.tzinfo is None or receipt.received_at.utcoffset() is None:
        raise _conflict("BULK_FINALIZATION_RECEIPT_SCHEMA_INVALID")
    return receipt


def _receipt_text(payload: dict[str, Any], key: str, limit: int) -> str:
    value = payload.get(key)
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > limit
        or "\x00" in value
    ):
        raise ValueError(f"invalid receipt {key}")
    return value


def _receipt_sha(payload: dict[str, Any], key: str) -> str:
    value = _receipt_text(payload, key, 64)
    if _SHA256.fullmatch(value) is None:
        raise ValueError(f"invalid receipt {key}")
    return value


def _receipt_text_list(payload: dict[str, Any], key: str) -> tuple[str, ...]:
    values = payload.get(key)
    if not isinstance(values, list) or len(values) > 100:
        raise ValueError(f"invalid receipt {key}")
    result = tuple(
        value
        for value in values
        if isinstance(value, str)
        and value
        and value == value.strip()
        and len(value) <= 500
        and "\x00" not in value
    )
    if len(result) != len(values) or len(set(result)) != len(result):
        raise ValueError(f"invalid receipt {key}")
    return result


def _scan(checkpoint: dict[str, Any]) -> WorkbookScan:
    payload = checkpoint.get("scan")
    if not isinstance(payload, dict):
        raise _conflict("BULK_FINALIZATION_SCAN_SCHEMA_INVALID")
    return deserialize_workbook_scan(cast(dict[str, object], payload))


def _verify_rebuilt_checkpoint(
    plan: BulkFinalizationEntryRow,
    checkpoint: dict[str, Any],
    rebuilt: LongWorkflowResult,
) -> None:
    serialized = serialize_long_candidate(rebuilt.candidate.candidate)
    mapping = _mapping_payload(rebuilt)
    provenance = serialized.get("provenance")
    if not isinstance(provenance, dict):
        raise _conflict("BULK_FINALIZATION_REBUILT_PROVENANCE_INVALID")
    if (
        rebuilt.candidate.candidate_digest != plan.expected_long_candidate_digest
        or serialized != checkpoint.get("long_candidate")
        or canonical_json_sha256(serialized) != checkpoint.get("long_candidate_sha256")
        or mapping != checkpoint.get("mapping")
        or canonical_json_sha256(mapping) != plan.expected_mapping_sha256
        or canonical_json_sha256(provenance.get("binding_selections"))
        != checkpoint.get("binding_selections_sha256")
    ):
        raise _conflict("BULK_FINALIZATION_PREPARED_CANDIDATE_STALE")


def _mapping_payload(result: LongWorkflowResult) -> dict[str, object]:
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


def _locked_plan(session: Session, plan: BulkFinalizationEntryRow) -> BulkFinalizationEntryRow:
    row = session.scalar(
        select(BulkFinalizationEntryRow)
        .where(
            BulkFinalizationEntryRow.project_key == plan.project_key,
            BulkFinalizationEntryRow.id == plan.id,
            BulkFinalizationEntryRow.status == BulkFinalizationEntryStatus.PROCESSING.value,
            BulkFinalizationEntryRow.row_version == plan.row_version,
        )
        .with_for_update()
    )
    if row is None:
        raise _conflict("BULK_FINALIZATION_ENTRY_CAS_CONFLICT")
    return row


def _snapshot(
    command: BulkFinalizationCommandRow,
    rows: tuple[BulkFinalizationEntryRow, ...],
) -> BulkFinalizationSnapshot:
    if len(rows) != command.entry_count:
        raise ValueError("Bulk finalization immutable plan count changed")
    labels = {
        "QUEUED": "반영 대기",
        "PROCESSING": "정상 후보 반영 중",
        "COMPLETED": "정상 후보 Long 반영 완료",
        "BLOCKED": "정상 후보 반영 보류",
        "PENDING": "대기",
        "BLOCKED_ENTRY": "보류",
    }
    entries = tuple(
        BulkFinalizationEntrySnapshot(
            entry_id=row.id,
            bulk_entry_id=row.bulk_entry_id,
            ordinal=row.ordinal,
            status=BulkFinalizationEntryStatus(row.status),
            status_label=(
                labels["BLOCKED_ENTRY"]
                if row.status == "BLOCKED"
                else labels.get(row.status, row.status)
            ),
            attempt_count=row.attempt_count,
            row_version=row.row_version,
            long_source_file_id=row.long_source_file_id,
            long_ingestion_job_id=row.long_ingestion_job_id,
            long_status=row.long_status,
            long_row_version=row.long_row_version,
            replayed=row.replayed,
            error_code=row.error_code,
        )
        for row in rows
    )
    summary = BulkFinalizationSummary(
        total=len(rows),
        pending=sum(row.status == "PENDING" for row in rows),
        processing=sum(row.status == "PROCESSING" for row in rows),
        completed=sum(row.status == "COMPLETED" for row in rows),
        blocked=sum(row.status == "BLOCKED" for row in rows),
    )
    return BulkFinalizationSnapshot(
        command_id=command.id,
        batch_id=command.batch_id,
        project_key=command.project_key,
        supplier_scope=command.supplier_scope,
        status=BulkFinalizationStatus(command.status),
        status_label=labels[command.status],
        message=(
            "선택 가능한 정상 후보만 Long의 PENDING/HELD 상태로 반영했습니다. "
            "초기 DB Gate 완료나 VALID 확정을 의미하지 않습니다."
            if command.status == "COMPLETED"
            else "정상 후보의 명시적 Long 반영 상태를 확인합니다."
        ),
        finalization_digest=command.finalization_digest,
        reason=command.reason,
        row_version=command.row_version,
        created_at=command.created_at,
        updated_at=command.updated_at,
        finished_at=command.finished_at,
        entries=entries,
        summary=summary,
    )


def _conflict(code: str) -> BulkFinalizationConflictError:
    return BulkFinalizationConflictError(
        code,
        "저장된 준비 근거가 명시적으로 확인한 후보와 일치하지 않습니다.",
        "반영 근거 확인 필요",
    )


def _unavailable(code: str) -> BulkFinalizationUnavailableError:
    return BulkFinalizationUnavailableError(
        code,
        "정상 후보 반영 서비스를 안전하게 완료할 수 없습니다.",
        "서비스 확인 필요",
    )


def _safe_cause(error: BaseException, stage: str) -> dict[str, str]:
    cause_type = f"{type(error).__module__}.{type(error).__qualname__}"
    return {
        "stage": stage,
        "cause_type": cause_type,
        "cause_type_sha256": sha256(cause_type.encode("utf-8")).hexdigest(),
    }
