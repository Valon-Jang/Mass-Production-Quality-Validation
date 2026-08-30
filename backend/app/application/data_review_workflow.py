"""HTTP-facing orchestration for explicit data-status review decisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.application.data_review import (
    DataReviewAuthorizationError,
    DataStatusReviewService,
    DecideDataStatusCommand,
    ExpectedMasterVersion,
    ExpectedMeasurementVersion,
    IneligibleDataReviewCandidateError,
    StaleDataReviewCandidateError,
)
from app.domain.data_review import DataReviewCandidate, canonical_json_sha256
from app.domain.identity import LOCAL_OWNER
from app.domain.long_format import LongDataStatus
from app.infrastructure.data_review import (
    DataReviewCommandConflictError,
    DataReviewNotFoundError,
    DataReviewPersistenceError,
    PersistedDataStatusDecision,
    StaleDataReviewWriteError,
)
from app.infrastructure.database import Database
from app.infrastructure.long_format import (
    LongIngestionJobRow,
    LongInspectionResultRow,
    OqcLotRow,
)


@dataclass(frozen=True, slots=True)
class DataReviewTargetsRequest:
    project_key: str
    ingestion_job_id: str


@dataclass(frozen=True, slots=True)
class DataReviewCandidateRequest:
    project_key: str
    result_id: str


@dataclass(frozen=True, slots=True, order=True)
class DataReviewExpectedMeasurement:
    sample_ordinal: int
    measurement_id: str
    row_version: int

    def __post_init__(self) -> None:
        _exact(self.measurement_id, "measurement_id")
        if self.sample_ordinal < 1 or self.row_version < 1:
            raise ValueError("measurement ordinal and row_version must be positive")


@dataclass(frozen=True, slots=True)
class DataReviewExpectedMaster:
    history_id: str
    revision_id: str
    history_row_version: int
    revision_row_version: int
    payload_sha256: str

    def __post_init__(self) -> None:
        _exact(self.history_id, "history_id")
        _exact(self.revision_id, "revision_id")
        if min(self.history_row_version, self.revision_row_version) < 1:
            raise ValueError("Master row versions must be positive")
        _sha256(self.payload_sha256, "payload_sha256")


@dataclass(frozen=True, slots=True)
class DataReviewCandidateCas:
    expected_result_row_version: int
    expected_item_row_version: int | None
    expected_measurement_versions: tuple[DataReviewExpectedMeasurement, ...]
    expected_master: DataReviewExpectedMaster | None

    def __post_init__(self) -> None:
        if self.expected_result_row_version < 1:
            raise ValueError("expected result row_version must be positive")
        if self.expected_item_row_version is not None and self.expected_item_row_version < 1:
            raise ValueError("expected item row_version must be positive")
        if tuple(sorted(self.expected_measurement_versions)) != self.expected_measurement_versions:
            raise ValueError("expected measurements must be deterministically ordered")
        ids = tuple(value.measurement_id for value in self.expected_measurement_versions)
        ordinals = tuple(value.sample_ordinal for value in self.expected_measurement_versions)
        if len(set(ids)) != len(ids) or len(set(ordinals)) != len(ordinals):
            raise ValueError("expected measurement identity must be unique")


@dataclass(frozen=True, slots=True)
class DecideDataReviewRequest:
    project_key: str
    result_id: str
    target_status: LongDataStatus
    candidate_sha256: str
    cas: DataReviewCandidateCas
    reason: str
    confirmed: bool


@dataclass(frozen=True, slots=True)
class DataReviewTarget:
    result_id: str
    source_row_key: str
    data_status: LongDataStatus
    row_version: int
    canonical_item_key: str | None
    lot_id: str
    lot_ordinal: int
    source_lot_text: str | None
    inspection_date: date | None
    reviewable: bool


@dataclass(frozen=True, slots=True)
class DataReviewTargetList:
    project_key: str
    ingestion_job_id: str
    job_status: str
    targets: tuple[DataReviewTarget, ...]
    official_values_created: bool = False


class DataReviewCorePort(Protocol):
    def candidate(self, *, project_key: str, result_id: str) -> DataReviewCandidate: ...

    def decide(self, command: DecideDataStatusCommand) -> PersistedDataStatusDecision: ...


class DataReviewWorkflowError(RuntimeError):
    """Stable safe error that never exposes database or source internals."""

    def __init__(self, code: str, message: str, status_label: str) -> None:
        self.code = code
        self.safe_message = message
        self.status_label = status_label
        super().__init__(code)


class DataReviewWorkflowValidationError(DataReviewWorkflowError):
    pass


class DataReviewWorkflowNotFoundError(DataReviewWorkflowError):
    pass


class DataReviewWorkflowConflictError(DataReviewWorkflowError):
    pass


class DataReviewWorkflowUnavailableError(DataReviewWorkflowError):
    pass


class DataReviewWorkflowService:
    """Expose existing evidence reconstruction and atomic decision boundaries."""

    def __init__(
        self,
        database: Database,
        *,
        review_service: DataReviewCorePort | None = None,
    ) -> None:
        self._database = database
        self._review = review_service or DataStatusReviewService(database)

    def targets(self, request: DataReviewTargetsRequest) -> DataReviewTargetList:
        _validate_scope(request.project_key, request.ingestion_job_id)
        try:
            with self._database.session() as session:
                job = session.scalar(
                    select(LongIngestionJobRow).where(
                        LongIngestionJobRow.project_key == request.project_key,
                        LongIngestionJobRow.id == request.ingestion_job_id,
                    )
                )
                if job is None:
                    raise DataReviewWorkflowNotFoundError(
                        "DATA_REVIEW_JOB_NOT_FOUND",
                        "해당 프로젝트에서 Long 적재 결과를 찾을 수 없습니다.",
                        "검토 대상 없음",
                    )
                rows = session.execute(
                    select(LongInspectionResultRow, OqcLotRow)
                    .join(
                        OqcLotRow,
                        (OqcLotRow.project_key == LongInspectionResultRow.project_key)
                        & (OqcLotRow.id == LongInspectionResultRow.oqc_lot_id)
                        & (OqcLotRow.source_file_id == LongInspectionResultRow.source_file_id),
                    )
                    .where(
                        LongInspectionResultRow.project_key == request.project_key,
                        OqcLotRow.ingestion_job_id == request.ingestion_job_id,
                    )
                    .order_by(
                        OqcLotRow.lot_ordinal,
                        LongInspectionResultRow.source_row_key,
                        LongInspectionResultRow.id,
                    )
                ).all()
                targets = tuple(
                    DataReviewTarget(
                        result_id=result.id,
                        source_row_key=result.source_row_key,
                        data_status=LongDataStatus(result.data_status),
                        row_version=result.row_version,
                        canonical_item_key=result.canonical_item_key,
                        lot_id=lot.id,
                        lot_ordinal=lot.lot_ordinal,
                        source_lot_text=lot.source_lot_text,
                        inspection_date=lot.inspection_date,
                        reviewable=result.data_status == LongDataStatus.PENDING.value,
                    )
                    for result, lot in rows
                )
                return DataReviewTargetList(
                    project_key=request.project_key,
                    ingestion_job_id=request.ingestion_job_id,
                    job_status=job.status,
                    targets=targets,
                )
        except DataReviewWorkflowError:
            raise
        except (LookupError, TypeError, ValueError) as error:
            raise _conflict("DATA_REVIEW_TARGET_INTEGRITY_ERROR") from error
        except (SQLAlchemyError, OSError, RuntimeError) as error:
            raise _unavailable("DATA_REVIEW_TARGETS_UNAVAILABLE") from error

    def candidate(self, request: DataReviewCandidateRequest) -> DataReviewCandidate:
        _validate_scope(request.project_key, request.result_id)
        try:
            return self._review.candidate(
                project_key=request.project_key,
                result_id=request.result_id,
            )
        except DataReviewNotFoundError as error:
            raise DataReviewWorkflowNotFoundError(
                "DATA_REVIEW_RESULT_NOT_FOUND",
                "해당 프로젝트에서 검사 결과를 찾을 수 없습니다.",
                "검사 결과 없음",
            ) from error
        except DataReviewPersistenceError as error:
            raise _conflict("DATA_REVIEW_EVIDENCE_INTEGRITY_ERROR") from error
        except (TypeError, ValueError) as error:
            raise _conflict("DATA_REVIEW_CANDIDATE_INTEGRITY_ERROR") from error
        except (SQLAlchemyError, OSError, RuntimeError) as error:
            raise _unavailable("DATA_REVIEW_CANDIDATE_UNAVAILABLE") from error

    def decide(self, request: DecideDataReviewRequest) -> PersistedDataStatusDecision:
        _validate_decision_request(request)
        item_row_version = request.cas.expected_item_row_version
        if item_row_version is None:
            raise DataReviewWorkflowValidationError(
                "INVALID_DATA_REVIEW_CAS",
                "결정 가능한 검사항목 버전이 필요합니다.",
                "검토 근거 오류",
            )
        command = DecideDataStatusCommand(
            project_key=request.project_key,
            result_id=request.result_id,
            command_id=_command_id(request),
            target_status=request.target_status,
            expected_candidate_sha256=request.candidate_sha256,
            expected_result_row_version=request.cas.expected_result_row_version,
            expected_measurement_versions=tuple(
                ExpectedMeasurementVersion(
                    sample_ordinal=value.sample_ordinal,
                    measurement_id=value.measurement_id,
                    row_version=value.row_version,
                )
                for value in request.cas.expected_measurement_versions
            ),
            expected_item_row_version=item_row_version,
            expected_master=(
                ExpectedMasterVersion(
                    history_id=request.cas.expected_master.history_id,
                    revision_id=request.cas.expected_master.revision_id,
                    history_row_version=request.cas.expected_master.history_row_version,
                    revision_row_version=request.cas.expected_master.revision_row_version,
                    payload_sha256=request.cas.expected_master.payload_sha256,
                )
                if request.cas.expected_master is not None
                else None
            ),
            actor=LOCAL_OWNER,
            reason=request.reason,
        )
        try:
            return self._review.decide(command)
        except DataReviewNotFoundError as error:
            raise DataReviewWorkflowNotFoundError(
                "DATA_REVIEW_RESULT_NOT_FOUND",
                "해당 프로젝트에서 검사 결과를 찾을 수 없습니다.",
                "검사 결과 없음",
            ) from error
        except (StaleDataReviewCandidateError, StaleDataReviewWriteError) as error:
            raise DataReviewWorkflowConflictError(
                "DATA_REVIEW_CANDIDATE_STALE",
                "검사 결과, 측정값, 검사항목 또는 Master 근거가 변경되었습니다.",
                "검토 후보 변경됨",
            ) from error
        except IneligibleDataReviewCandidateError as error:
            raise DataReviewWorkflowConflictError(
                "DATA_REVIEW_TARGET_NOT_ALLOWED",
                "현재 검토 근거에서는 선택한 데이터상태를 적용할 수 없습니다.",
                "결정 적용 불가",
            ) from error
        except DataReviewCommandConflictError as error:
            raise DataReviewWorkflowConflictError(
                "DATA_REVIEW_COMMAND_CONFLICT",
                "동일한 결정 요청의 저장 근거가 일치하지 않습니다.",
                "결정 요청 충돌",
            ) from error
        except DataReviewAuthorizationError as error:
            raise _unavailable("DATA_REVIEW_AUTHORIZATION_UNAVAILABLE") from error
        except DataReviewPersistenceError as error:
            raise _conflict("DATA_REVIEW_PERSISTENCE_INTEGRITY_ERROR") from error
        except (SQLAlchemyError, OSError, RuntimeError) as error:
            raise _unavailable("DATA_REVIEW_DECISION_UNAVAILABLE") from error


def candidate_cas(candidate: DataReviewCandidate) -> DataReviewCandidateCas:
    basis = candidate.basis
    selected = candidate.selected_master
    return DataReviewCandidateCas(
        expected_result_row_version=basis.result_row_version,
        expected_item_row_version=basis.item_row_version,
        expected_measurement_versions=tuple(
            DataReviewExpectedMeasurement(
                sample_ordinal=value.sample_ordinal,
                measurement_id=value.measurement_id,
                row_version=value.row_version,
            )
            for value in basis.measurements
        ),
        expected_master=(
            DataReviewExpectedMaster(
                history_id=selected.history_id,
                revision_id=selected.revision_id,
                history_row_version=selected.history_row_version,
                revision_row_version=selected.revision_row_version,
                payload_sha256=selected.payload_sha256,
            )
            if selected is not None
            else None
        ),
    )


def _validate_scope(project_key: str, identity: str) -> None:
    try:
        _exact(project_key, "project_key")
        _exact(identity, "identity")
    except ValueError as error:
        raise DataReviewWorkflowValidationError(
            "INVALID_DATA_REVIEW_SCOPE",
            "프로젝트와 검토 대상 식별자를 정확히 입력해 주세요.",
            "검토 요청 오류",
        ) from error


def _validate_decision_request(request: DecideDataReviewRequest) -> None:
    _validate_scope(request.project_key, request.result_id)
    if request.confirmed is not True:
        raise DataReviewWorkflowValidationError(
            "EXPLICIT_DATA_REVIEW_CONFIRMATION_REQUIRED",
            "데이터상태 결정을 명시적으로 확인해 주세요.",
            "명시적 확인 필요",
        )
    try:
        _sha256(request.candidate_sha256, "candidate_sha256")
        _exact(request.reason, "reason")
    except ValueError as error:
        raise DataReviewWorkflowValidationError(
            "INVALID_DATA_REVIEW_DECISION",
            "후보 식별값과 결정 사유를 정확히 입력해 주세요.",
            "결정 요청 오류",
        ) from error
    if len(request.reason) > 2000:
        raise DataReviewWorkflowValidationError(
            "INVALID_DATA_REVIEW_DECISION",
            "결정 사유는 2,000자 이하여야 합니다.",
            "결정 요청 오류",
        )


def _command_id(request: DecideDataReviewRequest) -> str:
    master = request.cas.expected_master
    digest = canonical_json_sha256(
        {
            "project_key": request.project_key,
            "result_id": request.result_id,
            "target_status": request.target_status.value,
            "candidate_sha256": request.candidate_sha256,
            "cas": {
                "expected_result_row_version": request.cas.expected_result_row_version,
                "expected_item_row_version": request.cas.expected_item_row_version,
                "expected_measurement_versions": [
                    {
                        "sample_ordinal": value.sample_ordinal,
                        "measurement_id": value.measurement_id,
                        "row_version": value.row_version,
                    }
                    for value in request.cas.expected_measurement_versions
                ],
                "expected_master": (
                    {
                        "history_id": master.history_id,
                        "revision_id": master.revision_id,
                        "history_row_version": master.history_row_version,
                        "revision_row_version": master.revision_row_version,
                        "payload_sha256": master.payload_sha256,
                    }
                    if master is not None
                    else None
                ),
            },
            "reason": request.reason,
        }
    )
    return f"dstat-ui-{digest}"


def _exact(value: str, name: str) -> None:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be an exact nonblank string")


def _sha256(value: str, name: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{name} must be a lowercase SHA-256")


def _conflict(code: str) -> DataReviewWorkflowConflictError:
    return DataReviewWorkflowConflictError(
        code,
        "저장된 Long 또는 Master 검토 근거의 무결성을 확인할 수 없습니다.",
        "검토 근거 무결성 오류",
    )


def _unavailable(code: str) -> DataReviewWorkflowUnavailableError:
    return DataReviewWorkflowUnavailableError(
        code,
        "데이터상태 검토를 안전하게 처리할 수 없습니다.",
        "데이터상태 검토 불가",
    )
