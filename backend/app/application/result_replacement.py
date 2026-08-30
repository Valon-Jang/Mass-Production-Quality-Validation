"""Read-only replacement candidates and one atomic explicit ADMIN command."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.application.data_review import (
    DecideDataStatusCommand,
    ExpectedMasterVersion,
    ExpectedMeasurementVersion,
    append_data_status_decision_audit,
    build_data_review_candidate,
    data_review_intent_sha256,
    validate_data_review_expectations,
)
from app.domain.audit import AuditChange
from app.domain.data_review import ReviewCandidateState, SystemJudgment
from app.domain.identity import Actor, Role
from app.domain.long_format import LongDataStatus
from app.domain.result_replacement import (
    REPLACEMENT_CHAIN_LIMIT,
    PersistedReplacementDecision,
    ReplacementDifference,
    ReplacementDifferenceCode,
    ReplacementIdentityProof,
    ReplacementIssue,
    ReplacementIssueCode,
    ReplacementMeasurementProof,
    ReplacementResultProof,
    ReplacementSuccessorProof,
    ResultReplacementCandidate,
    canonical_json_sha256,
    measurement_set_sha256,
)
from app.infrastructure.audit import AuditLog, AuditRepository
from app.infrastructure.data_review import (
    DataReviewPersistenceError,
    DataReviewRepository,
    DataStatusTransitionRow,
    StaleDataReviewWriteError,
)
from app.infrastructure.database import Database
from app.infrastructure.long_format import canonical_json_sha256 as long_json_sha256
from app.infrastructure.result_replacement import (
    LoadedReplacementResult,
    ResultReplacementConflictError,
    ResultReplacementNotFoundError,
    ResultReplacementPersistenceError,
    ResultReplacementRepository,
    ResultReplacementTransitionRow,
    StaleResultReplacementError,
    validate_replaced_projection_for_data_review,
)


class ResultReplacementError(RuntimeError):
    def __init__(self, code: str, message: str, status_label: str) -> None:
        self.code = code
        self.safe_message = message
        self.status_label = status_label
        super().__init__(code)


class ResultReplacementValidationError(ResultReplacementError):
    pass


class ResultReplacementAuthorizationError(ResultReplacementError):
    pass


class ResultReplacementMissingError(ResultReplacementError):
    pass


class ResultReplacementIneligibleError(ResultReplacementError):
    pass


class ResultReplacementStaleError(ResultReplacementError):
    pass


class ResultReplacementUnavailableError(ResultReplacementError):
    pass


@dataclass(frozen=True, slots=True)
class ReplacementCandidateRequest:
    project_key: str
    predecessor_result_id: str
    successor_result_id: str

    def __post_init__(self) -> None:
        _request_text(self.project_key, "project_key", 64)
        _request_text(self.predecessor_result_id, "predecessor_result_id", 36)
        _request_text(self.successor_result_id, "successor_result_id", 36)
        if self.predecessor_result_id == self.successor_result_id:
            raise ValueError("predecessor and successor must be distinct")


@dataclass(frozen=True, slots=True)
class DecideResultReplacementCommand:
    project_key: str
    predecessor_result_id: str
    successor_result_id: str
    candidate_sha256: str
    expected_predecessor_result_row_version: int
    expected_successor_result_row_version: int
    expected_predecessor_measurement_set_sha256: str
    expected_successor_measurement_set_sha256: str
    expected_predecessor_decision_transition_id: str
    expected_successor_data_review_candidate_sha256: str
    confirmed: bool
    reason: str
    actor: Actor

    def __post_init__(self) -> None:
        ReplacementCandidateRequest(
            self.project_key,
            self.predecessor_result_id,
            self.successor_result_id,
        )
        for name in (
            "candidate_sha256",
            "expected_predecessor_measurement_set_sha256",
            "expected_successor_measurement_set_sha256",
            "expected_successor_data_review_candidate_sha256",
        ):
            _sha256(getattr(self, name), name)
        _request_text(
            self.expected_predecessor_decision_transition_id,
            "expected_predecessor_decision_transition_id",
            36,
        )
        _request_text(self.reason, "reason", 2_000)
        if (
            min(
                self.expected_predecessor_result_row_version,
                self.expected_successor_result_row_version,
            )
            < 1
        ):
            raise ValueError("expected result row versions must be positive")
        if not self.confirmed:
            raise ValueError("replacement requires explicit confirmation")


class ResultReplacementService:
    def __init__(
        self,
        database: Database,
        *,
        replacement_repository: ResultReplacementRepository | None = None,
        data_review_repository: DataReviewRepository | None = None,
        audit_repository: AuditRepository | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._database = database
        self._replacements = replacement_repository or ResultReplacementRepository()
        self._reviews = data_review_repository or DataReviewRepository()
        self._audit = audit_repository or AuditRepository()
        self._clock = clock or _utc_now

    def candidate(self, request: ReplacementCandidateRequest) -> ResultReplacementCandidate:
        try:
            with self._database.session() as session:
                return self._candidate(session, request=request, lock=False)
        except ResultReplacementError:
            raise
        except ResultReplacementNotFoundError as error:
            raise _not_found() from error
        except SQLAlchemyError as error:
            raise _unavailable() from error
        except (DataReviewPersistenceError, ResultReplacementPersistenceError) as error:
            raise _integrity() from error

    def decide(
        self,
        command: DecideResultReplacementCommand,
    ) -> PersistedReplacementDecision:
        if not command.actor.has_role(Role.ADMIN):
            raise ResultReplacementAuthorizationError(
                "RESULT_REPLACEMENT_FORBIDDEN",
                "수정본 연결은 관리자만 실행할 수 있습니다.",
                "권한 확인 필요",
            )
        intent_sha256 = _replacement_intent_sha256(command)
        try:
            return self._decide_once(command, intent_sha256)
        except ResultReplacementError:
            raise
        except ResultReplacementConflictError:
            raise
        except (StaleResultReplacementError, StaleDataReviewWriteError) as error:
            try:
                with self._database.session() as session:
                    existing = self._replacements.find_pair(
                        session,
                        project_key=command.project_key,
                        predecessor_result_id=command.predecessor_result_id,
                        successor_result_id=command.successor_result_id,
                    )
                    if existing is None:
                        raise _stale() from error
                    return self._replay(session, existing, intent_sha256=intent_sha256)
            except SQLAlchemyError as replay_error:
                raise _unavailable() from replay_error
        except SQLAlchemyError as error:
            raise _unavailable() from error
        except (DataReviewPersistenceError, ResultReplacementPersistenceError) as error:
            raise _integrity() from error

    def get(
        self,
        *,
        project_key: str,
        replacement_id: str,
    ) -> PersistedReplacementDecision:
        _request_text(project_key, "project_key", 64)
        _request_text(replacement_id, "replacement_id", 36)
        try:
            with self._database.session() as session:
                row = self._replacements.find_id(
                    session,
                    project_key=project_key,
                    replacement_id=replacement_id,
                )
                if row is None:
                    raise _not_found()
                return self._replay(session, row, intent_sha256=row.intent_sha256)
        except ResultReplacementError:
            raise
        except SQLAlchemyError as error:
            raise _unavailable() from error
        except (DataReviewPersistenceError, ResultReplacementPersistenceError) as error:
            raise _integrity() from error

    def _decide_once(
        self,
        command: DecideResultReplacementCommand,
        intent_sha256: str,
    ) -> PersistedReplacementDecision:
        with self._database.session() as session, session.begin():
            # Replay is intentionally resolved before current eligibility/CAS.
            existing = self._replacements.find_pair(
                session,
                project_key=command.project_key,
                predecessor_result_id=command.predecessor_result_id,
                successor_result_id=command.successor_result_id,
            )
            if existing is not None:
                return self._replay(session, existing, intent_sha256=intent_sha256)

            request = ReplacementCandidateRequest(
                command.project_key,
                command.predecessor_result_id,
                command.successor_result_id,
            )
            candidate = self._candidate(session, request=request, lock=True)
            _validate_command_candidate(command, candidate)
            if not candidate.can_replace:
                raise ResultReplacementIneligibleError(
                    "RESULT_REPLACEMENT_INELIGIBLE",
                    "현재 근거로는 수정본 연결을 실행할 수 없습니다.",
                    "수정본 연결 보류",
                )

            review_candidate = build_data_review_candidate(
                self._reviews.load_basis(
                    session,
                    project_key=command.project_key,
                    result_id=command.successor_result_id,
                    lock=True,
                )
            )
            successor_command = _successor_decision_command(
                command,
                candidate=review_candidate,
            )
            validate_data_review_expectations(successor_command, review_candidate)
            if (
                review_candidate.state != ReviewCandidateState.EVALUATED
                or LongDataStatus.VALID not in review_candidate.allowed_target_statuses
            ):
                raise ResultReplacementIneligibleError(
                    "RESULT_REPLACEMENT_INELIGIBLE",
                    "후속 결과가 승인 Master 기반 VALID 후보가 아닙니다.",
                    "후속 결과 검토 필요",
                )
            occurred_at = self._occurred_at()
            successor_decision = self._reviews.apply_decision(
                session,
                candidate=review_candidate,
                command_id=successor_command.command_id,
                intent_sha256=data_review_intent_sha256(successor_command),
                target_status=LongDataStatus.VALID,
                decided_by=command.actor.actor_id,
                decided_at=occurred_at,
                reason=command.reason,
            )
            append_data_status_decision_audit(
                session,
                audit_repository=self._audit,
                actor=command.actor,
                reason=command.reason,
                candidate=review_candidate,
                decision=successor_decision,
                occurred_at=occurred_at,
                requirement_id="ING-041",
            )
            replacement = self._replacements.persist_pair(
                session,
                candidate=candidate,
                command_id=_replacement_command_id(command),
                intent_sha256=intent_sha256,
                successor_transition_id=successor_decision.transition_id,
                decided_by=command.actor.actor_id,
                decided_at=occurred_at,
                reason=command.reason,
            )
            _append_replacement_audit(
                session,
                audit_repository=self._audit,
                actor=command.actor,
                reason=command.reason,
                candidate=candidate,
                replacement=replacement,
                occurred_at=occurred_at,
            )
            return self._replacements.decision(replacement, replayed=False)

    def _candidate(
        self,
        session: object,
        *,
        request: ReplacementCandidateRequest,
        lock: bool,
    ) -> ResultReplacementCandidate:
        from sqlalchemy.orm import Session

        if not isinstance(session, Session):
            raise TypeError("replacement candidate requires one SQLAlchemy Session")
        predecessor = self._replacements.load_result(
            session,
            project_key=request.project_key,
            result_id=request.predecessor_result_id,
            lock=lock,
        )
        successor = self._replacements.load_result(
            session,
            project_key=request.project_key,
            result_id=request.successor_result_id,
            lock=lock,
        )
        if predecessor.result.data_status not in {
            LongDataStatus.VALID.value,
            LongDataStatus.SUSPECT.value,
        }:
            raise _ineligible("선행 결과는 VALID 또는 SUSPECT 상태여야 합니다.")
        if predecessor.result.current_data_status_transition_id is None:
            raise _integrity()
        if successor.result.data_status != LongDataStatus.PENDING.value:
            raise _ineligible("The successor result must still be PENDING.")
        original_transition = session.scalar(
            select(DataStatusTransitionRow).where(
                DataStatusTransitionRow.project_key == request.project_key,
                DataStatusTransitionRow.id == predecessor.result.current_data_status_transition_id,
                DataStatusTransitionRow.inspection_result_id == predecessor.result.id,
                DataStatusTransitionRow.source_file_id == predecessor.result.source_file_id,
            )
        )
        if original_transition is None:
            raise _integrity()
        self._reviews.replayed_decision(session, original_transition)

        successor_review = build_data_review_candidate(
            self._reviews.load_basis(
                session,
                project_key=request.project_key,
                result_id=request.successor_result_id,
                lock=lock,
            )
        )
        predecessor_proof = _predecessor_proof(predecessor, original_transition)
        successor_proof = _successor_proof(successor, successor_review)
        identity, identity_issues = _identity(predecessor, successor)
        issues = list(identity_issues)
        if successor_review.state != ReviewCandidateState.EVALUATED:
            issues.append(
                ReplacementIssue(
                    ReplacementIssueCode.SUCCESSOR_NOT_EVALUATED,
                    "후속 결과는 승인 Master 기반 EVALUATED 후보여야 합니다.",
                )
            )
        if LongDataStatus.VALID not in successor_review.allowed_target_statuses:
            issues.append(
                ReplacementIssue(
                    ReplacementIssueCode.SUCCESSOR_VALID_NOT_ALLOWED,
                    "후속 결과에 VALID 결정이 허용되지 않습니다.",
                )
            )
        if (
            self._replacements.outgoing(
                session,
                project_key=request.project_key,
                result_id=predecessor.result.id,
            )
            is not None
        ):
            issues.append(
                ReplacementIssue(
                    ReplacementIssueCode.EXISTING_OUTGOING_REPLACEMENT,
                    "선행 결과에는 이미 후속 수정본이 연결되어 있습니다.",
                )
            )
        if (
            self._replacements.incoming(
                session,
                project_key=request.project_key,
                result_id=successor.result.id,
            )
            is not None
        ):
            issues.append(
                ReplacementIssue(
                    ReplacementIssueCode.EXISTING_INCOMING_REPLACEMENT,
                    "후속 결과는 이미 다른 선행 결과에 연결되어 있습니다.",
                )
            )
        issues.extend(
            self._chain_issues(
                session,
                project_key=request.project_key,
                predecessor_result_id=predecessor.result.id,
                successor_result_id=successor.result.id,
            )
        )
        return ResultReplacementCandidate(
            project_key=request.project_key,
            predecessor=predecessor_proof,
            successor=successor_proof,
            identity=identity,
            differences=_differences(
                predecessor,
                successor,
                successor_review.proposed_system_judgment,
            ),
            issues=tuple(sorted(set(issues))),
        )

    def _chain_issues(
        self,
        session: object,
        *,
        project_key: str,
        predecessor_result_id: str,
        successor_result_id: str,
    ) -> list[ReplacementIssue]:
        from sqlalchemy.orm import Session

        if not isinstance(session, Session):
            raise TypeError("replacement chain requires one SQLAlchemy Session")
        visited = {predecessor_result_id}
        current = predecessor_result_id
        for _ in range(REPLACEMENT_CHAIN_LIMIT):
            link = self._replacements.incoming(
                session,
                project_key=project_key,
                result_id=current,
            )
            if link is None:
                return []
            current = link.predecessor_result_id
            if current == successor_result_id or current in visited:
                return [
                    ReplacementIssue(
                        ReplacementIssueCode.CHAIN_CYCLE,
                        "수정본 연결이 순환 체인을 만들 수 있습니다.",
                    )
                ]
            visited.add(current)
        return [
            ReplacementIssue(
                ReplacementIssueCode.CHAIN_LIMIT_REACHED,
                "수정본 체인이 서버 검증 한도를 초과했습니다.",
            )
        ]

    def _replay(
        self,
        session: object,
        row: ResultReplacementTransitionRow,
        *,
        intent_sha256: str,
    ) -> PersistedReplacementDecision:
        from sqlalchemy.orm import Session

        if not isinstance(session, Session):
            raise TypeError("replacement replay requires one SQLAlchemy Session")
        if row.intent_sha256 != intent_sha256:
            raise ResultReplacementConflictError(
                "replacement pair was already used for a different intent"
            )
        if canonical_json_sha256(row.candidate_snapshot) != row.candidate_sha256:
            raise ResultReplacementPersistenceError("replacement candidate digest is invalid")
        if (
            set(row.candidate_snapshot)
            != {
                "candidate_contract_version",
                "project_key",
                "predecessor",
                "successor",
                "identity",
                "differences",
                "issues",
                "capabilities",
            }
            or row.candidate_snapshot.get("project_key") != row.project_key
        ):
            raise ResultReplacementPersistenceError("replacement candidate shape is invalid")
        predecessor = self._replacements.load_result(
            session,
            project_key=row.project_key,
            result_id=row.predecessor_result_id,
            lock=False,
        )
        successor = self._replacements.load_result(
            session,
            project_key=row.project_key,
            result_id=row.successor_result_id,
            lock=False,
        )
        validate_replaced_projection_for_data_review(
            session,
            project_key=row.project_key,
            result=predecessor.result,
            measurements=predecessor.measurements,
            original_transition_id=row.predecessor_original_data_status_transition_id,
        )
        successor_transition = session.scalar(
            select(DataStatusTransitionRow).where(
                DataStatusTransitionRow.project_key == row.project_key,
                DataStatusTransitionRow.id == row.successor_data_status_transition_id,
                DataStatusTransitionRow.inspection_result_id == row.successor_result_id,
                DataStatusTransitionRow.source_file_id == row.successor_source_file_id,
            )
        )
        if successor_transition is None:
            raise ResultReplacementPersistenceError("successor decision transition is absent")
        self._reviews.replayed_decision(session, successor_transition)
        snapshot_successor = row.candidate_snapshot.get("successor")
        if not isinstance(snapshot_successor, dict) or (
            snapshot_successor.get("result_id") != row.successor_result_id
            or snapshot_successor.get("source_file_id") != row.successor_source_file_id
            or snapshot_successor.get("lot_id") != row.successor_lot_id
            or snapshot_successor.get("data_status") != row.successor_before_status
            or snapshot_successor.get("row_version") != row.successor_before_result_row_version
            or snapshot_successor.get("data_review_candidate_sha256")
            != successor_transition.candidate_sha256
            or snapshot_successor.get("measurement_count") != row.successor_measurement_count
            or snapshot_successor.get("measurement_set_sha256")
            != row.successor_measurement_set_sha256
            or successor_transition.before_result_row_version
            != row.successor_before_result_row_version
            or successor_transition.after_result_row_version
            != row.successor_after_result_row_version
        ):
            raise ResultReplacementPersistenceError("successor candidate projection is not exact")
        child_rows = self._replacements.projection(session, row).measurements
        successor_children = tuple(value for value in child_rows if value.side == "SUCCESSOR")
        successor_outgoing = self._replacements.outgoing(
            session,
            project_key=row.project_key,
            result_id=row.successor_result_id,
        )
        if successor.result.data_status == LongDataStatus.VALID.value:
            successor_projection_is_exact = (
                successor_outgoing is None
                and successor.result.current_replacement_transition_id is None
                and successor.result.row_version == row.successor_after_result_row_version
            )
        else:
            successor_projection_is_exact = (
                successor.result.data_status == LongDataStatus.REPLACED.value
                and successor_outgoing is not None
                and successor.result.current_replacement_transition_id == successor_outgoing.id
                and successor.result.row_version == row.successor_after_result_row_version + 1
            )
        if (
            not successor_projection_is_exact
            or len(successor.measurements) != row.successor_measurement_count
            or len(successor_children) != row.successor_measurement_count
        ):
            raise ResultReplacementPersistenceError("successor projection is not exact")
        successor_child_by_id = {value.measurement_id: value for value in successor_children}
        successor_before_proofs: list[ReplacementMeasurementProof] = []
        for measurement in successor.measurements:
            proof = successor_child_by_id.get(measurement.id)
            if successor_outgoing is None:
                current_measurement_is_exact = (
                    proof is not None
                    and proof.after_row_version == measurement.row_version
                    and measurement.data_status == LongDataStatus.VALID.value
                    and measurement.replacement_transition_id is None
                )
            else:
                current_measurement_is_exact = (
                    proof is not None
                    and proof.after_row_version + 1 == measurement.row_version
                    and measurement.data_status == LongDataStatus.REPLACED.value
                    and measurement.replacement_transition_id == successor_outgoing.id
                )
            if (
                proof is None
                or proof.inspection_result_id != row.successor_result_id
                or proof.source_file_id != row.successor_source_file_id
                or proof.sample_ordinal != measurement.sample_ordinal
                or proof.before_status != LongDataStatus.PENDING.value
                or proof.after_status != LongDataStatus.VALID.value
                or proof.before_row_version + 1 != proof.after_row_version
                or proof.evidence_sha256 != measurement.evidence_sha256
                or not current_measurement_is_exact
                or long_json_sha256(measurement.evidence) != measurement.evidence_sha256
            ):
                raise ResultReplacementPersistenceError(
                    "successor measurement projection is not exact"
                )
            successor_before_proofs.append(
                ReplacementMeasurementProof(
                    measurement_id=measurement.id,
                    sample_ordinal=measurement.sample_ordinal,
                    source_cell=measurement.source_cell,
                    data_status=LongDataStatus.PENDING,
                    row_version=proof.before_row_version,
                    evidence_sha256=measurement.evidence_sha256,
                )
            )
        if (
            measurement_set_sha256(tuple(successor_before_proofs))
            != row.successor_measurement_set_sha256
        ):
            raise ResultReplacementPersistenceError("successor measurement-set digest is invalid")
        _validate_pair_audit(session, row)
        return self._replacements.decision(row, replayed=True)

    def _occurred_at(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("replacement clock must return a timezone-aware datetime")
        return value.astimezone(UTC)


def _predecessor_proof(
    value: LoadedReplacementResult,
    transition: DataStatusTransitionRow,
) -> ReplacementResultProof:
    measurements = _measurement_proofs(value)
    return ReplacementResultProof(
        result_id=value.result.id,
        source_file_id=value.result.source_file_id,
        lot_id=value.lot.id,
        data_status=LongDataStatus(value.result.data_status),
        row_version=value.result.row_version,
        original_data_status_transition_id=transition.id,
        original_decision_candidate_sha256=transition.candidate_sha256,
        system_judgment=(
            SystemJudgment(value.result.system_judgment)
            if value.result.system_judgment is not None
            else None
        ),
        measurement_set_sha256=measurement_set_sha256(measurements),
        measurements=measurements,
    )


def _successor_proof(
    value: LoadedReplacementResult,
    candidate: object,
) -> ReplacementSuccessorProof:
    from app.domain.data_review import DataReviewCandidate

    if not isinstance(candidate, DataReviewCandidate):
        raise TypeError("successor proof requires a DataReviewCandidate")
    measurements = _measurement_proofs(value)
    master = candidate.selected_master
    return ReplacementSuccessorProof(
        result_id=value.result.id,
        source_file_id=value.result.source_file_id,
        lot_id=value.lot.id,
        data_status=LongDataStatus(value.result.data_status),
        row_version=value.result.row_version,
        data_review_state=candidate.state,
        data_review_candidate_sha256=candidate.candidate_sha256,
        proposed_system_judgment=candidate.proposed_system_judgment,
        selected_master_history_id=master.history_id if master is not None else None,
        selected_master_revision_id=master.revision_id if master is not None else None,
        selected_master_payload_sha256=master.payload_sha256 if master is not None else None,
        item_row_version=candidate.basis.item_row_version,
        measurement_set_sha256=measurement_set_sha256(measurements),
        measurements=measurements,
    )


def _measurement_proofs(value: LoadedReplacementResult) -> tuple[ReplacementMeasurementProof, ...]:
    proofs: list[ReplacementMeasurementProof] = []
    for measurement in value.measurements:
        if (
            measurement.data_status != value.result.data_status
            or long_json_sha256(measurement.evidence) != measurement.evidence_sha256
        ):
            raise ResultReplacementPersistenceError("measurement evidence is not exact")
        proofs.append(
            ReplacementMeasurementProof(
                measurement_id=measurement.id,
                sample_ordinal=measurement.sample_ordinal,
                source_cell=measurement.source_cell,
                data_status=LongDataStatus(measurement.data_status),
                row_version=measurement.row_version,
                evidence_sha256=measurement.evidence_sha256,
            )
        )
    return tuple(proofs)


def _identity(
    predecessor: LoadedReplacementResult,
    successor: LoadedReplacementResult,
) -> tuple[ReplacementIdentityProof, tuple[ReplacementIssue, ...]]:
    values = (
        predecessor.lot.canonical_model_key,
        predecessor.result.canonical_model_part_key,
        predecessor.lot.canonical_supplier_key,
        predecessor.result.canonical_item_key,
        predecessor.lot.source_lot_text,
    )
    if any(value is None or not value or value != value.strip() for value in values):
        raise ResultReplacementPersistenceError("predecessor canonical identity is incomplete")
    identity = ReplacementIdentityProof(*values)  # type: ignore[arg-type]
    successor_values = (
        successor.lot.canonical_model_key,
        successor.result.canonical_model_part_key,
        successor.lot.canonical_supplier_key,
        successor.result.canonical_item_key,
        successor.lot.source_lot_text,
    )
    issues: tuple[ReplacementIssue, ...] = ()
    if values != successor_values:
        issues = (
            ReplacementIssue(
                ReplacementIssueCode.IDENTITY_MISMATCH,
                "모델, 부품, 공급사, 검사항목, LOT가 정확히 일치하지 않습니다.",
            ),
        )
    return identity, issues


def _differences(
    predecessor: LoadedReplacementResult,
    successor: LoadedReplacementResult,
    proposed_judgment: SystemJudgment | None,
) -> tuple[ReplacementDifference, ...]:
    values: set[ReplacementDifference] = set()
    direction_not_evaluable = False
    predecessor_judgment = predecessor.result.system_judgment
    successor_judgment = proposed_judgment.value if proposed_judgment is not None else None
    if predecessor_judgment != successor_judgment:
        values.add(
            ReplacementDifference(
                ReplacementDifferenceCode.JUDGMENT_CHANGED,
                "system_judgment",
                predecessor_judgment,
                successor_judgment,
            )
        )
    if predecessor_judgment == "FAIL" and successor_judgment == "PASS":
        values.add(
            ReplacementDifference(
                ReplacementDifferenceCode.NG_TO_PASS,
                "system_judgment",
                "FAIL",
                "PASS",
            )
        )
    if predecessor.lot.inspection_date != successor.lot.inspection_date:
        direction_not_evaluable = True
        values.add(
            ReplacementDifference(
                ReplacementDifferenceCode.INSPECTION_DATE_CHANGED,
                "inspection_date",
                (
                    predecessor.lot.inspection_date.isoformat()
                    if predecessor.lot.inspection_date is not None
                    else None
                ),
                (
                    successor.lot.inspection_date.isoformat()
                    if successor.lot.inspection_date is not None
                    else None
                ),
            )
        )
    for field in sorted(
        set(predecessor.result.source_evidence) | set(successor.result.source_evidence)
    ):
        before = predecessor.result.source_evidence.get(field)
        after = successor.result.source_evidence.get(field)
        if before != after:
            direction_not_evaluable = True
            values.add(
                ReplacementDifference(
                    ReplacementDifferenceCode.SOURCE_FIELD_CHANGED,
                    field,
                    _bounded_json(before),
                    _bounded_json(after),
                )
            )
    before_raw = [value.raw_value_text for value in predecessor.measurements]
    after_raw = [value.raw_value_text for value in successor.measurements]
    if len(before_raw) != len(after_raw):
        direction_not_evaluable = True
        values.add(
            ReplacementDifference(
                ReplacementDifferenceCode.SAMPLE_COUNT_CHANGED,
                "sample_count",
                str(len(before_raw)),
                str(len(after_raw)),
            )
        )
    if before_raw != after_raw:
        direction_not_evaluable = True
        values.add(
            ReplacementDifference(
                ReplacementDifferenceCode.MEASUREMENT_SET_CHANGED,
                "raw_measurement_set_sha256",
                canonical_json_sha256(before_raw),
                canonical_json_sha256(after_raw),
            )
        )
    if direction_not_evaluable or not values:
        values.add(
            ReplacementDifference(
                ReplacementDifferenceCode.NOT_EVALUABLE,
                "change_direction",
                None,
                None,
            )
        )
    return tuple(sorted(values))


def _successor_decision_command(
    command: DecideResultReplacementCommand,
    *,
    candidate: object,
) -> DecideDataStatusCommand:
    from app.domain.data_review import DataReviewCandidate

    if not isinstance(candidate, DataReviewCandidate):
        raise TypeError("successor command requires a DataReviewCandidate")
    master = candidate.selected_master
    return DecideDataStatusCommand(
        project_key=command.project_key,
        result_id=command.successor_result_id,
        command_id=str(
            uuid5(
                NAMESPACE_URL,
                f"mass-production-quality-validation:replacement-successor:{_replacement_command_id(command)}",
            )
        ),
        target_status=LongDataStatus.VALID,
        expected_candidate_sha256=candidate.candidate_sha256,
        expected_result_row_version=candidate.basis.result_row_version,
        expected_measurement_versions=tuple(
            ExpectedMeasurementVersion(
                value.sample_ordinal,
                value.measurement_id,
                value.row_version,
            )
            for value in candidate.basis.measurements
        ),
        expected_item_row_version=candidate.basis.item_row_version or 0,
        expected_master=(
            ExpectedMasterVersion(
                master.history_id,
                master.revision_id,
                master.history_row_version,
                master.revision_row_version,
                master.payload_sha256,
            )
            if master is not None
            else None
        ),
        actor=command.actor,
        reason=command.reason,
    )


def _validate_command_candidate(
    command: DecideResultReplacementCommand,
    candidate: ResultReplacementCandidate,
) -> None:
    if (
        candidate.candidate_sha256 != command.candidate_sha256
        or candidate.predecessor.row_version != command.expected_predecessor_result_row_version
        or candidate.successor.row_version != command.expected_successor_result_row_version
        or candidate.predecessor.measurement_set_sha256
        != command.expected_predecessor_measurement_set_sha256
        or candidate.successor.measurement_set_sha256
        != command.expected_successor_measurement_set_sha256
        or candidate.predecessor.original_data_status_transition_id
        != command.expected_predecessor_decision_transition_id
        or candidate.successor.data_review_candidate_sha256
        != command.expected_successor_data_review_candidate_sha256
    ):
        raise _stale()


def _replacement_command_id(command: DecideResultReplacementCommand) -> str:
    return str(
        uuid5(
            NAMESPACE_URL,
            "mass-production-quality-validation:result-replacement:"
            f"{command.project_key}:{command.predecessor_result_id}:{command.successor_result_id}",
        )
    )


def _replacement_intent_sha256(command: DecideResultReplacementCommand) -> str:
    return canonical_json_sha256(
        {
            "project_key": command.project_key,
            "predecessor_result_id": command.predecessor_result_id,
            "successor_result_id": command.successor_result_id,
            "candidate_sha256": command.candidate_sha256,
            "expected_predecessor_result_row_version": (
                command.expected_predecessor_result_row_version
            ),
            "expected_successor_result_row_version": command.expected_successor_result_row_version,
            "expected_predecessor_measurement_set_sha256": (
                command.expected_predecessor_measurement_set_sha256
            ),
            "expected_successor_measurement_set_sha256": (
                command.expected_successor_measurement_set_sha256
            ),
            "expected_predecessor_decision_transition_id": (
                command.expected_predecessor_decision_transition_id
            ),
            "expected_successor_data_review_candidate_sha256": (
                command.expected_successor_data_review_candidate_sha256
            ),
            "confirmed": command.confirmed,
            "reason": command.reason,
            "actor": {
                "actor_id": command.actor.actor_id,
                "kind": command.actor.kind.value,
                "roles": sorted(role.value for role in command.actor.roles),
            },
        }
    )


def _append_replacement_audit(
    session: object,
    *,
    audit_repository: AuditRepository,
    actor: Actor,
    reason: str,
    candidate: ResultReplacementCandidate,
    replacement: ResultReplacementTransitionRow,
    occurred_at: datetime,
) -> None:
    from sqlalchemy.orm import Session

    if not isinstance(session, Session):
        raise TypeError("replacement Audit requires one SQLAlchemy Session")
    before_state, after_state = result_replacement_audit_states(replacement)
    audit = audit_repository.append(
        session,
        AuditChange(
            actor=actor,
            action="RESULT_REPLACED",
            target_type="RESULT_REPLACEMENT",
            target_id=f"{candidate.project_key}:{replacement.id}",
            before_state=before_state,
            after_state=after_state,
            reason=reason,
            requirement_id="ING-042",
            source_reference=(
                f"results:{candidate.predecessor.result_id}->{candidate.successor.result_id}"
            ),
        ),
    )
    audit.occurred_at = occurred_at
    session.flush()


def _validate_pair_audit(session: object, row: ResultReplacementTransitionRow) -> None:
    from sqlalchemy.orm import Session

    if not isinstance(session, Session):
        raise TypeError("replacement Audit validation requires one SQLAlchemy Session")
    audits = tuple(
        session.scalars(
            select(AuditLog).where(
                AuditLog.action == "RESULT_REPLACED",
                AuditLog.target_type == "RESULT_REPLACEMENT",
                AuditLog.target_id == f"{row.project_key}:{row.id}",
            )
        ).all()
    )
    before_state, after_state = result_replacement_audit_states(row)
    source_reference = f"results:{row.predecessor_result_id}->{row.successor_result_id}"
    matches = [
        value
        for value in audits
        if value.actor_id == row.decided_by
        and value.actor_kind == "LOCAL_OWNER"
        and "ADMIN" in value.actor_roles
        and value.occurred_at == row.decided_at
        and value.reason == row.reason
        and value.requirement_id == "ING-042"
        and value.source_reference == source_reference
        and value.before_state == before_state
        and value.after_state == after_state
    ]
    if len(matches) != 1:
        raise ResultReplacementPersistenceError("matching replacement Audit evidence is absent")


def result_replacement_audit_states(
    row: ResultReplacementTransitionRow,
) -> tuple[dict[str, object], dict[str, object]]:
    return (
        {
            "predecessor_result_id": row.predecessor_result_id,
            "predecessor_source_file_id": row.predecessor_source_file_id,
            "predecessor_lot_id": row.predecessor_lot_id,
            "predecessor_original_data_status_transition_id": (
                row.predecessor_original_data_status_transition_id
            ),
            "predecessor_status": row.predecessor_before_status,
            "predecessor_result_row_version": row.predecessor_before_result_row_version,
            "predecessor_measurement_count": row.predecessor_measurement_count,
            "predecessor_measurement_set_sha256": row.predecessor_measurement_set_sha256,
            "successor_result_id": row.successor_result_id,
            "successor_source_file_id": row.successor_source_file_id,
            "successor_lot_id": row.successor_lot_id,
            "successor_status": row.successor_before_status,
            "successor_result_row_version": row.successor_before_result_row_version,
            "successor_measurement_count": row.successor_measurement_count,
            "successor_measurement_set_sha256": row.successor_measurement_set_sha256,
            "candidate_sha256": row.candidate_sha256,
        },
        {
            "replacement_id": row.id,
            "predecessor_result_id": row.predecessor_result_id,
            "predecessor_source_file_id": row.predecessor_source_file_id,
            "predecessor_lot_id": row.predecessor_lot_id,
            "predecessor_original_data_status_transition_id": (
                row.predecessor_original_data_status_transition_id
            ),
            "predecessor_status": row.predecessor_after_status,
            "predecessor_result_row_version": row.predecessor_after_result_row_version,
            "predecessor_measurement_count": row.predecessor_measurement_count,
            "predecessor_measurement_set_sha256": row.predecessor_measurement_set_sha256,
            "successor_result_id": row.successor_result_id,
            "successor_source_file_id": row.successor_source_file_id,
            "successor_lot_id": row.successor_lot_id,
            "successor_status": row.successor_after_status,
            "successor_result_row_version": row.successor_after_result_row_version,
            "successor_measurement_count": row.successor_measurement_count,
            "successor_measurement_set_sha256": row.successor_measurement_set_sha256,
            "successor_data_status_transition_id": row.successor_data_status_transition_id,
            "candidate_sha256": row.candidate_sha256,
            "intent_sha256": row.intent_sha256,
        },
    )


def _bounded_json(value: object) -> str | None:
    if value is None:
        return None
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return payload if len(payload) <= 500 else payload[:497] + "..."


def _request_text(value: str, name: str, maximum: int) -> None:
    if not value or value != value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must be an exact bounded value")


def _sha256(value: str, name: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")


def _not_found() -> ResultReplacementMissingError:
    return ResultReplacementMissingError(
        "RESULT_REPLACEMENT_NOT_FOUND",
        "요청한 수정본 연결 대상을 찾을 수 없습니다.",
        "대상 없음",
    )


def _ineligible(message: str) -> ResultReplacementIneligibleError:
    return ResultReplacementIneligibleError(
        "RESULT_REPLACEMENT_INELIGIBLE",
        message,
        "수정본 연결 보류",
    )


def _stale() -> ResultReplacementStaleError:
    return ResultReplacementStaleError(
        "RESULT_REPLACEMENT_STALE",
        "검토 후 결과 또는 근거가 변경되었습니다. 후보를 다시 조회해 주세요.",
        "최신 근거 재조회 필요",
    )


def _integrity() -> ResultReplacementError:
    return ResultReplacementError(
        "RESULT_REPLACEMENT_EVIDENCE_INTEGRITY",
        "저장된 수정본 근거의 무결성을 확인할 수 없습니다.",
        "근거 확인 필요",
    )


def _unavailable() -> ResultReplacementUnavailableError:
    return ResultReplacementUnavailableError(
        "RESULT_REPLACEMENT_DATABASE_UNAVAILABLE",
        "수정본 연결 저장소를 사용할 수 없습니다.",
        "서비스 확인 필요",
    )


def _utc_now() -> datetime:
    return datetime.now(UTC)
