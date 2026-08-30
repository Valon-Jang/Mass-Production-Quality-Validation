"""Canonical bounded manual Store -> Scan -> Mapping Preview route.

The route deliberately has no durable processing claim, result cache, or
cross-process exclusion.  Repeating a manual intake therefore preserves a new
receipt while the Original File Store may reuse the same content-addressed
blob.  Durable replay/idempotency belongs to a later persistent workflow.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import ClassVar

from app.application.manual_ingestion import (
    ManualIngestionRequest,
    ManualIngestionStatus,
    ManualWorkbookIngestionService,
)
from app.application.mapping_preview import (
    MappingTemplateCatalog,
    build_mapping_preview,
)
from app.domain.mapping import MappingPreviewRequest, MappingPreviewResult, MappingPreviewState
from app.domain.source_file import SourceFileReceipt
from app.domain.workbook_scan import ScanPolicy, WorkbookScan, WorkbookScanFailure


class StoreScanMappingStatus(StrEnum):
    PREVIEW_READY = "PREVIEW_READY"
    RAW_PRESERVED_SCAN_FAILED = "RAW_PRESERVED_SCAN_FAILED"
    RAW_PRESERVED_MAPPING_REQUIRED = "RAW_PRESERVED_MAPPING_REQUIRED"


class StoreScanMappingStage(StrEnum):
    MAPPING = "MAPPING"


@dataclass(frozen=True, slots=True)
class ResolvedMappingScope:
    """A caller-resolved project and supplier scope; this route does not infer either."""

    project_key: str
    supplier_scope: str

    def __post_init__(self) -> None:
        for field_name in ("project_key", "supplier_scope"):
            value = getattr(self, field_name)
            if not value.strip() or value != value.strip():
                raise ValueError(f"{field_name} must be a resolved, non-blank value")


@dataclass(frozen=True, slots=True)
class StoreScanMappingRequest:
    scope: ResolvedMappingScope
    source: Path
    declared_mime_type: str
    scan_policy: ScanPolicy
    model_candidates: tuple[str, ...] = ()
    lot_candidates: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StoreScanMappingOutcome:
    """One provenance-bound result without creating any official OQC values."""

    status: StoreScanMappingStatus
    scope: ResolvedMappingScope
    receipt: SourceFileReceipt
    scan: WorkbookScan | None = None
    scan_failure: WorkbookScanFailure | None = None
    mapping_result: MappingPreviewResult | None = None

    def __post_init__(self) -> None:
        if self.receipt.project_key != self.scope.project_key:
            raise ValueError("stored receipt belongs to a different resolved project")

        if self.status == StoreScanMappingStatus.RAW_PRESERVED_SCAN_FAILED:
            if (
                self.scan is not None
                or self.scan_failure is None
                or self.mapping_result is not None
            ):
                raise ValueError("scan-failed outcome must contain only receipt and scan failure")
            return

        if self.scan is None or self.scan_failure is not None or self.mapping_result is None:
            raise ValueError("mapped outcome requires receipt, scan, and mapping result only")
        self._validate_scan_provenance()

        if self.mapping_result.official_values_created:
            raise ValueError("Mapping Preview must not create official values")
        if self.mapping_result.calculations_performed:
            raise ValueError("Mapping Preview must not perform calculations")

        if self.status == StoreScanMappingStatus.PREVIEW_READY:
            if (
                self.mapping_result.state != MappingPreviewState.PREVIEW_READY
                or self.mapping_result.preview is None
                or self.mapping_result.issues
            ):
                raise ValueError("preview-ready outcome requires one issue-free preview")
            self._validate_ready_preview_provenance()
            return

        if self.status == StoreScanMappingStatus.RAW_PRESERVED_MAPPING_REQUIRED:
            if (
                self.mapping_result.state != MappingPreviewState.MAPPING_REQUIRED
                or self.mapping_result.preview is not None
                or not self.mapping_result.issues
            ):
                raise ValueError("mapping-required outcome requires explicit issues and no preview")
            return

        raise ValueError("unsupported Store -> Scan -> Mapping outcome status")

    @property
    def model_candidates(self) -> tuple[str, ...]:
        """Return intake candidates without treating them as mapped or official values."""

        return self.receipt.model_candidates

    @property
    def lot_candidates(self) -> tuple[str, ...]:
        """Return intake candidates without treating them as mapped or official values."""

        return self.receipt.lot_candidates

    def _validate_scan_provenance(self) -> None:
        if self.scan is None:  # pragma: no cover - guarded by the caller
            raise AssertionError("scan provenance requires a scan")
        if not (
            self.receipt.content_sha256
            == self.scan.source_sha256_before
            == self.scan.source_sha256_after
        ):
            raise ValueError("receipt and scan hashes do not identify the same immutable source")
        if self.receipt.original_filename != self.scan.source_name:
            raise ValueError("receipt and scan source names do not match")
        if self.receipt.size_bytes != self.scan.source_size_bytes:
            raise ValueError("receipt and scan source sizes do not match")

    def _validate_ready_preview_provenance(self) -> None:
        if self.scan is None or self.mapping_result is None:  # pragma: no cover - guarded above
            raise AssertionError("ready preview provenance requires scan and mapping result")
        preview = self.mapping_result.preview
        if preview is None:  # pragma: no cover - guarded above
            raise AssertionError("ready preview provenance requires a preview")
        if not (
            self.receipt.content_sha256
            == preview.source_sha256_before
            == preview.source_sha256_after
        ):
            raise ValueError("receipt and Mapping Preview hashes do not match")
        if preview.source_name != self.receipt.original_filename:
            raise ValueError("receipt and Mapping Preview source names do not match")
        if preview.source_size_bytes != self.receipt.size_bytes:
            raise ValueError("receipt and Mapping Preview source sizes do not match")
        if preview.project_key != self.receipt.project_key:
            raise ValueError("receipt and Mapping Preview projects do not match")
        if preview.supplier_scope != self.scope.supplier_scope:
            raise ValueError("Mapping Preview supplier scope differs from the resolved scope")
        if preview.source_issues != self.scan.issues:
            raise ValueError("Mapping Preview did not preserve scanner issues exactly")


class StoreScanMappingUnexpectedError(RuntimeError):
    """An implementation defect after raw preservation, with stage evidence."""

    def __init__(
        self,
        *,
        receipt: SourceFileReceipt,
        scan: WorkbookScan,
        stage: StoreScanMappingStage,
    ) -> None:
        self.receipt = receipt
        self.scan = scan
        self.stage = stage
        super().__init__(f"unexpected {stage.value.lower()} failure after raw source preservation")


MappingPreviewBuilder = Callable[
    [WorkbookScan, MappingPreviewRequest, MappingTemplateCatalog],
    MappingPreviewResult,
]


class StoreScanMappingService:
    """Compose the existing manual Store -> Scan route with Mapping Preview.

    This bounded local service is recomputable but does not provide durable
    replay idempotency or cross-process exclusion.  A persistent catalog may
    be a fully materialized immutable snapshot and remain usable after its
    database session closes.  That snapshot does not observe later database
    changes automatically; the caller must explicitly load and inject a new
    catalog instance.
    """

    durable_replay_supported: ClassVar[bool] = False
    cross_process_exclusion_supported: ClassVar[bool] = False

    def __init__(
        self,
        *,
        ingestion_service: ManualWorkbookIngestionService,
        registry: MappingTemplateCatalog,
        preview_builder: MappingPreviewBuilder = build_mapping_preview,
    ) -> None:
        self._ingestion_service = ingestion_service
        self._registry = registry
        self._preview_builder = preview_builder

    def execute(self, request: StoreScanMappingRequest) -> StoreScanMappingOutcome:
        ingestion = self._ingestion_service.ingest(
            ManualIngestionRequest(
                project_key=request.scope.project_key,
                source=request.source,
                declared_mime_type=request.declared_mime_type,
                scan_policy=request.scan_policy,
                model_candidates=request.model_candidates,
                lot_candidates=request.lot_candidates,
            )
        )

        if ingestion.status == ManualIngestionStatus.RAW_PRESERVED_SCAN_FAILED:
            return StoreScanMappingOutcome(
                status=StoreScanMappingStatus.RAW_PRESERVED_SCAN_FAILED,
                scope=request.scope,
                receipt=ingestion.receipt,
                scan_failure=ingestion.scan_failure,
            )

        scan = ingestion.scan
        if scan is None:  # pragma: no cover - protected by ManualIngestionOutcome
            raise AssertionError("successful manual ingestion did not return a scan")

        try:
            mapping_result = self._preview_builder(
                scan,
                MappingPreviewRequest(
                    project_key=ingestion.receipt.project_key,
                    supplier_scope=request.scope.supplier_scope,
                ),
                self._registry,
            )
            if mapping_result.state == MappingPreviewState.PREVIEW_READY:
                status = StoreScanMappingStatus.PREVIEW_READY
            elif mapping_result.state == MappingPreviewState.MAPPING_REQUIRED:
                status = StoreScanMappingStatus.RAW_PRESERVED_MAPPING_REQUIRED
            else:  # pragma: no cover - enum currently has exactly two states
                raise ValueError("Mapping Preview returned an unsupported state")
            return StoreScanMappingOutcome(
                status=status,
                scope=request.scope,
                receipt=ingestion.receipt,
                scan=scan,
                mapping_result=mapping_result,
            )
        except Exception as error:
            raise StoreScanMappingUnexpectedError(
                receipt=ingestion.receipt,
                scan=scan,
                stage=StoreScanMappingStage.MAPPING,
            ) from error
