from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import BinaryIO

import pytest
from openpyxl import Workbook  # type: ignore[import-untyped]

from app.application.manual_ingestion import ManualWorkbookIngestionService
from app.application.mapping_preview import (
    InMemoryMappingTemplateRegistry,
    MappingTemplateCatalog,
)
from app.application.mapping_template_commands import (
    ApproveMappingTemplateRevisionCommand,
    CreateMappingTemplateRevisionCommand,
    MappingTemplateCommandService,
    ReviewMappingTemplateRevisionCommand,
)
from app.application.store_scan_mapping import (
    MappingPreviewBuilder,
    ResolvedMappingScope,
    StoreScanMappingOutcome,
    StoreScanMappingRequest,
    StoreScanMappingService,
    StoreScanMappingStage,
    StoreScanMappingStatus,
    StoreScanMappingUnexpectedError,
)
from app.domain.identity import Actor, ActorKind, Role
from app.domain.mapping import (
    CellAddress,
    HeaderTokenAssertion,
    IdentifierKind,
    IdentifierMapping,
    InspectionRowMapping,
    MappingIssueCode,
    MappingPreviewRequest,
    MappingPreviewResult,
    MappingPreviewState,
    MappingTemplate,
    MappingTemplateStatus,
    MergeSignatureAssertion,
    RowStructureAssertion,
    SheetStructureAssertion,
    WorkbookFingerprint,
)
from app.domain.workbook_scan import (
    IssueSeverity,
    ScanIssue,
    ScanPolicy,
    SheetKind,
    SourceLocation,
    WorkbookScan,
    WorkbookScanFailure,
    WorkbookScanFailureStatus,
)
from app.infrastructure.database import Base, Database
from app.infrastructure.excel import OpenpyxlWorkbookScanner
from app.infrastructure.file_store import XLSX_MIME, OriginalFileStore
from app.infrastructure.mapping_templates import MappingTemplateRepository

_SHEET = "Synthetic Report"
_PROJECT = "project-alpha"
_SUPPLIER_SCOPE = "supplier-scope-alpha"
_SOURCE_SUPPLIER = "SUPPLIER-SYNTHETIC"
_WORKFLOW_TIME = datetime(2026, 8, 15, 7, 0, tzinfo=UTC)
_REVIEWER = Actor(
    actor_id="synthetic-reviewer",
    kind=ActorKind.LOCAL_OWNER,
    roles=frozenset({Role.REVIEWER}),
)
_ADMIN = Actor(
    actor_id="synthetic-admin",
    kind=ActorKind.LOCAL_OWNER,
    roles=frozenset({Role.ADMIN}),
)


def _address(coordinate: str) -> CellAddress:
    return CellAddress(sheet_name=_SHEET, coordinate=coordinate)


def _save_synthetic_workbook(path: Path, *, supplier: str = _SOURCE_SUPPLIER) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = _SHEET
    sheet["A1"] = "Synthetic Quality Report"
    sheet["A2"] = "Supplier"
    sheet["B2"] = supplier
    sheet["C2"] = "Model"
    sheet["D2"] = "MODEL-SOURCE"
    sheet["E2"] = "Lot"
    sheet["F2"] = "LOT-SOURCE"
    sheet["G2"] = "Inspection Date"
    sheet["H2"] = "2026-06-01"
    sheet["A3"] = "Item"
    sheet["B3"] = "Sample 1"
    sheet["C3"] = "Supplier Result"
    sheet["A4"] = "Synthetic dimension"
    sheet["B4"] = 10.25
    sheet["C4"] = "OK"
    workbook.save(path)
    return path.read_bytes()


def _template(
    *,
    project_key: str = _PROJECT,
    supplier_scope: str = _SUPPLIER_SCOPE,
) -> MappingTemplate:
    inspection_row = InspectionRowMapping(
        row_key="synthetic-row",
        item=_address("A4"),
        sample_cells=(_address("B4"),),
        supplier_result=_address("C4"),
    )
    return MappingTemplate(
        template_id="synthetic-route-template",
        schema_version="1",
        revision=1,
        status=MappingTemplateStatus.APPROVED,
        project_key=project_key,
        supplier_scope=supplier_scope,
        supplier_source_aliases=(_SOURCE_SUPPLIER,),
        approved_by="synthetic-reviewer",
        approved_at=datetime(2026, 1, 1, tzinfo=UTC),
        effective_from=date(2026, 1, 1),
        effective_to=date(2026, 12, 31),
        fingerprint=WorkbookFingerprint(
            header_tokens=(
                HeaderTokenAssertion(_address("A1"), "Synthetic Quality Report"),
                HeaderTokenAssertion(_address("A2"), "Supplier"),
                HeaderTokenAssertion(_address("C2"), "Model"),
                HeaderTokenAssertion(_address("E2"), "Lot"),
                HeaderTokenAssertion(_address("G2"), "Inspection Date"),
                HeaderTokenAssertion(_address("A3"), "Item"),
                HeaderTokenAssertion(_address("C3"), "Supplier Result"),
            ),
            sheet_structures=(
                SheetStructureAssertion(
                    sheet_name=_SHEET,
                    expected_position=0,
                    expected_kind=SheetKind.WORKSHEET,
                    expected_visibility="visible",
                    expected_used_range="A1:H4",
                ),
            ),
            merge_signatures=(MergeSignatureAssertion(_SHEET, ()),),
            row_structures=(
                RowStructureAssertion(
                    row_key=inspection_row.row_key,
                    sheet_name=_SHEET,
                    row_index=4,
                    expected_non_empty_cells=inspection_row.all_addresses,
                ),
            ),
        ),
        identifiers=(
            IdentifierMapping(IdentifierKind.SUPPLIER, _address("B2")),
            IdentifierMapping(IdentifierKind.MODEL, _address("D2")),
            IdentifierMapping(IdentifierKind.LOT_NUMBER, _address("F2")),
            IdentifierMapping(IdentifierKind.INSPECTION_DATE, _address("H2")),
        ),
        inspection_rows=(inspection_row,),
    )


def _registry(*templates: MappingTemplate) -> InMemoryMappingTemplateRegistry:
    registry = InMemoryMappingTemplateRegistry()
    for template in templates:
        registry.register(template)
    return registry


def _request(source: Path) -> StoreScanMappingRequest:
    return StoreScanMappingRequest(
        scope=ResolvedMappingScope(
            project_key=_PROJECT,
            supplier_scope=_SUPPLIER_SCOPE,
        ),
        source=source,
        declared_mime_type=XLSX_MIME,
        scan_policy=ScanPolicy(max_cells=10_000),
        model_candidates=("MODEL-CANDIDATE",),
        lot_candidates=("LOT-CANDIDATE",),
    )


def _route(
    store_root: Path,
    registry: MappingTemplateCatalog,
) -> tuple[StoreScanMappingService, OriginalFileStore]:
    store = OriginalFileStore(store_root, max_bytes=1024 * 1024)
    ingestion = ManualWorkbookIngestionService(
        file_store=store,
        scanner=OpenpyxlWorkbookScanner(),
    )
    return (
        StoreScanMappingService(ingestion_service=ingestion, registry=registry),
        store,
    )


@pytest.mark.required_test_id("DQ-P1-ROUTE-003")
def test_canonical_route_binds_receipt_scan_and_ready_preview_provenance(
    tmp_path: Path,
) -> None:
    source = tmp_path / "synthetic-ready.xlsx"
    original = _save_synthetic_workbook(source)
    expected_hash = hashlib.sha256(original).hexdigest()
    service, _ = _route(tmp_path / "s", _registry(_template()))

    outcome = service.execute(_request(source))

    assert outcome.status == StoreScanMappingStatus.PREVIEW_READY
    assert outcome.scan is not None
    assert outcome.scan_failure is None
    assert outcome.mapping_result is not None
    assert outcome.mapping_result.state == MappingPreviewState.PREVIEW_READY
    assert outcome.mapping_result.preview is not None
    preview = outcome.mapping_result.preview
    assert (
        outcome.receipt.content_sha256
        == outcome.scan.source_sha256_before
        == outcome.scan.source_sha256_after
        == preview.source_sha256_before
        == preview.source_sha256_after
        == expected_hash
    )
    assert outcome.receipt.original_filename == outcome.scan.source_name == preview.source_name
    assert outcome.receipt.size_bytes == outcome.scan.source_size_bytes == preview.source_size_bytes
    assert outcome.receipt.project_key == outcome.scope.project_key == preview.project_key
    assert preview.supplier_scope == outcome.scope.supplier_scope
    assert preview.source_issues == outcome.scan.issues

    identifiers = {identifier.kind: identifier.evidence for identifier in preview.identifiers}
    assert outcome.model_candidates == ("MODEL-CANDIDATE",)
    assert outcome.lot_candidates == ("LOT-CANDIDATE",)
    assert identifiers[IdentifierKind.MODEL].raw_value == "MODEL-SOURCE"
    assert identifiers[IdentifierKind.LOT_NUMBER].raw_value == "LOT-SOURCE"
    assert outcome.model_candidates != (identifiers[IdentifierKind.MODEL].raw_value,)
    assert outcome.lot_candidates != (identifiers[IdentifierKind.LOT_NUMBER].raw_value,)
    assert outcome.mapping_result.official_values_created is False
    assert outcome.mapping_result.calculations_performed is False
    assert source.read_bytes() == original

    with pytest.raises(ValueError, match="source sizes"):
        StoreScanMappingOutcome(
            status=outcome.status,
            scope=outcome.scope,
            receipt=outcome.receipt,
            scan=replace(outcome.scan, source_size_bytes=outcome.scan.source_size_bytes + 1),
            mapping_result=outcome.mapping_result,
        )
    for changed_preview in (
        replace(preview, source_sha256_after="0" * 64),
        replace(preview, source_name="different.xlsx"),
        replace(preview, source_size_bytes=preview.source_size_bytes + 1),
        replace(preview, project_key="different-project"),
        replace(preview, supplier_scope="different-supplier-scope"),
    ):
        with pytest.raises(ValueError):
            StoreScanMappingOutcome(
                status=outcome.status,
                scope=outcome.scope,
                receipt=outcome.receipt,
                scan=outcome.scan,
                mapping_result=replace(outcome.mapping_result, preview=changed_preview),
            )


class RejectingScanner:
    def scan(self, source: Path, policy: ScanPolicy | None = None) -> WorkbookScan:
        del source, policy
        raise AssertionError("canonical route must scan the preserved stream")

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
                code="SYNTHETIC_SCAN_REJECTION",
                severity=IssueSeverity.ERROR,
                message="synthetic scanner rejection",
                location=SourceLocation.workbook(),
            ),
        )


@pytest.mark.required_test_id("DQ-P1-ROUTE-004")
def test_known_scan_failure_preserves_raw_and_never_invokes_mapper(tmp_path: Path) -> None:
    source = tmp_path / "synthetic-scan-failure.xlsx"
    original = _save_synthetic_workbook(source)
    store = OriginalFileStore(tmp_path / "s", max_bytes=1024 * 1024)
    ingestion = ManualWorkbookIngestionService(file_store=store, scanner=RejectingScanner())
    mapper_called = False

    def must_not_map(
        scan: WorkbookScan,
        request: MappingPreviewRequest,
        registry: MappingTemplateCatalog,
    ) -> MappingPreviewResult:
        nonlocal mapper_called
        del scan, request, registry
        mapper_called = True
        raise AssertionError("mapper was invoked after scan failure")

    service = StoreScanMappingService(
        ingestion_service=ingestion,
        registry=_registry(_template()),
        preview_builder=must_not_map,
    )

    outcome = service.execute(_request(source))

    assert outcome.status == StoreScanMappingStatus.RAW_PRESERVED_SCAN_FAILED
    assert outcome.scan is None
    assert outcome.scan_failure is not None
    assert outcome.mapping_result is None
    assert mapper_called is False
    with store.open_source(outcome.receipt) as stored_source:
        assert stored_source.read() == original


@pytest.mark.required_test_id("DQ-P1-ROUTE-005")
def test_mapping_required_is_distinct_and_keeps_raw_plus_scan(tmp_path: Path) -> None:
    source = tmp_path / "synthetic-mapping-required.xlsx"
    original = _save_synthetic_workbook(source)
    service, store = _route(tmp_path / "s", _registry())

    outcome = service.execute(_request(source))

    assert outcome.status == StoreScanMappingStatus.RAW_PRESERVED_MAPPING_REQUIRED
    assert outcome.scan is not None
    assert outcome.scan_failure is None
    assert outcome.mapping_result is not None
    assert outcome.mapping_result.state == MappingPreviewState.MAPPING_REQUIRED
    assert outcome.mapping_result.preview is None
    assert {issue.code for issue in outcome.mapping_result.issues} == {
        MappingIssueCode.TEMPLATE_MISSING
    }
    assert outcome.mapping_result.official_values_created is False
    assert outcome.mapping_result.calculations_performed is False
    with store.open_source(outcome.receipt) as stored_source:
        assert stored_source.read() == original


@pytest.mark.required_test_id("DQ-P1-ROUTE-006")
def test_resolved_scope_is_required_and_supplier_or_project_mismatch_fails_closed(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="project_key"):
        ResolvedMappingScope(project_key=" ", supplier_scope=_SUPPLIER_SCOPE)
    with pytest.raises(ValueError, match="supplier_scope"):
        ResolvedMappingScope(project_key=_PROJECT, supplier_scope="")

    wrong_supplier_source = tmp_path / "synthetic-wrong-supplier.xlsx"
    _save_synthetic_workbook(wrong_supplier_source, supplier="UNREGISTERED-SUPPLIER")
    supplier_service, _ = _route(
        tmp_path / "s1",
        _registry(_template()),
    )
    supplier_outcome = supplier_service.execute(_request(wrong_supplier_source))
    assert supplier_outcome.status == StoreScanMappingStatus.RAW_PRESERVED_MAPPING_REQUIRED
    assert supplier_outcome.mapping_result is not None
    assert MappingIssueCode.SUPPLIER_EVIDENCE_MISMATCH in {
        issue.code for issue in supplier_outcome.mapping_result.issues
    }

    wrong_project_source = tmp_path / "synthetic-wrong-project.xlsx"
    _save_synthetic_workbook(wrong_project_source)
    project_service, _ = _route(
        tmp_path / "s2",
        _registry(_template(project_key="project-beta")),
    )
    project_outcome = project_service.execute(_request(wrong_project_source))
    assert project_outcome.status == StoreScanMappingStatus.RAW_PRESERVED_MAPPING_REQUIRED
    assert project_outcome.mapping_result is not None
    assert MappingIssueCode.PROJECT_SCOPE_MISMATCH in {
        issue.code for issue in project_outcome.mapping_result.issues
    }


@pytest.mark.required_test_id("DQ-P1-ROUTE-007")
def test_repeated_manual_intake_reuses_blob_but_keeps_receipts_and_limits_explicit(
    tmp_path: Path,
) -> None:
    source = tmp_path / "synthetic-repeat.xlsx"
    original = _save_synthetic_workbook(source)
    store_root = tmp_path / "s"
    service, store = _route(store_root, _registry(_template()))
    request = _request(source)

    first = service.execute(request)
    second = service.execute(request)

    assert first.status == second.status == StoreScanMappingStatus.PREVIEW_READY
    assert first.receipt.receipt_id != second.receipt.receipt_id
    assert first.receipt.blob_id == second.receipt.blob_id
    assert first.receipt.content_sha256 == second.receipt.content_sha256
    assert (
        len(
            store.list_receipts(
                project_key=_PROJECT,
                content_sha256=first.receipt.content_sha256,
            )
        )
        == 2
    )
    assert len(list(store_root.rglob(f"{first.receipt.content_sha256}.xlsx"))) == 1
    assert StoreScanMappingService.durable_replay_supported is False
    assert StoreScanMappingService.cross_process_exclusion_supported is False
    assert source.read_bytes() == original


@pytest.mark.required_test_id("DQ-P1-ROUTE-008")
def test_unexpected_mapper_defect_retains_receipt_scan_stage_and_cause(tmp_path: Path) -> None:
    source = tmp_path / "synthetic-mapper-defect.xlsx"
    original = _save_synthetic_workbook(source)
    store = OriginalFileStore(tmp_path / "s", max_bytes=1024 * 1024)
    ingestion = ManualWorkbookIngestionService(
        file_store=store,
        scanner=OpenpyxlWorkbookScanner(),
    )
    defect = RuntimeError("synthetic mapper defect")

    def explode(
        scan: WorkbookScan,
        request: MappingPreviewRequest,
        registry: MappingTemplateCatalog,
    ) -> MappingPreviewResult:
        del scan, request, registry
        raise defect

    preview_builder: MappingPreviewBuilder = explode
    service = StoreScanMappingService(
        ingestion_service=ingestion,
        registry=_registry(_template()),
        preview_builder=preview_builder,
    )

    with pytest.raises(StoreScanMappingUnexpectedError) as captured:
        service.execute(_request(source))

    error = captured.value
    assert error.stage == StoreScanMappingStage.MAPPING
    assert error.receipt.content_sha256 == error.scan.source_sha256_before
    assert error.receipt.content_sha256 == error.scan.source_sha256_after
    assert error.__cause__ is defect
    with store.open_source(error.receipt) as stored_source:
        assert stored_source.read() == original


@pytest.mark.required_test_id("DQ-P1-ROUTE-009")
def test_persisted_approved_catalog_routes_after_session_close_and_database_restart(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "mapping.sqlite3"
    database = Database(f"sqlite+pysqlite:///{database_path.as_posix()}")
    Base.metadata.create_all(database.engine)
    repository = MappingTemplateRepository()
    commands = MappingTemplateCommandService(
        database,
        repository=repository,
        clock=lambda: _WORKFLOW_TIME,
    )
    draft_template = replace(
        _template(),
        status=MappingTemplateStatus.DRAFT,
        approved_by=None,
        approved_at=None,
    )
    try:
        created = commands.create_revision(
            CreateMappingTemplateRevisionCommand(
                template=draft_template,
                expected_history_row_version=0,
                actor=_REVIEWER,
                reason="Create a source-verified synthetic Mapping Template.",
                source_reference="synthetic-route-fixture",
            )
        )
        assert created.template.status == MappingTemplateStatus.DRAFT

        reviewed = commands.review(
            ReviewMappingTemplateRevisionCommand(
                project_key=_PROJECT,
                supplier_scope=_SUPPLIER_SCOPE,
                template_id=draft_template.template_id,
                revision=draft_template.revision,
                expected_history_row_version=created.history_row_version,
                expected_revision_row_version=created.revision_row_version,
                actor=_REVIEWER,
                reason="Review the synthetic source cells and effective period.",
            )
        )
        assert reviewed.template.status == MappingTemplateStatus.REVIEWED

        approved = commands.approve(
            ApproveMappingTemplateRevisionCommand(
                project_key=_PROJECT,
                supplier_scope=_SUPPLIER_SCOPE,
                template_id=draft_template.template_id,
                revision=draft_template.revision,
                expected_history_row_version=reviewed.history_row_version,
                expected_revision_row_version=reviewed.revision_row_version,
                actor=_ADMIN,
                reason="Approve the reviewed synthetic Mapping Template.",
            )
        )
        assert approved.template.status == MappingTemplateStatus.APPROVED
        assert approved.template.approved_by == _ADMIN.actor_id

        with database.session() as session:
            first_catalog: MappingTemplateCatalog = repository.load_catalog(
                session,
                project_key=_PROJECT,
            )

        commands.create_revision(
            CreateMappingTemplateRevisionCommand(
                template=replace(
                    draft_template,
                    revision=2,
                    effective_from=date(2027, 1, 1),
                    effective_to=date(2027, 12, 31),
                ),
                expected_history_row_version=approved.history_row_version,
                actor=_REVIEWER,
                reason="Create a later synthetic draft after taking the snapshot.",
            )
        )
    finally:
        database.dispose()

    assert tuple(template.revision for template in first_catalog.templates) == (1,)
    source = tmp_path / "persistent-route.xlsx"
    original = _save_synthetic_workbook(source)
    first_service, _ = _route(tmp_path / "p1", first_catalog)
    first_outcome = first_service.execute(_request(source))
    assert first_outcome.status == StoreScanMappingStatus.PREVIEW_READY
    assert first_outcome.mapping_result is not None
    assert first_outcome.mapping_result.preview is not None
    assert first_outcome.mapping_result.preview.template_revision == 1
    assert source.read_bytes() == original

    restarted = Database(f"sqlite+pysqlite:///{database_path.as_posix()}")
    try:
        with restarted.session() as session:
            restarted_catalog: MappingTemplateCatalog = repository.load_catalog(
                session,
                project_key=_PROJECT,
            )
    finally:
        restarted.dispose()

    assert tuple(template.revision for template in first_catalog.templates) == (1,)
    assert tuple(template.revision for template in restarted_catalog.templates) == (1, 2)
    restarted_service, _ = _route(tmp_path / "p2", restarted_catalog)
    restarted_outcome = restarted_service.execute(_request(source))
    assert restarted_outcome.status == StoreScanMappingStatus.PREVIEW_READY
    assert restarted_outcome.mapping_result is not None
    assert restarted_outcome.mapping_result.preview is not None
    assert restarted_outcome.mapping_result.preview.template_revision == 1
    assert source.read_bytes() == original
