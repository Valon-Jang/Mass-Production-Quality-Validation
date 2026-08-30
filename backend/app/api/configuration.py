"""Korean-safe API for explicit project-local canonical first setup."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from datetime import date, datetime
from typing import Any, Literal, Never, Protocol

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from app.api.intake import SafeErrorResponse
from app.application.configuration_workflow import (
    ApprovedMappingSelection,
    ConfigurationCellSource,
    ConfigurationSnapshot,
    ConfigurationWorkflowConflictError,
    ConfigurationWorkflowError,
    ConfigurationWorkflowNotFoundError,
    ConfigurationWorkflowUnavailableError,
    ConfigurationWorkflowValidationError,
    CreateInspectionItemRequest,
    CreateMasterSpecDraftRequest,
    CreateModelPartRequest,
    CreateModelRequest,
    CreateRowBindingDraftRequest,
    CreateSupplierRequest,
    MappingRowSelection,
    MasterSpecWorkflowRequest,
    RowBindingWorkflowRequest,
    SetItemDispositionRequest,
)
from app.domain.long_format import MeasurementMode, SamplePolicy
from app.domain.master_config import InspectionItemDisposition
from app.infrastructure.master_config import (
    PersistedCanonicalInspectionItem,
    PersistedCanonicalModel,
    PersistedCanonicalModelPart,
    PersistedCanonicalRowBindingRevision,
    PersistedCanonicalSupplier,
    PersistedMasterSpecRevision,
)


class ConfigurationWorkflowPort(Protocol):
    def snapshot(self, project_key: str) -> ConfigurationSnapshot: ...

    def create_model(self, request: CreateModelRequest) -> PersistedCanonicalModel: ...

    def create_supplier(self, request: CreateSupplierRequest) -> PersistedCanonicalSupplier: ...

    def create_model_part(self, request: CreateModelPartRequest) -> PersistedCanonicalModelPart: ...

    def create_inspection_item(
        self, request: CreateInspectionItemRequest
    ) -> PersistedCanonicalInspectionItem: ...

    def set_item_disposition(
        self, request: SetItemDispositionRequest
    ) -> PersistedCanonicalInspectionItem: ...

    def create_master_spec_draft(
        self, request: CreateMasterSpecDraftRequest
    ) -> PersistedMasterSpecRevision: ...

    def review_master_spec(
        self, request: MasterSpecWorkflowRequest
    ) -> PersistedMasterSpecRevision: ...

    def approve_master_spec(
        self, request: MasterSpecWorkflowRequest
    ) -> PersistedMasterSpecRevision: ...

    def create_row_binding_draft(
        self, request: CreateRowBindingDraftRequest
    ) -> PersistedCanonicalRowBindingRevision: ...

    def review_row_binding(
        self, request: RowBindingWorkflowRequest
    ) -> PersistedCanonicalRowBindingRevision: ...

    def approve_row_binding(
        self, request: RowBindingWorkflowRequest
    ) -> PersistedCanonicalRowBindingRevision: ...


class CreateModelRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_key: str = Field(min_length=1, max_length=200)
    model_key: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=2000)


class CreateSupplierRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_key: str = Field(min_length=1, max_length=200)
    supplier_key: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=2000)


class CreateModelPartRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_key: str = Field(min_length=1, max_length=200)
    model_key: str = Field(min_length=1, max_length=200)
    model_part_key: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=2000)


class CreateInspectionItemRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_key: str = Field(min_length=1, max_length=200)
    model_part_key: str = Field(min_length=1, max_length=200)
    item_key: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=2000)


class SetItemDispositionRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_key: str = Field(min_length=1, max_length=200)
    item_key: str = Field(min_length=1, max_length=200)
    disposition: Literal["MANAGED", "EXCLUDED"]
    expected_row_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)


class CreateMasterSpecDraftRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_key: str = Field(min_length=1, max_length=200)
    canonical_item_key: str = Field(min_length=1, max_length=200)
    target: str | None
    lsl: str | None
    usl: str | None
    unit: str = Field(min_length=1, max_length=200)
    external_spec_revision: str = Field(min_length=1, max_length=200)
    effective_from: date
    effective_to: date | None
    source_reference: str = Field(min_length=1, max_length=2000)
    expected_history_row_version: Literal[0]
    reason: str = Field(min_length=1, max_length=2000)


class MasterSpecWorkflowRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_key: str = Field(min_length=1, max_length=200)
    canonical_item_key: str = Field(min_length=1, max_length=200)
    revision: Literal[1]
    expected_history_row_version: int = Field(ge=1)
    expected_revision_row_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)


class CreateRowBindingDraftRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_key: str = Field(min_length=1, max_length=200)
    supplier_scope: str = Field(min_length=1, max_length=200)
    template_id: str = Field(min_length=1, max_length=200)
    template_revision: int = Field(ge=1)
    row_key: str = Field(min_length=1, max_length=200)
    source_model_values: tuple[str, ...] = Field(min_length=1)
    canonical_model_key: str = Field(min_length=1, max_length=200)
    canonical_supplier_key: str = Field(min_length=1, max_length=200)
    canonical_model_part_key: str = Field(min_length=1, max_length=200)
    canonical_item_key: str = Field(min_length=1, max_length=200)
    measurement_mode: Literal["NUMERIC", "QUALITATIVE", "JUDGMENT_ONLY"]
    sample_policy: Literal["AT_LEAST_ONE", "ZERO_ALLOWED"]
    effective_from: date
    effective_to: date | None
    expected_history_row_version: Literal[0]
    reason: str = Field(min_length=1, max_length=2000)


class RowBindingWorkflowRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_key: str = Field(min_length=1, max_length=200)
    supplier_scope: str = Field(min_length=1, max_length=200)
    template_id: str = Field(min_length=1, max_length=200)
    template_revision: int = Field(ge=1)
    row_key: str = Field(min_length=1, max_length=200)
    binding_revision: Literal[1]
    expected_history_row_version: int = Field(ge=1)
    expected_revision_row_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)


class CanonicalModelResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_key: str
    model_key: str
    display_name: str
    row_version: int


class CanonicalSupplierResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_key: str
    supplier_key: str
    display_name: str
    row_version: int


class CanonicalModelPartResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_key: str
    model_key: str
    model_part_key: str
    display_name: str
    row_version: int


class CanonicalInspectionItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_key: str
    model_part_key: str
    item_key: str
    display_name: str
    disposition: Literal["CANDIDATE", "MANAGED", "EXCLUDED"]
    row_version: int


class MasterSpecResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_key: str
    canonical_item_key: str
    revision: int
    status: Literal["DRAFT", "REVIEWED", "APPROVED"]
    target: str | None
    lsl: str | None
    usl: str | None
    unit: str
    external_spec_revision: str
    declared_effective_from: date
    declared_effective_to: date | None
    resolved_effective_to: date | None
    change_reason: str
    source_reference: str
    reviewed_by: str | None
    reviewed_at: datetime | None
    approved_by: str | None
    approved_at: datetime | None
    history_id: str
    revision_id: str
    payload_sha256: str
    history_row_version: int
    revision_row_version: int


class RowBindingResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_key: str
    supplier_scope: str
    template_id: str
    template_revision: int
    row_key: str
    binding_revision: int
    status: Literal["DRAFT", "REVIEWED", "APPROVED"]
    source_model_values: tuple[str, ...]
    canonical_model_key: str
    canonical_supplier_key: str
    canonical_model_part_key: str
    canonical_item_key: str
    measurement_mode: Literal["NUMERIC", "QUALITATIVE", "JUDGMENT_ONLY"]
    sample_policy: Literal["AT_LEAST_ONE", "ZERO_ALLOWED"]
    declared_effective_from: date
    declared_effective_to: date | None
    resolved_effective_to: date | None
    change_reason: str
    source_reference: str
    reviewed_by: str | None
    reviewed_at: datetime | None
    approved_by: str | None
    approved_at: datetime | None
    history_id: str
    revision_id: str
    payload_sha256: str
    history_row_version: int
    revision_row_version: int


class ConfigurationCellSourceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sheet_name: str
    coordinate: str


class MappingRowSelectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    row_key: str
    sheet_name: str
    row_index: int
    item_source: ConfigurationCellSourceResponse
    method_source: ConfigurationCellSourceResponse | None
    instrument_source: ConfigurationCellSourceResponse | None
    specification_source: ConfigurationCellSourceResponse | None
    tolerance_source: ConfigurationCellSourceResponse | None
    minimum_source: ConfigurationCellSourceResponse | None
    maximum_source: ConfigurationCellSourceResponse | None
    sample_cells: tuple[ConfigurationCellSourceResponse, ...]
    supplier_result_source: ConfigurationCellSourceResponse | None
    section_source: ConfigurationCellSourceResponse | None
    category_source: ConfigurationCellSourceResponse | None
    unit_source: ConfigurationCellSourceResponse | None
    measurement_point_source: ConfigurationCellSourceResponse | None
    measurement_location_source: ConfigurationCellSourceResponse | None
    cavity_source: ConfigurationCellSourceResponse | None
    target_source: ConfigurationCellSourceResponse | None
    lsl_source: ConfigurationCellSourceResponse | None
    usl_source: ConfigurationCellSourceResponse | None
    source_spec_revision_source: ConfigurationCellSourceResponse | None


class ApprovedMappingSelectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_key: str
    supplier_scope: str
    template_id: str
    revision: int
    schema_version: Literal["2"]
    status: Literal["APPROVED"]
    history_id: str
    revision_id: str
    payload_sha256: str
    history_row_version: int
    revision_row_version: int
    declared_effective_from: date
    declared_effective_to: date | None
    resolved_effective_to: date | None
    supplier_source_aliases: tuple[str, ...]
    model_source: ConfigurationCellSourceResponse | None
    rows: tuple[MappingRowSelectionResponse, ...]


class ConfigurationCapabilitiesResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    first_master_revision_only: Literal[True]
    first_binding_revision_only: Literal[True]
    later_revisions_supported: Literal[False]
    supersession_supported: Literal[False]
    actor_source: Literal["TRUSTED_LOCAL_OWNER"]


class ConfigurationSnapshotResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_key: str
    models: tuple[CanonicalModelResponse, ...]
    suppliers: tuple[CanonicalSupplierResponse, ...]
    model_parts: tuple[CanonicalModelPartResponse, ...]
    inspection_items: tuple[CanonicalInspectionItemResponse, ...]
    master_specs: tuple[MasterSpecResponse, ...]
    row_bindings: tuple[RowBindingResponse, ...]
    approved_mapping_revisions: tuple[ApprovedMappingSelectionResponse, ...]
    capabilities: ConfigurationCapabilitiesResponse
    official_values_created: Literal[False]
    auto_effects: Literal[False]
    ai_used: Literal[False]


_SAFE_ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    status.HTTP_400_BAD_REQUEST: {"model": SafeErrorResponse},
    status.HTTP_404_NOT_FOUND: {"model": SafeErrorResponse},
    status.HTTP_409_CONFLICT: {"model": SafeErrorResponse},
    status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": SafeErrorResponse},
    status.HTTP_503_SERVICE_UNAVAILABLE: {"model": SafeErrorResponse},
}


class _SafeConfigurationValidationRoute(APIRoute):
    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def safe_handler(request: Request) -> Response:
            try:
                return await original(request)
            except RequestValidationError as error:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "INVALID_CONFIGURATION_REQUEST",
                        "message": "설정 요청 형식과 필수 입력값을 확인해 주세요.",
                        "status_label": "설정 요청 오류",
                    },
                ) from error

        return safe_handler


def create_configuration_router(service: ConfigurationWorkflowPort) -> APIRouter:
    """Create injected routes without opening a default database at import time."""

    router = APIRouter(
        prefix="/api/v1/configuration",
        tags=["configuration"],
        route_class=_SafeConfigurationValidationRoute,
    )

    @router.get(
        "/snapshot",
        response_model=ConfigurationSnapshotResponse,
        responses=_SAFE_ERROR_RESPONSES,
    )
    async def get_snapshot(
        project_key: str = Query(min_length=1, max_length=200),
    ) -> ConfigurationSnapshotResponse:
        result = await _call(service.snapshot, project_key)
        return _snapshot_response(result)

    @router.post(
        "/models",
        response_model=CanonicalModelResponse,
        status_code=status.HTTP_201_CREATED,
        responses=_SAFE_ERROR_RESPONSES,
    )
    async def create_model(body: CreateModelRequestBody) -> CanonicalModelResponse:
        result = await _call(
            service.create_model,
            CreateModelRequest(**body.model_dump()),
        )
        return _model_response(result)

    @router.post(
        "/suppliers",
        response_model=CanonicalSupplierResponse,
        status_code=status.HTTP_201_CREATED,
        responses=_SAFE_ERROR_RESPONSES,
    )
    async def create_supplier(body: CreateSupplierRequestBody) -> CanonicalSupplierResponse:
        result = await _call(
            service.create_supplier,
            CreateSupplierRequest(**body.model_dump()),
        )
        return _supplier_response(result)

    @router.post(
        "/model-parts",
        response_model=CanonicalModelPartResponse,
        status_code=status.HTTP_201_CREATED,
        responses=_SAFE_ERROR_RESPONSES,
    )
    async def create_model_part(body: CreateModelPartRequestBody) -> CanonicalModelPartResponse:
        result = await _call(
            service.create_model_part,
            CreateModelPartRequest(**body.model_dump()),
        )
        return _part_response(result)

    @router.post(
        "/inspection-items",
        response_model=CanonicalInspectionItemResponse,
        status_code=status.HTTP_201_CREATED,
        responses=_SAFE_ERROR_RESPONSES,
    )
    async def create_item(
        body: CreateInspectionItemRequestBody,
    ) -> CanonicalInspectionItemResponse:
        result = await _call(
            service.create_inspection_item,
            CreateInspectionItemRequest(**body.model_dump()),
        )
        return _item_response(result)

    @router.post(
        "/inspection-items/dispositions",
        response_model=CanonicalInspectionItemResponse,
        responses=_SAFE_ERROR_RESPONSES,
    )
    async def set_item_disposition(
        body: SetItemDispositionRequestBody,
    ) -> CanonicalInspectionItemResponse:
        result = await _call(
            service.set_item_disposition,
            SetItemDispositionRequest(
                project_key=body.project_key,
                item_key=body.item_key,
                disposition=InspectionItemDisposition(body.disposition),
                expected_row_version=body.expected_row_version,
                reason=body.reason,
            ),
        )
        return _item_response(result)

    @router.post(
        "/master-specs/drafts",
        response_model=MasterSpecResponse,
        status_code=status.HTTP_201_CREATED,
        responses=_SAFE_ERROR_RESPONSES,
    )
    async def create_master_draft(
        body: CreateMasterSpecDraftRequestBody,
    ) -> MasterSpecResponse:
        result = await _call(
            service.create_master_spec_draft,
            CreateMasterSpecDraftRequest(**body.model_dump()),
        )
        return _master_response(result)

    @router.post(
        "/master-specs/reviews",
        response_model=MasterSpecResponse,
        responses=_SAFE_ERROR_RESPONSES,
    )
    async def review_master(body: MasterSpecWorkflowRequestBody) -> MasterSpecResponse:
        result = await _call(
            service.review_master_spec,
            MasterSpecWorkflowRequest(**body.model_dump()),
        )
        return _master_response(result)

    @router.post(
        "/master-specs/approvals",
        response_model=MasterSpecResponse,
        responses=_SAFE_ERROR_RESPONSES,
    )
    async def approve_master(body: MasterSpecWorkflowRequestBody) -> MasterSpecResponse:
        result = await _call(
            service.approve_master_spec,
            MasterSpecWorkflowRequest(**body.model_dump()),
        )
        return _master_response(result)

    @router.post(
        "/row-bindings/drafts",
        response_model=RowBindingResponse,
        status_code=status.HTTP_201_CREATED,
        responses=_SAFE_ERROR_RESPONSES,
    )
    async def create_binding_draft(
        body: CreateRowBindingDraftRequestBody,
    ) -> RowBindingResponse:
        result = await _call(
            service.create_row_binding_draft,
            CreateRowBindingDraftRequest(
                project_key=body.project_key,
                supplier_scope=body.supplier_scope,
                template_id=body.template_id,
                template_revision=body.template_revision,
                row_key=body.row_key,
                source_model_values=body.source_model_values,
                canonical_model_key=body.canonical_model_key,
                canonical_supplier_key=body.canonical_supplier_key,
                canonical_model_part_key=body.canonical_model_part_key,
                canonical_item_key=body.canonical_item_key,
                measurement_mode=MeasurementMode(body.measurement_mode),
                sample_policy=SamplePolicy(body.sample_policy),
                effective_from=body.effective_from,
                effective_to=body.effective_to,
                expected_history_row_version=body.expected_history_row_version,
                reason=body.reason,
            ),
        )
        return _binding_response(result)

    @router.post(
        "/row-bindings/reviews",
        response_model=RowBindingResponse,
        responses=_SAFE_ERROR_RESPONSES,
    )
    async def review_binding(body: RowBindingWorkflowRequestBody) -> RowBindingResponse:
        result = await _call(
            service.review_row_binding,
            RowBindingWorkflowRequest(**body.model_dump()),
        )
        return _binding_response(result)

    @router.post(
        "/row-bindings/approvals",
        response_model=RowBindingResponse,
        responses=_SAFE_ERROR_RESPONSES,
    )
    async def approve_binding(body: RowBindingWorkflowRequestBody) -> RowBindingResponse:
        result = await _call(
            service.approve_row_binding,
            RowBindingWorkflowRequest(**body.model_dump()),
        )
        return _binding_response(result)

    return router


async def _call(function: Callable[..., Any], *args: object) -> Any:
    try:
        return await run_in_threadpool(function, *args)
    except ConfigurationWorkflowError as error:
        _raise_application_error(error)
    except Exception as error:
        raise _unexpected_http_error() from error


def _model_response(record: PersistedCanonicalModel) -> CanonicalModelResponse:
    return CanonicalModelResponse(
        project_key=record.model.project_key,
        model_key=record.model.model_key,
        display_name=record.model.display_name,
        row_version=record.row_version,
    )


def _supplier_response(record: PersistedCanonicalSupplier) -> CanonicalSupplierResponse:
    return CanonicalSupplierResponse(
        project_key=record.supplier.project_key,
        supplier_key=record.supplier.supplier_key,
        display_name=record.supplier.display_name,
        row_version=record.row_version,
    )


def _part_response(record: PersistedCanonicalModelPart) -> CanonicalModelPartResponse:
    return CanonicalModelPartResponse(
        project_key=record.model_part.project_key,
        model_key=record.model_part.model_key,
        model_part_key=record.model_part.model_part_key,
        display_name=record.model_part.display_name,
        row_version=record.row_version,
    )


def _item_response(record: PersistedCanonicalInspectionItem) -> CanonicalInspectionItemResponse:
    return CanonicalInspectionItemResponse(
        project_key=record.item.project_key,
        model_part_key=record.item.model_part_key,
        item_key=record.item.item_key,
        display_name=record.item.display_name,
        disposition=record.item.disposition.value,
        row_version=record.row_version,
    )


def _master_response(record: PersistedMasterSpecRevision) -> MasterSpecResponse:
    spec = record.spec
    return MasterSpecResponse(
        project_key=spec.project_key,
        canonical_item_key=spec.canonical_item_key,
        revision=spec.revision,
        status=spec.status.value,
        target=str(spec.target) if spec.target is not None else None,
        lsl=str(spec.lsl) if spec.lsl is not None else None,
        usl=str(spec.usl) if spec.usl is not None else None,
        unit=spec.unit,
        external_spec_revision=spec.external_spec_revision,
        declared_effective_from=spec.effective_from,
        declared_effective_to=spec.effective_to,
        resolved_effective_to=record.resolved_effective_to,
        change_reason=spec.change_reason,
        source_reference=spec.source_reference,
        reviewed_by=spec.reviewed_by,
        reviewed_at=spec.reviewed_at,
        approved_by=spec.approved_by,
        approved_at=spec.approved_at,
        history_id=record.history_id,
        revision_id=record.revision_id,
        payload_sha256=record.payload_sha256,
        history_row_version=record.history_row_version,
        revision_row_version=record.revision_row_version,
    )


def _binding_response(record: PersistedCanonicalRowBindingRevision) -> RowBindingResponse:
    binding = record.binding
    key = binding.key
    return RowBindingResponse(
        project_key=key.project_key,
        supplier_scope=key.supplier_scope,
        template_id=key.template_id,
        template_revision=key.template_revision,
        row_key=key.row_key,
        binding_revision=binding.binding_revision,
        status=binding.status.value,
        source_model_values=binding.source_model_values,
        canonical_model_key=binding.canonical_model_key,
        canonical_supplier_key=binding.canonical_supplier_key,
        canonical_model_part_key=binding.canonical_model_part_key,
        canonical_item_key=binding.canonical_item_key,
        measurement_mode=binding.measurement_mode.value,
        sample_policy=binding.sample_policy.value,
        declared_effective_from=binding.effective_from,
        declared_effective_to=binding.effective_to,
        resolved_effective_to=record.resolved_effective_to,
        change_reason=binding.change_reason,
        source_reference=binding.source_reference,
        reviewed_by=binding.reviewed_by,
        reviewed_at=binding.reviewed_at,
        approved_by=binding.approved_by,
        approved_at=binding.approved_at,
        history_id=record.history_id,
        revision_id=record.revision_id,
        payload_sha256=record.payload_sha256,
        history_row_version=record.history_row_version,
        revision_row_version=record.revision_row_version,
    )


def _cell_response(value: ConfigurationCellSource) -> ConfigurationCellSourceResponse:
    return ConfigurationCellSourceResponse(
        sheet_name=value.sheet_name,
        coordinate=value.coordinate,
    )


def _optional_cell_response(
    value: ConfigurationCellSource | None,
) -> ConfigurationCellSourceResponse | None:
    return _cell_response(value) if value is not None else None


def _mapping_row_response(row: MappingRowSelection) -> MappingRowSelectionResponse:
    return MappingRowSelectionResponse(
        row_key=row.row_key,
        sheet_name=row.sheet_name,
        row_index=row.row_index,
        item_source=_cell_response(row.item_source),
        method_source=_optional_cell_response(row.method_source),
        instrument_source=_optional_cell_response(row.instrument_source),
        specification_source=_optional_cell_response(row.specification_source),
        tolerance_source=_optional_cell_response(row.tolerance_source),
        minimum_source=_optional_cell_response(row.minimum_source),
        maximum_source=_optional_cell_response(row.maximum_source),
        sample_cells=tuple(_cell_response(value) for value in row.sample_cells),
        supplier_result_source=_optional_cell_response(row.supplier_result_source),
        section_source=_optional_cell_response(row.section_source),
        category_source=_optional_cell_response(row.category_source),
        unit_source=_optional_cell_response(row.unit_source),
        measurement_point_source=_optional_cell_response(row.measurement_point_source),
        measurement_location_source=_optional_cell_response(row.measurement_location_source),
        cavity_source=_optional_cell_response(row.cavity_source),
        target_source=_optional_cell_response(row.target_source),
        lsl_source=_optional_cell_response(row.lsl_source),
        usl_source=_optional_cell_response(row.usl_source),
        source_spec_revision_source=_optional_cell_response(row.source_spec_revision_source),
    )


def _mapping_response(
    value: ApprovedMappingSelection,
) -> ApprovedMappingSelectionResponse:
    return ApprovedMappingSelectionResponse(
        project_key=value.project_key,
        supplier_scope=value.supplier_scope,
        template_id=value.template_id,
        revision=value.revision,
        schema_version="2",
        status="APPROVED",
        history_id=value.history_id,
        revision_id=value.revision_id,
        payload_sha256=value.payload_sha256,
        history_row_version=value.history_row_version,
        revision_row_version=value.revision_row_version,
        declared_effective_from=value.declared_effective_from,
        declared_effective_to=value.declared_effective_to,
        resolved_effective_to=value.resolved_effective_to,
        supplier_source_aliases=value.supplier_source_aliases,
        model_source=_optional_cell_response(value.model_source),
        rows=tuple(_mapping_row_response(row) for row in value.rows),
    )


def _snapshot_response(snapshot: ConfigurationSnapshot) -> ConfigurationSnapshotResponse:
    return ConfigurationSnapshotResponse(
        project_key=snapshot.project_key,
        models=tuple(_model_response(value) for value in snapshot.models),
        suppliers=tuple(_supplier_response(value) for value in snapshot.suppliers),
        model_parts=tuple(_part_response(value) for value in snapshot.model_parts),
        inspection_items=tuple(_item_response(value) for value in snapshot.inspection_items),
        master_specs=tuple(_master_response(value) for value in snapshot.master_specs),
        row_bindings=tuple(_binding_response(value) for value in snapshot.row_bindings),
        approved_mapping_revisions=tuple(
            _mapping_response(value) for value in snapshot.approved_mapping_revisions
        ),
        capabilities=ConfigurationCapabilitiesResponse(
            first_master_revision_only=True,
            first_binding_revision_only=True,
            later_revisions_supported=False,
            supersession_supported=False,
            actor_source="TRUSTED_LOCAL_OWNER",
        ),
        official_values_created=False,
        auto_effects=False,
        ai_used=False,
    )


def _raise_application_error(error: ConfigurationWorkflowError) -> Never:
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    if isinstance(error, ConfigurationWorkflowValidationError):
        status_code = status.HTTP_400_BAD_REQUEST
    elif isinstance(error, ConfigurationWorkflowNotFoundError):
        status_code = status.HTTP_404_NOT_FOUND
    elif isinstance(error, ConfigurationWorkflowConflictError):
        status_code = status.HTTP_409_CONFLICT
    elif isinstance(error, ConfigurationWorkflowUnavailableError):
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    raise HTTPException(
        status_code=status_code,
        detail={
            "code": error.code,
            "message": error.safe_message,
            "status_label": error.status_label,
        },
    ) from error


def _unexpected_http_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={
            "code": "CONFIGURATION_UNEXPECTED_FAILURE",
            "message": "설정 요청 처리 중 안전하게 복구할 수 없는 오류가 발생했습니다.",
            "status_label": "설정 처리 오류",
        },
    )
