"""Fail-closed transaction boundary for pending-only Long-format persistence."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import select

from app.application.long_candidate import build_long_candidate
from app.application.mapping_preview import build_mapping_preview
from app.application.store_scan_mapping import (
    StoreScanMappingOutcome,
    StoreScanMappingStatus,
)
from app.domain.long_format import (
    CanonicalRowBinding,
    CanonicalRowBindingCatalog,
    CanonicalRowBindingKey,
    CanonicalRowBindingSignature,
    LongCandidateResult,
    LongCandidateState,
)
from app.domain.mapping import MappingPreviewRequest, MappingTemplateStatus
from app.infrastructure.database import Database
from app.infrastructure.long_format import (
    LongClaimBasis,
    LongClaimResult,
    LongFormatRepository,
    LongJobStatus,
    LongMaterializationCounts,
    canonical_json_sha256,
    serialize_long_candidate,
)
from app.infrastructure.mapping_templates import (
    MappingTemplateRepository,
    MappingTemplateRevisionRow,
    PersistedMappingTemplate,
    PersistentMappingTemplateCatalog,
)


class LongPersistenceValidationError(ValueError):
    """The supplied route, candidate, or persisted mapping proof disagrees."""


class LongMaterializationFailedError(RuntimeError):
    """A claimed job failed atomically while creating pending Long rows."""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        super().__init__(f"Long-format materialization failed for ingestion job {job_id}")


@dataclass(frozen=True, slots=True)
class LongPersistenceRequest:
    outcome: StoreScanMappingOutcome
    candidate: LongCandidateResult
    loader_version: str
    scan_contract_version: str


@dataclass(frozen=True, slots=True)
class LongPersistenceResult:
    source_file_id: str
    ingestion_job_id: str
    status: LongJobStatus
    row_version: int
    reused_job_id: str | None
    blocking_job_id: str | None
    replayed: bool
    counts: LongMaterializationCounts


class LongPersistenceService:
    """Validate, claim, then materialize in separately committed transactions."""

    def __init__(
        self,
        database: Database,
        *,
        repository: LongFormatRepository | None = None,
        mapping_repository: MappingTemplateRepository | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._database = database
        self._repository = repository or LongFormatRepository()
        self._mapping_repository = mapping_repository or MappingTemplateRepository()
        self._clock = clock or _utc_now

    def persist(self, request: LongPersistenceRequest) -> LongPersistenceResult:
        _validate_version(request.loader_version, "loader_version")
        _validate_version(request.scan_contract_version, "scan_contract_version")
        record, payload_sha256 = self._validate_before_claim(request)
        candidate_snapshot = serialize_long_candidate(request.candidate)
        candidate_snapshot_sha256 = canonical_json_sha256(candidate_snapshot)
        binding_fingerprint = _binding_fingerprint(candidate_snapshot)
        receipt = request.outcome.receipt
        materialization_fingerprint = canonical_json_sha256(
            {
                "project_key": receipt.project_key,
                "content_sha256": receipt.content_sha256,
                "mapping_template_revision_id": record.revision_id,
                "mapping_payload_sha256": payload_sha256,
                "binding_fingerprint": binding_fingerprint,
                "loader_version": request.loader_version,
                "scan_contract_version": request.scan_contract_version,
            }
        )
        idempotency_key = canonical_json_sha256(
            {
                "project_key": receipt.project_key,
                "receipt_id": receipt.receipt_id,
                "mapping_template_revision_id": record.revision_id,
                "binding_fingerprint": binding_fingerprint,
                "loader_version": request.loader_version,
                "scan_contract_version": request.scan_contract_version,
            }
        )
        scan = request.outcome.scan
        if scan is None:  # protected by _validate_before_claim
            raise AssertionError("PREVIEW_READY outcome unexpectedly has no scan")
        basis = LongClaimBasis(
            receipt=receipt,
            scan=scan,
            scan_contract_version=request.scan_contract_version,
            mapping_template_revision_id=record.revision_id,
            mapping_payload_sha256=payload_sha256,
            binding_catalog_revision=request.candidate.provenance.binding_catalog_revision,
            binding_fingerprint=binding_fingerprint,
            loader_version=request.loader_version,
            idempotency_key=idempotency_key,
            materialization_fingerprint=materialization_fingerprint,
            issues=request.candidate.issues,
            candidate_snapshot=candidate_snapshot,
            candidate_snapshot_sha256=candidate_snapshot_sha256,
            held_without_materialization=(request.candidate.state == LongCandidateState.LOAD_HELD),
            claimed_at=self._occurred_at(),
        )
        with self._database.session() as session, session.begin():
            claim = self._repository.claim(session, basis)

        if claim.status != LongJobStatus.PROCESSING:
            return self._result(claim)

        try:
            with self._database.session() as session, session.begin():
                counts = self._repository.materialize(
                    session,
                    claim=claim,
                    candidate=request.candidate,
                )
                terminal_status = (
                    LongJobStatus.PARTIAL_HELD
                    if request.candidate.state == LongCandidateState.PARTIAL_HOLD
                    else LongJobStatus.COMPLETED_PENDING
                )
                completed = self._repository.mark_materialized(
                    session,
                    project_key=receipt.project_key,
                    job_id=claim.ingestion_job_id,
                    expected_row_version=claim.row_version,
                    status=terminal_status,
                    counts=counts,
                    finished_at=self._occurred_at(),
                )
        except Exception as error:
            self._record_failed_claim(claim, error)
            raise LongMaterializationFailedError(claim.ingestion_job_id) from error
        return self._result(completed)

    def _validate_before_claim(
        self,
        request: LongPersistenceRequest,
    ) -> tuple[PersistedMappingTemplate, str]:
        outcome = request.outcome
        if (
            outcome.status != StoreScanMappingStatus.PREVIEW_READY
            or outcome.scan is None
            or outcome.mapping_result is None
            or outcome.mapping_result.preview is None
        ):
            raise LongPersistenceValidationError(
                "Long persistence requires one complete PREVIEW_READY route outcome"
            )
        preview = outcome.mapping_result.preview
        provenance = request.candidate.provenance
        if provenance.receipt != outcome.receipt:
            raise LongPersistenceValidationError(
                "candidate receipt differs from the route outcome receipt"
            )
        if request.candidate.official_values_created or request.candidate.calculations_performed:
            raise LongPersistenceValidationError(
                "a Long persistence candidate cannot contain official calculations"
            )

        with self._database.session() as session:
            record = self._mapping_repository.get(
                session,
                project_key=outcome.scope.project_key,
                supplier_scope=outcome.scope.supplier_scope,
                template_id=provenance.template_id,
                revision=provenance.template_revision,
            )
            if record.template.status != MappingTemplateStatus.APPROVED:
                raise LongPersistenceValidationError(
                    "the exact persisted Mapping Template revision is not APPROVED"
                )
            payload_sha256 = session.scalar(
                select(MappingTemplateRevisionRow.payload_sha256).where(
                    MappingTemplateRevisionRow.id == record.revision_id
                )
            )
            if payload_sha256 is None:
                raise LongPersistenceValidationError(
                    "the exact persisted Mapping Template revision disappeared"
                )
            rebuilt_mapping = build_mapping_preview(
                outcome.scan,
                MappingPreviewRequest(
                    project_key=outcome.scope.project_key,
                    supplier_scope=outcome.scope.supplier_scope,
                ),
                PersistentMappingTemplateCatalog((record,)),
            )
        if rebuilt_mapping != outcome.mapping_result:
            raise LongPersistenceValidationError(
                "route Mapping Preview was not produced by the exact persisted revision"
            )
        if preview.project_key != outcome.receipt.project_key:
            raise LongPersistenceValidationError("preview and receipt projects differ")

        evidence_catalog = _SelectionEvidenceCatalog(request.candidate)
        rebuilt_candidate = build_long_candidate(outcome, evidence_catalog)
        if rebuilt_candidate != request.candidate:
            raise LongPersistenceValidationError(
                "Long candidate differs from its preserved binding-selection evidence"
            )
        return record, payload_sha256

    def _record_failed_claim(self, claim: LongClaimResult, error: Exception) -> None:
        error_code = type(error).__qualname__[:120]
        with self._database.session() as session, session.begin():
            self._repository.mark_failed(
                session,
                project_key=claim.project_key,
                job_id=claim.ingestion_job_id,
                expected_row_version=claim.row_version,
                finished_at=self._occurred_at(),
                error_code=error_code,
                error_summary="Pending Long-format materialization transaction failed.",
            )

    def _result(self, claim: LongClaimResult) -> LongPersistenceResult:
        with self._database.session() as session:
            job = self._repository.get_job(
                session,
                project_key=claim.project_key,
                job_id=claim.ingestion_job_id,
            )
            return LongPersistenceResult(
                source_file_id=job.source_file_id,
                ingestion_job_id=job.id,
                status=LongJobStatus(job.status),
                row_version=job.row_version,
                reused_job_id=job.reused_job_id,
                blocking_job_id=job.blocking_job_id,
                replayed=claim.replayed,
                counts=LongMaterializationCounts(
                    lot_count=job.lot_count,
                    result_count=job.result_count,
                    measurement_count=job.measurement_count,
                    held_result_count=job.held_result_count,
                ),
            )

    def _occurred_at(self) -> datetime:
        value = self._clock()
        if value.utcoffset() is None:
            raise ValueError("Long persistence clock must return an aware datetime")
        return value.astimezone(UTC)


class _SelectionEvidenceCatalog(CanonicalRowBindingCatalog):
    def __init__(self, candidate: LongCandidateResult) -> None:
        self._revision = candidate.provenance.binding_catalog_revision
        self._matches = {
            selection.requested_key: tuple(
                _binding_from_signature(signature) for signature in selection.matches
            )
            for selection in candidate.provenance.binding_selections
        }

    @property
    def catalog_revision(self) -> str:
        return self._revision

    def find(self, key: CanonicalRowBindingKey) -> Sequence[CanonicalRowBinding]:
        return self._matches.get(key, ())


def _binding_from_signature(signature: CanonicalRowBindingSignature) -> CanonicalRowBinding:
    return CanonicalRowBinding(
        key=signature.key,
        binding_revision=signature.binding_revision,
        status=signature.status,
        approved_by=signature.approved_by,
        approved_at=signature.approved_at,
        effective_from=signature.effective_from,
        effective_to=signature.effective_to,
        source_model_values=signature.source_model_values,
        canonical_model_key=signature.canonical_model_key,
        canonical_supplier_key=signature.canonical_supplier_key,
        canonical_model_part_key=signature.canonical_model_part_key,
        canonical_item_key=signature.canonical_item_key,
        sample_policy=signature.sample_policy,
        measurement_mode=signature.measurement_mode,
    )


def _binding_fingerprint(candidate_snapshot: dict[str, object]) -> str:
    provenance = candidate_snapshot.get("provenance")
    if not isinstance(provenance, dict):
        raise LongPersistenceValidationError("candidate provenance snapshot is malformed")
    selections = cast(dict[str, object], provenance).get("binding_selections")
    if not isinstance(selections, list):
        raise LongPersistenceValidationError("candidate binding snapshot is malformed")
    return canonical_json_sha256(selections)


def _validate_version(value: str, field_name: str) -> None:
    if not value.strip() or value != value.strip() or len(value) > 64:
        raise LongPersistenceValidationError(
            f"{field_name} must be an exact non-blank value of at most 64 characters"
        )


def _utc_now() -> datetime:
    return datetime.now(UTC)
