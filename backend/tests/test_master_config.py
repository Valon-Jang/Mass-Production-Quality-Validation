from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.application.master_config_commands import (
    ApproveCanonicalRowBindingRevisionCommand,
    ApproveMasterSpecRevisionCommand,
    CreateCanonicalInspectionItemCommand,
    CreateCanonicalModelCommand,
    CreateCanonicalModelPartCommand,
    CreateCanonicalRowBindingRevisionCommand,
    CreateCanonicalSupplierCommand,
    CreateMasterSpecRevisionCommand,
    MasterConfigAuthorizationError,
    MasterConfigCommandService,
    ReviewCanonicalRowBindingRevisionCommand,
    ReviewMasterSpecRevisionCommand,
    SetInspectionItemDispositionCommand,
    SupersedeCanonicalRowBindingRevisionCommand,
    SupersedeMasterSpecRevisionCommand,
)
from app.domain.audit import AuditChange
from app.domain.identity import SYSTEM_ACTOR, Actor, ActorKind, Role
from app.domain.long_format import (
    CanonicalRowBindingKey,
    MeasurementMode,
    SamplePolicy,
)
from app.domain.master_config import (
    CanonicalInspectionItem,
    CanonicalModel,
    CanonicalModelPart,
    CanonicalRowBindingRevision,
    CanonicalSupplier,
    ConfigurationRevisionStatus,
    InspectionItemDisposition,
    MasterSpecRevision,
)
from app.infrastructure.audit import AuditLog, AuditRepository
from app.infrastructure.database import Base, Database
from app.infrastructure.mapping_templates import (
    MappingTemplateHistoryRow,
    MappingTemplateRevisionRow,
)
from app.infrastructure.master_config import (
    CanonicalModelRow,
    CanonicalRowBindingHistoryRow,
    CanonicalRowBindingRevisionRow,
    CanonicalRowBindingSupersessionRow,
    CanonicalSupplierRow,
    ImmutableMasterConfigRevisionError,
    MasterConfigEffectivePeriodError,
    MasterConfigNotFoundError,
    MasterConfigPayloadIntegrityError,
    MasterConfigRepository,
    MasterConfigScopeError,
    MasterSpecHistoryRow,
    MasterSpecRevisionRow,
    MasterSpecSupersessionRow,
    PersistedCanonicalRowBindingRevision,
    PersistedMasterSpecRevision,
    StaleMasterConfigWriteError,
)
from app.infrastructure.schema import SCHEMA_HEAD_REVISION

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 8, 15, 9, 0, tzinfo=UTC)

DUAL_OWNER = Actor(
    actor_id="synthetic-owner",
    kind=ActorKind.LOCAL_OWNER,
    roles=frozenset({Role.ADMIN, Role.REVIEWER, Role.VIEWER}),
)
REVIEWER = Actor(
    actor_id="synthetic-reviewer",
    kind=ActorKind.LOCAL_OWNER,
    roles=frozenset({Role.REVIEWER}),
)
ADMIN = Actor(
    actor_id="synthetic-admin",
    kind=ActorKind.LOCAL_OWNER,
    roles=frozenset({Role.ADMIN}),
)
VIEWER = Actor(
    actor_id="synthetic-viewer",
    kind=ActorKind.LOCAL_OWNER,
    roles=frozenset({Role.VIEWER}),
)


class _Clock:
    def __init__(self) -> None:
        self._value = NOW

    def __call__(self) -> datetime:
        value = self._value
        self._value += timedelta(minutes=1)
        return value


def _config(database_url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "backend" / "migrations"))
    config.set_main_option("prepend_sys_path", str(ROOT / "backend"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


@pytest.fixture
def database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Database]:
    monkeypatch.delenv("MASS_PRODUCTION_QUALITY_VALIDATION_DATABASE_URL", raising=False)
    path = tmp_path / "master-config.sqlite3"
    url = f"sqlite+pysqlite:///{path.as_posix()}"
    command.upgrade(_config(url), "head")
    value = Database(url)
    try:
        yield value
    finally:
        value.dispose()


def _service(
    database: Database,
    *,
    audit_repository: AuditRepository | None = None,
) -> MasterConfigCommandService:
    return MasterConfigCommandService(
        database,
        audit_repository=audit_repository,
        clock=_Clock(),
    )


class _FailingAuditRepository(AuditRepository):
    def append(self, session: Session, change: AuditChange) -> AuditLog:
        raise RuntimeError("synthetic audit append failure")


def _prior_migration_snapshot(connection: Connection) -> dict[str, tuple[object, ...]]:
    statements = {
        "audit": ("SELECT id, action, reason FROM audit_log WHERE id = 'master-prior-audit'"),
        "mapping": (
            "SELECT id, status, template_payload, payload_sha256, row_version "
            "FROM mapping_template_revisions WHERE id = 'master-prior-revision'"
        ),
        "source": (
            "SELECT id, parse_status, content_sha256, row_version "
            "FROM source_files WHERE id = 'master-prior-source'"
        ),
        "sheet": (
            "SELECT id, snapshot_sha256, row_version "
            "FROM source_sheets WHERE id = 'master-prior-sheet'"
        ),
        "job": (
            "SELECT id, status, candidate_snapshot_sha256, row_version "
            "FROM ingestion_jobs WHERE id = 'master-prior-job'"
        ),
        "lot": (
            "SELECT id, data_status, identifier_evidence_sha256, row_version "
            "FROM oqc_lots WHERE id = 'master-prior-lot'"
        ),
        "result": (
            "SELECT id, data_status, system_judgment_status, spec_evaluation_status, "
            "source_evidence_sha256, candidate_snapshot_sha256, row_version "
            "FROM inspection_results WHERE id = 'master-prior-result'"
        ),
        "measurement": (
            "SELECT id, data_status, evidence_sha256, raw_numeric_value, "
            "standardized_value, unit_conversion_status, row_version "
            "FROM measurements WHERE id = 'master-prior-measurement'"
        ),
    }
    result: dict[str, tuple[object, ...]] = {}
    for name, statement in statements.items():
        row = connection.execute(text(statement)).one()
        result[name] = tuple(row)
    return result


def _insert_mapping_scope(
    database: Database,
    *,
    project_key: str,
    supplier_scope: str = "source-supplier-alpha",
    template_id: str = "synthetic-oqc-layout",
    revision: int = 1,
) -> tuple[str, str]:
    history_id = str(uuid4())
    revision_id = str(uuid4())
    with database.session() as session, session.begin():
        session.add(
            MappingTemplateHistoryRow(
                id=history_id,
                project_key=project_key,
                supplier_scope=supplier_scope,
                template_id=template_id,
                row_version=3,
                created_at=NOW,
            )
        )
        session.add(
            MappingTemplateRevisionRow(
                id=revision_id,
                history_id=history_id,
                revision_number=revision,
                schema_version="2",
                status=ConfigurationRevisionStatus.APPROVED.value,
                template_payload={},
                payload_sha256="a" * 64,
                declared_effective_from=date(2026, 1, 1),
                declared_effective_to=date(2026, 12, 31),
                resolved_effective_to=None,
                reviewed_by="synthetic-reviewer",
                reviewed_at=NOW,
                approved_by="synthetic-admin",
                approved_at=NOW,
                row_version=3,
                created_at=NOW,
            )
        )
    return history_id, revision_id


def _bootstrap(
    database: Database,
    *,
    project_key: str = "project-alpha",
    disposition: InspectionItemDisposition = InspectionItemDisposition.MANAGED,
    item_key: str = "item-width",
    insert_mapping: bool = True,
) -> MasterConfigCommandService:
    service = _service(database)
    service.create_model(
        CreateCanonicalModelCommand(
            model=CanonicalModel(project_key, "model-a", "Synthetic Model A"),
            actor=DUAL_OWNER,
            reason="Create synthetic canonical model.",
        )
    )
    service.create_supplier(
        CreateCanonicalSupplierCommand(
            supplier=CanonicalSupplier(project_key, "supplier-a", "Synthetic Supplier A"),
            actor=DUAL_OWNER,
            reason="Create independent synthetic supplier axis.",
        )
    )
    service.create_model_part(
        CreateCanonicalModelPartCommand(
            model_part=CanonicalModelPart(
                project_key,
                "model-a",
                "model-a:part-top",
                "Synthetic Top Part",
            ),
            actor=DUAL_OWNER,
            reason="Attach synthetic model-part to model.",
        )
    )
    service.create_inspection_item(
        CreateCanonicalInspectionItemCommand(
            item=CanonicalInspectionItem(
                project_key,
                "model-a:part-top",
                item_key,
                "Synthetic Width",
            ),
            actor=DUAL_OWNER,
            reason="Create synthetic item as an explicit candidate.",
        )
    )
    if disposition != InspectionItemDisposition.CANDIDATE:
        service.set_item_disposition(
            SetInspectionItemDispositionCommand(
                project_key=project_key,
                item_key=item_key,
                disposition=disposition,
                expected_row_version=1,
                actor=DUAL_OWNER,
                reason="Make an explicit synthetic item-scope decision.",
            )
        )
    if insert_mapping:
        _insert_mapping_scope(database, project_key=project_key)
    return service


def _spec(
    *,
    project_key: str = "project-alpha",
    item_key: str = "item-width",
    revision: int = 1,
    effective_from: date = date(2026, 1, 1),
    effective_to: date | None = date(2026, 12, 31),
    target: Decimal | None = Decimal("10.0000"),
    lsl: Decimal | None = Decimal("9.7500"),
    usl: Decimal | None = Decimal("10.2500"),
) -> MasterSpecRevision:
    return MasterSpecRevision(
        project_key=project_key,
        canonical_item_key=item_key,
        revision=revision,
        status=ConfigurationRevisionStatus.DRAFT,
        target=target,
        lsl=lsl,
        usl=usl,
        unit="mm",
        external_spec_revision=f"SYN-REV-{revision}",
        effective_from=effective_from,
        effective_to=effective_to,
        change_reason=f"Synthetic numeric revision {revision}.",
        source_reference=f"synthetic://approved-source/{revision}",
    )


def _binding(
    *,
    project_key: str = "project-alpha",
    item_key: str = "item-width",
    revision: int = 1,
    effective_from: date = date(2026, 1, 1),
    effective_to: date | None = date(2026, 12, 31),
) -> CanonicalRowBindingRevision:
    return CanonicalRowBindingRevision(
        key=CanonicalRowBindingKey(
            project_key=project_key,
            supplier_scope="source-supplier-alpha",
            template_id="synthetic-oqc-layout",
            template_revision=1,
            row_key="inspection-row-width",
        ),
        binding_revision=revision,
        status=ConfigurationRevisionStatus.DRAFT,
        effective_from=effective_from,
        effective_to=effective_to,
        source_model_values=("MODEL-A", "MODEL A"),
        canonical_model_key="model-a",
        canonical_supplier_key="supplier-a",
        canonical_model_part_key="model-a:part-top",
        canonical_item_key=item_key,
        sample_policy=SamplePolicy.AT_LEAST_ONE,
        measurement_mode=MeasurementMode.NUMERIC,
        change_reason=f"Synthetic exact row binding revision {revision}.",
        source_reference=f"synthetic://binding-evidence/{revision}",
    )


def _approve_spec(
    service: MasterConfigCommandService,
    spec: MasterSpecRevision,
    *,
    expected_history_row_version: int = 0,
) -> tuple[
    PersistedMasterSpecRevision,
    PersistedMasterSpecRevision,
    PersistedMasterSpecRevision,
]:
    created = service.create_master_spec_revision(
        CreateMasterSpecRevisionCommand(
            spec=spec,
            expected_history_row_version=expected_history_row_version,
            actor=DUAL_OWNER,
            reason="Create synthetic numeric Master Spec evidence.",
        )
    )
    reviewed = service.review_master_spec_revision(
        ReviewMasterSpecRevisionCommand(
            project_key=spec.project_key,
            canonical_item_key=spec.canonical_item_key,
            revision=spec.revision,
            expected_history_row_version=created.history_row_version,
            expected_revision_row_version=created.revision_row_version,
            actor=DUAL_OWNER,
            reason="Review synthetic numeric Master Spec evidence.",
        )
    )
    approved = service.approve_master_spec_revision(
        ApproveMasterSpecRevisionCommand(
            project_key=spec.project_key,
            canonical_item_key=spec.canonical_item_key,
            revision=spec.revision,
            expected_history_row_version=reviewed.history_row_version,
            expected_revision_row_version=reviewed.revision_row_version,
            actor=DUAL_OWNER,
            reason="Approve synthetic numeric Master Spec evidence.",
        )
    )
    return created, reviewed, approved


def _approve_binding(
    service: MasterConfigCommandService,
    binding: CanonicalRowBindingRevision,
    *,
    expected_history_row_version: int = 0,
) -> tuple[
    PersistedCanonicalRowBindingRevision,
    PersistedCanonicalRowBindingRevision,
    PersistedCanonicalRowBindingRevision,
]:
    created = service.create_row_binding_revision(
        CreateCanonicalRowBindingRevisionCommand(
            binding=binding,
            expected_history_row_version=expected_history_row_version,
            actor=DUAL_OWNER,
            reason="Create exact synthetic row-binding evidence.",
        )
    )
    reviewed = service.review_row_binding_revision(
        ReviewCanonicalRowBindingRevisionCommand(
            key=binding.key,
            binding_revision=binding.binding_revision,
            expected_history_row_version=created.history_row_version,
            expected_revision_row_version=created.revision_row_version,
            actor=DUAL_OWNER,
            reason="Review exact synthetic row-binding evidence.",
        )
    )
    approved = service.approve_row_binding_revision(
        ApproveCanonicalRowBindingRevisionCommand(
            key=binding.key,
            binding_revision=binding.binding_revision,
            expected_history_row_version=reviewed.history_row_version,
            expected_revision_row_version=reviewed.revision_row_version,
            actor=DUAL_OWNER,
            reason="Approve exact synthetic row-binding evidence.",
        )
    )
    return created, reviewed, approved


@pytest.mark.required_test_id("DQ-P1-MASTER-001")
def test_project_isolated_hierarchy_and_independent_supplier_axis(database: Database) -> None:
    _bootstrap(database, project_key="project-alpha")
    _bootstrap(database, project_key="project-beta")
    repository = MasterConfigRepository()

    with database.session() as session:
        alpha_model = repository.get_model(
            session,
            project_key="project-alpha",
            model_key="model-a",
        )
        beta_model = repository.get_model(
            session,
            project_key="project-beta",
            model_key="model-a",
        )
        alpha_part = repository.get_model_part(
            session,
            project_key="project-alpha",
            model_part_key="model-a:part-top",
        )
        alpha_item = repository.get_inspection_item(
            session,
            project_key="project-alpha",
            item_key="item-width",
        )
        alpha_supplier = repository.get_supplier(
            session,
            project_key="project-alpha",
            supplier_key="supplier-a",
        )
        assert alpha_model.row_id != beta_model.row_id
        assert alpha_part.model_id == alpha_model.row_id
        assert alpha_item.model_part_id == alpha_part.row_id
        assert alpha_supplier.row_id not in {alpha_model.row_id, alpha_part.row_id}
        assert session.scalar(select(func.count()).select_from(CanonicalModelRow)) == 2
        assert session.scalar(select(func.count()).select_from(CanonicalSupplierRow)) == 2

    service = _service(database)
    with pytest.raises(MasterConfigScopeError, match="already exists"):
        service.create_model_part(
            CreateCanonicalModelPartCommand(
                model_part=CanonicalModelPart(
                    "project-alpha",
                    "model-a",
                    "model-a:part-top",
                    "Duplicate Part",
                ),
                actor=DUAL_OWNER,
                reason="Synthetic duplicate must fail closed.",
            )
        )
    with pytest.raises(MasterConfigNotFoundError, match="project"):
        service.create_model_part(
            CreateCanonicalModelPartCommand(
                model_part=CanonicalModelPart(
                    "project-alpha",
                    "model-only-in-beta",
                    "project-alpha:invalid-part",
                    "Invalid Cross Project Part",
                ),
                actor=DUAL_OWNER,
                reason="Cross-project hierarchy must fail closed.",
            )
        )


@pytest.mark.required_test_id("DQ-P1-MASTER-002")
def test_item_disposition_is_explicit_cas_protected_and_audited(database: Database) -> None:
    service = _bootstrap(
        database,
        disposition=InspectionItemDisposition.CANDIDATE,
    )
    repository = MasterConfigRepository()
    with database.session() as session:
        candidate = repository.get_inspection_item(
            session,
            project_key="project-alpha",
            item_key="item-width",
        )
    assert candidate.item.disposition == InspectionItemDisposition.CANDIDATE
    assert candidate.row_version == 1

    managed = service.set_item_disposition(
        SetInspectionItemDispositionCommand(
            project_key="project-alpha",
            item_key="item-width",
            disposition=InspectionItemDisposition.MANAGED,
            expected_row_version=1,
            actor=DUAL_OWNER,
            reason="Explicitly manage this synthetic item.",
        )
    )
    assert managed.item.disposition == InspectionItemDisposition.MANAGED
    assert managed.row_version == 2

    with pytest.raises(StaleMasterConfigWriteError, match="stale"):
        service.set_item_disposition(
            SetInspectionItemDispositionCommand(
                project_key="project-alpha",
                item_key="item-width",
                disposition=InspectionItemDisposition.EXCLUDED,
                expected_row_version=1,
                actor=DUAL_OWNER,
                reason="A stale decision must not overwrite the managed state.",
            )
        )

    with database.session() as session:
        stored = repository.get_inspection_item(
            session,
            project_key="project-alpha",
            item_key="item-width",
        )
        audits = list(
            session.scalars(
                select(AuditLog)
                .where(AuditLog.action == "INSPECTION_ITEM_DISPOSITION_SET")
                .order_by(AuditLog.occurred_at)
            ).all()
        )
    assert stored.item.disposition == InspectionItemDisposition.MANAGED
    assert stored.row_version == 2
    assert len(audits) == 1
    assert audits[0].before_state is not None
    assert audits[0].before_state["disposition"] == "CANDIDATE"
    assert audits[0].after_state is not None
    assert audits[0].after_state["disposition"] == "MANAGED"
    assert audits[0].after_state["row_version"] == 2


@pytest.mark.required_test_id("DQ-P1-MASTER-003")
def test_numeric_master_spec_requires_separate_review_and_admin_approval(
    database: Database,
) -> None:
    service = _bootstrap(database)
    spec = _spec()
    created = service.create_master_spec_revision(
        CreateMasterSpecRevisionCommand(
            spec=spec,
            expected_history_row_version=0,
            actor=DUAL_OWNER,
            reason="Create synthetic spec draft.",
        )
    )
    assert created.spec.status == ConfigurationRevisionStatus.DRAFT
    assert created.spec.reviewed_by is None
    assert created.spec.approved_by is None

    with pytest.raises(MasterConfigAuthorizationError, match="ADMIN"):
        service.approve_master_spec_revision(
            ApproveMasterSpecRevisionCommand(
                project_key="project-alpha",
                canonical_item_key="item-width",
                revision=1,
                expected_history_row_version=1,
                expected_revision_row_version=1,
                actor=REVIEWER,
                reason="Reviewer cannot perform final approval.",
            )
        )
    with pytest.raises(MasterConfigAuthorizationError, match="REVIEWER"):
        service.review_master_spec_revision(
            ReviewMasterSpecRevisionCommand(
                project_key="project-alpha",
                canonical_item_key="item-width",
                revision=1,
                expected_history_row_version=1,
                expected_revision_row_version=1,
                actor=ADMIN,
                reason="Admin-only context cannot impersonate Reviewer.",
            )
        )

    reviewed = service.review_master_spec_revision(
        ReviewMasterSpecRevisionCommand(
            project_key="project-alpha",
            canonical_item_key="item-width",
            revision=1,
            expected_history_row_version=1,
            expected_revision_row_version=1,
            actor=DUAL_OWNER,
            reason="The local owner makes a distinct review decision.",
        )
    )
    approved = service.approve_master_spec_revision(
        ApproveMasterSpecRevisionCommand(
            project_key="project-alpha",
            canonical_item_key="item-width",
            revision=1,
            expected_history_row_version=reviewed.history_row_version,
            expected_revision_row_version=reviewed.revision_row_version,
            actor=DUAL_OWNER,
            reason="The same local owner makes a separate admin approval.",
        )
    )
    assert reviewed.spec.status == ConfigurationRevisionStatus.REVIEWED
    assert approved.spec.status == ConfigurationRevisionStatus.APPROVED
    assert approved.spec.reviewed_by == DUAL_OWNER.actor_id
    assert approved.spec.approved_by == DUAL_OWNER.actor_id
    assert approved.spec.reviewed_at is not None
    assert approved.spec.approved_at is not None
    assert approved.spec.reviewed_at < approved.spec.approved_at

    with database.session() as session:
        decisions = list(
            session.scalars(
                select(AuditLog)
                .where(
                    AuditLog.action.in_(
                        ("MASTER_SPEC_REVISION_REVIEWED", "MASTER_SPEC_REVISION_APPROVED")
                    )
                )
                .order_by(AuditLog.occurred_at)
            ).all()
        )
    assert [item.action for item in decisions] == [
        "MASTER_SPEC_REVISION_REVIEWED",
        "MASTER_SPEC_REVISION_APPROVED",
    ]
    assert all(item.actor_id == DUAL_OWNER.actor_id for item in decisions)


@pytest.mark.required_test_id("DQ-P1-MASTER-004")
def test_master_spec_revision_is_immutable_exact_and_resolved_by_supersession(
    database: Database,
) -> None:
    service = _bootstrap(database)
    _, _, predecessor = _approve_spec(service, _spec())
    successor_spec = _spec(
        revision=2,
        effective_from=date(2026, 7, 1),
        target=Decimal("10.0500"),
        lsl=Decimal("9.8000"),
        usl=Decimal("10.3000"),
    )
    successor_created = service.create_master_spec_revision(
        CreateMasterSpecRevisionCommand(
            spec=successor_spec,
            expected_history_row_version=predecessor.history_row_version,
            actor=DUAL_OWNER,
            reason="Append immutable synthetic successor revision.",
        )
    )
    successor_reviewed = service.review_master_spec_revision(
        ReviewMasterSpecRevisionCommand(
            project_key="project-alpha",
            canonical_item_key="item-width",
            revision=2,
            expected_history_row_version=successor_created.history_row_version,
            expected_revision_row_version=successor_created.revision_row_version,
            actor=DUAL_OWNER,
            reason="Review immutable synthetic successor revision.",
        )
    )
    result = service.supersede_master_spec_revision(
        SupersedeMasterSpecRevisionCommand(
            project_key="project-alpha",
            canonical_item_key="item-width",
            predecessor_revision=1,
            successor_revision=2,
            expected_history_row_version=successor_reviewed.history_row_version,
            expected_predecessor_row_version=predecessor.revision_row_version,
            expected_successor_row_version=successor_reviewed.revision_row_version,
            actor=DUAL_OWNER,
            reason="Resolve predecessor at the day before successor effectivity.",
        )
    )
    assert result.predecessor.spec.effective_to == date(2026, 12, 31)
    assert result.predecessor.resolved_effective_to == date(2026, 6, 30)
    assert result.successor.spec.status == ConfigurationRevisionStatus.APPROVED
    assert result.successor.spec.target == Decimal("10.0500")

    with database.session() as session:
        rows = list(
            session.scalars(
                select(MasterSpecRevisionRow).order_by(MasterSpecRevisionRow.revision_number)
            ).all()
        )
        assert rows[0].spec_payload["target"] == "10.0000"
        assert rows[0].spec_payload["lsl"] == "9.7500"
        assert rows[0].spec_payload["usl"] == "10.2500"
        assert rows[0].declared_effective_to == date(2026, 12, 31)
        assert rows[0].resolved_effective_to == date(2026, 6, 30)
        assert all(isinstance(rows[0].spec_payload[name], str) for name in ("target", "lsl", "usl"))

    with pytest.raises(ImmutableMasterConfigRevisionError, match="overwritten"):
        service.create_master_spec_revision(
            CreateMasterSpecRevisionCommand(
                spec=_spec(revision=1),
                expected_history_row_version=result.successor.history_row_version,
                actor=DUAL_OWNER,
                reason="Revision overwrite must fail closed.",
            )
        )
    with pytest.raises(ValueError, match="at least one limit"):
        _spec(lsl=None, usl=None, target=Decimal("10.0"))
    with pytest.raises(ValueError, match="finite Decimal"):
        _spec(target=Decimal("NaN"))
    with pytest.raises(ValueError, match="lsl must not exceed"):
        _spec(lsl=Decimal("11"), usl=Decimal("10"), target=None)
    one_sided = _spec(lsl=None, usl=Decimal("10.25"), target=Decimal("10.00"))
    assert one_sided.lsl is None

    repository = MasterConfigRepository()
    with database.session() as session, session.begin():
        row = session.scalar(
            select(MasterSpecRevisionRow).where(MasterSpecRevisionRow.revision_number == 2)
        )
        assert row is not None
        row.spec_payload = {**row.spec_payload, "target": "99.0000"}
    with (
        database.session() as session,
        pytest.raises(MasterConfigPayloadIntegrityError, match="digest"),
    ):
        repository.get_master_spec(
            session,
            project_key="project-alpha",
            canonical_item_key="item-width",
            revision=2,
        )


@pytest.mark.required_test_id("DQ-P1-MASTER-005")
def test_roles_projects_mapping_scope_and_supplier_foreign_keys_fail_closed(
    database: Database,
) -> None:
    service = _bootstrap(database)
    service.create_supplier(
        CreateCanonicalSupplierCommand(
            supplier=CanonicalSupplier(
                "project-alpha",
                "supplier-b",
                "Synthetic Supplier B",
            ),
            actor=DUAL_OWNER,
            reason="Create a second supplier solely for mismatch testing.",
        )
    )
    with pytest.raises(MasterConfigAuthorizationError):
        service.create_master_spec_revision(
            CreateMasterSpecRevisionCommand(
                spec=_spec(),
                expected_history_row_version=0,
                actor=VIEWER,
                reason="Viewer cannot create Master configuration.",
            )
        )
    with pytest.raises(MasterConfigAuthorizationError):
        service.create_master_spec_revision(
            CreateMasterSpecRevisionCommand(
                spec=_spec(),
                expected_history_row_version=0,
                actor=SYSTEM_ACTOR,
                reason="System principal cannot impersonate a human reviewer.",
            )
        )

    _, other_revision_id = _insert_mapping_scope(
        database,
        project_key="project-alpha",
        template_id="other-layout",
    )
    with database.session() as session:
        mapping_history = session.scalar(
            select(MappingTemplateHistoryRow).where(
                MappingTemplateHistoryRow.project_key == "project-alpha",
                MappingTemplateHistoryRow.template_id == "synthetic-oqc-layout",
            )
        )
        supplier = session.scalar(
            select(CanonicalSupplierRow).where(
                CanonicalSupplierRow.project_key == "project-alpha",
                CanonicalSupplierRow.supplier_key == "supplier-a",
            )
        )
        assert mapping_history is not None and supplier is not None
        mapping_history_id = mapping_history.id
        supplier_id = supplier.id

    with pytest.raises(IntegrityError), database.session() as session, session.begin():
        session.add(
            CanonicalRowBindingHistoryRow(
                project_key="project-alpha",
                supplier_scope="source-supplier-alpha",
                template_id="synthetic-oqc-layout",
                template_revision=1,
                row_key="invalid-mixed-mapping-scope",
                canonical_supplier_id=supplier_id,
                mapping_history_id=mapping_history_id,
                mapping_revision_id=other_revision_id,
                row_version=0,
                created_at=NOW,
            )
        )
        session.flush()

    _, _, approved_binding = _approve_binding(service, _binding())
    with database.session() as session:
        history = session.get(CanonicalRowBindingHistoryRow, approved_binding.history_id)
        source_row = session.get(CanonicalRowBindingRevisionRow, approved_binding.revision_id)
        supplier_b = session.scalar(
            select(CanonicalSupplierRow).where(
                CanonicalSupplierRow.project_key == "project-alpha",
                CanonicalSupplierRow.supplier_key == "supplier-b",
            )
        )
        assert history is not None and source_row is not None and supplier_b is not None
        supplier_b_id = supplier_b.id
        copied = {
            "project_key": source_row.project_key,
            "history_id": source_row.history_id,
            "status": ConfigurationRevisionStatus.DRAFT.value,
            "binding_payload": source_row.binding_payload,
            "payload_sha256": source_row.payload_sha256,
            "canonical_model_id": source_row.canonical_model_id,
            "canonical_model_part_id": source_row.canonical_model_part_id,
            "canonical_item_id": source_row.canonical_item_id,
            "declared_effective_from": source_row.declared_effective_from,
            "declared_effective_to": source_row.declared_effective_to,
        }
    with pytest.raises(IntegrityError), database.session() as session, session.begin():
        session.add(
            CanonicalRowBindingRevisionRow(
                id=str(uuid4()),
                binding_revision=99,
                canonical_supplier_id=supplier_b_id,
                resolved_effective_to=None,
                reviewed_by=None,
                reviewed_at=None,
                approved_by=None,
                approved_at=None,
                row_version=1,
                created_at=NOW,
                **copied,
            )
        )
        session.flush()

    wrong_project = replace(
        _binding(project_key="project-alpha"),
        key=replace(_binding().key, project_key="project-beta"),
    )
    with pytest.raises(MasterConfigScopeError, match="Mapping Template"):
        service.create_row_binding_revision(
            CreateCanonicalRowBindingRevisionCommand(
                binding=wrong_project,
                expected_history_row_version=0,
                actor=DUAL_OWNER,
                reason="Cross-project binding must not resolve through project-alpha.",
            )
        )


@pytest.mark.required_test_id("DQ-P1-MASTER-006")
def test_persistent_row_binding_workflow_materializes_existing_catalog_contract(
    database: Database,
) -> None:
    service = _bootstrap(database)
    repository = MasterConfigRepository()
    binding = _binding()
    created = service.create_row_binding_revision(
        CreateCanonicalRowBindingRevisionCommand(
            binding=binding,
            expected_history_row_version=0,
            actor=DUAL_OWNER,
            reason="Create persistent exact row binding.",
        )
    )
    with database.session() as session:
        assert (
            repository.load_row_binding_catalog(
                session,
                project_key="project-alpha",
                as_of=date(2026, 4, 1),
            ).records
            == ()
        )

    reviewed = service.review_row_binding_revision(
        ReviewCanonicalRowBindingRevisionCommand(
            key=binding.key,
            binding_revision=1,
            expected_history_row_version=created.history_row_version,
            expected_revision_row_version=created.revision_row_version,
            actor=DUAL_OWNER,
            reason="Review persistent exact row binding.",
        )
    )
    with database.session() as session:
        assert (
            repository.load_row_binding_catalog(
                session,
                project_key="project-alpha",
                as_of=date(2026, 4, 1),
            ).records
            == ()
        )

    approved = service.approve_row_binding_revision(
        ApproveCanonicalRowBindingRevisionCommand(
            key=binding.key,
            binding_revision=1,
            expected_history_row_version=reviewed.history_row_version,
            expected_revision_row_version=reviewed.revision_row_version,
            actor=DUAL_OWNER,
            reason="Approve persistent exact row binding.",
        )
    )
    assert approved.binding.status == ConfigurationRevisionStatus.APPROVED
    assert approved.binding.reviewed_by == DUAL_OWNER.actor_id
    assert approved.binding.approved_by == DUAL_OWNER.actor_id

    restarted = Database(str(database.engine.url))
    try:
        with restarted.session() as session:
            catalog = repository.load_row_binding_catalog(
                session,
                project_key="project-alpha",
                as_of=date(2026, 4, 1),
            )
            replay = repository.get_row_binding(
                session,
                key=binding.key,
                binding_revision=1,
            )
            decisions = list(
                session.scalars(
                    select(AuditLog)
                    .where(AuditLog.action.like("CANONICAL_ROW_BINDING_REVISION_%"))
                    .order_by(AuditLog.occurred_at)
                ).all()
            )
    finally:
        restarted.dispose()

    matches = catalog.find(binding.key)
    assert len(matches) == 1
    materialized = matches[0]
    assert materialized.key == binding.key
    assert materialized.binding_revision == 1
    assert materialized.source_model_values == ("MODEL-A", "MODEL A")
    assert materialized.canonical_model_key == "model-a"
    assert materialized.canonical_supplier_key == "supplier-a"
    assert materialized.canonical_model_part_key == "model-a:part-top"
    assert materialized.canonical_item_key == "item-width"
    assert materialized.sample_policy == SamplePolicy.AT_LEAST_ONE
    assert materialized.measurement_mode == MeasurementMode.NUMERIC
    assert replay.binding == approved.binding
    assert replay.payload_sha256 == approved.payload_sha256
    assert [item.action for item in decisions] == [
        "CANONICAL_ROW_BINDING_REVISION_CREATED",
        "CANONICAL_ROW_BINDING_REVISION_REVIEWED",
        "CANONICAL_ROW_BINDING_REVISION_APPROVED",
    ]


@pytest.mark.required_test_id("DQ-P1-MASTER-007")
def test_catalog_selection_is_approved_effective_as_of_and_deterministic(
    database: Database,
) -> None:
    service = _bootstrap(database)
    repository = MasterConfigRepository()
    _, _, master_one = _approve_spec(service, _spec())
    master_two = _spec(
        revision=2,
        effective_from=date(2026, 7, 1),
        target=Decimal("10.1000"),
        lsl=Decimal("9.8500"),
        usl=Decimal("10.3500"),
    )
    master_two_created = service.create_master_spec_revision(
        CreateMasterSpecRevisionCommand(
            spec=master_two,
            expected_history_row_version=master_one.history_row_version,
            actor=DUAL_OWNER,
            reason="Create next as-of Master revision.",
        )
    )
    master_two_reviewed = service.review_master_spec_revision(
        ReviewMasterSpecRevisionCommand(
            project_key="project-alpha",
            canonical_item_key="item-width",
            revision=2,
            expected_history_row_version=master_two_created.history_row_version,
            expected_revision_row_version=master_two_created.revision_row_version,
            actor=DUAL_OWNER,
            reason="Review next as-of Master revision.",
        )
    )
    with pytest.raises(MasterConfigEffectivePeriodError, match="overlap"):
        service.approve_master_spec_revision(
            ApproveMasterSpecRevisionCommand(
                project_key="project-alpha",
                canonical_item_key="item-width",
                revision=2,
                expected_history_row_version=master_two_reviewed.history_row_version,
                expected_revision_row_version=master_two_reviewed.revision_row_version,
                actor=DUAL_OWNER,
                reason="Overlapping direct approval must fail closed.",
            )
        )
    master_supersession = service.supersede_master_spec_revision(
        SupersedeMasterSpecRevisionCommand(
            project_key="project-alpha",
            canonical_item_key="item-width",
            predecessor_revision=1,
            successor_revision=2,
            expected_history_row_version=master_two_reviewed.history_row_version,
            expected_predecessor_row_version=master_one.revision_row_version,
            expected_successor_row_version=master_two_reviewed.revision_row_version,
            actor=DUAL_OWNER,
            reason="Resolve deterministic Master as-of selection.",
        )
    )

    _, _, binding_one = _approve_binding(service, _binding())
    binding_two = _binding(revision=2, effective_from=date(2026, 7, 1))
    binding_two_created = service.create_row_binding_revision(
        CreateCanonicalRowBindingRevisionCommand(
            binding=binding_two,
            expected_history_row_version=binding_one.history_row_version,
            actor=DUAL_OWNER,
            reason="Create next as-of binding revision.",
        )
    )
    binding_two_reviewed = service.review_row_binding_revision(
        ReviewCanonicalRowBindingRevisionCommand(
            key=binding_two.key,
            binding_revision=2,
            expected_history_row_version=binding_two_created.history_row_version,
            expected_revision_row_version=binding_two_created.revision_row_version,
            actor=DUAL_OWNER,
            reason="Review next as-of binding revision.",
        )
    )
    with pytest.raises(MasterConfigEffectivePeriodError, match="overlap"):
        service.approve_row_binding_revision(
            ApproveCanonicalRowBindingRevisionCommand(
                key=binding_two.key,
                binding_revision=2,
                expected_history_row_version=binding_two_reviewed.history_row_version,
                expected_revision_row_version=binding_two_reviewed.revision_row_version,
                actor=DUAL_OWNER,
                reason="Overlapping binding approval must fail closed.",
            )
        )
    binding_supersession = service.supersede_row_binding_revision(
        SupersedeCanonicalRowBindingRevisionCommand(
            key=binding_two.key,
            predecessor_revision=1,
            successor_revision=2,
            expected_history_row_version=binding_two_reviewed.history_row_version,
            expected_predecessor_row_version=binding_one.revision_row_version,
            expected_successor_row_version=binding_two_reviewed.revision_row_version,
            actor=DUAL_OWNER,
            reason="Resolve deterministic binding as-of selection.",
        )
    )

    with database.session() as session:
        june_master = repository.load_master_spec_catalog(
            session,
            project_key="project-alpha",
            as_of=date(2026, 6, 30),
        )
        july_master = repository.load_master_spec_catalog(
            session,
            project_key="project-alpha",
            as_of=date(2026, 7, 1),
        )
        june_binding = repository.load_row_binding_catalog(
            session,
            project_key="project-alpha",
            as_of=date(2026, 6, 30),
        )
        june_binding_again = repository.load_row_binding_catalog(
            session,
            project_key="project-alpha",
            as_of=date(2026, 6, 30),
        )
        july_binding = repository.load_row_binding_catalog(
            session,
            project_key="project-alpha",
            as_of=date(2026, 7, 1),
        )
        before_all = repository.load_row_binding_catalog(
            session,
            project_key="project-alpha",
            as_of=date(2025, 12, 31),
        )

    june_master_record = june_master.find("item-width")
    july_master_record = july_master.find("item-width")
    assert june_master_record is not None and july_master_record is not None
    assert june_master_record.spec.revision == 1
    assert june_master_record.spec.effective_to == date(2026, 12, 31)
    assert june_master_record.resolved_effective_to == date(2026, 6, 30)
    assert june_master_record.effective_end == date(2026, 6, 30)
    assert july_master_record.spec.revision == 2
    assert master_supersession.predecessor.spec.effective_to == date(2026, 12, 31)

    june_matches = june_binding.find(binding_one.binding.key)
    july_matches = july_binding.find(binding_one.binding.key)
    assert len(june_matches) == len(july_matches) == 1
    assert june_matches[0].binding_revision == 1
    assert june_matches[0].effective_to == date(2026, 6, 30)
    assert july_matches[0].binding_revision == 2
    assert july_matches[0].effective_to == date(2026, 12, 31)
    assert june_binding.records[0].binding.effective_to == date(2026, 12, 31)
    assert june_binding.records[0].resolved_effective_to == date(2026, 6, 30)
    assert binding_supersession.predecessor.binding.effective_to == date(2026, 12, 31)
    assert june_binding.catalog_revision == june_binding_again.catalog_revision
    assert before_all.records == ()


@pytest.mark.required_test_id("DQ-P1-MASTER-008")
def test_0004_fresh_upgrade_prior_rows_downgrade_reupgrade_and_metadata_parity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MASS_PRODUCTION_QUALITY_VALIDATION_DATABASE_URL", raising=False)
    monkeypatch.chdir(tmp_path)

    fresh_path = tmp_path / "master-fresh.sqlite3"
    fresh_url = f"sqlite+pysqlite:///{fresh_path.as_posix()}"
    fresh_config = _config(fresh_url)
    command.upgrade(fresh_config, "head")
    fresh_engine = create_engine(fresh_url)
    try:
        fresh_tables = set(inspect(fresh_engine).get_table_names())
        assert {
            "canonical_models",
            "canonical_model_parts",
            "canonical_inspection_items",
            "canonical_suppliers",
            "master_spec_histories",
            "master_spec_revisions",
            "master_spec_supersessions",
            "canonical_row_binding_histories",
            "canonical_row_binding_revisions",
            "canonical_row_binding_supersessions",
        }.issubset(fresh_tables)
        with fresh_engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == SCHEMA_HEAD_REVISION
            )
            differences = compare_metadata(
                MigrationContext.configure(connection, opts={"compare_type": True}),
                Base.metadata,
            )
        assert differences == []
    finally:
        fresh_engine.dispose()

    upgrade_path = tmp_path / "master-upgrade.sqlite3"
    upgrade_url = f"sqlite+pysqlite:///{upgrade_path.as_posix()}"
    upgrade_config = _config(upgrade_url)
    command.upgrade(upgrade_config, "0003")
    prior_candidate = {
        "provenance": {
            "receipt": {
                "project_key": "project-prior",
                "receipt_id": "receipt-prior",
                "content_sha256": "c" * 64,
            },
            "supplier_scope": "supplier-prior",
            "template_id": "layout-prior",
            "template_schema_version": "1",
            "template_revision": 1,
            "template_effective_from": "2026-01-01",
            "template_effective_to": "2026-12-31",
        }
    }
    prior_candidate_json = json.dumps(
        prior_candidate, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    prior_candidate_sha256 = hashlib.sha256(prior_candidate_json.encode("utf-8")).hexdigest()
    engine = create_engine(upgrade_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO audit_log "
                    "(id, occurred_at, actor_id, actor_kind, actor_roles, action, "
                    "target_type, target_id, before_state, after_state, reason, "
                    "requirement_id, source_reference) VALUES "
                    "('master-prior-audit', '2026-08-15 09:00:00', 'synthetic-owner', "
                    "'LOCAL_OWNER', '[\"ADMIN\"]', 'PRIOR_0003_AUDIT', "
                    "'migration_evidence', NULL, NULL, NULL, 'Preserve prior Audit.', "
                    "'GOV-008', NULL)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO mapping_template_histories "
                    "(id, project_key, supplier_scope, template_id, row_version, created_at) "
                    "VALUES ('master-prior-history', 'project-prior', 'supplier-prior', "
                    "'layout-prior', 3, '2026-08-15 09:00:00')"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO mapping_template_revisions "
                    "(id, history_id, revision, schema_version, status, template_payload, "
                    "payload_sha256, declared_effective_from, declared_effective_to, "
                    "resolved_effective_to, reviewed_by, reviewed_at, approved_by, "
                    "approved_at, row_version, created_at) VALUES "
                    "('master-prior-revision', 'master-prior-history', 1, '1', "
                    "'APPROVED', '{}', :digest, '2026-01-01', '2026-12-31', NULL, "
                    "'reviewer', '2026-08-15 09:01:00', 'admin', "
                    "'2026-08-15 09:02:00', 3, '2026-08-15 09:00:00')"
                ),
                {"digest": "b" * 64},
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
                    "('master-prior-source', 'project-prior', 'receipt-prior', 'blob-prior', "
                    ":digest, '2026-08-15 09:00:00', 'prior.xlsx', '[\"MODEL-P\"]', "
                    "'[\"LOT-P\"]', 'application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet', 'application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet', '.xlsx', 100, 'SCANNED', 'prior.xlsx', 100, "
                    ":digest, :digest, 'scan-v1', 10, 0, 'NOT_PRESENT', 'RAW_AND_DISPLAY', "
                    "0, '[]', 1, '2026-08-15 09:00:00')"
                ),
                {"digest": "c" * 64},
            )
            connection.execute(
                text(
                    "INSERT INTO source_sheets "
                    "(id, project_key, source_file_id, position, sheet_name, sheet_kind, "
                    "visibility, used_range, estimated_cells, merged_ranges, "
                    "hidden_row_ranges, hidden_column_ranges, formula_count, "
                    "protection_metadata, image_metadata, issues, scan_snapshot, "
                    "snapshot_sha256, row_version) VALUES "
                    "('master-prior-sheet', 'project-prior', 'master-prior-source', 0, "
                    "'OQC', 'WORKSHEET', 'VISIBLE', 'A1:H10', 80, '[]', '[]', '[]', 0, "
                    "'{}', '[]', '[]', '{}', :digest, 1)"
                ),
                {"digest": "d" * 64},
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
                    "('master-prior-job', 'project-prior', 'master-prior-source', :content, "
                    "'master-prior-revision', :mapping, 'catalog-prior', :binding, "
                    "'loader-v1', 'scan-v1', :idempotency, :materialization, 1, NULL, NULL, "
                    "'COMPLETED_PENDING', '2026-08-15 09:00:00', "
                    "'2026-08-15 09:03:00', 1, 1, 1, 0, NULL, NULL, '[]', :candidate_json, "
                    ":candidate, 2)"
                ),
                {
                    "content": "c" * 64,
                    "mapping": "b" * 64,
                    "binding": "e" * 64,
                    "idempotency": "f" * 64,
                    "materialization": "1" * 64,
                    "candidate_json": prior_candidate_json,
                    "candidate": prior_candidate_sha256,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO oqc_lots "
                    "(id, project_key, ingestion_job_id, source_file_id, lot_ordinal, "
                    "canonical_model_key, canonical_model_part_key, canonical_supplier_key, "
                    "source_lot_text, inspection_date, received_at, identifier_evidence, "
                    "identifier_evidence_sha256, data_status, hold_reasons, row_version) "
                    "VALUES ('master-prior-lot', 'project-prior', 'master-prior-job', "
                    "'master-prior-source', 1, 'model-prior', 'part-prior', "
                    "'supplier-prior', 'LOT-PRIOR', '2026-08-14', '2026-08-15 09:00:00', "
                    "'[]', :digest, 'PENDING', '[]', 1)"
                ),
                {"digest": "3" * 64},
            )
            connection.execute(
                text(
                    "INSERT INTO inspection_results "
                    "(id, project_key, oqc_lot_id, source_file_id, source_sheet_id, "
                    "source_row_key, binding_revision, canonical_model_part_key, "
                    "canonical_item_key, supplier_judgment_text, system_judgment, "
                    "system_judgment_status, spec_evaluation_status, source_evidence, "
                    "source_evidence_sha256, binding_snapshot, binding_snapshot_sha256, "
                    "candidate_snapshot_sha256, data_status, hold_reasons, row_version) "
                    "VALUES ('master-prior-result', 'project-prior', 'master-prior-lot', "
                    "'master-prior-source', 'master-prior-sheet', 'row-prior', 1, "
                    "'part-prior', 'item-prior', NULL, NULL, 'NOT_EVALUATED', "
                    "'NOT_EVALUATED', '{}', :source_digest, NULL, NULL, :candidate_digest, "
                    "'PENDING', '[]', 1)"
                ),
                {
                    "source_digest": "4" * 64,
                    "candidate_digest": prior_candidate_sha256,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO measurements "
                    "(id, project_key, inspection_result_id, source_file_id, source_sheet_id, "
                    "sample_ordinal, source_cell, raw_value_tag, raw_value_text, "
                    "raw_numeric_value, raw_qualitative_value, evidence, evidence_sha256, "
                    "formula_flag, standardized_value, unit_conversion_status, data_status, "
                    "hold_reasons, superseded_measurement_id, row_version) VALUES "
                    "('master-prior-measurement', 'project-prior', 'master-prior-result', "
                    "'master-prior-source', 'master-prior-sheet', 1, 'H8', 'DECIMAL', "
                    "'10.0000', '10.0000', NULL, '{}', :digest, 0, NULL, "
                    "'NOT_CONFIGURED', 'PENDING', '[]', NULL, 1)"
                ),
                {"digest": "5" * 64},
            )
        with engine.connect() as connection:
            baseline_snapshot = _prior_migration_snapshot(connection)
        assert baseline_snapshot["lot"] == (
            "master-prior-lot",
            "PENDING",
            "3" * 64,
            1,
        )
        assert baseline_snapshot["result"] == (
            "master-prior-result",
            "PENDING",
            "NOT_EVALUATED",
            "NOT_EVALUATED",
            "4" * 64,
            prior_candidate_sha256,
            1,
        )
        assert baseline_snapshot["measurement"] == (
            "master-prior-measurement",
            "PENDING",
            "5" * 64,
            "10.0000",
            None,
            "NOT_CONFIGURED",
            1,
        )
    finally:
        engine.dispose()

    command.upgrade(upgrade_config, "head")
    engine = create_engine(upgrade_url)
    try:
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == SCHEMA_HEAD_REVISION
            )
            proof_json, proof_sha256 = connection.execute(
                text(
                    "SELECT applied_mapping_proof, applied_mapping_proof_sha256 "
                    "FROM ingestion_jobs WHERE id='master-prior-job'"
                )
            ).one()
            proof = json.loads(proof_json) if isinstance(proof_json, str) else proof_json
            assert isinstance(proof, dict)
            assert proof["candidate_snapshot_sha256"] == prior_candidate_sha256
            assert proof["mapping_template_revision_id"] == "master-prior-revision"
            assert (
                hashlib.sha256(
                    json.dumps(
                        proof,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest()
                == proof_sha256
            )
            assert _prior_migration_snapshot(connection) == baseline_snapshot
    finally:
        engine.dispose()

    command.downgrade(upgrade_config, "0003")
    engine = create_engine(upgrade_url)
    try:
        tables = set(inspect(engine).get_table_names())
        assert "canonical_models" not in tables
        assert "master_spec_revisions" not in tables
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0003"
            assert _prior_migration_snapshot(connection) == baseline_snapshot
    finally:
        engine.dispose()

    command.upgrade(upgrade_config, "head")
    engine = create_engine(upgrade_url)
    try:
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == SCHEMA_HEAD_REVISION
            )
            assert (
                compare_metadata(
                    MigrationContext.configure(connection, opts={"compare_type": True}),
                    Base.metadata,
                )
                == []
            )
            assert _prior_migration_snapshot(connection) == baseline_snapshot
    finally:
        engine.dispose()
    assert SCHEMA_HEAD_REVISION == "0008"


@pytest.mark.required_test_id("DQ-P1-MASTER-009")
def test_audit_failure_rolls_back_mutation_and_stale_concurrency_is_rejected(
    database: Database,
) -> None:
    failing = _service(database, audit_repository=_FailingAuditRepository())
    with pytest.raises(RuntimeError, match="audit append failure"):
        failing.create_model(
            CreateCanonicalModelCommand(
                model=CanonicalModel(
                    "rollback-project",
                    "rollback-model",
                    "Rolled Back Model",
                ),
                actor=DUAL_OWNER,
                reason="The paired Audit append intentionally fails.",
            )
        )
    with database.session() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(CanonicalModelRow)
                .where(CanonicalModelRow.project_key == "rollback-project")
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.target_id == "rollback-project:rollback-model")
            )
            == 0
        )

    service = _bootstrap(
        database,
        project_key="project-alpha",
        disposition=InspectionItemDisposition.CANDIDATE,
    )
    with pytest.raises(RuntimeError, match="audit append failure"):
        failing.set_item_disposition(
            SetInspectionItemDispositionCommand(
                project_key="project-alpha",
                item_key="item-width",
                disposition=InspectionItemDisposition.MANAGED,
                expected_row_version=1,
                actor=DUAL_OWNER,
                reason="Rollback item mutation with failed Audit append.",
            )
        )
    repository = MasterConfigRepository()
    with database.session() as session:
        after_rollback = repository.get_inspection_item(
            session,
            project_key="project-alpha",
            item_key="item-width",
        )
    assert after_rollback.item.disposition == InspectionItemDisposition.CANDIDATE
    assert after_rollback.row_version == 1

    winner = service.set_item_disposition(
        SetInspectionItemDispositionCommand(
            project_key="project-alpha",
            item_key="item-width",
            disposition=InspectionItemDisposition.MANAGED,
            expected_row_version=1,
            actor=DUAL_OWNER,
            reason="First CAS writer wins.",
        )
    )
    assert winner.row_version == 2
    second_service = _service(database)
    with pytest.raises(StaleMasterConfigWriteError, match="stale"):
        second_service.set_item_disposition(
            SetInspectionItemDispositionCommand(
                project_key="project-alpha",
                item_key="item-width",
                disposition=InspectionItemDisposition.EXCLUDED,
                expected_row_version=1,
                actor=DUAL_OWNER,
                reason="Second stale CAS writer must lose.",
            )
        )
    with database.session() as session:
        final = repository.get_inspection_item(
            session,
            project_key="project-alpha",
            item_key="item-width",
        )
        disposition_audits = session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(AuditLog.action == "INSPECTION_ITEM_DISPOSITION_SET")
        )
    assert final.item.disposition == InspectionItemDisposition.MANAGED
    assert final.row_version == 2
    assert disposition_audits == 1

    _, _, master_one = _approve_spec(service, _spec())
    master_two = _spec(revision=2, effective_from=date(2026, 7, 1))
    master_two_created = service.create_master_spec_revision(
        CreateMasterSpecRevisionCommand(
            spec=master_two,
            expected_history_row_version=master_one.history_row_version,
            actor=DUAL_OWNER,
            reason="Prepare Master successor for atomic rollback evidence.",
        )
    )
    master_two_reviewed = service.review_master_spec_revision(
        ReviewMasterSpecRevisionCommand(
            project_key="project-alpha",
            canonical_item_key="item-width",
            revision=2,
            expected_history_row_version=master_two_created.history_row_version,
            expected_revision_row_version=master_two_created.revision_row_version,
            actor=DUAL_OWNER,
            reason="Review Master successor for atomic rollback evidence.",
        )
    )
    with database.session() as session:
        master_audit_count = session.scalar(select(func.count()).select_from(AuditLog))
    with pytest.raises(RuntimeError, match="audit append failure"):
        failing.supersede_master_spec_revision(
            SupersedeMasterSpecRevisionCommand(
                project_key="project-alpha",
                canonical_item_key="item-width",
                predecessor_revision=1,
                successor_revision=2,
                expected_history_row_version=master_two_reviewed.history_row_version,
                expected_predecessor_row_version=master_one.revision_row_version,
                expected_successor_row_version=master_two_reviewed.revision_row_version,
                actor=DUAL_OWNER,
                reason="All Master supersession writes must roll back with Audit failure.",
            )
        )
    with database.session() as session:
        master_history = session.scalar(select(MasterSpecHistoryRow))
        master_rows = list(
            session.scalars(
                select(MasterSpecRevisionRow).order_by(MasterSpecRevisionRow.revision_number)
            ).all()
        )
        assert master_history is not None
        assert master_history.row_version == master_two_reviewed.history_row_version
        assert [
            (row.status, row.row_version, row.resolved_effective_to) for row in master_rows
        ] == [
            ("APPROVED", master_one.revision_row_version, None),
            ("REVIEWED", master_two_reviewed.revision_row_version, None),
        ]
        assert session.scalar(select(func.count()).select_from(MasterSpecSupersessionRow)) == 0
        assert session.scalar(select(func.count()).select_from(AuditLog)) == master_audit_count

    _, _, binding_one = _approve_binding(service, _binding())
    binding_two = _binding(revision=2, effective_from=date(2026, 7, 1))
    binding_two_created = service.create_row_binding_revision(
        CreateCanonicalRowBindingRevisionCommand(
            binding=binding_two,
            expected_history_row_version=binding_one.history_row_version,
            actor=DUAL_OWNER,
            reason="Prepare Binding successor for atomic rollback evidence.",
        )
    )
    binding_two_reviewed = service.review_row_binding_revision(
        ReviewCanonicalRowBindingRevisionCommand(
            key=binding_two.key,
            binding_revision=2,
            expected_history_row_version=binding_two_created.history_row_version,
            expected_revision_row_version=binding_two_created.revision_row_version,
            actor=DUAL_OWNER,
            reason="Review Binding successor for atomic rollback evidence.",
        )
    )
    with database.session() as session:
        binding_audit_count = session.scalar(select(func.count()).select_from(AuditLog))
    with pytest.raises(RuntimeError, match="audit append failure"):
        failing.supersede_row_binding_revision(
            SupersedeCanonicalRowBindingRevisionCommand(
                key=binding_two.key,
                predecessor_revision=1,
                successor_revision=2,
                expected_history_row_version=binding_two_reviewed.history_row_version,
                expected_predecessor_row_version=binding_one.revision_row_version,
                expected_successor_row_version=binding_two_reviewed.revision_row_version,
                actor=DUAL_OWNER,
                reason="All Binding supersession writes must roll back with Audit failure.",
            )
        )
    with database.session() as session:
        binding_history = session.scalar(select(CanonicalRowBindingHistoryRow))
        binding_rows = list(
            session.scalars(
                select(CanonicalRowBindingRevisionRow).order_by(
                    CanonicalRowBindingRevisionRow.binding_revision
                )
            ).all()
        )
        assert binding_history is not None
        assert binding_history.row_version == binding_two_reviewed.history_row_version
        assert [
            (row.status, row.row_version, row.resolved_effective_to) for row in binding_rows
        ] == [
            ("APPROVED", binding_one.revision_row_version, None),
            ("REVIEWED", binding_two_reviewed.revision_row_version, None),
        ]
        assert (
            session.scalar(select(func.count()).select_from(CanonicalRowBindingSupersessionRow))
            == 0
        )
        assert session.scalar(select(func.count()).select_from(AuditLog)) == binding_audit_count


@pytest.mark.required_test_id("DQ-P1-MASTER-010")
def test_framework_never_auto_promotes_copies_supplier_spec_or_calculates(
    database: Database,
) -> None:
    service = _bootstrap(
        database,
        disposition=InspectionItemDisposition.CANDIDATE,
    )
    repository = MasterConfigRepository()
    spec = _spec()
    spec_created = service.create_master_spec_revision(
        CreateMasterSpecRevisionCommand(
            spec=spec,
            expected_history_row_version=0,
            actor=DUAL_OWNER,
            reason="Create explicit synthetic Master input, never supplier-derived.",
        )
    )
    spec_reviewed = service.review_master_spec_revision(
        ReviewMasterSpecRevisionCommand(
            project_key="project-alpha",
            canonical_item_key="item-width",
            revision=1,
            expected_history_row_version=spec_created.history_row_version,
            expected_revision_row_version=spec_created.revision_row_version,
            actor=DUAL_OWNER,
            reason="Review explicit synthetic Master input.",
        )
    )
    with pytest.raises(MasterConfigScopeError, match="MANAGED"):
        service.approve_master_spec_revision(
            ApproveMasterSpecRevisionCommand(
                project_key="project-alpha",
                canonical_item_key="item-width",
                revision=1,
                expected_history_row_version=spec_reviewed.history_row_version,
                expected_revision_row_version=spec_reviewed.revision_row_version,
                actor=DUAL_OWNER,
                reason="Candidate cannot enter approved Master selection.",
            )
        )

    binding = _binding()
    binding_created = service.create_row_binding_revision(
        CreateCanonicalRowBindingRevisionCommand(
            binding=binding,
            expected_history_row_version=0,
            actor=DUAL_OWNER,
            reason="Persist exact source identity without official promotion.",
        )
    )
    binding_reviewed = service.review_row_binding_revision(
        ReviewCanonicalRowBindingRevisionCommand(
            key=binding.key,
            binding_revision=1,
            expected_history_row_version=binding_created.history_row_version,
            expected_revision_row_version=binding_created.revision_row_version,
            actor=DUAL_OWNER,
            reason="Review exact source identity.",
        )
    )
    with pytest.raises(MasterConfigScopeError, match="CANDIDATE"):
        service.approve_row_binding_revision(
            ApproveCanonicalRowBindingRevisionCommand(
                key=binding.key,
                binding_revision=1,
                expected_history_row_version=binding_reviewed.history_row_version,
                expected_revision_row_version=binding_reviewed.revision_row_version,
                actor=DUAL_OWNER,
                reason="Candidate binding approval must fail closed.",
            )
        )

    excluded = service.set_item_disposition(
        SetInspectionItemDispositionCommand(
            project_key="project-alpha",
            item_key="item-width",
            disposition=InspectionItemDisposition.EXCLUDED,
            expected_row_version=1,
            actor=DUAL_OWNER,
            reason="Explicitly exclude while retaining exact source binding.",
        )
    )
    approved_binding = service.approve_row_binding_revision(
        ApproveCanonicalRowBindingRevisionCommand(
            key=binding.key,
            binding_revision=1,
            expected_history_row_version=binding_reviewed.history_row_version,
            expected_revision_row_version=binding_reviewed.revision_row_version,
            actor=DUAL_OWNER,
            reason="Approve source identity for an explicitly excluded item.",
        )
    )
    assert excluded.item.disposition == InspectionItemDisposition.EXCLUDED
    assert approved_binding.binding.canonical_item_key == "item-width"

    with database.session() as session:
        master_catalog = repository.load_master_spec_catalog(
            session,
            project_key="project-alpha",
            as_of=date(2026, 6, 1),
        )
        binding_catalog = repository.load_row_binding_catalog(
            session,
            project_key="project-alpha",
            as_of=date(2026, 6, 1),
        )
        master_rows = list(session.scalars(select(MasterSpecRevisionRow)).all())
        result_count = session.scalar(text("SELECT COUNT(*) FROM inspection_results"))
        measurement_count = session.scalar(text("SELECT COUNT(*) FROM measurements"))
    assert master_catalog.revisions == ()
    assert len(binding_catalog.find(binding.key)) == 1
    assert len(master_rows) == 1
    assert master_rows[0].status == ConfigurationRevisionStatus.REVIEWED.value
    assert master_rows[0].spec_payload["source_reference"] == spec.source_reference
    assert "supplier" not in master_rows[0].spec_payload
    assert "VALID" not in json.dumps(master_rows[0].spec_payload, sort_keys=True)
    assert result_count == 0
    assert measurement_count == 0
    master_columns = {
        column["name"] for column in inspect(database.engine).get_columns("master_spec_revisions")
    }
    assert not {"supplier_id", "supplier_key", "supplier_spec"} & master_columns
