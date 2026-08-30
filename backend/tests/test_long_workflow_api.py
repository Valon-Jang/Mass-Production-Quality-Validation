from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook  # type: ignore[import-untyped]
from sqlalchemy import func, select

from app.api.long import create_long_router
from app.application.long_workflow import LongWorkflowService
from app.application.mapping_registration import (
    CellSelection,
    CreateMappingDraftRequest,
    IdentifierSelection,
    InspectionRowSelection,
    MappingRegistrationService,
    MappingWorkflowRequest,
)
from app.application.mapping_template_commands import MappingTemplateCommandService
from app.application.mapping_workspace import MappingWorkspaceService
from app.application.master_config_commands import (
    ApproveCanonicalRowBindingRevisionCommand,
    CreateCanonicalInspectionItemCommand,
    CreateCanonicalModelCommand,
    CreateCanonicalModelPartCommand,
    CreateCanonicalRowBindingRevisionCommand,
    CreateCanonicalSupplierCommand,
    MasterConfigCommandService,
    ReviewCanonicalRowBindingRevisionCommand,
    SetInspectionItemDispositionCommand,
)
from app.domain.identity import LOCAL_OWNER
from app.domain.long_format import CanonicalRowBindingKey, MeasurementMode, SamplePolicy
from app.domain.mapping import IdentifierKind
from app.domain.master_config import (
    CanonicalInspectionItem,
    CanonicalModel,
    CanonicalModelPart,
    CanonicalRowBindingRevision,
    CanonicalSupplier,
    ConfigurationRevisionStatus,
    InspectionItemDisposition,
)
from app.domain.source_file import SourceFileReceipt
from app.domain.workbook_scan import ScanPolicy
from app.infrastructure.database import Base, Database
from app.infrastructure.excel.workbook_scanner import OpenpyxlWorkbookScanner
from app.infrastructure.file_store import XLSX_MIME, OriginalFileStore
from app.infrastructure.long_format import (
    LongIngestionJobRow,
    LongInspectionResultRow,
    LongMeasurementRow,
    LongSourceFileRow,
    LongSourceSheetRow,
    OqcLotRow,
)
from app.infrastructure.mapping_templates import MappingTemplateRepository

_NOW = datetime(2026, 8, 15, 10, 30, tzinfo=UTC)
_PROJECT = "project-alpha"
_SUPPLIER_SCOPE = "supplier-alpha"
_TEMPLATE_HEX = "c" * 32
_MODEL_SOURCE = "MODEL-A"
_LOT_SOURCE = "LOT-001"


@dataclass(frozen=True, slots=True)
class _Fixture:
    database: Database
    database_path: Path
    store: OriginalFileStore
    store_root: Path
    receipt: SourceFileReceipt
    source_path: Path
    template_id: str
    service: LongWorkflowService

    def close(self) -> None:
        self.database.dispose()
        _remove_store_root(self.store_root)


def _database(path: Path) -> Database:
    database = Database(f"sqlite+pysqlite:///{path.as_posix()}")
    Base.metadata.create_all(database.engine)
    return database


def _short_store_root(tmp_path: Path) -> Path:
    del tmp_path
    return Path(tempfile.mkdtemp(prefix="dq-long-ui-"))


def _remove_store_root(store_root: Path) -> None:
    resolved = store_root.resolve()
    temporary_root = Path(tempfile.gettempdir()).resolve()
    if resolved.parent != temporary_root or not resolved.name.startswith("dq-long-ui-"):
        raise AssertionError("refusing to remove a non-test File Store root")
    shutil.rmtree(resolved)


def _workbook(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "OQC"
    sheet["A1"] = "OQC Report"
    sheet["A2"] = "Inspection Date"
    sheet["B2"] = date(2026, 8, 15)
    sheet["A3"] = "Supplier"
    sheet["B3"] = "Supplier Alpha"
    sheet["A4"] = "Model"
    sheet["B4"] = _MODEL_SOURCE
    sheet["A5"] = "LOT"
    sheet["B5"] = _LOT_SOURCE
    for row_number, item, target, sample_1, sample_2 in (
        (7, "Length", 10.0, 10.1, 9.9),
        (8, "Width", 20.0, 20.1, 19.9),
    ):
        sheet[f"A{row_number}"] = "DIMENSION"
        sheet[f"B{row_number}"] = "CTQ"
        sheet[f"C{row_number}"] = item
        sheet[f"D{row_number}"] = "mm"
        sheet[f"E{row_number}"] = target
        sheet[f"F{row_number}"] = target - 0.5
        sheet[f"G{row_number}"] = target + 0.5
        sheet[f"H{row_number}"] = sample_1
        sheet[f"I{row_number}"] = sample_2
        sheet[f"J{row_number}"] = "PASS"
        sheet[f"K{row_number}"] = "Cavity-1"
        sheet[f"L{row_number}"] = "Caliper"
    workbook.save(path)
    workbook.close()


def _selection(coordinate: str) -> CellSelection:
    return CellSelection(sheet_name="OQC", coordinate=coordinate)


def _row_selection(row_key: str, row_number: int) -> InspectionRowSelection:
    return InspectionRowSelection(
        row_key=row_key,
        item=_selection(f"C{row_number}"),
        method=_selection(f"L{row_number}"),
        sample_cells=(_selection(f"H{row_number}"), _selection(f"I{row_number}")),
        supplier_result=_selection(f"J{row_number}"),
        section=_selection(f"A{row_number}"),
        category=_selection(f"B{row_number}"),
        unit=_selection(f"D{row_number}"),
        cavity=_selection(f"K{row_number}"),
        target=_selection(f"E{row_number}"),
        lsl=_selection(f"F{row_number}"),
        usl=_selection(f"G{row_number}"),
    )


def _approve_mapping(
    database: Database,
    store: OriginalFileStore,
    receipt: SourceFileReceipt,
) -> str:
    repository = MappingTemplateRepository()
    commands = MappingTemplateCommandService(
        database,
        repository=repository,
        clock=lambda: _NOW,
    )
    service = MappingRegistrationService(
        database=database,
        file_store=store,
        scanner=OpenpyxlWorkbookScanner(),
        scan_policy=ScanPolicy(),
        mapping_repository=repository,
        command_service=commands,
        id_factory=lambda: _TEMPLATE_HEX,
    )
    created = service.create_draft(
        CreateMappingDraftRequest(
            project_key=_PROJECT,
            receipt_id=receipt.receipt_id,
            content_sha256=receipt.content_sha256,
            supplier_scope=_SUPPLIER_SCOPE,
            effective_from=date(2026, 1, 1),
            effective_to=None,
            expected_history_row_version=0,
            reason="Create exact synthetic Long UI Mapping.",
            header_assertion_cells=(_selection("A1"), _selection("A2")),
            identifiers=(
                IdentifierSelection(IdentifierKind.INSPECTION_DATE, _selection("B2")),
                IdentifierSelection(IdentifierKind.SUPPLIER, _selection("B3")),
                IdentifierSelection(IdentifierKind.MODEL, _selection("B4")),
                IdentifierSelection(IdentifierKind.LOT_NUMBER, _selection("B5")),
            ),
            inspection_rows=(
                _row_selection("row-length", 7),
                _row_selection("row-width", 8),
            ),
        )
    )
    template_id = created.workflow.template_id
    reviewed = service.review(
        template_id=template_id,
        revision=1,
        request=MappingWorkflowRequest(
            project_key=_PROJECT,
            receipt_id=receipt.receipt_id,
            content_sha256=receipt.content_sha256,
            supplier_scope=_SUPPLIER_SCOPE,
            expected_history_row_version=created.workflow.history_row_version,
            expected_revision_row_version=created.workflow.revision_row_version,
            reason="Review exact synthetic Long UI Mapping.",
        ),
    )
    service.approve(
        template_id=template_id,
        revision=1,
        request=MappingWorkflowRequest(
            project_key=_PROJECT,
            receipt_id=receipt.receipt_id,
            content_sha256=receipt.content_sha256,
            supplier_scope=_SUPPLIER_SCOPE,
            expected_history_row_version=reviewed.workflow.history_row_version,
            expected_revision_row_version=reviewed.workflow.revision_row_version,
            reason="Approve exact synthetic Long UI Mapping.",
        ),
    )
    return template_id


def _approve_bindings(
    database: Database,
    *,
    template_id: str,
    row_keys: tuple[str, ...],
) -> None:
    if not row_keys:
        return
    commands = MasterConfigCommandService(database, clock=lambda: _NOW)
    commands.create_model(
        CreateCanonicalModelCommand(
            model=CanonicalModel(_PROJECT, "model-a", "Synthetic Model A"),
            actor=LOCAL_OWNER,
            reason="Create synthetic canonical model.",
        )
    )
    commands.create_supplier(
        CreateCanonicalSupplierCommand(
            supplier=CanonicalSupplier(_PROJECT, "supplier-a", "Synthetic Supplier A"),
            actor=LOCAL_OWNER,
            reason="Create synthetic canonical supplier.",
        )
    )
    commands.create_model_part(
        CreateCanonicalModelPartCommand(
            model_part=CanonicalModelPart(
                _PROJECT,
                "model-a",
                "model-a:part-top",
                "Synthetic Top Part",
            ),
            actor=LOCAL_OWNER,
            reason="Create synthetic canonical model-part.",
        )
    )
    item_by_row = {"row-length": "item-length", "row-width": "item-width"}
    for row_key in row_keys:
        item_key = item_by_row[row_key]
        commands.create_inspection_item(
            CreateCanonicalInspectionItemCommand(
                item=CanonicalInspectionItem(
                    _PROJECT,
                    "model-a:part-top",
                    item_key,
                    f"Synthetic {row_key}",
                ),
                actor=LOCAL_OWNER,
                reason="Create synthetic inspection item candidate.",
            )
        )
        commands.set_item_disposition(
            SetInspectionItemDispositionCommand(
                project_key=_PROJECT,
                item_key=item_key,
                disposition=InspectionItemDisposition.MANAGED,
                expected_row_version=1,
                actor=LOCAL_OWNER,
                reason="Explicitly manage synthetic inspection item.",
            )
        )
        binding = CanonicalRowBindingRevision(
            key=CanonicalRowBindingKey(
                project_key=_PROJECT,
                supplier_scope=_SUPPLIER_SCOPE,
                template_id=template_id,
                template_revision=1,
                row_key=row_key,
            ),
            binding_revision=1,
            status=ConfigurationRevisionStatus.DRAFT,
            effective_from=date(2026, 1, 1),
            effective_to=None,
            source_model_values=(_MODEL_SOURCE,),
            canonical_model_key="model-a",
            canonical_supplier_key="supplier-a",
            canonical_model_part_key="model-a:part-top",
            canonical_item_key=item_key,
            sample_policy=SamplePolicy.AT_LEAST_ONE,
            measurement_mode=MeasurementMode.NUMERIC,
            change_reason="Synthetic exact row binding.",
            source_reference=f"synthetic://long-ui/{row_key}",
        )
        created = commands.create_row_binding_revision(
            CreateCanonicalRowBindingRevisionCommand(
                binding=binding,
                expected_history_row_version=0,
                actor=LOCAL_OWNER,
                reason="Create exact synthetic row binding.",
            )
        )
        reviewed = commands.review_row_binding_revision(
            ReviewCanonicalRowBindingRevisionCommand(
                key=binding.key,
                binding_revision=1,
                expected_history_row_version=created.history_row_version,
                expected_revision_row_version=created.revision_row_version,
                actor=LOCAL_OWNER,
                reason="Review exact synthetic row binding.",
            )
        )
        commands.approve_row_binding_revision(
            ApproveCanonicalRowBindingRevisionCommand(
                key=binding.key,
                binding_revision=1,
                expected_history_row_version=reviewed.history_row_version,
                expected_revision_row_version=reviewed.revision_row_version,
                actor=LOCAL_OWNER,
                reason="Approve exact synthetic row binding.",
            )
        )


def _workflow_service(
    database: Database,
    store: OriginalFileStore,
) -> LongWorkflowService:
    workspace = MappingWorkspaceService(
        database=database,
        file_store=store,
        scanner=OpenpyxlWorkbookScanner(),
        scan_policy=ScanPolicy(),
    )
    return LongWorkflowService(database=database, mapping_workspace=workspace)


def _fixture(tmp_path: Path, *, binding_rows: tuple[str, ...]) -> _Fixture:
    tmp_path.mkdir(parents=True, exist_ok=True)
    source_path = tmp_path / "long-ui.xlsx"
    _workbook(source_path)
    store_root = _short_store_root(tmp_path)
    store = OriginalFileStore(store_root, max_bytes=5_000_000)
    receipt = store.preserve(
        project_key=_PROJECT,
        source=source_path,
        declared_mime_type=XLSX_MIME,
        model_candidates=(_MODEL_SOURCE,),
        lot_candidates=(_LOT_SOURCE,),
    )
    database_path = tmp_path / "long-ui.sqlite3"
    database = _database(database_path)
    template_id = _approve_mapping(database, store, receipt)
    _approve_bindings(database, template_id=template_id, row_keys=binding_rows)
    return _Fixture(
        database=database,
        database_path=database_path,
        store=store,
        store_root=store_root,
        receipt=receipt,
        source_path=source_path,
        template_id=template_id,
        service=_workflow_service(database, store),
    )


def _client(service: LongWorkflowService) -> TestClient:
    application = FastAPI()
    application.include_router(create_long_router(service))
    return TestClient(application)


def _candidate_body(receipt: SourceFileReceipt) -> dict[str, object]:
    return {
        "project_key": _PROJECT,
        "receipt_id": receipt.receipt_id,
        "content_sha256": receipt.content_sha256,
        "supplier_scope": _SUPPLIER_SCOPE,
    }


def _confirm_body(
    receipt: SourceFileReceipt,
    candidate_digest: str,
    *,
    confirmed: bool = True,
) -> dict[str, object]:
    return {
        **_candidate_body(receipt),
        "candidate_digest": candidate_digest,
        "confirmed": confirmed,
    }


def _candidate(client: TestClient, receipt: SourceFileReceipt) -> dict[str, Any]:
    response = client.post("/api/v1/long/candidates", json=_candidate_body(receipt))
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def _long_counts(database: Database) -> tuple[int, int, int, int, int, int]:
    with database.session() as session:
        values = (
            session.scalar(select(func.count()).select_from(LongSourceFileRow)),
            session.scalar(select(func.count()).select_from(LongSourceSheetRow)),
            session.scalar(select(func.count()).select_from(LongIngestionJobRow)),
            session.scalar(select(func.count()).select_from(OqcLotRow)),
            session.scalar(select(func.count()).select_from(LongInspectionResultRow)),
            session.scalar(select(func.count()).select_from(LongMeasurementRow)),
        )
    return cast(tuple[int, int, int, int, int, int], values)


@pytest.mark.required_test_id("DQ-P1-LONGUI-001")
def test_candidate_rebuild_is_exact_read_only_and_exposes_safe_provenance(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, binding_rows=())
    try:
        before = _long_counts(fixture.database)
        with _client(fixture.service) as client:
            payload = _candidate(client, fixture.receipt)
        after = _long_counts(fixture.database)
        candidate = payload["candidate"]
        assert before == after == (0, 0, 0, 0, 0, 0)
        assert payload["persistence"] is None
        assert candidate["project_key"] == _PROJECT
        assert candidate["supplier_scope"] == _SUPPLIER_SCOPE
        assert candidate["receipt"] == {
            "receipt_id": fixture.receipt.receipt_id,
            "content_sha256": fixture.receipt.content_sha256,
            "original_filename": "long-ui.xlsx",
            "size_bytes": fixture.receipt.size_bytes,
        }
        assert candidate["mapping"]["template_id"] == fixture.template_id
        assert candidate["mapping"]["schema_version"] == "2"
        assert candidate["mapping"]["revision"] == 1
        assert candidate["mapping"]["source_inspection_date"] == "2026-08-15"
        assert len(candidate["candidate_digest"]) == 64
        assert candidate["capabilities"] == {
            "can_confirm": True,
            "confirm_requires_digest": True,
            "auto_binding": False,
            "idempotency_managed_by_server": True,
        }
        assert not candidate["official_values_created"]
        assert not candidate["calculations_performed"]
        assert not candidate["auto_valid"]
        assert not candidate["ai_called"]
    finally:
        fixture.close()


@pytest.mark.required_test_id("DQ-P1-LONGUI-002")
def test_approved_bindings_make_deterministic_loadable_pending_rows(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path, binding_rows=("row-length", "row-width"))
    try:
        with _client(fixture.service) as client:
            first = _candidate(client, fixture.receipt)
            second = _candidate(client, fixture.receipt)
        candidate = first["candidate"]
        assert candidate["candidate_digest"] == second["candidate"]["candidate_digest"]
        assert candidate["state"] == "LOAD_CANDIDATE_READY"
        assert candidate["binding_catalog_revision"].startswith("sha256:")
        assert candidate["row_count"] == 2
        assert candidate["loadable_row_count"] == 2
        assert candidate["held_row_count"] == 0
        assert [
            (item["kind"], item["raw_value"]["value"]) for item in candidate["identifiers"]
        ] == [
            ("INSPECTION_DATE", "2026-08-15T00:00:00"),
            ("SUPPLIER", "Supplier Alpha"),
            ("MODEL", _MODEL_SOURCE),
            ("LOT_NUMBER", _LOT_SOURCE),
        ]
        assert [row["state"] for row in candidate["rows"]] == [
            "LOADABLE_PENDING",
            "LOADABLE_PENDING",
        ]
        assert [row["pending_data_status"] for row in candidate["rows"]] == [
            "PENDING",
            "PENDING",
        ]
        assert [row["measurement_count"] for row in candidate["rows"]] == [2, 2]
        assert candidate["rows"][0]["measurement_cells"] == [
            {"sheet_name": "OQC", "coordinate": "H7"},
            {"sheet_name": "OQC", "coordinate": "I7"},
        ]
        assert candidate["rows"][0]["binding"] == {
            "binding_revision": 1,
            "canonical_model_key": "model-a",
            "canonical_supplier_key": "supplier-a",
            "canonical_model_part_key": "model-a:part-top",
            "canonical_item_key": "item-length",
            "measurement_mode": "NUMERIC",
            "sample_policy": "AT_LEAST_ONE",
            "approved_by": LOCAL_OWNER.actor_id,
            "approved_at": _NOW.isoformat().replace("+00:00", "Z"),
            "effective_from": "2026-01-01",
            "effective_to": None,
        }
        assert candidate["issues"] == []
        assert _long_counts(fixture.database) == (0, 0, 0, 0, 0, 0)
    finally:
        fixture.close()


@pytest.mark.required_test_id("DQ-P1-LONGUI-003")
def test_missing_binding_is_typed_partial_or_global_hold_without_auto_creation(
    tmp_path: Path,
) -> None:
    partial = _fixture(tmp_path / "partial", binding_rows=("row-length",))
    global_hold = _fixture(tmp_path / "global", binding_rows=())
    try:
        with _client(partial.service) as client:
            partial_payload = _candidate(client, partial.receipt)["candidate"]
        with _client(global_hold.service) as client:
            global_payload = _candidate(client, global_hold.receipt)["candidate"]
        assert partial_payload["state"] == "PARTIAL_HOLD"
        assert partial_payload["loadable_row_count"] == 1
        assert partial_payload["held_row_count"] == 1
        assert partial_payload["rows"][0]["binding"] is not None
        assert partial_payload["rows"][1]["binding"] is None
        assert partial_payload["rows"][1]["state"] == "ROW_HELD"
        assert partial_payload["rows"][1]["pending_data_status"] == "HELD"
        assert [issue["code"] for issue in partial_payload["rows"][1]["issues"]] == [
            "CANONICAL_ROW_BINDING_MISSING"
        ]
        assert global_payload["state"] == "LOAD_HELD"
        assert global_payload["loadable_row_count"] == 0
        assert global_payload["held_row_count"] == 2
        assert all(row["binding"] is None for row in global_payload["rows"])
        assert _long_counts(partial.database) == (0, 0, 0, 0, 0, 0)
        assert _long_counts(global_hold.database) == (0, 0, 0, 0, 0, 0)
    finally:
        partial.close()
        global_hold.close()


@pytest.mark.required_test_id("DQ-P1-LONGUI-004")
def test_explicit_confirmation_persists_only_pending_and_held_framework_states(
    tmp_path: Path,
) -> None:
    partial = _fixture(tmp_path / "partial-confirm", binding_rows=("row-length",))
    held = _fixture(tmp_path / "held-confirm", binding_rows=())
    try:
        with _client(partial.service) as client:
            candidate = _candidate(client, partial.receipt)["candidate"]
            response = client.post(
                "/api/v1/long/confirmations",
                json=_confirm_body(partial.receipt, candidate["candidate_digest"]),
            )
        assert response.status_code == 200, response.text
        persistence = response.json()["persistence"]
        assert persistence["status"] == "PARTIAL_HELD"
        assert persistence["counts"] == {
            "lot_count": 2,
            "result_count": 2,
            "measurement_count": 4,
            "held_result_count": 1,
        }
        assert persistence["pending_only"] is True
        assert persistence["official_values_created"] is False
        assert persistence["calculations_performed"] is False
        assert persistence["auto_valid"] is False
        with partial.database.session() as session:
            result_rows = session.scalars(
                select(LongInspectionResultRow).order_by(LongInspectionResultRow.source_row_key)
            ).all()
            measurement_statuses = set(
                session.scalars(select(LongMeasurementRow.data_status)).all()
            )
            standardized = set(session.scalars(select(LongMeasurementRow.standardized_value)).all())
        assert [(row.source_row_key, row.data_status) for row in result_rows] == [
            ("row-length", "PENDING"),
            ("row-width", "HELD"),
        ]
        held_result = result_rows[1]
        assert held_result.binding_snapshot is None
        assert held_result.binding_snapshot_sha256 is None
        assert held_result.binding_revision is None
        assert held_result.canonical_model_part_key is None
        assert held_result.canonical_item_key is None
        assert measurement_statuses == {"PENDING", "HELD"}
        assert standardized == {None}

        with _client(held.service) as client:
            held_candidate = _candidate(client, held.receipt)["candidate"]
            held_response = client.post(
                "/api/v1/long/confirmations",
                json=_confirm_body(held.receipt, held_candidate["candidate_digest"]),
            )
        assert held_response.status_code == 200, held_response.text
        assert held_response.json()["persistence"]["status"] == "HELD"
        assert held_response.json()["persistence"]["counts"] == {
            "lot_count": 0,
            "result_count": 0,
            "measurement_count": 0,
            "held_result_count": 0,
        }
        assert _long_counts(held.database)[:3] == (1, 1, 1)
        assert _long_counts(held.database)[3:] == (0, 0, 0)
    finally:
        partial.close()
        held.close()


@pytest.mark.required_test_id("DQ-P1-LONGUI-005")
def test_confirmation_stale_digest_scope_and_receipt_tamper_are_fail_closed(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, binding_rows=("row-length", "row-width"))
    try:
        with _client(fixture.service) as client:
            candidate = _candidate(client, fixture.receipt)["candidate"]
            digest = candidate["candidate_digest"]

            false_confirmation = client.post(
                "/api/v1/long/confirmations",
                json=_confirm_body(fixture.receipt, digest, confirmed=False),
            )
            assert false_confirmation.status_code == 400
            assert false_confirmation.json()["detail"]["code"] == (
                "EXPLICIT_LONG_CONFIRMATION_REQUIRED"
            )

            tampered_digest = f"{'0' if digest[0] != '0' else '1'}{digest[1:]}"
            stale = client.post(
                "/api/v1/long/confirmations",
                json=_confirm_body(fixture.receipt, tampered_digest),
            )
            assert stale.status_code == 409
            assert stale.json()["detail"]["code"] == "LONG_CANDIDATE_STALE"

            wrong_project = _candidate_body(fixture.receipt)
            wrong_project["project_key"] = "project-other"
            wrong_project.update({"candidate_digest": digest, "confirmed": True})
            scoped = client.post("/api/v1/long/confirmations", json=wrong_project)
            assert scoped.status_code == 404
            assert scoped.json()["detail"]["code"] == "LONG_RECEIPT_NOT_FOUND"

            second_receipt = fixture.store.preserve(
                project_key=_PROJECT,
                source=fixture.source_path,
                declared_mime_type=XLSX_MIME,
                model_candidates=(_MODEL_SOURCE,),
                lot_candidates=(_LOT_SOURCE,),
            )
            receipt_mismatch = client.post(
                "/api/v1/long/confirmations",
                json=_confirm_body(second_receipt, digest),
            )
            assert receipt_mismatch.status_code == 409
            assert receipt_mismatch.json()["detail"]["code"] == "LONG_CANDIDATE_STALE"
        assert _long_counts(fixture.database) == (0, 0, 0, 0, 0, 0)
        assert str(tmp_path) not in stale.text
    finally:
        fixture.close()


@pytest.mark.required_test_id("DQ-P1-LONGUI-006")
def test_restart_confirmation_replays_same_job_without_duplicate_long_rows(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, binding_rows=("row-length", "row-width"))
    with _client(fixture.service) as client:
        candidate = _candidate(client, fixture.receipt)["candidate"]
        body = _confirm_body(fixture.receipt, candidate["candidate_digest"])
        first_response = client.post("/api/v1/long/confirmations", json=body)
    assert first_response.status_code == 200, first_response.text
    first = first_response.json()["persistence"]
    assert first["status"] == "COMPLETED_PENDING"
    assert first["replayed"] is False
    assert first["counts"] == {
        "lot_count": 1,
        "result_count": 2,
        "measurement_count": 4,
        "held_result_count": 0,
    }
    fixture.database.dispose()

    restarted_database = Database(f"sqlite+pysqlite:///{fixture.database_path.as_posix()}")
    restarted_store = OriginalFileStore(fixture.store_root, max_bytes=5_000_000)
    try:
        restarted_service = _workflow_service(restarted_database, restarted_store)
        with _client(restarted_service) as client:
            replay_response = client.post("/api/v1/long/confirmations", json=body)
        assert replay_response.status_code == 200, replay_response.text
        replay = replay_response.json()["persistence"]
        assert replay["replayed"] is True
        assert replay["ingestion_job_id"] == first["ingestion_job_id"]
        assert replay["source_file_id"] == first["source_file_id"]
        assert replay["counts"] == first["counts"]
        assert _long_counts(restarted_database) == (1, 1, 1, 1, 2, 4)
    finally:
        restarted_database.dispose()
        _remove_store_root(fixture.store_root)
