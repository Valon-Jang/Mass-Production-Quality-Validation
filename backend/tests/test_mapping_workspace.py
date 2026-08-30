from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook

from app.api.mapping import create_mapping_router
from app.application.mapping_template_commands import (
    ApproveMappingTemplateRevisionCommand,
    CreateMappingTemplateRevisionCommand,
    MappingTemplateCommandService,
    ReviewMappingTemplateRevisionCommand,
)
from app.application.mapping_workspace import (
    MappingWorkspaceAIState,
    MappingWorkspaceMode,
    MappingWorkspaceNotFoundError,
    MappingWorkspaceRequest,
    MappingWorkspaceService,
    MappingWorkspaceState,
)
from app.domain.identity import Actor, ActorKind, Role
from app.domain.mapping import (
    CellAddress,
    HeaderTokenAssertion,
    IdentifierKind,
    IdentifierMapping,
    InspectionRowMapping,
    MappingTemplate,
    MappingTemplateStatus,
    MergeSignatureAssertion,
    RowStructureAssertion,
    SheetStructureAssertion,
    WorkbookFingerprint,
)
from app.domain.source_file import SourceFileReceipt
from app.domain.workbook_scan import ScanPolicy, SheetKind
from app.infrastructure.database import Base, Database
from app.infrastructure.excel.workbook_scanner import OpenpyxlWorkbookScanner
from app.infrastructure.file_store import XLSX_MIME, OriginalFileStore

_NOW = datetime(2026, 8, 15, 9, 0, tzinfo=UTC)
_PROJECT = "project-alpha"
_SUPPLIER_SCOPE = "supplier-alpha"


def _database(path: Path) -> Database:
    database = Database(f"sqlite+pysqlite:///{path.as_posix()}")
    Base.metadata.create_all(database.engine)
    return database


def _workbook(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "OQC"
    sheet["A1"] = "OQC Report"
    sheet["A2"] = date(2026, 8, 15)
    sheet["B2"] = "Supplier Alpha"
    sheet["A4"] = "Length"
    sheet["B4"] = "=1+0.25"
    sheet["C4"] = "검토"
    workbook.save(path)
    workbook.close()


def _store_and_receipt(tmp_path: Path) -> tuple[OriginalFileStore, SourceFileReceipt]:
    source = tmp_path / "w.xlsx"
    _workbook(source)
    store = OriginalFileStore(tmp_path / "o", max_bytes=5_000_000)
    receipt = store.preserve(
        project_key=_PROJECT,
        source=source,
        declared_mime_type=XLSX_MIME,
    )
    return store, receipt


def _service(database: Database, store: OriginalFileStore) -> MappingWorkspaceService:
    return MappingWorkspaceService(
        database=database,
        file_store=store,
        scanner=OpenpyxlWorkbookScanner(),
        scan_policy=ScanPolicy(),
    )


def _request(
    receipt: SourceFileReceipt,
    *,
    project_key: str = _PROJECT,
    receipt_id: str | None = None,
    content_sha256: str | None = None,
    cell_limit: int = 120,
) -> MappingWorkspaceRequest:
    return MappingWorkspaceRequest(
        project_key=project_key,
        receipt_id=receipt_id or receipt.receipt_id,
        content_sha256=content_sha256 or receipt.content_sha256,
        supplier_scope=_SUPPLIER_SCOPE,
        cell_offset=0,
        cell_limit=cell_limit,
    )


def _address(coordinate: str) -> CellAddress:
    return CellAddress(sheet_name="OQC", coordinate=coordinate)


def _template() -> MappingTemplate:
    row = InspectionRowMapping(
        row_key="length-row",
        item=_address("A4"),
        sample_cells=(_address("B4"),),
        supplier_result=_address("C4"),
    )
    return MappingTemplate(
        template_id="oqc-layout",
        schema_version="1",
        revision=1,
        status=MappingTemplateStatus.DRAFT,
        project_key=_PROJECT,
        supplier_scope=_SUPPLIER_SCOPE,
        supplier_source_aliases=("Supplier Alpha",),
        approved_by=None,
        approved_at=None,
        effective_from=date(2026, 1, 1),
        effective_to=None,
        fingerprint=WorkbookFingerprint(
            header_tokens=(HeaderTokenAssertion(_address("A1"), "OQC Report"),),
            sheet_structures=(
                SheetStructureAssertion(
                    sheet_name="OQC",
                    expected_position=0,
                    expected_kind=SheetKind.WORKSHEET,
                    expected_visibility="visible",
                    expected_used_range="A1:C4",
                ),
            ),
            merge_signatures=(MergeSignatureAssertion("OQC", ()),),
            row_structures=(
                RowStructureAssertion(
                    row_key=row.row_key,
                    sheet_name="OQC",
                    row_index=4,
                    expected_non_empty_cells=row.all_addresses,
                ),
            ),
        ),
        identifiers=(
            IdentifierMapping(IdentifierKind.INSPECTION_DATE, _address("A2")),
            IdentifierMapping(IdentifierKind.SUPPLIER, _address("B2")),
        ),
        inspection_rows=(row,),
    )


@pytest.mark.required_test_id("DQ-P1-MAPUI-001")
def test_manual_review_returns_bounded_exact_source_cells_without_ai(tmp_path: Path) -> None:
    store, receipt = _store_and_receipt(tmp_path)
    database = _database(tmp_path / "mapping.sqlite3")
    try:
        result = _service(database, store).preview(_request(receipt, cell_limit=2))
    finally:
        database.dispose()

    assert result.state == MappingWorkspaceState.MAPPING_REQUIRED
    assert result.mode == MappingWorkspaceMode.MANUAL_SOURCE_REVIEW
    assert result.ai_state == MappingWorkspaceAIState.NOT_CALLED
    assert result.source_cells.total == 6
    assert len(result.source_cells.cells) == 2
    assert result.source_cells.truncated is True
    assert [(cell.sheet_name, cell.coordinate) for cell in result.source_cells.cells] == [
        ("OQC", "A1"),
        ("OQC", "A2"),
    ]
    assert not result.draft_command_available
    assert not result.long_confirmation_available
    assert not result.official_values_created


@pytest.mark.required_test_id("DQ-P1-MAPUI-002")
def test_receipt_replay_survives_service_and_database_restart(tmp_path: Path) -> None:
    _, receipt = _store_and_receipt(tmp_path)
    database_path = tmp_path / "restart.sqlite3"
    first_database = _database(database_path)
    first_database.dispose()

    restarted_database = Database(f"sqlite+pysqlite:///{database_path.as_posix()}")
    restarted_store = OriginalFileStore(tmp_path / "o", max_bytes=5_000_000)
    try:
        result = _service(restarted_database, restarted_store).preview(_request(receipt))
    finally:
        restarted_database.dispose()

    formula = next(cell for cell in result.source_cells.cells if cell.coordinate == "B4")
    assert result.receipt.receipt_id == receipt.receipt_id
    assert result.scan.source_sha256_before == receipt.content_sha256
    assert formula.formula_text == "=1+0.25"


@pytest.mark.required_test_id("DQ-P1-MAPUI-003")
def test_exact_receipt_identity_mismatch_is_fail_closed(tmp_path: Path) -> None:
    store, receipt = _store_and_receipt(tmp_path)
    database = _database(tmp_path / "mismatch.sqlite3")
    service = _service(database, store)
    try:
        for request in (
            _request(receipt, project_key="project-other"),
            _request(receipt, receipt_id="receipt-other"),
            _request(receipt, content_sha256="b" * 64),
        ):
            with pytest.raises(MappingWorkspaceNotFoundError) as captured:
                service.preview(request)
            assert captured.value.code == "MAPPING_RECEIPT_NOT_FOUND"
    finally:
        database.dispose()


@pytest.mark.required_test_id("DQ-P1-MAPUI-004")
def test_exact_approved_template_builds_preview_without_ai_or_persistence(tmp_path: Path) -> None:
    store, receipt = _store_and_receipt(tmp_path)
    database = _database(tmp_path / "approved.sqlite3")
    commands = MappingTemplateCommandService(database, clock=lambda: _NOW)
    reviewer = Actor("local-reviewer", ActorKind.LOCAL_OWNER, frozenset({Role.REVIEWER}))
    admin = Actor("local-admin", ActorKind.LOCAL_OWNER, frozenset({Role.ADMIN}))
    created = commands.create_revision(
        CreateMappingTemplateRevisionCommand(
            template=_template(),
            expected_history_row_version=0,
            actor=reviewer,
            reason="Synthetic source-cell Mapping draft.",
            source_reference=f"receipt:{receipt.receipt_id}",
        )
    )
    reviewed = commands.review(
        ReviewMappingTemplateRevisionCommand(
            project_key=_PROJECT,
            supplier_scope=_SUPPLIER_SCOPE,
            template_id="oqc-layout",
            revision=1,
            expected_history_row_version=created.history_row_version,
            expected_revision_row_version=created.revision_row_version,
            actor=reviewer,
            reason="Synthetic exact source cells reviewed.",
        )
    )
    commands.approve(
        ApproveMappingTemplateRevisionCommand(
            project_key=_PROJECT,
            supplier_scope=_SUPPLIER_SCOPE,
            template_id="oqc-layout",
            revision=1,
            expected_history_row_version=reviewed.history_row_version,
            expected_revision_row_version=reviewed.revision_row_version,
            actor=admin,
            reason="Synthetic revision approved for deterministic test.",
        )
    )
    service = _service(database, store)
    try:
        result = service.preview(_request(receipt))
        application = FastAPI()
        application.include_router(create_mapping_router(service))
        with TestClient(application) as client:
            response = client.get(
                f"/api/v1/mapping/receipts/{receipt.receipt_id}/preview",
                params={
                    "project_key": _PROJECT,
                    "content_sha256": receipt.content_sha256,
                    "supplier_scope": _SUPPLIER_SCOPE,
                },
            )
        payload = response.json()
    finally:
        database.dispose()

    assert result.state == MappingWorkspaceState.PREVIEW_READY
    assert result.mode == MappingWorkspaceMode.APPROVED_TEMPLATE
    assert result.ai_state == MappingWorkspaceAIState.NOT_CALLED
    assert result.template is not None and result.template.status == "APPROVED"
    assert result.preview is not None
    assert result.preview.inspection_rows[0].samples[0].formula_text == "=1+0.25"
    assert not result.long_confirmation_available
    assert not result.official_values_created
    assert not result.calculations_performed
    assert response.status_code == 200
    assert payload["preview"]["inspection_rows"][0]["samples"][0]["formula_text"] == "=1+0.25"
    assert {
        "method",
        "instrument",
        "specification",
        "tolerance",
        "minimum",
        "maximum",
        "section",
        "category",
        "unit",
        "measurement_point",
        "measurement_location",
        "cavity",
        "target",
        "lsl",
        "usl",
        "source_spec_revision",
        "supplier_result",
    }.issubset(payload["preview"]["inspection_rows"][0])
    assert any(issue["code"] == "FORMULA_CACHE_MISSING" for issue in payload["scan"]["issues"])
