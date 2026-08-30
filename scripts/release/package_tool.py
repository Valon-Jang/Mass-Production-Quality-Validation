"""Build and transact a hash-verified Mass Production Quality Validation Windows extension package.

The module uses only the Python 3.12 standard library so an extracted package
can verify and install itself before its private runtime environment exists.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
import zipfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Literal, cast
from uuid import uuid4

_FORMAT = "MASS_PRODUCTION_QUALITY_VALIDATION_EXTENSION_ZIP_V1"
_INVENTORY_NAME = "package-files.json"
_EXTENSION_MANIFEST_NAME = "extension-manifest.json"
_INSTALL_RECEIPT_NAME = "install-receipt.json"
_PAYLOAD_PREFIX = "payload/"
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_MAX_PACKAGE_FILES = 20_000
_MAX_PACKAGE_BYTES = 512 * 1024 * 1024
_VERSION_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+\Z")
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")

InstallAction = Literal["install", "update"]
RuntimeProvisioner = Callable[[Path, Path], None]
FailureInjector = Callable[[str], None]


class PackageToolError(RuntimeError):
    """A fail-closed package validation or transaction error."""


@dataclass(frozen=True, slots=True)
class PackageBuildResult:
    artifact_path: str
    artifact_sha256: str
    extension_id: str
    mass_production_quality_validation_version: str
    file_count: int
    size_bytes: int


@dataclass(frozen=True, slots=True)
class OperationResult:
    action: str
    dry_run: bool
    extension_id: str
    mass_production_quality_validation_version: str
    install_root: str
    data_root: str
    data_preserved: bool
    runtime_provisioned: bool
    cleanup_pending: bool


@dataclass(frozen=True, slots=True)
class VerifiedPackage:
    files: Mapping[str, bytes]
    extension_id: str
    mass_production_quality_validation_version: str
    inventory_sha256: str


def build_package(*, repo_root: Path, output_path: Path) -> PackageBuildResult:
    """Create a byte-reproducible ZIP from an explicit runtime allowlist."""

    root = repo_root.resolve(strict=True)
    output = output_path.resolve()
    sources = _collect_runtime_sources(root)
    extension = _validated_extension_manifest(sources[_EXTENSION_MANIFEST_NAME])
    _validate_repository_versions(root, extension)
    _validate_runtime_lock(extension, sources[f"{_PAYLOAD_PREFIX}requirements/runtime.lock"])

    inventory = _inventory_bytes(
        extension_id=cast(str, extension["extension_id"]),
        mass_production_quality_validation_version=cast(
            str, extension["mass_production_quality_validation_version"]
        ),
        files=sources,
    )
    archive_files = dict(sources)
    archive_files[_INVENTORY_NAME] = inventory
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.parent / f".{output.name}.{uuid4().hex}.tmp"
    try:
        with zipfile.ZipFile(
            temporary,
            mode="x",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as archive:
            for name in sorted(archive_files):
                info = zipfile.ZipInfo(name, date_time=_ZIP_TIMESTAMP)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                archive.writestr(info, archive_files[name], compresslevel=9)
        os.replace(temporary, output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    digest = _sha256_path(output)
    return PackageBuildResult(
        artifact_path=str(output),
        artifact_sha256=digest,
        extension_id=cast(str, extension["extension_id"]),
        mass_production_quality_validation_version=cast(
            str, extension["mass_production_quality_validation_version"]
        ),
        file_count=len(archive_files),
        size_bytes=output.stat().st_size,
    )


def verify_package(package_path: Path) -> VerifiedPackage:
    """Read and verify a ZIP or extracted package without writing to disk."""

    package = package_path.resolve(strict=True)
    files = _read_package_image(package)
    if _INVENTORY_NAME not in files:
        raise PackageToolError(f"package is missing {_INVENTORY_NAME}")
    inventory_bytes = files[_INVENTORY_NAME]
    inventory = _json_object(inventory_bytes, _INVENTORY_NAME)
    _require_exact_keys(
        inventory,
        {
            "format",
            "schema_version",
            "extension_id",
            "mass_production_quality_validation_version",
            "entries",
        },
        _INVENTORY_NAME,
    )
    if inventory["format"] != _FORMAT or inventory["schema_version"] != 1:
        raise PackageToolError("unsupported package inventory format")
    entries_raw = inventory["entries"]
    if not isinstance(entries_raw, list):
        raise PackageToolError("package inventory entries must be an array")
    expected: dict[str, tuple[int, str]] = {}
    for index, value in enumerate(entries_raw):
        entry = _object(value, f"inventory entry {index}")
        _require_exact_keys(entry, {"path", "size_bytes", "sha256"}, f"entry {index}")
        path = _safe_archive_name(_exact_string(entry["path"], f"entry {index} path"))
        size = entry["size_bytes"]
        digest = _exact_string(entry["sha256"], f"entry {index} sha256")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise PackageToolError(f"entry {index} has invalid size")
        if _SHA256_PATTERN.fullmatch(digest) is None:
            raise PackageToolError(f"entry {index} has invalid SHA-256")
        if path in expected:
            raise PackageToolError(f"duplicate package inventory path: {path}")
        expected[path] = (size, digest)

    actual_names = set(files)
    if actual_names != set(expected) | {_INVENTORY_NAME}:
        missing = sorted((set(expected) | {_INVENTORY_NAME}) - actual_names)
        extra = sorted(actual_names - (set(expected) | {_INVENTORY_NAME}))
        raise PackageToolError(f"package file set mismatch; missing={missing}, extra={extra}")
    for name, (expected_size, expected_digest) in expected.items():
        data = files[name]
        if len(data) != expected_size or _sha256_bytes(data) != expected_digest:
            raise PackageToolError(f"package file integrity failed: {name}")

    extension = _validated_extension_manifest(files[_EXTENSION_MANIFEST_NAME])
    extension_id = cast(str, extension["extension_id"])
    version = cast(str, extension["mass_production_quality_validation_version"])
    if (
        inventory["extension_id"] != extension_id
        or inventory["mass_production_quality_validation_version"] != version
    ):
        raise PackageToolError("extension manifest and package inventory identity disagree")
    required = {
        "Install-MassProductionQualityValidation.ps1",
        "package_tool.py",
        f"{_PAYLOAD_PREFIX}Launch-MassProductionQualityValidation.ps1",
        f"{_PAYLOAD_PREFIX}alembic.ini",
        f"{_PAYLOAD_PREFIX}pyproject.toml",
        f"{_PAYLOAD_PREFIX}requirements/runtime.lock",
        f"{_PAYLOAD_PREFIX}backend/app/main.py",
        f"{_PAYLOAD_PREFIX}frontend/dist/index.html",
    }
    if not required.issubset(files):
        raise PackageToolError(f"package runtime is incomplete: {sorted(required - set(files))}")
    _validate_runtime_lock(extension, files[f"{_PAYLOAD_PREFIX}requirements/runtime.lock"])
    return VerifiedPackage(
        files=files,
        extension_id=extension_id,
        mass_production_quality_validation_version=version,
        inventory_sha256=_sha256_bytes(inventory_bytes),
    )


def install_package(
    *,
    action: InstallAction,
    package_path: Path,
    install_root: Path,
    data_root: Path,
    dry_run: bool = False,
    runtime_provisioner: RuntimeProvisioner | None = None,
    failure_injector: FailureInjector | None = None,
) -> OperationResult:
    """Install or update code atomically; data is never created or removed here."""

    package = verify_package(package_path)
    install, data = _validated_roots(install_root, data_root)
    existing = _existing_receipt(install)
    if action == "install" and install.exists():
        raise PackageToolError("install target already exists; use update explicitly")
    if action == "update":
        if existing is None:
            raise PackageToolError("update requires a verified existing installation")
        if existing["extension_id"] != package.extension_id:
            raise PackageToolError("update extension identity does not match the installation")
        if Path(cast(str, existing["data_root"])).resolve() != data:
            raise PackageToolError("update data root does not match the installation receipt")
        current_version = _version_tuple(
            cast(str, existing["mass_production_quality_validation_version"])
        )
        if _version_tuple(package.mass_production_quality_validation_version) < current_version:
            raise PackageToolError("downgrade is not permitted by the update command")
        _verify_existing_payload(install, existing)
    if dry_run:
        return OperationResult(
            action=action,
            dry_run=True,
            extension_id=package.extension_id,
            mass_production_quality_validation_version=package.mass_production_quality_validation_version,
            install_root=str(install),
            data_root=str(data),
            data_preserved=True,
            runtime_provisioned=False,
            cleanup_pending=False,
        )

    install_parent = install.parent
    install_parent.mkdir(parents=True, exist_ok=True)
    stage = install_parent / f".{install.name}.stage.{uuid4().hex}"
    backup = install_parent / f".{install.name}.backup.{uuid4().hex}"
    swapped_new = False
    moved_old = False
    provisioner = runtime_provisioner or _provision_runtime
    inject = failure_injector or (lambda _point: None)
    try:
        stage.mkdir()
        _write_payload(package, stage, data)
        provisioner(stage, stage / "requirements" / "runtime.lock")
        _require_runtime_python(stage)
        inject("after_provision")
        if action == "update":
            os.replace(install, backup)
            moved_old = True
        os.replace(stage, install)
        swapped_new = True
        inject("after_swap")
        _verify_installed_tree(install, package, data)
    except Exception as error:
        try:
            if swapped_new and install.exists():
                _remove_transaction_tree(install, install_parent, expected_name=install.name)
            if moved_old and backup.exists():
                os.replace(backup, install)
        finally:
            if stage.exists():
                _remove_transaction_tree(
                    stage,
                    install_parent,
                    expected_prefix=f".{install.name}.stage.",
                )
        if isinstance(error, PackageToolError):
            raise
        raise PackageToolError(f"{action} transaction failed and was rolled back") from error

    cleanup_pending = False
    if moved_old:
        try:
            _remove_transaction_tree(
                backup,
                install_parent,
                expected_prefix=f".{install.name}.backup.",
            )
        except OSError:
            # Verification above is the commit point. Never restore a backup
            # that may have been partially deleted after the new tree passed.
            cleanup_pending = True

    return OperationResult(
        action=action,
        dry_run=False,
        extension_id=package.extension_id,
        mass_production_quality_validation_version=package.mass_production_quality_validation_version,
        install_root=str(install),
        data_root=str(data),
        data_preserved=True,
        runtime_provisioned=True,
        cleanup_pending=cleanup_pending,
    )


def remove_installation(
    *,
    install_root: Path,
    data_root: Path,
    dry_run: bool = False,
    failure_injector: FailureInjector | None = None,
) -> OperationResult:
    """Remove verified code only; the separate data root is always preserved."""

    install, data = _validated_roots(install_root, data_root)
    receipt = _existing_receipt(install)
    if receipt is None:
        raise PackageToolError("remove requires a verified existing installation")
    if Path(cast(str, receipt["data_root"])).resolve() != data:
        raise PackageToolError("remove data root does not match the installation receipt")
    extension_id = cast(str, receipt["extension_id"])
    version = cast(str, receipt["mass_production_quality_validation_version"])
    if dry_run:
        return OperationResult(
            action="remove",
            dry_run=True,
            extension_id=extension_id,
            mass_production_quality_validation_version=version,
            install_root=str(install),
            data_root=str(data),
            data_preserved=True,
            runtime_provisioned=False,
            cleanup_pending=False,
        )

    parent = install.parent
    tombstone = parent / f".{install.name}.remove.{uuid4().hex}"
    inject = failure_injector or (lambda _point: None)
    moved = False
    try:
        os.replace(install, tombstone)
        moved = True
        inject("after_remove_swap")
    except Exception as error:
        if moved and tombstone.exists() and not install.exists():
            os.replace(tombstone, install)
        if isinstance(error, PackageToolError):
            raise
        raise PackageToolError("remove transaction failed and was rolled back") from error
    cleanup_pending = False
    try:
        _remove_transaction_tree(tombstone, parent, expected_prefix=f".{install.name}.remove.")
    except OSError:
        # The atomic rename is the remove commit point. Data remains separate;
        # a partially deleted code tombstone must never be restored as active.
        cleanup_pending = True
    return OperationResult(
        action="remove",
        dry_run=False,
        extension_id=extension_id,
        mass_production_quality_validation_version=version,
        install_root=str(install),
        data_root=str(data),
        data_preserved=True,
        runtime_provisioned=False,
        cleanup_pending=cleanup_pending,
    )


def _collect_runtime_sources(root: Path) -> dict[str, bytes]:
    mappings = {
        root / "packaging" / "extension-manifest.json": _EXTENSION_MANIFEST_NAME,
        root
        / "scripts"
        / "release"
        / "Install-Package.ps1": "Install-MassProductionQualityValidation.ps1",
        root / "scripts" / "release" / "package_tool.py": "package_tool.py",
        root / "packaging" / "Launch-MassProductionQualityValidation.ps1": (
            f"{_PAYLOAD_PREFIX}Launch-MassProductionQualityValidation.ps1"
        ),
        root / "alembic.ini": f"{_PAYLOAD_PREFIX}alembic.ini",
        root / "pyproject.toml": f"{_PAYLOAD_PREFIX}pyproject.toml",
        root / "requirements" / "runtime.lock": f"{_PAYLOAD_PREFIX}requirements/runtime.lock",
    }
    files: dict[str, bytes] = {}
    for source, destination in mappings.items():
        files[destination] = _read_source_file(source, root)
    _collect_tree(root, root / "backend" / "app", f"{_PAYLOAD_PREFIX}backend/app", files, {".py"})
    _collect_tree(
        root,
        root / "backend" / "migrations",
        f"{_PAYLOAD_PREFIX}backend/migrations",
        files,
        {".py"},
    )
    _collect_tree(root, root / "frontend" / "dist", f"{_PAYLOAD_PREFIX}frontend/dist", files)
    return files


def _collect_tree(
    root: Path,
    source_root: Path,
    destination_root: str,
    files: dict[str, bytes],
    suffixes: set[str] | None = None,
) -> None:
    if not source_root.is_dir():
        raise PackageToolError(
            f"required runtime directory is missing: {source_root.relative_to(root)}"
        )
    for source in sorted(source_root.rglob("*")):
        if source.is_symlink():
            raise PackageToolError(
                f"runtime source symlink is not allowed: {source.relative_to(root)}"
            )
        if not source.is_file() or (suffixes is not None and source.suffix not in suffixes):
            continue
        relative = source.relative_to(source_root).as_posix()
        destination = _safe_archive_name(f"{destination_root}/{relative}")
        files[destination] = _read_source_file(source, root)


def _read_source_file(source: Path, root: Path) -> bytes:
    resolved = source.resolve(strict=True)
    if source.is_symlink() or not resolved.is_relative_to(root):
        raise PackageToolError(f"runtime source escapes repository: {source}")
    return resolved.read_bytes()


def _inventory_bytes(
    *,
    extension_id: str,
    mass_production_quality_validation_version: str,
    files: Mapping[str, bytes],
) -> bytes:
    entries = [
        {"path": name, "size_bytes": len(files[name]), "sha256": _sha256_bytes(files[name])}
        for name in sorted(files)
    ]
    return _canonical_json(
        {
            "format": _FORMAT,
            "schema_version": 1,
            "extension_id": extension_id,
            "mass_production_quality_validation_version": (
                mass_production_quality_validation_version
            ),
            "entries": entries,
        }
    )


def _read_package_image(package: Path) -> dict[str, bytes]:
    if package.is_file():
        try:
            with zipfile.ZipFile(package, "r") as archive:
                infos = archive.infolist()
                if len(infos) > _MAX_PACKAGE_FILES:
                    raise PackageToolError("package contains too many files")
                names: set[str] = set()
                total = 0
                files: dict[str, bytes] = {}
                for info in infos:
                    if info.is_dir():
                        continue
                    name = _safe_archive_name(info.filename)
                    if name in names:
                        raise PackageToolError(f"duplicate ZIP member: {name}")
                    names.add(name)
                    total += info.file_size
                    if total > _MAX_PACKAGE_BYTES:
                        raise PackageToolError("package expands beyond the safety limit")
                    data = archive.read(info)
                    if len(data) != info.file_size:
                        raise PackageToolError(f"ZIP member size mismatch: {name}")
                    files[name] = data
                return files
        except (OSError, zipfile.BadZipFile, RuntimeError) as error:
            raise PackageToolError("package ZIP is unreadable") from error
    if not package.is_dir():
        raise PackageToolError("package path must be a ZIP or extracted directory")
    files = {}
    total = 0
    for path in sorted(package.rglob("*")):
        if path.is_symlink():
            raise PackageToolError("extracted package may not contain symlinks")
        if not path.is_file():
            continue
        name = _safe_archive_name(path.relative_to(package).as_posix())
        data = path.read_bytes()
        total += len(data)
        if len(files) >= _MAX_PACKAGE_FILES or total > _MAX_PACKAGE_BYTES:
            raise PackageToolError("extracted package exceeds safety limits")
        files[name] = data
    return files


def _validated_extension_manifest(data: bytes) -> dict[str, object]:
    manifest = _json_object(data, _EXTENSION_MANIFEST_NAME)
    _require_exact_keys(
        manifest,
        {
            "schema_version",
            "extension_id",
            "display_name",
            "mass_production_quality_validation_version",
            "contract_majors",
            "scheduler_compatibility",
            "runtime",
            "entrypoint",
            "install_policy",
        },
        _EXTENSION_MANIFEST_NAME,
    )
    if manifest["schema_version"] != 1:
        raise PackageToolError("unsupported extension manifest schema")
    extension_id = _exact_string(manifest["extension_id"], "extension_id")
    version = _exact_string(
        manifest["mass_production_quality_validation_version"],
        "mass_production_quality_validation_version",
    )
    if (
        extension_id != "com.massproductionqualityvalidation.oqc-local"
        or _VERSION_PATTERN.fullmatch(version) is None
    ):
        raise PackageToolError("extension identity or version is invalid")
    if (
        manifest["display_name"] != "Mass Production Quality Validation"
        or manifest["entrypoint"] != "Launch-MassProductionQualityValidation.ps1"
    ):
        raise PackageToolError("extension display name or entrypoint is invalid")
    contracts = _object(manifest["contract_majors"], "contract_majors")
    _require_exact_keys(
        contracts,
        {"extension_package", "manual_intake_api", "scheduler_queue"},
        "contract_majors",
    )
    if contracts != {"extension_package": 1, "manual_intake_api": 1, "scheduler_queue": None}:
        raise PackageToolError("extension contract majors are incompatible")
    scheduler = _object(manifest["scheduler_compatibility"], "scheduler_compatibility")
    if scheduler != {
        "status": "UNVERIFIED",
        "phase": "PHASE_5",
        "discovery": "BLOCKED_BY_INPUT",
    }:
        raise PackageToolError("Scheduler compatibility must remain unverified and blocked")
    runtime = _object(manifest["runtime"], "runtime")
    _require_exact_keys(
        runtime,
        {"python", "lock_path", "lock_sha256", "offline_wheelhouse_included"},
        "runtime",
    )
    if (
        runtime["python"] != "3.12"
        or runtime["lock_path"] != "requirements/runtime.lock"
        or runtime["offline_wheelhouse_included"] is not False
        or _SHA256_PATTERN.fullmatch(_exact_string(runtime["lock_sha256"], "lock_sha256")) is None
    ):
        raise PackageToolError("runtime declaration is invalid")
    policy = _object(manifest["install_policy"], "install_policy")
    _require_exact_keys(
        policy,
        {
            "scope",
            "code_data_separated",
            "uninstall_data_default",
            "registry",
            "autostart",
            "windows_service",
        },
        "install_policy",
    )
    if policy != {
        "scope": "CURRENT_USER_LOCAL",
        "code_data_separated": True,
        "uninstall_data_default": "PRESERVE",
        "registry": False,
        "autostart": False,
        "windows_service": False,
    }:
        raise PackageToolError("install policy violates the local extension boundary")
    return manifest


def _validate_repository_versions(root: Path, extension: Mapping[str, object]) -> None:
    expected = cast(str, extension["mass_production_quality_validation_version"])
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    project = _object(pyproject.get("project"), "pyproject project")
    frontend = _json_object((root / "frontend" / "package.json").read_bytes(), "frontend package")
    version_source = (root / "backend" / "app" / "version.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([0-9]+\.[0-9]+\.[0-9]+)"$', version_source, re.MULTILINE)
    versions = {
        expected,
        project.get("version"),
        frontend.get("version"),
        match.group(1) if match else None,
    }
    if versions != {expected}:
        raise PackageToolError(
            f"Mass Production Quality Validation version sources disagree: {versions}"
        )


def _validate_runtime_lock(extension: Mapping[str, object], lock_bytes: bytes) -> None:
    runtime = _object(extension["runtime"], "runtime")
    if _sha256_bytes(lock_bytes) != runtime["lock_sha256"]:
        raise PackageToolError("runtime lock SHA-256 does not match the extension manifest")
    if b"--hash=sha256:" not in lock_bytes or len(lock_bytes) < 1024:
        raise PackageToolError("runtime lock is not a complete hash-locked dependency set")


def _write_payload(package: VerifiedPackage, stage: Path, data_root: Path) -> None:
    payload_entries: list[dict[str, object]] = []
    for name in sorted(package.files):
        if not name.startswith(_PAYLOAD_PREFIX):
            continue
        relative = name.removeprefix(_PAYLOAD_PREFIX)
        destination = _safe_destination(stage, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(package.files[name])
        payload_entries.append(
            {
                "path": relative,
                "size_bytes": len(package.files[name]),
                "sha256": _sha256_bytes(package.files[name]),
            }
        )
    (stage / _EXTENSION_MANIFEST_NAME).write_bytes(package.files[_EXTENSION_MANIFEST_NAME])
    receipt = {
        "schema_version": 1,
        "extension_id": package.extension_id,
        "mass_production_quality_validation_version": (
            package.mass_production_quality_validation_version
        ),
        "package_inventory_sha256": package.inventory_sha256,
        "data_root": str(data_root),
        "payload": payload_entries,
    }
    (stage / _INSTALL_RECEIPT_NAME).write_bytes(_canonical_json(receipt))


def _verify_installed_tree(install: Path, package: VerifiedPackage, data_root: Path) -> None:
    receipt = _existing_receipt(install)
    if receipt is None:
        raise PackageToolError("installed receipt is missing or invalid")
    if (
        receipt["extension_id"] != package.extension_id
        or receipt["mass_production_quality_validation_version"]
        != package.mass_production_quality_validation_version
        or receipt["package_inventory_sha256"] != package.inventory_sha256
        or Path(cast(str, receipt["data_root"])).resolve() != data_root
    ):
        raise PackageToolError("installed receipt does not match the package transaction")
    payload = receipt["payload"]
    if not isinstance(payload, list):
        raise PackageToolError("installed payload receipt is invalid")
    for raw in payload:
        entry = _object(raw, "installed payload entry")
        relative = _exact_string(entry.get("path"), "installed payload path")
        destination = _safe_destination(install, relative)
        size = entry.get("size_bytes")
        digest = entry.get("sha256")
        if (
            not destination.is_file()
            or not isinstance(size, int)
            or destination.stat().st_size != size
            or not isinstance(digest, str)
            or _sha256_path(destination) != digest
        ):
            raise PackageToolError(f"installed payload integrity failed: {relative}")
    _require_runtime_python(install)


def _existing_receipt(install: Path) -> dict[str, object] | None:
    if not install.exists():
        return None
    if not install.is_dir() or install.is_symlink():
        raise PackageToolError("install target is not a safe directory")
    receipt_path = install / _INSTALL_RECEIPT_NAME
    if not receipt_path.is_file():
        raise PackageToolError(
            "existing install target is not managed by Mass Production Quality Validation"
        )
    receipt = _json_object(receipt_path.read_bytes(), _INSTALL_RECEIPT_NAME)
    _require_exact_keys(
        receipt,
        {
            "schema_version",
            "extension_id",
            "mass_production_quality_validation_version",
            "package_inventory_sha256",
            "data_root",
            "payload",
        },
        _INSTALL_RECEIPT_NAME,
    )
    if (
        receipt["schema_version"] != 1
        or receipt["extension_id"] != "com.massproductionqualityvalidation.oqc-local"
    ):
        raise PackageToolError("existing installation receipt identity is invalid")
    _version_tuple(
        _exact_string(receipt["mass_production_quality_validation_version"], "installed version")
    )
    _exact_string(receipt["data_root"], "installed data root")
    digest = _exact_string(receipt["package_inventory_sha256"], "package inventory digest")
    if _SHA256_PATTERN.fullmatch(digest) is None or not isinstance(receipt["payload"], list):
        raise PackageToolError("existing installation receipt is malformed")
    return receipt


def _verify_existing_payload(install: Path, receipt: Mapping[str, object]) -> None:
    manifest_path = install / _EXTENSION_MANIFEST_NAME
    if not manifest_path.is_file():
        raise PackageToolError("existing extension manifest is missing")
    manifest = _validated_extension_manifest(manifest_path.read_bytes())
    if (
        manifest["extension_id"] != receipt["extension_id"]
        or manifest["mass_production_quality_validation_version"]
        != receipt["mass_production_quality_validation_version"]
    ):
        raise PackageToolError("existing extension identity does not match its receipt")
    payload = receipt["payload"]
    if not isinstance(payload, list):
        raise PackageToolError("existing payload receipt is malformed")
    seen: set[str] = set()
    for index, raw in enumerate(payload):
        entry = _object(raw, f"existing payload entry {index}")
        _require_exact_keys(
            entry,
            {"path", "size_bytes", "sha256"},
            f"existing payload entry {index}",
        )
        relative = _exact_string(entry["path"], f"existing payload entry {index} path")
        if relative in seen:
            raise PackageToolError("existing payload receipt contains duplicate paths")
        seen.add(relative)
        size = entry["size_bytes"]
        digest = entry["sha256"]
        destination = _safe_destination(install, relative)
        if (
            not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
            or not isinstance(digest, str)
            or _SHA256_PATTERN.fullmatch(digest) is None
            or not destination.is_file()
            or destination.stat().st_size != size
            or _sha256_path(destination) != digest
        ):
            raise PackageToolError(f"existing payload integrity failed: {relative}")
    _require_runtime_python(install)


def _provision_runtime(stage: Path, lock_path: Path) -> None:
    if sys.version_info[:2] != (3, 12):
        raise PackageToolError("installer must run with Python 3.12")
    venv = stage / ".venv"
    _run_checked([sys.executable, "-m", "venv", str(venv)], cwd=stage)
    python = venv / "Scripts" / "python.exe"
    _run_checked(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "--require-hashes",
            "--requirement",
            str(lock_path),
        ],
        cwd=stage,
    )
    _run_checked([str(python), "-m", "pip", "check"], cwd=stage)


def _run_checked(arguments: Sequence[str], *, cwd: Path) -> None:
    result = subprocess.run(arguments, cwd=cwd, check=False)
    if result.returncode != 0:
        raise PackageToolError(f"runtime command failed with exit code {result.returncode}")


def _require_runtime_python(root: Path) -> None:
    if not (root / ".venv" / "Scripts" / "python.exe").is_file():
        raise PackageToolError("private Python runtime was not provisioned")


def _validated_roots(install_root: Path, data_root: Path) -> tuple[Path, Path]:
    install = install_root.resolve()
    data = data_root.resolve()
    home = Path.home().resolve()
    for value, label in ((install, "install"), (data, "data")):
        if value.parent == value or value == home:
            raise PackageToolError(f"{label} root is dangerously broad")
    if install == data or install.is_relative_to(data) or data.is_relative_to(install):
        raise PackageToolError("install and data roots must be disjoint")
    return install, data


def _remove_transaction_tree(
    target: Path,
    parent: Path,
    *,
    expected_prefix: str | None = None,
    expected_name: str | None = None,
) -> None:
    resolved = target.resolve()
    if resolved.parent != parent.resolve():
        raise PackageToolError("transaction cleanup target escaped its exact parent")
    if expected_prefix is not None and not resolved.name.startswith(expected_prefix):
        raise PackageToolError("transaction cleanup target has an unexpected name")
    if expected_name is not None and resolved.name != expected_name:
        raise PackageToolError("transaction cleanup target is not the install root")
    shutil.rmtree(resolved)


def _safe_destination(root: Path, relative: str) -> Path:
    safe = _safe_archive_name(relative)
    destination = (root / Path(*PurePosixPath(safe).parts)).resolve()
    if not destination.is_relative_to(root.resolve()):
        raise PackageToolError("package destination escaped its staging root")
    return destination


def _safe_archive_name(value: str) -> str:
    if not value or "\\" in value or "\x00" in value:
        raise PackageToolError("package path is not canonical POSIX text")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise PackageToolError(f"unsafe package path: {value}")
    canonical = path.as_posix()
    if canonical != value:
        raise PackageToolError(f"non-canonical package path: {value}")
    return canonical


def _canonical_json(value: object) -> bytes:
    serialized = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"{serialized}\n".encode()


def _json_object(data: bytes, label: str) -> dict[str, object]:
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PackageToolError(f"{label} is not valid UTF-8 JSON") from error
    return _object(value, label)


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise PackageToolError(f"{label} must be a JSON object")
    return cast(dict[str, object], value)


def _require_exact_keys(value: Mapping[str, object], expected: set[str], label: str) -> None:
    if set(value) != expected:
        raise PackageToolError(f"{label} keys do not match the contract")


def _exact_string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise PackageToolError(f"{label} must be exact non-blank text")
    return value


def _version_tuple(value: str) -> tuple[int, int, int]:
    if _VERSION_PATTERN.fullmatch(value) is None:
        raise PackageToolError("version must use three numeric components")
    major, minor, patch = value.split(".")
    return int(major), int(minor), int(patch)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _print_result(result: PackageBuildResult | OperationResult) -> None:
    print(json.dumps(asdict(result), ensure_ascii=False, sort_keys=True))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--repo-root", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--package", type=Path, required=True)
    for action in ("install", "update"):
        command = commands.add_parser(action)
        command.add_argument("--package", type=Path, required=True)
        command.add_argument("--install-root", type=Path, required=True)
        command.add_argument("--data-root", type=Path, required=True)
        command.add_argument("--dry-run", action="store_true")
    remove = commands.add_parser("remove")
    remove.add_argument("--install-root", type=Path, required=True)
    remove.add_argument("--data-root", type=Path, required=True)
    remove.add_argument("--dry-run", action="store_true")
    return parser


def main(arguments: Iterable[str] | None = None) -> int:
    parsed = _parser().parse_args(list(arguments) if arguments is not None else None)
    try:
        if parsed.command == "build":
            _print_result(build_package(repo_root=parsed.repo_root, output_path=parsed.output))
        elif parsed.command == "verify":
            package = verify_package(parsed.package)
            print(
                json.dumps(
                    {
                        "extension_id": package.extension_id,
                        "mass_production_quality_validation_version": (
                            package.mass_production_quality_validation_version
                        ),
                        "inventory_sha256": package.inventory_sha256,
                        "verified": True,
                    },
                    sort_keys=True,
                )
            )
        elif parsed.command in {"install", "update"}:
            _print_result(
                install_package(
                    action=cast(InstallAction, parsed.command),
                    package_path=parsed.package,
                    install_root=parsed.install_root,
                    data_root=parsed.data_root,
                    dry_run=parsed.dry_run,
                )
            )
        else:
            _print_result(
                remove_installation(
                    install_root=parsed.install_root,
                    data_root=parsed.data_root,
                    dry_run=parsed.dry_run,
                )
            )
    except PackageToolError as error:
        print(
            f"Mass Production Quality Validation package operation refused: {error}",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
