"""Isolated Korean synthetic OQC acceptance harness.

The harness composes only existing production boundaries.  Every database,
File Store, and generated workbook lives below pytest's temporary directory.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from scripts.build_korean_oqc_samples import build_korean_oqc_samples

from app.application.manual_ingestion import ManualWorkbookIngestionService
from app.application.mapping_preview import MappingTemplateCatalog
from app.application.mapping_template_commands import (
    ApproveMappingTemplateRevisionCommand,
    CreateMappingTemplateRevisionCommand,
    MappingTemplateCommandService,
    ReviewMappingTemplateRevisionCommand,
)
from app.application.store_scan_mapping import (
    ResolvedMappingScope,
    StoreScanMappingOutcome,
    StoreScanMappingRequest,
    StoreScanMappingService,
)
from app.domain.identity import Actor, ActorKind, Role
from app.domain.long_format import (
    CanonicalRowBinding,
    CanonicalRowBindingKey,
    CanonicalRowBindingStatus,
    MaterializedCanonicalRowBindingCatalog,
    MeasurementMode,
    SamplePolicy,
)
from app.domain.mapping import (
    CellAddress,
    HeaderTokenAssertion,
    IdentifierKind,
    IdentifierMapping,
    InspectionRowMapping,
    MappingTemplate,
    MappingTemplateStatus,
    MergeSignatureAssertion,
    RowStructureAssertion,
    SheetStructureAssertion,
    WorkbookFingerprint,
)
from app.domain.workbook_scan import ScanPolicy, SheetScan, WorkbookScan
from app.infrastructure.database import Base, Database
from app.infrastructure.excel import OpenpyxlWorkbookScanner
from app.infrastructure.file_store import XLSX_MIME, OriginalFileStore
from app.infrastructure.mapping_templates import (
    MappingTemplateRepository,
    PersistedMappingTemplate,
)

PROJECT_KEY = "mass-production-quality-validation-koqc-synthetic"
SUPPLIER_SCOPE = "synthetic-supplier-scope"
SOURCE_SUPPLIER = "가상정밀 주식회사"
SOURCE_MODEL = "DNX-가상-100"
BASELINE_LOT = "가상LOT-260815-A"
HISTORICAL_LOT = "가상LOT-260731-H"
REPORT_SHEET = "출하검사성적서"
TEMPLATE_ID = "korean-synthetic-oqc-standard"
WORKFLOW_TIME = datetime(2026, 8, 15, 9, 0, tzinfo=UTC)

BASELINE_IDENTIFIER_VALUES = {
    IdentifierKind.SUPPLIER: SOURCE_SUPPLIER,
    IdentifierKind.MODEL: SOURCE_MODEL,
    IdentifierKind.PART_NUMBER: "DNX-TRAY-가상-001",
    IdentifierKind.LOT_NUMBER: BASELINE_LOT,
    IdentifierKind.INSPECTION_DATE: datetime(2026, 8, 15),
    IdentifierKind.REVISION: "가상REV.C",
}

BASELINE_ROWS: tuple[tuple[str, MeasurementMode, tuple[str | float, ...]], ...] = (
    ("긁힘·찍힘", MeasurementMode.QUALITATIVE, ("이상없음",) * 8),
    ("버·날카로움", MeasurementMode.QUALITATIVE, ("이상없음",) * 8),
    (
        "전체 길이",
        MeasurementMode.NUMERIC,
        (399.8, 400.1, 400.0, 399.9, 400.2, 400.1, 399.9, 400.0),
    ),
    (
        "전체 폭",
        MeasurementMode.NUMERIC,
        (299.7, 300.0, 299.9, 300.1, 300.2, 299.8, 300.0, 300.1),
    ),
    (
        "전체 높이",
        MeasurementMode.NUMERIC,
        (25.0, 25.0, 25.0, 25.0, 25.0, 25.0, 25.0, 25.0),
    ),
    (
        "삽입 작동력",
        MeasurementMode.NUMERIC,
        (27.1, 26.8, 27.5, 28.0, 27.2, 27.6, 27.4, 27.0),
    ),
)

HISTORICAL_ROWS: tuple[tuple[str, MeasurementMode, tuple[str | float, ...]], ...] = (
    ("긁힘·찍힘", MeasurementMode.QUALITATIVE, ("이상없음",) * 8),
    ("버·날카로움", MeasurementMode.QUALITATIVE, ("이상없음",) * 8),
    (
        "전체 길이",
        MeasurementMode.NUMERIC,
        (399.77, 400.07, 399.97, 399.87, 400.17, 400.07, 399.87, 399.97),
    ),
    (
        "전체 폭",
        MeasurementMode.NUMERIC,
        (299.67, 299.97, 299.87, 300.07, 300.17, 299.77, 299.97, 300.07),
    ),
    (
        "전체 높이",
        MeasurementMode.NUMERIC,
        (24.97, 24.97, 24.97, 24.97, 24.97, 24.97, 24.97, 24.97),
    ),
    (
        "삽입 작동력",
        MeasurementMode.NUMERIC,
        (27.07, 26.77, 27.47, 27.97, 27.17, 27.57, 27.37, 26.97),
    ),
)

_IDENTIFIER_COORDINATES = {
    IdentifierKind.SUPPLIER: "B3",
    IdentifierKind.MODEL: "D3",
    IdentifierKind.PART_NUMBER: "H3",
    IdentifierKind.LOT_NUMBER: "B4",
    IdentifierKind.INSPECTION_DATE: "F4",
    IdentifierKind.REVISION: "H4",
}

_REVIEWER = Actor(
    actor_id="synthetic-koqc-reviewer",
    kind=ActorKind.LOCAL_OWNER,
    roles=frozenset({Role.REVIEWER}),
)
_ADMIN = Actor(
    actor_id="synthetic-koqc-admin",
    kind=ActorKind.LOCAL_OWNER,
    roles=frozenset({Role.ADMIN}),
)


@dataclass(slots=True)
class KoreanOqcAcceptanceContext:
    root: Path
    database_path: Path
    sample_paths: tuple[Path, ...]
    database: Database
    mapping: PersistedMappingTemplate
    catalog: MappingTemplateCatalog
    store: OriginalFileStore

    def execute(
        self,
        sample_index: int,
        *,
        model_candidates: tuple[str, ...] = (),
        lot_candidates: tuple[str, ...] = (),
    ) -> StoreScanMappingOutcome:
        route = StoreScanMappingService(
            ingestion_service=ManualWorkbookIngestionService(
                file_store=self.store,
                scanner=OpenpyxlWorkbookScanner(),
            ),
            registry=self.catalog,
        )
        return route.execute(
            StoreScanMappingRequest(
                scope=ResolvedMappingScope(
                    project_key=PROJECT_KEY,
                    supplier_scope=SUPPLIER_SCOPE,
                ),
                source=self.sample_paths[sample_index],
                declared_mime_type=XLSX_MIME,
                scan_policy=ScanPolicy(max_cells=50_000),
                model_candidates=model_candidates,
                lot_candidates=lot_candidates,
            )
        )

    def stored_bytes(self, outcome: StoreScanMappingOutcome) -> bytes:
        with self.store.open_source(outcome.receipt) as stream:
            return stream.read()

    def dispose(self) -> None:
        self.database.dispose()


def build_acceptance_context(tmp_path: Path) -> KoreanOqcAcceptanceContext:
    # Keep paths short enough for legacy Windows MAX_PATH consumers inside
    # openpyxl/File Store tests while retaining complete isolation in tmp_path.
    root = tmp_path / "k"
    sample_paths = build_korean_oqc_samples(root / "workbooks")
    baseline_scan = OpenpyxlWorkbookScanner().scan(
        sample_paths[0],
        ScanPolicy(max_cells=50_000),
    )
    database_path = root / "d.sqlite3"
    database = Database(f"sqlite+pysqlite:///{database_path.as_posix()}")
    Base.metadata.create_all(database.engine)
    repository = MappingTemplateRepository()
    mapping = _persist_approved_mapping(
        database,
        repository,
        _baseline_template(baseline_scan),
    )
    with database.session() as session:
        catalog: MappingTemplateCatalog = repository.load_catalog(
            session,
            project_key=PROJECT_KEY,
        )
    return KoreanOqcAcceptanceContext(
        root=root,
        database_path=database_path,
        sample_paths=sample_paths,
        database=database,
        mapping=mapping,
        catalog=catalog,
        store=OriginalFileStore(root / "s", max_bytes=8 * 1024 * 1024),
    )


def baseline_binding_catalog(template: MappingTemplate) -> MaterializedCanonicalRowBindingCatalog:
    bindings = tuple(
        CanonicalRowBinding(
            key=CanonicalRowBindingKey(
                project_key=PROJECT_KEY,
                supplier_scope=SUPPLIER_SCOPE,
                template_id=template.template_id,
                template_revision=template.revision,
                row_key=f"oqc-row-{ordinal:02d}",
            ),
            binding_revision=1,
            status=CanonicalRowBindingStatus.APPROVED,
            approved_by=_ADMIN.actor_id,
            approved_at=WORKFLOW_TIME,
            effective_from=date(2026, 1, 1),
            effective_to=date(2026, 12, 31),
            source_model_values=(SOURCE_MODEL,),
            canonical_model_key="synthetic:model:dnx-100",
            canonical_supplier_key="synthetic:supplier:virtual-precision",
            canonical_model_part_key="synthetic:part:tray-001",
            canonical_item_key=f"synthetic:item:{ordinal:02d}",
            sample_policy=SamplePolicy.AT_LEAST_ONE,
            measurement_mode=row[1],
        )
        for ordinal, row in enumerate(BASELINE_ROWS, start=1)
    )
    return MaterializedCanonicalRowBindingCatalog(bindings=bindings)


def report_cell(scan: WorkbookScan, coordinate: str) -> object:
    return scan_cell(scan, REPORT_SHEET, coordinate)


def scan_cell(scan: WorkbookScan, sheet_name: str, coordinate: str) -> object:
    sheet = scan_sheet(scan, sheet_name)
    matches = tuple(cell for cell in sheet.cells if cell.coordinate == coordinate)
    if len(matches) != 1:
        raise AssertionError(f"expected one {sheet_name}!{coordinate} cell, got {len(matches)}")
    return matches[0].stored_value


def scan_sheet(scan: WorkbookScan, name: str) -> SheetScan:
    return _sheet(scan, name)


def _baseline_template(scan: WorkbookScan) -> MappingTemplate:
    report = _sheet(scan, REPORT_SHEET)
    rows = tuple(_inspection_row(row_number) for row_number in range(8, 14))
    row_structures = tuple(
        RowStructureAssertion(
            row_key=row.row_key,
            sheet_name=REPORT_SHEET,
            row_index=row.item.row_index,
            expected_non_empty_cells=tuple(
                CellAddress(REPORT_SHEET, cell.coordinate)
                for cell in report.cells
                if _row_number(cell.coordinate) == row.item.row_index
            ),
        )
        for row in rows
    )
    return MappingTemplate(
        template_id=TEMPLATE_ID,
        schema_version="1",
        revision=1,
        status=MappingTemplateStatus.DRAFT,
        project_key=PROJECT_KEY,
        supplier_scope=SUPPLIER_SCOPE,
        supplier_source_aliases=(SOURCE_SUPPLIER,),
        approved_by=None,
        approved_at=None,
        effective_from=date(2026, 1, 1),
        effective_to=date(2026, 12, 31),
        fingerprint=WorkbookFingerprint(
            header_tokens=(
                HeaderTokenAssertion(
                    CellAddress(REPORT_SHEET, "A1"), "출하검사성적서 (합성 검증용)"
                ),
                HeaderTokenAssertion(CellAddress(REPORT_SHEET, "A3"), "업체명"),
                HeaderTokenAssertion(CellAddress(REPORT_SHEET, "A7"), "순번"),
                HeaderTokenAssertion(CellAddress(REPORT_SHEET, "C7"), "검사항목"),
                HeaderTokenAssertion(CellAddress(REPORT_SHEET, "H7"), "시료1"),
                HeaderTokenAssertion(CellAddress(REPORT_SHEET, "Q7"), "업체판정"),
            ),
            sheet_structures=tuple(
                SheetStructureAssertion(
                    sheet_name=sheet.name,
                    expected_position=sheet.position,
                    expected_kind=sheet.kind,
                    expected_visibility=sheet.visibility,
                    expected_used_range=sheet.used_range,
                )
                for sheet in scan.sheets
            ),
            merge_signatures=tuple(
                MergeSignatureAssertion(
                    sheet_name=sheet.name,
                    expected_merged_ranges=sheet.merged_ranges,
                )
                for sheet in scan.sheets
            ),
            row_structures=row_structures,
        ),
        identifiers=tuple(
            IdentifierMapping(kind, CellAddress(REPORT_SHEET, coordinate))
            for kind, coordinate in _IDENTIFIER_COORDINATES.items()
        ),
        inspection_rows=rows,
    )


def _inspection_row(row_number: int) -> InspectionRowMapping:
    ordinal = row_number - 7
    return InspectionRowMapping(
        row_key=f"oqc-row-{ordinal:02d}",
        item=CellAddress(REPORT_SHEET, f"C{row_number}"),
        method=CellAddress(REPORT_SHEET, f"F{row_number}"),
        specification=CellAddress(REPORT_SHEET, f"D{row_number}"),
        sample_cells=tuple(
            CellAddress(REPORT_SHEET, f"{column}{row_number}")
            for column in ("H", "I", "J", "K", "L", "M", "N", "O")
        ),
        supplier_result=CellAddress(REPORT_SHEET, f"Q{row_number}"),
    )


def _persist_approved_mapping(
    database: Database,
    repository: MappingTemplateRepository,
    draft: MappingTemplate,
) -> PersistedMappingTemplate:
    commands = MappingTemplateCommandService(
        database,
        repository=repository,
        clock=lambda: WORKFLOW_TIME,
    )
    created = commands.create_revision(
        CreateMappingTemplateRevisionCommand(
            template=draft,
            expected_history_row_version=0,
            actor=_REVIEWER,
            reason="Register the source-verified Korean synthetic OQC mapping.",
            source_reference="synthetic-korean-oqc-baseline",
        )
    )
    reviewed = commands.review(
        ReviewMappingTemplateRevisionCommand(
            project_key=PROJECT_KEY,
            supplier_scope=SUPPLIER_SCOPE,
            template_id=TEMPLATE_ID,
            revision=1,
            expected_history_row_version=created.history_row_version,
            expected_revision_row_version=created.revision_row_version,
            actor=_REVIEWER,
            reason="Review identifiers and every mapped row/sample source cell.",
        )
    )
    return commands.approve(
        ApproveMappingTemplateRevisionCommand(
            project_key=PROJECT_KEY,
            supplier_scope=SUPPLIER_SCOPE,
            template_id=TEMPLATE_ID,
            revision=1,
            expected_history_row_version=reviewed.history_row_version,
            expected_revision_row_version=reviewed.revision_row_version,
            actor=_ADMIN,
            reason="Approve the reviewed synthetic Mapping Template revision.",
        )
    )


def _sheet(scan: WorkbookScan, name: str) -> SheetScan:
    matches = tuple(sheet for sheet in scan.sheets if sheet.name == name)
    if len(matches) != 1:
        raise AssertionError(f"expected one {name!r} sheet, got {len(matches)}")
    return matches[0]


def _row_number(coordinate: str) -> int:
    return int("".join(character for character in coordinate if character.isdigit()))
