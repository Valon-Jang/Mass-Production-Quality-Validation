from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, func, select

from app.api.historical_comparison import create_historical_comparison_router
from app.application.historical_comparison import (
    HISTORY_MAX_SAMPLES_PER_RESULT,
    HistoricalComparisonError,
    HistoricalComparisonRequest,
    HistoricalComparisonService,
    HistoricalDateRange,
    HistoricalFilters,
)
from app.infrastructure.data_review import DataStatusTransitionRow
from app.infrastructure.database import Base, Database
from app.infrastructure.long_format import (
    LongIngestionJobRow,
    LongInspectionResultRow,
    LongMeasurementRow,
    LongSourceFileRow,
    LongSourceSheetRow,
    OqcLotRow,
    build_applied_mapping_proof,
    canonical_json_sha256,
)
from app.infrastructure.mapping_templates import (
    MappingTemplateHistoryRow,
    MappingTemplateRevisionRow,
)
from app.infrastructure.master_config import (
    CanonicalInspectionItemRow,
    CanonicalModelPartRow,
    CanonicalModelRow,
    MasterSpecHistoryRow,
    MasterSpecRevisionRow,
    MasterSpecSupersessionRow,
)

NOW = datetime(2026, 8, 16, 9, 0, tzinfo=UTC)
PROJECT = "history-project"
MAPPING_SHA = "a" * 64


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _cell(coordinate: str, value: str) -> dict[str, object]:
    return {
        "sheet_name": "OQC",
        "coordinate": coordinate,
        "raw_value": {"kind": "str", "value": value},
        "cached_value": {"kind": "none", "value": None},
        "formula_text": None,
        "number_format": "General",
        "data_type": "s",
        "display_value": None,
        "display_value_status": "NOT_RENDERED",
        "value_kind": "TEXT",
    }


def _database(
    tmp_path: Path,
    *,
    samples: int = 2,
    terminal: bool = False,
    applied_master: bool = False,
) -> Database:
    database = Database(f"sqlite+pysqlite:///{(tmp_path / 'history.sqlite3').as_posix()}")
    Base.metadata.create_all(database.engine)
    candidate_snapshot = {
        "provenance": {
            "receipt": {
                "project_key": PROJECT,
                "receipt_id": "receipt-history",
                "content_sha256": "d" * 64,
            },
            "supplier_scope": "supplier-alpha",
            "template_id": "template-history",
            "template_schema_version": "2",
            "template_revision": 1,
            "template_effective_from": "2026-01-01",
            "template_effective_to": None,
        }
    }
    candidate_sha = canonical_json_sha256(candidate_snapshot)
    applied_mapping_proof = build_applied_mapping_proof(
        project_key=PROJECT,
        source_file_id="source-history",
        receipt_id="receipt-history",
        content_sha256="d" * 64,
        mapping_template_revision_id="mapping-revision",
        mapping_payload_sha256=MAPPING_SHA,
        candidate_snapshot=candidate_snapshot,
        candidate_snapshot_sha256=candidate_sha,
    )
    source_evidence = {
        "item": _cell("C8", "폭"),
        "unit": _cell("G8", "mm"),
        "source_spec_revision": _cell("H4", "REV-A"),
    }
    binding = {
        "key": {
            "project_key": PROJECT,
            "supplier_scope": "supplier-alpha",
            "template_id": "template-history",
            "template_revision": 1,
            "row_key": "row-8",
        },
        "binding_revision": 1,
        "status": "APPROVED",
        "approved_by": "local-owner",
        "approved_at": NOW.isoformat(),
        "effective_from": "2026-01-01",
        "effective_to": None,
        "source_model_values": ["MODEL-A"],
        "canonical_model_key": "model-a",
        "canonical_supplier_key": "supplier-a",
        "canonical_model_part_key": "part-a",
        "canonical_item_key": "item-width",
        "sample_policy": "ALL_NON_EMPTY",
        "measurement_mode": "QUALITATIVE",
    }
    with database.session() as session, session.begin():
        if applied_master:
            session.add(
                CanonicalModelRow(
                    id="model-history-id",
                    project_key=PROJECT,
                    model_key="model-a",
                    display_name="Model A",
                    row_version=1,
                    created_at=NOW,
                )
            )
            session.flush()
            session.add(
                CanonicalModelPartRow(
                    id="part-history-id",
                    project_key=PROJECT,
                    model_id="model-history-id",
                    model_part_key="part-a",
                    display_name="Part A",
                    row_version=1,
                    created_at=NOW,
                )
            )
            session.flush()
            session.add(
                CanonicalInspectionItemRow(
                    id="item-history-id",
                    project_key=PROJECT,
                    model_part_id="part-history-id",
                    item_key="item-width",
                    display_name="Width",
                    disposition="MANAGED",
                    row_version=1,
                    created_at=NOW,
                )
            )
            session.flush()
            session.add(
                MasterSpecHistoryRow(
                    id="master-history-id",
                    project_key=PROJECT,
                    item_id="item-history-id",
                    row_version=1,
                    created_at=NOW,
                )
            )
            session.flush()
            session.add(
                MasterSpecRevisionRow(
                    id="master-revision-1",
                    project_key=PROJECT,
                    history_id="master-history-id",
                    revision_number=1,
                    status="APPROVED",
                    spec_payload={},
                    payload_sha256=canonical_json_sha256({}),
                    declared_effective_from=date(2026, 1, 1),
                    declared_effective_to=None,
                    resolved_effective_to=None,
                    reviewed_by="reviewer",
                    reviewed_at=NOW,
                    approved_by="admin",
                    approved_at=NOW,
                    row_version=1,
                    created_at=NOW,
                )
            )
            session.flush()
        session.add(
            MappingTemplateHistoryRow(
                id="mapping-history",
                project_key=PROJECT,
                supplier_scope="supplier-alpha",
                template_id="template-history",
                row_version=3,
                created_at=NOW,
            )
        )
        session.add(
            MappingTemplateRevisionRow(
                id="mapping-revision",
                history_id="mapping-history",
                revision_number=1,
                schema_version="2",
                status="APPROVED",
                template_payload={},
                payload_sha256=MAPPING_SHA,
                declared_effective_from=date(2026, 1, 1),
                declared_effective_to=None,
                resolved_effective_to=None,
                reviewed_by="reviewer",
                reviewed_at=NOW,
                approved_by="admin",
                approved_at=NOW,
                row_version=3,
                created_at=NOW,
            )
        )
        session.add(
            LongSourceFileRow(
                id="source-history",
                project_key=PROJECT,
                receipt_id="receipt-history",
                blob_id="sha256:" + "d" * 64,
                content_sha256="d" * 64,
                received_at=NOW,
                original_filename="과거_OQC.xlsx",
                model_candidates=["MODEL-A"],
                lot_candidates=["LOT-A"],
                declared_mime_type="application/xlsx",
                detected_mime_type="application/xlsx",
                canonical_extension=".xlsx",
                size_bytes=123,
                parse_status="SCANNED",
                scan_source_name="과거_OQC.xlsx",
                scan_source_size_bytes=123,
                scan_sha256_before="d" * 64,
                scan_sha256_after="d" * 64,
                scan_contract_version="workbook-scan-v1",
                estimated_cells=20,
                external_link_count=0,
                macro_handling="NOT_APPLICABLE",
                display_value_contract="NOT_RENDERED",
                is_golden_workbook_evidence=False,
                scan_issues=[],
                row_version=1,
                created_at=NOW,
            )
        )
        session.flush()
        scan_snapshot: dict[str, object] = {"sheet_name": "OQC"}
        session.add(
            LongSourceSheetRow(
                id="sheet-history",
                project_key=PROJECT,
                source_file_id="source-history",
                position=0,
                sheet_name="OQC",
                sheet_kind="WORKSHEET",
                visibility="visible",
                used_range="A1:Q20",
                estimated_cells=20,
                merged_ranges=[],
                hidden_row_ranges=[],
                hidden_column_ranges=[],
                formula_count=0,
                protection_metadata={},
                image_metadata=[],
                issues=[],
                scan_snapshot=scan_snapshot,
                snapshot_sha256=canonical_json_sha256(scan_snapshot),
                row_version=1,
            )
        )
        session.add(
            LongIngestionJobRow(
                id="job-history",
                project_key=PROJECT,
                source_file_id="source-history",
                content_sha256="d" * 64,
                mapping_template_revision_id="mapping-revision",
                mapping_payload_sha256=MAPPING_SHA,
                binding_catalog_revision="catalog-history-v1",
                binding_fingerprint="b" * 64,
                loader_version="long-ui-v1",
                scan_contract_version="workbook-scan-v1",
                idempotency_key="i" * 64,
                materialization_fingerprint="m" * 64,
                owns_materialization=True,
                reused_job_id=None,
                blocking_job_id=None,
                status="COMPLETED_PENDING",
                started_at=NOW,
                finished_at=NOW,
                lot_count=1,
                result_count=1,
                measurement_count=samples,
                held_result_count=0,
                error_code=None,
                error_summary=None,
                issues=[],
                candidate_snapshot=candidate_snapshot,
                candidate_snapshot_sha256=candidate_sha,
                applied_mapping_proof=applied_mapping_proof,
                applied_mapping_proof_sha256=canonical_json_sha256(applied_mapping_proof),
                row_version=1,
            )
        )
        session.flush()
        identifiers: list[dict[str, object]] = []
        session.add(
            OqcLotRow(
                id="lot-history",
                project_key=PROJECT,
                ingestion_job_id="job-history",
                source_file_id="source-history",
                lot_ordinal=1,
                canonical_model_key="model-a",
                canonical_model_part_key="part-a",
                canonical_supplier_key="supplier-a",
                source_lot_text="LOT-A",
                inspection_date=date(2026, 8, 15),
                received_at=NOW,
                identifier_evidence=identifiers,
                identifier_evidence_sha256=canonical_json_sha256(identifiers),
                data_status="PENDING",
                hold_reasons=[],
                row_version=1,
            )
        )
        session.flush()
        result = LongInspectionResultRow(
            id="result-history",
            project_key=PROJECT,
            oqc_lot_id="lot-history",
            source_file_id="source-history",
            source_sheet_id="sheet-history",
            source_row_key="row-8",
            binding_revision=1,
            canonical_model_part_key="part-a",
            canonical_item_key="item-width",
            supplier_judgment_text="PASS",
            system_judgment=None,
            system_judgment_status="NOT_EVALUATED",
            spec_evaluation_status="NOT_EVALUATED",
            source_evidence=source_evidence,
            source_evidence_sha256=canonical_json_sha256(source_evidence),
            binding_snapshot=binding,
            binding_snapshot_sha256=canonical_json_sha256(binding),
            candidate_snapshot_sha256=candidate_sha,
            data_status="PENDING",
            hold_reasons=[],
            current_data_status_transition_id=None,
            current_decision_command_id=None,
            current_decision_candidate_sha256=None,
            current_decision_mode=None,
            applied_master_history_id=None,
            applied_master_revision_id=None,
            applied_master_revision_number=None,
            applied_master_history_row_version=None,
            applied_master_revision_row_version=None,
            applied_master_payload_sha256=None,
            applied_master_declared_effective_from=None,
            applied_master_declared_effective_to=None,
            applied_master_resolved_effective_to=None,
            current_decided_by=None,
            current_decided_at=None,
            current_decision_reason=None,
            row_version=1,
        )
        session.add(result)
        session.flush()
        for ordinal in range(1, samples + 1):
            evidence = _cell(f"H{ordinal + 8}", f"S{ordinal}")
            raw = cast(dict[str, object], evidence["raw_value"])
            session.add(
                LongMeasurementRow(
                    id=f"measurement-{ordinal}",
                    project_key=PROJECT,
                    inspection_result_id=result.id,
                    source_file_id="source-history",
                    source_sheet_id="sheet-history",
                    sample_ordinal=ordinal,
                    source_cell=f"H{ordinal + 8}",
                    raw_value_tag="str",
                    raw_value_text=_canonical(raw),
                    raw_numeric_value=None,
                    raw_qualitative_value=f"S{ordinal}",
                    evidence=evidence,
                    evidence_sha256=canonical_json_sha256(evidence),
                    formula_flag=False,
                    standardized_value=None,
                    unit_conversion_status="NOT_CONFIGURED",
                    data_status="PENDING",
                    hold_reasons=[],
                    superseded_measurement_id=None,
                    row_version=1,
                )
            )
        session.flush()
        if terminal or applied_master:
            evaluated = applied_master
            target_status = "VALID" if evaluated else "SUSPECT"
            master_payload_sha = canonical_json_sha256({}) if evaluated else None
            transition_candidate: dict[str, object] = {"candidate": "review"}
            decision_snapshot: dict[str, object] = {
                "decision": target_status.lower(),
                "evaluation_mode": "EVALUATED" if evaluated else "REVIEW_ONLY",
            }
            transition = DataStatusTransitionRow(
                id="transition-history",
                project_key=PROJECT,
                source_file_id="source-history",
                inspection_result_id=result.id,
                command_id="decision-history",
                intent_sha256="e" * 64,
                from_status="PENDING",
                to_status=target_status,
                before_result_row_version=1,
                after_result_row_version=2,
                measurement_count=samples,
                candidate_snapshot=transition_candidate,
                candidate_sha256=canonical_json_sha256(transition_candidate),
                decision_snapshot=decision_snapshot,
                decision_snapshot_sha256=canonical_json_sha256(decision_snapshot),
                evaluation_mode="EVALUATED" if evaluated else "REVIEW_ONLY",
                system_judgment="PASS" if evaluated else None,
                system_judgment_status="EVALUATED" if evaluated else "NOT_EVALUATED",
                spec_evaluation_status=(
                    "EVALUATED_APPROVED_MASTER" if evaluated else "NOT_EVALUATED"
                ),
                applied_master_history_id="master-history-id" if evaluated else None,
                applied_master_revision_id="master-revision-1" if evaluated else None,
                applied_master_revision_number=1 if evaluated else None,
                applied_master_history_row_version=1 if evaluated else None,
                applied_master_revision_row_version=1 if evaluated else None,
                applied_master_payload_sha256=master_payload_sha,
                applied_master_declared_effective_from=(date(2026, 1, 1) if evaluated else None),
                applied_master_declared_effective_to=None,
                applied_master_resolved_effective_to=None,
                decided_by="local-owner",
                decided_at=NOW,
                reason="명시적 과거 데이터 검토",
            )
            session.add(transition)
            session.flush()
            result.data_status = target_status
            result.current_data_status_transition_id = transition.id
            result.current_decision_command_id = transition.command_id
            result.current_decision_candidate_sha256 = transition.candidate_sha256
            result.current_decision_mode = transition.evaluation_mode
            result.system_judgment = transition.system_judgment
            result.system_judgment_status = transition.system_judgment_status
            result.spec_evaluation_status = transition.spec_evaluation_status
            result.applied_master_history_id = transition.applied_master_history_id
            result.applied_master_revision_id = transition.applied_master_revision_id
            result.applied_master_revision_number = transition.applied_master_revision_number
            result.applied_master_history_row_version = (
                transition.applied_master_history_row_version
            )
            result.applied_master_revision_row_version = (
                transition.applied_master_revision_row_version
            )
            result.applied_master_payload_sha256 = transition.applied_master_payload_sha256
            result.applied_master_declared_effective_from = (
                transition.applied_master_declared_effective_from
            )
            result.applied_master_declared_effective_to = (
                transition.applied_master_declared_effective_to
            )
            result.applied_master_resolved_effective_to = (
                transition.applied_master_resolved_effective_to
            )
            result.current_decided_by = transition.decided_by
            result.current_decided_at = transition.decided_at
            result.current_decision_reason = transition.reason
            result.row_version = 2
            for measurement in session.scalars(select(LongMeasurementRow)):
                measurement.data_status = target_status
                measurement.row_version = 2
    return database


def _request(*statuses: str, limit: int = 100) -> HistoricalComparisonRequest:
    return HistoricalComparisonRequest(
        project_key=PROJECT,
        left=HistoricalDateRange(date(2026, 8, 1), date(2026, 8, 15)),
        right=HistoricalDateRange(date(2026, 8, 15), date(2026, 8, 31)),
        data_statuses=tuple(sorted(statuses)),
        filters=HistoricalFilters(canonical_model_key="model-a"),
        limit_per_side=limit,
    )


@pytest.mark.required_test_id("DQ-P2-HIST-001")
@pytest.mark.required_test_id("DQ-P2-HISTUI-001")
def test_on_demand_history_returns_exact_raw_and_revision_provenance(tmp_path: Path) -> None:
    database = _database(tmp_path)
    try:
        before = _counts(database)
        response = HistoricalComparisonService(database).compare(_request("PENDING"))
        after = _counts(database)
        assert before == after
        result = response.left.results[0]
        assert result.receipt_id == "receipt-history"
        assert result.content_sha256 == "d" * 64
        assert result.source_row_key == "row-8"
        assert {field.role for field in result.source_fields} == {
            "item",
            "unit",
            "source_spec_revision",
        }
        assert result.mapping.applied_effective_from == date(2026, 1, 1)
        assert result.binding_revision == 1
        assert result.decision is None
        assert response.official_values_created is False
        assert response.calculations_performed is False
        assert response.statistics_performed is False
        assert response.ai_used is False

        application = FastAPI()
        application.include_router(
            create_historical_comparison_router(HistoricalComparisonService(database))
        )
        body = {
            "project_key": PROJECT,
            "left": {"date_from": "2026-08-01", "date_to": "2026-08-15"},
            "right": {"date_from": "2026-08-15", "date_to": "2026-08-31"},
            "data_statuses": ["PENDING"],
            "filters": {"canonical_model_key": "model-a"},
            "limit_per_side": 100,
        }
        with TestClient(application) as client:
            http_response = client.post("/api/v1/history/comparisons", json=body)
            invalid = client.post(
                "/api/v1/history/comparisons",
                json={**body, "data_statuses": ["PENDING", "PENDING"]},
            )
        assert http_response.status_code == 200
        http_payload = http_response.json()
        assert http_payload["left"]["results"][0]["result_id"] == "result-history"
        assert http_payload["left"]["results"][0]["source_fields"][0]["sheet_name"] == "OQC"
        assert http_payload["capabilities"] == {
            "official_values_created": False,
            "calculations_performed": False,
            "trend_analysis": False,
            "thresholds_applied": False,
            "current_master_rejudgment": False,
            "ai_used": False,
        }
        assert invalid.status_code == 400
        assert set(invalid.json()["detail"]) == {"code", "message", "status_label"}
    finally:
        database.dispose()


@pytest.mark.required_test_id("DQ-P2-HIST-002")
def test_terminal_decision_proof_and_two_date_sides_remain_independent(tmp_path: Path) -> None:
    database = _database(tmp_path, terminal=True)
    try:
        response = HistoricalComparisonService(database).compare(_request("SUSPECT"))
        assert response.left.total_result_count == response.right.total_result_count == 1
        assert response.left.mapping_revision_ids == ("mapping-revision",)
        decision = response.right.results[0].decision
        assert decision is not None
        assert decision.from_status == "PENDING" and decision.to_status == "SUSPECT"
        assert decision.before_result_row_version == 1
        assert decision.after_result_row_version == 2
        assert decision.command_id == "decision-history"
    finally:
        database.dispose()


@pytest.mark.required_test_id("DQ-P2-HIST-003")
def test_sample_projection_is_explicitly_capped_with_full_ordered_digest(tmp_path: Path) -> None:
    database = _database(
        tmp_path,
        samples=HISTORY_MAX_SAMPLES_PER_RESULT + 5,
        applied_master=True,
    )
    try:
        statements: list[str] = []

        def record_sql(
            _connection: object,
            _cursor: object,
            statement: str,
            _parameters: object,
            _context: object,
            _executemany: object,
        ) -> None:
            statements.append(statement)

        event.listen(database.engine, "before_cursor_execute", record_sql)
        try:
            first = HistoricalComparisonService(database).compare(_request("VALID"))
            second = HistoricalComparisonService(database).compare(_request("VALID"))
        finally:
            event.remove(database.engine, "before_cursor_execute", record_sql)
        result = first.left.results[0]
        assert result.total_sample_count == HISTORY_MAX_SAMPLES_PER_RESULT + 5
        assert result.returned_sample_count == HISTORY_MAX_SAMPLES_PER_RESULT
        assert result.samples_has_more is True
        assert len(result.samples) == HISTORY_MAX_SAMPLES_PER_RESULT
        assert result.sample_set_sha256 == second.left.results[0].sample_set_sha256
        assert first.left.total_sample_count == HISTORY_MAX_SAMPLES_PER_RESULT + 5
        assert first.left.returned_results_sample_count == HISTORY_MAX_SAMPLES_PER_RESULT + 5
        assert result.system_judgment == "PASS"
        assert result.applied_master is not None
        applied_at_decision = result.applied_master
        assert applied_at_decision.revision_id == "master-revision-1"
        assert result.decision is not None
        assert result.decision.evaluation_mode == "EVALUATED"
        assert not any(
            re.search(r"ingestion_jobs\.candidate_snapshot(?!_sha256)", statement)
            for statement in statements
        )

        with database.session() as session, session.begin():
            history = session.get(MasterSpecHistoryRow, "master-history-id")
            predecessor = session.get(MasterSpecRevisionRow, "master-revision-1")
            assert history is not None and predecessor is not None
            history.row_version = 2
            predecessor.row_version = 2
            predecessor.resolved_effective_to = date(2026, 8, 31)
            session.add(
                MasterSpecRevisionRow(
                    id="master-revision-2",
                    project_key=PROJECT,
                    history_id=history.id,
                    revision_number=2,
                    status="APPROVED",
                    spec_payload={"revision": 2},
                    payload_sha256=canonical_json_sha256({"revision": 2}),
                    declared_effective_from=date(2026, 9, 1),
                    declared_effective_to=None,
                    resolved_effective_to=None,
                    reviewed_by="reviewer",
                    reviewed_at=NOW,
                    approved_by="admin",
                    approved_at=NOW,
                    row_version=1,
                    created_at=NOW,
                )
            )
            session.flush()
            session.add(
                MasterSpecSupersessionRow(
                    id="master-supersession-1",
                    project_key=PROJECT,
                    history_id=history.id,
                    predecessor_revision_id=predecessor.id,
                    successor_revision_id="master-revision-2",
                    predecessor_effective_to=date(2026, 8, 31),
                    decided_by="admin",
                    decided_at=NOW,
                    reason="later approved Master revision",
                )
            )
        evolved = HistoricalComparisonService(database).compare(_request("VALID"))
        evolved_result = evolved.left.results[0]
        assert evolved_result.applied_master == applied_at_decision
        assert evolved_result.system_judgment == "PASS"
        assert evolved_result.decision == result.decision
    finally:
        database.dispose()


@pytest.mark.required_test_id("DQ-P2-HIST-004")
@pytest.mark.required_test_id("DQ-P2-HISTUI-002")
def test_scope_range_and_coordinated_projection_tamper_fail_closed(tmp_path: Path) -> None:
    database = _database(tmp_path)
    try:
        empty = HistoricalComparisonService(database).compare(
            HistoricalComparisonRequest(
                project_key="another-project",
                left=HistoricalDateRange(date(2026, 8, 1), date(2026, 8, 31)),
                right=HistoricalDateRange(date(2026, 8, 1), date(2026, 8, 31)),
                data_statuses=("PENDING",),
                filters=HistoricalFilters(),
                limit_per_side=10,
            )
        )
        assert empty.left.total_result_count == empty.right.total_result_count == 0
        with pytest.raises(ValueError):
            HistoricalDateRange(date(2026, 8, 31), date(2026, 8, 1))

        with database.session() as session, session.begin():
            measurement = session.scalar(select(LongMeasurementRow))
            assert measurement is not None
            forged = dict(measurement.evidence)
            forged["coordinate"] = "Z99"
            measurement.evidence = forged
            measurement.evidence_sha256 = canonical_json_sha256(forged)
        with pytest.raises(HistoricalComparisonError) as error:
            HistoricalComparisonService(database).compare(_request("PENDING"))
        assert error.value.code == "HISTORY_EVIDENCE_UNAVAILABLE"
        assert "Z99" not in error.value.safe_message
        corrupt_app = FastAPI()
        corrupt_app.include_router(
            create_historical_comparison_router(HistoricalComparisonService(database))
        )
        comparison_body = {
            "project_key": PROJECT,
            "left": {"date_from": "2026-08-01", "date_to": "2026-08-15"},
            "right": {"date_from": "2026-08-15", "date_to": "2026-08-31"},
            "data_statuses": ["PENDING"],
            "filters": {"canonical_model_key": "model-a"},
            "limit_per_side": 10,
        }
        with TestClient(corrupt_app) as client:
            corrupt_response = client.post("/api/v1/history/comparisons", json=comparison_body)
        assert corrupt_response.status_code == 409
        assert corrupt_response.json()["detail"]["code"] == "HISTORY_EVIDENCE_UNAVAILABLE"
        assert "Z99" not in json.dumps(corrupt_response.json(), ensure_ascii=False)

        with database.session() as session, session.begin():
            measurement = session.scalar(select(LongMeasurementRow))
            assert measurement is not None
            measurement.evidence = _cell(measurement.source_cell, "S1")
            measurement.evidence_sha256 = canonical_json_sha256(measurement.evidence)
            measurement.raw_numeric_value = _canonical({"kind": "int", "value": "1"})
        with pytest.raises(HistoricalComparisonError) as numeric_error:
            HistoricalComparisonService(database).compare(_request("PENDING"))
        assert numeric_error.value.code == "HISTORY_EVIDENCE_UNAVAILABLE"

        with database.session() as session, session.begin():
            measurement = session.scalar(select(LongMeasurementRow))
            result = session.scalar(select(LongInspectionResultRow))
            assert measurement is not None and result is not None
            measurement.raw_numeric_value = None
            assert result.binding_snapshot is not None
            original_binding = json.loads(_canonical(result.binding_snapshot))
            forged_binding = json.loads(_canonical(original_binding))
            forged_binding["key"]["project_key"] = "another-project"
            result.binding_snapshot = forged_binding
            result.binding_snapshot_sha256 = canonical_json_sha256(forged_binding)
        with pytest.raises(HistoricalComparisonError) as binding_error:
            HistoricalComparisonService(database).compare(_request("PENDING"))
        assert binding_error.value.code == "HISTORY_EVIDENCE_UNAVAILABLE"

        with database.session() as session, session.begin():
            result = session.scalar(select(LongInspectionResultRow))
            job = session.scalar(select(LongIngestionJobRow))
            assert result is not None and job is not None
            result.binding_snapshot = original_binding
            result.binding_snapshot_sha256 = canonical_json_sha256(original_binding)
            changed = dict(job.candidate_snapshot)
            changed_provenance = dict(cast(dict[str, object], changed["provenance"]))
            changed_provenance["template_effective_from"] = "2025-01-01"
            changed["provenance"] = changed_provenance
            changed_sha = canonical_json_sha256(changed)
            job.candidate_snapshot = changed
            job.candidate_snapshot_sha256 = changed_sha
            result.candidate_snapshot_sha256 = changed_sha
        with pytest.raises(HistoricalComparisonError) as snapshot_error:
            HistoricalComparisonService(database).compare(_request("PENDING"))
        assert snapshot_error.value.code == "HISTORY_EVIDENCE_UNAVAILABLE"

        scope_dir = tmp_path / "cross-project"
        scope_dir.mkdir()
        scoped = _database(scope_dir)
        try:
            with scoped.session() as session, session.begin():
                session.add(
                    MappingTemplateHistoryRow(
                        id="other-mapping-history",
                        project_key="another-project",
                        supplier_scope="supplier-alpha",
                        template_id="other-template",
                        row_version=3,
                        created_at=NOW,
                    )
                )
                session.add(
                    MappingTemplateRevisionRow(
                        id="other-mapping-revision",
                        history_id="other-mapping-history",
                        revision_number=1,
                        schema_version="2",
                        status="APPROVED",
                        template_payload={},
                        payload_sha256="c" * 64,
                        declared_effective_from=date(2026, 1, 1),
                        declared_effective_to=None,
                        resolved_effective_to=None,
                        reviewed_by="reviewer",
                        reviewed_at=NOW,
                        approved_by="admin",
                        approved_at=NOW,
                        row_version=3,
                        created_at=NOW,
                    )
                )
                session.flush()
                job = session.scalar(select(LongIngestionJobRow))
                assert job is not None
                proof = dict(job.applied_mapping_proof)
                proof.update(
                    {
                        "mapping_template_revision_id": "other-mapping-revision",
                        "mapping_payload_sha256": "c" * 64,
                        "template_id": "other-template",
                    }
                )
                job.mapping_template_revision_id = "other-mapping-revision"
                job.mapping_payload_sha256 = "c" * 64
                job.applied_mapping_proof = proof
                job.applied_mapping_proof_sha256 = canonical_json_sha256(proof)
            with pytest.raises(HistoricalComparisonError) as scope_error:
                HistoricalComparisonService(scoped).compare(_request("PENDING"))
            assert scope_error.value.code == "HISTORY_EVIDENCE_UNAVAILABLE"
        finally:
            scoped.dispose()

        class _Unavailable:
            def compare(self, request: HistoricalComparisonRequest) -> object:
                del request
                raise HistoricalComparisonError(
                    "HISTORY_DATABASE_UNAVAILABLE",
                    "database is unavailable",
                    "database unavailable",
                )

        unavailable_app = FastAPI()
        unavailable_app.include_router(create_historical_comparison_router(_Unavailable()))
        with TestClient(unavailable_app) as client:
            unavailable = client.post("/api/v1/history/comparisons", json=comparison_body)
        assert unavailable.status_code == 503
        assert unavailable.json()["detail"]["code"] == "HISTORY_DATABASE_UNAVAILABLE"
    finally:
        database.dispose()


def _counts(database: Database) -> tuple[int, int, int]:
    with database.session() as session:
        return (
            session.scalar(select(func.count()).select_from(OqcLotRow)) or 0,
            session.scalar(select(func.count()).select_from(LongInspectionResultRow)) or 0,
            session.scalar(select(func.count()).select_from(LongMeasurementRow)) or 0,
        )
