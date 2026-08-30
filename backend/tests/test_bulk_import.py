from __future__ import annotations

import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from dataclasses import replace as dataclass_replace
from datetime import UTC, date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from types import SimpleNamespace
from typing import Any, cast

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook  # type: ignore[import-untyped]
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.exc import IntegrityError

import app.application.bulk_import as bulk_module
from app.api.bulk import create_bulk_router
from app.application.bulk_import import (
    BulkImportConflictError,
    BulkImportManager,
    BulkImportValidationError,
    BulkStagedFile,
    BulkSubmitRequest,
)
from app.application.manual_ingestion import (
    ManualIngestionOutcome,
    ManualIngestionStatus,
    ManualIngestionUnexpectedScanError,
    ManualWorkbookIngestionService,
)
from app.domain.bulk_import import (
    BulkBatchSnapshot,
    BulkEntryOutcome,
)
from app.domain.long_format import (
    LongCandidateIssue,
    LongCandidateState,
    LongIssueCode,
    LongIssueScope,
)
from app.domain.mapping import MappingIssue, MappingIssueCode
from app.domain.source_file import SourceFileReceipt
from app.domain.workbook_scan import (
    DisplayValueStatus,
    MacroHandling,
    ScanPolicy,
    WorkbookScan,
    WorkbookScanFailure,
    WorkbookScanState,
)
from app.infrastructure.bulk_import import BulkBatchRow, BulkEntryRow
from app.infrastructure.database import Base, Database
from app.infrastructure.excel import OpenpyxlWorkbookScanner
from app.infrastructure.file_store import XLSX_MIME, OriginalFileStore
from app.infrastructure.long_format import (
    LongIngestionJobRow,
    LongInspectionResultRow,
    LongMeasurementRow,
)
from app.infrastructure.schema import SCHEMA_HEAD_REVISION

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)
PROJECT = "bulk-project"
SUPPLIER = "supplier-alpha"


class _FakeCandidate:
    def __init__(
        self,
        serialized: dict[str, Any],
        *,
        state: LongCandidateState = LongCandidateState.LOAD_CANDIDATE_READY,
        issue: LongCandidateIssue | None = None,
        issues: tuple[LongCandidateIssue, ...] | None = None,
    ) -> None:
        self.serialized = serialized
        self.state = state
        self.loadable_rows = (object(),) if state == LongCandidateState.LOAD_CANDIDATE_READY else ()
        self.held_rows = () if state == LongCandidateState.LOAD_CANDIDATE_READY else (object(),)
        self.issues = issues if issues is not None else (() if issue is None else (issue,))
        self.rows: tuple[Any, ...] = ()


class _FakeIngestion:
    def __init__(self, modes: dict[str, str]) -> None:
        self.modes = modes
        self.calls = 0
        self.scan_calls = 0
        self.receipt_ids: list[str] = []

    def ingest(self, request: Any) -> ManualIngestionOutcome:
        self.calls += 1
        content = request.source.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        receipt = SourceFileReceipt(
            receipt_id=request.reserved_receipt_id,
            project_key=request.project_key,
            blob_id=f"sha256:{digest}",
            content_sha256=digest,
            received_at=request.reserved_received_at,
            original_filename=request.source.name,
            model_candidates=(),
            lot_candidates=(),
            declared_mime_type=request.declared_mime_type,
            detected_mime_type=XLSX_MIME,
            canonical_extension=".xlsx",
            size_bytes=len(content),
        )
        self.receipt_ids.append(receipt.receipt_id)
        if request.on_preserved is not None:
            request.on_preserved(receipt)
        self.scan_calls += 1
        if self.modes.get(digest) == "unexpected_scan":
            raise ManualIngestionUnexpectedScanError(receipt) from RuntimeError(
                r"secret-token at C:\private\source.xlsx"
            )
        if self.modes.get(digest) == "scan_failed":
            failure = cast(
                WorkbookScanFailure,
                SimpleNamespace(status=SimpleNamespace(value="CORRUPT_OOXML")),
            )
            return ManualIngestionOutcome(
                ManualIngestionStatus.RAW_PRESERVED_SCAN_FAILED,
                receipt,
                scan_failure=failure,
            )
        return ManualIngestionOutcome(
            ManualIngestionStatus.STORED_AND_SCANNED,
            receipt,
            scan=WorkbookScan(
                state=WorkbookScanState.SCANNED,
                source_name=receipt.original_filename,
                source_size_bytes=receipt.size_bytes,
                source_sha256_before=receipt.content_sha256,
                source_sha256_after=receipt.content_sha256,
                sheets=(),
                issues=(),
                estimated_cells=0,
                external_link_count=0,
                macro_handling=MacroHandling.NOT_APPLICABLE,
                display_value_contract=DisplayValueStatus.NOT_RENDERED,
            ),
        )


class _FakeMapping:
    def __init__(self, modes: dict[str, str]) -> None:
        self.modes = modes
        self.preview_calls = 0
        self.preview_scanned_calls = 0

    def preview_scanned(self, request: Any, *, receipt: SourceFileReceipt, scan: Any) -> Any:
        self.preview_scanned_calls += 1
        return self._snapshot(request.content_sha256, receipt, scan)

    def preview(self, request: Any) -> Any:
        self.preview_calls += 1
        receipt = SimpleNamespace(
            receipt_id=request.receipt_id,
            project_key=request.project_key,
            content_sha256=request.content_sha256,
        )
        scan = WorkbookScan(
            state=WorkbookScanState.SCANNED,
            source_name="replayed.xlsx",
            source_size_bytes=0,
            source_sha256_before=request.content_sha256,
            source_sha256_after=request.content_sha256,
            sheets=(),
            issues=(),
            estimated_cells=0,
            external_link_count=0,
            macro_handling=MacroHandling.NOT_APPLICABLE,
            display_value_contract=DisplayValueStatus.NOT_RENDERED,
        )
        return self._snapshot(request.content_sha256, receipt, scan)

    def _snapshot(self, digest: str, receipt: Any, scan: WorkbookScan) -> Any:
        if self.modes.get(digest) == "mapping_variation":
            issue = MappingIssue(
                MappingIssueCode.FINGERPRINT_ROW_STRUCTURE_MISMATCH,
                "Synthetic row structure differs.",
                sheet_name="OQC",
                coordinate="A7",
            )
            return SimpleNamespace(
                state=bulk_module.MappingWorkspaceState.MAPPING_REQUIRED,
                issues=(issue,),
                receipt=receipt,
                scan=scan,
            )
        return SimpleNamespace(
            state=bulk_module.MappingWorkspaceState.PREVIEW_READY,
            issues=(),
            receipt=receipt,
            scan=scan,
        )


class _FakeLong:
    def __init__(self, modes: dict[str, str], payloads: dict[str, dict[str, Any]]) -> None:
        self.modes = modes
        self.payloads = payloads
        self.candidate_calls = 0
        self.workspace_calls = 0

    def candidate(self, request: Any) -> Any:
        del request
        self.candidate_calls += 1
        raise AssertionError("Bulk must not rescan through candidate()")

    def candidate_from_workspace(self, request: Any, workspace: Any) -> Any:
        self.workspace_calls += 1
        mode = self.modes.get(request.content_sha256)
        if mode == "worker_exception":
            raise LookupError(r"private detail at C:\internal\worker.db")
        issue = None
        state = LongCandidateState.LOAD_CANDIDATE_READY
        if mode == "identifier_hold":
            state = LongCandidateState.LOAD_HELD
            issue = LongCandidateIssue(
                LongIssueCode.MODEL_IDENTIFIER_MISSING,
                LongIssueScope.SOURCE,
                "Synthetic model identifier is missing.",
            )
        elif mode == "binding_hold":
            state = LongCandidateState.LOAD_HELD
            issue = LongCandidateIssue(
                LongIssueCode.CANONICAL_ROW_BINDING_MISSING,
                LongIssueScope.ROW,
                "Synthetic row binding is missing.",
                row_key="row-1",
            )
        issues = None
        if mode == "many_issues":
            state = LongCandidateState.LOAD_HELD
            issues = tuple(
                LongCandidateIssue(
                    LongIssueCode.CANONICAL_ROW_BINDING_MISSING,
                    LongIssueScope.ROW,
                    f"Synthetic bounded issue {index}.",
                    row_key=f"row-{index}",
                    expected="x" * 8_000,
                    observed=f"value-{index}",
                )
                for index in range(1_000)
            )
        serialized = cast(
            dict[str, Any],
            json.loads(json.dumps(self.payloads[request.content_sha256])),
        )
        receipt = cast(SourceFileReceipt, workspace.receipt)
        provenance = cast(dict[str, Any], serialized["provenance"])
        provenance["receipt"] = {
            "receipt_id": receipt.receipt_id,
            "project_key": receipt.project_key,
            "blob_id": receipt.blob_id,
            "content_sha256": receipt.content_sha256,
            "received_at": receipt.received_at.isoformat(),
            "original_filename": receipt.original_filename,
            "model_candidates": list(receipt.model_candidates),
            "lot_candidates": list(receipt.lot_candidates),
            "declared_mime_type": receipt.declared_mime_type,
            "detected_mime_type": receipt.detected_mime_type,
            "canonical_extension": receipt.canonical_extension,
            "size_bytes": receipt.size_bytes,
        }
        candidate = _FakeCandidate(
            serialized,
            state=state,
            issue=issue,
            issues=issues,
        )
        proof = SimpleNamespace(
            template_id="template-approved",
            revision=1,
            payload_sha256="a" * 64,
            effective_from=date(2026, 1, 1),
            effective_to=None,
            history_row_version=3,
            revision_row_version=3,
        )
        return SimpleNamespace(
            candidate=SimpleNamespace(
                candidate=candidate,
                candidate_digest=hashlib.sha256(request.content_sha256.encode()).hexdigest(),
                mapping_proof=proof,
            ),
            persistence=None,
        )


@dataclass
class _Harness:
    database: Database
    staging: Path
    manager: BulkImportManager
    ingestion: Any
    mapping: _FakeMapping
    long: _FakeLong
    modes: dict[str, str]
    payloads: dict[str, dict[str, Any]]

    def close(self) -> None:
        self.manager.shutdown()
        self.database.dispose()


class _CountingScanner(OpenpyxlWorkbookScanner):
    def __init__(self) -> None:
        self.calls = 0

    def scan_stream(self, source: Any, *, source_name: str, policy: Any = None) -> WorkbookScan:
        self.calls += 1
        return super().scan_stream(source, source_name=source_name, policy=policy)


@pytest.fixture(autouse=True)
def _fake_serializer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        bulk_module,
        "serialize_long_candidate",
        lambda candidate: candidate.serialized,
    )


def _harness(tmp_path: Path, *, start: bool = True, capacity: int = 8) -> _Harness:
    database = Database(f"sqlite+pysqlite:///{(tmp_path / 'bulk.sqlite3').as_posix()}")
    Base.metadata.create_all(database.engine)
    staging = tmp_path / "bulk-staging"
    staging.mkdir()
    modes: dict[str, str] = {}
    payloads: dict[str, dict[str, Any]] = {}
    ingestion = _FakeIngestion(modes)
    mapping = _FakeMapping(modes)
    long = _FakeLong(modes, payloads)
    manager = BulkImportManager(
        database=database,
        ingestion_service=cast(Any, ingestion),
        mapping_workspace=mapping,
        long_workflow=long,
        staging_root=staging,
        max_files=20,
        max_file_bytes=1_000_000,
        max_batch_bytes=5_000_000,
        queue_capacity=capacity,
        scan_policy=ScanPolicy(),
        clock=lambda: NOW,
        receipt_link_retry_delay_seconds=0.01,
    )
    harness = _Harness(database, staging, manager, ingestion, mapping, long, modes, payloads)
    if start:
        manager.start()
    return harness


def _real_harness(tmp_path: Path) -> tuple[_Harness, OriginalFileStore, _CountingScanner]:
    database = Database(f"sqlite+pysqlite:///{(tmp_path / 'real-bulk.sqlite3').as_posix()}")
    Base.metadata.create_all(database.engine)
    staging = tmp_path / "real-bulk-staging"
    staging.mkdir()
    modes: dict[str, str] = {}
    payloads: dict[str, dict[str, Any]] = {}
    store = OriginalFileStore(tmp_path / "real-originals", max_bytes=1_000_000, clock=lambda: NOW)
    scanner = _CountingScanner()
    ingestion = ManualWorkbookIngestionService(file_store=store, scanner=scanner)
    mapping = _FakeMapping(modes)
    long = _FakeLong(modes, payloads)
    manager = BulkImportManager(
        database=database,
        ingestion_service=ingestion,
        mapping_workspace=mapping,
        long_workflow=long,
        staging_root=staging,
        max_files=20,
        max_file_bytes=1_000_000,
        max_batch_bytes=5_000_000,
        queue_capacity=8,
        scan_policy=ScanPolicy(),
        clock=lambda: NOW,
        receipt_link_retry_delay_seconds=0.01,
    )
    harness = _Harness(database, staging, manager, ingestion, mapping, long, modes, payloads)
    manager.start()
    return harness, store, scanner


def _evidence(value: Any) -> dict[str, Any]:
    return {"raw_value": {"kind": type(value).__name__, "value": value}}


def _payload(
    *,
    model: str = "MODEL-A",
    part: str = "PART-1",
    lot: str = "LOT-1",
    item: str = "Length",
    method: str = "Caliper",
    sample: float = 10.0,
    judgment: str = "PASS",
) -> dict[str, Any]:
    return {
        "state": "LOAD_CANDIDATE_READY",
        "provenance": {
            "source_inspection_date": "2026-08-15",
            "binding_catalog_revision": "sha256:volatile-catalog",
            "binding_selections": [{"row_key": "row-1", "binding_revision": 1}],
        },
        "source_identifiers": [
            {"kind": "MODEL", "evidence": _evidence(model)},
            {"kind": "PART_NUMBER", "evidence": _evidence(part)},
            {"kind": "LOT_NUMBER", "evidence": _evidence(lot)},
        ],
        "rows": [
            {
                "row_key": "row-1",
                "binding": {"canonical_item_key": "item-length"},
                "item": _evidence(item),
                "method": _evidence(method),
                "specification": _evidence("10 +/- 0.5"),
                "tolerance": _evidence("0.5"),
                "measurements": [{"sample_ordinal": 1, "raw_numeric_value": sample}],
                "supplier_judgment": _evidence(judgment),
                "section": _evidence("DIMENSION"),
                "issues": [],
                "state": "LOADABLE_PENDING",
                "data_status": "PENDING",
                "system_judgment_status": "NOT_EVALUATED",
                "system_judgment": None,
                "spec_evaluation_status": "NOT_EVALUATED",
            }
        ],
    }


def _stage(
    harness: _Harness,
    *,
    token: str,
    content: bytes,
    ordinal: int = 0,
    payload: dict[str, Any] | None = None,
    mode: str | None = None,
) -> BulkStagedFile:
    filename = f"{token}.xlsx"
    relative = Path(hashlib.md5(token.encode()).hexdigest()) / str(ordinal) / filename
    path = harness.staging / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    harness.payloads[digest] = payload or _payload(lot=f"LOT-{token}")
    if mode is not None:
        harness.modes[digest] = mode
    return BulkStagedFile(
        ordinal=ordinal,
        filename=filename,
        mime_type=XLSX_MIME,
        size_bytes=len(content),
        upload_sha256=digest,
        staged_relative_path=relative.as_posix(),
    )


def _submit(
    harness: _Harness,
    files: tuple[BulkStagedFile, ...],
    *,
    key: str,
    project: str = PROJECT,
    supplier: str = SUPPLIER,
) -> BulkBatchSnapshot:
    return harness.manager.submit(BulkSubmitRequest(project, supplier, key, files))


def _terminal(harness: _Harness, batch_id: str, *, timeout: float = 5.0) -> BulkBatchSnapshot:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = harness.manager.get(project_key=PROJECT, batch_id=batch_id)
        if snapshot.terminal:
            return snapshot
        time.sleep(0.01)
    raise AssertionError("Bulk batch did not become terminal")


@pytest.mark.required_test_id("DQ-P2-BULK-001")
def test_multi_file_staging_preserves_distinct_receipts_and_cleans_staging(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    try:
        first = _stage(harness, token="one", content=b"one", ordinal=0)
        second = _stage(harness, token="two", content=b"two", ordinal=1)
        result = _terminal(harness, _submit(harness, (first, second), key="bulk-key-001").batch_id)

        assert result.summary.total == 2
        assert result.summary.candidate_ready == 2
        assert {entry.receipt.receipt_id for entry in result.entries if entry.receipt} == set(
            harness.ingestion.receipt_ids
        )
        assert len(harness.ingestion.receipt_ids) == len(set(harness.ingestion.receipt_ids)) == 2
        assert not any(path.is_file() for path in harness.staging.rglob("*"))
    finally:
        harness.close()

    with TemporaryDirectory(prefix="dq-b-") as short_temp:
        actual_root = Path(short_temp)
        actual, store, scanner = _real_harness(actual_root)
        try:
            workbook_path = actual_root / "actual.xlsx"
            workbook = Workbook()
            workbook.active.title = "OQC"
            workbook.active["A1"] = "synthetic bulk"
            workbook.save(workbook_path)
            workbook.close()
            content = workbook_path.read_bytes()
            first = _stage(actual, token="actual-one", content=content, ordinal=0)
            second = _stage(actual, token="actual-two", content=content, ordinal=1)
            result = _terminal(
                actual,
                _submit(actual, (first, second), key="bulk-key-001-real").batch_id,
            )

            receipts = tuple(entry.receipt for entry in result.entries)
            assert all(receipt is not None for receipt in receipts)
            assert len({receipt.receipt_id for receipt in receipts if receipt is not None}) == 2
            assert len({receipt.content_sha256 for receipt in receipts if receipt is not None}) == 1
            assert scanner.calls == actual.mapping.preview_scanned_calls == 2
            digest = cast(Any, receipts[0]).content_sha256
            assert len(store.list_receipts(project_key=PROJECT, content_sha256=digest)) == 2
            assert not any(path.is_file() for path in actual.staging.rglob("*"))
        finally:
            actual.close()


@pytest.mark.required_test_id("DQ-P2-BULK-002")
def test_approved_mapping_and_long_candidate_reuse_exactly_one_scan(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    try:
        staged = _stage(harness, token="ready", content=b"ready")
        result = _terminal(harness, _submit(harness, (staged,), key="bulk-key-002").batch_id)

        entry = result.entries[0]
        assert entry.outcome == BulkEntryOutcome.CANDIDATE_READY
        assert entry.mapping is not None and entry.mapping.template_sha256 == "a" * 64
        assert entry.candidate is not None and entry.candidate.loadable_row_count == 1
        assert harness.ingestion.calls == harness.mapping.preview_scanned_calls == 1
        assert harness.mapping.preview_calls == harness.long.candidate_calls == 0
        assert harness.long.workspace_calls == 1
    finally:
        harness.close()


@pytest.mark.required_test_id("DQ-P2-BULK-003")
def test_mapping_fingerprint_and_same_series_variation_are_typed(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    try:
        changed_layout = _stage(
            harness,
            token="layout",
            content=b"layout",
            mode="mapping_variation",
        )
        first = _terminal(
            harness,
            _submit(harness, (changed_layout,), key="bulk-key-003a").batch_id,
        )
        assert first.entries[0].outcome == BulkEntryOutcome.VARIATION_REVIEW_REQUIRED
        assert first.entries[0].issues[0].location == "OQC!A7"
        assert "\\" not in json.dumps([issue.location for issue in first.entries[0].issues])

        baseline = _stage(
            harness,
            token="baseline",
            content=b"baseline",
            payload=_payload(lot="LOT-A"),
        )
        _terminal(harness, _submit(harness, (baseline,), key="bulk-key-003b").batch_id)
        new_lot_changed_item = _stage(
            harness,
            token="new-lot",
            content=b"new-lot",
            payload=_payload(lot="LOT-B", item="Width"),
        )
        varied = _terminal(
            harness,
            _submit(harness, (new_lot_changed_item,), key="bulk-key-003c").batch_id,
        )
        assert varied.entries[0].outcome == BulkEntryOutcome.VARIATION_REVIEW_REQUIRED

        other_model = _stage(
            harness,
            token="other-model",
            content=b"other-model",
            payload=_payload(model="MODEL-B", lot="LOT-C", item="Other"),
        )
        isolated = _terminal(
            harness,
            _submit(harness, (other_model,), key="bulk-key-003d").batch_id,
        )
        assert isolated.entries[0].outcome == BulkEntryOutcome.CANDIDATE_READY
    finally:
        harness.close()


@pytest.mark.required_test_id("DQ-P2-BULK-004")
def test_scan_failure_preserves_receipt_and_redacts_internal_details(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    try:
        staged = _stage(harness, token="scan", content=b"scan", mode="scan_failed")
        result = _terminal(harness, _submit(harness, (staged,), key="bulk-key-004").batch_id)

        entry = result.entries[0]
        assert entry.outcome == BulkEntryOutcome.SCAN_FAILED
        assert entry.receipt is not None and entry.receipt.content_sha256 == staged.upload_sha256
        serialized = json.dumps([issue.message for issue in entry.issues], ensure_ascii=False)
        assert str(tmp_path) not in serialized and "Traceback" not in serialized

        scanner_error = _stage(
            harness,
            token="unexpected-scanner",
            content=b"unexpected-scanner",
            ordinal=0,
            mode="unexpected_scan",
        )
        worker_error = _stage(
            harness,
            token="unexpected-worker",
            content=b"unexpected-worker",
            ordinal=1,
            mode="worker_exception",
        )
        following = _stage(
            harness,
            token="after-errors",
            content=b"after-errors",
            ordinal=2,
        )
        isolated = _terminal(
            harness,
            _submit(
                harness,
                (scanner_error, worker_error, following),
                key="bulk-key-004-isolation",
            ).batch_id,
        )
        assert [item.outcome for item in isolated.entries] == [
            BulkEntryOutcome.ERROR,
            BulkEntryOutcome.ERROR,
            BulkEntryOutcome.CANDIDATE_READY,
        ]
        assert isolated.entries[0].receipt is not None
        assert isolated.entries[1].receipt is not None
        expected_causes = (
            ("WORKBOOK_SCAN", "builtins.RuntimeError"),
            ("ENTRY_PROCESSING", "builtins.LookupError"),
        )
        for failed, (stage, cause_type) in zip(isolated.entries[:2], expected_causes, strict=True):
            provenance = failed.issues[0].expected_json
            assert provenance == {
                "stage": stage,
                "cause_type": cause_type,
                "cause_type_sha256": hashlib.sha256(cause_type.encode()).hexdigest(),
            }
            safe_payload = json.dumps(failed.issues[0].expected_json, ensure_ascii=False)
            assert "secret-token" not in safe_payload
            assert "private detail" not in safe_payload
            assert str(tmp_path) not in safe_payload
            assert "C:\\" not in safe_payload
    finally:
        harness.close()


@pytest.mark.required_test_id("DQ-P2-BULK-005")
def test_identifier_and_binding_holds_are_isolated_per_file(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    try:
        ready = _stage(harness, token="hold-ready", content=b"hold-ready", ordinal=0)
        identifier = _stage(
            harness,
            token="identifier",
            content=b"identifier",
            ordinal=1,
            mode="identifier_hold",
        )
        binding = _stage(
            harness,
            token="binding",
            content=b"binding",
            ordinal=2,
            mode="binding_hold",
        )
        result = _terminal(
            harness,
            _submit(harness, (ready, identifier, binding), key="bulk-key-005").batch_id,
        )
        assert [entry.outcome for entry in result.entries] == [
            BulkEntryOutcome.CANDIDATE_READY,
            BulkEntryOutcome.IDENTIFIER_HOLD,
            BulkEntryOutcome.BINDING_HOLD,
        ]
        assert result.summary.candidate_ready == 1
        assert result.summary.identifier_hold == result.summary.binding_hold == 1

        many = _stage(
            harness,
            token="many-issues",
            content=b"many-issues",
            mode="many_issues",
        )
        bounded = _terminal(
            harness,
            _submit(harness, (many,), key="bulk-key-005-bounded").batch_id,
        ).entries[0]
        assert bounded.outcome == BulkEntryOutcome.BINDING_HOLD
        assert len(bounded.issues) == 200
        marker = bounded.issues[-1]
        assert marker.code == "BULK_ISSUES_TRUNCATED"
        assert marker.expected_json["total_count"] == 1_001
        assert marker.expected_json["truncated_count"] > 0
        assert len(marker.expected_json["full_issues_sha256"]) == 64
        assert bounded.issues[0].expected_json["truncated"] is True

        oversized_payload = _payload(lot="LOT-OVERSIZED")
        oversized_payload["rows"][0]["source_evidence_blob"] = "x" * (2 * 1024 * 1024)
        oversized = _stage(
            harness,
            token="oversized-evidence",
            content=b"oversized-evidence",
            payload=oversized_payload,
        )
        evidence_hold = _terminal(
            harness,
            _submit(harness, (oversized,), key="bulk-key-005-evidence").batch_id,
        ).entries[0]
        assert evidence_hold.outcome == BulkEntryOutcome.BINDING_HOLD
        assert evidence_hold.issues[0].code == "BULK_EVIDENCE_LIMIT_EXCEEDED"
        assert evidence_hold.candidate is None
        with harness.database.session() as session:
            persisted = session.get(BulkEntryRow, evidence_hold.entry_id)
            assert persisted is not None
            assert "revision_evidence" in inspect(persisted).unloaded
            assert persisted.revision_evidence_sha256 is None
    finally:
        harness.close()


@pytest.mark.required_test_id("DQ-P2-BULK-006")
def test_exact_bytes_and_same_lot_retest_keep_separate_receipts(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    try:
        original = _stage(
            harness,
            token="original",
            content=b"original",
            payload=_payload(lot="LOT-X"),
        )
        base = _terminal(
            harness, _submit(harness, (original,), key="bulk-key-006a").batch_id
        ).entries[0]
        exact = _stage(
            harness,
            token="exact-copy",
            content=b"original",
            payload=_payload(lot="LOT-X"),
        )
        exact_result = _terminal(
            harness, _submit(harness, (exact,), key="bulk-key-006b").batch_id
        ).entries[0]
        retest = _stage(
            harness,
            token="retest",
            content=b"different-container",
            payload=_payload(lot="LOT-X"),
        )
        retest_result = _terminal(
            harness, _submit(harness, (retest,), key="bulk-key-006c").batch_id
        ).entries[0]

        assert exact_result.outcome == retest_result.outcome == BulkEntryOutcome.DUPLICATE_CANDIDATE
        assert exact_result.issues[-1].code == "BULK_EXACT_DUPLICATE_CANDIDATE"
        assert retest_result.issues[0].code == "BULK_SAME_LOT_RETEST_CANDIDATE"
        assert (
            len(
                {
                    base.receipt.receipt_id,
                    exact_result.receipt.receipt_id,
                    retest_result.receipt.receipt_id,
                }
            )
            == 3
        )

        noisy = _stage(
            harness,
            token="noisy-original",
            content=b"noisy-original",
            mode="many_issues",
        )
        noisy_base = _terminal(
            harness,
            _submit(harness, (noisy,), key="bulk-key-006d").batch_id,
        ).entries[0]
        noisy_copy = _stage(
            harness,
            token="noisy-copy",
            content=b"noisy-original",
            mode="many_issues",
        )
        noisy_duplicate = _terminal(
            harness,
            _submit(harness, (noisy_copy,), key="bulk-key-006e").batch_id,
        ).entries[0]
        assert noisy_base.outcome == BulkEntryOutcome.BINDING_HOLD
        assert noisy_duplicate.outcome == BulkEntryOutcome.DUPLICATE_CANDIDATE
        assert noisy_duplicate.duplicate_of_entry_id == noisy_base.entry_id
        assert noisy_duplicate.issues[0].code == "BULK_EXACT_DUPLICATE_CANDIDATE"
        assert noisy_duplicate.issues[0].expected_json == {"original_outcome": "BINDING_HOLD"}
        assert any(issue.code == "BULK_ISSUES_TRUNCATED" for issue in noisy_duplicate.issues)
    finally:
        harness.close()


@pytest.mark.required_test_id("DQ-P2-BULK-007")
def test_same_lot_changed_evidence_requires_revision_review_against_first_baseline(
    tmp_path: Path,
) -> None:
    harness = _harness(tmp_path)
    try:
        a = _stage(harness, token="rev-a", content=b"rev-a", payload=_payload(sample=10.0))
        a_result = _terminal(harness, _submit(harness, (a,), key="bulk-key-007a").batch_id).entries[
            0
        ]
        b = _stage(harness, token="rev-b", content=b"rev-b", payload=_payload(sample=10.2))
        b_result = _terminal(harness, _submit(harness, (b,), key="bulk-key-007b").batch_id).entries[
            0
        ]
        c = _stage(harness, token="rev-c", content=b"rev-c", payload=_payload(sample=10.4))
        c_result = _terminal(harness, _submit(harness, (c,), key="bulk-key-007c").batch_id).entries[
            0
        ]
        new_lot = _stage(
            harness,
            token="history",
            content=b"history",
            payload=_payload(lot="LOT-2", sample=11.0),
        )
        history = _terminal(
            harness, _submit(harness, (new_lot,), key="bulk-key-007d").batch_id
        ).entries[0]

        assert b_result.outcome == c_result.outcome == BulkEntryOutcome.REVISION_REVIEW_REQUIRED
        assert (
            b_result.revision_baseline_entry_id
            == c_result.revision_baseline_entry_id
            == a_result.entry_id
        )
        assert history.outcome == BulkEntryOutcome.CANDIDATE_READY
        assert any("SAMPLE" in issue.code for issue in b_result.issues)

        large_base_payload = _payload(
            model="MODEL-HIGH",
            part="PART-HIGH",
            lot="LOT-HIGH",
        )
        large_base = _stage(
            harness,
            token="large-rev-base",
            content=b"large-rev-base",
            payload=large_base_payload,
        )
        large_base_result = _terminal(
            harness,
            _submit(harness, (large_base,), key="bulk-key-007e").batch_id,
        ).entries[0]
        large_changed_payload = _payload(
            model="MODEL-HIGH",
            part="PART-HIGH",
            lot="LOT-HIGH",
        )
        large_changed_payload["rows"][0]["synthetic_differences"] = {
            f"field_{index:03d}": index for index in range(250)
        }
        large_changed = _stage(
            harness,
            token="large-rev-changed",
            content=b"large-rev-changed",
            payload=large_changed_payload,
        )
        large_revision = _terminal(
            harness,
            _submit(harness, (large_changed,), key="bulk-key-007f").batch_id,
        ).entries[0]
        assert large_revision.outcome == BulkEntryOutcome.REVISION_REVIEW_REQUIRED
        assert large_revision.revision_baseline_entry_id == large_base_result.entry_id
        assert large_revision.issues[0].code == "BULK_REVISION_DIFF_TRUNCATED"
        assert large_revision.issues[0].expected_json["total_differences"] == 250

        large_copy = _stage(
            harness,
            token="large-rev-copy",
            content=b"large-rev-changed",
            payload=large_changed_payload,
        )
        large_duplicate = _terminal(
            harness,
            _submit(harness, (large_copy,), key="bulk-key-007g").batch_id,
        ).entries[0]
        assert large_duplicate.outcome == BulkEntryOutcome.DUPLICATE_CANDIDATE
        assert large_duplicate.duplicate_of_entry_id == large_revision.entry_id
        assert large_duplicate.revision_baseline_entry_id == large_base_result.entry_id
        assert large_duplicate.issues[0].code == "BULK_EXACT_DUPLICATE_CANDIDATE"
        assert any(issue.code == "BULK_REVISION_DIFF_TRUNCATED" for issue in large_duplicate.issues)
    finally:
        harness.close()


@pytest.mark.required_test_id("DQ-P2-BULK-008")
def test_manifest_idempotency_replays_exact_request_and_conflicts_on_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _harness(tmp_path, start=False)
    try:
        first = _stage(harness, token="idem-a", content=b"idem")
        created = _submit(harness, (first,), key="bulk-key-008")
        replay_file = _stage(harness, token="idem-a", content=b"idem")
        replay = _submit(harness, (replay_file,), key="bulk-key-008")
        assert replay.replayed and replay.batch_id == created.batch_id
        assert not (harness.staging / replay_file.staged_relative_path).exists()

        changed = _stage(harness, token="idem-b", content=b"changed")
        with pytest.raises(BulkImportConflictError, match="BULK_IDEMPOTENCY_CONFLICT"):
            _submit(harness, (changed,), key="bulk-key-008")
        assert not (harness.staging / changed.staged_relative_path).exists()
        with harness.database.session() as session:
            assert session.scalar(select(func.count()).select_from(BulkBatchRow)) == 1

        invalid_project = _stage(harness, token="invalid-project", content=b"scope")
        with pytest.raises(BulkImportValidationError, match="INVALID_BULK_PROJECT"):
            _submit(
                harness,
                (invalid_project,),
                key="bulk-key-scope-project",
                project="P" * 65,
            )
        assert not (harness.staging / invalid_project.staged_relative_path).exists()
        invalid_supplier = _stage(harness, token="invalid-supplier", content=b"supplier")
        with pytest.raises(BulkImportValidationError, match="INVALID_BULK_SUPPLIER_SCOPE"):
            _submit(
                harness,
                (invalid_supplier,),
                key="bulk-key-scope-supplier",
                supplier="S" * 201,
            )
        assert not (harness.staging / invalid_supplier.staged_relative_path).exists()

        race_first = _stage(harness, token="race", content=b"race")
        second_relative = Path("e" * 32) / "0" / race_first.filename
        second_path = harness.staging / second_relative
        second_path.parent.mkdir(parents=True)
        second_path.write_bytes(b"race")
        race_second = dataclass_replace(
            race_first,
            staged_relative_path=second_relative.as_posix(),
        )
        barrier = Barrier(2)
        original_create = harness.manager._repository.create_batch

        def racing_create(session: Any, **kwargs: Any) -> Any:
            barrier.wait(timeout=5)
            return original_create(session, **kwargs)

        monkeypatch.setattr(harness.manager._repository, "create_batch", racing_create)
        request_one = BulkSubmitRequest(PROJECT, SUPPLIER, "bulk-key-race", (race_first,))
        request_two = BulkSubmitRequest(PROJECT, SUPPLIER, "bulk-key-race", (race_second,))
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = (
                executor.submit(harness.manager.submit, request_one),
                executor.submit(harness.manager.submit, request_two),
            )
            raced = tuple(future.result(timeout=10) for future in futures)
        assert len({item.batch_id for item in raced}) == 1
        assert sorted(item.replayed for item in raced) == [False, True]
        with harness.database.session() as session:
            assert session.scalar(select(func.count()).select_from(BulkBatchRow)) == 2
    finally:
        harness.close()


@pytest.mark.required_test_id("DQ-P2-BULK-009")
def test_restart_recovers_processing_same_receipt_rejects_tamper_and_cleans_orphans(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _harness(tmp_path, start=False)
    try:
        staged = _stage(harness, token="restart", content=b"restart")
        created = _submit(harness, (staged,), key="bulk-key-009a")
        with harness.database.session() as session, session.begin():
            batch = session.get(BulkBatchRow, created.batch_id)
            entry = session.scalar(
                select(BulkEntryRow).where(BulkEntryRow.batch_id == created.batch_id)
            )
            assert batch is not None and entry is not None
            batch.status = "PROCESSING"
            entry.status = "PROCESSING"
            reserved_receipt_id = entry.reserved_receipt_id
        orphan = harness.staging / ("f" * 32) / "0" / "orphan.xlsx"
        orphan.parent.mkdir(parents=True)
        orphan.write_bytes(b"orphan")
        harness.manager.start()
        recovered = _terminal(harness, created.batch_id)
        assert recovered.entries[0].receipt.receipt_id == reserved_receipt_id
        assert not orphan.exists()

        harness.manager.shutdown()
        harness.manager.start()
        extra = _stage(harness, token="same-instance", content=b"same-instance")
        assert _terminal(harness, _submit(harness, (extra,), key="bulk-key-009b").batch_id).terminal

        original_persist_receipt = harness.manager._persist_receipt
        receipt_link_attempts = 0
        scans_before_receipt_retry = harness.ingestion.scan_calls

        def flaky_receipt_link(project_key: str, entry_id: str, receipt: SourceFileReceipt) -> None:
            nonlocal receipt_link_attempts
            receipt_link_attempts += 1
            if receipt_link_attempts <= 3:
                raise bulk_module._RawReceiptLinkRetry
            original_persist_receipt(project_key, entry_id, receipt)

        monkeypatch.setattr(harness.manager, "_persist_receipt", flaky_receipt_link)
        retry_staged = _stage(harness, token="receipt-link", content=b"receipt-link")
        retry_result = _terminal(
            harness,
            _submit(harness, (retry_staged,), key="bulk-key-009-receipt-link").batch_id,
        ).entries[0]
        assert retry_result.outcome == BulkEntryOutcome.CANDIDATE_READY
        assert retry_result.attempt_count == 4
        assert receipt_link_attempts == 4
        assert harness.ingestion.scan_calls == scans_before_receipt_retry + 1
        assert len(set(harness.ingestion.receipt_ids[-4:])) == 1
        assert not (harness.staging / retry_staged.staged_relative_path).exists()
        monkeypatch.setattr(harness.manager, "_persist_receipt", original_persist_receipt)

        harness.manager.shutdown()
        tampered = _stage(harness, token="tamper", content=b"before")
        tamper_batch = _submit(harness, (tampered,), key="bulk-key-009c")
        (harness.staging / tampered.staged_relative_path).write_bytes(b"after")
        calls_before = harness.ingestion.calls
        harness.manager.start()
        tamper_result = _terminal(harness, tamper_batch.batch_id).entries[0]
        assert tamper_result.outcome == BulkEntryOutcome.ERROR
        assert tamper_result.receipt is None
        assert harness.ingestion.calls == calls_before
        assert not (harness.staging / tampered.staged_relative_path).exists()

        workbook_path = tmp_path / "reserved.xlsx"
        book = Workbook()
        book.active["A1"] = "synthetic"
        book.save(workbook_path)
        book.close()
        store = OriginalFileStore(tmp_path / "store", max_bytes=1_000_000)
        reserved = "1" * 32
        first_receipt = store.preserve(
            project_key=PROJECT,
            source=workbook_path,
            declared_mime_type=XLSX_MIME,
            receipt_id=reserved,
            received_at=NOW,
        )
        replay_receipt = store.preserve(
            project_key=PROJECT,
            source=workbook_path,
            declared_mime_type=XLSX_MIME,
            receipt_id=reserved,
            received_at=NOW,
        )
        assert first_receipt == replay_receipt
        assert (
            len(
                store.list_receipts(
                    project_key=PROJECT, content_sha256=first_receipt.content_sha256
                )
            )
            == 1
        )
    finally:
        harness.close()


@pytest.mark.required_test_id("DQ-P2-BULK-010")
def test_0006_migration_is_table_only_blocks_data_loss_and_never_writes_default_db(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("MASS_PRODUCTION_QUALITY_VALIDATION_DATABASE_URL", raising=False)
    database_path = tmp_path / "migration.sqlite3"
    url = f"sqlite+pysqlite:///{database_path.as_posix()}"
    config = _alembic_config(url)
    command.upgrade(config, "0005")
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO source_files (id, project_key, receipt_id, blob_id, "
                    "content_sha256, received_at, original_filename, model_candidates, "
                    "lot_candidates, declared_mime_type, detected_mime_type, "
                    "canonical_extension, size_bytes, parse_status, scan_source_name, "
                    "scan_source_size_bytes, scan_sha256_before, scan_sha256_after, "
                    "scan_contract_version, estimated_cells, external_link_count, "
                    "macro_handling, display_value_contract, is_golden_workbook_evidence, "
                    "scan_issues, row_version, created_at) VALUES "
                    "('bulk-prior-source', 'bulk-prior-project', 'bulk-prior-receipt', "
                    "'sha256:bulk-prior', :digest, '2026-08-15 09:00:00', "
                    "'prior.xlsx', '[\"MODEL-A\"]', '[\"LOT-A\"]', :mime, :mime, '.xlsx', "
                    "123, 'SCANNED', 'prior.xlsx', 123, :digest, :digest, 'scan-v1', "
                    "5, 0, 'NOT_APPLICABLE', 'RAW_AND_DISPLAY', 0, '[]', 7, "
                    "'2026-08-15 09:00:00')"
                ),
                {"digest": "d" * 64, "mime": XLSX_MIME},
            )
        with engine.connect() as connection:
            prior_snapshot = tuple(
                connection.execute(
                    text(
                        "SELECT id, project_key, content_sha256, parse_status, scan_issues, "
                        "row_version FROM source_files WHERE id='bulk-prior-source'"
                    )
                ).one()
            )
        prior_tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    command.upgrade(config, "0006")
    engine = create_engine(url)
    try:
        tables = set(inspect(engine).get_table_names())
        assert prior_tables.issubset(tables)
        assert {"bulk_import_batches", "bulk_import_entries"}.issubset(tables)
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0006"
            assert (
                tuple(
                    connection.execute(
                        text(
                            "SELECT id, project_key, content_sha256, parse_status, scan_issues, "
                            "row_version FROM source_files WHERE id='bulk-prior-source'"
                        )
                    ).one()
                )
                == prior_snapshot
            )
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
    finally:
        engine.dispose()

    with pytest.raises(IntegrityError, match="bulk_batch_summary_shape"):
        engine = create_engine(url)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO bulk_import_batches "
                        "(id, project_key, supplier_scope, idempotency_key, manifest_sha256, "
                        "status, entry_count, terminal_summary, terminal_summary_sha256, "
                        "row_version, created_at, updated_at, finished_at) VALUES "
                        "('bad-summary', 'bulk-project', 'supplier', 'bad-summary-key', :sha, "
                        "'COMPLETED', 1, '{}', NULL, 1, :now, :now, :now)"
                    ),
                    {"sha": "a" * 64, "now": "2026-08-15 10:00:00"},
                )
        finally:
            engine.dispose()

    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO bulk_import_batches "
                    "(id, project_key, supplier_scope, idempotency_key, manifest_sha256, "
                    "status, entry_count, terminal_summary, terminal_summary_sha256, "
                    "row_version, created_at, updated_at, finished_at) VALUES "
                    "('guard-batch', 'bulk-project', 'supplier', 'guard-batch-key', :sha, "
                    "'STAGED', 1, NULL, NULL, 1, :now, :now, NULL)"
                ),
                {"sha": "a" * 64, "now": "2026-08-15 10:00:00"},
            )
        with (
            pytest.raises(IntegrityError, match="bulk_entry_receipt_shape"),
            engine.begin() as connection,
        ):
            connection.execute(
                text(
                    "INSERT INTO bulk_import_entries "
                    "(id, project_key, batch_id, ordinal, reserved_receipt_id, "
                    "reserved_received_at, filename, mime_type, size_bytes, upload_sha256, "
                    "staged_relative_path, status, outcome, status_code, message, "
                    "attempt_count, receipt_payload, receipt_sha256, mapping_payload, "
                    "mapping_sha256, candidate_payload, candidate_sha256, "
                    "revision_identity, revision_evidence, revision_evidence_sha256, "
                    "issues, issues_sha256, duplicate_of_entry_id, "
                    "revision_baseline_entry_id, row_version, created_at, updated_at, "
                    "finished_at) VALUES "
                    "('bad-receipt', 'bulk-project', 'guard-batch', 0, :receipt, :now, "
                    "'bad.xlsx', :mime, 1, :sha, NULL, 'STAGED', NULL, 'BULK_STAGED', "
                    "'staged', 0, '{}', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, "
                    "'[]', :issues_sha, NULL, NULL, 1, :now, :now, NULL)"
                ),
                {
                    "receipt": "1" * 32,
                    "now": "2026-08-15 10:00:00",
                    "mime": XLSX_MIME,
                    "sha": "b" * 64,
                    "issues_sha": hashlib.sha256(b"[]").hexdigest(),
                },
            )
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO bulk_import_entries "
                    "(id, project_key, batch_id, ordinal, reserved_receipt_id, "
                    "reserved_received_at, filename, mime_type, size_bytes, upload_sha256, "
                    "staged_relative_path, status, outcome, status_code, message, attempt_count, "
                    "receipt_payload, receipt_sha256, mapping_payload, mapping_sha256, "
                    "candidate_payload, candidate_sha256, revision_identity, revision_evidence, "
                    "revision_evidence_sha256, issues, issues_sha256, duplicate_of_entry_id, "
                    "revision_baseline_entry_id, row_version, created_at, updated_at, finished_at) "
                    "VALUES ('guard-entry', 'bulk-project', 'guard-batch', 0, :receipt, :now, "
                    "'guard.xlsx', :mime, 1, :sha, NULL, 'STAGED', NULL, 'BULK_STAGED', "
                    "'staged', 0, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, '[]', "
                    ":issues_sha, NULL, NULL, 1, :now, :now, NULL)"
                ),
                {
                    "receipt": "2" * 32,
                    "now": "2026-08-15 10:00:00",
                    "mime": XLSX_MIME,
                    "sha": "c" * 64,
                    "issues_sha": hashlib.sha256(b"[]").hexdigest(),
                },
            )
    finally:
        engine.dispose()
    with pytest.raises(RuntimeError, match="durable Bulk history exists"):
        command.downgrade(config, "0005")
    engine = create_engine(url)
    try:
        assert {"bulk_import_batches", "bulk_import_entries"}.issubset(
            inspect(engine).get_table_names()
        )
        with engine.begin() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0006"
            connection.execute(text("DELETE FROM bulk_import_entries"))
    finally:
        engine.dispose()
    with pytest.raises(RuntimeError, match="durable Bulk history exists"):
        command.downgrade(config, "0005")
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM bulk_import_batches"))
    finally:
        engine.dispose()

    command.downgrade(config, "0005")
    engine = create_engine(url)
    try:
        assert "bulk_import_batches" not in inspect(engine).get_table_names()
        with engine.connect() as connection:
            assert (
                tuple(
                    connection.execute(
                        text(
                            "SELECT id, project_key, content_sha256, parse_status, scan_issues, "
                            "row_version FROM source_files WHERE id='bulk-prior-source'"
                        )
                    ).one()
                )
                == prior_snapshot
            )
    finally:
        engine.dispose()
    command.upgrade(config, SCHEMA_HEAD_REVISION)

    database = Database(url)
    try:
        with database.session() as session:
            assert session.scalar(select(func.count()).select_from(LongIngestionJobRow)) == 0
            assert session.scalar(select(func.count()).select_from(LongInspectionResultRow)) == 0
            assert session.scalar(select(func.count()).select_from(LongMeasurementRow)) == 0
    finally:
        database.dispose()
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
            assert (
                compare_metadata(
                    MigrationContext.configure(connection, opts={"compare_type": True}),
                    Base.metadata,
                )
                == []
            )
            assert (
                tuple(
                    connection.execute(
                        text(
                            "SELECT id, project_key, content_sha256, parse_status, scan_issues, "
                            "row_version FROM source_files WHERE id='bulk-prior-source'"
                        )
                    ).one()
                )
                == prior_snapshot
            )
    finally:
        engine.dispose()
    assert SCHEMA_HEAD_REVISION == "0008"
    assert not (tmp_path / ".localdata").exists()


def _alembic_config(database_url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "migrations"))
    config.set_main_option("prepend_sys_path", str(ROOT / "backend"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def _api_client(harness: _Harness) -> TestClient:
    app = FastAPI()
    app.include_router(create_bulk_router(harness.manager, staging_root=harness.staging))
    return TestClient(app)


def _post(client: TestClient, *, key: str, content: bytes, name: str = "bulk.xlsx") -> Any:
    return client.post(
        "/api/v1/bulk/batches",
        data={
            "project_key": PROJECT,
            "supplier_scope": SUPPLIER,
            "idempotency_key": key,
        },
        files=[("workbooks", (name, content, XLSX_MIME))],
    )


@pytest.mark.required_test_id("DQ-P2-BULKUI-001")
def test_bulk_http_submit_and_poll_returns_exact_candidate_proof(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    try:
        content = b"api-ready"
        harness.payloads[hashlib.sha256(content).hexdigest()] = _payload()
        client = _api_client(harness)
        created = _post(client, key="bulk-ui-001", content=content)
        assert created.status_code == 202
        body = created.json()
        final = _terminal(harness, body["batch_id"])
        polled = client.get(
            f"/api/v1/bulk/batches/{final.batch_id}", params={"project_key": PROJECT}
        )
        assert polled.status_code == 200
        assert polled.json()["entries"][0]["candidate"]["candidate_digest"]
        assert polled.json()["poll_after_ms"] is None
    finally:
        harness.close()


@pytest.mark.required_test_id("DQ-P2-BULKUI-002")
def test_bulk_http_typed_exception_and_no_auto_capabilities(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    try:
        content = b"api-variation"
        digest = hashlib.sha256(content).hexdigest()
        harness.payloads[digest] = _payload()
        harness.modes[digest] = "mapping_variation"
        created = _post(_api_client(harness), key="bulk-ui-002", content=content)
        final = _terminal(harness, created.json()["batch_id"])
        capabilities = final.capabilities
        assert final.entries[0].outcome == BulkEntryOutcome.VARIATION_REVIEW_REQUIRED
        assert not any(
            (
                capabilities.per_file_approval,
                capabilities.finalize_available,
                capabilities.auto_long,
                capabilities.auto_valid,
                capabilities.auto_replaced,
                capabilities.auto_revision,
                capabilities.ai_used,
            )
        )
    finally:
        harness.close()


@pytest.mark.required_test_id("DQ-P2-BULKUI-003")
def test_bulk_http_durable_get_is_project_isolated_and_safe(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    try:
        content = b"api-project"
        harness.payloads[hashlib.sha256(content).hexdigest()] = _payload()
        client = _api_client(harness)
        batch_id = _post(client, key="bulk-ui-003", content=content).json()["batch_id"]
        denied = client.get(
            f"/api/v1/bulk/batches/{batch_id}", params={"project_key": "other-project"}
        )
        assert denied.status_code == 404
        assert set(denied.json()["detail"]) == {"code", "message", "status_label"}
        assert str(tmp_path) not in denied.text
    finally:
        harness.close()


@pytest.mark.required_test_id("DQ-P2-BULKUI-004")
def test_bulk_http_multipart_validation_is_bounded_and_cleans_staging(tmp_path: Path) -> None:
    harness = _harness(tmp_path)
    try:
        client = _api_client(harness)
        invalid = _post(
            client,
            key="bulk-ui-004",
            content=b"bad",
            name="../escape.xlsx",
        )
        assert invalid.status_code == 400
        assert invalid.json()["detail"]["code"] == "INVALID_BULK_FILENAME"
        unsupported = _post(
            client,
            key="bulk-ui-004b",
            content=b"bad",
            name="bad.xls",
        )
        assert unsupported.status_code == 415
        assert not any(path.is_file() for path in harness.staging.rglob("*"))
    finally:
        harness.close()
