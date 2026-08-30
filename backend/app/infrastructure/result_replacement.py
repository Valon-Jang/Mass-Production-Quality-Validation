"""Persistence primitives for explicit, linear result-replacement history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from uuid import uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
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

from app.domain.long_format import LongDataStatus
from app.domain.result_replacement import (
    PersistedReplacementDecision,
    ReplacementMeasurementProof,
    ResultReplacementCandidate,
    canonical_json_sha256,
    measurement_set_sha256,
    serialize_result_replacement_candidate,
)
from app.infrastructure.audit import UTCDateTime
from app.infrastructure.database import Base
from app.infrastructure.long_format import (
    LongInspectionResultRow,
    LongMeasurementRow,
    OqcLotRow,
)


def _new_id() -> str:
    return str(uuid4())


class ResultReplacementTransitionRow(Base):
    __tablename__ = "result_replacement_transitions"
    __table_args__ = (
        UniqueConstraint(
            "project_key",
            "id",
            name="uq_result_replacement_project_id",
        ),
        UniqueConstraint(
            "project_key",
            "id",
            "predecessor_result_id",
            "predecessor_source_file_id",
            name="uq_result_replacement_predecessor_projection",
        ),
        UniqueConstraint(
            "project_key",
            "id",
            "successor_result_id",
            "successor_source_file_id",
            name="uq_result_replacement_successor_projection",
        ),
        UniqueConstraint(
            "project_key",
            "command_id",
            name="uq_result_replacement_project_command",
        ),
        UniqueConstraint(
            "project_key",
            "predecessor_result_id",
            name="uq_result_replacement_outgoing",
        ),
        UniqueConstraint(
            "project_key",
            "successor_result_id",
            name="uq_result_replacement_incoming",
        ),
        UniqueConstraint(
            "project_key",
            "predecessor_result_id",
            "successor_result_id",
            name="uq_result_replacement_pair",
        ),
        ForeignKeyConstraint(
            [
                "project_key",
                "predecessor_result_id",
                "predecessor_source_file_id",
                "predecessor_lot_id",
            ],
            [
                "inspection_results.project_key",
                "inspection_results.id",
                "inspection_results.source_file_id",
                "inspection_results.oqc_lot_id",
            ],
            name="fk_result_replacement_predecessor_result",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "project_key",
                "successor_result_id",
                "successor_source_file_id",
                "successor_lot_id",
            ],
            [
                "inspection_results.project_key",
                "inspection_results.id",
                "inspection_results.source_file_id",
                "inspection_results.oqc_lot_id",
            ],
            name="fk_result_replacement_successor_result",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "project_key",
                "predecessor_original_data_status_transition_id",
                "predecessor_result_id",
                "predecessor_source_file_id",
            ],
            [
                "data_status_transitions.project_key",
                "data_status_transitions.id",
                "data_status_transitions.inspection_result_id",
                "data_status_transitions.source_file_id",
            ],
            name="fk_result_replacement_predecessor_decision",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "project_key",
                "successor_data_status_transition_id",
                "successor_result_id",
                "successor_source_file_id",
            ],
            [
                "data_status_transitions.project_key",
                "data_status_transitions.id",
                "data_status_transitions.inspection_result_id",
                "data_status_transitions.source_file_id",
            ],
            name="fk_result_replacement_successor_decision",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "predecessor_result_id != successor_result_id",
            name="result_replacement_distinct_results",
        ),
        CheckConstraint(
            "predecessor_before_status IN ('VALID','SUSPECT') "
            "AND predecessor_after_status = 'REPLACED' "
            "AND successor_before_status = 'PENDING' "
            "AND successor_after_status = 'VALID'",
            name="result_replacement_status_steps",
        ),
        CheckConstraint(
            "predecessor_after_result_row_version = predecessor_before_result_row_version + 1 "
            "AND successor_after_result_row_version = successor_before_result_row_version + 1",
            name="result_replacement_result_version_steps",
        ),
        CheckConstraint(
            "predecessor_measurement_count >= 1 AND successor_measurement_count >= 1",
            name="result_replacement_measurement_counts",
        ),
        CheckConstraint(
            "length(intent_sha256) = 64 AND length(candidate_sha256) = 64 "
            "AND length(predecessor_measurement_set_sha256) = 64 "
            "AND length(successor_measurement_set_sha256) = 64",
            name="result_replacement_digest_lengths",
        ),
        Index(
            "ix_result_replacement_predecessor",
            "project_key",
            "predecessor_result_id",
        ),
        Index(
            "ix_result_replacement_successor",
            "project_key",
            "successor_result_id",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    project_key: Mapped[str] = mapped_column(String(200), nullable=False)
    command_id: Mapped[str] = mapped_column(String(120), nullable=False)
    intent_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    candidate_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    candidate_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    predecessor_result_id: Mapped[str] = mapped_column(String(36), nullable=False)
    predecessor_source_file_id: Mapped[str] = mapped_column(String(36), nullable=False)
    predecessor_lot_id: Mapped[str] = mapped_column(String(36), nullable=False)
    predecessor_original_data_status_transition_id: Mapped[str] = mapped_column(
        String(36), nullable=False
    )
    predecessor_before_status: Mapped[str] = mapped_column(String(16), nullable=False)
    predecessor_after_status: Mapped[str] = mapped_column(String(16), nullable=False)
    predecessor_before_result_row_version: Mapped[int] = mapped_column(Integer, nullable=False)
    predecessor_after_result_row_version: Mapped[int] = mapped_column(Integer, nullable=False)
    predecessor_measurement_count: Mapped[int] = mapped_column(Integer, nullable=False)
    predecessor_measurement_set_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    successor_result_id: Mapped[str] = mapped_column(String(36), nullable=False)
    successor_source_file_id: Mapped[str] = mapped_column(String(36), nullable=False)
    successor_lot_id: Mapped[str] = mapped_column(String(36), nullable=False)
    successor_data_status_transition_id: Mapped[str] = mapped_column(String(36), nullable=False)
    successor_before_status: Mapped[str] = mapped_column(String(16), nullable=False)
    successor_after_status: Mapped[str] = mapped_column(String(16), nullable=False)
    successor_before_result_row_version: Mapped[int] = mapped_column(Integer, nullable=False)
    successor_after_result_row_version: Mapped[int] = mapped_column(Integer, nullable=False)
    successor_measurement_count: Mapped[int] = mapped_column(Integer, nullable=False)
    successor_measurement_set_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    decided_by: Mapped[str] = mapped_column(String(120), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(UTCDateTime(), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)


class ResultReplacementMeasurementRow(Base):
    __tablename__ = "result_replacement_measurements"
    __table_args__ = (
        UniqueConstraint(
            "project_key",
            "transition_id",
            "side",
            "sample_ordinal",
            name="uq_result_replacement_measurement_ordinal",
        ),
        UniqueConstraint(
            "project_key",
            "transition_id",
            "measurement_id",
            name="uq_result_replacement_measurement_identity",
        ),
        ForeignKeyConstraint(
            ["project_key", "transition_id"],
            ["result_replacement_transitions.project_key", "result_replacement_transitions.id"],
            name="fk_result_replacement_measurement_transition",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["project_key", "measurement_id", "inspection_result_id", "source_file_id"],
            [
                "measurements.project_key",
                "measurements.id",
                "measurements.inspection_result_id",
                "measurements.source_file_id",
            ],
            name="fk_result_replacement_measurement_source",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "project_key",
                "transition_id",
                "predecessor_result_id",
                "predecessor_source_file_id",
            ],
            [
                "result_replacement_transitions.project_key",
                "result_replacement_transitions.id",
                "result_replacement_transitions.predecessor_result_id",
                "result_replacement_transitions.predecessor_source_file_id",
            ],
            name="fk_result_replacement_measurement_predecessor",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "project_key",
                "transition_id",
                "successor_result_id",
                "successor_source_file_id",
            ],
            [
                "result_replacement_transitions.project_key",
                "result_replacement_transitions.id",
                "result_replacement_transitions.successor_result_id",
                "result_replacement_transitions.successor_source_file_id",
            ],
            name="fk_result_replacement_measurement_successor",
            ondelete="RESTRICT",
        ),
        CheckConstraint("side IN ('PREDECESSOR','SUCCESSOR')", name="replacement_side"),
        CheckConstraint(
            "(side = 'PREDECESSOR' "
            "AND predecessor_result_id = inspection_result_id "
            "AND predecessor_source_file_id = source_file_id "
            "AND successor_result_id IS NULL AND successor_source_file_id IS NULL) OR "
            "(side = 'SUCCESSOR' "
            "AND successor_result_id = inspection_result_id "
            "AND successor_source_file_id = source_file_id "
            "AND predecessor_result_id IS NULL AND predecessor_source_file_id IS NULL)",
            name="replacement_measurement_side_scope",
        ),
        CheckConstraint(
            "(side = 'PREDECESSOR' AND before_status IN ('VALID','SUSPECT') "
            "AND after_status = 'REPLACED') OR "
            "(side = 'SUCCESSOR' AND before_status = 'PENDING' AND after_status = 'VALID')",
            name="replacement_measurement_status_step",
        ),
        CheckConstraint(
            "after_row_version = before_row_version + 1 AND sample_ordinal >= 1",
            name="replacement_measurement_version_step",
        ),
        CheckConstraint(
            "length(evidence_sha256) = 64",
            name="replacement_measurement_evidence_digest",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_id)
    project_key: Mapped[str] = mapped_column(String(200), nullable=False)
    transition_id: Mapped[str] = mapped_column(String(36), nullable=False)
    side: Mapped[str] = mapped_column(String(16), nullable=False)
    measurement_id: Mapped[str] = mapped_column(String(36), nullable=False)
    inspection_result_id: Mapped[str] = mapped_column(String(36), nullable=False)
    source_file_id: Mapped[str] = mapped_column(String(36), nullable=False)
    predecessor_result_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    predecessor_source_file_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    successor_result_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    successor_source_file_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    sample_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    before_status: Mapped[str] = mapped_column(String(16), nullable=False)
    after_status: Mapped[str] = mapped_column(String(16), nullable=False)
    before_row_version: Mapped[int] = mapped_column(Integer, nullable=False)
    after_row_version: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_sha256: Mapped[str] = mapped_column(String(64), nullable=False)


_result_table = cast(Table, LongInspectionResultRow.__table__)
_result_table.append_constraint(
    ForeignKeyConstraint(
        ["project_key", "current_replacement_transition_id", "id", "source_file_id"],
        [
            "result_replacement_transitions.project_key",
            "result_replacement_transitions.id",
            "result_replacement_transitions.predecessor_result_id",
            "result_replacement_transitions.predecessor_source_file_id",
        ],
        name="fk_inspection_result_current_replacement",
        ondelete="RESTRICT",
        use_alter=True,
    )
)
_measurement_table = cast(Table, LongMeasurementRow.__table__)
_measurement_table.append_constraint(
    ForeignKeyConstraint(
        [
            "project_key",
            "replacement_transition_id",
            "inspection_result_id",
            "source_file_id",
        ],
        [
            "result_replacement_transitions.project_key",
            "result_replacement_transitions.id",
            "result_replacement_transitions.predecessor_result_id",
            "result_replacement_transitions.predecessor_source_file_id",
        ],
        name="fk_measurement_replacement_transition",
        ondelete="RESTRICT",
        use_alter=True,
    )
)


@dataclass(frozen=True, slots=True)
class ReplacementProjection:
    transition: ResultReplacementTransitionRow
    measurements: tuple[ResultReplacementMeasurementRow, ...]


@dataclass(frozen=True, slots=True)
class LoadedReplacementResult:
    result: LongInspectionResultRow
    lot: OqcLotRow
    measurements: tuple[LongMeasurementRow, ...]


class ResultReplacementPersistenceError(RuntimeError):
    pass


class ResultReplacementNotFoundError(ResultReplacementPersistenceError):
    pass


class StaleResultReplacementError(ResultReplacementPersistenceError):
    pass


class ResultReplacementConflictError(ResultReplacementPersistenceError):
    pass


class ResultReplacementRepository:
    """No method commits; the Application service owns the paired transaction."""

    def load_result(
        self,
        session: Session,
        *,
        project_key: str,
        result_id: str,
        lock: bool,
    ) -> LoadedReplacementResult:
        statement = select(LongInspectionResultRow).where(
            LongInspectionResultRow.project_key == project_key,
            LongInspectionResultRow.id == result_id,
        )
        if lock:
            statement = statement.with_for_update()
        result = session.scalar(statement)
        if result is None:
            raise ResultReplacementNotFoundError("inspection result was not found")
        lot_statement = select(OqcLotRow).where(
            OqcLotRow.project_key == project_key,
            OqcLotRow.id == result.oqc_lot_id,
            OqcLotRow.source_file_id == result.source_file_id,
        )
        if lock:
            lot_statement = lot_statement.with_for_update()
        lot = session.scalar(lot_statement)
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
        measurements = tuple(session.scalars(measurement_statement).all())
        if lot is None:
            raise ResultReplacementNotFoundError("inspection result lot was not found")
        return LoadedReplacementResult(result, lot, measurements)

    def find_pair(
        self,
        session: Session,
        *,
        project_key: str,
        predecessor_result_id: str,
        successor_result_id: str,
    ) -> ResultReplacementTransitionRow | None:
        return session.scalar(
            select(ResultReplacementTransitionRow).where(
                ResultReplacementTransitionRow.project_key == project_key,
                ResultReplacementTransitionRow.predecessor_result_id == predecessor_result_id,
                ResultReplacementTransitionRow.successor_result_id == successor_result_id,
            )
        )

    def find_id(
        self,
        session: Session,
        *,
        project_key: str,
        replacement_id: str,
    ) -> ResultReplacementTransitionRow | None:
        return session.scalar(
            select(ResultReplacementTransitionRow).where(
                ResultReplacementTransitionRow.project_key == project_key,
                ResultReplacementTransitionRow.id == replacement_id,
            )
        )

    def outgoing(
        self,
        session: Session,
        *,
        project_key: str,
        result_id: str,
    ) -> ResultReplacementTransitionRow | None:
        return session.scalar(
            select(ResultReplacementTransitionRow).where(
                ResultReplacementTransitionRow.project_key == project_key,
                ResultReplacementTransitionRow.predecessor_result_id == result_id,
            )
        )

    def incoming(
        self,
        session: Session,
        *,
        project_key: str,
        result_id: str,
    ) -> ResultReplacementTransitionRow | None:
        return session.scalar(
            select(ResultReplacementTransitionRow).where(
                ResultReplacementTransitionRow.project_key == project_key,
                ResultReplacementTransitionRow.successor_result_id == result_id,
            )
        )

    def persist_pair(
        self,
        session: Session,
        *,
        candidate: ResultReplacementCandidate,
        command_id: str,
        intent_sha256: str,
        successor_transition_id: str,
        decided_by: str,
        decided_at: datetime,
        reason: str,
    ) -> ResultReplacementTransitionRow:
        predecessor = candidate.predecessor
        successor = candidate.successor
        snapshot = serialize_result_replacement_candidate(candidate)
        row = ResultReplacementTransitionRow(
            project_key=candidate.project_key,
            command_id=command_id,
            intent_sha256=intent_sha256,
            candidate_snapshot=snapshot,
            candidate_sha256=candidate.candidate_sha256,
            predecessor_result_id=predecessor.result_id,
            predecessor_source_file_id=predecessor.source_file_id,
            predecessor_lot_id=predecessor.lot_id,
            predecessor_original_data_status_transition_id=(
                predecessor.original_data_status_transition_id
            ),
            predecessor_before_status=predecessor.data_status.value,
            predecessor_after_status=LongDataStatus.REPLACED.value,
            predecessor_before_result_row_version=predecessor.row_version,
            predecessor_after_result_row_version=predecessor.row_version + 1,
            predecessor_measurement_count=predecessor.measurement_count,
            predecessor_measurement_set_sha256=predecessor.measurement_set_sha256,
            successor_result_id=successor.result_id,
            successor_source_file_id=successor.source_file_id,
            successor_lot_id=successor.lot_id,
            successor_data_status_transition_id=successor_transition_id,
            successor_before_status=LongDataStatus.PENDING.value,
            successor_after_status=LongDataStatus.VALID.value,
            successor_before_result_row_version=successor.row_version,
            successor_after_result_row_version=successor.row_version + 1,
            successor_measurement_count=successor.measurement_count,
            successor_measurement_set_sha256=successor.measurement_set_sha256,
            decided_by=decided_by,
            decided_at=decided_at,
            reason=reason,
        )
        session.add(row)
        try:
            session.flush()
        except IntegrityError as error:
            raise StaleResultReplacementError(
                "replacement identity, branch, merge, or evidence scope is stale"
            ) from error

        for proof in predecessor.measurements:
            session.add(
                _measurement_row(
                    row,
                    proof,
                    side="PREDECESSOR",
                    result_id=predecessor.result_id,
                    source_file_id=predecessor.source_file_id,
                    before_status=predecessor.data_status,
                )
            )
            mutation = cast(
                CursorResult[Any],
                session.execute(
                    update(LongMeasurementRow)
                    .where(
                        LongMeasurementRow.project_key == candidate.project_key,
                        LongMeasurementRow.id == proof.measurement_id,
                        LongMeasurementRow.inspection_result_id == predecessor.result_id,
                        LongMeasurementRow.source_file_id == predecessor.source_file_id,
                        LongMeasurementRow.data_status == predecessor.data_status.value,
                        LongMeasurementRow.row_version == proof.row_version,
                        LongMeasurementRow.replacement_transition_id.is_(None),
                    )
                    .values(
                        data_status=LongDataStatus.REPLACED.value,
                        replacement_transition_id=row.id,
                        row_version=proof.row_version + 1,
                    )
                    .execution_options(synchronize_session=False)
                ),
            )
            if mutation.rowcount != 1:
                raise StaleResultReplacementError(
                    "predecessor measurement changed during replacement"
                )

        for proof in successor.measurements:
            session.add(
                _measurement_row(
                    row,
                    proof,
                    side="SUCCESSOR",
                    result_id=successor.result_id,
                    source_file_id=successor.source_file_id,
                    before_status=LongDataStatus.PENDING,
                )
            )

        mutation = cast(
            CursorResult[Any],
            session.execute(
                update(LongInspectionResultRow)
                .where(
                    LongInspectionResultRow.project_key == candidate.project_key,
                    LongInspectionResultRow.id == predecessor.result_id,
                    LongInspectionResultRow.source_file_id == predecessor.source_file_id,
                    LongInspectionResultRow.oqc_lot_id == predecessor.lot_id,
                    LongInspectionResultRow.data_status == predecessor.data_status.value,
                    LongInspectionResultRow.row_version == predecessor.row_version,
                    LongInspectionResultRow.current_replacement_transition_id.is_(None),
                )
                .values(
                    data_status=LongDataStatus.REPLACED.value,
                    current_replacement_transition_id=row.id,
                    row_version=predecessor.row_version + 1,
                )
                .execution_options(synchronize_session=False)
            ),
        )
        if mutation.rowcount != 1:
            raise StaleResultReplacementError("predecessor result changed during replacement")
        session.flush()
        return row

    def projection(
        self,
        session: Session,
        row: ResultReplacementTransitionRow,
    ) -> ReplacementProjection:
        measurements = tuple(
            session.scalars(
                select(ResultReplacementMeasurementRow)
                .where(
                    ResultReplacementMeasurementRow.project_key == row.project_key,
                    ResultReplacementMeasurementRow.transition_id == row.id,
                )
                .order_by(
                    ResultReplacementMeasurementRow.side,
                    ResultReplacementMeasurementRow.sample_ordinal,
                    ResultReplacementMeasurementRow.measurement_id,
                )
            ).all()
        )
        return ReplacementProjection(row, measurements)

    @staticmethod
    def decision(
        row: ResultReplacementTransitionRow,
        *,
        replayed: bool,
    ) -> PersistedReplacementDecision:
        return PersistedReplacementDecision(
            replacement_id=row.id,
            project_key=row.project_key,
            predecessor_result_id=row.predecessor_result_id,
            successor_result_id=row.successor_result_id,
            predecessor_result_row_version=row.predecessor_after_result_row_version,
            successor_result_row_version=row.successor_after_result_row_version,
            successor_data_status_transition_id=row.successor_data_status_transition_id,
            predecessor_measurement_count=row.predecessor_measurement_count,
            successor_measurement_count=row.successor_measurement_count,
            candidate_sha256=row.candidate_sha256,
            intent_sha256=row.intent_sha256,
            decided_by=row.decided_by,
            decided_at=row.decided_at,
            reason=row.reason,
            replayed=replayed,
        )


def validate_replaced_projection_for_data_review(
    session: Session,
    *,
    project_key: str,
    result: LongInspectionResultRow,
    measurements: tuple[LongMeasurementRow, ...],
    original_transition_id: str,
) -> ResultReplacementTransitionRow:
    """Verify only the outgoing projection; never recurse into successor replay."""

    repository = ResultReplacementRepository()
    row = repository.outgoing(session, project_key=project_key, result_id=result.id)
    from app.infrastructure.data_review import DataStatusTransitionRow

    original = session.scalar(
        select(DataStatusTransitionRow).where(
            DataStatusTransitionRow.project_key == project_key,
            DataStatusTransitionRow.id == original_transition_id,
            DataStatusTransitionRow.inspection_result_id == result.id,
            DataStatusTransitionRow.source_file_id == result.source_file_id,
        )
    )
    snapshot_predecessor = row.candidate_snapshot.get("predecessor") if row is not None else None
    if (
        row is None
        or original is None
        or not isinstance(snapshot_predecessor, dict)
        or row.id != result.current_replacement_transition_id
        or row.predecessor_original_data_status_transition_id != original_transition_id
        or row.predecessor_source_file_id != result.source_file_id
        or row.predecessor_lot_id != result.oqc_lot_id
        or row.predecessor_after_result_row_version != result.row_version
        or row.predecessor_before_result_row_version != original.after_result_row_version
        or row.predecessor_before_status != original.to_status
        or result.data_status != LongDataStatus.REPLACED.value
        or canonical_json_sha256(row.candidate_snapshot) != row.candidate_sha256
        or snapshot_predecessor.get("result_id") != row.predecessor_result_id
        or snapshot_predecessor.get("source_file_id") != row.predecessor_source_file_id
        or snapshot_predecessor.get("lot_id") != row.predecessor_lot_id
        or snapshot_predecessor.get("data_status") != row.predecessor_before_status
        or snapshot_predecessor.get("row_version") != row.predecessor_before_result_row_version
        or snapshot_predecessor.get("original_data_status_transition_id")
        != row.predecessor_original_data_status_transition_id
        or snapshot_predecessor.get("original_decision_candidate_sha256")
        != original.candidate_sha256
        or snapshot_predecessor.get("measurement_count") != row.predecessor_measurement_count
        or snapshot_predecessor.get("measurement_set_sha256")
        != row.predecessor_measurement_set_sha256
    ):
        raise ResultReplacementPersistenceError("replacement result projection is not exact")
    predecessor_rows = tuple(
        value
        for value in repository.projection(session, row).measurements
        if value.side == "PREDECESSOR"
    )
    if len(predecessor_rows) != row.predecessor_measurement_count or len(measurements) != len(
        predecessor_rows
    ):
        raise ResultReplacementPersistenceError("replacement measurement count is not exact")
    by_id = {value.measurement_id: value for value in predecessor_rows}
    original_samples = original.candidate_snapshot.get("samples")
    if not isinstance(original_samples, list):
        raise ResultReplacementPersistenceError("original decision sample proof is absent")
    original_by_id = {
        value.get("measurement_id"): value
        for value in original_samples
        if isinstance(value, dict) and isinstance(value.get("measurement_id"), str)
    }
    before_proofs: list[ReplacementMeasurementProof] = []
    for measurement in measurements:
        proof = by_id.get(measurement.id)
        original_sample = original_by_id.get(measurement.id)
        if (
            proof is None
            or not isinstance(original_sample, dict)
            or proof.inspection_result_id != result.id
            or proof.source_file_id != result.source_file_id
            or proof.sample_ordinal != measurement.sample_ordinal
            or proof.after_row_version != measurement.row_version
            or proof.before_row_version + 1 != proof.after_row_version
            or original_sample.get("row_version") != proof.before_row_version - 1
            or proof.before_status != row.predecessor_before_status
            or proof.after_status != LongDataStatus.REPLACED.value
            or proof.evidence_sha256 != measurement.evidence_sha256
            or measurement.data_status != LongDataStatus.REPLACED.value
            or measurement.replacement_transition_id != row.id
        ):
            raise ResultReplacementPersistenceError(
                "replacement measurement projection is not exact"
            )
        before_proofs.append(
            ReplacementMeasurementProof(
                measurement_id=measurement.id,
                sample_ordinal=measurement.sample_ordinal,
                source_cell=measurement.source_cell,
                data_status=LongDataStatus(proof.before_status),
                row_version=proof.before_row_version,
                evidence_sha256=measurement.evidence_sha256,
            )
        )
    if measurement_set_sha256(tuple(before_proofs)) != row.predecessor_measurement_set_sha256:
        raise ResultReplacementPersistenceError("predecessor measurement-set digest is invalid")
    return row


def _measurement_row(
    transition: ResultReplacementTransitionRow,
    proof: ReplacementMeasurementProof,
    *,
    side: str,
    result_id: str,
    source_file_id: str,
    before_status: LongDataStatus,
) -> ResultReplacementMeasurementRow:
    return ResultReplacementMeasurementRow(
        project_key=transition.project_key,
        transition_id=transition.id,
        side=side,
        measurement_id=proof.measurement_id,
        inspection_result_id=result_id,
        source_file_id=source_file_id,
        predecessor_result_id=result_id if side == "PREDECESSOR" else None,
        predecessor_source_file_id=(source_file_id if side == "PREDECESSOR" else None),
        successor_result_id=result_id if side == "SUCCESSOR" else None,
        successor_source_file_id=(source_file_id if side == "SUCCESSOR" else None),
        sample_ordinal=proof.sample_ordinal,
        before_status=before_status.value,
        after_status=(
            LongDataStatus.REPLACED.value if side == "PREDECESSOR" else LongDataStatus.VALID.value
        ),
        before_row_version=proof.row_version,
        after_row_version=proof.row_version + 1,
        evidence_sha256=proof.evidence_sha256,
    )
