from __future__ import annotations

import hashlib
import json
import os
import zipfile
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

import app.infrastructure.file_store.original as file_store_module
from app.infrastructure.file_store import (
    XLSM_MIME,
    XLSX_MIME,
    OriginalFileStore,
    SourceChangedDuringIngestError,
    SourceFileValidationError,
)

_CONTENT_TYPES_NAMESPACE = "http://schemas.openxmlformats.org/package/2006/content-types"
_XLSX_MAIN_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
_XLSM_MAIN_TYPE = "application/vnd.ms-excel.sheet.macroEnabled.main+xml"
_WORKSHEET_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"


def _write_ooxml(path: Path, declarations: str, *, include_worksheet: bool = False) -> bytes:
    content_types = f"""<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="{_CONTENT_TYPES_NAMESPACE}">
{declarations}
</Types>"""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("xl/workbook.xml", "<workbook/>")
        if include_worksheet:
            archive.writestr("xl/worksheets/sheet1.xml", "<worksheet/>")
    return path.read_bytes()


def _make_ooxml(path: Path, *, macro_enabled: bool = False) -> bytes:
    workbook_type = _XLSM_MAIN_TYPE if macro_enabled else _XLSX_MAIN_TYPE
    return _write_ooxml(
        path,
        f'  <Override PartName="/xl/workbook.xml" ContentType="{workbook_type}"/>',
    )


@pytest.mark.required_test_id("DQ-P1-FSTORE-001")
@pytest.mark.required_test_id("DQ-P1-FSTORE-002")
def test_preserves_original_bytes_and_complete_public_metadata(tmp_path: Path) -> None:
    source = tmp_path / "Vendor OQC.xlsx"
    original = _make_ooxml(source)
    before_hash = hashlib.sha256(original).hexdigest()
    store_root = tmp_path / "store"
    now = datetime(2026, 8, 15, 4, 30, tzinfo=UTC)
    store = OriginalFileStore(store_root, max_bytes=1024 * 1024, clock=lambda: now)

    receipt = store.preserve(
        project_key="project-alpha",
        source=source,
        declared_mime_type=XLSX_MIME,
        model_candidates=(" MODEL-A ", "MODEL-A", "MODEL-B"),
        lot_candidates=("LOT-001",),
    )

    assert source.read_bytes() == original
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before_hash
    assert receipt.content_sha256 == before_hash
    assert receipt.blob_id == f"sha256:{before_hash}"
    assert receipt.received_at == now
    assert receipt.original_filename == source.name
    assert receipt.model_candidates == ("MODEL-A", "MODEL-B")
    assert receipt.lot_candidates == ("LOT-001",)
    assert receipt.declared_mime_type == XLSX_MIME
    assert receipt.detected_mime_type == XLSX_MIME
    assert receipt.size_bytes == len(original)
    public_json = json.dumps(asdict(receipt), default=str)
    assert str(store_root.resolve()) not in public_json
    assert str(source.resolve()) not in public_json
    with store.open_source(receipt) as stream:
        assert stream.read() == original
        assert not stream.writable()


@pytest.mark.required_test_id("DQ-P1-FSTORE-006")
def test_projects_are_isolated_and_locator_values_cannot_traverse(tmp_path: Path) -> None:
    source = tmp_path / "same.xlsm"
    original = _make_ooxml(source, macro_enabled=True)
    store_root = tmp_path / "store"
    store = OriginalFileStore(store_root, max_bytes=1024 * 1024)

    alpha = store.preserve(project_key="alpha", source=source, declared_mime_type=XLSM_MIME)
    beta = store.preserve(project_key="beta", source=source, declared_mime_type=XLSM_MIME)
    upper_alpha = store.preserve(project_key="ALPHA", source=source, declared_mime_type=XLSM_MIME)

    assert alpha.content_sha256 == beta.content_sha256 == upper_alpha.content_sha256
    assert len(list(store_root.rglob(f"{alpha.content_sha256}.xlsm"))) == 3
    assert store.list_receipts(project_key="alpha", content_sha256=alpha.content_sha256) == (alpha,)
    assert store.list_receipts(project_key="beta", content_sha256=beta.content_sha256) == (beta,)
    assert store.list_receipts(project_key="ALPHA", content_sha256=upper_alpha.content_sha256) == (
        upper_alpha,
    )
    with store.open_source(alpha) as stream:
        assert stream.read() == original
    with pytest.raises(SourceFileValidationError, match="project_key"):
        store.preserve(project_key="../escape", source=source, declared_mime_type=XLSM_MIME)
    with (
        pytest.raises(SourceFileValidationError, match="project_key"),
        store.open_source(replace(alpha, project_key="../beta")),
    ):
        pass
    with pytest.raises(SourceFileValidationError, match="SHA-256"):
        store.list_receipts(project_key="alpha", content_sha256="../receipt")


@pytest.mark.required_test_id("DQ-P1-FSTORE-005")
def test_duplicate_delivery_adds_receipt_without_replacing_the_raw_blob(tmp_path: Path) -> None:
    source = tmp_path / "repeat.xlsx"
    original = _make_ooxml(source)
    store_root = tmp_path / "store"
    store = OriginalFileStore(store_root, max_bytes=1024 * 1024)

    first = store.preserve(
        project_key="personal-project", source=source, declared_mime_type=XLSX_MIME
    )
    second = store.preserve(
        project_key="personal-project", source=source, declared_mime_type=XLSX_MIME
    )
    receipts = store.list_receipts(
        project_key="personal-project", content_sha256=first.content_sha256
    )

    assert first.receipt_id != second.receipt_id
    assert {item.receipt_id for item in receipts} == {first.receipt_id, second.receipt_id}
    assert len(list(store_root.rglob(f"{first.content_sha256}.xlsx"))) == 1

    with store.open_source(first) as stream:
        assert stream.read() == original


@pytest.mark.required_test_id("DQ-P1-FSTORE-003")
def test_opc_types_and_rejections_leave_no_partial_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    valid_store = OriginalFileStore(tmp_path / "v", max_bytes=1024 * 1024)

    default_variant = tmp_path / "default-content-type.xlsx"
    default_variant_bytes = _write_ooxml(
        default_variant,
        "\n".join(
            (
                f'  <Default Extension="xml" ContentType="{_XLSX_MAIN_TYPE}"/>',
                '  <Override PartName="/xl/worksheets/sheet1.xml" '
                f'ContentType="{_WORKSHEET_TYPE}"/>',
            )
        ),
        include_worksheet=True,
    )
    default_variant_hash = hashlib.sha256(default_variant_bytes).hexdigest()
    default_receipt = valid_store.preserve(
        project_key="alpha",
        source=default_variant,
        declared_mime_type=XLSX_MIME,
    )
    assert default_receipt.content_sha256 == default_variant_hash
    assert hashlib.sha256(default_variant.read_bytes()).hexdigest() == default_variant_hash
    with valid_store.open_source(default_receipt) as stored_default:
        assert stored_default.read() == default_variant_bytes

    override_wins = tmp_path / "exact-override-wins.xlsx"
    override_bytes = _write_ooxml(
        override_wins,
        "\n".join(
            (
                f'  <Default Extension="xml" ContentType="{_XLSM_MAIN_TYPE}"/>',
                f'  <Override PartName="/xl/workbook.xml" ContentType="{_XLSX_MAIN_TYPE}"/>',
            )
        ),
    )
    override_receipt = valid_store.preserve(
        project_key="alpha",
        source=override_wins,
        declared_mime_type=XLSX_MIME,
    )
    assert (
        hashlib.sha256(override_wins.read_bytes()).hexdigest()
        == hashlib.sha256(override_bytes).hexdigest()
    )
    with valid_store.open_source(override_receipt) as stored_override:
        assert stored_override.read() == override_bytes

    rejected_opc_root = tmp_path / "r"
    rejected_opc_store = OriginalFileStore(rejected_opc_root, max_bytes=1024 * 1024)
    ambiguous_packages = {
        "duplicate-default.xlsx": "\n".join(
            (
                f'  <Default Extension="xml" ContentType="{_XLSX_MAIN_TYPE}"/>',
                f'  <Default Extension="XML" ContentType="{_XLSX_MAIN_TYPE}"/>',
            )
        ),
        "duplicate-override.xlsx": "\n".join(
            (
                f'  <Override PartName="/xl/workbook.xml" ContentType="{_XLSX_MAIN_TYPE}"/>',
                f'  <Override PartName="/xl/workbook.xml" ContentType="{_XLSX_MAIN_TYPE}"/>',
            )
        ),
    }
    for filename, declarations in ambiguous_packages.items():
        ambiguous = tmp_path / filename
        _write_ooxml(ambiguous, declarations)
        with pytest.raises(SourceFileValidationError) as ambiguity:
            rejected_opc_store.preserve(
                project_key="alpha", source=ambiguous, declared_mime_type=XLSX_MIME
            )
        assert ambiguity.value.code == "AMBIGUOUS_OOXML_CONTENT_TYPE"

    wrong_default = tmp_path / "wrong-default.xlsx"
    _write_ooxml(
        wrong_default,
        f'  <Default Extension="xml" ContentType="{_XLSM_MAIN_TYPE}"/>',
    )
    with pytest.raises(SourceFileValidationError) as wrong_effective_type:
        rejected_opc_store.preserve(
            project_key="alpha", source=wrong_default, declared_mime_type=XLSX_MIME
        )
    assert wrong_effective_type.value.code == "OOXML_TYPE_MISMATCH"
    assert not [path for path in rejected_opc_root.rglob("*") if path.is_file()]

    store_root = tmp_path / "store"
    store = OriginalFileStore(store_root, max_bytes=600)

    wrong_extension = tmp_path / "oqc.txt"
    wrong_extension.write_bytes(b"not an OOXML workbook")
    with pytest.raises(SourceFileValidationError) as unsupported:
        store.preserve(project_key="alpha", source=wrong_extension, declared_mime_type="text/plain")
    assert unsupported.value.code == "UNSUPPORTED_EXTENSION"

    invalid_zip = tmp_path / "invalid.xlsx"
    invalid_zip.write_bytes(b"not a zip")
    with pytest.raises(SourceFileValidationError) as invalid:
        store.preserve(project_key="alpha", source=invalid_zip, declared_mime_type=XLSX_MIME)
    assert invalid.value.code == "INVALID_OOXML_PACKAGE"

    wrong_declared_mime = tmp_path / "wrong-declared.xlsx"
    _make_ooxml(wrong_declared_mime)
    with pytest.raises(SourceFileValidationError) as declared_mismatch:
        store.preserve(
            project_key="alpha",
            source=wrong_declared_mime,
            declared_mime_type=XLSM_MIME,
        )
    assert declared_mismatch.value.code == "DECLARED_MIME_MISMATCH"

    mismatched = tmp_path / "macro.xlsx"
    _make_ooxml(mismatched, macro_enabled=True)
    with pytest.raises(SourceFileValidationError) as mismatch:
        store.preserve(project_key="alpha", source=mismatched, declared_mime_type=XLSX_MIME)
    assert mismatch.value.code == "OOXML_TYPE_MISMATCH"

    too_large = tmp_path / "large.xlsx"
    _make_ooxml(too_large)
    too_large.write_bytes(too_large.read_bytes() + (b"x" * 700))
    with pytest.raises(SourceFileValidationError) as large:
        store.preserve(project_key="alpha", source=too_large, declared_mime_type=XLSX_MIME)
    assert large.value.code == "SOURCE_TOO_LARGE"

    changing = tmp_path / "changing.xlsx"
    _make_ooxml(changing)
    roomy_store = OriginalFileStore(store_root, max_bytes=1024 * 1024)
    original_copy = roomy_store._copy_source_to_temp

    def copy_then_mutate(source: Path, destination: Path) -> tuple[str, int]:
        result = original_copy(source, destination)
        with source.open("ab") as stream:
            stream.write(b"changed outside the store")
            stream.flush()
            os.fsync(stream.fileno())
        return result

    monkeypatch.setattr(roomy_store, "_copy_source_to_temp", copy_then_mutate)
    with pytest.raises(SourceChangedDuringIngestError):
        roomy_store.preserve(project_key="alpha", source=changing, declared_mime_type=XLSX_MIME)

    atomic_source = tmp_path / "atomic.xlsx"
    _make_ooxml(atomic_source)
    atomic_store = OriginalFileStore(store_root, max_bytes=1024 * 1024)
    real_replace = os.replace
    replace_calls = 0

    def fail_receipt_replace(source: Path, destination: Path) -> None:
        nonlocal replace_calls
        replace_calls += 1
        if replace_calls == 2:
            raise OSError("synthetic atomic receipt failure")
        real_replace(source, destination)

    with monkeypatch.context() as atomic_failure:
        atomic_failure.setattr(file_store_module.os, "replace", fail_receipt_replace)
        with pytest.raises(OSError, match="atomic receipt failure"):
            atomic_store.preserve(
                project_key="alpha", source=atomic_source, declared_mime_type=XLSX_MIME
            )

    assert not list(store_root.rglob("*.tmp"))
    assert not list(store_root.rglob("*.json"))
    assert not list(store_root.rglob("*.xlsx"))
    assert not list(store_root.rglob("*.xlsm"))
