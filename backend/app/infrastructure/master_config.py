"""SQLAlchemy persistence for canonical hierarchy and approved configuration."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    ForeignKeyConstraint,
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

from app.domain.long_format import CanonicalRowBinding, CanonicalRowBindingKey
from app.domain.master_config import (
    CanonicalInspectionItem,
    CanonicalModel,
    CanonicalModelPart,
    CanonicalRowBindingRevision,
    CanonicalRowBindingSupersessionDecision,
    CanonicalSupplier,
    ConfigurationRevisionStatus,
    EffectiveMasterSpecRevision,
    InspectionItemDisposition,
    MasterSpecRevision,
    MasterSpecSupersessionDecision,
    MaterializedMasterSpecCatalog,
)
from app.infrastructure.audit import UTCDateTime
from app.infrastructure.database import Base
from app.infrastructure.mapping_templates import (
    MappingTemplateHistoryRow,
    MappingTemplateRevisionRow,
)


def _new_id() -> str:
    return str(uuid4())


class CanonicalModelRow(Base):
    __tablename__ = "canonical_models"
    __table_args__ = (
        UniqueConstraint("project_key", "model_key", name="uq_canonical_model_key"),
        UniqueConstraint("project_key", "id", name="uq_canonical_model_project_id"),
        CheckConstraint("row_version >= 1", name="canonical_model_row_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    project_key: Mapped[str] = mapped_column(String(200), nullable=False)
    model_key: Mapped[str] = mapped_column(String(200), nullable=False)
    display_name: Mapped[str] = mapped_column(String(500), nullable=False)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class CanonicalSupplierRow(Base):
    __tablename__ = "canonical_suppliers"
    __table_args__ = (
        UniqueConstraint("project_key", "supplier_key", name="uq_canonical_supplier_key"),
        UniqueConstraint("project_key", "id", name="uq_canonical_supplier_project_id"),
        CheckConstraint("row_version >= 1", name="canonical_supplier_row_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    project_key: Mapped[str] = mapped_column(String(200), nullable=False)
    supplier_key: Mapped[str] = mapped_column(String(200), nullable=False)
    display_name: Mapped[str] = mapped_column(String(500), nullable=False)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class CanonicalModelPartRow(Base):
    __tablename__ = "canonical_model_parts"
    __table_args__ = (
        UniqueConstraint("project_key", "model_part_key", name="uq_canonical_model_part_key"),
        UniqueConstraint("project_key", "id", name="uq_canonical_model_part_project_id"),
        UniqueConstraint(
            "project_key",
            "id",
            "model_id",
            name="uq_canonical_model_part_project_id_model",
        ),
        ForeignKeyConstraint(
            ["project_key", "model_id"],
            ["canonical_models.project_key", "canonical_models.id"],
            name="fk_canonical_model_part_project_model",
            ondelete="RESTRICT",
        ),
        CheckConstraint("row_version >= 1", name="canonical_model_part_row_version"),
        Index("ix_canonical_model_parts_model", "project_key", "model_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    project_key: Mapped[str] = mapped_column(String(200), nullable=False)
    model_id: Mapped[str] = mapped_column(String(36), nullable=False)
    model_part_key: Mapped[str] = mapped_column(String(200), nullable=False)
    display_name: Mapped[str] = mapped_column(String(500), nullable=False)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class CanonicalInspectionItemRow(Base):
    __tablename__ = "canonical_inspection_items"
    __table_args__ = (
        UniqueConstraint("project_key", "item_key", name="uq_canonical_inspection_item_key"),
        UniqueConstraint("project_key", "id", name="uq_canonical_inspection_item_project_id"),
        UniqueConstraint(
            "project_key",
            "id",
            "model_part_id",
            name="uq_canonical_inspection_item_project_id_part",
        ),
        ForeignKeyConstraint(
            ["project_key", "model_part_id"],
            ["canonical_model_parts.project_key", "canonical_model_parts.id"],
            name="fk_canonical_inspection_item_project_part",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "disposition IN ('CANDIDATE', 'MANAGED', 'EXCLUDED')",
            name="canonical_inspection_item_disposition",
        ),
        CheckConstraint("row_version >= 1", name="canonical_inspection_item_row_version"),
        Index("ix_canonical_inspection_items_part", "project_key", "model_part_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    project_key: Mapped[str] = mapped_column(String(200), nullable=False)
    model_part_id: Mapped[str] = mapped_column(String(36), nullable=False)
    item_key: Mapped[str] = mapped_column(String(200), nullable=False)
    display_name: Mapped[str] = mapped_column(String(500), nullable=False)
    disposition: Mapped[str] = mapped_column(String(24), nullable=False)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class MasterSpecHistoryRow(Base):
    __tablename__ = "master_spec_histories"
    __table_args__ = (
        UniqueConstraint("project_key", "item_id", name="uq_master_spec_history_item"),
        UniqueConstraint("project_key", "id", name="uq_master_spec_history_project_id"),
        ForeignKeyConstraint(
            ["project_key", "item_id"],
            ["canonical_inspection_items.project_key", "canonical_inspection_items.id"],
            name="fk_master_spec_history_project_item",
            ondelete="RESTRICT",
        ),
        CheckConstraint("row_version >= 0", name="master_spec_history_row_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    project_key: Mapped[str] = mapped_column(String(200), nullable=False)
    item_id: Mapped[str] = mapped_column(String(36), nullable=False)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class MasterSpecRevisionRow(Base):
    __tablename__ = "master_spec_revisions"
    __table_args__ = (
        UniqueConstraint("history_id", "revision", name="uq_master_spec_history_revision"),
        UniqueConstraint(
            "project_key",
            "id",
            "history_id",
            name="uq_master_spec_revision_project_id_history",
        ),
        ForeignKeyConstraint(
            ["project_key", "history_id"],
            ["master_spec_histories.project_key", "master_spec_histories.id"],
            name="fk_master_spec_revision_project_history",
            ondelete="RESTRICT",
        ),
        CheckConstraint("revision >= 1", name="master_spec_revision_positive"),
        CheckConstraint("row_version >= 1", name="master_spec_revision_row_version"),
        CheckConstraint("length(payload_sha256) = 64", name="master_spec_payload_digest_length"),
        CheckConstraint(
            "status IN ('DRAFT', 'REVIEWED', 'APPROVED')",
            name="master_spec_revision_status",
        ),
        CheckConstraint(
            "declared_effective_to IS NULL OR declared_effective_to >= declared_effective_from",
            name="master_spec_declared_effectivity",
        ),
        CheckConstraint(
            "resolved_effective_to IS NULL OR resolved_effective_to >= declared_effective_from",
            name="master_spec_resolved_effectivity",
        ),
        CheckConstraint(
            "resolved_effective_to IS NULL OR declared_effective_to IS NULL "
            "OR resolved_effective_to <= declared_effective_to",
            name="master_spec_resolved_does_not_extend_declared",
        ),
        CheckConstraint(
            "(status = 'DRAFT' AND reviewed_by IS NULL AND reviewed_at IS NULL "
            "AND approved_by IS NULL AND approved_at IS NULL) OR "
            "(status = 'REVIEWED' AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL "
            "AND approved_by IS NULL AND approved_at IS NULL) OR "
            "(status = 'APPROVED' AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL "
            "AND approved_by IS NOT NULL AND approved_at IS NOT NULL)",
            name="master_spec_workflow_metadata",
        ),
        Index("ix_master_spec_revisions_history", "project_key", "history_id"),
        Index(
            "ix_master_spec_revisions_effective", "project_key", "status", "declared_effective_from"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    project_key: Mapped[str] = mapped_column(String(200), nullable=False)
    history_id: Mapped[str] = mapped_column(String(36), nullable=False)
    revision_number: Mapped[int] = mapped_column("revision", Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    spec_payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
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


class MasterSpecSupersessionRow(Base):
    __tablename__ = "master_spec_supersessions"
    __table_args__ = (
        UniqueConstraint("predecessor_revision_id", name="uq_master_spec_supersession_predecessor"),
        UniqueConstraint("successor_revision_id", name="uq_master_spec_supersession_successor"),
        ForeignKeyConstraint(
            ["project_key", "history_id"],
            ["master_spec_histories.project_key", "master_spec_histories.id"],
            name="fk_master_spec_supersession_project_history",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_key", "predecessor_revision_id", "history_id"],
            [
                "master_spec_revisions.project_key",
                "master_spec_revisions.id",
                "master_spec_revisions.history_id",
            ],
            name="fk_master_spec_supersession_predecessor",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_key", "successor_revision_id", "history_id"],
            [
                "master_spec_revisions.project_key",
                "master_spec_revisions.id",
                "master_spec_revisions.history_id",
            ],
            name="fk_master_spec_supersession_successor",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "predecessor_revision_id <> successor_revision_id",
            name="master_spec_supersession_distinct",
        ),
        Index("ix_master_spec_supersessions_history", "project_key", "history_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    project_key: Mapped[str] = mapped_column(String(200), nullable=False)
    history_id: Mapped[str] = mapped_column(String(36), nullable=False)
    predecessor_revision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    successor_revision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    predecessor_effective_to: Mapped[date] = mapped_column(Date, nullable=False)
    decided_by: Mapped[str] = mapped_column(String(120), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)


class CanonicalRowBindingHistoryRow(Base):
    __tablename__ = "canonical_row_binding_histories"
    __table_args__ = (
        UniqueConstraint(
            "project_key",
            "supplier_scope",
            "template_id",
            "template_revision",
            "row_key",
            name="uq_canonical_row_binding_history_key",
        ),
        UniqueConstraint("project_key", "id", name="uq_canonical_row_binding_history_project_id"),
        UniqueConstraint(
            "project_key",
            "id",
            "canonical_supplier_id",
            name="uq_canonical_row_binding_history_project_id_supplier",
        ),
        ForeignKeyConstraint(
            ["project_key", "canonical_supplier_id"],
            ["canonical_suppliers.project_key", "canonical_suppliers.id"],
            name="fk_canonical_row_binding_history_supplier",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "project_key",
                "supplier_scope",
                "template_id",
                "mapping_history_id",
            ],
            [
                "mapping_template_histories.project_key",
                "mapping_template_histories.supplier_scope",
                "mapping_template_histories.template_id",
                "mapping_template_histories.id",
            ],
            name="fk_canonical_row_binding_history_mapping_history",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["mapping_history_id", "template_revision", "mapping_revision_id"],
            [
                "mapping_template_revisions.history_id",
                "mapping_template_revisions.revision",
                "mapping_template_revisions.id",
            ],
            name="fk_canonical_row_binding_history_mapping_revision",
            ondelete="RESTRICT",
        ),
        CheckConstraint("template_revision >= 1", name="canonical_row_binding_template_revision"),
        CheckConstraint("row_version >= 0", name="canonical_row_binding_history_row_version"),
        Index(
            "ix_canonical_row_binding_histories_mapping",
            "mapping_history_id",
            "mapping_revision_id",
        ),
        Index(
            "ix_canonical_row_binding_histories_supplier", "project_key", "canonical_supplier_id"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    project_key: Mapped[str] = mapped_column(String(200), nullable=False)
    supplier_scope: Mapped[str] = mapped_column(String(200), nullable=False)
    template_id: Mapped[str] = mapped_column(String(200), nullable=False)
    template_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    row_key: Mapped[str] = mapped_column(String(200), nullable=False)
    canonical_supplier_id: Mapped[str] = mapped_column(String(36), nullable=False)
    mapping_history_id: Mapped[str] = mapped_column(String(36), nullable=False)
    mapping_revision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class CanonicalRowBindingRevisionRow(Base):
    __tablename__ = "canonical_row_binding_revisions"
    __table_args__ = (
        UniqueConstraint(
            "history_id",
            "binding_revision",
            name="uq_canonical_row_binding_history_revision",
        ),
        UniqueConstraint(
            "project_key",
            "id",
            "history_id",
            name="uq_canonical_row_binding_revision_project_id_history",
        ),
        ForeignKeyConstraint(
            ["project_key", "history_id", "canonical_supplier_id"],
            [
                "canonical_row_binding_histories.project_key",
                "canonical_row_binding_histories.id",
                "canonical_row_binding_histories.canonical_supplier_id",
            ],
            name="fk_canonical_row_binding_revision_history",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_key", "canonical_model_id"],
            ["canonical_models.project_key", "canonical_models.id"],
            name="fk_canonical_row_binding_revision_model",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_key", "canonical_supplier_id"],
            ["canonical_suppliers.project_key", "canonical_suppliers.id"],
            name="fk_canonical_row_binding_revision_supplier",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_key", "canonical_model_part_id", "canonical_model_id"],
            [
                "canonical_model_parts.project_key",
                "canonical_model_parts.id",
                "canonical_model_parts.model_id",
            ],
            name="fk_canonical_row_binding_revision_model_part",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_key", "canonical_item_id", "canonical_model_part_id"],
            [
                "canonical_inspection_items.project_key",
                "canonical_inspection_items.id",
                "canonical_inspection_items.model_part_id",
            ],
            name="fk_canonical_row_binding_revision_item",
            ondelete="RESTRICT",
        ),
        CheckConstraint("binding_revision >= 1", name="canonical_row_binding_revision_positive"),
        CheckConstraint("row_version >= 1", name="canonical_row_binding_revision_row_version"),
        CheckConstraint(
            "length(payload_sha256) = 64",
            name="canonical_row_binding_payload_digest_length",
        ),
        CheckConstraint(
            "status IN ('DRAFT', 'REVIEWED', 'APPROVED')",
            name="canonical_row_binding_revision_status",
        ),
        CheckConstraint(
            "declared_effective_to IS NULL OR declared_effective_to >= declared_effective_from",
            name="canonical_row_binding_declared_effectivity",
        ),
        CheckConstraint(
            "resolved_effective_to IS NULL OR resolved_effective_to >= declared_effective_from",
            name="canonical_row_binding_resolved_effectivity",
        ),
        CheckConstraint(
            "resolved_effective_to IS NULL OR declared_effective_to IS NULL "
            "OR resolved_effective_to <= declared_effective_to",
            name="canonical_row_binding_resolved_does_not_extend_declared",
        ),
        CheckConstraint(
            "(status = 'DRAFT' AND reviewed_by IS NULL AND reviewed_at IS NULL "
            "AND approved_by IS NULL AND approved_at IS NULL) OR "
            "(status = 'REVIEWED' AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL "
            "AND approved_by IS NULL AND approved_at IS NULL) OR "
            "(status = 'APPROVED' AND reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL "
            "AND approved_by IS NOT NULL AND approved_at IS NOT NULL)",
            name="canonical_row_binding_workflow_metadata",
        ),
        Index("ix_canonical_row_binding_revisions_history", "project_key", "history_id"),
        Index(
            "ix_canonical_row_binding_revisions_effective",
            "project_key",
            "status",
            "declared_effective_from",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    project_key: Mapped[str] = mapped_column(String(200), nullable=False)
    history_id: Mapped[str] = mapped_column(String(36), nullable=False)
    binding_revision: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    binding_payload: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_model_id: Mapped[str] = mapped_column(String(36), nullable=False)
    canonical_supplier_id: Mapped[str] = mapped_column(String(36), nullable=False)
    canonical_model_part_id: Mapped[str] = mapped_column(String(36), nullable=False)
    canonical_item_id: Mapped[str] = mapped_column(String(36), nullable=False)
    declared_effective_from: Mapped[date] = mapped_column(Date, nullable=False)
    declared_effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    resolved_effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)


class CanonicalRowBindingSupersessionRow(Base):
    __tablename__ = "canonical_row_binding_supersessions"
    __table_args__ = (
        UniqueConstraint(
            "predecessor_revision_id",
            name="uq_canonical_row_binding_supersession_predecessor",
        ),
        UniqueConstraint(
            "successor_revision_id",
            name="uq_canonical_row_binding_supersession_successor",
        ),
        ForeignKeyConstraint(
            ["project_key", "history_id"],
            ["canonical_row_binding_histories.project_key", "canonical_row_binding_histories.id"],
            name="fk_canonical_row_binding_supersession_history",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_key", "predecessor_revision_id", "history_id"],
            [
                "canonical_row_binding_revisions.project_key",
                "canonical_row_binding_revisions.id",
                "canonical_row_binding_revisions.history_id",
            ],
            name="fk_canonical_row_binding_supersession_predecessor",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_key", "successor_revision_id", "history_id"],
            [
                "canonical_row_binding_revisions.project_key",
                "canonical_row_binding_revisions.id",
                "canonical_row_binding_revisions.history_id",
            ],
            name="fk_canonical_row_binding_supersession_successor",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "predecessor_revision_id <> successor_revision_id",
            name="canonical_row_binding_supersession_distinct",
        ),
        Index("ix_canonical_row_binding_supersessions_history", "project_key", "history_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    project_key: Mapped[str] = mapped_column(String(200), nullable=False)
    history_id: Mapped[str] = mapped_column(String(36), nullable=False)
    predecessor_revision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    successor_revision_id: Mapped[str] = mapped_column(String(36), nullable=False)
    predecessor_effective_to: Mapped[date] = mapped_column(Date, nullable=False)
    decided_by: Mapped[str] = mapped_column(String(120), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)


@dataclass(frozen=True, slots=True)
class PersistedCanonicalModel:
    model: CanonicalModel
    row_id: str
    row_version: int


@dataclass(frozen=True, slots=True)
class PersistedCanonicalSupplier:
    supplier: CanonicalSupplier
    row_id: str
    row_version: int


@dataclass(frozen=True, slots=True)
class PersistedCanonicalModelPart:
    model_part: CanonicalModelPart
    row_id: str
    model_id: str
    row_version: int


@dataclass(frozen=True, slots=True)
class PersistedCanonicalInspectionItem:
    item: CanonicalInspectionItem
    row_id: str
    model_part_id: str
    row_version: int


@dataclass(frozen=True, slots=True)
class InspectionItemDispositionMutation:
    before: PersistedCanonicalInspectionItem
    after: PersistedCanonicalInspectionItem


@dataclass(frozen=True, slots=True)
class PersistedMasterSpecRevision:
    spec: MasterSpecRevision
    history_id: str
    revision_id: str
    history_row_version: int
    revision_row_version: int
    resolved_effective_to: date | None
    payload_sha256: str


@dataclass(frozen=True, slots=True)
class MasterSpecWorkflowMutation:
    before: PersistedMasterSpecRevision
    after: PersistedMasterSpecRevision


@dataclass(frozen=True, slots=True)
class PersistedMasterSpecSupersession:
    decision: MasterSpecSupersessionDecision
    predecessor: PersistedMasterSpecRevision
    successor: PersistedMasterSpecRevision


@dataclass(frozen=True, slots=True)
class PersistedCanonicalRowBindingRevision:
    binding: CanonicalRowBindingRevision
    history_id: str
    revision_id: str
    history_row_version: int
    revision_row_version: int
    resolved_effective_to: date | None
    payload_sha256: str


@dataclass(frozen=True, slots=True)
class CanonicalRowBindingWorkflowMutation:
    before: PersistedCanonicalRowBindingRevision
    after: PersistedCanonicalRowBindingRevision


@dataclass(frozen=True, slots=True)
class PersistedCanonicalRowBindingSupersession:
    decision: CanonicalRowBindingSupersessionDecision
    predecessor: PersistedCanonicalRowBindingRevision
    successor: PersistedCanonicalRowBindingRevision


class MasterConfigPersistenceError(RuntimeError):
    """Base storage error for project-local configuration commands."""


class MasterConfigNotFoundError(MasterConfigPersistenceError):
    pass


class MasterConfigScopeError(MasterConfigPersistenceError):
    pass


class StaleMasterConfigWriteError(MasterConfigPersistenceError):
    pass


class ImmutableMasterConfigRevisionError(MasterConfigPersistenceError):
    pass


class MasterConfigPayloadIntegrityError(MasterConfigPersistenceError):
    pass


class MasterConfigEffectivePeriodError(MasterConfigPersistenceError):
    pass


class PersistentCanonicalRowBindingCatalog:
    """Read-only as-of snapshot retaining declared and resolved periods."""

    def __init__(
        self,
        *,
        project_key: str,
        as_of: date,
        records: tuple[PersistedCanonicalRowBindingRevision, ...],
    ) -> None:
        _require_exact(project_key, "project_key")
        _require_date(as_of, "as_of")
        keys = tuple(record.binding.key for record in records)
        if len(set(keys)) != len(keys):
            raise MasterConfigEffectivePeriodError(
                "multiple approved row-binding revisions are effective for one exact key"
            )
        if any(record.binding.key.project_key != project_key for record in records):
            raise MasterConfigScopeError("row-binding catalog cannot mix projects")
        self.project_key = project_key
        self.as_of = as_of
        self.records = records
        payload = {
            "project_key": project_key,
            "as_of": as_of.isoformat(),
            "bindings": [
                {
                    "signature": _operational_binding(record).signature.version_parts,
                    "declared_effective_to": (
                        record.binding.effective_to.isoformat()
                        if record.binding.effective_to is not None
                        else None
                    ),
                    "resolved_effective_to": (
                        record.resolved_effective_to.isoformat()
                        if record.resolved_effective_to is not None
                        else None
                    ),
                    "payload_sha256": record.payload_sha256,
                }
                for record in records
            ],
        }
        self.catalog_revision = f"sha256:{_payload_digest(payload)}"

    def find(self, key: CanonicalRowBindingKey) -> tuple[CanonicalRowBinding, ...]:
        return tuple(
            _operational_binding(record) for record in self.records if record.binding.key == key
        )


class MasterConfigRepository:
    """Explicit-CAS repository; callers own commit and Audit transaction boundaries."""

    def create_model(
        self,
        session: Session,
        model: CanonicalModel,
        *,
        created_at: datetime,
    ) -> PersistedCanonicalModel:
        _require_aware(created_at, "created_at")
        row = CanonicalModelRow(
            project_key=model.project_key,
            model_key=model.model_key,
            display_name=model.display_name,
            row_version=1,
            created_at=created_at,
        )
        session.add(row)
        _flush_identity(session, "canonical model already exists or violates project scope")
        return PersistedCanonicalModel(model=model, row_id=row.id, row_version=1)

    def create_supplier(
        self,
        session: Session,
        supplier: CanonicalSupplier,
        *,
        created_at: datetime,
    ) -> PersistedCanonicalSupplier:
        _require_aware(created_at, "created_at")
        row = CanonicalSupplierRow(
            project_key=supplier.project_key,
            supplier_key=supplier.supplier_key,
            display_name=supplier.display_name,
            row_version=1,
            created_at=created_at,
        )
        session.add(row)
        _flush_identity(session, "canonical supplier already exists or violates project scope")
        return PersistedCanonicalSupplier(supplier=supplier, row_id=row.id, row_version=1)

    def create_model_part(
        self,
        session: Session,
        model_part: CanonicalModelPart,
        *,
        created_at: datetime,
    ) -> PersistedCanonicalModelPart:
        _require_aware(created_at, "created_at")
        model = self._model(session, model_part.project_key, model_part.model_key)
        row = CanonicalModelPartRow(
            project_key=model_part.project_key,
            model_id=model.id,
            model_part_key=model_part.model_part_key,
            display_name=model_part.display_name,
            row_version=1,
            created_at=created_at,
        )
        session.add(row)
        _flush_identity(session, "canonical model-part already exists or violates project scope")
        return PersistedCanonicalModelPart(
            model_part=model_part,
            row_id=row.id,
            model_id=model.id,
            row_version=1,
        )

    def create_inspection_item(
        self,
        session: Session,
        item: CanonicalInspectionItem,
        *,
        created_at: datetime,
    ) -> PersistedCanonicalInspectionItem:
        _require_aware(created_at, "created_at")
        if item.disposition != InspectionItemDisposition.CANDIDATE:
            raise MasterConfigScopeError("new inspection items must start as CANDIDATE")
        model_part = self._model_part(session, item.project_key, item.model_part_key)
        row = CanonicalInspectionItemRow(
            project_key=item.project_key,
            model_part_id=model_part.id,
            item_key=item.item_key,
            display_name=item.display_name,
            disposition=item.disposition.value,
            row_version=1,
            created_at=created_at,
        )
        session.add(row)
        _flush_identity(
            session, "canonical inspection item already exists or violates project scope"
        )
        return PersistedCanonicalInspectionItem(
            item=item,
            row_id=row.id,
            model_part_id=model_part.id,
            row_version=1,
        )

    def set_item_disposition(
        self,
        session: Session,
        *,
        project_key: str,
        item_key: str,
        disposition: InspectionItemDisposition,
        expected_row_version: int,
    ) -> InspectionItemDispositionMutation:
        row = self._item(session, project_key, item_key)
        if row.row_version != expected_row_version:
            raise StaleMasterConfigWriteError("inspection item row_version is stale")
        before = self._to_item_record(session, row)
        self._cas(
            session,
            CanonicalInspectionItemRow,
            row.id,
            expected_row_version,
            disposition=disposition.value,
        )
        return InspectionItemDispositionMutation(
            before=before,
            after=replace(
                before,
                item=replace(before.item, disposition=disposition),
                row_version=expected_row_version + 1,
            ),
        )

    def get_model(
        self, session: Session, *, project_key: str, model_key: str
    ) -> PersistedCanonicalModel:
        row = self._model(session, project_key, model_key)
        return PersistedCanonicalModel(
            model=CanonicalModel(row.project_key, row.model_key, row.display_name),
            row_id=row.id,
            row_version=row.row_version,
        )

    def get_supplier(
        self, session: Session, *, project_key: str, supplier_key: str
    ) -> PersistedCanonicalSupplier:
        row = self._supplier(session, project_key, supplier_key)
        return PersistedCanonicalSupplier(
            supplier=CanonicalSupplier(row.project_key, row.supplier_key, row.display_name),
            row_id=row.id,
            row_version=row.row_version,
        )

    def get_model_part(
        self, session: Session, *, project_key: str, model_part_key: str
    ) -> PersistedCanonicalModelPart:
        row = self._model_part(session, project_key, model_part_key)
        model = session.get(CanonicalModelRow, row.model_id)
        if model is None or model.project_key != project_key:
            raise MasterConfigPayloadIntegrityError("model-part parent scope is invalid")
        return PersistedCanonicalModelPart(
            model_part=CanonicalModelPart(
                project_key,
                model.model_key,
                row.model_part_key,
                row.display_name,
            ),
            row_id=row.id,
            model_id=row.model_id,
            row_version=row.row_version,
        )

    def get_inspection_item(
        self, session: Session, *, project_key: str, item_key: str
    ) -> PersistedCanonicalInspectionItem:
        return self._to_item_record(session, self._item(session, project_key, item_key))

    def create_master_spec_draft(
        self,
        session: Session,
        spec: MasterSpecRevision,
        *,
        expected_history_row_version: int,
        created_at: datetime,
    ) -> PersistedMasterSpecRevision:
        if spec.status != ConfigurationRevisionStatus.DRAFT:
            raise ImmutableMasterConfigRevisionError("a Master Spec revision must start as DRAFT")
        _require_aware(created_at, "created_at")
        item = self._item(session, spec.project_key, spec.canonical_item_key)
        history = self._master_history(session, spec.project_key, item.id)
        if history is None:
            if expected_history_row_version != 0:
                raise StaleMasterConfigWriteError("Master Spec history row_version is stale")
            history = MasterSpecHistoryRow(
                project_key=spec.project_key,
                item_id=item.id,
                row_version=0,
                created_at=created_at,
            )
            session.add(history)
            try:
                session.flush()
            except IntegrityError as error:
                raise StaleMasterConfigWriteError(
                    "Master Spec history was concurrently created"
                ) from error
        elif history.row_version != expected_history_row_version:
            raise StaleMasterConfigWriteError("Master Spec history row_version is stale")
        rows = self._master_revision_rows(session, history.id)
        self._assert_append_only_revision(rows, spec.revision, "Master Spec")
        self._cas(session, MasterSpecHistoryRow, history.id, expected_history_row_version)
        payload = _serialize_master_spec(spec)
        row = MasterSpecRevisionRow(
            project_key=spec.project_key,
            history_id=history.id,
            revision_number=spec.revision,
            status=ConfigurationRevisionStatus.DRAFT.value,
            spec_payload=payload,
            payload_sha256=_payload_digest(payload),
            declared_effective_from=spec.effective_from,
            declared_effective_to=spec.effective_to,
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
            raise ImmutableMasterConfigRevisionError(
                "a Master Spec revision identity is immutable"
            ) from error
        return PersistedMasterSpecRevision(
            spec=spec,
            history_id=history.id,
            revision_id=row.id,
            history_row_version=expected_history_row_version + 1,
            revision_row_version=1,
            resolved_effective_to=None,
            payload_sha256=row.payload_sha256,
        )

    def review_master_spec(
        self,
        session: Session,
        *,
        project_key: str,
        canonical_item_key: str,
        revision: int,
        expected_history_row_version: int,
        expected_revision_row_version: int,
        reviewed_by: str,
        reviewed_at: datetime,
    ) -> MasterSpecWorkflowMutation:
        _require_aware(reviewed_at, "reviewed_at")
        history, row, item = self._load_master_rows(
            session, project_key, canonical_item_key, revision
        )
        self._assert_versions(
            history.row_version,
            row.row_version,
            expected_history_row_version,
            expected_revision_row_version,
            "Master Spec",
        )
        before = self._to_master_record(history, row, item)
        reviewed = before.spec.reviewed(actor_id=reviewed_by, occurred_at=reviewed_at)
        self._cas(session, MasterSpecHistoryRow, history.id, expected_history_row_version)
        self._cas(
            session,
            MasterSpecRevisionRow,
            row.id,
            expected_revision_row_version,
            status=ConfigurationRevisionStatus.REVIEWED.value,
            reviewed_by=reviewed_by,
            reviewed_at=reviewed_at,
        )
        return MasterSpecWorkflowMutation(
            before=before,
            after=replace(
                before,
                spec=reviewed,
                history_row_version=expected_history_row_version + 1,
                revision_row_version=expected_revision_row_version + 1,
            ),
        )

    def approve_master_spec(
        self,
        session: Session,
        *,
        project_key: str,
        canonical_item_key: str,
        revision: int,
        expected_history_row_version: int,
        expected_revision_row_version: int,
        approved_by: str,
        approved_at: datetime,
    ) -> MasterSpecWorkflowMutation:
        _require_aware(approved_at, "approved_at")
        history, row, item = self._load_master_rows(
            session, project_key, canonical_item_key, revision
        )
        self._assert_versions(
            history.row_version,
            row.row_version,
            expected_history_row_version,
            expected_revision_row_version,
            "Master Spec",
        )
        if item.disposition != InspectionItemDisposition.MANAGED.value:
            raise MasterConfigScopeError("only a MANAGED item may have an approved Master Spec")
        before = self._to_master_record(history, row, item)
        approved = before.spec.approved(actor_id=approved_by, occurred_at=approved_at)
        if self._master_approved_overlaps(session, history.id, row):
            raise MasterConfigEffectivePeriodError(
                "approved Master Spec revisions for one item cannot overlap"
            )
        self._cas(session, MasterSpecHistoryRow, history.id, expected_history_row_version)
        self._cas(
            session,
            MasterSpecRevisionRow,
            row.id,
            expected_revision_row_version,
            status=ConfigurationRevisionStatus.APPROVED.value,
            approved_by=approved_by,
            approved_at=approved_at,
        )
        return MasterSpecWorkflowMutation(
            before=before,
            after=replace(
                before,
                spec=approved,
                history_row_version=expected_history_row_version + 1,
                revision_row_version=expected_revision_row_version + 1,
            ),
        )

    def supersede_master_spec(
        self,
        session: Session,
        *,
        project_key: str,
        canonical_item_key: str,
        predecessor_revision: int,
        successor_revision: int,
        expected_history_row_version: int,
        expected_predecessor_row_version: int,
        expected_successor_row_version: int,
        decided_by: str,
        decided_at: datetime,
        reason: str,
    ) -> PersistedMasterSpecSupersession:
        _require_aware(decided_at, "decided_at")
        history, predecessor_row, item = self._load_master_rows(
            session, project_key, canonical_item_key, predecessor_revision
        )
        successor_row = self._master_revision_row(session, history.id, successor_revision)
        if successor_row is None:
            raise MasterConfigNotFoundError("successor Master Spec revision was not found")
        self._assert_versions(
            history.row_version,
            predecessor_row.row_version,
            expected_history_row_version,
            expected_predecessor_row_version,
            "Master Spec",
        )
        if successor_row.row_version != expected_successor_row_version:
            raise StaleMasterConfigWriteError("Master Spec successor row_version is stale")
        if item.disposition != InspectionItemDisposition.MANAGED.value:
            raise MasterConfigScopeError("only a MANAGED item may have an approved Master Spec")
        predecessor = self._to_master_record(history, predecessor_row, item)
        successor = self._to_master_record(history, successor_row, item)
        self._assert_supersession_states(predecessor.spec, successor.spec, "Master Spec")
        if successor_revision <= predecessor_revision:
            raise ImmutableMasterConfigRevisionError(
                "Master Spec successor revision must be greater than predecessor"
            )
        if self._master_supersession_exists(session, predecessor_row.id, successor_row.id):
            raise ImmutableMasterConfigRevisionError("Master Spec supersession already exists")
        predecessor_end = self._validate_supersession_period(
            self._master_approved_rows(session, history.id),
            predecessor_row,
            successor_row,
            subject="Master Spec",
        )
        self._cas(session, MasterSpecHistoryRow, history.id, expected_history_row_version)
        self._cas(
            session,
            MasterSpecRevisionRow,
            predecessor_row.id,
            expected_predecessor_row_version,
            resolved_effective_to=predecessor_end,
        )
        self._cas(
            session,
            MasterSpecRevisionRow,
            successor_row.id,
            expected_successor_row_version,
            status=ConfigurationRevisionStatus.APPROVED.value,
            approved_by=decided_by,
            approved_at=decided_at,
        )
        decision = MasterSpecSupersessionDecision(
            project_key=project_key,
            canonical_item_key=canonical_item_key,
            predecessor_revision=predecessor_revision,
            successor_revision=successor_revision,
            predecessor_effective_to=predecessor_end,
            decided_by=decided_by,
            decided_at=decided_at,
            reason=reason,
        )
        session.add(
            MasterSpecSupersessionRow(
                project_key=project_key,
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
            raise ImmutableMasterConfigRevisionError(
                "Master Spec supersession already exists or violates scope"
            ) from error
        approved_successor = successor.spec.approved(actor_id=decided_by, occurred_at=decided_at)
        return PersistedMasterSpecSupersession(
            decision=decision,
            predecessor=replace(
                predecessor,
                history_row_version=expected_history_row_version + 1,
                revision_row_version=expected_predecessor_row_version + 1,
                resolved_effective_to=predecessor_end,
            ),
            successor=replace(
                successor,
                spec=approved_successor,
                history_row_version=expected_history_row_version + 1,
                revision_row_version=expected_successor_row_version + 1,
            ),
        )

    def get_master_spec(
        self,
        session: Session,
        *,
        project_key: str,
        canonical_item_key: str,
        revision: int,
    ) -> PersistedMasterSpecRevision:
        history, row, item = self._load_master_rows(
            session, project_key, canonical_item_key, revision
        )
        return self._to_master_record(history, row, item)

    def load_master_spec_catalog(
        self,
        session: Session,
        *,
        project_key: str,
        as_of: date,
    ) -> MaterializedMasterSpecCatalog:
        _require_exact(project_key, "project_key")
        _require_date(as_of, "as_of")
        rows = session.execute(
            select(MasterSpecHistoryRow, MasterSpecRevisionRow, CanonicalInspectionItemRow)
            .join(
                MasterSpecRevisionRow,
                (MasterSpecRevisionRow.project_key == MasterSpecHistoryRow.project_key)
                & (MasterSpecRevisionRow.history_id == MasterSpecHistoryRow.id),
            )
            .join(
                CanonicalInspectionItemRow,
                (CanonicalInspectionItemRow.project_key == MasterSpecHistoryRow.project_key)
                & (CanonicalInspectionItemRow.id == MasterSpecHistoryRow.item_id),
            )
            .where(
                MasterSpecHistoryRow.project_key == project_key,
                MasterSpecRevisionRow.status == ConfigurationRevisionStatus.APPROVED.value,
                CanonicalInspectionItemRow.disposition == InspectionItemDisposition.MANAGED.value,
            )
            .order_by(CanonicalInspectionItemRow.item_key, MasterSpecRevisionRow.revision_number)
        ).all()
        records = tuple(
            self._to_master_record(history, row, item)
            for history, row, item in rows
            if _effective_on(
                row.declared_effective_from,
                row.resolved_effective_to or row.declared_effective_to,
                as_of,
            )
        )
        revisions = tuple(
            EffectiveMasterSpecRevision(
                spec=record.spec,
                resolved_effective_to=record.resolved_effective_to,
            )
            for record in records
        )
        try:
            return MaterializedMasterSpecCatalog(
                project_key=project_key,
                as_of=as_of,
                revisions=revisions,
            )
        except ValueError as error:
            raise MasterConfigEffectivePeriodError(str(error)) from error

    def find_effective_master_spec_records(
        self,
        session: Session,
        *,
        project_key: str,
        canonical_item_key: str,
        as_of: date,
        lock: bool = False,
    ) -> tuple[PersistedMasterSpecRevision, ...]:
        """Return exact persisted APPROVED records without collapsing their IDs.

        The data-review boundary needs the historical history/revision identities,
        row versions, digest, and both declared/resolved periods.  Unlike the
        materialized catalog, this method deliberately returns every matching row
        so a corrupted overlapping configuration is visible and can fail closed.
        """

        _require_exact(project_key, "project_key")
        _require_exact(canonical_item_key, "canonical_item_key")
        _require_date(as_of, "as_of")
        item_statement = select(CanonicalInspectionItemRow).where(
            CanonicalInspectionItemRow.project_key == project_key,
            CanonicalInspectionItemRow.item_key == canonical_item_key,
        )
        if lock:
            item_statement = item_statement.with_for_update()
        item = session.scalar(item_statement)
        if item is None:
            raise MasterConfigNotFoundError("canonical inspection item was not found")
        history_statement = select(MasterSpecHistoryRow).where(
            MasterSpecHistoryRow.project_key == project_key,
            MasterSpecHistoryRow.item_id == item.id,
        )
        if lock:
            history_statement = history_statement.with_for_update()
        history = session.scalar(history_statement)
        if history is None:
            return ()
        revision_statement = (
            select(MasterSpecRevisionRow)
            .where(
                MasterSpecRevisionRow.project_key == project_key,
                MasterSpecRevisionRow.history_id == history.id,
                MasterSpecRevisionRow.status == ConfigurationRevisionStatus.APPROVED.value,
            )
            .order_by(MasterSpecRevisionRow.revision_number, MasterSpecRevisionRow.id)
        )
        if lock:
            revision_statement = revision_statement.with_for_update()
        rows = session.scalars(revision_statement).all()
        return tuple(
            self._to_master_record(history, row, item)
            for row in rows
            if _effective_on(
                row.declared_effective_from,
                row.resolved_effective_to or row.declared_effective_to,
                as_of,
            )
        )

    def create_row_binding_draft(
        self,
        session: Session,
        binding: CanonicalRowBindingRevision,
        *,
        expected_history_row_version: int,
        created_at: datetime,
    ) -> PersistedCanonicalRowBindingRevision:
        if binding.status != ConfigurationRevisionStatus.DRAFT:
            raise ImmutableMasterConfigRevisionError("a row-binding revision must start as DRAFT")
        _require_aware(created_at, "created_at")
        mapping_history, mapping_revision = self._mapping_scope_rows(session, binding)
        model, supplier, model_part, item = self._binding_canonical_rows(session, binding)
        history = self._binding_history(session, binding)
        if history is None:
            if expected_history_row_version != 0:
                raise StaleMasterConfigWriteError("row-binding history row_version is stale")
            history = CanonicalRowBindingHistoryRow(
                project_key=binding.key.project_key,
                supplier_scope=binding.key.supplier_scope,
                template_id=binding.key.template_id,
                template_revision=binding.key.template_revision,
                row_key=binding.key.row_key,
                canonical_supplier_id=supplier.id,
                mapping_history_id=mapping_history.id,
                mapping_revision_id=mapping_revision.id,
                row_version=0,
                created_at=created_at,
            )
            session.add(history)
            try:
                session.flush()
            except IntegrityError as error:
                raise StaleMasterConfigWriteError(
                    "row-binding history was concurrently created or violates exact scope"
                ) from error
        else:
            if history.row_version != expected_history_row_version:
                raise StaleMasterConfigWriteError("row-binding history row_version is stale")
            self._validate_binding_history_scope(
                session,
                history,
                canonical_supplier_id=supplier.id,
            )
        rows = self._binding_revision_rows(session, history.id)
        self._assert_append_only_revision(rows, binding.binding_revision, "row binding")
        self._cas(
            session,
            CanonicalRowBindingHistoryRow,
            history.id,
            expected_history_row_version,
        )
        payload = _serialize_row_binding(binding)
        row = CanonicalRowBindingRevisionRow(
            project_key=binding.key.project_key,
            history_id=history.id,
            binding_revision=binding.binding_revision,
            status=ConfigurationRevisionStatus.DRAFT.value,
            binding_payload=payload,
            payload_sha256=_payload_digest(payload),
            canonical_model_id=model.id,
            canonical_supplier_id=supplier.id,
            canonical_model_part_id=model_part.id,
            canonical_item_id=item.id,
            declared_effective_from=binding.effective_from,
            declared_effective_to=binding.effective_to,
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
            raise ImmutableMasterConfigRevisionError(
                "a row-binding revision identity is immutable or violates project hierarchy"
            ) from error
        return PersistedCanonicalRowBindingRevision(
            binding=binding,
            history_id=history.id,
            revision_id=row.id,
            history_row_version=expected_history_row_version + 1,
            revision_row_version=1,
            resolved_effective_to=None,
            payload_sha256=row.payload_sha256,
        )

    def review_row_binding(
        self,
        session: Session,
        *,
        key: CanonicalRowBindingKey,
        binding_revision: int,
        expected_history_row_version: int,
        expected_revision_row_version: int,
        reviewed_by: str,
        reviewed_at: datetime,
    ) -> CanonicalRowBindingWorkflowMutation:
        _require_aware(reviewed_at, "reviewed_at")
        history, row = self._load_binding_rows(session, key, binding_revision)
        self._assert_versions(
            history.row_version,
            row.row_version,
            expected_history_row_version,
            expected_revision_row_version,
            "row binding",
        )
        before = self._to_binding_record(session, history, row)
        reviewed = before.binding.reviewed(actor_id=reviewed_by, occurred_at=reviewed_at)
        self._cas(
            session,
            CanonicalRowBindingHistoryRow,
            history.id,
            expected_history_row_version,
        )
        self._cas(
            session,
            CanonicalRowBindingRevisionRow,
            row.id,
            expected_revision_row_version,
            status=ConfigurationRevisionStatus.REVIEWED.value,
            reviewed_by=reviewed_by,
            reviewed_at=reviewed_at,
        )
        return CanonicalRowBindingWorkflowMutation(
            before=before,
            after=replace(
                before,
                binding=reviewed,
                history_row_version=expected_history_row_version + 1,
                revision_row_version=expected_revision_row_version + 1,
            ),
        )

    def approve_row_binding(
        self,
        session: Session,
        *,
        key: CanonicalRowBindingKey,
        binding_revision: int,
        expected_history_row_version: int,
        expected_revision_row_version: int,
        approved_by: str,
        approved_at: datetime,
    ) -> CanonicalRowBindingWorkflowMutation:
        _require_aware(approved_at, "approved_at")
        history, row = self._load_binding_rows(session, key, binding_revision)
        self._assert_versions(
            history.row_version,
            row.row_version,
            expected_history_row_version,
            expected_revision_row_version,
            "row binding",
        )
        before = self._to_binding_record(session, history, row)
        self._require_binding_approvable(session, history, row)
        approved = before.binding.approved(actor_id=approved_by, occurred_at=approved_at)
        if self._binding_approved_overlaps(session, history.id, row):
            raise MasterConfigEffectivePeriodError(
                "approved row-binding revisions for one exact key cannot overlap"
            )
        self._cas(
            session,
            CanonicalRowBindingHistoryRow,
            history.id,
            expected_history_row_version,
        )
        self._cas(
            session,
            CanonicalRowBindingRevisionRow,
            row.id,
            expected_revision_row_version,
            status=ConfigurationRevisionStatus.APPROVED.value,
            approved_by=approved_by,
            approved_at=approved_at,
        )
        return CanonicalRowBindingWorkflowMutation(
            before=before,
            after=replace(
                before,
                binding=approved,
                history_row_version=expected_history_row_version + 1,
                revision_row_version=expected_revision_row_version + 1,
            ),
        )

    def supersede_row_binding(
        self,
        session: Session,
        *,
        key: CanonicalRowBindingKey,
        predecessor_revision: int,
        successor_revision: int,
        expected_history_row_version: int,
        expected_predecessor_row_version: int,
        expected_successor_row_version: int,
        decided_by: str,
        decided_at: datetime,
        reason: str,
    ) -> PersistedCanonicalRowBindingSupersession:
        _require_aware(decided_at, "decided_at")
        history, predecessor_row = self._load_binding_rows(session, key, predecessor_revision)
        successor_row = self._binding_revision_row(session, history.id, successor_revision)
        if successor_row is None:
            raise MasterConfigNotFoundError("successor row-binding revision was not found")
        self._assert_versions(
            history.row_version,
            predecessor_row.row_version,
            expected_history_row_version,
            expected_predecessor_row_version,
            "row binding",
        )
        if successor_row.row_version != expected_successor_row_version:
            raise StaleMasterConfigWriteError("row-binding successor row_version is stale")
        predecessor = self._to_binding_record(session, history, predecessor_row)
        successor = self._to_binding_record(session, history, successor_row)
        self._assert_supersession_states(predecessor.binding, successor.binding, "row binding")
        if successor_revision <= predecessor_revision:
            raise ImmutableMasterConfigRevisionError(
                "row-binding successor revision must be greater than predecessor"
            )
        self._require_binding_approvable(session, history, successor_row)
        if self._binding_supersession_exists(session, predecessor_row.id, successor_row.id):
            raise ImmutableMasterConfigRevisionError("row-binding supersession already exists")
        predecessor_end = self._validate_supersession_period(
            self._binding_approved_rows(session, history.id),
            predecessor_row,
            successor_row,
            subject="row binding",
        )
        self._cas(
            session,
            CanonicalRowBindingHistoryRow,
            history.id,
            expected_history_row_version,
        )
        self._cas(
            session,
            CanonicalRowBindingRevisionRow,
            predecessor_row.id,
            expected_predecessor_row_version,
            resolved_effective_to=predecessor_end,
        )
        self._cas(
            session,
            CanonicalRowBindingRevisionRow,
            successor_row.id,
            expected_successor_row_version,
            status=ConfigurationRevisionStatus.APPROVED.value,
            approved_by=decided_by,
            approved_at=decided_at,
        )
        decision = CanonicalRowBindingSupersessionDecision(
            key=predecessor.binding.key,
            predecessor_revision=predecessor_revision,
            successor_revision=successor_revision,
            predecessor_effective_to=predecessor_end,
            decided_by=decided_by,
            decided_at=decided_at,
            reason=reason,
        )
        session.add(
            CanonicalRowBindingSupersessionRow(
                project_key=history.project_key,
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
            raise ImmutableMasterConfigRevisionError(
                "row-binding supersession already exists or violates scope"
            ) from error
        approved_successor = successor.binding.approved(
            actor_id=decided_by,
            occurred_at=decided_at,
        )
        return PersistedCanonicalRowBindingSupersession(
            decision=decision,
            predecessor=replace(
                predecessor,
                history_row_version=expected_history_row_version + 1,
                revision_row_version=expected_predecessor_row_version + 1,
                resolved_effective_to=predecessor_end,
            ),
            successor=replace(
                successor,
                binding=approved_successor,
                history_row_version=expected_history_row_version + 1,
                revision_row_version=expected_successor_row_version + 1,
            ),
        )

    def get_row_binding(
        self,
        session: Session,
        *,
        key: CanonicalRowBindingKey,
        binding_revision: int,
    ) -> PersistedCanonicalRowBindingRevision:
        history, row = self._load_binding_rows(session, key, binding_revision)
        return self._to_binding_record(session, history, row)

    def load_row_binding_catalog(
        self,
        session: Session,
        *,
        project_key: str,
        as_of: date,
    ) -> PersistentCanonicalRowBindingCatalog:
        _require_exact(project_key, "project_key")
        _require_date(as_of, "as_of")
        rows = session.execute(
            select(CanonicalRowBindingHistoryRow, CanonicalRowBindingRevisionRow)
            .join(
                CanonicalRowBindingRevisionRow,
                (
                    CanonicalRowBindingRevisionRow.project_key
                    == CanonicalRowBindingHistoryRow.project_key
                )
                & (CanonicalRowBindingRevisionRow.history_id == CanonicalRowBindingHistoryRow.id)
                & (
                    CanonicalRowBindingRevisionRow.canonical_supplier_id
                    == CanonicalRowBindingHistoryRow.canonical_supplier_id
                ),
            )
            .where(
                CanonicalRowBindingHistoryRow.project_key == project_key,
                CanonicalRowBindingRevisionRow.status == ConfigurationRevisionStatus.APPROVED.value,
            )
            .order_by(
                CanonicalRowBindingHistoryRow.supplier_scope,
                CanonicalRowBindingHistoryRow.template_id,
                CanonicalRowBindingHistoryRow.template_revision,
                CanonicalRowBindingHistoryRow.row_key,
                CanonicalRowBindingRevisionRow.binding_revision,
            )
        ).all()
        records: list[PersistedCanonicalRowBindingRevision] = []
        for history, row in rows:
            if not _effective_on(
                row.declared_effective_from,
                row.resolved_effective_to or row.declared_effective_to,
                as_of,
            ):
                continue
            self._require_binding_approvable(session, history, row)
            records.append(self._to_binding_record(session, history, row))
        return PersistentCanonicalRowBindingCatalog(
            project_key=project_key,
            as_of=as_of,
            records=tuple(records),
        )

    @staticmethod
    def _model(session: Session, project_key: str, model_key: str) -> CanonicalModelRow:
        row = session.scalar(
            select(CanonicalModelRow).where(
                CanonicalModelRow.project_key == project_key,
                CanonicalModelRow.model_key == model_key,
            )
        )
        if row is None:
            raise MasterConfigNotFoundError("canonical model was not found in the project")
        return row

    @staticmethod
    def _supplier(
        session: Session,
        project_key: str,
        supplier_key: str,
    ) -> CanonicalSupplierRow:
        row = session.scalar(
            select(CanonicalSupplierRow).where(
                CanonicalSupplierRow.project_key == project_key,
                CanonicalSupplierRow.supplier_key == supplier_key,
            )
        )
        if row is None:
            raise MasterConfigNotFoundError("canonical supplier was not found in the project")
        return row

    @staticmethod
    def _model_part(
        session: Session,
        project_key: str,
        model_part_key: str,
    ) -> CanonicalModelPartRow:
        row = session.scalar(
            select(CanonicalModelPartRow).where(
                CanonicalModelPartRow.project_key == project_key,
                CanonicalModelPartRow.model_part_key == model_part_key,
            )
        )
        if row is None:
            raise MasterConfigNotFoundError("canonical model-part was not found in the project")
        return row

    @staticmethod
    def _item(
        session: Session,
        project_key: str,
        item_key: str,
    ) -> CanonicalInspectionItemRow:
        row = session.scalar(
            select(CanonicalInspectionItemRow).where(
                CanonicalInspectionItemRow.project_key == project_key,
                CanonicalInspectionItemRow.item_key == item_key,
            )
        )
        if row is None:
            raise MasterConfigNotFoundError(
                "canonical inspection item was not found in the project"
            )
        return row

    def _to_item_record(
        self,
        session: Session,
        row: CanonicalInspectionItemRow,
    ) -> PersistedCanonicalInspectionItem:
        part = session.get(CanonicalModelPartRow, row.model_part_id)
        if part is None or part.project_key != row.project_key:
            raise MasterConfigPayloadIntegrityError("inspection-item parent scope is invalid")
        return PersistedCanonicalInspectionItem(
            item=CanonicalInspectionItem(
                project_key=row.project_key,
                model_part_key=part.model_part_key,
                item_key=row.item_key,
                display_name=row.display_name,
                disposition=InspectionItemDisposition(row.disposition),
            ),
            row_id=row.id,
            model_part_id=row.model_part_id,
            row_version=row.row_version,
        )

    @staticmethod
    def _master_history(
        session: Session,
        project_key: str,
        item_id: str,
    ) -> MasterSpecHistoryRow | None:
        return session.scalar(
            select(MasterSpecHistoryRow).where(
                MasterSpecHistoryRow.project_key == project_key,
                MasterSpecHistoryRow.item_id == item_id,
            )
        )

    @staticmethod
    def _master_revision_rows(
        session: Session,
        history_id: str,
    ) -> list[MasterSpecRevisionRow]:
        return list(
            session.scalars(
                select(MasterSpecRevisionRow)
                .where(MasterSpecRevisionRow.history_id == history_id)
                .order_by(MasterSpecRevisionRow.revision_number)
            ).all()
        )

    @staticmethod
    def _master_revision_row(
        session: Session,
        history_id: str,
        revision: int,
    ) -> MasterSpecRevisionRow | None:
        return session.scalar(
            select(MasterSpecRevisionRow).where(
                MasterSpecRevisionRow.history_id == history_id,
                MasterSpecRevisionRow.revision_number == revision,
            )
        )

    def _load_master_rows(
        self,
        session: Session,
        project_key: str,
        canonical_item_key: str,
        revision: int,
    ) -> tuple[MasterSpecHistoryRow, MasterSpecRevisionRow, CanonicalInspectionItemRow]:
        item = self._item(session, project_key, canonical_item_key)
        history = self._master_history(session, project_key, item.id)
        if history is None:
            raise MasterConfigNotFoundError("Master Spec history was not found")
        row = self._master_revision_row(session, history.id, revision)
        if row is None or row.project_key != project_key:
            raise MasterConfigNotFoundError("Master Spec revision was not found")
        return history, row, item

    @staticmethod
    def _to_master_record(
        history: MasterSpecHistoryRow,
        row: MasterSpecRevisionRow,
        item: CanonicalInspectionItemRow,
    ) -> PersistedMasterSpecRevision:
        if history.project_key != row.project_key or history.project_key != item.project_key:
            raise MasterConfigScopeError("Master Spec rows cross a project boundary")
        if history.item_id != item.id or row.history_id != history.id:
            raise MasterConfigPayloadIntegrityError("Master Spec hierarchy references are invalid")
        if _payload_digest(row.spec_payload) != row.payload_sha256:
            raise MasterConfigPayloadIntegrityError("Master Spec payload digest does not match")
        spec = _deserialize_master_spec(row, item.item_key)
        return PersistedMasterSpecRevision(
            spec=spec,
            history_id=history.id,
            revision_id=row.id,
            history_row_version=history.row_version,
            revision_row_version=row.row_version,
            resolved_effective_to=row.resolved_effective_to,
            payload_sha256=row.payload_sha256,
        )

    @staticmethod
    def _mapping_scope_rows(
        session: Session,
        binding: CanonicalRowBindingRevision,
    ) -> tuple[MappingTemplateHistoryRow, MappingTemplateRevisionRow]:
        key = binding.key
        history = session.scalar(
            select(MappingTemplateHistoryRow).where(
                MappingTemplateHistoryRow.project_key == key.project_key,
                MappingTemplateHistoryRow.supplier_scope == key.supplier_scope,
                MappingTemplateHistoryRow.template_id == key.template_id,
            )
        )
        if history is None:
            raise MasterConfigScopeError("exact Mapping Template history scope was not found")
        revision = session.scalar(
            select(MappingTemplateRevisionRow).where(
                MappingTemplateRevisionRow.history_id == history.id,
                MappingTemplateRevisionRow.revision_number == key.template_revision,
            )
        )
        if revision is None:
            raise MasterConfigScopeError("exact Mapping Template revision scope was not found")
        if revision.status != ConfigurationRevisionStatus.APPROVED.value:
            raise MasterConfigScopeError(
                "row binding requires an APPROVED Mapping Template revision"
            )
        return history, revision

    def _binding_canonical_rows(
        self,
        session: Session,
        binding: CanonicalRowBindingRevision,
    ) -> tuple[
        CanonicalModelRow,
        CanonicalSupplierRow,
        CanonicalModelPartRow,
        CanonicalInspectionItemRow,
    ]:
        project_key = binding.key.project_key
        model = self._model(session, project_key, binding.canonical_model_key)
        supplier = self._supplier(session, project_key, binding.canonical_supplier_key)
        model_part = self._model_part(session, project_key, binding.canonical_model_part_key)
        item = self._item(session, project_key, binding.canonical_item_key)
        if model_part.model_id != model.id:
            raise MasterConfigScopeError("canonical model-part does not belong to the model")
        if item.model_part_id != model_part.id:
            raise MasterConfigScopeError("canonical item does not belong to the model-part")
        return model, supplier, model_part, item

    @staticmethod
    def _binding_history(
        session: Session,
        binding: CanonicalRowBindingRevision,
    ) -> CanonicalRowBindingHistoryRow | None:
        key = binding.key
        return session.scalar(
            select(CanonicalRowBindingHistoryRow).where(
                CanonicalRowBindingHistoryRow.project_key == key.project_key,
                CanonicalRowBindingHistoryRow.supplier_scope == key.supplier_scope,
                CanonicalRowBindingHistoryRow.template_id == key.template_id,
                CanonicalRowBindingHistoryRow.template_revision == key.template_revision,
                CanonicalRowBindingHistoryRow.row_key == key.row_key,
            )
        )

    @staticmethod
    def _binding_history_by_key(
        session: Session,
        key: CanonicalRowBindingKey,
    ) -> CanonicalRowBindingHistoryRow | None:
        return session.scalar(
            select(CanonicalRowBindingHistoryRow).where(
                CanonicalRowBindingHistoryRow.project_key == key.project_key,
                CanonicalRowBindingHistoryRow.supplier_scope == key.supplier_scope,
                CanonicalRowBindingHistoryRow.template_id == key.template_id,
                CanonicalRowBindingHistoryRow.template_revision == key.template_revision,
                CanonicalRowBindingHistoryRow.row_key == key.row_key,
            )
        )

    @staticmethod
    def _binding_revision_rows(
        session: Session,
        history_id: str,
    ) -> list[CanonicalRowBindingRevisionRow]:
        return list(
            session.scalars(
                select(CanonicalRowBindingRevisionRow)
                .where(CanonicalRowBindingRevisionRow.history_id == history_id)
                .order_by(CanonicalRowBindingRevisionRow.binding_revision)
            ).all()
        )

    @staticmethod
    def _binding_revision_row(
        session: Session,
        history_id: str,
        revision: int,
    ) -> CanonicalRowBindingRevisionRow | None:
        return session.scalar(
            select(CanonicalRowBindingRevisionRow).where(
                CanonicalRowBindingRevisionRow.history_id == history_id,
                CanonicalRowBindingRevisionRow.binding_revision == revision,
            )
        )

    def _load_binding_rows(
        self,
        session: Session,
        key: CanonicalRowBindingKey,
        revision: int,
    ) -> tuple[CanonicalRowBindingHistoryRow, CanonicalRowBindingRevisionRow]:
        history = self._binding_history_by_key(session, key)
        if history is None:
            raise MasterConfigNotFoundError("canonical row-binding history was not found")
        self._validate_binding_history_scope(session, history)
        row = self._binding_revision_row(session, history.id, revision)
        if row is None or row.project_key != history.project_key:
            raise MasterConfigNotFoundError("canonical row-binding revision was not found")
        if row.canonical_supplier_id != history.canonical_supplier_id:
            raise MasterConfigScopeError("row-binding revision supplier does not match history")
        return history, row

    @staticmethod
    def _validate_binding_history_scope(
        session: Session,
        history: CanonicalRowBindingHistoryRow,
        *,
        canonical_supplier_id: str | None = None,
    ) -> None:
        mapping_history = session.get(MappingTemplateHistoryRow, history.mapping_history_id)
        mapping_revision = session.get(MappingTemplateRevisionRow, history.mapping_revision_id)
        if mapping_history is None or mapping_revision is None:
            raise MasterConfigScopeError("row-binding Mapping references do not exist")
        if (
            mapping_history.project_key != history.project_key
            or mapping_history.supplier_scope != history.supplier_scope
            or mapping_history.template_id != history.template_id
            or mapping_revision.history_id != mapping_history.id
            or mapping_revision.revision_number != history.template_revision
        ):
            raise MasterConfigScopeError(
                "row-binding Mapping history/revision scope is inconsistent"
            )
        if mapping_revision.status != ConfigurationRevisionStatus.APPROVED.value:
            raise MasterConfigScopeError("row binding references a non-approved Mapping revision")
        if (
            canonical_supplier_id is not None
            and history.canonical_supplier_id != canonical_supplier_id
        ):
            raise MasterConfigScopeError("row-binding history supplier cannot change by revision")

    def _to_binding_record(
        self,
        session: Session,
        history: CanonicalRowBindingHistoryRow,
        row: CanonicalRowBindingRevisionRow,
    ) -> PersistedCanonicalRowBindingRevision:
        self._validate_binding_history_scope(session, history)
        if row.project_key != history.project_key or row.history_id != history.id:
            raise MasterConfigScopeError("row-binding revision crosses its history scope")
        if row.canonical_supplier_id != history.canonical_supplier_id:
            raise MasterConfigScopeError("row-binding revision supplier differs from history")
        model = session.get(CanonicalModelRow, row.canonical_model_id)
        supplier = session.get(CanonicalSupplierRow, row.canonical_supplier_id)
        part = session.get(CanonicalModelPartRow, row.canonical_model_part_id)
        item = session.get(CanonicalInspectionItemRow, row.canonical_item_id)
        if any(candidate is None for candidate in (model, supplier, part, item)):
            raise MasterConfigPayloadIntegrityError("row-binding canonical reference is missing")
        assert model is not None and supplier is not None and part is not None and item is not None
        if any(
            candidate.project_key != history.project_key
            for candidate in (model, supplier, part, item)
        ):
            raise MasterConfigScopeError("row-binding canonical references cross projects")
        if part.model_id != model.id or item.model_part_id != part.id:
            raise MasterConfigScopeError("row-binding canonical hierarchy is inconsistent")
        if _payload_digest(row.binding_payload) != row.payload_sha256:
            raise MasterConfigPayloadIntegrityError("row-binding payload digest does not match")
        binding = _deserialize_row_binding(history, row)
        if (
            binding.canonical_model_key != model.model_key
            or binding.canonical_supplier_key != supplier.supplier_key
            or binding.canonical_model_part_key != part.model_part_key
            or binding.canonical_item_key != item.item_key
        ):
            raise MasterConfigPayloadIntegrityError(
                "row-binding payload does not match canonical foreign keys"
            )
        return PersistedCanonicalRowBindingRevision(
            binding=binding,
            history_id=history.id,
            revision_id=row.id,
            history_row_version=history.row_version,
            revision_row_version=row.row_version,
            resolved_effective_to=row.resolved_effective_to,
            payload_sha256=row.payload_sha256,
        )

    def _require_binding_approvable(
        self,
        session: Session,
        history: CanonicalRowBindingHistoryRow,
        row: CanonicalRowBindingRevisionRow,
    ) -> None:
        self._validate_binding_history_scope(session, history)
        if row.canonical_supplier_id != history.canonical_supplier_id:
            raise MasterConfigScopeError("row-binding supplier scope is inconsistent")
        item = session.get(CanonicalInspectionItemRow, row.canonical_item_id)
        if item is None or item.project_key != history.project_key:
            raise MasterConfigScopeError("row binding requires a project-local item")
        if item.disposition == InspectionItemDisposition.CANDIDATE.value:
            raise MasterConfigScopeError("a CANDIDATE item cannot have an approved row binding")

    @staticmethod
    def _master_approved_rows(
        session: Session,
        history_id: str,
    ) -> list[MasterSpecRevisionRow]:
        return list(
            session.scalars(
                select(MasterSpecRevisionRow).where(
                    MasterSpecRevisionRow.history_id == history_id,
                    MasterSpecRevisionRow.status == ConfigurationRevisionStatus.APPROVED.value,
                )
            ).all()
        )

    def _master_approved_overlaps(
        self,
        session: Session,
        history_id: str,
        candidate: MasterSpecRevisionRow,
    ) -> bool:
        return any(
            _periods_overlap(
                candidate.declared_effective_from,
                candidate.declared_effective_to,
                row.declared_effective_from,
                row.resolved_effective_to or row.declared_effective_to,
            )
            for row in self._master_approved_rows(session, history_id)
            if row.id != candidate.id
        )

    @staticmethod
    def _binding_approved_rows(
        session: Session,
        history_id: str,
    ) -> list[CanonicalRowBindingRevisionRow]:
        return list(
            session.scalars(
                select(CanonicalRowBindingRevisionRow).where(
                    CanonicalRowBindingRevisionRow.history_id == history_id,
                    CanonicalRowBindingRevisionRow.status
                    == ConfigurationRevisionStatus.APPROVED.value,
                )
            ).all()
        )

    def _binding_approved_overlaps(
        self,
        session: Session,
        history_id: str,
        candidate: CanonicalRowBindingRevisionRow,
    ) -> bool:
        return any(
            _periods_overlap(
                candidate.declared_effective_from,
                candidate.declared_effective_to,
                row.declared_effective_from,
                row.resolved_effective_to or row.declared_effective_to,
            )
            for row in self._binding_approved_rows(session, history_id)
            if row.id != candidate.id
        )

    @staticmethod
    def _master_supersession_exists(
        session: Session,
        predecessor_id: str,
        successor_id: str,
    ) -> bool:
        return (
            session.scalar(
                select(MasterSpecSupersessionRow.id).where(
                    (MasterSpecSupersessionRow.predecessor_revision_id == predecessor_id)
                    | (MasterSpecSupersessionRow.successor_revision_id == successor_id)
                )
            )
            is not None
        )

    @staticmethod
    def _binding_supersession_exists(
        session: Session,
        predecessor_id: str,
        successor_id: str,
    ) -> bool:
        return (
            session.scalar(
                select(CanonicalRowBindingSupersessionRow.id).where(
                    (CanonicalRowBindingSupersessionRow.predecessor_revision_id == predecessor_id)
                    | (CanonicalRowBindingSupersessionRow.successor_revision_id == successor_id)
                )
            )
            is not None
        )

    @staticmethod
    def _assert_append_only_revision(rows: list[Any], revision: int, subject: str) -> None:
        values = [
            getattr(row, "revision_number", getattr(row, "binding_revision", None)) for row in rows
        ]
        if revision in values:
            raise ImmutableMasterConfigRevisionError(f"{subject} revision cannot be overwritten")
        if values and revision < max(cast(list[int], values)):
            raise ImmutableMasterConfigRevisionError(f"{subject} revision cannot be downgraded")

    @staticmethod
    def _assert_versions(
        actual_history: int,
        actual_revision: int,
        expected_history: int,
        expected_revision: int,
        subject: str,
    ) -> None:
        if actual_history != expected_history:
            raise StaleMasterConfigWriteError(f"{subject} history row_version is stale")
        if actual_revision != expected_revision:
            raise StaleMasterConfigWriteError(f"{subject} revision row_version is stale")

    @staticmethod
    def _assert_supersession_states(
        predecessor: Any,
        successor: Any,
        subject: str,
    ) -> None:
        if predecessor.status != ConfigurationRevisionStatus.APPROVED:
            raise ImmutableMasterConfigRevisionError(
                f"{subject} supersession requires an APPROVED predecessor"
            )
        if successor.status != ConfigurationRevisionStatus.REVIEWED:
            raise ImmutableMasterConfigRevisionError(
                f"{subject} supersession requires a REVIEWED successor"
            )

    @staticmethod
    def _validate_supersession_period(
        approved_rows: list[Any],
        predecessor: Any,
        successor: Any,
        *,
        subject: str,
    ) -> date:
        successor_start = successor.declared_effective_from
        predecessor_existing_end = (
            predecessor.resolved_effective_to or predecessor.declared_effective_to
        )
        if not _effective_on(
            predecessor.declared_effective_from,
            predecessor_existing_end,
            successor_start,
        ):
            raise MasterConfigEffectivePeriodError(
                f"{subject} predecessor must span successor effective_from"
            )
        predecessor_end = cast(date, successor_start - timedelta(days=1))
        if predecessor_end < predecessor.declared_effective_from:
            raise MasterConfigEffectivePeriodError(
                f"{subject} supersession cannot create an empty predecessor period"
            )
        if (
            predecessor.declared_effective_to is not None
            and predecessor_end > predecessor.declared_effective_to
        ):
            raise MasterConfigEffectivePeriodError(
                f"{subject} resolved period cannot extend declared effectivity"
            )
        conflicts = [
            row
            for row in approved_rows
            if row.id != predecessor.id
            and _periods_overlap(
                successor.declared_effective_from,
                successor.declared_effective_to,
                row.declared_effective_from,
                row.resolved_effective_to or row.declared_effective_to,
            )
        ]
        if conflicts:
            raise MasterConfigEffectivePeriodError(
                f"{subject} successor overlaps another approved revision"
            )
        return predecessor_end

    @staticmethod
    def _cas(
        session: Session,
        model: Any,
        row_id: str,
        expected: int,
        **values: object,
    ) -> None:
        result = cast(
            CursorResult[Any],
            session.execute(
                update(model)
                .where(model.id == row_id, model.row_version == expected)
                .values(row_version=expected + 1, **values)
                .execution_options(synchronize_session=False)
            ),
        )
        if result.rowcount != 1:
            raise StaleMasterConfigWriteError("configuration row_version is stale")


def _serialize_master_spec(spec: MasterSpecRevision) -> dict[str, object]:
    return {
        "project_key": spec.project_key,
        "canonical_item_key": spec.canonical_item_key,
        "revision": spec.revision,
        "target": _decimal_text(spec.target),
        "lsl": _decimal_text(spec.lsl),
        "usl": _decimal_text(spec.usl),
        "unit": spec.unit,
        "external_spec_revision": spec.external_spec_revision,
        "effective_from": spec.effective_from.isoformat(),
        "effective_to": spec.effective_to.isoformat() if spec.effective_to is not None else None,
        "change_reason": spec.change_reason,
        "source_reference": spec.source_reference,
    }


_MASTER_SPEC_PAYLOAD_KEYS = frozenset(
    {
        "project_key",
        "canonical_item_key",
        "revision",
        "target",
        "lsl",
        "usl",
        "unit",
        "external_spec_revision",
        "effective_from",
        "effective_to",
        "change_reason",
        "source_reference",
    }
)


def _deserialize_master_spec(
    row: MasterSpecRevisionRow,
    item_key: str,
) -> MasterSpecRevision:
    payload = row.spec_payload
    if frozenset(payload) != _MASTER_SPEC_PAYLOAD_KEYS:
        raise MasterConfigPayloadIntegrityError("Master Spec payload key set is invalid")
    try:
        revision = _exact_int(payload["revision"], "revision")
        project_key = _exact_string(payload["project_key"], "project_key")
        payload_item_key = _exact_string(payload["canonical_item_key"], "canonical_item_key")
        effective_from = date.fromisoformat(
            _exact_string(payload["effective_from"], "effective_from")
        )
        effective_to = _optional_date(payload["effective_to"], "effective_to")
        spec = MasterSpecRevision(
            project_key=project_key,
            canonical_item_key=payload_item_key,
            revision=revision,
            status=ConfigurationRevisionStatus(row.status),
            target=_optional_decimal(payload["target"], "target"),
            lsl=_optional_decimal(payload["lsl"], "lsl"),
            usl=_optional_decimal(payload["usl"], "usl"),
            unit=_exact_string(payload["unit"], "unit"),
            external_spec_revision=_exact_string(
                payload["external_spec_revision"], "external_spec_revision"
            ),
            effective_from=effective_from,
            effective_to=effective_to,
            change_reason=_exact_string(payload["change_reason"], "change_reason"),
            source_reference=_exact_string(payload["source_reference"], "source_reference"),
            reviewed_by=row.reviewed_by,
            reviewed_at=row.reviewed_at,
            approved_by=row.approved_by,
            approved_at=row.approved_at,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise MasterConfigPayloadIntegrityError("stored Master Spec payload is invalid") from error
    if (
        spec.canonical_item_key != item_key
        or spec.revision != row.revision_number
        or spec.effective_from != row.declared_effective_from
        or spec.effective_to != row.declared_effective_to
    ):
        raise MasterConfigPayloadIntegrityError("Master Spec payload differs from indexed columns")
    if row.resolved_effective_to is not None and (
        row.resolved_effective_to < spec.effective_from
        or (spec.effective_to is not None and row.resolved_effective_to > spec.effective_to)
    ):
        raise MasterConfigPayloadIntegrityError("Master Spec resolved effectivity is invalid")
    return spec


def _serialize_row_binding(binding: CanonicalRowBindingRevision) -> dict[str, object]:
    return {
        "project_key": binding.key.project_key,
        "supplier_scope": binding.key.supplier_scope,
        "template_id": binding.key.template_id,
        "template_revision": binding.key.template_revision,
        "row_key": binding.key.row_key,
        "binding_revision": binding.binding_revision,
        "effective_from": binding.effective_from.isoformat(),
        "effective_to": (
            binding.effective_to.isoformat() if binding.effective_to is not None else None
        ),
        "source_model_values": list(binding.source_model_values),
        "canonical_model_key": binding.canonical_model_key,
        "canonical_supplier_key": binding.canonical_supplier_key,
        "canonical_model_part_key": binding.canonical_model_part_key,
        "canonical_item_key": binding.canonical_item_key,
        "sample_policy": binding.sample_policy.value,
        "measurement_mode": binding.measurement_mode.value,
        "change_reason": binding.change_reason,
        "source_reference": binding.source_reference,
    }


_ROW_BINDING_PAYLOAD_KEYS = frozenset(
    {
        "project_key",
        "supplier_scope",
        "template_id",
        "template_revision",
        "row_key",
        "binding_revision",
        "effective_from",
        "effective_to",
        "source_model_values",
        "canonical_model_key",
        "canonical_supplier_key",
        "canonical_model_part_key",
        "canonical_item_key",
        "sample_policy",
        "measurement_mode",
        "change_reason",
        "source_reference",
    }
)


def _deserialize_row_binding(
    history: CanonicalRowBindingHistoryRow,
    row: CanonicalRowBindingRevisionRow,
) -> CanonicalRowBindingRevision:
    from app.domain.long_format import (
        CanonicalRowBindingKey,
        MeasurementMode,
        SamplePolicy,
    )

    payload = row.binding_payload
    if frozenset(payload) != _ROW_BINDING_PAYLOAD_KEYS:
        raise MasterConfigPayloadIntegrityError("row-binding payload key set is invalid")
    try:
        raw_models = payload["source_model_values"]
        if not isinstance(raw_models, list):
            raise TypeError("source_model_values must be a list")
        source_models = tuple(_exact_string(value, "source_model_values") for value in raw_models)
        effective_from = date.fromisoformat(
            _exact_string(payload["effective_from"], "effective_from")
        )
        effective_to = _optional_date(payload["effective_to"], "effective_to")
        binding = CanonicalRowBindingRevision(
            key=CanonicalRowBindingKey(
                project_key=_exact_string(payload["project_key"], "project_key"),
                supplier_scope=_exact_string(payload["supplier_scope"], "supplier_scope"),
                template_id=_exact_string(payload["template_id"], "template_id"),
                template_revision=_exact_int(payload["template_revision"], "template_revision"),
                row_key=_exact_string(payload["row_key"], "row_key"),
            ),
            binding_revision=_exact_int(payload["binding_revision"], "binding_revision"),
            status=ConfigurationRevisionStatus(row.status),
            effective_from=effective_from,
            effective_to=effective_to,
            source_model_values=source_models,
            canonical_model_key=_exact_string(
                payload["canonical_model_key"], "canonical_model_key"
            ),
            canonical_supplier_key=_exact_string(
                payload["canonical_supplier_key"], "canonical_supplier_key"
            ),
            canonical_model_part_key=_exact_string(
                payload["canonical_model_part_key"], "canonical_model_part_key"
            ),
            canonical_item_key=_exact_string(payload["canonical_item_key"], "canonical_item_key"),
            sample_policy=SamplePolicy(_exact_string(payload["sample_policy"], "sample_policy")),
            measurement_mode=MeasurementMode(
                _exact_string(payload["measurement_mode"], "measurement_mode")
            ),
            change_reason=_exact_string(payload["change_reason"], "change_reason"),
            source_reference=_exact_string(payload["source_reference"], "source_reference"),
            reviewed_by=row.reviewed_by,
            reviewed_at=row.reviewed_at,
            approved_by=row.approved_by,
            approved_at=row.approved_at,
        )
    except (KeyError, TypeError, ValueError) as error:
        raise MasterConfigPayloadIntegrityError("stored row-binding payload is invalid") from error
    if (
        binding.key.project_key != history.project_key
        or binding.key.supplier_scope != history.supplier_scope
        or binding.key.template_id != history.template_id
        or binding.key.template_revision != history.template_revision
        or binding.key.row_key != history.row_key
        or binding.binding_revision != row.binding_revision
        or binding.effective_from != row.declared_effective_from
        or binding.effective_to != row.declared_effective_to
    ):
        raise MasterConfigPayloadIntegrityError("row-binding payload differs from indexed columns")
    if row.resolved_effective_to is not None and (
        row.resolved_effective_to < binding.effective_from
        or (binding.effective_to is not None and row.resolved_effective_to > binding.effective_to)
    ):
        raise MasterConfigPayloadIntegrityError("row-binding resolved effectivity is invalid")
    return binding


def _operational_binding(
    record: PersistedCanonicalRowBindingRevision,
) -> CanonicalRowBinding:
    effective_end = record.resolved_effective_to or record.binding.effective_to
    return replace(record.binding, effective_to=effective_end).materialize()


def _payload_digest(payload: object) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _decimal_text(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _optional_decimal(value: object, field_name: str) -> Decimal | None:
    if value is None:
        return None
    text = _exact_string(value, field_name)
    parsed = Decimal(text)
    if not parsed.is_finite() or str(parsed) != text:
        raise ValueError(f"{field_name} is not canonical finite Decimal text")
    return parsed


def _exact_string(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{field_name} must be an exact non-blank string")
    return value


def _exact_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    return value


def _optional_date(value: object, field_name: str) -> date | None:
    if value is None:
        return None
    return date.fromisoformat(_exact_string(value, field_name))


def _effective_on(start: date, end: date | None, value: date) -> bool:
    return start <= value and (end is None or value <= end)


def _periods_overlap(
    left_start: date,
    left_end: date | None,
    right_start: date,
    right_end: date | None,
) -> bool:
    return left_start <= (right_end or date.max) and right_start <= (left_end or date.max)


def _require_exact(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{field_name} must be an exact non-blank value")


def _require_date(value: date, field_name: str) -> None:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise ValueError(f"{field_name} must be a date")


def _require_aware(value: datetime, field_name: str) -> None:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")


def _flush_identity(session: Session, message: str) -> None:
    try:
        session.flush()
    except IntegrityError as error:
        raise MasterConfigScopeError(message) from error
