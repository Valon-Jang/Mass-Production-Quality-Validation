from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook  # type: ignore[import-untyped]
from sqlalchemy import func, select

from app.api.mapping import create_mapping_registration_router
from app.application.mapping_registration import MappingRegistrationService
from app.application.mapping_template_commands import MappingTemplateCommandService
from app.domain.identity import LOCAL_OWNER
from app.domain.mapping import MappingTemplateStatus
from app.domain.source_file import SourceFileReceipt
from app.domain.workbook_scan import ScanPolicy
from app.infrastructure.audit import AuditLog
from app.infrastructure.database import Base, Database
from app.infrastructure.excel.workbook_scanner import OpenpyxlWorkbookScanner
from app.infrastructure.file_store import XLSX_MIME, OriginalFileStore
from app.infrastructure.long_format import (
    LongInspectionResultRow,
    LongMeasurementRow,
    OqcLotRow,
)
from app.infrastructure.mapping_templates import MappingTemplateRepository

_NOW = datetime(2026, 8, 15, 12, 30, tzinfo=UTC)
_PROJECT = "project-alpha"
_SUPPLIER_SCOPE = "supplier-alpha"
_SUPPLIER_SOURCE = "Supplier Alpha  "


def _database(path: Path) -> Database:
    database = Database(f"sqlite+pysqlite:///{path.as_posix()}")
    Base.metadata.create_all(database.engine)
    return database


def _workbook(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "OQC"
    sheet["A1"] = "OQC Report"
    sheet["A2"] = "Inspection Date"
    sheet["B2"] = date(2026, 8, 15)
    sheet["A3"] = "Supplier"
    sheet["B3"] = _SUPPLIER_SOURCE
    sheet["A5"] = "DIMENSION"
    sheet["B5"] = "CTQ"
    sheet["C5"] = "Length"
    sheet["D5"] = "mm"
    sheet["E5"] = 10.0
    sheet["F5"] = 9.5
    sheet["G5"] = 10.5
    sheet["H5"] = 10.1
    sheet["I5"] = 9.9
    sheet["J5"] = "PASS"
    sheet["K5"] = "Cavity-1"
    sheet["L5"] = "Caliper"
    workbook.save(path)
    workbook.close()


def _store_root(tmp_path: Path) -> Path:
    suffix = hashlib.sha256(str(tmp_path).encode()).hexdigest()[:10]
    return tmp_path.parent / f"map-{suffix}"


def _receipt(tmp_path: Path) -> tuple[OriginalFileStore, SourceFileReceipt]:
    source = tmp_path / "수동매핑.xlsx"
    _workbook(source)
    store = OriginalFileStore(_store_root(tmp_path), max_bytes=5_000_000)
    receipt = store.preserve(
        project_key=_PROJECT,
        source=source,
        declared_mime_type=XLSX_MIME,
    )
    return store, receipt


def _service(
    database: Database,
    store: OriginalFileStore,
    *,
    template_hex: str = "a" * 32,
) -> MappingRegistrationService:
    repository = MappingTemplateRepository()
    commands = MappingTemplateCommandService(
        database,
        repository=repository,
        clock=lambda: _NOW,
    )
    return MappingRegistrationService(
        database=database,
        file_store=store,
        scanner=OpenpyxlWorkbookScanner(),
        scan_policy=ScanPolicy(),
        mapping_repository=repository,
        command_service=commands,
        id_factory=lambda: template_hex,
    )


def _client(service: MappingRegistrationService) -> TestClient:
    application = FastAPI()
    application.include_router(create_mapping_registration_router(service))
    return TestClient(application)


def _cell(coordinate: str) -> dict[str, str]:
    return {"sheet_name": "OQC", "coordinate": coordinate}


def _draft_body(receipt: SourceFileReceipt) -> dict[str, Any]:
    return {
        "project_key": _PROJECT,
        "receipt_id": receipt.receipt_id,
        "content_sha256": receipt.content_sha256,
        "supplier_scope": _SUPPLIER_SCOPE,
        "effective_from": "2026-01-01",
        "effective_to": None,
        "expected_history_row_version": 0,
        "reason": "원본 셀을 직접 확인하여 첫 매핑 초안을 생성합니다.",
        "header_assertion_cells": [_cell("A1"), _cell("A2")],
        "identifiers": [
            {"kind": "INSPECTION_DATE", "source": _cell("B2")},
            {"kind": "SUPPLIER", "source": _cell("B3")},
        ],
        "inspection_rows": [
            {
                "row_key": "length-row",
                "item": _cell("C5"),
                "method": _cell("L5"),
                "sample_cells": [_cell("H5"), _cell("I5")],
                "supplier_result": _cell("J5"),
                "section": _cell("A5"),
                "category": _cell("B5"),
                "unit": _cell("D5"),
                "cavity": _cell("K5"),
                "target": _cell("E5"),
                "lsl": _cell("F5"),
                "usl": _cell("G5"),
            }
        ],
    }


def _workflow_body(
    receipt: SourceFileReceipt,
    *,
    history_version: int,
    revision_version: int,
) -> dict[str, Any]:
    return {
        "project_key": _PROJECT,
        "receipt_id": receipt.receipt_id,
        "content_sha256": receipt.content_sha256,
        "supplier_scope": _SUPPLIER_SCOPE,
        "expected_history_row_version": history_version,
        "expected_revision_row_version": revision_version,
        "reason": "원본 Receipt와 선택 좌표를 다시 확인했습니다.",
    }


def _post_draft(client: TestClient, receipt: SourceFileReceipt) -> dict[str, Any]:
    response = client.post("/api/v1/mapping/templates/drafts", json=_draft_body(receipt))
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def _workflow_path(template_id: str, command: str, *, revision: int = 1) -> str:
    return f"/api/v1/mapping/templates/{template_id}/revisions/{revision}/{command}"


def _audit_rows(database: Database) -> tuple[AuditLog, ...]:
    with database.session() as session:
        return tuple(session.scalars(select(AuditLog).order_by(AuditLog.action)).all())


@pytest.mark.required_test_id("DQ-P1-MAPUI-005")
def test_receipt_bound_schema_v2_draft_uses_server_derived_fingerprint(
    tmp_path: Path,
) -> None:
    store, receipt = _receipt(tmp_path)
    database = _database(tmp_path / "draft.sqlite3")
    try:
        request_body = _draft_body(receipt)
        assert not {
            "template_id",
            "schema_version",
            "revision",
            "supplier_source_aliases",
            "fingerprint",
            "actor",
            "roles",
        }.intersection(request_body)
        with _client(_service(database, store)) as client:
            payload = _post_draft(client, receipt)

        workflow = payload["workflow"]
        proof = payload["proof"]
        assert workflow == {
            "template_id": f"map-{'a' * 32}",
            "schema_version": "2",
            "revision": 1,
            "status": "DRAFT",
            "project_key": _PROJECT,
            "supplier_scope": _SUPPLIER_SCOPE,
            "effective_from": "2026-01-01",
            "effective_to": None,
            "history_id": workflow["history_id"],
            "revision_id": workflow["revision_id"],
            "history_row_version": 1,
            "revision_row_version": 1,
            "reviewed_by": None,
            "reviewed_at": None,
            "approved_by": None,
            "approved_at": None,
            "capabilities": {
                "can_review": True,
                "can_approve": False,
                "additional_revisions_supported": False,
            },
        }
        assert proof["receipt_id"] == receipt.receipt_id
        assert proof["content_sha256"] == receipt.content_sha256
        assert proof["original_filename"] == "수동매핑.xlsx"
        assert proof["header_assertion_count"] == 2
        assert proof["identifier_count"] == 2
        assert proof["inspection_row_count"] == 1
        assert proof["mapped_cell_count"] == 14
        assert len(proof["fingerprint_sha256"]) == 64
        assert not proof["official_values_created"]
        assert not proof["calculations_performed"]
        assert payload["preview"] is None

        repository = MappingTemplateRepository()
        with database.session() as session:
            record = repository.get(
                session,
                project_key=_PROJECT,
                supplier_scope=_SUPPLIER_SCOPE,
                template_id=workflow["template_id"],
                revision=1,
            )
        template = record.template
        assert template.supplier_source_aliases == (_SUPPLIER_SOURCE,)
        assert [item.expected_token for item in template.fingerprint.header_tokens] == [
            "OQC Report",
            "Inspection Date",
        ]
        assert [
            source.coordinate
            for source in template.fingerprint.row_structures[0].expected_non_empty_cells
        ] == [f"{column}5" for column in "ABCDEFGHIJKL"]
        assert set(template.inspection_rows[0].all_addresses).issubset(
            template.fingerprint.row_structures[0].expected_non_empty_cells
        )

        audits = _audit_rows(database)
        assert len(audits) == 1
        assert audits[0].action == "MAPPING_TEMPLATE_REVISION_CREATED"
        assert audits[0].actor_id == LOCAL_OWNER.actor_id
        assert audits[0].actor_kind == LOCAL_OWNER.kind.value
        assert set(audits[0].actor_roles) == {role.value for role in LOCAL_OWNER.roles}
        assert receipt.receipt_id in (audits[0].source_reference or "")
        assert receipt.content_sha256 in (audits[0].source_reference or "")

        replay_database = _database(tmp_path / "draft-replay.sqlite3")
        try:
            with _client(_service(replay_database, store, template_hex="b" * 32)) as replay_client:
                replay = _post_draft(replay_client, receipt)
            assert replay["proof"]["fingerprint_sha256"] == proof["fingerprint_sha256"]
        finally:
            replay_database.dispose()
    finally:
        database.dispose()


@pytest.mark.required_test_id("DQ-P1-MAPUI-006")
def test_review_is_a_separate_trusted_cas_command_with_audit(tmp_path: Path) -> None:
    store, receipt = _receipt(tmp_path)
    database = _database(tmp_path / "review.sqlite3")
    try:
        with _client(_service(database, store)) as client:
            draft = _post_draft(client, receipt)
            workflow = draft["workflow"]
            response = client.post(
                _workflow_path(workflow["template_id"], "review"),
                json=_workflow_body(receipt, history_version=1, revision_version=1),
            )
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["workflow"]["status"] == "REVIEWED"
        assert payload["workflow"]["history_row_version"] == 2
        assert payload["workflow"]["revision_row_version"] == 2
        assert payload["workflow"]["reviewed_by"] == LOCAL_OWNER.actor_id
        assert payload["workflow"]["approved_by"] is None
        assert payload["workflow"]["capabilities"] == {
            "can_review": False,
            "can_approve": True,
            "additional_revisions_supported": False,
        }
        assert payload["preview"] is None
        audits = _audit_rows(database)
        assert {row.action for row in audits} == {
            "MAPPING_TEMPLATE_REVISION_CREATED",
            "MAPPING_TEMPLATE_REVIEWED",
        }
        reviewed = next(row for row in audits if row.action == "MAPPING_TEMPLATE_REVIEWED")
        assert reviewed.before_state is not None
        assert reviewed.before_state["status"] == "DRAFT"
        assert reviewed.after_state is not None
        assert reviewed.after_state["status"] == "REVIEWED"
        assert reviewed.actor_id == LOCAL_OWNER.actor_id
    finally:
        database.dispose()


@pytest.mark.required_test_id("DQ-P1-MAPUI-007")
def test_approval_reloads_fresh_catalog_and_same_receipt_preview(tmp_path: Path) -> None:
    store, receipt = _receipt(tmp_path)
    database = _database(tmp_path / "approve.sqlite3")
    try:
        with _client(_service(database, store)) as client:
            draft = _post_draft(client, receipt)
            template_id = draft["workflow"]["template_id"]
            reviewed_response = client.post(
                _workflow_path(template_id, "review"),
                json=_workflow_body(receipt, history_version=1, revision_version=1),
            )
            assert reviewed_response.status_code == 200, reviewed_response.text
            approved_response = client.post(
                _workflow_path(template_id, "approve"),
                json=_workflow_body(receipt, history_version=2, revision_version=2),
            )
        assert approved_response.status_code == 200, approved_response.text
        payload = approved_response.json()
        assert payload["workflow"]["status"] == "APPROVED"
        assert payload["workflow"]["history_row_version"] == 3
        assert payload["workflow"]["revision_row_version"] == 3
        assert payload["workflow"]["approved_by"] == LOCAL_OWNER.actor_id
        assert payload["workflow"]["capabilities"] == {
            "can_review": False,
            "can_approve": False,
            "additional_revisions_supported": False,
        }
        assert payload["preview"] == {
            "state": "PREVIEW_READY",
            "source_inspection_date": "2026-08-15",
            "identifier_count": 2,
            "inspection_row_count": 1,
            "system_judgment_status": "NOT_EVALUATED",
            "official_values_created": False,
            "calculations_performed": False,
        }
        with database.session() as session:
            catalog = MappingTemplateRepository().load_catalog(session, project_key=_PROJECT)
        assert len(catalog.templates) == 1
        assert catalog.templates[0].status == MappingTemplateStatus.APPROVED
        assert len(_audit_rows(database)) == 3
    finally:
        database.dispose()


@pytest.mark.required_test_id("DQ-P1-MAPUI-008")
def test_scope_role_cell_and_stored_source_tamper_fail_closed(tmp_path: Path) -> None:
    store, receipt = _receipt(tmp_path)
    database = _database(tmp_path / "negative-source.sqlite3")
    try:
        with _client(_service(database, store)) as client:
            wrong_project = _draft_body(receipt)
            wrong_project["project_key"] = "project-other"
            response = client.post("/api/v1/mapping/templates/drafts", json=wrong_project)
            assert response.status_code == 404
            assert response.json()["detail"]["code"] == "MAPPING_RECEIPT_NOT_FOUND"

            missing_supplier = _draft_body(receipt)
            missing_supplier["identifiers"] = [missing_supplier["identifiers"][0]]
            response = client.post("/api/v1/mapping/templates/drafts", json=missing_supplier)
            assert response.status_code == 400
            assert response.json()["detail"]["code"] in {
                "INVALID_MAPPING_SELECTION",
                "SUPPLIER_IDENTIFIER_REQUIRED",
            }

            invalid_header = _draft_body(receipt)
            invalid_header["header_assertion_cells"] = [_cell("Z99")]
            response = client.post("/api/v1/mapping/templates/drafts", json=invalid_header)
            assert response.status_code == 400
            assert response.json()["detail"]["code"] == "SOURCE_CELL_NOT_FOUND"
            assert len(_audit_rows(database)) == 0

            draft = _post_draft(client, receipt)
            second_receipt = store.preserve(
                project_key=_PROJECT,
                source=tmp_path / "수동매핑.xlsx",
                declared_mime_type=XLSX_MIME,
            )
            different_receipt_body = _workflow_body(
                second_receipt,
                history_version=1,
                revision_version=1,
            )
            response = client.post(
                _workflow_path(draft["workflow"]["template_id"], "review"),
                json=different_receipt_body,
            )
            assert response.status_code == 409
            assert response.json()["detail"]["code"] == "MAPPING_TEMPLATE_RECEIPT_MISMATCH"

            blob = next(_store_root(tmp_path).rglob("*.xlsx"))
            blob.write_bytes(b"not-the-original-workbook")
            response = client.post(
                _workflow_path(draft["workflow"]["template_id"], "review"),
                json=_workflow_body(receipt, history_version=1, revision_version=1),
            )
            assert response.status_code == 503
            error_payload = response.json()
            assert error_payload["detail"]["code"] == "MAPPING_SOURCE_UNAVAILABLE"
            assert str(tmp_path) not in response.text
            assert "StoredSource" not in response.text
        audits = _audit_rows(database)
        assert len(audits) == 1
        assert audits[0].action == "MAPPING_TEMPLATE_REVISION_CREATED"
    finally:
        database.dispose()


@pytest.mark.required_test_id("DQ-P1-MAPUI-009")
def test_direct_approval_forged_identity_stale_cas_and_outputs_are_blocked(
    tmp_path: Path,
) -> None:
    store, receipt = _receipt(tmp_path)
    database = _database(tmp_path / "workflow-negative.sqlite3")
    try:
        with _client(_service(database, store)) as client:
            draft = _post_draft(client, receipt)
            template_id = draft["workflow"]["template_id"]

            direct = client.post(
                _workflow_path(template_id, "approve"),
                json=_workflow_body(receipt, history_version=1, revision_version=1),
            )
            assert direct.status_code == 409
            assert direct.json()["detail"]["code"] == "MAPPING_STATUS_CONFLICT"

            forged = _workflow_body(receipt, history_version=1, revision_version=1)
            forged["actor"] = {"actor_id": "admin", "roles": ["ADMIN"]}
            forged_response = client.post(
                _workflow_path(template_id, "review"),
                json=forged,
            )
            assert forged_response.status_code == 400
            assert forged_response.json() == {
                "detail": {
                    "code": "INVALID_MAPPING_REQUEST",
                    "message": "매핑 요청 형식과 필수 입력값을 확인해 주세요.",
                    "status_label": "매핑 요청 오류",
                }
            }

            unsupported = client.post(
                _workflow_path(template_id, "review", revision=2),
                json=_workflow_body(receipt, history_version=1, revision_version=1),
            )
            assert unsupported.status_code == 400
            assert unsupported.json()["detail"]["code"] == ("ADDITIONAL_REVISION_NOT_SUPPORTED")

            stale = client.post(
                _workflow_path(template_id, "review"),
                json=_workflow_body(receipt, history_version=2, revision_version=2),
            )
            assert stale.status_code == 409
            assert stale.json()["detail"]["code"] == "MAPPING_VERSION_CONFLICT"

        with database.session() as session:
            record = MappingTemplateRepository().get(
                session,
                project_key=_PROJECT,
                supplier_scope=_SUPPLIER_SCOPE,
                template_id=template_id,
                revision=1,
            )
            long_counts = (
                session.scalar(select(func.count()).select_from(OqcLotRow)),
                session.scalar(select(func.count()).select_from(LongInspectionResultRow)),
                session.scalar(select(func.count()).select_from(LongMeasurementRow)),
            )
        assert record.template.status == MappingTemplateStatus.DRAFT
        assert record.history_row_version == 1
        assert record.revision_row_version == 1
        assert long_counts == (0, 0, 0)
        assert [row.action for row in _audit_rows(database)] == [
            "MAPPING_TEMPLATE_REVISION_CREATED"
        ]
    finally:
        database.dispose()
