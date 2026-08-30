from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import func, select, update

from app.application.long_candidate import build_long_candidate
from app.application.long_persistence import LongPersistenceRequest, LongPersistenceService
from app.application.mapping_preview import InMemoryMappingTemplateRegistry, build_mapping_preview
from app.application.mapping_template_commands import (
    ApproveMappingTemplateRevisionCommand,
    CreateMappingTemplateRevisionCommand,
    MappingTemplateCommandService,
    ReviewMappingTemplateRevisionCommand,
)
from app.application.store_scan_mapping import (
    ResolvedMappingScope,
    StoreScanMappingOutcome,
    StoreScanMappingStatus,
)
from app.domain.identity import Actor, ActorKind, Role
from app.domain.long_format import (
    CanonicalRowBinding,
    CanonicalRowBindingKey,
    CanonicalRowBindingStatus,
    LongCandidateState,
    LongDataStatus,
    MaterializedCanonicalRowBindingCatalog,
    MeasurementMode,
    SamplePolicy,
    SpecEvaluationStatus,
    UnitConversionStatus,
)
from app.domain.mapping import (
    CellAddress,
    HeaderTokenAssertion,
    IdentifierKind,
    IdentifierMapping,
    InspectionRowMapping,
    MappingPreview,
    MappingPreviewRequest,
    MappingPreviewState,
    MappingTemplate,
    MappingTemplateStatus,
    MergeSignatureAssertion,
    PreviewValueKind,
    RowStructureAssertion,
    SheetStructureAssertion,
    SystemJudgmentStatus,
    WorkbookFingerprint,
)
from app.domain.source_file import SourceFileReceipt
from app.domain.workbook_scan import (
    CellEvidence,
    DisplayValueStatus,
    MacroHandling,
    SheetKind,
    SheetProtectionMetadata,
    SheetScan,
    WorkbookScan,
    WorkbookScanState,
)
from app.infrastructure.database import Base, Database
from app.infrastructure.long_format import (
    LongFormatRepository,
    LongIngestionJobRow,
    LongInspectionResultRow,
    LongJobStatus,
    LongMeasurementRow,
    OqcLotRow,
    canonical_json_sha256,
    serialize_long_candidate,
)
from app.infrastructure.mapping_templates import (
    MappingTemplatePayloadIntegrityError,
    MappingTemplateRepository,
    MappingTemplateRevisionRow,
    PersistedMappingTemplate,
    _payload_digest,
    _serialize_template_payload,
)

_SHEET = "OQC V2"
_PROJECT = "project-mapping-v2"
_SUPPLIER_SCOPE = "supplier-mapping-v2"
_SUPPLIER = "SUPPLIER-V2"
_MODEL = "MODEL-V2"
_LOT = "LOT-V2"
_INSPECTION_DATE = date(2026, 6, 15)
_NOW = datetime(2026, 8, 15, 10, 0, tzinfo=UTC)
_HASH = "b" * 64
_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

_V1_MAPPING_ROW_KEYS = {
    "row_key",
    "item",
    "method",
    "instrument",
    "specification",
    "tolerance",
    "minimum",
    "maximum",
    "sample_cells",
    "supplier_result",
}
_V2_MAPPING_ROW_KEYS = _V1_MAPPING_ROW_KEYS | {
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
}
_V1_LONG_ROW_KEYS = {
    "row_key",
    "state",
    "binding",
    "item",
    "method",
    "instrument",
    "specification",
    "tolerance",
    "minimum",
    "maximum",
    "measurements",
    "supplier_judgment",
    "issues",
    "data_status",
    "system_judgment_status",
    "system_judgment",
    "spec_evaluation_status",
}
_V2_LONG_EVIDENCE_KEYS = {
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
}
_V1_SOURCE_EVIDENCE_KEYS = {
    "item",
    "method",
    "instrument",
    "specification",
    "tolerance",
    "minimum",
    "maximum",
    "supplier_judgment",
}

_REVIEWER = Actor(
    actor_id="mapping-v2-reviewer",
    kind=ActorKind.LOCAL_OWNER,
    roles=frozenset({Role.REVIEWER}),
)
_ADMIN = Actor(
    actor_id="mapping-v2-admin",
    kind=ActorKind.LOCAL_OWNER,
    roles=frozenset({Role.ADMIN}),
)


def _address(coordinate: str) -> CellAddress:
    return CellAddress(_SHEET, coordinate)


def _cell(coordinate: str, value: object) -> CellEvidence:
    if isinstance(value, (date, datetime)):
        data_type = "d"
        number_format = "yyyy-mm-dd"
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        data_type = "n"
        number_format = "0.000"
    else:
        data_type = "s"
        number_format = "General"
    return CellEvidence(
        coordinate=coordinate,
        stored_value=value,
        cached_value=None,
        formula_text=None,
        number_format=number_format,
        data_type=data_type,
        display_value=None,
        display_value_status=DisplayValueStatus.NOT_RENDERED,
    )


def _row_values(row_number: int, *, variant: int = 1) -> dict[str, object]:
    if variant == 1:
        context = ("mm", "P-01", "중앙", "CAV-A", 2.0, 1.9, 2.1, 2.01)
    else:
        context = ("μm", "P-02", "가장자리", "CAV-B", 2000, 1900, 2100, 2010)
    unit, point, location, cavity, target, lsl, usl, sample = context
    return {
        f"A{row_number}": "두께",
        f"B{row_number}": "치수검사",
        f"C{row_number}": "CTQ",
        f"D{row_number}": "마이크로미터 측정",
        f"E{row_number}": "GAUGE-VIRTUAL",
        f"F{row_number}": "1.90 ~ 2.10",
        f"G{row_number}": "±0.10",
        f"H{row_number}": 1.9,
        f"I{row_number}": 2.1,
        f"J{row_number}": unit,
        f"K{row_number}": point,
        f"L{row_number}": location,
        f"M{row_number}": cavity,
        f"N{row_number}": target,
        f"O{row_number}": lsl,
        f"P{row_number}": usl,
        f"Q{row_number}": "SPEC-R2",
        f"R{row_number}": sample,
        f"S{row_number}": "SUPPLIER-PASS",
    }


def _scan(*, include_second: bool = False) -> WorkbookScan:
    values: dict[str, object] = {
        "A1": "Synthetic OQC Mapping V2",
        "A2": "Supplier",
        "B2": _SUPPLIER,
        "C2": "Model",
        "D2": _MODEL,
        "E2": "Lot",
        "F2": _LOT,
        "G2": "Inspection Date",
        "H2": _INSPECTION_DATE,
        "I2": "Part Number",
        "J2": "PART-V2",
        "K2": "Part Name",
        "L2": "Virtual Housing",
        "M2": "Production Date",
        "N2": date(2026, 6, 14),
        "O2": "Current Shipment",
        "P2": 800,
        "Q2": "Supplier Cumulative Shipment",
        "R2": 12_400,
        "S2": "Revision",
        "T2": "REV-V2",
        **_row_values(4),
    }
    if include_second:
        values.update(_row_values(5, variant=2))
    cells = tuple(_cell(coordinate, value) for coordinate, value in values.items())
    sheet = SheetScan(
        name=_SHEET,
        kind=SheetKind.WORKSHEET,
        position=0,
        visibility="visible",
        used_range=f"A1:T{5 if include_second else 4}",
        estimated_cells=len(cells),
        merged_ranges=(),
        hidden_row_ranges=(),
        hidden_column_ranges=(),
        cells=cells,
        row_candidates=(),
        protection=SheetProtectionMetadata(enabled=False, protected_actions=()),
        images=(),
        issues=(),
    )
    return WorkbookScan(
        state=WorkbookScanState.SCANNED,
        source_name="mapping-v2.xlsx",
        source_size_bytes=8_192,
        source_sha256_before=_HASH,
        source_sha256_after=_HASH,
        sheets=(sheet,),
        issues=(),
        estimated_cells=len(cells),
        external_link_count=0,
        macro_handling=MacroHandling.NOT_APPLICABLE,
        is_golden_workbook_evidence=False,
    )


def _inspection_row(
    row_number: int,
    *,
    map_v2: bool,
) -> InspectionRowMapping:
    v2_roles: dict[str, CellAddress | None] = {
        "section": _address(f"B{row_number}") if map_v2 else None,
        "category": _address(f"C{row_number}") if map_v2 else None,
        "unit": _address(f"J{row_number}") if map_v2 else None,
        "measurement_point": _address(f"K{row_number}") if map_v2 else None,
        "measurement_location": _address(f"L{row_number}") if map_v2 else None,
        "cavity": _address(f"M{row_number}") if map_v2 else None,
        "target": _address(f"N{row_number}") if map_v2 else None,
        "lsl": _address(f"O{row_number}") if map_v2 else None,
        "usl": _address(f"P{row_number}") if map_v2 else None,
        "source_spec_revision": _address(f"Q{row_number}") if map_v2 else None,
    }
    return InspectionRowMapping(
        row_key=f"row-{row_number}",
        item=_address(f"A{row_number}"),
        method=_address(f"D{row_number}"),
        instrument=_address(f"E{row_number}"),
        specification=_address(f"F{row_number}"),
        tolerance=_address(f"G{row_number}"),
        minimum=_address(f"H{row_number}"),
        maximum=_address(f"I{row_number}"),
        sample_cells=(_address(f"R{row_number}"),),
        supplier_result=_address(f"S{row_number}"),
        **v2_roles,
    )


def _template(
    *,
    schema_version: str,
    status: MappingTemplateStatus = MappingTemplateStatus.APPROVED,
    include_second: bool = False,
    map_v2: bool | None = None,
    template_id: str | None = None,
) -> MappingTemplate:
    if map_v2 is None:
        map_v2 = schema_version == "2"
    scan = _scan(include_second=include_second)
    row_numbers = (4, 5) if include_second else (4,)
    rows = tuple(_inspection_row(row_number, map_v2=map_v2) for row_number in row_numbers)
    sheet = scan.sheets[0]
    approved = status == MappingTemplateStatus.APPROVED
    identifiers = [
        IdentifierMapping(IdentifierKind.SUPPLIER, _address("B2")),
        IdentifierMapping(IdentifierKind.MODEL, _address("D2")),
        IdentifierMapping(IdentifierKind.LOT_NUMBER, _address("F2")),
        IdentifierMapping(IdentifierKind.INSPECTION_DATE, _address("H2")),
        IdentifierMapping(IdentifierKind.PART_NUMBER, _address("J2")),
        IdentifierMapping(IdentifierKind.REVISION, _address("T2")),
    ]
    if schema_version == "2":
        identifiers.extend(
            (
                IdentifierMapping(IdentifierKind.PART_NAME, _address("L2")),
                IdentifierMapping(IdentifierKind.PRODUCTION_DATE, _address("N2")),
                IdentifierMapping(IdentifierKind.CURRENT_SHIPMENT_QUANTITY, _address("P2")),
                IdentifierMapping(
                    IdentifierKind.SUPPLIER_CUMULATIVE_SHIPMENT_QUANTITY,
                    _address("R2"),
                ),
            )
        )
    return MappingTemplate(
        template_id=template_id or f"mapping-schema-v{schema_version}",
        schema_version=schema_version,
        revision=1,
        status=status,
        project_key=_PROJECT,
        supplier_scope=_SUPPLIER_SCOPE,
        supplier_source_aliases=(_SUPPLIER,),
        approved_by=_ADMIN.actor_id if approved else None,
        approved_at=_NOW if approved else None,
        effective_from=date(2026, 1, 1),
        effective_to=date(2026, 12, 31),
        fingerprint=WorkbookFingerprint(
            header_tokens=(HeaderTokenAssertion(_address("A1"), "Synthetic OQC Mapping V2"),),
            sheet_structures=(
                SheetStructureAssertion(
                    sheet_name=_SHEET,
                    expected_position=0,
                    expected_kind=SheetKind.WORKSHEET,
                    expected_visibility="visible",
                    expected_used_range=sheet.used_range,
                ),
            ),
            merge_signatures=(MergeSignatureAssertion(_SHEET, ()),),
            row_structures=tuple(
                RowStructureAssertion(
                    row_key=row.row_key,
                    sheet_name=_SHEET,
                    row_index=row.item.row_index,
                    expected_non_empty_cells=tuple(
                        _address(cell.coordinate)
                        for cell in sheet.cells
                        if cell.coordinate.endswith(str(row.item.row_index))
                    ),
                )
                for row in rows
            ),
        ),
        identifiers=tuple(identifiers),
        inspection_rows=rows,
    )


def _preview(template: MappingTemplate, *, include_second: bool = False) -> MappingPreview:
    registry = InMemoryMappingTemplateRegistry()
    registry.register(template)
    result = build_mapping_preview(
        _scan(include_second=include_second),
        MappingPreviewRequest(project_key=_PROJECT, supplier_scope=_SUPPLIER_SCOPE),
        registry,
    )
    assert result.state == MappingPreviewState.PREVIEW_READY
    assert result.preview is not None
    return result.preview


def _outcome(template: MappingTemplate, *, include_second: bool = False) -> StoreScanMappingOutcome:
    scan = _scan(include_second=include_second)
    registry = InMemoryMappingTemplateRegistry()
    registry.register(template)
    mapping_result = build_mapping_preview(
        scan,
        MappingPreviewRequest(project_key=_PROJECT, supplier_scope=_SUPPLIER_SCOPE),
        registry,
    )
    assert mapping_result.state == MappingPreviewState.PREVIEW_READY
    return StoreScanMappingOutcome(
        status=StoreScanMappingStatus.PREVIEW_READY,
        scope=ResolvedMappingScope(_PROJECT, _SUPPLIER_SCOPE),
        receipt=SourceFileReceipt(
            receipt_id="mapping-v2-receipt",
            project_key=_PROJECT,
            blob_id=f"sha256:{_HASH}",
            content_sha256=_HASH,
            received_at=_NOW,
            original_filename=scan.source_name,
            model_candidates=(_MODEL,),
            lot_candidates=(_LOT,),
            declared_mime_type=_MIME,
            detected_mime_type=_MIME,
            canonical_extension=".xlsx",
            size_bytes=scan.source_size_bytes,
        ),
        scan=scan,
        mapping_result=mapping_result,
    )


def _bindings(template: MappingTemplate) -> MaterializedCanonicalRowBindingCatalog:
    return MaterializedCanonicalRowBindingCatalog(
        bindings=tuple(
            CanonicalRowBinding(
                key=CanonicalRowBindingKey(
                    project_key=_PROJECT,
                    supplier_scope=_SUPPLIER_SCOPE,
                    template_id=template.template_id,
                    template_revision=template.revision,
                    row_key=row.row_key,
                ),
                binding_revision=1,
                status=CanonicalRowBindingStatus.APPROVED,
                approved_by=_ADMIN.actor_id,
                approved_at=_NOW,
                effective_from=date(2026, 1, 1),
                effective_to=date(2026, 12, 31),
                source_model_values=(_MODEL,),
                canonical_model_key="canonical:model:v2",
                canonical_supplier_key="canonical:supplier:v2",
                canonical_model_part_key="canonical:part:v2",
                canonical_item_key=f"canonical:item:{row.row_key}",
                sample_policy=SamplePolicy.AT_LEAST_ONE,
                measurement_mode=MeasurementMode.NUMERIC,
            )
            for row in template.inspection_rows
        )
    )


def _database(path: Path) -> Database:
    database = Database(f"sqlite+pysqlite:///{path.as_posix()}")
    Base.metadata.create_all(database.engine)
    return database


def _persist_mapping(database: Database, draft: MappingTemplate) -> PersistedMappingTemplate:
    commands = MappingTemplateCommandService(database, clock=lambda: _NOW)
    created = commands.create_revision(
        CreateMappingTemplateRevisionCommand(
            template=draft,
            expected_history_row_version=0,
            actor=_REVIEWER,
            reason="Register exact synthetic source-evidence mapping.",
            source_reference="synthetic-mapping-v2",
        )
    )
    reviewed = commands.review(
        ReviewMappingTemplateRevisionCommand(
            project_key=_PROJECT,
            supplier_scope=_SUPPLIER_SCOPE,
            template_id=draft.template_id,
            revision=1,
            expected_history_row_version=created.history_row_version,
            expected_revision_row_version=created.revision_row_version,
            actor=_REVIEWER,
            reason="Review every exact source-evidence role.",
        )
    )
    return commands.approve(
        ApproveMappingTemplateRevisionCommand(
            project_key=_PROJECT,
            supplier_scope=_SUPPLIER_SCOPE,
            template_id=draft.template_id,
            revision=1,
            expected_history_row_version=reviewed.history_row_version,
            expected_revision_row_version=reviewed.revision_row_version,
            actor=_ADMIN,
            reason="Approve reviewed source-evidence mapping only.",
        )
    )


def _persistent_outcome(
    database: Database,
    mapping: PersistedMappingTemplate,
) -> StoreScanMappingOutcome:
    with database.session() as session:
        catalog = MappingTemplateRepository().load_catalog(session, project_key=_PROJECT)
    scan = _scan()
    mapping_result = build_mapping_preview(
        scan,
        MappingPreviewRequest(project_key=_PROJECT, supplier_scope=_SUPPLIER_SCOPE),
        catalog,
    )
    assert mapping_result.state == MappingPreviewState.PREVIEW_READY
    receipt = SourceFileReceipt(
        receipt_id="mapping-v2-receipt",
        project_key=_PROJECT,
        blob_id=f"sha256:{_HASH}",
        content_sha256=_HASH,
        received_at=_NOW,
        original_filename=scan.source_name,
        model_candidates=(_MODEL,),
        lot_candidates=(_LOT,),
        declared_mime_type=_MIME,
        detected_mime_type=_MIME,
        canonical_extension=".xlsx",
        size_bytes=scan.source_size_bytes,
    )
    assert mapping.template.status == MappingTemplateStatus.APPROVED
    return StoreScanMappingOutcome(
        status=StoreScanMappingStatus.PREVIEW_READY,
        scope=ResolvedMappingScope(_PROJECT, _SUPPLIER_SCOPE),
        receipt=receipt,
        scan=scan,
        mapping_result=mapping_result,
    )


def _persistence_request(
    outcome: StoreScanMappingOutcome,
    template: MappingTemplate,
) -> LongPersistenceRequest:
    return LongPersistenceRequest(
        outcome=outcome,
        candidate=build_long_candidate(outcome, _bindings(template)),
        loader_version="mapping-v2-loader-v1",
        scan_contract_version="mapping-v2-scan-v1",
    )


@pytest.mark.required_test_id("DQ-P1-MAPV2-001")
def test_schema_v1_payload_long_snapshot_and_replay_remain_shape_compatible(
    tmp_path: Path,
) -> None:
    v1 = _template(schema_version="1", map_v2=False)
    mapping_payload = _serialize_template_payload(v1)
    mapping_row = cast(list[dict[str, object]], mapping_payload["inspection_rows"])[0]
    assert set(mapping_row) == _V1_MAPPING_ROW_KEYS
    assert not (_V2_MAPPING_ROW_KEYS - _V1_MAPPING_ROW_KEYS) & set(mapping_row)
    assert _payload_digest(mapping_payload) == (
        "aa1030349d1dc499f9e5d685fd7e158ce5ab3be21821a9bcb249721b7ecb0e5b"
    )

    with pytest.raises(ValueError, match="v2 identifier roles"):
        replace(
            v1,
            identifiers=(
                *v1.identifiers,
                IdentifierMapping(IdentifierKind.PART_NAME, _address("L2")),
            ),
        )
    with pytest.raises(ValueError, match="v2 inspection roles"):
        replace(
            v1,
            inspection_rows=(replace(v1.inspection_rows[0], unit=_address("U4")),),
        )

    database_path = tmp_path / "v1-compat.sqlite3"
    database = _database(database_path)
    persisted = _persist_mapping(
        database,
        _template(
            schema_version="1",
            status=MappingTemplateStatus.DRAFT,
            map_v2=False,
        ),
    )
    outcome = _persistent_outcome(database, persisted)
    request = _persistence_request(outcome, persisted.template)
    snapshot = serialize_long_candidate(request.candidate)
    snapshot_row = cast(list[dict[str, object]], snapshot["rows"])[0]
    assert set(snapshot_row) == _V1_LONG_ROW_KEYS
    assert not _V2_LONG_EVIDENCE_KEYS & set(snapshot_row)
    snapshot_sha256 = canonical_json_sha256(snapshot)

    first = LongPersistenceService(database, clock=lambda: _NOW).persist(request)
    with database.session() as session:
        job = session.scalar(select(LongIngestionJobRow))
        stored_row = session.scalar(select(LongInspectionResultRow))
    assert job is not None
    assert stored_row is not None
    assert set(stored_row.source_evidence) == _V1_SOURCE_EVIDENCE_KEYS
    assert job.candidate_snapshot_sha256 == snapshot_sha256
    assert stored_row.source_evidence_sha256 == canonical_json_sha256(stored_row.source_evidence)
    assert snapshot_sha256 == "ef21c962168e766039a10e41c8618d6704dca3066e246eb24fbc1f767ed274a3"
    assert stored_row.source_evidence_sha256 == (
        "b9b748407bd6bb27f935899d27a1c5d1b34f413119272b26ab834a8ea0a4c991"
    )
    database.dispose()

    restarted = Database(f"sqlite+pysqlite:///{database_path.as_posix()}")
    try:
        replay = LongPersistenceService(restarted, clock=lambda: _NOW).persist(request)
        with restarted.session() as session:
            counts = (
                session.scalar(select(func.count()).select_from(OqcLotRow)),
                session.scalar(select(func.count()).select_from(LongInspectionResultRow)),
                session.scalar(select(func.count()).select_from(LongMeasurementRow)),
            )
        assert replay.replayed is True
        assert replay.ingestion_job_id == first.ingestion_job_id
        assert replay.status == LongJobStatus.COMPLETED_PENDING
        assert counts == (1, 1, 1)
    finally:
        restarted.dispose()


@pytest.mark.required_test_id("DQ-P1-MAPV2-002")
def test_schema_v2_preserves_extended_identifier_source_evidence() -> None:
    preview = _preview(_template(schema_version="2"))
    identifiers = {item.kind: item.evidence for item in preview.identifiers}
    assert {
        kind: (evidence.source.coordinate, evidence.raw_value)
        for kind, evidence in identifiers.items()
        if kind
        in {
            IdentifierKind.PART_NAME,
            IdentifierKind.PRODUCTION_DATE,
            IdentifierKind.CURRENT_SHIPMENT_QUANTITY,
            IdentifierKind.SUPPLIER_CUMULATIVE_SHIPMENT_QUANTITY,
        }
    } == {
        IdentifierKind.PART_NAME: ("L2", "Virtual Housing"),
        IdentifierKind.PRODUCTION_DATE: ("N2", date(2026, 6, 14)),
        IdentifierKind.CURRENT_SHIPMENT_QUANTITY: ("P2", 800),
        IdentifierKind.SUPPLIER_CUMULATIVE_SHIPMENT_QUANTITY: ("R2", 12_400),
    }
    assert all(
        evidence.display_value_status == DisplayValueStatus.NOT_RENDERED
        for evidence in identifiers.values()
    )
    assert {
        kind: (
            evidence.cached_value,
            evidence.formula_text,
            evidence.number_format,
            evidence.value_kind,
        )
        for kind, evidence in identifiers.items()
        if kind
        in {
            IdentifierKind.PART_NAME,
            IdentifierKind.PRODUCTION_DATE,
            IdentifierKind.CURRENT_SHIPMENT_QUANTITY,
            IdentifierKind.SUPPLIER_CUMULATIVE_SHIPMENT_QUANTITY,
        }
    } == {
        IdentifierKind.PART_NAME: (None, None, "General", PreviewValueKind.QUALITATIVE),
        IdentifierKind.PRODUCTION_DATE: (
            None,
            None,
            "yyyy-mm-dd",
            PreviewValueKind.TEMPORAL,
        ),
        IdentifierKind.CURRENT_SHIPMENT_QUANTITY: (
            None,
            None,
            "0.000",
            PreviewValueKind.NUMERIC,
        ),
        IdentifierKind.SUPPLIER_CUMULATIVE_SHIPMENT_QUANTITY: (
            None,
            None,
            "0.000",
            PreviewValueKind.NUMERIC,
        ),
    }


@pytest.mark.required_test_id("DQ-P1-MAPV2-003")
def test_schema_v2_preserves_extended_row_and_spec_evidence() -> None:
    preview = _preview(_template(schema_version="2"))
    row = preview.inspection_rows[0]
    evidence = {
        "section": row.section,
        "category": row.category,
        "unit": row.unit,
        "measurement_point": row.measurement_point,
        "measurement_location": row.measurement_location,
        "cavity": row.cavity,
        "target": row.target,
        "lsl": row.lsl,
        "usl": row.usl,
        "source_spec_revision": row.source_spec_revision,
    }
    assert {
        key: (value.source.coordinate, value.raw_value)
        for key, value in evidence.items()
        if value is not None
    } == {
        "section": ("B4", "치수검사"),
        "category": ("C4", "CTQ"),
        "unit": ("J4", "mm"),
        "measurement_point": ("K4", "P-01"),
        "measurement_location": ("L4", "중앙"),
        "cavity": ("M4", "CAV-A"),
        "target": ("N4", 2.0),
        "lsl": ("O4", 1.9),
        "usl": ("P4", 2.1),
        "source_spec_revision": ("Q4", "SPEC-R2"),
    }
    assert row.system_judgment_status == SystemJudgmentStatus.NOT_EVALUATED
    assert row.system_judgment is None


@pytest.mark.required_test_id("DQ-P1-MAPV2-004")
def test_schema_v2_persistent_roundtrip_digest_and_schema_shape_tamper_fail_closed(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path / "mapping-v2-roundtrip.sqlite3")
    repository = MappingTemplateRepository()
    v2 = _persist_mapping(
        database,
        _template(schema_version="2", status=MappingTemplateStatus.DRAFT),
    )
    v1 = _persist_mapping(
        database,
        _template(
            schema_version="1",
            status=MappingTemplateStatus.DRAFT,
            map_v2=False,
            template_id="mapping-schema-v1-shape",
        ),
    )
    try:
        with database.session() as session:
            loaded = repository.get(
                session,
                project_key=_PROJECT,
                supplier_scope=_SUPPLIER_SCOPE,
                template_id=v2.template.template_id,
                revision=1,
            )
            v2_row = session.get(MappingTemplateRevisionRow, v2.revision_id)
            v1_row = session.get(MappingTemplateRevisionRow, v1.revision_id)
        assert loaded.template == v2.template
        assert v2_row is not None and v1_row is not None
        assert v2_row.payload_sha256 == _payload_digest(v2_row.template_payload)
        serialized_row = cast(list[dict[str, object]], v2_row.template_payload["inspection_rows"])[
            0
        ]
        assert set(serialized_row) == _V2_MAPPING_ROW_KEYS

        with database.session() as session, session.begin():
            session.execute(
                update(MappingTemplateRevisionRow)
                .where(MappingTemplateRevisionRow.id == v2.revision_id)
                .values(schema_version="1")
            )
        with (
            database.session() as session,
            pytest.raises(MappingTemplatePayloadIntegrityError, match="payload shape differs"),
        ):
            repository.get(
                session,
                project_key=_PROJECT,
                supplier_scope=_SUPPLIER_SCOPE,
                template_id=v2.template.template_id,
                revision=1,
            )
        with database.session() as session, session.begin():
            session.execute(
                update(MappingTemplateRevisionRow)
                .where(MappingTemplateRevisionRow.id == v2.revision_id)
                .values(schema_version="2")
            )

        missing_role = deepcopy(v2_row.template_payload)
        missing_rows = cast(list[dict[str, object]], missing_role["inspection_rows"])
        del missing_rows[0]["unit"]
        with database.session() as session, session.begin():
            session.execute(
                update(MappingTemplateRevisionRow)
                .where(MappingTemplateRevisionRow.id == v2.revision_id)
                .values(
                    template_payload=missing_role,
                    payload_sha256=_payload_digest(missing_role),
                )
            )
        with (
            database.session() as session,
            pytest.raises(MappingTemplatePayloadIntegrityError, match="payload shape differs"),
        ):
            repository.get(
                session,
                project_key=_PROJECT,
                supplier_scope=_SUPPLIER_SCOPE,
                template_id=v2.template.template_id,
                revision=1,
            )

        wrong_type = deepcopy(v2_row.template_payload)
        wrong_type_rows = cast(list[dict[str, object]], wrong_type["inspection_rows"])
        wrong_type_rows[0]["unit"] = "J4"
        with database.session() as session, session.begin():
            session.execute(
                update(MappingTemplateRevisionRow)
                .where(MappingTemplateRevisionRow.id == v2.revision_id)
                .values(
                    template_payload=wrong_type,
                    payload_sha256=_payload_digest(wrong_type),
                )
            )
        with (
            database.session() as session,
            pytest.raises(MappingTemplatePayloadIntegrityError, match="unit is not an object"),
        ):
            repository.get(
                session,
                project_key=_PROJECT,
                supplier_scope=_SUPPLIER_SCOPE,
                template_id=v2.template.template_id,
                revision=1,
            )

        invalid_address = deepcopy(v2_row.template_payload)
        invalid_rows = cast(list[dict[str, object]], invalid_address["inspection_rows"])
        invalid_rows[0]["unit"] = {"sheet_name": _SHEET, "coordinate": "not-a-cell"}
        with database.session() as session, session.begin():
            session.execute(
                update(MappingTemplateRevisionRow)
                .where(MappingTemplateRevisionRow.id == v2.revision_id)
                .values(
                    template_payload=invalid_address,
                    payload_sha256=_payload_digest(invalid_address),
                )
            )
        with database.session() as session, pytest.raises(ValueError, match="coordinate"):
            repository.get(
                session,
                project_key=_PROJECT,
                supplier_scope=_SUPPLIER_SCOPE,
                template_id=v2.template.template_id,
                revision=1,
            )
        with database.session() as session, session.begin():
            session.execute(
                update(MappingTemplateRevisionRow)
                .where(MappingTemplateRevisionRow.id == v2.revision_id)
                .values(schema_version="2")
            )
            session.execute(
                update(MappingTemplateRevisionRow)
                .where(MappingTemplateRevisionRow.id == v1.revision_id)
                .values(schema_version="2")
            )
        with (
            database.session() as session,
            pytest.raises(MappingTemplatePayloadIntegrityError, match="payload shape differs"),
        ):
            repository.get(
                session,
                project_key=_PROJECT,
                supplier_scope=_SUPPLIER_SCOPE,
                template_id=v1.template.template_id,
                revision=1,
            )

        tampered = deepcopy(v2_row.template_payload)
        tampered_rows = cast(list[dict[str, object]], tampered["inspection_rows"])
        tampered_rows[0]["unknown_role"] = {"sheet_name": _SHEET, "coordinate": "Z99"}
        with database.session() as session, session.begin():
            session.execute(
                update(MappingTemplateRevisionRow)
                .where(MappingTemplateRevisionRow.id == v2.revision_id)
                .values(template_payload=tampered)
            )
        with (
            database.session() as session,
            pytest.raises(MappingTemplatePayloadIntegrityError, match="digest does not match"),
        ):
            repository.get(
                session,
                project_key=_PROJECT,
                supplier_scope=_SUPPLIER_SCOPE,
                template_id=v2.template.template_id,
                revision=1,
            )
        with database.session() as session, session.begin():
            session.execute(
                update(MappingTemplateRevisionRow)
                .where(MappingTemplateRevisionRow.id == v2.revision_id)
                .values(payload_sha256=_payload_digest(tampered))
            )
        with (
            database.session() as session,
            pytest.raises(MappingTemplatePayloadIntegrityError, match="payload shape differs"),
        ):
            repository.get(
                session,
                project_key=_PROJECT,
                supplier_scope=_SUPPLIER_SCOPE,
                template_id=v2.template.template_id,
                revision=1,
            )
    finally:
        database.dispose()


@pytest.mark.required_test_id("DQ-P1-MAPV2-005")
def test_schema_v2_preview_flows_to_pending_long_and_json_without_interpretation() -> None:
    template = _template(schema_version="2")
    outcome = _outcome(template)
    candidate = build_long_candidate(outcome, _bindings(template))
    preview = outcome.mapping_result.preview if outcome.mapping_result is not None else None
    assert preview is not None
    assert candidate.state == LongCandidateState.LOAD_CANDIDATE_READY
    assert candidate.source_identifiers == preview.identifiers
    row = candidate.rows[0]
    preview_row = preview.inspection_rows[0]
    assert row.section == preview_row.section
    assert row.category == preview_row.category
    assert row.unit == preview_row.unit
    assert row.measurement_point == preview_row.measurement_point
    assert row.measurement_location == preview_row.measurement_location
    assert row.cavity == preview_row.cavity
    assert row.target == preview_row.target
    assert row.lsl == preview_row.lsl
    assert row.usl == preview_row.usl
    assert row.source_spec_revision == preview_row.source_spec_revision
    snapshot = serialize_long_candidate(candidate)
    snapshot_row = cast(list[dict[str, object]], snapshot["rows"])[0]
    assert set(snapshot_row) == _V1_LONG_ROW_KEYS | _V2_LONG_EVIDENCE_KEYS
    unit_evidence = cast(dict[str, object], snapshot_row["unit"])
    assert (unit_evidence["sheet_name"], unit_evidence["coordinate"]) == (_SHEET, "J4")
    assert unit_evidence["raw_value"] == {"kind": "str", "value": "mm"}
    with pytest.raises(ValueError, match="schema-v1 Long candidate"):
        replace(
            candidate,
            provenance=replace(candidate.provenance, template_schema_version="1"),
        )
    assert candidate.official_values_created is False
    assert candidate.calculations_performed is False


@pytest.mark.required_test_id("DQ-P1-MAPV2-006")
def test_schema_v2_optional_roles_are_not_inferred_and_same_item_contexts_stay_separate() -> None:
    optional_template = _template(schema_version="2", map_v2=False)
    optional = _preview(optional_template)
    optional_row = optional.inspection_rows[0]
    assert optional_row.has_v2_evidence is False
    assert optional_row.unit is None
    assert optional_row.measurement_location is None
    assert optional_row.target is None
    optional_candidate = build_long_candidate(
        _outcome(optional_template),
        _bindings(optional_template),
    )
    optional_long_row = optional_candidate.rows[0]
    assert optional_long_row.has_v2_evidence is False
    optional_snapshot = serialize_long_candidate(optional_candidate)
    optional_snapshot_row = cast(list[dict[str, object]], optional_snapshot["rows"])[0]
    assert {key: optional_snapshot_row[key] for key in _V2_LONG_EVIDENCE_KEYS} == dict.fromkeys(
        _V2_LONG_EVIDENCE_KEYS
    )

    separated = _preview(
        _template(schema_version="2", include_second=True),
        include_second=True,
    )
    first, second = separated.inspection_rows
    assert first.item.raw_value == second.item.raw_value == "두께"
    assert first.row_key != second.row_key
    assert first.unit is not None and second.unit is not None
    assert first.measurement_location is not None and second.measurement_location is not None
    assert first.cavity is not None and second.cavity is not None
    assert (first.unit.raw_value, first.measurement_location.raw_value, first.cavity.raw_value) == (
        "mm",
        "중앙",
        "CAV-A",
    )
    assert (
        second.unit.raw_value,
        second.measurement_location.raw_value,
        second.cavity.raw_value,
    ) == (
        "μm",
        "가장자리",
        "CAV-B",
    )
    separated_template = _template(schema_version="2", include_second=True)
    separated_outcome = _outcome(separated_template, include_second=True)
    separated_candidate = build_long_candidate(
        separated_outcome,
        _bindings(separated_template),
    )
    assert separated_candidate.state == LongCandidateState.LOAD_CANDIDATE_READY
    assert tuple(row.row_key for row in separated_candidate.rows) == ("row-4", "row-5")
    assert tuple(row.item.raw_value for row in separated_candidate.rows) == ("두께", "두께")
    assert tuple(
        row.binding.canonical_item_key if row.binding is not None else None
        for row in separated_candidate.rows
    ) == ("canonical:item:row-4", "canonical:item:row-5")
    assert tuple(
        (
            row.unit.raw_value if row.unit is not None else None,
            row.measurement_location.raw_value if row.measurement_location is not None else None,
            row.cavity.raw_value if row.cavity is not None else None,
        )
        for row in separated_candidate.rows
    ) == (("mm", "중앙", "CAV-A"), ("μm", "가장자리", "CAV-B"))
    with pytest.raises(ValueError, match="schema-v1 Mapping Preview"):
        replace(separated, template_schema_version="1")


@pytest.mark.required_test_id("DQ-P1-MAPV2-007")
def test_schema_v2_pending_persistence_replay_keeps_evidence_without_calculation(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "mapping-v2-long.sqlite3"
    database = _database(database_path)
    persisted = _persist_mapping(
        database,
        _template(schema_version="2", status=MappingTemplateStatus.DRAFT),
    )
    outcome = _persistent_outcome(database, persisted)
    request = _persistence_request(outcome, persisted.template)
    candidate = request.candidate
    first = LongPersistenceService(database, clock=lambda: _NOW).persist(request)
    try:
        with database.session() as session:
            lot = session.scalar(select(OqcLotRow))
            result = session.scalar(select(LongInspectionResultRow))
            measurement = session.scalar(select(LongMeasurementRow))
            snapshot = LongFormatRepository().load_candidate_snapshot(
                session,
                project_key=_PROJECT,
                job_id=first.ingestion_job_id,
            )
        assert lot is not None and result is not None and measurement is not None
        assert first.status == LongJobStatus.COMPLETED_PENDING
        assert first.counts.lot_count == first.counts.result_count == 1
        assert first.counts.measurement_count == 1
        assert {item["kind"] for item in lot.identifier_evidence} >= {
            IdentifierKind.PART_NAME.value,
            IdentifierKind.PRODUCTION_DATE.value,
            IdentifierKind.CURRENT_SHIPMENT_QUANTITY.value,
            IdentifierKind.SUPPLIER_CUMULATIVE_SHIPMENT_QUANTITY.value,
        }
        identifier_evidence = {
            cast(str, item["kind"]): cast(dict[str, object], item["evidence"])
            for item in lot.identifier_evidence
        }
        assert {
            kind: (
                evidence["coordinate"],
                evidence["raw_value"],
            )
            for kind, evidence in identifier_evidence.items()
            if kind
            in {
                IdentifierKind.PART_NAME.value,
                IdentifierKind.PRODUCTION_DATE.value,
                IdentifierKind.CURRENT_SHIPMENT_QUANTITY.value,
                IdentifierKind.SUPPLIER_CUMULATIVE_SHIPMENT_QUANTITY.value,
            }
        } == {
            IdentifierKind.PART_NAME.value: ("L2", {"kind": "str", "value": "Virtual Housing"}),
            IdentifierKind.PRODUCTION_DATE.value: (
                "N2",
                {"kind": "date", "value": "2026-06-14"},
            ),
            IdentifierKind.CURRENT_SHIPMENT_QUANTITY.value: (
                "P2",
                {"kind": "int", "value": "800"},
            ),
            IdentifierKind.SUPPLIER_CUMULATIVE_SHIPMENT_QUANTITY.value: (
                "R2",
                {"kind": "int", "value": "12400"},
            ),
        }
        assert lot.identifier_evidence_sha256 == canonical_json_sha256(lot.identifier_evidence)
        assert set(result.source_evidence) == _V1_SOURCE_EVIDENCE_KEYS | _V2_LONG_EVIDENCE_KEYS
        assert {
            key: (
                cast(dict[str, object], result.source_evidence[key])["coordinate"],
                cast(dict[str, object], result.source_evidence[key])["raw_value"],
            )
            for key in ("unit", "measurement_location", "cavity", "source_spec_revision")
        } == {
            "unit": ("J4", {"kind": "str", "value": "mm"}),
            "measurement_location": ("L4", {"kind": "str", "value": "중앙"}),
            "cavity": ("M4", {"kind": "str", "value": "CAV-A"}),
            "source_spec_revision": ("Q4", {"kind": "str", "value": "SPEC-R2"}),
        }
        assert result.source_evidence_sha256 == canonical_json_sha256(result.source_evidence)
        assert result.data_status == LongDataStatus.PENDING.value
        assert result.system_judgment is None
        assert result.system_judgment_status == SystemJudgmentStatus.NOT_EVALUATED.value
        assert result.spec_evaluation_status == SpecEvaluationStatus.NOT_EVALUATED.value
        assert measurement.standardized_value is None
        assert measurement.unit_conversion_status == UnitConversionStatus.NOT_CONFIGURED.value
        assert snapshot == serialize_long_candidate(candidate)
        assert candidate.official_values_created is False
        assert candidate.calculations_performed is False
    finally:
        database.dispose()

    restarted = Database(f"sqlite+pysqlite:///{database_path.as_posix()}")
    try:
        replay = LongPersistenceService(restarted, clock=lambda: _NOW).persist(request)
        with restarted.session() as session:
            counts = (
                session.scalar(select(func.count()).select_from(OqcLotRow)),
                session.scalar(select(func.count()).select_from(LongInspectionResultRow)),
                session.scalar(select(func.count()).select_from(LongMeasurementRow)),
            )
        assert replay.replayed is True
        assert replay.ingestion_job_id == first.ingestion_job_id
        assert replay.status == LongJobStatus.COMPLETED_PENDING
        assert counts == (1, 1, 1)
    finally:
        restarted.dispose()
