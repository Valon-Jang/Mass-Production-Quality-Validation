from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.application.mapping_preview import MappingTemplateCatalog
from app.application.mapping_template_commands import (
    ApproveMappingTemplateRevisionCommand,
    CreateMappingTemplateRevisionCommand,
    MappingTemplateAuthorizationError,
    MappingTemplateCommandService,
    ReviewMappingTemplateRevisionCommand,
    SupersedeMappingTemplateRevisionCommand,
    UntrustedMappingTemplateActorError,
)
from app.domain.audit import AuditChange
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
    TemplateHistoryError,
    TemplateHistoryErrorCode,
    WorkbookFingerprint,
)
from app.domain.workbook_scan import SheetKind
from app.infrastructure.audit import AuditLog, AuditRepository
from app.infrastructure.database import Base, Database
from app.infrastructure.mapping_templates import (
    MappingTemplateHistoryRow,
    MappingTemplateRepository,
    MappingTemplateRevisionRow,
    MappingTemplateSupersessionRow,
    PersistedMappingTemplate,
    StaleMappingTemplateWriteError,
)

_NOW = datetime(2026, 8, 15, 6, 30, tzinfo=UTC)
_REVIEWER = Actor(
    actor_id="reviewer-001",
    kind=ActorKind.LOCAL_OWNER,
    roles=frozenset({Role.REVIEWER}),
)
_ADMIN = Actor(
    actor_id="admin-001",
    kind=ActorKind.LOCAL_OWNER,
    roles=frozenset({Role.ADMIN}),
)
_VIEWER = Actor(
    actor_id="viewer-001",
    kind=ActorKind.LOCAL_OWNER,
    roles=frozenset({Role.VIEWER}),
)


def _clock() -> datetime:
    return _NOW


def _database(path: Path) -> Database:
    database = Database(f"sqlite+pysqlite:///{path.as_posix()}")
    Base.metadata.create_all(database.engine)
    return database


def _address(coordinate: str) -> CellAddress:
    return CellAddress(sheet_name="OQC", coordinate=coordinate)


def _template(
    *,
    project_key: str = "project-alpha",
    revision: int = 1,
    effective_from: date = date(2026, 1, 1),
    effective_to: date | None = None,
) -> MappingTemplate:
    row = InspectionRowMapping(
        row_key="dimension-row",
        item=_address("A4"),
        sample_cells=(_address("B4"),),
        supplier_result=_address("C4"),
    )
    return MappingTemplate(
        template_id="oqc-layout",
        schema_version="1",
        revision=revision,
        status=MappingTemplateStatus.DRAFT,
        project_key=project_key,
        supplier_scope="supplier-alpha",
        supplier_source_aliases=("Supplier Alpha",),
        approved_by=None,
        approved_at=None,
        effective_from=effective_from,
        effective_to=effective_to,
        fingerprint=WorkbookFingerprint(
            header_tokens=(
                HeaderTokenAssertion(source=_address("A1"), expected_token="OQC Report"),
            ),
            sheet_structures=(
                SheetStructureAssertion(
                    sheet_name="OQC",
                    expected_position=0,
                    expected_kind=SheetKind.WORKSHEET,
                    expected_visibility="visible",
                    expected_used_range="A1:C4",
                ),
            ),
            merge_signatures=(
                MergeSignatureAssertion(
                    sheet_name="OQC",
                    expected_merged_ranges=(),
                ),
            ),
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


def _create(
    service: MappingTemplateCommandService,
    template: MappingTemplate,
    expected_history_row_version: int,
    *,
    actor: Actor = _REVIEWER,
) -> PersistedMappingTemplate:
    return service.create_revision(
        CreateMappingTemplateRevisionCommand(
            template=template,
            expected_history_row_version=expected_history_row_version,
            actor=actor,
            reason="Register a source-verified Mapping Template revision.",
            source_reference="source-file:synthetic-fixture",
        )
    )


def _review(
    service: MappingTemplateCommandService,
    *,
    revision: int,
    history_version: int,
    revision_version: int,
    actor: Actor = _REVIEWER,
) -> PersistedMappingTemplate:
    return service.review(
        ReviewMappingTemplateRevisionCommand(
            project_key="project-alpha",
            supplier_scope="supplier-alpha",
            template_id="oqc-layout",
            revision=revision,
            expected_history_row_version=history_version,
            expected_revision_row_version=revision_version,
            actor=actor,
            reason="Review source cells and the declared effective period.",
        )
    )


def _approve(
    service: MappingTemplateCommandService,
    *,
    revision: int,
    history_version: int,
    revision_version: int,
    actor: Actor = _ADMIN,
) -> PersistedMappingTemplate:
    return service.approve(
        ApproveMappingTemplateRevisionCommand(
            project_key="project-alpha",
            supplier_scope="supplier-alpha",
            template_id="oqc-layout",
            revision=revision,
            expected_history_row_version=history_version,
            expected_revision_row_version=revision_version,
            actor=actor,
            reason="Final ADMIN approval after review.",
        )
    )


@pytest.mark.required_test_id("DQ-P1-MAP-010")
def test_persistent_roundtrip_survives_restart_and_isolates_projects(tmp_path: Path) -> None:
    database_path = tmp_path / "mapping-roundtrip.sqlite3"
    database = _database(database_path)
    original_alpha = _template(effective_to=date(2026, 12, 31))
    original_beta = _template(project_key="project-beta")
    service = MappingTemplateCommandService(database, clock=_clock)
    try:
        _create(service, original_alpha, 0)
        _create(service, original_beta, 0)
    finally:
        database.dispose()

    restarted = Database(f"sqlite+pysqlite:///{database_path.as_posix()}")
    repository = MappingTemplateRepository()
    try:
        with restarted.session() as session:
            alpha = repository.get(
                session,
                project_key="project-alpha",
                supplier_scope="supplier-alpha",
                template_id="oqc-layout",
                revision=1,
            )
            beta = repository.get(
                session,
                project_key="project-beta",
                supplier_scope="supplier-alpha",
                template_id="oqc-layout",
                revision=1,
            )
            alpha_catalog = repository.load_catalog(session, project_key="project-alpha")

        assert alpha.template == original_alpha
        assert beta.template == original_beta
        assert alpha.history_id != beta.history_id
        assert alpha.history_row_version == beta.history_row_version == 1
        assert alpha_catalog.templates == (original_alpha,)
        assert all(template.project_key == "project-alpha" for template in alpha_catalog.templates)
        catalog_contract: MappingTemplateCatalog = alpha_catalog
        assert catalog_contract.resolved_effective_to(original_alpha) == date(2026, 12, 31)
        with pytest.raises(FrozenInstanceError):
            alpha.template.revision = 99  # type: ignore[misc]
    finally:
        restarted.dispose()


@pytest.mark.required_test_id("DQ-P1-MAP-011")
def test_review_and_admin_approval_are_audited_without_mutating_payload(tmp_path: Path) -> None:
    database = _database(tmp_path / "mapping-approval.sqlite3")
    service = MappingTemplateCommandService(database, clock=_clock)
    repository = MappingTemplateRepository()
    try:
        _create(service, _template(), 0)
        with database.session() as session:
            payload_before = session.scalar(
                select(MappingTemplateRevisionRow.template_payload).where(
                    MappingTemplateRevisionRow.revision_number == 1
                )
            )

        _review(service, revision=1, history_version=1, revision_version=1)
        with pytest.raises(MappingTemplateAuthorizationError):
            _approve(
                service,
                revision=1,
                history_version=2,
                revision_version=2,
                actor=_REVIEWER,
            )
        approved = _approve(service, revision=1, history_version=2, revision_version=2)

        _create(service, _template(revision=2, effective_from=date(2027, 1, 1)), 3, actor=_ADMIN)
        admin_reviewed = _review(
            service,
            revision=2,
            history_version=4,
            revision_version=1,
            actor=_ADMIN,
        )
        with database.session() as session:
            stored = repository.get(
                session,
                project_key="project-alpha",
                supplier_scope="supplier-alpha",
                template_id="oqc-layout",
                revision=1,
            )
            payload_after = session.scalar(
                select(MappingTemplateRevisionRow.template_payload).where(
                    MappingTemplateRevisionRow.revision_number == 1
                )
            )
            audits = session.scalars(select(AuditLog).order_by(AuditLog.occurred_at)).all()

        assert approved.template.status == MappingTemplateStatus.APPROVED
        assert stored.template.reviewed_by == _REVIEWER.actor_id
        assert stored.template.approved_by == _ADMIN.actor_id
        assert stored.history_row_version == 5
        assert stored.revision_row_version == 3
        assert admin_reviewed.template.status == MappingTemplateStatus.REVIEWED
        assert payload_after == payload_before
        assert [audit.action for audit in audits] == [
            "MAPPING_TEMPLATE_REVISION_CREATED",
            "MAPPING_TEMPLATE_REVIEWED",
            "MAPPING_TEMPLATE_APPROVED",
            "MAPPING_TEMPLATE_REVISION_CREATED",
            "MAPPING_TEMPLATE_REVIEWED",
        ]
        assert audits[2].actor_roles == ["ADMIN"]
        approval_before = cast(dict[str, object], audits[2].before_state)
        approval_after = cast(dict[str, object], audits[2].after_state)
        assert approval_before["status"] == "REVIEWED"
        assert approval_after["status"] == "APPROVED"
    finally:
        database.dispose()


@pytest.mark.required_test_id("DQ-P1-MAP-012")
def test_admin_supersession_resolves_predecessor_end_and_is_immutable(tmp_path: Path) -> None:
    database = _database(tmp_path / "mapping-supersession.sqlite3")
    service = MappingTemplateCommandService(database, clock=_clock)
    repository = MappingTemplateRepository()
    try:
        _create(service, _template(), 0)
        _review(service, revision=1, history_version=1, revision_version=1)
        _approve(service, revision=1, history_version=2, revision_version=2)
        _create(service, _template(revision=2, effective_from=date(2027, 1, 1)), 3)
        _review(service, revision=2, history_version=4, revision_version=1)

        reviewer_command = SupersedeMappingTemplateRevisionCommand(
            project_key="project-alpha",
            supplier_scope="supplier-alpha",
            template_id="oqc-layout",
            predecessor_revision=1,
            successor_revision=2,
            expected_history_row_version=5,
            expected_predecessor_row_version=3,
            expected_successor_row_version=2,
            actor=_REVIEWER,
            reason="Move to the reviewed successor at the year boundary.",
        )
        with pytest.raises(MappingTemplateAuthorizationError):
            service.supersede(reviewer_command)
        result = service.supersede(replace(reviewer_command, actor=_ADMIN))
        with pytest.raises(TemplateHistoryError) as duplicate:
            service.supersede(
                replace(
                    reviewer_command,
                    expected_history_row_version=6,
                    expected_predecessor_row_version=4,
                    expected_successor_row_version=3,
                    actor=_ADMIN,
                )
            )
        assert duplicate.value.code == TemplateHistoryErrorCode.SUPERSESSION_DUPLICATE

        with database.session() as session:
            catalog = repository.load_catalog(session, project_key="project-alpha")
            decision_count = session.scalar(
                select(func.count()).select_from(MappingTemplateSupersessionRow)
            )
            audit = session.scalar(
                select(AuditLog).where(AuditLog.action == "MAPPING_TEMPLATE_SUPERSEDED")
            )

        predecessor, successor = catalog.templates
        assert result.decision.predecessor_effective_to == date(2026, 12, 31)
        assert result.predecessor.resolved_effective_to == date(2026, 12, 31)
        assert result.predecessor.revision_row_version == 4
        assert result.successor.template.status == MappingTemplateStatus.APPROVED
        assert result.successor.history_row_version == 6
        assert result.successor.revision_row_version == 3
        assert catalog.resolved_effective_to(predecessor) == date(2026, 12, 31)
        assert catalog.is_effective_on(predecessor, date(2026, 12, 31))
        assert not catalog.is_effective_on(predecessor, date(2027, 1, 1))
        assert catalog.is_effective_on(successor, date(2027, 1, 1))
        assert decision_count == 1
        assert audit is not None
        audit_before = cast(dict[str, object], audit.before_state)
        audit_after = cast(dict[str, object], audit.after_state)
        predecessor_before = cast(dict[str, object], audit_before["predecessor"])
        predecessor_after = cast(dict[str, object], audit_after["predecessor"])
        assert predecessor_before["resolved_effective_to"] is None
        assert predecessor_after["resolved_effective_to"] == "2026-12-31"
        with pytest.raises(FrozenInstanceError):
            result.decision.reason = "changed"  # type: ignore[misc]
    finally:
        database.dispose()


class _FailingAuditRepository(AuditRepository):
    def append(self, session: Session, change: AuditChange) -> AuditLog:
        raise RuntimeError("synthetic audit failure")


@pytest.mark.required_test_id("DQ-P1-MAP-013")
def test_unauthorized_invalid_and_audit_failures_roll_back_all_writes(tmp_path: Path) -> None:
    database = _database(tmp_path / "mapping-rollbacks.sqlite3")
    template = _template()
    failing_service = MappingTemplateCommandService(
        database,
        audit_repository=_FailingAuditRepository(),
        clock=_clock,
    )
    service = MappingTemplateCommandService(database, clock=_clock)
    repository = MappingTemplateRepository()
    try:
        with pytest.raises(RuntimeError, match="synthetic audit failure"):
            _create(failing_service, template, 0)
        with pytest.raises(MappingTemplateAuthorizationError):
            _create(service, template, 0, actor=_VIEWER)
        with pytest.raises(UntrustedMappingTemplateActorError):
            _create(service, template, 0, actor=cast(Actor, "forged-actor"))

        with database.session() as session:
            assert session.scalar(select(func.count()).select_from(MappingTemplateHistoryRow)) == 0
            assert session.scalar(select(func.count()).select_from(MappingTemplateRevisionRow)) == 0
            assert session.scalar(select(func.count()).select_from(AuditLog)) == 0

        _create(service, template, 0, actor=_ADMIN)
        with pytest.raises(TemplateHistoryError) as invalid:
            _approve(service, revision=1, history_version=1, revision_version=1)
        assert invalid.value.code == TemplateHistoryErrorCode.INVALID_STATUS_TRANSITION
        _review(
            service,
            revision=1,
            history_version=1,
            revision_version=1,
            actor=_ADMIN,
        )
        with pytest.raises(RuntimeError, match="synthetic audit failure"):
            _approve(
                failing_service,
                revision=1,
                history_version=2,
                revision_version=2,
            )
        with database.session() as session:
            stored = repository.get(
                session,
                project_key="project-alpha",
                supplier_scope="supplier-alpha",
                template_id="oqc-layout",
                revision=1,
            )
            assert stored.template.status == MappingTemplateStatus.REVIEWED
            assert stored.history_row_version == 2
            assert stored.revision_row_version == 2
            assert session.scalar(select(func.count()).select_from(AuditLog)) == 2
    finally:
        database.dispose()


@pytest.mark.required_test_id("DQ-P1-MAP-014")
def test_stale_duplicate_and_overlap_conflicts_leave_no_partial_state(tmp_path: Path) -> None:
    database = _database(tmp_path / "mapping-conflicts.sqlite3")
    service = MappingTemplateCommandService(database, clock=_clock)
    repository = MappingTemplateRepository()
    try:
        _create(service, _template(), 0)
        _review(service, revision=1, history_version=1, revision_version=1)
        _approve(service, revision=1, history_version=2, revision_version=2)

        with pytest.raises(StaleMappingTemplateWriteError):
            _create(service, _template(revision=2, effective_from=date(2027, 1, 1)), 2)
        with pytest.raises(TemplateHistoryError) as duplicate:
            _create(service, _template(), 3)
        assert duplicate.value.code == TemplateHistoryErrorCode.REVISION_OVERWRITE

        _create(service, _template(revision=2, effective_from=date(2027, 1, 1)), 3)
        with pytest.raises(StaleMappingTemplateWriteError):
            _review(service, revision=2, history_version=4, revision_version=99)
        _review(service, revision=2, history_version=4, revision_version=1)
        with pytest.raises(TemplateHistoryError) as overlap:
            _approve(service, revision=2, history_version=5, revision_version=2)
        assert overlap.value.code == TemplateHistoryErrorCode.EFFECTIVE_PERIOD_OVERLAP

        with database.session() as session:
            revision_two = repository.get(
                session,
                project_key="project-alpha",
                supplier_scope="supplier-alpha",
                template_id="oqc-layout",
                revision=2,
            )
            assert revision_two.template.status == MappingTemplateStatus.REVIEWED
            assert revision_two.history_row_version == 5
            assert revision_two.revision_row_version == 2
            assert session.scalar(select(func.count()).select_from(AuditLog)) == 5
            assert (
                session.scalar(select(func.count()).select_from(MappingTemplateSupersessionRow))
                == 0
            )
    finally:
        database.dispose()
