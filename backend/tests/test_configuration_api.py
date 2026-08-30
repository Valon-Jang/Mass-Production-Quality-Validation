from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.configuration import create_configuration_router
from app.api.long import create_long_router
from app.application.configuration_workflow import ConfigurationWorkflowService
from app.application.long_workflow import LongWorkflowService
from app.infrastructure.audit import AuditLog
from app.infrastructure.database import Base, Database
from app.infrastructure.long_format import LongInspectionResultRow
from app.infrastructure.master_config import (
    CanonicalInspectionItemRow,
    CanonicalRowBindingRevisionRow,
    MasterConfigRepository,
    MasterSpecRevisionRow,
)
from tests.test_long_workflow_api import (
    _Fixture as LongFixture,
)
from tests.test_long_workflow_api import (
    _fixture as make_long_fixture,
)
from tests.test_long_workflow_api import (
    _workflow_service as make_long_workflow,
)

_PROJECT = "project-alpha"
_OTHER_PROJECT = "project-beta"
_MODEL_KEY = "model-a"
_PART_KEY = "model-a:part-top"
_SUPPLIER_KEY = "supplier-a"
_MODEL_SOURCE = "MODEL-A"
_SUPPLIER_SCOPE = "supplier-alpha"


def _database(path: Path) -> Database:
    database = Database(f"sqlite+pysqlite:///{path.as_posix()}")
    Base.metadata.create_all(database.engine)
    return database


def _client(database: Database) -> TestClient:
    application = FastAPI()
    application.include_router(create_configuration_router(ConfigurationWorkflowService(database)))
    return TestClient(application)


def _post(client: TestClient, path: str, body: dict[str, object]) -> dict[str, Any]:
    response = client.post(path, json=body)
    assert response.status_code in {200, 201}, response.text
    return cast(dict[str, Any], response.json())


def _create_base_hierarchy(
    client: TestClient,
    *,
    item_key: str = "item-length",
    disposition: str | None = "MANAGED",
) -> dict[str, Any]:
    _post(
        client,
        "/api/v1/configuration/models",
        {
            "project_key": _PROJECT,
            "model_key": _MODEL_KEY,
            "display_name": "Synthetic Model A",
            "reason": "Create the exact synthetic model.",
        },
    )
    _post(
        client,
        "/api/v1/configuration/suppliers",
        {
            "project_key": _PROJECT,
            "supplier_key": _SUPPLIER_KEY,
            "display_name": "Synthetic Supplier A",
            "reason": "Create the exact synthetic supplier.",
        },
    )
    _post(
        client,
        "/api/v1/configuration/model-parts",
        {
            "project_key": _PROJECT,
            "model_key": _MODEL_KEY,
            "model_part_key": _PART_KEY,
            "display_name": "Synthetic Top Part",
            "reason": "Create the exact synthetic model-part.",
        },
    )
    item = _create_item(client, item_key=item_key)
    if disposition is None:
        return item
    return _set_disposition(
        client,
        item_key=item_key,
        disposition=disposition,
        expected_row_version=item["row_version"],
    )


def _create_item(client: TestClient, *, item_key: str) -> dict[str, Any]:
    return _post(
        client,
        "/api/v1/configuration/inspection-items",
        {
            "project_key": _PROJECT,
            "model_part_key": _PART_KEY,
            "item_key": item_key,
            "display_name": f"Synthetic {item_key}",
            "reason": "Create an explicit inspection-item candidate.",
        },
    )


def _set_disposition(
    client: TestClient,
    *,
    item_key: str,
    disposition: str,
    expected_row_version: int,
) -> dict[str, Any]:
    return _post(
        client,
        "/api/v1/configuration/inspection-items/dispositions",
        {
            "project_key": _PROJECT,
            "item_key": item_key,
            "disposition": disposition,
            "expected_row_version": expected_row_version,
            "reason": f"Explicitly decide {item_key} as {disposition}.",
        },
    )


def _master_draft_body(*, item_key: str = "item-length") -> dict[str, object]:
    return {
        "project_key": _PROJECT,
        "canonical_item_key": item_key,
        "target": "10.00",
        "lsl": "9.500",
        "usl": "10.500",
        "unit": "mm",
        "external_spec_revision": "SPEC-R1",
        "effective_from": "2026-01-01",
        "effective_to": "2026-12-31",
        "source_reference": "synthetic://approved-drawing/spec-r1",
        "expected_history_row_version": 0,
        "reason": "Create exact numeric Master Spec revision one.",
    }


def _master_workflow_body(value: dict[str, Any], *, reason: str) -> dict[str, object]:
    return {
        "project_key": value["project_key"],
        "canonical_item_key": value["canonical_item_key"],
        "revision": 1,
        "expected_history_row_version": value["history_row_version"],
        "expected_revision_row_version": value["revision_row_version"],
        "reason": reason,
    }


def _binding_draft_body(
    fixture: LongFixture,
    *,
    row_key: str = "row-length",
    item_key: str = "item-length",
) -> dict[str, object]:
    return {
        "project_key": _PROJECT,
        "supplier_scope": _SUPPLIER_SCOPE,
        "template_id": fixture.template_id,
        "template_revision": 1,
        "row_key": row_key,
        "source_model_values": [_MODEL_SOURCE],
        "canonical_model_key": _MODEL_KEY,
        "canonical_supplier_key": _SUPPLIER_KEY,
        "canonical_model_part_key": _PART_KEY,
        "canonical_item_key": item_key,
        "measurement_mode": "NUMERIC",
        "sample_policy": "AT_LEAST_ONE",
        "effective_from": "2026-01-01",
        "effective_to": None,
        "expected_history_row_version": 0,
        "reason": f"Create the exact first binding for {row_key}.",
    }


def _binding_workflow_body(value: dict[str, Any], *, reason: str) -> dict[str, object]:
    return {
        "project_key": value["project_key"],
        "supplier_scope": value["supplier_scope"],
        "template_id": value["template_id"],
        "template_revision": value["template_revision"],
        "row_key": value["row_key"],
        "binding_revision": 1,
        "expected_history_row_version": value["history_row_version"],
        "expected_revision_row_version": value["revision_row_version"],
        "reason": reason,
    }


def _approve_binding(
    client: TestClient,
    fixture: LongFixture,
    *,
    row_key: str,
    item_key: str,
) -> dict[str, Any]:
    draft = _post(
        client,
        "/api/v1/configuration/row-bindings/drafts",
        _binding_draft_body(fixture, row_key=row_key, item_key=item_key),
    )
    reviewed = _post(
        client,
        "/api/v1/configuration/row-bindings/reviews",
        _binding_workflow_body(draft, reason=f"Review {row_key} binding separately."),
    )
    return _post(
        client,
        "/api/v1/configuration/row-bindings/approvals",
        _binding_workflow_body(reviewed, reason=f"Approve {row_key} binding separately."),
    )


def _audit_actions(database: Database) -> list[str]:
    with database.session() as session:
        return sorted(session.scalars(select(AuditLog.action)).all())


@pytest.mark.required_test_id("DQ-P1-CFGUI-001")
def test_project_snapshot_is_read_only_scoped_and_exposes_approved_mapping_rows(
    tmp_path: Path,
) -> None:
    fixture = make_long_fixture(tmp_path, binding_rows=())
    try:
        before = len(_audit_actions(fixture.database))
        with _client(fixture.database) as client:
            response = client.get(
                "/api/v1/configuration/snapshot",
                params={"project_key": _PROJECT},
            )
            other = client.get(
                "/api/v1/configuration/snapshot",
                params={"project_key": _OTHER_PROJECT},
            )
        assert response.status_code == 200, response.text
        assert other.status_code == 200, other.text
        payload = response.json()
        assert payload["project_key"] == _PROJECT
        assert payload["models"] == []
        assert payload["master_specs"] == []
        assert payload["row_bindings"] == []
        assert len(payload["approved_mapping_revisions"]) == 1
        mapping = payload["approved_mapping_revisions"][0]
        assert mapping["template_id"] == fixture.template_id
        assert mapping["schema_version"] == "2"
        assert mapping["status"] == "APPROVED"
        assert len(mapping["payload_sha256"]) == 64
        assert mapping["model_source"] == {"sheet_name": "OQC", "coordinate": "B4"}
        assert [row["row_key"] for row in mapping["rows"]] == [
            "row-length",
            "row-width",
        ]
        assert mapping["rows"][0]["item_source"] == {
            "sheet_name": "OQC",
            "coordinate": "C7",
        }
        assert mapping["rows"][0]["unit_source"]["coordinate"] == "D7"
        assert mapping["rows"][0]["sample_cells"] == [
            {"sheet_name": "OQC", "coordinate": "H7"},
            {"sheet_name": "OQC", "coordinate": "I7"},
        ]
        assert payload["capabilities"] == {
            "first_master_revision_only": True,
            "first_binding_revision_only": True,
            "later_revisions_supported": False,
            "supersession_supported": False,
            "actor_source": "TRUSTED_LOCAL_OWNER",
        }
        assert not payload["official_values_created"]
        assert not payload["auto_effects"]
        assert not payload["ai_used"]
        assert other.json()["approved_mapping_revisions"] == []
        assert len(_audit_actions(fixture.database)) == before
    finally:
        fixture.close()


@pytest.mark.required_test_id("DQ-P1-CFGUI-002")
def test_explicit_hierarchy_and_supplier_creation_is_audited_without_defaults(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path / "hierarchy.sqlite3")
    try:
        with _client(database) as client:
            item = _create_base_hierarchy(client, disposition=None)
            snapshot = client.get(
                "/api/v1/configuration/snapshot",
                params={"project_key": _PROJECT},
            ).json()
        assert item == {
            "project_key": _PROJECT,
            "model_part_key": _PART_KEY,
            "item_key": "item-length",
            "display_name": "Synthetic item-length",
            "disposition": "CANDIDATE",
            "row_version": 1,
        }
        assert [value["model_key"] for value in snapshot["models"]] == [_MODEL_KEY]
        assert [value["supplier_key"] for value in snapshot["suppliers"]] == [_SUPPLIER_KEY]
        assert snapshot["model_parts"][0]["model_key"] == _MODEL_KEY
        assert snapshot["inspection_items"] == [item]
        assert snapshot["master_specs"] == []
        assert snapshot["row_bindings"] == []
        assert _audit_actions(database) == [
            "CANONICAL_INSPECTION_ITEM_CREATED",
            "CANONICAL_MODEL_CREATED",
            "CANONICAL_MODEL_PART_CREATED",
            "CANONICAL_SUPPLIER_CREATED",
        ]
    finally:
        database.dispose()


@pytest.mark.required_test_id("DQ-P1-CFGUI-003")
def test_candidate_disposition_is_explicit_terminal_and_cas_protected(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path / "disposition.sqlite3")
    try:
        with _client(database) as client:
            managed = _create_base_hierarchy(client)
            repeat = client.post(
                "/api/v1/configuration/inspection-items/dispositions",
                json={
                    "project_key": _PROJECT,
                    "item_key": "item-length",
                    "disposition": "EXCLUDED",
                    "expected_row_version": 2,
                    "reason": "Attempt to replace an explicit decision.",
                },
            )
            excluded_candidate = _create_item(client, item_key="item-appearance")
            excluded = _set_disposition(
                client,
                item_key="item-appearance",
                disposition="EXCLUDED",
                expected_row_version=excluded_candidate["row_version"],
            )
            stale_candidate = _create_item(client, item_key="item-width")
            stale = client.post(
                "/api/v1/configuration/inspection-items/dispositions",
                json={
                    "project_key": _PROJECT,
                    "item_key": stale_candidate["item_key"],
                    "disposition": "MANAGED",
                    "expected_row_version": 99,
                    "reason": "Use a stale synthetic row version.",
                },
            )
        assert managed["disposition"] == "MANAGED"
        assert managed["row_version"] == 2
        assert excluded["disposition"] == "EXCLUDED"
        assert repeat.status_code == 409
        assert repeat.json()["detail"]["code"] == "CONFIGURATION_DISPOSITION_ALREADY_DECIDED"
        assert stale.status_code == 409
        assert stale.json()["detail"]["code"] == "CONFIGURATION_STALE_VERSION"
    finally:
        database.dispose()


@pytest.mark.required_test_id("DQ-P1-CFGUI-004")
def test_numeric_master_first_draft_preserves_exact_decimals_and_explicit_provenance(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path / "master-draft.sqlite3")
    try:
        with _client(database) as client:
            _create_base_hierarchy(client)
            body = _master_draft_body()
            assert not {"actor", "roles", "revision", "status"}.intersection(body)
            draft = _post(client, "/api/v1/configuration/master-specs/drafts", body)
            target_only = dict(_master_draft_body())
            target_only.update(
                {
                    "canonical_item_key": "item-length",
                    "lsl": None,
                    "usl": None,
                }
            )
            invalid = client.post(
                "/api/v1/configuration/master-specs/drafts",
                json=target_only,
            )
        assert draft["revision"] == 1
        assert draft["status"] == "DRAFT"
        assert (draft["target"], draft["lsl"], draft["usl"]) == (
            "10.00",
            "9.500",
            "10.500",
        )
        assert draft["unit"] == "mm"
        assert draft["declared_effective_from"] == "2026-01-01"
        assert draft["declared_effective_to"] == "2026-12-31"
        assert draft["resolved_effective_to"] is None
        assert draft["change_reason"] == body["reason"]
        assert draft["source_reference"] == body["source_reference"]
        assert draft["reviewed_by"] is None and draft["approved_by"] is None
        assert (draft["history_row_version"], draft["revision_row_version"]) == (1, 1)
        assert len(draft["payload_sha256"]) == 64
        assert invalid.status_code == 400
        with database.session() as session:
            assert session.scalar(select(func.count()).select_from(MasterSpecRevisionRow)) == 1
    finally:
        database.dispose()


@pytest.mark.required_test_id("DQ-P1-CFGUI-005")
def test_master_review_is_separate_and_direct_draft_approval_is_rejected(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path / "master-review.sqlite3")
    try:
        with _client(database) as client:
            _create_base_hierarchy(client)
            draft = _post(
                client,
                "/api/v1/configuration/master-specs/drafts",
                _master_draft_body(),
            )
            direct = client.post(
                "/api/v1/configuration/master-specs/approvals",
                json=_master_workflow_body(draft, reason="Invalid direct approval."),
            )
            reviewed = _post(
                client,
                "/api/v1/configuration/master-specs/reviews",
                _master_workflow_body(draft, reason="Review exact Master separately."),
            )
        assert direct.status_code == 400 or direct.status_code == 409
        assert reviewed["status"] == "REVIEWED"
        assert reviewed["reviewed_by"] == "local-owner"
        assert reviewed["approved_by"] is None
        assert (reviewed["history_row_version"], reviewed["revision_row_version"]) == (2, 2)
        actions = _audit_actions(database)
        assert "MASTER_SPEC_REVISION_REVIEWED" in actions
        assert "MASTER_SPEC_REVISION_APPROVED" not in actions
    finally:
        database.dispose()


@pytest.mark.required_test_id("DQ-P1-CFGUI-006")
def test_master_admin_approval_materializes_exact_effective_catalog_without_auto_valid(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path / "master-approve.sqlite3")
    try:
        with _client(database) as client:
            _create_base_hierarchy(client)
            draft = _post(
                client,
                "/api/v1/configuration/master-specs/drafts",
                _master_draft_body(),
            )
            reviewed = _post(
                client,
                "/api/v1/configuration/master-specs/reviews",
                _master_workflow_body(draft, reason="Reviewer checks exact numeric limits."),
            )
            approved = _post(
                client,
                "/api/v1/configuration/master-specs/approvals",
                _master_workflow_body(reviewed, reason="Admin approves exact numeric limits."),
            )
            snapshot = client.get(
                "/api/v1/configuration/snapshot",
                params={"project_key": _PROJECT},
            ).json()
        assert approved["status"] == "APPROVED"
        assert approved["reviewed_by"] == approved["approved_by"] == "local-owner"
        assert approved["reviewed_at"] != approved["approved_at"] or (
            approved["reviewed_at"] is not None and approved["approved_at"] is not None
        )
        assert (approved["history_row_version"], approved["revision_row_version"]) == (3, 3)
        assert snapshot["master_specs"] == [approved]
        with database.session() as session:
            catalog = MasterConfigRepository().load_master_spec_catalog(
                session,
                project_key=_PROJECT,
                as_of=date(2026, 8, 15),
            )
            long_results = session.scalar(select(func.count()).select_from(LongInspectionResultRow))
        selected = catalog.find("item-length")
        assert selected is not None
        assert selected.spec.target is not None and str(selected.spec.target) == "10.00"
        assert long_results == 0
        assert "MASTER_SPEC_REVISION_APPROVED" in _audit_actions(database)
    finally:
        database.dispose()


@pytest.mark.required_test_id("DQ-P1-CFGUI-007")
def test_approved_mapping_row_selector_creates_server_provenanced_first_binding_draft(
    tmp_path: Path,
) -> None:
    fixture = make_long_fixture(tmp_path, binding_rows=())
    try:
        with _client(fixture.database) as client:
            _create_base_hierarchy(client)
            snapshot = client.get(
                "/api/v1/configuration/snapshot",
                params={"project_key": _PROJECT},
            ).json()
            mapping = snapshot["approved_mapping_revisions"][0]
            body = _binding_draft_body(fixture)
            assert not {
                "actor",
                "roles",
                "binding_revision",
                "status",
                "source_reference",
                "mapping_history_id",
                "mapping_revision_id",
            }.intersection(body)
            draft = _post(
                client,
                "/api/v1/configuration/row-bindings/drafts",
                body,
            )
            wrong_row = dict(_binding_draft_body(fixture, row_key="row-not-present"))
            rejected = client.post(
                "/api/v1/configuration/row-bindings/drafts",
                json=wrong_row,
            )
        assert mapping["rows"][0]["row_key"] == "row-length"
        assert draft["binding_revision"] == 1
        assert draft["status"] == "DRAFT"
        assert draft["source_model_values"] == [_MODEL_SOURCE]
        assert draft["canonical_item_key"] == "item-length"
        assert draft["source_reference"] == (
            f"mapping-template:{_PROJECT}:{_SUPPLIER_SCOPE}:{fixture.template_id}:"
            f"1:row-length:sha256:{mapping['payload_sha256']}"
        )
        assert draft["reviewed_by"] is None and draft["approved_by"] is None
        assert rejected.status_code == 404
        assert rejected.json()["detail"]["code"] == "CONFIGURATION_MAPPING_ROW_NOT_FOUND"
        with fixture.database.session() as session:
            assert (
                session.scalar(select(func.count()).select_from(CanonicalRowBindingRevisionRow))
                == 1
            )
    finally:
        fixture.close()


@pytest.mark.required_test_id("DQ-P1-CFGUI-008")
def test_excluded_item_binding_requires_separate_review_and_admin_approval(
    tmp_path: Path,
) -> None:
    fixture = make_long_fixture(tmp_path, binding_rows=())
    try:
        with _client(fixture.database) as client:
            _create_base_hierarchy(client, disposition="EXCLUDED")
            draft = _post(
                client,
                "/api/v1/configuration/row-bindings/drafts",
                _binding_draft_body(fixture),
            )
            direct = client.post(
                "/api/v1/configuration/row-bindings/approvals",
                json=_binding_workflow_body(draft, reason="Invalid direct binding approval."),
            )
            reviewed = _post(
                client,
                "/api/v1/configuration/row-bindings/reviews",
                _binding_workflow_body(draft, reason="Reviewer checks exact row identity."),
            )
            approved = _post(
                client,
                "/api/v1/configuration/row-bindings/approvals",
                _binding_workflow_body(reviewed, reason="Admin approves exact row identity."),
            )
        assert direct.status_code in {400, 409}
        assert reviewed["status"] == "REVIEWED"
        assert approved["status"] == "APPROVED"
        assert approved["reviewed_by"] == approved["approved_by"] == "local-owner"
        with fixture.database.session() as session:
            catalog = MasterConfigRepository().load_row_binding_catalog(
                session,
                project_key=_PROJECT,
                as_of=date(2026, 8, 15),
            )
        assert len(catalog.records) == 1
        assert catalog.records[0].binding.canonical_item_key == "item-length"
        assert "CANONICAL_ROW_BINDING_REVISION_REVIEWED" in _audit_actions(fixture.database)
        assert "CANONICAL_ROW_BINDING_REVISION_APPROVED" in _audit_actions(fixture.database)
    finally:
        fixture.close()


@pytest.mark.required_test_id("DQ-P1-CFGUI-009")
def test_restart_approved_binding_catalog_makes_matching_long_rows_loadable(
    tmp_path: Path,
) -> None:
    fixture = make_long_fixture(tmp_path, binding_rows=())
    restarted: Database | None = None
    try:
        with _client(fixture.database) as client:
            _create_base_hierarchy(client)
            second = _create_item(client, item_key="item-width")
            _set_disposition(
                client,
                item_key="item-width",
                disposition="MANAGED",
                expected_row_version=second["row_version"],
            )
            _approve_binding(
                client,
                fixture,
                row_key="row-length",
                item_key="item-length",
            )
            _approve_binding(
                client,
                fixture,
                row_key="row-width",
                item_key="item-width",
            )
        fixture.database.dispose()
        restarted = Database(f"sqlite+pysqlite:///{fixture.database_path.as_posix()}")
        with _client(restarted) as client:
            snapshot = client.get(
                "/api/v1/configuration/snapshot",
                params={"project_key": _PROJECT},
            ).json()
        assert [value["status"] for value in snapshot["row_bindings"]] == [
            "APPROVED",
            "APPROVED",
        ]
        long_application = FastAPI()
        long_service: LongWorkflowService = make_long_workflow(restarted, fixture.store)
        long_application.include_router(create_long_router(long_service))
        with TestClient(long_application) as client:
            response = client.post(
                "/api/v1/long/candidates",
                json={
                    "project_key": _PROJECT,
                    "receipt_id": fixture.receipt.receipt_id,
                    "content_sha256": fixture.receipt.content_sha256,
                    "supplier_scope": _SUPPLIER_SCOPE,
                },
            )
        assert response.status_code == 200, response.text
        candidate = response.json()["candidate"]
        assert candidate["state"] == "LOAD_CANDIDATE_READY"
        assert candidate["loadable_row_count"] == 2
        assert candidate["held_row_count"] == 0
        assert [row["state"] for row in candidate["rows"]] == [
            "LOADABLE_PENDING",
            "LOADABLE_PENDING",
        ]
        assert all(row["pending_data_status"] == "PENDING" for row in candidate["rows"])
        with restarted.session() as session:
            assert session.scalar(select(func.count()).select_from(LongInspectionResultRow)) == 0
    finally:
        if restarted is not None:
            restarted.dispose()
        fixture.close()


@pytest.mark.required_test_id("DQ-P1-CFGUI-010")
def test_stale_forged_cross_scope_and_automatic_effects_fail_closed(
    tmp_path: Path,
) -> None:
    fixture = make_long_fixture(tmp_path, binding_rows=())
    try:
        with _client(fixture.database) as client:
            _create_base_hierarchy(client)
            forged_actor = client.post(
                "/api/v1/configuration/models",
                json={
                    "project_key": _PROJECT,
                    "model_key": "forged-model",
                    "display_name": "Forged",
                    "reason": "Attempt forged authority.",
                    "actor": "admin",
                    "roles": ["ADMIN"],
                },
            )
            cross_scope = client.post(
                "/api/v1/configuration/model-parts",
                json={
                    "project_key": _OTHER_PROJECT,
                    "model_key": _MODEL_KEY,
                    "model_part_key": "cross-project-part",
                    "display_name": "Cross Project Part",
                    "reason": "Attempt a cross-project parent reference.",
                },
            )
            forged_binding = dict(_binding_draft_body(fixture))
            forged_binding["canonical_supplier_key"] = "supplier-not-present"
            rejected_binding = client.post(
                "/api/v1/configuration/row-bindings/drafts",
                json=forged_binding,
            )
            later_master = dict(_master_draft_body())
            later_master["expected_history_row_version"] = 1
            rejected_later = client.post(
                "/api/v1/configuration/master-specs/drafts",
                json=later_master,
            )
            snapshot = client.get(
                "/api/v1/configuration/snapshot",
                params={"project_key": _PROJECT},
            ).json()
        assert forged_actor.status_code == 400
        assert forged_actor.json()["detail"]["code"] == "INVALID_CONFIGURATION_REQUEST"
        assert cross_scope.status_code == 404
        assert rejected_binding.status_code == 404
        assert rejected_later.status_code == 400
        assert snapshot["master_specs"] == []
        assert snapshot["row_bindings"] == []
        assert not snapshot["official_values_created"]
        assert not snapshot["auto_effects"]
        assert not snapshot["ai_used"]
        with fixture.database.session() as session:
            assert session.scalar(select(func.count()).select_from(MasterSpecRevisionRow)) == 0
            assert (
                session.scalar(select(func.count()).select_from(CanonicalRowBindingRevisionRow))
                == 0
            )
            assert session.scalar(select(func.count()).select_from(LongInspectionResultRow)) == 0
            item = session.scalar(
                select(CanonicalInspectionItemRow).where(
                    CanonicalInspectionItemRow.item_key == "item-length"
                )
            )
        assert item is not None and item.disposition == "MANAGED"
    finally:
        fixture.close()
