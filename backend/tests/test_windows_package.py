from __future__ import annotations

import hashlib
import json
import subprocess
import zipfile
from pathlib import Path
from typing import Any

import pytest
from scripts.release.package_tool import (
    PackageToolError,
    build_package,
    install_package,
    remove_installation,
    verify_package,
)

ROOT = Path(__file__).resolve().parents[2]


def _build(tmp_path: Path, name: str = "mass-production-quality-validation.zip") -> Path:
    output = tmp_path / name
    build_package(repo_root=ROOT, output_path=output)
    return output


def _fake_runtime(stage: Path, lock_path: Path) -> None:
    assert lock_path.is_file()
    scripts = stage / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "python.exe").write_bytes(b"synthetic-python-3.12")
    (stage / ".venv" / "runtime-lock.sha256").write_text(
        hashlib.sha256(lock_path.read_bytes()).hexdigest(),
        encoding="ascii",
    )


def _tree_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _fail_at(expected: str) -> Any:
    def fail(point: str) -> None:
        if point == expected:
            raise RuntimeError(f"synthetic failure at {point}")

    return fail


def _run_powershell(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", *arguments],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


@pytest.mark.required_test_id("DQ-P1-WINPKG-001")
def test_extension_artifact_is_byte_reproducible_and_runtime_complete(tmp_path: Path) -> None:
    first = _build(tmp_path, "first.zip")
    second = _build(tmp_path, "second.zip")

    assert first.read_bytes() == second.read_bytes()
    assert (
        hashlib.sha256(first.read_bytes()).hexdigest()
        == hashlib.sha256(second.read_bytes()).hexdigest()
    )
    verified = verify_package(first)
    assert verified.extension_id == "com.massproductionqualityvalidation.oqc-local"
    assert verified.mass_production_quality_validation_version == "0.1.0"
    names = set(verified.files)
    assert {
        "extension-manifest.json",
        "package-files.json",
        "Install-MassProductionQualityValidation.ps1",
        "package_tool.py",
        "payload/Launch-MassProductionQualityValidation.ps1",
        "payload/backend/app/main.py",
        "payload/backend/migrations/versions/0005_persist_data_status_review.py",
        "payload/frontend/dist/index.html",
        "payload/requirements/runtime.lock",
    }.issubset(names)
    assert not any(
        "__pycache__" in name
        or name.endswith((".pyc", ".sqlite3", ".db"))
        or name.startswith("payload/backend/tests/")
        or name.startswith("payload/frontend/node_modules/")
        for name in names
    )
    with zipfile.ZipFile(first) as archive:
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0) for info in archive.infolist())


@pytest.mark.required_test_id("DQ-P1-WINPKG-002")
def test_manifest_keeps_scheduler_unverified_and_any_byte_tamper_fails_closed(
    tmp_path: Path,
) -> None:
    package = _build(tmp_path)
    verified = verify_package(package)
    manifest = json.loads(verified.files["extension-manifest.json"])

    assert manifest["contract_majors"] == {
        "extension_package": 1,
        "manual_intake_api": 1,
        "scheduler_queue": None,
    }
    assert manifest["scheduler_compatibility"] == {
        "status": "UNVERIFIED",
        "phase": "PHASE_5",
        "discovery": "BLOCKED_BY_INPUT",
    }
    assert manifest["runtime"]["offline_wheelhouse_included"] is False
    runtime_lock = verified.files["payload/requirements/runtime.lock"]
    assert hashlib.sha256(runtime_lock).hexdigest() == manifest["runtime"]["lock_sha256"]
    assert b"--hash=sha256:" in runtime_lock

    tampered = tmp_path / "tampered.zip"
    with (
        zipfile.ZipFile(package, "r") as source,
        zipfile.ZipFile(tampered, "w", compression=zipfile.ZIP_DEFLATED) as destination,
    ):
        for info in source.infolist():
            data = source.read(info)
            if info.filename == "payload/backend/app/main.py":
                data += b"\n# tampered\n"
            destination.writestr(info, data)

    with pytest.raises(PackageToolError, match="integrity failed"):
        verify_package(tampered)


@pytest.mark.required_test_id("DQ-P1-WINPKG-003")
def test_dry_run_is_write_free_and_unsafe_or_overlapping_roots_are_rejected(
    tmp_path: Path,
) -> None:
    package = _build(tmp_path)
    install_root = tmp_path / "code"
    data_root = tmp_path / "data"

    result = install_package(
        action="install",
        package_path=package,
        install_root=install_root,
        data_root=data_root,
        dry_run=True,
    )

    assert result.dry_run is True
    assert result.runtime_provisioned is False
    assert result.data_preserved is True
    assert not install_root.exists()
    assert not data_root.exists()
    assert tuple(path for path in tmp_path.iterdir() if path.name not in {package.name}) == ()

    with pytest.raises(PackageToolError, match="disjoint"):
        install_package(
            action="install",
            package_path=package,
            install_root=install_root,
            data_root=install_root / "data",
            dry_run=True,
        )
    with pytest.raises(PackageToolError, match="dangerously broad"):
        install_package(
            action="install",
            package_path=package,
            install_root=Path.home(),
            data_root=data_root,
            dry_run=True,
        )

    wrapper = _run_powershell(
        [
            "-File",
            str(ROOT / "scripts" / "release" / "Install-Package.ps1"),
            "-Action",
            "Install",
            "-PackagePath",
            str(package),
            "-InstallRoot",
            str(install_root),
            "-DataRoot",
            str(data_root),
            "-DryRun",
        ]
    )
    assert wrapper.returncode == 0, wrapper.stderr
    assert json.loads(wrapper.stdout)["dry_run"] is True
    assert not install_root.exists()
    assert not data_root.exists()


@pytest.mark.required_test_id("DQ-P1-WINPKG-004")
def test_install_update_and_both_pre_and_post_swap_failures_are_atomic(tmp_path: Path) -> None:
    package = _build(tmp_path)
    install_root = tmp_path / "code"
    data_root = tmp_path / "data"

    installed = install_package(
        action="install",
        package_path=package,
        install_root=install_root,
        data_root=data_root,
        runtime_provisioner=_fake_runtime,
    )
    assert installed.runtime_provisioned is True
    assert installed.cleanup_pending is False
    assert (install_root / "Launch-MassProductionQualityValidation.ps1").is_file()
    assert (install_root / ".venv" / "Scripts" / "python.exe").is_file()
    assert not data_root.exists()

    marker = install_root / "local-install-marker.txt"
    marker.write_text("old-install", encoding="utf-8")
    before = _tree_snapshot(install_root)
    with pytest.raises(PackageToolError, match="rolled back"):
        install_package(
            action="update",
            package_path=package,
            install_root=install_root,
            data_root=data_root,
            runtime_provisioner=_fake_runtime,
            failure_injector=_fail_at("after_swap"),
        )
    assert _tree_snapshot(install_root) == before

    fresh_root = tmp_path / "fresh-code"
    with pytest.raises(PackageToolError, match="rolled back"):
        install_package(
            action="install",
            package_path=package,
            install_root=fresh_root,
            data_root=data_root,
            runtime_provisioner=_fake_runtime,
            failure_injector=_fail_at("after_swap"),
        )
    assert not fresh_root.exists()

    updated = install_package(
        action="update",
        package_path=package,
        install_root=install_root,
        data_root=data_root,
        runtime_provisioner=_fake_runtime,
    )
    assert updated.cleanup_pending is False
    assert not marker.exists()
    assert not tuple(tmp_path.glob(".code.stage.*"))
    assert not tuple(tmp_path.glob(".code.backup.*"))


@pytest.mark.required_test_id("DQ-P1-WINPKG-005")
def test_remove_is_rollback_safe_and_preserves_every_user_data_byte(tmp_path: Path) -> None:
    package = _build(tmp_path)
    install_root = tmp_path / "code"
    data_root = tmp_path / "data"
    install_package(
        action="install",
        package_path=package,
        install_root=install_root,
        data_root=data_root,
        runtime_provisioner=_fake_runtime,
    )
    data_root.mkdir()
    (data_root / "mass_production_quality_validation.sqlite3").write_bytes(b"user-db")
    (data_root / "original.xlsx").write_bytes(b"user-original")
    data_before = _tree_snapshot(data_root)
    code_before = _tree_snapshot(install_root)

    dry_run = remove_installation(
        install_root=install_root,
        data_root=data_root,
        dry_run=True,
    )
    assert dry_run.data_preserved is True
    assert _tree_snapshot(install_root) == code_before
    assert _tree_snapshot(data_root) == data_before

    with pytest.raises(PackageToolError, match="rolled back"):
        remove_installation(
            install_root=install_root,
            data_root=data_root,
            failure_injector=_fail_at("after_remove_swap"),
        )
    assert _tree_snapshot(install_root) == code_before
    assert _tree_snapshot(data_root) == data_before

    removed = remove_installation(install_root=install_root, data_root=data_root)
    assert removed.data_preserved is True
    assert not install_root.exists()
    assert _tree_snapshot(data_root) == data_before
    assert not tuple(tmp_path.glob(".code.remove.*"))


@pytest.mark.required_test_id("DQ-P1-WINPKG-006")
def test_launcher_is_localhost_single_instance_and_has_no_persistent_os_integration(
    tmp_path: Path,
) -> None:
    package = _build(tmp_path)
    install_root = tmp_path / "code"
    data_root = tmp_path / "data"
    install_package(
        action="install",
        package_path=package,
        install_root=install_root,
        data_root=data_root,
        runtime_provisioner=_fake_runtime,
    )
    launcher = install_root / "Launch-MassProductionQualityValidation.ps1"

    parsed = _run_powershell(
        [
            "-Command",
            (
                "$tokens=$null;$errors=$null;"
                f"[System.Management.Automation.Language.Parser]::ParseFile('{launcher}',"
                "[ref]$tokens,[ref]$errors)|Out-Null;"
                "if($errors.Count -ne 0){$errors|ForEach-Object{$_.Message};exit 1}"
            ),
        ]
    )
    assert parsed.returncode == 0, parsed.stderr
    dry_run = _run_powershell(
        [
            "-File",
            str(launcher),
            "-DataRoot",
            str(data_root),
            "-Port",
            "18765",
            "-NoBrowser",
            "-DryRun",
        ]
    )
    assert dry_run.returncode == 0, dry_run.stderr
    plan = json.loads(dry_run.stdout)
    assert plan == {
        "action": "launch",
        "dry_run": True,
        "host": "127.0.0.1",
        "port": 18765,
        "code_root": str(install_root),
        "data_root": str(data_root),
        "browser": False,
        "persistent_os_integration": False,
    }
    assert not data_root.exists()

    source = launcher.read_text(encoding="utf-8")
    lowered = source.lower()
    assert "local\\massproductionqualityvalidationlocalhostport$port" in lowered
    assert "127.0.0.1" in source
    assert "/api/v1/health/live" in source
    assert "start-process $browserurl" in lowered
    for forbidden in (
        "reg.exe",
        "new-itemproperty",
        "set-itemproperty",
        "schtasks",
        "new-service",
        "start-service",
        "cloud scheduler",
        "outlook",
    ):
        assert forbidden not in lowered
