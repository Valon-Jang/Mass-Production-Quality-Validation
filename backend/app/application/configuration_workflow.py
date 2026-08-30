"""Project-local first-setup orchestration for canonical configuration.

This HTTP-facing facade deliberately exposes only the first immutable revision
workflow.  It injects the trusted local owner, delegates every mutation and
Audit write to :class:`MasterConfigCommandService`, and builds a read-only
project snapshot from integrity-checked persistent records.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import TypeVar

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.application.master_config_commands import (
    ApproveCanonicalRowBindingRevisionCommand,
    ApproveMasterSpecRevisionCommand,
    CreateCanonicalInspectionItemCommand,
    CreateCanonicalModelCommand,
    CreateCanonicalModelPartCommand,
    CreateCanonicalRowBindingRevisionCommand,
    CreateCanonicalSupplierCommand,
    CreateMasterSpecRevisionCommand,
    MasterConfigAuthorizationError,
    MasterConfigCommandService,
    ReviewCanonicalRowBindingRevisionCommand,
    ReviewMasterSpecRevisionCommand,
    SetInspectionItemDispositionCommand,
)
from app.domain.identity import LOCAL_OWNER
from app.domain.long_format import (
    CanonicalRowBindingKey,
    MeasurementMode,
    SamplePolicy,
)
from app.domain.mapping import CellAddress, IdentifierKind, MappingTemplateStatus
from app.domain.master_config import (
    CanonicalInspectionItem,
    CanonicalModel,
    CanonicalModelPart,
    CanonicalRowBindingRevision,
    CanonicalSupplier,
    ConfigurationRevisionStatus,
    InspectionItemDisposition,
    MasterSpecRevision,
)
from app.infrastructure.database import Database
from app.infrastructure.mapping_templates import (
    MappingTemplateHistoryRow,
    MappingTemplateNotFoundError,
    MappingTemplatePayloadIntegrityError,
    MappingTemplateRepository,
    MappingTemplateRevisionRow,
    PersistedMappingTemplate,
    StaleMappingTemplateWriteError,
)
from app.infrastructure.master_config import (
    CanonicalInspectionItemRow,
    CanonicalModelPartRow,
    CanonicalModelRow,
    CanonicalRowBindingHistoryRow,
    CanonicalRowBindingRevisionRow,
    CanonicalSupplierRow,
    ImmutableMasterConfigRevisionError,
    MasterConfigEffectivePeriodError,
    MasterConfigNotFoundError,
    MasterConfigPayloadIntegrityError,
    MasterConfigRepository,
    MasterConfigScopeError,
    MasterSpecHistoryRow,
    MasterSpecRevisionRow,
    PersistedCanonicalInspectionItem,
    PersistedCanonicalModel,
    PersistedCanonicalModelPart,
    PersistedCanonicalRowBindingRevision,
    PersistedCanonicalSupplier,
    PersistedMasterSpecRevision,
    StaleMasterConfigWriteError,
)

_SUPPORTED_UI_MAPPING_SCHEMA_VERSION = "2"
_FIRST_REVISION = 1
_T = TypeVar("_T")


@dataclass(frozen=True, slots=True)
class CreateModelRequest:
    project_key: str
    model_key: str
    display_name: str
    reason: str


@dataclass(frozen=True, slots=True)
class CreateSupplierRequest:
    project_key: str
    supplier_key: str
    display_name: str
    reason: str


@dataclass(frozen=True, slots=True)
class CreateModelPartRequest:
    project_key: str
    model_key: str
    model_part_key: str
    display_name: str
    reason: str


@dataclass(frozen=True, slots=True)
class CreateInspectionItemRequest:
    project_key: str
    model_part_key: str
    item_key: str
    display_name: str
    reason: str


@dataclass(frozen=True, slots=True)
class SetItemDispositionRequest:
    project_key: str
    item_key: str
    disposition: InspectionItemDisposition
    expected_row_version: int
    reason: str


@dataclass(frozen=True, slots=True)
class CreateMasterSpecDraftRequest:
    project_key: str
    canonical_item_key: str
    target: str | None
    lsl: str | None
    usl: str | None
    unit: str
    external_spec_revision: str
    effective_from: date
    effective_to: date | None
    source_reference: str
    expected_history_row_version: int
    reason: str


@dataclass(frozen=True, slots=True)
class MasterSpecWorkflowRequest:
    project_key: str
    canonical_item_key: str
    revision: int
    expected_history_row_version: int
    expected_revision_row_version: int
    reason: str


@dataclass(frozen=True, slots=True)
class CreateRowBindingDraftRequest:
    project_key: str
    supplier_scope: str
    template_id: str
    template_revision: int
    row_key: str
    source_model_values: tuple[str, ...]
    canonical_model_key: str
    canonical_supplier_key: str
    canonical_model_part_key: str
    canonical_item_key: str
    measurement_mode: MeasurementMode
    sample_policy: SamplePolicy
    effective_from: date
    effective_to: date | None
    expected_history_row_version: int
    reason: str


@dataclass(frozen=True, slots=True)
class RowBindingWorkflowRequest:
    project_key: str
    supplier_scope: str
    template_id: str
    template_revision: int
    row_key: str
    binding_revision: int
    expected_history_row_version: int
    expected_revision_row_version: int
    reason: str

    @property
    def key(self) -> CanonicalRowBindingKey:
        return CanonicalRowBindingKey(
            project_key=self.project_key,
            supplier_scope=self.supplier_scope,
            template_id=self.template_id,
            template_revision=self.template_revision,
            row_key=self.row_key,
        )


@dataclass(frozen=True, slots=True, order=True)
class ConfigurationCellSource:
    sheet_name: str
    coordinate: str


@dataclass(frozen=True, slots=True)
class MappingRowSelection:
    row_key: str
    sheet_name: str
    row_index: int
    item_source: ConfigurationCellSource
    method_source: ConfigurationCellSource | None
    instrument_source: ConfigurationCellSource | None
    specification_source: ConfigurationCellSource | None
    tolerance_source: ConfigurationCellSource | None
    minimum_source: ConfigurationCellSource | None
    maximum_source: ConfigurationCellSource | None
    sample_cells: tuple[ConfigurationCellSource, ...]
    supplier_result_source: ConfigurationCellSource | None
    section_source: ConfigurationCellSource | None
    category_source: ConfigurationCellSource | None
    unit_source: ConfigurationCellSource | None
    measurement_point_source: ConfigurationCellSource | None
    measurement_location_source: ConfigurationCellSource | None
    cavity_source: ConfigurationCellSource | None
    target_source: ConfigurationCellSource | None
    lsl_source: ConfigurationCellSource | None
    usl_source: ConfigurationCellSource | None
    source_spec_revision_source: ConfigurationCellSource | None


@dataclass(frozen=True, slots=True)
class ApprovedMappingSelection:
    project_key: str
    supplier_scope: str
    template_id: str
    revision: int
    schema_version: str
    status: MappingTemplateStatus
    history_id: str
    revision_id: str
    payload_sha256: str
    history_row_version: int
    revision_row_version: int
    declared_effective_from: date
    declared_effective_to: date | None
    resolved_effective_to: date | None
    supplier_source_aliases: tuple[str, ...]
    model_source: ConfigurationCellSource | None
    rows: tuple[MappingRowSelection, ...]


@dataclass(frozen=True, slots=True)
class ConfigurationCapabilities:
    first_master_revision_only: bool = True
    first_binding_revision_only: bool = True
    later_revisions_supported: bool = False
    supersession_supported: bool = False
    actor_source: str = "TRUSTED_LOCAL_OWNER"


@dataclass(frozen=True, slots=True)
class ConfigurationSnapshot:
    project_key: str
    models: tuple[PersistedCanonicalModel, ...]
    suppliers: tuple[PersistedCanonicalSupplier, ...]
    model_parts: tuple[PersistedCanonicalModelPart, ...]
    inspection_items: tuple[PersistedCanonicalInspectionItem, ...]
    master_specs: tuple[PersistedMasterSpecRevision, ...]
    row_bindings: tuple[PersistedCanonicalRowBindingRevision, ...]
    approved_mapping_revisions: tuple[ApprovedMappingSelection, ...]
    capabilities: ConfigurationCapabilities = ConfigurationCapabilities()
    official_values_created: bool = False
    auto_effects: bool = False
    ai_used: bool = False


class ConfigurationWorkflowError(RuntimeError):
    """Stable safe error that does not expose SQL or internal source paths."""

    def __init__(self, code: str, message: str, status_label: str) -> None:
        self.code = code
        self.safe_message = message
        self.status_label = status_label
        super().__init__(code)


class ConfigurationWorkflowValidationError(ConfigurationWorkflowError):
    pass


class ConfigurationWorkflowNotFoundError(ConfigurationWorkflowError):
    pass


class ConfigurationWorkflowConflictError(ConfigurationWorkflowError):
    pass


class ConfigurationWorkflowUnavailableError(ConfigurationWorkflowError):
    pass


class ConfigurationWorkflowService:
    """Thin trusted facade over the existing persistent command service."""

    def __init__(
        self,
        database: Database,
        *,
        commands: MasterConfigCommandService | None = None,
        repository: MasterConfigRepository | None = None,
        mapping_repository: MappingTemplateRepository | None = None,
    ) -> None:
        self._database = database
        self._repository = repository or MasterConfigRepository()
        self._mapping_repository = mapping_repository or MappingTemplateRepository()
        self._commands = commands or MasterConfigCommandService(
            database,
            repository=self._repository,
        )

    def snapshot(self, project_key: str) -> ConfigurationSnapshot:
        _request_exact(project_key, "project_key")
        try:
            with self._database.session() as session:
                model_rows = session.scalars(
                    select(CanonicalModelRow)
                    .where(CanonicalModelRow.project_key == project_key)
                    .order_by(CanonicalModelRow.model_key, CanonicalModelRow.id)
                ).all()
                supplier_rows = session.scalars(
                    select(CanonicalSupplierRow)
                    .where(CanonicalSupplierRow.project_key == project_key)
                    .order_by(CanonicalSupplierRow.supplier_key, CanonicalSupplierRow.id)
                ).all()
                part_rows = session.scalars(
                    select(CanonicalModelPartRow)
                    .where(CanonicalModelPartRow.project_key == project_key)
                    .order_by(CanonicalModelPartRow.model_part_key, CanonicalModelPartRow.id)
                ).all()
                item_rows = session.scalars(
                    select(CanonicalInspectionItemRow)
                    .where(CanonicalInspectionItemRow.project_key == project_key)
                    .order_by(CanonicalInspectionItemRow.item_key, CanonicalInspectionItemRow.id)
                ).all()
                models = tuple(
                    self._repository.get_model(
                        session, project_key=project_key, model_key=row.model_key
                    )
                    for row in model_rows
                )
                suppliers = tuple(
                    self._repository.get_supplier(
                        session, project_key=project_key, supplier_key=row.supplier_key
                    )
                    for row in supplier_rows
                )
                parts = tuple(
                    self._repository.get_model_part(
                        session, project_key=project_key, model_part_key=row.model_part_key
                    )
                    for row in part_rows
                )
                items = tuple(
                    self._repository.get_inspection_item(
                        session, project_key=project_key, item_key=row.item_key
                    )
                    for row in item_rows
                )
                masters = self._master_records(session, project_key)
                bindings = self._binding_records(session, project_key)
                mappings = self._approved_mapping_selections(session, project_key)
            return ConfigurationSnapshot(
                project_key=project_key,
                models=models,
                suppliers=suppliers,
                model_parts=parts,
                inspection_items=items,
                master_specs=masters,
                row_bindings=bindings,
                approved_mapping_revisions=mappings,
            )
        except ConfigurationWorkflowError:
            raise
        except (MasterConfigPayloadIntegrityError, MappingTemplatePayloadIntegrityError) as error:
            raise _conflict("CONFIGURATION_INTEGRITY_ERROR") from error
        except (MasterConfigNotFoundError, MappingTemplateNotFoundError) as error:
            raise _conflict("CONFIGURATION_REFERENCE_INTEGRITY_ERROR") from error
        except (TypeError, ValueError) as error:
            raise _conflict("CONFIGURATION_SNAPSHOT_INTEGRITY_ERROR") from error
        except (SQLAlchemyError, OSError, RuntimeError) as error:
            raise _unavailable("CONFIGURATION_SNAPSHOT_UNAVAILABLE") from error

    def create_model(self, request: CreateModelRequest) -> PersistedCanonicalModel:
        return self._mutate(
            lambda: self._commands.create_model(
                CreateCanonicalModelCommand(
                    model=CanonicalModel(
                        project_key=_request_exact(request.project_key, "project_key"),
                        model_key=_request_exact(request.model_key, "model_key"),
                        display_name=_request_exact(request.display_name, "display_name"),
                    ),
                    actor=LOCAL_OWNER,
                    reason=_request_exact(request.reason, "reason"),
                )
            )
        )

    def create_supplier(self, request: CreateSupplierRequest) -> PersistedCanonicalSupplier:
        return self._mutate(
            lambda: self._commands.create_supplier(
                CreateCanonicalSupplierCommand(
                    supplier=CanonicalSupplier(
                        project_key=_request_exact(request.project_key, "project_key"),
                        supplier_key=_request_exact(request.supplier_key, "supplier_key"),
                        display_name=_request_exact(request.display_name, "display_name"),
                    ),
                    actor=LOCAL_OWNER,
                    reason=_request_exact(request.reason, "reason"),
                )
            )
        )

    def create_model_part(self, request: CreateModelPartRequest) -> PersistedCanonicalModelPart:
        return self._mutate(
            lambda: self._commands.create_model_part(
                CreateCanonicalModelPartCommand(
                    model_part=CanonicalModelPart(
                        project_key=_request_exact(request.project_key, "project_key"),
                        model_key=_request_exact(request.model_key, "model_key"),
                        model_part_key=_request_exact(request.model_part_key, "model_part_key"),
                        display_name=_request_exact(request.display_name, "display_name"),
                    ),
                    actor=LOCAL_OWNER,
                    reason=_request_exact(request.reason, "reason"),
                )
            )
        )

    def create_inspection_item(
        self, request: CreateInspectionItemRequest
    ) -> PersistedCanonicalInspectionItem:
        return self._mutate(
            lambda: self._commands.create_inspection_item(
                CreateCanonicalInspectionItemCommand(
                    item=CanonicalInspectionItem(
                        project_key=_request_exact(request.project_key, "project_key"),
                        model_part_key=_request_exact(request.model_part_key, "model_part_key"),
                        item_key=_request_exact(request.item_key, "item_key"),
                        display_name=_request_exact(request.display_name, "display_name"),
                        disposition=InspectionItemDisposition.CANDIDATE,
                    ),
                    actor=LOCAL_OWNER,
                    reason=_request_exact(request.reason, "reason"),
                )
            )
        )

    def set_item_disposition(
        self, request: SetItemDispositionRequest
    ) -> PersistedCanonicalInspectionItem:
        _request_exact(request.project_key, "project_key")
        _request_exact(request.item_key, "item_key")
        _request_exact(request.reason, "reason")
        if request.disposition not in {
            InspectionItemDisposition.MANAGED,
            InspectionItemDisposition.EXCLUDED,
        }:
            raise _validation("CONFIGURATION_DISPOSITION_INVALID")
        if request.expected_row_version < 1:
            raise _validation("CONFIGURATION_ROW_VERSION_INVALID")
        try:
            with self._database.session() as session:
                current = self._repository.get_inspection_item(
                    session,
                    project_key=request.project_key,
                    item_key=request.item_key,
                )
            if current.item.disposition != InspectionItemDisposition.CANDIDATE:
                raise _conflict("CONFIGURATION_DISPOSITION_ALREADY_DECIDED")
        except ConfigurationWorkflowError:
            raise
        except MasterConfigNotFoundError as error:
            raise _not_found("CONFIGURATION_ITEM_NOT_FOUND") from error
        except (MasterConfigPayloadIntegrityError, MasterConfigScopeError) as error:
            raise _conflict("CONFIGURATION_ITEM_INTEGRITY_ERROR") from error
        except (SQLAlchemyError, OSError, RuntimeError) as error:
            raise _unavailable("CONFIGURATION_READ_UNAVAILABLE") from error
        return self._mutate(
            lambda: self._commands.set_item_disposition(
                SetInspectionItemDispositionCommand(
                    project_key=request.project_key,
                    item_key=request.item_key,
                    disposition=request.disposition,
                    expected_row_version=request.expected_row_version,
                    actor=LOCAL_OWNER,
                    reason=request.reason,
                )
            )
        )

    def create_master_spec_draft(
        self, request: CreateMasterSpecDraftRequest
    ) -> PersistedMasterSpecRevision:
        self._require_first_history(request.expected_history_row_version, "Master Spec")
        self._require_managed_item(request.project_key, request.canonical_item_key)
        try:
            spec = MasterSpecRevision(
                project_key=_request_exact(request.project_key, "project_key"),
                canonical_item_key=_request_exact(request.canonical_item_key, "canonical_item_key"),
                revision=_FIRST_REVISION,
                status=ConfigurationRevisionStatus.DRAFT,
                target=_parse_decimal(request.target, "target"),
                lsl=_parse_decimal(request.lsl, "lsl"),
                usl=_parse_decimal(request.usl, "usl"),
                unit=_request_exact(request.unit, "unit"),
                external_spec_revision=_request_exact(
                    request.external_spec_revision, "external_spec_revision"
                ),
                effective_from=request.effective_from,
                effective_to=request.effective_to,
                change_reason=_request_exact(request.reason, "reason"),
                source_reference=_request_exact(request.source_reference, "source_reference"),
            )
        except ConfigurationWorkflowError:
            raise
        except (TypeError, ValueError) as error:
            raise _validation("CONFIGURATION_MASTER_SPEC_INVALID") from error
        return self._mutate(
            lambda: self._commands.create_master_spec_revision(
                CreateMasterSpecRevisionCommand(
                    spec=spec,
                    expected_history_row_version=0,
                    actor=LOCAL_OWNER,
                    reason=request.reason,
                )
            )
        )

    def review_master_spec(self, request: MasterSpecWorkflowRequest) -> PersistedMasterSpecRevision:
        self._require_first_revision(request.revision, "Master Spec")
        return self._mutate(
            lambda: self._commands.review_master_spec_revision(
                ReviewMasterSpecRevisionCommand(
                    project_key=_request_exact(request.project_key, "project_key"),
                    canonical_item_key=_request_exact(
                        request.canonical_item_key, "canonical_item_key"
                    ),
                    revision=_FIRST_REVISION,
                    expected_history_row_version=request.expected_history_row_version,
                    expected_revision_row_version=request.expected_revision_row_version,
                    actor=LOCAL_OWNER,
                    reason=_request_exact(request.reason, "reason"),
                )
            )
        )

    def approve_master_spec(
        self, request: MasterSpecWorkflowRequest
    ) -> PersistedMasterSpecRevision:
        self._require_first_revision(request.revision, "Master Spec")
        return self._mutate(
            lambda: self._commands.approve_master_spec_revision(
                ApproveMasterSpecRevisionCommand(
                    project_key=_request_exact(request.project_key, "project_key"),
                    canonical_item_key=_request_exact(
                        request.canonical_item_key, "canonical_item_key"
                    ),
                    revision=_FIRST_REVISION,
                    expected_history_row_version=request.expected_history_row_version,
                    expected_revision_row_version=request.expected_revision_row_version,
                    actor=LOCAL_OWNER,
                    reason=_request_exact(request.reason, "reason"),
                )
            )
        )

    def create_row_binding_draft(
        self, request: CreateRowBindingDraftRequest
    ) -> PersistedCanonicalRowBindingRevision:
        self._require_first_history(request.expected_history_row_version, "row binding")
        for name, value in (
            ("project_key", request.project_key),
            ("supplier_scope", request.supplier_scope),
            ("template_id", request.template_id),
            ("row_key", request.row_key),
            ("canonical_model_key", request.canonical_model_key),
            ("canonical_supplier_key", request.canonical_supplier_key),
            ("canonical_model_part_key", request.canonical_model_part_key),
            ("canonical_item_key", request.canonical_item_key),
            ("reason", request.reason),
        ):
            _request_exact(value, name)
        if request.template_revision < 1:
            raise _validation("CONFIGURATION_MAPPING_REVISION_INVALID")
        self._require_decided_item(request.project_key, request.canonical_item_key)
        mapping, payload_sha256 = self._require_approved_mapping_row(request)
        source_reference = _binding_source_reference(mapping, payload_sha256, request.row_key)
        try:
            binding = CanonicalRowBindingRevision(
                key=CanonicalRowBindingKey(
                    project_key=request.project_key,
                    supplier_scope=request.supplier_scope,
                    template_id=request.template_id,
                    template_revision=request.template_revision,
                    row_key=request.row_key,
                ),
                binding_revision=_FIRST_REVISION,
                status=ConfigurationRevisionStatus.DRAFT,
                effective_from=request.effective_from,
                effective_to=request.effective_to,
                source_model_values=request.source_model_values,
                canonical_model_key=request.canonical_model_key,
                canonical_supplier_key=request.canonical_supplier_key,
                canonical_model_part_key=request.canonical_model_part_key,
                canonical_item_key=request.canonical_item_key,
                sample_policy=request.sample_policy,
                measurement_mode=request.measurement_mode,
                change_reason=request.reason,
                source_reference=source_reference,
            )
        except (TypeError, ValueError) as error:
            raise _validation("CONFIGURATION_ROW_BINDING_INVALID") from error
        return self._mutate(
            lambda: self._commands.create_row_binding_revision(
                CreateCanonicalRowBindingRevisionCommand(
                    binding=binding,
                    expected_history_row_version=0,
                    actor=LOCAL_OWNER,
                    reason=request.reason,
                )
            )
        )

    def review_row_binding(
        self, request: RowBindingWorkflowRequest
    ) -> PersistedCanonicalRowBindingRevision:
        self._require_first_revision(request.binding_revision, "row binding")
        return self._mutate(
            lambda: self._commands.review_row_binding_revision(
                ReviewCanonicalRowBindingRevisionCommand(
                    key=request.key,
                    binding_revision=_FIRST_REVISION,
                    expected_history_row_version=request.expected_history_row_version,
                    expected_revision_row_version=request.expected_revision_row_version,
                    actor=LOCAL_OWNER,
                    reason=_request_exact(request.reason, "reason"),
                )
            )
        )

    def approve_row_binding(
        self, request: RowBindingWorkflowRequest
    ) -> PersistedCanonicalRowBindingRevision:
        self._require_first_revision(request.binding_revision, "row binding")
        return self._mutate(
            lambda: self._commands.approve_row_binding_revision(
                ApproveCanonicalRowBindingRevisionCommand(
                    key=request.key,
                    binding_revision=_FIRST_REVISION,
                    expected_history_row_version=request.expected_history_row_version,
                    expected_revision_row_version=request.expected_revision_row_version,
                    actor=LOCAL_OWNER,
                    reason=_request_exact(request.reason, "reason"),
                )
            )
        )

    def _master_records(
        self, session: Session, project_key: str
    ) -> tuple[PersistedMasterSpecRevision, ...]:
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
            .where(MasterSpecHistoryRow.project_key == project_key)
            .order_by(
                CanonicalInspectionItemRow.item_key,
                MasterSpecRevisionRow.revision_number,
                MasterSpecRevisionRow.id,
            )
        ).all()
        return tuple(
            self._repository.get_master_spec(
                session,
                project_key=project_key,
                canonical_item_key=item.item_key,
                revision=revision.revision_number,
            )
            for _history, revision, item in rows
        )

    def _binding_records(
        self, session: Session, project_key: str
    ) -> tuple[PersistedCanonicalRowBindingRevision, ...]:
        rows = session.execute(
            select(CanonicalRowBindingHistoryRow, CanonicalRowBindingRevisionRow)
            .join(
                CanonicalRowBindingRevisionRow,
                (
                    CanonicalRowBindingRevisionRow.project_key
                    == CanonicalRowBindingHistoryRow.project_key
                )
                & (CanonicalRowBindingRevisionRow.history_id == CanonicalRowBindingHistoryRow.id),
            )
            .where(CanonicalRowBindingHistoryRow.project_key == project_key)
            .order_by(
                CanonicalRowBindingHistoryRow.supplier_scope,
                CanonicalRowBindingHistoryRow.template_id,
                CanonicalRowBindingHistoryRow.template_revision,
                CanonicalRowBindingHistoryRow.row_key,
                CanonicalRowBindingRevisionRow.binding_revision,
                CanonicalRowBindingRevisionRow.id,
            )
        ).all()
        return tuple(
            self._repository.get_row_binding(
                session,
                key=CanonicalRowBindingKey(
                    project_key=history.project_key,
                    supplier_scope=history.supplier_scope,
                    template_id=history.template_id,
                    template_revision=history.template_revision,
                    row_key=history.row_key,
                ),
                binding_revision=revision.binding_revision,
            )
            for history, revision in rows
        )

    def _approved_mapping_selections(
        self, session: Session, project_key: str
    ) -> tuple[ApprovedMappingSelection, ...]:
        rows = session.execute(
            select(MappingTemplateHistoryRow, MappingTemplateRevisionRow)
            .join(
                MappingTemplateRevisionRow,
                MappingTemplateRevisionRow.history_id == MappingTemplateHistoryRow.id,
            )
            .where(
                MappingTemplateHistoryRow.project_key == project_key,
                MappingTemplateRevisionRow.status == MappingTemplateStatus.APPROVED.value,
                MappingTemplateRevisionRow.schema_version == _SUPPORTED_UI_MAPPING_SCHEMA_VERSION,
            )
            .order_by(
                MappingTemplateHistoryRow.supplier_scope,
                MappingTemplateHistoryRow.template_id,
                MappingTemplateRevisionRow.revision_number,
                MappingTemplateRevisionRow.id,
            )
        ).all()
        selections: list[ApprovedMappingSelection] = []
        for history, row in rows:
            record = self._mapping_repository.get(
                session,
                project_key=project_key,
                supplier_scope=history.supplier_scope,
                template_id=history.template_id,
                revision=row.revision_number,
            )
            selections.append(_mapping_selection(record, row.payload_sha256))
        return tuple(selections)

    def _require_approved_mapping_row(
        self, request: CreateRowBindingDraftRequest
    ) -> tuple[PersistedMappingTemplate, str]:
        try:
            with self._database.session() as session:
                record = self._mapping_repository.get(
                    session,
                    project_key=request.project_key,
                    supplier_scope=request.supplier_scope,
                    template_id=request.template_id,
                    revision=request.template_revision,
                )
                row = session.get(MappingTemplateRevisionRow, record.revision_id)
                if row is None or row.history_id != record.history_id:
                    raise MappingTemplatePayloadIntegrityError(
                        "Mapping revision identity is inconsistent"
                    )
                if (
                    record.template.status != MappingTemplateStatus.APPROVED
                    or record.template.schema_version != _SUPPORTED_UI_MAPPING_SCHEMA_VERSION
                ):
                    raise ConfigurationWorkflowConflictError(
                        "CONFIGURATION_MAPPING_NOT_SELECTABLE",
                        "승인된 Mapping v2 행만 연결할 수 있습니다.",
                        "Mapping 선택 불가",
                    )
                matching_rows = tuple(
                    candidate
                    for candidate in record.template.inspection_rows
                    if candidate.row_key == request.row_key
                )
                if len(matching_rows) != 1:
                    raise ConfigurationWorkflowNotFoundError(
                        "CONFIGURATION_MAPPING_ROW_NOT_FOUND",
                        "선택한 Mapping 행을 해당 승인 리비전에서 찾을 수 없습니다.",
                        "Mapping 행 없음",
                    )
                return record, row.payload_sha256
        except ConfigurationWorkflowError:
            raise
        except MappingTemplateNotFoundError as error:
            raise _not_found("CONFIGURATION_MAPPING_NOT_FOUND") from error
        except MappingTemplatePayloadIntegrityError as error:
            raise _conflict("CONFIGURATION_MAPPING_INTEGRITY_ERROR") from error
        except (SQLAlchemyError, OSError, RuntimeError) as error:
            raise _unavailable("CONFIGURATION_MAPPING_READ_UNAVAILABLE") from error

    def _require_managed_item(self, project_key: str, item_key: str) -> None:
        item = self._read_item(project_key, item_key)
        if item.item.disposition != InspectionItemDisposition.MANAGED:
            raise _conflict("CONFIGURATION_MASTER_REQUIRES_MANAGED_ITEM")

    def _require_decided_item(self, project_key: str, item_key: str) -> None:
        item = self._read_item(project_key, item_key)
        if item.item.disposition == InspectionItemDisposition.CANDIDATE:
            raise _conflict("CONFIGURATION_BINDING_REQUIRES_DECIDED_ITEM")

    def _read_item(self, project_key: str, item_key: str) -> PersistedCanonicalInspectionItem:
        _request_exact(project_key, "project_key")
        _request_exact(item_key, "canonical_item_key")
        try:
            with self._database.session() as session:
                return self._repository.get_inspection_item(
                    session,
                    project_key=project_key,
                    item_key=item_key,
                )
        except MasterConfigNotFoundError as error:
            raise _not_found("CONFIGURATION_ITEM_NOT_FOUND") from error
        except (MasterConfigPayloadIntegrityError, MasterConfigScopeError) as error:
            raise _conflict("CONFIGURATION_ITEM_INTEGRITY_ERROR") from error
        except (SQLAlchemyError, OSError, RuntimeError) as error:
            raise _unavailable("CONFIGURATION_READ_UNAVAILABLE") from error

    @staticmethod
    def _require_first_history(value: int, subject: str) -> None:
        if value != 0:
            raise ConfigurationWorkflowConflictError(
                "CONFIGURATION_LATER_REVISION_UNSUPPORTED",
                f"{subject} 후속 리비전은 이번 화면에서 지원하지 않습니다.",
                "후속 리비전 미지원",
            )

    @staticmethod
    def _require_first_revision(value: int, subject: str) -> None:
        if value != _FIRST_REVISION:
            raise ConfigurationWorkflowConflictError(
                "CONFIGURATION_LATER_REVISION_UNSUPPORTED",
                f"{subject} 첫 리비전만 이번 화면에서 지원합니다.",
                "후속 리비전 미지원",
            )

    def _mutate(self, operation: Callable[[], _T]) -> _T:
        try:
            return operation()
        except ConfigurationWorkflowError:
            raise
        except MasterConfigNotFoundError as error:
            raise _not_found("CONFIGURATION_REFERENCE_NOT_FOUND") from error
        except StaleMasterConfigWriteError as error:
            raise _conflict("CONFIGURATION_STALE_VERSION") from error
        except (
            ImmutableMasterConfigRevisionError,
            MasterConfigEffectivePeriodError,
            MasterConfigPayloadIntegrityError,
            MasterConfigScopeError,
            StaleMappingTemplateWriteError,
        ) as error:
            raise _conflict("CONFIGURATION_COMMAND_CONFLICT") from error
        except MasterConfigAuthorizationError as error:
            raise _unavailable("CONFIGURATION_TRUST_BOUNDARY_FAILURE") from error
        except (TypeError, ValueError, InvalidOperation) as error:
            raise _validation("CONFIGURATION_REQUEST_INVALID") from error
        except (SQLAlchemyError, OSError, RuntimeError) as error:
            raise _unavailable("CONFIGURATION_COMMAND_UNAVAILABLE") from error


def _mapping_selection(
    record: PersistedMappingTemplate, payload_sha256: str
) -> ApprovedMappingSelection:
    template = record.template
    model_sources = tuple(
        identifier.source
        for identifier in template.identifiers
        if identifier.kind == IdentifierKind.MODEL
    )
    if len(model_sources) > 1:
        raise MappingTemplatePayloadIntegrityError("Mapping has duplicate MODEL identifiers")
    rows = tuple(
        MappingRowSelection(
            row_key=row.row_key,
            sheet_name=row.item.sheet_name,
            row_index=row.item.row_index,
            item_source=_cell(row.item),
            method_source=_optional_cell(row.method),
            instrument_source=_optional_cell(row.instrument),
            specification_source=_optional_cell(row.specification),
            tolerance_source=_optional_cell(row.tolerance),
            minimum_source=_optional_cell(row.minimum),
            maximum_source=_optional_cell(row.maximum),
            sample_cells=tuple(_cell(source) for source in row.sample_cells),
            supplier_result_source=_optional_cell(row.supplier_result),
            section_source=_optional_cell(row.section),
            category_source=_optional_cell(row.category),
            unit_source=_optional_cell(row.unit),
            measurement_point_source=_optional_cell(row.measurement_point),
            measurement_location_source=_optional_cell(row.measurement_location),
            cavity_source=_optional_cell(row.cavity),
            target_source=_optional_cell(row.target),
            lsl_source=_optional_cell(row.lsl),
            usl_source=_optional_cell(row.usl),
            source_spec_revision_source=_optional_cell(row.source_spec_revision),
        )
        for row in template.inspection_rows
    )
    return ApprovedMappingSelection(
        project_key=template.project_key,
        supplier_scope=template.supplier_scope,
        template_id=template.template_id,
        revision=template.revision,
        schema_version=template.schema_version,
        status=template.status,
        history_id=record.history_id,
        revision_id=record.revision_id,
        payload_sha256=payload_sha256,
        history_row_version=record.history_row_version,
        revision_row_version=record.revision_row_version,
        declared_effective_from=template.effective_from,
        declared_effective_to=template.effective_to,
        resolved_effective_to=record.resolved_effective_to,
        supplier_source_aliases=template.supplier_source_aliases,
        model_source=_cell(model_sources[0]) if model_sources else None,
        rows=rows,
    )


def _binding_source_reference(
    record: PersistedMappingTemplate, payload_sha256: str, row_key: str
) -> str:
    template = record.template
    return (
        "mapping-template:"
        f"{template.project_key}:{template.supplier_scope}:{template.template_id}:"
        f"{template.revision}:{row_key}:sha256:{payload_sha256}"
    )


def _cell(value: CellAddress) -> ConfigurationCellSource:
    return ConfigurationCellSource(
        sheet_name=value.sheet_name,
        coordinate=value.coordinate,
    )


def _optional_cell(value: CellAddress | None) -> ConfigurationCellSource | None:
    return _cell(value) if value is not None else None


def _parse_decimal(value: str | None, field_name: str) -> Decimal | None:
    if value is None:
        return None
    exact = _request_exact(value, field_name)
    try:
        parsed = Decimal(exact)
    except InvalidOperation as error:
        raise _validation("CONFIGURATION_DECIMAL_INVALID") from error
    if not parsed.is_finite():
        raise _validation("CONFIGURATION_DECIMAL_INVALID")
    return parsed


def _request_exact(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ConfigurationWorkflowValidationError(
            "CONFIGURATION_REQUEST_INVALID",
            f"{field_name} 값을 공백 없이 정확히 입력해 주세요.",
            "설정 요청 오류",
        )
    return value


def _validation(code: str) -> ConfigurationWorkflowValidationError:
    return ConfigurationWorkflowValidationError(
        code,
        "설정 입력값과 필수 확인 정보를 다시 확인해 주세요.",
        "설정 요청 오류",
    )


def _not_found(code: str) -> ConfigurationWorkflowNotFoundError:
    return ConfigurationWorkflowNotFoundError(
        code,
        "해당 프로젝트에서 선택한 설정 근거를 찾을 수 없습니다.",
        "설정 근거 없음",
    )


def _conflict(code: str) -> ConfigurationWorkflowConflictError:
    return ConfigurationWorkflowConflictError(
        code,
        "현재 설정 상태, 프로젝트 범위 또는 행 버전이 요청과 일치하지 않습니다.",
        "설정 충돌",
    )


def _unavailable(code: str) -> ConfigurationWorkflowUnavailableError:
    return ConfigurationWorkflowUnavailableError(
        code,
        "설정 서비스를 현재 사용할 수 없습니다. 잠시 후 다시 시도해 주세요.",
        "설정 서비스 준비 안 됨",
    )
