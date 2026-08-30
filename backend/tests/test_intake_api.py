from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from threading import Event, current_thread
from time import monotonic, sleep
from typing import Any
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook  # type: ignore[import-untyped]

from app.api import create_intake_router
from app.application.intake_jobs import IntakeJobManager
from app.application.manual_ingestion import (
    ManualIngestionOutcome,
    ManualIngestionRequest,
    ManualIngestionStatus,
    ManualWorkbookIngestionService,
)
from app.domain.source_file import SourceFileReceipt
from app.domain.workbook_scan import (
    DisplayValueStatus,
    IssueSeverity,
    MacroHandling,
    ScanIssue,
    ScanPolicy,
    SourceLocation,
    WorkbookScan,
    WorkbookScanFailure,
    WorkbookScanFailureStatus,
    WorkbookScanState,
)
from app.infrastructure.excel import OpenpyxlWorkbookScanner
from app.infrastructure.file_store import XLSX_MIME, OriginalFileStore

_JOB_ID = re.compile(r"[0-9a-f]{32}\Z")


def _workbook_bytes() -> bytes:
    stream = BytesIO()
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "OQC 성적서"
    sheet["A1"] = "MODEL-A"
    sheet["A2"] = "LOT-001"
    sheet["B3"] = 10.25
    workbook.save(stream)
    workbook.close()
    return stream.getvalue()


class TrackingService:
    def __init__(self, delegate: ManualWorkbookIngestionService) -> None:
        self._delegate = delegate
        self.thread_names: list[str] = []

    def ingest(self, request: ManualIngestionRequest) -> ManualIngestionOutcome:
        self.thread_names.append(current_thread().name)
        return self._delegate.ingest(request)


class SyntheticSuccessfulService:
    def ingest(self, request: ManualIngestionRequest) -> ManualIngestionOutcome:
        payload = request.source.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        received_at = datetime.now(UTC)
        receipt = SourceFileReceipt(
            receipt_id=uuid4().hex,
            project_key=request.project_key,
            blob_id=f"sha256:{digest}",
            content_sha256=digest,
            received_at=received_at,
            original_filename=request.source.name,
            model_candidates=request.model_candidates,
            lot_candidates=request.lot_candidates,
            declared_mime_type=request.declared_mime_type,
            detected_mime_type=request.declared_mime_type,
            canonical_extension=request.source.suffix.lower(),
            size_bytes=len(payload),
        )
        scan = WorkbookScan(
            state=WorkbookScanState.SCANNED,
            source_name=request.source.name,
            source_size_bytes=len(payload),
            source_sha256_before=digest,
            source_sha256_after=digest,
            sheets=(),
            issues=(),
            estimated_cells=0,
            external_link_count=0,
            macro_handling=MacroHandling.NOT_APPLICABLE,
            display_value_contract=DisplayValueStatus.NOT_RENDERED,
        )
        return ManualIngestionOutcome(
            status=ManualIngestionStatus.STORED_AND_SCANNED,
            receipt=receipt,
            scan=scan,
        )


class BlockingSuccessfulService(SyntheticSuccessfulService):
    def __init__(self) -> None:
        self.entered = Event()
        self.release = Event()
        self.call_count = 0

    def ingest(self, request: ManualIngestionRequest) -> ManualIngestionOutcome:
        self.call_count += 1
        if self.call_count == 1:
            self.entered.set()
            if not self.release.wait(timeout=5):
                raise RuntimeError("synthetic blocking service timed out")
        return super().ingest(request)


class RejectingScanner:
    def scan(self, source: Path, policy: ScanPolicy | None = None) -> WorkbookScan:
        del source, policy
        raise AssertionError("manual intake must scan the preserved stream")

    def scan_stream(
        self,
        source: Any,
        *,
        source_name: str,
        policy: ScanPolicy | None = None,
    ) -> WorkbookScan:
        del source, source_name, policy
        raise WorkbookScanFailure(
            WorkbookScanFailureStatus.CORRUPT_OOXML,
            ScanIssue(
                code="PRIVATE_SCAN_FAILURE",
                severity=IssueSeverity.ERROR,
                message=r"C:\private\source\secret.xlsx could not be parsed",
                location=SourceLocation.cell("OQC", "P10"),
            ),
        )


class ExplodingService:
    def ingest(self, request: ManualIngestionRequest) -> ManualIngestionOutcome:
        del request
        raise RuntimeError(r"C:\private\source\secret.xlsx")


def _manager(
    *,
    service: Any,
    staging_root: Path,
    max_upload_bytes: int = 2 * 1024 * 1024,
    queue_capacity: int = 2,
    registry_capacity: int = 4,
) -> IntakeJobManager:
    return IntakeJobManager(
        ingestion_service=service,
        staging_root=staging_root,
        max_upload_bytes=max_upload_bytes,
        queue_capacity=queue_capacity,
        registry_capacity=registry_capacity,
        scan_policy=ScanPolicy(max_cells=10_000),
        shutdown_timeout_seconds=5,
        poll_after_ms=100,
    )


@contextmanager
def _client(manager: IntakeJobManager) -> Iterator[TestClient]:
    application = FastAPI()
    application.include_router(create_intake_router(manager))
    manager.start()
    try:
        with TestClient(application) as client:
            yield client
    finally:
        manager.shutdown()


def _post(client: TestClient, payload: bytes, *, filename: str = "OQC.xlsx") -> Any:
    return client.post(
        "/api/v1/intake/jobs",
        data={"project_key": "project-alpha"},
        files={"workbook": (filename, payload, XLSX_MIME)},
    )


def _await_terminal(
    client: TestClient,
    *,
    job_id: str,
    project_key: str = "project-alpha",
    timeout_seconds: float = 5,
) -> dict[str, Any]:
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        response = client.get(
            f"/api/v1/intake/jobs/{job_id}",
            params={"project_key": project_key},
        )
        assert response.status_code == 200
        body: dict[str, Any] = response.json()
        if body["terminal"] is True:
            return body
        sleep(0.01)
    raise AssertionError(f"intake job {job_id} did not become terminal")


def _assert_empty_staging(staging_root: Path) -> None:
    assert not staging_root.exists() or tuple(staging_root.iterdir()) == ()


@pytest.mark.required_test_id("DQ-P1-UIINTAKE-001")
def test_manual_intake_runs_scan_on_worker_and_returns_korean_async_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    staging_root = tmp_path / "staging"
    original_store = OriginalFileStore(tmp_path / "o", max_bytes=2 * 1024 * 1024)
    service = TrackingService(
        ManualWorkbookIngestionService(
            file_store=original_store,
            scanner=OpenpyxlWorkbookScanner(),
        )
    )
    manager = _manager(service=service, staging_root=staging_root)
    payload = _workbook_bytes()

    assert not staging_root.exists()
    with _client(manager) as client:
        assert not staging_root.exists()
        response = client.post(
            "/api/v1/intake/jobs",
            data={
                "project_key": "project-alpha",
                "model_hint": "MODEL-A",
                "lot_hint": "LOT-001",
            },
            files={"workbook": ("OQC_검사성적서.xlsx", payload, XLSX_MIME)},
        )

        assert response.status_code == 202
        accepted = response.json()
        assert _JOB_ID.fullmatch(accepted["job_id"])
        assert accepted["status"] in {"QUEUED", "PROCESSING", "MAPPING_REQUIRED"}
        assert accepted["status_label"] in {"접수 대기", "처리 중", "매핑 등록 필요"}
        terminal = _await_terminal(client, job_id=accepted["job_id"])

    assert terminal["status"] == "MAPPING_REQUIRED", terminal["issues"]
    assert terminal["terminal"] is True
    assert terminal["poll_after_ms"] is None
    assert terminal["receipt"]["original_filename"] == "OQC_검사성적서.xlsx"
    assert terminal["receipt"]["size_bytes"] == len(payload)
    assert terminal["receipt"]["model_candidates"] == ["MODEL-A"]
    assert terminal["receipt"]["lot_candidates"] == ["LOT-001"]
    assert terminal["scan"]["source_size_bytes"] == len(payload)
    assert terminal["scan"]["sha256_before"] == terminal["receipt"]["content_sha256"]
    assert terminal["scan"]["sha256_after"] == terminal["receipt"]["content_sha256"]
    assert "source_sha256_before" not in terminal["scan"]
    assert terminal["scan"]["sheet_count"] == 1
    assert terminal["scan"]["sheets"][0]["name"] == "OQC 성적서"
    assert terminal["scan"]["sheets"][0]["kind"] == "WORKSHEET"
    assert terminal["issues"] == [
        {
            "code": "DISPLAY_VALUE_NOT_RENDERED",
            "message": "통합 문서 스캔 결과를 확인해 주세요.",
            "location": "workbook",
        }
    ]
    assert service.thread_names == ["mass-production-quality-validation-intake-worker"]
    _assert_empty_staging(staging_root)
    assert not (tmp_path / ".localdata").exists()


@pytest.mark.required_test_id("DQ-P1-UIINTAKE-002")
def test_validation_size_and_capacity_fail_safely_without_extra_staging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    staging_root = tmp_path / "capacity-staging"
    blocking = BlockingSuccessfulService()
    manager = _manager(
        service=blocking,
        staging_root=staging_root,
        queue_capacity=1,
        registry_capacity=2,
    )

    manager.start()
    application = FastAPI()
    application.include_router(create_intake_router(manager))
    try:
        with TestClient(application) as client:
            first = _post(client, b"first")
            assert first.status_code == 202
            assert blocking.entered.wait(timeout=2)
            second = _post(client, b"second")
            assert second.status_code == 202
            rejected = _post(client, b"third")
            assert rejected.status_code == 429
            assert rejected.json() == {
                "detail": {
                    "code": "INTAKE_CAPACITY_REACHED",
                    "message": (
                        "현재 처리 가능한 접수 건수를 초과했습니다. 잠시 후 다시 시도해 주세요."
                    ),
                    "status_label": "접수 용량 초과",
                }
            }
            assert len(tuple(staging_root.iterdir())) == 2
            blocking.release.set()
            _await_terminal(client, job_id=first.json()["job_id"])
            _await_terminal(client, job_id=second.json()["job_id"])
    finally:
        blocking.release.set()
        manager.shutdown()
    _assert_empty_staging(staging_root)

    validation_staging = tmp_path / "validation-staging"
    validation_manager = _manager(
        service=SyntheticSuccessfulService(),
        staging_root=validation_staging,
        max_upload_bytes=8,
    )
    with _client(validation_manager) as client:
        unsupported = client.post(
            "/api/v1/intake/jobs",
            data={"project_key": "project-alpha"},
            files={"workbook": ("legacy.xls", b"data", "application/vnd.ms-excel")},
        )
        wrong_mime = client.post(
            "/api/v1/intake/jobs",
            data={"project_key": "project-alpha"},
            files={"workbook": ("OQC.xlsx", b"data", "application/octet-stream")},
        )
        too_large = _post(client, b"123456789")
        unsafe_name = _post(client, b"data", filename="../secret.xlsx")
        missing_fields = client.post("/api/v1/intake/jobs")

    assert unsupported.status_code == 415
    assert unsupported.json()["detail"]["code"] == "UNSUPPORTED_EXTENSION"
    assert wrong_mime.status_code == 415
    assert wrong_mime.json()["detail"]["code"] == "DECLARED_MIME_MISMATCH"
    assert too_large.status_code == 413
    assert too_large.json()["detail"]["code"] == "UPLOAD_TOO_LARGE"
    assert unsafe_name.status_code == 400
    assert unsafe_name.json()["detail"]["code"] == "INVALID_FILENAME"
    assert missing_fields.status_code == 400
    assert missing_fields.json()["detail"] == {
        "code": "PROJECT_KEY_REQUIRED",
        "message": "프로젝트 키를 입력해 주세요.",
        "status_label": "프로젝트 키 필요",
    }
    _assert_empty_staging(validation_staging)
    assert not (tmp_path / ".localdata").exists()


@pytest.mark.required_test_id("DQ-P1-UIINTAKE-003")
def test_job_read_is_opaque_and_exactly_project_isolated(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    manager = _manager(
        service=SyntheticSuccessfulService(),
        staging_root=tmp_path / "staging",
    )

    with _client(manager) as client:
        accepted = _post(client, b"synthetic workbook")
        assert accepted.status_code == 202
        job_id = accepted.json()["job_id"]
        correct = _await_terminal(client, job_id=job_id)
        wrong_project = client.get(
            f"/api/v1/intake/jobs/{job_id}",
            params={"project_key": "project-beta"},
        )
        unknown = client.get(
            f"/api/v1/intake/jobs/{'f' * 32}",
            params={"project_key": "project-alpha"},
        )
        malformed_project = client.get(
            f"/api/v1/intake/jobs/{job_id}",
            params={"project_key": "../project-alpha"},
        )
        missing_project = client.get(f"/api/v1/intake/jobs/{job_id}")

    assert _JOB_ID.fullmatch(job_id)
    assert correct["project_key"] == "project-alpha"
    for response in (wrong_project, unknown, malformed_project):
        assert response.status_code == 404
        assert response.json() == {
            "detail": {
                "code": "INTAKE_JOB_NOT_FOUND",
                "message": "해당 프로젝트에서 접수 작업을 찾을 수 없습니다.",
                "status_label": "접수 작업 없음",
            }
        }
        assert job_id not in response.text
    assert missing_project.status_code == 400
    assert missing_project.json()["detail"]["code"] == "PROJECT_KEY_REQUIRED"
    assert not (tmp_path / ".localdata").exists()


@pytest.mark.required_test_id("DQ-P1-UIINTAKE-004")
def test_scan_and_unexpected_failures_are_redacted_and_terminal_cleanup_is_exact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    staging_root = tmp_path / "known-failure-staging"
    store = OriginalFileStore(tmp_path / "o", max_bytes=2 * 1024 * 1024)
    manager = _manager(
        service=ManualWorkbookIngestionService(file_store=store, scanner=RejectingScanner()),
        staging_root=staging_root,
    )
    payload = _workbook_bytes()

    with _client(manager) as client:
        accepted = _post(client, payload, filename="실패원본.xlsx")
        assert accepted.status_code == 202
        failed = _await_terminal(client, job_id=accepted.json()["job_id"])

    assert failed["status"] == "RAW_PRESERVED_SCAN_FAILED", failed["issues"]
    assert failed["receipt"]["original_filename"] == "실패원본.xlsx"
    assert failed["scan"] is None
    assert failed["issues"] == [
        {
            "code": "PRIVATE_SCAN_FAILURE",
            "message": "통합 문서 스캔 결과를 확인해 주세요.",
            "location": "OQC!P10",
        }
    ]
    failed_text = str(failed).lower()
    assert "secret.xlsx" not in failed_text
    assert "\\private\\" not in failed_text
    assert str(tmp_path).lower() not in failed_text
    assert (
        len(
            store.list_receipts(
                project_key="project-alpha",
                content_sha256=failed["receipt"]["content_sha256"],
            )
        )
        == 1
    )
    _assert_empty_staging(staging_root)

    unexpected_staging = tmp_path / "unexpected-failure-staging"
    unexpected_manager = _manager(
        service=ExplodingService(),
        staging_root=unexpected_staging,
    )
    with _client(unexpected_manager) as client:
        accepted = _post(client, b"synthetic workbook")
        unexpected = _await_terminal(client, job_id=accepted.json()["job_id"])

    assert unexpected["status"] == "ERROR"
    assert unexpected["receipt"] is None
    assert unexpected["issues"] == [
        {
            "code": "INTAKE_UNEXPECTED_FAILURE",
            "message": "접수 처리 중 예상하지 못한 오류가 발생했습니다.",
            "location": None,
        }
    ]
    assert "private" not in str(unexpected).lower()
    assert str(tmp_path).lower() not in str(unexpected).lower()
    _assert_empty_staging(unexpected_staging)
    assert not (tmp_path / ".localdata").exists()
