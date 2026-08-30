"""Append-only SQLAlchemy audit model and repository."""

from collections.abc import Sequence
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, DateTime, Index, String, Text, select
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.orm import Mapped, Session, mapped_column
from sqlalchemy.types import TypeDecorator

from app.domain.audit import AuditChange
from app.infrastructure.database import Base


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return str(uuid4())


class UTCDateTime(TypeDecorator[datetime]):
    """Persist aware timestamps as UTC and restore awareness after SQLite reads."""

    impl = DateTime
    cache_ok = True

    def __init__(self) -> None:
        super().__init__(timezone=True)

    def process_bind_param(
        self,
        value: datetime | None,
        _dialect: Dialect,
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("UTCDateTime requires a timezone-aware value")
        return value.astimezone(UTC)

    def process_result_value(
        self,
        value: datetime | None,
        _dialect: Dialect,
    ) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class AuditLog(Base):
    """Immutable-by-convention audit row.

    No update or delete method is exposed by ``AuditRepository``. Transaction
    commit remains the calling use case's responsibility.
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_occurred_at", "occurred_at"),
        Index("ix_audit_log_target", "target_type", "target_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    occurred_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        nullable=False,
        default=_utc_now,
    )
    actor_id: Mapped[str] = mapped_column(String(120), nullable=False)
    actor_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_roles: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    target_type: Mapped[str] = mapped_column(String(120), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    before_state: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    after_state: Mapped[dict[str, object] | None] = mapped_column(JSON, nullable=True)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    requirement_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_reference: Mapped[str | None] = mapped_column(String(500), nullable=True)


class AuditRepository:
    def append(self, session: Session, change: AuditChange) -> AuditLog:
        record = AuditLog(
            actor_id=change.actor.actor_id,
            actor_kind=change.actor.kind.value,
            actor_roles=sorted(role.value for role in change.actor.roles),
            action=change.action,
            target_type=change.target_type,
            target_id=change.target_id,
            before_state=(dict(change.before_state) if change.before_state is not None else None),
            after_state=(dict(change.after_state) if change.after_state is not None else None),
            reason=change.reason,
            requirement_id=change.requirement_id,
            source_reference=change.source_reference,
        )
        session.add(record)
        session.flush()
        return record

    def list_recent(self, session: Session, *, limit: int = 100) -> Sequence[AuditLog]:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        statement = select(AuditLog).order_by(AuditLog.occurred_at.desc()).limit(limit)
        return session.scalars(statement).all()
