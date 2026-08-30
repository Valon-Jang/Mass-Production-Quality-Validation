from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import backend.tests.test_data_status_review as dstat
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.api.data_review import create_data_review_router
from app.application.data_review import DataStatusReviewService
from app.application.data_review_workflow import DataReviewWorkflowService
from app.infrastructure.audit import AuditLog
from app.infrastructure.data_review import DataStatusTransitionRow
from app.infrastructure.database import Database
from app.infrastructure.long_format import (
    LongInspectionResultRow,
    LongMeasurementRow,
    OqcLotRow,
)

_PROJECT = dstat._PROJECT
_ITEM_KEY = dstat._ITEM_KEY
_REASON = "검사 결과의 데이터상태를 명시적으로 결정합니다."


def _workflow(database: Database) -> DataReviewWorkflowService:
    return DataReviewWorkflowService(
        database,
        review_service=DataStatusReviewService(database, clock=dstat._clock),
    )


def _client(database: Database) -> TestClient:
    application = FastAPI()
    application.include_router(create_data_review_router(_workflow(database)))
    return TestClient(application)


def _job_id(fixture: dstat._ReviewFixture) -> str:
    with fixture.database.session() as session:
        lot = session.get(OqcLotRow, fixture.lot_id)
    assert lot is not None
    return lot.ingestion_job_id


def _targets(client: TestClient, fixture: dstat._ReviewFixture) -> dict[str, Any]:
    response = client.post(
        "/api/v1/data-reviews/targets",
        json={"project_key": _PROJECT, "ingestion_job_id": _job_id(fixture)},
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json())


def _candidate(client: TestClient, fixture: dstat._ReviewFixture) -> dict[str, Any]:
    response = client.post(
        "/api/v1/data-reviews/candidates",
        json={"project_key": _PROJECT, "result_id": fixture.result_id},
    )
    assert response.status_code == 200, response.text
    return cast(dict[str, Any], response.json()["candidate"])


def _decision_body(
    fixture: dstat._ReviewFixture,
    candidate: dict[str, Any],
    *,
    target_status: str,
    confirmed: bool = True,
) -> dict[str, Any]:
    return {
        "project_key": _PROJECT,
        "result_id": fixture.result_id,
        "target_status": target_status,
        "candidate_sha256": candidate["candidate_sha256"],
        "cas": deepcopy(candidate["cas"]),
        "reason": _REASON,
        "confirmed": confirmed,
    }


def _review_counts(database: Database) -> tuple[int, int]:
    with database.session() as session:
        transitions = session.scalar(select(func.count()).select_from(DataStatusTransitionRow))
        decisions = session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == "DATA_STATUS_DECIDED")
        )
    assert transitions is not None and decisions is not None
    return transitions, decisions


@pytest.mark.required_test_id("DQ-P1-DSTATUI-001")
def test_long_job_targets_and_candidate_are_read_only_with_exact_provenance_and_cas(
    tmp_path: Path,
) -> None:
    fixture = dstat._fixture(tmp_path)
    try:
        before = _review_counts(fixture.database)
        with _client(fixture.database) as client:
            targets = _targets(client, fixture)
            first = _candidate(client, fixture)
            second = _candidate(client, fixture)
        assert targets == {
            "project_key": _PROJECT,
            "ingestion_job_id": _job_id(fixture),
            "job_status": "COMPLETED_PENDING",
            "targets": [
                {
                    "result_id": fixture.result_id,
                    "source_row_key": "row-4",
                    "data_status": "PENDING",
                    "row_version": 1,
                    "canonical_item_key": _ITEM_KEY,
                    "lot_id": fixture.lot_id,
                    "lot_ordinal": 1,
                    "source_lot_text": dstat.mapping_v2._LOT,
                    "inspection_date": "2026-06-15",
                    "reviewable": True,
                    "status_label": "검토 대기",
                }
            ],
            "official_values_created": False,
        }
        assert first == second
        assert first["state"] == "EVALUATED"
        assert first["result"] == {
            "id": fixture.result_id,
            "source_file_id": first["result"]["source_file_id"],
            "lot_id": fixture.lot_id,
            "source_content_sha256": dstat.mapping_v2._HASH,
            "inspection_date": "2026-06-15",
            "data_status": "PENDING",
            "current_system_judgment": None,
            "current_system_judgment_status": "NOT_EVALUATED",
            "current_spec_evaluation_status": "NOT_EVALUATED",
            "source_evidence_sha256": first["result"]["source_evidence_sha256"],
            "binding_snapshot_sha256": first["result"]["binding_snapshot_sha256"],
            "candidate_snapshot_sha256": first["result"]["candidate_snapshot_sha256"],
        }
        assert all(
            len(first["result"][field]) == 64
            for field in (
                "source_content_sha256",
                "source_evidence_sha256",
                "binding_snapshot_sha256",
                "candidate_snapshot_sha256",
            )
        )
        assert first["source_unit"] == {
            "sheet_name": "OQC V2",
            "coordinate": "J4",
            "raw_value": "mm",
            "cell_evidence_sha256": first["source_unit"]["cell_evidence_sha256"],
        }
        assert len(first["source_unit"]["cell_evidence_sha256"]) == 64
        assert first["selected_master"] == first["master_candidates"][0]
        assert first["selected_master"] == {
            "project_key": _PROJECT,
            "canonical_item_key": _ITEM_KEY,
            "history_id": fixture.master.history_id,
            "revision_id": fixture.master.revision_id,
            "revision_number": 1,
            "history_row_version": fixture.master.history_row_version,
            "revision_row_version": fixture.master.revision_row_version,
            "payload_sha256": fixture.master.payload_sha256,
            "declared_effective_from": "2026-01-01",
            "declared_effective_to": "2026-12-31",
            "resolved_effective_to": None,
            "target": "2.00",
            "lsl": "1.90",
            "usl": "2.10",
            "unit": "mm",
            "external_spec_revision": "SYNTHETIC-R1",
        }
        assert [sample["source_cell"] for sample in first["samples"]] == ["U4", "V4"]
        assert [sample["comparison"] for sample in first["samples"]] == [
            "WITHIN_LIMITS",
            "WITHIN_LIMITS",
        ]
        assert [sample["numeric_value"] for sample in first["samples"]] == ["2.00", "2.10"]
        assert first["cas"]["expected_result_row_version"] == 1
        assert first["cas"]["expected_item_row_version"] == 2
        assert first["cas"]["expected_master"] == {
            "history_id": fixture.master.history_id,
            "revision_id": fixture.master.revision_id,
            "history_row_version": fixture.master.history_row_version,
            "revision_row_version": fixture.master.revision_row_version,
            "payload_sha256": fixture.master.payload_sha256,
        }
        assert len(first["cas"]["expected_measurement_versions"]) == 2
        assert first["capabilities"] == {
            "can_decide": True,
            "explicit_confirmation_required": True,
            "trusted_local_admin": True,
        }
        assert not first["official_values_created"]
        assert not first["unit_conversion_performed"]
        assert not first["ai_used"]
        assert not first["statistics_calculated"]
        assert _review_counts(fixture.database) == before
    finally:
        fixture.database.dispose()


@pytest.mark.required_test_id("DQ-P1-DSTATUI-002")
def test_candidate_keeps_pending_status_separate_from_pass_and_fail_master_comparison(
    tmp_path: Path,
) -> None:
    passing = dstat._fixture(tmp_path / "pass")
    failing = dstat._fixture(tmp_path / "fail", fail_sample=True)
    try:
        with _client(passing.database) as client:
            pass_candidate = _candidate(client, passing)
        with _client(failing.database) as client:
            fail_candidate = _candidate(client, failing)
        for candidate, judgment in (
            (pass_candidate, "PASS"),
            (fail_candidate, "FAIL"),
        ):
            assert candidate["state"] == "EVALUATED"
            assert candidate["result"]["data_status"] == "PENDING"
            assert candidate["result"]["current_system_judgment"] is None
            assert candidate["result"]["current_system_judgment_status"] == "NOT_EVALUATED"
            assert candidate["proposed_system_judgment"] == judgment
            assert candidate["proposed_system_judgment_status"] == "EVALUATED"
            assert candidate["proposed_spec_evaluation_status"] == ("EVALUATED_APPROVED_MASTER")
            assert candidate["allowed_target_statuses"] == [
                "EXCLUDED",
                "SUSPECT",
                "VALID",
            ]
        assert [sample["comparison"] for sample in fail_candidate["samples"]] == [
            "WITHIN_LIMITS",
            "ABOVE_USL",
        ]
        assert _review_counts(passing.database) == (0, 0)
        assert _review_counts(failing.database) == (0, 0)
    finally:
        passing.database.dispose()
        failing.database.dispose()


@pytest.mark.required_test_id("DQ-P1-DSTATUI-003")
def test_explicit_trusted_local_admin_can_mark_fail_candidate_valid_atomically(
    tmp_path: Path,
) -> None:
    fixture = dstat._fixture(tmp_path, fail_sample=True)
    try:
        with fixture.database.session() as session:
            lot = session.get(OqcLotRow, fixture.lot_id)
            assert lot is not None
            lot_before = (
                lot.data_status,
                lot.row_version,
                lot.identifier_evidence_sha256,
            )
        with _client(fixture.database) as client:
            candidate = _candidate(client, fixture)
            response = client.post(
                "/api/v1/data-reviews/decisions",
                json=_decision_body(fixture, candidate, target_status="VALID"),
            )
        assert response.status_code == 200, response.text
        decision = response.json()["decision"]
        assert decision["target_status"] == "VALID"
        assert decision["evaluation_mode"] == "EVALUATED"
        assert decision["system_judgment"] == "FAIL"
        assert decision["candidate_sha256"] == candidate["candidate_sha256"]
        assert decision["master"] == candidate["selected_master"]
        assert decision["measurement_count"] == 2
        assert decision["result_row_version"] == 2
        assert decision["replayed"] is False
        assert decision["auto_decision"] is False
        assert decision["ai_used"] is False
        assert decision["additional_calculation"] is False
        with fixture.database.session() as session:
            result = session.get(LongInspectionResultRow, fixture.result_id)
            lot = session.get(OqcLotRow, fixture.lot_id)
            measurements = session.scalars(
                select(LongMeasurementRow).order_by(LongMeasurementRow.sample_ordinal)
            ).all()
            transition = session.get(DataStatusTransitionRow, decision["transition_id"])
            audit = session.scalar(
                select(AuditLog).where(
                    AuditLog.action == "DATA_STATUS_DECIDED",
                    AuditLog.target_id == f"{_PROJECT}:{fixture.result_id}",
                )
            )
        assert result is not None and lot is not None and transition is not None
        assert result.data_status == "VALID"
        assert result.system_judgment == "FAIL"
        assert [value.data_status for value in measurements] == ["VALID", "VALID"]
        assert [value.row_version for value in measurements] == [2, 2]
        assert (lot.data_status, lot.row_version, lot.identifier_evidence_sha256) == lot_before
        assert transition.decided_by == "local-owner"
        assert audit is not None
        assert audit.actor_id == "local-owner"
        assert "ADMIN" in audit.actor_roles
        assert _review_counts(fixture.database) == (1, 1)
    finally:
        fixture.database.dispose()


@pytest.mark.required_test_id("DQ-P1-DSTATUI-004")
def test_review_only_and_held_candidates_expose_only_safe_explicit_choices(
    tmp_path: Path,
) -> None:
    review_only = dstat._fixture(tmp_path / "review-only", source_unit=" mm ")
    held = dstat._fixture(tmp_path / "held")
    try:
        with held.database.session() as session, session.begin():
            result = session.get(LongInspectionResultRow, held.result_id)
            assert result is not None
            result.data_status = "HELD"
            for measurement in session.scalars(select(LongMeasurementRow)).all():
                measurement.data_status = "HELD"
        with _client(review_only.database) as client:
            review_candidate = _candidate(client, review_only)
            invalid = client.post(
                "/api/v1/data-reviews/decisions",
                json=_decision_body(review_only, review_candidate, target_status="VALID"),
            )
            suspect = client.post(
                "/api/v1/data-reviews/decisions",
                json=_decision_body(review_only, review_candidate, target_status="SUSPECT"),
            )
        assert review_candidate["state"] == "REVIEW_ONLY"
        assert review_candidate["source_unit"]["raw_value"] == " mm "
        assert review_candidate["selected_master"] is None
        assert review_candidate["proposed_system_judgment"] is None
        assert review_candidate["allowed_target_statuses"] == ["EXCLUDED", "SUSPECT"]
        assert {issue["code"] for issue in review_candidate["issues"]} == {"UNIT_MISMATCH"}
        assert invalid.status_code == 409
        assert invalid.json()["detail"]["code"] == "DATA_REVIEW_TARGET_NOT_ALLOWED"
        assert suspect.status_code == 200, suspect.text
        assert suspect.json()["decision"]["evaluation_mode"] == "REVIEW_ONLY"
        assert suspect.json()["decision"]["system_judgment"] is None

        with _client(held.database) as client:
            held_targets = _targets(client, held)
            held_candidate = _candidate(client, held)
            held_decision = client.post(
                "/api/v1/data-reviews/decisions",
                json=_decision_body(held, held_candidate, target_status="EXCLUDED"),
            )
        assert held_targets["targets"][0]["data_status"] == "HELD"
        assert held_targets["targets"][0]["reviewable"] is False
        assert held_candidate["state"] == "INELIGIBLE"
        assert held_candidate["allowed_target_statuses"] == []
        assert held_candidate["capabilities"]["can_decide"] is False
        assert "RESULT_HELD" in {issue["code"] for issue in held_candidate["issues"]}
        assert held_decision.status_code == 409
        assert held_decision.json()["detail"]["code"] == "DATA_REVIEW_TARGET_NOT_ALLOWED"
        assert _review_counts(held.database) == (0, 0)
    finally:
        review_only.database.dispose()
        held.database.dispose()


@pytest.mark.required_test_id("DQ-P1-DSTATUI-005")
def test_scope_stale_cas_forged_identity_and_confirmation_fail_closed_safely(
    tmp_path: Path,
) -> None:
    fixture = dstat._fixture(tmp_path)
    try:
        with _client(fixture.database) as client:
            candidate = _candidate(client, fixture)
            wrong_scope = client.post(
                "/api/v1/data-reviews/candidates",
                json={"project_key": "project-other", "result_id": fixture.result_id},
            )

            stale_digest_body = _decision_body(fixture, candidate, target_status="VALID")
            digest = cast(str, stale_digest_body["candidate_sha256"])
            stale_digest_body["candidate_sha256"] = (
                f"{'0' if digest[0] != '0' else '1'}{digest[1:]}"
            )
            stale_digest = client.post(
                "/api/v1/data-reviews/decisions",
                json=stale_digest_body,
            )

            stale_cas_body = _decision_body(fixture, candidate, target_status="VALID")
            stale_cas = cast(dict[str, Any], stale_cas_body["cas"])
            measurements = cast(list[dict[str, Any]], stale_cas["expected_measurement_versions"])
            measurements[0]["row_version"] += 1
            stale_version = client.post(
                "/api/v1/data-reviews/decisions",
                json=stale_cas_body,
            )

            forged = _decision_body(fixture, candidate, target_status="VALID")
            forged["actor"] = {"actor_id": "browser-admin", "roles": ["ADMIN"]}
            forged["command_id"] = "browser-command"
            forged["idempotency_key"] = "browser-idempotency"
            forged["server_version"] = "browser-version"
            forged_actor = client.post("/api/v1/data-reviews/decisions", json=forged)

            unconfirmed = client.post(
                "/api/v1/data-reviews/decisions",
                json=_decision_body(
                    fixture,
                    candidate,
                    target_status="VALID",
                    confirmed=False,
                ),
            )

        assert wrong_scope.status_code == 404
        assert wrong_scope.json()["detail"]["code"] == "DATA_REVIEW_RESULT_NOT_FOUND"
        assert stale_digest.status_code == 409
        assert stale_digest.json()["detail"]["code"] == "DATA_REVIEW_CANDIDATE_STALE"
        assert stale_version.status_code == 409
        assert stale_version.json()["detail"]["code"] == "DATA_REVIEW_CANDIDATE_STALE"
        assert forged_actor.status_code == 400
        assert forged_actor.json()["detail"]["code"] == "INVALID_DATA_REVIEW_REQUEST"
        assert unconfirmed.status_code == 400
        assert unconfirmed.json()["detail"]["code"] == (
            "EXPLICIT_DATA_REVIEW_CONFIRMATION_REQUIRED"
        )
        for response in (wrong_scope, stale_digest, stale_version, forged_actor, unconfirmed):
            assert str(tmp_path) not in response.text
            assert "Traceback" not in response.text
        assert _review_counts(fixture.database) == (0, 0)
        with fixture.database.session() as session:
            result = session.get(LongInspectionResultRow, fixture.result_id)
            statuses = set(session.scalars(select(LongMeasurementRow.data_status)).all())
        assert result is not None and result.data_status == "PENDING"
        assert statuses == {"PENDING"}
    finally:
        fixture.database.dispose()


@pytest.mark.required_test_id("DQ-P1-DSTATUI-006")
def test_restart_replays_server_owned_exact_decision_without_duplicate_mutation(
    tmp_path: Path,
) -> None:
    fixture = dstat._fixture(tmp_path, fail_sample=True)
    with _client(fixture.database) as client:
        candidate = _candidate(client, fixture)
        body = _decision_body(fixture, candidate, target_status="VALID")
        first_response = client.post("/api/v1/data-reviews/decisions", json=body)
    assert first_response.status_code == 200, first_response.text
    first = first_response.json()["decision"]
    assert first["replayed"] is False
    fixture.database.dispose()

    restarted = Database(f"sqlite+pysqlite:///{fixture.database_path.as_posix()}")
    try:
        with _client(restarted) as client:
            replay_response = client.post("/api/v1/data-reviews/decisions", json=body)
        assert replay_response.status_code == 200, replay_response.text
        replay = replay_response.json()["decision"]
        assert replay["replayed"] is True
        assert replay["transition_id"] == first["transition_id"]
        assert replay["intent_sha256"] == first["intent_sha256"]
        assert replay["candidate_sha256"] == first["candidate_sha256"]
        assert replay["target_status"] == first["target_status"] == "VALID"
        assert replay["system_judgment"] == first["system_judgment"] == "FAIL"
        assert replay["master"] == first["master"]
        assert _review_counts(restarted) == (1, 1)
        with restarted.session() as session:
            result_count = session.scalar(select(func.count()).select_from(LongInspectionResultRow))
            measurement_rows = session.scalars(
                select(LongMeasurementRow).order_by(LongMeasurementRow.sample_ordinal)
            ).all()
        assert result_count == 1
        assert [(value.data_status, value.row_version) for value in measurement_rows] == [
            ("VALID", 2),
            ("VALID", 2),
        ]
    finally:
        restarted.dispose()
