"""Receipt-replayed pending Long candidate and explicit confirmation workflow."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.exc import SQLAlchemyError

from app.application.long_candidate import LongCandidateInputError, build_long_candidate
from app.application.long_persistence import (
    LongMaterializationFailedError,
    LongPersistenceRequest,
    LongPersistenceResult,
    LongPersistenceService,
    LongPersistenceValidationError,
)
from app.application.mapping_workspace import (
    ApprovedTemplateProof,
    MappingWorkspaceError,
    MappingWorkspaceNotFoundError,
    MappingWorkspaceRequest,
    MappingWorkspaceSnapshot,
    MappingWorkspaceSourceError,
    MappingWorkspaceState,
    MappingWorkspaceValidationError,
)
from app.application.store_scan_mapping import (
    ResolvedMappingScope,
    StoreScanMappingOutcome,
    StoreScanMappingStatus,
)
from app.domain.long_format import LongCandidateResult
from app.domain.mapping import MappingPreviewResult, MappingPreviewState
from app.domain.source_file import SourceFileReceipt
from app.domain.workbook_scan import WorkbookScan
from app.infrastructure.database import Database
from app.infrastructure.long_format import (
    LongPersistenceError,
    canonical_json_sha256,
    serialize_long_candidate,
)
from app.infrastructure.master_config import (
    MasterConfigEffectivePeriodError,
    MasterConfigPayloadIntegrityError,
    MasterConfigPersistenceError,
    MasterConfigRepository,
    MasterConfigScopeError,
)

LONG_UI_LOADER_VERSION = "long-ui-loader-v1"
LONG_UI_SCAN_CONTRACT_VERSION = "openpyxl-workbook-scan-v1"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class LongCandidateRequest:
    project_key: str
    receipt_id: str
    content_sha256: str
    supplier_scope: str


@dataclass(frozen=True, slots=True)
class ConfirmLongCandidateRequest:
    project_key: str
    receipt_id: str
    content_sha256: str
    supplier_scope: str
    candidate_digest: str
    confirmed: bool

    @property
    def candidate_request(self) -> LongCandidateRequest:
        return LongCandidateRequest(
            project_key=self.project_key,
            receipt_id=self.receipt_id,
            content_sha256=self.content_sha256,
            supplier_scope=self.supplier_scope,
        )


@dataclass(frozen=True, slots=True)
class LongWorkflowCandidate:
    candidate: LongCandidateResult
    candidate_digest: str
    mapping_proof: ApprovedTemplateProof
    can_confirm: bool = True
    official_values_created: bool = False
    calculations_performed: bool = False
    auto_valid: bool = False
    ai_called: bool = False

    def __post_init__(self) -> None:
        if not _SHA256_PATTERN.fullmatch(self.candidate_digest):
            raise ValueError("candidate_digest must be a lowercase SHA-256")
        if not self.can_confirm:
            raise ValueError("a reconstructed candidate must remain explicitly confirmable")
        if any(
            (
                self.official_values_created,
                self.calculations_performed,
                self.auto_valid,
                self.ai_called,
            )
        ):
            raise ValueError("pending Long workflow cannot create official results")


@dataclass(frozen=True, slots=True)
class LongWorkflowResult:
    candidate: LongWorkflowCandidate
    persistence: LongPersistenceResult | None


@dataclass(frozen=True, slots=True)
class _RebuiltLongCandidate:
    response: LongWorkflowCandidate
    outcome: StoreScanMappingOutcome


class LongWorkflowError(RuntimeError):
    """Safe application error with no internal path or raw exception text."""

    def __init__(self, code: str, message: str, status_label: str) -> None:
        self.code = code
        self.safe_message = message
        self.status_label = status_label
        super().__init__(code)


class LongWorkflowValidationError(LongWorkflowError):
    pass


class LongWorkflowNotFoundError(LongWorkflowError):
    pass


class LongWorkflowConflictError(LongWorkflowError):
    pass


class LongWorkflowUnavailableError(LongWorkflowError):
    pass


class ReceiptMappingWorkspacePort(Protocol):
    def preview(self, request: MappingWorkspaceRequest) -> MappingWorkspaceSnapshot: ...

    def preview_scanned(
        self,
        request: MappingWorkspaceRequest,
        *,
        receipt: SourceFileReceipt,
        scan: WorkbookScan,
    ) -> MappingWorkspaceSnapshot: ...


class LongWorkflowService:
    """Rebuild every proof for reads and again inside explicit confirmation."""

    def __init__(
        self,
        *,
        database: Database,
        mapping_workspace: ReceiptMappingWorkspacePort,
        master_repository: MasterConfigRepository | None = None,
        persistence_service: LongPersistenceService | None = None,
        loader_version: str = LONG_UI_LOADER_VERSION,
        scan_contract_version: str = LONG_UI_SCAN_CONTRACT_VERSION,
    ) -> None:
        self._database = database
        self._mapping_workspace = mapping_workspace
        self._master_repository = master_repository or MasterConfigRepository()
        self._persistence = persistence_service or LongPersistenceService(database)
        self._loader_version = _exact_version(loader_version, "loader_version")
        self._scan_contract_version = _exact_version(
            scan_contract_version,
            "scan_contract_version",
        )

    def candidate(self, request: LongCandidateRequest) -> LongWorkflowResult:
        rebuilt = self._rebuild(request)
        return LongWorkflowResult(candidate=rebuilt.response, persistence=None)

    def candidate_from_workspace(
        self,
        request: LongCandidateRequest,
        workspace: MappingWorkspaceSnapshot,
    ) -> LongWorkflowResult:
        """Build a read-only candidate without rescanning a validated workspace."""

        _validate_candidate_request(request)
        if (
            workspace.state != MappingWorkspaceState.PREVIEW_READY
            or workspace.receipt.project_key != request.project_key
            or workspace.receipt.receipt_id != request.receipt_id
            or workspace.receipt.content_sha256 != request.content_sha256
            or workspace.supplier_scope != request.supplier_scope
        ):
            raise LongWorkflowConflictError(
                "LONG_WORKSPACE_SCOPE_MISMATCH",
                "승인 매핑 작업공간과 Long 후보 범위가 일치하지 않습니다.",
                "Long 후보 범위 오류",
            )
        rebuilt = self._rebuild_workspace(request, workspace)
        return LongWorkflowResult(candidate=rebuilt.response, persistence=None)

    def candidate_prepared(
        self,
        request: LongCandidateRequest,
        *,
        receipt: SourceFileReceipt,
        scan: WorkbookScan,
    ) -> LongWorkflowResult:
        """Rebuild a candidate from a durable one-time scan, without workbook I/O."""

        workspace = self._prepared_workspace(request, receipt=receipt, scan=scan)
        return self.candidate_from_workspace(request, workspace)

    def confirm(self, request: ConfirmLongCandidateRequest) -> LongWorkflowResult:
        _validate_confirm_request(request)
        rebuilt = self._rebuild(request.candidate_request)
        return self._confirm_rebuilt(request, rebuilt)

    def confirm_prepared(
        self,
        request: ConfirmLongCandidateRequest,
        *,
        receipt: SourceFileReceipt,
        scan: WorkbookScan,
    ) -> LongWorkflowResult:
        """Explicitly persist a strictly reconstructed prepared scan candidate."""

        _validate_confirm_request(request)
        workspace = self._prepared_workspace(request.candidate_request, receipt=receipt, scan=scan)
        rebuilt = self._rebuild_workspace(request.candidate_request, workspace)
        return self._confirm_rebuilt(request, rebuilt)

    def _confirm_rebuilt(
        self,
        request: ConfirmLongCandidateRequest,
        rebuilt: _RebuiltLongCandidate,
    ) -> LongWorkflowResult:
        if request.candidate_digest != rebuilt.response.candidate_digest:
            raise LongWorkflowConflictError(
                "LONG_CANDIDATE_STALE",
                "원본, 매핑 또는 행 연결 근거가 바뀌었습니다. 후보를 다시 확인해 주세요.",
                "Long 후보 변경됨",
            )
        try:
            persisted = self._persistence.persist(
                LongPersistenceRequest(
                    outcome=rebuilt.outcome,
                    candidate=rebuilt.response.candidate,
                    loader_version=self._loader_version,
                    scan_contract_version=self._scan_contract_version,
                )
            )
        except LongPersistenceValidationError as error:
            raise LongWorkflowConflictError(
                "LONG_CONFIRMATION_STALE",
                "확인 시점의 원본 또는 영속 근거가 후보와 일치하지 않습니다.",
                "Long 확인 충돌",
            ) from error
        except LongPersistenceError as error:
            raise LongWorkflowConflictError(
                "LONG_PERSISTENCE_INTEGRITY_ERROR",
                "기존 Long 적재 근거와 현재 후보가 일치하지 않습니다.",
                "Long 무결성 오류",
            ) from error
        except LongMaterializationFailedError as error:
            raise LongWorkflowUnavailableError(
                "LONG_MATERIALIZATION_FAILED",
                "대기 상태 Long 적재를 완료하지 못했습니다.",
                "Long 적재 실패",
            ) from error
        except (SQLAlchemyError, OSError, RuntimeError) as error:
            raise _unavailable("LONG_PERSISTENCE_UNAVAILABLE") from error
        return LongWorkflowResult(candidate=rebuilt.response, persistence=persisted)

    def _prepared_workspace(
        self,
        request: LongCandidateRequest,
        *,
        receipt: SourceFileReceipt,
        scan: WorkbookScan,
    ) -> MappingWorkspaceSnapshot:
        _validate_candidate_request(request)
        if (
            receipt.project_key != request.project_key
            or receipt.receipt_id != request.receipt_id
            or receipt.content_sha256 != request.content_sha256
            or scan.source_sha256_before != request.content_sha256
            or scan.source_sha256_after != request.content_sha256
            or scan.source_size_bytes != receipt.size_bytes
            or scan.source_name != receipt.original_filename
        ):
            raise LongWorkflowConflictError(
                "LONG_PREPARED_SOURCE_MISMATCH",
                "저장된 1회 검사 근거와 원본 Receipt가 일치하지 않습니다.",
                "Long 준비 근거 오류",
            )
        try:
            workspace = self._mapping_workspace.preview_scanned(
                MappingWorkspaceRequest(
                    project_key=request.project_key,
                    receipt_id=request.receipt_id,
                    content_sha256=request.content_sha256,
                    supplier_scope=request.supplier_scope,
                    cell_offset=0,
                    cell_limit=1,
                ),
                receipt=receipt,
                scan=scan,
            )
        except MappingWorkspaceNotFoundError as error:
            raise LongWorkflowNotFoundError(
                "LONG_RECEIPT_NOT_FOUND",
                "해당 프로젝트에서 보존된 원본을 찾을 수 없습니다.",
                "원본 없음",
            ) from error
        except MappingWorkspaceValidationError as error:
            raise _validation("INVALID_LONG_SCOPE") from error
        except MappingWorkspaceSourceError as error:
            raise _unavailable(error.code) from error
        except MappingWorkspaceError as error:
            raise LongWorkflowConflictError(
                "LONG_PREPARED_MAPPING_FAILED",
                "저장된 검사 근거로 승인 매핑을 다시 확인할 수 없습니다.",
                "매핑 재확인 실패",
            ) from error
        if workspace.state != MappingWorkspaceState.PREVIEW_READY:
            raise LongWorkflowConflictError(
                "APPROVED_MAPPING_REQUIRED",
                "저장된 검사 근거에 정확히 일치하는 승인 매핑이 필요합니다.",
                "승인 매핑 필요",
            )
        return workspace

    def _rebuild(self, request: LongCandidateRequest) -> _RebuiltLongCandidate:
        _validate_candidate_request(request)
        workspace = self._mapping_preview(request)
        return self._rebuild_workspace(request, workspace)

    def _rebuild_workspace(
        self,
        request: LongCandidateRequest,
        workspace: MappingWorkspaceSnapshot,
    ) -> _RebuiltLongCandidate:
        preview = workspace.preview
        mapping_proof = workspace.template
        if preview is None or mapping_proof is None:
            raise AssertionError("PREVIEW_READY workspace lost its approved proof")
        mapping_result = MappingPreviewResult(
            state=MappingPreviewState.PREVIEW_READY,
            preview=preview,
            issues=(),
        )
        outcome = StoreScanMappingOutcome(
            status=StoreScanMappingStatus.PREVIEW_READY,
            scope=ResolvedMappingScope(
                project_key=request.project_key,
                supplier_scope=request.supplier_scope,
            ),
            receipt=workspace.receipt,
            scan=workspace.scan,
            mapping_result=mapping_result,
        )
        try:
            with self._database.session() as session:
                binding_catalog = self._master_repository.load_row_binding_catalog(
                    session,
                    project_key=request.project_key,
                    as_of=preview.source_inspection_date,
                )
        except (MasterConfigEffectivePeriodError, MasterConfigScopeError) as error:
            raise LongWorkflowConflictError(
                "LONG_BINDING_CATALOG_CONFLICT",
                "승인된 행 연결 근거를 하나로 확정할 수 없습니다.",
                "행 연결 확인 필요",
            ) from error
        except MasterConfigPayloadIntegrityError as error:
            raise LongWorkflowConflictError(
                "LONG_BINDING_CATALOG_INTEGRITY_ERROR",
                "저장된 행 연결 근거의 무결성을 확인할 수 없습니다.",
                "행 연결 무결성 오류",
            ) from error
        except (MasterConfigPersistenceError, SQLAlchemyError) as error:
            raise _unavailable("LONG_BINDING_CATALOG_UNAVAILABLE") from error
        try:
            candidate = build_long_candidate(outcome, binding_catalog)
            candidate_digest = _candidate_digest(candidate, mapping_proof)
        except LongCandidateInputError as error:
            raise LongWorkflowConflictError(
                "LONG_CANDIDATE_NOT_READY",
                "승인된 매핑 근거로 Long 후보를 만들 수 없습니다.",
                "Long 후보 준비 안 됨",
            ) from error
        except (TypeError, ValueError) as error:
            raise LongWorkflowConflictError(
                "LONG_CANDIDATE_INTEGRITY_ERROR",
                "Long 후보의 원본 근거를 안전하게 재구성할 수 없습니다.",
                "Long 후보 무결성 오류",
            ) from error
        return _RebuiltLongCandidate(
            response=LongWorkflowCandidate(
                candidate=candidate,
                candidate_digest=candidate_digest,
                mapping_proof=mapping_proof,
            ),
            outcome=outcome,
        )

    def _mapping_preview(self, request: LongCandidateRequest) -> MappingWorkspaceSnapshot:
        try:
            workspace = self._mapping_workspace.preview(
                MappingWorkspaceRequest(
                    project_key=request.project_key,
                    receipt_id=request.receipt_id,
                    content_sha256=request.content_sha256,
                    supplier_scope=request.supplier_scope,
                    cell_offset=0,
                    cell_limit=1,
                )
            )
        except MappingWorkspaceNotFoundError as error:
            raise LongWorkflowNotFoundError(
                "LONG_RECEIPT_NOT_FOUND",
                "해당 프로젝트에서 보존된 원본을 찾을 수 없습니다.",
                "원본 없음",
            ) from error
        except MappingWorkspaceValidationError as error:
            raise _validation("INVALID_LONG_SCOPE") from error
        except MappingWorkspaceSourceError as error:
            raise _unavailable(error.code) from error
        except MappingWorkspaceError as error:
            raise LongWorkflowConflictError(
                "LONG_MAPPING_REPLAY_FAILED",
                "승인된 매핑 근거를 다시 확인할 수 없습니다.",
                "매핑 재확인 실패",
            ) from error
        if workspace.state != MappingWorkspaceState.PREVIEW_READY:
            raise LongWorkflowConflictError(
                "APPROVED_MAPPING_REQUIRED",
                "이 원본에는 정확히 일치하는 승인 매핑이 필요합니다.",
                "승인 매핑 필요",
            )
        return workspace


def _candidate_digest(
    candidate: LongCandidateResult,
    mapping_proof: ApprovedTemplateProof,
) -> str:
    return canonical_json_sha256(
        {
            "candidate": serialize_long_candidate(candidate),
            "mapping_proof": {
                "history_id": mapping_proof.history_id,
                "revision_id": mapping_proof.revision_id,
                "template_id": mapping_proof.template_id,
                "schema_version": mapping_proof.schema_version,
                "revision": mapping_proof.revision,
                "status": mapping_proof.status,
                "payload_sha256": mapping_proof.payload_sha256,
                "effective_from": mapping_proof.effective_from.isoformat(),
                "effective_to": (
                    mapping_proof.effective_to.isoformat()
                    if mapping_proof.effective_to is not None
                    else None
                ),
                "approved_by": mapping_proof.approved_by,
                "approved_at": mapping_proof.approved_at.isoformat(),
                "history_row_version": mapping_proof.history_row_version,
                "revision_row_version": mapping_proof.revision_row_version,
            },
        }
    )


def _validate_candidate_request(request: LongCandidateRequest) -> None:
    for field_name in ("project_key", "receipt_id", "supplier_scope"):
        value = getattr(request, field_name)
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise _validation("INVALID_LONG_SCOPE")
    if not _SHA256_PATTERN.fullmatch(request.content_sha256):
        raise _validation("INVALID_CONTENT_SHA256")


def _validate_confirm_request(request: ConfirmLongCandidateRequest) -> None:
    _validate_candidate_request(request.candidate_request)
    if request.confirmed is not True:
        raise LongWorkflowValidationError(
            "EXPLICIT_LONG_CONFIRMATION_REQUIRED",
            "Long 대기 적재를 진행하려면 명시적으로 확인해 주세요.",
            "명시적 확인 필요",
        )
    if not _SHA256_PATTERN.fullmatch(request.candidate_digest):
        raise LongWorkflowValidationError(
            "INVALID_CANDIDATE_DIGEST",
            "Long 후보 식별자가 올바르지 않습니다.",
            "후보 식별자 오류",
        )


def _exact_version(value: str, field_name: str) -> str:
    if not value.strip() or value != value.strip() or len(value) > 64:
        raise ValueError(f"{field_name} must be an exact value of at most 64 characters")
    return value


def _validation(code: str) -> LongWorkflowValidationError:
    return LongWorkflowValidationError(
        code,
        "프로젝트, 원본 식별자와 업체 범위를 정확히 입력해 주세요.",
        "Long 요청 확인 필요",
    )


def _unavailable(code: str) -> LongWorkflowUnavailableError:
    return LongWorkflowUnavailableError(
        code,
        "Long 처리 근거를 안전하게 확인할 수 없습니다.",
        "Long 처리 불가",
    )
