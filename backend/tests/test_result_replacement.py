from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import backend.tests.test_data_status_review as data_review
import backend.tests.test_mapping_v2_evidence as mapping_v2
import backend.tests.test_migrations as migration_tests
import pytest
from alembic import command as alembic_command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.api.historical_comparison import create_historical_comparison_router
from app.api.result_replacement import create_result_replacement_router
from app.application.data_review import DataStatusReviewService, DecideDataStatusCommand
from app.application.historical_comparison import (
    HistoricalComparisonError,
    HistoricalComparisonRequest,
    HistoricalComparisonService,
    HistoricalDateRange,
    HistoricalFilters,
)
from app.application.long_candidate import build_long_candidate
from app.application.long_persistence import LongPersistenceRequest, LongPersistenceService
from app.application.mapping_preview import build_mapping_preview
from app.application.result_replacement import (
    DecideResultReplacementCommand,
    ReplacementCandidateRequest,
    ResultReplacementIneligibleError,
    ResultReplacementMissingError,
    ResultReplacementService,
    ResultReplacementStaleError,
    ResultReplacementUnavailableError,
)
from app.application.store_scan_mapping import (
    ResolvedMappingScope,
    StoreScanMappingOutcome,
    StoreScanMappingStatus,
)
from app.domain.audit import AuditChange
from app.domain.identity import Actor, ActorKind, Role
from app.domain.long_format import LongDataStatus
from app.domain.mapping import MappingPreviewRequest, MappingPreviewState
from app.domain.result_replacement import (
    ReplacementDifferenceCode,
    measurement_set_sha256,
)
from app.domain.source_file import SourceFileReceipt
from app.infrastructure.audit import AuditLog, AuditRepository
from app.infrastructure.data_review import DataReviewRepository, DataStatusTransitionRow
from app.infrastructure.database import Base, Database
from app.infrastructure.long_format import (
    LongInspectionResultRow,
    LongMeasurementRow,
    OqcLotRow,
)
from app.infrastructure.mapping_templates import MappingTemplateRepository
from app.infrastructure.result_replacement import (
    ResultReplacementConflictError,
    ResultReplacementMeasurementRow,
    ResultReplacementTransitionRow,
)

_PROJECT = mapping_v2._PROJECT
_SUPPLIER_SCOPE = mapping_v2._SUPPLIER_SCOPE
_NOW = datetime(2026, 8, 15, 11, 0, tzinfo=UTC)
_SUCCESSOR_HASH = "b" * 64
_OTHER_ADMIN = Actor(
    actor_id="replacement-other-admin",
    kind=ActorKind.LOCAL_OWNER,
    roles=frozenset({Role.ADMIN}),
)


@dataclass(frozen=True, slots=True)
class _ReplacementFixture:
    review: data_review._ReviewFixture
    predecessor_result_id: str
    successor_result_id: str
    sibling_result_id: str
    predecessor_review_command: DecideDataStatusCommand


def _add_result(
    review: data_review._ReviewFixture,
    *,
    suffix: str,
    digest: str,
    values: tuple[Decimal, Decimal],
) -> str:
    with review.database.session() as session:
        catalog = MappingTemplateRepository().load_catalog(session, project_key=_PROJECT)
    assert len(catalog.templates) == 1
    template = catalog.templates[0]
    scan = replace(
        data_review._two_sample_scan(values=values),
        source_name=f"replacement-{suffix}.xlsx",
        source_sha256_before=digest,
        source_sha256_after=digest,
    )
    preview = build_mapping_preview(
        scan,
        MappingPreviewRequest(project_key=_PROJECT, supplier_scope=_SUPPLIER_SCOPE),
        catalog,
    )
    assert preview.state == MappingPreviewState.PREVIEW_READY
    receipt = SourceFileReceipt(
        receipt_id=f"replacement-receipt-{suffix}",
        project_key=_PROJECT,
        blob_id=f"sha256:{digest}",
        content_sha256=digest,
        received_at=_NOW,
        original_filename=scan.source_name,
        model_candidates=(mapping_v2._MODEL,),
        lot_candidates=(mapping_v2._LOT,),
        declared_mime_type=mapping_v2._MIME,
        detected_mime_type=mapping_v2._MIME,
        canonical_extension=".xlsx",
        size_bytes=scan.source_size_bytes,
    )
    outcome = StoreScanMappingOutcome(
        status=StoreScanMappingStatus.PREVIEW_READY,
        scope=ResolvedMappingScope(_PROJECT, _SUPPLIER_SCOPE),
        receipt=receipt,
        scan=scan,
        mapping_result=preview,
    )
    candidate = build_long_candidate(outcome, mapping_v2._bindings(template))
    persisted = LongPersistenceService(review.database, clock=lambda: _NOW).persist(
        LongPersistenceRequest(
            outcome=outcome,
            candidate=candidate,
            loader_version="result-replacement-long-v1",
            scan_contract_version="result-replacement-scan-v1",
        )
    )
    with review.database.session() as session:
        result_id = session.scalar(
            select(LongInspectionResultRow.id)
            .join(OqcLotRow, OqcLotRow.id == LongInspectionResultRow.oqc_lot_id)
            .where(OqcLotRow.ingestion_job_id == persisted.ingestion_job_id)
        )
    assert result_id is not None
    return result_id


def _fixture(
    tmp_path: Path,
    *,
    predecessor_fail: bool = True,
    predecessor_status: LongDataStatus = LongDataStatus.VALID,
    successor_values: tuple[Decimal, Decimal] = (
        Decimal("2.00"),
        Decimal("2.10"),
    ),
) -> _ReplacementFixture:
    review = data_review._fixture(
        tmp_path / "replacement",
        fail_sample=predecessor_fail,
    )
    review_service = DataStatusReviewService(review.database, clock=lambda: _NOW)
    predecessor_candidate = review_service.candidate(
        project_key=_PROJECT,
        result_id=review.result_id,
    )
    predecessor_command = data_review._command(
        predecessor_candidate,
        target=predecessor_status,
        command_id="replacement-predecessor-decision",
    )
    predecessor_decision = review_service.decide(predecessor_command)
    assert predecessor_decision.target_status == predecessor_status
    successor_result_id = _add_result(
        review,
        suffix="successor",
        digest=_SUCCESSOR_HASH,
        values=successor_values,
    )
    sibling_result_id = _add_result(
        review,
        suffix="sibling",
        digest="c" * 64,
        values=(Decimal("2.00"), Decimal("2.05")),
    )
    return _ReplacementFixture(
        review=review,
        predecessor_result_id=review.result_id,
        successor_result_id=successor_result_id,
        sibling_result_id=sibling_result_id,
        predecessor_review_command=predecessor_command,
    )


def _candidate(fixture: _ReplacementFixture):
    return ResultReplacementService(fixture.review.database, clock=lambda: _NOW).candidate(
        ReplacementCandidateRequest(
            _PROJECT,
            fixture.predecessor_result_id,
            fixture.successor_result_id,
        )
    )


def _command(
    candidate,
    *,
    reason: str = "Confirm one audited replacement link.",
    actor: Actor = data_review._ADMIN,
):
    return DecideResultReplacementCommand(
        project_key=candidate.project_key,
        predecessor_result_id=candidate.predecessor.result_id,
        successor_result_id=candidate.successor.result_id,
        candidate_sha256=candidate.candidate_sha256,
        expected_predecessor_result_row_version=candidate.predecessor.row_version,
        expected_successor_result_row_version=candidate.successor.row_version,
        expected_predecessor_measurement_set_sha256=(candidate.predecessor.measurement_set_sha256),
        expected_successor_measurement_set_sha256=(candidate.successor.measurement_set_sha256),
        expected_predecessor_decision_transition_id=(
            candidate.predecessor.original_data_status_transition_id
        ),
        expected_successor_data_review_candidate_sha256=(
            candidate.successor.data_review_candidate_sha256
        ),
        confirmed=True,
        reason=reason,
        actor=actor,
    )


class _FailSecondAuditRepository(AuditRepository):
    def __init__(self) -> None:
        self.calls = 0

    def append(self, session, change: AuditChange):
        self.calls += 1
        if self.calls == 2:
            raise RuntimeError("synthetic replacement Audit failure")
        return super().append(session, change)


@pytest.mark.required_test_id("DQ-P2-REPL-001")
def test_candidate_is_read_only_deterministic_and_identity_scoped(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    with fixture.review.database.session() as session:
        before_audits = session.scalar(select(func.count()).select_from(AuditLog))
    first = _candidate(fixture)
    second = _candidate(fixture)

    assert first == second
    assert first.candidate_sha256 == second.candidate_sha256
    assert first.can_replace
    assert first.predecessor.data_status == LongDataStatus.VALID
    assert first.successor.data_status == LongDataStatus.PENDING
    assert first.identity.source_lot_text == mapping_v2._LOT
    assert not first.capabilities.automatic_replacement
    assert not first.capabilities.automatic_valid
    with fixture.review.database.session() as session:
        assert session.scalar(select(func.count()).select_from(ResultReplacementTransitionRow)) == 0
        assert session.scalar(select(func.count()).select_from(AuditLog)) == before_audits


@pytest.mark.required_test_id("DQ-P2-REPL-002")
def test_candidate_marks_fail_to_pass_and_changed_evidence_without_inference(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    candidate = _candidate(fixture)
    codes = {value.code for value in candidate.differences}

    assert ReplacementDifferenceCode.NG_TO_PASS in codes
    assert ReplacementDifferenceCode.JUDGMENT_CHANGED in codes
    assert ReplacementDifferenceCode.MEASUREMENT_SET_CHANGED in codes
    assert ReplacementDifferenceCode.NOT_EVALUABLE in codes
    assert candidate.predecessor.system_judgment is not None
    assert candidate.predecessor.system_judgment.value == "FAIL"
    assert candidate.successor.proposed_system_judgment is not None
    assert candidate.successor.proposed_system_judgment.value == "PASS"
    assert not candidate.capabilities.measurement_pairing


@pytest.mark.required_test_id("DQ-P2-REPL-003")
@pytest.mark.required_test_id("DQ-P2-REPLUI-001")
def test_atomic_pair_changes_only_selected_results_and_complete_measurement_sets(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    _assert_stale_forged_and_cross_scope_zero_mutation(fixture)
    service = ResultReplacementService(fixture.review.database, clock=lambda: _NOW)
    candidate = _candidate(fixture)
    decision = service.decide(_command(candidate))

    assert not decision.replayed
    with fixture.review.database.session() as session:
        predecessor = session.get(LongInspectionResultRow, fixture.predecessor_result_id)
        successor = session.get(LongInspectionResultRow, fixture.successor_result_id)
        sibling = session.get(LongInspectionResultRow, fixture.sibling_result_id)
        predecessor_samples = session.scalars(
            select(LongMeasurementRow).where(
                LongMeasurementRow.inspection_result_id == fixture.predecessor_result_id
            )
        ).all()
        successor_samples = session.scalars(
            select(LongMeasurementRow).where(
                LongMeasurementRow.inspection_result_id == fixture.successor_result_id
            )
        ).all()
        sibling_samples = session.scalars(
            select(LongMeasurementRow).where(
                LongMeasurementRow.inspection_result_id == fixture.sibling_result_id
            )
        ).all()
    assert predecessor is not None and successor is not None and sibling is not None
    assert (predecessor.data_status, predecessor.row_version) == ("REPLACED", 3)
    assert (successor.data_status, successor.row_version) == ("VALID", 2)
    assert (sibling.data_status, sibling.row_version) == ("PENDING", 1)
    assert {(value.data_status, value.row_version) for value in predecessor_samples} == {
        ("REPLACED", 3)
    }
    assert {(value.data_status, value.row_version) for value in successor_samples} == {("VALID", 2)}
    assert {(value.data_status, value.row_version) for value in sibling_samples} == {("PENDING", 1)}
    assert decision.predecessor_measurement_count == len(predecessor_samples)
    assert decision.successor_measurement_count == len(successor_samples)


@pytest.mark.required_test_id("DQ-P2-REPL-004")
def test_suspect_predecessor_and_fail_successor_become_replaced_and_valid_atomically(
    tmp_path: Path,
) -> None:
    fixture = _fixture(
        tmp_path,
        predecessor_fail=False,
        predecessor_status=LongDataStatus.SUSPECT,
        successor_values=(Decimal("2.00"), Decimal("2.11")),
    )
    candidate = _candidate(fixture)
    assert candidate.predecessor.data_status == LongDataStatus.SUSPECT
    assert candidate.successor.proposed_system_judgment is not None
    assert candidate.successor.proposed_system_judgment.value == "FAIL"

    decision = ResultReplacementService(
        fixture.review.database,
        clock=lambda: _NOW,
    ).decide(_command(candidate, reason="Explicitly accept FAIL successor as VALID."))
    with fixture.review.database.session() as session:
        predecessor = session.get(LongInspectionResultRow, fixture.predecessor_result_id)
        successor = session.get(LongInspectionResultRow, fixture.successor_result_id)
        official = DataReviewRepository().select_valid_measurements(
            session,
            project_key=_PROJECT,
            canonical_item_key=data_review._ITEM_KEY,
        )
    assert predecessor is not None and successor is not None
    assert predecessor.data_status == "REPLACED"
    assert successor.data_status == "VALID"
    assert successor.system_judgment == "FAIL"
    assert {value.result_id for value in official} == {fixture.successor_result_id}
    assert decision.predecessor_result_id not in {value.result_id for value in official}


def _history_request() -> HistoricalComparisonRequest:
    period = HistoricalDateRange(date(2026, 6, 15), date(2026, 6, 15))
    return HistoricalComparisonRequest(
        project_key=_PROJECT,
        left=period,
        right=period,
        data_statuses=("REPLACED", "VALID"),
        filters=HistoricalFilters(canonical_model_key=data_review._MODEL_KEY),
        limit_per_side=20,
    )


@pytest.mark.required_test_id("DQ-P2-REPL-005")
def test_a_to_b_to_c_preserves_complete_result_measurement_and_history_chain(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    service = ResultReplacementService(fixture.review.database, clock=lambda: _NOW)
    first = service.decide(_command(_candidate(fixture), reason="Link A to B."))
    third_result_id = _add_result(
        fixture.review,
        suffix="chain-tail",
        digest="e" * 64,
        values=(Decimal("2.00"), Decimal("2.08")),
    )
    second_candidate = service.candidate(
        ReplacementCandidateRequest(
            _PROJECT,
            fixture.successor_result_id,
            third_result_id,
        )
    )
    second = service.decide(_command(second_candidate, reason="Link B to C."))

    with fixture.review.database.session() as session:
        statuses = dict(
            session.execute(
                select(LongInspectionResultRow.id, LongInspectionResultRow.data_status).where(
                    LongInspectionResultRow.id.in_(
                        (
                            fixture.predecessor_result_id,
                            fixture.successor_result_id,
                            third_result_id,
                        )
                    )
                )
            ).all()
        )
        children = session.scalars(
            select(ResultReplacementMeasurementRow).order_by(
                ResultReplacementMeasurementRow.transition_id,
                ResultReplacementMeasurementRow.side,
                ResultReplacementMeasurementRow.sample_ordinal,
            )
        ).all()
    assert statuses == {
        fixture.predecessor_result_id: "REPLACED",
        fixture.successor_result_id: "REPLACED",
        third_result_id: "VALID",
    }
    assert len(children) == 8
    assert {value.transition_id for value in children} == {
        first.replacement_id,
        second.replacement_id,
    }
    assert all(value.after_row_version == value.before_row_version + 1 for value in children)

    history = HistoricalComparisonService(fixture.review.database).compare(_history_request())
    middle = next(
        value for value in history.left.results if value.result_id == fixture.successor_result_id
    )
    assert middle.replacement_chain is not None
    assert middle.replacement_chain.head_result_id == fixture.predecessor_result_id
    assert middle.replacement_chain.tail_result_id == third_result_id
    assert middle.replacement_chain.current_position == 1
    assert middle.replacement_chain.returned_link_count == 2
    assert not middle.replacement_chain.has_more


@pytest.mark.required_test_id("DQ-P2-REPL-008")
def test_two_audits_and_all_pair_mutations_roll_back_together(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    candidate = _candidate(fixture)
    with fixture.review.database.session() as session:
        before_audits = session.scalar(select(func.count()).select_from(AuditLog))
        before_transitions = session.scalar(
            select(func.count()).select_from(DataStatusTransitionRow)
        )
    failing_audit = _FailSecondAuditRepository()
    service = ResultReplacementService(
        fixture.review.database,
        audit_repository=failing_audit,
        clock=lambda: _NOW,
    )

    with pytest.raises(RuntimeError, match="replacement Audit failure"):
        service.decide(_command(candidate))

    with fixture.review.database.session() as session:
        predecessor = session.get(LongInspectionResultRow, fixture.predecessor_result_id)
        successor = session.get(LongInspectionResultRow, fixture.successor_result_id)
        samples = session.scalars(
            select(LongMeasurementRow).where(
                LongMeasurementRow.inspection_result_id.in_(
                    (fixture.predecessor_result_id, fixture.successor_result_id)
                )
            )
        ).all()
        assert session.scalar(select(func.count()).select_from(AuditLog)) == before_audits
        assert (
            session.scalar(select(func.count()).select_from(DataStatusTransitionRow))
            == before_transitions
        )
        assert session.scalar(select(func.count()).select_from(ResultReplacementTransitionRow)) == 0
    assert predecessor is not None and successor is not None
    assert (predecessor.data_status, predecessor.row_version) == ("VALID", 2)
    assert (successor.data_status, successor.row_version) == ("PENDING", 1)
    assert {value.data_status for value in samples} == {"VALID", "PENDING"}

    decision = ResultReplacementService(
        fixture.review.database,
        clock=lambda: _NOW,
    ).decide(_command(candidate))
    with fixture.review.database.session() as session:
        audits = session.scalars(
            select(AuditLog)
            .where(
                AuditLog.target_id.in_(
                    (
                        f"{_PROJECT}:{fixture.successor_result_id}",
                        f"{_PROJECT}:{decision.replacement_id}",
                    )
                )
            )
            .order_by(AuditLog.action)
        ).all()
    assert {value.action for value in audits} == {
        "DATA_STATUS_DECIDED",
        "RESULT_REPLACED",
    }
    assert {value.requirement_id for value in audits} == {"ING-041", "ING-042"}
    assert {value.occurred_at for value in audits} == {_NOW}
    assert {value.actor_id for value in audits} == {data_review._ADMIN.actor_id}
    assert {value.reason for value in audits} == {decision.reason}


def _assert_stale_forged_and_cross_scope_zero_mutation(
    fixture: _ReplacementFixture,
) -> None:
    candidate = _candidate(fixture)
    service = ResultReplacementService(fixture.review.database, clock=lambda: _NOW)
    with fixture.review.database.session() as session:
        before = (
            session.scalar(select(func.count()).select_from(ResultReplacementTransitionRow)),
            session.scalar(select(func.sum(LongInspectionResultRow.row_version))),
            session.scalar(select(func.sum(LongMeasurementRow.row_version))),
        )

    forged = replace(_command(candidate), candidate_sha256="a" * 64)
    with pytest.raises(ResultReplacementStaleError):
        service.decide(forged)
    stale = replace(
        _command(candidate),
        expected_successor_result_row_version=candidate.successor.row_version + 1,
    )
    with pytest.raises(ResultReplacementStaleError):
        service.decide(stale)
    with pytest.raises(ResultReplacementMissingError) as cross_scope:
        service.candidate(
            ReplacementCandidateRequest(
                "other-project",
                fixture.predecessor_result_id,
                fixture.successor_result_id,
            )
        )
    assert getattr(cross_scope.value, "code", "") == "RESULT_REPLACEMENT_NOT_FOUND"

    with fixture.review.database.session() as session:
        after = (
            session.scalar(select(func.count()).select_from(ResultReplacementTransitionRow)),
            session.scalar(select(func.sum(LongInspectionResultRow.row_version))),
            session.scalar(select(func.sum(LongMeasurementRow.row_version))),
        )
    assert after == before


def _candidate_body(fixture: _ReplacementFixture) -> dict[str, object]:
    return {
        "project_key": _PROJECT,
        "predecessor_result_id": fixture.predecessor_result_id,
        "successor_result_id": fixture.successor_result_id,
    }


def _decision_body(candidate) -> dict[str, object]:
    return {
        "project_key": candidate.project_key,
        "predecessor_result_id": candidate.predecessor.result_id,
        "successor_result_id": candidate.successor.result_id,
        "candidate_sha256": candidate.candidate_sha256,
        "expected_predecessor_result_row_version": candidate.predecessor.row_version,
        "expected_successor_result_row_version": candidate.successor.row_version,
        "expected_predecessor_measurement_set_sha256": (
            candidate.predecessor.measurement_set_sha256
        ),
        "expected_successor_measurement_set_sha256": (candidate.successor.measurement_set_sha256),
        "expected_predecessor_decision_transition_id": (
            candidate.predecessor.original_data_status_transition_id
        ),
        "expected_successor_data_review_candidate_sha256": (
            candidate.successor.data_review_candidate_sha256
        ),
        "confirmed": True,
        "reason": "Confirm the bounded HTTP replacement contract.",
    }


@pytest.mark.required_test_id("DQ-P2-REPL-006")
@pytest.mark.required_test_id("DQ-P2-REPLUI-002")
def test_http_contract_is_bounded_explicit_authorized_and_safe(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    candidate_value = _candidate(fixture)
    service = ResultReplacementService(fixture.review.database, clock=lambda: _NOW)
    application = FastAPI()
    application.include_router(create_result_replacement_router(service))
    with TestClient(application) as client:
        response = client.post(
            "/api/v1/result-replacements/candidates",
            json=_candidate_body(fixture),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["can_replace"] is True
        assert body["predecessor"]["returned_measurement_count"] == 2
        assert body["predecessor"]["measurements_has_more"] is False
        assert not {
            "actor",
            "roles",
            "command_id",
            "target_status",
        }.intersection(_decision_body(candidate_value))

        invalid = _decision_body(candidate_value)
        invalid["confirmed"] = False
        error = client.post("/api/v1/result-replacements/decisions", json=invalid)
        assert error.status_code == 400
        assert set(error.json()["detail"]) == {"code", "message", "status_label"}
        extra = dict(_candidate_body(fixture), actor="forged-admin")
        extra_error = client.post(
            "/api/v1/result-replacements/candidates",
            json=extra,
        )
        assert extra_error.status_code == 400
        stale = _decision_body(candidate_value)
        stale["candidate_sha256"] = "a" * 64
        stale_error = client.post(
            "/api/v1/result-replacements/decisions",
            json=stale,
        )
        assert stale_error.status_code == 409
        cross_scope = client.post(
            "/api/v1/result-replacements/candidates",
            json={**_candidate_body(fixture), "project_key": "other-project"},
        )
        assert cross_scope.status_code == 404
        missing = client.get(
            "/api/v1/result-replacements/not-found",
            params={"project_key": _PROJECT},
        )
        assert missing.status_code == 404
        safe_payload = str(
            [
                error.json(),
                extra_error.json(),
                stale_error.json(),
                cross_scope.json(),
                missing.json(),
            ]
        )
        assert str(tmp_path) not in safe_payload
        assert "C:\\" not in safe_payload

        accepted = client.post(
            "/api/v1/result-replacements/decisions",
            json=_decision_body(candidate_value),
        )
        assert accepted.status_code == 200
        accepted_body = accepted.json()
        assert accepted_body["predecessor_status"] == "REPLACED"
        assert accepted_body["successor_status"] == "VALID"
        assert accepted_body["replayed"] is False
        replay = client.get(
            f"/api/v1/result-replacements/{accepted_body['replacement_id']}",
            params={"project_key": _PROJECT},
        )
        assert replay.status_code == 200
        replay_body = replay.json()
        assert replay_body["replacement_id"] == accepted_body["replacement_id"]
        assert replay_body["candidate_sha256"] == accepted_body["candidate_sha256"]
        assert replay_body["intent_sha256"] == accepted_body["intent_sha256"]
        assert replay_body["replayed"] is True

    viewer_app = FastAPI()
    viewer_app.include_router(
        create_result_replacement_router(service, trusted_actor=data_review._VIEWER)
    )
    with TestClient(viewer_app) as client:
        forbidden = client.post(
            "/api/v1/result-replacements/decisions",
            json=_decision_body(candidate_value),
        )
        assert forbidden.status_code == 403
        assert forbidden.json()["detail"]["code"] == "RESULT_REPLACEMENT_FORBIDDEN"

    base = candidate_value
    large_measurements = tuple(
        replace(
            candidate_measurement,
            measurement_id=f"bounded-measurement-{ordinal:03d}",
            sample_ordinal=ordinal,
        )
        for ordinal, candidate_measurement in enumerate(
            (base.predecessor.measurements[0] for _ in range(101)),
            start=1,
        )
    )
    large_predecessor = replace(
        base.predecessor,
        measurements=large_measurements,
        measurement_set_sha256=measurement_set_sha256(large_measurements),
    )
    large_candidate = replace(base, predecessor=large_predecessor)

    class _LargeCandidatePort:
        def candidate(self, request):
            return large_candidate

        def decide(self, command):
            raise AssertionError("bounded candidate test cannot decide")

        def get(self, *, project_key: str, replacement_id: str):
            raise AssertionError("bounded candidate test cannot get")

    bounded_app = FastAPI()
    bounded_app.include_router(create_result_replacement_router(_LargeCandidatePort()))
    with TestClient(bounded_app) as client:
        bounded = client.post(
            "/api/v1/result-replacements/candidates",
            json=_candidate_body(fixture),
        )
    assert bounded.status_code == 200
    bounded_proof = bounded.json()["predecessor"]
    assert bounded_proof["measurement_count"] == 101
    assert bounded_proof["returned_measurement_count"] == 100
    assert bounded_proof["measurements_has_more"] is True
    assert bounded_proof["measurement_set_sha256"] == measurement_set_sha256(large_measurements)

    class _UnavailablePort:
        @staticmethod
        def _raise():
            raise ResultReplacementUnavailableError(
                "RESULT_REPLACEMENT_DATABASE_UNAVAILABLE",
                "Temporary local database failure.",
                "Service unavailable",
            )

        def candidate(self, request):
            self._raise()

        def decide(self, command):
            self._raise()

        def get(self, *, project_key: str, replacement_id: str):
            self._raise()

    unavailable_app = FastAPI()
    unavailable_app.include_router(create_result_replacement_router(_UnavailablePort()))
    with TestClient(unavailable_app) as client:
        unavailable = client.post(
            "/api/v1/result-replacements/candidates",
            json=_candidate_body(fixture),
        )
    assert unavailable.status_code == 503
    assert set(unavailable.json()["detail"]) == {"code", "message", "status_label"}


@pytest.mark.required_test_id("DQ-P2-REPL-007")
def test_restart_replay_precedes_eligibility_and_survives_a_to_b_to_c(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    service = ResultReplacementService(fixture.review.database, clock=lambda: _NOW)
    first_candidate = _candidate(fixture)
    first_command = _command(first_candidate)
    first = service.decide(first_command)

    third_result_id = _add_result(
        fixture.review,
        suffix="third",
        digest="d" * 64,
        values=(Decimal("2.00"), Decimal("2.09")),
    )
    second_candidate = service.candidate(
        ReplacementCandidateRequest(
            _PROJECT,
            fixture.successor_result_id,
            third_result_id,
        )
    )
    second = service.decide(_command(second_candidate, reason="Link B to C explicitly."))
    assert second.predecessor_result_id == fixture.successor_result_id

    restarted = ResultReplacementService(fixture.review.database, clock=lambda: _NOW)
    replay = restarted.decide(first_command)
    assert replay.replayed
    assert replay.replacement_id == first.replacement_id
    assert replay.decided_by == first.decided_by
    assert replay.decided_at == first.decided_at
    with pytest.raises(ResultReplacementConflictError):
        restarted.decide(_command(first_candidate, actor=_OTHER_ADMIN))

    fourth_result_id = _add_result(
        fixture.review,
        suffix="branch-target",
        digest="f" * 64,
        values=(Decimal("2.00"), Decimal("2.07")),
    )
    with pytest.raises(ResultReplacementIneligibleError):
        restarted.candidate(
            ReplacementCandidateRequest(
                _PROJECT,
                fixture.successor_result_id,
                fourth_result_id,
            )
        )
    with pytest.raises(ResultReplacementIneligibleError):
        restarted.candidate(
            ReplacementCandidateRequest(
                _PROJECT,
                third_result_id,
                fixture.predecessor_result_id,
            )
        )
    with fixture.review.database.session() as session:
        links = session.scalars(select(ResultReplacementTransitionRow)).all()
    assert len(links) == 2
    assert len({value.predecessor_result_id for value in links}) == 2
    assert len({value.successor_result_id for value in links}) == 2


@pytest.mark.required_test_id("DQ-P2-REPL-009")
def test_original_review_replay_and_history_fail_closed_on_chain_audit_tamper(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    replacement = ResultReplacementService(
        fixture.review.database,
        clock=lambda: _NOW,
    ).decide(_command(_candidate(fixture)))

    original = DataStatusReviewService(
        fixture.review.database,
        clock=lambda: _NOW,
    ).decide(fixture.predecessor_review_command)
    assert original.replayed
    assert original.result_id == fixture.predecessor_result_id

    history_service = HistoricalComparisonService(fixture.review.database)
    comparison = history_service.compare(_history_request())
    predecessor = next(
        value
        for value in comparison.left.results
        if value.result_id == fixture.predecessor_result_id
    )
    assert predecessor.decision is not None
    assert predecessor.decision.transition_id == original.transition_id
    assert predecessor.replacement_chain is not None
    assert predecessor.replacement_chain.returned_link_count == 1
    assert predecessor.replacement_chain.links[0].replacement_id == replacement.replacement_id

    application = FastAPI()
    application.include_router(create_historical_comparison_router(history_service))
    request_body = {
        "project_key": _PROJECT,
        "left": {"date_from": "2026-06-15", "date_to": "2026-06-15"},
        "right": {"date_from": "2026-06-15", "date_to": "2026-06-15"},
        "data_statuses": ["REPLACED", "VALID"],
        "filters": {"canonical_model_key": data_review._MODEL_KEY},
        "limit_per_side": 20,
    }
    with TestClient(application) as client:
        response = client.post("/api/v1/history/comparisons", json=request_body)
    assert response.status_code == 200
    response_result = next(
        value
        for value in response.json()["left"]["results"]
        if value["result_id"] == fixture.predecessor_result_id
    )
    response_chain = response_result["replacement_chain"]
    assert response_chain["links_sha256"] == mapping_v2.canonical_json_sha256(
        response_chain["links"]
    )

    with fixture.review.database.session() as session, session.begin():
        successor_audit = session.scalar(
            select(AuditLog).where(
                AuditLog.action == "DATA_STATUS_DECIDED",
                AuditLog.target_id == f"{_PROJECT}:{fixture.successor_result_id}",
            )
        )
        assert successor_audit is not None
        session.delete(successor_audit)
    with pytest.raises(HistoricalComparisonError) as tamper:
        history_service.compare(_history_request())
    assert tamper.value.code == "HISTORY_EVIDENCE_UNAVAILABLE"


def _migration_snapshot(database_url: str) -> dict[str, list[dict[str, object]]]:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            return {
                "results": [
                    dict(value)
                    for value in connection.execute(
                        text(
                            "SELECT id,project_key,data_status,row_version,source_evidence_sha256,"
                            "binding_snapshot_sha256,candidate_snapshot_sha256,"
                            "current_data_status_transition_id,current_decision_command_id,"
                            "current_decision_candidate_sha256,current_decision_mode,"
                            "system_judgment,system_judgment_status,spec_evaluation_status,"
                            "applied_master_history_id,applied_master_revision_id,"
                            "applied_master_payload_sha256,current_decided_by,current_decided_at,"
                            "current_decision_reason FROM inspection_results ORDER BY id"
                        )
                    ).mappings()
                ],
                "measurements": [
                    dict(value)
                    for value in connection.execute(
                        text(
                            "SELECT id,project_key,inspection_result_id,data_status,row_version,"
                            "evidence_sha256,raw_value_tag,raw_value_text,raw_numeric_value,"
                            "raw_qualitative_value,source_cell FROM measurements ORDER BY id"
                        )
                    ).mappings()
                ],
                "transitions": [
                    dict(value)
                    for value in connection.execute(
                        text(
                            "SELECT id,project_key,inspection_result_id,command_id,intent_sha256,"
                            "candidate_sha256,decision_snapshot_sha256,from_status,to_status,"
                            "before_result_row_version,after_result_row_version,decided_by,"
                            "decided_at,reason FROM data_status_transitions ORDER BY id"
                        )
                    ).mappings()
                ],
                "audits": [
                    dict(value)
                    for value in connection.execute(
                        text(
                            "SELECT id,actor_id,action,target_type,target_id,reason,requirement_id,"
                            "source_reference FROM audit_log ORDER BY id"
                        )
                    ).mappings()
                ],
            }
    finally:
        engine.dispose()


@pytest.mark.required_test_id("DQ-P2-REPL-010")
def test_0008_preserves_real_decisions_and_refuses_lossy_downgrade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MASS_PRODUCTION_QUALITY_VALIDATION_DATABASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)
    fixture = _fixture(tmp_path / "migration")
    review_service = DataStatusReviewService(fixture.review.database, clock=lambda: _NOW)
    suspect_candidate = review_service.candidate(
        project_key=_PROJECT,
        result_id=fixture.sibling_result_id,
    )
    suspect_command = data_review._command(
        suspect_candidate,
        target=LongDataStatus.SUSPECT,
        command_id="replacement-migration-suspect-decision",
    )
    suspect_decision = review_service.decide(suspect_command)
    assert suspect_decision.target_status == LongDataStatus.SUSPECT
    database_path = fixture.review.database_path
    database_url = f"sqlite+pysqlite:///{database_path.as_posix()}"
    before = _migration_snapshot(database_url)
    fixture.review.database.dispose()
    config = migration_tests._config(database_url)
    alembic_command.stamp(config, "0008")

    alembic_command.downgrade(config, "0007")
    assert _migration_snapshot(database_url) == before
    engine = create_engine(database_url)
    try:
        assert "result_replacement_transitions" not in inspect(engine).get_table_names()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE measurements SET data_status='REPLACED' "
                    "WHERE inspection_result_id=:result_id AND sample_ordinal=1"
                ),
                {"result_id": fixture.predecessor_result_id},
            )
        corrupt = _migration_snapshot(database_url)
        with pytest.raises(IntegrityError, match="CHECK constraint failed"):
            alembic_command.upgrade(config, "0008")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0007"
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
            failed_tables = set(inspect(engine).get_table_names())
            assert "result_replacement_transitions" not in failed_tables
            assert not any(
                name.startswith("_alembic_tmp_") or "0008_backup" in name for name in failed_tables
            )
        assert _migration_snapshot(database_url) == corrupt
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE measurements SET data_status='VALID' "
                    "WHERE inspection_result_id=:result_id AND sample_ordinal=1"
                ),
                {"result_id": fixture.predecessor_result_id},
            )
    finally:
        engine.dispose()

    alembic_command.upgrade(config, "0008")
    assert _migration_snapshot(database_url) == before
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT COUNT(*) FROM result_replacement_transitions")) == 0
            )
            assert (
                connection.scalar(text("SELECT COUNT(*) FROM result_replacement_measurements")) == 0
            )
    finally:
        engine.dispose()
    restarted = Database(database_url)
    try:
        original = DataStatusReviewService(restarted, clock=lambda: _NOW).decide(
            fixture.predecessor_review_command
        )
        assert original.replayed
        suspect_replay = DataStatusReviewService(restarted, clock=lambda: _NOW).decide(
            suspect_command
        )
        assert suspect_replay.replayed
        assert suspect_replay.target_status == LongDataStatus.SUSPECT
        service = ResultReplacementService(restarted, clock=lambda: _NOW)
        candidate = service.candidate(
            ReplacementCandidateRequest(
                _PROJECT,
                fixture.predecessor_result_id,
                fixture.successor_result_id,
            )
        )
        service.decide(_command(candidate))
    finally:
        restarted.dispose()

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
            assert (
                compare_metadata(
                    MigrationContext.configure(connection, opts={"compare_type": True}),
                    Base.metadata,
                )
                == []
            )
            tables_before_refusal = set(inspect(engine).get_table_names())
            assert not any(
                name.startswith("_alembic_tmp_") or "0008_backup" in name
                for name in tables_before_refusal
            )
        with pytest.raises(RuntimeError, match="0008 downgrade refused"):
            alembic_command.downgrade(config, "0007")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0008"
            assert (
                connection.scalar(text("SELECT COUNT(*) FROM result_replacement_transitions")) == 1
            )
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
            assert set(inspect(engine).get_table_names()) == tables_before_refusal
    finally:
        engine.dispose()
    assert not (tmp_path / ".localdata").exists()
