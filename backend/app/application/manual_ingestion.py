"""Manual single-workbook route through the canonical store-to-scan pipeline."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from app.domain.source_file import SourceFileReceipt
from app.domain.workbook_scan import (
    ScanPolicy,
    WorkbookScan,
    WorkbookScanFailure,
    WorkbookScannerPort,
)
from app.infrastructure.file_store import OriginalFileStore


class ManualIngestionStatus(StrEnum):
    STORED_AND_SCANNED = "STORED_AND_SCANNED"
    RAW_PRESERVED_SCAN_FAILED = "RAW_PRESERVED_SCAN_FAILED"


@dataclass(frozen=True, slots=True)
class ManualIngestionRequest:
    project_key: str
    source: Path
    declared_mime_type: str
    scan_policy: ScanPolicy
    model_candidates: tuple[str, ...] = ()
    lot_candidates: tuple[str, ...] = ()
    reserved_receipt_id: str | None = None
    reserved_received_at: datetime | None = None
    on_preserved: Callable[[SourceFileReceipt], None] | None = None


@dataclass(frozen=True, slots=True)
class ManualIngestionOutcome:
    status: ManualIngestionStatus
    receipt: SourceFileReceipt
    scan: WorkbookScan | None = None
    scan_failure: WorkbookScanFailure | None = None

    def __post_init__(self) -> None:
        has_scan = self.scan is not None
        has_failure = self.scan_failure is not None
        if has_scan == has_failure:
            raise ValueError("exactly one of scan or scan_failure must be present")
        if self.status == ManualIngestionStatus.STORED_AND_SCANNED and not has_scan:
            raise ValueError("successful outcome requires a scan")
        if self.status == ManualIngestionStatus.RAW_PRESERVED_SCAN_FAILED and not has_failure:
            raise ValueError("failed outcome requires a scan failure")


class ManualIngestionIntegrityError(RuntimeError):
    """The stored content receipt and scanner evidence disagree."""

    def __init__(self, receipt: SourceFileReceipt) -> None:
        self.receipt = receipt
        super().__init__("stored source hash and workbook scan hash do not match")


class ManualIngestionUnexpectedScanError(RuntimeError):
    """Unexpected scanner failure after the raw source was already preserved."""

    def __init__(self, receipt: SourceFileReceipt) -> None:
        self.receipt = receipt
        super().__init__("unexpected scanner failure after raw source preservation")


class ManualWorkbookIngestionService:
    """Use the same immutable store and deterministic scanner for manual intake."""

    def __init__(
        self,
        *,
        file_store: OriginalFileStore,
        scanner: WorkbookScannerPort,
    ) -> None:
        self._file_store = file_store
        self._scanner = scanner

    def ingest(self, request: ManualIngestionRequest) -> ManualIngestionOutcome:
        if request.reserved_receipt_id is None and request.reserved_received_at is None:
            receipt = self._file_store.preserve(
                project_key=request.project_key,
                source=request.source,
                declared_mime_type=request.declared_mime_type,
                model_candidates=request.model_candidates,
                lot_candidates=request.lot_candidates,
            )
        else:
            receipt = self._file_store.preserve(
                project_key=request.project_key,
                source=request.source,
                declared_mime_type=request.declared_mime_type,
                model_candidates=request.model_candidates,
                lot_candidates=request.lot_candidates,
                receipt_id=request.reserved_receipt_id,
                received_at=request.reserved_received_at,
            )
        if request.on_preserved is not None:
            request.on_preserved(receipt)

        with self._file_store.open_source(receipt) as stored_source:
            try:
                scan = self._scanner.scan_stream(
                    stored_source,
                    source_name=receipt.original_filename,
                    policy=request.scan_policy,
                )
            except WorkbookScanFailure as failure:
                return ManualIngestionOutcome(
                    status=ManualIngestionStatus.RAW_PRESERVED_SCAN_FAILED,
                    receipt=receipt,
                    scan_failure=failure,
                )
            except Exception as error:
                raise ManualIngestionUnexpectedScanError(receipt) from error

        if (
            scan.source_sha256_before != receipt.content_sha256
            or scan.source_sha256_after != receipt.content_sha256
        ):
            raise ManualIngestionIntegrityError(receipt)

        return ManualIngestionOutcome(
            status=ManualIngestionStatus.STORED_AND_SCANNED,
            receipt=receipt,
            scan=scan,
        )
