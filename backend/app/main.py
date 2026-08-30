"""FastAPI application factory for the local Mass Production Quality Validation process."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Protocol

from fastapi import FastAPI, HTTPException, status
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from app.api import (
    create_bulk_finalization_router,
    create_bulk_router,
    create_configuration_router,
    create_data_review_router,
    create_historical_comparison_router,
    create_intake_router,
    create_long_router,
    create_mapping_registration_router,
    create_mapping_router,
    create_result_replacement_router,
)
from app.api.bulk import BulkImportPort
from app.api.bulk_finalization import BulkFinalizationPort
from app.api.configuration import ConfigurationWorkflowPort
from app.api.data_review import DataReviewWorkflowPort
from app.api.historical_comparison import HistoricalComparisonPort
from app.api.long import LongWorkflowPort
from app.api.mapping import MappingRegistrationPort, MappingWorkspacePort
from app.api.result_replacement import ResultReplacementPort
from app.application.bulk_finalization import (
    BulkFinalizationManager,
    BulkFinalizationUnavailableError,
    SubmitBulkFinalizationRequest,
)
from app.application.bulk_import import (
    BulkImportManager,
    BulkImportUnavailableError,
    BulkSubmitRequest,
)
from app.application.configuration_workflow import (
    ConfigurationSnapshot,
    ConfigurationWorkflowService,
    ConfigurationWorkflowUnavailableError,
    CreateInspectionItemRequest,
    CreateMasterSpecDraftRequest,
    CreateModelPartRequest,
    CreateModelRequest,
    CreateRowBindingDraftRequest,
    CreateSupplierRequest,
    MasterSpecWorkflowRequest,
    RowBindingWorkflowRequest,
    SetItemDispositionRequest,
)
from app.application.data_review_workflow import (
    DataReviewCandidateRequest,
    DataReviewTargetList,
    DataReviewTargetsRequest,
    DataReviewWorkflowService,
    DataReviewWorkflowUnavailableError,
    DecideDataReviewRequest,
)
from app.application.historical_comparison import (
    HistoricalComparison,
    HistoricalComparisonError,
    HistoricalComparisonRequest,
    HistoricalComparisonService,
)
from app.application.intake_jobs import IntakeJobManager
from app.application.long_workflow import (
    ConfirmLongCandidateRequest,
    LongCandidateRequest,
    LongWorkflowResult,
    LongWorkflowService,
    LongWorkflowUnavailableError,
)
from app.application.manual_ingestion import ManualWorkbookIngestionService
from app.application.mapping_registration import (
    CreateMappingDraftRequest,
    MappingRegistrationResult,
    MappingRegistrationService,
    MappingRegistrationUnavailableError,
    MappingWorkflowRequest,
)
from app.application.mapping_workspace import (
    MappingWorkspaceRequest,
    MappingWorkspaceService,
    MappingWorkspaceSnapshot,
    MappingWorkspaceSourceError,
)
from app.application.result_replacement import (
    DecideResultReplacementCommand,
    ReplacementCandidateRequest,
    ResultReplacementService,
    ResultReplacementUnavailableError,
)
from app.config import AppSettings, get_settings
from app.domain.bulk_finalization import BulkFinalizationCandidate, BulkFinalizationSnapshot
from app.domain.bulk_import import BulkBatchSnapshot, BulkLimits
from app.domain.data_review import DataReviewCandidate
from app.domain.result_replacement import (
    PersistedReplacementDecision,
    ResultReplacementCandidate,
)
from app.domain.workbook_scan import ScanPolicy
from app.infrastructure.data_review import PersistedDataStatusDecision
from app.infrastructure.database import Database
from app.infrastructure.excel.workbook_scanner import OpenpyxlWorkbookScanner
from app.infrastructure.file_store import OriginalFileStore
from app.infrastructure.master_config import (
    PersistedCanonicalInspectionItem,
    PersistedCanonicalModel,
    PersistedCanonicalModelPart,
    PersistedCanonicalRowBindingRevision,
    PersistedCanonicalSupplier,
    PersistedMasterSpecRevision,
)
from app.version import __version__


class DatabaseHealthPort(Protocol):
    def check(self) -> None: ...

    def dispose(self) -> None: ...


class LiveResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str
    version: str


class ReadyResponse(LiveResponse):
    database: Literal["ready"] = "ready"


def create_app(
    *,
    settings: AppSettings | None = None,
    database: DatabaseHealthPort | None = None,
    intake_manager: IntakeJobManager | None = None,
    mapping_workspace: MappingWorkspacePort | None = None,
    mapping_registration: MappingRegistrationPort | None = None,
    long_workflow: LongWorkflowPort | None = None,
    data_review_workflow: DataReviewWorkflowPort | None = None,
    configuration_workflow: ConfigurationWorkflowPort | None = None,
    bulk_manager: BulkImportManager | None = None,
    bulk_finalization: BulkFinalizationPort | None = None,
    historical_comparison: HistoricalComparisonPort | None = None,
    result_replacement: ResultReplacementPort | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()
    resolved_database = database or Database(resolved_settings.database_url)
    resolved_intake = intake_manager or _create_intake_manager(resolved_settings)
    resolved_mapping = mapping_workspace or _create_mapping_workspace(
        resolved_settings,
        resolved_database,
    )
    resolved_mapping_registration = mapping_registration or _create_mapping_registration(
        resolved_settings,
        resolved_database,
    )
    resolved_long = long_workflow or _create_long_workflow(
        resolved_settings,
        resolved_database,
    )
    resolved_data_review = data_review_workflow or _create_data_review_workflow(
        resolved_database,
    )
    resolved_configuration = configuration_workflow or _create_configuration_workflow(
        resolved_database,
    )
    resolved_bulk: BulkImportPort = bulk_manager or _create_bulk_manager(
        resolved_settings,
        resolved_database,
    )
    resolved_bulk_finalization = bulk_finalization or _create_bulk_finalization(
        resolved_settings,
        resolved_database,
    )
    resolved_historical_comparison = historical_comparison or _create_historical_comparison(
        resolved_database
    )
    resolved_result_replacement = result_replacement or _create_result_replacement(
        resolved_database
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        bulk_started = False
        finalization_started = False
        try:
            resolved_intake.start()
            if isinstance(resolved_bulk, BulkImportManager):
                try:
                    resolved_database.check()
                except (SQLAlchemyError, OSError, RuntimeError):
                    # Liveness and the readiness error response must remain
                    # available while an empty or stale database is awaiting
                    # migration. The durable worker is started only after the
                    # complete schema is ready.
                    pass
                else:
                    resolved_bulk.start()
                    bulk_started = True
                    if isinstance(resolved_bulk_finalization, BulkFinalizationManager):
                        resolved_bulk_finalization.start()
                        finalization_started = True
            yield
        finally:
            try:
                if finalization_started and isinstance(
                    resolved_bulk_finalization, BulkFinalizationManager
                ):
                    resolved_bulk_finalization.shutdown()
            finally:
                try:
                    if bulk_started and isinstance(resolved_bulk, BulkImportManager):
                        resolved_bulk.shutdown()
                finally:
                    resolved_intake.shutdown()
                    resolved_database.dispose()

    application = FastAPI(
        title=resolved_settings.app_name,
        version=__version__,
        lifespan=lifespan,
    )
    application.include_router(create_intake_router(resolved_intake))
    application.include_router(create_mapping_router(resolved_mapping))
    application.include_router(create_mapping_registration_router(resolved_mapping_registration))
    application.include_router(create_long_router(resolved_long))
    application.include_router(create_data_review_router(resolved_data_review))
    application.include_router(create_configuration_router(resolved_configuration))
    application.include_router(
        create_bulk_router(resolved_bulk, staging_root=resolved_settings.bulk_staging_root)
    )
    application.include_router(create_bulk_finalization_router(resolved_bulk_finalization))
    application.include_router(create_historical_comparison_router(resolved_historical_comparison))
    application.include_router(create_result_replacement_router(resolved_result_replacement))

    @application.get("/api/v1/health/live", response_model=LiveResponse)
    def live() -> LiveResponse:
        return LiveResponse(service=resolved_settings.app_name, version=__version__)

    @application.get("/api/v1/health/ready", response_model=ReadyResponse)
    def ready() -> ReadyResponse:
        try:
            resolved_database.check()
        except (SQLAlchemyError, OSError, RuntimeError) as error:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"status": "unavailable", "component": "database"},
            ) from error
        return ReadyResponse(service=resolved_settings.app_name, version=__version__)

    frontend_dist = _resolve_frontend_dist(resolved_settings.frontend_dist_path)
    if (frontend_dist / "index.html").is_file():
        application.mount(
            "/",
            StaticFiles(directory=str(frontend_dist), html=True),
            name="frontend",
        )

    return application


def _create_intake_manager(settings: AppSettings) -> IntakeJobManager:
    file_store = OriginalFileStore(
        settings.original_file_store_root,
        max_bytes=settings.max_upload_bytes,
    )
    ingestion_service = ManualWorkbookIngestionService(
        file_store=file_store,
        scanner=OpenpyxlWorkbookScanner(),
    )
    return IntakeJobManager(
        ingestion_service=ingestion_service,
        staging_root=settings.intake_staging_root,
        max_upload_bytes=settings.max_upload_bytes,
        queue_capacity=settings.intake_queue_capacity,
        registry_capacity=settings.intake_registry_capacity,
        scan_policy=ScanPolicy(),
    )


def _create_mapping_workspace(
    settings: AppSettings,
    database: DatabaseHealthPort,
) -> MappingWorkspacePort:
    if not isinstance(database, Database):
        return _UnavailableMappingWorkspace()
    return MappingWorkspaceService(
        database=database,
        file_store=OriginalFileStore(
            settings.original_file_store_root,
            max_bytes=settings.max_upload_bytes,
        ),
        scanner=OpenpyxlWorkbookScanner(),
        scan_policy=ScanPolicy(),
    )


def _create_mapping_registration(
    settings: AppSettings,
    database: DatabaseHealthPort,
) -> MappingRegistrationPort:
    if not isinstance(database, Database):
        return _UnavailableMappingRegistration()
    return MappingRegistrationService(
        database=database,
        file_store=OriginalFileStore(
            settings.original_file_store_root,
            max_bytes=settings.max_upload_bytes,
        ),
        scanner=OpenpyxlWorkbookScanner(),
        scan_policy=ScanPolicy(),
    )


def _create_long_workflow(
    settings: AppSettings,
    database: DatabaseHealthPort,
) -> LongWorkflowPort:
    if not isinstance(database, Database):
        return _UnavailableLongWorkflow()
    mapping_workspace = MappingWorkspaceService(
        database=database,
        file_store=OriginalFileStore(
            settings.original_file_store_root,
            max_bytes=settings.max_upload_bytes,
        ),
        scanner=OpenpyxlWorkbookScanner(),
        scan_policy=ScanPolicy(),
    )
    return LongWorkflowService(
        database=database,
        mapping_workspace=mapping_workspace,
    )


def _create_data_review_workflow(
    database: DatabaseHealthPort,
) -> DataReviewWorkflowPort:
    if not isinstance(database, Database):
        return _UnavailableDataReviewWorkflow()
    return DataReviewWorkflowService(database)


def _create_configuration_workflow(
    database: DatabaseHealthPort,
) -> ConfigurationWorkflowPort:
    if not isinstance(database, Database):
        return _UnavailableConfigurationWorkflow()
    return ConfigurationWorkflowService(database)


def _create_bulk_manager(
    settings: AppSettings,
    database: DatabaseHealthPort,
) -> BulkImportPort:
    if not isinstance(database, Database):
        return _UnavailableBulkImport(
            BulkLimits(
                settings.bulk_max_files,
                settings.max_upload_bytes,
                settings.bulk_max_batch_bytes,
            )
        )
    file_store = OriginalFileStore(
        settings.original_file_store_root,
        max_bytes=settings.max_upload_bytes,
    )
    scanner = OpenpyxlWorkbookScanner()
    ingestion = ManualWorkbookIngestionService(file_store=file_store, scanner=scanner)
    mapping = MappingWorkspaceService(
        database=database,
        file_store=file_store,
        scanner=scanner,
        scan_policy=ScanPolicy(),
    )
    long_workflow = LongWorkflowService(database=database, mapping_workspace=mapping)
    return BulkImportManager(
        database=database,
        ingestion_service=ingestion,
        mapping_workspace=mapping,
        long_workflow=long_workflow,
        staging_root=settings.bulk_staging_root,
        max_files=settings.bulk_max_files,
        max_file_bytes=settings.max_upload_bytes,
        max_batch_bytes=settings.bulk_max_batch_bytes,
        queue_capacity=settings.bulk_queue_capacity,
        scan_policy=ScanPolicy(),
    )


def _create_bulk_finalization(
    settings: AppSettings,
    database: DatabaseHealthPort,
) -> BulkFinalizationPort:
    if not isinstance(database, Database):
        return _UnavailableBulkFinalization()
    long_workflow = _create_long_workflow(settings, database)
    if not isinstance(long_workflow, LongWorkflowService):
        return _UnavailableBulkFinalization()
    return BulkFinalizationManager(database=database, long_workflow=long_workflow)


def _create_historical_comparison(
    database: DatabaseHealthPort,
) -> HistoricalComparisonPort:
    if not isinstance(database, Database):
        return _UnavailableHistoricalComparison()
    return HistoricalComparisonService(database)


def _create_result_replacement(
    database: DatabaseHealthPort,
) -> ResultReplacementPort:
    if not isinstance(database, Database):
        return _UnavailableResultReplacement()
    return ResultReplacementService(database)


class _UnavailableMappingWorkspace:
    """Fail closed when a health-only DB test double has no Mapping session."""

    def preview(self, request: MappingWorkspaceRequest) -> MappingWorkspaceSnapshot:
        del request
        raise MappingWorkspaceSourceError("MAPPING_SERVICE_UNAVAILABLE")


class _UnavailableMappingRegistration:
    """Fail closed when a health-only DB test double has no Mapping session."""

    def create_draft(self, request: CreateMappingDraftRequest) -> MappingRegistrationResult:
        del request
        raise _mapping_registration_unavailable()

    def review(
        self,
        *,
        template_id: str,
        revision: int,
        request: MappingWorkflowRequest,
    ) -> MappingRegistrationResult:
        del template_id, revision, request
        raise _mapping_registration_unavailable()

    def approve(
        self,
        *,
        template_id: str,
        revision: int,
        request: MappingWorkflowRequest,
    ) -> MappingRegistrationResult:
        del template_id, revision, request
        raise _mapping_registration_unavailable()


def _mapping_registration_unavailable() -> MappingRegistrationUnavailableError:
    return MappingRegistrationUnavailableError(
        "MAPPING_SERVICE_UNAVAILABLE",
        "매핑 등록 서비스를 사용할 수 없습니다.",
        "매핑 서비스 준비 안 됨",
    )


class _UnavailableLongWorkflow:
    """Fail closed when a health-only DB test double has no Long session."""

    def candidate(self, request: LongCandidateRequest) -> LongWorkflowResult:
        del request
        raise _long_workflow_unavailable()

    def confirm(self, request: ConfirmLongCandidateRequest) -> LongWorkflowResult:
        del request
        raise _long_workflow_unavailable()


class _UnavailableDataReviewWorkflow:
    """Fail closed when a health-only DB test double has no review session."""

    def targets(self, request: DataReviewTargetsRequest) -> DataReviewTargetList:
        del request
        raise _data_review_workflow_unavailable()

    def candidate(self, request: DataReviewCandidateRequest) -> DataReviewCandidate:
        del request
        raise _data_review_workflow_unavailable()

    def decide(self, request: DecideDataReviewRequest) -> PersistedDataStatusDecision:
        del request
        raise _data_review_workflow_unavailable()


class _UnavailableConfigurationWorkflow:
    """Fail closed when a health-only DB test double has no configuration session."""

    def snapshot(self, project_key: str) -> ConfigurationSnapshot:
        del project_key
        raise _configuration_workflow_unavailable()

    def create_model(self, request: CreateModelRequest) -> PersistedCanonicalModel:
        del request
        raise _configuration_workflow_unavailable()

    def create_supplier(self, request: CreateSupplierRequest) -> PersistedCanonicalSupplier:
        del request
        raise _configuration_workflow_unavailable()

    def create_model_part(self, request: CreateModelPartRequest) -> PersistedCanonicalModelPart:
        del request
        raise _configuration_workflow_unavailable()

    def create_inspection_item(
        self, request: CreateInspectionItemRequest
    ) -> PersistedCanonicalInspectionItem:
        del request
        raise _configuration_workflow_unavailable()

    def set_item_disposition(
        self, request: SetItemDispositionRequest
    ) -> PersistedCanonicalInspectionItem:
        del request
        raise _configuration_workflow_unavailable()

    def create_master_spec_draft(
        self, request: CreateMasterSpecDraftRequest
    ) -> PersistedMasterSpecRevision:
        del request
        raise _configuration_workflow_unavailable()

    def review_master_spec(self, request: MasterSpecWorkflowRequest) -> PersistedMasterSpecRevision:
        del request
        raise _configuration_workflow_unavailable()

    def approve_master_spec(
        self, request: MasterSpecWorkflowRequest
    ) -> PersistedMasterSpecRevision:
        del request
        raise _configuration_workflow_unavailable()

    def create_row_binding_draft(
        self, request: CreateRowBindingDraftRequest
    ) -> PersistedCanonicalRowBindingRevision:
        del request
        raise _configuration_workflow_unavailable()

    def review_row_binding(
        self, request: RowBindingWorkflowRequest
    ) -> PersistedCanonicalRowBindingRevision:
        del request
        raise _configuration_workflow_unavailable()

    def approve_row_binding(
        self, request: RowBindingWorkflowRequest
    ) -> PersistedCanonicalRowBindingRevision:
        del request
        raise _configuration_workflow_unavailable()


class _UnavailableBulkImport:
    def __init__(self, limits: BulkLimits) -> None:
        self._limits = limits

    @property
    def limits(self) -> BulkLimits:
        return self._limits

    def submit(self, request: BulkSubmitRequest) -> BulkBatchSnapshot:
        del request
        raise _bulk_unavailable()

    def get(self, *, project_key: str, batch_id: str) -> BulkBatchSnapshot:
        del project_key, batch_id
        raise _bulk_unavailable()


class _UnavailableBulkFinalization:
    def candidate(self, *, project_key: str, batch_id: str) -> BulkFinalizationCandidate:
        del project_key, batch_id
        raise _bulk_finalization_unavailable()

    def submit(self, request: SubmitBulkFinalizationRequest) -> BulkFinalizationSnapshot:
        del request
        raise _bulk_finalization_unavailable()

    def get(self, *, project_key: str, batch_id: str) -> BulkFinalizationSnapshot:
        del project_key, batch_id
        raise _bulk_finalization_unavailable()


class _UnavailableHistoricalComparison:
    def compare(self, request: HistoricalComparisonRequest) -> HistoricalComparison:
        del request
        raise _historical_comparison_unavailable()


class _UnavailableResultReplacement:
    """Fail closed when a health-only DB test double has no replacement session."""

    def candidate(
        self,
        request: ReplacementCandidateRequest,
    ) -> ResultReplacementCandidate:
        del request
        raise _result_replacement_unavailable()

    def decide(
        self,
        command: DecideResultReplacementCommand,
    ) -> PersistedReplacementDecision:
        del command
        raise _result_replacement_unavailable()

    def get(
        self,
        *,
        project_key: str,
        replacement_id: str,
    ) -> PersistedReplacementDecision:
        del project_key, replacement_id
        raise _result_replacement_unavailable()


def _bulk_unavailable() -> BulkImportUnavailableError:
    return BulkImportUnavailableError(
        "BULK_SERVICE_UNAVAILABLE",
        "일괄 등록 서비스를 사용할 수 없습니다.",
        "일괄 등록 준비 필요",
    )


def _bulk_finalization_unavailable() -> BulkFinalizationUnavailableError:
    return BulkFinalizationUnavailableError(
        "BULK_FINALIZATION_SERVICE_UNAVAILABLE",
        "일괄 반영 서비스를 사용할 수 없습니다.",
        "일괄 반영 준비 필요",
    )


def _historical_comparison_unavailable() -> HistoricalComparisonError:
    return HistoricalComparisonError(
        "HISTORICAL_COMPARISON_SERVICE_UNAVAILABLE",
        "과거 원본 근거 비교 서비스를 사용할 수 없습니다.",
        "과거 비교 준비 필요",
    )


def _result_replacement_unavailable() -> ResultReplacementUnavailableError:
    return ResultReplacementUnavailableError(
        "RESULT_REPLACEMENT_SERVICE_UNAVAILABLE",
        "수정본 연결 서비스를 사용할 수 없습니다.",
        "수정본 연결 준비 필요",
    )


def _long_workflow_unavailable() -> LongWorkflowUnavailableError:
    return LongWorkflowUnavailableError(
        "LONG_SERVICE_UNAVAILABLE",
        "Long 후보 서비스를 사용할 수 없습니다.",
        "Long 서비스 준비 안 됨",
    )


def _data_review_workflow_unavailable() -> DataReviewWorkflowUnavailableError:
    return DataReviewWorkflowUnavailableError(
        "DATA_REVIEW_SERVICE_UNAVAILABLE",
        "데이터상태 검토 서비스를 사용할 수 없습니다.",
        "데이터상태 검토 준비 안 됨",
    )


def _configuration_workflow_unavailable() -> ConfigurationWorkflowUnavailableError:
    return ConfigurationWorkflowUnavailableError(
        "CONFIGURATION_SERVICE_UNAVAILABLE",
        "설정 서비스를 사용할 수 없습니다.",
        "설정 서비스 준비 안 됨",
    )


def _resolve_frontend_dist(configured_path: Path) -> Path:
    return configured_path.resolve()


app = create_app()
