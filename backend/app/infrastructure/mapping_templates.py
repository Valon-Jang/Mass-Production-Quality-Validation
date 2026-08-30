"""SQLAlchemy persistence for immutable Mapping Template revision payloads."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    select,
    update,
)
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

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
    TemplateSupersessionDecision,
    WorkbookFingerprint,
)
from app.domain.workbook_scan import SheetKind
from app.infrastructure.audit import UTCDateTime
from app.infrastructure.database import Base


def _new_id() -> str:
    return str(uuid4())


class MappingTemplateHistoryRow(Base):
    __tablename__ = "mapping_template_histories"
    __table_args__ = (
        UniqueConstraint(
            "project_key",
            "supplier_scope",
            "template_id",
            name="uq_mapping_template_history_scope",
        ),
        Index(
            "uq_mapping_template_history_scope_id",
            "project_key",
            "supplier_scope",
            "template_id",
            "id",
            unique=True,
        ),
        CheckConstraint("row_version >= 0", name="mapping_template_history_row_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    project_key: Mapped[str] = mapped_column(String(200), nullable=False)
    supplier_scope: Mapped[str] = mapped_column(String(200), nullable=False)
    template_id: Mapped[str] = mapped_column(String(200), nullable=False)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class MappingTemplateRevisionRow(Base):
    __tablename__ = "mapping_template_revisions"
    __table_args__ = (
        UniqueConstraint(
            "history_id",
            "revision",
            name="uq_mapping_template_revision_history_revision",
        ),
        Index(
            "uq_mapping_template_revision_history_revision_id",
            "history_id",
            "revision",
            "id",
            unique=True,
        ),
        CheckConstraint("revision >= 1", name="mapping_template_revision_positive"),
        CheckConstraint("row_version >= 1", name="mapping_template_revision_row_version"),
        CheckConstraint(
            "status IN ('DRAFT', 'REVIEWED', 'APPROVED')",
            name="mapping_template_revision_status",
        ),
        CheckConstraint(
            "declared_effective_to IS NULL OR declared_effective_to >= declared_effective_from",
            name="mapping_template_declared_effectivity",
        ),
        CheckConstraint(
            "resolved_effective_to IS NULL OR resolved_effective_to >= declared_effective_from",
            name="mapping_template_resolved_effectivity",
        ),
        CheckConstraint(
            "(status = 'DRAFT' AND reviewed_by IS NULL AND reviewed_at IS NULL "
            "AND approved_by IS NULL AND approved_at IS NULL) OR "
            "(status = 'REVIEWED' AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL "
            "AND approved_by IS NULL AND approved_at IS NULL) OR "
            "(status = 'APPROVED' AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL "
            "AND approved_by IS NOT NULL AND approved_at IS NOT NULL)",
            name="mapping_template_workflow_metadata",
        ),
        Index("ix_mapping_template_revisions_history", "history_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    history_id: Mapped[str] = mapped_column(
        ForeignKey("mapping_template_histories.id", ondelete="RESTRICT"),
        nullable=False,
    )
    revision_number: Mapped[int] = mapped_column("revision", Integer, nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    template_payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    declared_effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    declared_effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    resolved_effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class MappingTemplateSupersessionRow(Base):
    __tablename__ = "mapping_template_supersessions"
    __table_args__ = (
        UniqueConstraint(
            "predecessor_revision_id",
            name="uq_mapping_template_supersession_predecessor",
        ),
        UniqueConstraint(
            "successor_revision_id",
            name="uq_mapping_template_supersession_successor",
        ),
        CheckConstraint(
            "predecessor_revision_id <> successor_revision_id",
            name="mapping_template_supersession_distinct_revisions",
        ),
        Index("ix_mapping_template_supersessions_history", "history_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    history_id: Mapped[str] = mapped_column(
        ForeignKey("mapping_template_histories.id", ondelete="RESTRICT"),
        nullable=False,
    )
    predecessor_revision_id: Mapped[str] = mapped_column(
        ForeignKey("mapping_template_revisions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    successor_revision_id: Mapped[str] = mapped_column(
        ForeignKey("mapping_template_revisions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    predecessor_effective_to: Mapped[date] = mapped_column(Date, nullable=False)
    decided_by: Mapped[str] = mapped_column(String(120), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)


@dataclass(frozen=True, slots=True)
class PersistedMappingTemplate:
    template: MappingTemplate
    history_id: str
    revision_id: str
    history_row_version: int
    revision_row_version: int
    resolved_effective_to: date | None


@dataclass(frozen=True, slots=True)
class PersistedTemplateSupersession:
    decision: TemplateSupersessionDecision
    predecessor: PersistedMappingTemplate
    successor: PersistedMappingTemplate


class MappingTemplatePersistenceError(RuntimeError):
    """Base class for storage failures safe to expose to an application use case."""


class MappingTemplateNotFoundError(MappingTemplatePersistenceError):
    """The requested project-local template history or revision does not exist."""


class StaleMappingTemplateWriteError(MappingTemplatePersistenceError):
    """A required aggregate or revision row_version no longer matches."""


class MappingTemplatePayloadIntegrityError(MappingTemplatePersistenceError):
    """The stored immutable payload is malformed or no longer matches its digest."""


class PersistentMappingTemplateCatalog:
    """Immutable project-local snapshot suitable for Mapping Preview."""

    def __init__(self, records: tuple[PersistedMappingTemplate, ...]) -> None:
        project_keys = {record.template.project_key for record in records}
        if len(project_keys) > 1:
            raise ValueError("a persistent catalog cannot mix projects")
        self._records = records
        self._resolved_ends = {
            _template_key(record.template): record.resolved_effective_to for record in records
        }

    @property
    def templates(self) -> tuple[MappingTemplate, ...]:
        return tuple(record.template for record in self._records)

    def is_effective_on(self, template: MappingTemplate, value: date) -> bool:
        return (
            template.effective_from <= value <= (self.resolved_effective_to(template) or date.max)
        )

    def resolved_effective_to(self, template: MappingTemplate) -> date | None:
        resolved = self._resolved_ends.get(_template_key(template))
        return resolved if resolved is not None else template.effective_to


@dataclass(frozen=True, slots=True)
class MappingTemplateWorkflowMutation:
    before: PersistedMappingTemplate
    after: PersistedMappingTemplate


class MappingTemplateRepository:
    """Repository with explicit CAS writes; commit remains the use case's responsibility."""

    def create_draft(
        self,
        session: Session,
        template: MappingTemplate,
        *,
        expected_history_row_version: int,
        created_at: datetime,
    ) -> PersistedMappingTemplate:
        if template.status != MappingTemplateStatus.DRAFT:
            raise TemplateHistoryError(
                TemplateHistoryErrorCode.INVALID_STATUS_TRANSITION,
                "a persisted revision must be created as DRAFT",
            )
        _require_aware(created_at, "created_at")
        history = self._history(session, template)
        if history is None:
            if expected_history_row_version != 0:
                raise StaleMappingTemplateWriteError("mapping history row_version is stale")
            history = MappingTemplateHistoryRow(
                project_key=template.project_key,
                supplier_scope=template.supplier_scope,
                template_id=template.template_id,
                row_version=0,
                created_at=created_at,
            )
            session.add(history)
            try:
                session.flush()
            except IntegrityError as error:
                raise StaleMappingTemplateWriteError(
                    "mapping history was concurrently created"
                ) from error
        elif history.row_version != expected_history_row_version:
            raise StaleMappingTemplateWriteError("mapping history row_version is stale")

        rows = self._revision_rows(session, history.id)
        if any(row.revision_number == template.revision for row in rows):
            raise TemplateHistoryError(
                TemplateHistoryErrorCode.REVISION_OVERWRITE,
                "a persisted template revision is immutable",
            )
        if rows and template.revision < max(row.revision_number for row in rows):
            raise TemplateHistoryError(
                TemplateHistoryErrorCode.REVISION_DOWNGRADE,
                "a lower revision cannot be appended after a higher revision",
            )

        self._cas_history(session, history.id, expected_history_row_version)
        payload = _serialize_template_payload(template)
        row = MappingTemplateRevisionRow(
            history_id=history.id,
            revision_number=template.revision,
            schema_version=template.schema_version,
            status=MappingTemplateStatus.DRAFT.value,
            template_payload=payload,
            payload_sha256=_payload_digest(payload),
            declared_effective_from=template.effective_from,
            declared_effective_to=template.effective_to,
            resolved_effective_to=None,
            reviewed_by=None,
            reviewed_at=None,
            approved_by=None,
            approved_at=None,
            row_version=1,
            created_at=created_at,
        )
        session.add(row)
        try:
            session.flush()
        except IntegrityError as error:
            raise TemplateHistoryError(
                TemplateHistoryErrorCode.REVISION_OVERWRITE,
                "a persisted template revision is immutable",
            ) from error
        return PersistedMappingTemplate(
            template=template,
            history_id=history.id,
            revision_id=row.id,
            history_row_version=expected_history_row_version + 1,
            revision_row_version=1,
            resolved_effective_to=None,
        )

    def review(
        self,
        session: Session,
        *,
        project_key: str,
        supplier_scope: str,
        template_id: str,
        revision: int,
        expected_history_row_version: int,
        expected_revision_row_version: int,
        reviewed_by: str,
        reviewed_at: datetime,
    ) -> MappingTemplateWorkflowMutation:
        _require_aware(reviewed_at, "reviewed_at")
        history, row = self._load_rows(session, project_key, supplier_scope, template_id, revision)
        self._assert_versions(
            history,
            row,
            expected_history_row_version,
            expected_revision_row_version,
        )
        before = self._to_record(history, row)
        if before.template.status != MappingTemplateStatus.DRAFT:
            raise TemplateHistoryError(
                TemplateHistoryErrorCode.INVALID_STATUS_TRANSITION,
                "only a DRAFT revision can be reviewed",
            )
        self._cas_history(session, history.id, expected_history_row_version)
        self._cas_revision(
            session,
            row.id,
            expected_revision_row_version,
            status=MappingTemplateStatus.REVIEWED.value,
            reviewed_by=reviewed_by,
            reviewed_at=reviewed_at,
        )
        reviewed = replace(
            before.template,
            status=MappingTemplateStatus.REVIEWED,
            reviewed_by=reviewed_by,
            reviewed_at=reviewed_at,
        )
        return MappingTemplateWorkflowMutation(
            before=before,
            after=replace(
                before,
                template=reviewed,
                history_row_version=expected_history_row_version + 1,
                revision_row_version=expected_revision_row_version + 1,
            ),
        )

    def approve(
        self,
        session: Session,
        *,
        project_key: str,
        supplier_scope: str,
        template_id: str,
        revision: int,
        expected_history_row_version: int,
        expected_revision_row_version: int,
        approved_by: str,
        approved_at: datetime,
    ) -> MappingTemplateWorkflowMutation:
        _require_aware(approved_at, "approved_at")
        history, row = self._load_rows(session, project_key, supplier_scope, template_id, revision)
        self._assert_versions(
            history,
            row,
            expected_history_row_version,
            expected_revision_row_version,
        )
        before = self._to_record(history, row)
        if before.template.status != MappingTemplateStatus.REVIEWED:
            raise TemplateHistoryError(
                TemplateHistoryErrorCode.INVALID_STATUS_TRANSITION,
                "only a REVIEWED revision can be approved",
            )
        if self._approved_overlaps(session, history.id, row):
            raise TemplateHistoryError(
                TemplateHistoryErrorCode.EFFECTIVE_PERIOD_OVERLAP,
                "approved template revisions in one scoped history cannot overlap",
            )
        self._cas_history(session, history.id, expected_history_row_version)
        self._cas_revision(
            session,
            row.id,
            expected_revision_row_version,
            status=MappingTemplateStatus.APPROVED.value,
            approved_by=approved_by,
            approved_at=approved_at,
        )
        approved = replace(
            before.template,
            status=MappingTemplateStatus.APPROVED,
            approved_by=approved_by,
            approved_at=approved_at,
        )
        return MappingTemplateWorkflowMutation(
            before=before,
            after=replace(
                before,
                template=approved,
                history_row_version=expected_history_row_version + 1,
                revision_row_version=expected_revision_row_version + 1,
            ),
        )

    def supersede(
        self,
        session: Session,
        *,
        project_key: str,
        supplier_scope: str,
        template_id: str,
        predecessor_revision: int,
        successor_revision: int,
        expected_history_row_version: int,
        expected_predecessor_row_version: int,
        expected_successor_row_version: int,
        decided_by: str,
        decided_at: datetime,
        reason: str,
    ) -> PersistedTemplateSupersession:
        _require_aware(decided_at, "decided_at")
        history, predecessor_row = self._load_rows(
            session, project_key, supplier_scope, template_id, predecessor_revision
        )
        successor_row = self._revision_row(session, history.id, successor_revision)
        if successor_row is None:
            raise MappingTemplateNotFoundError("successor mapping revision was not found")
        self._assert_versions(
            history,
            predecessor_row,
            expected_history_row_version,
            expected_predecessor_row_version,
        )
        if successor_row.row_version != expected_successor_row_version:
            raise StaleMappingTemplateWriteError("successor revision row_version is stale")
        predecessor = self._to_record(history, predecessor_row)
        successor = self._to_record(history, successor_row)
        if self._supersession_exists(session, predecessor_row.id, successor_row.id):
            raise TemplateHistoryError(
                TemplateHistoryErrorCode.SUPERSESSION_DUPLICATE,
                "a predecessor or successor already has a supersession decision",
            )
        if predecessor.template.status != MappingTemplateStatus.APPROVED:
            raise TemplateHistoryError(
                TemplateHistoryErrorCode.SUPERSESSION_PREDECESSOR_MISSING,
                "supersession requires an approved predecessor",
            )
        if successor.template.status != MappingTemplateStatus.REVIEWED:
            raise TemplateHistoryError(
                TemplateHistoryErrorCode.INVALID_STATUS_TRANSITION,
                "a successor must be REVIEWED before ADMIN supersession approval",
            )
        if successor_revision <= predecessor_revision:
            raise TemplateHistoryError(
                TemplateHistoryErrorCode.REVISION_DOWNGRADE,
                "a successor revision must be greater than its predecessor",
            )
        successor_start = successor.template.effective_from
        spanning = [
            candidate
            for candidate in self._approved_rows(session, history.id)
            if candidate.declared_effective_from < successor_start
            and successor_start <= _effective_end(candidate)
        ]
        if len(spanning) != 1 or spanning[0].id != predecessor_row.id:
            raise TemplateHistoryError(
                TemplateHistoryErrorCode.SUPERSESSION_PREDECESSOR_MISSING,
                "supersession requires exactly one approved predecessor spanning the start date",
            )
        predecessor_end = successor_start - timedelta(days=1)
        conflicts = [
            candidate
            for candidate in self._approved_rows(session, history.id)
            if candidate.id != predecessor_row.id
            and _periods_overlap(
                successor.template.effective_from,
                successor.template.effective_to,
                candidate.declared_effective_from,
                candidate.resolved_effective_to or candidate.declared_effective_to,
            )
        ]
        if conflicts:
            raise TemplateHistoryError(
                TemplateHistoryErrorCode.EFFECTIVE_PERIOD_OVERLAP,
                "successor still overlaps another approved revision",
            )

        self._cas_history(session, history.id, expected_history_row_version)
        self._cas_revision(
            session,
            predecessor_row.id,
            expected_predecessor_row_version,
            resolved_effective_to=predecessor_end,
        )
        self._cas_revision(
            session,
            successor_row.id,
            expected_successor_row_version,
            status=MappingTemplateStatus.APPROVED.value,
            approved_by=decided_by,
            approved_at=decided_at,
        )
        decision = TemplateSupersessionDecision(
            project_key=project_key,
            supplier_scope=supplier_scope,
            template_id=template_id,
            predecessor_revision=predecessor_revision,
            successor_revision=successor_revision,
            predecessor_effective_to=predecessor_end,
            decided_by=decided_by,
            decided_at=decided_at,
            reason=reason,
        )
        session.add(
            MappingTemplateSupersessionRow(
                history_id=history.id,
                predecessor_revision_id=predecessor_row.id,
                successor_revision_id=successor_row.id,
                predecessor_effective_to=predecessor_end,
                decided_by=decided_by,
                decided_at=decided_at,
                reason=reason,
            )
        )
        try:
            session.flush()
        except IntegrityError as error:
            raise TemplateHistoryError(
                TemplateHistoryErrorCode.SUPERSESSION_DUPLICATE,
                "a predecessor or successor already has a supersession decision",
            ) from error

        approved_successor = replace(
            successor.template,
            status=MappingTemplateStatus.APPROVED,
            approved_by=decided_by,
            approved_at=decided_at,
        )
        return PersistedTemplateSupersession(
            decision=decision,
            predecessor=replace(
                predecessor,
                history_row_version=expected_history_row_version + 1,
                revision_row_version=expected_predecessor_row_version + 1,
                resolved_effective_to=predecessor_end,
            ),
            successor=replace(
                successor,
                template=approved_successor,
                history_row_version=expected_history_row_version + 1,
                revision_row_version=expected_successor_row_version + 1,
            ),
        )

    def get(
        self,
        session: Session,
        *,
        project_key: str,
        supplier_scope: str,
        template_id: str,
        revision: int,
    ) -> PersistedMappingTemplate:
        history, row = self._load_rows(session, project_key, supplier_scope, template_id, revision)
        return self._to_record(history, row)

    def load_catalog(
        self,
        session: Session,
        *,
        project_key: str,
    ) -> PersistentMappingTemplateCatalog:
        histories = session.scalars(
            select(MappingTemplateHistoryRow).where(
                MappingTemplateHistoryRow.project_key == project_key
            )
        ).all()
        records = tuple(
            self._to_record(history, row)
            for history in histories
            for row in self._revision_rows(session, history.id)
        )
        return PersistentMappingTemplateCatalog(records)

    @staticmethod
    def _history(
        session: Session,
        template: MappingTemplate,
    ) -> MappingTemplateHistoryRow | None:
        return session.scalar(
            select(MappingTemplateHistoryRow).where(
                MappingTemplateHistoryRow.project_key == template.project_key,
                MappingTemplateHistoryRow.supplier_scope == template.supplier_scope,
                MappingTemplateHistoryRow.template_id == template.template_id,
            )
        )

    @staticmethod
    def _history_by_key(
        session: Session,
        project_key: str,
        supplier_scope: str,
        template_id: str,
    ) -> MappingTemplateHistoryRow | None:
        return session.scalar(
            select(MappingTemplateHistoryRow).where(
                MappingTemplateHistoryRow.project_key == project_key,
                MappingTemplateHistoryRow.supplier_scope == supplier_scope,
                MappingTemplateHistoryRow.template_id == template_id,
            )
        )

    @staticmethod
    def _revision_rows(
        session: Session,
        history_id: str,
    ) -> list[MappingTemplateRevisionRow]:
        return list(
            session.scalars(
                select(MappingTemplateRevisionRow)
                .where(MappingTemplateRevisionRow.history_id == history_id)
                .order_by(MappingTemplateRevisionRow.revision_number)
            ).all()
        )

    @staticmethod
    def _revision_row(
        session: Session,
        history_id: str,
        revision: int,
    ) -> MappingTemplateRevisionRow | None:
        return session.scalar(
            select(MappingTemplateRevisionRow).where(
                MappingTemplateRevisionRow.history_id == history_id,
                MappingTemplateRevisionRow.revision_number == revision,
            )
        )

    def _load_rows(
        self,
        session: Session,
        project_key: str,
        supplier_scope: str,
        template_id: str,
        revision: int,
    ) -> tuple[MappingTemplateHistoryRow, MappingTemplateRevisionRow]:
        history = self._history_by_key(session, project_key, supplier_scope, template_id)
        if history is None:
            raise MappingTemplateNotFoundError("mapping template history was not found")
        row = self._revision_row(session, history.id, revision)
        if row is None:
            raise MappingTemplateNotFoundError("mapping template revision was not found")
        return history, row

    @staticmethod
    def _assert_versions(
        history: MappingTemplateHistoryRow,
        row: MappingTemplateRevisionRow,
        expected_history_row_version: int,
        expected_revision_row_version: int,
    ) -> None:
        if history.row_version != expected_history_row_version:
            raise StaleMappingTemplateWriteError("mapping history row_version is stale")
        if row.row_version != expected_revision_row_version:
            raise StaleMappingTemplateWriteError("mapping revision row_version is stale")

    @staticmethod
    def _cas_history(session: Session, history_id: str, expected: int) -> None:
        result = cast(
            CursorResult[Any],
            session.execute(
                update(MappingTemplateHistoryRow)
                .where(
                    MappingTemplateHistoryRow.id == history_id,
                    MappingTemplateHistoryRow.row_version == expected,
                )
                .values(row_version=expected + 1)
                .execution_options(synchronize_session=False)
            ),
        )
        if result.rowcount != 1:
            raise StaleMappingTemplateWriteError("mapping history row_version is stale")

    @staticmethod
    def _cas_revision(
        session: Session,
        revision_id: str,
        expected: int,
        **values: object,
    ) -> None:
        result = cast(
            CursorResult[Any],
            session.execute(
                update(MappingTemplateRevisionRow)
                .where(
                    MappingTemplateRevisionRow.id == revision_id,
                    MappingTemplateRevisionRow.row_version == expected,
                )
                .values(row_version=expected + 1, **values)
                .execution_options(synchronize_session=False)
            ),
        )
        if result.rowcount != 1:
            raise StaleMappingTemplateWriteError("mapping revision row_version is stale")

    def _approved_overlaps(
        self,
        session: Session,
        history_id: str,
        candidate: MappingTemplateRevisionRow,
    ) -> bool:
        return any(
            _periods_overlap(
                candidate.declared_effective_from,
                candidate.declared_effective_to,
                existing.declared_effective_from,
                existing.resolved_effective_to or existing.declared_effective_to,
            )
            for existing in self._approved_rows(session, history_id)
            if existing.id != candidate.id
        )

    @staticmethod
    def _approved_rows(
        session: Session,
        history_id: str,
    ) -> list[MappingTemplateRevisionRow]:
        return list(
            session.scalars(
                select(MappingTemplateRevisionRow).where(
                    MappingTemplateRevisionRow.history_id == history_id,
                    MappingTemplateRevisionRow.status == MappingTemplateStatus.APPROVED.value,
                )
            ).all()
        )

    @staticmethod
    def _supersession_exists(
        session: Session,
        predecessor_revision_id: str,
        successor_revision_id: str,
    ) -> bool:
        return (
            session.scalar(
                select(MappingTemplateSupersessionRow.id).where(
                    (
                        MappingTemplateSupersessionRow.predecessor_revision_id
                        == predecessor_revision_id
                    )
                    | (
                        MappingTemplateSupersessionRow.successor_revision_id
                        == successor_revision_id
                    )
                )
            )
            is not None
        )

    @staticmethod
    def _to_record(
        history: MappingTemplateHistoryRow,
        row: MappingTemplateRevisionRow,
    ) -> PersistedMappingTemplate:
        payload = row.template_payload
        if _payload_digest(payload) != row.payload_sha256:
            raise MappingTemplatePayloadIntegrityError(
                "stored Mapping Template payload digest does not match"
            )
        return PersistedMappingTemplate(
            template=_deserialize_template(history, row, payload),
            history_id=history.id,
            revision_id=row.id,
            history_row_version=history.row_version,
            revision_row_version=row.row_version,
            resolved_effective_to=row.resolved_effective_to,
        )


def _serialize_template_payload(template: MappingTemplate) -> dict[str, object]:
    return {
        "supplier_source_aliases": list(template.supplier_source_aliases),
        "fingerprint": {
            "header_tokens": [
                {
                    "source": _serialize_address(assertion.source),
                    "expected_token": assertion.expected_token,
                }
                for assertion in template.fingerprint.header_tokens
            ],
            "sheet_structures": [
                {
                    "sheet_name": assertion.sheet_name,
                    "expected_position": assertion.expected_position,
                    "expected_kind": assertion.expected_kind.value,
                    "expected_visibility": assertion.expected_visibility,
                    "expected_used_range": assertion.expected_used_range,
                }
                for assertion in template.fingerprint.sheet_structures
            ],
            "merge_signatures": [
                {
                    "sheet_name": assertion.sheet_name,
                    "expected_merged_ranges": list(assertion.expected_merged_ranges),
                }
                for assertion in template.fingerprint.merge_signatures
            ],
            "row_structures": [
                {
                    "row_key": assertion.row_key,
                    "sheet_name": assertion.sheet_name,
                    "row_index": assertion.row_index,
                    "expected_non_empty_cells": [
                        _serialize_address(address)
                        for address in assertion.expected_non_empty_cells
                    ],
                }
                for assertion in template.fingerprint.row_structures
            ],
        },
        "identifiers": [
            {"kind": mapping.kind.value, "source": _serialize_address(mapping.source)}
            for mapping in template.identifiers
        ],
        "inspection_rows": [
            _serialize_inspection_row(mapping, schema_version=template.schema_version)
            for mapping in template.inspection_rows
        ],
    }


_V2_INSPECTION_ROLE_KEYS = (
    "section",
    "category",
    "unit",
    "measurement_point",
    "measurement_location",
    "cavity",
    "target",
    "lsl",
    "usl",
    "source_spec_revision",
)
_V1_INSPECTION_ROW_KEYS = frozenset(
    {
        "row_key",
        "item",
        "method",
        "instrument",
        "specification",
        "tolerance",
        "minimum",
        "maximum",
        "sample_cells",
        "supplier_result",
    }
)


def _serialize_inspection_row(
    mapping: InspectionRowMapping,
    *,
    schema_version: str,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "row_key": mapping.row_key,
        "item": _serialize_address(mapping.item),
        "method": _serialize_optional_address(mapping.method),
        "instrument": _serialize_optional_address(mapping.instrument),
        "specification": _serialize_optional_address(mapping.specification),
        "tolerance": _serialize_optional_address(mapping.tolerance),
        "minimum": _serialize_optional_address(mapping.minimum),
        "maximum": _serialize_optional_address(mapping.maximum),
        "sample_cells": [_serialize_address(address) for address in mapping.sample_cells],
        "supplier_result": _serialize_optional_address(mapping.supplier_result),
    }
    if schema_version == "2":
        payload.update(
            {
                "section": _serialize_optional_address(mapping.section),
                "category": _serialize_optional_address(mapping.category),
                "unit": _serialize_optional_address(mapping.unit),
                "measurement_point": _serialize_optional_address(mapping.measurement_point),
                "measurement_location": _serialize_optional_address(mapping.measurement_location),
                "cavity": _serialize_optional_address(mapping.cavity),
                "target": _serialize_optional_address(mapping.target),
                "lsl": _serialize_optional_address(mapping.lsl),
                "usl": _serialize_optional_address(mapping.usl),
                "source_spec_revision": _serialize_optional_address(mapping.source_spec_revision),
            }
        )
    return payload


def _deserialize_template(
    history: MappingTemplateHistoryRow,
    row: MappingTemplateRevisionRow,
    payload: dict[str, object],
) -> MappingTemplate:
    fingerprint = _as_dict(payload, "fingerprint")
    inspection_row_payloads = _as_dict_list(payload, "inspection_rows")
    _validate_inspection_payload_schema(inspection_row_payloads, row.schema_version)
    return MappingTemplate(
        template_id=history.template_id,
        schema_version=row.schema_version,
        revision=row.revision_number,
        status=MappingTemplateStatus(row.status),
        project_key=history.project_key,
        supplier_scope=history.supplier_scope,
        supplier_source_aliases=tuple(_as_string_list(payload, "supplier_source_aliases")),
        approved_by=row.approved_by,
        approved_at=row.approved_at,
        effective_from=row.declared_effective_from,
        effective_to=row.declared_effective_to,
        fingerprint=WorkbookFingerprint(
            header_tokens=tuple(
                HeaderTokenAssertion(
                    source=_deserialize_address(_as_dict(item, "source")),
                    expected_token=_as_string(item, "expected_token"),
                )
                for item in _as_dict_list(fingerprint, "header_tokens")
            ),
            sheet_structures=tuple(
                SheetStructureAssertion(
                    sheet_name=_as_string(item, "sheet_name"),
                    expected_position=_as_int(item, "expected_position"),
                    expected_kind=SheetKind(_as_string(item, "expected_kind")),
                    expected_visibility=_as_string(item, "expected_visibility"),
                    expected_used_range=_as_optional_string(item, "expected_used_range"),
                )
                for item in _as_dict_list(fingerprint, "sheet_structures")
            ),
            merge_signatures=tuple(
                MergeSignatureAssertion(
                    sheet_name=_as_string(item, "sheet_name"),
                    expected_merged_ranges=tuple(_as_string_list(item, "expected_merged_ranges")),
                )
                for item in _as_dict_list(fingerprint, "merge_signatures")
            ),
            row_structures=tuple(
                RowStructureAssertion(
                    row_key=_as_string(item, "row_key"),
                    sheet_name=_as_string(item, "sheet_name"),
                    row_index=_as_int(item, "row_index"),
                    expected_non_empty_cells=tuple(
                        _deserialize_address(address)
                        for address in _as_dict_list(item, "expected_non_empty_cells")
                    ),
                )
                for item in _as_dict_list(fingerprint, "row_structures")
            ),
        ),
        identifiers=tuple(
            IdentifierMapping(
                kind=IdentifierKind(_as_string(item, "kind")),
                source=_deserialize_address(_as_dict(item, "source")),
            )
            for item in _as_dict_list(payload, "identifiers")
        ),
        inspection_rows=tuple(
            InspectionRowMapping(
                row_key=_as_string(item, "row_key"),
                item=_deserialize_address(_as_dict(item, "item")),
                method=_deserialize_optional_address(item, "method"),
                instrument=_deserialize_optional_address(item, "instrument"),
                specification=_deserialize_optional_address(item, "specification"),
                tolerance=_deserialize_optional_address(item, "tolerance"),
                minimum=_deserialize_optional_address(item, "minimum"),
                maximum=_deserialize_optional_address(item, "maximum"),
                sample_cells=tuple(
                    _deserialize_address(address) for address in _as_dict_list(item, "sample_cells")
                ),
                supplier_result=_deserialize_optional_address(item, "supplier_result"),
                section=_deserialize_v2_optional_address(item, "section", row.schema_version),
                category=_deserialize_v2_optional_address(item, "category", row.schema_version),
                unit=_deserialize_v2_optional_address(item, "unit", row.schema_version),
                measurement_point=_deserialize_v2_optional_address(
                    item, "measurement_point", row.schema_version
                ),
                measurement_location=_deserialize_v2_optional_address(
                    item, "measurement_location", row.schema_version
                ),
                cavity=_deserialize_v2_optional_address(item, "cavity", row.schema_version),
                target=_deserialize_v2_optional_address(item, "target", row.schema_version),
                lsl=_deserialize_v2_optional_address(item, "lsl", row.schema_version),
                usl=_deserialize_v2_optional_address(item, "usl", row.schema_version),
                source_spec_revision=_deserialize_v2_optional_address(
                    item, "source_spec_revision", row.schema_version
                ),
            )
            for item in inspection_row_payloads
        ),
        reviewed_by=row.reviewed_by,
        reviewed_at=row.reviewed_at,
    )


def _serialize_address(address: CellAddress) -> dict[str, object]:
    return {"sheet_name": address.sheet_name, "coordinate": address.coordinate}


def _serialize_optional_address(address: CellAddress | None) -> dict[str, object] | None:
    return None if address is None else _serialize_address(address)


def _deserialize_address(value: dict[str, object]) -> CellAddress:
    return CellAddress(
        sheet_name=_as_string(value, "sheet_name"),
        coordinate=_as_string(value, "coordinate"),
    )


def _deserialize_optional_address(
    value: dict[str, object],
    key: str,
) -> CellAddress | None:
    nested = value.get(key)
    if nested is None:
        return None
    if not isinstance(nested, dict) or any(not isinstance(item, str) for item in nested):
        raise MappingTemplatePayloadIntegrityError(f"payload field {key} is not an object")
    return _deserialize_address(cast(dict[str, object], nested))


def _deserialize_v2_optional_address(
    value: dict[str, object],
    key: str,
    schema_version: str,
) -> CellAddress | None:
    if schema_version == "1":
        return None
    return _deserialize_optional_address(value, key)


def _validate_inspection_payload_schema(
    rows: list[dict[str, object]],
    schema_version: str,
) -> None:
    expected_keys: frozenset[str] | None = None
    if schema_version == "1":
        expected_keys = _V1_INSPECTION_ROW_KEYS
    elif schema_version == "2":
        expected_keys = _V1_INSPECTION_ROW_KEYS | frozenset(_V2_INSPECTION_ROLE_KEYS)
    if expected_keys is not None:
        for row in rows:
            actual_keys = frozenset(row)
            if actual_keys == expected_keys:
                continue
            missing = sorted(expected_keys - actual_keys)
            unexpected = sorted(actual_keys - expected_keys)
            raise MappingTemplatePayloadIntegrityError(
                f"Mapping Template schema v{schema_version} inspection payload shape differs; "
                f"missing={missing!r}, unexpected={unexpected!r}"
            )


def _payload_digest(payload: dict[str, object]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _as_dict(value: dict[str, object], key: str) -> dict[str, object]:
    nested = value.get(key)
    if not isinstance(nested, dict) or any(not isinstance(item, str) for item in nested):
        raise MappingTemplatePayloadIntegrityError(f"payload field {key} is not an object")
    return cast(dict[str, object], nested)


def _as_dict_list(value: dict[str, object], key: str) -> list[dict[str, object]]:
    nested = value.get(key)
    if not isinstance(nested, list):
        raise MappingTemplatePayloadIntegrityError(f"payload field {key} is not a list")
    if any(
        not isinstance(item, dict) or any(not isinstance(child_key, str) for child_key in item)
        for item in nested
    ):
        raise MappingTemplatePayloadIntegrityError(f"payload field {key} has invalid items")
    return cast(list[dict[str, object]], nested)


def _as_string(value: dict[str, object], key: str) -> str:
    nested = value.get(key)
    if not isinstance(nested, str):
        raise MappingTemplatePayloadIntegrityError(f"payload field {key} is not a string")
    return nested


def _as_optional_string(value: dict[str, object], key: str) -> str | None:
    nested = value.get(key)
    if nested is not None and not isinstance(nested, str):
        raise MappingTemplatePayloadIntegrityError(f"payload field {key} is not an optional string")
    return nested


def _as_int(value: dict[str, object], key: str) -> int:
    nested = value.get(key)
    if not isinstance(nested, int) or isinstance(nested, bool):
        raise MappingTemplatePayloadIntegrityError(f"payload field {key} is not an integer")
    return nested


def _as_string_list(value: dict[str, object], key: str) -> list[str]:
    nested = value.get(key)
    if not isinstance(nested, list) or any(not isinstance(item, str) for item in nested):
        raise MappingTemplatePayloadIntegrityError(f"payload field {key} is not a string list")
    return cast(list[str], nested)


def _template_key(template: MappingTemplate) -> tuple[str, str, str, int]:
    return (
        template.project_key,
        template.supplier_scope,
        template.template_id,
        template.revision,
    )


def _effective_end(row: MappingTemplateRevisionRow) -> date:
    return row.resolved_effective_to or row.declared_effective_to or date.max


def _periods_overlap(
    left_start: date,
    left_end: date | None,
    right_start: date,
    right_end: date | None,
) -> bool:
    return left_start <= (right_end or date.max) and right_start <= (left_end or date.max)


def _require_aware(value: datetime, name: str) -> None:
    if value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
