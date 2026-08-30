"""Bounded, read-only historical OQC evidence comparison.

This use case returns stored source and revision proof.  It deliberately does
not calculate trends, capability, thresholds, or any new quality judgment.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from hashlib import sha256
from typing import Any, Never, cast

from sqlalchemy import func, or_, select
from sqlalchemy.exc import SQLAlchemyError

from app.application.result_replacement import result_replacement_audit_states
from app.domain.result_replacement import REPLACEMENT_CHAIN_LIMIT
from app.infrastructure.audit import AuditLog
from app.infrastructure.data_review import (
    DataReviewPersistenceError,
    DataStatusTransitionRow,
    validate_data_status_transition_evidence,
)
from app.infrastructure.database import Database
from app.infrastructure.long_format import (
    LongIngestionJobRow,
    LongInspectionResultRow,
    LongMeasurementRow,
    LongPersistenceIntegrityError,
    LongSourceFileRow,
    LongSourceSheetRow,
    OqcLotRow,
    canonical_json_sha256,
    verify_applied_mapping_proof,
)
from app.infrastructure.mapping_templates import (
    MappingTemplateHistoryRow,
    MappingTemplateRevisionRow,
)
from app.infrastructure.master_config import MasterSpecHistoryRow, MasterSpecRevisionRow
from app.infrastructure.result_replacement import (
    ResultReplacementMeasurementRow,
    ResultReplacementTransitionRow,
)

HISTORY_MAX_RESULTS_PER_SIDE = 200
HISTORY_MAX_SAMPLES_PER_RESULT = 100
HISTORY_MAX_TOTAL_SAMPLES = 10_000
HISTORY_MAX_REPLACEMENT_LINK_PROJECTIONS = 10_000
HISTORY_MAX_REPLACEMENT_CHILD_ROWS = 10_000
HISTORY_MAX_REPLACEMENT_AUDIT_ROWS = 10_000
_MAX_TEXT = 2_000
_PROJECT_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_STATUSES = frozenset({"PENDING", "HELD", "VALID", "SUSPECT", "EXCLUDED", "REPLACED"})
_SOURCE_ROLES = (
    "item",
    "method",
    "instrument",
    "specification",
    "tolerance",
    "minimum",
    "maximum",
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
    "supplier_judgment",
)
_CELL_KEYS = {
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
}


@dataclass(frozen=True, slots=True)
class HistoricalDateRange:
    date_from: date
    date_to: date

    def __post_init__(self) -> None:
        if self.date_to < self.date_from:
            raise ValueError("historical date range is reversed")


@dataclass(frozen=True, slots=True)
class HistoricalFilters:
    canonical_model_key: str | None = None
    canonical_model_part_key: str | None = None
    canonical_item_key: str | None = None
    canonical_supplier_key: str | None = None
    mapping_revision_id: str | None = None

    def __post_init__(self) -> None:
        for value in (
            self.canonical_model_key,
            self.canonical_model_part_key,
            self.canonical_item_key,
            self.canonical_supplier_key,
            self.mapping_revision_id,
        ):
            if value is not None:
                _exact(value, "historical filter", 200)


@dataclass(frozen=True, slots=True)
class HistoricalComparisonRequest:
    project_key: str
    left: HistoricalDateRange
    right: HistoricalDateRange
    data_statuses: tuple[str, ...]
    filters: HistoricalFilters
    limit_per_side: int = 100

    def __post_init__(self) -> None:
        if _PROJECT_KEY.fullmatch(self.project_key) is None:
            raise ValueError("invalid project key")
        if not self.data_statuses or len(set(self.data_statuses)) != len(self.data_statuses):
            raise ValueError("data statuses must be a nonempty unique tuple")
        if tuple(sorted(self.data_statuses)) != self.data_statuses:
            raise ValueError("data statuses must use deterministic order")
        if not set(self.data_statuses).issubset(_STATUSES):
            raise ValueError("unsupported data status")
        if not 1 <= self.limit_per_side <= HISTORY_MAX_RESULTS_PER_SIDE:
            raise ValueError("limit_per_side is outside the server bound")


@dataclass(frozen=True, slots=True)
class HistoricalCellProof:
    role: str
    sheet_name: str
    coordinate: str
    raw_value: dict[str, object]
    cached_value: dict[str, object]
    formula_text: str | None
    number_format: str
    data_type: str
    display_value: str | None
    display_value_status: str
    value_kind: str
    evidence_sha256: str


@dataclass(frozen=True, slots=True)
class HistoricalSample:
    measurement_id: str
    sample_ordinal: int
    row_version: int
    data_status: str
    source_sheet_name: str
    source_cell: str
    raw_value_tag: str
    raw_value_text: str | None
    raw_numeric_value: str | None
    raw_qualitative_value: str | None
    formula_flag: bool
    evidence_sha256: str


@dataclass(frozen=True, slots=True)
class HistoricalMappingProof:
    revision_id: str
    template_id: str
    revision: int
    payload_sha256: str
    schema_version: str
    applied_effective_from: date
    applied_effective_to: date | None
    current_declared_effective_from: date
    current_declared_effective_to: date | None
    current_resolved_effective_to: date | None
    candidate_snapshot_sha256: str


@dataclass(frozen=True, slots=True)
class HistoricalDecisionProof:
    transition_id: str
    command_id: str
    evaluation_mode: str
    candidate_sha256: str
    decided_by: str
    decided_at: datetime
    reason: str
    from_status: str
    to_status: str
    before_result_row_version: int
    after_result_row_version: int
    intent_sha256: str
    decision_snapshot_sha256: str


@dataclass(frozen=True, slots=True)
class HistoricalMasterProof:
    history_id: str
    revision_id: str
    revision: int
    history_row_version: int
    revision_row_version: int
    payload_sha256: str
    declared_effective_from: date
    declared_effective_to: date | None
    resolved_effective_to: date | None


@dataclass(frozen=True, slots=True)
class HistoricalReplacementLink:
    replacement_id: str
    predecessor_result_id: str
    successor_result_id: str
    predecessor_original_data_status_transition_id: str
    successor_data_status_transition_id: str
    predecessor_before_status: str
    predecessor_after_status: str
    successor_before_status: str
    successor_after_status: str
    predecessor_before_result_row_version: int
    predecessor_after_result_row_version: int
    successor_before_result_row_version: int
    successor_after_result_row_version: int
    predecessor_measurement_count: int
    predecessor_measurement_set_sha256: str
    successor_measurement_count: int
    successor_measurement_set_sha256: str
    candidate_sha256: str
    intent_sha256: str
    decided_by: str
    decided_at: datetime
    reason: str


@dataclass(frozen=True, slots=True)
class HistoricalReplacementChainProof:
    head_result_id: str
    tail_result_id: str | None
    current_result_id: str
    current_position: int | None
    returned_link_count: int
    has_more: bool
    links_sha256: str
    links: tuple[HistoricalReplacementLink, ...]


@dataclass(frozen=True, slots=True)
class HistoricalResult:
    result_id: str
    lot_id: str
    source_file_id: str
    ingestion_job_id: str
    result_row_version: int
    data_status: str
    inspection_date: date
    lot_text: str | None
    canonical_model_key: str | None
    canonical_model_part_key: str | None
    canonical_item_key: str | None
    canonical_supplier_key: str | None
    receipt_id: str
    received_at: datetime
    original_filename: str
    content_sha256: str
    source_row_key: str
    source_sheet_name: str
    source_evidence_sha256: str
    source_fields: tuple[HistoricalCellProof, ...]
    supplier_judgment_text: str | None
    system_judgment: str | None
    system_judgment_status: str
    spec_evaluation_status: str
    candidate_snapshot_sha256: str
    mapping: HistoricalMappingProof
    binding_catalog_revision: str
    binding_fingerprint: str
    binding_revision: int | None
    binding_snapshot_sha256: str | None
    binding_proof: dict[str, object] | None
    applied_master: HistoricalMasterProof | None
    decision: HistoricalDecisionProof | None
    replacement_chain: HistoricalReplacementChainProof | None
    total_sample_count: int
    returned_sample_count: int
    samples_has_more: bool
    sample_set_sha256: str
    samples: tuple[HistoricalSample, ...]


@dataclass(frozen=True, slots=True)
class HistoricalSide:
    date_range: HistoricalDateRange
    total_result_count: int
    returned_result_count: int
    results_has_more: bool
    total_sample_count: int
    returned_results_sample_count: int
    mapping_revision_ids: tuple[str, ...]
    results: tuple[HistoricalResult, ...]


@dataclass(frozen=True, slots=True)
class HistoricalComparison:
    project_key: str
    data_statuses: tuple[str, ...]
    filters: HistoricalFilters
    left: HistoricalSide
    right: HistoricalSide
    official_values_created: bool = False
    calculations_performed: bool = False
    statistics_performed: bool = False
    ai_used: bool = False


class HistoricalComparisonError(RuntimeError):
    def __init__(self, code: str, message: str, status_label: str) -> None:
        self.code = code
        self.safe_message = message
        self.status_label = status_label
        super().__init__(code)


class HistoricalComparisonService:
    def __init__(self, database: Database) -> None:
        self._database = database

    def compare(self, request: HistoricalComparisonRequest) -> HistoricalComparison:
        try:
            with self._database.session() as session:
                replacement_cache = _ReplacementHistoryCache()
                left = self._side(session, request, request.left, replacement_cache)
                right = self._side(session, request, request.right, replacement_cache)
        except HistoricalComparisonError:
            raise
        except SQLAlchemyError as error:
            raise HistoricalComparisonError(
                "HISTORY_DATABASE_UNAVAILABLE",
                "과거 데이터 조회 저장소를 사용할 수 없습니다.",
                "서비스 확인 필요",
            ) from error
        except (ValueError, TypeError, KeyError) as error:
            raise HistoricalComparisonError(
                "HISTORY_EVIDENCE_UNAVAILABLE",
                "저장된 과거 원본 근거를 안전하게 조회할 수 없습니다.",
                "과거 근거 확인 필요",
            ) from error
        return HistoricalComparison(
            project_key=request.project_key,
            data_statuses=request.data_statuses,
            filters=request.filters,
            left=left,
            right=right,
        )

    def _side(
        self,
        session: Any,
        request: HistoricalComparisonRequest,
        period: HistoricalDateRange,
        replacement_cache: _ReplacementHistoryCache,
    ) -> HistoricalSide:
        conditions = [
            LongInspectionResultRow.project_key == request.project_key,
            OqcLotRow.inspection_date.is_not(None),
            OqcLotRow.inspection_date >= period.date_from,
            OqcLotRow.inspection_date <= period.date_to,
            LongInspectionResultRow.data_status.in_(request.data_statuses),
        ]
        filters = request.filters
        if filters.canonical_model_key is not None:
            conditions.append(OqcLotRow.canonical_model_key == filters.canonical_model_key)
        if filters.canonical_model_part_key is not None:
            conditions.append(
                LongInspectionResultRow.canonical_model_part_key == filters.canonical_model_part_key
            )
        if filters.canonical_item_key is not None:
            conditions.append(
                LongInspectionResultRow.canonical_item_key == filters.canonical_item_key
            )
        if filters.canonical_supplier_key is not None:
            conditions.append(OqcLotRow.canonical_supplier_key == filters.canonical_supplier_key)
        if filters.mapping_revision_id is not None:
            conditions.append(
                LongIngestionJobRow.mapping_template_revision_id == filters.mapping_revision_id
            )

        base = (
            select(
                LongInspectionResultRow,
                OqcLotRow.id.label("lot_id"),
                OqcLotRow.ingestion_job_id.label("lot_ingestion_job_id"),
                OqcLotRow.inspection_date.label("lot_inspection_date"),
                OqcLotRow.source_lot_text.label("lot_source_text"),
                OqcLotRow.canonical_model_key.label("lot_model_key"),
                OqcLotRow.canonical_supplier_key.label("lot_supplier_key"),
                LongSourceFileRow.id.label("source_file_id"),
                LongSourceFileRow.receipt_id.label("source_receipt_id"),
                LongSourceFileRow.received_at.label("source_received_at"),
                LongSourceFileRow.original_filename.label("source_original_filename"),
                LongSourceFileRow.content_sha256.label("source_content_sha256"),
                LongSourceSheetRow.sheet_name.label("result_sheet_name"),
                MappingTemplateRevisionRow.id.label("mapping_revision_id"),
                MappingTemplateRevisionRow.revision_number.label("mapping_revision_number"),
                MappingTemplateRevisionRow.schema_version.label("mapping_schema_version"),
                MappingTemplateRevisionRow.payload_sha256.label("mapping_payload_sha256"),
                MappingTemplateRevisionRow.declared_effective_from.label(
                    "mapping_declared_effective_from"
                ),
                MappingTemplateRevisionRow.declared_effective_to.label(
                    "mapping_declared_effective_to"
                ),
                MappingTemplateRevisionRow.resolved_effective_to.label(
                    "mapping_resolved_effective_to"
                ),
                MappingTemplateHistoryRow.template_id.label("mapping_template_id"),
                MappingTemplateHistoryRow.supplier_scope.label("mapping_supplier_scope"),
                LongIngestionJobRow.id.label("job_id"),
                LongIngestionJobRow.project_key.label("job_project_key"),
                LongIngestionJobRow.source_file_id.label("job_source_file_id"),
                LongIngestionJobRow.content_sha256.label("job_content_sha256"),
                LongIngestionJobRow.mapping_payload_sha256.label("job_mapping_payload_sha256"),
                LongIngestionJobRow.binding_catalog_revision.label("job_binding_catalog_revision"),
                LongIngestionJobRow.binding_fingerprint.label("job_binding_fingerprint"),
                LongIngestionJobRow.candidate_snapshot_sha256.label(
                    "job_candidate_snapshot_sha256"
                ),
                LongIngestionJobRow.applied_mapping_proof.label("applied_mapping_proof"),
                LongIngestionJobRow.applied_mapping_proof_sha256.label(
                    "applied_mapping_proof_sha256"
                ),
            )
            .join(
                OqcLotRow,
                (OqcLotRow.project_key == LongInspectionResultRow.project_key)
                & (OqcLotRow.id == LongInspectionResultRow.oqc_lot_id),
            )
            .join(
                LongSourceFileRow,
                (LongSourceFileRow.project_key == LongInspectionResultRow.project_key)
                & (LongSourceFileRow.id == LongInspectionResultRow.source_file_id),
            )
            .join(
                LongSourceSheetRow,
                (LongSourceSheetRow.project_key == LongInspectionResultRow.project_key)
                & (LongSourceSheetRow.id == LongInspectionResultRow.source_sheet_id),
            )
            .join(
                LongIngestionJobRow,
                (LongIngestionJobRow.project_key == LongInspectionResultRow.project_key)
                & (LongIngestionJobRow.id == OqcLotRow.ingestion_job_id),
            )
            .join(
                MappingTemplateRevisionRow,
                MappingTemplateRevisionRow.id == LongIngestionJobRow.mapping_template_revision_id,
            )
            .join(
                MappingTemplateHistoryRow,
                MappingTemplateHistoryRow.id == MappingTemplateRevisionRow.history_id,
            )
            .where(*conditions)
        )
        scope_mismatch = session.scalar(
            base.with_only_columns(LongInspectionResultRow.id)
            .where(MappingTemplateHistoryRow.project_key != LongIngestionJobRow.project_key)
            .limit(1)
        )
        if scope_mismatch is not None:
            raise ValueError("historical Mapping project scope changed")
        base = base.where(MappingTemplateHistoryRow.project_key == LongIngestionJobRow.project_key)
        total = cast(int, session.scalar(select(func.count()).select_from(base.subquery())) or 0)
        total_samples = cast(
            int,
            session.scalar(
                select(func.count(LongMeasurementRow.id))
                .join(
                    LongInspectionResultRow,
                    (LongInspectionResultRow.project_key == LongMeasurementRow.project_key)
                    & (LongInspectionResultRow.id == LongMeasurementRow.inspection_result_id),
                )
                .join(
                    OqcLotRow,
                    (OqcLotRow.project_key == LongInspectionResultRow.project_key)
                    & (OqcLotRow.id == LongInspectionResultRow.oqc_lot_id),
                )
                .join(
                    LongIngestionJobRow,
                    (LongIngestionJobRow.project_key == OqcLotRow.project_key)
                    & (LongIngestionJobRow.id == OqcLotRow.ingestion_job_id),
                )
                .where(*conditions)
            )
            or 0,
        )
        mapping_ids = tuple(
            session.scalars(
                base.with_only_columns(MappingTemplateRevisionRow.id)
                .order_by(None)
                .distinct()
                .order_by(MappingTemplateRevisionRow.id)
                .limit(HISTORY_MAX_RESULTS_PER_SIDE + 1)
            )
        )
        if len(mapping_ids) > HISTORY_MAX_RESULTS_PER_SIDE:
            raise HistoricalComparisonError(
                "HISTORY_REVISION_SET_LIMIT_EXCEEDED",
                "요청 범위의 Mapping revision 수가 안전한 조회 한도를 넘었습니다.",
                "조회 범위 축소 필요",
            )
        rows = session.execute(
            base.order_by(OqcLotRow.inspection_date, LongInspectionResultRow.id).limit(
                request.limit_per_side
            )
        ).all()
        result_ids = [cast(LongInspectionResultRow, row[0]).id for row in rows]
        returned_samples = 0
        if result_ids:
            returned_samples = cast(
                int,
                session.scalar(
                    select(func.count())
                    .select_from(LongMeasurementRow)
                    .where(
                        LongMeasurementRow.project_key == request.project_key,
                        LongMeasurementRow.inspection_result_id.in_(result_ids),
                    )
                )
                or 0,
            )
        if returned_samples > HISTORY_MAX_TOTAL_SAMPLES:
            raise HistoricalComparisonError(
                "HISTORY_SAMPLE_SCAN_LIMIT_EXCEEDED",
                "요청 범위의 표본 수가 안전한 조회 한도를 넘었습니다. 기간이나 항목을 줄여 주세요.",
                "조회 범위 축소 필요",
            )
        _prepare_replacement_history(
            replacement_cache,
            session,
            request.project_key,
            result_ids,
        )
        results = tuple(
            self._result(session, request.project_key, row, replacement_cache) for row in rows
        )
        return HistoricalSide(
            date_range=period,
            total_result_count=total,
            returned_result_count=len(results),
            results_has_more=total > len(results),
            total_sample_count=total_samples,
            returned_results_sample_count=sum(item.total_sample_count for item in results),
            mapping_revision_ids=mapping_ids,
            results=results,
        )

    def _result(
        self,
        session: Any,
        project_key: str,
        joined: Any,
        replacement_cache: _ReplacementHistoryCache,
    ) -> HistoricalResult:
        result = cast(LongInspectionResultRow, joined[0])
        inspection_date = joined.lot_inspection_date
        if not isinstance(inspection_date, date):
            raise ValueError("selected historical lot has no inspection date")
        if (
            joined.source_file_id != joined.job_source_file_id
            or joined.lot_ingestion_job_id != joined.job_id
            or joined.source_content_sha256 != joined.job_content_sha256
            or result.candidate_snapshot_sha256 != joined.job_candidate_snapshot_sha256
        ):
            raise ValueError("historical job/source provenance changed")
        _verify_json(result.source_evidence, result.source_evidence_sha256, "source evidence")
        if (result.binding_snapshot is None) != (result.binding_snapshot_sha256 is None):
            raise ValueError("binding proof shape changed")
        if result.binding_snapshot is not None:
            _verify_json(
                result.binding_snapshot,
                cast(str, result.binding_snapshot_sha256),
                "binding proof",
            )
        mapping_proof = _mapping_proof(joined)
        binding_proof = _binding_proof(
            result.binding_snapshot,
            project_key=project_key,
            supplier_scope=cast(str, joined.mapping_supplier_scope),
            template_id=mapping_proof.template_id,
            template_revision=mapping_proof.revision,
            row_key=result.source_row_key,
            binding_revision=result.binding_revision,
        )
        samples, total_samples, sample_digest = _samples(
            session, project_key, result.id, result.data_status
        )
        replacement_chain = _replacement_chain(
            project_key,
            result,
            replacement_cache,
        )
        decision = _decision(result)
        if decision is not None:
            transition = session.scalar(
                select(DataStatusTransitionRow).where(
                    DataStatusTransitionRow.project_key == project_key,
                    DataStatusTransitionRow.id == decision.transition_id,
                    DataStatusTransitionRow.inspection_result_id == result.id,
                )
            )
            if transition is None:
                raise ValueError("decision transition proof changed")
            decision = _verify_transition(result, transition, decision)
        master_proof = _master(result)
        if master_proof is not None:
            master_history = session.scalar(
                select(MasterSpecHistoryRow).where(
                    MasterSpecHistoryRow.project_key == project_key,
                    MasterSpecHistoryRow.id == master_proof.history_id,
                )
            )
            master_revision = session.scalar(
                select(MasterSpecRevisionRow).where(
                    MasterSpecRevisionRow.project_key == project_key,
                    MasterSpecRevisionRow.id == master_proof.revision_id,
                    MasterSpecRevisionRow.history_id == master_proof.history_id,
                )
            )
            if master_history is None or master_revision is None:
                raise ValueError("applied Master identity is missing")
            if (
                master_history.row_version < master_proof.history_row_version
                or master_revision.revision_number != master_proof.revision
                or master_revision.row_version < master_proof.revision_row_version
                or master_revision.payload_sha256 != master_proof.payload_sha256
                or master_revision.declared_effective_from != master_proof.declared_effective_from
                or master_revision.declared_effective_to != master_proof.declared_effective_to
            ):
                raise ValueError("applied Master identity changed")
        return HistoricalResult(
            result_id=result.id,
            lot_id=cast(str, joined.lot_id),
            source_file_id=cast(str, joined.source_file_id),
            ingestion_job_id=cast(str, joined.job_id),
            result_row_version=result.row_version,
            data_status=result.data_status,
            inspection_date=inspection_date,
            lot_text=_bounded_optional(cast(str | None, joined.lot_source_text)),
            canonical_model_key=cast(str | None, joined.lot_model_key),
            canonical_model_part_key=result.canonical_model_part_key,
            canonical_item_key=result.canonical_item_key,
            canonical_supplier_key=cast(str | None, joined.lot_supplier_key),
            receipt_id=cast(str, joined.source_receipt_id),
            received_at=cast(datetime, joined.source_received_at),
            original_filename=_bounded(cast(str, joined.source_original_filename), "filename"),
            content_sha256=_sha(cast(str, joined.source_content_sha256), "content sha"),
            source_row_key=_bounded(result.source_row_key, "source row key"),
            source_sheet_name=_bounded(cast(str, joined.result_sheet_name), "source sheet"),
            source_evidence_sha256=_sha(result.source_evidence_sha256, "source evidence sha"),
            source_fields=_source_fields(result.source_evidence),
            supplier_judgment_text=_bounded_optional(result.supplier_judgment_text),
            system_judgment=_bounded_optional(result.system_judgment),
            system_judgment_status=result.system_judgment_status,
            spec_evaluation_status=result.spec_evaluation_status,
            candidate_snapshot_sha256=_sha(
                result.candidate_snapshot_sha256, "candidate snapshot sha"
            ),
            mapping=mapping_proof,
            binding_catalog_revision=_bounded(
                cast(str, joined.job_binding_catalog_revision), "binding catalog revision"
            ),
            binding_fingerprint=_sha(
                cast(str, joined.job_binding_fingerprint), "binding fingerprint"
            ),
            binding_revision=result.binding_revision,
            binding_snapshot_sha256=result.binding_snapshot_sha256,
            binding_proof=binding_proof,
            applied_master=master_proof,
            decision=decision,
            replacement_chain=replacement_chain,
            total_sample_count=total_samples,
            returned_sample_count=len(samples),
            samples_has_more=total_samples > len(samples),
            sample_set_sha256=sample_digest,
            samples=samples,
        )


def _samples(
    session: Any, project_key: str, result_id: str, result_status: str
) -> tuple[tuple[HistoricalSample, ...], int, str]:
    statement = (
        select(LongMeasurementRow, LongSourceSheetRow.sheet_name)
        .join(
            LongSourceSheetRow,
            (LongSourceSheetRow.project_key == LongMeasurementRow.project_key)
            & (LongSourceSheetRow.id == LongMeasurementRow.source_sheet_id)
            & (LongSourceSheetRow.source_file_id == LongMeasurementRow.source_file_id),
        )
        .where(
            LongMeasurementRow.project_key == project_key,
            LongMeasurementRow.inspection_result_id == result_id,
        )
        .order_by(LongMeasurementRow.sample_ordinal, LongMeasurementRow.id)
    )
    digest = sha256(b"history-sample-set-v1\n")
    returned: list[HistoricalSample] = []
    total = 0
    prior_ordinal = 0
    for measurement, sheet_name in session.execute(statement).yield_per(500):
        measurement = cast(LongMeasurementRow, measurement)
        if measurement.data_status != result_status or measurement.sample_ordinal <= prior_ordinal:
            raise ValueError("sample status/order proof changed")
        prior_ordinal = measurement.sample_ordinal
        _verify_json(measurement.evidence, measurement.evidence_sha256, "sample evidence")
        _verify_measurement_projection(measurement, cast(str, sheet_name))
        payload = {
            "id": measurement.id,
            "sample_ordinal": measurement.sample_ordinal,
            "row_version": measurement.row_version,
            "data_status": measurement.data_status,
            "source_sheet_name": sheet_name,
            "source_cell": measurement.source_cell,
            "raw_value_tag": measurement.raw_value_tag,
            "raw_value_text": measurement.raw_value_text,
            "raw_numeric_value": measurement.raw_numeric_value,
            "raw_qualitative_value": measurement.raw_qualitative_value,
            "formula_flag": measurement.formula_flag,
            "evidence_sha256": measurement.evidence_sha256,
        }
        digest.update(_canonical_bytes(payload))
        digest.update(b"\n")
        total += 1
        if len(returned) < HISTORY_MAX_SAMPLES_PER_RESULT:
            returned.append(
                HistoricalSample(
                    measurement_id=measurement.id,
                    sample_ordinal=measurement.sample_ordinal,
                    row_version=measurement.row_version,
                    data_status=measurement.data_status,
                    source_sheet_name=_bounded(cast(str, sheet_name), "sample sheet"),
                    source_cell=_bounded(measurement.source_cell, "sample cell"),
                    raw_value_tag=_bounded(measurement.raw_value_tag, "raw value tag"),
                    raw_value_text=_bounded_optional(measurement.raw_value_text),
                    raw_numeric_value=_bounded_optional(measurement.raw_numeric_value),
                    raw_qualitative_value=_bounded_optional(measurement.raw_qualitative_value),
                    formula_flag=measurement.formula_flag,
                    evidence_sha256=_sha(measurement.evidence_sha256, "sample evidence sha"),
                )
            )
    return tuple(returned), total, digest.hexdigest()


@dataclass(slots=True)
class _ReplacementResultState:
    result_id: str
    source_file_id: str
    lot_id: str
    data_status: str
    row_version: int
    current_replacement_transition_id: str | None


@dataclass(slots=True)
class _ReplacementHistoryCache:
    incoming: dict[str, ResultReplacementTransitionRow | None] = field(default_factory=dict)
    outgoing: dict[str, ResultReplacementTransitionRow | None] = field(default_factory=dict)
    links: dict[str, HistoricalReplacementLink] = field(default_factory=dict)
    transitions: dict[str, ResultReplacementTransitionRow] = field(default_factory=dict)
    results: dict[str, _ReplacementResultState] = field(default_factory=dict)
    decisions: dict[str, DataStatusTransitionRow] = field(default_factory=dict)
    children: dict[
        str,
        tuple[tuple[ResultReplacementMeasurementRow, LongMeasurementRow], ...],
    ] = field(default_factory=dict)
    audits: dict[str, tuple[AuditLog, ...]] = field(default_factory=dict)
    decision_audits: dict[str, tuple[AuditLog, ...]] = field(default_factory=dict)
    expanded_result_ids: set[str] = field(default_factory=set)
    returned_link_projections: int = 0


def _prepare_replacement_history(
    cache: _ReplacementHistoryCache,
    session: Any,
    project_key: str,
    seed_result_ids: list[str],
) -> None:
    frontier = set(seed_result_ids) - cache.expanded_result_ids
    new_transitions: dict[str, ResultReplacementTransitionRow] = {}
    for _depth in range(REPLACEMENT_CHAIN_LIMIT + 2):
        if not frontier:
            break
        found: list[ResultReplacementTransitionRow] = []
        for chunk in _chunks(tuple(sorted(frontier))):
            found.extend(
                cast(
                    list[ResultReplacementTransitionRow],
                    list(
                        session.scalars(
                            select(ResultReplacementTransitionRow).where(
                                ResultReplacementTransitionRow.project_key == project_key,
                                or_(
                                    ResultReplacementTransitionRow.predecessor_result_id.in_(chunk),
                                    ResultReplacementTransitionRow.successor_result_id.in_(chunk),
                                ),
                            )
                        ).all()
                    ),
                )
            )
        for result_id in frontier:
            cache.incoming.setdefault(result_id, None)
            cache.outgoing.setdefault(result_id, None)
        next_frontier: set[str] = set()
        for row in found:
            is_new_transition = row.id not in cache.transitions
            previous_outgoing = cache.outgoing.get(row.predecessor_result_id)
            previous_incoming = cache.incoming.get(row.successor_result_id)
            if (previous_outgoing is not None and previous_outgoing.id != row.id) or (
                previous_incoming is not None and previous_incoming.id != row.id
            ):
                raise ValueError("replacement chain branches or merges")
            cache.outgoing[row.predecessor_result_id] = row
            cache.incoming[row.successor_result_id] = row
            cache.transitions[row.id] = row
            if is_new_transition:
                new_transitions[row.id] = row
            next_frontier.add(row.predecessor_result_id)
            next_frontier.add(row.successor_result_id)
        if len(cache.transitions) > HISTORY_MAX_REPLACEMENT_LINK_PROJECTIONS:
            _raise_replacement_history_limit()
        cache.expanded_result_ids.update(frontier)
        frontier = next_frontier - cache.expanded_result_ids
    if frontier:
        _raise_replacement_history_limit()
    _preload_replacement_evidence(
        cache,
        session,
        project_key,
        tuple(sorted(new_transitions)),
    )


def _preload_replacement_evidence(
    cache: _ReplacementHistoryCache,
    session: Any,
    project_key: str,
    transition_ids: tuple[str, ...],
) -> None:
    if not transition_ids:
        return
    result_ids = tuple(
        sorted(
            {
                result_id
                for transition_id in transition_ids
                for result_id in (
                    cache.transitions[transition_id].predecessor_result_id,
                    cache.transitions[transition_id].successor_result_id,
                )
                if result_id not in cache.results
            }
        )
    )
    for chunk in _chunks(result_ids):
        for values in session.execute(
            select(
                LongInspectionResultRow.id,
                LongInspectionResultRow.source_file_id,
                LongInspectionResultRow.oqc_lot_id,
                LongInspectionResultRow.data_status,
                LongInspectionResultRow.row_version,
                LongInspectionResultRow.current_replacement_transition_id,
            ).where(
                LongInspectionResultRow.project_key == project_key,
                LongInspectionResultRow.id.in_(chunk),
            )
        ):
            state = _ReplacementResultState(
                result_id=cast(str, values.id),
                source_file_id=cast(str, values.source_file_id),
                lot_id=cast(str, values.oqc_lot_id),
                data_status=cast(str, values.data_status),
                row_version=cast(int, values.row_version),
                current_replacement_transition_id=cast(
                    str | None,
                    values.current_replacement_transition_id,
                ),
            )
            cache.results[state.result_id] = state

    decision_ids = tuple(
        sorted(
            {
                decision_id
                for transition_id in transition_ids
                for decision_id in (
                    cache.transitions[transition_id].predecessor_original_data_status_transition_id,
                    cache.transitions[transition_id].successor_data_status_transition_id,
                )
                if decision_id not in cache.decisions
            }
        )
    )
    for chunk in _chunks(decision_ids):
        for row in session.scalars(
            select(DataStatusTransitionRow).where(
                DataStatusTransitionRow.project_key == project_key,
                DataStatusTransitionRow.id.in_(chunk),
            )
        ):
            cache.decisions[row.id] = row

    new_child_count = 0
    for chunk in _chunks(transition_ids):
        new_child_count += cast(
            int,
            session.scalar(
                select(func.count())
                .select_from(ResultReplacementMeasurementRow)
                .where(
                    ResultReplacementMeasurementRow.project_key == project_key,
                    ResultReplacementMeasurementRow.transition_id.in_(chunk),
                )
            )
            or 0,
        )
    if sum(len(values) for values in cache.children.values()) + new_child_count > (
        HISTORY_MAX_REPLACEMENT_CHILD_ROWS
    ):
        _raise_replacement_history_limit()
    child_groups: dict[
        str,
        list[tuple[ResultReplacementMeasurementRow, LongMeasurementRow]],
    ] = {transition_id: [] for transition_id in transition_ids}
    for chunk in _chunks(transition_ids):
        child_query = (
            select(ResultReplacementMeasurementRow, LongMeasurementRow)
            .join(
                LongMeasurementRow,
                (LongMeasurementRow.project_key == ResultReplacementMeasurementRow.project_key)
                & (LongMeasurementRow.id == ResultReplacementMeasurementRow.measurement_id)
                & (
                    LongMeasurementRow.inspection_result_id
                    == ResultReplacementMeasurementRow.inspection_result_id
                )
                & (
                    LongMeasurementRow.source_file_id
                    == ResultReplacementMeasurementRow.source_file_id
                ),
            )
            .where(
                ResultReplacementMeasurementRow.project_key == project_key,
                ResultReplacementMeasurementRow.transition_id.in_(chunk),
            )
            .order_by(
                ResultReplacementMeasurementRow.transition_id,
                ResultReplacementMeasurementRow.side,
                ResultReplacementMeasurementRow.sample_ordinal,
                ResultReplacementMeasurementRow.measurement_id,
            )
        )
        for child, measurement in session.execute(child_query):
            typed_child = cast(ResultReplacementMeasurementRow, child)
            child_groups[typed_child.transition_id].append(
                (typed_child, cast(LongMeasurementRow, measurement))
            )
    for transition_id, values in child_groups.items():
        cache.children[transition_id] = tuple(values)

    pair_audit_targets = tuple(
        f"{project_key}:{transition_id}"
        for transition_id in transition_ids
        if transition_id not in cache.audits
    )
    decision_result_ids = tuple(
        sorted(
            {
                cache.decisions[decision_id].inspection_result_id
                for transition_id in transition_ids
                for decision_id in (
                    cache.transitions[transition_id].predecessor_original_data_status_transition_id,
                    cache.transitions[transition_id].successor_data_status_transition_id,
                )
                if cache.decisions[decision_id].inspection_result_id not in cache.decision_audits
            }
        )
    )
    decision_audit_targets = tuple(
        f"{project_key}:{result_id}" for result_id in decision_result_ids
    )
    new_audit_count = _count_audits(
        session,
        action="RESULT_REPLACED",
        target_type="RESULT_REPLACEMENT",
        targets=pair_audit_targets,
    ) + _count_audits(
        session,
        action="DATA_STATUS_DECIDED",
        target_type="INSPECTION_RESULT",
        targets=decision_audit_targets,
    )
    existing_audit_count = sum(len(values) for values in cache.audits.values()) + sum(
        len(values) for values in cache.decision_audits.values()
    )
    if existing_audit_count + new_audit_count > HISTORY_MAX_REPLACEMENT_AUDIT_ROWS:
        _raise_replacement_history_limit()

    audit_groups: dict[str, list[AuditLog]] = {value: [] for value in pair_audit_targets}
    for chunk in _chunks(pair_audit_targets):
        for audit in session.scalars(
            select(AuditLog).where(
                AuditLog.action == "RESULT_REPLACED",
                AuditLog.target_type == "RESULT_REPLACEMENT",
                AuditLog.target_id.in_(chunk),
            )
        ):
            if audit.target_id in audit_groups:
                audit_groups[cast(str, audit.target_id)].append(audit)
    for transition_id in transition_ids:
        if transition_id not in cache.audits:
            cache.audits[transition_id] = tuple(audit_groups[f"{project_key}:{transition_id}"])
    decision_audit_groups: dict[str, list[AuditLog]] = {
        value: [] for value in decision_audit_targets
    }
    for chunk in _chunks(decision_audit_targets):
        for audit in session.scalars(
            select(AuditLog).where(
                AuditLog.action == "DATA_STATUS_DECIDED",
                AuditLog.target_type == "INSPECTION_RESULT",
                AuditLog.target_id.in_(chunk),
            )
        ):
            if audit.target_id in decision_audit_groups:
                decision_audit_groups[cast(str, audit.target_id)].append(audit)
    for result_id in decision_result_ids:
        cache.decision_audits[result_id] = tuple(
            decision_audit_groups[f"{project_key}:{result_id}"]
        )


def _count_audits(
    session: Any,
    *,
    action: str,
    target_type: str,
    targets: tuple[str, ...],
) -> int:
    total = 0
    for chunk in _chunks(targets):
        total += cast(
            int,
            session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.action == action,
                    AuditLog.target_type == target_type,
                    AuditLog.target_id.in_(chunk),
                )
            )
            or 0,
        )
    return total


def _chunks(values: tuple[str, ...], size: int = 400) -> tuple[tuple[str, ...], ...]:
    return tuple(values[index : index + size] for index in range(0, len(values), size))


def _raise_replacement_history_limit() -> Never:
    raise HistoricalComparisonError(
        "HISTORY_REPLACEMENT_EVIDENCE_LIMIT_EXCEEDED",
        "수정본 연결 근거가 안전한 조회 한도를 넘었습니다. 기간이나 항목을 줄여 주세요.",
        "조회 범위 축소 필요",
    )


def _replacement_chain(
    project_key: str,
    current_result: LongInspectionResultRow,
    cache: _ReplacementHistoryCache,
) -> HistoricalReplacementChainProof | None:
    incoming = _cached_replacement_row(
        current_result.id,
        incoming=True,
        cache=cache,
    )
    outgoing = _cached_replacement_row(
        current_result.id,
        incoming=False,
        cache=cache,
    )
    if incoming is None and outgoing is None:
        if (
            current_result.data_status == "REPLACED"
            or current_result.current_replacement_transition_id is not None
        ):
            raise ValueError("replacement projection lost its chain")
        return None

    cursor = current_result.id
    seen_backwards = {cursor}
    backward_count = 0
    backward_truncated = False
    while True:
        row = _cached_replacement_row(
            cursor,
            incoming=True,
            cache=cache,
        )
        if row is None:
            break
        if row.successor_result_id != cursor or row.predecessor_result_id in seen_backwards:
            raise ValueError("replacement chain is cyclic")
        cursor = row.predecessor_result_id
        seen_backwards.add(cursor)
        backward_count += 1
        if backward_count > REPLACEMENT_CHAIN_LIMIT:
            backward_truncated = True
            break

    head_result_id = cursor
    links: list[HistoricalReplacementLink] = []
    result_order = [head_result_id]
    seen_forwards = {head_result_id}
    has_more = backward_truncated
    cursor = head_result_id
    for index in range(REPLACEMENT_CHAIN_LIMIT + 1):
        row = _cached_replacement_row(
            cursor,
            incoming=False,
            cache=cache,
        )
        if row is None:
            break
        if row.predecessor_result_id != cursor or row.successor_result_id in seen_forwards:
            raise ValueError("replacement chain is cyclic")
        if index == REPLACEMENT_CHAIN_LIMIT:
            has_more = True
            break
        link = cache.links.get(row.id)
        if link is None:
            link = _replacement_link(project_key, row, cache)
            cache.links[row.id] = link
        links.append(link)
        cursor = row.successor_result_id
        seen_forwards.add(cursor)
        result_order.append(cursor)

    if current_result.id not in result_order and not has_more:
        raise ValueError("replacement chain is disconnected")
    current_position = (
        result_order.index(current_result.id)
        if current_result.id in result_order and not has_more
        else None
    )
    cache.returned_link_projections += len(links)
    if cache.returned_link_projections > HISTORY_MAX_REPLACEMENT_LINK_PROJECTIONS:
        _raise_replacement_history_limit()
    link_payloads = [_replacement_link_payload(value) for value in links]
    return HistoricalReplacementChainProof(
        head_result_id=head_result_id,
        tail_result_id=(result_order[-1] if not has_more else None),
        current_result_id=current_result.id,
        current_position=current_position,
        returned_link_count=len(links),
        has_more=has_more,
        links_sha256=canonical_json_sha256(link_payloads),
        links=tuple(links),
    )


def _cached_replacement_row(
    result_id: str,
    *,
    incoming: bool,
    cache: _ReplacementHistoryCache,
) -> ResultReplacementTransitionRow | None:
    values = cache.incoming if incoming else cache.outgoing
    if result_id not in values:
        raise ValueError("replacement chain resolver is incomplete")
    return values[result_id]


def _replacement_link(
    project_key: str,
    row: ResultReplacementTransitionRow,
    cache: _ReplacementHistoryCache,
) -> HistoricalReplacementLink:
    if (
        row.project_key != project_key
        or row.predecessor_result_id == row.successor_result_id
        or row.predecessor_before_status not in {"VALID", "SUSPECT"}
        or row.predecessor_after_status != "REPLACED"
        or row.successor_before_status != "PENDING"
        or row.successor_after_status != "VALID"
        or row.predecessor_after_result_row_version != row.predecessor_before_result_row_version + 1
        or row.successor_after_result_row_version != row.successor_before_result_row_version + 1
    ):
        raise ValueError("replacement transition shape changed")
    _verify_json(row.candidate_snapshot, row.candidate_sha256, "replacement candidate")
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
        or row.candidate_snapshot.get("project_key") != project_key
    ):
        raise ValueError("replacement candidate scope changed")
    predecessor_snapshot = row.candidate_snapshot.get("predecessor")
    successor_snapshot = row.candidate_snapshot.get("successor")
    if not isinstance(predecessor_snapshot, dict) or not isinstance(successor_snapshot, dict):
        raise ValueError("replacement candidate result proof changed")
    if (
        predecessor_snapshot.get("result_id") != row.predecessor_result_id
        or predecessor_snapshot.get("source_file_id") != row.predecessor_source_file_id
        or predecessor_snapshot.get("lot_id") != row.predecessor_lot_id
        or predecessor_snapshot.get("data_status") != row.predecessor_before_status
        or predecessor_snapshot.get("row_version") != row.predecessor_before_result_row_version
        or predecessor_snapshot.get("original_data_status_transition_id")
        != row.predecessor_original_data_status_transition_id
        or predecessor_snapshot.get("measurement_count") != row.predecessor_measurement_count
        or predecessor_snapshot.get("measurement_set_sha256")
        != row.predecessor_measurement_set_sha256
        or successor_snapshot.get("result_id") != row.successor_result_id
        or successor_snapshot.get("source_file_id") != row.successor_source_file_id
        or successor_snapshot.get("lot_id") != row.successor_lot_id
        or successor_snapshot.get("data_status") != row.successor_before_status
        or successor_snapshot.get("row_version") != row.successor_before_result_row_version
        or successor_snapshot.get("measurement_count") != row.successor_measurement_count
        or successor_snapshot.get("measurement_set_sha256") != row.successor_measurement_set_sha256
    ):
        raise ValueError("replacement candidate projection changed")

    predecessor = cache.results.get(row.predecessor_result_id)
    successor = cache.results.get(row.successor_result_id)
    if (
        predecessor is None
        or successor is None
        or predecessor.source_file_id != row.predecessor_source_file_id
        or predecessor.lot_id != row.predecessor_lot_id
        or successor.source_file_id != row.successor_source_file_id
        or successor.lot_id != row.successor_lot_id
        or predecessor.data_status != "REPLACED"
        or predecessor.current_replacement_transition_id != row.id
        or predecessor.row_version != row.predecessor_after_result_row_version
    ):
        raise ValueError("replacement predecessor projection changed")
    if successor.current_replacement_transition_id is None:
        successor_projection_is_exact = (
            successor.data_status == "VALID"
            and successor.row_version == row.successor_after_result_row_version
        )
    else:
        successor_outgoing = cache.outgoing.get(successor.result_id)
        successor_projection_is_exact = (
            successor.data_status == "REPLACED"
            and successor.row_version == row.successor_after_result_row_version + 1
            and successor_outgoing is not None
            and successor_outgoing.id == successor.current_replacement_transition_id
        )
    if not successor_projection_is_exact:
        raise ValueError("replacement successor projection changed")

    original_transition = cache.decisions.get(row.predecessor_original_data_status_transition_id)
    successor_transition = cache.decisions.get(row.successor_data_status_transition_id)
    if original_transition is None or successor_transition is None:
        raise ValueError("replacement decision transition is missing")
    _verify_json(
        original_transition.candidate_snapshot,
        original_transition.candidate_sha256,
        "predecessor decision candidate",
    )
    _verify_json(
        successor_transition.candidate_snapshot,
        successor_transition.candidate_sha256,
        "successor decision candidate",
    )
    _verify_json(
        successor_transition.decision_snapshot,
        successor_transition.decision_snapshot_sha256,
        "successor decision snapshot",
    )
    if (
        original_transition.project_key != project_key
        or original_transition.inspection_result_id != row.predecessor_result_id
        or original_transition.source_file_id != row.predecessor_source_file_id
        or successor_transition.project_key != project_key
        or successor_transition.inspection_result_id != row.successor_result_id
        or successor_transition.source_file_id != row.successor_source_file_id
        or original_transition.to_status != row.predecessor_before_status
        or original_transition.after_result_row_version != row.predecessor_before_result_row_version
        or predecessor_snapshot.get("original_decision_candidate_sha256")
        != original_transition.candidate_sha256
        or successor_transition.from_status != row.successor_before_status
        or successor_transition.to_status != row.successor_after_status
        or successor_transition.before_result_row_version != row.successor_before_result_row_version
        or successor_transition.after_result_row_version != row.successor_after_result_row_version
        or successor_snapshot.get("data_review_candidate_sha256")
        != successor_transition.candidate_sha256
        or successor_transition.decided_by != row.decided_by
        or successor_transition.decided_at != row.decided_at
        or successor_transition.reason != row.reason
    ):
        raise ValueError("replacement decision projection changed")
    predecessor_incoming = cache.incoming.get(row.predecessor_result_id)
    predecessor_requirement_id = (
        "ING-041"
        if predecessor_incoming is not None
        and predecessor_incoming.successor_data_status_transition_id == original_transition.id
        else None
    )
    try:
        validate_data_status_transition_evidence(
            original_transition,
            cache.decision_audits.get(original_transition.inspection_result_id, ()),
            expected_requirement_id=predecessor_requirement_id,
        )
        validate_data_status_transition_evidence(
            successor_transition,
            cache.decision_audits.get(successor_transition.inspection_result_id, ()),
            expected_requirement_id="ING-041",
        )
    except DataReviewPersistenceError as error:
        raise ValueError("replacement data-status Audit proof changed") from error

    child_rows = cache.children.get(row.id)
    if child_rows is None:
        raise ValueError("replacement measurement proof was not preloaded")
    original_samples = _candidate_sample_map(original_transition.candidate_snapshot)
    successor_samples = _candidate_sample_map(successor_transition.candidate_snapshot)
    proof_payloads: dict[str, list[dict[str, object]]] = {
        "PREDECESSOR": [],
        "SUCCESSOR": [],
    }
    for child, measurement in child_rows:
        _verify_json(measurement.evidence, measurement.evidence_sha256, "replacement sample")
        candidate_sample = (
            original_samples.get(child.measurement_id)
            if child.side == "PREDECESSOR"
            else successor_samples.get(child.measurement_id)
        )
        expected_result_id = (
            row.predecessor_result_id if child.side == "PREDECESSOR" else row.successor_result_id
        )
        expected_source_file_id = (
            row.predecessor_source_file_id
            if child.side == "PREDECESSOR"
            else row.successor_source_file_id
        )
        expected_before_status = (
            row.predecessor_before_status
            if child.side == "PREDECESSOR"
            else row.successor_before_status
        )
        expected_after_status = (
            row.predecessor_after_status
            if child.side == "PREDECESSOR"
            else row.successor_after_status
        )
        if child.side == "PREDECESSOR":
            current_measurement_is_exact = (
                measurement.data_status == "REPLACED"
                and measurement.row_version == child.after_row_version
                and measurement.replacement_transition_id == row.id
            )
            candidate_version_is_exact = (
                isinstance(candidate_sample, dict)
                and candidate_sample.get("row_version") == child.before_row_version - 1
            )
        else:
            if successor.current_replacement_transition_id is None:
                current_measurement_is_exact = (
                    measurement.data_status == "VALID"
                    and measurement.row_version == child.after_row_version
                    and measurement.replacement_transition_id is None
                )
            else:
                current_measurement_is_exact = (
                    measurement.data_status == "REPLACED"
                    and measurement.row_version == child.after_row_version + 1
                    and measurement.replacement_transition_id
                    == successor.current_replacement_transition_id
                )
            candidate_version_is_exact = (
                isinstance(candidate_sample, dict)
                and candidate_sample.get("row_version") == child.before_row_version
            )
        if (
            child.side not in proof_payloads
            or child.project_key != project_key
            or child.transition_id != row.id
            or child.inspection_result_id != expected_result_id
            or child.source_file_id != expected_source_file_id
            or child.before_status != expected_before_status
            or child.after_status != expected_after_status
            or child.evidence_sha256 != measurement.evidence_sha256
            or child.sample_ordinal != measurement.sample_ordinal
            or child.after_row_version != child.before_row_version + 1
            or not current_measurement_is_exact
            or not candidate_version_is_exact
            or not isinstance(candidate_sample, dict)
            or candidate_sample.get("measurement_id") != child.measurement_id
            or candidate_sample.get("sample_ordinal") != child.sample_ordinal
            or candidate_sample.get("source_cell") != measurement.source_cell
            or candidate_sample.get("evidence_sha256") != child.evidence_sha256
        ):
            raise ValueError("replacement measurement projection changed")
        proof_payloads[child.side].append(
            {
                "measurement_id": child.measurement_id,
                "sample_ordinal": child.sample_ordinal,
                "source_cell": measurement.source_cell,
                "data_status": child.before_status,
                "row_version": child.before_row_version,
                "evidence_sha256": child.evidence_sha256,
            }
        )
    if (
        len(proof_payloads["PREDECESSOR"]) != row.predecessor_measurement_count
        or len(proof_payloads["SUCCESSOR"]) != row.successor_measurement_count
        or canonical_json_sha256(proof_payloads["PREDECESSOR"])
        != row.predecessor_measurement_set_sha256
        or canonical_json_sha256(proof_payloads["SUCCESSOR"])
        != row.successor_measurement_set_sha256
    ):
        raise ValueError("replacement measurement-set proof changed")
    _verify_replacement_audit(cache, row)

    return HistoricalReplacementLink(
        replacement_id=row.id,
        predecessor_result_id=row.predecessor_result_id,
        successor_result_id=row.successor_result_id,
        predecessor_original_data_status_transition_id=(
            row.predecessor_original_data_status_transition_id
        ),
        successor_data_status_transition_id=row.successor_data_status_transition_id,
        predecessor_before_status=row.predecessor_before_status,
        predecessor_after_status=row.predecessor_after_status,
        successor_before_status=row.successor_before_status,
        successor_after_status=row.successor_after_status,
        predecessor_before_result_row_version=row.predecessor_before_result_row_version,
        predecessor_after_result_row_version=row.predecessor_after_result_row_version,
        successor_before_result_row_version=row.successor_before_result_row_version,
        successor_after_result_row_version=row.successor_after_result_row_version,
        predecessor_measurement_count=row.predecessor_measurement_count,
        predecessor_measurement_set_sha256=_sha(
            row.predecessor_measurement_set_sha256,
            "predecessor measurement set sha",
        ),
        successor_measurement_count=row.successor_measurement_count,
        successor_measurement_set_sha256=_sha(
            row.successor_measurement_set_sha256,
            "successor measurement set sha",
        ),
        candidate_sha256=_sha(row.candidate_sha256, "replacement candidate sha"),
        intent_sha256=_sha(row.intent_sha256, "replacement intent sha"),
        decided_by=_bounded(row.decided_by, "replacement actor"),
        decided_at=row.decided_at,
        reason=_bounded(row.reason, "replacement reason"),
    )


def _candidate_sample_map(value: dict[str, object]) -> dict[str, dict[str, object]]:
    samples = value.get("samples")
    if not isinstance(samples, list):
        raise ValueError("replacement decision samples are missing")
    result: dict[str, dict[str, object]] = {}
    for sample in samples:
        if not isinstance(sample, dict) or not isinstance(sample.get("measurement_id"), str):
            raise ValueError("replacement decision sample shape changed")
        measurement_id = cast(str, sample["measurement_id"])
        if measurement_id in result:
            raise ValueError("replacement decision sample identity is duplicated")
        result[measurement_id] = sample
    return result


def _verify_replacement_audit(
    cache: _ReplacementHistoryCache,
    row: ResultReplacementTransitionRow,
) -> None:
    before_state, after_state = result_replacement_audit_states(row)
    source_reference = f"results:{row.predecessor_result_id}->{row.successor_result_id}"
    matches = [
        audit
        for audit in cache.audits.get(row.id, ())
        if audit.actor_id == row.decided_by
        and audit.actor_kind == "LOCAL_OWNER"
        and "ADMIN" in audit.actor_roles
        and audit.occurred_at == row.decided_at
        and audit.reason == row.reason
        and audit.requirement_id == "ING-042"
        and audit.source_reference == source_reference
        and audit.before_state == before_state
        and audit.after_state == after_state
    ]
    if len(matches) != 1:
        raise ValueError("replacement Audit proof changed")


def _replacement_link_payload(value: HistoricalReplacementLink) -> dict[str, object]:
    return {
        "replacement_id": value.replacement_id,
        "predecessor_result_id": value.predecessor_result_id,
        "successor_result_id": value.successor_result_id,
        "predecessor_original_data_status_transition_id": (
            value.predecessor_original_data_status_transition_id
        ),
        "successor_data_status_transition_id": value.successor_data_status_transition_id,
        "predecessor_status_before": value.predecessor_before_status,
        "predecessor_status_after": value.predecessor_after_status,
        "successor_status_before": value.successor_before_status,
        "successor_status_after": value.successor_after_status,
        "predecessor_result_row_version_before": (value.predecessor_before_result_row_version),
        "predecessor_result_row_version_after": value.predecessor_after_result_row_version,
        "successor_result_row_version_before": value.successor_before_result_row_version,
        "successor_result_row_version_after": value.successor_after_result_row_version,
        "predecessor_measurement_count": value.predecessor_measurement_count,
        "predecessor_measurement_set_sha256": value.predecessor_measurement_set_sha256,
        "successor_measurement_count": value.successor_measurement_count,
        "successor_measurement_set_sha256": value.successor_measurement_set_sha256,
        "candidate_sha256": value.candidate_sha256,
        "intent_sha256": value.intent_sha256,
        "decided_by": value.decided_by,
        "decided_at": _json_datetime(value.decided_at),
        "reason": value.reason,
    }


def _json_datetime(value: datetime) -> str:
    rendered = value.isoformat()
    return rendered[:-6] + "Z" if rendered.endswith("+00:00") else rendered


def _source_fields(source_evidence: dict[str, object]) -> tuple[HistoricalCellProof, ...]:
    fields: list[HistoricalCellProof] = []
    for role in _SOURCE_ROLES:
        value = source_evidence.get(role)
        if value is None:
            continue
        if not isinstance(value, dict) or set(value) != _CELL_KEYS:
            raise ValueError("source cell proof shape changed")
        raw = _tagged(value.get("raw_value"))
        cached = _tagged(value.get("cached_value"))
        evidence_sha = canonical_json_sha256(value)
        fields.append(
            HistoricalCellProof(
                role=role,
                sheet_name=_bounded(_string(value, "sheet_name"), "source sheet"),
                coordinate=_bounded(_string(value, "coordinate"), "source cell"),
                raw_value=raw,
                cached_value=cached,
                formula_text=_bounded_optional(_optional_string(value, "formula_text")),
                number_format=_bounded(_string(value, "number_format"), "number format"),
                data_type=_bounded(_string(value, "data_type"), "data type"),
                display_value=_bounded_optional(_optional_string(value, "display_value")),
                display_value_status=_bounded(
                    _string(value, "display_value_status"), "display value status"
                ),
                value_kind=_bounded(_string(value, "value_kind"), "value kind"),
                evidence_sha256=evidence_sha,
            )
        )
    return tuple(fields)


def _verify_measurement_projection(measurement: LongMeasurementRow, source_sheet_name: str) -> None:
    evidence = measurement.evidence
    if not isinstance(evidence, dict) or set(evidence) != _CELL_KEYS:
        raise ValueError("sample evidence shape changed")
    raw = _tagged(evidence.get("raw_value"))
    raw_text = _canonical_bytes(raw).decode("utf-8")
    if (
        evidence.get("sheet_name") != source_sheet_name
        or evidence.get("coordinate") != measurement.source_cell
        or raw.get("kind") != measurement.raw_value_tag
        or raw_text != measurement.raw_value_text
        or (evidence.get("formula_text") is not None) != measurement.formula_flag
    ):
        raise ValueError("sample source projection changed")
    qualitative = measurement.raw_qualitative_value
    if qualitative is not None and not (
        raw.get("kind") == "str" and raw.get("value") == qualitative
    ):
        raise ValueError("sample qualitative projection changed")
    numeric = measurement.raw_numeric_value
    if numeric is not None:
        tagged_numeric = json.loads(numeric)
        if (
            not isinstance(tagged_numeric, dict)
            or set(tagged_numeric) != {"kind", "value"}
            or tagged_numeric.get("kind") not in {"int", "float", "decimal"}
            or tagged_numeric != raw
        ):
            raise ValueError("sample numeric projection changed")


def _binding_proof(
    value: dict[str, object] | None,
    *,
    project_key: str,
    supplier_scope: str,
    template_id: str,
    template_revision: int,
    row_key: str,
    binding_revision: int | None,
) -> dict[str, object] | None:
    if value is None:
        if binding_revision is not None:
            raise ValueError("binding revision lost its proof")
        return None
    allowed = {
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
    if set(value) != allowed or len(_canonical_bytes(value)) > 16_384:
        raise ValueError("binding proof is outside the bounded schema")
    key = value.get("key")
    expected_key = {
        "project_key": project_key,
        "supplier_scope": supplier_scope,
        "template_id": template_id,
        "template_revision": template_revision,
        "row_key": row_key,
    }
    if (
        not isinstance(key, dict)
        or key != expected_key
        or value.get("binding_revision") != binding_revision
    ):
        raise ValueError("binding source scope changed")
    return cast(dict[str, object], json.loads(_canonical_bytes(value)))


def _mapping_proof(
    job_projection: Any,
) -> HistoricalMappingProof:
    try:
        proof = verify_applied_mapping_proof(
            job_projection.applied_mapping_proof,
            job_projection.applied_mapping_proof_sha256,
            project_key=job_projection.job_project_key,
            source_file_id=job_projection.job_source_file_id,
            receipt_id=job_projection.source_receipt_id,
            content_sha256=job_projection.job_content_sha256,
            candidate_snapshot_sha256=job_projection.job_candidate_snapshot_sha256,
            mapping_template_revision_id=job_projection.mapping_revision_id,
            mapping_payload_sha256=job_projection.job_mapping_payload_sha256,
        )
    except LongPersistenceIntegrityError as error:
        raise ValueError("applied Mapping proof changed") from error
    effective_from = proof["template_effective_from"]
    effective_to = proof["template_effective_to"]
    if not isinstance(effective_from, str) or (
        effective_to is not None and not isinstance(effective_to, str)
    ):
        raise ValueError("applied Mapping effectivity changed")
    if (
        job_projection.job_mapping_payload_sha256 != job_projection.mapping_payload_sha256
        or proof["supplier_scope"] != job_projection.mapping_supplier_scope
        or proof["template_id"] != job_projection.mapping_template_id
        or proof["template_revision"] != job_projection.mapping_revision_number
        or proof["template_schema_version"] != job_projection.mapping_schema_version
    ):
        raise ValueError("applied Mapping identity changed")
    return HistoricalMappingProof(
        revision_id=cast(str, job_projection.mapping_revision_id),
        template_id=cast(str, job_projection.mapping_template_id),
        revision=cast(int, job_projection.mapping_revision_number),
        payload_sha256=_sha(cast(str, job_projection.mapping_payload_sha256), "mapping sha"),
        schema_version=cast(str, job_projection.mapping_schema_version),
        applied_effective_from=date.fromisoformat(effective_from),
        applied_effective_to=(
            date.fromisoformat(effective_to) if isinstance(effective_to, str) else None
        ),
        current_declared_effective_from=cast(date, job_projection.mapping_declared_effective_from),
        current_declared_effective_to=cast(
            date | None, job_projection.mapping_declared_effective_to
        ),
        current_resolved_effective_to=cast(
            date | None, job_projection.mapping_resolved_effective_to
        ),
        candidate_snapshot_sha256=_sha(
            cast(str, job_projection.job_candidate_snapshot_sha256),
            "job candidate snapshot sha",
        ),
    )


def _decision(result: LongInspectionResultRow) -> HistoricalDecisionProof | None:
    values = (
        result.current_data_status_transition_id,
        result.current_decision_command_id,
        result.current_decision_mode,
        result.current_decision_candidate_sha256,
        result.current_decided_by,
        result.current_decided_at,
        result.current_decision_reason,
    )
    if all(value is None for value in values):
        if result.data_status not in {"PENDING", "HELD"}:
            raise ValueError("terminal result lost decision proof")
        return None
    if any(value is None for value in values):
        raise ValueError("decision proof is partial")
    return HistoricalDecisionProof(
        transition_id=cast(str, values[0]),
        command_id=cast(str, values[1]),
        evaluation_mode=cast(str, values[2]),
        candidate_sha256=_sha(cast(str, values[3]), "decision candidate sha"),
        decided_by=cast(str, values[4]),
        decided_at=cast(datetime, values[5]),
        reason=_bounded(cast(str, values[6]), "decision reason"),
        from_status="PENDING",
        to_status=result.data_status,
        before_result_row_version=result.row_version - 1,
        after_result_row_version=result.row_version,
        intent_sha256="0" * 64,
        decision_snapshot_sha256="0" * 64,
    )


def _master(result: LongInspectionResultRow) -> HistoricalMasterProof | None:
    if result.applied_master_history_id is None:
        return None
    values = (
        result.applied_master_revision_id,
        result.applied_master_revision_number,
        result.applied_master_history_row_version,
        result.applied_master_revision_row_version,
        result.applied_master_payload_sha256,
        result.applied_master_declared_effective_from,
    )
    if any(value is None for value in values):
        raise ValueError("applied Master proof is partial")
    return HistoricalMasterProof(
        history_id=result.applied_master_history_id,
        revision_id=cast(str, values[0]),
        revision=cast(int, values[1]),
        history_row_version=cast(int, values[2]),
        revision_row_version=cast(int, values[3]),
        payload_sha256=_sha(cast(str, values[4]), "Master payload sha"),
        declared_effective_from=cast(date, values[5]),
        declared_effective_to=result.applied_master_declared_effective_to,
        resolved_effective_to=result.applied_master_resolved_effective_to,
    )


def _verify_transition(
    result: LongInspectionResultRow,
    transition: DataStatusTransitionRow,
    decision: HistoricalDecisionProof,
) -> HistoricalDecisionProof:
    _verify_json(
        transition.candidate_snapshot,
        transition.candidate_sha256,
        "decision candidate snapshot",
    )
    _verify_json(
        transition.decision_snapshot,
        transition.decision_snapshot_sha256,
        "decision snapshot",
    )
    if result.data_status == "REPLACED":
        status_projection_is_exact = (
            result.current_replacement_transition_id is not None
            and transition.to_status in {"VALID", "SUSPECT"}
            and transition.after_result_row_version + 1 == result.row_version
        )
    else:
        status_projection_is_exact = (
            result.current_replacement_transition_id is None
            and transition.to_status == result.data_status
            and transition.after_result_row_version == result.row_version
        )
    if (
        not status_projection_is_exact
        or transition.id != result.current_data_status_transition_id
        or transition.command_id != decision.command_id
        or transition.candidate_sha256 != decision.candidate_sha256
        or transition.evaluation_mode != decision.evaluation_mode
        or transition.decided_by != decision.decided_by
        or transition.decided_at != decision.decided_at
        or transition.reason != decision.reason
        or transition.system_judgment != result.system_judgment
        or transition.system_judgment_status != result.system_judgment_status
        or transition.spec_evaluation_status != result.spec_evaluation_status
        or transition.applied_master_history_id != result.applied_master_history_id
        or transition.applied_master_revision_id != result.applied_master_revision_id
        or transition.applied_master_revision_number != result.applied_master_revision_number
        or transition.applied_master_history_row_version
        != result.applied_master_history_row_version
        or transition.applied_master_revision_row_version
        != result.applied_master_revision_row_version
        or transition.applied_master_payload_sha256 != result.applied_master_payload_sha256
        or transition.applied_master_declared_effective_from
        != result.applied_master_declared_effective_from
        or transition.applied_master_declared_effective_to
        != result.applied_master_declared_effective_to
        or transition.applied_master_resolved_effective_to
        != result.applied_master_resolved_effective_to
    ):
        raise ValueError("decision transition projection changed")
    return replace(
        decision,
        from_status=transition.from_status,
        to_status=transition.to_status,
        before_result_row_version=transition.before_result_row_version,
        after_result_row_version=transition.after_result_row_version,
        intent_sha256=_sha(transition.intent_sha256, "intent sha"),
        decision_snapshot_sha256=_sha(transition.decision_snapshot_sha256, "decision snapshot sha"),
    )


def _verify_json(value: object, expected_sha: str, name: str) -> None:
    _sha(expected_sha, f"{name} sha")
    if canonical_json_sha256(value) != expected_sha:
        raise ValueError(f"{name} digest changed")


def _tagged(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {"kind", "value"}:
        raise ValueError("tagged source value shape changed")
    if not isinstance(value.get("kind"), str):
        raise ValueError("tagged source kind changed")
    if len(_canonical_bytes(value)) > 4_096:
        raise HistoricalComparisonError(
            "HISTORY_SOURCE_VALUE_LIMIT_EXCEEDED",
            "원본 셀 값이 안전한 조회 한도를 넘었습니다.",
            "원본 값 확인 필요",
        )
    return cast(dict[str, object], json.loads(_canonical_bytes(value)))


def _string(value: dict[str, object], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str):
        raise ValueError(f"{key} is not text")
    return item


def _optional_string(value: dict[str, object], key: str) -> str | None:
    item = value.get(key)
    if item is not None and not isinstance(item, str):
        raise ValueError(f"{key} is not optional text")
    return item


def _exact(value: str, name: str, limit: int) -> str:
    if not value or value != value.strip() or len(value) > limit or "\x00" in value:
        raise ValueError(f"{name} is invalid")
    return value


def _bounded(value: str, name: str) -> str:
    return _exact(value, name, _MAX_TEXT)


def _bounded_optional(value: str | None) -> str | None:
    return None if value is None else _bounded(value, "historical text")


def _sha(value: str | None, name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ValueError(f"{name} is invalid")
    return value


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
