"""Read-only review candidates and explicit ADMIN data-status decisions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.domain.audit import AuditChange
from app.domain.data_review import (
    DataReviewBasis,
    DataReviewCandidate,
    ReviewCandidateState,
    ReviewedSample,
    ReviewIssue,
    ReviewIssueCode,
    SampleComparison,
    SystemJudgment,
    canonical_json_sha256,
)
from app.domain.identity import Actor, Role
from app.domain.long_format import LongDataStatus, MeasurementMode, SpecEvaluationStatus
from app.domain.mapping import SystemJudgmentStatus
from app.domain.master_config import InspectionItemDisposition
from app.infrastructure.audit import AuditRepository
from app.infrastructure.data_review import (
    DataReviewCommandConflictError,
    DataReviewRepository,
    PersistedDataStatusDecision,
    StaleDataReviewWriteError,
)
from app.infrastructure.database import Database


class DataReviewAuthorizationError(PermissionError):
    pass


class DataReviewCandidateError(ValueError):
    pass


class StaleDataReviewCandidateError(DataReviewCandidateError):
    pass


class IneligibleDataReviewCandidateError(DataReviewCandidateError):
    pass


@dataclass(frozen=True, slots=True, order=True)
class ExpectedMeasurementVersion:
    sample_ordinal: int
    measurement_id: str
    row_version: int

    def __post_init__(self) -> None:
        _require_exact(self.measurement_id, "measurement_id")
        if self.sample_ordinal < 1 or self.row_version < 1:
            raise ValueError("measurement ordinal and row_version must be positive")


@dataclass(frozen=True, slots=True)
class ExpectedMasterVersion:
    history_id: str
    revision_id: str
    history_row_version: int
    revision_row_version: int
    payload_sha256: str

    def __post_init__(self) -> None:
        _require_exact(self.history_id, "history_id")
        _require_exact(self.revision_id, "revision_id")
        if min(self.history_row_version, self.revision_row_version) < 1:
            raise ValueError("Master row versions must be positive")
        _require_sha256(self.payload_sha256, "payload_sha256")


@dataclass(frozen=True, slots=True)
class DecideDataStatusCommand:
    project_key: str
    result_id: str
    command_id: str
    target_status: LongDataStatus
    expected_candidate_sha256: str
    expected_result_row_version: int
    expected_measurement_versions: tuple[ExpectedMeasurementVersion, ...]
    expected_item_row_version: int
    expected_master: ExpectedMasterVersion | None
    actor: Actor
    reason: str

    def __post_init__(self) -> None:
        for name in ("project_key", "result_id", "command_id", "reason"):
            _require_exact(getattr(self, name), name)
        _require_sha256(self.expected_candidate_sha256, "expected_candidate_sha256")
        if self.expected_result_row_version < 1 or self.expected_item_row_version < 1:
            raise ValueError("expected result/item row versions must be positive")
        if tuple(sorted(self.expected_measurement_versions)) != (
            self.expected_measurement_versions
        ):
            raise ValueError("expected measurement versions must be deterministically sorted")
        ids = tuple(value.measurement_id for value in self.expected_measurement_versions)
        ordinals = tuple(value.sample_ordinal for value in self.expected_measurement_versions)
        if len(set(ids)) != len(ids):
            raise ValueError("expected measurement IDs must be unique")
        if len(set(ordinals)) != len(ordinals):
            raise ValueError("expected measurement ordinals must be unique")
        if self.target_status not in {
            LongDataStatus.VALID,
            LongDataStatus.SUSPECT,
            LongDataStatus.EXCLUDED,
        }:
            raise ValueError("this command accepts only an explicit terminal review decision")


def build_data_review_candidate(basis: DataReviewBasis) -> DataReviewCandidate:
    """Evaluate only exact raw numeric samples against one approved Master."""

    blocking = list(basis.blocking_issues)
    review_only = list(basis.review_only_issues)
    if basis.data_status == LongDataStatus.HELD:
        blocking.append(_issue(ReviewIssueCode.RESULT_HELD, "HELD never transitions"))
    elif basis.data_status != LongDataStatus.PENDING:
        blocking.append(
            _issue(
                ReviewIssueCode.RESULT_NOT_PENDING,
                f"result status {basis.data_status.value} is not reviewable",
            )
        )
    if basis.item_disposition is None:
        blocking.append(_issue(ReviewIssueCode.ITEM_NOT_MAPPED, "item decision is absent"))
    elif basis.item_disposition == InspectionItemDisposition.CANDIDATE:
        blocking.append(
            _issue(ReviewIssueCode.ITEM_CANDIDATE, "CANDIDATE requires an item decision")
        )

    if blocking:
        return _non_evaluated_candidate(
            basis,
            state=ReviewCandidateState.INELIGIBLE,
            issues=blocking + review_only,
            allowed=(),
        )

    if basis.item_disposition == InspectionItemDisposition.EXCLUDED:
        return _non_evaluated_candidate(
            basis,
            state=ReviewCandidateState.REVIEW_ONLY,
            issues=review_only,
            allowed=(LongDataStatus.EXCLUDED,),
        )

    if basis.inspection_date is None:
        review_only.append(
            _issue(
                ReviewIssueCode.INSPECTION_DATE_MISSING,
                "approved Master cannot be selected without inspection date",
            )
        )
    if not basis.masters:
        review_only.append(
            _issue(ReviewIssueCode.MASTER_NOT_FOUND, "no approved/effective Master exists")
        )
    elif len(basis.masters) > 1:
        blocking.append(
            _issue(
                ReviewIssueCode.MASTER_AMBIGUOUS,
                "multiple approved/effective Master revisions exist",
            )
        )
    if blocking:
        return _non_evaluated_candidate(
            basis,
            state=ReviewCandidateState.INELIGIBLE,
            issues=blocking + review_only,
            allowed=(),
        )

    if not basis.masters:
        return _non_evaluated_candidate(
            basis,
            state=ReviewCandidateState.REVIEW_ONLY,
            issues=review_only,
            allowed=(LongDataStatus.EXCLUDED, LongDataStatus.SUSPECT),
        )
    master = basis.masters[0]
    if basis.measurement_mode != MeasurementMode.NUMERIC:
        review_only.append(
            _issue(
                ReviewIssueCode.QUALITATIVE_REVIEW_REQUIRED,
                "only an exact NUMERIC row binding can be evaluated",
            )
        )
    if not basis.measurements:
        review_only.append(_issue(ReviewIssueCode.ZERO_MEASUREMENTS, "no raw samples are present"))
    if basis.source_unit is None:
        if not any(issue.code == ReviewIssueCode.UNIT_EVIDENCE_MISSING for issue in review_only):
            review_only.append(
                _issue(ReviewIssueCode.UNIT_EVIDENCE_MISSING, "exact source unit is absent")
            )
    elif basis.source_unit.raw_value != master.unit:
        review_only.append(
            _issue(
                ReviewIssueCode.UNIT_MISMATCH,
                f"source unit {basis.source_unit.raw_value!r} != Master unit {master.unit!r}",
            )
        )
    if any(
        value.numeric_value is None or value.formula_flag for value in basis.measurements
    ) and not any(
        issue.code
        in {
            ReviewIssueCode.NON_NUMERIC_MEASUREMENT,
            ReviewIssueCode.NONFINITE_MEASUREMENT,
            ReviewIssueCode.FORMULA_MEASUREMENT,
        }
        for issue in review_only
    ):
        review_only.append(
            _issue(
                ReviewIssueCode.NON_NUMERIC_MEASUREMENT,
                "at least one sample is not an exact raw finite numeric value",
            )
        )
    if review_only:
        return _non_evaluated_candidate(
            basis,
            state=ReviewCandidateState.REVIEW_ONLY,
            issues=review_only,
            allowed=(LongDataStatus.EXCLUDED, LongDataStatus.SUSPECT),
        )

    reviewed_samples: list[ReviewedSample] = []
    failed = False
    for evidence in basis.measurements:
        value = evidence.numeric_value
        if value is None:  # guarded by REVIEW_ONLY construction above
            raise AssertionError("numeric review candidate lost one exact numeric sample")
        comparison = SampleComparison.WITHIN_LIMITS
        if master.lsl is not None and value < master.lsl:
            comparison = SampleComparison.BELOW_LSL
        elif master.usl is not None and value > master.usl:
            comparison = SampleComparison.ABOVE_USL
        if comparison != SampleComparison.WITHIN_LIMITS:
            failed = True
        reviewed_samples.append(ReviewedSample(evidence=evidence, comparison=comparison))
    return DataReviewCandidate(
        basis=basis,
        state=ReviewCandidateState.EVALUATED,
        issues=(),
        selected_master=master,
        samples=tuple(reviewed_samples),
        proposed_system_judgment=(SystemJudgment.FAIL if failed else SystemJudgment.PASS),
        proposed_system_judgment_status=SystemJudgmentStatus.EVALUATED,
        proposed_spec_evaluation_status=SpecEvaluationStatus.EVALUATED_APPROVED_MASTER,
        allowed_target_statuses=(
            LongDataStatus.EXCLUDED,
            LongDataStatus.SUSPECT,
            LongDataStatus.VALID,
        ),
    )


class DataStatusReviewService:
    def __init__(
        self,
        database: Database,
        *,
        repository: DataReviewRepository | None = None,
        audit_repository: AuditRepository | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._database = database
        self._repository = repository or DataReviewRepository()
        self._audit = audit_repository or AuditRepository()
        self._clock = clock or _utc_now

    def candidate(self, *, project_key: str, result_id: str) -> DataReviewCandidate:
        with self._database.session() as session:
            basis = self._repository.load_basis(
                session,
                project_key=project_key,
                result_id=result_id,
                lock=False,
            )
        return build_data_review_candidate(basis)

    def decide(self, command: DecideDataStatusCommand) -> PersistedDataStatusDecision:
        if not command.actor.has_role(Role.ADMIN):
            raise DataReviewAuthorizationError("data-status decision requires ADMIN")
        intent_sha256 = data_review_intent_sha256(command)
        try:
            return self._decide_once(command, intent_sha256)
        except StaleDataReviewWriteError as error:
            # A same-command race can lose at the unique insert after both
            # callers observed no row.  The failed transaction is already
            # rolled back here; replay only the now-committed identical intent.
            with self._database.session() as session:
                existing = self._repository.find_by_command(
                    session,
                    project_key=command.project_key,
                    command_id=command.command_id,
                )
                if existing is None:
                    raise error
                if existing.intent_sha256 != intent_sha256:
                    raise DataReviewCommandConflictError(
                        "command_id was concurrently used for a different intent"
                    ) from error
                return self._repository.replayed_decision(session, existing)

    def _decide_once(
        self,
        command: DecideDataStatusCommand,
        intent_sha256: str,
    ) -> PersistedDataStatusDecision:
        with self._database.session() as session, session.begin():
            existing = self._repository.find_by_command(
                session,
                project_key=command.project_key,
                command_id=command.command_id,
            )
            if existing is not None:
                if existing.intent_sha256 != intent_sha256:
                    raise DataReviewCommandConflictError(
                        "command_id was already used for a different data-status intent"
                    )
                return self._repository.replayed_decision(session, existing)

            basis = self._repository.load_basis(
                session,
                project_key=command.project_key,
                result_id=command.result_id,
                lock=True,
            )
            candidate = build_data_review_candidate(basis)
            validate_data_review_expectations(command, candidate)
            if candidate.state == ReviewCandidateState.INELIGIBLE:
                raise IneligibleDataReviewCandidateError(
                    "the exact result is structurally ineligible for a data-status decision"
                )
            if command.target_status not in candidate.allowed_target_statuses:
                raise IneligibleDataReviewCandidateError(
                    "target status is not allowed by the rebuilt exact candidate"
                )
            decided_at = self._occurred_at()
            decision = self._repository.apply_decision(
                session,
                candidate=candidate,
                command_id=command.command_id,
                intent_sha256=intent_sha256,
                target_status=command.target_status,
                decided_by=command.actor.actor_id,
                decided_at=decided_at,
                reason=command.reason,
            )
            append_data_status_decision_audit(
                session,
                audit_repository=self._audit,
                actor=command.actor,
                reason=command.reason,
                candidate=candidate,
                decision=decision,
                occurred_at=decided_at,
            )
            return decision

    def _occurred_at(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("data-status clock must return a timezone-aware datetime")
        return value.astimezone(UTC)


def _non_evaluated_candidate(
    basis: DataReviewBasis,
    *,
    state: ReviewCandidateState,
    issues: list[ReviewIssue],
    allowed: tuple[LongDataStatus, ...],
) -> DataReviewCandidate:
    return DataReviewCandidate(
        basis=basis,
        state=state,
        issues=tuple(sorted(set(issues))),
        selected_master=None,
        samples=tuple(
            ReviewedSample(evidence=value, comparison=SampleComparison.NOT_EVALUATED)
            for value in basis.measurements
        ),
        proposed_system_judgment=None,
        proposed_system_judgment_status=SystemJudgmentStatus.NOT_EVALUATED,
        proposed_spec_evaluation_status=SpecEvaluationStatus.NOT_EVALUATED,
        allowed_target_statuses=tuple(sorted(allowed, key=lambda value: value.value)),
    )


def data_review_intent_sha256(command: DecideDataStatusCommand) -> str:
    return canonical_json_sha256(
        {
            "project_key": command.project_key,
            "result_id": command.result_id,
            "command_id": command.command_id,
            "target_status": command.target_status.value,
            "expected_candidate_sha256": command.expected_candidate_sha256,
            "expected_result_row_version": command.expected_result_row_version,
            "expected_measurement_versions": [
                {
                    "sample_ordinal": value.sample_ordinal,
                    "measurement_id": value.measurement_id,
                    "row_version": value.row_version,
                }
                for value in command.expected_measurement_versions
            ],
            "expected_item_row_version": command.expected_item_row_version,
            "expected_master": (
                {
                    "history_id": command.expected_master.history_id,
                    "revision_id": command.expected_master.revision_id,
                    "history_row_version": command.expected_master.history_row_version,
                    "revision_row_version": command.expected_master.revision_row_version,
                    "payload_sha256": command.expected_master.payload_sha256,
                }
                if command.expected_master is not None
                else None
            ),
            "actor": {
                "actor_id": command.actor.actor_id,
                "kind": command.actor.kind.value,
                "roles": sorted(role.value for role in command.actor.roles),
            },
            "reason": command.reason,
        }
    )


def validate_data_review_expectations(
    command: DecideDataStatusCommand,
    candidate: DataReviewCandidate,
) -> None:
    basis = candidate.basis
    if (
        candidate.candidate_sha256 != command.expected_candidate_sha256
        or basis.result_row_version != command.expected_result_row_version
        or basis.item_row_version != command.expected_item_row_version
    ):
        raise StaleDataReviewCandidateError(
            "result, item, or candidate digest changed before the decision"
        )
    actual_measurements = tuple(
        ExpectedMeasurementVersion(
            value.sample_ordinal,
            value.measurement_id,
            value.row_version,
        )
        for value in basis.measurements
    )
    if actual_measurements != command.expected_measurement_versions:
        raise StaleDataReviewCandidateError(
            "measurement identity or row_version changed before the decision"
        )
    selected = candidate.selected_master
    if selected is None:
        if command.expected_master is not None:
            raise StaleDataReviewCandidateError(
                "candidate no longer has the expected approved Master"
            )
    else:
        expected = command.expected_master
        if expected is None or (
            expected.history_id != selected.history_id
            or expected.revision_id != selected.revision_id
            or expected.history_row_version != selected.history_row_version
            or expected.revision_row_version != selected.revision_row_version
            or expected.payload_sha256 != selected.payload_sha256
        ):
            raise StaleDataReviewCandidateError(
                "approved Master identity, version, or digest changed before decision"
            )


def append_data_status_decision_audit(
    session: Session,
    *,
    audit_repository: AuditRepository,
    actor: Actor,
    reason: str,
    candidate: DataReviewCandidate,
    decision: PersistedDataStatusDecision,
    occurred_at: datetime,
    requirement_id: str | None = None,
) -> None:
    """Append the established decision Audit inside a caller-owned transaction."""

    basis = candidate.basis
    audit = audit_repository.append(
        session,
        AuditChange(
            actor=actor,
            action="DATA_STATUS_DECIDED",
            target_type="INSPECTION_RESULT",
            target_id=f"{basis.project_key}:{basis.result_id}",
            before_state={
                "data_status": basis.data_status.value,
                "result_row_version": basis.result_row_version,
                "measurement_versions": [
                    {
                        "sample_ordinal": value.sample_ordinal,
                        "measurement_id": value.measurement_id,
                        "row_version": value.row_version,
                    }
                    for value in basis.measurements
                ],
                "candidate_sha256": candidate.candidate_sha256,
            },
            after_state={
                "data_status": decision.target_status.value,
                "result_row_version": decision.result_row_version,
                "transition_id": decision.transition_id,
                "evaluation_mode": decision.evaluation_mode.value,
                "system_judgment": (
                    decision.system_judgment.value if decision.system_judgment is not None else None
                ),
                "master_history_id": (
                    decision.master.history_id if decision.master is not None else None
                ),
                "master_revision_id": (
                    decision.master.revision_id if decision.master is not None else None
                ),
                "master_payload_sha256": (
                    decision.master.payload_sha256 if decision.master is not None else None
                ),
            },
            reason=reason,
            requirement_id=requirement_id,
            source_reference=f"result:{basis.result_id}",
        ),
    )
    audit.occurred_at = occurred_at
    session.flush()


def _issue(code: ReviewIssueCode, detail: str) -> ReviewIssue:
    return ReviewIssue(code=code, detail=detail)


def _require_exact(value: str, name: str) -> None:
    if not value or value != value.strip():
        raise ValueError(f"{name} must be an exact non-blank value")


def _require_sha256(value: str, name: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")


def _utc_now() -> datetime:
    return datetime.now(UTC)
