"""Trusted-Actor commands for persistent canonical and Master configuration."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.domain.audit import AuditChange
from app.domain.identity import Actor, ActorKind, Role
from app.domain.long_format import CanonicalRowBindingKey
from app.domain.master_config import (
    CanonicalInspectionItem,
    CanonicalModel,
    CanonicalModelPart,
    CanonicalRowBindingRevision,
    CanonicalSupplier,
    InspectionItemDisposition,
    MasterSpecRevision,
)
from app.infrastructure.audit import AuditRepository
from app.infrastructure.database import Database
from app.infrastructure.master_config import (
    CanonicalRowBindingWorkflowMutation,
    MasterConfigRepository,
    MasterSpecWorkflowMutation,
    PersistedCanonicalInspectionItem,
    PersistedCanonicalModel,
    PersistedCanonicalModelPart,
    PersistedCanonicalRowBindingRevision,
    PersistedCanonicalRowBindingSupersession,
    PersistedCanonicalSupplier,
    PersistedMasterSpecRevision,
    PersistedMasterSpecSupersession,
)


class MasterConfigAuthorizationError(PermissionError):
    pass


class UntrustedMasterConfigActorError(MasterConfigAuthorizationError):
    pass


@dataclass(frozen=True, slots=True)
class CreateCanonicalModelCommand:
    model: CanonicalModel
    actor: Actor
    reason: str
    source_reference: str | None = None
    requirement_id: str | None = "CFG-001"


@dataclass(frozen=True, slots=True)
class CreateCanonicalSupplierCommand:
    supplier: CanonicalSupplier
    actor: Actor
    reason: str
    source_reference: str | None = None
    requirement_id: str | None = "CFG-016"


@dataclass(frozen=True, slots=True)
class CreateCanonicalModelPartCommand:
    model_part: CanonicalModelPart
    actor: Actor
    reason: str
    source_reference: str | None = None
    requirement_id: str | None = "CFG-001"


@dataclass(frozen=True, slots=True)
class CreateCanonicalInspectionItemCommand:
    item: CanonicalInspectionItem
    actor: Actor
    reason: str
    source_reference: str | None = None
    requirement_id: str | None = "CFG-016"


@dataclass(frozen=True, slots=True)
class SetInspectionItemDispositionCommand:
    project_key: str
    item_key: str
    disposition: InspectionItemDisposition
    expected_row_version: int
    actor: Actor
    reason: str
    source_reference: str | None = None
    requirement_id: str | None = "CFG-017"


@dataclass(frozen=True, slots=True)
class CreateMasterSpecRevisionCommand:
    spec: MasterSpecRevision
    expected_history_row_version: int
    actor: Actor
    reason: str
    requirement_id: str | None = "CFG-001"


@dataclass(frozen=True, slots=True)
class ReviewMasterSpecRevisionCommand:
    project_key: str
    canonical_item_key: str
    revision: int
    expected_history_row_version: int
    expected_revision_row_version: int
    actor: Actor
    reason: str
    source_reference: str | None = None
    requirement_id: str | None = "GOV-008"


@dataclass(frozen=True, slots=True)
class ApproveMasterSpecRevisionCommand(ReviewMasterSpecRevisionCommand):
    pass


@dataclass(frozen=True, slots=True)
class SupersedeMasterSpecRevisionCommand:
    project_key: str
    canonical_item_key: str
    predecessor_revision: int
    successor_revision: int
    expected_history_row_version: int
    expected_predecessor_row_version: int
    expected_successor_row_version: int
    actor: Actor
    reason: str
    source_reference: str | None = None
    requirement_id: str | None = "GOV-008"


@dataclass(frozen=True, slots=True)
class CreateCanonicalRowBindingRevisionCommand:
    binding: CanonicalRowBindingRevision
    expected_history_row_version: int
    actor: Actor
    reason: str
    requirement_id: str | None = "ING-023"


@dataclass(frozen=True, slots=True)
class ReviewCanonicalRowBindingRevisionCommand:
    key: CanonicalRowBindingKey
    binding_revision: int
    expected_history_row_version: int
    expected_revision_row_version: int
    actor: Actor
    reason: str
    source_reference: str | None = None
    requirement_id: str | None = "GOV-008"


@dataclass(frozen=True, slots=True)
class ApproveCanonicalRowBindingRevisionCommand(ReviewCanonicalRowBindingRevisionCommand):
    pass


@dataclass(frozen=True, slots=True)
class SupersedeCanonicalRowBindingRevisionCommand:
    key: CanonicalRowBindingKey
    predecessor_revision: int
    successor_revision: int
    expected_history_row_version: int
    expected_predecessor_row_version: int
    expected_successor_row_version: int
    actor: Actor
    reason: str
    source_reference: str | None = None
    requirement_id: str | None = "GOV-008"


class MasterConfigCommandService:
    """Executes each mutation and its append-only Audit record atomically."""

    def __init__(
        self,
        database: Database,
        *,
        repository: MasterConfigRepository | None = None,
        audit_repository: AuditRepository | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._database = database
        self._repository = repository or MasterConfigRepository()
        self._audit = audit_repository or AuditRepository()
        self._clock = clock or _utc_now

    def create_model(self, command: CreateCanonicalModelCommand) -> PersistedCanonicalModel:
        _require_role(command.actor, Role.ADMIN)
        _validate_reason(command.reason)
        occurred_at = self._occurred_at()
        with self._database.session() as session, session.begin():
            created = self._repository.create_model(
                session,
                command.model,
                created_at=occurred_at,
            )
            self._append_creation_audit(
                session,
                command.actor,
                action="CANONICAL_MODEL_CREATED",
                target_type="canonical_model",
                target_id=f"{command.model.project_key}:{command.model.model_key}",
                after_state=_model_state(created),
                reason=command.reason,
                requirement_id=command.requirement_id,
                source_reference=command.source_reference,
            )
        return created

    def create_supplier(
        self,
        command: CreateCanonicalSupplierCommand,
    ) -> PersistedCanonicalSupplier:
        _require_role(command.actor, Role.ADMIN)
        _validate_reason(command.reason)
        occurred_at = self._occurred_at()
        with self._database.session() as session, session.begin():
            created = self._repository.create_supplier(
                session,
                command.supplier,
                created_at=occurred_at,
            )
            self._append_creation_audit(
                session,
                command.actor,
                action="CANONICAL_SUPPLIER_CREATED",
                target_type="canonical_supplier",
                target_id=f"{command.supplier.project_key}:{command.supplier.supplier_key}",
                after_state=_supplier_state(created),
                reason=command.reason,
                requirement_id=command.requirement_id,
                source_reference=command.source_reference,
            )
        return created

    def create_model_part(
        self,
        command: CreateCanonicalModelPartCommand,
    ) -> PersistedCanonicalModelPart:
        _require_role(command.actor, Role.ADMIN)
        _validate_reason(command.reason)
        occurred_at = self._occurred_at()
        with self._database.session() as session, session.begin():
            created = self._repository.create_model_part(
                session,
                command.model_part,
                created_at=occurred_at,
            )
            self._append_creation_audit(
                session,
                command.actor,
                action="CANONICAL_MODEL_PART_CREATED",
                target_type="canonical_model_part",
                target_id=(f"{command.model_part.project_key}:{command.model_part.model_part_key}"),
                after_state=_model_part_state(created),
                reason=command.reason,
                requirement_id=command.requirement_id,
                source_reference=command.source_reference,
            )
        return created

    def create_inspection_item(
        self,
        command: CreateCanonicalInspectionItemCommand,
    ) -> PersistedCanonicalInspectionItem:
        _require_role(command.actor, Role.ADMIN)
        _validate_reason(command.reason)
        occurred_at = self._occurred_at()
        with self._database.session() as session, session.begin():
            created = self._repository.create_inspection_item(
                session,
                command.item,
                created_at=occurred_at,
            )
            self._append_creation_audit(
                session,
                command.actor,
                action="CANONICAL_INSPECTION_ITEM_CREATED",
                target_type="canonical_inspection_item",
                target_id=f"{command.item.project_key}:{command.item.item_key}",
                after_state=_item_state(created),
                reason=command.reason,
                requirement_id=command.requirement_id,
                source_reference=command.source_reference,
            )
        return created

    def set_item_disposition(
        self,
        command: SetInspectionItemDispositionCommand,
    ) -> PersistedCanonicalInspectionItem:
        _require_role(command.actor, Role.ADMIN)
        _validate_reason(command.reason)
        _validate_expected_version(command.expected_row_version)
        with self._database.session() as session, session.begin():
            mutation = self._repository.set_item_disposition(
                session,
                project_key=command.project_key,
                item_key=command.item_key,
                disposition=command.disposition,
                expected_row_version=command.expected_row_version,
            )
            self._append_mutation_audit(
                session,
                command.actor,
                action="INSPECTION_ITEM_DISPOSITION_SET",
                target_type="canonical_inspection_item",
                target_id=f"{command.project_key}:{command.item_key}",
                before_state=_item_state(mutation.before),
                after_state=_item_state(mutation.after),
                reason=command.reason,
                requirement_id=command.requirement_id,
                source_reference=command.source_reference,
            )
        return mutation.after

    def create_master_spec_revision(
        self,
        command: CreateMasterSpecRevisionCommand,
    ) -> PersistedMasterSpecRevision:
        _require_any_role(command.actor, Role.REVIEWER, Role.ADMIN)
        _validate_reason(command.reason)
        _validate_expected_version(command.expected_history_row_version, allow_zero=True)
        occurred_at = self._occurred_at()
        with self._database.session() as session, session.begin():
            created = self._repository.create_master_spec_draft(
                session,
                command.spec,
                expected_history_row_version=command.expected_history_row_version,
                created_at=occurred_at,
            )
            self._append_creation_audit(
                session,
                command.actor,
                action="MASTER_SPEC_REVISION_CREATED",
                target_type="master_spec_revision",
                target_id=_master_target(created),
                after_state=_master_spec_state(created),
                reason=command.reason,
                requirement_id=command.requirement_id,
                source_reference=command.spec.source_reference,
            )
        return created

    def review_master_spec_revision(
        self,
        command: ReviewMasterSpecRevisionCommand,
    ) -> PersistedMasterSpecRevision:
        _require_role(command.actor, Role.REVIEWER)
        _validate_revision_command(command)
        occurred_at = self._occurred_at()
        with self._database.session() as session, session.begin():
            mutation = self._repository.review_master_spec(
                session,
                project_key=command.project_key,
                canonical_item_key=command.canonical_item_key,
                revision=command.revision,
                expected_history_row_version=command.expected_history_row_version,
                expected_revision_row_version=command.expected_revision_row_version,
                reviewed_by=command.actor.actor_id,
                reviewed_at=occurred_at,
            )
            self._append_master_workflow_audit(
                session,
                mutation,
                command.actor,
                action="MASTER_SPEC_REVISION_REVIEWED",
                reason=command.reason,
                requirement_id=command.requirement_id,
                source_reference=command.source_reference,
            )
        return mutation.after

    def approve_master_spec_revision(
        self,
        command: ApproveMasterSpecRevisionCommand,
    ) -> PersistedMasterSpecRevision:
        _require_role(command.actor, Role.ADMIN)
        _validate_revision_command(command)
        occurred_at = self._occurred_at()
        with self._database.session() as session, session.begin():
            mutation = self._repository.approve_master_spec(
                session,
                project_key=command.project_key,
                canonical_item_key=command.canonical_item_key,
                revision=command.revision,
                expected_history_row_version=command.expected_history_row_version,
                expected_revision_row_version=command.expected_revision_row_version,
                approved_by=command.actor.actor_id,
                approved_at=occurred_at,
            )
            self._append_master_workflow_audit(
                session,
                mutation,
                command.actor,
                action="MASTER_SPEC_REVISION_APPROVED",
                reason=command.reason,
                requirement_id=command.requirement_id,
                source_reference=command.source_reference,
            )
        return mutation.after

    def supersede_master_spec_revision(
        self,
        command: SupersedeMasterSpecRevisionCommand,
    ) -> PersistedMasterSpecSupersession:
        _require_role(command.actor, Role.ADMIN)
        _validate_reason(command.reason)
        _validate_scope(command.project_key, command.canonical_item_key)
        _validate_positive(command.predecessor_revision, "predecessor_revision")
        _validate_positive(command.successor_revision, "successor_revision")
        for value in (
            command.expected_history_row_version,
            command.expected_predecessor_row_version,
            command.expected_successor_row_version,
        ):
            _validate_expected_version(value)
        occurred_at = self._occurred_at()
        with self._database.session() as session, session.begin():
            result = self._repository.supersede_master_spec(
                session,
                project_key=command.project_key,
                canonical_item_key=command.canonical_item_key,
                predecessor_revision=command.predecessor_revision,
                successor_revision=command.successor_revision,
                expected_history_row_version=command.expected_history_row_version,
                expected_predecessor_row_version=command.expected_predecessor_row_version,
                expected_successor_row_version=command.expected_successor_row_version,
                decided_by=command.actor.actor_id,
                decided_at=occurred_at,
                reason=command.reason,
            )
            self._append_mutation_audit(
                session,
                command.actor,
                action="MASTER_SPEC_REVISION_SUPERSEDED",
                target_type="master_spec_revision",
                target_id=_master_target(result.successor),
                before_state={
                    "predecessor": _master_spec_state(result.predecessor, before_supersession=True),
                    "successor": _master_spec_state(result.successor, before_approval=True),
                },
                after_state={
                    "predecessor": _master_spec_state(result.predecessor),
                    "successor": _master_spec_state(result.successor),
                },
                reason=command.reason,
                requirement_id=command.requirement_id,
                source_reference=command.source_reference,
            )
        return result

    def create_row_binding_revision(
        self,
        command: CreateCanonicalRowBindingRevisionCommand,
    ) -> PersistedCanonicalRowBindingRevision:
        _require_any_role(command.actor, Role.REVIEWER, Role.ADMIN)
        _validate_reason(command.reason)
        _validate_expected_version(command.expected_history_row_version, allow_zero=True)
        occurred_at = self._occurred_at()
        with self._database.session() as session, session.begin():
            created = self._repository.create_row_binding_draft(
                session,
                command.binding,
                expected_history_row_version=command.expected_history_row_version,
                created_at=occurred_at,
            )
            self._append_creation_audit(
                session,
                command.actor,
                action="CANONICAL_ROW_BINDING_REVISION_CREATED",
                target_type="canonical_row_binding_revision",
                target_id=_binding_target(created),
                after_state=_binding_state(created),
                reason=command.reason,
                requirement_id=command.requirement_id,
                source_reference=command.binding.source_reference,
            )
        return created

    def review_row_binding_revision(
        self,
        command: ReviewCanonicalRowBindingRevisionCommand,
    ) -> PersistedCanonicalRowBindingRevision:
        _require_role(command.actor, Role.REVIEWER)
        _validate_binding_revision_command(command)
        occurred_at = self._occurred_at()
        with self._database.session() as session, session.begin():
            mutation = self._repository.review_row_binding(
                session,
                key=command.key,
                binding_revision=command.binding_revision,
                expected_history_row_version=command.expected_history_row_version,
                expected_revision_row_version=command.expected_revision_row_version,
                reviewed_by=command.actor.actor_id,
                reviewed_at=occurred_at,
            )
            self._append_binding_workflow_audit(
                session,
                mutation,
                command.actor,
                action="CANONICAL_ROW_BINDING_REVISION_REVIEWED",
                reason=command.reason,
                requirement_id=command.requirement_id,
                source_reference=command.source_reference,
            )
        return mutation.after

    def approve_row_binding_revision(
        self,
        command: ApproveCanonicalRowBindingRevisionCommand,
    ) -> PersistedCanonicalRowBindingRevision:
        _require_role(command.actor, Role.ADMIN)
        _validate_binding_revision_command(command)
        occurred_at = self._occurred_at()
        with self._database.session() as session, session.begin():
            mutation = self._repository.approve_row_binding(
                session,
                key=command.key,
                binding_revision=command.binding_revision,
                expected_history_row_version=command.expected_history_row_version,
                expected_revision_row_version=command.expected_revision_row_version,
                approved_by=command.actor.actor_id,
                approved_at=occurred_at,
            )
            self._append_binding_workflow_audit(
                session,
                mutation,
                command.actor,
                action="CANONICAL_ROW_BINDING_REVISION_APPROVED",
                reason=command.reason,
                requirement_id=command.requirement_id,
                source_reference=command.source_reference,
            )
        return mutation.after

    def supersede_row_binding_revision(
        self,
        command: SupersedeCanonicalRowBindingRevisionCommand,
    ) -> PersistedCanonicalRowBindingSupersession:
        _require_role(command.actor, Role.ADMIN)
        _validate_reason(command.reason)
        _validate_positive(command.predecessor_revision, "predecessor_revision")
        _validate_positive(command.successor_revision, "successor_revision")
        for value in (
            command.expected_history_row_version,
            command.expected_predecessor_row_version,
            command.expected_successor_row_version,
        ):
            _validate_expected_version(value)
        occurred_at = self._occurred_at()
        with self._database.session() as session, session.begin():
            result = self._repository.supersede_row_binding(
                session,
                key=command.key,
                predecessor_revision=command.predecessor_revision,
                successor_revision=command.successor_revision,
                expected_history_row_version=command.expected_history_row_version,
                expected_predecessor_row_version=command.expected_predecessor_row_version,
                expected_successor_row_version=command.expected_successor_row_version,
                decided_by=command.actor.actor_id,
                decided_at=occurred_at,
                reason=command.reason,
            )
            self._append_mutation_audit(
                session,
                command.actor,
                action="CANONICAL_ROW_BINDING_REVISION_SUPERSEDED",
                target_type="canonical_row_binding_revision",
                target_id=_binding_target(result.successor),
                before_state={
                    "predecessor": _binding_state(result.predecessor, before_supersession=True),
                    "successor": _binding_state(result.successor, before_approval=True),
                },
                after_state={
                    "predecessor": _binding_state(result.predecessor),
                    "successor": _binding_state(result.successor),
                },
                reason=command.reason,
                requirement_id=command.requirement_id,
                source_reference=command.source_reference,
            )
        return result

    def _append_master_workflow_audit(
        self,
        session: Session,
        mutation: MasterSpecWorkflowMutation,
        actor: Actor,
        *,
        action: str,
        reason: str,
        requirement_id: str | None,
        source_reference: str | None,
    ) -> None:
        self._append_mutation_audit(
            session,
            actor,
            action=action,
            target_type="master_spec_revision",
            target_id=_master_target(mutation.after),
            before_state=_master_spec_state(mutation.before),
            after_state=_master_spec_state(mutation.after),
            reason=reason,
            requirement_id=requirement_id,
            source_reference=source_reference,
        )

    def _append_binding_workflow_audit(
        self,
        session: Session,
        mutation: CanonicalRowBindingWorkflowMutation,
        actor: Actor,
        *,
        action: str,
        reason: str,
        requirement_id: str | None,
        source_reference: str | None,
    ) -> None:
        self._append_mutation_audit(
            session,
            actor,
            action=action,
            target_type="canonical_row_binding_revision",
            target_id=_binding_target(mutation.after),
            before_state=_binding_state(mutation.before),
            after_state=_binding_state(mutation.after),
            reason=reason,
            requirement_id=requirement_id,
            source_reference=source_reference,
        )

    def _append_creation_audit(
        self,
        session: Session,
        actor: Actor,
        *,
        action: str,
        target_type: str,
        target_id: str,
        after_state: Mapping[str, object],
        reason: str,
        requirement_id: str | None,
        source_reference: str | None,
    ) -> None:
        self._append_mutation_audit(
            session,
            actor,
            action=action,
            target_type=target_type,
            target_id=target_id,
            before_state=None,
            after_state=after_state,
            reason=reason,
            requirement_id=requirement_id,
            source_reference=source_reference,
        )

    def _append_mutation_audit(
        self,
        session: Session,
        actor: Actor,
        *,
        action: str,
        target_type: str,
        target_id: str,
        before_state: Mapping[str, object] | None,
        after_state: Mapping[str, object],
        reason: str,
        requirement_id: str | None,
        source_reference: str | None,
    ) -> None:
        self._audit.append(
            session,
            AuditChange(
                actor=actor,
                action=action,
                target_type=target_type,
                target_id=target_id,
                before_state=before_state,
                after_state=after_state,
                reason=reason,
                requirement_id=requirement_id,
                source_reference=source_reference,
            ),
        )

    def _occurred_at(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError("Master Configuration command clock must return an aware datetime")
        return value.astimezone(UTC)


MasterConfigCommands = MasterConfigCommandService


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _require_role(actor: Actor, role: Role) -> None:
    if not isinstance(actor, Actor):
        raise UntrustedMasterConfigActorError("command actor must be a trusted Actor")
    if actor.kind != ActorKind.LOCAL_OWNER:
        raise MasterConfigAuthorizationError(
            "only a trusted local human Actor may change Master Configuration"
        )
    if not actor.has_role(role):
        raise MasterConfigAuthorizationError(
            f"actor lacks required Master Configuration role {role.value}"
        )


def _require_any_role(actor: Actor, *roles: Role) -> None:
    if not isinstance(actor, Actor):
        raise UntrustedMasterConfigActorError("command actor must be a trusted Actor")
    if actor.kind != ActorKind.LOCAL_OWNER:
        raise MasterConfigAuthorizationError(
            "only a trusted local human Actor may change Master Configuration"
        )
    if not any(actor.has_role(role) for role in roles):
        raise MasterConfigAuthorizationError("actor lacks a required Master Configuration role")


def _validate_reason(reason: str) -> None:
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("reason must not be blank")


def _validate_scope(*values: str) -> None:
    if any(
        not isinstance(value, str) or not value.strip() or value != value.strip()
        for value in values
    ):
        raise ValueError("configuration scope values must be exact and non-blank")


def _validate_expected_version(value: int, *, allow_zero: bool = False) -> None:
    minimum = 0 if allow_zero else 1
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"expected row_version must be at least {minimum}")


def _validate_positive(value: int, field_name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{field_name} must be positive")


def _validate_revision_command(
    command: ReviewMasterSpecRevisionCommand | ApproveMasterSpecRevisionCommand,
) -> None:
    _validate_scope(command.project_key, command.canonical_item_key)
    _validate_reason(command.reason)
    _validate_positive(command.revision, "revision")
    _validate_expected_version(command.expected_history_row_version)
    _validate_expected_version(command.expected_revision_row_version)


def _validate_binding_revision_command(
    command: (ReviewCanonicalRowBindingRevisionCommand | ApproveCanonicalRowBindingRevisionCommand),
) -> None:
    _validate_reason(command.reason)
    _validate_positive(command.binding_revision, "binding_revision")
    _validate_expected_version(command.expected_history_row_version)
    _validate_expected_version(command.expected_revision_row_version)


def _model_state(record: PersistedCanonicalModel) -> dict[str, object]:
    return {
        "project_key": record.model.project_key,
        "model_key": record.model.model_key,
        "display_name": record.model.display_name,
        "row_id": record.row_id,
        "row_version": record.row_version,
    }


def _supplier_state(record: PersistedCanonicalSupplier) -> dict[str, object]:
    return {
        "project_key": record.supplier.project_key,
        "supplier_key": record.supplier.supplier_key,
        "display_name": record.supplier.display_name,
        "row_id": record.row_id,
        "row_version": record.row_version,
    }


def _model_part_state(record: PersistedCanonicalModelPart) -> dict[str, object]:
    return {
        "project_key": record.model_part.project_key,
        "model_key": record.model_part.model_key,
        "model_part_key": record.model_part.model_part_key,
        "display_name": record.model_part.display_name,
        "row_id": record.row_id,
        "model_id": record.model_id,
        "row_version": record.row_version,
    }


def _item_state(record: PersistedCanonicalInspectionItem) -> dict[str, object]:
    return {
        "project_key": record.item.project_key,
        "model_part_key": record.item.model_part_key,
        "item_key": record.item.item_key,
        "display_name": record.item.display_name,
        "disposition": record.item.disposition.value,
        "row_id": record.row_id,
        "model_part_id": record.model_part_id,
        "row_version": record.row_version,
    }


def _master_target(record: PersistedMasterSpecRevision) -> str:
    spec = record.spec
    return f"{spec.project_key}:{spec.canonical_item_key}:{spec.revision}"


def _master_spec_state(
    record: PersistedMasterSpecRevision,
    *,
    before_supersession: bool = False,
    before_approval: bool = False,
) -> dict[str, object]:
    spec = record.spec
    status = "REVIEWED" if before_approval else spec.status.value
    approved_by = None if before_approval else spec.approved_by
    approved_at = None if before_approval else spec.approved_at
    resolved = None if before_supersession else record.resolved_effective_to
    version_offset = 1 if before_supersession or before_approval else 0
    return {
        "project_key": spec.project_key,
        "canonical_item_key": spec.canonical_item_key,
        "revision": spec.revision,
        "status": status,
        "target": _decimal_state(spec.target),
        "lsl": _decimal_state(spec.lsl),
        "usl": _decimal_state(spec.usl),
        "unit": spec.unit,
        "external_spec_revision": spec.external_spec_revision,
        "declared_effective_from": spec.effective_from.isoformat(),
        "declared_effective_to": spec.effective_to.isoformat() if spec.effective_to else None,
        "resolved_effective_to": resolved.isoformat() if resolved else None,
        "change_reason": spec.change_reason,
        "source_reference": spec.source_reference,
        "reviewed_by": spec.reviewed_by,
        "reviewed_at": spec.reviewed_at.isoformat() if spec.reviewed_at else None,
        "approved_by": approved_by,
        "approved_at": approved_at.isoformat() if approved_at else None,
        "payload_sha256": record.payload_sha256,
        "history_row_version": record.history_row_version - version_offset,
        "revision_row_version": record.revision_row_version - version_offset,
    }


def _binding_target(record: PersistedCanonicalRowBindingRevision) -> str:
    binding = record.binding
    key = binding.key
    return (
        f"{key.project_key}:{key.supplier_scope}:{key.template_id}:"
        f"{key.template_revision}:{key.row_key}:{binding.binding_revision}"
    )


def _binding_state(
    record: PersistedCanonicalRowBindingRevision,
    *,
    before_supersession: bool = False,
    before_approval: bool = False,
) -> dict[str, object]:
    binding = record.binding
    key = binding.key
    status = "REVIEWED" if before_approval else binding.status.value
    approved_by = None if before_approval else binding.approved_by
    approved_at = None if before_approval else binding.approved_at
    resolved = None if before_supersession else record.resolved_effective_to
    version_offset = 1 if before_supersession or before_approval else 0
    return {
        "project_key": key.project_key,
        "supplier_scope": key.supplier_scope,
        "template_id": key.template_id,
        "template_revision": key.template_revision,
        "row_key": key.row_key,
        "binding_revision": binding.binding_revision,
        "status": status,
        "source_model_values": list(binding.source_model_values),
        "canonical_model_key": binding.canonical_model_key,
        "canonical_supplier_key": binding.canonical_supplier_key,
        "canonical_model_part_key": binding.canonical_model_part_key,
        "canonical_item_key": binding.canonical_item_key,
        "sample_policy": binding.sample_policy.value,
        "measurement_mode": binding.measurement_mode.value,
        "declared_effective_from": binding.effective_from.isoformat(),
        "declared_effective_to": (
            binding.effective_to.isoformat() if binding.effective_to else None
        ),
        "resolved_effective_to": resolved.isoformat() if resolved else None,
        "change_reason": binding.change_reason,
        "source_reference": binding.source_reference,
        "reviewed_by": binding.reviewed_by,
        "reviewed_at": binding.reviewed_at.isoformat() if binding.reviewed_at else None,
        "approved_by": approved_by,
        "approved_at": approved_at.isoformat() if approved_at else None,
        "payload_sha256": record.payload_sha256,
        "history_row_version": record.history_row_version - version_offset,
        "revision_row_version": record.revision_row_version - version_offset,
    }


def _decimal_state(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None
