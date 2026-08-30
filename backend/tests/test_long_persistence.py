from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application.long_candidate import build_long_candidate
from app.application.long_persistence import (
    LongMaterializationFailedError,
    LongPersistenceRequest,
    LongPersistenceService,
    LongPersistenceValidationError,
)
from app.application.mapping_preview import (
    InMemoryMappingTemplateRegistry,
    build_mapping_preview,
)
from app.application.mapping_template_commands import (
    ApproveMappingTemplateRevisionCommand,
    CreateMappingTemplateRevisionCommand,
    MappingTemplateCommandService,
    ReviewMappingTemplateRevisionCommand,
)
from app.application.store_scan_mapping import (
    ResolvedMappingScope,
    StoreScanMappingOutcome,
    StoreScanMappingStatus,
)
from app.domain.identity import Actor, ActorKind, Role
from app.domain.long_format import (
    CanonicalRowBinding,
    CanonicalRowBindingKey,
    CanonicalRowBindingStatus,
    LongCandidateResult,
    LongCandidateState,
    MaterializedCanonicalRowBindingCatalog,
    MeasurementMode,
    SamplePolicy,
)
from app.domain.mapping import (
    CellAddress,
    HeaderTokenAssertion,
    IdentifierKind,
    IdentifierMapping,
    InspectionRowMapping,
    MappingPreviewRequest,
    MappingPreviewState,
    MappingTemplate,
    MappingTemplateStatus,
    MergeSignatureAssertion,
    RowStructureAssertion,
    SheetStructureAssertion,
    WorkbookFingerprint,
)
from app.domain.source_file import SourceFileReceipt
from app.domain.workbook_scan import (
    CellEvidence,
    DisplayValueStatus,
    IssueSeverity,
    MacroHandling,
    RowCandidate,
    RowCandidateKind,
    ScanIssue,
    SheetKind,
    SheetProtectionMetadata,
    SheetScan,
    SourceLocation,
    WorkbookScan,
    WorkbookScanState,
)
from app.infrastructure.database import Base, Database
from app.infrastructure.long_format import (
    LongClaimResult,
    LongFormatRepository,
    LongIngestionJobRow,
    LongInspectionResultRow,
    LongJobStatus,
    LongMaterializationCounts,
    LongMeasurementRow,
    LongSourceFileRow,
    LongSourceSheetRow,
    OqcLotRow,
    StaleLongJobWriteError,
    serialize_long_candidate,
)
from app.infrastructure.mapping_templates import PersistedMappingTemplate

_SHEET = "OQC"
_SUPPLIER_SCOPE = "supplier-scope"
_SOURCE_SUPPLIER = "SUPPLIER-A"
_SOURCE_MODEL = "MODEL-A"
_SOURCE_LOT = "LOT-A"
_INSPECTION_DATE = date(2026, 6, 15)
_NOW = datetime(2026, 8, 15, 8, 30, tzinfo=UTC)
_HASH = "a" * 64
_ADMIN = Actor(
    actor_id="local-admin",
    kind=ActorKind.LOCAL_OWNER,
    roles=frozenset({Role.ADMIN}),
)
_REVIEWER = Actor(
    actor_id="local-reviewer",
    kind=ActorKind.LOCAL_OWNER,
    roles=frozenset({Role.REVIEWER}),
)


@dataclass(frozen=True, slots=True)
class _Fixture:
    database: Database
    mapping: PersistedMappingTemplate
    outcome: StoreScanMappingOutcome
    candidate: LongCandidateResult


def _clock() -> datetime:
    return _NOW


def _database(path: Path) -> Database:
    database = Database(f"sqlite+pysqlite:///{path.as_posix()}")
    Base.metadata.create_all(database.engine)
    return database


def _address(coordinate: str) -> CellAddress:
    return CellAddress(sheet_name=_SHEET, coordinate=coordinate)


def _row(row_key: str, row_number: int, samples: tuple[str, ...]) -> InspectionRowMapping:
    return InspectionRowMapping(
        row_key=row_key,
        item=_address(f"A{row_number}"),
        sample_cells=tuple(_address(f"{column}{row_number}") for column in samples),
        supplier_result=_address(f"D{row_number}"),
    )


def _template(
    project_key: str, *, numeric_samples: tuple[str, ...] = ("B", "C")
) -> MappingTemplate:
    rows = (
        _row("numeric-row", 4, numeric_samples),
        _row("qualitative-row", 5, ("B",)),
    )
    fingerprint_cells = {
        "numeric-row": tuple(_address(f"{column}4") for column in ("A", "B", "C", "D")),
        "qualitative-row": tuple(_address(f"{column}5") for column in ("A", "B", "D")),
    }
    return MappingTemplate(
        template_id="oqc-layout",
        schema_version="1",
        revision=1,
        status=MappingTemplateStatus.DRAFT,
        project_key=project_key,
        supplier_scope=_SUPPLIER_SCOPE,
        supplier_source_aliases=(_SOURCE_SUPPLIER,),
        approved_by=None,
        approved_at=None,
        effective_from=date(2026, 1, 1),
        effective_to=date(2026, 12, 31),
        fingerprint=WorkbookFingerprint(
            header_tokens=(HeaderTokenAssertion(_address("A1"), "OQC Report"),),
            sheet_structures=(
                SheetStructureAssertion(
                    sheet_name=_SHEET,
                    expected_position=0,
                    expected_kind=SheetKind.WORKSHEET,
                    expected_visibility="visible",
                    expected_used_range="A1:F5",
                ),
            ),
            merge_signatures=(MergeSignatureAssertion(_SHEET, ()),),
            row_structures=tuple(
                RowStructureAssertion(
                    row_key=row.row_key,
                    sheet_name=_SHEET,
                    row_index=row.item.row_index,
                    expected_non_empty_cells=fingerprint_cells[row.row_key],
                )
                for row in rows
            ),
        ),
        identifiers=(
            IdentifierMapping(IdentifierKind.SUPPLIER, _address("B2")),
            IdentifierMapping(IdentifierKind.MODEL, _address("C2")),
            IdentifierMapping(IdentifierKind.LOT_NUMBER, _address("D2")),
            IdentifierMapping(IdentifierKind.INSPECTION_DATE, _address("E2")),
            IdentifierMapping(IdentifierKind.PART_NUMBER, _address("F2")),
        ),
        inspection_rows=rows,
    )


def _cell(
    coordinate: str,
    value: object,
    data_type: str,
    *,
    cached_value: object | None = None,
    formula_text: str | None = None,
) -> CellEvidence:
    return CellEvidence(
        coordinate=coordinate,
        stored_value=value,
        cached_value=cached_value,
        formula_text=formula_text,
        number_format="General",
        data_type=data_type,
        display_value=None,
        display_value_status=DisplayValueStatus.NOT_RENDERED,
    )


def _scan(*, formula_sample: bool = False) -> WorkbookScan:
    cells = (
        _cell("A1", "OQC Report", "s"),
        _cell("B2", _SOURCE_SUPPLIER, "s"),
        _cell("C2", _SOURCE_MODEL, "s"),
        _cell("D2", _SOURCE_LOT, "s"),
        _cell("E2", _INSPECTION_DATE, "d"),
        _cell("F2", "PART-A", "s"),
        _cell("A4", "Dimension", "s"),
        _cell(
            "B4",
            "=10+0.25" if formula_sample else Decimal("10.25"),
            "f" if formula_sample else "n",
            cached_value=Decimal("10.25") if formula_sample else None,
            formula_text="=10+0.25" if formula_sample else None,
        ),
        _cell("C4", Decimal("9.75"), "n"),
        _cell("D4", "SUPPLIER-OK", "s"),
        _cell("A5", "Appearance", "s"),
        _cell("B5", "CLEAR", "s"),
        _cell("D5", "SUPPLIER-OK", "s"),
    )
    scan_issues = (
        (
            ScanIssue(
                code="CALCULATION_REFRESH_REQUIRED",
                severity=IssueSeverity.WARNING,
                message="Synthetic formula requires an explicit workbook refresh.",
                location=SourceLocation.cell(_SHEET, "B4"),
            ),
        )
        if formula_sample
        else ()
    )
    sheet = SheetScan(
        name=_SHEET,
        kind=SheetKind.WORKSHEET,
        position=0,
        visibility="visible",
        used_range="A1:F5",
        estimated_cells=len(cells),
        merged_ranges=(),
        hidden_row_ranges=(),
        hidden_column_ranges=(),
        cells=cells,
        row_candidates=(
            RowCandidate(
                row_index=4,
                kind=RowCandidateKind.STRUCTURAL,
                reason="synthetic exact row",
                signature=("Dimension",),
            ),
        ),
        protection=SheetProtectionMetadata(enabled=False, protected_actions=()),
        images=(),
        issues=scan_issues,
    )
    return WorkbookScan(
        state=(
            WorkbookScanState.SCANNED_WITH_WARNINGS if formula_sample else WorkbookScanState.SCANNED
        ),
        source_name="report.xlsx",
        source_size_bytes=4096,
        source_sha256_before=_HASH,
        source_sha256_after=_HASH,
        sheets=(sheet,),
        issues=scan_issues,
        estimated_cells=len(cells),
        external_link_count=0,
        macro_handling=MacroHandling.NOT_APPLICABLE,
        is_golden_workbook_evidence=False,
    )


def _persist_mapping(database: Database, project_key: str) -> PersistedMappingTemplate:
    commands = MappingTemplateCommandService(database, clock=_clock)
    created = commands.create_revision(
        CreateMappingTemplateRevisionCommand(
            template=_template(project_key),
            expected_history_row_version=0,
            actor=_REVIEWER,
            reason="Register exact synthetic source mapping.",
        )
    )
    reviewed = commands.review(
        ReviewMappingTemplateRevisionCommand(
            project_key=project_key,
            supplier_scope=_SUPPLIER_SCOPE,
            template_id="oqc-layout",
            revision=1,
            expected_history_row_version=created.history_row_version,
            expected_revision_row_version=created.revision_row_version,
            actor=_REVIEWER,
            reason="Review exact source cells.",
        )
    )
    return commands.approve(
        ApproveMappingTemplateRevisionCommand(
            project_key=project_key,
            supplier_scope=_SUPPLIER_SCOPE,
            template_id="oqc-layout",
            revision=1,
            expected_history_row_version=reviewed.history_row_version,
            expected_revision_row_version=reviewed.revision_row_version,
            actor=_ADMIN,
            reason="Approve exact source mapping.",
        )
    )


def _outcome(
    mapping: PersistedMappingTemplate,
    *,
    receipt_id: str = "receipt-1",
    received_at: datetime = _NOW,
    template_override: MappingTemplate | None = None,
    formula_sample: bool = False,
) -> StoreScanMappingOutcome:
    scan = _scan(formula_sample=formula_sample)
    template = template_override or mapping.template
    registry = InMemoryMappingTemplateRegistry()
    registry.register(template)
    mapping_result = build_mapping_preview(
        scan,
        MappingPreviewRequest(
            project_key=template.project_key,
            supplier_scope=template.supplier_scope,
        ),
        registry,
    )
    assert mapping_result.state == MappingPreviewState.PREVIEW_READY
    receipt = SourceFileReceipt(
        receipt_id=receipt_id,
        project_key=template.project_key,
        blob_id=f"sha256:{_HASH}",
        content_sha256=_HASH,
        received_at=received_at,
        original_filename=scan.source_name,
        model_candidates=(_SOURCE_MODEL,),
        lot_candidates=(_SOURCE_LOT,),
        declared_mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        detected_mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        canonical_extension=".xlsx",
        size_bytes=scan.source_size_bytes,
    )
    return StoreScanMappingOutcome(
        status=StoreScanMappingStatus.PREVIEW_READY,
        scope=ResolvedMappingScope(
            project_key=template.project_key,
            supplier_scope=template.supplier_scope,
        ),
        receipt=receipt,
        scan=scan,
        mapping_result=mapping_result,
    )


def _binding(
    project_key: str,
    row_key: str,
    *,
    status: CanonicalRowBindingStatus = CanonicalRowBindingStatus.APPROVED,
    effective_from: date = date(2026, 1, 1),
    effective_to: date | None = date(2026, 12, 31),
    source_models: tuple[str, ...] = (_SOURCE_MODEL,),
) -> CanonicalRowBinding:
    approved = status == CanonicalRowBindingStatus.APPROVED
    qualitative = row_key == "qualitative-row"
    return CanonicalRowBinding(
        key=CanonicalRowBindingKey(
            project_key=project_key,
            supplier_scope=_SUPPLIER_SCOPE,
            template_id="oqc-layout",
            template_revision=1,
            row_key=row_key,
        ),
        binding_revision=1,
        status=status,
        approved_by="binding-admin" if approved else None,
        approved_at=_NOW if approved else None,
        effective_from=effective_from,
        effective_to=effective_to,
        source_model_values=source_models,
        canonical_model_key=f"{project_key}:MODEL-A",
        canonical_supplier_key=f"{project_key}:SUPPLIER-A",
        canonical_model_part_key=f"{project_key}:PART-A",
        canonical_item_key=f"{project_key}:{row_key}",
        sample_policy=SamplePolicy.AT_LEAST_ONE,
        measurement_mode=(MeasurementMode.QUALITATIVE if qualitative else MeasurementMode.NUMERIC),
    )


def _candidate(
    outcome: StoreScanMappingOutcome,
    bindings: tuple[CanonicalRowBinding, ...] | None = None,
) -> LongCandidateResult:
    project_key = outcome.scope.project_key
    catalog = MaterializedCanonicalRowBindingCatalog(
        bindings
        if bindings is not None
        else (
            _binding(project_key, "numeric-row"),
            _binding(project_key, "qualitative-row"),
        )
    )
    return build_long_candidate(outcome, catalog)


def _fixture(tmp_path: Path, *, project_key: str = "project-alpha") -> _Fixture:
    database = _database(tmp_path / f"{project_key}.sqlite3")
    mapping = _persist_mapping(database, project_key)
    outcome = _outcome(mapping)
    return _Fixture(
        database=database,
        mapping=mapping,
        outcome=outcome,
        candidate=_candidate(outcome),
    )


def _request(
    outcome: StoreScanMappingOutcome, candidate: LongCandidateResult
) -> LongPersistenceRequest:
    return LongPersistenceRequest(
        outcome=outcome,
        candidate=candidate,
        loader_version="long-loader-v1",
        scan_contract_version="workbook-scan-v1",
    )


@pytest.mark.required_test_id("DQ-P1-LDB-001")
def test_ready_candidate_roundtrips_pending_rows_and_exact_cell_evidence(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    repository = LongFormatRepository()
    try:
        result = LongPersistenceService(fixture.database, clock=_clock).persist(
            _request(fixture.outcome, fixture.candidate)
        )
        assert result.status == LongJobStatus.COMPLETED_PENDING
        assert result.counts.lot_count == 1
        assert result.counts.result_count == 2
        assert result.counts.measurement_count == 3
        assert result.counts.held_result_count == 0

        with fixture.database.session() as session:
            results = session.scalars(select(LongInspectionResultRow)).all()
            measurements = session.scalars(select(LongMeasurementRow)).all()
            stored_snapshot = repository.load_candidate_snapshot(
                session,
                project_key="project-alpha",
                job_id=result.ingestion_job_id,
            )
            restored = repository.load_measurement_evidence(
                session,
                project_key="project-alpha",
                job_id=result.ingestion_job_id,
            )

        assert stored_snapshot == serialize_long_candidate(fixture.candidate)
        assert all(row.data_status == "PENDING" for row in results)
        assert all(row.system_judgment is None for row in results)
        assert all(row.system_judgment_status == "NOT_EVALUATED" for row in results)
        assert all(row.spec_evaluation_status == "NOT_EVALUATED" for row in results)
        assert all(row.standardized_value is None for row in measurements)
        assert all(row.unit_conversion_status == "NOT_CONFIGURED" for row in measurements)
        expected_evidence = tuple(
            measurement.evidence
            for row in fixture.candidate.rows
            for measurement in row.measurements
        )
        assert restored == expected_evidence
        assert {item.source.coordinate: item.raw_value for item in restored} == {
            "B4": Decimal("10.25"),
            "C4": Decimal("9.75"),
            "B5": "CLEAR",
        }
    finally:
        fixture.database.dispose()


@pytest.mark.required_test_id("DQ-P1-LDB-002")
def test_partial_candidate_keeps_loadable_and_held_rows_with_all_samples(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    formula_outcome = _outcome(
        fixture.mapping,
        receipt_id="receipt-formula",
        formula_sample=True,
    )
    partial = _candidate(formula_outcome)
    assert partial.state == LongCandidateState.PARTIAL_HOLD
    try:
        result = LongPersistenceService(fixture.database, clock=_clock).persist(
            _request(formula_outcome, partial)
        )
        with fixture.database.session() as session:
            rows = session.scalars(
                select(LongInspectionResultRow).order_by(LongInspectionResultRow.source_row_key)
            ).all()
            sample_count = session.scalar(select(func.count()).select_from(LongMeasurementRow))
            snapshot = LongFormatRepository().load_candidate_snapshot(
                session,
                project_key="project-alpha",
                job_id=result.ingestion_job_id,
            )
            restored = LongFormatRepository.load_measurement_evidence(
                session,
                project_key="project-alpha",
                job_id=result.ingestion_job_id,
            )
            formula_measurement = session.scalar(
                select(LongMeasurementRow).where(LongMeasurementRow.source_cell == "B4")
            )
        assert result.status == LongJobStatus.PARTIAL_HELD
        assert result.counts.lot_count == 1
        assert result.counts.result_count == 2
        assert result.counts.measurement_count == 3
        assert result.counts.held_result_count == 1
        assert [(row.source_row_key, row.data_status) for row in rows] == [
            ("numeric-row", "HELD"),
            ("qualitative-row", "PENDING"),
        ]
        assert rows[0].canonical_item_key is not None
        assert rows[1].canonical_item_key is not None
        assert sample_count == 3
        assert snapshot == serialize_long_candidate(partial)
        expected_evidence = tuple(
            measurement.evidence for row in partial.rows for measurement in row.measurements
        )
        assert restored == expected_evidence
        formula = restored[0]
        assert formula.raw_value == "=10+0.25"
        assert formula.cached_value == Decimal("10.25")
        assert formula.formula_text == "=10+0.25"
        assert formula_measurement is not None
        assert formula_measurement.raw_numeric_value is None
        assert formula_measurement.raw_qualitative_value is None
        assert formula_measurement.standardized_value is None
        formula_issue_codes = {
            issue["code"]
            for row in snapshot["rows"]  # type: ignore[union-attr]
            if row["row_key"] == "numeric-row"
            for issue in row["issues"]
        }
        assert "CALCULATION_REFRESH_REQUIRED" in formula_issue_codes
    finally:
        fixture.database.dispose()


@pytest.mark.required_test_id("DQ-P1-LDB-003")
def test_forged_preview_is_rejected_and_untrusted_binding_never_populates_canonical_ids(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    service = LongPersistenceService(fixture.database, clock=_clock)
    forged_row = replace(
        fixture.mapping.template.inspection_rows[0],
        sample_cells=(_address("B4"),),
    )
    forged_template = replace(
        fixture.mapping.template,
        inspection_rows=(forged_row, fixture.mapping.template.inspection_rows[1]),
    )
    forged_outcome = _outcome(fixture.mapping, template_override=forged_template)
    forged_candidate = _candidate(forged_outcome)
    try:
        with pytest.raises(LongPersistenceValidationError, match="exact persisted revision"):
            service.persist(_request(forged_outcome, forged_candidate))
        with fixture.database.session() as session:
            assert session.scalar(select(func.count()).select_from(LongSourceFileRow)) == 0
            assert session.scalar(select(func.count()).select_from(LongIngestionJobRow)) == 0

        valid_outcome = fixture.outcome
        draft_binding = _binding(
            "project-alpha",
            "qualitative-row",
            status=CanonicalRowBindingStatus.DRAFT,
        )
        candidate = _candidate(
            valid_outcome,
            (_binding("project-alpha", "numeric-row"), draft_binding),
        )
        assert candidate.state == LongCandidateState.PARTIAL_HOLD
        result = service.persist(_request(valid_outcome, candidate))
        with fixture.database.session() as session:
            held = session.scalar(
                select(LongInspectionResultRow).where(
                    LongInspectionResultRow.source_row_key == "qualitative-row"
                )
            )
        assert result.status == LongJobStatus.PARTIAL_HELD
        assert held is not None
        assert held.binding_snapshot is not None
        assert held.binding_snapshot["status"] == "DRAFT"
        assert held.binding_revision is None
        assert held.canonical_model_part_key is None
        assert held.canonical_item_key is None
    finally:
        fixture.database.dispose()


@pytest.mark.required_test_id("DQ-P1-LDB-004")
def test_exact_replay_survives_restart_without_duplicate_materialization(tmp_path: Path) -> None:
    database_path = tmp_path / "restart.sqlite3"
    database = _database(database_path)
    mapping = _persist_mapping(database, "project-alpha")
    outcome = _outcome(mapping)
    candidate = _candidate(outcome)
    first = LongPersistenceService(database, clock=_clock).persist(_request(outcome, candidate))
    database.dispose()

    restarted = Database(f"sqlite+pysqlite:///{database_path.as_posix()}")
    try:
        replay = LongPersistenceService(restarted, clock=_clock).persist(
            _request(outcome, candidate)
        )
        with restarted.session() as session:
            counts = {
                "sources": session.scalar(select(func.count()).select_from(LongSourceFileRow)),
                "jobs": session.scalar(select(func.count()).select_from(LongIngestionJobRow)),
                "lots": session.scalar(select(func.count()).select_from(OqcLotRow)),
                "results": session.scalar(
                    select(func.count()).select_from(LongInspectionResultRow)
                ),
                "measurements": session.scalar(
                    select(func.count()).select_from(LongMeasurementRow)
                ),
            }
        assert replay.replayed is True
        assert replay.ingestion_job_id == first.ingestion_job_id
        assert replay.status == LongJobStatus.COMPLETED_PENDING
        assert counts == {"sources": 1, "jobs": 1, "lots": 1, "results": 2, "measurements": 3}
        with (
            restarted.session() as session,
            pytest.raises(StaleLongJobWriteError),
            session.begin(),
        ):
            LongFormatRepository().mark_materialized(
                session,
                project_key="project-alpha",
                job_id=first.ingestion_job_id,
                expected_row_version=first.row_version,
                status=LongJobStatus.COMPLETED_PENDING,
                counts=first.counts,
                finished_at=_NOW,
            )
        with (
            restarted.session() as session,
            pytest.raises(StaleLongJobWriteError),
            session.begin(),
        ):
            LongFormatRepository().mark_failed(
                session,
                project_key="project-alpha",
                job_id=first.ingestion_job_id,
                expected_row_version=first.row_version,
                finished_at=_NOW,
                error_code="STALE_SYNTHETIC_FAILURE",
                error_summary="This stale transition must never commit.",
            )
    finally:
        restarted.dispose()


class _InterruptedRepository(LongFormatRepository):
    def materialize(
        self,
        session: Session,
        *,
        claim: LongClaimResult,
        candidate: LongCandidateResult,
    ) -> LongMaterializationCounts:
        raise KeyboardInterrupt("synthetic process interruption")


class _FailingRepository(LongFormatRepository):
    def materialize(
        self,
        session: Session,
        *,
        claim: LongClaimResult,
        candidate: LongCandidateResult,
    ) -> LongMaterializationCounts:
        super().materialize(session, claim=claim, candidate=candidate)
        raise RuntimeError("synthetic fatal materialization failure")


@pytest.mark.required_test_id("DQ-P1-LDB-005")
def test_distinct_receipts_reuse_only_success_and_hold_on_processing_or_failed_owner(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    service = LongPersistenceService(fixture.database, clock=_clock)
    try:
        first = service.persist(_request(fixture.outcome, fixture.candidate))
        second_outcome = _outcome(
            fixture.mapping,
            receipt_id="receipt-2",
            received_at=datetime(2026, 8, 15, 8, 31, tzinfo=UTC),
        )
        second = service.persist(_request(second_outcome, _candidate(second_outcome)))
        assert second.status == LongJobStatus.REUSED
        assert second.reused_job_id == first.ingestion_job_id
        assert second.source_file_id != first.source_file_id
        assert second.counts == first.counts
        with fixture.database.session() as session:
            assert session.scalar(select(func.count()).select_from(LongSourceFileRow)) == 2
            assert session.scalar(select(func.count()).select_from(LongIngestionJobRow)) == 2
            assert session.scalar(select(func.count()).select_from(OqcLotRow)) == 1
            assert session.scalar(select(func.count()).select_from(LongInspectionResultRow)) == 2
            assert session.scalar(select(func.count()).select_from(LongMeasurementRow)) == 3

        scan_v2_outcome = _outcome(
            fixture.mapping,
            receipt_id="receipt-scan-v2",
            received_at=datetime(2026, 8, 15, 8, 32, tzinfo=UTC),
        )
        scan_v2_request = replace(
            _request(scan_v2_outcome, _candidate(scan_v2_outcome)),
            scan_contract_version="workbook-scan-v2",
        )
        scan_v2 = service.persist(scan_v2_request)
        assert scan_v2.status == LongJobStatus.COMPLETED_PENDING
        assert scan_v2.reused_job_id is None
        assert scan_v2.source_file_id not in {first.source_file_id, second.source_file_id}
        scan_v2_replay = service.persist(scan_v2_request)
        assert scan_v2_replay.replayed is True
        assert scan_v2_replay.ingestion_job_id == scan_v2.ingestion_job_id
        assert scan_v2_replay.status == LongJobStatus.COMPLETED_PENDING
        with fixture.database.session() as session:
            assert session.scalar(select(func.count()).select_from(LongSourceFileRow)) == 3
            assert session.scalar(select(func.count()).select_from(LongIngestionJobRow)) == 3
            assert session.scalar(select(func.count()).select_from(OqcLotRow)) == 2
            assert session.scalar(select(func.count()).select_from(LongInspectionResultRow)) == 4
            assert session.scalar(select(func.count()).select_from(LongMeasurementRow)) == 6

        beta_mapping = _persist_mapping(fixture.database, "project-beta")
        beta_outcome = _outcome(beta_mapping, receipt_id="receipt-beta")
        beta = service.persist(_request(beta_outcome, _candidate(beta_outcome)))
        assert beta.status == LongJobStatus.COMPLETED_PENDING
        assert beta.reused_job_id is None

        interrupted_outcome = _outcome(
            fixture.mapping,
            receipt_id="receipt-processing-owner",
        )
        interrupted_request = replace(
            _request(interrupted_outcome, _candidate(interrupted_outcome)),
            loader_version="interrupted-loader-v1",
        )
        with pytest.raises(KeyboardInterrupt):
            LongPersistenceService(
                fixture.database,
                repository=_InterruptedRepository(),
                clock=_clock,
            ).persist(interrupted_request)
        blocked_outcome = _outcome(
            fixture.mapping,
            receipt_id="receipt-processing-follower",
        )
        blocked = service.persist(
            replace(
                _request(blocked_outcome, _candidate(blocked_outcome)),
                loader_version="interrupted-loader-v1",
            )
        )
        assert blocked.status == LongJobStatus.RECOVERY_REQUIRED
        assert blocked.blocking_job_id is not None

        resumed = service.persist(interrupted_request)
        assert resumed.status == LongJobStatus.COMPLETED_PENDING
        assert resumed.ingestion_job_id == blocked.blocking_job_id
        resumed_replay = service.persist(interrupted_request)
        assert resumed_replay.replayed is True
        assert resumed_replay.ingestion_job_id == resumed.ingestion_job_id
        assert resumed_replay.status == LongJobStatus.COMPLETED_PENDING

        with fixture.database.session() as session:
            assert session.scalar(select(func.count()).select_from(LongSourceFileRow)) == 6
            assert session.scalar(select(func.count()).select_from(LongIngestionJobRow)) == 6
            assert session.scalar(select(func.count()).select_from(OqcLotRow)) == 4
            assert session.scalar(select(func.count()).select_from(LongInspectionResultRow)) == 8
            assert session.scalar(select(func.count()).select_from(LongMeasurementRow)) == 12
            blocked_job = session.get(LongIngestionJobRow, blocked.ingestion_job_id)
            projects = set(session.scalars(select(OqcLotRow.project_key)).all())
        assert blocked_job is not None
        assert blocked_job.status == LongJobStatus.RECOVERY_REQUIRED.value
        assert projects == {"project-alpha", "project-beta"}
    finally:
        fixture.database.dispose()


@pytest.mark.required_test_id("DQ-P1-LDB-006")
def test_fatal_materialization_rolls_back_long_rows_then_records_failed_job(tmp_path: Path) -> None:
    fixture = _fixture(tmp_path)
    failing = LongPersistenceService(
        fixture.database,
        repository=_FailingRepository(),
        clock=_clock,
    )
    try:
        with pytest.raises(LongMaterializationFailedError) as failure:
            failing.persist(_request(fixture.outcome, fixture.candidate))
        with fixture.database.session() as session:
            job = session.scalar(select(LongIngestionJobRow))
            assert session.scalar(select(func.count()).select_from(LongSourceFileRow)) == 1
            assert session.scalar(select(func.count()).select_from(LongSourceSheetRow)) == 1
            assert session.scalar(select(func.count()).select_from(OqcLotRow)) == 0
            assert session.scalar(select(func.count()).select_from(LongInspectionResultRow)) == 0
            assert session.scalar(select(func.count()).select_from(LongMeasurementRow)) == 0
        assert job is not None
        assert job.id == failure.value.job_id
        assert job.status == LongJobStatus.FAILED.value
        assert job.owns_materialization is True
        assert job.error_summary == "Pending Long-format materialization transaction failed."

        follower_outcome = _outcome(fixture.mapping, receipt_id="receipt-after-failure")
        follower = LongPersistenceService(fixture.database, clock=_clock).persist(
            _request(follower_outcome, _candidate(follower_outcome))
        )
        assert follower.status == LongJobStatus.RECOVERY_REQUIRED
        assert follower.blocking_job_id == job.id
        replay = LongPersistenceService(fixture.database, clock=_clock).persist(
            _request(fixture.outcome, fixture.candidate)
        )
        assert replay.status == LongJobStatus.FAILED
        assert replay.replayed is True
    finally:
        fixture.database.dispose()


@pytest.mark.required_test_id("DQ-P1-LDB-007")
def test_global_hold_keeps_complete_snapshot_and_database_rejects_official_values(
    tmp_path: Path,
) -> None:
    fixture = _fixture(tmp_path)
    held = _candidate(fixture.outcome, ())
    assert held.state == LongCandidateState.LOAD_HELD
    service = LongPersistenceService(fixture.database, clock=_clock)
    try:
        held_result = service.persist(_request(fixture.outcome, held))
        with fixture.database.session() as session:
            snapshot = LongFormatRepository().load_candidate_snapshot(
                session,
                project_key="project-alpha",
                job_id=held_result.ingestion_job_id,
            )
            source_sheet = session.scalar(select(LongSourceSheetRow))
            assert session.scalar(select(func.count()).select_from(OqcLotRow)) == 0
        assert held_result.status == LongJobStatus.HELD
        assert snapshot == serialize_long_candidate(held)
        assert len(snapshot["rows"]) == 2  # type: ignore[arg-type]
        assert source_sheet is not None
        assert len(source_sheet.scan_snapshot["cells"]) == 13  # type: ignore[arg-type]

        ready_outcome = _outcome(fixture.mapping, receipt_id="receipt-ready")
        ready = service.persist(_request(ready_outcome, _candidate(ready_outcome)))
        assert ready.status == LongJobStatus.COMPLETED_PENDING
        with (
            fixture.database.session() as session,
            pytest.raises(IntegrityError),
            session.begin(),
        ):
            # 0005 expands the storage vocabulary for the explicit review
            # service; the pending Long materializer still never emits a
            # terminal value, and unknown trust values remain DB-invalid.
            session.execute(update(LongMeasurementRow).values(data_status="OFFICIAL"))
        with (
            fixture.database.session() as session,
            pytest.raises(IntegrityError),
            session.begin(),
        ):
            session.execute(update(LongMeasurementRow).values(standardized_value="10.0"))
        with (
            fixture.database.session() as session,
            pytest.raises(IntegrityError),
            session.begin(),
        ):
            session.execute(
                update(LongInspectionResultRow).values(
                    system_judgment="PASS",
                    system_judgment_status="EVALUATED",
                )
            )
        with fixture.database.session() as session:
            measurement_id = session.scalar(select(LongMeasurementRow.id))
        assert measurement_id is not None
        with (
            fixture.database.session() as session,
            pytest.raises(IntegrityError),
            session.begin(),
        ):
            session.execute(
                update(LongMeasurementRow)
                .where(LongMeasurementRow.id == measurement_id)
                .values(project_key="project-other")
            )
    finally:
        fixture.database.dispose()
