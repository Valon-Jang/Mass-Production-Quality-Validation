from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import BinaryIO

import pytest
from openpyxl import Workbook  # type: ignore[import-untyped]

from app.application.manual_ingestion import (
    ManualIngestionRequest,
    ManualIngestionStatus,
    ManualIngestionUnexpectedScanError,
    ManualWorkbookIngestionService,
)
from app.domain.source_file import SourceFileReceipt
from app.domain.workbook_scan import (
    IssueSeverity,
    ScanIssue,
    ScanPolicy,
    SourceLocation,
    WorkbookScan,
    WorkbookScanFailure,
    WorkbookScanFailureStatus,
)
from app.infrastructure.excel import OpenpyxlWorkbookScanner
from app.infrastructure.file_store import (
    XLSX_MIME,
    OriginalFileStore,
    StoredSourceIntegrityError,
    StoredSourceNotFoundError,
)


def _save_workbook(path: Path) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "OQC"
    sheet["A1"] = "MODEL-A"
    sheet["A2"] = "LOT-001"
    sheet["B3"] = 10.1234
    workbook.save(path)
    return path.read_bytes()


@pytest.mark.required_test_id("DQ-P1-ROUTE-001")
def test_manual_route_uses_project_isolated_store_then_the_same_scanner(
    tmp_path: Path,
) -> None:
    source = tmp_path / "oqc.xlsx"
    original = _save_workbook(source)
    store = OriginalFileStore(tmp_path / "store", max_bytes=1024 * 1024)
    service = ManualWorkbookIngestionService(
        file_store=store,
        scanner=OpenpyxlWorkbookScanner(),
    )
    request = ManualIngestionRequest(
        project_key="project-alpha",
        source=source,
        declared_mime_type=XLSX_MIME,
        scan_policy=ScanPolicy(max_cells=10_000),
        model_candidates=("MODEL-A",),
        lot_candidates=("LOT-001",),
    )

    alpha = service.ingest(request)
    beta = service.ingest(
        ManualIngestionRequest(
            project_key="project-beta",
            source=source,
            declared_mime_type=XLSX_MIME,
            scan_policy=ScanPolicy(max_cells=10_000),
        )
    )

    assert alpha.status == ManualIngestionStatus.STORED_AND_SCANNED
    assert alpha.scan is not None
    assert alpha.scan_failure is None
    assert alpha.scan.source_sha256_before == alpha.receipt.content_sha256
    assert alpha.scan.source_sha256_after == alpha.receipt.content_sha256
    assert alpha.receipt.project_key == "project-alpha"
    assert beta.receipt.project_key == "project-beta"
    assert alpha.receipt.content_sha256 == beta.receipt.content_sha256
    assert (
        len(
            store.list_receipts(
                project_key="project-alpha",
                content_sha256=alpha.receipt.content_sha256,
            )
        )
        == 1
    )
    assert (
        len(
            store.list_receipts(
                project_key="project-beta",
                content_sha256=beta.receipt.content_sha256,
            )
        )
        == 1
    )
    assert source.read_bytes() == original


class RejectingScanner:
    def scan(self, source: Path, policy: ScanPolicy | None = None) -> WorkbookScan:
        raise AssertionError("manual route must scan the stored stream")

    def scan_stream(
        self,
        source: BinaryIO,
        *,
        source_name: str,
        policy: ScanPolicy | None = None,
    ) -> WorkbookScan:
        del source, source_name, policy
        raise WorkbookScanFailure(
            WorkbookScanFailureStatus.CORRUPT_OOXML,
            ScanIssue(
                code="SYNTHETIC_SCAN_FAILURE",
                severity=IssueSeverity.ERROR,
                message="synthetic scanner rejection",
                location=SourceLocation.workbook(),
            ),
        )


@pytest.mark.required_test_id("DQ-P1-FSTORE-004")
def test_known_scan_failure_returns_raw_preserved_outcome(tmp_path: Path) -> None:
    source = tmp_path / "oqc.xlsx"
    original = _save_workbook(source)
    store = OriginalFileStore(tmp_path / "store", max_bytes=1024 * 1024)
    service = ManualWorkbookIngestionService(file_store=store, scanner=RejectingScanner())

    outcome = service.ingest(
        ManualIngestionRequest(
            project_key="project-alpha",
            source=source,
            declared_mime_type=XLSX_MIME,
            scan_policy=ScanPolicy(max_cells=10_000),
        )
    )

    assert outcome.status == ManualIngestionStatus.RAW_PRESERVED_SCAN_FAILED
    assert outcome.scan is None
    assert outcome.scan_failure is not None
    assert outcome.scan_failure.status == WorkbookScanFailureStatus.CORRUPT_OOXML
    with store.open_source(outcome.receipt) as stored_source:
        assert stored_source.read() == original


class UnexpectedScanner(RejectingScanner):
    def scan_stream(
        self,
        source: BinaryIO,
        *,
        source_name: str,
        policy: ScanPolicy | None = None,
    ) -> WorkbookScan:
        del source, source_name, policy
        raise RuntimeError("synthetic implementation defect")


@pytest.mark.required_test_id("DQ-P1-ROUTE-002")
def test_unexpected_scan_error_exposes_preserved_receipt_and_keeps_cause(
    tmp_path: Path,
) -> None:
    source = tmp_path / "oqc.xlsx"
    original = _save_workbook(source)
    store = OriginalFileStore(tmp_path / "store", max_bytes=1024 * 1024)
    service = ManualWorkbookIngestionService(file_store=store, scanner=UnexpectedScanner())

    with pytest.raises(ManualIngestionUnexpectedScanError) as captured:
        service.ingest(
            ManualIngestionRequest(
                project_key="project-alpha",
                source=source,
                declared_mime_type=XLSX_MIME,
                scan_policy=ScanPolicy(max_cells=10_000),
            )
        )

    assert isinstance(captured.value.__cause__, RuntimeError)
    with store.open_source(captured.value.receipt) as stored_source:
        assert stored_source.read() == original


@pytest.mark.parametrize(
    ("sabotage", "expected_error"),
    [
        ("missing", StoredSourceNotFoundError),
        ("integrity", StoredSourceIntegrityError),
    ],
)
def test_stored_blob_errors_are_not_misclassified_as_scanner_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sabotage: str,
    expected_error: type[Exception],
) -> None:
    source = tmp_path / "oqc.xlsx"
    _save_workbook(source)
    store_root = tmp_path / "store"
    store = OriginalFileStore(store_root, max_bytes=1024 * 1024)
    preserve = store.preserve

    def preserve_then_sabotage(
        *,
        project_key: str,
        source: Path,
        declared_mime_type: str,
        model_candidates: Sequence[str] = (),
        lot_candidates: Sequence[str] = (),
    ) -> SourceFileReceipt:
        receipt = preserve(
            project_key=project_key,
            source=source,
            declared_mime_type=declared_mime_type,
            model_candidates=model_candidates,
            lot_candidates=lot_candidates,
        )
        blob = next(store_root.rglob(f"{receipt.content_sha256}{receipt.canonical_extension}"))
        if sabotage == "missing":
            blob.unlink()
        else:
            with blob.open("ab") as stream:
                stream.write(b"synthetic corruption")
        return receipt

    monkeypatch.setattr(store, "preserve", preserve_then_sabotage)
    service = ManualWorkbookIngestionService(file_store=store, scanner=UnexpectedScanner())

    with pytest.raises(expected_error) as captured:
        service.ingest(
            ManualIngestionRequest(
                project_key="project-alpha",
                source=source,
                declared_mime_type=XLSX_MIME,
                scan_policy=ScanPolicy(max_cells=10_000),
            )
        )

    assert not isinstance(captured.value, ManualIngestionUnexpectedScanError)
