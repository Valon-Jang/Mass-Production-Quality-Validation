"""Role-checked Mapping Template commands for a trusted pre-auth Actor context."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.domain.audit import AuditChange
from app.domain.identity import Actor, ActorKind, Role
from app.domain.mapping import MappingTemplate, MappingTemplateStatus
from app.infrastructure.audit import AuditRepository
from app.infrastructure.database import Database
from app.infrastructure.mapping_templates import (
    MappingTemplateRepository,
    MappingTemplateWorkflowMutation,
    PersistedMappingTemplate,
    PersistedTemplateSupersession,
)


class MappingTemplateAuthorizationError(PermissionError):
    """The trusted actor does not hold the role required by the command."""


class UntrustedMappingTemplateActorError(MappingTemplateAuthorizationError):
    """The caller supplied raw identity data instead of a trusted Actor context."""


@dataclass(frozen=True, slots=True)
class CreateMappingTemplateRevisionCommand:
    template: MappingTemplate
    expected_history_row_version: int
    actor: Actor
    reason: str
    source_reference: str | None = None
    requirement_id: str | None = "ING-012"


@dataclass(frozen=True, slots=True)
class ReviewMappingTemplateRevisionCommand:
    project_key: str
    supplier_scope: str
    template_id: str
    revision: int
    expected_history_row_version: int
    expected_revision_row_version: int
    actor: Actor
    reason: str
    source_reference: str | None = None
    requirement_id: str | None = "GOV-008"


@dataclass(frozen=True, slots=True)
class ApproveMappingTemplateRevisionCommand:
    project_key: str
    supplier_scope: str
    template_id: str
    revision: int
    expected_history_row_version: int
    expected_revision_row_version: int
    actor: Actor
    reason: str
    source_reference: str | None = None
    requirement_id: str | None = "GOV-008"


@dataclass(frozen=True, slots=True)
class SupersedeMappingTemplateRevisionCommand:
    project_key: str
    supplier_scope: str
    template_id: str
    predecessor_revision: int
    successor_revision: int
    expected_history_row_version: int
    expected_predecessor_row_version: int
    expected_successor_row_version: int
    actor: Actor
    reason: str
    source_reference: str | None = None
    requirement_id: str | None = "GOV-008"


class MappingTemplateCommandService:
    """Runs each workflow decision and its AuditLog append in one transaction."""

    def __init__(
        self,
        database: Database,
        *,
        repository: MappingTemplateRepository | None = None,
        audit_repository: AuditRepository | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._database = database
        self._repository = repository or MappingTemplateRepository()
        self._audit = audit_repository or AuditRepository()
        self._clock = clock or _utc_now

    def create_revision(
        self,
        command: CreateMappingTemplateRevisionCommand,
    ) -> PersistedMappingTemplate:
        _require_human_role(command.actor, Role.REVIEWER, Role.ADMIN)
        _validate_reason(command.reason)
        _validate_expected_version(command.expected_history_row_version, allow_zero=True)
        occurred_at = self._occurred_at()
        with self._database.session() as session, session.begin():
            created = self._repository.create_draft(
                session,
                command.template,
                expected_history_row_version=command.expected_history_row_version,
                created_at=occurred_at,
            )
            self._audit.append(
                session,
                AuditChange(
                    actor=command.actor,
                    action="MAPPING_TEMPLATE_REVISION_CREATED",
                    target_type="mapping_template_revision",
                    target_id=_target_id(created),
                    before_state=None,
                    after_state=_audit_state(created),
                    reason=command.reason,
                    requirement_id=command.requirement_id,
                    source_reference=command.source_reference,
                ),
            )
        return created

    def review(
        self,
        command: ReviewMappingTemplateRevisionCommand,
    ) -> PersistedMappingTemplate:
        _require_human_role(command.actor, Role.REVIEWER, Role.ADMIN)
        _validate_revision_command(command)
        occurred_at = self._occurred_at()
        with self._database.session() as session, session.begin():
            mutation = self._repository.review(
                session,
                project_key=command.project_key,
                supplier_scope=command.supplier_scope,
                template_id=command.template_id,
                revision=command.revision,
                expected_history_row_version=command.expected_history_row_version,
                expected_revision_row_version=command.expected_revision_row_version,
                reviewed_by=command.actor.actor_id,
                reviewed_at=occurred_at,
            )
            self._append_workflow_audit(
                session,
                mutation,
                command.actor,
                action="MAPPING_TEMPLATE_REVIEWED",
                reason=command.reason,
                requirement_id=command.requirement_id,
                source_reference=command.source_reference,
            )
        return mutation.after

    def approve(
        self,
        command: ApproveMappingTemplateRevisionCommand,
    ) -> PersistedMappingTemplate:
        _require_human_role(command.actor, Role.ADMIN)
        _validate_revision_command(command)
        occurred_at = self._occurred_at()
        with self._database.session() as session, session.begin():
            mutation = self._repository.approve(
                session,
                project_key=command.project_key,
                supplier_scope=command.supplier_scope,
                template_id=command.template_id,
                revision=command.revision,
                expected_history_row_version=command.expected_history_row_version,
                expected_revision_row_version=command.expected_revision_row_version,
                approved_by=command.actor.actor_id,
                approved_at=occurred_at,
            )
            self._append_workflow_audit(
                session,
                mutation,
                command.actor,
                action="MAPPING_TEMPLATE_APPROVED",
                reason=command.reason,
                requirement_id=command.requirement_id,
                source_reference=command.source_reference,
            )
        return mutation.after

    def supersede(
        self,
        command: SupersedeMappingTemplateRevisionCommand,
    ) -> PersistedTemplateSupersession:
        _require_human_role(command.actor, Role.ADMIN)
        _validate_scope(
            command.project_key,
            command.supplier_scope,
            command.template_id,
        )
        _validate_reason(command.reason)
        if command.predecessor_revision < 1 or command.successor_revision < 1:
            raise ValueError("mapping revisions must be positive")
        for version in (
            command.expected_history_row_version,
            command.expected_predecessor_row_version,
            command.expected_successor_row_version,
        ):
            _validate_expected_version(version)
        occurred_at = self._occurred_at()
        with self._database.session() as session, session.begin():
            result = self._repository.supersede(
                session,
                project_key=command.project_key,
                supplier_scope=command.supplier_scope,
                template_id=command.template_id,
                predecessor_revision=command.predecessor_revision,
                successor_revision=command.successor_revision,
                expected_history_row_version=command.expected_history_row_version,
                expected_predecessor_row_version=command.expected_predecessor_row_version,
                expected_successor_row_version=command.expected_successor_row_version,
                decided_by=command.actor.actor_id,
                decided_at=occurred_at,
                reason=command.reason,
            )
            predecessor_before = replace(
                result.predecessor,
                history_row_version=result.predecessor.history_row_version - 1,
                revision_row_version=result.predecessor.revision_row_version - 1,
                resolved_effective_to=None,
            )
            successor_before = replace(
                result.successor,
                template=replace(
                    result.successor.template,
                    status=MappingTemplateStatus.REVIEWED,
                    approved_by=None,
                    approved_at=None,
                ),
                history_row_version=result.successor.history_row_version - 1,
                revision_row_version=result.successor.revision_row_version - 1,
            )
            self._audit.append(
                session,
                AuditChange(
                    actor=command.actor,
                    action="MAPPING_TEMPLATE_SUPERSEDED",
                    target_type="mapping_template_revision",
                    target_id=_target_id(result.successor),
                    before_state={
                        "predecessor": _audit_state(predecessor_before),
                        "successor": _audit_state(successor_before),
                    },
                    after_state={
                        "predecessor": _audit_state(result.predecessor),
                        "successor": _audit_state(result.successor),
                        "decision_effective_to": (
                            result.decision.predecessor_effective_to.isoformat()
                        ),
                    },
                    reason=command.reason,
                    requirement_id=command.requirement_id,
                    source_reference=command.source_reference,
                ),
            )
        return result

    def _append_workflow_audit(
        self,
        session: Session,
        mutation: MappingTemplateWorkflowMutation,
        actor: Actor,
        *,
        action: str,
        reason: str,
        requirement_id: str | None,
        source_reference: str | None,
    ) -> None:
        self._audit.append(
            session,
            AuditChange(
                actor=actor,
                action=action,
                target_type="mapping_template_revision",
                target_id=_target_id(mutation.after),
                before_state=_audit_state(mutation.before),
                after_state=_audit_state(mutation.after),
                reason=reason,
                requirement_id=requirement_id,
                source_reference=source_reference,
            ),
        )

    def _occurred_at(self) -> datetime:
        value = self._clock()
        if value.utcoffset() is None:
            raise ValueError("Mapping Template command clock must return an aware datetime")
        return value.astimezone(UTC)


MappingTemplateCommands = MappingTemplateCommandService


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _require_human_role(actor: Actor, *roles: Role) -> None:
    if not isinstance(actor, Actor):
        raise UntrustedMappingTemplateActorError("command actor must be a trusted Actor")
    if actor.kind != ActorKind.LOCAL_OWNER:
        raise MappingTemplateAuthorizationError(
            "only a trusted local human Actor may change Mapping Templates"
        )
    if not any(actor.has_role(role) for role in roles):
        raise MappingTemplateAuthorizationError("actor lacks the required Mapping Template role")


def _validate_revision_command(
    command: ReviewMappingTemplateRevisionCommand | ApproveMappingTemplateRevisionCommand,
) -> None:
    _validate_scope(command.project_key, command.supplier_scope, command.template_id)
    _validate_reason(command.reason)
    if command.revision < 1:
        raise ValueError("mapping revision must be positive")
    _validate_expected_version(command.expected_history_row_version)
    _validate_expected_version(command.expected_revision_row_version)


def _validate_scope(project_key: str, supplier_scope: str, template_id: str) -> None:
    if any(not value.strip() for value in (project_key, supplier_scope, template_id)):
        raise ValueError("mapping template scope values must not be blank")


def _validate_reason(reason: str) -> None:
    if not reason.strip():
        raise ValueError("reason must not be blank")


def _validate_expected_version(value: int, *, allow_zero: bool = False) -> None:
    minimum = 0 if allow_zero else 1
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"expected row_version must be at least {minimum}")


def _target_id(record: PersistedMappingTemplate) -> str:
    template = record.template
    return (
        f"{template.project_key}:{template.supplier_scope}:"
        f"{template.template_id}:{template.revision}"
    )


def _audit_state(
    record: PersistedMappingTemplate,
) -> dict[str, object]:
    template = record.template
    resolved = record.resolved_effective_to
    return {
        "project_key": template.project_key,
        "supplier_scope": template.supplier_scope,
        "template_id": template.template_id,
        "revision": template.revision,
        "status": template.status.value,
        "effective_from": template.effective_from.isoformat(),
        "declared_effective_to": (
            template.effective_to.isoformat() if template.effective_to else None
        ),
        "resolved_effective_to": resolved.isoformat() if resolved else None,
        "reviewed_by": template.reviewed_by,
        "reviewed_at": template.reviewed_at.isoformat() if template.reviewed_at else None,
        "approved_by": template.approved_by,
        "approved_at": template.approved_at.isoformat() if template.approved_at else None,
        "history_row_version": record.history_row_version,
        "revision_row_version": record.revision_row_version,
    }
