"""Validate the immutable baseline and the separate living requirement trackers."""

from __future__ import annotations

import csv
import hashlib
import io
import re
import sys
import zipfile
from pathlib import Path

EXPECTED_PACKAGE_SHA256 = "17750504c999f8c7ec331646ce00456d71bc9a33018f9cefe23be8ce41ebba03"
EXPECTED_BASELINE_ROWS = 333
EXPECTED_MANIFEST_ROWS = 14
ALLOWED_IMPLEMENTATION_STATUSES = {
    "NOT_STARTED",
    "IN_PROGRESS",
    "IMPLEMENTED",
    "VERIFIED",
    "BLOCKED_BY_INPUT",
    "DEFERRED_BY_PHASE",
    "OUT_OF_SCOPE_CONFIRMED",
}
BASELINE_NAME = "13A_MASS_PRODUCTION_QUALITY_VALIDATION_REQUIREMENTS_CHECKLIST.csv"
MANIFEST_PATTERN = re.compile(r"^([0-9a-fA-F]{64})  \./(.+)$")


class IntegrityError(RuntimeError):
    """Raised when a governed input or tracker violates its contract."""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise IntegrityError(f"CSV has no header: {path}")
        return list(reader.fieldnames), list(reader)


def _require_columns(path: Path, columns: list[str], required: set[str]) -> None:
    missing = sorted(required.difference(columns))
    if missing:
        raise IntegrityError(f"CSV is missing columns {missing}: {path}")


def _require_unique_ids(path: Path, rows: list[dict[str, str]]) -> set[str]:
    ids = [row.get("requirement_id", "").strip() for row in rows]
    blank_rows = [index + 2 for index, value in enumerate(ids) if not value]
    if blank_rows:
        raise IntegrityError(f"Blank requirement_id at rows {blank_rows}: {path}")
    duplicates = sorted({value for value in ids if ids.count(value) > 1})
    if duplicates:
        raise IntegrityError(f"Duplicate requirement IDs {duplicates}: {path}")
    return set(ids)


def validate_package(root: Path) -> bytes:
    package_path = root / "MASS_PRODUCTION_QUALITY_VALIDATION_CODEX_HANDOFF_PACKAGE.zip"
    package_bytes = package_path.read_bytes()
    actual_package_hash = _sha256(package_bytes)
    if actual_package_hash != EXPECTED_PACKAGE_SHA256:
        raise IntegrityError(
            "Baseline package SHA-256 mismatch: "
            f"expected {EXPECTED_PACKAGE_SHA256}, got {actual_package_hash}"
        )

    with zipfile.ZipFile(io.BytesIO(package_bytes)) as archive:
        file_names = [name for name in archive.namelist() if not name.endswith("/")]
        manifest_names = [name for name in file_names if name.endswith("/MANIFEST_SHA256.txt")]
        if len(manifest_names) != 1:
            raise IntegrityError(f"Expected one inner manifest, found {len(manifest_names)}")

        manifest_name = manifest_names[0]
        prefix = manifest_name.removesuffix("MANIFEST_SHA256.txt")
        manifest_lines = archive.read(manifest_name).decode("utf-8-sig").splitlines()
        declarations: dict[str, str] = {}
        for line in manifest_lines:
            match = MANIFEST_PATTERN.fullmatch(line.strip())
            if match is None:
                raise IntegrityError(f"Malformed manifest line: {line!r}")
            expected_hash, relative_name = match.groups()
            if relative_name in declarations:
                raise IntegrityError(f"Duplicate manifest path: {relative_name}")
            declarations[relative_name] = expected_hash.lower()

        if len(declarations) != EXPECTED_MANIFEST_ROWS:
            raise IntegrityError(
                f"Expected {EXPECTED_MANIFEST_ROWS} manifest rows, found {len(declarations)}"
            )
        if len(file_names) != EXPECTED_MANIFEST_ROWS + 1:
            raise IntegrityError(
                f"Expected {EXPECTED_MANIFEST_ROWS + 1} files in ZIP, found {len(file_names)}"
            )

        for relative_name, expected_hash in declarations.items():
            archive_name = f"{prefix}{relative_name}"
            try:
                content = archive.read(archive_name)
            except KeyError as error:
                raise IntegrityError(f"Manifest file is missing: {relative_name}") from error
            actual_hash = _sha256(content)
            if actual_hash != expected_hash:
                raise IntegrityError(
                    f"Manifest SHA-256 mismatch for {relative_name}: "
                    f"expected {expected_hash}, got {actual_hash}"
                )

        return archive.read(f"{prefix}{BASELINE_NAME}")


def validate_trackers(root: Path, baseline_bytes: bytes) -> tuple[int, int, int, int]:
    baseline_path = root / "requirements" / BASELINE_NAME
    if baseline_path.read_bytes() != baseline_bytes:
        raise IntegrityError(
            "The repository baseline checklist differs from the immutable ZIP; "
            "record changes in LIVING_REQUIREMENTS_AMENDMENTS.csv"
        )

    baseline_columns, baseline_rows = _read_csv(baseline_path)
    _require_columns(
        baseline_path,
        baseline_columns,
        {"requirement_id", "implementation_status", "code_reference", "test_reference"},
    )
    if len(baseline_rows) != EXPECTED_BASELINE_ROWS:
        raise IntegrityError(
            f"Expected {EXPECTED_BASELINE_ROWS} baseline rows, found {len(baseline_rows)}"
        )
    baseline_ids = _require_unique_ids(baseline_path, baseline_rows)
    non_initial = sorted(
        row["requirement_id"]
        for row in baseline_rows
        if row["implementation_status"] != "NOT_STARTED"
    )
    if non_initial:
        raise IntegrityError(
            "The immutable baseline tracker must retain NOT_STARTED state; "
            f"changed IDs: {non_initial}"
        )

    amendments_path = root / "requirements" / "LIVING_REQUIREMENTS_AMENDMENTS.csv"
    amendment_columns, amendment_rows = _read_csv(amendments_path)
    _require_columns(
        amendments_path,
        amendment_columns,
        {
            "requirement_id",
            "implementation_status",
            "decision_date",
            "amendment_type",
            "supersedes_or_clarifies",
        },
    )
    amendment_ids = _require_unique_ids(amendments_path, amendment_rows)
    overlapping_ids = sorted(baseline_ids.intersection(amendment_ids))
    if overlapping_ids:
        raise IntegrityError(
            "Living amendments must use new IDs and reference clarified baseline IDs in "
            f"supersedes_or_clarifies; overlaps: {overlapping_ids}"
        )
    invalid_statuses = sorted(
        {
            row["implementation_status"]
            for row in amendment_rows
            if row["implementation_status"] not in ALLOWED_IMPLEMENTATION_STATUSES
        }
    )
    if invalid_statuses:
        raise IntegrityError(f"Invalid living implementation statuses: {invalid_statuses}")

    scope_path = root / "requirements" / "PHASE_0_1_GATE_SCOPE.csv"
    scope_columns, scope_rows = _read_csv(scope_path)
    _require_columns(
        scope_path,
        scope_columns,
        {"requirement_id", "phase", "scope_disposition", "required_test_ids"},
    )
    known_ids = baseline_ids | amendment_ids
    unknown_scope_ids = sorted({row["requirement_id"] for row in scope_rows}.difference(known_ids))
    if unknown_scope_ids:
        raise IntegrityError(f"Gate scope references unknown requirements: {unknown_scope_ids}")

    test_manifest_path = root / "backend" / "tests" / "required_regression_test_ids.txt"
    test_ids = [
        line.strip()
        for line in test_manifest_path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if len(test_ids) != len(set(test_ids)):
        raise IntegrityError("Required regression test ID manifest contains duplicates")
    required_from_scope = {
        test_id.strip()
        for row in scope_rows
        for test_id in row["required_test_ids"].replace(";", ",").split(",")
        if test_id.strip()
    }
    missing_manifest_ids = sorted(required_from_scope.difference(test_ids))
    if missing_manifest_ids:
        raise IntegrityError(
            f"Gate scope test IDs missing from regression manifest: {missing_manifest_ids}"
        )
    orphan_manifest_ids = sorted(set(test_ids).difference(required_from_scope))
    if orphan_manifest_ids:
        raise IntegrityError(
            f"Regression manifest IDs are not mapped by Gate scope: {orphan_manifest_ids}"
        )

    unscoped_amendments = sorted(
        amendment_ids.difference({row["requirement_id"] for row in scope_rows})
    )
    if unscoped_amendments:
        raise IntegrityError(f"Living amendments missing Gate scope rows: {unscoped_amendments}")

    status_path = root / "requirements" / "LIVING_IMPLEMENTATION_STATUS.csv"
    status_columns, status_rows = _read_csv(status_path)
    _require_columns(
        status_path,
        status_columns,
        {
            "requirement_id",
            "implementation_status",
            "code_reference",
            "test_reference",
            "acceptance_evidence",
            "last_updated",
        },
    )
    status_ids = _require_unique_ids(status_path, status_rows)
    unknown_status_ids = sorted(status_ids.difference(known_ids))
    if unknown_status_ids:
        raise IntegrityError(
            f"Living implementation status references unknown requirements: {unknown_status_ids}"
        )
    invalid_overlay_statuses = sorted(
        {
            row["implementation_status"]
            for row in status_rows
            if row["implementation_status"] not in ALLOWED_IMPLEMENTATION_STATUSES
        }
    )
    if invalid_overlay_statuses:
        raise IntegrityError(f"Invalid implementation overlay statuses: {invalid_overlay_statuses}")

    required_baseline_overlay_ids = {
        row["requirement_id"]
        for row in scope_rows
        if row["phase"] in {"Phase 0", "Phase 1"} and row["requirement_id"] in baseline_ids
    }
    missing_living_statuses = sorted(required_baseline_overlay_ids.difference(status_ids))
    if missing_living_statuses:
        raise IntegrityError(
            "Current Phase 0/1 baseline requirements missing Living status rows: "
            f"{missing_living_statuses}"
        )

    incomplete_verified = sorted(
        row["requirement_id"]
        for row in status_rows
        if row["implementation_status"] == "VERIFIED"
        and not all(
            row[field].strip()
            for field in ("code_reference", "test_reference", "acceptance_evidence")
        )
    )
    if incomplete_verified:
        raise IntegrityError(
            "VERIFIED Living status rows require code, test, and acceptance evidence: "
            f"{incomplete_verified}"
        )

    return len(baseline_rows), len(amendment_rows), len(scope_rows), len(status_rows)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    try:
        baseline_bytes = validate_package(root)
        baseline_count, amendment_count, scope_count, status_count = validate_trackers(
            root, baseline_bytes
        )
    except (IntegrityError, FileNotFoundError, zipfile.BadZipFile, UnicodeDecodeError) as error:
        print(f"REQUIREMENT INTEGRITY FAILED: {error}", file=sys.stderr)
        return 1

    print(
        "Requirement integrity passed: "
        f"baseline={baseline_count}, living_amendments={amendment_count}, "
        f"gate_scope={scope_count}, status_overlay={status_count}, "
        f"manifest_files={EXPECTED_MANIFEST_ROWS}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
