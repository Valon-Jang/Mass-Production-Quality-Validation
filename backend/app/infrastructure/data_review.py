"""Persistence and evidence reconstruction for explicit data-status review."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
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
    Table,
    Text,
    UniqueConstraint,
    select,
    update,
)
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.domain.data_review import (
    DataReviewBasis,
    DataReviewCandidate,
    HistoricalMasterEvidence,
    ReviewCandidateState,
    ReviewIssue,
    ReviewIssueCode,
    ReviewMeasurementEvidence,
    SourceUnitEvidence,
    SystemJudgment,
    canonical_json_sha256,
    serialize_data_review_candidate,
)
from app.domain.long_format import LongDataStatus, MeasurementMode, SpecEvaluationStatus
from app.domain.mapping import SystemJudgmentStatus
from app.domain.master_config import InspectionItemDisposition
from app.infrastructure.audit import AuditLog, UTCDateTime
from app.infrastructure.database import Base
from app.infrastructure.long_format import (
    LongIngestionJobRow,
    LongInspectionResultRow,
    LongMeasurementRow,
    LongSourceFileRow,
    LongSourceSheetRow,
    OqcLotRow,
    deserialize_cell_evidence,
    untagged_value,
)
from app.infrastructure.long_format import (
    canonical_json_sha256 as long_json_sha256,
)
from app.infrastructure.master_config import (
    CanonicalInspectionItemRow,
    CanonicalModelPartRow,
    MasterConfigPayloadIntegrityError,
    MasterConfigRepository,
    MasterConfigScopeError,
    PersistedMasterSpecRevision,
)


def _new_id() -> str:
    return str(uuid4())


class DataStatusTransitionRow(Base):
    __tablename__ = "data_status_transitions"
    __table_args__ = (
        UniqueConstraint(
            "project_key",
            "id",
            name="uq_data_status_transition_project_id",
        ),
        UniqueConstraint(
            "project_key",
            "id",
            "inspection_result_id",
            "source_file_id",
            name="uq_data_status_transition_projection_scope",
        ),
        UniqueConstraint(
            "project_key",
            "command_id",
            name="uq_data_status_transition_project_command",
        ),
        UniqueConstraint(
            "project_key",
            "inspection_result_id",
            "before_result_row_version",
            name="uq_data_status_transition_result_before_version",
        ),
        ForeignKeyConstraint(
            ["project_key", "inspection_result_id", "source_file_id"],
            [
                "inspection_results.project_key",
                "inspection_results.id",
                "inspection_results.source_file_id",
            ],
            name="fk_data_status_transition_result_source",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_key", "source_file_id"],
            ["source_files.project_key", "source_files.id"],
            name="fk_data_status_transition_source",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_key", "applied_master_history_id"],
            ["master_spec_histories.project_key", "master_spec_histories.id"],
            name="fk_data_status_transition_master_history",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_key", "applied_master_revision_id", "applied_master_history_id"],
            [
                "master_spec_revisions.project_key",
                "master_spec_revisions.id",
                "master_spec_revisions.history_id",
            ],
            name="fk_data_status_transition_master_revision",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "from_status = 'PENDING'",
            name="data_status_transition_from_pending",
        ),
        CheckConstraint(
            "to_status IN ('VALID', 'SUSPECT', 'EXCLUDED')",
            name="data_status_transition_target",
        ),
        CheckConstraint(
            "after_result_row_version = before_result_row_version + 1",
            name="data_status_transition_result_version_step",
        ),
        CheckConstraint(
            "measurement_count >= 0",
            name="data_status_transition_measurement_count",
        ),
        CheckConstraint(
            "length(candidate_sha256) = 64 AND length(intent_sha256) = 64 "
            "AND length(decision_snapshot_sha256) = 64",
            name="data_status_transition_digest_lengths",
        ),
        CheckConstraint(
            "(evaluation_mode = 'EVALUATED' "
            "AND to_status IN ('VALID', 'SUSPECT', 'EXCLUDED') "
            "AND system_judgment IN ('PASS', 'FAIL') "
            "AND system_judgment_status = 'EVALUATED' "
            "AND spec_evaluation_status = 'EVALUATED_APPROVED_MASTER' "
            "AND applied_master_history_id IS NOT NULL "
            "AND applied_master_revision_id IS NOT NULL "
            "AND applied_master_revision_number IS NOT NULL "
            "AND applied_master_history_row_version IS NOT NULL "
            "AND applied_master_revision_row_version IS NOT NULL "
            "AND applied_master_payload_sha256 IS NOT NULL "
            "AND applied_master_revision_number >= 1 "
            "AND applied_master_history_row_version >= 1 "
            "AND applied_master_revision_row_version >= 1 "
            "AND length(applied_master_payload_sha256) = 64 "
            "AND applied_master_declared_effective_from IS NOT NULL) OR "
            "(evaluation_mode = 'REVIEW_ONLY' "
            "AND to_status IN ('SUSPECT', 'EXCLUDED') "
            "AND system_judgment IS NULL "
            "AND system_judgment_status = 'NOT_EVALUATED' "
            "AND spec_evaluation_status = 'NOT_EVALUATED' "
            "AND applied_master_history_id IS NULL "
            "AND applied_master_revision_id IS NULL "
            "AND applied_master_revision_number IS NULL "
            "AND applied_master_history_row_version IS NULL "
            "AND applied_master_revision_row_version IS NULL "
            "AND applied_master_payload_sha256 IS NULL "
            "AND applied_master_declared_effective_from IS NULL "
            "AND applied_master_declared_effective_to IS NULL "
            "AND applied_master_resolved_effective_to IS NULL)",
            name="data_status_transition_evaluation_shape",
        ),
        CheckConstraint(
            "applied_master_declared_effective_to IS NULL "
            "OR applied_master_declared_effective_to >= applied_master_declared_effective_from",
            name="data_status_transition_master_declared_period",
        ),
        CheckConstraint(
            "applied_master_resolved_effective_to IS NULL "
            "OR (applied_master_resolved_effective_to >= applied_master_declared_effective_from "
            "AND (applied_master_declared_effective_to IS NULL "
            "OR applied_master_resolved_effective_to <= applied_master_declared_effective_to))",
            name="data_status_transition_master_resolved_period",
        ),
        Index(
            "ix_data_status_transitions_result",
            "project_key",
            "inspection_result_id",
            "before_result_row_version",
        ),
        Index(
            "ix_data_status_transitions_master",
            "project_key",
            "applied_master_history_id",
            "applied_master_revision_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    project_key: Mapped[str] = mapped_column(String(200), nullable=False)
    source_file_id: Mapped[str] = mapped_column(String(36), nullable=False)
    inspection_result_id: Mapped[str] = mapped_column(String(36), nullable=False)
    command_id: Mapped[str] = mapped_column(String(120), nullable=False)
    intent_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    from_status: Mapped[str] = mapped_column(String(16), nullable=False)
    to_status: Mapped[str] = mapped_column(String(16), nullable=False)
    before_result_row_version: Mapped[int] = mapped_column(Integer, nullable=False)
    after_result_row_version: Mapped[int] = mapped_column(Integer, nullable=False)
    measurement_count: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    candidate_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    decision_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    decision_snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluation_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    system_judgment: Mapped[str | None] = mapped_column(String(16), nullable=True)
    system_judgment_status: Mapped[str] = mapped_column(String(32), nullable=False)
    spec_evaluation_status: Mapped[str] = mapped_column(String(40), nullable=False)
    applied_master_history_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    applied_master_revision_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    applied_master_revision_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    applied_master_history_row_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    applied_master_revision_row_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    applied_master_payload_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    applied_master_declared_effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    applied_master_declared_effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    applied_master_resolved_effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    decided_by: Mapped[str] = mapped_column(String(120), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)


def validate_data_status_transition_evidence(
    row: DataStatusTransitionRow,
    audits: Sequence[AuditLog],
    *,
    expected_requirement_id: str | None,
) -> None:
    """Verify immutable decision snapshot and its exact same-transaction Audit proof."""

    decision_candidate = row.decision_snapshot.get("candidate")
    candidate_result = row.candidate_snapshot.get("result")
    samples_value = row.candidate_snapshot.get("samples")
    if (
        canonical_json_sha256(row.candidate_snapshot) != row.candidate_sha256
        or canonical_json_sha256(row.decision_snapshot) != row.decision_snapshot_sha256
        or decision_candidate != row.candidate_snapshot
        or row.decision_snapshot.get("candidate_sha256") != row.candidate_sha256
        or row.decision_snapshot.get("command_id") != row.command_id
        or row.decision_snapshot.get("intent_sha256") != row.intent_sha256
        or row.decision_snapshot.get("target_status") != row.to_status
        or row.decision_snapshot.get("decided_by") != row.decided_by
        or row.decision_snapshot.get("decided_at") != row.decided_at.isoformat()
        or row.decision_snapshot.get("reason") != row.reason
        or not isinstance(candidate_result, dict)
        or not isinstance(samples_value, list)
        or len(samples_value) != row.measurement_count
        or row.candidate_snapshot.get("state") != row.evaluation_mode
        or row.candidate_snapshot.get("proposed_system_judgment") != row.system_judgment
        or row.candidate_snapshot.get("proposed_system_judgment_status")
        != row.system_judgment_status
        or row.candidate_snapshot.get("proposed_spec_evaluation_status")
        != row.spec_evaluation_status
        or candidate_result.get("id") != row.inspection_result_id
        or candidate_result.get("source_file_id") != row.source_file_id
        or candidate_result.get("data_status") != row.from_status
        or candidate_result.get("row_version") != row.before_result_row_version
    ):
        raise DataReviewPersistenceError("stored decision snapshot evidence does not match")
    expected_before = {
        "data_status": row.from_status,
        "result_row_version": row.before_result_row_version,
        "measurement_versions": [
            {
                "sample_ordinal": value.get("sample_ordinal"),
                "measurement_id": value.get("measurement_id"),
                "row_version": value.get("row_version"),
            }
            for value in samples_value
            if isinstance(value, dict)
        ],
        "candidate_sha256": row.candidate_sha256,
    }
    expected_after = {
        "data_status": row.to_status,
        "result_row_version": row.after_result_row_version,
        "transition_id": row.id,
        "evaluation_mode": row.evaluation_mode,
        "system_judgment": row.system_judgment,
        "master_history_id": row.applied_master_history_id,
        "master_revision_id": row.applied_master_revision_id,
        "master_payload_sha256": row.applied_master_payload_sha256,
    }
    matching_audits = [
        audit
        for audit in audits
        if audit.action == "DATA_STATUS_DECIDED"
        and audit.target_type == "INSPECTION_RESULT"
        and audit.target_id == f"{row.project_key}:{row.inspection_result_id}"
        and audit.occurred_at == row.decided_at
        and audit.actor_id == row.decided_by
        and audit.actor_kind == "LOCAL_OWNER"
        and "ADMIN" in audit.actor_roles
        and audit.reason == row.reason
        and audit.before_state == expected_before
        and audit.after_state == expected_after
        and audit.requirement_id == expected_requirement_id
        and audit.source_reference == f"result:{row.inspection_result_id}"
    ]
    if len(matching_audits) != 1:
        raise DataReviewPersistenceError("matching atomic Audit evidence is missing")


# These projection constraints are registered only with the review slice.  This
# avoids coupling pending-only Long bootstrap tests to Master tables while the
# complete Alembic/runtime metadata still enforces exact project-local evidence.
_inspection_result_table = cast(Table, LongInspectionResultRow.__table__)
_inspection_result_table.append_constraint(
    ForeignKeyConstraint(
        [
            "project_key",
            "applied_master_revision_id",
            "applied_master_history_id",
        ],
        [
            "master_spec_revisions.project_key",
            "master_spec_revisions.id",
            "master_spec_revisions.history_id",
        ],
        name="fk_inspection_result_applied_master_revision",
        ondelete="RESTRICT",
    )
)
_inspection_result_table.append_constraint(
    ForeignKeyConstraint(
        ["project_key", "applied_master_history_id"],
        ["master_spec_histories.project_key", "master_spec_histories.id"],
        name="fk_inspection_result_applied_master_history",
        ondelete="RESTRICT",
    )
)
_inspection_result_table.append_constraint(
    ForeignKeyConstraint(
        [
            "project_key",
            "current_data_status_transition_id",
            "id",
            "source_file_id",
        ],
        [
            "data_status_transitions.project_key",
            "data_status_transitions.id",
            "data_status_transitions.inspection_result_id",
            "data_status_transitions.source_file_id",
        ],
        name="fk_inspection_result_current_data_status_transition",
        ondelete="RESTRICT",
        use_alter=True,
    )
)


@dataclass(frozen=True, slots=True)
class PersistedDataStatusDecision:
    transition_id: str
    project_key: str
    result_id: str
    command_id: str
    intent_sha256: str
    candidate_sha256: str
    target_status: LongDataStatus
    result_row_version: int
    measurement_count: int
    evaluation_mode: ReviewCandidateState
    system_judgment: SystemJudgment | None
    master: HistoricalMasterEvidence | None
    replayed: bool


@dataclass(frozen=True, slots=True)
class ValidMeasurementRecord:
    project_key: str
    result_id: str
    measurement_id: str
    canonical_item_key: str
    sample_ordinal: int
    source_cell: str
    raw_value_text: str
    evidence_sha256: str
    system_judgment: SystemJudgment
    master_revision_id: str


class DataReviewPersistenceError(RuntimeError):
    pass


class DataReviewNotFoundError(DataReviewPersistenceError):
    pass


class StaleDataReviewWriteError(DataReviewPersistenceError):
    pass


class DataReviewCommandConflictError(DataReviewPersistenceError):
    pass


class DataReviewRepository:
    """No method commits; the Application service owns every transaction."""

    def __init__(self, master_repository: MasterConfigRepository | None = None) -> None:
        self._masters = master_repository or MasterConfigRepository()

    def load_basis(
        self,
        session: Session,
        *,
        project_key: str,
        result_id: str,
        lock: bool = False,
    ) -> DataReviewBasis:
        _require_exact(project_key, "project_key")
        _require_exact(result_id, "result_id")
        result_statement = select(LongInspectionResultRow).where(
            LongInspectionResultRow.project_key == project_key,
            LongInspectionResultRow.id == result_id,
        )
        if lock:
            result_statement = result_statement.with_for_update()
        result = session.scalar(result_statement)
        if result is None:
            raise DataReviewNotFoundError("inspection result was not found in the project")
        lot = session.scalar(
            select(OqcLotRow).where(
                OqcLotRow.project_key == project_key,
                OqcLotRow.id == result.oqc_lot_id,
                OqcLotRow.source_file_id == result.source_file_id,
            )
        )
        if lot is None:
            raise DataReviewNotFoundError("inspection result lot evidence was not found")
        source = session.scalar(
            select(LongSourceFileRow).where(
                LongSourceFileRow.project_key == project_key,
                LongSourceFileRow.id == result.source_file_id,
            )
        )
        job = session.scalar(
            select(LongIngestionJobRow).where(
                LongIngestionJobRow.project_key == project_key,
                LongIngestionJobRow.id == lot.ingestion_job_id,
                LongIngestionJobRow.source_file_id == result.source_file_id,
            )
        )
        if source is None or job is None:
            raise DataReviewNotFoundError("immutable source or ingestion evidence was not found")
        measurement_statement = (
            select(LongMeasurementRow)
            .where(
                LongMeasurementRow.project_key == project_key,
                LongMeasurementRow.inspection_result_id == result.id,
                LongMeasurementRow.source_file_id == result.source_file_id,
            )
            .order_by(LongMeasurementRow.sample_ordinal, LongMeasurementRow.id)
        )
        if lock:
            measurement_statement = measurement_statement.with_for_update()
        measurement_rows = tuple(session.scalars(measurement_statement).all())
        source_sheets = {
            row.id: row
            for row in session.scalars(
                select(LongSourceSheetRow).where(
                    LongSourceSheetRow.project_key == project_key,
                    LongSourceSheetRow.source_file_id == result.source_file_id,
                )
            ).all()
        }

        blocking: list[ReviewIssue] = []
        review_only: list[ReviewIssue] = []
        self._validate_result_projection(result, blocking)
        self._validate_source_and_candidate(result, lot, source, job, blocking)
        self._validate_candidate_row(
            result,
            measurement_rows,
            source_sheets,
            job,
            blocking,
        )
        measurement_mode = self._validate_binding(result, lot, blocking)
        source_unit = self._source_unit(result, blocking, review_only)
        measurements = tuple(
            self._measurement_evidence(row, result, blocking, review_only)
            for row in measurement_rows
        )

        item: CanonicalInspectionItemRow | None = None
        if result.canonical_item_key is None:
            blocking.append(_issue(ReviewIssueCode.ITEM_NOT_MAPPED, "result has no item key"))
        else:
            item_statement = select(CanonicalInspectionItemRow).where(
                CanonicalInspectionItemRow.project_key == project_key,
                CanonicalInspectionItemRow.item_key == result.canonical_item_key,
            )
            if lock:
                item_statement = item_statement.with_for_update()
            item = session.scalar(item_statement)
            if item is None:
                blocking.append(
                    _issue(ReviewIssueCode.ITEM_NOT_MAPPED, "canonical item row is absent")
                )
            else:
                part = session.get(CanonicalModelPartRow, item.model_part_id)
                if (
                    part is None
                    or part.project_key != project_key
                    or result.canonical_model_part_key != part.model_part_key
                ):
                    blocking.append(
                        _issue(
                            ReviewIssueCode.BINDING_EVIDENCE_INTEGRITY,
                            "result item and model-part hierarchy disagree",
                        )
                    )

        masters: tuple[HistoricalMasterEvidence, ...] = ()
        if (
            item is not None
            and lot.inspection_date is not None
            and item.disposition == InspectionItemDisposition.MANAGED.value
        ):
            try:
                records = self._masters.find_effective_master_spec_records(
                    session,
                    project_key=project_key,
                    canonical_item_key=item.item_key,
                    as_of=lot.inspection_date,
                    lock=lock,
                )
                masters = tuple(_master_evidence(record) for record in records)
            except (MasterConfigPayloadIntegrityError, MasterConfigScopeError, ValueError) as error:
                blocking.append(_issue(ReviewIssueCode.MASTER_EVIDENCE_INTEGRITY, str(error)))

        return DataReviewBasis(
            project_key=project_key,
            result_id=result.id,
            source_file_id=result.source_file_id,
            lot_id=lot.id,
            source_content_sha256=source.content_sha256,
            inspection_date=lot.inspection_date,
            data_status=LongDataStatus(result.data_status),
            result_row_version=result.row_version,
            current_system_judgment=result.system_judgment,
            current_system_judgment_status=SystemJudgmentStatus(result.system_judgment_status),
            current_spec_evaluation_status=SpecEvaluationStatus(result.spec_evaluation_status),
            source_evidence_sha256=result.source_evidence_sha256,
            binding_snapshot_sha256=result.binding_snapshot_sha256,
            candidate_snapshot_sha256=result.candidate_snapshot_sha256,
            canonical_item_key=(item.item_key if item is not None else result.canonical_item_key),
            item_disposition=(
                InspectionItemDisposition(item.disposition) if item is not None else None
            ),
            item_row_version=item.row_version if item is not None else None,
            measurement_mode=measurement_mode,
            source_unit=source_unit,
            measurements=measurements,
            masters=masters,
            blocking_issues=_sorted_issues(blocking),
            review_only_issues=_sorted_issues(review_only),
        )

    def find_by_command(
        self,
        session: Session,
        *,
        project_key: str,
        command_id: str,
    ) -> DataStatusTransitionRow | None:
        return session.scalar(
            select(DataStatusTransitionRow).where(
                DataStatusTransitionRow.project_key == project_key,
                DataStatusTransitionRow.command_id == command_id,
            )
        )

    def apply_decision(
        self,
        session: Session,
        *,
        candidate: DataReviewCandidate,
        command_id: str,
        intent_sha256: str,
        target_status: LongDataStatus,
        decided_by: str,
        decided_at: datetime,
        reason: str,
    ) -> PersistedDataStatusDecision:
        if candidate.state == ReviewCandidateState.INELIGIBLE:
            raise ValueError("an INELIGIBLE candidate cannot be persisted")
        if target_status not in candidate.allowed_target_statuses:
            raise ValueError("target data status is not allowed by the exact candidate")
        _require_exact(command_id, "command_id")
        _require_exact(decided_by, "decided_by")
        _require_exact(reason, "reason")
        if decided_at.tzinfo is None or decided_at.utcoffset() is None:
            raise ValueError("decided_at must be timezone-aware")

        basis = candidate.basis
        candidate_snapshot = serialize_data_review_candidate(candidate)
        if canonical_json_sha256(candidate_snapshot) != candidate.candidate_sha256:
            raise AssertionError("candidate digest changed during decision persistence")
        master = candidate.selected_master
        decision_snapshot = _decision_snapshot(
            candidate_snapshot=candidate_snapshot,
            candidate_sha256=candidate.candidate_sha256,
            command_id=command_id,
            intent_sha256=intent_sha256,
            target_status=target_status,
            decided_by=decided_by,
            decided_at=decided_at,
            reason=reason,
        )
        transition = DataStatusTransitionRow(
            project_key=basis.project_key,
            source_file_id=basis.source_file_id,
            inspection_result_id=basis.result_id,
            command_id=command_id,
            intent_sha256=intent_sha256,
            from_status=LongDataStatus.PENDING.value,
            to_status=target_status.value,
            before_result_row_version=basis.result_row_version,
            after_result_row_version=basis.result_row_version + 1,
            measurement_count=len(basis.measurements),
            candidate_snapshot=candidate_snapshot,
            candidate_sha256=candidate.candidate_sha256,
            decision_snapshot=decision_snapshot,
            decision_snapshot_sha256=canonical_json_sha256(decision_snapshot),
            evaluation_mode=candidate.state.value,
            system_judgment=(
                candidate.proposed_system_judgment.value
                if candidate.proposed_system_judgment is not None
                else None
            ),
            system_judgment_status=candidate.proposed_system_judgment_status.value,
            spec_evaluation_status=candidate.proposed_spec_evaluation_status.value,
            applied_master_history_id=master.history_id if master is not None else None,
            applied_master_revision_id=master.revision_id if master is not None else None,
            applied_master_revision_number=(master.revision_number if master is not None else None),
            applied_master_history_row_version=(
                master.history_row_version if master is not None else None
            ),
            applied_master_revision_row_version=(
                master.revision_row_version if master is not None else None
            ),
            applied_master_payload_sha256=(master.payload_sha256 if master is not None else None),
            applied_master_declared_effective_from=(
                master.declared_effective_from if master is not None else None
            ),
            applied_master_declared_effective_to=(
                master.declared_effective_to if master is not None else None
            ),
            applied_master_resolved_effective_to=(
                master.resolved_effective_to if master is not None else None
            ),
            decided_by=decided_by,
            decided_at=decided_at,
            reason=reason,
        )
        session.add(transition)
        try:
            session.flush()
        except IntegrityError as error:
            raise StaleDataReviewWriteError(
                "data-status transition identity or evidence scope is stale"
            ) from error

        for measurement in basis.measurements:
            mutation = cast(
                CursorResult[Any],
                session.execute(
                    update(LongMeasurementRow)
                    .where(
                        LongMeasurementRow.project_key == basis.project_key,
                        LongMeasurementRow.inspection_result_id == basis.result_id,
                        LongMeasurementRow.id == measurement.measurement_id,
                        LongMeasurementRow.data_status == LongDataStatus.PENDING.value,
                        LongMeasurementRow.row_version == measurement.row_version,
                    )
                    .values(
                        data_status=target_status.value,
                        row_version=measurement.row_version + 1,
                    )
                    .execution_options(synchronize_session=False)
                ),
            )
            if mutation.rowcount != 1:
                raise StaleDataReviewWriteError(
                    "a measurement status or row_version changed during review"
                )

        result_values: dict[str, object] = {
            "data_status": target_status.value,
            "current_data_status_transition_id": transition.id,
            "current_decision_command_id": command_id,
            "current_decision_candidate_sha256": candidate.candidate_sha256,
            "current_decision_mode": candidate.state.value,
            "current_decided_by": decided_by,
            "current_decided_at": decided_at,
            "current_decision_reason": reason,
            "system_judgment": (
                candidate.proposed_system_judgment.value
                if candidate.proposed_system_judgment is not None
                else None
            ),
            "system_judgment_status": candidate.proposed_system_judgment_status.value,
            "spec_evaluation_status": candidate.proposed_spec_evaluation_status.value,
            "applied_master_history_id": master.history_id if master is not None else None,
            "applied_master_revision_id": master.revision_id if master is not None else None,
            "applied_master_revision_number": (
                master.revision_number if master is not None else None
            ),
            "applied_master_history_row_version": (
                master.history_row_version if master is not None else None
            ),
            "applied_master_revision_row_version": (
                master.revision_row_version if master is not None else None
            ),
            "applied_master_payload_sha256": (
                master.payload_sha256 if master is not None else None
            ),
            "applied_master_declared_effective_from": (
                master.declared_effective_from if master is not None else None
            ),
            "applied_master_declared_effective_to": (
                master.declared_effective_to if master is not None else None
            ),
            "applied_master_resolved_effective_to": (
                master.resolved_effective_to if master is not None else None
            ),
            "row_version": basis.result_row_version + 1,
        }
        result_mutation = cast(
            CursorResult[Any],
            session.execute(
                update(LongInspectionResultRow)
                .where(
                    LongInspectionResultRow.project_key == basis.project_key,
                    LongInspectionResultRow.id == basis.result_id,
                    LongInspectionResultRow.source_file_id == basis.source_file_id,
                    LongInspectionResultRow.data_status == LongDataStatus.PENDING.value,
                    LongInspectionResultRow.row_version == basis.result_row_version,
                )
                .values(**result_values)
                .execution_options(synchronize_session=False)
            ),
        )
        if result_mutation.rowcount != 1:
            raise StaleDataReviewWriteError(
                "inspection result status or row_version changed during review"
            )
        session.flush()
        return PersistedDataStatusDecision(
            transition_id=transition.id,
            project_key=basis.project_key,
            result_id=basis.result_id,
            command_id=command_id,
            intent_sha256=intent_sha256,
            candidate_sha256=candidate.candidate_sha256,
            target_status=target_status,
            result_row_version=basis.result_row_version + 1,
            measurement_count=len(basis.measurements),
            evaluation_mode=candidate.state,
            system_judgment=candidate.proposed_system_judgment,
            master=master,
            replayed=False,
        )

    def replayed_decision(
        self,
        session: Session,
        row: DataStatusTransitionRow,
    ) -> PersistedDataStatusDecision:
        decision_candidate = row.decision_snapshot.get("candidate")
        if (
            canonical_json_sha256(row.candidate_snapshot) != row.candidate_sha256
            or canonical_json_sha256(row.decision_snapshot) != row.decision_snapshot_sha256
            or decision_candidate != row.candidate_snapshot
            or row.decision_snapshot.get("candidate_sha256") != row.candidate_sha256
            or row.decision_snapshot.get("command_id") != row.command_id
            or row.decision_snapshot.get("intent_sha256") != row.intent_sha256
            or row.decision_snapshot.get("target_status") != row.to_status
            or row.decision_snapshot.get("decided_by") != row.decided_by
            or row.decision_snapshot.get("decided_at") != row.decided_at.isoformat()
            or row.decision_snapshot.get("reason") != row.reason
        ):
            raise DataReviewPersistenceError("stored decision snapshot digest does not match")
        candidate_result = row.candidate_snapshot.get("result")
        samples_value = row.candidate_snapshot.get("samples")
        selected_master = row.candidate_snapshot.get("selected_master")
        if (
            not isinstance(candidate_result, dict)
            or not isinstance(samples_value, list)
            or row.candidate_snapshot.get("state") != row.evaluation_mode
            or row.candidate_snapshot.get("proposed_system_judgment") != row.system_judgment
            or row.candidate_snapshot.get("proposed_system_judgment_status")
            != row.system_judgment_status
            or row.candidate_snapshot.get("proposed_spec_evaluation_status")
            != row.spec_evaluation_status
            or cast(dict[str, object], candidate_result).get("id") != row.inspection_result_id
            or cast(dict[str, object], candidate_result).get("source_file_id") != row.source_file_id
            or cast(dict[str, object], candidate_result).get("data_status") != row.from_status
            or cast(dict[str, object], candidate_result).get("row_version")
            != row.before_result_row_version
        ):
            raise DataReviewPersistenceError("transition columns differ from candidate snapshot")
        result = session.scalar(
            select(LongInspectionResultRow).where(
                LongInspectionResultRow.project_key == row.project_key,
                LongInspectionResultRow.id == row.inspection_result_id,
                LongInspectionResultRow.source_file_id == row.source_file_id,
            )
        )
        if (
            result is None
            or result.current_data_status_transition_id != row.id
            or result.current_decision_command_id != row.command_id
            or result.current_decision_candidate_sha256 != row.candidate_sha256
            or result.current_decision_mode != row.evaluation_mode
            or result.system_judgment != row.system_judgment
            or result.system_judgment_status != row.system_judgment_status
            or result.spec_evaluation_status != row.spec_evaluation_status
            or result.applied_master_history_id != row.applied_master_history_id
            or result.applied_master_revision_id != row.applied_master_revision_id
            or result.applied_master_revision_number != row.applied_master_revision_number
            or result.applied_master_history_row_version != row.applied_master_history_row_version
            or result.applied_master_revision_row_version != row.applied_master_revision_row_version
            or result.applied_master_payload_sha256 != row.applied_master_payload_sha256
            or result.applied_master_declared_effective_from
            != row.applied_master_declared_effective_from
            or result.applied_master_declared_effective_to
            != row.applied_master_declared_effective_to
            or result.applied_master_resolved_effective_to
            != row.applied_master_resolved_effective_to
            or result.current_decided_by != row.decided_by
            or result.current_decided_at != row.decided_at
            or result.current_decision_reason != row.reason
        ):
            raise DataReviewPersistenceError("current result projection differs from transition")
        candidate_result_payload = cast(dict[str, object], candidate_result)
        if (
            long_json_sha256(result.source_evidence) != result.source_evidence_sha256
            or result.binding_snapshot is None
            or result.binding_snapshot_sha256 is None
            or long_json_sha256(result.binding_snapshot) != result.binding_snapshot_sha256
            or candidate_result_payload.get("source_evidence_sha256")
            != result.source_evidence_sha256
            or candidate_result_payload.get("binding_snapshot_sha256")
            != result.binding_snapshot_sha256
            or candidate_result_payload.get("candidate_snapshot_sha256")
            != result.candidate_snapshot_sha256
            or result.hold_reasons
        ):
            raise DataReviewPersistenceError("current result source evidence is not exact")
        measurements = tuple(
            session.scalars(
                select(LongMeasurementRow)
                .where(
                    LongMeasurementRow.project_key == row.project_key,
                    LongMeasurementRow.inspection_result_id == row.inspection_result_id,
                    LongMeasurementRow.source_file_id == row.source_file_id,
                )
                .order_by(LongMeasurementRow.sample_ordinal, LongMeasurementRow.id)
            ).all()
        )
        if len(measurements) != row.measurement_count or len(samples_value) != len(measurements):
            raise DataReviewPersistenceError("measurement projection differs from transition")
        replaced_projection = None
        if result.data_status == LongDataStatus.REPLACED.value:
            try:
                from app.infrastructure.result_replacement import (
                    validate_replaced_projection_for_data_review,
                )

                replaced_projection = validate_replaced_projection_for_data_review(
                    session,
                    project_key=row.project_key,
                    result=result,
                    measurements=measurements,
                    original_transition_id=row.id,
                )
            except (ImportError, RuntimeError, ValueError) as error:
                raise DataReviewPersistenceError(
                    "replacement projection differs from original decision"
                ) from error
        elif (
            result.data_status != row.to_status
            or result.row_version != row.after_result_row_version
            or result.current_replacement_transition_id is not None
        ):
            raise DataReviewPersistenceError("current result projection differs from transition")
        expected_measurement_status = (
            LongDataStatus.REPLACED.value if replaced_projection is not None else row.to_status
        )
        measurement_version_steps = 2 if replaced_projection is not None else 1
        for snapshot_value, measurement in zip(samples_value, measurements, strict=True):
            if not isinstance(snapshot_value, dict):
                raise DataReviewPersistenceError("candidate measurement snapshot is invalid")
            snapshot = cast(dict[str, object], snapshot_value)
            raw_payload = measurement.evidence.get("raw_value")
            if (
                snapshot.get("measurement_id") != measurement.id
                or snapshot.get("sample_ordinal") != measurement.sample_ordinal
                or snapshot.get("source_cell") != measurement.source_cell
                or snapshot.get("row_version")
                != measurement.row_version - measurement_version_steps
                or snapshot.get("evidence_sha256") != measurement.evidence_sha256
                or snapshot.get("raw_value_json") != measurement.raw_value_text
                or snapshot.get("raw_numeric_value_json") != measurement.raw_numeric_value
                or snapshot.get("raw_qualitative_value") != measurement.raw_qualitative_value
                or measurement.data_status != expected_measurement_status
                or long_json_sha256(measurement.evidence) != measurement.evidence_sha256
                or not isinstance(raw_payload, dict)
                or _canonical_json(raw_payload) != measurement.raw_value_text
                or measurement.raw_value_tag != cast(dict[str, object], raw_payload).get("kind")
                or snapshot.get("formula_flag") != measurement.formula_flag
                or measurement.formula_flag
                != (measurement.evidence.get("formula_text") is not None)
                or measurement.standardized_value is not None
                or measurement.unit_conversion_status != "NOT_CONFIGURED"
                or measurement.hold_reasons
            ):
                raise DataReviewPersistenceError(
                    "measurement projection differs from candidate snapshot"
                )
        self._validate_replay_long_anchor(
            session,
            transition=row,
            result=result,
            measurements=measurements,
            current_status=expected_measurement_status,
        )
        master = _master_from_transition(row)
        if (master is None) != (selected_master is None):
            raise DataReviewPersistenceError("transition Master projection shape differs")
        if master is not None and (
            master.history_id != row.applied_master_history_id
            or master.revision_id != row.applied_master_revision_id
            or master.revision_number != row.applied_master_revision_number
            or master.history_row_version != row.applied_master_history_row_version
            or master.revision_row_version != row.applied_master_revision_row_version
            or master.payload_sha256 != row.applied_master_payload_sha256
            or master.declared_effective_from != row.applied_master_declared_effective_from
            or master.declared_effective_to != row.applied_master_declared_effective_to
            or master.resolved_effective_to != row.applied_master_resolved_effective_to
        ):
            raise DataReviewPersistenceError("transition Master columns differ from snapshot")
        audits = session.scalars(
            select(AuditLog).where(
                AuditLog.action == "DATA_STATUS_DECIDED",
                AuditLog.target_type == "INSPECTION_RESULT",
                AuditLog.target_id == f"{row.project_key}:{row.inspection_result_id}",
            )
        ).all()
        expected_requirement_id: str | None = None
        try:
            from app.infrastructure.result_replacement import ResultReplacementTransitionRow

            replacement_successor = session.scalar(
                select(ResultReplacementTransitionRow.id).where(
                    ResultReplacementTransitionRow.project_key == row.project_key,
                    ResultReplacementTransitionRow.successor_data_status_transition_id == row.id,
                )
            )
            if replacement_successor is not None:
                expected_requirement_id = "ING-041"
        except ImportError:
            pass
        validate_data_status_transition_evidence(
            row,
            audits,
            expected_requirement_id=expected_requirement_id,
        )
        expected_before = {
            "data_status": row.from_status,
            "result_row_version": row.before_result_row_version,
            "measurement_versions": [
                {
                    "sample_ordinal": value.get("sample_ordinal"),
                    "measurement_id": value.get("measurement_id"),
                    "row_version": value.get("row_version"),
                }
                for value in samples_value
                if isinstance(value, dict)
            ],
            "candidate_sha256": row.candidate_sha256,
        }
        expected_after = {
            "data_status": row.to_status,
            "result_row_version": row.after_result_row_version,
            "transition_id": row.id,
            "evaluation_mode": row.evaluation_mode,
            "system_judgment": row.system_judgment,
            "master_history_id": row.applied_master_history_id,
            "master_revision_id": row.applied_master_revision_id,
            "master_payload_sha256": row.applied_master_payload_sha256,
        }
        matching_audits = [
            audit
            for audit in audits
            if audit.occurred_at == row.decided_at
            and audit.actor_id == row.decided_by
            and audit.actor_kind == "LOCAL_OWNER"
            and "ADMIN" in audit.actor_roles
            and audit.reason == row.reason
            and audit.before_state == expected_before
            and audit.after_state == expected_after
            and audit.requirement_id == expected_requirement_id
            and audit.source_reference == f"result:{row.inspection_result_id}"
        ]
        if len(matching_audits) != 1:
            raise DataReviewPersistenceError("matching atomic Audit evidence is missing")
        return PersistedDataStatusDecision(
            transition_id=row.id,
            project_key=row.project_key,
            result_id=row.inspection_result_id,
            command_id=row.command_id,
            intent_sha256=row.intent_sha256,
            candidate_sha256=row.candidate_sha256,
            target_status=LongDataStatus(row.to_status),
            result_row_version=row.after_result_row_version,
            measurement_count=row.measurement_count,
            evaluation_mode=ReviewCandidateState(row.evaluation_mode),
            system_judgment=(
                SystemJudgment(row.system_judgment) if row.system_judgment is not None else None
            ),
            master=master,
            replayed=True,
        )

    def _validate_replay_long_anchor(
        self,
        session: Session,
        *,
        transition: DataStatusTransitionRow,
        result: LongInspectionResultRow,
        measurements: tuple[LongMeasurementRow, ...],
        current_status: str,
    ) -> None:
        lot = session.scalar(
            select(OqcLotRow).where(
                OqcLotRow.project_key == transition.project_key,
                OqcLotRow.id == result.oqc_lot_id,
                OqcLotRow.source_file_id == transition.source_file_id,
            )
        )
        source = session.scalar(
            select(LongSourceFileRow).where(
                LongSourceFileRow.project_key == transition.project_key,
                LongSourceFileRow.id == transition.source_file_id,
            )
        )
        job = (
            session.scalar(
                select(LongIngestionJobRow).where(
                    LongIngestionJobRow.project_key == transition.project_key,
                    LongIngestionJobRow.id == lot.ingestion_job_id,
                    LongIngestionJobRow.source_file_id == transition.source_file_id,
                )
            )
            if lot is not None
            else None
        )
        if lot is None or source is None or job is None:
            raise DataReviewPersistenceError("immutable Long replay evidence is missing")
        source_sheets = {
            value.id: value
            for value in session.scalars(
                select(LongSourceSheetRow).where(
                    LongSourceSheetRow.project_key == transition.project_key,
                    LongSourceSheetRow.source_file_id == transition.source_file_id,
                )
            ).all()
        }
        blocking: list[ReviewIssue] = []
        self._validate_source_and_candidate(result, lot, source, job, blocking)
        self._validate_candidate_row(
            result,
            measurements,
            source_sheets,
            job,
            blocking,
            decided_status=current_status,
        )
        self._validate_binding(result, lot, blocking)
        if blocking:
            raise DataReviewPersistenceError(
                "current decision differs from immutable Long evidence: "
                + "; ".join(issue.detail for issue in blocking)
            )

    def select_valid_measurements(
        self,
        session: Session,
        *,
        project_key: str,
        canonical_item_key: str | None = None,
    ) -> tuple[ValidMeasurementRecord, ...]:
        statement = (
            select(LongInspectionResultRow, LongMeasurementRow)
            .join(
                LongMeasurementRow,
                (LongMeasurementRow.project_key == LongInspectionResultRow.project_key)
                & (LongMeasurementRow.inspection_result_id == LongInspectionResultRow.id)
                & (LongMeasurementRow.source_file_id == LongInspectionResultRow.source_file_id),
            )
            .where(
                LongInspectionResultRow.project_key == project_key,
                LongInspectionResultRow.data_status == LongDataStatus.VALID.value,
                LongMeasurementRow.data_status == LongDataStatus.VALID.value,
            )
            .order_by(
                LongInspectionResultRow.id,
                LongMeasurementRow.sample_ordinal,
                LongMeasurementRow.id,
            )
        )
        if canonical_item_key is not None:
            statement = statement.where(
                LongInspectionResultRow.canonical_item_key == canonical_item_key
            )
        rows = session.execute(statement).all()
        records: list[ValidMeasurementRecord] = []
        for result, measurement in rows:
            if (
                result.canonical_item_key is None
                or result.system_judgment is None
                or result.applied_master_revision_id is None
                or measurement.raw_value_text is None
            ):
                raise DataReviewPersistenceError(
                    "VALID projection is incomplete and cannot enter official selection"
                )
            records.append(
                ValidMeasurementRecord(
                    project_key=project_key,
                    result_id=result.id,
                    measurement_id=measurement.id,
                    canonical_item_key=result.canonical_item_key,
                    sample_ordinal=measurement.sample_ordinal,
                    source_cell=measurement.source_cell,
                    raw_value_text=measurement.raw_value_text,
                    evidence_sha256=measurement.evidence_sha256,
                    system_judgment=SystemJudgment(result.system_judgment),
                    master_revision_id=result.applied_master_revision_id,
                )
            )
        return tuple(records)

    @staticmethod
    def _validate_result_projection(
        result: LongInspectionResultRow,
        blocking: list[ReviewIssue],
    ) -> None:
        if result.data_status == LongDataStatus.HELD.value:
            blocking.append(_issue(ReviewIssueCode.RESULT_HELD, "HELD is immutable here"))
        elif result.data_status != LongDataStatus.PENDING.value:
            blocking.append(
                _issue(
                    ReviewIssueCode.RESULT_NOT_PENDING,
                    f"current result status is {result.data_status}",
                )
            )
        projection_values = (
            result.current_data_status_transition_id,
            result.current_decision_command_id,
            result.current_decision_candidate_sha256,
            result.current_decision_mode,
            result.applied_master_history_id,
            result.applied_master_revision_id,
            result.current_decided_by,
            result.current_decided_at,
            result.current_decision_reason,
        )
        if (
            any(value is not None for value in projection_values)
            or result.system_judgment is not None
            or result.system_judgment_status != SystemJudgmentStatus.NOT_EVALUATED.value
            or result.spec_evaluation_status != SpecEvaluationStatus.NOT_EVALUATED.value
        ) and result.data_status in {
            LongDataStatus.PENDING.value,
            LongDataStatus.HELD.value,
        }:
            blocking.append(
                _issue(
                    ReviewIssueCode.RESULT_PROJECTION_NOT_EMPTY,
                    "pending/held result already contains decision projection",
                )
            )

    @staticmethod
    def _validate_source_and_candidate(
        result: LongInspectionResultRow,
        lot: OqcLotRow,
        source: LongSourceFileRow,
        job: LongIngestionJobRow,
        blocking: list[ReviewIssue],
    ) -> None:
        if long_json_sha256(result.source_evidence) != result.source_evidence_sha256:
            blocking.append(
                _issue(
                    ReviewIssueCode.SOURCE_EVIDENCE_INTEGRITY,
                    "result source-evidence digest mismatch",
                )
            )
        if (
            long_json_sha256(job.candidate_snapshot) != job.candidate_snapshot_sha256
            or result.candidate_snapshot_sha256 != job.candidate_snapshot_sha256
        ):
            blocking.append(
                _issue(
                    ReviewIssueCode.CANDIDATE_EVIDENCE_INTEGRITY,
                    "persisted Long candidate snapshot digest mismatch",
                )
            )
            return
        try:
            provenance = _object(job.candidate_snapshot, "provenance")
            receipt = _object(provenance, "receipt")
            if (
                _string(receipt, "project_key") != result.project_key
                or _string(receipt, "content_sha256") != source.content_sha256
                or _string(receipt, "blob_id") != source.blob_id
                or _string(provenance, "source_inspection_date")
                != (lot.inspection_date.isoformat() if lot.inspection_date is not None else None)
            ):
                raise ValueError("Long candidate receipt differs from immutable source")
        except (TypeError, ValueError) as error:
            blocking.append(_issue(ReviewIssueCode.CANDIDATE_EVIDENCE_INTEGRITY, str(error)))

    @staticmethod
    def _validate_candidate_row(
        result: LongInspectionResultRow,
        measurements: tuple[LongMeasurementRow, ...],
        source_sheets: dict[str, LongSourceSheetRow],
        job: LongIngestionJobRow,
        blocking: list[ReviewIssue],
        *,
        decided_status: str | None = None,
    ) -> None:
        if long_json_sha256(job.candidate_snapshot) != job.candidate_snapshot_sha256:
            return
        try:
            rows_value = job.candidate_snapshot.get("rows")
            if not isinstance(rows_value, list):
                raise ValueError("Long candidate rows are missing")
            matches = [
                cast(dict[str, object], value)
                for value in rows_value
                if isinstance(value, dict) and value.get("row_key") == result.source_row_key
            ]
            if len(matches) != 1:
                raise ValueError("result row_key does not select exactly one Long candidate row")
            candidate_row = matches[0]
            provenance = _object(job.candidate_snapshot, "provenance")
            schema_version = _string(provenance, "template_schema_version")
            source_keys = [
                "item",
                "method",
                "instrument",
                "specification",
                "tolerance",
                "minimum",
                "maximum",
                "supplier_judgment",
            ]
            if schema_version == "2":
                source_keys.extend(
                    [
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
                    ]
                )
            expected_source = {key: candidate_row.get(key) for key in source_keys}
            if expected_source != result.source_evidence:
                raise ValueError("result source evidence differs from signed Long candidate row")
            expected_state = candidate_row.get("state")
            signed_status = (
                LongDataStatus.HELD.value
                if expected_state == "ROW_HELD"
                else LongDataStatus.PENDING.value
            )
            if expected_state not in {"LOADABLE_PENDING", "ROW_HELD"}:
                raise ValueError("signed Long candidate row state is invalid")
            if decided_status is not None:
                if signed_status != LongDataStatus.PENDING.value:
                    raise ValueError("a decided result did not originate as PENDING")
                expected_status = decided_status
            else:
                expected_status = signed_status
            if (
                result.data_status != expected_status
                or candidate_row.get("issues") != result.hold_reasons
            ):
                raise ValueError("result status/holds differ from signed Long candidate row")
            item_evidence = candidate_row.get("item")
            if not isinstance(item_evidence, dict):
                raise ValueError("signed item evidence is invalid")
            result_sheet = source_sheets.get(result.source_sheet_id)
            if result_sheet is None or result_sheet.sheet_name != cast(
                dict[str, object], item_evidence
            ).get("sheet_name"):
                raise ValueError("result source sheet differs from signed item evidence")
            supplier_evidence = candidate_row.get("supplier_judgment")
            expected_supplier_text: str | None = None
            if isinstance(supplier_evidence, dict):
                raw_supplier = cast(dict[str, object], supplier_evidence).get("raw_value")
                if isinstance(raw_supplier, dict):
                    decoded_supplier = untagged_value(cast(dict[str, object], raw_supplier))
                    if isinstance(decoded_supplier, str):
                        expected_supplier_text = decoded_supplier
            if result.supplier_judgment_text != expected_supplier_text:
                raise ValueError("supplier text differs from signed source evidence")
            if candidate_row.get("binding") != result.binding_snapshot:
                raise ValueError("result binding differs from signed Long candidate row")
            binding = _object(candidate_row, "binding")
            binding_key = _object(binding, "key")
            if (
                _string(binding_key, "supplier_scope") != _string(provenance, "supplier_scope")
                or _string(binding_key, "template_id") != _string(provenance, "template_id")
                or _integer(binding_key, "template_revision")
                != _integer(provenance, "template_revision")
            ):
                raise ValueError("signed binding scope differs from Long candidate provenance")
            expected_measurements = candidate_row.get("measurements")
            if not isinstance(expected_measurements, list) or len(expected_measurements) != len(
                measurements
            ):
                raise ValueError("measurement count differs from signed Long candidate row")
            for expected_value, stored in zip(expected_measurements, measurements, strict=True):
                if not isinstance(expected_value, dict):
                    raise ValueError("signed Long measurement is not an object")
                expected = cast(dict[str, object], expected_value)
                expected_evidence = expected.get("evidence")
                if not isinstance(expected_evidence, dict):
                    raise ValueError("signed Long measurement evidence is not an object")
                expected_raw = cast(dict[str, object], expected_evidence).get("raw_value")
                stored_numeric = (
                    json.loads(stored.raw_numeric_value)
                    if stored.raw_numeric_value is not None
                    else {"kind": "none", "value": None}
                )
                expected_sheet_name = cast(dict[str, object], expected_evidence).get("sheet_name")
                expected_coordinate = cast(dict[str, object], expected_evidence).get("coordinate")
                stored_sheet = source_sheets.get(stored.source_sheet_id)
                if (
                    expected.get("sample_ordinal") != stored.sample_ordinal
                    or expected_evidence != stored.evidence
                    or stored_sheet is None
                    or stored_sheet.sheet_name != expected_sheet_name
                    or stored.source_cell != expected_coordinate
                    or stored.hold_reasons != candidate_row.get("issues")
                    or stored.data_status != expected_status
                    or _canonical_json(expected_raw) != stored.raw_value_text
                    or expected.get("raw_numeric_value") != stored_numeric
                    or expected.get("raw_qualitative_value") != stored.raw_qualitative_value
                    or expected.get("standardized_value") != stored.standardized_value
                    or expected.get("unit_conversion_status") != stored.unit_conversion_status
                ):
                    raise ValueError(
                        f"measurement {stored.id} differs from signed Long candidate row"
                    )
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            blocking.append(_issue(ReviewIssueCode.CANDIDATE_EVIDENCE_INTEGRITY, str(error)))

    @staticmethod
    def _validate_binding(
        result: LongInspectionResultRow,
        lot: OqcLotRow,
        blocking: list[ReviewIssue],
    ) -> MeasurementMode | None:
        payload = result.binding_snapshot
        digest = result.binding_snapshot_sha256
        if payload is None or digest is None or long_json_sha256(payload) != digest:
            blocking.append(
                _issue(
                    ReviewIssueCode.BINDING_EVIDENCE_INTEGRITY,
                    "approved binding snapshot or digest is missing",
                )
            )
            return None
        try:
            key = _object(payload, "key")
            expected_keys = {
                "key",
                "binding_revision",
                "status",
                "approved_by",
                "approved_at",
                "effective_from",
                "effective_to",
                "source_model_values",
                "canonical_model_key",
                "canonical_supplier_key",
                "canonical_model_part_key",
                "canonical_item_key",
                "sample_policy",
                "measurement_mode",
            }
            if set(payload) != expected_keys:
                raise ValueError("binding snapshot keys differ from the exact schema")
            if set(key) != {
                "project_key",
                "supplier_scope",
                "template_id",
                "template_revision",
                "row_key",
            }:
                raise ValueError("binding key shape is invalid")
            if (
                _string(key, "project_key") != result.project_key
                or _string(key, "row_key") != result.source_row_key
                or _integer(payload, "binding_revision") != result.binding_revision
                or _string(payload, "status") != "APPROVED"
                or _string(payload, "canonical_item_key") != result.canonical_item_key
                or _string(payload, "canonical_model_part_key") != result.canonical_model_part_key
                or _string(payload, "canonical_model_key") != lot.canonical_model_key
                or _string(payload, "canonical_supplier_key") != lot.canonical_supplier_key
                or _string(payload, "canonical_model_part_key") != lot.canonical_model_part_key
            ):
                raise ValueError("binding snapshot differs from indexed result identity")
            if lot.inspection_date is None:
                return MeasurementMode(_string(payload, "measurement_mode"))
            effective_from = date.fromisoformat(_string(payload, "effective_from"))
            effective_to_value = payload.get("effective_to")
            effective_to = (
                date.fromisoformat(effective_to_value)
                if isinstance(effective_to_value, str)
                else None
            )
            if not (
                effective_from <= lot.inspection_date
                and (effective_to is None or lot.inspection_date <= effective_to)
            ):
                raise ValueError("binding snapshot was not effective on inspection date")
            return MeasurementMode(_string(payload, "measurement_mode"))
        except (TypeError, ValueError) as error:
            blocking.append(_issue(ReviewIssueCode.BINDING_EVIDENCE_INTEGRITY, str(error)))
            return None

    @staticmethod
    def _source_unit(
        result: LongInspectionResultRow,
        blocking: list[ReviewIssue],
        review_only: list[ReviewIssue],
    ) -> SourceUnitEvidence | None:
        payload = result.source_evidence.get("unit")
        if payload is None:
            review_only.append(
                _issue(ReviewIssueCode.UNIT_EVIDENCE_MISSING, "source row has no v2 unit")
            )
            return None
        if not isinstance(payload, dict):
            blocking.append(
                _issue(
                    ReviewIssueCode.SOURCE_EVIDENCE_INTEGRITY,
                    "v2 unit evidence is not an object",
                )
            )
            return None
        try:
            exact_payload = cast(dict[str, object], payload)
            if set(exact_payload) != {
                "sheet_name",
                "coordinate",
                "raw_value",
                "cached_value",
                "formula_text",
                "number_format",
                "data_type",
                "display_value",
                "display_value_status",
                "value_kind",
            }:
                raise ValueError("v2 unit evidence key set is invalid")
            evidence = deserialize_cell_evidence(exact_payload)
            if evidence.formula_text is not None or not isinstance(evidence.raw_value, str):
                review_only.append(
                    _issue(
                        ReviewIssueCode.UNIT_EVIDENCE_NOT_EXACT_TEXT,
                        "unit must be one non-formula source string",
                    )
                )
                return None
            cell_json = _canonical_json(exact_payload)
            return SourceUnitEvidence(
                sheet_name=evidence.source.sheet_name,
                coordinate=evidence.source.coordinate,
                raw_value=evidence.raw_value,
                cell_evidence_json=cell_json,
                cell_evidence_sha256=_text_sha256(cell_json),
            )
        except (KeyError, TypeError, ValueError) as error:
            blocking.append(
                _issue(
                    ReviewIssueCode.SOURCE_EVIDENCE_INTEGRITY,
                    f"v2 unit evidence is malformed: {error}",
                )
            )
            return None

    @staticmethod
    def _measurement_evidence(
        row: LongMeasurementRow,
        result: LongInspectionResultRow,
        blocking: list[ReviewIssue],
        review_only: list[ReviewIssue],
    ) -> ReviewMeasurementEvidence:
        if row.data_status != result.data_status:
            blocking.append(
                _issue(
                    ReviewIssueCode.MEASUREMENT_STATUS_MISMATCH,
                    f"measurement {row.id} status differs from its result",
                )
            )
        evidence_valid = long_json_sha256(row.evidence) == row.evidence_sha256
        raw_payload = row.evidence.get("raw_value")
        raw_json = row.raw_value_text or "<missing>"
        numeric_json = row.raw_numeric_value
        numeric_value: Decimal | None = None
        try:
            if not evidence_valid or not isinstance(raw_payload, dict):
                raise ValueError("measurement evidence digest or raw tag is invalid")
            exact_raw = cast(dict[str, object], raw_payload)
            canonical_raw = _canonical_json(exact_raw)
            if row.raw_value_text != canonical_raw or row.raw_value_tag != exact_raw.get("kind"):
                raise ValueError("measurement raw columns differ from evidence")
            formula_text = row.evidence.get("formula_text")
            if row.formula_flag != (formula_text is not None):
                raise ValueError("measurement formula flag differs from evidence")
            if row.standardized_value is not None or row.unit_conversion_status != "NOT_CONFIGURED":
                raise ValueError("unapproved standardized measurement value exists")
            raw_value = untagged_value(exact_raw)
            if numeric_json is not None:
                numeric_payload = json.loads(numeric_json)
                if not isinstance(numeric_payload, dict) or numeric_payload != exact_raw:
                    raise ValueError("raw numeric projection differs from exact source tag")
            if isinstance(raw_value, bool):
                raw_value = None
            if isinstance(raw_value, int):
                numeric_value = Decimal(raw_value)
            elif isinstance(raw_value, Decimal):
                numeric_value = raw_value
            elif isinstance(raw_value, float):
                if math.isfinite(raw_value):
                    numeric_value = Decimal.from_float(raw_value)
                else:
                    review_only.append(
                        _issue(
                            ReviewIssueCode.NONFINITE_MEASUREMENT,
                            f"measurement {row.id} is nonfinite",
                        )
                    )
            if numeric_value is not None and not numeric_value.is_finite():
                numeric_value = None
                review_only.append(
                    _issue(
                        ReviewIssueCode.NONFINITE_MEASUREMENT,
                        f"measurement {row.id} is nonfinite",
                    )
                )
            if numeric_value is None and not any(
                issue.code == ReviewIssueCode.NONFINITE_MEASUREMENT and row.id in issue.detail
                for issue in review_only
            ):
                review_only.append(
                    _issue(
                        ReviewIssueCode.NON_NUMERIC_MEASUREMENT,
                        f"measurement {row.id} is not numeric",
                    )
                )
            if row.formula_flag:
                review_only.append(
                    _issue(
                        ReviewIssueCode.FORMULA_MEASUREMENT,
                        f"measurement {row.id} is formula-derived",
                    )
                )
        except (InvalidOperation, json.JSONDecodeError, TypeError, ValueError) as error:
            blocking.append(
                _issue(
                    ReviewIssueCode.MEASUREMENT_EVIDENCE_INTEGRITY,
                    f"measurement {row.id}: {error}",
                )
            )
            numeric_value = None
        return ReviewMeasurementEvidence(
            measurement_id=row.id,
            sample_ordinal=row.sample_ordinal,
            source_cell=row.source_cell,
            row_version=row.row_version,
            evidence_sha256=row.evidence_sha256,
            raw_value_json=raw_json,
            raw_numeric_value_json=numeric_json,
            raw_qualitative_value=row.raw_qualitative_value,
            formula_flag=row.formula_flag,
            numeric_value=numeric_value,
        )


def _master_evidence(record: PersistedMasterSpecRevision) -> HistoricalMasterEvidence:
    spec = record.spec
    return HistoricalMasterEvidence(
        project_key=spec.project_key,
        canonical_item_key=spec.canonical_item_key,
        history_id=record.history_id,
        revision_id=record.revision_id,
        revision_number=spec.revision,
        history_row_version=record.history_row_version,
        revision_row_version=record.revision_row_version,
        payload_sha256=record.payload_sha256,
        declared_effective_from=spec.effective_from,
        declared_effective_to=spec.effective_to,
        resolved_effective_to=record.resolved_effective_to,
        target=spec.target,
        lsl=spec.lsl,
        usl=spec.usl,
        unit=spec.unit,
        external_spec_revision=spec.external_spec_revision,
    )


def _master_from_transition(row: DataStatusTransitionRow) -> HistoricalMasterEvidence | None:
    if row.applied_master_history_id is None:
        return None
    snapshot = row.candidate_snapshot.get("selected_master")
    if not isinstance(snapshot, dict):
        raise DataReviewPersistenceError("transition Master snapshot is missing")
    try:
        value = cast(dict[str, object], snapshot)
        return HistoricalMasterEvidence(
            project_key=_string(value, "project_key"),
            canonical_item_key=_string(value, "canonical_item_key"),
            history_id=_string(value, "history_id"),
            revision_id=_string(value, "revision_id"),
            revision_number=_integer(value, "revision_number"),
            history_row_version=_integer(value, "history_row_version"),
            revision_row_version=_integer(value, "revision_row_version"),
            payload_sha256=_string(value, "payload_sha256"),
            declared_effective_from=date.fromisoformat(_string(value, "declared_effective_from")),
            declared_effective_to=_optional_date(value.get("declared_effective_to")),
            resolved_effective_to=_optional_date(value.get("resolved_effective_to")),
            target=_optional_decimal(value.get("target")),
            lsl=_optional_decimal(value.get("lsl")),
            usl=_optional_decimal(value.get("usl")),
            unit=_string(value, "unit"),
            external_spec_revision=_string(value, "external_spec_revision"),
        )
    except (TypeError, ValueError) as error:
        raise DataReviewPersistenceError("transition Master snapshot is invalid") from error


def _decision_snapshot(
    *,
    candidate_snapshot: dict[str, object],
    candidate_sha256: str,
    command_id: str,
    intent_sha256: str,
    target_status: LongDataStatus,
    decided_by: str,
    decided_at: datetime,
    reason: str,
) -> dict[str, object]:
    return {
        "candidate": candidate_snapshot,
        "candidate_sha256": candidate_sha256,
        "command_id": command_id,
        "intent_sha256": intent_sha256,
        "target_status": target_status.value,
        "decided_by": decided_by,
        "decided_at": decided_at.isoformat(),
        "reason": reason,
    }


def _issue(code: ReviewIssueCode, detail: str) -> ReviewIssue:
    detail = detail.strip() or code.value
    return ReviewIssue(code=code, detail=detail)


def _sorted_issues(values: list[ReviewIssue]) -> tuple[ReviewIssue, ...]:
    return tuple(sorted(set(values)))


def _object(payload: dict[str, object], key: str) -> dict[str, object]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    return cast(dict[str, object], value)


def _string(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a non-blank string")
    return value


def _integer(payload: dict[str, object], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} must be an integer")
    return value


def _optional_date(value: object) -> date | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("optional date must be an ISO string or null")
    return date.fromisoformat(value)


def _optional_decimal(value: object) -> Decimal | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("optional Decimal must be canonical text or null")
    return Decimal(value)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _text_sha256(value: str) -> str:
    return canonical_json_sha256(json.loads(value))


def _require_exact(value: str, name: str) -> None:
    if not value or value != value.strip():
        raise ValueError(f"{name} must be an exact non-blank value")
