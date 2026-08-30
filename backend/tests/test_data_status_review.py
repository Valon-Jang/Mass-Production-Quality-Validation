from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import backend.tests.test_mapping_v2_evidence as mapping_v2
import backend.tests.test_migrations as migration_tests
import pytest
from alembic import command as alembic_command
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.exc import IntegrityError

from app.application.data_review import (
    DataReviewAuthorizationError,
    DataStatusReviewService,
    DecideDataStatusCommand,
    ExpectedMasterVersion,
    ExpectedMeasurementVersion,
    IneligibleDataReviewCandidateError,
    StaleDataReviewCandidateError,
)
from app.application.long_candidate import build_long_candidate
from app.application.long_persistence import LongPersistenceRequest, LongPersistenceService
from app.application.mapping_preview import InMemoryMappingTemplateRegistry, build_mapping_preview
from app.application.master_config_commands import (
    ApproveMasterSpecRevisionCommand,
    CreateCanonicalInspectionItemCommand,
    CreateCanonicalModelCommand,
    CreateCanonicalModelPartCommand,
    CreateCanonicalSupplierCommand,
    CreateMasterSpecRevisionCommand,
    MasterConfigCommandService,
    ReviewMasterSpecRevisionCommand,
    SetInspectionItemDispositionCommand,
    SupersedeMasterSpecRevisionCommand,
)
from app.application.store_scan_mapping import (
    ResolvedMappingScope,
    StoreScanMappingOutcome,
    StoreScanMappingStatus,
)
from app.domain.audit import AuditChange
from app.domain.data_review import ReviewCandidateState, ReviewIssueCode, SystemJudgment
from app.domain.identity import Actor, ActorKind, Role
from app.domain.long_format import LongDataStatus, SpecEvaluationStatus
from app.domain.mapping import MappingPreviewRequest, MappingPreviewState, MappingTemplateStatus
from app.domain.master_config import (
    CanonicalInspectionItem,
    CanonicalModel,
    CanonicalModelPart,
    CanonicalSupplier,
    ConfigurationRevisionStatus,
    InspectionItemDisposition,
    MasterSpecRevision,
)
from app.domain.source_file import SourceFileReceipt
from app.infrastructure.audit import AuditLog, AuditRepository
from app.infrastructure.data_review import (
    DataReviewCommandConflictError,
    DataReviewPersistenceError,
    DataReviewRepository,
    DataStatusTransitionRow,
)
from app.infrastructure.database import Database
from app.infrastructure.long_format import (
    LongIngestionJobRow,
    LongInspectionResultRow,
    LongMeasurementRow,
    OqcLotRow,
)
from app.infrastructure.master_config import (
    CanonicalInspectionItemRow,
    MasterSpecHistoryRow,
    MasterSpecRevisionRow,
    PersistedMasterSpecRevision,
    _payload_digest,
    _serialize_master_spec,
)

_PROJECT = mapping_v2._PROJECT
_SUPPLIER_SCOPE = mapping_v2._SUPPLIER_SCOPE
_MODEL_KEY = "canonical:model:v2"
_SUPPLIER_KEY = "canonical:supplier:v2"
_PART_KEY = "canonical:part:v2"
_ITEM_KEY = "canonical:item:row-4"
_NOW = datetime(2026, 8, 15, 11, 0, tzinfo=UTC)
_INSPECTION_DATE = mapping_v2._INSPECTION_DATE

_ADMIN = Actor(
    actor_id="data-review-admin",
    kind=ActorKind.LOCAL_OWNER,
    roles=frozenset({Role.ADMIN}),
)
_REVIEWER = Actor(
    actor_id="data-review-reviewer",
    kind=ActorKind.LOCAL_OWNER,
    roles=frozenset({Role.REVIEWER}),
)
_VIEWER = Actor(
    actor_id="data-review-viewer",
    kind=ActorKind.LOCAL_OWNER,
    roles=frozenset({Role.VIEWER}),
)


@dataclass(frozen=True, slots=True)
class _ReviewFixture:
    database: Database
    database_path: Path
    result_id: str
    lot_id: str
    master: PersistedMasterSpecRevision


def _clock() -> datetime:
    return _NOW


def _two_sample_scan(
    *,
    values: tuple[Decimal, Decimal] = (Decimal("2.00"), Decimal("2.10")),
    source_unit: str = "mm",
):
    base = mapping_v2._scan()
    sheet = base.sheets[0]
    extra = (
        mapping_v2._cell("U4", values[0]),
        mapping_v2._cell("V4", values[1]),
    )
    cells = tuple(
        mapping_v2._cell("J4", source_unit) if value.coordinate == "J4" else value
        for value in sheet.cells
    )
    changed_sheet = replace(
        sheet,
        used_range="A1:V4",
        estimated_cells=sheet.estimated_cells + len(extra),
        cells=(*cells, *extra),
    )
    return replace(
        base,
        sheets=(changed_sheet,),
        estimated_cells=base.estimated_cells + len(extra),
    )


def _two_sample_template():
    base = mapping_v2._template(
        schema_version="2",
        status=MappingTemplateStatus.DRAFT,
    )
    row = replace(
        base.inspection_rows[0],
        sample_cells=(mapping_v2._address("U4"), mapping_v2._address("V4")),
    )
    sheet_structure = replace(
        base.fingerprint.sheet_structures[0],
        expected_used_range="A1:V4",
    )
    row_structure = replace(
        base.fingerprint.row_structures[0],
        expected_non_empty_cells=(
            *base.fingerprint.row_structures[0].expected_non_empty_cells,
            mapping_v2._address("U4"),
            mapping_v2._address("V4"),
        ),
    )
    return replace(
        base,
        fingerprint=replace(
            base.fingerprint,
            sheet_structures=(sheet_structure,),
            row_structures=(row_structure,),
        ),
        inspection_rows=(row,),
    )


def _long_outcome(
    database: Database,
    *,
    fail_sample: bool = False,
    source_unit: str = "mm",
) -> tuple[str, str]:
    template = _two_sample_template()
    persisted = mapping_v2._persist_mapping(database, template)
    scan = _two_sample_scan(
        values=(Decimal("2.00"), Decimal("2.11"))
        if fail_sample
        else (Decimal("2.00"), Decimal("2.10")),
        source_unit=source_unit,
    )
    registry = InMemoryMappingTemplateRegistry()
    registry.register(persisted.template)
    preview = build_mapping_preview(
        scan,
        MappingPreviewRequest(project_key=_PROJECT, supplier_scope=_SUPPLIER_SCOPE),
        registry,
    )
    assert preview.state == MappingPreviewState.PREVIEW_READY
    receipt = SourceFileReceipt(
        receipt_id="data-review-receipt",
        project_key=_PROJECT,
        blob_id=f"sha256:{mapping_v2._HASH}",
        content_sha256=mapping_v2._HASH,
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
    candidate = build_long_candidate(outcome, mapping_v2._bindings(persisted.template))
    persisted_long = LongPersistenceService(database, clock=_clock).persist(
        LongPersistenceRequest(
            outcome=outcome,
            candidate=candidate,
            loader_version="data-review-long-v1",
            scan_contract_version="data-review-scan-v1",
        )
    )
    assert persisted_long.counts.result_count == 1
    assert persisted_long.counts.measurement_count == 2
    with database.session() as session:
        result = session.scalar(select(LongInspectionResultRow))
        lot = session.scalar(select(OqcLotRow))
    assert result is not None
    assert lot is not None
    return result.id, lot.id


def _create_master_configuration(
    database: Database,
    *,
    disposition: InspectionItemDisposition = InspectionItemDisposition.MANAGED,
    lsl: Decimal | None = Decimal("1.90"),
    usl: Decimal | None = Decimal("2.10"),
    unit: str = "mm",
) -> PersistedMasterSpecRevision:
    commands = MasterConfigCommandService(database, clock=_clock)
    commands.create_model(
        CreateCanonicalModelCommand(
            model=CanonicalModel(_PROJECT, _MODEL_KEY, "Synthetic model"),
            actor=_ADMIN,
            reason="Register synthetic review model.",
        )
    )
    commands.create_supplier(
        CreateCanonicalSupplierCommand(
            supplier=CanonicalSupplier(_PROJECT, _SUPPLIER_KEY, "Synthetic supplier"),
            actor=_ADMIN,
            reason="Register synthetic review supplier.",
        )
    )
    commands.create_model_part(
        CreateCanonicalModelPartCommand(
            model_part=CanonicalModelPart(
                _PROJECT,
                _MODEL_KEY,
                _PART_KEY,
                "Synthetic part",
            ),
            actor=_ADMIN,
            reason="Register synthetic review part.",
        )
    )
    created_item = commands.create_inspection_item(
        CreateCanonicalInspectionItemCommand(
            item=CanonicalInspectionItem(
                _PROJECT,
                _PART_KEY,
                _ITEM_KEY,
                "Synthetic width",
                InspectionItemDisposition.CANDIDATE,
            ),
            actor=_ADMIN,
            reason="Register explicit synthetic item disposition.",
        )
    )
    if disposition != InspectionItemDisposition.CANDIDATE:
        commands.set_item_disposition(
            SetInspectionItemDispositionCommand(
                project_key=_PROJECT,
                item_key=_ITEM_KEY,
                disposition=disposition,
                expected_row_version=created_item.row_version,
                actor=_ADMIN,
                reason="Record the explicit synthetic item disposition.",
            )
        )
    draft = commands.create_master_spec_revision(
        CreateMasterSpecRevisionCommand(
            spec=MasterSpecRevision(
                project_key=_PROJECT,
                canonical_item_key=_ITEM_KEY,
                revision=1,
                status=ConfigurationRevisionStatus.DRAFT,
                target=Decimal("2.00"),
                lsl=lsl,
                usl=usl,
                unit=unit,
                external_spec_revision="SYNTHETIC-R1",
                effective_from=date(2026, 1, 1),
                effective_to=date(2026, 12, 31),
                change_reason="Synthetic framework verification only.",
                source_reference="synthetic-master-evidence",
            ),
            expected_history_row_version=0,
            actor=_REVIEWER,
            reason="Create synthetic Master evidence.",
        )
    )
    reviewed = commands.review_master_spec_revision(
        ReviewMasterSpecRevisionCommand(
            project_key=_PROJECT,
            canonical_item_key=_ITEM_KEY,
            revision=1,
            expected_history_row_version=draft.history_row_version,
            expected_revision_row_version=draft.revision_row_version,
            actor=_REVIEWER,
            reason="Review synthetic Master evidence.",
        )
    )
    return commands.approve_master_spec_revision(
        ApproveMasterSpecRevisionCommand(
            project_key=_PROJECT,
            canonical_item_key=_ITEM_KEY,
            revision=1,
            expected_history_row_version=reviewed.history_row_version,
            expected_revision_row_version=reviewed.revision_row_version,
            actor=_ADMIN,
            reason="Approve synthetic Master evidence for framework testing.",
        )
    )


def _fixture(
    tmp_path: Path,
    *,
    fail_sample: bool = False,
    disposition: InspectionItemDisposition = InspectionItemDisposition.MANAGED,
    unit: str = "mm",
    source_unit: str = "mm",
) -> _ReviewFixture:
    tmp_path.mkdir(parents=True, exist_ok=True)
    database_path = tmp_path / "data-status-review.sqlite3"
    database = mapping_v2._database(database_path)
    result_id, lot_id = _long_outcome(
        database,
        fail_sample=fail_sample,
        source_unit=source_unit,
    )
    master = _create_master_configuration(database, disposition=disposition, unit=unit)
    return _ReviewFixture(database, database_path, result_id, lot_id, master)


def _command(
    candidate,
    *,
    target: LongDataStatus,
    command_id: str = "data-review-command-1",
    actor: Actor = _ADMIN,
) -> DecideDataStatusCommand:
    selected = candidate.selected_master
    expected_master = (
        ExpectedMasterVersion(
            history_id=selected.history_id,
            revision_id=selected.revision_id,
            history_row_version=selected.history_row_version,
            revision_row_version=selected.revision_row_version,
            payload_sha256=selected.payload_sha256,
        )
        if selected is not None
        else None
    )
    return DecideDataStatusCommand(
        project_key=candidate.basis.project_key,
        result_id=candidate.basis.result_id,
        command_id=command_id,
        target_status=target,
        expected_candidate_sha256=candidate.candidate_sha256,
        expected_result_row_version=candidate.basis.result_row_version,
        expected_measurement_versions=tuple(
            ExpectedMeasurementVersion(
                sample_ordinal=value.sample_ordinal,
                measurement_id=value.measurement_id,
                row_version=value.row_version,
            )
            for value in candidate.basis.measurements
        ),
        expected_item_row_version=candidate.basis.item_row_version,
        expected_master=expected_master,
        actor=actor,
        reason="Apply one explicit synthetic data-status decision.",
    )


@pytest.mark.required_test_id("DQ-P1-DSTAT-001")
def test_candidate_is_deterministic_read_only_and_uses_exact_approved_master(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    service = DataStatusReviewService(fixture.database, clock=_clock)
    first = service.candidate(project_key=_PROJECT, result_id=fixture.result_id)
    second = service.candidate(project_key=_PROJECT, result_id=fixture.result_id)

    assert first == second
    assert first.candidate_sha256 == second.candidate_sha256
    assert first.state == ReviewCandidateState.EVALUATED
    assert first.proposed_system_judgment == SystemJudgment.PASS
    assert first.selected_master is not None
    assert first.selected_master.history_id == fixture.master.history_id
    assert first.selected_master.revision_id == fixture.master.revision_id
    assert first.selected_master.payload_sha256 == fixture.master.payload_sha256
    assert [sample.comparison.value for sample in first.samples] == [
        "WITHIN_LIMITS",
        "WITHIN_LIMITS",
    ]
    assert not first.official_values_created
    assert not first.unit_conversion_performed
    assert not first.ai_used
    assert not first.statistics_calculated

    with fixture.database.session() as session:
        result = session.get(LongInspectionResultRow, fixture.result_id)
        statuses = session.scalars(
            select(LongMeasurementRow.data_status).order_by(LongMeasurementRow.sample_ordinal)
        ).all()
        transition_count = session.scalar(select(func.count()).select_from(DataStatusTransitionRow))
    assert result is not None
    assert result.data_status == "PENDING"
    assert result.system_judgment is None
    assert result.system_judgment_status == "NOT_EVALUATED"
    assert result.spec_evaluation_status == "NOT_EVALUATED"
    assert statuses == ["PENDING", "PENDING"]
    assert transition_count == 0


@pytest.mark.required_test_id("DQ-P1-DSTAT-002")
def test_candidate_does_not_promote_convert_standardize_or_use_supplier_spec_fields(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    original = DataStatusReviewService(fixture.database, clock=_clock).candidate(
        project_key=_PROJECT,
        result_id=fixture.result_id,
    )
    assert original.state == ReviewCandidateState.EVALUATED
    assert original.proposed_system_judgment == SystemJudgment.PASS
    with fixture.database.session() as session, session.begin():
        result = session.get(LongInspectionResultRow, fixture.result_id)
        assert result is not None
        source = dict(result.source_evidence)
        for key in ("specification", "tolerance", "minimum", "maximum", "target", "lsl", "usl"):
            source[key] = {"untrusted_supplier_value": "MUST-NOT-BE-EVALUATED"}
        result.source_evidence = source
        result.source_evidence_sha256 = mapping_v2.canonical_json_sha256(source)
    candidate = DataStatusReviewService(fixture.database, clock=_clock).candidate(
        project_key=_PROJECT,
        result_id=fixture.result_id,
    )
    # The signed Long snapshot detects source evidence edits; supplier limits are not
    # silently accepted as an alternative evaluation basis.
    assert candidate.state == ReviewCandidateState.INELIGIBLE
    assert ReviewIssueCode.CANDIDATE_EVIDENCE_INTEGRITY in {
        issue.code for issue in candidate.issues
    }
    assert not candidate.official_values_created
    assert not candidate.unit_conversion_performed
    assert not candidate.ai_used
    assert not candidate.statistics_calculated
    with fixture.database.session() as session:
        measurements = session.scalars(select(LongMeasurementRow)).all()
    assert all(value.standardized_value is None for value in measurements)
    assert all(value.unit_conversion_status == "NOT_CONFIGURED" for value in measurements)
    assert all(value.data_status == "PENDING" for value in measurements)


@pytest.mark.required_test_id("DQ-P1-DSTAT-003")
def test_admin_can_explicitly_mark_fail_candidate_valid_atomically_without_touching_lot(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, fail_sample=True)
    service = DataStatusReviewService(fixture.database, clock=_clock)
    candidate = service.candidate(project_key=_PROJECT, result_id=fixture.result_id)
    assert candidate.state == ReviewCandidateState.EVALUATED
    assert candidate.proposed_system_judgment == SystemJudgment.FAIL
    # IDs are intentionally made lexically opposite to sample order; concurrency
    # expectations are ordered by sample ordinal, never UUID lexical coincidence.
    with fixture.database.session() as session, session.begin():
        measurements = session.scalars(
            select(LongMeasurementRow).order_by(LongMeasurementRow.sample_ordinal)
        ).all()
        measurements[0].id = "zzzzzzzz-zzzz-zzzz-zzzz-zzzzzzzzzzzz"
        measurements[1].id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

    candidate = service.candidate(project_key=_PROJECT, result_id=fixture.result_id)
    with fixture.database.session() as session:
        lot_before = session.get(OqcLotRow, fixture.lot_id)
        assert lot_before is not None
        lot_snapshot = (
            lot_before.data_status,
            lot_before.row_version,
            lot_before.identifier_evidence_sha256,
        )
    decision = service.decide(_command(candidate, target=LongDataStatus.VALID))

    assert decision.target_status == LongDataStatus.VALID
    assert decision.system_judgment == SystemJudgment.FAIL
    assert decision.evaluation_mode == ReviewCandidateState.EVALUATED
    assert decision.measurement_count == 2
    with fixture.database.session() as session:
        result = session.get(LongInspectionResultRow, fixture.result_id)
        lot_after = session.get(OqcLotRow, fixture.lot_id)
        measurements = session.scalars(
            select(LongMeasurementRow).order_by(LongMeasurementRow.sample_ordinal)
        ).all()
        transitions = session.scalars(select(DataStatusTransitionRow)).all()
    assert result is not None
    assert lot_after is not None
    assert result.data_status == "VALID"
    assert result.system_judgment == "FAIL"
    assert result.system_judgment_status == "EVALUATED"
    assert result.spec_evaluation_status == SpecEvaluationStatus.EVALUATED_APPROVED_MASTER.value
    assert [value.data_status for value in measurements] == ["VALID", "VALID"]
    assert [value.row_version for value in measurements] == [2, 2]
    assert (
        lot_after.data_status,
        lot_after.row_version,
        lot_after.identifier_evidence_sha256,
    ) == lot_snapshot
    assert len(transitions) == 1
    with fixture.database.session() as session:
        selected = DataReviewRepository().select_valid_measurements(
            session,
            project_key=_PROJECT,
            canonical_item_key=_ITEM_KEY,
        )
    assert [value.sample_ordinal for value in selected] == [1, 2]
    with fixture.database.session() as session, session.begin():
        first_measurement = session.scalar(
            select(LongMeasurementRow).order_by(LongMeasurementRow.sample_ordinal)
        )
        assert first_measurement is not None
        first_measurement.data_status = "SUSPECT"
        first_measurement.row_version += 1
    with fixture.database.session() as session:
        result_valid_only = DataReviewRepository().select_valid_measurements(
            session,
            project_key=_PROJECT,
            canonical_item_key=_ITEM_KEY,
        )
    assert [value.sample_ordinal for value in result_valid_only] == [2]


@pytest.mark.required_test_id("DQ-P1-DSTAT-004")
def test_only_admin_can_decide_and_unauthorized_attempt_is_zero_mutation(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    service = DataStatusReviewService(fixture.database, clock=_clock)
    candidate = service.candidate(project_key=_PROJECT, result_id=fixture.result_id)
    with fixture.database.session() as session:
        audit_before = session.scalar(select(func.count()).select_from(AuditLog))

    with pytest.raises(DataReviewAuthorizationError, match="ADMIN"):
        service.decide(_command(candidate, target=LongDataStatus.VALID, actor=_VIEWER))

    with fixture.database.session() as session:
        result = session.get(LongInspectionResultRow, fixture.result_id)
        measurements = session.scalars(select(LongMeasurementRow)).all()
        audit_after = session.scalar(select(func.count()).select_from(AuditLog))
        transition_count = session.scalar(select(func.count()).select_from(DataStatusTransitionRow))
    assert result is not None
    assert result.data_status == "PENDING"
    assert result.row_version == 1
    assert all(value.data_status == "PENDING" and value.row_version == 1 for value in measurements)
    assert audit_after == audit_before
    assert transition_count == 0


@pytest.mark.required_test_id("DQ-P1-DSTAT-005")
def test_unit_mismatch_is_review_only_and_never_creates_a_master_judgment(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path, source_unit=" mm ")
    service = DataStatusReviewService(fixture.database, clock=_clock)
    candidate = service.candidate(project_key=_PROJECT, result_id=fixture.result_id)

    assert candidate.state == ReviewCandidateState.REVIEW_ONLY
    assert candidate.basis.source_unit is not None
    assert candidate.basis.source_unit.raw_value == " mm "
    assert ReviewIssueCode.UNIT_MISMATCH in {issue.code for issue in candidate.issues}
    assert candidate.allowed_target_statuses == (
        LongDataStatus.EXCLUDED,
        LongDataStatus.SUSPECT,
    )
    with pytest.raises(IneligibleDataReviewCandidateError, match="not allowed"):
        service.decide(_command(candidate, target=LongDataStatus.VALID))

    decision = service.decide(
        _command(
            candidate,
            target=LongDataStatus.SUSPECT,
            command_id="review-only-suspect",
        )
    )
    assert decision.evaluation_mode == ReviewCandidateState.REVIEW_ONLY
    assert decision.system_judgment is None
    assert decision.master is None
    with fixture.database.session() as session:
        result = session.get(LongInspectionResultRow, fixture.result_id)
        measurements = session.scalars(select(LongMeasurementRow)).all()
        selected = DataReviewRepository().select_valid_measurements(
            session,
            project_key=_PROJECT,
        )
    assert result is not None
    assert result.data_status == "SUSPECT"
    assert result.system_judgment is None
    assert result.system_judgment_status == "NOT_EVALUATED"
    assert result.spec_evaluation_status == "NOT_EVALUATED"
    assert result.applied_master_revision_id is None
    assert all(value.data_status == "SUSPECT" for value in measurements)
    assert all(value.standardized_value is None for value in measurements)
    assert selected == ()
    with fixture.database.session() as session, session.begin():
        first_measurement = session.scalar(
            select(LongMeasurementRow).order_by(LongMeasurementRow.sample_ordinal)
        )
        assert first_measurement is not None
        first_measurement.data_status = "VALID"
    with fixture.database.session() as session:
        result_status_gate = DataReviewRepository().select_valid_measurements(
            session,
            project_key=_PROJECT,
        )
    assert result_status_gate == ()


@pytest.mark.required_test_id("DQ-P1-DSTAT-006")
def test_held_candidate_missing_item_and_signed_together_tampering_fail_closed(
    tmp_path: Path,
) -> None:
    held = _fixture(tmp_path / "held")
    with held.database.session() as session, session.begin():
        result = session.get(LongInspectionResultRow, held.result_id)
        assert result is not None
        result.data_status = "HELD"
        for measurement in session.scalars(select(LongMeasurementRow)).all():
            measurement.data_status = "HELD"
    held_candidate = DataStatusReviewService(held.database, clock=_clock).candidate(
        project_key=_PROJECT,
        result_id=held.result_id,
    )
    assert held_candidate.state == ReviewCandidateState.INELIGIBLE
    assert ReviewIssueCode.RESULT_HELD in {issue.code for issue in held_candidate.issues}
    with pytest.raises(IneligibleDataReviewCandidateError):
        DataStatusReviewService(held.database, clock=_clock).decide(
            _command(held_candidate, target=LongDataStatus.EXCLUDED)
        )

    candidate_item = _fixture(tmp_path / "candidate-item")
    with candidate_item.database.session() as session, session.begin():
        item = session.scalar(
            select(CanonicalInspectionItemRow).where(
                CanonicalInspectionItemRow.item_key == _ITEM_KEY
            )
        )
        assert item is not None
        item.disposition = "CANDIDATE"
        item.row_version += 1
    blocked_candidate = DataStatusReviewService(candidate_item.database, clock=_clock).candidate(
        project_key=_PROJECT,
        result_id=candidate_item.result_id,
    )
    assert blocked_candidate.state == ReviewCandidateState.INELIGIBLE
    assert ReviewIssueCode.ITEM_CANDIDATE in {issue.code for issue in blocked_candidate.issues}

    missing_master = _fixture(tmp_path / "missing-master")
    with missing_master.database.session() as session, session.begin():
        session.execute(text("DELETE FROM master_spec_revisions"))
        session.execute(text("DELETE FROM master_spec_histories"))
    no_master_candidate = DataStatusReviewService(
        missing_master.database,
        clock=_clock,
    ).candidate(project_key=_PROJECT, result_id=missing_master.result_id)
    assert no_master_candidate.state == ReviewCandidateState.REVIEW_ONLY
    assert ReviewIssueCode.MASTER_NOT_FOUND in {issue.code for issue in no_master_candidate.issues}
    assert LongDataStatus.VALID not in no_master_candidate.allowed_target_statuses

    ambiguous_master = _fixture(tmp_path / "ambiguous-master")
    overlapping_spec = MasterSpecRevision(
        project_key=_PROJECT,
        canonical_item_key=_ITEM_KEY,
        revision=2,
        status=ConfigurationRevisionStatus.APPROVED,
        target=Decimal("2.00"),
        lsl=Decimal("1.80"),
        usl=Decimal("2.20"),
        unit="mm",
        external_spec_revision="SYNTHETIC-OVERLAP",
        effective_from=date(2026, 1, 1),
        effective_to=date(2026, 12, 31),
        change_reason="Deliberate overlapping fixture for fail-closed selection.",
        source_reference="synthetic-overlap",
        reviewed_by=_REVIEWER.actor_id,
        reviewed_at=_NOW,
        approved_by=_ADMIN.actor_id,
        approved_at=_NOW,
    )
    overlapping_payload = _serialize_master_spec(overlapping_spec)
    with ambiguous_master.database.session() as session, session.begin():
        session.add(
            MasterSpecRevisionRow(
                id="overlapping-master-revision",
                project_key=_PROJECT,
                history_id=ambiguous_master.master.history_id,
                revision_number=2,
                status="APPROVED",
                spec_payload=overlapping_payload,
                payload_sha256=_payload_digest(overlapping_payload),
                declared_effective_from=overlapping_spec.effective_from,
                declared_effective_to=overlapping_spec.effective_to,
                resolved_effective_to=None,
                reviewed_by=_REVIEWER.actor_id,
                reviewed_at=_NOW,
                approved_by=_ADMIN.actor_id,
                approved_at=_NOW,
                row_version=1,
                created_at=_NOW,
            )
        )
    ambiguous_candidate = DataStatusReviewService(
        ambiguous_master.database,
        clock=_clock,
    ).candidate(project_key=_PROJECT, result_id=ambiguous_master.result_id)
    assert ambiguous_candidate.state == ReviewCandidateState.INELIGIBLE
    assert len(ambiguous_candidate.basis.masters) == 2
    assert ReviewIssueCode.MASTER_AMBIGUOUS in {issue.code for issue in ambiguous_candidate.issues}

    missing_item = _fixture(tmp_path / "missing-item")
    with missing_item.database.session() as session, session.begin():
        session.execute(text("DELETE FROM master_spec_revisions"))
        session.execute(text("DELETE FROM master_spec_histories"))
        session.execute(text("DELETE FROM canonical_inspection_items"))
    missing_candidate = DataStatusReviewService(missing_item.database, clock=_clock).candidate(
        project_key=_PROJECT,
        result_id=missing_item.result_id,
    )
    assert missing_candidate.state == ReviewCandidateState.INELIGIBLE
    assert missing_candidate.basis.canonical_item_key == _ITEM_KEY
    assert missing_candidate.basis.item_disposition is None
    assert ReviewIssueCode.ITEM_NOT_MAPPED in {issue.code for issue in missing_candidate.issues}

    unit_tamper = _fixture(tmp_path / "unit-tamper")
    with unit_tamper.database.session() as session, session.begin():
        result = session.get(LongInspectionResultRow, unit_tamper.result_id)
        assert result is not None
        source = dict(result.source_evidence)
        unit = dict(source["unit"])
        unit["raw_value"] = {"kind": "str", "value": "cm"}
        source["unit"] = unit
        result.source_evidence = source
        result.source_evidence_sha256 = mapping_v2.canonical_json_sha256(source)
    unit_candidate = DataStatusReviewService(unit_tamper.database, clock=_clock).candidate(
        project_key=_PROJECT,
        result_id=unit_tamper.result_id,
    )
    assert unit_candidate.state == ReviewCandidateState.INELIGIBLE
    assert ReviewIssueCode.CANDIDATE_EVIDENCE_INTEGRITY in {
        issue.code for issue in unit_candidate.issues
    }

    measurement_tamper = _fixture(tmp_path / "measurement-tamper")
    with measurement_tamper.database.session() as session, session.begin():
        measurement = session.scalar(
            select(LongMeasurementRow).order_by(LongMeasurementRow.sample_ordinal)
        )
        assert measurement is not None
        evidence = dict(measurement.evidence)
        evidence["raw_value"] = {"kind": "decimal", "value": "9.99"}
        measurement.evidence = evidence
        measurement.evidence_sha256 = mapping_v2.canonical_json_sha256(evidence)
        measurement.raw_value_tag = "decimal"
        measurement.raw_value_text = '{"kind":"decimal","value":"9.99"}'
        measurement.raw_numeric_value = measurement.raw_value_text
    measurement_candidate = DataStatusReviewService(
        measurement_tamper.database,
        clock=_clock,
    ).candidate(project_key=_PROJECT, result_id=measurement_tamper.result_id)
    assert measurement_candidate.state == ReviewCandidateState.INELIGIBLE
    assert ReviewIssueCode.CANDIDATE_EVIDENCE_INTEGRITY in {
        issue.code for issue in measurement_candidate.issues
    }

    binding_tamper = _fixture(tmp_path / "binding-tamper")
    with binding_tamper.database.session() as session, session.begin():
        result = session.get(LongInspectionResultRow, binding_tamper.result_id)
        assert result is not None
        binding = dict(result.binding_snapshot or {})
        binding["canonical_item_key"] = "forged-item"
        result.binding_snapshot = binding
        result.binding_snapshot_sha256 = mapping_v2.canonical_json_sha256(binding)
        result.canonical_item_key = "forged-item"
    binding_candidate = DataStatusReviewService(binding_tamper.database, clock=_clock).candidate(
        project_key=_PROJECT,
        result_id=binding_tamper.result_id,
    )
    assert binding_candidate.state == ReviewCandidateState.INELIGIBLE
    assert {
        ReviewIssueCode.CANDIDATE_EVIDENCE_INTEGRITY,
        ReviewIssueCode.ITEM_NOT_MAPPED,
    }.issubset({issue.code for issue in binding_candidate.issues})

    isolated = _fixture(tmp_path / "same-lot-isolation")
    with isolated.database.session() as session, session.begin():
        original = session.get(LongInspectionResultRow, isolated.result_id)
        job = session.scalar(select(LongIngestionJobRow))
        original_measurements = session.scalars(
            select(LongMeasurementRow).order_by(LongMeasurementRow.sample_ordinal)
        ).all()
        assert original is not None and job is not None
        snapshot = deepcopy(job.candidate_snapshot)
        rows = snapshot["rows"]
        assert isinstance(rows, list) and len(rows) == 1
        held_row = deepcopy(rows[0])
        assert isinstance(held_row, dict)
        held_row["row_key"] = "row-held-neighbor"
        held_row["state"] = "ROW_HELD"
        held_row["data_status"] = "HELD"
        held_row["issues"] = [{"code": "SYNTHETIC_NEIGHBOR_HOLD"}]
        held_binding = deepcopy(held_row["binding"])
        assert isinstance(held_binding, dict)
        held_key = held_binding["key"]
        assert isinstance(held_key, dict)
        held_key["row_key"] = "row-held-neighbor"
        held_binding["canonical_item_key"] = "canonical:item:held-neighbor"
        held_row["binding"] = held_binding
        rows.append(held_row)
        snapshot_digest = mapping_v2.canonical_json_sha256(snapshot)
        job.candidate_snapshot = snapshot
        job.candidate_snapshot_sha256 = snapshot_digest
        job.result_count = 2
        job.measurement_count = 4
        job.held_result_count = 1
        original.candidate_snapshot_sha256 = snapshot_digest
        held_result = LongInspectionResultRow(
            id="held-neighbor-result",
            project_key=_PROJECT,
            oqc_lot_id=isolated.lot_id,
            source_file_id=original.source_file_id,
            source_sheet_id=original.source_sheet_id,
            source_row_key="row-held-neighbor",
            binding_revision=original.binding_revision,
            canonical_model_part_key=original.canonical_model_part_key,
            canonical_item_key="canonical:item:held-neighbor",
            supplier_judgment_text=original.supplier_judgment_text,
            system_judgment=None,
            system_judgment_status="NOT_EVALUATED",
            spec_evaluation_status="NOT_EVALUATED",
            source_evidence=deepcopy(original.source_evidence),
            source_evidence_sha256=original.source_evidence_sha256,
            binding_snapshot=held_binding,
            binding_snapshot_sha256=mapping_v2.canonical_json_sha256(held_binding),
            candidate_snapshot_sha256=snapshot_digest,
            data_status="HELD",
            hold_reasons=deepcopy(held_row["issues"]),
            row_version=1,
        )
        session.add(held_result)
        session.flush()
        for index, measurement in enumerate(original_measurements, start=1):
            session.add(
                LongMeasurementRow(
                    id=f"held-neighbor-measurement-{index}",
                    project_key=_PROJECT,
                    inspection_result_id=held_result.id,
                    source_file_id=measurement.source_file_id,
                    source_sheet_id=measurement.source_sheet_id,
                    sample_ordinal=measurement.sample_ordinal,
                    source_cell=measurement.source_cell,
                    raw_value_tag=measurement.raw_value_tag,
                    raw_value_text=measurement.raw_value_text,
                    raw_numeric_value=measurement.raw_numeric_value,
                    raw_qualitative_value=measurement.raw_qualitative_value,
                    evidence=deepcopy(measurement.evidence),
                    evidence_sha256=measurement.evidence_sha256,
                    formula_flag=measurement.formula_flag,
                    standardized_value=None,
                    unit_conversion_status="NOT_CONFIGURED",
                    data_status="HELD",
                    hold_reasons=deepcopy(held_row["issues"]),
                    superseded_measurement_id=None,
                    row_version=1,
                )
            )
    isolation_service = DataStatusReviewService(isolated.database, clock=_clock)
    isolated_candidate = isolation_service.candidate(
        project_key=_PROJECT,
        result_id=isolated.result_id,
    )
    assert isolated_candidate.state == ReviewCandidateState.EVALUATED
    isolation_service.decide(
        _command(
            isolated_candidate,
            target=LongDataStatus.VALID,
            command_id="isolated-valid-decision",
        )
    )
    with isolated.database.session() as session:
        neighbor = session.get(LongInspectionResultRow, "held-neighbor-result")
        lot = session.get(OqcLotRow, isolated.lot_id)
    assert neighbor is not None and neighbor.data_status == "HELD"
    assert lot is not None and lot.data_status == "PENDING"


@pytest.mark.required_test_id("DQ-P1-DSTAT-007")
def test_decision_preserves_historical_master_identity_and_effectivity_snapshot(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    service = DataStatusReviewService(fixture.database, clock=_clock)
    candidate = service.candidate(project_key=_PROJECT, result_id=fixture.result_id)
    decision = service.decide(_command(candidate, target=LongDataStatus.VALID))
    assert decision.master is not None
    with fixture.database.session() as session:
        transition_before = session.get(DataStatusTransitionRow, decision.transition_id)
        assert transition_before is not None
        historical_snapshot = (
            transition_before.applied_master_history_id,
            transition_before.applied_master_revision_id,
            transition_before.applied_master_payload_sha256,
            transition_before.applied_master_declared_effective_from,
            transition_before.applied_master_declared_effective_to,
            transition_before.applied_master_resolved_effective_to,
            transition_before.candidate_snapshot,
            transition_before.decision_snapshot,
        )

    commands = MasterConfigCommandService(fixture.database, clock=_clock)
    draft = commands.create_master_spec_revision(
        CreateMasterSpecRevisionCommand(
            spec=MasterSpecRevision(
                project_key=_PROJECT,
                canonical_item_key=_ITEM_KEY,
                revision=2,
                status=ConfigurationRevisionStatus.DRAFT,
                target=Decimal("2.00"),
                lsl=Decimal("1.95"),
                usl=Decimal("2.05"),
                unit="mm",
                external_spec_revision="SYNTHETIC-R2",
                effective_from=date(2026, 7, 1),
                effective_to=date(2026, 12, 31),
                change_reason="Synthetic later revision.",
                source_reference="synthetic-master-evidence-r2",
            ),
            expected_history_row_version=fixture.master.history_row_version,
            actor=_REVIEWER,
            reason="Create later synthetic Master revision.",
        )
    )
    reviewed = commands.review_master_spec_revision(
        ReviewMasterSpecRevisionCommand(
            project_key=_PROJECT,
            canonical_item_key=_ITEM_KEY,
            revision=2,
            expected_history_row_version=draft.history_row_version,
            expected_revision_row_version=draft.revision_row_version,
            actor=_REVIEWER,
            reason="Review later synthetic Master revision.",
        )
    )
    commands.supersede_master_spec_revision(
        SupersedeMasterSpecRevisionCommand(
            project_key=_PROJECT,
            canonical_item_key=_ITEM_KEY,
            predecessor_revision=1,
            successor_revision=2,
            expected_history_row_version=reviewed.history_row_version,
            expected_predecessor_row_version=fixture.master.revision_row_version,
            expected_successor_row_version=reviewed.revision_row_version,
            actor=_ADMIN,
            reason="Resolve later synthetic Master effectivity.",
        )
    )
    with fixture.database.session() as session:
        transition_after = session.get(DataStatusTransitionRow, decision.transition_id)
        predecessor = session.get(MasterSpecRevisionRow, fixture.master.revision_id)
    assert transition_after is not None
    assert predecessor is not None
    assert predecessor.resolved_effective_to == date(2026, 6, 30)
    assert (
        transition_after.applied_master_history_id,
        transition_after.applied_master_revision_id,
        transition_after.applied_master_payload_sha256,
        transition_after.applied_master_declared_effective_from,
        transition_after.applied_master_declared_effective_to,
        transition_after.applied_master_resolved_effective_to,
        transition_after.candidate_snapshot,
        transition_after.decision_snapshot,
    ) == historical_snapshot


@pytest.mark.required_test_id("DQ-P1-DSTAT-008")
def test_cas_master_race_and_non_utc_restart_replay_are_idempotent(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    initial = DataStatusReviewService(fixture.database, clock=_clock).candidate(
        project_key=_PROJECT,
        result_id=fixture.result_id,
    )
    base_command = _command(initial, target=LongDataStatus.VALID, command_id="restart-command")
    stale_result_command = replace(base_command, expected_result_row_version=2)
    with pytest.raises(StaleDataReviewCandidateError):
        DataStatusReviewService(fixture.database, clock=_clock).decide(stale_result_command)

    with fixture.database.session() as session, session.begin():
        first_measurement = session.scalar(
            select(LongMeasurementRow).order_by(LongMeasurementRow.sample_ordinal)
        )
        history = session.get(MasterSpecHistoryRow, fixture.master.history_id)
        revision = session.get(MasterSpecRevisionRow, fixture.master.revision_id)
        assert first_measurement is not None and history is not None and revision is not None
        first_measurement.row_version += 1
        history.row_version += 1
        revision.row_version += 1
    with pytest.raises(StaleDataReviewCandidateError):
        DataStatusReviewService(fixture.database, clock=_clock).decide(base_command)

    current = DataStatusReviewService(fixture.database, clock=_clock).candidate(
        project_key=_PROJECT,
        result_id=fixture.result_id,
    )

    def kst_clock() -> datetime:
        return datetime(2026, 8, 15, 20, 0, tzinfo=timezone(timedelta(hours=9)))

    service = DataStatusReviewService(fixture.database, clock=kst_clock)
    command = _command(current, target=LongDataStatus.VALID, command_id="restart-command")
    first = service.decide(command)
    assert not first.replayed
    database_url = str(fixture.database.engine.url)
    fixture.database.dispose()

    restarted = Database(database_url)
    try:
        replay = DataStatusReviewService(restarted, clock=kst_clock).decide(command)
        assert replay.replayed
        assert replay.transition_id == first.transition_id
        assert replay.candidate_sha256 == first.candidate_sha256
        with pytest.raises(DataReviewCommandConflictError):
            DataStatusReviewService(restarted, clock=kst_clock).decide(
                replace(command, target_status=LongDataStatus.SUSPECT)
            )
        with restarted.session() as session:
            transition = session.get(DataStatusTransitionRow, first.transition_id)
            counts = (
                session.scalar(select(func.count()).select_from(DataStatusTransitionRow)),
                session.scalar(
                    select(func.count()).where(AuditLog.action == "DATA_STATUS_DECIDED")
                ),
            )
        assert transition is not None
        assert transition.decided_at == datetime(2026, 8, 15, 11, 0, tzinfo=UTC)
        assert transition.decision_snapshot["decided_at"] == "2026-08-15T11:00:00+00:00"
        assert counts == (1, 1)

        with restarted.session() as session, session.begin():
            transition = session.get(DataStatusTransitionRow, first.transition_id)
            result = session.get(LongInspectionResultRow, fixture.result_id)
            measurement = session.scalar(
                select(LongMeasurementRow).order_by(LongMeasurementRow.sample_ordinal)
            )
            audit = session.scalar(select(AuditLog).where(AuditLog.action == "DATA_STATUS_DECIDED"))
            assert transition is not None and result is not None
            assert measurement is not None and audit is not None
            evidence = deepcopy(measurement.evidence)
            new_raw = {"kind": "decimal", "value": "2.01"}
            evidence["raw_value"] = new_raw
            evidence_sha256 = mapping_v2.canonical_json_sha256(evidence)
            raw_json = json.dumps(new_raw, sort_keys=True, separators=(",", ":"))
            measurement.evidence = evidence
            measurement.evidence_sha256 = evidence_sha256
            measurement.raw_value_text = raw_json
            measurement.raw_numeric_value = raw_json

            candidate_snapshot = deepcopy(transition.candidate_snapshot)
            samples = candidate_snapshot["samples"]
            assert isinstance(samples, list) and isinstance(samples[0], dict)
            samples[0]["evidence_sha256"] = evidence_sha256
            samples[0]["raw_value_json"] = raw_json
            samples[0]["raw_numeric_value_json"] = raw_json
            samples[0]["numeric_value"] = "2.01"
            candidate_sha256 = mapping_v2.canonical_json_sha256(candidate_snapshot)
            decision_snapshot = deepcopy(transition.decision_snapshot)
            decision_snapshot["candidate"] = candidate_snapshot
            decision_snapshot["candidate_sha256"] = candidate_sha256
            transition.candidate_snapshot = candidate_snapshot
            transition.candidate_sha256 = candidate_sha256
            transition.decision_snapshot = decision_snapshot
            transition.decision_snapshot_sha256 = mapping_v2.canonical_json_sha256(
                decision_snapshot
            )
            result.current_decision_candidate_sha256 = candidate_sha256
            audit_before_state = dict(audit.before_state or {})
            audit_before_state["candidate_sha256"] = candidate_sha256
            audit.before_state = audit_before_state
        with pytest.raises(DataReviewPersistenceError, match="immutable Long"):
            DataStatusReviewService(restarted, clock=kst_clock).decide(command)
    finally:
        restarted.dispose()


class _FailingAuditRepository(AuditRepository):
    def append(self, session, change: AuditChange):
        raise RuntimeError("synthetic data-review Audit failure")


@pytest.mark.required_test_id("DQ-P1-DSTAT-009")
def test_transition_result_measurements_and_audit_commit_or_roll_back_together(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    candidate = DataStatusReviewService(fixture.database, clock=_clock).candidate(
        project_key=_PROJECT,
        result_id=fixture.result_id,
    )
    command = _command(candidate, target=LongDataStatus.VALID, command_id="atomic-command")
    with fixture.database.session() as session:
        audit_before = session.scalar(select(func.count()).select_from(AuditLog))
    failing = DataStatusReviewService(
        fixture.database,
        audit_repository=_FailingAuditRepository(),
        clock=_clock,
    )
    with pytest.raises(RuntimeError, match="Audit failure"):
        failing.decide(command)

    with fixture.database.session() as session:
        result = session.get(LongInspectionResultRow, fixture.result_id)
        measurements = session.scalars(select(LongMeasurementRow)).all()
        transition_count = session.scalar(select(func.count()).select_from(DataStatusTransitionRow))
        audit_after_failure = session.scalar(select(func.count()).select_from(AuditLog))
    assert result is not None
    assert (result.data_status, result.row_version) == ("PENDING", 1)
    assert all((value.data_status, value.row_version) == ("PENDING", 1) for value in measurements)
    assert transition_count == 0
    assert audit_after_failure == audit_before

    decision = DataStatusReviewService(fixture.database, clock=_clock).decide(command)
    with fixture.database.session() as session:
        transition = session.get(DataStatusTransitionRow, decision.transition_id)
        audit = session.scalar(
            select(AuditLog).where(
                AuditLog.action == "DATA_STATUS_DECIDED",
                AuditLog.target_id == f"{_PROJECT}:{fixture.result_id}",
            )
        )
    assert transition is not None
    assert audit is not None
    assert mapping_v2.canonical_json_sha256(transition.candidate_snapshot) == (
        transition.candidate_sha256
    )
    assert mapping_v2.canonical_json_sha256(transition.decision_snapshot) == (
        transition.decision_snapshot_sha256
    )
    assert audit.after_state is not None
    assert audit.after_state["transition_id"] == transition.id
    with fixture.database.session() as session, session.begin():
        stored_audit = session.get(AuditLog, audit.id)
        assert stored_audit is not None
        stored_audit.reason = "coordinated Audit metadata tamper"
    with pytest.raises(DataReviewPersistenceError, match="Audit evidence"):
        DataStatusReviewService(fixture.database, clock=_clock).decide(command)


def _seed_0004_long_state(database_url: str, *, measurement_digest: str) -> None:
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.execute(
                text(
                    "INSERT INTO mapping_template_histories "
                    "(id, project_key, supplier_scope, template_id, row_version, created_at) "
                    "VALUES ('dstat-history', 'dstat-project', 'dstat-supplier-scope', "
                    "'dstat-layout', 3, '2026-08-15 11:00:00')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO mapping_template_revisions "
                    "(id, history_id, revision, schema_version, status, template_payload, "
                    "payload_sha256, declared_effective_from, declared_effective_to, "
                    "resolved_effective_to, reviewed_by, reviewed_at, approved_by, "
                    "approved_at, row_version, created_at) VALUES "
                    "('dstat-revision', 'dstat-history', 1, '1', 'APPROVED', '{}', :digest, "
                    "'2026-01-01', '2026-12-31', NULL, 'reviewer', "
                    "'2026-08-15 10:00:00', 'admin', '2026-08-15 10:30:00', 3, "
                    "'2026-08-15 09:00:00')"
                ),
                {"digest": "1" * 64},
            )
            connection.execute(
                text(
                    "INSERT INTO source_files "
                    "(id, project_key, receipt_id, blob_id, content_sha256, received_at, "
                    "original_filename, model_candidates, lot_candidates, declared_mime_type, "
                    "detected_mime_type, canonical_extension, size_bytes, parse_status, "
                    "scan_source_name, scan_source_size_bytes, scan_sha256_before, "
                    "scan_sha256_after, scan_contract_version, estimated_cells, "
                    "external_link_count, macro_handling, display_value_contract, "
                    "is_golden_workbook_evidence, scan_issues, row_version, created_at) VALUES "
                    "('dstat-source', 'dstat-project', 'dstat-receipt', 'dstat-blob', :digest, "
                    "'2026-08-15 09:00:00', 'synthetic.xlsx', '[\"MODEL\"]', '[\"LOT\"]', "
                    "'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', "
                    "'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', "
                    "'.xlsx', 100, 'SCANNED', 'synthetic.xlsx', 100, :digest, :digest, "
                    "'scan-v1', 10, 0, 'NOT_APPLICABLE', 'RAW_AND_DISPLAY', 0, '[]', 1, "
                    "'2026-08-15 09:00:00')"
                ),
                {"digest": "2" * 64},
            )
            connection.execute(
                text(
                    "INSERT INTO source_sheets "
                    "(id, project_key, source_file_id, position, sheet_name, sheet_kind, "
                    "visibility, used_range, estimated_cells, merged_ranges, hidden_row_ranges, "
                    "hidden_column_ranges, formula_count, protection_metadata, image_metadata, "
                    "issues, scan_snapshot, snapshot_sha256, row_version) VALUES "
                    "('dstat-sheet', 'dstat-project', 'dstat-source', 0, 'OQC', 'WORKSHEET', "
                    "'visible', 'A1:H10', 80, '[]', '[]', '[]', 0, '{}', '[]', '[]', '{}', "
                    ":digest, 1)"
                ),
                {"digest": "3" * 64},
            )
            connection.execute(
                text(
                    "INSERT INTO ingestion_jobs "
                    "(id, project_key, source_file_id, content_sha256, "
                    "mapping_template_revision_id, mapping_payload_sha256, "
                    "binding_catalog_revision, binding_fingerprint, loader_version, "
                    "scan_contract_version, idempotency_key, materialization_fingerprint, "
                    "owns_materialization, reused_job_id, blocking_job_id, status, started_at, "
                    "finished_at, lot_count, result_count, measurement_count, "
                    "held_result_count, error_code, error_summary, issues, candidate_snapshot, "
                    "candidate_snapshot_sha256, row_version) VALUES "
                    "('dstat-job', 'dstat-project', 'dstat-source', :content, "
                    "'dstat-revision', :mapping, 'catalog-v1', :binding, 'loader-v1', "
                    "'scan-v1', :idempotency, :materialization, 1, NULL, NULL, "
                    "'COMPLETED_PENDING', '2026-08-15 09:00:00', "
                    "'2026-08-15 09:01:00', 1, 1, 1, 0, NULL, NULL, '[]', '{}', "
                    ":candidate, 2)"
                ),
                {
                    "content": "2" * 64,
                    "mapping": "1" * 64,
                    "binding": "4" * 64,
                    "idempotency": "5" * 64,
                    "materialization": "6" * 64,
                    "candidate": "7" * 64,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO oqc_lots "
                    "(id, project_key, ingestion_job_id, source_file_id, lot_ordinal, "
                    "canonical_model_key, canonical_model_part_key, canonical_supplier_key, "
                    "source_lot_text, inspection_date, received_at, identifier_evidence, "
                    "identifier_evidence_sha256, data_status, hold_reasons, row_version) VALUES "
                    "('dstat-lot', 'dstat-project', 'dstat-job', 'dstat-source', 1, "
                    "'model', 'part', 'supplier', 'LOT', '2026-08-14', "
                    "'2026-08-15 09:00:00', '[]', :digest, 'PENDING', '[]', 1)"
                ),
                {"digest": "8" * 64},
            )
            connection.execute(
                text(
                    "INSERT INTO inspection_results "
                    "(id, project_key, oqc_lot_id, source_file_id, source_sheet_id, "
                    "source_row_key, binding_revision, canonical_model_part_key, "
                    "canonical_item_key, supplier_judgment_text, system_judgment, "
                    "system_judgment_status, spec_evaluation_status, source_evidence, "
                    "source_evidence_sha256, binding_snapshot, binding_snapshot_sha256, "
                    "candidate_snapshot_sha256, data_status, hold_reasons, row_version) VALUES "
                    "('dstat-result', 'dstat-project', 'dstat-lot', 'dstat-source', "
                    "'dstat-sheet', 'row-1', 1, 'part', 'item', 'supplier-pass', NULL, "
                    "'NOT_EVALUATED', 'NOT_EVALUATED', '{}', :source_digest, NULL, NULL, "
                    ":candidate_digest, 'PENDING', '[]', 1)"
                ),
                {"source_digest": "9" * 64, "candidate_digest": "7" * 64},
            )
            connection.execute(
                text(
                    "INSERT INTO measurements "
                    "(id, project_key, inspection_result_id, source_file_id, source_sheet_id, "
                    "sample_ordinal, source_cell, raw_value_tag, raw_value_text, "
                    "raw_numeric_value, raw_qualitative_value, evidence, evidence_sha256, "
                    "formula_flag, standardized_value, unit_conversion_status, data_status, "
                    "hold_reasons, superseded_measurement_id, row_version) VALUES "
                    "('dstat-measurement', 'dstat-project', 'dstat-result', 'dstat-source', "
                    "'dstat-sheet', 1, 'H8', 'decimal', "
                    '\'{"kind":"decimal","value":"2.00"}\', '
                    '\'{"kind":"decimal","value":"2.00"}\', NULL, '
                    '\'{"raw_value":{"kind":"decimal","value":"2.00"}}\', '
                    ":digest, 0, NULL, 'NOT_CONFIGURED', 'PENDING', '[]', NULL, 1)"
                ),
                {"digest": measurement_digest},
            )
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
    finally:
        engine.dispose()


def _migration_long_snapshot(database_url: str) -> dict[str, tuple[object, ...]]:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            result = connection.execute(
                text(
                    "SELECT id, data_status, system_judgment, system_judgment_status, "
                    "spec_evaluation_status, source_evidence, source_evidence_sha256, "
                    "candidate_snapshot_sha256, row_version FROM inspection_results "
                    "WHERE id='dstat-result'"
                )
            ).one()
            measurement = connection.execute(
                text(
                    "SELECT id, data_status, evidence, evidence_sha256, raw_value_text, "
                    "raw_numeric_value, standardized_value, unit_conversion_status, "
                    "row_version FROM measurements WHERE id='dstat-measurement'"
                )
            ).one()
            lot = connection.execute(
                text(
                    "SELECT id, data_status, identifier_evidence, "
                    "identifier_evidence_sha256, row_version FROM oqc_lots WHERE id='dstat-lot'"
                )
            ).one()
        return {
            "result": tuple(result),
            "measurement": tuple(measurement),
            "lot": tuple(lot),
        }
    finally:
        engine.dispose()


def _install_review_only_terminal_projection(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
            connection.execute(
                text(
                    "INSERT INTO data_status_transitions "
                    "(id, project_key, source_file_id, inspection_result_id, command_id, "
                    "intent_sha256, from_status, to_status, before_result_row_version, "
                    "after_result_row_version, measurement_count, candidate_snapshot, "
                    "candidate_sha256, decision_snapshot, decision_snapshot_sha256, "
                    "evaluation_mode, system_judgment, system_judgment_status, "
                    "spec_evaluation_status, applied_master_history_id, "
                    "applied_master_revision_id, applied_master_revision_number, "
                    "applied_master_history_row_version, applied_master_revision_row_version, "
                    "applied_master_payload_sha256, applied_master_declared_effective_from, "
                    "applied_master_declared_effective_to, applied_master_resolved_effective_to, "
                    "decided_by, decided_at, reason) VALUES "
                    "('dstat-transition', 'dstat-project', 'dstat-source', 'dstat-result', "
                    "'dstat-terminal-command', :intent, 'PENDING', 'SUSPECT', 1, 2, 1, '{}', "
                    ":candidate, '{}', :decision, 'REVIEW_ONLY', NULL, 'NOT_EVALUATED', "
                    "'NOT_EVALUATED', NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, "
                    "'migration-admin', '2026-08-15 11:00:00', "
                    "'Synthetic terminal downgrade guard.')"
                ),
                {
                    "intent": "a" * 64,
                    "candidate": "b" * 64,
                    "decision": "c" * 64,
                },
            )
            connection.execute(
                text(
                    "UPDATE inspection_results SET data_status='SUSPECT', "
                    "current_data_status_transition_id='dstat-transition', "
                    "current_decision_command_id='dstat-terminal-command', "
                    "current_decision_candidate_sha256=:candidate, "
                    "current_decision_mode='REVIEW_ONLY', current_decided_by='migration-admin', "
                    "current_decided_at='2026-08-15 11:00:00', "
                    "current_decision_reason='Synthetic terminal downgrade guard.', "
                    "row_version=2 WHERE id='dstat-result'"
                ),
                {"candidate": "b" * 64},
            )
            connection.execute(
                text(
                    "UPDATE measurements SET data_status='SUSPECT', row_version=2 "
                    "WHERE id='dstat-measurement'"
                )
            )
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
    finally:
        engine.dispose()


@pytest.mark.required_test_id("DQ-P1-DSTAT-010")
def test_0005_migration_recovers_from_failure_preserves_data_and_guards_downgrade(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MASS_PRODUCTION_QUALITY_VALIDATION_DATABASE_URL", raising=False)
    isolated_cwd = tmp_path / "isolated-cwd"
    isolated_cwd.mkdir()
    monkeypatch.chdir(isolated_cwd)
    database_path = tmp_path / "data-status-migration.sqlite3"
    database_url = f"sqlite+pysqlite:///{database_path.as_posix()}"
    config = migration_tests._config(database_url)
    alembic_command.upgrade(config, "0004")
    _seed_0004_long_state(database_url, measurement_digest="short-digest")
    baseline = _migration_long_snapshot(database_url)

    with pytest.raises(IntegrityError, match="CHECK constraint failed"):
        alembic_command.upgrade(config, "0005")
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0004"
            artifacts = connection.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE "
                    "name='_measurements_0005_backup' OR name LIKE '_alembic_tmp_%'"
                )
            ).all()
            assert artifacts == []
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
        assert "data_status_transitions" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()
    assert _migration_long_snapshot(database_url) == baseline

    engine = create_engine(database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE measurements SET evidence_sha256=:digest WHERE id='dstat-measurement'"
                ),
                {"digest": "d" * 64},
            )
    finally:
        engine.dispose()
    repaired = _migration_long_snapshot(database_url)
    alembic_command.upgrade(config, "0005")
    assert _migration_long_snapshot(database_url) == repaired
    alembic_command.downgrade(config, "0004")
    assert _migration_long_snapshot(database_url) == repaired
    alembic_command.upgrade(config, "0005")
    assert _migration_long_snapshot(database_url) == repaired

    engine = create_engine(database_url)
    try:
        result_checks = " ".join(
            str(value["sqltext"])
            for value in inspect(engine).get_check_constraints("inspection_results")
        )
        measurement_checks = " ".join(
            str(value["sqltext"]) for value in inspect(engine).get_check_constraints("measurements")
        )
        assert "REPLACED" in result_checks
        assert "REPLACED" in measurement_checks
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE measurements SET data_status='REPLACED' WHERE id='dstat-measurement'")
            )
            assert (
                connection.scalar(
                    text("SELECT data_status FROM measurements WHERE id='dstat-measurement'")
                )
                == "REPLACED"
            )
            connection.execute(
                text("UPDATE measurements SET data_status='PENDING' WHERE id='dstat-measurement'")
            )
        # REPLACED is vocabulary/DB compatibility only in this slice.  A result
        # has no current decision-projection shape or command for it until a
        # future explicit transition migration is implemented.
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                text("UPDATE inspection_results SET data_status='REPLACED' WHERE id='dstat-result'")
            )
    finally:
        engine.dispose()

    _install_review_only_terminal_projection(database_url)
    terminal = _migration_long_snapshot(database_url)
    with pytest.raises(RuntimeError, match="downgrade refused"):
        alembic_command.downgrade(config, "0004")
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0005"
            assert connection.scalar(text("SELECT COUNT(*) FROM data_status_transitions")) == 1
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
        assert "data_status_transitions" in inspect(engine).get_table_names()
    finally:
        engine.dispose()
    assert _migration_long_snapshot(database_url) == terminal
    assert tuple(isolated_cwd.iterdir()) == ()
    assert not (isolated_cwd / ".localdata").exists()
