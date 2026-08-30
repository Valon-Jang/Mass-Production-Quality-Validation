from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, date, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, inspect, select, text

import app.application.bulk_finalization as finalization_module
from app.api.bulk_finalization import create_bulk_finalization_router
from app.application.bulk_finalization import (
    BulkFinalizationConflictError,
    BulkFinalizationManager,
    BulkFinalizationUnavailableError,
    SubmitBulkFinalizationRequest,
)
from app.application.long_workflow import (
    LONG_UI_LOADER_VERSION,
    LONG_UI_SCAN_CONTRACT_VERSION,
)
from app.domain.bulk_finalization import BulkFinalizationStatus
from app.domain.workbook_scan import (
    DisplayValueStatus,
    MacroHandling,
    WorkbookScan,
    WorkbookScanState,
)
from app.infrastructure.audit import AuditLog
from app.infrastructure.bulk_finalization import (
    BulkFinalizationCommandRow,
    BulkFinalizationEntryRow,
)
from app.infrastructure.bulk_import import (
    BULK_PREPARED_CHECKPOINT_VERSION,
    BulkBatchRow,
    BulkEntryRow,
)
from app.infrastructure.database import Base, Database
from app.infrastructure.long_format import (
    LongIngestionJobRow,
    LongJobStatus,
    LongSourceFileRow,
    build_applied_mapping_proof,
    canonical_json_sha256,
    serialize_workbook_scan,
)
from app.infrastructure.mapping_templates import (
    MappingTemplateHistoryRow,
    MappingTemplateRevisionRow,
)
from app.infrastructure.schema import SCHEMA_HEAD_REVISION

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
PROJECT = "bulk-final-project"
SUPPLIER = "supplier-alpha"
MAPPING_SHA = "a" * 64
LONG_DIGEST = hashlib.sha256(b"long-candidate").hexdigest()
SOURCE_ID = "source-final"
JOB_ID = "job-final"
MAPPING_REVISION_ID = "mapping-final"


class _SerializedCandidate:
    def __init__(self, serialized: dict[str, Any]) -> None:
        self.serialized = serialized


class _PreparedWorkflow:
    def __init__(self) -> None:
        self.candidate_calls = 0
        self.confirm_calls = 0

    def _response(self, request: Any, *, persisted: bool) -> Any:
        serialized = _long_payload()
        proof = SimpleNamespace(
            template_id="template-final",
            revision=1,
            payload_sha256=MAPPING_SHA,
            effective_from=date(2026, 1, 1),
            effective_to=None,
            history_row_version=3,
            revision_row_version=3,
        )
        candidate = SimpleNamespace(
            candidate=_SerializedCandidate(serialized),
            candidate_digest=LONG_DIGEST,
            mapping_proof=proof,
        )
        persistence = None
        if persisted:
            persistence = SimpleNamespace(
                source_file_id=SOURCE_ID,
                ingestion_job_id=JOB_ID,
                status=LongJobStatus.COMPLETED_PENDING,
                row_version=1,
                replayed=False,
            )
        return SimpleNamespace(candidate=candidate, persistence=persistence)

    def candidate_prepared(self, request: Any, *, receipt: Any, scan: Any) -> Any:
        assert receipt.project_key == PROJECT
        assert scan.source_sha256_before == receipt.content_sha256
        self.candidate_calls += 1
        return self._response(request, persisted=False)

    def confirm_prepared(self, request: Any, *, receipt: Any, scan: Any) -> Any:
        assert request.confirmed is True
        assert scan.source_sha256_after == receipt.content_sha256
        self.confirm_calls += 1
        return self._response(request, persisted=True)


@pytest.fixture(autouse=True)
def _serialize_fake_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        finalization_module,
        "serialize_long_candidate",
        lambda candidate: cast(_SerializedCandidate, candidate).serialized,
    )


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _long_payload() -> dict[str, Any]:
    return {
        "state": "LOAD_CANDIDATE_READY",
        "provenance": {
            "binding_selections": [{"row_key": "row-1", "binding_revision": 1}],
        },
        "rows": [],
        "official_values_created": False,
        "calculations_performed": False,
    }


def _mapping_payload() -> dict[str, Any]:
    return {
        "template_id": "template-final",
        "revision": 1,
        "template_sha256": MAPPING_SHA,
        "effective_from": "2026-01-01",
        "effective_to": None,
        "history_row_version": 3,
        "revision_row_version": 3,
    }


def _receipt(receipt_id: str, digest: str, filename: str, size: int) -> dict[str, Any]:
    return {
        "receipt_id": receipt_id,
        "project_key": PROJECT,
        "blob_id": f"sha256:{digest}",
        "content_sha256": digest,
        "received_at": NOW.isoformat(),
        "original_filename": filename,
        "model_candidates": [],
        "lot_candidates": [],
        "declared_mime_type": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        "detected_mime_type": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        "canonical_extension": ".xlsx",
        "size_bytes": size,
    }


def _checkpoint(receipt: dict[str, Any]) -> dict[str, Any]:
    scan = WorkbookScan(
        state=WorkbookScanState.SCANNED,
        source_name=cast(str, receipt["original_filename"]),
        source_size_bytes=cast(int, receipt["size_bytes"]),
        source_sha256_before=cast(str, receipt["content_sha256"]),
        source_sha256_after=cast(str, receipt["content_sha256"]),
        sheets=(),
        issues=(),
        estimated_cells=0,
        external_link_count=0,
        macro_handling=MacroHandling.NOT_APPLICABLE,
        display_value_contract=DisplayValueStatus.NOT_RENDERED,
    )
    candidate = _long_payload()
    mapping = _mapping_payload()
    selections = candidate["provenance"]["binding_selections"]
    return {
        "version": BULK_PREPARED_CHECKPOINT_VERSION,
        "loader_version": LONG_UI_LOADER_VERSION,
        "scan_contract_version": LONG_UI_SCAN_CONTRACT_VERSION,
        "receipt": receipt,
        "scan": serialize_workbook_scan(scan),
        "mapping": mapping,
        "mapping_sha256": canonical_json_sha256(mapping),
        "binding_selections_sha256": canonical_json_sha256(selections),
        "long_candidate": candidate,
        "long_candidate_digest": LONG_DIGEST,
        "long_candidate_sha256": canonical_json_sha256(candidate),
    }


def _database(tmp_path: Path) -> Database:
    database = Database(f"sqlite+pysqlite:///{(tmp_path / 'final.sqlite3').as_posix()}")
    Base.metadata.create_all(database.engine)
    _seed_long_parent(database)
    return database


def _seed_long_parent(database: Database) -> None:
    candidate_snapshot = {
        "provenance": {
            "receipt": {
                "project_key": PROJECT,
                "receipt_id": "long-receipt",
                "content_sha256": "f" * 64,
            },
            "supplier_scope": SUPPLIER,
            "template_id": "template-final",
            "template_schema_version": "2",
            "template_revision": 1,
            "template_effective_from": "2026-01-01",
            "template_effective_to": None,
        }
    }
    candidate_sha256 = canonical_json_sha256(candidate_snapshot)
    applied_mapping_proof = build_applied_mapping_proof(
        project_key=PROJECT,
        source_file_id=SOURCE_ID,
        receipt_id="long-receipt",
        content_sha256="f" * 64,
        mapping_template_revision_id=MAPPING_REVISION_ID,
        mapping_payload_sha256=MAPPING_SHA,
        candidate_snapshot=candidate_snapshot,
        candidate_snapshot_sha256=candidate_sha256,
    )
    with database.session() as session, session.begin():
        session.add(
            MappingTemplateHistoryRow(
                id="mapping-history-final",
                project_key=PROJECT,
                supplier_scope=SUPPLIER,
                template_id="template-final",
                row_version=3,
                created_at=NOW,
            )
        )
        session.add(
            MappingTemplateRevisionRow(
                id=MAPPING_REVISION_ID,
                history_id="mapping-history-final",
                revision_number=1,
                schema_version="2",
                status="APPROVED",
                template_payload={},
                payload_sha256=MAPPING_SHA,
                declared_effective_from=date(2026, 1, 1),
                declared_effective_to=None,
                resolved_effective_to=None,
                reviewed_by="reviewer",
                reviewed_at=NOW,
                approved_by="admin",
                approved_at=NOW,
                row_version=3,
                created_at=NOW,
            )
        )
        session.add(
            LongSourceFileRow(
                id=SOURCE_ID,
                project_key=PROJECT,
                receipt_id="long-receipt",
                blob_id="sha256:" + "f" * 64,
                content_sha256="f" * 64,
                received_at=NOW,
                original_filename="long.xlsx",
                model_candidates=[],
                lot_candidates=[],
                declared_mime_type="application/xlsx",
                detected_mime_type="application/xlsx",
                canonical_extension=".xlsx",
                size_bytes=1,
                parse_status="SCANNED",
                scan_source_name="long.xlsx",
                scan_source_size_bytes=1,
                scan_sha256_before="f" * 64,
                scan_sha256_after="f" * 64,
                scan_contract_version="workbook-scan-v1",
                estimated_cells=0,
                external_link_count=0,
                macro_handling="NOT_APPLICABLE",
                display_value_contract="NOT_RENDERED",
                is_golden_workbook_evidence=False,
                scan_issues=[],
                row_version=1,
                created_at=NOW,
            )
        )
        session.flush()
        session.add(
            LongIngestionJobRow(
                id=JOB_ID,
                project_key=PROJECT,
                source_file_id=SOURCE_ID,
                content_sha256="f" * 64,
                mapping_template_revision_id=MAPPING_REVISION_ID,
                mapping_payload_sha256=MAPPING_SHA,
                binding_catalog_revision="catalog-v1",
                binding_fingerprint="b" * 64,
                loader_version="long-ui-v1",
                scan_contract_version="workbook-scan-v1",
                idempotency_key="i" * 64,
                materialization_fingerprint="m" * 64,
                owns_materialization=True,
                reused_job_id=None,
                blocking_job_id=None,
                status="COMPLETED_PENDING",
                started_at=NOW,
                finished_at=NOW,
                lot_count=1,
                result_count=1,
                measurement_count=1,
                held_result_count=0,
                error_code=None,
                error_summary=None,
                issues=[],
                candidate_snapshot=candidate_snapshot,
                candidate_snapshot_sha256=candidate_sha256,
                applied_mapping_proof=applied_mapping_proof,
                applied_mapping_proof_sha256=canonical_json_sha256(applied_mapping_proof),
                row_version=1,
            )
        )


def _seed_batch(
    database: Database,
    token: str,
    *,
    legacy: bool = False,
    excluded: bool = False,
) -> str:
    batch_id = f"batch-{token}"
    filename = f"{token}.xlsx"
    content = token.encode("utf-8")
    digest = hashlib.sha256(content).hexdigest()
    receipt_id = hashlib.md5(token.encode("utf-8")).hexdigest()
    receipt = _receipt(receipt_id, digest, filename, len(content))
    checkpoint = None if legacy or excluded else _checkpoint(receipt)
    mapping = _mapping_payload() if not excluded else None
    long_digest = LONG_DIGEST
    candidate = (
        {"state": "LOAD_CANDIDATE_READY", "candidate_digest": long_digest} if not excluded else None
    )
    issues: list[dict[str, object]] = []
    summary = {
        "total": 1,
        "processing": 0,
        "candidate_ready": 0 if excluded else 1,
        "duplicate": 0,
        "variation": 0,
        "mapping_required": 1 if excluded else 0,
        "scan_failed": 0,
        "revision_review_required": 0,
        "identifier_hold": 0,
        "binding_hold": 0,
        "error": 0,
    }
    with database.session() as session, session.begin():
        session.add(
            BulkBatchRow(
                id=batch_id,
                project_key=PROJECT,
                supplier_scope=SUPPLIER,
                idempotency_key=f"idempotency-{token}",
                manifest_sha256=hashlib.sha256(f"manifest-{token}".encode()).hexdigest(),
                status=("COMPLETED_WITH_EXCEPTIONS" if excluded else "COMPLETED"),
                entry_count=1,
                terminal_summary=summary,
                terminal_summary_sha256=canonical_json_sha256(summary),
                row_version=2,
                created_at=NOW,
                updated_at=NOW,
                finished_at=NOW,
            )
        )
        session.add(
            BulkEntryRow(
                id=f"entry-{token}",
                project_key=PROJECT,
                batch_id=batch_id,
                ordinal=0,
                reserved_receipt_id=receipt_id,
                reserved_received_at=NOW,
                filename=filename,
                mime_type=cast(str, receipt["declared_mime_type"]),
                size_bytes=len(content),
                upload_sha256=digest,
                staged_relative_path=None,
                status="TERMINAL",
                outcome=("MAPPING_REQUIRED" if excluded else "CANDIDATE_READY"),
                status_code=("BULK_MAPPING_REQUIRED" if excluded else "BULK_CANDIDATE_READY"),
                message="synthetic",
                attempt_count=1,
                receipt_payload=receipt,
                receipt_sha256=canonical_json_sha256(receipt),
                mapping_payload=mapping,
                mapping_sha256=(canonical_json_sha256(mapping) if mapping else None),
                candidate_payload=candidate,
                candidate_sha256=(canonical_json_sha256(candidate) if candidate else None),
                revision_identity=None,
                revision_evidence=None,
                revision_evidence_sha256=None,
                prepared_checkpoint=checkpoint,
                prepared_checkpoint_sha256=(
                    canonical_json_sha256(checkpoint) if checkpoint else None
                ),
                prepared_checkpoint_version=(
                    BULK_PREPARED_CHECKPOINT_VERSION if checkpoint else None
                ),
                prepared_checkpoint_bytes=(
                    len(_canonical_bytes(checkpoint)) if checkpoint else None
                ),
                issues=issues,
                issues_sha256=canonical_json_sha256(issues),
                duplicate_of_entry_id=None,
                revision_baseline_entry_id=None,
                row_version=4,
                created_at=NOW,
                updated_at=NOW,
                finished_at=NOW,
            )
        )
    return batch_id


def _append_excluded_entry(database: Database, batch_id: str, token: str) -> str:
    content = f"excluded-{token}".encode()
    digest = hashlib.sha256(content).hexdigest()
    receipt_id = hashlib.md5(content).hexdigest()
    receipt = _receipt(receipt_id, digest, f"excluded-{token}.xlsx", len(content))
    issues: list[dict[str, object]] = [
        {
            "code": "BULK_MAPPING_REQUIRED",
            "category": "MAPPING",
            "severity": "HOLD",
            "message": "mapping review required",
            "location": "OQC!C8",
            "evidence_path": "sheet:OQC/cell:C8",
            "expected": None,
            "observed": None,
        }
    ]
    entry_id = f"entry-excluded-{token}"
    with database.session() as session, session.begin():
        batch = session.get(BulkBatchRow, batch_id)
        assert batch is not None and batch.terminal_summary is not None
        summary = dict(batch.terminal_summary)
        summary["total"] = 2
        summary["mapping_required"] = 1
        batch.entry_count = 2
        batch.status = "COMPLETED_WITH_EXCEPTIONS"
        batch.terminal_summary = summary
        batch.terminal_summary_sha256 = canonical_json_sha256(summary)
        batch.row_version += 1
        session.add(
            BulkEntryRow(
                id=entry_id,
                project_key=PROJECT,
                batch_id=batch_id,
                ordinal=1,
                reserved_receipt_id=receipt_id,
                reserved_received_at=NOW,
                filename=cast(str, receipt["original_filename"]),
                mime_type=cast(str, receipt["declared_mime_type"]),
                size_bytes=len(content),
                upload_sha256=digest,
                staged_relative_path=None,
                status="TERMINAL",
                outcome="MAPPING_REQUIRED",
                status_code="BULK_MAPPING_REQUIRED",
                message="mapping review required",
                attempt_count=1,
                receipt_payload=receipt,
                receipt_sha256=canonical_json_sha256(receipt),
                mapping_payload=None,
                mapping_sha256=None,
                candidate_payload=None,
                candidate_sha256=None,
                revision_identity=None,
                revision_evidence=None,
                revision_evidence_sha256=None,
                prepared_checkpoint=None,
                prepared_checkpoint_sha256=None,
                prepared_checkpoint_version=None,
                prepared_checkpoint_bytes=None,
                issues=issues,
                issues_sha256=canonical_json_sha256(issues),
                duplicate_of_entry_id=None,
                revision_baseline_entry_id=None,
                row_version=4,
                created_at=NOW,
                updated_at=NOW,
                finished_at=NOW,
            )
        )
    return entry_id


def _request(candidate: Any) -> SubmitBulkFinalizationRequest:
    return SubmitBulkFinalizationRequest(
        project_key=PROJECT,
        batch_id=candidate.batch_id,
        finalization_digest=candidate.finalization_digest,
        confirmed=True,
        reason="과거 정상 후보의 PENDING Long 반영",
    )


def _terminal(manager: BulkFinalizationManager, batch_id: str) -> Any:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        snapshot = manager.get(project_key=PROJECT, batch_id=batch_id)
        if snapshot.terminal:
            return snapshot
        time.sleep(0.01)
    raise AssertionError("finalization did not reach a terminal state")


@pytest.mark.required_test_id("DQ-P2-BULKFINAL-001")
@pytest.mark.required_test_id("DQ-P2-BULKFINALUI-001")
def test_candidate_is_batch_wide_read_only_and_preserves_exclusions(tmp_path: Path) -> None:
    database = _database(tmp_path)
    try:
        batch_id = _seed_batch(database, "candidate")
        workflow = _PreparedWorkflow()
        manager = BulkFinalizationManager(database=database, long_workflow=workflow)
        with database.session() as session:
            before = (
                session.scalar(select(func.count()).select_from(BulkFinalizationCommandRow)),
                session.scalar(select(func.count()).select_from(BulkFinalizationEntryRow)),
            )
        candidate = manager.candidate(project_key=PROJECT, batch_id=batch_id)
        with database.session() as session:
            after = (
                session.scalar(select(func.count()).select_from(BulkFinalizationCommandRow)),
                session.scalar(select(func.count()).select_from(BulkFinalizationEntryRow)),
            )
        assert before == after == (0, 0)
        assert candidate.can_finalize and len(candidate.eligible_entries) == 1
        proof = candidate.eligible_entries[0]
        assert proof.prepared_checkpoint_version == BULK_PREPARED_CHECKPOINT_VERSION
        assert proof.prepared_checkpoint_bytes > 0
        assert workflow.candidate_calls == workflow.confirm_calls == 0
    finally:
        database.dispose()


@pytest.mark.required_test_id("DQ-P2-BULKFINAL-002")
@pytest.mark.required_test_id("DQ-P2-BULKFINALUI-002")
def test_explicit_submit_is_durable_async_and_audited(tmp_path: Path) -> None:
    database = _database(tmp_path)
    try:
        batch_id = _seed_batch(database, "submit")
        manager = BulkFinalizationManager(database=database, long_workflow=_PreparedWorkflow())
        application = FastAPI()
        application.include_router(create_bulk_finalization_router(manager))
        with TestClient(application) as client:
            missing_scope = client.get(f"/api/v1/bulk/batches/{batch_id}/finalization-candidate")
            assert missing_scope.status_code == 400
            assert set(missing_scope.json()["detail"]) == {
                "code",
                "message",
                "status_label",
            }
            missing = client.get(
                "/api/v1/bulk/batches/missing/finalization-candidate",
                params={"project_key": PROJECT},
            )
            assert missing.status_code == 404

            candidate_response = client.get(
                f"/api/v1/bulk/batches/{batch_id}/finalization-candidate",
                params={"project_key": PROJECT},
            )
            assert candidate_response.status_code == 200
            candidate_payload = candidate_response.json()
            assert candidate_payload["eligible_count"] == 1
            assert candidate_payload["excluded_count"] == 0
            assert candidate_payload["capabilities"]["batch_wide_only"] is True
            assert candidate_payload["capabilities"]["auto_valid"] is False

            stale = client.post(
                f"/api/v1/bulk/batches/{batch_id}/finalizations",
                json={
                    "project_key": PROJECT,
                    "finalization_digest": "0" * 64,
                    "confirmed": True,
                    "reason": "explicit stale intent",
                },
            )
            assert stale.status_code == 409
            accepted = client.post(
                f"/api/v1/bulk/batches/{batch_id}/finalizations",
                json={
                    "project_key": PROJECT,
                    "finalization_digest": candidate_payload["finalization_digest"],
                    "confirmed": True,
                    "reason": "explicit batch-wide PENDING Long materialization",
                },
            )
            assert accepted.status_code == 202
            assert accepted.json()["status"] == "QUEUED"
            assert accepted.json()["summary"]["pending"] == 1
            persisted = client.get(
                f"/api/v1/bulk/batches/{batch_id}/finalizations",
                params={"project_key": PROJECT},
            )
            assert persisted.status_code == 200
            assert persisted.json()["command_id"] == accepted.json()["command_id"]
        with database.session() as session:
            audit = session.scalar(
                select(AuditLog).where(AuditLog.action == "bulk_finalization_requested")
            )
            assert audit is not None and audit.actor_id == "local-owner"
            assert cast(dict[str, object], audit.after_state)["auto_valid"] is False
    finally:
        database.dispose()


@pytest.mark.required_test_id("DQ-P2-BULKFINAL-003")
@pytest.mark.required_test_id("DQ-P2-BULKFINALUI-003")
def test_worker_materializes_only_successful_pending_long_and_system_audits(tmp_path: Path) -> None:
    database = _database(tmp_path)
    manager: BulkFinalizationManager | None = None
    try:
        batch_id = _seed_batch(database, "success")
        excluded_entry_id = _append_excluded_entry(database, batch_id, "success")
        workflow = _PreparedWorkflow()
        manager = BulkFinalizationManager(database=database, long_workflow=workflow)
        candidate = manager.candidate(project_key=PROJECT, batch_id=batch_id)
        assert len(candidate.eligible_entries) == 1
        assert len(candidate.excluded_entries) == 1
        assert candidate.excluded_entries[0].entry_id == excluded_entry_id
        manager.submit(_request(candidate))
        manager.start()
        snapshot = _terminal(manager, batch_id)
        assert snapshot.status == BulkFinalizationStatus.COMPLETED
        assert snapshot.summary.total == snapshot.summary.completed == 1
        assert len(snapshot.entries) == 1
        assert snapshot.entries[0].long_status == "COMPLETED_PENDING"
        assert workflow.candidate_calls == workflow.confirm_calls == 1
        with database.session() as session:
            excluded = session.get(BulkEntryRow, excluded_entry_id)
            assert excluded is not None
            assert excluded.outcome == "MAPPING_REQUIRED"
            assert excluded.row_version == 4
            system_actions = tuple(
                session.scalars(
                    select(AuditLog).where(
                        AuditLog.action.in_(
                            (
                                "bulk_finalization_entry_materialized",
                                "bulk_finalization_finished",
                            )
                        )
                    )
                )
            )
            assert {row.actor_id for row in system_actions} == {
                "mass-production-quality-validation-system"
            }
            assert "VALID" not in json.dumps(
                [row.after_state for row in system_actions], ensure_ascii=False
            )
    finally:
        if manager is not None:
            manager.shutdown()
        database.dispose()


@pytest.mark.required_test_id("DQ-P2-BULKFINAL-004")
def test_checkpoint_tamper_and_legacy_candidate_fail_closed(tmp_path: Path) -> None:
    database = _database(tmp_path)
    manager: BulkFinalizationManager | None = None
    try:
        legacy = _seed_batch(database, "legacy", legacy=True)
        manager = BulkFinalizationManager(database=database, long_workflow=_PreparedWorkflow())
        candidate = manager.candidate(project_key=PROJECT, batch_id=legacy)
        assert not candidate.can_finalize
        assert candidate.excluded_entries[0].status_code == (
            "BULK_FINALIZATION_PREPARATION_REQUIRED"
        )
        with pytest.raises(BulkFinalizationConflictError):
            manager.submit(
                SubmitBulkFinalizationRequest(
                    PROJECT,
                    legacy,
                    candidate.finalization_digest,
                    True,
                    "legacy cannot finalize",
                )
            )

        tampered = _seed_batch(database, "tampered")
        prepared = manager.candidate(project_key=PROJECT, batch_id=tampered)
        manager.submit(_request(prepared))
        with database.session() as session, session.begin():
            row = session.scalar(select(BulkEntryRow).where(BulkEntryRow.batch_id == tampered))
            assert row is not None and row.prepared_checkpoint is not None
            changed = dict(row.prepared_checkpoint)
            changed["loader_version"] = "forged"
            row.prepared_checkpoint = changed
        manager.start()
        blocked = _terminal(manager, tampered)
        assert blocked.status == BulkFinalizationStatus.BLOCKED
        assert blocked.entries[0].error_code is not None

        manager.shutdown()
        missing_plan = _seed_batch(database, "missing-plan")
        manager = BulkFinalizationManager(database=database, long_workflow=_PreparedWorkflow())
        missing_candidate = manager.candidate(project_key=PROJECT, batch_id=missing_plan)
        manager.submit(_request(missing_candidate))
        with database.session() as session, session.begin():
            plan = session.scalar(
                select(BulkFinalizationEntryRow).where(
                    BulkFinalizationEntryRow.batch_id == missing_plan
                )
            )
            assert plan is not None
            session.delete(plan)
        with pytest.raises(BulkFinalizationUnavailableError) as missing_error:
            manager.get(project_key=PROJECT, batch_id=missing_plan)
        assert missing_error.value.code == "BULK_FINALIZATION_READ_UNAVAILABLE"

        class _Unavailable:
            def candidate(self, *, project_key: str, batch_id: str) -> Any:
                del project_key, batch_id
                raise BulkFinalizationUnavailableError(
                    "BULK_FINALIZATION_SERVICE_UNAVAILABLE",
                    "service is unavailable",
                    "service unavailable",
                )

            def submit(self, request: SubmitBulkFinalizationRequest) -> Any:
                del request
                raise AssertionError("unreachable")

            def get(self, *, project_key: str, batch_id: str) -> Any:
                del project_key, batch_id
                raise AssertionError("unreachable")

        unavailable_app = FastAPI()
        unavailable_app.include_router(create_bulk_finalization_router(_Unavailable()))
        with TestClient(unavailable_app) as client:
            unavailable = client.get(
                "/api/v1/bulk/batches/unready/finalization-candidate",
                params={"project_key": PROJECT},
            )
        assert unavailable.status_code == 503
        assert unavailable.json()["detail"]["code"] == ("BULK_FINALIZATION_SERVICE_UNAVAILABLE")
    finally:
        if manager is not None:
            manager.shutdown()
        database.dispose()


@pytest.mark.required_test_id("DQ-P2-BULKFINAL-005")
def test_capacity_restart_and_idempotent_replay_do_not_duplicate_commands(tmp_path: Path) -> None:
    database = _database(tmp_path)
    manager: BulkFinalizationManager | None = None
    restarted: BulkFinalizationManager | None = None
    try:
        batch_ids = tuple(_seed_batch(database, f"queue-{index}") for index in range(3))
        workflow = _PreparedWorkflow()
        manager = BulkFinalizationManager(
            database=database, long_workflow=workflow, queue_capacity=1
        )
        candidates = [manager.candidate(project_key=PROJECT, batch_id=item) for item in batch_ids]
        for candidate in candidates:
            manager.submit(_request(candidate))
        manager.start()
        manager.shutdown()
        restarted = BulkFinalizationManager(
            database=database, long_workflow=workflow, queue_capacity=1
        )
        restarted.start()
        snapshots = tuple(_terminal(restarted, item) for item in batch_ids)
        replay = restarted.submit(_request(candidates[0]))
        assert all(item.status == BulkFinalizationStatus.COMPLETED for item in snapshots)
        assert replay.command_id == snapshots[0].command_id
        with database.session() as session:
            assert session.scalar(
                select(func.count()).select_from(BulkFinalizationCommandRow)
            ) == len(batch_ids)
            assert session.scalar(
                select(func.count()).select_from(BulkFinalizationEntryRow)
            ) == len(batch_ids)
    finally:
        if manager is not None:
            manager.shutdown()
        if restarted is not None:
            restarted.shutdown()
        database.dispose()


@pytest.mark.required_test_id("DQ-P2-BULKFINAL-006")
def test_0007_upgrade_metadata_fk_and_downgrade_guard(tmp_path: Path) -> None:
    path = tmp_path / "migration.sqlite3"
    url = f"sqlite+pysqlite:///{path.as_posix()}"
    config = _alembic_config(url)
    command.upgrade(config, "0006")
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO audit_log (id, occurred_at, actor_id, actor_kind, "
                    "actor_roles, action, target_type, target_id, before_state, after_state, "
                    "reason, requirement_id, source_reference) VALUES "
                    "('prior-audit', :now, 'system', 'SYSTEM', '[\"SYSTEM\"]', "
                    "'prior', 'migration', NULL, NULL, NULL, 'preserve', NULL, NULL)"
                ),
                {"now": NOW.replace(tzinfo=None)},
            )
        prior = engine.connect().execute(text("SELECT * FROM audit_log")).mappings().one()
    finally:
        engine.dispose()
    command.upgrade(config, "0007")
    engine = create_engine(url)
    try:
        tables = set(inspect(engine).get_table_names())
        assert {"bulk_finalization_commands", "bulk_finalization_entries"}.issubset(tables)
        assert {
            column["name"] for column in inspect(engine).get_columns("bulk_import_entries")
        } >= {
            "prepared_checkpoint",
            "prepared_checkpoint_sha256",
            "prepared_checkpoint_version",
            "prepared_checkpoint_bytes",
        }
        with engine.connect() as connection:
            assert connection.execute(text("SELECT * FROM audit_log")).mappings().one() == prior
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO bulk_import_batches (id,project_key,supplier_scope,"
                    "idempotency_key,manifest_sha256,status,entry_count,terminal_summary,"
                    "terminal_summary_sha256,row_version,created_at,updated_at,finished_at) "
                    "VALUES ('guard','guard-project','supplier','guard-key',:sha,'STAGED',1,"
                    "NULL,NULL,1,:now,:now,NULL)"
                ),
                {"sha": "a" * 64, "now": NOW.replace(tzinfo=None)},
            )
            connection.execute(
                text(
                    "INSERT INTO bulk_finalization_commands (id,project_key,batch_id,"
                    "supplier_scope,finalization_digest,reason,requested_by,status,entry_count,"
                    "row_version,created_at,updated_at,finished_at) VALUES "
                    "('command','guard-project','guard','supplier',:sha,'reason','owner',"
                    "'QUEUED',1,1,:now,:now,NULL)"
                ),
                {"sha": "b" * 64, "now": NOW.replace(tzinfo=None)},
            )
        with pytest.raises(RuntimeError, match="downgrade is blocked"):
            command.downgrade(config, "0006")
        assert "bulk_finalization_commands" in inspect(engine).get_table_names()
    finally:
        engine.dispose()

    command.upgrade(config, SCHEMA_HEAD_REVISION)
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            assert connection.execute(text("SELECT * FROM audit_log")).mappings().one() == prior
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
            assert (
                compare_metadata(
                    MigrationContext.configure(connection, opts={"compare_type": True}),
                    Base.metadata,
                )
                == []
            )
    finally:
        engine.dispose()

    assert not (tmp_path / ".localdata").exists()


def _alembic_config(url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "migrations"))
    config.set_main_option("prepend_sys_path", str(ROOT / "backend"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config
